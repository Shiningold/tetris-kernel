"""系统声明面（契约 v1 · M3 · FR-9.1 / FR-9.2）。

公共叶子——**不 import 任何 `tetris_kernel.*` 模块，也不依赖第三方**；只用标准库
（架构 §2.3 / §4.1）。本模块只做「声明」，不做「行为」。

## 声明了什么

- `SystemManifest`：一个系统对外的**承诺**——名字 · 版本 · 订阅的事件 ·
  派发的命令 · 产出者 · 是否需要宿主注入落盘出口。
- `SYSTEM_MANIFESTS`：**已登记系统清单容器**（名字 → 声明）+ 登记 / 注销原语。
- `manifest_canonical_text()`：声明的**规范化串**（契约基线哈希的输入口径）。
- `check_manifest_types()`：声明的**自证**——订阅的事件与派发的命令必须已登记，
  **产出者必须在代号表里（或就是它自己的名字）**。

## 声明为什么进契约面

FR-9.2 说「插件可声明其订阅的事件与发出的命令，**用于自动生成依赖图与文档**」
——**这份声明是跨方的东西**（系统写、宿主读、工具消费、文档引用），**必须有一个
所有方都认的地方**。放 `contracts/`（公共叶子）→ **不进冻结区**、**不 import
任何东西**、**受只增不改基线保护**。

**声明面里不放工厂**：工厂要 import 具体实现（`systems/achievements/` 之类）
→ **不能进 `contracts/`**。**声明与工厂分两层**：声明在 `contracts/`，工厂在系统
自己的包里。

## 声明住在谁那里

**每个系统在自己的包里自带一份声明**（`SYSTEM_MANIFEST` 常量，住在
`systems/<名字>/manifest.py`）——**不是集中登记在一张名单里**。

理由是 M3-D2 / `G3-1`：**「新增能力 = 新增文件」**。集中登记意味着新增一个系统
要改一个既有文件（这里就是 `contracts/systems.py`）——那条判据当场不成立，
而它正是 M3 的正题。

于是两份清单的分工是：

- **`SYSTEM_MANIFESTS`（本模块）**：**运行期**的「已登记系统」清单——宿主注册表
  装上 / 摘下系统时往里登记 / 注销；
- **契约基线里的「系统声明」键类**：**静态**扫描各系统包里的 `SYSTEM_MANIFEST`
  得到（`systems/manifests.py` 的 `discover_manifests()`）——基线要的是**确定的**
  声明集，不能取决于运行时谁装了什么。

两者**同源**：都来自那些 `SYSTEM_MANIFEST` 常量。

## 自证：订阅与命令必须指向已登记的类型

声明里写的事件类型 / 命令类型**必须在契约注册表里有**——写错名字（`"piece:rotatd"`）
当场报错，不许安静地挂一个永远收不到事件的订阅。

**产出者也在自证范围内**（M3 审查报告 L1）：**产出者代号表** ∪ **本系统自己的
名字**（需求 §8.2 允许"系统名"——**不发事件**的系统，产出是文件而不是事件）。
**拼错了没有任何一处会红**——基线照收、安装照过，而消费方按 producer 过滤时
**静默漏掉整个系统的事件**。

**自证放在本模块且不 import 注册表**：注册表（`events.py` / `commands.py`）与本模块
是**同层**，互相 import 会形成环。取而代之，`check_manifest_types()` **收下已
登记的名字集合**再判断——**口径在契约面，数据由调用方递进来**。

## 全局订阅（`*`）

`GLOBAL_SUBSCRIPTION` = `"*"`：**订阅全局**——需求 §8.1 的约定明写「日志系统订阅
全局，因此新事件自动进入日志，无需额外接线」。它**不是**一个事件类型名，
所以自证对它网开一面（**其余名字一律必须是已登记的事件类型**）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

#: 全局订阅的写法（**不是**事件类型名——需求 §8.1 的约定）。
GLOBAL_SUBSCRIPTION = "*"


# ---------------------------------------------------------------------------
# 声明结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemManifest:
    """一个系统对外的**承诺**。

    字段：**名字** · **版本** · **订阅声明** · **命令声明** · **产出者** ·
    **落盘需求**（spec-M3 §6.4）。

    - `name`：**全局唯一**（清单容器的键）；
    - `subscribes`：订阅的事件类型；`GLOBAL_SUBSCRIPTION`（`"*"`）表示订阅全局；
    - `commands`：派发的命令类型——**必须是已登记的命令**；
    - `producer`：与需求 §8.1 的「产出者」列同义（**不是命名空间**）；
    - `needsSink`：该系统是否需要宿主注入落盘出口（M3 的旋转统计系统要 ·
      成就系统不要）。

    **字段名即契约**：改名 = major 变更。构造时就地校验非法值——**声明写错当场
    炸**，不留到装系统时才发现。
    """

    name: str
    version: str
    subscribes: tuple[str, ...]
    commands: tuple[str, ...]
    producer: str
    needsSink: bool

    def __post_init__(self) -> None:
        """就地校验声明（**当场报错，不留到装系统时**）。"""
        if not self.name:
            raise ValueError("系统声明的名字不能为空")
        if not self.version:
            raise ValueError(f"系统 {self.name}：版本不能为空")
        if not self.producer:
            raise ValueError(f"系统 {self.name}：产出者不能为空")
        for field_name, values in (
            ("subscribes", self.subscribes),
            ("commands", self.commands),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"系统 {self.name}：{field_name} 里有重复项")
            for value in values:
                if not value:
                    raise ValueError(f"系统 {self.name}：{field_name} 里有空名字")
        # **两个都空 = 什么都不干**——那不是系统，是个摆设。
        # 单个空是合法的：只订阅不派命令（日志 / 指标 / 回放），或只派命令不订阅。
        if not self.subscribes and not self.commands:
            raise ValueError(f"系统 {self.name}：订阅与命令不能同时为空")


# ---------------------------------------------------------------------------
# 已登记系统清单容器
# ---------------------------------------------------------------------------

#: 已登记系统清单的**内部存储**（唯一真相）。
#
#: **不直接暴露可变字典**：对外只有 `SYSTEM_MANIFESTS` 这个只读视图，登记与注销
#: 走两个原语——**改动路径只有一处**，就不会有"谁直接改了字典"的第二个真相。
_REGISTERED: dict[str, SystemManifest] = {}

#: 已登记系统清单（名字 → 声明）——**只读视图**。
SYSTEM_MANIFESTS: Mapping[str, SystemManifest] = MappingProxyType(_REGISTERED)


def register_system_manifest(manifest: SystemManifest) -> None:
    """把一个系统声明登记进清单（宿主注册表装系统时调用）。

    **重名即报错**：名字是清单的键，**全局唯一**——两个系统抢一个名字时，
    后装的悄悄顶掉先装的（就像基线条目撞键那样），必须当场炸。
    """
    if manifest.name in _REGISTERED:
        raise ValueError(f"系统名已登记：{manifest.name}")
    _REGISTERED[manifest.name] = manifest


def unregister_system_manifest(name: str) -> SystemManifest | None:
    """从清单里注销一个系统；**没登记过就是没有**（返回 `None`，不顶替）。"""
    return _REGISTERED.pop(name, None)


# ---------------------------------------------------------------------------
# 规范化串与自证
# ---------------------------------------------------------------------------


def manifest_canonical_text(manifest: SystemManifest) -> str:
    """声明的**规范化串**——契约基线哈希的输入。

    **口径放在契约面**：工具与测试都调它，谁都不许另抄一份拼法——两处各拼一次，
    哈希就对不上，而对不上的原因没人查得出来。

    字段顺序**写死**（不按字典序）：顺序变了就是一次基线哈希变化，而顺序本不该
    携带语义——所以它必须由这一处说了算。
    """
    return "\n".join(
        (
            f"name={manifest.name}",
            f"version={manifest.version}",
            f"subscribes={','.join(manifest.subscribes)}",
            f"commands={','.join(manifest.commands)}",
            f"producer={manifest.producer}",
            f"needsSink={'true' if manifest.needsSink else 'false'}",
        )
    )


def check_manifest_types(
    manifest: SystemManifest,
    *,
    known_events: Iterable[str],
    known_commands: Iterable[str],
    known_producers: Iterable[str],
) -> tuple[str, ...]:
    """自证：声明里的类型**必须已登记**——返回问题清单（空 = 通过）。

    **这是跨文件自证**：订阅的事件类型必须在事件注册表里，派发的命令类型必须在
    命令注册表里。写错一个名字（`"piece:rotatd"`）当场被抓住——不然它会挂一个
    永远收不到事件的订阅，而系统看上去"装上了"。

    **注册表由调用方递进来**：本模块是公共叶子，不 import 同层的
    `events.py` / `commands.py`（互相 import 会成环）。

    **全局订阅（`"*"`）不算类型名**，放行。

    **产出者也在自证范围内**（M3 审查报告 L1）：合法值 = **产出者代号表** ∪
    **本系统自己的名字**（后者是需求 §8.2 允许的"系统名"——**不发事件**的
    系统，产出是文件而不是事件，它的产出者代号就是系统名）。

    **为什么这条要有牙**：产出者拼错（`"achivements"`）时**基线照收、安装照过**
    ——而消费方按 producer 过滤事件时会**静默漏掉整个系统的事件**。**声明与
    实际发事件的人对不上**，没有任何一处会红。
    """
    problems: list[str] = []
    event_names = set(known_events)
    command_names = set(known_commands)
    for event_type in manifest.subscribes:
        if event_type == GLOBAL_SUBSCRIPTION:
            continue
        if event_type not in event_names:
            problems.append(f"订阅了未登记的事件类型：{event_type}")
    for command_type in manifest.commands:
        if command_type not in command_names:
            problems.append(f"派发了未登记的命令类型：{command_type}")
    # **产出者**：代号表里的名字，**或者它自己**（需求 §8.2 允许"系统名"）。
    if manifest.producer not in set(known_producers) | {manifest.name}:
        problems.append(f"产出者不在代号表里：{manifest.producer}")
    return tuple(problems)


__all__ = [
    "GLOBAL_SUBSCRIPTION",
    "SYSTEM_MANIFESTS",
    "SystemManifest",
    "check_manifest_types",
    "manifest_canonical_text",
    "register_system_manifest",
    "unregister_system_manifest",
]
