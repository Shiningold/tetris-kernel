"""宿主层：装配层、帧循环、命令行。

唯一知道"怎么把部件拼起来"的地方（架构 §4.1）。

批次 A 落在本层的只有**时钟源**（S7 口径 2）：真实 / 虚拟是同一接口的两种
实现，所以**放在同一个文件**——拆到两层就是两处维护同一对签名。

批次 C（S14）补上**装配层**与**无头帧循环**：`assemble()` 把内核、总线与
时钟拼成一件成品，`HeadlessLoop` 跑「命令轮次循环 → 帧推进五步 → 帧末拉
快照」的固定三段。命令行入口在 `host/cli.py`（**不**从包级导出——`main` 这
个名字太泛，入口按 `tetris_kernel.host.cli:main` 走）。

**批次 B（M2）补上落盘接线**：`Sink` 是落盘出口的类型（**装配约定，不属
于契约面**），`resolve_data_dir()` / `default_sink()` 是 ADR-074 的默认落盘
路径策略（**项目优先 → 不可写时回落 home**，写入只有一处，不设环境变量）。
"""

from .assembly import (
    DATA_DIR_NAME,
    HOME_DIR_NAME,
    OBSERVABLE_SYSTEM_FACTORIES,
    Assembly,
    DataDirectory,
    ObservableFactory,
    Sink,
    assemble,
    default_sink,
    resolve_data_dir,
)
from .clock import (
    DEFAULT_STEP_MS,
    Clock,
    RealClock,
    VirtualClock,
)
from .loop import (
    HeadlessLoop,
    RunReport,
)

__all__ = [
    "DATA_DIR_NAME",
    "DEFAULT_STEP_MS",
    "HOME_DIR_NAME",
    "OBSERVABLE_SYSTEM_FACTORIES",
    "Assembly",
    "Clock",
    "DataDirectory",
    "HeadlessLoop",
    "ObservableFactory",
    "RealClock",
    "RunReport",
    "Sink",
    "VirtualClock",
    "assemble",
    "default_sink",
    "resolve_data_dir",
]
