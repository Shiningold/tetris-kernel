"""宿主帧循环（S14 · plan-M0.md §1.4.4.1 ②③ · 架构 §6.1 / §6.2 / §6.6）。

## 一帧的固定三段

1. **派发 `tick`**——本帧的推进开关（ADR-022：帧号推进与推进许可是两件事，
   帧号在 tick **出队**时就 +1，推进许可在 tick **被接受**时才授予）；
2. **命令轮次循环** `kernel.run_rounds(publish)`——内核的 `publish` 吃**一批**
   草稿，总线的 `publish` 吃**一条**，**宿主负责逐条转发**；
3. **帧推进五步** `kernel.advance_frame()`——**生成器必须被耗尽**（B-9）。

**帧末** = 第 2 段结束**且**第 3 段的步骤序列跑完（架构 §6.6）：此时拉一次
快照，**当且仅当 `snapshot.version` 与上一帧末不同**才产出一条 `state:changed`
（**每帧至多一条**——版本号每次变更递增是内部计数器行为，脏标记实际由
`version` 承担）。

## `state:changed` 由宿主发，内核一个字节都不改

「帧末」的边界要等宿主的帧循环才存在，所以这条事件由**宿主**在帧末构造并
`bus.publish`（plan-M0.md §1.4.4.1 ③）。负载 13 个字段**照抄契约声明**，值
全部取自帧末快照，用 `kernel.drafts.make_draft` 构造——**字段名与顺序和契约
逐条相等由它当场校验**（写错当场报错，不等回放校验踩空）。

元数据只带 `cause=None`（与内核 `initial_events()` 同款），`source` 交给总线
的 `default_source`；产出者由总线按注册表填（契约里这条事件的产出者是
`host`——**命名空间 ≠ 产出者，ADR-063**；产出者列的语义是"谁发出这个事件"，
需求 §8.1）。

**`source` 恒为空串是自洽的**：这条事件**没有上游命令**——它是对整帧的观察，
不是某条命令的结果，所以 `cause=None`（ADR-062：「`None` = 无上游命令」），
`source` 也就跟着是空串（总线 `default_source` 的默认值，语义同样是"无上游
命令"）。同帧的 `command:accepted` / `clock:gravity` 有上游（tick 命令），
所以它们带 `source='host'`、`cause=(帧号, 帧内序号)`。**两条路径各有各的
上游，不该被拉平成同一个值**。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from ..kernel.commands import Command
from ..kernel.drafts import METADATA_CAUSE, EventDraft, make_draft
from ..kernel.snapshot import Snapshot
from .assembly import Assembly

#: 帧推进的步骤序列：一批草稿一步。
StepBatches = Iterator[tuple[EventDraft, ...]]


@dataclass(frozen=True)
class RunReport:
    """一次无头运行的统计（**跑完后的快照口径**）。

    - `frames`：宿主跑过的帧数；
    - `events`：总线上发出过的**事件数**（含构造时那两条初始事件）；
    - `fingerprint`：最终棋盘的**结构指纹**（`board.structureFingerprint`）；
    - `score` / `lines` / `piecesPlaced`：最终计分与统计；
    - `phase`：最终的生命周期状态。
    """

    frames: int
    events: int
    fingerprint: str
    score: int
    lines: int
    piecesPlaced: int
    phase: str


class HeadlessLoop:
    """无头帧循环：命令进、事件出（架构 §4.3 · plan-M0.md §1.4.4.1 ②）。

    **不挂任何渲染与输入模块即可运行**（S14 任务清单）：本类只认内核、总线
    与时钟三样。
    """

    def __init__(self, assembly: Assembly, *, source: str = "host") -> None:
        """构造帧循环。

        **构造时把 `kernel.initial_events()`（`kernel:ready` · `game:created`）
        发到总线上**——内核不 import 总线，**发布是宿主的事**（§1.4.4.1 ②）。
        想实时收到它们的订阅者要**先订阅、后构造**；晚订阅的从 `bus.history()`
        里取。

        `source` 是命令的来源标记（只随事件透传，**内核不得因它分支**）。
        """
        self._assembly = assembly
        self._source = source
        self._frames = 0
        # 上一帧末的版本号（构造时的版本 = 0）。**脏标记由 version 承担**
        # （架构 §6.6）——所以初值取构造时的快照版本。
        self._last_version = assembly.kernel.snapshot().version
        for draft in assembly.kernel.initial_events():
            assembly.bus.publish(draft)

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def assembly(self) -> Assembly:
        """装配好的三件套（内核 / 总线 / 时钟）。"""
        return self._assembly

    @property
    def frames(self) -> int:
        """**跑完**的帧数（跑坏了的帧不计）。"""
        return self._frames

    # ------------------------------------------------------------------
    # 开局与帧
    # ------------------------------------------------------------------

    def start(self) -> None:
        """开局：派发 `start` 命令并跑一轮命令循环（**不开帧**）。

        开局本身会改状态（出生第一块方块），但**不开帧**——帧号与推进许可都
        是 tick 给的（ADR-022）。
        """
        kernel = self._assembly.kernel
        kernel.submit(Command(type="start", source=self._source))
        kernel.run_rounds(self._publish_batch)

    def run_frame(self) -> int:
        """跑一帧，返回**本帧发出的事件数**。

        事件数取总线已分配的序号差——**序号由总线分配**（架构 §4.2），所以
        这个数是「本帧真的发出了多少条事件」，不是宿主自己数的。
        """
        bus = self._assembly.bus
        before = bus.last_seq
        self._run_frame_body()
        return bus.last_seq - before

    def run_frames(self, frames: int) -> RunReport:
        """跑满 `frames` 帧，返回运行报告。

        **局结束也不提前退出**：帧号照走，推进许可不再授予（§1.4.4.1 ②）。

        **负帧数直接报错**：负数会让 `range()` 变成空循环，悄悄返回一份"跑了 0
        帧"的报告——那是调用方的 bug，API 层也要挡（CLI 层同样挡，两处口径一致）。
        """
        if frames < 0:
            raise ValueError(f"frames 不能为负，实为 {frames}")
        for _ in range(frames):
            self.run_frame()
        snapshot = self.snapshot()
        return RunReport(
            frames=self._frames,
            events=self._assembly.bus.last_seq,
            fingerprint=snapshot.board.structureFingerprint,
            score=snapshot.score,
            lines=snapshot.lines,
            piecesPlaced=snapshot.piecesPlaced,
            phase=snapshot.phase,
        )

    def snapshot(self) -> Snapshot:
        """当前内核状态的只读快照（**纯读**：不碰随机源、不改任何状态）。"""
        return self._assembly.kernel.snapshot()

    # ------------------------------------------------------------------
    # 内部：一帧的三段
    # ------------------------------------------------------------------

    def _run_frame_body(self) -> None:
        """一帧的固定三段 + 帧末产出（架构 §6.6）。"""
        kernel = self._assembly.kernel

        # 第一段：派发 tick（**帧首以 tick 为队首重建队列**，内核负责）。
        kernel.submit(Command(type="tick", source=self._source))

        # 第二段：命令轮次循环（分发期间新派发的命令在下一轮生效，G0-4）。
        kernel.run_rounds(self._publish_batch)

        # 第三段：帧推进五步（**必须耗尽**）。
        self._advance_frame()

        # 帧末：拉一次快照 → 变了才产出一条 state:changed。
        self._emit_state_changed()
        self._frames += 1

    def _advance_frame(self) -> None:
        """第三段：帧推进五步（**生成器必须被耗尽**，B-9 / 架构 §6.2）。

        中途抛异常时用 `finally` 把剩余步骤**跑完并照发**，原始异常照常向外
        抛——**排空阶段再抛就吞掉**（正在传播的那个才是要报的）。
        """
        steps = self._assembly.kernel.advance_frame()
        try:
            for batch in steps:
                self._publish_batch(batch)
        finally:
            self._drain(steps)

    def _drain(self, steps: StepBatches) -> None:
        """把帧推进的剩余步骤**跑完并照发**（架构 §6.2 · B-9）。

        提前退出（`break`）或中途抛异常时，步骤序列都靠这里耗尽——半帧状态
        （锁定已完成、消行未做）后续全错，而且**静默**。
        """
        try:
            for batch in steps:
                self._publish_batch(batch)
        except Exception:
            # 排空阶段再抛就吞掉——正在传播的那个异常才是要报的。
            pass

    def _publish_batch(self, drafts: tuple[EventDraft, ...]) -> None:
        """把一批草稿**逐条**转交给总线。

        内核的 `publish` 吃一批草稿，总线的 `publish` 吃一条——**宿主负责逐条
        转发**（§1.4.4.1 ②）。
        """
        bus = self._assembly.bus
        for draft in drafts:
            bus.publish(draft)

    def _emit_state_changed(self) -> None:
        """帧末产出 `state:changed`（**每帧至多一条**）。

        **当且仅当 `snapshot.version` 与上一帧末不同**才产出——版本没变就
        一条都不发（架构 §6.6）。
        """
        snapshot = self._assembly.kernel.snapshot()
        if snapshot.version == self._last_version:
            return
        self._last_version = snapshot.version
        self._assembly.bus.publish(_state_changed_draft(snapshot))


def _state_changed_draft(snapshot: Snapshot) -> EventDraft:
    """构造 `state:changed` 草稿（**负载 13 个字段照抄契约声明**）。

    值全部取自帧末快照；`activeType` 是快照 `active` 的**类型投影**（`active`
    为 `None` 时取 `None`）。

    用 `make_draft` 构造——**负载字段名与顺序和契约逐条相等由它当场校验**。
    """
    active = snapshot.active
    return make_draft(
        "state:changed",
        (
            ("version", snapshot.version),
            ("rule", snapshot.rule),
            ("phase", snapshot.phase),
            ("score", snapshot.score),
            ("lines", snapshot.lines),
            ("level", snapshot.level),
            ("combo", snapshot.combo),
            ("b2b", snapshot.b2b),
            ("piecesPlaced", snapshot.piecesPlaced),
            ("activeType", None if active is None else active.pieceType),
            ("hold", snapshot.hold),
            ("holdUsed", snapshot.holdUsed),
            ("next", snapshot.next),
        ),
        snapshot.frame,
        ((METADATA_CAUSE, None),),
    )


__all__ = [
    "HeadlessLoop",
    "RunReport",
    "StepBatches",
]
