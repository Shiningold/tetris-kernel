"""成就系统的**对外声明与工厂**（M3-S5 · FR-9.1 / FR-9.2 · FR-9.3）。

声明与工厂**都住在系统自己的包里**——新增系统 = 新增文件，既有文件零改动
（M3-D2 / `G3-1`）。工厂（`create_system`）在**这个模块**里，实现
（`AchievementsSystem`）在 `system.py`，成就在 `definitions.py`。
"""

from __future__ import annotations

from ...contracts.systems import GLOBAL_SUBSCRIPTION, SystemManifest
from ...kernel.snapshot import Snapshot
from ..context import InstallContext
from .system import AchievementsSystem

#: 成就系统的对外承诺（**契约面进基线**）。
#:
#: **订阅全局**（`"*"`）——达成条件由**成就定义**决定（定义按事件类型与
#: 计数写），而定义会随系统成长；**订阅全局时新事件自动进达成判定**，
#: 无需改声明。
#:
#: **产出者 `achievements`**：它发 `achievement:unlocked`（产出者 ≠ 命名空间）。
#:
#: **不需要 sink**（`needsSink=False`）：成就是**事件**，不是文件——
#: 落盘是订阅者的事（路径是宿主的事 · M3-D5）。
SYSTEM_MANIFEST = SystemManifest(
    name="achievements",
    version="1",
    subscribes=(GLOBAL_SUBSCRIPTION,),
    commands=(),
    producer="achievements",
    needsSink=False,
)


def create_system(context: InstallContext) -> AchievementsSystem[Snapshot]:
    """造一个成就系统（**只拿总线**：不碰内核状态 · M3-D3）。

    **上下文里没有 sink**——成就不落盘，用不上。
    """
    return AchievementsSystem(context.bus)


__all__ = ["SYSTEM_MANIFEST", "create_system"]
