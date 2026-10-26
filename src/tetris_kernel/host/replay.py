"""回放宿主（宿主层 · M2-S9 · FR-6.2）。

**起内核 · 派发命令 · 推进帧 · 取四项**——这些是**宿主**的事（架构 §4.1：
唯一知道"怎么把部件拼起来"的地方是装配层）。

**为什么驱动器在宿主层**：`systems/replay/` **不造内核**——它连
`kernel.core` 都不 import。它只管"录了什么、按什么顺序重放"，内核由宿主起、
命令由宿主派。

## 配额必须放大（B-6 · quota.py 的硬要求）

实时局按帧限额（单帧 100 条命令）。**重放不能按那个上限跑**——超限的命令
会被**丢弃**（`quota_exceeded`），而**录像不允许丢命令**（B-6）：丢一条 =
录像与回放对不上，重放静默分叉。

所以回放宿主注入**大得多的上限**（`REPLAY_LIMITS`）。
"""

from __future__ import annotations

from ..bus.bus import EventBus
from ..kernel.commands import Command
from ..kernel.core import Kernel
from ..kernel.drafts import EventDraft
from ..kernel.quota import QuotaLimits
from ..kernel.snapshot import Snapshot
from ..systems.replay.format import FourItems, RecordedCommand

#: 回放宿主的配额上限（**远大于实时局** · B-6 / quota.py 的硬要求）。
#:
#: `max_commands` 是**单帧**上限，`max_rounds` 是**单帧轮次**上限——回放把一帧
#: 的命令一次性铺进去，按实时局的上限会当场超限并**丢命令**。
REPLAY_LIMITS = QuotaLimits(
    max_rounds=64,
    max_commands=100_000,
    max_write_cells=None,
)


class KernelReplayDriver:
    """回放驱动器（**宿主实现**）：把录下的命令派发给一个**全新的内核实例**。

    - `kernel`：**同种子 · 同规则集**的新内核（重放要按录像头的规则集起）；
    - `bus`：事件总线（回放期间的事件照常发出去）。
    """

    def __init__(self, kernel: Kernel, bus: EventBus[Snapshot]) -> None:
        self._kernel = kernel
        self._bus = bus

    def dispatch(self, commands: tuple[RecordedCommand, ...]) -> None:
        """派发一批命令（**原样**——类型 / 参数 / 来源都不改）。

        **来源透传原值**（ADR-062）：回放时**不改写成 `replay`**——改写会让
        FR-6.4 的事件流回归基线永久失配。
        """
        for command in commands:
            self._kernel.submit(
                Command(type=command.type, args=command.args, source=command.source)
            )
        self._kernel.run_rounds(self._publish_batch)

    def advance_frame(self) -> None:
        """推进一帧（**帧推进五步**——**生成器必须被耗尽**，B-9）。"""
        for batch in self._kernel.advance_frame():
            self._publish_batch(batch)

    def four_items(self) -> FourItems:
        """当前局面的四项（**棋盘指纹 · 分数 · 行数 · 方块数**）。"""
        snapshot = self._kernel.snapshot()
        return FourItems(
            fingerprint=snapshot.board.structureFingerprint,
            score=snapshot.score,
            lines=snapshot.lines,
            pieces_placed=snapshot.piecesPlaced,
        )

    def _publish_batch(self, drafts: tuple[EventDraft, ...]) -> None:
        """把一批草稿**逐条**转交给总线（宿主的活）。"""
        for draft in drafts:
            self._bus.publish(draft)


__all__ = [
    "REPLAY_LIMITS",
    "KernelReplayDriver",
]
