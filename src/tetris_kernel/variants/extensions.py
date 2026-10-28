"""变体的**扩展槽自述面**（变体层 · M4-S5 · 架构 §4.5 / §5.4 · spec-M4 §7.4）。

## 协议归声明方

**变体要告诉宿主"我要往扩展槽里放什么"，宿主才知道去问谁。** 这条约定写在
**变体层**而不是宿主层，理由有两条，缺一不可：

- **变体层禁 import 宿主**（架构 §2.3 / §4.1 / §7.7）→ **协议不能住在宿主层**
  （那样变体就得反过来 import 宿主，红线当场破）；
- **契约面零变更**（`G4-7`）→ **协议不能住进 `contracts/`**（那是 v1 定稿的
  承诺面，M4 一个字节都不许动）。

所以协议住在**变体层**：变体 import 它（同一层，合法），宿主也 import 它
（宿主层在最上，合法）——**双方都不越界**。

## 结构化类型：实现方不需要 import 也能被认出来

`ExtensionProvider` 是 `Protocol`——**宿主用 `isinstance` 认它**（运行时只查
方法在不在），**变体只要有同名方法就算数**，**不必 import 这个模块**。
**这是变体层零依赖的关键**：协议是"形状"，不是"基类"。

## 只放标量

扩展槽的形状由内核在 M3 定死（架构 §5.4）：`tuple[tuple[str, JsonScalar], ...]`，
**只放标量**（`int` / `float` / `str` / `bool` / `None`），**禁止容器与嵌套**——
**复杂状态由变体自己持有**，进槽的只是**摘要**。

**这里的类型别名是本地复制的**（不是从内核抄的）：变体层禁 import 内核
（架构 §2.3），而契约层没有这一条（`G4-7` 不许加）→ **只能自己写一份**。
**它是结构化类型的一部分，不构成第二个真相**：值最终由内核的指纹算法编码，
**编码规则只有内核那一份**。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

#: 扩展槽里允许的值（**架构 §5.4：只放标量，禁止容器与嵌套**）。
#:
#: **布尔与整数是两种东西**——内核的 TLV 编码给它们不同的标签
#: （`kernel/snapshot.py`）→ 同一个值写 `True` 与写 `1` **摘要不同**。
ExtensionScalar = int | float | str | bool | None

#: 一条扩展条目：**（key · 值）**。
ExtensionEntry = tuple[str, ExtensionScalar]

#: key 的**命名空间分隔符**（宿主用它拼 `<提供者名>.<裸名>`）。
KEY_SEPARATOR = "."


@runtime_checkable
class ExtensionProvider(Protocol):
    """**能往扩展槽里放东西的东西**（变体侧的自述形状）。

    **实现方只需要有同名方法**——`isinstance` 走运行时检查，**查方法在不在**
    （`runtime_checkable` 的口径），**不查继承关系**。

    **返回的是"裸名"**：key 的命名空间由**宿主**加（`<提供者名>.<裸名>`）——
    命名空间是全局责任，**不该由变体自己拼**（否则两个变体会撞名）。
    """

    def extension_entries(self) -> Iterable[ExtensionEntry]:
        """本变体要往扩展槽里放的条目（**只放标量**）。"""
        ...


__all__ = [
    "KEY_SEPARATOR",
    "ExtensionEntry",
    "ExtensionProvider",
    "ExtensionScalar",
]
