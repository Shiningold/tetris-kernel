"""宿主侧的**扩展槽合并器**（宿主层 · M4-S5 · 架构 §4.5 / §5.4 · spec-M4 §7.4）。

## 它做什么

**帧末**问各个变体"你要往扩展槽里放什么" → 汇总成一个扩展槽 → **调内核既有的
入口 `_set_extensions`**（**M0 已定型的入口** → **冻结区零改动**）。

**内核不理解内容**（架构 §5.4）：它只原样携带、按 key 排序、算摘要。
**校验全在宿主这一侧做**——容器当场拒、key 重复当场炸。

## 谁提供条目

**已装上且实现了 `extension_entries()` 的系统实例**（鸭子类型 · 结构化协议）。

**为什么按"系统"而不是按"变体"找**：变体 A（只换规则集）**没有系统** →
**它天然没有扩展条目**（扩展槽空 → 扩展指纹是空槽的摘要）；变体 B 挂了生长
系统 → **它就是提供者**。**"提供扩展条目"是系统的一个可选能力**，不是变体的
另一个登记字段——**变体登记面（`host/variants.py`）不在本次改动范围内**。

## key 的命名空间由宿主加

合并后的 key = `<提供者名>.<变体给的裸名>`（如 `growth.crystalCount`）。

**为什么不让变体自己拼**：命名空间是**全局责任**——两个变体都叫"crystalCount"
时，谁的算谁的？**宿主加前缀，两个变体就不会互相顶掉**；而且**变体层禁 import
宿主**（架构 §2.3），它也不该知道全局命名空间。

## 合并**不另发事件**

扩展槽的每次变化**都源于一条内核事件**（变体在事件回调里更新自己的状态）→
**已被那条事件覆盖**（N5）。**`state:changed` 不是扩展槽的通报渠道**：它的判据
是 `snapshot.version`，而扩展槽合并**不改版本号**（内核的 `_set_extensions` 不
`_touch()`），负载 13 个字段也**不含扩展槽**（契约固定）→ **发了也看不出变了
什么**。

**扩展槽的权威观测面是快照**：`snapshot().extensions` 与
`snapshot().extensionFingerprint`（内核每次 `snapshot()` 现算）。

## 走内核的私有入口：这不叫"开后门"

`Kernel._set_extensions` 是**内核唯一**的扩展槽入口，**M0 已定型**——
**新开一个公开入口就是冻结区破例**（M4-D1）。**它是内核对宿主开放的既有
约定**，不是变体自己往内核写（变体连 import 内核都不行，架构 §2.3）。
"""

from __future__ import annotations

from collections.abc import Mapping

from ..kernel.core import Kernel
from ..variants.extensions import (
    KEY_SEPARATOR,
    ExtensionEntry,
    ExtensionProvider,
    ExtensionScalar,
)

#: 扩展槽里允许的类型（架构 §5.4：只放标量，禁止容器与嵌套）。
#:
#: **`bool` 是 `int` 的子类**——`isinstance(True, int)` 为真 → 布尔**合法**
#: （内核的 TLV 给布尔单独的标签，写 `True` 与写 `1` 摘要不同）。
_SCALAR_TYPES = (bool, int, float, str)


class ExtensionMergeError(RuntimeError):
    """**扩展槽合并失败**（结构错误 · 当场炸，不静默）。

    **两种触发**：

    - **值不是标量**（容器 / 嵌套）→ 违反架构 §5.4 的"只放标量"；
    - **key 重复**（同一提供者给了两个一样的裸名，或拼完前缀后撞了）→
      架构 §5.4 明写**重复即结构错误，不"去重取末值"**（去重会**静默丢掉
      一个扩展**——那正是扩展槽要防的事）。

    **为什么不"记错然后继续"**：扩展槽是**回放校验的第五项**（`G4-2`）——
    **少一个扩展 = 变体局回放四项全等而第五项不等**，而且**看不出原因**。
    **当场炸比静默错好**。
    """


def is_scalar(value: object) -> bool:
    """值是不是**扩展槽允许的标量**（`int` / `float` / `str` / `bool` / `None`）。

    **`None` 合法**：它是 TLV 标签表里的一项（`kernel/snapshot.py`）——
    **"这一项没有值"也是一条信息**。
    """
    return value is None or isinstance(value, _SCALAR_TYPES)


def namespace_key(provider_name: str, raw_key: str) -> str:
    """把变体给的**裸名**拼成**带命名空间的 key**（`<提供者名>.<裸名>`）。"""
    return f"{provider_name}{KEY_SEPARATOR}{raw_key}"


def merge_extension_entries(
    providers: Mapping[str, ExtensionProvider],
) -> tuple[ExtensionEntry, ...]:
    """把各提供者的条目汇总成一个扩展槽（**key 唯一 · 只放标量**）。

    - `providers`：**提供者名 → 提供者**（名字来自系统名 → 兼作 key 的命名空间）。

    **校验在这里做，不在内核做**：内核只原样携带、不理解内容（架构 §5.4）。

    **空输入返回空槽**：没有提供者 = 没有扩展 = **不是错误**（变体 A 就是
    这种情况）。
    """
    merged: dict[str, ExtensionScalar] = {}
    for provider_name, provider in providers.items():
        entries = provider.extension_entries()
        for raw_key, value in entries:
            if not isinstance(raw_key, str) or not raw_key:
                raise ExtensionMergeError(
                    f"扩展条目 {provider_name} 的 key 不是非空字符串：{raw_key!r}"
                )
            if not is_scalar(value):
                raise ExtensionMergeError(
                    f"扩展条目 {provider_name}.{raw_key} 的值不是标量："
                    f"{type(value).__name__}（架构 §5.4：只放标量，禁止容器与嵌套）"
                )
            key = namespace_key(provider_name, raw_key)
            if key in merged:
                raise ExtensionMergeError(
                    f"扩展槽的 key 重复：{key!r}（架构 §5.4：不'去重取末值'）"
                )
            merged[key] = value
    # **按 key 排序**（架构 §5.4 的规范化口径：同一组扩展、不同合并顺序
    # → 同一个快照）——内核的 `build_extensions` 也会再排一次，**这里是
    # 提前对齐**，好让"合并结果"本身就可复现。
    return tuple(sorted(merged.items(), key=lambda item: item[0]))


def apply_extensions(
    kernel: Kernel,
    providers: Mapping[str, ExtensionProvider],
) -> tuple[ExtensionEntry, ...]:
    """合并各提供者的条目，**写进内核的扩展槽**，返回合并结果。

    **走内核既有的私有入口**（`Kernel._set_extensions` · **M0 已定型**）——
    **不新开入口**（那才是冻结区破例）。

    **内核不 `_touch()`** → **版本号不变** → **不触发 `state:changed`**
    （那条事件的判据是版本号变化；扩展槽的观测面是快照，见模块头）。
    """
    entries = merge_extension_entries(providers)
    kernel._set_extensions(entries)
    return entries


def providers_of(instances: Mapping[str, object]) -> dict[str, ExtensionProvider]:
    """从**一批具名实例**里挑出扩展槽提供者（**按名字建命名空间**）。

    - `instances`：**实例名 → 实例**（名字来自系统名 → 兼作 key 的命名空间，
      如 `growth`）。

    **鸭子类型**：`isinstance` 走运行时检查，**只查 `extension_entries` 方法在
    不在**——**不查继承关系** → **实现方不必 import 协议模块**。

    **没有这个方法的实例不是提供者**（不是错误）：扩展条目是**可选能力**。
    """
    providers: dict[str, ExtensionProvider] = {}
    for name, instance in instances.items():
        if isinstance(instance, ExtensionProvider):
            providers[name] = instance
    return providers


__all__ = [
    "ExtensionMergeError",
    "apply_extensions",
    "is_scalar",
    "merge_extension_entries",
    "namespace_key",
    "providers_of",
]
