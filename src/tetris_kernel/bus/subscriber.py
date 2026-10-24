"""订阅模式、优先级与注册表（总线层 · S11）。

## 三种订阅模式（FR-3.1）

| 模式 | 写法 | 命中 |
| --- | --- | --- |
| 精确 | `piece:locked` | 只有这一个事件类型 |
| 命名空间通配 | `piece:*` | 该前缀下的全部事件类型 |
| 全局 | `*` | 全部事件类型 |

通配是**前缀匹配**：`piece:*` 命中 `piece:spawned` / `piece:locked` …；
`kernel:system:*` 命中 `kernel:system:installed` / `kernel:system:removed`。

## 两个入口共享一张表（ADR-057）

`subscribe`（**单参数**，给适配器与渲染器）与 `subscribe_system`（**双参数**，
给系统层）写进**同一张注册表**，**排序在合并之后进行**——否则"业务订阅者先于
日志订阅者"（FR-3.3）在跨入口时不成立。

## 优先级

**数值大者先收到**（默认 `0`）；同优先级按订阅先后（FIFO）。优先级序列是
**一张表一条序列**，两个入口共用。

## 视图只走上下文（ADR-021 / ADR-059）

只读视图**只**通过双参数入口的 `ctx.view()` 取得；**总线不提供
`bus.view()`**——访问器可以在任何时候被调用，那时内核处于什么状态没有定义
（架构 §4.3）。渲染器走单参数入口，**拿不到视图**。
"""

from __future__ import annotations

from collections.abc import Callable

from .envelope import Envelope


def matches(pattern: str, event_type: str) -> bool:
    """模式是否命中该事件类型（精确 / 命名空间通配 / 全局）。

    通配按**前缀**判定：`piece:*` 去掉末尾的 `*` 后与事件类型做前缀比较。
    """
    if pattern == "*":
        return True
    if pattern.endswith(":*"):
        return event_type.startswith(pattern[:-1])
    return pattern == event_type


def default_name(callback: object) -> str:
    """订阅者的默认名：回调的 `__qualname__`，取不到就用模式兜底。

    **名字进 `kernel:error` 的负载**（契约字段 `subscriber`）——报错时得说得
    清是哪个订阅者炸了。
    """
    qualname = getattr(callback, "__qualname__", None)
    if isinstance(qualname, str) and qualname:
        return qualname
    return repr(callback)


class SystemContext[V]:
    """系统层订阅者的上下文：**只有 `view()` 一个出口**。

    `view()` **惰性取视图、按发布点缓存**（架构 §5.2）：同一次发布里所有
    订阅者拿到**同一份**对象——"分发期间内核状态不变"因此是结构性事实。
    """

    def __init__(self, provider: Callable[[], V] | None) -> None:
        self._provider = provider
        self._cached: V | None = None
        self._loaded = False

    def view(self) -> V:
        """取本次发布的只读视图（**惰性 + 按发布点缓存**）。

        视图由宿主注入（`view_provider`）；没注入就当场报错——**宁可报错，
        也不给一份来路不明的状态**。
        """
        if not self._loaded:
            provider = self._provider
            if provider is None:
                raise RuntimeError("总线未注入视图提供者：ctx.view() 不可用")
            self._cached = provider()
            self._loaded = True
        cached = self._cached
        if cached is None:
            raise RuntimeError("视图提供者返回了 None")
        return cached


#: 单参数订阅者：只拿事件，**无视图**（架构 §4.3）。
type SingleHandler = Callable[[Envelope], None]

#: 系统层订阅者：事件 + 上下文（`ctx.view()` 取只读视图）。
type SystemHandler[V] = Callable[[Envelope, SystemContext[V]], None]


class Subscription[V]:
    """一个订阅的句柄：模式 · 优先级 · 回调，可注销。

    句柄是 ADR-063 要求的：插件卸载时校验该系统的订阅**全部注销**。
    """

    def __init__(
        self,
        registry: SubscriberRegistry[V],
        name: str,
        pattern: str,
        priority: int,
        order: int,
        handler: SystemHandler[V],
    ) -> None:
        self._registry = registry
        self._name = name
        self._pattern = pattern
        self._priority = priority
        self._order = order
        self._handler = handler

    @property
    def name(self) -> str:
        """订阅者名（进 `kernel:error` 的负载）。"""
        return self._name

    @property
    def pattern(self) -> str:
        """订阅模式。"""
        return self._pattern

    @property
    def priority(self) -> int:
        """优先级（数值大者先收到）。"""
        return self._priority

    @property
    def order(self) -> int:
        """注册序号（同优先级时按它 FIFO）。"""
        return self._order

    @property
    def handler(self) -> SystemHandler[V]:
        """回调。

        **两个入口在这里归一**：单参数入口的回调已被包装成"事件 + 上下文"
        形式（上下文对它不可见），分发时只有一条调用路径。
        """
        return self._handler

    @property
    def active(self) -> bool:
        """是否仍在注册表里（注销后为 `False`）。"""
        return self._registry.contains(self)

    def unsubscribe(self) -> None:
        """注销本订阅（**幂等**）。"""
        self._registry.remove(self)


class SubscriberRegistry[V]:
    """订阅者注册表：**两个入口共用一张表**（ADR-057）。

    注册表**只存一份**、**只排一次序**——排序发生在"两个入口的订阅合并
    之后"，跨入口的优先级才成立（ADR-057 的原话）。
    """

    def __init__(self) -> None:
        self._subscriptions: list[Subscription[V]] = []
        self._next_order = 0

    def add(
        self,
        pattern: str,
        handler: SystemHandler[V],
        priority: int,
        name: str,
    ) -> Subscription[V]:
        """登记一个订阅，返回句柄。"""
        subscription = Subscription[V](
            self,
            name,
            pattern,
            priority,
            self._next_order,
            handler,
        )
        self._next_order += 1
        self._subscriptions.append(subscription)
        return subscription

    def remove(self, subscription: Subscription[V]) -> None:
        """注销一个订阅（**幂等**）。"""
        self._subscriptions = [item for item in self._subscriptions if item is not subscription]

    def contains(self, subscription: Subscription[V]) -> bool:
        """该订阅是否仍在表里。"""
        return any(item is subscription for item in self._subscriptions)

    def matching(self, event_type: str) -> tuple[Subscription[V], ...]:
        """命中该事件类型的订阅，**按优先级排序**（合并之后才排）。

        排序键 = `(-优先级, 注册序号)`：优先级大者先，同优先级按订阅先后。
        """
        hits = [item for item in self._subscriptions if matches(item.pattern, event_type)]
        hits.sort(key=lambda item: (-item.priority, item.order))
        return tuple(hits)

    def __len__(self) -> int:
        """当前订阅数。"""
        return len(self._subscriptions)


__all__ = [
    "SingleHandler",
    "SubscriberRegistry",
    "Subscription",
    "SystemContext",
    "SystemHandler",
    "default_name",
    "matches",
]
