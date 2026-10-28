"""重力轴的**形状表与踢墙表**（变体 A · M4-A5 · spec-M4 §7.2）。

## 这个模块是什么

变体 A（重力可旋转）**随轴变的是两张表**——**形状表**（方块在盒子里的占位）与
**踢墙表**（旋转时的候选偏移）。**内核不做变换，表由规则集整体提供**
（架构 §7.3.1 纪律 2）。

**表从哪来**：**不是另抄一套真值表**，而是**把标准 `down` 轴的表按轴做刚体变换**
（`rules.tables.SHAPE_TABLE["down"]` / `KICK_TABLE["down"]`）。**为什么这样够用**：
SRS 的形状在 90° / 180° 旋转下有已知等价（**一套表转过来就是另一轴的表**），
而踢墙偏移**是相对位移**——**位移的映射是线性的**。

**为什么不做四轴全量真值表**：4 轴 × 7 方块 × 4 朝向 × 16 种转向 = **标准表的
四倍**，而**变体的踢墙语义本来就是"变体自定义"**（标准 SRS 的踢墙在横向重力下
没有物理对应物）。**这条取舍写在变体自己的 README 里**——**"变体 A 的踢墙表是
标准偏移的轴映射，不是旋转重力下的真 SRS"**。

## 变换是什么

**绕盒子中心的刚体旋转**，把方块的**基准贴地面**对齐新的重力方向：

| 轴 | 重力方向 | 形状变换（坐标） | 偏移变换（向量） |
| --- | --- | --- | --- |
| `down` | `(0, 1)` | 恒等 | 恒等 |
| `right` | `(1, 0)` | `(x, y) → (y, N-1-x)` | `(dx, dy) → (dy, -dx)` |
| `left` | `(-1, 0)` | `(x, y) → (N-1-y, x)` | `(dx, dy) → (-dy, dx)` |

- **`N` 是盒子边长**（`BOX_SIZE_BY_PIECE`：I 用 4×4 · O 用 2×2 · 其余 3×3）——
  **绕中心转**保证**盒子尺寸不变**，旋转时方块不会"跳位"（标准 SRS 的盒子也是
  正方形，正是为了这个）。
- **偏移变换没有中心**——偏移是**相对位移**，不是位置。
- **旋转方向**：屏幕坐标是 **x 右正 · y 下正**，所以**视觉逆时针**的线性部分是
  `(dx, dy) → (dy, -dx)`（`right` 轴用它：把"下"转到"右"）；**视觉顺时针**是
  `(dx, dy) → (-dy, dx)`（`left` 轴用它：把"下"转到"左"）。

## 朝向编号不变（为什么手感不反）

**形状整体转了 90°，玩家的旋转手感不会反过来**——**刚体旋转与 SRS 的旋转可交换**：
`T(SRS_顺(down[r])) = SRS_顺(T(down[r]))`。**所以 `right[r] = T(down[r])`**
（**不做朝向重编号**）时，玩家按 CW 看到的**仍然是屏幕上的顺时针**。

**"横向重力下的旋转是绕重力轴"这件事做不到**：那是三维的旋转，**二维棋盘表达不了**
（投影到屏幕等于方块的厚度方向变了）。**变体 A 的旋转是屏幕旋转**——**这条写在
变体自己的 README 里**。

## 不做 `up` 轴

`up` 轴的重力是 `(0, -1)`，而内核的碰撞判定把 **`y < 0` 当作"棋盘之上的出生区"**
（不算冲突 · `kernel/core.py:2011`）→ 向上重力的方块会**飞出棋盘**。
**四个轴做三个，是有理由的**——理由写在这里，不是漏做。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from ...rules.tables import (
    GRAVITY_VECTORS,
    KICK_TABLE,
    SHAPE_TABLE,
)

#: 变体 A 支持的轴（**三个**——`up` 的理由见模块 docstring）。
AXIS_VALUES: tuple[str, ...] = ("down", "right", "left")

#: 方块的**盒子边长**（SRS 的基准盒：I 用 4×4 · O 用 2×2 · 其余 3×3）。
#:
#: **为什么需要它**：形状变换是**绕盒子中心**的旋转，**中心由盒子边长决定**——
#: 标准表里的占位格子**推不出盒高**（I 的朝向 0 只占 `y = 1` 那一行，可它的盒
#: 是 4×4）。**这张表是"盒子规格"这个事实的投影**，不是抄来的第二份占位表。
BOX_SIZE_BY_PIECE: Mapping[str, int] = MappingProxyType(
    {
        "I": 4,
        "O": 2,
        "J": 3,
        "L": 3,
        "S": 3,
        "T": 3,
        "Z": 3,
    }
)

#: 形状表：方块类型 → 朝向 → 占位格子（**一个轴一张表**）。
ShapeTable = Mapping[str, Mapping[int, tuple[tuple[int, int], ...]]]

#: 踢墙表：`(旋转前朝向, 旋转后朝向)` → 踢墙组 → 候选偏移（**候选顺序 = 优先级**）。
KickTable = Mapping[tuple[int, int], Mapping[str, tuple[tuple[int, int], ...]]]


# ---------------------------------------------------------------------------
# 变换
# ---------------------------------------------------------------------------


def _rotate_cell(cell: tuple[int, int], size: int, axis: str) -> tuple[int, int]:
    """单个占位格子的轴变换（**绕盒子中心**）。"""
    x, y = cell
    if axis == "right":
        # 视觉逆时针 90°：把"下"转到"右"。
        return (y, size - 1 - x)
    if axis == "left":
        # 视觉顺时针 90°：把"下"转到"左"。
        return (size - 1 - y, x)
    return (x, y)


def _rotate_offset(offset: tuple[int, int], axis: str) -> tuple[int, int]:
    """单个候选偏移的轴变换（**相对位移，没有中心**）。"""
    dx, dy = offset
    if axis == "right":
        return (dy, -dx)
    if axis == "left":
        return (-dy, dx)
    return (dx, dy)


def _sort_cells(cells: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    """按**行优先**（上到下、每行左到右）排——与标准表同一口径。

    **排序口径必须自洽**：标准表全是行优先（`rules/tables.py` 的口径），
    变换后的格子若不重排，**逐条比对就没法比**。
    """
    return tuple(sorted(cells, key=lambda cell: (cell[1], cell[0])))


# ---------------------------------------------------------------------------
# 按轴生成表
# ---------------------------------------------------------------------------


def shape_table_for_axis(axis: str) -> ShapeTable:
    """取某轴的形状表（方块类型 → 朝向 → 占位格子）。

    **`down` 轴直接给标准表**（不复制第二份真相）；其余轴由标准表**变换**得到。
    """
    if axis not in AXIS_VALUES:
        raise ValueError(f"变体 A 不支持的重力轴：{axis!r}（支持 {AXIS_VALUES}）")
    standard = SHAPE_TABLE["down"]
    if axis == "down":
        return standard
    return MappingProxyType(
        {
            piece: MappingProxyType(
                {
                    rotation: _sort_cells(
                        tuple(_rotate_cell(cell, BOX_SIZE_BY_PIECE[piece], axis) for cell in cells)
                    )
                    for rotation, cells in orientations.items()
                }
            )
            for piece, orientations in standard.items()
        }
    )


def kick_table_for_axis(axis: str) -> KickTable:
    """取某轴的踢墙表（`(from, to)` → 踢墙组 → 候选偏移）。

    **候选顺序不变**（顺序是**优先级**：第一个能落下的就用它）——**只变换坐标**。
    """
    if axis not in AXIS_VALUES:
        raise ValueError(f"变体 A 不支持的重力轴：{axis!r}（支持 {AXIS_VALUES}）")
    standard = KICK_TABLE["down"]
    if axis == "down":
        return standard
    return MappingProxyType(
        {
            transition: MappingProxyType(
                {
                    group: tuple(_rotate_offset(offset, axis) for offset in offsets)
                    for group, offsets in by_group.items()
                }
            )
            for transition, by_group in standard.items()
        }
    )


def gravity_vector_for_axis(axis: str) -> tuple[int, int]:
    """取某轴的重力方向向量（**方向是向量不是 dy** —— 横向重力时 `dy = 0`）。"""
    if axis not in AXIS_VALUES:
        raise ValueError(f"变体 A 不支持的重力轴：{axis!r}（支持 {AXIS_VALUES}）")
    return GRAVITY_VECTORS[axis]


__all__ = [
    "AXIS_VALUES",
    "BOX_SIZE_BY_PIECE",
    "KickTable",
    "ShapeTable",
    "gravity_vector_for_axis",
    "kick_table_for_axis",
    "shape_table_for_axis",
]
