"""旋转统计系统的**对外声明与工厂**（M3-S6 · FR-9.1 / FR-9.2 · AC-3）。

声明与工厂**都住在系统自己的包里**——新增系统 = 新增文件，既有文件零改动
（M3-D2 / `G3-1`）。工厂（`create_system`）在**这个模块**里，实现
（`RotationsSystem`）在 `system.py`。
"""

from __future__ import annotations

from ...contracts.systems import SystemManifest
from ...kernel.snapshot import Snapshot
from ..context import InstallContext
from .system import PIECE_ROTATED_EVENT, RotationsSystem

#: 旋转统计系统的对外承诺（**契约面进基线**）。
#:
#: **只订阅 `piece:rotated`**——它只数旋转这一件事，别的都不看。
#:
#: **产出者 `rotations`**：**这个系统一条事件都不发**（产出是**计数文件**，
#: 不是事件）——所以它的产出者代号**就是系统名**（需求 §8.2 的元数据
#: `producer` 本来就允许「系统名」）。**它不在需求 §8.1 的「产出者」列里**：
#: 那一列是**事件**的产出者，而它不发事件。
#:
#: **需要 sink**（`needsSink=True`）：计数落盘经宿主注入的出口（M3-D5）——
#: 它**知道有 sink，不知道路径**。
SYSTEM_MANIFEST = SystemManifest(
    name="rotations",
    version="1",
    subscribes=(PIECE_ROTATED_EVENT,),
    commands=(),
    producer="rotations",
    needsSink=True,
)


def create_system(context: InstallContext) -> RotationsSystem[Snapshot]:
    """造一个旋转统计系统（**只拿总线**：不碰内核状态 · M3-D3）。

    **出口（sink）此刻拿不到**——安装上下文里没有 sink 字段，
    宿主接线（`tetris sim` 的落盘开关）落地之前它**只计数不落盘**。
    **它自己不许 `open()`**——路径是宿主的事（M3-D5）。
    """
    return RotationsSystem(context.bus)


__all__ = ["SYSTEM_MANIFEST", "create_system"]
