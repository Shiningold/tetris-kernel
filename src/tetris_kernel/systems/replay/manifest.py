"""回放系统的**对外声明**（M3 · FR-9.1 / FR-9.2）。

声明住在**系统自己的包里**——新增系统 = 新增文件，既有文件零改动
（M3-D2 / `G3-1`）。工厂（`RecordingSystem`）在 `recorder.py`，声明在这里。

**这个包里有三样东西，只有一样是「系统」**：录制系统（`recorder.py`）是系统；
播放会话（`playback.py`）由**宿主驱动**（不订阅任何事件）；事件面板（`trace.py`）
是 M2 的逐帧检视工具，不是系统。**所以本声明说的是录制系统那一半。**
"""

from __future__ import annotations

from ...contracts.systems import SystemManifest

#: 回放系统的对外承诺（**契约面进基线**）。
#:
#: **订阅四条事件**：`command:accepted`（**只录被接受的命令**——录了被拒的会
#: 分叉）· `clock:tick`（帧边界）· `game:over`（局末四项）· `kernel:ready`
#: （种子与规则集标识）。
#:
#: **产出者 `systems/replay/`**：它发 `replay:` 那 5 条（**产出者 ≠ 命名空间**）。
#:
#: **需要 sink**：录像是一份**交出的产物**，落盘经宿主注入的出口（覆盖语义）。
SYSTEM_MANIFEST = SystemManifest(
    name="replay",
    version="1",
    subscribes=(
        "command:accepted",
        "clock:tick",
        "game:over",
        "kernel:ready",
    ),
    commands=(),
    producer="systems/replay/",
    needsSink=True,
)

__all__ = ["SYSTEM_MANIFEST"]
