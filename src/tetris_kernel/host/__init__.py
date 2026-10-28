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

**M2 审查修订补上"三个产物各用各的出口"**（ADR-085）：`ObservableSinks` 是
三个出口的打包（日志 · 指标 · 录像），`default_sinks()` 一次解析目录、造出
三个文件——**一个产物一个文件**。日志与指标是**追加语义**，录像是**覆盖
语义**。

**批次 D（S12）补上降级控制器**：`DegradationController` 按 ADR-031 的**三档
固定顺序**与**滞回**调档——① 关闭调试事件 → ② 降低日志级别 → ③ 关闭事件历史
缓冲。**默认不装配**（M2 是无头批次，喂帧开销的接线留给 M3 的实时宿主）。
"""

from .assembly import (
    DATA_DIR_NAME,
    HOME_DIR_NAME,
    OBSERVABLE_SYSTEM_FACTORIES,
    RULE_SETS,
    Assembly,
    DataDirectory,
    FrameCounter,
    ObservableFactory,
    ObservableSinks,
    PlayAssembly,
    Sink,
    assemble,
    assemble_play,
    default_sink,
    default_sinks,
    register_rule_set,
    resolve_data_dir,
    rule_set_of,
    unregister_rule_set,
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
from .performance import (
    DEGRADE_P99_MS,
    DEGRADE_WINDOW_FRAMES,
    DEGRADED_MIN_LEVEL,
    RECOVER_FRAMES,
    RECOVER_P99_MS,
    THOUSAND_BASE_SEED,
    THOUSAND_BUDGET_SECONDS,
    TIER_DEBUG_EVENTS,
    TIER_EVENT_HISTORY,
    TIER_LOG_LEVEL,
    TIER_NONE,
    TIERS,
    DegradationController,
    FrameCostWindow,
    measure_frame,
)
from .play import (
    MAX_CATCHUP_FRAMES,
    PlayReport,
    PlaySession,
)
from .variants import (
    VARIANTS,
    VariantManifest,
    register_variant,
    unregister_variant,
    variant_catalog,
    variant_of,
    variant_rule_set,
    variant_systems,
)

__all__ = [
    "DATA_DIR_NAME",
    "DEGRADE_P99_MS",
    "DEGRADE_WINDOW_FRAMES",
    "DEGRADED_MIN_LEVEL",
    "DEFAULT_STEP_MS",
    "HOME_DIR_NAME",
    "MAX_CATCHUP_FRAMES",
    "OBSERVABLE_SYSTEM_FACTORIES",
    "RECOVER_FRAMES",
    "RECOVER_P99_MS",
    "RULE_SETS",
    "THOUSAND_BASE_SEED",
    "THOUSAND_BUDGET_SECONDS",
    "TIERS",
    "TIER_DEBUG_EVENTS",
    "TIER_EVENT_HISTORY",
    "TIER_LOG_LEVEL",
    "TIER_NONE",
    "VARIANTS",
    "Assembly",
    "Clock",
    "DataDirectory",
    "DegradationController",
    "FrameCostWindow",
    "FrameCounter",
    "HeadlessLoop",
    "ObservableFactory",
    "ObservableSinks",
    "PlayAssembly",
    "PlayReport",
    "PlaySession",
    "RealClock",
    "RunReport",
    "Sink",
    "VariantManifest",
    "VirtualClock",
    "assemble",
    "assemble_play",
    "default_sink",
    "default_sinks",
    "measure_frame",
    "register_rule_set",
    "register_variant",
    "resolve_data_dir",
    "rule_set_of",
    "unregister_rule_set",
    "unregister_variant",
    "variant_catalog",
    "variant_of",
    "variant_rule_set",
    "variant_systems",
]
