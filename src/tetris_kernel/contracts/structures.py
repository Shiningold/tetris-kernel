"""命名结构注册表（类型文法形态 9 的唯一权威清单）。

公共叶子——**不 import 任何 `tetris_kernel.*` 模块，也不依赖第三方**；只用标准库
（架构 §2.3 / §4.1）。本模块只做「声明」，不做「行为」。

## 这张表是什么

**结构名 → 成员表**。形态 9（命名结构引用，如 `Board` / `RuleContext`）的结构名
**不是自由命名的别名**——文法表附加规则 4 要求"没登记的结构名一律不合规"，
S6 的基线脚本按成员表展开计算哈希（架构 §7.6）。

**它是类型文法层面的东西，不是某个模块的私产**：快照（`Board` / `Active`）、
放置（`PlacementInfo`）、规则（`RuleContext` 等五项）三处都在用它，所以它住在
自己的模块里，而不是寄居在 `snapshot.py`。

## 成员表从声明推导，不另抄一份

- `Board` / `Active`：从 `snapshot.py` 的 `BOARD_FIELDS` / `ACTIVE_FIELDS` 取。
- 其余结构：从 frozen dataclass 的字段注解取（`PlacementInfo` 与规则侧的五个
  结构）——**dataclass 的注解就是声明本体**，另抄一张表就是抄一份会漂移的副本。

**回验从这张注册表推导结构名集合，不另抄一份名单**（测试侧的纪律）——抄一份的
后果是：成员表改了名，回验还认旧名，规则与机检悄悄分叉。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType

from .placement import PlacementInfo
from .rules import ClearGroup, PlacementClass, RuleContext, ScoreDelta, ScoreState
from .snapshot import ACTIVE_FIELDS, BOARD_FIELDS, FieldSpec


def _member_types(cls: type) -> Mapping[str, str]:
    """从 frozen dataclass 推出成员表：成员名 → 类型表达式。

    类型表达式取自 dataclass 的字段注解（`from __future__ import annotations`
    下它就是源码里的字符串，跨进程稳定）——**注解即声明**，这里不重述一遍。

    **注解不是字符串就当场报错**：没有 future import 的模块里，`field.type`
    是运行时类型对象，`str()` 出来是 `"<class 'int'>"` 这种带模块前缀的写法
    ——既不是形态 9 的标识符，也不是合法类型串，而它会被基线哈希进去。
    这个假设必须**受检**，不能靠"每个模块都记得写 future import"。

    参数标注用 `type` 而不是 `type[DataclassInstance]`：后者只存在于类型存根
    （typeshed），运行时 `dataclasses` 模块里没有它，import 会在导入期就炸。
    """
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls!r} 不是 dataclass，推不出成员表")
    members: dict[str, str] = {}
    for field in dataclasses.fields(cls):
        if not isinstance(field.type, str):
            raise TypeError(
                f"{cls.__name__}.{field.name} 的注解不是字符串（是 {field.type!r}）："
                "该模块缺 `from __future__ import annotations`，"
                "成员表会产出非法类型串"
            )
        members[field.name] = field.type
    return MappingProxyType(members)


def _table_types(table: Mapping[str, FieldSpec]) -> Mapping[str, str]:
    """从字段声明表推出成员表：成员名 → 类型表达式。"""
    return MappingProxyType({name: spec.type for name, spec in table.items()})


#: **命名结构注册表**（类型文法形态 9）：结构名 → 成员表（成员名 → 类型串）。
#:
#: 结构名的来源与顺序：快照的两个（`Board` / `Active`）→ 放置的一个
#: （`PlacementInfo`）→ 规则的五个（`RuleContext` / `PlacementClass` /
#: `ClearGroup` / `ScoreState` / `ScoreDelta`）。**顺序不携带语义**，但一经写入
#: 不重排（重排会让基线哈希变一次，而它本不该变）。
#:
#: 成员表的值是**类型表达式字符串**，不是 `FieldSpec`——`FieldSpec` 带中文说明，
#: 说明留在各自的声明处（`BOARD_FIELDS` 与各 dataclass 的 docstring），
#: 注册表只负责"这个结构有哪些成员、各是什么类型"。
NAMED_STRUCTS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "Board": _table_types(BOARD_FIELDS),
        "Active": _table_types(ACTIVE_FIELDS),
        "PlacementInfo": _member_types(PlacementInfo),
        "RuleContext": _member_types(RuleContext),
        "PlacementClass": _member_types(PlacementClass),
        "ClearGroup": _member_types(ClearGroup),
        "ScoreState": _member_types(ScoreState),
        "ScoreDelta": _member_types(ScoreDelta),
    }
)

__all__ = ["NAMED_STRUCTS"]
