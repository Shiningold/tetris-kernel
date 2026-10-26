"""存档宿主（宿主层 · M2-S10 · FR-10.1 / FR-10.2 · ADR-080）。

## 为什么映射在宿主层

`systems/replay/` **不 import `kernel.core`**（与 S9 同一条纪律：系统层不造
内核、不碰内核的内部结构）。**内核的私有结构 ↔ 存档数据**的映射是**宿主**的
活（架构 §4.1：唯一知道"怎么把部件拼起来"的地方是装配层）。

**恢复只经内核的入口**：宿主**不写内核的字段**——它把存档翻译成内核认得的
`KernelArchive`，再交给 `Kernel.restore_state()`（**只有内核能写状态**）。

## 导出是纯读

导出**不改内核的任何状态**，也**不碰随机源**——`Kernel.export_state()` 只读
MT 状态，不取值（取值会改变这一局的走向）。

## 存档自带"这份局面属于谁"

存档头里带**种子**与**规则集标识**：恢复时内核会核对这两项——**对不上当场
报错**（NFR-9：不猜、不静默降级）。**载入一份存档前，用存档里的种子与规则集
标识建内核**，恢复才有意义。
"""

from __future__ import annotations

from ..kernel.core import ActivePiece, Kernel, KernelArchive, KernelState
from ..systems.replay.archive import (
    Archive,
    ArchivedActive,
    ArchivedBoard,
    ArchivedState,
    archive_from_json,
    archive_to_json,
)


class KernelArchiveDriver:
    """存档驱动器（**宿主实现**）：导出局面 · 恢复局面。

    - `kernel`：要导出 / 恢复的内核实例。

    **驱动器不持有状态**：它只是"内核 ↔ 存档"的翻译器，换个内核照样能用。
    """

    def __init__(self, kernel: Kernel) -> None:
        self._kernel = kernel

    @property
    def kernel(self) -> Kernel:
        """绑着的内核实例。"""
        return self._kernel

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    def export(self) -> Archive:
        """导出当前局面（**纯读**：内核一个字节都没改）。"""
        archive = self._kernel.export_state()
        return Archive(
            seed=archive.seed,
            rule=archive.rule,
            rngState=archive.rng_state,
            state=_to_archived_state(archive),
        )

    def export_text(self) -> str:
        """导出当前局面 → **存档文本**（单个 JSON 文档）。"""
        return archive_to_json(self.export())

    # ------------------------------------------------------------------
    # 恢复
    # ------------------------------------------------------------------

    def restore(self, archive: Archive) -> None:
        """把一份存档恢复进内核（**只有内核能写状态**）。

        **规则集 / 种子对不上当场报错**——见 `Kernel.restore_state()`。
        """
        self._kernel.restore_state(_to_kernel_archive(archive))

    def restore_text(self, text: str) -> None:
        """把一段存档文本恢复进内核（**先解析，再恢复**）。"""
        self.restore(archive_from_json(text))


# ---------------------------------------------------------------------------
# 内核结构 ↔ 存档数据
# ---------------------------------------------------------------------------


def _to_archived_state(archive: KernelArchive) -> ArchivedState:
    """内核的存档点 → 存档的局面（**棋盘逐行拷贝**：两边不共享内层列表）。

    **帧号与棋盘几何从存档点取**——它们不在 `KernelState` 里（帧号是内核的
    普通属性，几何是内核的构造参数），但快照的 `frame` 与 `board` 子结构都带
    它们，存档要能重建同一块棋盘。
    """
    state = archive.state
    active = state.active
    return ArchivedState(
        version=state.version,
        phase=state.phase,
        frame=archive.frame,
        board=ArchivedBoard(
            width=archive.width,
            height=archive.height,
            hiddenRows=archive.hidden_rows,
            cells=tuple(tuple(row) for row in state.board),
        ),
        active=None if active is None else _to_archived_active(active),
        ghostDelta=state.ghost_delta,
        hold=state.hold,
        holdUsed=state.hold_used,
        next=state.next_queue,
        score=state.score,
        lines=state.lines,
        level=state.level,
        combo=state.combo,
        b2b=state.b2b,
        piecesPlaced=state.pieces_placed,
        grounded=state.grounded,
        lockTimer=state.lock_timer,
        gravityTimer=state.gravity_timer,
        extensions=state.extensions,
    )


def _to_archived_active(active: ActivePiece) -> ArchivedActive:
    """内核的活动方块 → 存档的活动方块（**9 个字段一个不少**）。"""
    return ArchivedActive(
        pieceType=active.pieceType,
        rotation=active.rotation,
        x=active.x,
        y=active.y,
        lastMoveWasRotation=active.lastMoveWasRotation,
        kickIndex=active.kickIndex,
        kickOffset=active.kickOffset,
        lastCause=active.lastCause,
        lockResets=active.lockResets,
        lockResetPending=active.lockResetPending,
    )


def _to_kernel_archive(archive: Archive) -> KernelArchive:
    """存档 → 内核的存档点（**棋盘逐行拷贝**：两边不共享内层列表）。"""
    state = archive.state
    return KernelArchive(
        seed=archive.seed,
        rule=archive.rule,
        state=_to_kernel_state(state),
        frame=state.frame,
        rng_state=archive.rngState,
        width=state.board.width,
        height=state.board.height,
        hidden_rows=state.board.hiddenRows,
    )


def _to_kernel_state(state: ArchivedState) -> KernelState:
    """存档的局面 → 内核的局面。"""
    active = state.active
    return KernelState(
        version=state.version,
        phase=state.phase,
        board=[list(row) for row in state.board.cells],
        active=None if active is None else _to_active_piece(active),
        ghost_delta=state.ghostDelta,
        hold=state.hold,
        hold_used=state.holdUsed,
        next_queue=state.next,
        score=state.score,
        lines=state.lines,
        level=state.level,
        combo=state.combo,
        b2b=state.b2b,
        pieces_placed=state.piecesPlaced,
        grounded=state.grounded,
        lock_timer=state.lockTimer,
        gravity_timer=state.gravityTimer,
        extensions=state.extensions,
    )


def _to_active_piece(active: ArchivedActive) -> ActivePiece:
    """存档的活动方块 → 内核的活动方块（**9 个字段一个不少**）。"""
    return ActivePiece(
        pieceType=active.pieceType,
        rotation=active.rotation,
        x=active.x,
        y=active.y,
        lastMoveWasRotation=active.lastMoveWasRotation,
        kickIndex=active.kickIndex,
        kickOffset=active.kickOffset,
        lastCause=active.lastCause,
        lockResets=active.lockResets,
        lockResetPending=active.lockResetPending,
    )


__all__ = [
    "KernelArchiveDriver",
]
