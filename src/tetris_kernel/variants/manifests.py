"""变体目录的**声明扫描**（变体层 · M4-S2 · FR-9.2 · spec-M4 §6.4）。

## 变体层扫自己那一层

变体 B（落地后生长）**也是一个系统**——它订阅 `piece:locked`、派发 `writeCells`
——**它受同一套声明面管**（FR-9.1 / FR-9.2）。但它**住在 `variants/`**，而 M3 的
声明扫描只扫 `systems/` → **它扫不到**。

**扩容为什么不落在 `systems/`**：`systems/` 与 `variants/` **同级**（架构 §2.3），
系统层 import 变体层是**横向依赖** → **依赖方向当场红**。所以**变体层扫自己
那一层**（本模块），**宿主层做汇总**（`host/discovery.py`）——**两边都不越界**。

## 约定：声明与工厂住在变体自己的包里

与系统包同款约定（`systems/manifests.py`）：

- `SYSTEM_MANIFEST`（**声明**：名字 · 版本 · 订阅 · 命令 · 产出者 · 落盘需求）——
  **可缺**（**规则集型变体没有系统**，比如变体 A 只换规则集）；
- `create_system(context)`（**工厂**）——**可缺**（声明与工厂可以分开住）。

**声明的形状是契约面**（`contracts/systems.py` 的 `SystemManifest`）——**形状不
因住在哪一层而变**：变体系统与普通系统**受同一把自证的尺子**（订阅的事件与
派发的命令必须已登记 · 产出者必须在代号表里或就是它自己的名字）。

## 发现只读，不改状态

本模块**只读**：扫到就登记进返回的清单，**不装系统、不订阅、不发事件**。
**导入期只允许定义**——导入纯净度由宿主侧的守卫查（`host/discovery.py` 的
`ImportPurityGuard`）。
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType, ModuleType

from ..contracts.systems import SystemManifest
from ..systems.context import SystemFactory

#: 声明模块的模块名（**约定**：变体包下的 `manifest.py`）。
MANIFEST_MODULE = "manifest"

#: 声明常量的名字（**约定**：模块里的 `SYSTEM_MANIFEST`）。
MANIFEST_ATTRIBUTE = "SYSTEM_MANIFEST"

#: 工厂函数的名字（**约定**：模块里的 `create_system`）。
FACTORY_ATTRIBUTE = "create_system"

#: 变体包自身（**扫描的根**）——本模块住在变体包里，但它是**模块**不是包，
#: 所以路径要从父包取。
_VARIANTS_PACKAGE = importlib.import_module(__package__ or "tetris_kernel.variants")


@dataclass(frozen=True)
class VariantModule:
    """一个变体包的「自述」：**包名 · 声明 · 工厂**。

    **声明可以是 `None`**：**规则集型变体没有系统**（变体 A 只换规则集，
    不挂任何系统）——**没有声明不是错误**，它只是"这个变体不带系统"。
    """

    package: str
    manifest: SystemManifest | None
    factory: SystemFactory | None
    module: ModuleType


def _module_of(root: ModuleType, package_name: str) -> ModuleType | None:
    """取一个变体包的自述模块；**没有就是没有**（返回 `None`，不顶替）。"""
    loaded = importlib.import_module(f"{root.__name__}.{package_name}")
    module_names = {info.name for info in pkgutil.iter_modules(loaded.__path__)}
    if MANIFEST_MODULE not in module_names:
        return None
    return importlib.import_module(f"{root.__name__}.{package_name}.{MANIFEST_MODULE}")


def discover_variant_modules(package: ModuleType | None = None) -> tuple[VariantModule, ...]:
    """扫描变体目录，返回**所有变体包**（**按包名排序**，结果可复现）。

    `package` 省略时扫**变体层自己**；**测试可以递给它一个临时包**——「变体带
    一个系统就多一个声明」这件事得能**在临时目录里真验**。

    **重名即报错**：声明的名字是清单的键，两个变体抢一个名字时后扫到的会悄悄
    顶掉先扫到的——那正是清单要防的分叉，当场炸掉。
    """
    root = _VARIANTS_PACKAGE if package is None else package
    found: list[VariantModule] = []
    seen: dict[str, str] = {}
    for info in sorted(pkgutil.iter_modules(list(root.__path__)), key=lambda item: item.name):
        if not info.ispkg:
            continue
        module = _module_of(root, info.name)
        if module is None:
            # **没有 `manifest.py` 的变体包**：规则集型变体（只换规则集）——
            # **没有声明不是错误**。
            continue
        manifest = getattr(module, MANIFEST_ATTRIBUTE, None)
        if manifest is not None and not isinstance(manifest, SystemManifest):
            raise TypeError(
                f"{info.name}.{MANIFEST_MODULE} 的 {MANIFEST_ATTRIBUTE} "
                f"不是 SystemManifest（是 {type(manifest).__name__}）"
            )
        if manifest is not None:
            if manifest.name in seen:
                raise ValueError(
                    f"变体系统名重复：{manifest.name}（{seen[manifest.name]} 与 {info.name}）"
                )
            seen[manifest.name] = info.name
        factory = getattr(module, FACTORY_ATTRIBUTE, None)
        if factory is not None and not callable(factory):
            raise TypeError(f"{info.name}.{MANIFEST_MODULE} 的 {FACTORY_ATTRIBUTE} 不可调用")
        found.append(
            VariantModule(
                package=info.name,
                manifest=manifest,
                factory=factory,
                module=module,
            )
        )
    return tuple(found)


def discover_variant_manifests() -> Mapping[str, SystemManifest]:
    """扫描变体目录，返回**变体带系统的声明清单**（名字 → 声明）。

    **规则集型变体不在这里**（它没有系统声明）——**它是规则集，不是系统**。
    """
    return MappingProxyType(
        {
            item.manifest.name: item.manifest
            for item in discover_variant_modules()
            if item.manifest is not None
        }
    )


__all__ = [
    "FACTORY_ATTRIBUTE",
    "MANIFEST_ATTRIBUTE",
    "MANIFEST_MODULE",
    "VariantModule",
    "discover_variant_manifests",
    "discover_variant_modules",
]
