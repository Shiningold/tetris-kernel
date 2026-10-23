"""`PlacementInfo` 声明（契约清单第 10 项 · 架构 §7.3.1）。

T-Spin 判定（`classify_placement`）需要的「最后一手」上下文，由内核在锁定时
提供。6 个字段在 spec §6.5 / 架构 §7.3.1 定死，本模块是它的机器可读落点：
frozen dataclass 即声明本体——注解在 `from __future__ import annotations` 下
以源码字符串形式存进 `__dataclass_fields__[*].type`，跨进程稳定，S6 的基线
脚本可直接按字段哈希它。

## 为什么放 contracts/ 不放 rules/

它是**中性交换结构**：`rules/` 可以 import `contracts/`，反过来不行。放
`rules/` 就进了冻结区——变体想改 T-Spin 定义会直接撞 G4-1（plan-M0.md S4
常见坑 / ADR-061）。规则集无状态，`PlacementInfo` 是内核递给它的**一次性
事实**，不是规则集可写的东西。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlacementInfo:
    """一次放置的上下文（T-Spin 判定的输入，架构 §7.3.1）。

    字段与类型 = spec §6.5 定稿的 6 条，顺序即声明顺序（字段顺序进基线，
    不得重排）。`cells` 是**绝对坐标**（x 右正、y 下正，`COORD_CELLS`），
    与快照 `board.cells` 的坐标系同一条。

    `kickIndex` / `kickOffset` 可空：未发生踢墙时为 `None`——「没有踢墙」
    与「踢墙偏移为 (0, 0)」是两件事，用 `None` 表达前者，不用魔法值兜底
    （与 `piece:held.taken` 同一条口径：约定不写下来等于没有）。
    """

    pieceType: str
    rotation: int
    cells: tuple[tuple[int, int], ...]
    lastMoveWasRotation: bool
    kickIndex: int | None
    kickOffset: tuple[int, int] | None


__all__ = ["PlacementInfo"]
