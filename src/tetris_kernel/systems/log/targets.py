"""日志的输出目标（**可插拔** · 控制台 · 文件 · 内存）。

## 三个目标各写各的格式，但吃的是同一条条目

- **控制台**（`ConsoleTarget`）：人读的一行；
- **文件**（`SinkTarget`）：**经注入的 sink** 写 **JSON 行**（一行一条 · 机器
  可读 · 可 diff）——**部件不自己 `open()`、不知道路径**（M2-D2 · ADR-064）；
- **内存**（`MemoryTarget`）：条目留在列表里（面板与测试都用它）。

## 频道过滤在目标这一侧

目标可以只收**某几个频道**的条目（`channels`）——"这份输出给谁看"是**输出
目标的事**，不是日志系统的事。`channels=None` = 收全部频道。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import TextIO

from .entry import LogEntry

#: 落盘出口（**宿主注入的可调用对象** · ADR-064）。
#:
#: **为什么在这里再写一遍别名**：`systems/` **不能** import `host/`（依赖方向），
#: 所以拿不到 `host.assembly.Sink`。它是**同一个可调用类型**（结构化类型），
#: 这里自己写一份即可——**别名不是契约，不进基线**。
type Sink = Callable[[bytes], None]


class LogTarget:
    """输出目标基类：**吃一条条目**。

    **可插拔**：新增一种输出就是新增一个子类，**日志系统不用改**。
    """

    def __init__(self, name: str, *, channels: frozenset[str] | None = None) -> None:
        """`channels`：只收这几个频道（`None` = 全部）。"""
        self._name = name
        self._channels = channels

    @property
    def name(self) -> str:
        """目标名（报告与排查时好指认是谁写的）。"""
        return self._name

    @property
    def channels(self) -> frozenset[str] | None:
        """只收这几个频道（`None` = 全部频道）。"""
        return self._channels

    def accepts(self, entry: LogEntry) -> bool:
        """这条条目要不要交给本目标（**频道过滤**）。"""
        if self._channels is None:
            return True
        return entry.channel in self._channels

    def write(self, entry: LogEntry) -> None:
        """写一条条目（**子类实现**）。"""
        raise NotImplementedError


class ConsoleTarget(LogTarget):
    """控制台目标：**人读的一行**。"""

    def __init__(
        self,
        name: str = "console",
        *,
        channels: frozenset[str] | None = None,
        stream: TextIO | None = None,
    ) -> None:
        """`stream` 省略时用 `sys.stdout`（**测试可以注入自己的流**）。"""
        super().__init__(name, channels=channels)
        self._stream = stream

    def write(self, entry: LogEntry) -> None:
        stream = self._stream if self._stream is not None else sys.stdout
        stream.write(format_entry(entry) + "\n")


class SinkTarget(LogTarget):
    """文件目标：**经注入的 sink** 写 JSON 行。

    **部件不自己 `open()`、不用 `Path.home()`**（M2-D2 · ADR-064）——路径是
    **宿主**的事，这个目标只认一个"把字节交出去"的可调用对象。
    """

    def __init__(
        self,
        sink: Sink,
        name: str = "file",
        *,
        channels: frozenset[str] | None = None,
    ) -> None:
        super().__init__(name, channels=channels)
        self._sink = sink

    def write(self, entry: LogEntry) -> None:
        line = json.dumps(
            {
                "level": entry.level,
                "channel": entry.channel,
                "message": entry.message,
                "data": dict(entry.data),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        self._sink((line + "\n").encode("utf-8"))


class MemoryTarget(LogTarget):
    """内存目标：**条目留在列表里**（面板与测试都用它）。"""

    def __init__(self, name: str = "memory", *, channels: frozenset[str] | None = None) -> None:
        super().__init__(name, channels=channels)
        self._entries: list[LogEntry] = []

    @property
    def entries(self) -> tuple[LogEntry, ...]:
        """已收到的条目（**由旧到新**）。"""
        return tuple(self._entries)

    def write(self, entry: LogEntry) -> None:
        self._entries.append(entry)


def format_entry(entry: LogEntry) -> str:
    """人读的一行：`级别 频道 消息 数据`。

    **数据按字段名排序**——同一批条目的渲染逐字可比（测试与 diff 都靠它）。
    """
    data = " ".join(f"{key}={value}" for key, value in sorted(entry.data))
    head = f"{entry.level.upper():<5} [{entry.channel}] {entry.message}"
    return f"{head} {data}".rstrip()


__all__ = [
    "ConsoleTarget",
    "LogTarget",
    "MemoryTarget",
    "Sink",
    "SinkTarget",
    "format_entry",
]
