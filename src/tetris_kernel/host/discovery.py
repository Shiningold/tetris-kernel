"""系统的**自动发现** + **系统清单与依赖图**（宿主层 · M3-S4 · FR-9.2 · M3-D2）。

## 宿主不维护手写名单

**这是本模块的红线**：名单一旦存在，每个新系统都要改一个既有文件登记 →
**`G3-1`（新增能力 = 新增文件）永久失去牙齿**。所以宿主**扫描**系统目录，
让每个系统**自带声明与工厂**（约定见 `systems/manifests.py`）。

## 三样东西

1. **系统目录**（`system_catalog`）：**能装的**系统（声明 + 工厂俱全）；
2. **已声明的系统清单**（`declared_manifests`）：**所有**声明过的系统——**含
   工厂不在包里的**（M2 的三个内建可观测系统：工厂在宿主侧，落盘出口由宿主
   注入）。**清单回答「有哪些系统」，目录回答「哪些能装」**；
3. **依赖图**（`dependency_graph`）：**谁订阅了什么 + 谁发出什么命令**
   （FR-9.2）——**结构可被测试直接断言**（"给人看的图"不算）。

## 导入纯净度（守卫）

被扫描的包**导入时只允许定义**——**导入即订阅 / 即开文件**会破掉「装上才生效」
的语义。`ImportPurityGuard` 查三件事：

- **落盘目录的文件集合不变**（导入即写文件）；
- **总线订阅数不变**（导入即订阅到别人的总线上）；
- **被导入模块里没有 `Subscription` 实例**（导入即订阅到**它自己那条**总线上
  ——上面那条量不到）。

**守卫要能触发**：恒真的守卫等于没守（契约基线那条门就是这么写的）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ..bus.bus import EventBus
from ..bus.subscriber import Subscription
from ..contracts.systems import SystemManifest
from ..kernel.snapshot import Snapshot
from ..systems.context import SystemEntry
from ..systems.manifests import SystemModule, discover_system_modules

# ---------------------------------------------------------------------------
# 目录与清单
# ---------------------------------------------------------------------------


def system_modules() -> tuple[SystemModule, ...]:
    """**扫描**系统目录，返回所有自带声明的系统的自述记录。"""
    return discover_system_modules()


def declared_manifests() -> Mapping[str, SystemManifest]:
    """**所有**已声明的系统（名字 → 声明）——**含工厂不在包里的**。"""
    return MappingProxyType(
        {item.manifest.name: item.manifest for item in discover_system_modules()}
    )


def system_catalog() -> Mapping[str, SystemEntry]:
    """**能装的**系统的目录条目（名字 → 声明 + 工厂）。

    **没有工厂的系统不在这里**（M2 的三个内建可观测系统：工厂在宿主侧）——
    **清单与目录不是一回事**：清单回答「有哪些系统」，目录回答「哪些能装」。
    """
    entries: dict[str, SystemEntry] = {}
    for item in discover_system_modules():
        if item.factory is None:
            continue
        entries[item.manifest.name] = SystemEntry(
            manifest=item.manifest,
            factory=item.factory,
        )
    return MappingProxyType(entries)


# ---------------------------------------------------------------------------
# 依赖图（FR-9.2）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DependencyGraph:
    """**依赖图**：谁订阅了什么 + 谁发出什么命令（**可机读**）。

    三个维度：

    - `subscriptions`：系统 → 订阅的事件类型；
    - `commands`：系统 → 派发的命令类型；
    - `producers`：系统 → 产出者（**产出者 ≠ 命名空间**）。

    **反向索引现算**（`subscribers_of` / `dispatchers_of`）——**不另存一份**，
    两份索引会分叉，而分叉了没人查得出来。
    """

    subscriptions: Mapping[str, tuple[str, ...]]
    commands: Mapping[str, tuple[str, ...]]
    producers: Mapping[str, str]

    def subscribers_of(self, event_type: str) -> tuple[str, ...]:
        """**谁订阅了这个事件**（按名字排序）。"""
        return tuple(
            sorted(
                name
                for name, events in self.subscriptions.items()
                if event_type in events or "*" in events
            )
        )

    def dispatchers_of(self, command_type: str) -> tuple[str, ...]:
        """**谁派发这个命令**（按名字排序）。"""
        return tuple(
            sorted(name for name, commands in self.commands.items() if command_type in commands)
        )

    def as_dict(self) -> dict[str, object]:
        """机器可读形态（**给 `--json` 用**）。"""
        return {
            "subscriptions": {name: list(values) for name, values in self.subscriptions.items()},
            "commands": {name: list(values) for name, values in self.commands.items()},
            "producers": dict(self.producers),
        }


def dependency_graph(manifests: Mapping[str, SystemManifest] | None = None) -> DependencyGraph:
    """由**系统清单**汇总出依赖图（FR-9.2）。

    **清单是唯一输入**——依赖图不重新扫描、不另读一份订阅集（两处各写一份
    就会分叉，而基线只钉住声明）。
    """
    source = declared_manifests() if manifests is None else manifests
    return DependencyGraph(
        subscriptions=MappingProxyType(
            {name: manifest.subscribes for name, manifest in source.items()}
        ),
        commands=MappingProxyType({name: manifest.commands for name, manifest in source.items()}),
        producers=MappingProxyType({name: manifest.producer for name, manifest in source.items()}),
    )


# ---------------------------------------------------------------------------
# 导入纯净度守卫
# ---------------------------------------------------------------------------


def _files_under(directory: Path) -> frozenset[str]:
    """一个目录下的文件清单（**目录不存在就是空**——不创建它）。

    **字节码缓存不算**（`__pycache__` / `.pyc`）：导入 Python 模块本来就会写它，
    那是解释器的账，不是系统的副作用——**把它算进去，守卫就永远红**。
    """
    if not directory.is_dir():
        return frozenset()
    return frozenset(
        str(path)
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )


@dataclass
class ImportPurityGuard:
    """**导入纯净度守卫**：扫描前后**不许有副作用**。

    **它守的到底是什么**（照「实现可能怎么写坏」写）：

    - **落盘目录的文件集合变了** → 有包在导入时写了文件；
    - **总线订阅数变了** → 有包在导入时订阅了总线；
    - **被导入的模块里出现了 `Subscription` 实例** → 有包在导入时订阅了
      **它自己那条**总线（上面那条量不到）。

    **用法**：`problems = guard.scan()`（扫描并当场比对）或
    `guard.check()`（比对自己抓的快照）。
    """

    bus: EventBus[Snapshot] | None = None
    data_dir: Path | None = None
    _files_before: frozenset[str] = frozenset()
    _subscriptions_before: int = 0
    _modules: tuple[SystemModule, ...] = ()

    def snapshot(self) -> None:
        """**抓快照**（扫描之前调一次）。"""
        self._files_before = _files_under(self.data_dir) if self.data_dir else frozenset()
        self._subscriptions_before = self.bus.subscriber_count if self.bus is not None else 0

    def check(self) -> tuple[str, ...]:
        """比对自己抓的快照与**现在**——返回问题清单（空 = 干净）。"""
        problems: list[str] = []
        if self.data_dir is not None:
            after = _files_under(self.data_dir)
            created = sorted(after - self._files_before)
            if created:
                problems.append(f"导入期间落盘目录多了文件：{created}")
        if self.bus is not None:
            after_count = self.bus.subscriber_count
            if after_count != self._subscriptions_before:
                problems.append(
                    f"导入期间总线订阅数变了：{self._subscriptions_before} → {after_count}"
                )
        for item in self._modules:
            leaked = sorted(
                key for key, value in vars(item.module).items() if isinstance(value, Subscription)
            )
            if leaked:
                problems.append(f"系统 {item.manifest.name} 的模块里有订阅句柄：{leaked}")
        return tuple(problems)

    def scan(self) -> tuple[str, ...]:
        """**扫描 + 比对**（一次做完），返回问题清单（空 = 干净）。"""
        self.snapshot()
        self._modules = discover_system_modules()
        return self.check()


__all__ = [
    "DependencyGraph",
    "ImportPurityGuard",
    "declared_manifests",
    "dependency_graph",
    "system_catalog",
    "system_modules",
]
