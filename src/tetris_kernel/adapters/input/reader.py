"""跨平台的**按键读取**（适配层 · M4-S7 · FR-8.3 · spec-M4 §7.7）。

## 这一层只干一件事：**把平台按键变成"按下 / 抬起"**

**它不认识玩法**——**不认识"左移"是什么命令**（那是 `keyboard.py` 的映射表）。
**它只说"某个键按下了 / 松开了"**。

## 为什么不用第三方库

**不引 `keyboard` / `pynput`**——**要编译 · 要权限 · 跨平台行为不一致**。
**标准库 + 平台分支**够用（环境纪律：少一个依赖没收益）。

## 两个平台的现实差别（**说在前面，别当成实现细节**）

| 平台 | 抬起（key-up）怎么知道 | 后果 |
| --- | --- | --- |
| **Windows** | **`GetAsyncKeyState`**（`ctypes`） | **抬起精确** → DAS / ARR 我们说了算 |
| **POSIX** | **终端没有按键状态 API** → **靠"重复流停了"推断** | **抬起是推断的** |

**POSIX 的代价说清楚**：终端**自己会重复**按住不放的键（xterm 默认首轮延迟
~500ms、之后 ~33ms 一次）→ **我们收到的是一串重复的"按下"**。

- **我们自己的 DAS / ARR 计时器照旧驱动移动**（**它才是权威**）→ 终端发来的
  重复**被当成"这个键还按着"**（**不是新的按下**）；
- **抬起靠"这个键的按键流静默了 `idle_ms`"推断** → **默认值刻意取小
  （100ms）**：**必须小于 DAS**（默认 150ms）——**否则一次轻点会被当成按住**
  （**轻点应该只移一格**）；
- **代价**：**POSIX 上 DAS 低于终端的首轮重复延迟时，前半段会没有连发**
  （终端还没开始重复 → 我们以为键松了）→ **这一段是终端的行为，不是我们的**。

**Windows 没有这个问题**（抬起精确）→ **两个平台的 DAS / ARR 行为一致**。

## 读取**不许阻塞推进**

**按键读取必须是非阻塞的**（spec §7.5：「输入在轮询间隙读（不阻塞推进）」）——
POSIX 用 `select` 的零超时，Windows 用按键状态轮询，**都不等输入**。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

#: 按键事件的两种状态（**按下 / 抬起**）。
KIND_DOWN = "down"
KIND_UP = "up"

#: POSIX 上"按键流静默多久算抬起"（**毫秒**）。
#:
#: **刻意取小**：**必须小于 DAS**（默认 150ms）——**否则一次轻点会被当成按住**
#: （**轻点应该只移一格**）。
DEFAULT_IDLE_MS = 100


@dataclass(frozen=True)
class KeyEvent:
    """一次按键事件：**键名 + 状态**。

    **键名是归一化的**（`left` / `right` / `up` / `down` / `space` / `z` …）——
    **平台差异在这一层封掉**（Windows 的 `'\xe0H'` 与 POSIX 的 `'\x1b[A'` 都归一
    成 `up`）。
    """

    key: str
    kind: str


@runtime_checkable
class KeyReader(Protocol):
    """**按键读取器**的接口（**结构化类型**）。

    **不许阻塞**：`poll()` **立即返回**当前能读到的按键事件（**没有就是空
    元组**）。
    """

    def poll(self, now_ms: int) -> tuple[KeyEvent, ...]:
        """取当前可读的按键事件（**非阻塞** · `now_ms` 是宿主给的时刻）。"""
        ...

    def close(self) -> None:
        """**收尾**（恢复终端设置 · 平台没有要恢复的就空操作）。"""
        ...


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

#: Windows 虚拟键码 → **归一化键名**。
#:
#: **只用 `GetAsyncKeyState`**（不问字符流）——**抬起因此是精确的**。
_WINDOWS_KEYS: tuple[tuple[int, str], ...] = (
    (0x25, "left"),
    (0x26, "up"),
    (0x27, "right"),
    (0x28, "down"),
    (0x20, "space"),
    (0x5A, "z"),
    (0x58, "x"),
    (0x43, "c"),
    (0x50, "p"),
    (0x51, "q"),
    (0x1B, "escape"),
    (0x0D, "enter"),
)


class WindowsKeyReader:
    """Windows 的按键读取（**`GetAsyncKeyState` · 抬起精确**）。"""

    def __init__(self) -> None:
        import ctypes  # **只在 Windows 上 import**（别的平台没这个模块的用法）

        self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        self._down: set[str] = set()

    def poll(self, now_ms: int) -> tuple[KeyEvent, ...]:
        """轮询每个键的**当前状态**（**抬起精确**）。"""
        del now_ms  # **Windows 的抬起是精确的** —— **不需要时间**。
        events: list[KeyEvent] = []
        for code, name in _WINDOWS_KEYS:
            state = self._user32.GetAsyncKeyState(code)
            pressed = bool(state & 0x8000)
            if pressed and name not in self._down:
                self._down.add(name)
                events.append(KeyEvent(name, KIND_DOWN))
            elif not pressed and name in self._down:
                self._down.discard(name)
                events.append(KeyEvent(name, KIND_UP))
        return tuple(events)

    def close(self) -> None:
        """**没有终端设置要恢复**（空操作）。"""
        return None


# ---------------------------------------------------------------------------
# POSIX
# ---------------------------------------------------------------------------

#: POSIX 的转义序列 → **归一化键名**。
_POSIX_ESCAPES: tuple[tuple[str, str], ...] = (
    ("\x1b[A", "up"),
    ("\x1b[B", "down"),
    ("\x1b[C", "right"),
    ("\x1b[D", "left"),
    ("\x1b[5", "page_up"),
    ("\x1b[6", "page_down"),
)


class PosixKeyReader:
    """POSIX 的按键读取（**`termios` 原始模式 + `select` 零超时**）。

    **抬起是推断的**（见模块头的"两个平台的现实差别"）。
    """

    def __init__(self, *, idle_ms: int = DEFAULT_IDLE_MS) -> None:
        import select
        import termios
        import tty

        self._select = select
        # **POSIX 专有的模块在 Windows 的类型桩里没有这些成员** → **按
        # `Any` 拿**（**这一段代码本来也只在 POSIX 上跑**）。
        self._termios: Any = termios
        self._tty: Any = tty
        self._idle_ms = idle_ms
        self._fd = sys.stdin.fileno()
        self._saved = self._termios.tcgetattr(self._fd)
        self._tty.setcbreak(self._fd)
        self._buffer = ""
        self._last_seen: dict[str, int] = {}
        self._now = 0

    def _parse(self) -> list[KeyEvent]:
        """从字节缓冲里解析出按键事件（**转义序列按最长匹配**）。"""
        events: list[KeyEvent] = []
        buffer = self._buffer
        while buffer:
            matched = False
            for sequence, name in _POSIX_ESCAPES:
                if buffer.startswith(sequence):
                    events.append(KeyEvent(name, KIND_DOWN))
                    buffer = buffer[len(sequence) :]
                    matched = True
                    break
            if matched:
                continue
            char = buffer[0]
            buffer = buffer[1:]
            if char == "\x1b":
                # **单独的 ESC**（没跟序列）→ 当作 escape 键。
                events.append(KeyEvent("escape", KIND_DOWN))
                continue
            if char in ("\r", "\n"):
                events.append(KeyEvent("enter", KIND_DOWN))
                continue
            if char == " ":
                events.append(KeyEvent("space", KIND_DOWN))
                continue
            if char == "\x03":
                events.append(KeyEvent("ctrl_c", KIND_DOWN))
                continue
            if char.isprintable():
                events.append(KeyEvent(char.lower(), KIND_DOWN))
        self._buffer = buffer
        return events

    def poll(self, now_ms: int) -> tuple[KeyEvent, ...]:
        """读当前能读到的字节（**零超时**）+ **推断抬起**。"""
        self._now = now_ms
        events: list[KeyEvent] = []
        while True:
            ready, _, _ = self._select.select([self._fd], [], [], 0)
            if not ready:
                break
            data = sys.stdin.read(1)
            if not data:
                break
            self._buffer += data
        events.extend(self._parse())
        for event in events:
            if event.kind == KIND_DOWN:
                self._last_seen[event.key] = self._now
        # **推断抬起**：**这个键的按键流静默超过 `idle_ms`**。
        for key, last in list(self._last_seen.items()):
            if self._now - last > self._idle_ms:
                del self._last_seen[key]
                events.append(KeyEvent(key, KIND_UP))
        return tuple(events)

    def close(self) -> None:
        """**恢复终端设置**（**不恢复就等于把用户的终端留在原始模式**）。"""
        try:
            self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._saved)
        except (OSError, ValueError):
            # **恢复失败不炸**（终端可能已经关了）。
            pass


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def create_key_reader(*, idle_ms: int = DEFAULT_IDLE_MS) -> KeyReader:
    """造一个**当前平台**的按键读取器。

    **平台分支在这里封掉**（spec §7.7）——**上层只看见 `KeyReader`**。
    """
    if sys.platform.startswith("win"):
        return WindowsKeyReader()
    return PosixKeyReader(idle_ms=idle_ms)


__all__ = [
    "DEFAULT_IDLE_MS",
    "KIND_DOWN",
    "KIND_UP",
    "KeyEvent",
    "KeyReader",
    "PosixKeyReader",
    "WindowsKeyReader",
    "create_key_reader",
]
