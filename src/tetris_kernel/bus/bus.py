"""事件总线（总线层 · S11）。

**序号唯一权威** · 三种订阅 · 两个入口 · 异常隔离 · 待发布队列 · 环形历史。

## 序号唯一权威（架构 §4.2）

草稿**不含序号**；总线在封装信封时分配**全局单调**序号。序号是事件溯源的
唯一时间轴（架构 §5.1）——`reset` 之后帧号会重复，序号不会。

## 异常隔离（FR-3.4）

订阅者抛错 → 转 `kernel:error`（负载四字段：`subscriber` 订阅者名 /
`eventType` 原事件类型 / `eventSeq` **原事件的全局序号** / `error` 错误摘要），
**不中断内核、不影响其它订阅者**。

**错误事件自身不再产生错误事件**——否则"必然抛错"的订阅者会让总线陷入
无限循环。

## 待发布队列（架构 §5.1）

分发期间产生的自产事件（`log:entry` / `metric:sampled` /
`achievement:unlocked`）**延后到分发结束后 FIFO 发出**；序号在**入队时**
分配，因此"序号顺序 == 分发顺序"恒成立。

## 不提供 `bus.view()`（ADR-021）

只读视图**只**走系统层入口的 `ctx.view()`——总线上的访问器可以在任何时候
被调用，那时内核处于什么状态没有定义（架构 §4.3）。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from .envelope import Draft, Envelope, Metadata, build_metadata, producer_of
from .subscriber import (
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
        self._pending: deque[Envelope] = deque()
        self._traces: deque[str] = deque(maxlen=DEFAULT_TRACE_LIMIT)
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
        """把待发布队列按 FIFO 逐条发出（分发期间产生的自产事件）。"""
        while self._pending:
            envelope = self._pending.popleft()
            self._dispatching = True
            try:
                self._deliver(envelope)
            finally:
                self._dispatching = False

    def _deliver(self, envelope: Envelope) -> None:
        """把一条事件交给**全部命中**的订阅者（优先级已排好）。

        一次分发只建**一个** `ctx`：所有订阅者拿到的是**同一份**视图
        （架构 §5.2 的"按发布点缓存"）。
        """
        self._history.append(envelope)
        ctx: SystemContext[V] = SystemContext[V](self._view_provider)
        is_error_event = envelope.type == ERROR_EVENT_TYPE
        for subscription in self._registry.matching(envelope.type):
            try:
                subscription.handler(envelope, ctx)
            except Exception as error:
                # 异常隔离是本模块的职责：这里**必须**兜住订阅者的异常。
                self._handle_failure(subscription, envelope, error, is_error_event)

    def _handle_failure(
        self,
        subscription: Subscription[V],
        envelope: Envelope,
        error: Exception,
        is_error_event: bool,
    ) -> None:
        """订阅者抛错 → 转 `kernel:error`；**其余订阅者不受影响**。"""
        summary = f"{type(error).__name__}: {error}"
        if is_error_event:
            # 错误事件自身不再产生错误事件——否则必然抛错的订阅者会造成
            # 无限循环。
            self._trace(f"订阅者 {subscription.name} 处理错误事件时再次抛错：{summary}")
            return
        self._queue_error(subscription, envelope, summary)

    def _queue_error(
        self,
        subscription: Subscription[V],
        envelope: Envelope,
        summary: str,
    ) -> None:
        """把 `kernel:error` 排进待发布队列（**不打断当前分发**）。"""
        producer = producer_of(ERROR_EVENT_TYPE) or "bus"
        metadata: Metadata = build_metadata((), producer, self._default_source)
        payload = (
            ("subscriber", subscription.name),
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

        区间查询与时间旅行留 M2（FR-3.6 是 S 级需求）。
        """
        return tuple(self._history)

    def traces(self) -> tuple[str, ...]:
        """最近的轨迹记录（静默丢弃、二次抛错等）。"""
        return tuple(self._traces)

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
]
