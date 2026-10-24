"""宿主层：装配层、帧循环、命令行。

唯一知道"怎么把部件拼起来"的地方（架构 §4.1）。

批次 A 落在本层的只有**时钟源**（S7 口径 2）：真实 / 虚拟是同一接口的两种
实现，所以**放在同一个文件**——拆到两层就是两处维护同一对签名。
装配层与帧循环随 S14 补上，**不提前实现别的阶段的事**。
"""

from .clock import (
    DEFAULT_STEP_MS,
    Clock,
    RealClock,
    VirtualClock,
)

__all__ = [
    "DEFAULT_STEP_MS",
    "Clock",
    "RealClock",
    "VirtualClock",
]
