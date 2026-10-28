"""日志系统的**对外声明**（M3 · FR-9.1 / FR-9.2）。

声明住在**系统自己的包里**——新增系统 = 新增文件，既有文件零改动
（M3-D2 / `G3-1`）。工厂（`LogSystem`）在 `system.py`，声明在这里，**两边分两层**：
声明进契约面（`contracts/systems.py` 的 `SystemManifest`），工厂不。
"""

from __future__ import annotations

from ...contracts.systems import GLOBAL_SUBSCRIPTION, SystemManifest

#: 日志系统的对外承诺（**契约面进基线**）。
#:
#: **订阅全局**（`"*"`）——需求 §8.1 的约定：日志系统订阅全局，因此新事件自动
#: 进入日志，无需额外接线。
#:
#: **产出者 `log`**：它发 `log:entry`（产出者 ≠ 命名空间）。
#:
#: **需要 sink**：日志落盘经宿主注入的出口（ADR-064）——它**知道有 sink，
#: 不知道路径**。
SYSTEM_MANIFEST = SystemManifest(
    name="log",
    version="1",
    subscribes=(GLOBAL_SUBSCRIPTION,),
    commands=(),
    producer="log",
    needsSink=True,
)

__all__ = ["SYSTEM_MANIFEST"]
