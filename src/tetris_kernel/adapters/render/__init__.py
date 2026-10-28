"""渲染适配层（M4-S6 · FR-8.1 / FR-8.2 · spec-M4 §7.6）。

**渲染器只消费事件与快照**（M4-D2）——**不写内核状态** · **不用只读视图
通道** · **画面完全由事件推导**（FR-8.2）。

**界面与内核之间只有三个接口**：**事件** · **规则集** · **棋盘尺寸**——
**换一个渲染器不需要改内核**。
"""

from .terminal import (
    BLOCK_VALUE,
    CELL_ACTIVE,
    CELL_BLOCK,
    CELL_EMPTY,
    CELL_SPECIAL,
    EventLike,
    TerminalRenderer,
)

__all__ = [
    "BLOCK_VALUE",
    "CELL_ACTIVE",
    "CELL_BLOCK",
    "CELL_EMPTY",
    "CELL_SPECIAL",
    "EventLike",
    "TerminalRenderer",
]
