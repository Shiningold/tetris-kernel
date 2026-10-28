"""系统注册表（宿主层 · M3-S3 · FR-9.1 / FR-9.2 · AC-8 · M3-D1）。

**宿主层的系统表**：按名字装上 / 摘下 / 重装 —— **摘得干净**。

## 三个动作，两态

M3 的系统生命周期**只有两个动作**：**装上** 与 **摘下**（spec §7.1）。**没有「暂停」**
——要暂停就别装。**「标记为不活跃」不是卸载**：还在订阅还在派发，那就是暂停。

## 装上（顺序有讲究）

1. **按名字找声明与工厂** —— **不在册就明确报错**（不静默跳过：静默跳过会让
   「我装上了」变成一句空话）。
2. **创建实例** → **接上命令通道**（系统派命令的通路）。
3. **按声明订阅事件**（订阅集来自 `SystemManifest.subscribes`）。
4. **发 `kernel:system:installed`**（负载：插件名 · 配置）。
5. **装上之后立刻开始收事件** —— 装卸动作本身**不产生内核状态变更**（M3-D3）。

## 摘下（顺序**不可乱**）

1. **① 先撤命令通道** —— **M3-D1 的头号坑**：只退订不撤通道 = 系统「人不在了，
   话还在说」→ 还能改变内核状态 → **状态变更没有事件伴随**（破 N5）→ **AC-8
   当场失效**。**这一条比退订更早**，因为退订只保证「不再听」，撤通道才保证
   「不再说」。
2. **② 退订**（总线的订阅原语 · ADR-057 —— **那只是订阅者级的原语，这里要的是
   系统级的完整语义**）。
3. **③ 停止派发** —— 通道已撤，不再有新的命令出去。**但队列里已入队的命令照常
   执行**：命令是「已发生的事实」，丢弃它会让内核行为**依赖卸载时机** → 破坏确定性。
4. **④ 发 `kernel:system:removed`**（负载：插件名 · 是否干净）。

**为什么 `removed` 事件排在撤通道与退订之后**：订阅者在这个事件里看到的是
「我已经不在了」的世界——**先发事件再撤通道，订阅者会看到自己还活着**（顺序
倒过来的写法必须被反向验证逮住）。

## 卸载不干净怎么办

系统释放资源时抛错 → **记错**，但**不占用 `kernel:error`**（架构 §6.4：
`kernel:error` 只留给「订阅者异常被隔离」这一种语义）。**占用它 = 语义被占**，
消费方分不清「某个订阅者炸了」与「某个系统卸载得不干净」。

## 谁住哪一层

- **注册表住在宿主层**（`host/`）——**内核永不持有系统表**（N6）：装配是宿主的
  事，内核只认「当前这一套」；
- **上下文与契约形状住在系统层**（`systems/context.py`）——**系统包里的工厂要用
  它**，而系统层不许 import 宿主层；
- **声明与工厂住在系统自己的包里**（`systems/<名字>/manifest.py`）——**新增能力 =
  新增文件**（M3-D2 / `G3-1`）：集中登记在一张名单里的话，每个新系统都要改一个
  既有文件，那条判据当场失去牙齿；
- **本模块不 import 任何系统实现**——它只认「声明 + 工厂」这个形状。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from ..bus.bus import EventBus
from ..bus.subscriber import Subscription
from ..contracts.systems import SystemManifest
from ..kernel.core import Kernel
from ..kernel.drafts import Payload, make_draft
from ..kernel.snapshot import Snapshot
from ..systems.context import (
    CommandChannel,
    ConfigValue,
    InstallContext,
    Sink,
    SystemConfig,
    SystemEntry,
    SystemFactory,
)

#: **按系统名给落盘出口**的工厂（宿主侧造）。
#:
#: **为什么是「按名字问」而不是「一张表」**：宿主**不认识具体系统**（M3-D2 的
#: 纪律反过来用在这里）——它只知道"有个系统叫这个名字，给它一个出口"。
#: **返回 `None` = 这个系统不落盘**。
type SystemSinkFactory = Callable[[str], Sink | None]

#: 装卸事件（契约已登记 · 产出者 `host`）。
SYSTEM_INSTALLED_EVENT = "kernel:system:installed"
SYSTEM_REMOVED_EVENT = "kernel:system:removed"


class SystemRegistryError(RuntimeError):
    """系统注册表的基础错误。"""


class UnknownSystemError(SystemRegistryError):
    """**名字不在册** —— 明确报错，不静默跳过。"""


class SystemAlreadyInstalledError(SystemRegistryError):
    """**同一个系统装了两遍** —— 名字是键，装两遍会让「摘下」摘不干净。"""


class SystemNotInstalledError(SystemRegistryError):
    """**要摘的系统没装** —— 明确报错，不静默跳过。"""


@dataclass(frozen=True)
class InstalledSystem:
    """**已装上**的系统的记录（注册表内部用）。"""

    instance: object
    channel: CommandChannel
    subscriptions: tuple[Subscription[Snapshot], ...]


class SystemRegistry:
    """宿主层的**系统表**：装 / 卸 / 重装 / 查 / 列。

    **它不 import 任何系统实现**——目录（`catalog`）是**递进来的**：
    内建的可观测系统由宿主装配层给（它们的落盘出口是宿主注入的），新系统由
    自动发现给（`host/discovery.py`）。**注册表只认「声明 + 工厂」这个形状。**
    """

    def __init__(
        self,
        *,
        bus: EventBus[Snapshot],
        kernel: Kernel,
        catalog: Mapping[str, SystemEntry],
        system_sink: SystemSinkFactory | None = None,
    ) -> None:
        self._bus = bus
        self._kernel = kernel
        self._catalog: Mapping[str, SystemEntry] = MappingProxyType(dict(catalog))
        self._installed: dict[str, InstalledSystem] = {}
        self._problems: list[str] = []
        #: **按系统名给落盘出口**的工厂（`None` = 装上的系统都不落盘）。
        self._system_sink = system_sink

    # ------------------------------------------------------------------
    # 只读视图
    # ------------------------------------------------------------------

    def installed(self) -> tuple[str, ...]:
        """已装上的系统名字（**按装上顺序**）。"""
        return tuple(self._installed)

    def manifest_of(self, name: str) -> SystemManifest:
        """取某个系统的声明（**不在册就报错**）。"""
        entry = self._catalog.get(name)
        if entry is None:
            raise UnknownSystemError(f"系统不在册：{name}")
        return entry.manifest

    def instance_of(self, name: str) -> object:
        """取已装系统的实例（**没装就报错**）。"""
        record = self._installed.get(name)
        if record is None:
            raise SystemNotInstalledError(f"系统没装：{name}")
        return record.instance

    def channel_of(self, name: str) -> CommandChannel:
        """取已装系统的命令通道（**没装就报错**）。"""
        record = self._installed.get(name)
        if record is None:
            raise SystemNotInstalledError(f"系统没装：{name}")
        return record.channel

    def problems(self) -> tuple[str, ...]:
        """**记错**的那些事（卸载不干净等）——**不占用 `kernel:error`**。"""
        return tuple(self._problems)

    # ------------------------------------------------------------------
    # 装
    # ------------------------------------------------------------------

    def install(self, name: str, config: Mapping[str, ConfigValue] | None = None) -> object:
        """装上一个系统，返回它的实例。

        **顺序不可乱**（见模块 docstring）：找声明与工厂 → 建实例 → 接命令通道
        → 按声明订阅 → 发 `kernel:system:installed`。
        """
        entry = self._catalog.get(name)
        if entry is None:
            # **不在册 → 明确报错**：静默跳过会让调用方以为装上了。
            raise UnknownSystemError(f"系统不在册：{name}")
        if name in self._installed:
            raise SystemAlreadyInstalledError(f"系统已经装上了：{name}")

        channel = CommandChannel(self._kernel.submit)
        context = InstallContext(
            name=name,
            bus=self._bus,
            commands=channel,
            config=_config_of(config),
            # **落盘出口按名字问宿主**（`None` = 这个系统不落盘）——
            # 系统**知道有 sink，不知道路径**（ADR-064）。
            # **只有声明 `needsSink=True` 的系统才拿到出口**：不需要落盘的
            # 系统（成就是事件）**不接出口**——出口给多了就是白给。
            sink=(
                None
                if self._system_sink is None or not entry.manifest.needsSink
                else self._system_sink(name)
            ),
        )
        instance = entry.factory(context)
        subscriptions = self._subscribe(entry.manifest, instance)
        self._installed[name] = InstalledSystem(
            instance=instance,
            channel=channel,
            subscriptions=subscriptions,
        )
        self._publish(
            SYSTEM_INSTALLED_EVENT,
            (("name", name), ("config", _as_payload(context.config))),
        )
        return instance

    def _subscribe(
        self, manifest: SystemManifest, instance: object
    ) -> tuple[Subscription[Snapshot], ...]:
        """按**声明**接上订阅。

        **两种系统，两条路**：

        - **带 `on_event` 的系统**（新系统 · S5 / S6）：注册表**按声明**订阅——
          **订阅集只有一个真相**（`SystemManifest.subscribes`），系统里不另写一份
          名单（两处各写一份就会分叉）；
        - **自己订阅的系统**（M2 的三个内建系统）：它们在构造时自己订阅，句柄由
          系统自己暴露（`subscriptions` 属性）——**注册表只负责摘干净**。

        **注册表不替系统发明订阅**：声明之外的东西，系统自己去订阅并把句柄交出来
        ——**订阅集仍然只有一个真相**（声明 + 系统自己交出来的那些）。
        """
        handler = getattr(instance, "on_event", None)
        if handler is None:
            exposed = getattr(instance, "subscriptions", None)
            if exposed is None:
                raise SystemRegistryError(
                    f"系统 {manifest.name}：既没有 on_event，也没有暴露 subscriptions —— "
                    "注册表没法接上它的订阅，也没法摘干净"
                )
            return tuple(exposed)
        return tuple(
            self._bus.subscribe(pattern, handler, name=manifest.name)
            for pattern in manifest.subscribes
        )

    # ------------------------------------------------------------------
    # 卸
    # ------------------------------------------------------------------

    def uninstall(self, name: str) -> None:
        """摘下一个系统（**顺序不可乱**：先撤通道 → 退订 → 释放 → 发事件）。"""
        record = self._installed.get(name)
        if record is None:
            raise SystemNotInstalledError(f"系统没装：{name}")

        # ① **先撤命令通道**（M3-D1）：还在派命令 = 没摘干净。
        record.channel.revoke()
        # ② **退订**。
        for subscription in record.subscriptions:
            subscription.unsubscribe()
        # ③ **释放资源**（不干净 → 记错，**不占用 `kernel:error`**）。
        clean = self._release(name, record.instance)
        # ④ **发事件**（在撤通道与退订之后：订阅者看到的世界已经是「我不在了」）。
        self._publish(SYSTEM_REMOVED_EVENT, (("name", name), ("clean", clean)))
        del self._installed[name]

    def _release(self, name: str, instance: object) -> bool:
        """释放系统的资源；**抛错就是卸载不干净**（记错，返回 `False`）。"""
        closer = getattr(instance, "close", None)
        if closer is None:
            return True
        try:
            closer()
        except Exception as error:
            # **不占用 `kernel:error`**（架构 §6.4）：那一条只留给订阅者异常隔离。
            self._problems.append(f"系统 {name} 卸载时不干净：{error}")
            return False
        return True

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    def _publish(self, event_type: str, payload: Payload) -> None:
        """发一条装卸事件（**产出者由总线填** —— 契约里这两个事件的产出者是 `host`）。"""
        self._bus.publish(make_draft(event_type, payload, self._kernel.frame))


def _config_of(config: Mapping[str, ConfigValue] | None) -> SystemConfig:
    """配置 → **不可变的扁平元组**（顺序按键名排好，保证可复现）。"""
    if not config:
        return ()
    return tuple((key, config[key]) for key in sorted(config))


def _as_payload(config: SystemConfig) -> Payload:
    """配置 → 事件负载（**值的类型不变**，只换容器类型）。

    **配置进事件前不许塞不可序列化的东西**——`SCALAR_MAP` 只收扁平标量，
    塞进去别的，`make_draft` 当场报错（契约逐条相等那一条）。
    """
    return cast("Payload", tuple((key, value) for key, value in config))


__all__ = [
    "SYSTEM_INSTALLED_EVENT",
    "SYSTEM_REMOVED_EVENT",
    "CommandChannel",
    "ConfigValue",
    "InstallContext",
    "InstalledSystem",
    "Sink",
    "SystemAlreadyInstalledError",
    "SystemConfig",
    "SystemEntry",
    "SystemFactory",
    "SystemNotInstalledError",
    "SystemRegistry",
    "SystemRegistryError",
    "SystemSinkFactory",
    "UnknownSystemError",
]
