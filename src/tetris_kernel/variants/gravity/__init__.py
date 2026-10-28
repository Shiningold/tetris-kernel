"""变体 A：**重力可旋转**（规则集替换路径 · M4-S3 · spec-M4 §7.2）。

## 这个变体是什么

**换掉整套规则集**（9 条签名），**内核与 `rules/` 一个字节都没动**——它是
**"换玩法不改内核"这条路的第一个真样例**。

**玩法**：重力方向不再是"下"，而是**右**或**左**——方块沿横向落下、贴着侧墙
堆起来。**形状表与踢墙表随轴变**（`M4-A5`）——**内核不做变换，表由规则集整体
提供**（架构 §7.3.1 纪律 2）。

## 三个必须知道的限制（都写在这个变体自己的文档里）

1. **轴是规则集实例的常量**——内核恒传 `ctx.axis = "down"`，**轴绑在实例上**。
2. **消行仍是"满行消"**——内核的塌落是按行的，**"消列"要改内核**（M4-D1 破）。
3. **玩家的操作仍是屏幕方向的**——软降硬编码向下、左右移硬编码 `x ± 1`。

**详见同目录的 `README.md`**（变体自己的文档：语义与取舍写在那里）。
"""

from __future__ import annotations

from .rules import GravityRuleSet, gravity_rule_set
from .tables import (
    AXIS_VALUES,
    BOX_SIZE_BY_PIECE,
    gravity_vector_for_axis,
    kick_table_for_axis,
    shape_table_for_axis,
)

__all__ = [
    "AXIS_VALUES",
    "BOX_SIZE_BY_PIECE",
    "GravityRuleSet",
    "gravity_rule_set",
    "gravity_vector_for_axis",
    "kick_table_for_axis",
    "shape_table_for_axis",
]
