"""变体 A 的**规则集**（重力可旋转 · 9 条签名 · spec-M4 §7.2）。

## 这个变体重写了几条

**三条**：`gravity_vector`（重力方向随轴变）· `shape_cells`（形状表随轴变）·
`kick_offsets`（踢墙表随轴变）。

**其余六条复用标准实现**（`rules.standard`）——**消行扫描 · 放置分类 · 计分 ·
软降计分 · 重力间隔 · 边界拓扑**。**为什么复用**：这几条的口径**与重力轴无关**
（或按 M4 的口径**就是满行消**），**重抄一份就是第二个真相**。

**消行为什么仍是"满行消"**：**内核的塌落是按行的**——`step_clear` 取的是
`group.index` 当**行号**，`step_collapse` 把被消的行删掉、上面补空行。
**横向重力若要"消列"**，内核的塌落就得改成**轴感知**——**那是内核改动 =
冻结区破例 = M4-D1 破**（spec-M4 §7.2 的 K4）。**这条口径写进变体自己的 README。**

## 轴是**实例的常量**，不是 `ctx` 的字段

**内核构造 `RuleContext` 时 `axis` 恒为 `GRAVITY_AXIS`**（`kernel/core.py:114`
定义 `"down"` · `:2095` 写进 `ctx`）——**内核自己不读 `ctx.axis`**（全库唯一的
消费者是 `rules/standard.py`）。**所以"重力方向随 `ctx.axis` 变"做不到**：
`ctx.axis` 永远是 `down`。

**口径**：**变体规则集实例自己持一个不可变的轴常量**，`gravity_vector`
**忽略 `ctx.axis`**、返回自己那套向量。**实例持不可变常量不违反"规则集无状态"**
（架构 §7.3.1 纪律 1 禁的是**可变**状态；存档只存规则集**标识**）。

**"切轴" = 换规则集**：内核没有"改轴"这条命令，而 M3 的 `switchRuleSet` 本就
存在——**换的是整套规则集，不是轴**。**开局前选轴**（局中换规则集内核本就禁止
且会重置局面）。

## 签名是冻结的

9 条签名的**参数名与个数**由 `rules/interface.py` 的 `signature_mismatches()`
逐条比对——**变体不重新发明签名**：复用标准实现的六条天然对得上，重写的三条
**参数名照抄**（`ctx` · `pieceType` · `rotation` · `fromRotation` · `toRotation`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...contracts.rules import RuleContext
from ...rules.interface import (
    ClassifyPlacement,
    GravityInterval,
    GravityVector,
    KickOffsets,
    ScanClears,
    ScoreClear,
    ShapeCells,
    SoftDropScore,
    WrapCell,
)
from ...rules.standard import (
    classify_placement,
    gravity_interval,
    scan_clears,
    score_clear,
    soft_drop_score,
    wrap_cell,
)
from ...rules.tables import KICK_GROUP_BY_PIECE, ROTATION_COUNT
from .tables import (
    AXIS_VALUES,
    gravity_vector_for_axis,
    kick_table_for_axis,
    shape_table_for_axis,
)

# ---------------------------------------------------------------------------
# 三条重写的实现（按轴绑定）
# ---------------------------------------------------------------------------


def _gravity_vector_for(axis: str) -> GravityVector:
    """造某个轴的 `gravity_vector`（**闭包持有轴常量**）。"""

    def gravity_vector(ctx: RuleContext) -> tuple[int, int]:
        """重力方向向量——**方向是向量不是 dy**（横向重力时 `dy = 0`）。"""
        return gravity_vector_for_axis(axis)

    return gravity_vector


def _shape_cells_for(axis: str) -> ShapeCells:
    """造某个轴的 `shape_cells`（**表由规则集整体提供**，内核不做变换）。"""

    def shape_cells(
        ctx: RuleContext,
        pieceType: str,
        rotation: int,
    ) -> tuple[tuple[int, int], ...]:
        """取形状表：**按本实例的轴**（不是 `ctx.axis`——它恒为 `down`）。"""
        table = shape_table_for_axis(axis)
        piece_table = table.get(pieceType)
        if piece_table is None:
            raise ValueError(f"未知方块类型 {pieceType!r}")
        cells = piece_table.get(rotation % ROTATION_COUNT)
        if cells is None:
            raise ValueError(f"方块 {pieceType!r} 没有朝向 {rotation % ROTATION_COUNT}")
        return cells

    return shape_cells


def _kick_offsets_for(axis: str) -> KickOffsets:
    """造某个轴的 `kick_offsets`（**偏移是标准偏移的轴映射**，不是真 SRS）。"""

    def kick_offsets(
        ctx: RuleContext,
        pieceType: str,
        fromRotation: int,
        toRotation: int,
    ) -> tuple[tuple[int, int], ...]:
        """取踢墙表：**按本实例的轴**（偏移按轴做线性映射）。"""
        table = kick_table_for_axis(axis)
        by_group = table.get((fromRotation % ROTATION_COUNT, toRotation % ROTATION_COUNT))
        if by_group is None:
            raise ValueError(f"没有从朝向 {fromRotation} 到朝向 {toRotation} 的踢墙偏移")
        group = KICK_GROUP_BY_PIECE.get(pieceType)
        if group is None:
            raise ValueError(f"未知方块类型 {pieceType!r}")
        return by_group[group]

    return kick_offsets


# ---------------------------------------------------------------------------
# 规则集（整体替换入口）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GravityRuleSet:
    """变体 A 的规则集：**9 条签名的组合入口**（FR-4.9）。

    **不可变、无状态**：唯一的可变信息是**轴**——而它是**构造时定下的不可变
    常量**（架构 §7.3.1 纪律 1 禁的是**可变**状态）。

    **只有三条随轴变**（`gravity_vector` · `shape_cells` · `kick_offsets`）；
    其余六条**复用标准实现**。
    """

    #: 本规则集的**重力轴**（`down` / `right` / `left`）——**构造时定死**。
    axis: str = "right"

    #: 随轴变的三条（**`init=False`**：它们由 `axis` 派生，不是独立输入）。
    gravity_vector: GravityVector = field(init=False)
    shape_cells: ShapeCells = field(init=False)
    kick_offsets: KickOffsets = field(init=False)

    # **复用标准实现的六条**（口径与重力轴无关，或按 K4 就是满行消）。
    wrap_cell: WrapCell = wrap_cell
    gravity_interval: GravityInterval = gravity_interval
    scan_clears: ScanClears = scan_clears
    classify_placement: ClassifyPlacement = classify_placement
    score_clear: ScoreClear = score_clear
    soft_drop_score: SoftDropScore = soft_drop_score

    def __post_init__(self) -> None:
        """校验轴的取值域，并把三条实现**绑到本实例的轴**上。"""
        if self.axis not in AXIS_VALUES:
            raise ValueError(f"变体 A 不支持的重力轴：{self.axis!r}（支持 {AXIS_VALUES}）")
        object.__setattr__(self, "gravity_vector", _gravity_vector_for(self.axis))
        object.__setattr__(self, "shape_cells", _shape_cells_for(self.axis))
        object.__setattr__(self, "kick_offsets", _kick_offsets_for(self.axis))


def gravity_rule_set(axis: str = "right") -> GravityRuleSet:
    """造一份变体 A 的规则集（**轴是参数**）。

    **给宿主层用**：登记变体时**一份轴一份实例**——**"切轴" = 换规则集**。
    """
    return GravityRuleSet(axis=axis)


__all__ = [
    "GravityRuleSet",
    "gravity_rule_set",
]
