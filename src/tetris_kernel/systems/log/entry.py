"""日志条目与级别映射（系统层 · 日志系统）。

## 级别是**映射出来的**，不是事件自带的（ADR-046）

事件**不携带级别**——"同一事件排障时是 trace、演示时可能是 info"（ADR-046 的
原话）。级别由**日志系统按事件类型映射**得出，映射表是**系统的配置**。

查找顺序（**先精确、后命名空间**）：

1. 事件类型全名（如 `piece:locked`）；
2. 命名空间（如 `piece`）；
3. 兜底级别（构造参数 `default_level`）。

**为什么精确优先于命名空间**：同一个命名空间里的事件常常不是一个量级——
`piece:spawned` 是 `debug`，`piece:hold-denied` 可能是 `warn`。先查命名空间
会让精确映射永远轮不到。

## 频道

频道是**路由用的标签**（"这条日志给谁看"）。默认每个事件进**自己命名空间**
那一个频道；配置可以让一个事件进**多个频道**（如 `board:lines-cleared` 同时
进 `board` 与 `score`）——**一个频道一条条目**（契约的 `log:entry` 只有一个
`channel` 字段）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

#: 扁平 JSON 标量（契约 `SCALAR_MAP` 的取值域）。
type Scalar = int | float | str | bool | None

#: 日志级别（**由映射得出**，事件本身不带 · ADR-046）。
LEVELS: tuple[str, ...] = ("trace", "debug", "info", "warn", "error")

#: 级别的大小序（**小的更轻**）——用来跟阈值比。
LEVEL_RANKS: Mapping[str, int] = MappingProxyType(
    {name: index for index, name in enumerate(LEVELS)}
)

#: 级别映射的键：事件类型全名或命名空间；值：级别。
type LevelMap = Mapping[str, str]

#: 频道映射的键：事件类型全名或命名空间；值：频道名清单。
type ChannelMap = Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class LogEntry:
    """一条结构化日志条目（**四个字段与契约 `log:entry` 逐条相等**）。

    - `level`：级别（映射出来的）；
    - `channel`：频道（路由用的标签）；
    - `message`：消息（默认是**事件类型名**）；
    - `data`：附加数据（**扁平 JSON 标量**，不可变）。

    **不可变**：条目会被交给多个输出目标，谁都不许就地改它。
    """

    level: str
    channel: str
    message: str
    data: tuple[tuple[str, Scalar], ...] = ()


def level_rank(level: str) -> int:
    """级别的大小序（**未知级别当场报错**——不糊弄过去）。"""
    rank = LEVEL_RANKS.get(level)
    if rank is None:
        raise ValueError(f"未知日志级别：{level!r}（合法值：{list(LEVELS)}）")
    return rank


def resolve_level(event_type: str, level_map: LevelMap, default_level: str) -> str:
    """按事件类型查级别（**精确 → 命名空间 → 兜底**）。"""
    direct = level_map.get(event_type)
    if direct is not None:
        return direct
    namespace = event_type.split(":", 1)[0]
    by_namespace = level_map.get(namespace)
    if by_namespace is not None:
        return by_namespace
    return default_level


def resolve_channels(event_type: str, channel_map: ChannelMap) -> tuple[str, ...]:
    """按事件类型查频道（**精确 → 命名空间 → 自己的命名空间**）。

    默认落在**自己命名空间**那一个频道——"按命名空间过滤"是契约给的能力
    （FR-3.2），默认就该对得上。
    """
    direct = channel_map.get(event_type)
    if direct is not None:
        return direct
    namespace = event_type.split(":", 1)[0]
    by_namespace = channel_map.get(namespace)
    if by_namespace is not None:
        return by_namespace
    return (namespace,)


__all__ = [
    "LEVELS",
    "LEVEL_RANKS",
    "ChannelMap",
    "LevelMap",
    "LogEntry",
    "Scalar",
    "level_rank",
    "resolve_channels",
    "resolve_level",
]
