"""日志系统：普通订阅者，输出结构化日志条目。

级别映射放在本系统内——事件不自带级别（ADR-046）。

**只消费事件，不写状态**（M2-D1）：它连只读视图都不取（单参数订阅入口）。
**落盘经 sink**（M2-D2 · ADR-064）：它**知道有 sink，不知道路径**。
"""

from .entry import (
    LEVEL_RANKS,
    LEVELS,
    ChannelMap,
    LevelMap,
    LogEntry,
    Scalar,
    level_rank,
    resolve_channels,
    resolve_level,
)
from .system import DEFAULT_LEVEL_MAP, LOG_ENTRY_EVENT, LogSystem
from .targets import ConsoleTarget, LogTarget, MemoryTarget, Sink, SinkTarget, format_entry

__all__ = [
    "DEFAULT_LEVEL_MAP",
    "LEVELS",
    "LEVEL_RANKS",
    "LOG_ENTRY_EVENT",
    "ChannelMap",
    "ConsoleTarget",
    "LevelMap",
    "LogEntry",
    "LogSystem",
    "LogTarget",
    "MemoryTarget",
    "Scalar",
    "Sink",
    "SinkTarget",
    "format_entry",
    "level_rank",
    "resolve_channels",
    "resolve_level",
]
