"""系统安装的**上下文与契约形状**（系统层 · M3 · FR-9.1 / FR-9.2）。

这一层是**系统与宿主之间那一面**的东西：宿主递给系统的上下文、系统派命令的
通道、系统的目录条目。**它住在系统层**（不是宿主层）——因为**系统包里的工厂要
用它**（`systems/<名字>/manifest.py` 的 `create_system(context)`），而**系统层不许
import 宿主层**（依赖方向：`host > adapters > systems > bus > kernel > rules >
contracts`）。**住错层 = 反向依赖 = `lint-imports` 立刻红。**

## 名字说明

**不叫 `SystemContext`**：总线层已经有一个 `SystemContext`（订阅者取只读视图的
那个）——**同名两义**会让读代码的人以为自己看错了。**这一层叫 `InstallContext`**
（安装上下文），一眼知道是「装系统时递给它的那份」。

## 上下文里为什么没有内核

系统层**只订阅事件 + 派发命令**（M3-D3）：它连内核对象都不该看见——
**最窄能力原则**。要派命令就派命令（走通道），要读状态就读事件负载。
**上下文里给了内核 = 这个纪律当场破掉**。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..bus.bus import EventBus
from ..contracts.commands import COMMANDS
from ..contracts.systems import SystemManifest
from ..kernel.commands import Command
from ..kernel.snapshot import Snapshot

#: 受控写入的命令名（**从契约取**，不复制字面量 —— 架构 §4.4 末条）。
WRITE_CELLS_COMMAND: str = "writeCells"

#: `writeCells` 的参数名（**同样从契约取**）。
WRITE_CELLS_ARG: str = COMMANDS[WRITE_CELLS_COMMAND].args[0].name

#: 配置值（**扁平标量** · 契约的 `SCALAR_MAP` 口径：不可序列化对象一律不进事件）。
type ConfigValue = int | float | str | bool | None

#: 系统的配置（键 → 值 · **顺序固定**的不可变元组）。
type SystemConfig = tuple[tuple[str, ConfigValue], ...]

#: 落盘出口（**宿主注入的可调用对象** · ADR-064）。
#:
#: **为什么系统侧自己写一份别名**：`systems/` **不能** import `host/`
#: （依赖方向），拿不到 `host.assembly.Sink`——它是**同一个可调用类型**
#: （结构化类型），这里自己写一份即可（**别名不是契约，不进基线**）。
type Sink = Callable[[bytes], None]


class CommandChannel:
    """系统派命令的通道 —— **可撤销**（M3-D1 的核心）。

    **为什么是对象而不是一个函数**：撤销必须是**状态**。「把提交函数换成空函数」
    那种做法撤不干净——系统还攥着旧引用，还能派命令。

    **撤销之后提交的命令被丢弃**（并计数）：**丢弃不报错**——系统不知道宿主
    什么时候摘了它，报错会让订阅者的异常处理把「已卸载」误当成「出错了」。
    """

    def __init__(self, submit: Callable[[Command], None]) -> None:
        self._submit = submit
        self._active = True
        self._revoked_count = 0

    def submit(self, command: Command) -> None:
        """派一条命令（**撤销后是空操作**）。"""
        if not self._active:
            self._revoked_count += 1
            return
        self._submit(command)

    def submit_cells(
        self,
        cells: tuple[tuple[int, int, int], ...],
        source: str = "system",
    ) -> None:
        """派一条 **`writeCells`** 命令（**受控写入** · FR-11.1）。

        **为什么命令的组装在这里做**：**变体层不能 import 内核**（架构 §2.3 /
        §4.1 / §7.7：`variants` 禁 import `kernel` / `bus`）——而 `Command` 是内核的
        类型。**变体只说"写这些格子"，命令的形状由系统层拼**。

        **参数名从契约取**（`contracts.commands.COMMANDS`）——**不复制字面量**
        （架构 §4.4 末条）：参数名写错，内核会**当场拒**（`invalid_argument`），
        而错误信息只会说"参数不合法"，不说是哪个名字错了。
        """
        if not cells:
            return
        self.submit(
            Command(
                type=WRITE_CELLS_COMMAND,
                args=((WRITE_CELLS_ARG, cells),),
                source=source,
            )
        )

    @property
    def active(self) -> bool:
        """通道是否还开着（**撤销后为 `False`**）。"""
        return self._active

    @property
    def revoked_count(self) -> int:
        """撤销之后被丢弃的命令条数（**可观测**：被摘下的系统还在派命令就看这里）。"""
        return self._revoked_count

    def revoke(self) -> None:
        """**撤掉通道**（幂等）。撤了之后这个系统就再也改不动内核状态。"""
        self._active = False


@dataclass(frozen=True)
class InstallContext:
    """装系统时递给工厂的**上下文**。

    字段：**名字** · **总线** · **命令通道** · **配置** · **落盘出口**。

    **上下文里没有内核**：系统层**只订阅事件 + 派发命令**（M3-D3），它连内核
    对象都不该看见——**最窄能力原则**。要派命令就派命令（走通道），要读状态就
    读事件负载。

    **落盘出口（`sink`）是 `None` = 不落盘**：系统**知道有 sink，不知道路径**
    （ADR-064）——**它自己不许 `open()`**（M3-D5）。
    """

    name: str
    bus: EventBus[Snapshot]
    commands: CommandChannel
    config: SystemConfig
    sink: Sink | None = None


#: 系统的工厂：拿一份安装上下文，造出系统实例。
type SystemFactory = Callable[[InstallContext], object]


@dataclass(frozen=True)
class SystemEntry:
    """目录里的一个系统：**声明 + 工厂**。

    **声明与工厂必须同源**（同一个包里的同一份承诺）——声明说订阅什么、派发什么，
    工厂就照着做；**两处各写一份**时，承诺与行为会悄悄分叉，而基线只钉住声明。
    """

    manifest: SystemManifest
    factory: SystemFactory


__all__ = [
    "CommandChannel",
    "ConfigValue",
    "InstallContext",
    "Sink",
    "SystemConfig",
    "SystemEntry",
    "SystemFactory",
]
