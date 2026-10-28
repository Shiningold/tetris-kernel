"""AI 系统的**对外声明与工厂**（系统层 · M4-S9 · FR-8.5 / FR-9.1 / FR-9.2）。

## 声明与工厂都住在系统自己的包里

与 `systems/achievements/` · `systems/rotations/` 同款约定（`systems/manifests.py`）：
**声明**（`SYSTEM_MANIFEST`）与**工厂**（`create_system`）都在本模块，
实现住在 `system.py`。

**新增能力 = 新增文件**（M3-D2 / `G3-1`）：**宿主不维护手写名单**——
`host/discovery.py` 扫到本包就把 AI 收进系统目录与依赖图。

## 声明的形状是契约面

`SystemManifest` 住在 `contracts/systems.py`——**AI 与普通系统受同一把自证的
尺子**（订阅的事件与派发的命令必须已登记 · 产出者必须是代号表里的名字或就是
它自己的名字）。

**它会进契约基线**（`system/ai` 一条键）——契约面**只增不改**（N6）：
新增一条声明是**新增**，既有键的哈希一个都不变。
"""

from __future__ import annotations

from ...contracts.systems import SystemManifest
from ..context import InstallContext
from .search import (
    HARD_DROP,
    MOVE_LEFT,
    MOVE_RIGHT,
    ROTATE_180,
    ROTATE_CCW,
    ROTATE_CW,
)
from .system import CLOCK_TICK_EVENT, PIECE_SPAWNED_EVENT, AiSystem

#: AI 系统的对外承诺（**契约面进基线**）。
#:
#: **订阅两条**：`piece:spawned`（新方块 = 新的决策机会）· `clock:tick`
#: （派命令的时机——**滴答那一刻的视图就是方块当下的位置**）。
#:
#: **派发六条**：`moveLeft` / `moveRight`（平移）· `rotateCW` / `rotate180` /
#: `rotateCCW`（旋转）· `hardDrop`（落子）——**与人类同通道**（FR-8.5）。
#:
#: **产出者 `ai`**：**这个系统一条事件都不发**（它的产出是**命令**，
#: 不是事件）→ 产出者代号**就是系统名**（需求 §8.2 的 `producer` 本来就允许
#: 「系统名」）。
#:
#: **不需要 sink**（`needsSink=False`）：**它的产出是命令，不是文件**。
SYSTEM_MANIFEST = SystemManifest(
    name="ai",
    version="1",
    subscribes=(
        PIECE_SPAWNED_EVENT,
        CLOCK_TICK_EVENT,
    ),
    commands=(
        MOVE_LEFT,
        MOVE_RIGHT,
        ROTATE_CW,
        ROTATE_180,
        ROTATE_CCW,
        HARD_DROP,
    ),
    producer="ai",
    needsSink=False,
)


def create_system(context: InstallContext) -> AiSystem:
    """造一个 AI 系统（**只要总线与命令通道**）。

    **它不要内核**——**要动局面就派命令**（与人类同一条通道）。
    """
    return AiSystem(context.bus, context.commands)


__all__ = ["SYSTEM_MANIFEST", "create_system"]
