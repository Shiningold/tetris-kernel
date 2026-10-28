"""变体 B：**落地后生长**（系统插件 + 受控写入路径 · M4-S4 · spec-M4 §7.3）。

## 这个变体是什么

**玩法差异全来自一个系统**：方块落地后，**在它上方长出晶体**（棋盘上的格子，
普通方块是 `1`、晶体是 `8`）——**规则集一个字不改**（还是标准玩法）。

**写入只走 `writeCells`**（FR-11.1）——**外部改变棋盘的唯一合法通道**。

**详见同目录的 `README.md`**（变体自己的文档：玩法语义与取舍写在那里）。
"""

from __future__ import annotations

from .manifest import SYSTEM_MANIFEST, create_system
from .system import (
    BOARD_CHANGED_EVENT,
    BOARD_COLLAPSED_EVENT,
    CLOCK_TICK_EVENT,
    CRYSTAL_VALUE,
    PIECE_LOCKED_EVENT,
    PIECE_SPAWNED_EVENT,
    SOURCE_NAME,
    WRITE_CELLS_COMMAND,
    CellSubmitter,
    EventLike,
    GrowthInstallContext,
    GrowthSystem,
)

__all__ = [
    "BOARD_CHANGED_EVENT",
    "BOARD_COLLAPSED_EVENT",
    "CLOCK_TICK_EVENT",
    "CRYSTAL_VALUE",
    "PIECE_LOCKED_EVENT",
    "PIECE_SPAWNED_EVENT",
    "SOURCE_NAME",
    "SYSTEM_MANIFEST",
    "WRITE_CELLS_COMMAND",
    "CellSubmitter",
    "EventLike",
    "GrowthInstallContext",
    "GrowthSystem",
    "create_system",
]
