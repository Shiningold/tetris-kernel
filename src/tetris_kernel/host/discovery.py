"""系统的**自动发现** + **系统清单与依赖图**（宿主层 · M3-S4 · FR-9.2 · M3-D2）。

## 宿主不维护手写名单

**这是本模块的红线**：名单一旦存在，每个新系统都要改一个既有文件登记 →
**`G3-1`（新增能力 = 新增文件）永久失去牙齿**。所以宿主**扫描**系统目录，
让每个系统**自带声明与工厂**（约定见 `systems/manifests.py`）。

## 扫描根覆盖 `systems/` 与 `variants/`（M4-S2）

变体 B（落地后生长）**也是一个系统**——它订阅事件、派发命令——但它**住在
`variants/`**（G4-6）。**声明面与依赖图要能看见它**（FR-9.2：声明的用处就是
生成依赖图），所以本模块把两边的声明**汇总**成一张清单。

**汇总放在宿主层的原因**：`systems/` 与 `variants/` **同级**（架构 §2.3）——
**系统层 import 变体层是横向依赖**（`lint-imports` 当场红）。**变体层扫自己
那一层**（`variants/manifests.py`），**宿主层做汇总**——**两边都不越界**。

**重名即报错**：系统名与变体系统名撞了 → **当场炸**（后扫到的悄悄顶掉先扫到
的，依赖图与基线都会指向错的那个）。

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
from types import MappingProxyType, ModuleType

from ..bus.bus import EventBus
from ..bus.subscriber import Subscription
from ..contracts.systems import SystemManifest
from ..kernel.snapshot import Snapshot
from ..systems.context import SystemEntry
from ..systems.manifests import SystemModule, discover_system_modules
from ..variants.manifests import VariantModule, discover_variant_modules

# ---------------------------------------------------------------------------
# 目录与清单
# ---------------------------------------------------------------------------


def system_modules() -> tuple[SystemModule, ...]:
    """**扫描**系统目录，返回所有自带声明的系统的自述记录。"""
    return discover_system_modules()


def variant_modules() -> tuple[VariantModule, ...]:
    """**扫描**变体目录，返回所有变体包的自述记录（M4-S2）。

    **规则集型变体的声明是 `None`**（它没有系统）——**那不是错误**。
    """
    return discover_variant_modules()


def _merged_manifests(
    systems_package: ModuleType | None = None,
    variants_package: ModuleType | None = None,
) -> Mapping[str, SystemManifest]:
    """把两边的声明**汇总成一张清单**（**重名即报错**）。

    **为什么在这里汇总**：宿主层两边都能 import（`systems/` 与 `variants/` 与
    它同级）——**系统层 import 变体层是横向依赖**（架构 §2.3）。

    **包参数可注入**：测试在临时目录里造变体包再验它出现在清单里——**不去
    污染仓里的变体目录**。
    """
    merged: dict[str, SystemManifest] = {}
    for manifest in discover_system_modules(systems_package):
        merged[manifest.manifest.name] = manifest.manifest
    for item in discover_variant_modules(variants_package):
        if item.manifest is None:
            continue
        if item.manifest.name in merged:
            raise ValueError(
                f"系统名重复：{item.manifest.name}"
                f"（系统目录与变体目录各有一个）——两个声明抢一个名字，依赖图会指向错的那个"
            )
        merged[item.manifest.name] = item.manifest
    return MappingProxyType(merged)


def declared_manifests(
    *,
    systems_package: ModuleType | None = None,
    variants_package: ModuleType | None = None,
) -> Mapping[str, SystemManifest]:
    """**所有**已声明的系统（名字 → 声明）——**含工厂不在包里的**。

    **扫描根覆盖 `systems/` 与 `variants/`**（M4-S2）——变体带系统时，
    **它也是系统**（FR-9.1），**依赖图要能看见它**（FR-9.2）。
    """
    return _merged_manifests(systems_package, variants_package)


def system_catalog(
    *,
    systems_package: ModuleType | None = None,
    variants_package: ModuleType | None = None,
) -> Mapping[str, SystemEntry]:
    """**能装的**系统的目录条目（名字 → 声明 + 工厂）。

    **没有工厂的系统不在这里**（M2 的三个内建可观测系统：工厂在宿主侧）——
    **清单与目录不是一回事**：清单回答「有哪些系统」，目录回答「哪些能装」。
    """
    entries: dict[str, SystemEntry] = {}
    for item in discover_system_modules(systems_package):
        if item.factory is None:
            continue
        entries[item.manifest.name] = SystemEntry(
            manifest=item.manifest,
            factory=item.factory,
        )
    for item in discover_variant_modules(variants_package):
        if item.manifest is None or item.factory is None:
            continue
        if item.manifest.name in entries:
            raise ValueError(f"系统名重复：{item.manifest.name}（系统目录与变体目录各有一个）")
        variant_factory = item.factory
        entries[item.manifest.name] = SystemEntry(
            manifest=item.manifest,
            # **变体层的工厂签名是"泛的"**（变体层不许 import 系统层 —— 横向
            # 依赖 · 架构 §2.3）→ **宿主层在这里包一层适配**，把真实的安装上下文
            # 递进去。**适配只做一次**，变体侧不必知道上下文的类型。
            factory=lambda context, factory=variant_factory: factory(context),
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
    _variant_modules: tuple[VariantModule, ...] = ()

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
        # **变体目录同样要查**（M4-S2）：变体包也是被扫描的包——**导入即订阅
        # 一样会破掉「装上才生效」的语义**。
        for item in self._variant_modules:
            leaked = sorted(
                key for key, value in vars(item.module).items() if isinstance(value, Subscription)
            )
            if leaked:
                label = item.manifest.name if item.manifest is not None else item.package
                problems.append(f"变体 {label} 的模块里有订阅句柄：{leaked}")
        return tuple(problems)

    def scan(self) -> tuple[str, ...]:
        """**扫描 + 比对**（一次做完），返回问题清单（空 = 干净）。"""
        self.snapshot()
        self._modules = discover_system_modules()
        self._variant_modules = discover_variant_modules()
        return self.check()


__all__ = [
    "DependencyGraph",
    "ImportPurityGuard",
    "declared_manifests",
    "dependency_graph",
    "system_catalog",
    "system_modules",
    "variant_modules",
]
