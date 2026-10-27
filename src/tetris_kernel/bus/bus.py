"""事件总线（总线层 · S11 · M2-S5）。

**序号唯一权威** · 三种订阅 · 两个入口 · 异常隔离 · 待发布队列 · 环形历史
· **中间件 · 历史查询 · 计数**。

## 序号唯一权威（架构 §4.2）

草稿**不含序号**；总线在封装信封时分配**全局单调**序号。序号是事件溯源的
唯一时间轴（架构 §5.1）——`reset` 之后帧号会重复，序号不会。

## 异常隔离（FR-3.4）

订阅者抛错 → 转 `kernel:error`（负载四字段：`subscriber` 订阅者名 /
`eventType` 原事件类型 / `eventSeq` **原事件的全局序号** / `error` 错误摘要），
**不中断内核、不影响其它订阅者**。

**错误事件自身不再产生错误事件**——否则"必然抛错"的订阅者会让总线陷入
无限循环。

**错误级联也要防（ADR-083）**：错误事件本身**会被别的订阅者变成新事件**——
日志系统把错误写成 `log:entry`，那条新事件又落回抛错订阅者手里 → 又产生
错误事件 → **无限级联**（实测：事件数无上界，宿主卡死）。所以**同一个订阅者
在一轮排空里只报一次错**，再炸只记 trace。

## 待发布队列（架构 §5.1）

分发期间产生的自产事件（`log:entry` / `metric:sampled` /
`achievement:unlocked`）**延后到分发结束后 FIFO 发出**；序号在**入队时**
分配，因此"序号顺序 == 分发顺序"恒成立。

## 中间件（M2-S5）

**横切处理**：分发链上、分发给订阅者**之前**跑一遍。中间件**只看事件、
不改事件**——信封是冻结的（`frozen=True`），负载是元组，改不动。

中间件抛错 → **与订阅者同款隔离**（转 `kernel:error` · 其余订阅者不受
影响 · 错误事件照常发出）。

## 历史查询与计数（M2-S5）

- **历史查询**（`query_history`）：按订阅模式的写法过滤环形历史。**不做
  按全局序号的随机跳转**（spec §11.2 K1——历史是环形缓冲，随机访问需要
  一套索引，M2 不做）。
- **计数**（`counts`）：**累计**按事件类型 / 按命名空间的条数。它是独立
  累加器，**不靠环形历史算**——历史满了会忘，计数不会。

## 历史缓冲的开关（降级第 ③ 档 · ADR-031）

`set_history_enabled(False)` **只关缓冲，不关分发**——订阅者照常收到每一条
事件，**状态变更仍伴随事件**。ADR-031 明写否决"引入关闭事件分发的快速模式"：
那会破坏**事件完备性**。

**计数不受影响**：`counts()` 是**独立累加器**（历史满了会忘，计数不会）。

## 不提供 `bus.view()`（ADR-021）

只读视图**只**走系统层入口的 `ctx.view()`——总线上的访问器可以在任何时候
被调用，那时内核处于什么状态没有定义（架构 §4.3）。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .envelope import Draft, Envelope, Metadata, build_metadata, producer_of
from .subscriber import (
    HistoryQuery,
    SingleHandler,
    SubscriberRegistry,
    Subscription,
    SystemContext,
    SystemHandler,
    default_name,
)

#: 订阅者异常隔离用的事件类型（契约已登记，产出者 = 总线）。
ERROR_EVENT_TYPE = "kernel:error"

#: 环形历史默认容量（最近 N 条；FR-3.6 的 S 级需求，M0 只做最小版）。
DEFAULT_HISTORY_LIMIT = 1024

#: 轨迹（trace）默认保留条数——静默丢弃与二次抛错都记在这里。
DEFAULT_TRACE_LIMIT = 64

#: 中间件：分发链上的横切处理（**只看事件，不改内容**）。
type Middleware = Callable[[Envelope], None]


@dataclass(frozen=True)
class EventCounts:
    """事件计数：**累计**按类型 / 按命名空间的条数。

    **它不是环形历史的长度**——历史满了会忘（最旧的被挤出去），计数不会。
    指标系统与"事件完备性"判据要的就是这个：**发出去过多少条**。

    - `by_type`：事件类型名 → 条数；
    - `by_namespace`：命名空间（事件类型名里 `:` 前的部分）→ 条数。
    """

    by_type: Mapping[str, int]
    by_namespace: Mapping[str, int]

    @property
    def total(self) -> int:
        """所有事件的条数（**不分公司**）。"""
        return sum(self.by_type.values())

    def count_of(self, event_type: str) -> int:
        """某个事件类型的条数（**没有就是 0**）。"""
        return self.by_type.get(event_type, 0)


class EventBus[V]:
    """事件总线：事件的传输与分发中枢。

    **序号唯一权威**（架构 §4.2）：`publish` 分配全局单调序号，草稿里没有
    序号。

    **两个入口共享一张注册表**（ADR-057）：`subscribe`（单参数，给适配器与
    渲染器）与 `subscribe_system`（双参数，给系统层）写进同一张表，排序在
    合并之后进行。

    **不提供 `view()`**（ADR-021）：视图只走 `ctx.view()`。
    """

    def __init__(
        self,
        *,
        view_provider: Callable[[], V] | None = None,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        default_source: str = "",
    ) -> None:
        """建总线。

        - `view_provider`：宿主注入的视图来源（内核快照，ADR-056）。**不注入
          则 `ctx.view()` 不可用**（当场报错，不给来路不明的状态）；
        - `history_limit`：环形历史保留条数（最近 N 条）；
        - `default_source`：草稿没带 `source` 时用的来源（空串 = 无上游命令）。
        """
        if history_limit < 1:
            raise ValueError(f"history_limit 至少为 1，实为 {history_limit}")
        self._view_provider = view_provider
        self._default_source = default_source
        self._registry: SubscriberRegistry[V] = SubscriberRegistry[V]()
        self._history: deque[Envelope] = deque(maxlen=history_limit)
        # **历史缓冲的开关**（降级第 ③ 档 · ADR-031）——关的是**缓冲**，不是分发。
        self._history_enabled = True
        self._pending: deque[Envelope] = deque()
        self._traces: deque[str] = deque(maxlen=DEFAULT_TRACE_LIMIT)
        self._middlewares: list[tuple[str, Middleware]] = []
        self._counts_by_type: dict[str, int] = {}
        self._counts_by_namespace: dict[str, int] = {}
        # **这一轮排空里已经报过错事件的订阅者**（防错误级联 · ADR-083）。
        self._errored_subscribers: set[str] = set()
        self._seq = 0
        self._dispatching = False

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------

    def subscribe(
        self,
        pattern: str,
        callback: SingleHandler,
        priority: int = 0,
        *,
        name: str | None = None,
    ) -> Subscription[V]:
        """**单参数**订阅：回调只拿事件，**拿不到视图**（架构 §4.3 / ADR-059）。

        给适配器与渲染器用。`pattern` 三选一：精确（`piece:locked`）· 命名
        空间通配（`piece:*`）· 全局（`*`）。

        `priority` **数值大者先收到**（默认 0）；同优先级按订阅先后。
        `name` 省略时取回调的 `__qualname__`——它进 `kernel:error` 的负载。
        """
        subscriber_name = default_name(callback) if name is None else name

        def handler(envelope: Envelope, _ctx: SystemContext[V]) -> None:
            callback(envelope)

        return self._registry.add(pattern, handler, priority, subscriber_name)

    def subscribe_system(
        self,
        pattern: str,
        callback: SystemHandler[V],
        priority: int = 0,
        *,
        name: str | None = None,
    ) -> Subscription[V]:
        """**双参数**订阅：回调拿事件 **+ 上下文**（`ctx.view()` 取只读视图）。

        给系统层用（架构 §4.3）。与 `subscribe` **共用同一张注册表与优先级
        序列**，排序在合并之后进行（ADR-057）。
        """
        subscriber_name = default_name(callback) if name is None else name
        return self._registry.add(pattern, callback, priority, subscriber_name)

    # ------------------------------------------------------------------
    # 中间件
    # ------------------------------------------------------------------

    def use(self, middleware: Middleware, *, name: str | None = None) -> str:
        """挂一个中间件（**分发链上、分发给订阅者之前**）。

        中间件是**横切处理**：它拿到的事件与订阅者拿到的是**同一个信封**，
        因此**改不动内容**（信封冻结、负载是元组）。

        `name` 省略时取回调的 `__qualname__`——它会进 `kernel:error` 的负载
        （中间件抛错时，负载里的 `subscriber` 字段放的是**抛错者的名字**）。

        返回中间件名（报错时好指认是谁炸了）。
        """
        middleware_name = default_name(middleware) if name is None else name
        self._middlewares.append((middleware_name, middleware))
        return middleware_name

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------

    def publish(self, draft: Draft) -> None:
        """发布一条事件草稿（**总线分配全局序号**）。

        草稿**不含序号**（架构 §4.2）。未登记的事件类型**静默丢弃并记
        trace**，不中断分发（架构 §5.1「未知事件容错」）。

        分发期间发布的事件进**待发布队列**，当前分发结束后 FIFO 发出。
        """
        envelope = self._envelope_of(draft)
        if envelope is None:
            return
        if self._dispatching:
            self._pending.append(envelope)
            return
        self._dispatch(envelope)

    def _envelope_of(self, draft: Draft) -> Envelope | None:
        """草稿 → 信封（**在这里分配序号**）；未登记的事件类型返回 `None`。"""
        producer = producer_of(draft.eventType)
        if producer is None:
            self._trace(f"未登记的事件类型 {draft.eventType!r}：已丢弃，不中断分发")
            return None
        return Envelope(
            seq=self._next_seq(),
            frame=draft.frame,
            type=draft.eventType,
            payload=draft.payload,
            metadata=build_metadata(draft.metadata, producer, self._default_source),
        )

    def _next_seq(self) -> int:
        """取下一个全局序号（**单调递增，从 1 开始**）。"""
        self._seq += 1
        return self._seq

    # ------------------------------------------------------------------
    # 分发
    # ------------------------------------------------------------------

    def _dispatch(self, envelope: Envelope) -> None:
        """分发一条事件，然后**排空待发布队列**。"""
        self._dispatching = True
        try:
            self._deliver(envelope)
        finally:
            self._dispatching = False
        self._drain_pending()

    def _drain_pending(self) -> None:
        """把待发布队列按 FIFO 逐条发出（分发期间产生的自产事件）。

        **一轮排空结束时清零"已报错订阅者"**——错误级联的边界就是**这一轮**
        （ADR-083）。
        """
        try:
            while self._pending:
                envelope = self._pending.popleft()
                self._dispatching = True
                try:
                    self._deliver(envelope)
                finally:
                    self._dispatching = False
        finally:
            self._errored_subscribers.clear()

    def _deliver(self, envelope: Envelope) -> None:
        """把一条事件交给**中间件**与**全部命中**的订阅者（优先级已排好）。

        一次分发只建**一个** `ctx`：所有订阅者拿到的是**同一份**视图
        （架构 §5.2 的"按发布点缓存"）。

        **中间件先跑**（分发链上、分发给订阅者之前）；**中间件抛错不影响
        订阅者**——横切处理坏了，最多是它自己缺席。
        """
        if self._history_enabled:
            self._history.append(envelope)
        self._count(envelope)
        ctx: SystemContext[V] = SystemContext[V](self._view_provider)
        is_error_event = envelope.type == ERROR_EVENT_TYPE
        self._run_middlewares(envelope, is_error_event)
        for subscription in self._registry.matching(envelope.type):
            try:
                subscription.handler(envelope, ctx)
            except Exception as error:
                # 异常隔离是本模块的职责：这里**必须**兜住订阅者的异常。
                self._handle_failure(subscription, envelope, error, is_error_event)

    def _run_middlewares(self, envelope: Envelope, is_error_event: bool) -> None:
        """跑一遍中间件（**按挂载顺序**）。

        **一个中间件抛错不影响后面的中间件，也不影响订阅者**——与订阅者
        同款隔离。
        """
        for name, middleware in self._middlewares:
            try:
                middleware(envelope)
            except Exception as error:
                self._report_failure("中间件", name, envelope, error, is_error_event)

    def _count(self, envelope: Envelope) -> None:
        """累加计数（**按类型 · 按命名空间**）。

        计数的是**进总线的事件**——被丢弃的未登记事件类型**不算**（它没
        进门）；错误事件算（它确实发出去了）。
        """
        event_type = envelope.type
        self._counts_by_type[event_type] = self._counts_by_type.get(event_type, 0) + 1
        namespace = event_type.split(":", 1)[0]
        self._counts_by_namespace[namespace] = self._counts_by_namespace.get(namespace, 0) + 1

    def _handle_failure(
        self,
        subscription: Subscription[V],
        envelope: Envelope,
        error: Exception,
        is_error_event: bool,
    ) -> None:
        """订阅者抛错 → 转 `kernel:error`；**其余订阅者不受影响**。"""
        self._report_failure("订阅者", subscription.name, envelope, error, is_error_event)

    def _report_failure(
        self,
        role: str,
        name: str,
        envelope: Envelope,
        error: Exception,
        is_error_event: bool,
    ) -> None:
        """抛错 → 转 `kernel:error`；**其余订阅者不受影响**。

        `role` 只是报告里的称呼（订阅者 / 中间件）；负载里放的是**抛错者
        的名字**——契约里那个字段叫 `subscriber`，**中间件也在分发链上，
        同样要能指认是谁炸了**（不改契约面：M2-D4 只增不改）。
        """
        summary = f"{type(error).__name__}: {error}"
        if is_error_event:
            # 错误事件自身不再产生错误事件——否则必然抛错的订阅者会让
            # 总线陷入无限循环。
            self._trace(f"{role} {name} 处理错误事件时再次抛错：{summary}")
            return
        if name in self._errored_subscribers:
            # **同一个订阅者在这一轮排空里只报一次错**（ADR-083）。
            #
            # **为什么**：错误事件本身会被**别的订阅者**变成新事件（日志系统
            # 把错误写成 `log:entry`）——那个新事件又落回抛错订阅者手里 → 又
            # 产生错误事件 → **无限级联**（实测：事件数无上界，宿主卡死）。
            # 所以：**已经报过错事件的订阅者，再炸只记 trace**。
            self._trace(f"{role} {name} 在错误级联里再次抛错：{summary}")
            return
        self._queue_error(name, envelope, summary)

    def _queue_error(
        self,
        name: str,
        envelope: Envelope,
        summary: str,
    ) -> None:
        """把 `kernel:error` 排进待发布队列（**不打断当前分发**）。"""
        # **记下这个订阅者**：这一轮排空里它再炸就只记 trace（ADR-083 的
        # 错误级联防线）。
        self._errored_subscribers.add(name)
        producer = producer_of(ERROR_EVENT_TYPE) or "bus"
        metadata: Metadata = build_metadata((), producer, self._default_source)
        payload = (
            ("subscriber", name),
            ("eventType", envelope.type),
            ("eventSeq", envelope.seq),
            ("error", summary),
        )
        self._pending.append(
            Envelope(
                seq=self._next_seq(),
                frame=envelope.frame,
                type=ERROR_EVENT_TYPE,
                payload=payload,
                metadata=metadata,
            )
        )

    # ------------------------------------------------------------------
    # 观测
    # ------------------------------------------------------------------

    def history(self) -> tuple[Envelope, ...]:
        """环形历史里的最近 N 条（**由旧到新**）。

        **按条件检索走 `query_history`**（M2-S5）；**按全局序号随机跳转
        不做**（spec §11.2 K1——历史是环形缓冲，随机访问需要一套索引）。
        """
        return tuple(self._history)

    def set_history_enabled(self, enabled: bool) -> None:
        """**开 / 关环形历史缓冲**（**降级第 ③ 档** · ADR-031）。

        **关的是缓冲，不是分发**（ADR-031 明写否决"关闭事件分发"）：订阅者照常
        收到每一条事件，**状态变更仍伴随事件**——**事件完备性不受损**。

        **计数不受影响**：`counts()` 是**独立累加器**（历史满了会忘，计数不会）
        ——指标系统聚合的是**事件**，不靠历史算。

        **关掉时缓冲一并清空**：这一档的收益是省下每条事件的追加开销与内存占用
        ——留着旧条目既占内存，又不给新证据。重新打开时缓冲从空开始。
        """
        if self._history_enabled == enabled:
            return
        self._history_enabled = enabled
        if not enabled:
            self._history.clear()

    def query_history(self, query: HistoryQuery | None = None) -> tuple[Envelope, ...]:
        """按条件检索环形历史（**由旧到新**）。

        `query` 省略时等价于 `history()`。过滤**复用订阅模式的那套写法**
        （精确 · `命名空间:*` · `*`），帧号区间与条数上限见 `HistoryQuery`。

        **不做按全局序号的随机跳转**（spec §11.2 K1）——要"跳到第 N 条"
        得有一套索引；环形缓冲丢过最旧的，按序号跳过去未必还找得到。
        """
        if query is None:
            return tuple(self._history)
        matched = [envelope for envelope in self._history if query.accepts(envelope)]
        limit = query.limit
        if limit is not None:
            if limit == 0:
                return ()
            if len(matched) > limit:
                # 取**最近** N 条。注意 `limit == 0` 时 `matched[-0:]` 等于
                # 整段（`-0` 就是 `0`）——上面先把它拦掉，这里才敢切片。
                matched = matched[-limit:]
        return tuple(matched)

    def counts(self) -> EventCounts:
        """事件计数（**累计** · 按类型 / 按命名空间）。

        它是**独立累加器**，不靠环形历史算——历史满了会忘，计数不会。
        """
        by_type: Mapping[str, int] = MappingProxyType(dict(self._counts_by_type))
        by_namespace: Mapping[str, int] = MappingProxyType(dict(self._counts_by_namespace))
        return EventCounts(by_type=by_type, by_namespace=by_namespace)

    def traces(self) -> tuple[str, ...]:
        """最近的轨迹记录（静默丢弃、二次抛错等）。"""
        return tuple(self._traces)

    @property
    def history_enabled(self) -> bool:
        """环形历史缓冲**开着没有**（降级第 ③ 档会把它关掉）。"""
        return self._history_enabled

    @property
    def last_seq(self) -> int:
        """已分配的最大序号（`0` = 还没发过事件）。"""
        return self._seq

    @property
    def pending_count(self) -> int:
        """待发布队列长度（**只在分发期间非零**）。"""
        return len(self._pending)

    @property
    def subscriber_count(self) -> int:
        """当前订阅数（两个入口合计）。"""
        return len(self._registry)

    def _trace(self, message: str) -> None:
        """记一条轨迹。"""
        self._traces.append(message)


__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "DEFAULT_TRACE_LIMIT",
    "ERROR_EVENT_TYPE",
    "EventBus",
    "EventCounts",
    "Middleware",
]
