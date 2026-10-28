"""AI 系统（系统层 · M4-S9 · spec-M4 §7.8 · FR-8.5 · AC-6 · 架构 §7.5）。

## 它是什么

**一个真的能替人玩的 AI**：订阅事件 → 算落点 → **派命令**。

- **与人类走完全相同的命令通道**（FR-8.5）：AI 派的是 `moveLeft` / `moveRight` /
  `rotateCW` / `rotate180` / `rotateCCW` / `hardDrop`——**内核分不出它和人类**
  （内核**不许因 `source` 分支** · M4-D5）；
- **不写棋盘**（M3-D3 的延续）：它**只订阅事件 + 派命令**——**没有第二条路**；
- **预算固定**（M4-D3）：决策在 `search.py` 里，**那里没有 `time`**。

## 什么时候决策

| 事件 | 干什么 |
| --- | --- |
| `piece:spawned` | **记下"这一块还没规划"**（新方块 = 新的决策机会） |
| `clock:tick` | **规划并派命令**（**这一帧的动作时机**） |

**为什么"出生时记、滴答时动"**：滴答那一刻拿到的视图**就是方块当下的位置**
（视图 = 快照 · ADR-056）——**规划用的坐标与命令执行时的坐标是同一个**，
不会出现"算的是出生点、落的时候已经掉了一格"。

**为什么每块只规划一次**：派完命令这一块就硬降落地了；**再规划一次就是
同一块被落两遍**。`piece:spawned` 负责把闸门重新打开。

## 来源标记只作元数据（M4-D5）

AI 派的命令带 `source="ai"`——**那只是"谁发的"这一个意思**
（仓库里的既有写法：`host` / `human` / `ai` / `replay` / `growth`）。
**内核不许因它分支** → **同一串命令，人类手动输入与 AI 派发，结果逐帧全等**
（AC-6 / G4-5）。
"""

from __future__ import annotations

from ...bus.bus import EventBus
from ...bus.envelope import Envelope
from ...bus.subscriber import Subscription, SystemContext
from ...kernel.commands import Command
from ...kernel.snapshot import Snapshot
from ..context import CommandChannel
from .search import plan_placement

#: 新方块出生（**决策机会的闸门**）。
PIECE_SPAWNED_EVENT = "piece:spawned"

#: 时钟滴答（**派命令的时机**）。
CLOCK_TICK_EVENT = "clock:tick"

#: AI 派命令时的**来源标记**（**只是元数据** · M4-D5 / FR-8.5）。
#:
#: **不是命名空间**（别写成 `variant:ai` 之类）——它是"谁发的"，
#: 与 `host` / `human` / `replay` / `growth` 同一口径。
SOURCE_NAME = "ai"


class AiSystem:
    """AI 系统（**只订阅事件 · 只派命令**）。

    - `bus`：事件总线（**双参数订阅** → 能拿只读视图 `ctx.view()`）；
    - `commands`：**派命令的通道**（宿主注入 · 系统层拿不到内核）。

    **它没有第二条路**：没有内核引用、没有写棋盘的路径——
    **AI 只能通过命令影响局面**（架构 §4.3 · M3-D3）。
    """

    def __init__(self, bus: EventBus[Snapshot], commands: CommandChannel) -> None:
        self._bus = bus
        self._commands = commands
        #: **这一块规划过没有**（规划一次就落地了，别落两遍）。
        self._planned = False
        #: **派过多少条命令**（可观测：它真的在玩）。
        self._dispatched = 0
        #: **规划过多少块**（可观测）。
        self._planned_count = 0
        # **双参数订阅**（`subscribe_system`）：系统层**要拿只读视图**
        # （架构 §4.3 · ADR-059）——单参数入口拿不到。
        self._subscriptions: tuple[Subscription[Snapshot], ...] = (
            bus.subscribe_system(PIECE_SPAWNED_EVENT, self._on_piece_spawned, name="AI 系统"),
            bus.subscribe_system(CLOCK_TICK_EVENT, self._on_clock_tick, name="AI 系统"),
        )

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def subscriptions(self) -> tuple[Subscription[Snapshot], ...]:
        """本系统的订阅句柄（**两条** · M3 摘系统时要逐个退订）。

        **暴露它不是为了方便**——是 M3 的硬要求：**退订是系统级语义**，
        注册表得拿得到句柄才摘得干净。
        """
        return self._subscriptions

    @property
    def dispatched_count(self) -> int:
        """**派过多少条命令**（它真的在玩 → 这个数不为零）。"""
        return self._dispatched

    @property
    def planned_count(self) -> int:
        """**规划过多少块方块**。"""
        return self._planned_count

    # ------------------------------------------------------------------
    # 订阅回调
    # ------------------------------------------------------------------

    def _on_piece_spawned(
        self,
        envelope: Envelope,
        ctx: SystemContext[Snapshot],
    ) -> None:
        """新方块出生 → **把闸门打开**（这一块还没规划）。"""
        self._planned = False

    def _on_clock_tick(
        self,
        envelope: Envelope,
        ctx: SystemContext[Snapshot],
    ) -> None:
        """滴答 → **规划这一块的落点 → 派命令**。

        **规划一次就够**：派完命令这一块就硬降落地了。
        """
        if self._planned:
            return
        snapshot = _view_of(ctx)
        if snapshot is None:
            # **拿不到视图就不决策**（总线没注入视图提供者）——
            # **宁可不动作，也不要拿一份来路不明的状态去派命令**。
            return
        active = snapshot.active
        if active is None:
            return
        placement = plan_placement(
            snapshot.board.cells,
            active.pieceType,
            active.x,
            active.y,
            active.rotation,
        )
        if placement is None:
            # **没有合法落点**（方块已经卡死）——什么都不派，让局面自己结束。
            self._planned = True
            return
        self._planned = True
        self._planned_count += 1
        for command_type in placement.commands:
            self._commands.submit(Command(type=command_type, source=SOURCE_NAME))
            self._dispatched += 1


def _view_of(ctx: SystemContext[Snapshot]) -> Snapshot | None:
    """取只读视图（**取不到就是没有**——不糊弄）。

    **为什么catch**：总线**可能没注入视图提供者**（架构 §4.3：没注入就当场
    报错）。AI 在那种总线上**不决策**——**宁可不动作，也不要拿一份来路不明的
    状态去派命令**。
    """
    try:
        return ctx.view()
    except RuntimeError:
        return None


__all__ = [
    "CLOCK_TICK_EVENT",
    "PIECE_SPAWNED_EVENT",
    "SOURCE_NAME",
    "AiSystem",
]
