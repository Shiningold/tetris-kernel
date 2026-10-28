"""变体 B「落地后生长」的**对外声明与工厂**（变体层 · M4-S4 · FR-9.1 / FR-9.2）。

## 声明与工厂都住在变体自己的包里

与系统包同款约定（`systems/manifests.py`）：**声明**（`SYSTEM_MANIFEST`）与**工厂**
（`create_system`）都在本模块，实现住在 `system.py`。

**变体层不许 import 系统层**（`systems.context` 通往总线与内核 → **传递依赖也
被禁**，架构 §2.3 / §4.1 / §7.7）→ **上下文与命令通道的形状由变体自己用
`Protocol` 声明**（`GrowthInstallContext` / `CellSubmitter`）——**结构化类型**：
真实的安装上下文满足它们。

## 声明的形状是契约面

`SystemManifest` 住在 `contracts/systems.py`——**变体系统与普通系统受同一把
自证的尺子**（订阅的事件与派发的命令必须已登记 · 产出者必须在代号表里或就是
它自己的名字）。

**它出现在依赖图里**（`host/discovery.py` 汇总 `systems/` 与 `variants/` 两边）
→ **变体 B 的系统声明进契约基线**（S4 显式刷新 · ADR-077）。
"""

from __future__ import annotations

from ...contracts.systems import SystemManifest
from .system import (
    BOARD_CHANGED_EVENT,
    BOARD_COLLAPSED_EVENT,
    CLOCK_TICK_EVENT,
    PIECE_LOCKED_EVENT,
    PIECE_SPAWNED_EVENT,
    WRITE_CELLS_COMMAND,
    GrowthInstallContext,
    GrowthSystem,
)

#: 落地后生长系统的对外承诺（**契约面进基线**）。
#:
#: **订阅五条**：`piece:locked`（触发生长）· `board:changed`（盘面镜像 + 写入确认）
#: · `board:collapsed`（盘面镜像要跟得上塌落）· `piece:spawned`（活动方块占哪几格
#: → 写的时候要避开）· `clock:tick`（每帧重试待写清单）。
#:
#: **派发 `writeCells`**：**外部改变棋盘的唯一合法通道**（FR-11.1 / B-5）。
#:
#: **产出者 `growth`**：**这个系统一条事件都不发**（棋盘变化由**内核**伴随，
#: N5：状态变更必须由状态所有者伴随）→ 它的产出者代号**就是系统名**
#: （需求 §8.2 的 `producer` 本来就允许「系统名」）。
#:
#: **不需要 sink**（`needsSink=False`）：**它的产出是棋盘上的格子**，不是文件。
SYSTEM_MANIFEST = SystemManifest(
    name="growth",
    version="1",
    subscribes=(
        PIECE_LOCKED_EVENT,
        BOARD_CHANGED_EVENT,
        BOARD_COLLAPSED_EVENT,
        PIECE_SPAWNED_EVENT,
        CLOCK_TICK_EVENT,
    ),
    commands=(WRITE_CELLS_COMMAND,),
    producer="growth",
    needsSink=False,
)


def create_system(context: GrowthInstallContext) -> GrowthSystem:
    """造一个落地后生长系统（**只要命令通道**）。

    **它不要总线**——**订阅由注册表按声明接上**（`on_event` 是入口），
    **它不要内核**——**要写就派命令**（`submit_cells`）。**最窄能力原则**。
    """
    return GrowthSystem(context.commands)


__all__ = ["SYSTEM_MANIFEST", "create_system"]
