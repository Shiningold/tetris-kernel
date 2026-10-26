"""宿主装配层（S14 · plan-M0.md §1.4.4.1 ① · 架构 §4.1）。

**唯一知道"怎么把部件拼起来"的地方**（架构 §4.1）：内核、总线、规则集三样
在这里拼成一件成品，其余模块只管用。

## 装配层做三件事

1. **造内核**：方块类型清单**从规则层取**（`rules.tables.PIECE_TYPES`）——
   内核不抄名单，抄了就是两处会悄悄分叉的副本（架构 §7.1）；规则集经
   `Kernel(rule_set=…)` 注入，换规则集不改编配层以外的东西（FR-4.9）；
2. **造总线**：`view_provider` 必须是 `kernel.snapshot`（ADR-056：视图 = 快照，
   **同一个 dataclass**，不写两套）；
3. **造时钟**：省略时用 `VirtualClock(step_ms=step_ms)`——**无头批次不读墙钟**
   （架构 §5.3：时钟源落宿主层，内核只认逻辑帧）。

## M2 新增：注入 sink 与可观测总开关

**落盘只走 sink**（ADR-064 · M2-D2）：部件**不自己 `open()`、不用
`Path.home()`**——`systems/` 连 import `adapters/` 都不行（依赖方向，
lint-imports 守着），**它知道有 sink，不知道路径**。

**路径策略是宿主的装配决定**（ADR-074）：项目优先 → 不可写时回落 home，
**写入只有一处**，**不设环境变量**。`adapters/` 只管"怎么把字节写下去"，
**没有"默认路径"这个概念**。

**可观测总开关关掉的是"装配"，不是"订阅"**（`G2-3` 的前提）：开关关掉时
那些工厂**一个都不许被调用**——"装配了再退订"会让 `G2-3` 假绿（系统还在
跑，只是不发事件）。

## 默认值从各自的地盘取

棋盘尺寸取 `kernel.core` 的常量、步长取 `host.clock` 的常量、历史容量取
`bus.bus` 的常量——**不在这里另抄一份**，抄了就是第二个真相。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..adapters.storage import FileSink
from ..bus.bus import DEFAULT_HISTORY_LIMIT, EventBus
from ..kernel.core import (
    DEFAULT_BOARD_HEIGHT,
    DEFAULT_BOARD_WIDTH,
    DEFAULT_HIDDEN_ROWS,
    Kernel,
)
from ..kernel.quota import DEFAULT_LIMITS, QuotaLimits
from ..kernel.snapshot import Snapshot
from ..rules.interface import RuleSet
from ..rules.standard import STANDARD_RULESET
from ..rules.tables import PIECE_TYPES
from ..systems.log import LogSystem, LogTarget, SinkTarget
from ..systems.metrics import MetricsSystem
from ..systems.replay import RecordingSystem
from .clock import DEFAULT_STEP_MS, Clock, VirtualClock

#: 落盘出口（**装配约定，不属于契约面** —— spec-M2 §3.3 · ADR-064 / ADR-074）。
#:
#: **为什么只是 `Callable[[bytes], None]`**：sink 是"把字节交出去"的出口，
#: 落到哪儿由**宿主**决定。系统层**不需要 import 这个别名**——它是同一个
#: 可调用类型（结构化类型），系统侧自己写 `Callable[[bytes], None]` 即可
#: （`systems/` **不能** import `host/`，依赖方向使然）。
type Sink = Callable[[bytes], None]

#: 项目侧的默认数据目录名（ADR-074）。
DATA_DIR_NAME = ".tetris-data"

#: 回落侧的目录名（`~/.tetris/`，ADR-074）。
HOME_DIR_NAME = ".tetris"


@dataclass(frozen=True)
class DataDirectory:
    """落盘目录的**最终落点** + 回落信息（ADR-074）。

    **"回落要能被用户看见"**：这份记录就是那份可见性——`fell_back` 与
    `reason` 说清"落到哪儿了、为什么不是项目目录"。**不许默默换地方**
    （两处都写会让同一局录像分叉，`G2-1` 的比对会假红）。
    """

    path: Path
    fell_back: bool = False
    reason: str = ""


def _is_writable_dir(path: Path) -> bool:
    """目录（或其最近的**已存在**祖先）是否可写。

    **看祖先**：目标目录多半还不存在（要自建），那能不能建取决于**它上面
    那个已存在的目录**能不能写。`os.access` 只问"这个目录能不能写"，不问
    "能不能在里面建东西"。
    """
    probe = path
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            return False
        probe = parent
    return probe.is_dir() and os.access(probe, os.W_OK)


def resolve_data_dir(
    *,
    project_root: Path | None = None,
    home: Path | None = None,
    writable: Callable[[Path], bool] | None = None,
) -> DataDirectory:
    """ADR-074 的默认落盘路径策略：**项目优先 → 不可写时回落 home**。

    - **写入只有一处**：要么项目、要么 home，**不在两处各写一份**；
    - **不设环境变量**：不读 `TETRIS_DATA_DIR` 之类，也不要求用户预先配置；
    - **解析路径不创建任何目录**——目录由落盘的那一环自建（`FileSink`），
      **解析路径不该有副作用**；
    - **回落不是静默的**：`fell_back` 与 `reason` 说清为什么。

    - `project_root`：项目根（省略时取**当前工作目录**——"项目目录"就是
      用户干活的那个目录）；`home`：用户主目录（省略时取 `Path.home()`）；
    - `writable`：可写性判定（**注入点**——"项目目录不可写"这件事在
      Windows 上很难可靠地造出来，测试靠注入判定函数，而不是靠改文件属性）。
    """
    root = Path.cwd() if project_root is None else Path(project_root)
    user_home = Path.home() if home is None else Path(home)
    probe = _is_writable_dir if writable is None else writable

    project_dir = root / DATA_DIR_NAME
    if probe(project_dir):
        return DataDirectory(path=project_dir, fell_back=False, reason="")

    fallback = user_home / HOME_DIR_NAME
    reason = f"项目目录不可写：{project_dir}"
    if not probe(fallback):
        # home 也不可写：策略到此为止，**写下去会当场抛错**（OSError）——
        # **不静默失败**（"默默换地方"与"假装能写"都是坏的）。
        reason = f"{reason}；回落目录也不可写：{fallback}"
    return DataDirectory(path=fallback, fell_back=True, reason=reason)


def default_sink(
    filename: str,
    *,
    project_root: Path | None = None,
    home: Path | None = None,
) -> tuple[Sink, DataDirectory]:
    """造一个落在默认数据目录里的落盘出口，并**回报最终落点**。

    **回落会打一行到 stderr**：这是"回落要能被用户看见"的兑现——返回值是
    给程序读的，那一行是给**人**看的。**库代码不替调用方做决定，但也不
    让它悄悄发生。**

    - `filename`：**不带墙钟**（ADR-064——文件名带时间戳会让"同一局录两次"
      产生两份文件）。
    """
    directory = resolve_data_dir(project_root=project_root, home=home)
    if directory.fell_back:
        print(f"落盘目录已回落到 home：{directory.path}（{directory.reason}）", file=sys.stderr)
    return FileSink(directory.path / filename), directory


#: 可观测系统的装配入口（**批次 C 往这张表里填满了**）。
#:
#: 每个工厂的签名一致：`(bus, sink) -> 系统对象`。**开关关掉时这些工厂
#: 一个都不许被调用**——不是"装配了再退订"（那是 `G2-3` 的假绿陷阱：
#: 系统还在跑，只是不发事件）。
#:
#: **为什么只有两个参数**：种子与规则集标识**不进工厂**——它们由内核从
#: `kernel:ready` 事件里带出来，录制系统自己去取（内核是那两项的权威）。
#: 顺带**装配层一个事件都不发**（宿主的一条不变量：造零件不许自己造事件）。
type ObservableFactory = Callable[[EventBus[Snapshot], Sink | None], object]


def _build_log_system(bus: EventBus[Snapshot], sink: Sink | None) -> LogSystem:
    """日志系统（**普通订阅者** · 级别映射在系统内 · ADR-046）。

    **默认不挂控制台目标**：控制台是**人读**的输出，日志系统的输出目标
    **可插拔**——装配层默认只挂文件目标（经 sink），**要不要往控制台打由调用方
    自己加目标**。
    """
    targets: tuple[LogTarget, ...] = () if sink is None else (SinkTarget(sink),)
    return LogSystem(bus, targets)


def _build_metrics_system(bus: EventBus[Snapshot], sink: Sink | None) -> MetricsSystem:
    """指标系统（**普通订阅者** · 每帧一条 `metric:sampled`）。"""
    return MetricsSystem(bus, sink)


def _build_recording_system(bus: EventBus[Snapshot], sink: Sink | None) -> RecordingSystem:
    """录像录制系统（**只录被接受的命令** · M2-D3）。"""
    return RecordingSystem(bus, sink)


#: 可观测系统的装配表（**批次 C 填上了三个系统**）。
#:
#: **顺序不影响结果**：三个系统都只**订阅**总线，谁先装谁后装看到的都是
#: 同一串事件——**装配层不发事件**（见上面的签名说明），没有"谁先谁后能看见
#: 谁的启动事件"这回事。
OBSERVABLE_SYSTEM_FACTORIES: tuple[ObservableFactory, ...] = (
    _build_log_system,
    _build_metrics_system,
    _build_recording_system,
)


@dataclass(frozen=True)
class Assembly:
    """装配好的三件套（**内核 / 总线 / 时钟**）+ M2 的可观测接线。

    - `kernel`：唯一的状态所有者（架构 §7.1）；
    - `bus`：事件的传输与分发中枢（序号由它分配）；
    - `clock`：帧循环的时钟源（**可注入**——回放 / 逐帧步进 / 倍速播放走同
      一个接口，架构 §5.3）。**M0 尚无消费方**：帧循环跑帧不 sleep、不看时间，
      `step_ms` 对 M0 的节奏零影响——这个口是给 M1 的回放 / 倍速预留的，别把
      "预留"当成"已实现"；
    - `sink`：**宿主注入的落盘出口**（`None` = 不落盘）；**路径由宿主定**
      （ADR-064 / ADR-074）——部件自己不 `open()`；
    - `observable_systems`：装配出来的可观测系统（**开关关掉时为空**）；
    - `observability_enabled`：可观测总开关的取值（**关掉 = 一个都不装配**）。
    """

    kernel: Kernel
    bus: EventBus[Snapshot]
    clock: Clock
    sink: Sink | None = None
    observable_systems: tuple[object, ...] = ()
    observability_enabled: bool = True


def assemble(
    *,
    seed: int = 0,
    piece_types: tuple[str, ...] = PIECE_TYPES,
    rule_set: RuleSet = STANDARD_RULESET,
    rule_id: str = "standard",
    board_width: int = DEFAULT_BOARD_WIDTH,
    board_height: int = DEFAULT_BOARD_HEIGHT,
    hidden_rows: int = DEFAULT_HIDDEN_ROWS,
    step_ms: int = DEFAULT_STEP_MS,
    mode: str = "live",
    limits: QuotaLimits = DEFAULT_LIMITS,
    clock: Clock | None = None,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
    sink: Sink | None = None,
    observability_enabled: bool = True,
) -> Assembly:
    """把内核、总线、规则集与时钟拼成一件成品。

    - `seed`：内核随机源种子（FR-1.4）；
    - `piece_types`：方块类型清单——**默认从规则层取**，内核自己不抄；
    - `rule_set`：规则接口（9 条，FR-4.9 可整体替换）；
    - `rule_id`：规则集标识（进快照与 `kernel:ready`）；
    - `board_width` / `board_height` / `hidden_rows`：棋盘尺寸（隐藏行是**数量**，
      不是可见起始行）；
    - `step_ms`：固定步长（毫秒，逻辑时间 = 帧号 × 步长）；
    - `mode`：运行模式（只随 `kernel:ready` 透传，**内核不按它分支**）；
    - `limits`：配额上限（**必须是构造参数**——回放宿主注入大得多的上限）；
    - `clock`：时钟源；**省略时用 `VirtualClock(step_ms=step_ms)`**——无头批次
      不读墙钟；
    - `history_limit`：总线环形历史保留条数；
    - `sink`：**宿主注入的落盘出口**（`None` = 不落盘）。**路径由宿主定**
      （M2-D2）——部件自己不 `open()`、不用 `Path.home()`；
    - `observability_enabled`：**可观测总开关**。关掉时那些工厂**一个都不被
      调用**——`G2-3`（关闭全部可观测系统后内核逐帧一致）的前提。
    """
    kernel = Kernel(
        seed=seed,
        piece_types=piece_types,
        rule_set=rule_set,
        rule_id=rule_id,
        board_width=board_width,
        board_height=board_height,
        hidden_rows=hidden_rows,
        step_ms=step_ms,
        mode=mode,
        limits=limits,
    )
    bus: EventBus[Snapshot] = EventBus[Snapshot](
        # 视图 = 快照，**同一个 dataclass**（ADR-056）。
        view_provider=kernel.snapshot,
        history_limit=history_limit,
    )
    observable_systems: tuple[object, ...] = ()
    if observability_enabled:
        # **开关关掉时这些工厂一个都不被调用**——不是"装配了再退订"。
        observable_systems = tuple(factory(bus, sink) for factory in OBSERVABLE_SYSTEM_FACTORIES)
    return Assembly(
        kernel=kernel,
        bus=bus,
        clock=VirtualClock(step_ms=step_ms) if clock is None else clock,
        sink=sink,
        observable_systems=observable_systems,
        observability_enabled=observability_enabled,
    )


__all__ = [
    "DATA_DIR_NAME",
    "HOME_DIR_NAME",
    "Assembly",
    "DataDirectory",
    "ObservableFactory",
    "OBSERVABLE_SYSTEM_FACTORIES",
    "Sink",
    "assemble",
    "default_sink",
    "resolve_data_dir",
]
