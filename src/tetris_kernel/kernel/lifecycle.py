"""生命周期状态机（内核层 · S9 · FR-1.1 · 架构 §7.1）。

## 四态 + 六种迁移

| 状态 | 含义 |
| --- | --- |
| `not_started` | 待开始（还没开局） |
| `running` | 运行中 |
| `paused` | 暂停（内核级暂停；帧号照走，推进许可被门禁收回） |
| `game_over` | 结束 |

| 迁移 | 触发 | 合法来源 | 结果 | 事件 |
| --- | --- | --- | --- | --- |
| `create` | 内核构造 | — | `not_started` | `game:created` |
| `start` | `start` 命令 | `not_started` | `running` | `game:started` |
| `pause` | `pause` 命令 | `running` | `paused` | `game:paused` |
| `resume` | `resume` 命令 | `paused` | `running` | `game:resumed` |
| `end` | 内部（顶出 / 溢出） | `running` | `game_over` | `game:over` |
| `reset` | `reset` 命令 | 任意 | `not_started` | `game:reset` |

**非法迁移被拒绝并发事件**（FR-1.1）：拒绝走命令通道的拒绝事件（原因
`illegal_transition`），对应的 `game:*` 事件**不发生**。

## 门禁三值从状态派生

`not_started` / `paused` / `game_over` 三个**拒绝原因**与三个状态一一对应——
状态机的状态名就是门禁原因的来源，**不另抄一份名单**（架构 §7.2）。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

# ---------------------------------------------------------------------------
# 四态
# ---------------------------------------------------------------------------

#: 待开始。
PHASE_NOT_STARTED = "not_started"

#: 运行中。
PHASE_RUNNING = "running"

#: 暂停（内核级暂停）。
PHASE_PAUSED = "paused"

#: 结束。
PHASE_GAME_OVER = "game_over"

#: 四个状态的稳定序列。
PHASES: tuple[str, ...] = (PHASE_NOT_STARTED, PHASE_RUNNING, PHASE_PAUSED, PHASE_GAME_OVER)

# ---------------------------------------------------------------------------
# 六种迁移
# ---------------------------------------------------------------------------

#: 创建（内核构造时的那一次）。
TRANSITION_CREATE = "create"

#: 开始。
TRANSITION_START = "start"

#: 暂停。
TRANSITION_PAUSE = "pause"

#: 继续。
TRANSITION_RESUME = "resume"

#: 结束。
TRANSITION_END = "end"

#: 重置。
TRANSITION_RESET = "reset"

#: 六种迁移的稳定序列。
TRANSITIONS: tuple[str, ...] = (
    TRANSITION_CREATE,
    TRANSITION_START,
    TRANSITION_PAUSE,
    TRANSITION_RESUME,
    TRANSITION_END,
    TRANSITION_RESET,
)

#: 状态 → 该状态下合法的迁移。
_ALLOWED: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        PHASE_NOT_STARTED: frozenset({TRANSITION_CREATE, TRANSITION_START, TRANSITION_RESET}),
        PHASE_RUNNING: frozenset({TRANSITION_PAUSE, TRANSITION_END, TRANSITION_RESET}),
        PHASE_PAUSED: frozenset({TRANSITION_RESUME, TRANSITION_RESET}),
        PHASE_GAME_OVER: frozenset({TRANSITION_RESET}),
    }
)

#: 非法迁移的拒绝原因（契约的 12 值枚举之一）。
REJECT_ILLEGAL_TRANSITION = "illegal_transition"


class Lifecycle:
    """四态状态机：持有当前状态，裁决迁移是否合法。

    **无状态机自己不发事件**——事件由内核按迁移结果发（架构 §4.3：事件是
    内核对外的唯一出口，状态机是内核的内部部件）。
    """

    def __init__(self) -> None:
        """构造即"创建"：初始状态是 `not_started`。"""
        self._phase = PHASE_NOT_STARTED

    @property
    def phase(self) -> str:
        """当前状态。"""
        return self._phase

    def can(self, transition: str) -> bool:
        """该迁移在当前状态下是否合法。"""
        return transition in _ALLOWED.get(self._phase, frozenset())

    def apply(self, transition: str) -> bool:
        """执行迁移。

        返回 `True` = 迁移合法且已生效；`False` = **状态原样不动**（调用方据此
        发拒绝事件 `illegal_transition`）。
        """
        if not self.can(transition):
            return False
        if transition == TRANSITION_START:
            self._phase = PHASE_RUNNING
        elif transition == TRANSITION_PAUSE:
            self._phase = PHASE_PAUSED
        elif transition == TRANSITION_RESUME:
            self._phase = PHASE_RUNNING
        elif transition == TRANSITION_END:
            self._phase = PHASE_GAME_OVER
        elif transition == TRANSITION_RESET:
            self._phase = PHASE_NOT_STARTED
        # TRANSITION_CREATE 落在 not_started（构造时的初始状态），无需改写。
        return True

    def reject_reason(self) -> str:
        """非法迁移的拒绝原因。"""
        return REJECT_ILLEGAL_TRANSITION


__all__ = [
    "PHASES",
    "PHASE_GAME_OVER",
    "PHASE_NOT_STARTED",
    "PHASE_PAUSED",
    "PHASE_RUNNING",
    "REJECT_ILLEGAL_TRANSITION",
    "TRANSITIONS",
    "TRANSITION_CREATE",
    "TRANSITION_END",
    "TRANSITION_PAUSE",
    "TRANSITION_RESET",
    "TRANSITION_RESUME",
    "TRANSITION_START",
    "Lifecycle",
]
