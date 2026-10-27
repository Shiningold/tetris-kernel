"""系统清单的**发现**（系统层 · M3 · FR-9.2 · M3-D2）。

**宿主不维护手写名单**——它扫描系统目录，找出自带声明的系统。

## 约定：声明住在系统自己的包里

每个系统包下有一个 `manifest` 模块，里面放着 `SYSTEM_MANIFEST` 常量
（`contracts.systems.SystemManifest`）。**声明与工厂住在同一个包里**——新增一个
系统 = **新增一个包 + 一个声明文件**，**既有文件零改动**（M3-D2 / `G3-1`）。

**为什么声明不集中登记在一张名单里**：集中登记意味着新增一个系统要改一个既有
文件（那张名单），而 `G3-1` 判的正是「落地提交只有新增文件」——当场不成立。

## 发现只读，不改状态

本模块**只读**：扫到就登记进返回的映射，**不装系统、不订阅、不发事件**。
装配是宿主的事（`host/registry.py`，S3）。

**导入期只允许定义**：被扫描的包不许有导入副作用（导入即订阅 / 即开文件）。
本模块自己也不许——它 import 系统包只是为了读那个常量。
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Mapping
from types import MappingProxyType

from ..contracts.systems import SystemManifest

#: 声明模块的模块名（**约定**：系统包下的 `manifest.py`）。
MANIFEST_MODULE = "manifest"

#: 声明常量的名字（**约定**：模块里的 `SYSTEM_MANIFEST`）。
MANIFEST_ATTRIBUTE = "SYSTEM_MANIFEST"

#: 系统包自身（**扫描的根**）——本模块住在系统包里，但它是**模块**不是包，
#: 所以路径要从父包取。
_SYSTEMS_PACKAGE = importlib.import_module(__package__ or "tetris_kernel.systems")


def _manifest_of(package_name: str) -> SystemManifest | None:
    """取一个系统包的声明；**没有声明就是没有**（返回 `None`，不顶替）。"""
    package = importlib.import_module(f"{_SYSTEMS_PACKAGE.__name__}.{package_name}")
    module_names = {info.name for info in pkgutil.iter_modules(package.__path__)}
    if MANIFEST_MODULE not in module_names:
        return None
    module = importlib.import_module(
        f"{_SYSTEMS_PACKAGE.__name__}.{package_name}.{MANIFEST_MODULE}"
    )
    manifest = getattr(module, MANIFEST_ATTRIBUTE, None)
    if manifest is None:
        return None
    if not isinstance(manifest, SystemManifest):
        raise TypeError(
            f"{package_name}.{MANIFEST_MODULE} 的 {MANIFEST_ATTRIBUTE} "
            f"不是 SystemManifest（是 {type(manifest).__name__}）"
        )
    return manifest


def discover_manifests() -> Mapping[str, SystemManifest]:
    """扫描系统目录，返回**已声明的系统清单**（名字 → 声明）。

    **重名即报错**：名字是清单的键，两个系统抢一个名字时后扫到的会悄悄顶掉
    先扫到的——那正是清单要防的分叉，当场炸掉。
    """
    found: dict[str, SystemManifest] = {}
    for info in pkgutil.iter_modules(list(_SYSTEMS_PACKAGE.__path__)):
        if not info.ispkg:
            continue
        manifest = _manifest_of(info.name)
        if manifest is None:
            continue
        if manifest.name in found:
            raise ValueError(f"系统名重复：{manifest.name}")
        found[manifest.name] = manifest
    return MappingProxyType(found)


__all__ = [
    "MANIFEST_ATTRIBUTE",
    "MANIFEST_MODULE",
    "discover_manifests",
]
