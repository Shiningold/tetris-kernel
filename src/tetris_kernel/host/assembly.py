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

**"写入只有一处"说的是一条落盘路径，不是一个文件**（ADR-085）：日志、指标、
录像是**三份不同的产物**，各有各的出口——**一个产物一个文件**。三个产物往
同一个文件写，**后写的覆盖先写的**（用覆盖语义写日志，文件里只剩最后一条）。
日志与指标是**一条一条往外冒**的流 → **追加语义**；录像是**整份交出**的产物
→ **覆盖语义**（两种语义是两个类，不是同一个类的两种模式）。

**可观测总开关关掉的是"装配"，不是"订阅"**（`G2-3` 的前提）：开关关掉时
那些工厂**一个都不许被调用**——"装配了再退订"会让 `G2-3` 假绿（系统还在
跑，只是不发事件）。

**降级控制器是可选接线**（ADR-031）：`degradation_enabled=True` 才装配
`DegradationController`——它要一个**日志系统**（第 ① 档关 debug 事件 · 第 ② 档
抬阈值），那个系统从装配出来的可观测系统里挑。**默认不装**：M2 是**无头批次**，
没有实时宿主去测每帧耗时，**喂帧开销的接线留给 M3**。

## 默认值从各自的地盘取

棋盘尺寸取 `kernel.core` 的常量、步长取 `host.clock` 的常量、历史容量取
`bus.bus` 的常量——**不在这里另抄一份**，抄了就是第二个真相。
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from ..adapters.storage import AppendSink, FileSink
from ..bus.bus import DEFAULT_HISTORY_LIMIT, EventBus
from ..contracts.systems import SystemManifest
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
from ..systems.log.manifest import SYSTEM_MANIFEST as LOG_MANIFEST
from ..systems.metrics import MetricsSystem
from ..systems.metrics.manifest import SYSTEM_MANIFEST as METRICS_MANIFEST
from ..systems.replay import OutcomeProvider, RecordingSystem
from ..systems.replay.format import recording_filename
from ..systems.replay.manifest import SYSTEM_MANIFEST as REPLAY_MANIFEST
from .clock import DEFAULT_STEP_MS, Clock, VirtualClock
from .performance import DegradationController
from .registry import SystemContext, SystemEntry, SystemFactory, SystemRegistry
from .replay import four_items_of

#: 落盘出口（**装配约定，不属于契约面** —— spec-M2 §3.3 · ADR-064 / ADR-074）。
#:
#: **为什么只是 `Callable[[bytes], None]`**：sink 是"把字节交出去"的出口，
#: 落到哪儿由**宿主**决定。系统层**不需要 import 这个别名**——它是同一个
#: 可调用类型（结构化类型），系统侧自己写 `Callable[[bytes], None]` 即可
#: （`systems/` **不能** import `host/`，依赖方向使然）。
type Sink = Callable[[bytes], None]


@dataclass(frozen=True)
class ObservableSinks:
    """三个可观测产物**各自**的落盘出口（**一个产物一个文件** · ADR-085）。

    - `log`：日志条目（**一条一条往外冒**）；
    - `metrics`：指标采样（**一帧一条**）；
    - `recording`：录像（**整份交出**）。

    **为什么不是一个 sink**：三个产物往**同一个文件**写，**后写的覆盖先写的**
    ——用覆盖语义写日志，文件里只剩最后一条（审查 H1）。**"写入只有一处"说的
    是一条落盘路径，不是一个文件**（ADR-074）。

    **三个字段各自可为 `None`**：`None` = **这一个产物不落盘**（不是"整个
    都不落盘"——日志可以不落、录像不能不落）。
    """

    log: Sink | None = None
    metrics: Sink | None = None
    recording: Sink | None = None


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
    """目录（或其最近的**已存在**祖先）是否**真能写**。

    **看祖先**：目标目录多半还不存在（要自建），那能不能建取决于**它上面
    那个已存在的目录**能不能写。

    **真写一次，不问 `os.access`**（审查 L3）：Windows 上目录 **ACL** 存在时
    `os.access` 的结果**不可靠**——可能认为可写，真写时抛 `PermissionError`；
    `Path.exists()` 对权限不足的目录也可能返回 `False`。**真写一次**才是
    ADR-074「不可写时回落 home」这条承诺的兑现。

    **探针不留残骸**：在目录里建一个临时文件**立刻删掉**——建不出来就是
    不可写。**它确实动过文件系统**（建 + 删一个临时文件）——这与
    "解析路径不创建任何目录"不冲突（**不建目录**），但**动过**这件事写
    在这里，不瞒。
    """
    probe = path
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            return False
        probe = parent
    if not probe.is_dir():
        return False
    return _can_write_in(probe)


def _can_write_in(directory: Path) -> bool:
    """在目录里**真写一次**（建个临时文件再删掉）——能不能写，问文件系统。

    **失败一律当不可写**：权限不足、磁盘满、只读挂载……**判不准就当不可
    写**（ADR-074 的回落路径是安全的那一侧）。
    """
    try:
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".tetris-writable-probe-", delete=True
        ):
            pass
    except OSError:
        return False
    return True


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
      **省略时走真写探针**（`_is_writable_dir`：建个临时文件再删掉）——不是
      问 `os.access`（ACL 场景下不可靠 · 审查 L3）。
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

    **这是覆盖语义的那个**（`FileSink`）：适合**整份交出**的产物（录像）。
    **日志与指标要追加语义**——用这个函数造它们的出口，文件里只会剩最后
    一条。要三个产物**各有各的出口**，用 `default_sinks()`（ADR-085）。
    """
    directory = resolve_data_dir(project_root=project_root, home=home)
    if directory.fell_back:
        print(f"落盘目录已回落到 home：{directory.path}（{directory.reason}）", file=sys.stderr)
    return FileSink(directory.path / filename), directory


def default_sinks(
    *,
    rule_id: str = "standard",
    project_root: Path | None = None,
    home: Path | None = None,
) -> tuple[ObservableSinks, DataDirectory]:
    """造**三个**落在默认数据目录里的落盘出口，并**回报最终落点**。

    **一个产物一个文件**（ADR-085）：

    | 产物 | 文件名 | 语义 | 为什么 |
    | --- | --- | --- | --- |
    | 日志 | `log-<规则集>.jsonl` | **追加** | 一条一条往外冒 |
    | 指标 | `metrics-<规则集>.jsonl` | **追加** | 一帧一条 |
    | 录像 | `recording-<规则集>.jsonl` | **覆盖** | 整份交出（原子替换） |

    **文件名不带墙钟**（ADR-064）：带时间戳会让"同一局录两次"产生两份
    不同的文件。

    **日志与指标是追加的**：文件里接着上一次运行的内容往下写——它们是
    **流**，不是一份交出的产物。要"每次从空开始"，自己造 sink（**宿主说了
    算**）。

    **规则集标识进文件名**：不同规则集的两局**各写各的**，不互相覆盖。
    **录像的文件名与录像格式模块给的 `recording_filename()` 逐字一致**——
    文件名是录像格式的一部分（spec §7.1），**不许这儿另起一个名字**。
    """
    safe_rule_id = _safe_rule_id(rule_id)
    directory = resolve_data_dir(project_root=project_root, home=home)
    if directory.fell_back:
        print(f"落盘目录已回落到 home：{directory.path}（{directory.reason}）", file=sys.stderr)
    return (
        ObservableSinks(
            log=AppendSink(directory.path / f"log-{safe_rule_id}.jsonl"),
            metrics=AppendSink(directory.path / f"metrics-{safe_rule_id}.jsonl"),
            recording=FileSink(directory.path / recording_filename(safe_rule_id)),
        ),
        directory,
    )


#: 规则集标识在文件名里允许出现的字符（**路径安全** · 与录像格式模块同款）。
_RULE_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _safe_rule_id(rule_id: str) -> str:
    """规则集标识 → **文件名安全**的一段（路径分隔符、空格都不许进）。

    **为什么当场报错而不是替换掉**：文件名要能反查回规则集——把 `a/b` 悄悄
    改成 `a-b`，查录像时才知道自己拿错了。**错了就报错**。

    **为什么放行这一组字符**：录像格式模块的 `recording_filename()` 用的是
    同一组（`isalnum()` 加 `-_.`）——**两边必须一致**，否则录像文件的名字会
    有两个真相。
    """
    if not rule_id or not set(rule_id) <= _RULE_ID_CHARS:
        raise ValueError(f"规则集标识不能进文件名：{rule_id!r}")
    return rule_id


def _outcome_provider_of(kernel: Kernel) -> OutcomeProvider:
    """造一个"给我局末四项"的可调用对象（**宿主注入** · ADR-085）。

    **为什么只能由宿主注入**：系统层**不碰内核状态**（M2-D1），而**棋盘指纹
    的算法只有内核那一份**（抄第二份就是第二个真相 · 架构 §7.1）。所以系统层
    只拿到一个**可调用对象**——**拿不到内核本身**（最窄能力原则）。

    **算法只有这一处**：四项由 `four_items_of()` 算——**重放驱动器也用它**
    （同一个 `host/replay.py`），两处必须算出同一个值。
    """
    return lambda: four_items_of(kernel.snapshot())


#: 可观测系统的装配入口（**批次 C 往这张表里填满了**）。
#:
#: 每个工厂的签名一致：`(bus, sinks, outcome_provider) -> 系统对象`。
#: **开关关掉时这些工厂一个都不许被调用**——不是"装配了再退订"（那是
#: `G2-3` 的假绿陷阱：系统还在跑，只是不发事件）。
#:
#: **为什么种子与规则集标识不进工厂**：它们由内核从 `kernel:ready` 事件里
#: 带出来，录制系统自己去取（内核是那两项的权威）。顺带**装配层一个事件都
#: 不发**（宿主的一条不变量：造零件不许自己造事件）。
#:
#: **为什么三个出口打包成一个对象**：三个产物**各有各的文件**（ADR-085），
#: 打包起来才不会"忘了给某一个产物分一个"——只给一个 `sink` 的话，三个
#: 系统会**共用它**，后写的覆盖先写的。
#:
#: **为什么局末四项的取法也进签名**：它**只能由宿主注入**——棋盘指纹的
#: 算法只有内核那一份（抄第二份就是第二个真相），而系统层**不碰内核状态**
#: （M2-D1）。工厂**拿得到 provider、拿不到 kernel**（最窄能力原则）。
type ObservableFactory = Callable[[EventBus[Snapshot], ObservableSinks, OutcomeProvider], object]


def _build_log_system(
    bus: EventBus[Snapshot], sinks: ObservableSinks, outcome_provider: OutcomeProvider
) -> LogSystem:
    """日志系统（**普通订阅者** · 级别映射在系统内 · ADR-046）。

    **默认不挂控制台目标**：控制台是**人读**的输出，日志系统的输出目标
    **可插拔**——装配层默认只挂文件目标（经 sink），**要不要往控制台打由调用方
    自己加目标**。

    **只用自己的那一个出口**（ADR-085）：日志写 `sinks.log`。
    """
    targets: tuple[LogTarget, ...] = () if sinks.log is None else (SinkTarget(sinks.log),)
    return LogSystem(bus, targets)


def _build_metrics_system(
    bus: EventBus[Snapshot], sinks: ObservableSinks, outcome_provider: OutcomeProvider
) -> MetricsSystem:
    """指标系统（**普通订阅者** · 每帧一条 `metric:sampled`）。

    **只用自己的那一个出口**（ADR-085）：指标写 `sinks.metrics`。
    """
    return MetricsSystem(bus, sinks.metrics)


def _build_recording_system(
    bus: EventBus[Snapshot], sinks: ObservableSinks, outcome_provider: OutcomeProvider
) -> RecordingSystem:
    """录像录制系统（**只录被接受的命令** · M2-D3）。

    **局末四项由宿主注入**（`outcome_provider`）：系统层**不碰内核状态**
    （M2-D1），**棋盘指纹的算法也只有内核那一份**——装配层不注入 provider，
    录像头里就没有比对基准，`G2-1` 的四项全等**永远没有基准可比**（审查 H2）。

    **只用自己的那一个出口**（ADR-085）：录像写 `sinks.recording`（**覆盖
    语义**——整份交出）。
    """
    return RecordingSystem(bus, sinks.recording, outcome_provider=outcome_provider)


#: 内建可观测系统的**名字 → 工厂**（M2 的三个系统）。
#:
#: **为什么它们的名字写在这儿**：它们的**落盘出口由宿主注入**（ADR-085），工厂
#: 本来就住在宿主层；**声明住在各自的系统包里**（`systems/<名字>/manifest.py`），
#: 名字与声明**从包里取**（`BUILT_IN_MANIFESTS`），这里只说「哪个工厂服务哪个
#: 名字」——**一个系统一个名字，没有第二份名单**。
#:
#: **这张表是内建清单，不是系统目录**：**新系统不许加进这里**——新系统自带声明
#: 与工厂，由自动发现收（`host/discovery.py` · M3-D2 / `G3-1`）。**名单一旦成为
#: 必经之路，每个新系统都要改一个既有文件，那条判据就永久失去牙齿。**
BUILT_IN_OBSERVABLE_FACTORIES: Mapping[str, ObservableFactory] = MappingProxyType(
    {
        "log": _build_log_system,
        "metrics": _build_metrics_system,
        "replay": _build_recording_system,
    }
)

#: 内建系统的**声明**（**从各自的系统包里取** —— 名字与声明同源）。
BUILT_IN_MANIFESTS: Mapping[str, SystemManifest] = MappingProxyType(
    {
        "log": LOG_MANIFEST,
        "metrics": METRICS_MANIFEST,
        "replay": REPLAY_MANIFEST,
    }
)


def built_in_system_entries(
    sinks: ObservableSinks,
    outcome_provider: OutcomeProvider,
) -> Mapping[str, SystemEntry]:
    """内建系统的**目录条目**（名字 → 声明 + 工厂）。

    **工厂的签名在这里适配**：M2 的工厂是 `(bus, sinks, outcome_provider)`，
    注册表给的是 `(context)`——**适配发生在宿主这一侧**，系统包里的工厂不用改
    签名（内建系统的工厂本来就在宿主层）。
    """
    entries: dict[str, SystemEntry] = {}
    for name, factory in BUILT_IN_OBSERVABLE_FACTORIES.items():
        manifest = BUILT_IN_MANIFESTS.get(name)
        if manifest is None:
            raise KeyError(f"内建系统 {name} 没有声明")
        entries[name] = SystemEntry(
            manifest=manifest,
            factory=_adapt_factory(factory, sinks, outcome_provider),
        )
    return MappingProxyType(entries)


def _adapt_factory(
    factory: ObservableFactory,
    sinks: ObservableSinks,
    outcome_provider: OutcomeProvider,
) -> SystemFactory:
    """把 M2 的工厂签名适配成注册表的 `(context)` 签名。"""

    def create(context: SystemContext) -> object:
        return factory(context.bus, sinks, outcome_provider)

    return create


#: 可观测系统的装配表（**批次 C 填上了三个系统**）。
#:
#: **顺序不影响结果**：三个系统都只**订阅**总线，谁先装谁后装看到的都是
#: 同一串事件——**装配层不发事件**（见上面的签名说明），没有"谁先谁后能看见
#: 谁的启动事件"这回事。
OBSERVABLE_SYSTEM_FACTORIES: tuple[ObservableFactory, ...] = tuple(
    BUILT_IN_OBSERVABLE_FACTORIES.values()
)


# ---------------------------------------------------------------------------
# 规则集接线：标识 → 规则集
# ---------------------------------------------------------------------------

#: 规则集标识 → 规则集（**宿主层的接线表** · 变体属 M4）。
#:
#: **为什么在宿主层**：`rules/` 是**冻结区**（M2-D5）——"哪个标识对应哪个
#: 规则集"是**宿主的接线决定**，不是规则层自己的事。**变体规则集来了就往
#: 这张表里加一行**（M4）。
#:
#: **为什么不能拿标准规则集顶替**：标识写着"变体"、内核却跑标准规则——
#: **重放出来的四项必然不等**，而报错只说"四项不等"，看不出"起错规则集了"。
#: **表里没有就是没有**（`rule_set_of()` 返回 `None`）。
RULE_SETS: Mapping[str, RuleSet] = MappingProxyType({"standard": STANDARD_RULESET})


def rule_set_of(rule_id: str) -> RuleSet | None:
    """按标识取规则集（**没有就是没有**——不拿别的规则集顶替）。"""
    return RULE_SETS.get(rule_id)


@dataclass(frozen=True)
class Assembly:
    """装配好的三件套（**内核 / 总线 / 时钟**）+ M2 的可观测接线。

    - `kernel`：唯一的状态所有者（架构 §7.1）；
    - `bus`：事件的传输与分发中枢（序号由它分配）；
    - `clock`：帧循环的时钟源（**可注入**——回放 / 逐帧步进 / 倍速播放走同
      一个接口，架构 §5.3）。**M0 尚无消费方**：帧循环跑帧不 sleep、不看时间，
      `step_ms` 对 M0 的节奏零影响——这个口是给 M1 的回放 / 倍速预留的，别把
      "预留"当成"已实现"；
    - `sinks`：**宿主注入的三个落盘出口**（**一个产物一个文件** · ADR-085）；
      **路径由宿主定**（ADR-064 / ADR-074）——部件自己不 `open()`；
    - `observable_systems`：装配出来的可观测系统（**开关关掉时为空**）；
    - `observability_enabled`：可观测总开关的取值（**关掉 = 一个都不装配**）；
    - `degradation_controller`：降级控制器（**没装配就是 `None`** · ADR-031）；
    - `registry`：**系统注册表**（M3 · 没装任何系统时为 `None`）——**宿主层的
      系统表**：装 / 卸 / 重装，**摘得干净**（连命令通道一起撤）。
    """

    kernel: Kernel
    bus: EventBus[Snapshot]
    clock: Clock
    sinks: ObservableSinks = field(default_factory=ObservableSinks)
    observable_systems: tuple[object, ...] = ()
    observability_enabled: bool = True
    degradation_controller: DegradationController[Snapshot] | None = None
    registry: SystemRegistry | None = None


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
    sinks: ObservableSinks | None = None,
    observability_enabled: bool = True,
    degradation_enabled: bool = False,
    systems: Sequence[str] = (),
    system_catalog: Mapping[str, SystemEntry] | None = None,
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
    - `sinks`：**宿主注入的落盘出口**（`None` = 三个产物都不落盘）。**路径由
      宿主定**（M2-D2）——部件自己不 `open()`、不用 `Path.home()`；**三个产物
      各用各的出口**（ADR-085）——共用一个的话，后写的覆盖先写的；
    - `observability_enabled`：**可观测总开关**。关掉时那些工厂**一个都不被
      调用**——`G2-3`（关闭全部可观测系统后内核逐帧一致）的前提；
    - `degradation_enabled`：**是否装配降级控制器**（ADR-031）。**默认不装**——
      M2 是**无头批次**，没有实时宿主去测每帧耗时；**喂帧开销的接线留给 M3
      的实时宿主**（那才是"用户手关"的地方）；
    - `systems`：**要装哪些系统**（M3 · 系统注册表）。**默认一个都不装**——
      装哪些系统是**宿主的决定**（FR-9.1）；**空 = 行为与 M2 一致**；
    - `system_catalog`：**额外的系统目录条目**（名字 → 声明 + 工厂）。省略时
      只有**内建系统**（日志 · 指标 · 录像）；**新系统由自动发现递进来**
      （`host/discovery.py`）。

    > **内建可观测系统要走注册表路径（好让它能被运行时摘下）**，就把
    > `observability_enabled` 关掉——**否则同一个系统会被装两遍**（可观测装配
    > 装一个，注册表再装一个），日志就写两遍了。**两条路各管一段**：可观测
    > 总开关管「这一局要不要观测」，注册表管「运行中能不能摘」。
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
    effective_sinks = ObservableSinks() if sinks is None else sinks
    observable_systems: tuple[object, ...] = ()
    if observability_enabled:
        # **开关关掉时这些工厂一个都不被调用**——不是"装配了再退订"。
        # **局末四项的取法由宿主注入**：棋盘指纹只有内核那一份算法（抄第二份
        # 就是第二个真相），而系统层**不碰内核状态**（M2-D1）——它只拿到一个
        # 可调用对象，拿不到内核。
        outcome_provider = _outcome_provider_of(kernel)
        observable_systems = tuple(
            factory(bus, effective_sinks, outcome_provider)
            for factory in OBSERVABLE_SYSTEM_FACTORIES
        )
    degradation_controller: DegradationController[Snapshot] | None = None
    if degradation_enabled:
        # **降级控制器要一个日志系统**（第 ① 档关 debug 事件 · 第 ② 档抬阈值）
        # ——从装配出来的系统里挑；**没有日志系统时那两档无事可做**，其余照常。
        log_system: LogSystem[Snapshot] | None = None
        for system in observable_systems:
            if isinstance(system, LogSystem):
                log_system = system
                break
        degradation_controller = DegradationController[Snapshot](
            bus=bus,
            log_system=log_system,
        )
    registry: SystemRegistry | None = None
    if systems:
        # **系统注册表**（M3）：**装哪些系统由宿主参数决定**（FR-9.1）。
        # 目录 = 内建系统 + 递进来的额外条目（**新系统走自动发现**）。
        catalog: dict[str, SystemEntry] = dict(
            built_in_system_entries(effective_sinks, _outcome_provider_of(kernel))
        )
        if system_catalog is not None:
            catalog.update(system_catalog)
        registry = SystemRegistry(bus=bus, kernel=kernel, catalog=catalog)
        for name in systems:
            registry.install(name)
    return Assembly(
        kernel=kernel,
        bus=bus,
        clock=VirtualClock(step_ms=step_ms) if clock is None else clock,
        sinks=effective_sinks,
        observable_systems=observable_systems,
        observability_enabled=observability_enabled,
        degradation_controller=degradation_controller,
        registry=registry,
    )


__all__ = [
    "DATA_DIR_NAME",
    "HOME_DIR_NAME",
    "RULE_SETS",
    "Assembly",
    "DataDirectory",
    "ObservableFactory",
    "ObservableSinks",
    "OBSERVABLE_SYSTEM_FACTORIES",
    "Sink",
    "assemble",
    "default_sink",
    "default_sinks",
    "resolve_data_dir",
    "rule_set_of",
]
