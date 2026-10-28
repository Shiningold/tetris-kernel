"""变体的**登记面**（宿主层 · M4-S1 · 架构 §4.5 · spec-M4 §7.1）。

## 变体是「玩法」，不是「插件」

M3 的自动发现扫的是 `systems/` 目录——**系统**是插件（订阅事件 · 派发命令），
**变体**是玩法（换规则集 · 挂系统）。两者混进同一张注册表，**名字空间会打架**
（系统名与变体名都叫 `growth` 时，谁是谁？）。**所以变体走显式登记表**。

**代价要写明**：「新增能力 = 新增文件」这条判据（M3-D2 / `G3-1`）**在变体这条
路上不成立**——变体要**登记一次**（`register_variant()`），这是**接线**，不是
「新增文件即被发现」。**这条不成立得理直气壮，而不是假装它也成立。**

## 变体标识 ≠ 规则集标识

- **变体标识**（`name`）：用户选的那个玩法（`gravity` · `growth`）。
- **规则集标识**（`ruleId`）：内核换规则集时用的那个标识（`standard` ·
  `gravity` · …）。

**两者不是一回事**：变体 B（落地后生长）**用的就是标准规则集**——玩法差异来自
它挂的那个系统，不是来自规则集。**把两者混成一个字段，变体 B 就没法登记**。

## 一处登记，两处生效

`register_variant()` 一次登记**同时**做两件事：进变体表 · 把它的规则集接进
`RULE_SETS`（**规则集接线表在 `host/assembly.py`**）。**改动路径只有一处**——
变体表与规则集表不会分叉。

**规则集标识已登记时**（变体 B 复用 `standard`）：**不重复登记**，但要确认
**表里那个规则集与变体声明的是同一个**——**一个标识指向两个规则集**会让重放
出来的四项必然不等，而报错看不出原因（架构 §4.5 的接线表口径）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..rules.interface import RuleSet
from .assembly import RULE_SETS, register_rule_set

# ---------------------------------------------------------------------------
# 变体声明
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VariantManifest:
    """一个变体对外的自述：**名字 · 版本 · 规则集 · 附带系统**。

    - `name`：**变体标识**（用户选的那个）——全局唯一（表的键）；
    - `version`：变体自己的版本号；
    - `ruleId`：**规则集标识**——内核换规则集时用它（**与 `name` 不是一回事**）；
    - `ruleSet`：规则集**实例**（内核拿到的"当前这一套"）；
    - `systems`：**附带系统**的名字——变体挂上去的那些插件（变体 B 挂生长系统，
      变体 A 不挂任何系统：它只换规则集）；
    - `description`：一句话说明（**给人看的**，不进任何基线）。

    **字段名即接线约定**：改名会让已登记的变体对不上——但这张表**不在契约基线
    里**（变体是内容不是承诺，见 spec-M4 §6.4）。
    """

    name: str
    version: str
    ruleId: str
    ruleSet: RuleSet
    systems: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        """就地校验（**当场报错，不留到装配时才发现**）。"""
        if not self.name:
            raise ValueError("变体标识不能为空")
        if not self.version:
            raise ValueError(f"变体 {self.name}：版本不能为空")
        if not self.ruleId:
            raise ValueError(f"变体 {self.name}：规则集标识不能为空")
        if len(set(self.systems)) != len(self.systems):
            raise ValueError(f"变体 {self.name}：附带系统里有重复项")
        for system_name in self.systems:
            if not system_name:
                raise ValueError(f"变体 {self.name}：附带系统里有空名字")


# ---------------------------------------------------------------------------
# 已登记变体清单
# ---------------------------------------------------------------------------

#: 已登记变体清单的**内部存储**（唯一真相）。
_VARIANTS: dict[str, VariantManifest] = {}

#: 已登记变体清单（标识 → 声明）——**只读视图**。
VARIANTS: Mapping[str, VariantManifest] = MappingProxyType(_VARIANTS)


def register_variant(manifest: VariantManifest) -> None:
    """登记一个变体（**一处登记，两处生效**）。

    1. **进变体表**（重名即报错——标识是键，后登记的会悄悄顶掉先登记的）；
    2. **把它的规则集接进 `RULE_SETS`**：
       - **标识未登记** → 登记进去（新规则集，变体 A 的情形）；
       - **标识已登记** → **不重复登记**，但要确认**表里那个规则集与变体声明的
         是同一个**——**一个标识指向两个规则集**会让重放出来的四项必然不等，
         而报错只说"四项不等"，看不出原因。

    **规则集必须满足冻结签名**（9 条 · 参数名与个数逐条相等）——不符当场拒。
    """
    if manifest.name in _VARIANTS:
        raise ValueError(f"变体标识已登记：{manifest.name}")
    registered = RULE_SETS.get(manifest.ruleId)
    if registered is None:
        register_rule_set(manifest.ruleId, manifest.ruleSet)
    elif registered is not manifest.ruleSet:
        raise ValueError(
            f"变体 {manifest.name} 的规则集标识 {manifest.ruleId!r} 已经指向另一个规则集"
            "（一个标识只能指向一个规则集——否则重放出来的四项必然不等）"
        )
    _VARIANTS[manifest.name] = manifest


def unregister_variant(name: str) -> VariantManifest | None:
    """从清单里注销一个变体；**没登记过就是没有**（返回 `None`，不顶替）。

    **给测试用**（登记一个假变体验证机制，再撤下来）。
    """
    return _VARIANTS.pop(name, None)


def variant_of(name: str) -> VariantManifest | None:
    """按标识取变体声明（**没有就是没有**——不拿标准玩法顶替）。"""
    return _VARIANTS.get(name)


def variant_catalog() -> Mapping[str, VariantManifest]:
    """**已登记变体的清单**（标识 → 声明）——只读视图。

    **清单与目录是一回事**（不像 M3 的系统：那里有"声明在包里、工厂在宿主侧"
    的分裂）。变体的声明与实现都由登记方给全。
    """
    return VARIANTS


def variant_rule_set(name: str) -> RuleSet | None:
    """取变体的规则集（**变体不在册就是没有**）。"""
    manifest = _VARIANTS.get(name)
    return None if manifest is None else manifest.ruleSet


def variant_systems(name: str) -> tuple[str, ...]:
    """取变体的**附带系统**名字（变体不在册 → 空元组）。

    **附带系统的声明与工厂由系统层管**（`systems/manifests.py`）——变体这里
    只说"要挂这些"，**不认识具体系统**。
    """
    manifest = _VARIANTS.get(name)
    return () if manifest is None else manifest.systems


__all__ = [
    "VARIANTS",
    "VariantManifest",
    "register_variant",
    "unregister_variant",
    "variant_catalog",
    "variant_of",
    "variant_rule_set",
    "variant_systems",
]
