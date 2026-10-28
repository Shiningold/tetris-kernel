"""**内建变体的登记**（宿主层 · M4-S3 · spec-M4 §7.2）。

## 变体是"接线"，登记在宿主层

变体**不是插件**——它不参与自动发现，**要登记一次**（`register_variant()`）。
**登记这件事住在宿主层**：宿主是**唯一知道怎么把部件拼起来的地方**（架构 §4.1），
而**变体层不许 import 宿主层**（依赖方向）。

**本模块提供的是"内建变体的清单 + 幂等登记"**——**变体 A 的三个轴各一份规则集
实例**（`gravity-down` / `gravity-right` / `gravity-left`）。

## 为什么是三份实例而不是一个会变的实例

**轴是规则集实例的不可变常量**（内核恒传 `ctx.axis = "down"` → 轴绑在实例上，
spec-M4 §7.2 的 K5）。**所以"切轴" = 换规则集**——**一份轴一份实例**，
登记三份，**开局前选一个**。

**不做 `up` 轴**：内核的碰撞判定把 `y < 0` 当作出生区 → 向上重力的方块会飞出
棋盘（理由写在变体自己的 README）。

## 幂等

`register_builtin_variants()` **可以反复调**：已登记的**跳过**，不报错——
**CLI 与测试都要能安全地调它**（登记是全局状态，**重复登记不是错误**）。
"""

from __future__ import annotations

from ..rules.standard import STANDARD_RULESET
from ..variants.gravity import AXIS_VALUES, gravity_rule_set
from .variants import VariantManifest, register_variant, variant_of

#: 内建变体的**版本号**（变体自己的版本 · 不是内核版本）。
BUILTIN_VARIANT_VERSION = "1.0.0"

#: 变体 B 的**系统名**（**挂在变体上的那个系统** · 住在 `variants/growth/`）。
GROWTH_SYSTEM = "growth"

#: 内建变体标识 → 一句话说明（**给人看的**，不进任何基线）。
_DESCRIPTIONS: dict[str, str] = {
    "gravity-down": "重力向下（与标准玩法等价 · 走变体 A 的规则集路径）",
    "gravity-right": "重力向右：方块沿横向落下，贴着右墙堆起来",
    "gravity-left": "重力向左：方块沿横向落下，贴着左墙堆起来",
    "growth": "落地后生长：方块落地后在上方长出晶体（受控写入路径）",
}


def builtin_variant_specs() -> tuple[VariantManifest, ...]:
    """内建变体的**声明清单**（**每次现造** —— 不共享可变状态）。

    **变体 A 的三个轴**：规则集标识与变体标识同名——玩法差异**完全来自规则集**，
    它不挂任何系统（**附带系统为空**）。

    **变体 B（落地后生长）**：**用的是标准规则集**（**规则集一个字不改**），
    **玩法差异来自它挂的那个系统**（`growth`）——**这正是"变体标识 ≠ 规则集
    标识"那条口径的兑现**：变体 B 的规则集就是标准的。
    """
    specs = [
        VariantManifest(
            name=f"gravity-{axis}",
            version=BUILTIN_VARIANT_VERSION,
            ruleId=f"gravity-{axis}",
            ruleSet=gravity_rule_set(axis),
            systems=(),
            description=_DESCRIPTIONS.get(f"gravity-{axis}", ""),
        )
        for axis in AXIS_VALUES
    ]
    specs.append(
        VariantManifest(
            name="growth",
            version=BUILTIN_VARIANT_VERSION,
            ruleId="standard",
            ruleSet=STANDARD_RULESET,
            systems=(GROWTH_SYSTEM,),
            description=_DESCRIPTIONS.get("growth", ""),
        )
    )
    return tuple(specs)


def register_builtin_variants() -> tuple[str, ...]:
    """把**内建变体**登记进变体表（**幂等** —— 已登记的跳过）。

    **返回本次真正登记的标识**（便于测试断言"登记了什么"）。
    """
    registered: list[str] = []
    for manifest in builtin_variant_specs():
        if variant_of(manifest.name) is not None:
            continue
        register_variant(manifest)
        registered.append(manifest.name)
    return tuple(registered)


__all__ = [
    "BUILTIN_VARIANT_VERSION",
    "builtin_variant_specs",
    "register_builtin_variants",
]
