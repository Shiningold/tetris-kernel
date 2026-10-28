"""指标系统的**对外声明**（M3 · FR-9.1 / FR-9.2）。

声明住在**系统自己的包里**——新增系统 = 新增文件，既有文件零改动
（M3-D2 / `G3-1`）。工厂（`MetricsSystem`）在 `system.py`，声明在这里。
"""

from __future__ import annotations

from ...contracts.systems import GLOBAL_SUBSCRIPTION, SystemManifest

#: 指标系统的对外承诺（**契约面进基线**）。
#:
#: **订阅全局**（`"*"`）——它聚合每一帧的指标，新事件自动进采样。
#:
#: **产出者 `metrics`**：它发 `metric:sampled`（产出者 ≠ 命名空间）。
#:
#: **需要 sink**：指标落盘经宿主注入的出口（ADR-064 · ADR-085）。
SYSTEM_MANIFEST = SystemManifest(
    name="metrics",
    version="1",
    subscribes=(GLOBAL_SUBSCRIPTION,),
    commands=(),
    producer="metrics",
    needsSink=True,
)

__all__ = ["SYSTEM_MANIFEST"]
