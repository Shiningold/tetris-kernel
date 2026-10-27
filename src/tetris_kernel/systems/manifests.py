"""系统清单的**发现**（系统层 · M3 · FR-9.2 · M3-D2）。

**宿主不维护手写名单**——它扫描系统目录，找出自带声明的系统。

## 约定：声明与工厂住在系统自己的包里

每个系统包下有一个 `manifest` 模块，里面放着：

- `SYSTEM_MANIFEST`（**声明**：名字 · 版本 · 订阅 · 命令 · 产出者 · 落盘需求）——
  **必填**；
- `create_system(context)`（**工厂**：拿一份安装上下文造出实例）——**可缺**
  （M2 的三个内建可观测系统的工厂住在宿主层：它们的落盘出口由宿主注入）。

**声明与工厂住在同一个包里**——新增一个系统 = **新增一个包 + 一个自述文件**，
**既有文件零改动**（M3-D2 / `G3-1`）。

**为什么声明不集中登记在一张名单里**：集中登记意味着新增一个系统要改一个既有
文件（那张名单），而 `G3-1` 判的正是「落地提交只有新增文件」——当场不成立。

## 发现只读，不改状态

本模块**只读**：扫到就登记进返回的清单，**不装系统、不订阅、不发事件**。
装配是宿主的事（`host/registry.py`）。

**导入期只允许定义**：被扫描的包不许有导入副作用（导入即订阅 / 即开文件）。
**导入纯净度由宿主侧的守卫查**（`host/discovery.py` 的 `ImportPurityGuard`）。
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType, ModuleType

from ..contracts.systems import SystemManifest
from .context import SystemFactory

#: 声明模块的模块名（**约定**：系统包下的 `manifest.py`）。
MANIFEST_MODULE = "manifest"

#: 声明常量的名字（**约定**：模块里的 `SYSTEM_MANIFEST`）。
MANIFEST_ATTRIBUTE = "SYSTEM_MANIFEST"

#: 工厂函数的名字（**约定**：模块里的 `create_system`）。
FACTORY_ATTRIBUTE = "create_system"

#: 系统包自身（**扫描的根**）——本模块住在系统包里，但它是**模块**不是包，
#: 所以路径要从父包取。
_SYSTEMS_PACKAGE = importlib.import_module(__package__ or "tetris_kernel.systems")


@dataclass(frozen=True)
class SystemModule:
    """一个系统包的「自述」：**包名 · 声明 · 工厂**。

    **工厂可以是 `None`**：M2 的三个内建可观测系统的工厂住在宿主层（它们的
    落盘出口由宿主注入），**声明仍然在包里**——**声明是承诺，工厂是实现**，
    两者可以分开住，但**声明与工厂同源时最好**。
    """

    package: str
    manifest: SystemManifest
    factory: SystemFactory | None
    module: ModuleType


def _module_of(root: ModuleType, package_name: str) -> ModuleType | None:
    """取一个系统包的自述模块；**没有就是没有**（返回 `None`，不顶替）。"""
    loaded = importlib.import_module(f"{root.__name__}.{package_name}")
    module_names = {info.name for info in pkgutil.iter_modules(loaded.__path__)}
    if MANIFEST_MODULE not in module_names:
        return None
    return importlib.import_module(f"{root.__name__}.{package_name}.{MANIFEST_MODULE}")


def discover_system_modules(package: ModuleType | None = None) -> tuple[SystemModule, ...]:
    """扫描系统目录，返回**所有自带声明的系统**（**按包名排序**，结果可复现）。

    `package` 省略时扫**系统层自己**；**测试可以递给它一个临时包**——
    「新增一个系统就多一个包」这件事得能**在临时目录里真验**（不去污染仓里的
    系统目录）。

    **重名即报错**：名字是清单的键，两个系统抢一个名字时后扫到的会悄悄顶掉
    先扫到的——那正是清单要防的分叉，当场炸掉。
    """
    root = _SYSTEMS_PACKAGE if package is None else package
    found: list[SystemModule] = []
    seen: dict[str, str] = {}
    for info in sorted(pkgutil.iter_modules(list(root.__path__)), key=lambda item: item.name):
        if not info.ispkg:
            continue
        module = _module_of(root, info.name)
        if module is None:
            continue
        manifest = getattr(module, MANIFEST_ATTRIBUTE, None)
        if manifest is None:
            continue
        if not isinstance(manifest, SystemManifest):
            raise TypeError(
                f"{info.name}.{MANIFEST_MODULE} 的 {MANIFEST_ATTRIBUTE} "
                f"不是 SystemManifest（是 {type(manifest).__name__}）"
            )
        if manifest.name in seen:
            raise ValueError(f"系统名重复：{manifest.name}（{seen[manifest.name]} 与 {info.name}）")
        seen[manifest.name] = info.name
        factory = getattr(module, FACTORY_ATTRIBUTE, None)
        if factory is not None and not callable(factory):
            raise TypeError(f"{info.name}.{MANIFEST_MODULE} 的 {FACTORY_ATTRIBUTE} 不可调用")
        found.append(
            SystemModule(
                package=info.name,
                manifest=manifest,
                factory=factory,
                module=module,
            )
        )
    return tuple(found)


def discover_manifests() -> Mapping[str, SystemManifest]:
    """扫描系统目录，返回**已声明的系统清单**（名字 → 声明）。

    **契约基线用的就是它**（`tools/contract_baseline.py` 的「系统声明」键类）——
    **基线要的是确定的声明集**，不能取决于运行时谁装了什么系统。
    """
    return MappingProxyType(
        {item.manifest.name: item.manifest for item in discover_system_modules()}
    )


__all__ = [
    "FACTORY_ATTRIBUTE",
    "MANIFEST_ATTRIBUTE",
    "MANIFEST_MODULE",
    "SystemModule",
    "discover_manifests",
    "discover_system_modules",
]
