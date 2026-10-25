"""内核：唯一的状态所有者（S9 · 架构 §7.1 · 需求 FR-2.x）。

## 内核是干什么的

**只接受命令、只发出事件**（架构 §4.3）：外部改不动内核的状态，内核也不认识
总线——它只产出**事件草稿**，草稿由宿主交给总线。

## 命令通道的三段生命周期

```
command:received  →  command:accepted  /  command:rejected
（出队执行前）        （接受：占帧内序号）   （拒绝：不改任何状态）
```

- `received` 在**出队执行前**发，不在入队时发——入队时本帧帧号尚未确定，在
  入队时发会让帧号差一帧，回放对齐整体错位（架构 §6.3）；
- **帧内序号只对被接受的命令编号**（每帧归零，被拒的不占号），**tick 也占号**
  （正常帧里占 0）；
- **拒绝不改任何状态**（FR-2.2）——门禁与参数校验都在副作用之前。

## 统一 phase 门禁（前置一道，不散落）

**门禁归属从 `contracts/commands.py` 推导**（`CommandSpec.lifecycle_gated`），
不手写名单——手写会与契约分叉。门禁三值（`not_started` / `paused` /
`game_over`）由当前状态派生（见 `kernel/lifecycle.py`）。

## 内核状态与快照一一对应

内核持有的状态字段**就是契约 `KERNEL_STATE_TO_SNAPSHOT` 声明的那些**
（架构 §5.2：内核每新增一个状态字段，快照必须同步新增一个字段）。活动方块
的内部附加信息（最后一手是不是旋转、踢墙信息）不进快照——它们是**判定用的
瞬时事实**，随 `PlacementInfo` 递给规则集。

## 活动方块不变量（架构 §6.8）

**命令执行时（帧推进已被完整消费后），`phase == running` 必然持有活动方块。**

依据：开局必出生一块（`_cmd_start`）· 锁定步之后的出生步必然补一块（帧推进
五步的第五步）· 结束本局时 `phase` 同时变成 `game_over`。

唯一能破坏它的是宿主提前 `break` 掉帧推进的步骤序列（架构 §6.2 明确要求
完整消费）——那会留下"锁定已完成、消行未做"的静默半帧。

**不满足时怎么办**：命令**只能用自己声明过的拒绝原因**。六条移动 / 旋转命令
声明了 `no_active_piece`，所以那条路径照常拒绝；**`hold` 没声明**——契约没
描述这个状态，就没有可发的拒绝事件，于是 `_reject` 的守卫把它升级成
`KernelInvariantError`（架构 §6.8）。

## 纯度

零第三方依赖、禁读墙钟、禁未注入的随机源（架构 §5.3）；**不 import 总线**
（架构 §7.1）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace

from ..contracts.commands import COMMANDS, CommandSpec
from ..contracts.placement import PlacementInfo
from ..contracts.rules import RuleContext, ScoreState
from ..contracts.types import Phase
from ..rules.interface import RuleSet
from ..rules.standard import STANDARD_RULESET
from . import frame
from .commands import Command, validate_args
from .drafts import (
    METADATA_CAUSE,
    METADATA_MERGED_COUNT,
    METADATA_SOURCE,
    EventDraft,
    JsonValue,
    Payload,
    make_draft,
)
from .lifecycle import (
    PHASE_GAME_OVER,
    PHASE_NOT_STARTED,
    PHASE_PAUSED,
    PHASE_RUNNING,
    TRANSITION_END,
    TRANSITION_PAUSE,
    TRANSITION_RESET,
    TRANSITION_RESUME,
    TRANSITION_START,
    Lifecycle,
)
from .quota import (
    DEFAULT_LIMITS,
    QUOTA_COMMAND_COUNT,
    QUOTA_ROUNDS,
    QUOTA_WRITE_CELLS,
    FrameQuota,
    QuotaLimits,
    RejectCoalescer,
    RejectGroup,
)
from .rng import KernelRng
from .snapshot import (
    Active,
    ExtensionEntry,
    Snapshot,
    build_board,
    build_extensions,
    build_snapshot,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 标准玩法的重力轴（M0 只有这一个轴；横向重力是 M4 的变体维度）。
GRAVITY_AXIS = "down"

#: 棋盘默认宽（列数）。
DEFAULT_BOARD_WIDTH = 10

#: 棋盘默认总行数（**含隐藏行**）。
DEFAULT_BOARD_HEIGHT = 22

#: 顶部隐藏行数（**数量**，不是可见起始行——契约 §8.3 的注）。
DEFAULT_HIDDEN_ROWS = 2

#: **渲染层取前几个当预览**（契约 `next` 字段的说法是"前 5 个"）。
#:
#: 但内核放的是**当前这一袋剩下的全部方块**（最多 7 个 = 方块种类数）：少放两个
#: 的话，"这一袋还剩哪几个"就只活在内核的私有字段里，存档恢复后取片序列会分叉
#: （FR-10.2：恢复后的局面必须与未中断时一致）。**渲染时取前 5 个当预览**。
PREVIEW_SIZE = 5

#: 等级的取值域（越界即 `level_out_of_range`）。
LEVEL_MIN = 1
LEVEL_MAX = 20

#: "没有类别"的类别键 / T-Spin 标记。
#:
#: **取值域由规则集定义**（契约的 `RULE_SET_DEFINED_FIELDS` 刻意不钉死它——
#: 钉死会把变体挡在门外）。标准规则集用的是 `none`，所以内核带一个自己的
#: 常量并注明出处；换规则集时改这一处。
CLASS_KEY_NONE = "none"

#: 出生横坐标（M0 的最小标准：所有方块同一个出生列）。
SPAWN_X = 3

#: 出生纵坐标（落在隐藏行里——可见区顶部之上）。
SPAWN_Y = 0

#: 旋转方向的取值（`piece:rotated.direction`）。
DIRECTION_CW = "cw"
DIRECTION_CCW = "ccw"
DIRECTION_180 = "180"

#: 契约的 12 值拒绝原因（本模块用到的那些）。
REJECT_INVALID_ARGUMENT = "invalid_argument"
REJECT_LEVEL_OUT_OF_RANGE = "level_out_of_range"
REJECT_OUT_OF_BOUNDS = "out_of_bounds"
REJECT_NO_ACTIVE_PIECE = "no_active_piece"
REJECT_HOLD_ALREADY_USED = "hold_already_used"
REJECT_ILLEGAL_TRANSITION = "illegal_transition"
REJECT_QUOTA_EXCEEDED = "quota_exceeded"
REJECT_NOT_AUTHORIZED = "not_authorized"
REJECT_WRITE_CONDITION_FAILED = "write_condition_failed"

#: `tick` 命令名（帧推进的开关）。
COMMAND_TICK = "tick"

#: `reset` 命令名（归零帧号 / 帧内序号 / 版本号）。
COMMAND_RESET = "reset"

#: 内部事件：`(事件类型, 负载字段)`。**由内核在拿到帧内序号之前产出**，
#: 元数据（`source` / `cause`）由内核统一补上。
RawEvent = tuple[str, Payload]


class KernelInvariantError(RuntimeError):
    """内核不变量被破坏（架构 §6.8 · 活动方块不变量）。

    **什么时候抛**：内核要拒绝一条命令，但**契约没有为这条命令声明可用的
    拒绝原因**——说明内核进入了契约没有描述的状态。典型成因：宿主提前
    `break` 掉帧推进的步骤序列（架构 §6.2 要求完整消费），留下"锁定已完成、
    消行未做"的半帧——此时 `running` 态却没有活动方块。

    **为什么不静默拒绝**：拒绝通道只留给「当前状态根本不该发这个命令」
    （架构 §7.2）。一个契约没描述的状态**没有可发的拒绝事件**——硬造一个
    就是"实现发出了契约未声明的拒绝原因"，那正是契约要防的事。宁可把异常
    抛给宿主，也不让消费方收到一条没声明的原因。
    """


def _is_plain_int(value: object) -> bool:
    """是不是整数。**布尔不算整数**——`True` 进了录像会变成 `1`，回放就漂了。"""
    return isinstance(value, int) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# 内部状态
# ---------------------------------------------------------------------------


@dataclass
class ActivePiece:
    """内核持有的活动方块（**比快照的 `Active` 多几项判定用的瞬时事实**）。

    快照只带 `pieceType` / `rotation` / `x` / `y`（契约 `ACTIVE_FIELDS`）；
    判定 T-Spin 需要的「最后一手是不是旋转」「踢墙信息」是**瞬时事实**，
    它们随 `PlacementInfo` 递给规则集，不进快照。
    """

    pieceType: str
    rotation: int
    x: int
    y: int
    lastMoveWasRotation: bool = False
    kickIndex: int | None = None
    kickOffset: tuple[int, int] | None = None
    lastCause: str = "ground"


@dataclass
class KernelState:
    """内核持有的状态（字段与契约 `KERNEL_STATE_TO_SNAPSHOT` 一一对应）。"""

    version: int = 0
    phase: Phase = PHASE_NOT_STARTED
    board: list[list[int]] = field(default_factory=list[list[int]])
    active: ActivePiece | None = None
    ghost_delta: tuple[int, int] = (0, 0)
    hold: str | None = None
    hold_used: bool = False
    next_queue: tuple[str, ...] = ()
    score: int = 0
    lines: int = 0
    level: int = 1
    combo: int = 0
    b2b: bool = False
    pieces_placed: int = 0
    grounded: bool = False
    lock_timer: int = 0
    gravity_timer: int = 0
    extensions: tuple[ExtensionEntry, ...] = ()


@dataclass(frozen=True)
class _Outcome:
    """一条命令的执行结果（内部用）。"""

    accepted: bool
    reason: str = ""
    events: tuple[RawEvent, ...] = ()
    quota_event: RawEvent | None = None


#: 宿主给的发布回调：把一批草稿交给总线分发（**内核不 import 总线**）。
PublishFn = Callable[[tuple[EventDraft, ...]], None]


# ---------------------------------------------------------------------------
# 内核
# ---------------------------------------------------------------------------


class Kernel:
    """俄罗斯方块内核：**唯一的状态所有者**。

    对外只有三个入口：`execute`（执行一条命令）· `advance_frame`（帧推进五步，
    S12 补上）· `snapshot`（导出只读快照）。
    """

    def __init__(
        self,
        *,
        seed: int,
        piece_types: tuple[str, ...],
        rule_set: RuleSet = STANDARD_RULESET,
        rule_id: str = "standard",
        board_width: int = DEFAULT_BOARD_WIDTH,
        board_height: int = DEFAULT_BOARD_HEIGHT,
        hidden_rows: int = DEFAULT_HIDDEN_ROWS,
        step_ms: int = 16,
        mode: str = "live",
        limits: QuotaLimits = DEFAULT_LIMITS,
    ) -> None:
        """构造内核（**创建**迁移：初始状态是"待开始"）。

        - `seed`：内核随机源种子（FR-1.4：可重置到初始状态）；
        - `piece_types`：方块类型清单——**由调用方给**：方块名是规则层的词汇，
          内核不抄一份名单（抄了就是两处会悄悄分叉的副本）；
        - `rule_set`：规则集（8 条接口，FR-4.9 可整体替换）；
        - `mode`：运行模式（只随 `kernel:ready` 透传，**内核不按它分支**）；
        - `limits`：配额上限（**必须是构造参数**——回放宿主注入大得多的上限，
          否则回放超限会丢命令，违反 B-6）。
        """
        if len(piece_types) < 2:
            raise ValueError(f"方块类型至少要有两种，收到 {len(piece_types)} 种")
        if board_width <= 0 or board_height <= 0:
            raise ValueError("棋盘尺寸必须为正")
        if not 0 <= hidden_rows < board_height:
            raise ValueError("隐藏行数必须落在棋盘内")

        self._seed = seed
        self._piece_types = piece_types
        self._rules = rule_set
        self._rule_id = rule_id
        self._width = board_width
        self._height = board_height
        self._hidden_rows = hidden_rows
        self._step_ms = step_ms
        self._mode = mode

        self._rng = KernelRng(seed)
        self._lifecycle = Lifecycle()
        self._state = KernelState(
            board=[[0] * board_width for _ in range(board_height)],
        )
        self._frame = 0
        self._seq = 0
        self._queue: list[Command] = []
        self._quota = FrameQuota(limits)
        # 单帧写入格子上限的默认值 = 棋盘面积（需求 §8.2 的"默认不超过棋盘面积"）。
        self._quota.with_write_cells_limit(
            limits.max_write_cells
            if limits.max_write_cells is not None
            else board_width * board_height
        )
        self._coalescer = RejectCoalescer()
        self._in_round = False
        # 推进许可：**在 tick 被接受时授予**（ADR-022）——帧号推进是另一件事。
        self._advance_permitted = False
        self._advance_cause: tuple[int, int] | None = None
        self._advance_source = "host"
        # 帧级标志：**这一帧锁定过没有**（消行步据此决定要不要调 `score_clear`）。
        # **运行时状态，不进快照**——它只活在一轮帧推进里。
        self._locked_in_frame = False

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def phase(self) -> str:
        """当前生命周期状态。"""
        return self._state.phase

    @property
    def frame(self) -> int:
        """当前逻辑帧号。"""
        return self._frame

    @property
    def pending(self) -> int:
        """队列里待执行的命令数。"""
        return len(self._queue)

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        """导出只读快照（**纯读**：不碰随机源、不改任何状态）。"""
        board = build_board(self._width, self._height, self._hidden_rows, self._state.board)
        active = self._state.active
        active_view = (
            None
            if active is None
            else Active(
                pieceType=active.pieceType,
                rotation=active.rotation,
                x=active.x,
                y=active.y,
            )
        )
        return build_snapshot(
            version=self._state.version,
            rule=self._rule_id,
            phase=self._state.phase,
            frame=self._frame,
            board=board,
            active=active_view,
            ghost_delta=self._state.ghost_delta,
            hold=self._state.hold,
            hold_used=self._state.hold_used,
            next_queue=self._state.next_queue,
            score=self._state.score,
            lines=self._state.lines,
            level=self._state.level,
            combo=self._state.combo,
            b2b=self._state.b2b,
            pieces_placed=self._state.pieces_placed,
            grounded=self._state.grounded,
            lock_timer=self._state.lock_timer,
            gravity_timer=self._state.gravity_timer,
            extensions=self._state.extensions,
        )

    def initial_events(self) -> tuple[EventDraft, ...]:
        """内核创建时的那两条事件（`kernel:ready` · `game:created`）。

        **与命令无关，所以没有 `source`**——元数据只带 `cause=None`。
        """
        ready = make_draft(
            "kernel:ready",
            (("seed", self._seed), ("rule", self._rule_id), ("mode", self._mode)),
            self._frame,
            ((METADATA_CAUSE, None),),
        )
        created = make_draft(
            "game:created",
            (("seed", self._seed), ("rule", self._rule_id)),
            self._frame,
            ((METADATA_CAUSE, None),),
        )
        return (ready, created)

    # ------------------------------------------------------------------
    # 命令通道
    # ------------------------------------------------------------------

    def submit(self, command: Command) -> None:
        """把命令**入队**（不执行）。

        订阅者在分发期间派发的命令走这里——它们**不在本轮生效**，当前分发
        结束后才按 FIFO 执行（G0-4 / ADR-039）。

        **帧首以 tick 为队首重建队列**（架构 §6.1）：tick 插到队首，上帧遗留
        的命令排在其后——tick 恒定是本帧第 1 条命令。
        """
        if command.type == COMMAND_TICK:
            self._queue.insert(0, command)
        else:
            self._queue.append(command)

    def run_rounds(self, publish: PublishFn) -> int:
        """轮次循环（ADR-039 · G0-4 · 架构 §6.4）。

        每一轮：取队执行（**超限判定在这里之前**）→ 把草稿交给 `publish`
        分发 → 分发期间新派发的命令进队列 → **下一轮**执行。

        `publish` 是宿主给的回调（内核**不 import 总线**）。分发期间订阅者调
        `submit` 派发的命令**不在本轮生效**——这是 G0-4 的结构性保证。

        返回实际跑了几轮。
        """
        rounds = 0
        while self._queue:
            if self._quota.remaining_rounds <= 0:
                # 轮次超限：丢弃剩余命令（**不跨帧顺延**）。
                drafts = self._drop_remaining(QUOTA_ROUNDS)
                if drafts:
                    publish(tuple(drafts))
                break
            batch, drafts = self._take_batch()
            self._in_round = True
            try:
                for command in batch:
                    drafts.extend(self.execute(command))
            finally:
                self._in_round = False
            drafts.extend(self.flush_rejects())
            self._quota.take_round()
            rounds += 1
            publish(tuple(drafts))
        return rounds

    def _take_batch(self) -> tuple[list[Command], list[EventDraft]]:
        """取队执行（**命令数配额在这里裁决**）。

        超限的剩余命令被丢弃并补发 `command:rejected`（`quota_exceeded`），
        **逐条计入合并计数**（不刷屏）。

        **帧首的 tick 先出队**：它出队即开新帧（帧号 +1、帧内序号归零、两个
        配额计数器清零——ADR-022 / ADR-040），所以本帧的命令额度从零开始算。
        `tick` 自身不计入命令数。
        """
        batch: list[Command] = []
        drafts: list[EventDraft] = []
        tick_in_batch = 0
        if self._queue and self._queue[0].type == COMMAND_TICK:
            batch.append(self._queue.pop(0))
            tick_in_batch = 1
            # 提前清零：本帧的额度从零算（tick 执行时还会再清一次，那次是幂等的）。
            self._quota.start_frame()
        remaining = self._quota.remaining_commands
        batch.extend(self._queue[:remaining])
        dropped = self._queue[remaining:]
        self._queue.clear()
        if dropped:
            drafts.extend(
                self._drop_commands(
                    dropped,
                    quota=QUOTA_COMMAND_COUNT,
                    limit=self._quota.limits.max_commands,
                    # **`actual` 是"本帧真实的非 tick 命令数"**：`tick` 自身不计入
                    # 命令数（架构 §6.4），所以要从批里减掉它——不减就恒差 1。
                    actual=self._quota.commands + len(batch) + len(dropped) - tick_in_batch,
                )
            )
        return batch, drafts

    def _drop_remaining(self, quota: str) -> list[EventDraft]:
        """丢弃队列里剩下的全部命令（轮次超限时用）。"""
        dropped = list(self._queue)
        self._queue.clear()
        if not dropped:
            return []
        drafts = self._drop_commands(
            dropped,
            quota=quota,
            limit=self._quota.limits.max_rounds,
            actual=self._quota.rounds + 1,
        )
        # 这一轮到此为止：把被丢弃命令的拒绝事件**冲刷出来**（不能只记不发）。
        drafts.extend(self.flush_rejects())
        return drafts

    def _drop_commands(
        self,
        commands: list[Command],
        *,
        quota: str,
        limit: int,
        actual: int,
    ) -> list[EventDraft]:
        """丢弃一批命令：逐条记入合并计数 + 发一条配额超限事件。

        **两类配额超限统一发 `kernel:quota-exceeded`**（靠负载里的配额名区分），
        `kernel:error` **只留给订阅者异常**（架构 §6.4）。
        """
        for command in commands:
            self._coalescer.record(
                phase=self._state.phase,
                command_type=command.type,
                reason=REJECT_QUOTA_EXCEEDED,
                frame=self._frame,
                source=command.source,
                args=command.args,
            )
        event = make_draft(
            "kernel:quota-exceeded",
            (("quota", quota), ("limit", limit), ("actual", actual)),
            self._frame,
            ((METADATA_CAUSE, None),),
        )
        return [event]

    def execute(self, command: Command) -> tuple[EventDraft, ...]:
        """执行一条命令（**出队执行**这一步）。

        返回这一条命令产生的全部事件草稿：先是 `command:received`，再是
        `command:accepted` 或 `command:rejected`，最后是命令的语义事件。
        """
        if command.type == COMMAND_TICK:
            # 帧号 +1、帧内序号归零在 **tick 出队时**发生（ADR-022）：
            # `received` 在这之后才发，所以一条命令不会带两个帧号。
            self._frame += 1
            self._seq = 0
            # 两个配额计数器**每帧开始清零**（不是按单轮，ADR-040）。
            self._quota.start_frame()
        else:
            # `tick` 自身不计入命令数（那是**配额计数器**；帧内序号是另一套）。
            self._quota.take_command()

        spec = COMMANDS.get(command.type)
        if spec is None:
            # 派发了契约之外的命令名 —— 运行时的取值域校验归内核（契约 §3 的注）。
            return self._reject(command, REJECT_INVALID_ARGUMENT)

        guard = self._guard_reason(spec)
        if guard is not None:
            return self._reject(command, guard)

        reason = validate_args(spec, command.args)
        if reason is not None:
            return self._reject(command, reason)

        outcome = self._dispatch(command)
        if not outcome.accepted:
            drafts = list(self._reject(command, outcome.reason))
            if outcome.quota_event is not None:
                drafts.insert(0, self._draft_from_raw(outcome.quota_event, command.source))
            # **被拒的命令也可以有语义事件**（`piece:hold-denied` 就是）：事件是
            # "这条命令发生了什么"的宣告，与它被不被接受无关。位置在裁决之后，
            # 与接受路径的「received → accepted → 语义事件」同构。
            drafts.extend(self._draft_from_raw(raw, command.source) for raw in outcome.events)
            return tuple(drafts)

        # 接受之前先冲刷待定的合并组：被拒的那组在**时间上更早**。
        flushed = self._group_drafts(self._coalescer.flush())
        seq = self._seq
        self._seq += 1
        cause = (self._frame, seq)
        metadata = ((METADATA_SOURCE, command.source), (METADATA_CAUSE, cause))
        if command.type == COMMAND_TICK:
            # **推进许可在 tick 被接受时授予**（ADR-022）：帧号已经在出队时
            # +1，这里授予的是"本帧可以推进"的许可。
            self._advance_permitted = True
            self._advance_cause = cause
            self._advance_source = command.source
        drafts = [
            *flushed,
            self._received_draft(command),
            make_draft(
                "command:accepted",
                (
                    ("commandType", command.type),
                    ("commandSeq", seq),
                    ("args", command.args),
                ),
                self._frame,
                metadata,
            ),
            *(
                make_draft(event_type, payload, self._frame, metadata)
                for event_type, payload in outcome.events
            ),
        ]
        if command.type == COMMAND_RESET:
            # `reset` 归零帧号、帧内序号与版本号——**在它自己的事件发完之后**：
            # 否则这条命令会带两个帧号（`received` 旧帧、`accepted` 新帧，
            # ADR-022 明确说那是错的）。
            self._frame = 0
            self._seq = 0
            self._state.version = 0
        return tuple(drafts)

    def _draft_from_raw(self, raw: RawEvent, source: str) -> EventDraft:
        """把内部事件（类型 + 负载）变成草稿（元数据补 `source` / `cause`）。"""
        event_type, payload = raw
        return make_draft(
            event_type,
            payload,
            self._frame,
            ((METADATA_SOURCE, source), (METADATA_CAUSE, None)),
        )

    # ------------------------------------------------------------------
    # 内部：门禁与拒绝
    # ------------------------------------------------------------------

    def _guard_reason(self, spec: CommandSpec) -> str | None:
        """统一 phase 门禁（**前置一道**，不散落在各命令里）。

        门禁归属取自契约的 `lifecycle_gated`——**不手写名单**。
        """
        if not spec.lifecycle_gated:
            return None
        phase = self._state.phase
        if phase == PHASE_RUNNING:
            return None
        # 门禁三值**就是三个状态名本身**——契约层已把它们声明成同一份词汇
        # （`contracts/commands.py` 的 `LIFECYCLE_REJECT_REASONS` 由
        # `contracts/types.py` 的 `PHASE_*` 派生）。这里引用常量而不是再抄一遍
        # 字面量：抄第二份，两处分叉时无人报警。
        if phase == PHASE_PAUSED:
            return PHASE_PAUSED
        if phase == PHASE_GAME_OVER:
            return PHASE_GAME_OVER
        return PHASE_NOT_STARTED

    def _received_draft(self, command: Command) -> EventDraft:
        """`command:received`（出队执行前发）。"""
        return make_draft(
            "command:received",
            (
                ("commandType", command.type),
                ("source", command.source),
                ("args", command.args),
            ),
            self._frame,
            ((METADATA_SOURCE, command.source), (METADATA_CAUSE, None)),
        )

    def _reject(self, command: Command, reason: str) -> tuple[EventDraft, ...]:
        """拒绝一条命令：**不改任何状态**，只发事件。

        **同因拒绝合并**（ADR-038）：连续同因的拒绝合并成**一条**事件，计数
        一致（`received` 的计数放在元数据里——契约的 `received` 负载没有这个
        字段）。合并窗口 = **一批命令**：轮次循环里是一轮，单独调用
        `execute` 时是这一条命令（调用结束即冲刷，调用方拿到的永远是完整的
        拒绝事件）。

        **拒绝原因必须在契约声明之内**（架构 §6.8）：守卫见
        `_assert_reason_declared`。
        """
        self._assert_reason_declared(command, reason)
        flushed = self._coalescer.record(
            phase=self._state.phase,
            command_type=command.type,
            reason=reason,
            frame=self._frame,
            source=command.source,
            args=command.args,
        )
        drafts = list(self._group_drafts(flushed))
        if not self._in_round:
            drafts.extend(self._group_drafts(self._coalescer.flush()))
        return tuple(drafts)

    def _assert_reason_declared(self, command: Command, reason: str) -> None:
        """**实现发出的拒绝原因必须在契约声明之内**（架构 §6.8）。

        契约的 `CommandSpec.reject_reasons` 是「该命令可能被拒绝的全部原因」
        ——实现发一个没声明的原因，消费方（回放校验、完备性检查）就会在它上面
        踩空，而 12 值枚举本身看不出破绽：值合法，**归属**不合法。

        所以这里当场拦下：**契约没描述的状态没有可发的拒绝事件**，抛
        `KernelInvariantError`，由宿主去查内核为什么进了那个状态。

        **契约之外的命令名不查**——它没有声明可查，`invalid_argument` 已经在
        全局枚举里。
        """
        spec = COMMANDS.get(command.type)
        if spec is None:
            return
        if reason in spec.reject_reasons:
            return
        raise KernelInvariantError(
            f"{command.type} 发出了契约未声明的拒绝原因 {reason!r}；"
            f"该命令声明的原因是 {list(spec.reject_reasons)}"
        )

    def _group_drafts(self, groups: tuple[RejectGroup, ...]) -> list[EventDraft]:
        """把合并组变成事件草稿（**成对**：`received` + `rejected`，计数一致）。"""
        drafts: list[EventDraft] = []
        for group in groups:
            received_metadata = (
                (METADATA_SOURCE, group.source),
                (METADATA_CAUSE, None),
                (METADATA_MERGED_COUNT, group.count),
            )
            rejected_metadata = ((METADATA_SOURCE, group.source), (METADATA_CAUSE, None))
            drafts.append(
                make_draft(
                    "command:received",
                    (
                        ("commandType", group.commandType),
                        ("source", group.source),
                        ("args", group.args),
                    ),
                    group.frame,
                    received_metadata,
                )
            )
            drafts.append(
                make_draft(
                    "command:rejected",
                    (
                        ("commandType", group.commandType),
                        ("reason", group.reason),
                        ("args", group.args),
                        ("mergedCount", group.count),
                    ),
                    group.frame,
                    rejected_metadata,
                )
            )
        return drafts

    def flush_rejects(self) -> tuple[EventDraft, ...]:
        """冲刷待定的同因拒绝合并组（**宿主的轮次循环结束时调一次**）。

        内核自己的 `run_rounds` 会在每轮结束时冲刷；宿主自己管队列时调它，
        否则合并组会一直攒着（事件不是不发，是**晚点发**）。
        """
        return tuple(self._group_drafts(self._coalescer.flush()))

    # ------------------------------------------------------------------
    # 内部：命令分发
    # ------------------------------------------------------------------

    def _dispatch(self, command: Command) -> _Outcome:
        """按命令类型分派（**状态校验与副作用都在这里**）。"""
        match command.type:
            case "start":
                return self._cmd_start()
            case "pause":
                return self._cmd_pause()
            case "resume":
                return self._cmd_resume()
            case "reset":
                return self._cmd_reset()
            case "moveLeft":
                return self._cmd_move(-1)
            case "moveRight":
                return self._cmd_move(1)
            case "softDrop":
                return self._cmd_soft_drop()
            case "hardDrop":
                return self._cmd_hard_drop()
            case "rotateCW":
                return self._cmd_rotate(1, DIRECTION_CW)
            case "rotateCCW":
                return self._cmd_rotate(-1, DIRECTION_CCW)
            case "rotate180":
                return self._cmd_rotate(2, DIRECTION_180)
            case "hold":
                return self._cmd_hold()
            case "tick":
                return self._cmd_tick()
            case "setLevel":
                return self._cmd_set_level(command)
            case "injectGarbage":
                return self._cmd_inject_garbage(command)
            case "writeCells":
                return self._cmd_write_cells(command)
            case _:
                # 契约之外的命令名：运行时的取值域校验归内核（契约 §3 的注）。
                return _Outcome(False, REJECT_INVALID_ARGUMENT)

    # ------------------------------------------------------------------
    # 内部：生命周期命令
    # ------------------------------------------------------------------

    def _cmd_start(self) -> _Outcome:
        """`start`：待开始 → 运行。"""
        if not self._lifecycle.apply(TRANSITION_START):
            return _Outcome(False, REJECT_ILLEGAL_TRANSITION)
        self._state.phase = PHASE_RUNNING
        events: list[RawEvent] = [
            ("game:started", (("level", self._state.level), ("seed", self._seed)))
        ]
        events.extend(self._spawn_piece())
        self._touch()
        return _Outcome(True, events=tuple(events))

    def _cmd_pause(self) -> _Outcome:
        """`pause`：运行 → 暂停（非法迁移被拒绝，对应的 `game:paused` 不发生）。"""
        if not self._lifecycle.apply(TRANSITION_PAUSE):
            return _Outcome(False, REJECT_ILLEGAL_TRANSITION)
        self._state.phase = PHASE_PAUSED
        self._touch()
        return _Outcome(True, events=(("game:paused", (("phase", self._state.phase),)),))

    def _cmd_resume(self) -> _Outcome:
        """`resume`：暂停 → 运行。"""
        if not self._lifecycle.apply(TRANSITION_RESUME):
            return _Outcome(False, REJECT_ILLEGAL_TRANSITION)
        self._state.phase = PHASE_RUNNING
        self._touch()
        return _Outcome(True, events=(("game:resumed", (("phase", self._state.phase),)),))

    def _cmd_reset(self) -> _Outcome:
        """`reset`：结束当前局并开新局。

        归零后与首次启动完全等价（FR-1.4）：随机源也回到初始种子。**帧号 /
        帧内序号 / 版本号的归零在命令自己的事件发完之后做**（见 `execute`）——
        否则这条命令会带两个帧号。
        """
        if not self._lifecycle.apply(TRANSITION_RESET):
            return _Outcome(False, REJECT_ILLEGAL_TRANSITION)
        self._state = KernelState(
            board=[[0] * self._width for _ in range(self._height)],
        )
        self._lifecycle = Lifecycle()
        self._rng = KernelRng(self._seed)
        return _Outcome(True, events=(("game:reset", (("seed", self._seed),)),))

    # ------------------------------------------------------------------
    # 内部：操作命令
    # ------------------------------------------------------------------

    def _cmd_move(self, dx: int) -> _Outcome:
        """水平移动一格。受阻时命令**仍被接受**，另发受阻事件（ADR-043）。"""
        active = self._state.active
        if active is None:
            return _Outcome(False, REJECT_NO_ACTIVE_PIECE)
        target_x = active.x + dx
        if self._collides(active.pieceType, active.rotation, target_x, active.y):
            return _Outcome(
                True,
                events=(
                    (
                        "piece:moved",
                        (
                            ("fromX", active.x),
                            ("fromY", active.y),
                            ("toX", active.x),
                            ("toY", active.y),
                            ("blocked", True),
                            ("cause", "command"),
                        ),
                    ),
                ),
            )
        active.x = target_x
        active.lastMoveWasRotation = False
        active.lastCause = "command"
        self._state.grounded = False
        self._state.ghost_delta = self._compute_ghost_delta()
        self._touch()
        return _Outcome(
            True,
            events=(
                (
                    "piece:moved",
                    (
                        ("fromX", active.x - dx),
                        ("fromY", active.y),
                        ("toX", active.x),
                        ("toY", active.y),
                        ("blocked", False),
                        ("cause", "command"),
                    ),
                ),
            ),
        )

    def _cmd_soft_drop(self) -> _Outcome:
        """软降一格（给分不落锁）。"""
        active = self._state.active
        if active is None:
            return _Outcome(False, REJECT_NO_ACTIVE_PIECE)
        if self._collides(active.pieceType, active.rotation, active.x, active.y + 1):
            return _Outcome(
                True,
                events=(
                    (
                        "piece:moved",
                        (
                            ("fromX", active.x),
                            ("fromY", active.y),
                            ("toX", active.x),
                            ("toY", active.y),
                            ("blocked", True),
                            ("cause", "softDrop"),
                        ),
                    ),
                ),
            )
        active.y += 1
        active.lastMoveWasRotation = False
        active.lastCause = "softDrop"
        self._state.grounded = False
        self._state.ghost_delta = self._compute_ghost_delta()
        before = self._state.score
        self._state.score = before + 1
        self._touch()
        return _Outcome(
            True,
            events=(
                (
                    "piece:softdrop",
                    (
                        ("pieceType", active.pieceType),
                        ("x", active.x),
                        ("y", active.y),
                    ),
                ),
                (
                    "score:changed",
                    (
                        ("delta", 1),
                        ("before", before),
                        ("after", self._state.score),
                        ("cause", "softDrop"),
                    ),
                ),
            ),
        )

    def _cmd_hard_drop(self) -> _Outcome:
        """硬降：直落到底（落锁在帧推进的锁定步）。"""
        active = self._state.active
        if active is None:
            return _Outcome(False, REJECT_NO_ACTIVE_PIECE)
        distance = 0
        while not self._collides(active.pieceType, active.rotation, active.x, active.y + 1):
            active.y += 1
            distance += 1
        active.lastCause = "hardDrop"
        self._state.grounded = True
        self._state.ghost_delta = self._compute_ghost_delta()
        self._touch()
        return _Outcome(
            True,
            events=(
                (
                    "piece:harddrop",
                    (
                        ("pieceType", active.pieceType),
                        ("distance", distance),
                        ("x", active.x),
                        ("y", active.y),
                        ("cells", self._active_cells(active)),
                    ),
                ),
            ),
        )

    def _cmd_rotate(self, delta: int, direction: str) -> _Outcome:
        """旋转。**无合法落点时命令仍被接受**，另发受阻事件（ADR-043）。"""
        active = self._state.active
        if active is None:
            return _Outcome(False, REJECT_NO_ACTIVE_PIECE)
        from_rotation = active.rotation
        to_rotation = (from_rotation + delta) % 4
        offsets = self._rules.kick_offsets(
            self._rule_context(), active.pieceType, from_rotation, to_rotation
        )
        for index, (ox, oy) in enumerate(offsets):
            target_x = active.x + ox
            target_y = active.y + oy
            if self._collides(active.pieceType, to_rotation, target_x, target_y):
                continue
            active.rotation = to_rotation
            active.x = target_x
            active.y = target_y
            active.lastMoveWasRotation = True
            active.lastCause = "command"
            active.kickIndex = index if (ox, oy) != (0, 0) else None
            active.kickOffset = (ox, oy) if (ox, oy) != (0, 0) else None
            self._state.grounded = False
            self._state.ghost_delta = self._compute_ghost_delta()
            self._touch()
            events: list[RawEvent] = [
                (
                    "piece:rotated",
                    (
                        ("fromRotation", from_rotation),
                        ("toRotation", to_rotation),
                        ("direction", direction),
                        ("blocked", False),
                    ),
                )
            ]
            if active.kickIndex is not None:
                events.append(
                    (
                        "piece:kicked",
                        (
                            ("kickIndex", active.kickIndex),
                            ("offsetX", ox),
                            ("offsetY", oy),
                        ),
                    )
                )
            return _Outcome(True, events=tuple(events))
        # 无合法落点：命令**仍被接受**（ADR-043）。
        return _Outcome(
            True,
            events=(
                (
                    "piece:rotated",
                    (
                        ("fromRotation", from_rotation),
                        ("toRotation", to_rotation),
                        ("direction", direction),
                        ("blocked", True),
                    ),
                ),
            ),
        )

    def _cmd_hold(self) -> _Outcome:
        """暂存当前方块并取出暂存槽里的方块。

        **「本块已用过暂存」时发 `piece:hold-denied`**（负载 `reason`）——
        这是暂存逻辑唯一的拒绝情形，也是契约 §8.1 里 `piece:hold-denied`
        描述的那件事（"暂存被拒"）。**门禁类与参数类的拒绝不进暂存逻辑**，
        所以不会发这条事件。

        **running 态没有活动方块 = 内核不变量破坏**（架构 §6.8）：契约没给
        `hold` 声明 `no_active_piece`，所以这个状态**没有可用的拒绝原因**——
        `_reject` 的守卫会把它升级成 `KernelInvariantError`，而不是硬造一条
        没声明的拒绝事件。
        """
        if self._state.hold_used:
            return _Outcome(
                False,
                REJECT_HOLD_ALREADY_USED,
                events=(("piece:hold-denied", (("reason", REJECT_HOLD_ALREADY_USED),)),),
            )
        active = self._state.active
        if active is None:
            return _Outcome(False, REJECT_NO_ACTIVE_PIECE)
        stored = active.pieceType
        taken = self._state.hold
        self._state.hold = stored
        self._state.hold_used = True
        events: list[RawEvent] = [("piece:held", (("stored", stored), ("taken", taken)))]
        if taken is None:
            # 暂存槽原为空 → 取新方块。
            self._state.active = None
            events.extend(self._spawn_piece())
        else:
            self._state.active = ActivePiece(
                pieceType=taken, rotation=0, x=SPAWN_X, y=SPAWN_Y, lastCause="command"
            )
            if self._collides(taken, 0, SPAWN_X, SPAWN_Y):
                events.append(self._end_game("blocked"))
            else:
                self._state.ghost_delta = self._compute_ghost_delta()
        self._touch()
        return _Outcome(True, events=tuple(events))

    def _cmd_tick(self) -> _Outcome:
        """`tick`：推进一帧的开关。

        **帧号 +1 与推进许可是两件事**（ADR-022）：帧号在出队时就 +1，推进
        许可在**被接受**时才授予——所以 `tick` 被拒（暂停期）时帧号照走，
        但本帧不推进。
        """
        return _Outcome(
            True,
            events=(("clock:tick", (("frame", self._frame), ("stepMs", self._step_ms))),),
        )

    # ------------------------------------------------------------------
    # 内部：参数类命令
    # ------------------------------------------------------------------

    def _cmd_set_level(self, command: Command) -> _Outcome:
        """`setLevel`：设置等级（**不受生命周期门禁约束**）。"""
        level = command.args[0][1]
        if not isinstance(level, int) or isinstance(level, bool):
            return _Outcome(False, REJECT_INVALID_ARGUMENT)
        if not LEVEL_MIN <= level <= LEVEL_MAX:
            return _Outcome(False, REJECT_LEVEL_OUT_OF_RANGE)
        from_level = self._state.level
        self._state.level = level
        interval = self._rules.gravity_interval(self._rule_context())
        self._touch()
        return _Outcome(
            True,
            events=(
                (
                    "progress:level-changed",
                    (
                        ("fromLevel", from_level),
                        ("toLevel", level),
                        ("gravityInterval", interval),
                    ),
                ),
            ),
        )

    def _cmd_inject_garbage(self, command: Command) -> _Outcome:
        """`injectGarbage`：注入垃圾行（改棋盘内容 → 受门禁约束）。"""
        rows_value = command.args[0][1]
        holes_value = command.args[1][1]
        if not isinstance(rows_value, int) or isinstance(rows_value, bool):
            return _Outcome(False, REJECT_INVALID_ARGUMENT)
        if not isinstance(holes_value, tuple):
            return _Outcome(False, REJECT_INVALID_ARGUMENT)
        rows = rows_value
        holes: list[int] = []
        for hole in holes_value:
            if not isinstance(hole, int) or isinstance(hole, bool):
                return _Outcome(False, REJECT_INVALID_ARGUMENT)
            holes.append(hole)
        visible_rows = self._height - self._hidden_rows
        # **参数不合规格统一用 `invalid_argument`**（契约的跨切规则）：行数越界
        # 与空洞列越界都是"参数不合规格"，不是 `out_of_bounds`——那个原因
        # 只声明给 `writeCells`（需求 §8.2 给 injectGarbage 列的情形只有
        # 「未开局」）。
        if rows < 0 or rows > visible_rows:
            return _Outcome(False, REJECT_INVALID_ARGUMENT)
        if any(hole < 0 or hole >= self._width for hole in holes):
            return _Outcome(False, REJECT_INVALID_ARGUMENT)
        hole_tuple = tuple(holes)

        board = self._state.board
        before = [row[:] for row in board]
        # 上移：顶部溢出直接丢（垃圾行把最上面的行顶出去了）。
        for row in range(self._height - rows):
            board[row] = board[row + rows][:]
        # 底部插入垃圾行（空洞列为 0）。
        hole_set = set(hole_tuple)
        for offset in range(rows):
            row = self._height - rows + offset
            board[row] = [0 if x in hole_set else 1 for x in range(self._width)]
        changes: list[tuple[int, int, int]] = []
        for row in range(self._height):
            for x in range(self._width):
                if board[row][x] != before[row][x]:
                    changes.append((x, row, board[row][x]))
        self._touch()
        return _Outcome(
            True,
            events=(
                ("board:garbage-received", (("rows", rows), ("holes", hole_tuple))),
                ("board:changed", (("cellChanges", tuple(changes)), ("cause", "garbage"))),
            ),
        )

    def _cmd_write_cells(self, command: Command) -> _Outcome:
        """`writeCells`：受控写入（外部改变棋盘的**唯一**合法通道）。"""
        cells_value = command.args[0][1]
        if not isinstance(cells_value, tuple):
            return _Outcome(False, REJECT_INVALID_ARGUMENT)
        cells: list[tuple[int, int, int]] = []
        for cell in cells_value:
            if not isinstance(cell, tuple) or len(cell) != 3:
                return _Outcome(False, REJECT_INVALID_ARGUMENT)
            x, y, value = cell
            if not _is_plain_int(x) or not _is_plain_int(y) or not _is_plain_int(value):
                return _Outcome(False, REJECT_INVALID_ARGUMENT)
            if not 0 <= x < self._width or not 0 <= y < self._height:
                return _Outcome(False, REJECT_OUT_OF_BOUNDS)
            cells.append((x, y, value))
        cell_tuple = tuple(cells)
        # 单帧写入格子总数上限：超限**整批拒绝**并发配额超限事件（需求 §8.2）。
        limit = self._quota.limits.max_write_cells
        if limit is not None and self._quota.write_cells + len(cell_tuple) > limit:
            return _Outcome(
                False,
                REJECT_QUOTA_EXCEEDED,
                quota_event=(
                    "kernel:quota-exceeded",
                    (
                        ("quota", QUOTA_WRITE_CELLS),
                        ("limit", limit),
                        ("actual", self._quota.write_cells + len(cell_tuple)),
                    ),
                ),
            )
        if not self._write_allowed(cell_tuple):
            return _Outcome(False, REJECT_NOT_AUTHORIZED)
        if self._write_condition_failed(cell_tuple):
            return _Outcome(False, REJECT_WRITE_CONDITION_FAILED)

        board = self._state.board
        changes: list[tuple[int, int, int]] = []
        for x, y, value in cell_tuple:
            board[y][x] = value
            changes.append((x, y, value))
        self._quota.take_write_cells(len(cell_tuple))
        self._touch()
        return _Outcome(
            True,
            events=(("board:changed", (("cellChanges", tuple(changes)), ("cause", "writeCells"))),),
        )

    def _write_allowed(self, cells: tuple[tuple[int, int, int], ...]) -> bool:
        """受控写入的授权（M0 默认放行；授权粒度可配置是 C 级需求，M1 再做）。"""
        return True

    def _write_condition_failed(self, cells: tuple[tuple[int, int, int], ...]) -> bool:
        """写入条件：不得改到活动方块占据的格子（那属于内核自己的状态）。"""
        active = self._state.active
        if active is None:
            return False
        occupied = set(self._active_cells(active))
        return any((x, y) in occupied for x, y, _value in cells)

    # ------------------------------------------------------------------
    # 帧推进五步（架构 §6.2 · ADR-023）
    # ------------------------------------------------------------------

    def advance_frame(self) -> Iterator[tuple[EventDraft, ...]]:
        """帧推进五步，**逐步产出草稿**。

        **必须完整消费**：装配层提前 `break` 会留下"锁定已完成、消行未做"的
        静默半帧（架构 §6.2）——宿主要用 `try/finally` 把序列耗尽。

        没有推进许可（tick 没被接受）或 phase 不是 running 时**本帧不推进**。
        """
        if not self._advance_permitted:
            return
        # 推进前复查 phase：`clock:tick` 分发期间若有人派发 `pause`，它会在
        # 下一轮被执行——首个步骤前必须复查（架构 §6.2）。
        if self._state.phase != PHASE_RUNNING:
            self._advance_permitted = False
            return
        self._advance_permitted = False
        # 帧级标志归零：每一帧从"没锁定过"开始（锁定的帧由 `step_lock` 置位）。
        self._locked_in_frame = False
        yield from frame.advance_frame(self)

    def _advance_metadata(self) -> tuple[tuple[str, JsonValue], ...]:
        """帧推进事件的元数据（`cause` 指向**批准推进的那条 tick**）。"""
        return (
            (METADATA_SOURCE, self._advance_source),
            (METADATA_CAUSE, self._advance_cause),
        )

    def step_gravity(self, context: frame.FrameContext) -> frame.StepOutcome:
        """第一步：重力。

        **两件事分开**（2026-10-24 契约 v1 增补，兑现规则集的重力间隔）：

        - **落格**由**重力计时**决定：距上次落格满一个间隔（规则集的
          `gravity_interval(ctx)`，标准 1 级 = 60 帧）才真正下移一格；没到期
          就**原地不动**（`dx` / `dy` 记 0）；
        - **触地判定每帧都做**（"再往重力方向走一格会不会撞"）——所以方块
          贴地的那一帧就被判为 `grounded`，紧随其后的锁定步当场锁定（**落即
          锁**，M0 不做锁定延迟）。

        重力受阻时**位移为 0**（事件照发——五步的节奏是固定的，订阅者据此
        知道"这一帧重力没能推动方块"）。
        """
        active = self._state.active
        if active is None:
            return frame.StepOutcome((), context)
        rule_context = self._rule_context()
        dx, dy = self._rules.gravity_vector(rule_context)
        interval = max(1, self._rules.gravity_interval(rule_context))
        timer = self._state.gravity_timer
        moved = False
        if timer == 0:
            # 到期：本帧落一格。
            moved = not self._collides(
                active.pieceType, active.rotation, active.x + dx, active.y + dy
            )
            if moved:
                active.x += dx
                active.y += dy
                active.lastCause = "ground"
                # 下一格在**一个间隔之后**落——本次落格已经消耗掉 1 帧。
                self._state.gravity_timer = interval - 1
        else:
            # 没到期：只走表。间隔变短时（等级下降）把剩余帧数**夹到新间隔**，
            # 别让节奏停在旧间隔上。
            self._state.gravity_timer = min(timer - 1, interval - 1)
        self._state.ghost_delta = self._compute_ghost_delta()
        # 触地判定：**幽灵落点就在脚下**（走 0 格）⟺ 再往重力方向走一格会撞。
        self._state.grounded = self._state.ghost_delta == (0, 0)
        self._touch()
        drafts = (
            make_draft(
                "clock:gravity",
                (
                    ("pieceType", active.pieceType),
                    ("x", active.x),
                    ("y", active.y),
                    ("dx", dx if moved else 0),
                    ("dy", dy if moved else 0),
                ),
                self._frame,
                self._advance_metadata(),
            ),
        )
        return frame.StepOutcome(drafts, context)

    def step_lock(self, context: frame.FrameContext) -> frame.StepOutcome:
        """第二步：锁定。

        **分类用的棋盘含本次放置、尚未消行**（规则集的两条约定之一）——所以
        分类在这里算，消行步只**搬运**不重新判定。
        """
        active = self._state.active
        if active is None or not self._state.grounded:
            return frame.StepOutcome((), context)
        placement = self._placement_info(active)
        cells = self._active_cells(active)
        board = self._state.board
        # 先算完再一次性写入（架构 §6.2）。
        changes = tuple((x, y, 1) for x, y in cells)
        for x, y in cells:
            board[y][x] = 1
        self._state.pieces_placed += 1
        self._state.active = None
        self._state.grounded = False
        self._state.ghost_delta = (0, 0)
        # **本帧锁定了**：消行步据此决定要不要调 `score_clear`（plan-M0.md
        # S13 段的第三条约定：「每次**放置锁定后**都要调 `score_clear`（含空
        # 组）」）。没有这一条，消行步会在**每一个没锁定的帧**也调一次
        # `score_clear`——空组即落空，**连击与背面连击链每帧都被断一次**，
        # FR-4.6 静默失效。
        self._locked_in_frame = True
        # **本块结束 → 暂存额度复位**（FR-4.5「每块一次」）：锁定就是"本块
        # 结束"，下一块重新获得额度。不复位就变成"每局一次"，暂存功能整局
        # 只生效一次（快照的 `holdUsed` 也会与契约写的"本块"分叉）。
        self._state.hold_used = False
        self._touch()
        classification = self._rules.classify_placement(board, placement, self._rule_context())
        cause = "hardDrop" if active.lastCause == "hardDrop" else "ground"
        metadata = self._advance_metadata()
        drafts = (
            make_draft(
                "piece:locked",
                (
                    ("pieceType", active.pieceType),
                    ("rotation", active.rotation),
                    ("x", active.x),
                    ("y", active.y),
                    ("cells", cells),
                    ("tSpin", classification.tSpin),
                    ("cause", cause),
                ),
                self._frame,
                metadata,
            ),
            make_draft(
                "board:changed",
                (("cellChanges", changes), ("cause", "lock")),
                self._frame,
                metadata,
            ),
        )
        return frame.StepOutcome(
            drafts,
            replace(context, placement=placement, classification=classification),
        )

    def step_clear(self, context: frame.FrameContext) -> frame.StepOutcome:
        """第三步：消行。

        **内核只搬运、不重新判定**（架构 §7.3.1）：`scan_clears` 产出的组
        `classification` 恒为 `None`，这里把 `classify_placement` 的结果挂到
        每个组上，再交给 `score_clear`——**落笔由内核做**。

        **每次锁定后都调 `score_clear`，含空组**（`docs/plan-M0.md` S13 段的
        第三条约定）：空组 = **落空** = 连击与背面连击链都断。**内核不自己改
        `combo` / `b2b`**——计分口径归规则集。

        **状态没变就不落笔**（架构 §6.2：先算完再一次性写入）：落空且本来
        就没有连击 / 链时什么都没变，也就不发事件。

        **只在"本帧锁定过"时才调 `score_clear`**（plan-M0.md S13 段的第三条
        约定：「每次**放置锁定后**都要调 `score_clear`（含空组）」）。落空是
        **一次放置没有消行**，它发生在锁定时——没锁定的帧里根本没有"落空"这
        回事，调一次空组等于**每帧都断一次连击**（FR-4.6 静默失效）。
        """
        if not self._locked_in_frame:
            # 本帧没有锁定 → 不是"落空"，**不调 `score_clear`**。
            return frame.StepOutcome((), context)
        self._locked_in_frame = False  # 一帧只算一次
        groups = self._rules.scan_clears(self._state.board, self._rule_context())
        classification = context.classification
        attached = tuple(replace(group, classification=classification) for group in groups)
        delta = self._rules.score_clear(attached, self._rule_context(), self._score_state())

        before_score = self._state.score
        before_lines = self._state.lines
        before_combo = self._state.combo
        before_b2b = self._state.b2b

        new_score = delta.state.score
        new_lines = delta.state.lines
        new_combo = delta.state.combo
        new_b2b = delta.state.b2b
        if (new_score, new_lines, new_combo, new_b2b) == (
            before_score,
            before_lines,
            before_combo,
            before_b2b,
        ):
            # 落空且本来就没有连击 / 链：状态没变，不落笔、不发事件。
            return frame.StepOutcome((), context)

        self._state.score = new_score
        self._state.lines = new_lines
        self._state.combo = new_combo
        self._state.b2b = new_b2b
        self._state.level = delta.state.level
        self._touch()
        rows = tuple(group.index for group in groups)
        metadata = self._advance_metadata()
        drafts: list[EventDraft] = []
        if groups:
            drafts.append(
                make_draft(
                    "board:lines-cleared",
                    (
                        ("rowIndexes", rows),
                        ("count", len(groups)),
                        ("tSpin", classification.tSpin if classification else CLASS_KEY_NONE),
                        (
                            "classKey",
                            classification.classKey if classification else CLASS_KEY_NONE,
                        ),
                    ),
                    self._frame,
                    metadata,
                )
            )
            drafts.append(
                make_draft(
                    "score:changed",
                    (
                        ("delta", delta.scoreDelta),
                        ("before", before_score),
                        ("after", new_score),
                        ("cause", "clear"),
                    ),
                    self._frame,
                    metadata,
                )
            )
        if new_combo != before_combo:
            drafts.append(
                make_draft(
                    "score:combo-changed",
                    (("combo", new_combo),),
                    self._frame,
                    metadata,
                )
            )
        if new_b2b != before_b2b:
            drafts.append(
                make_draft(
                    "score:b2b-changed",
                    (("b2b", new_b2b),),
                    self._frame,
                    metadata,
                )
            )
        return frame.StepOutcome(tuple(drafts), replace(context, cleared_rows=rows))

    def step_collapse(self, context: frame.FrameContext) -> frame.StepOutcome:
        """第四步：塌落（被消的行消失，上面的行落下来）。"""
        rows = context.cleared_rows
        if not rows:
            return frame.StepOutcome((), context)
        board = self._state.board
        before = [row[:] for row in board]
        cleared = set(rows)
        # 先算完再一次性写入（架构 §6.2）。
        kept = [board[row][:] for row in range(self._height) if row not in cleared]
        while len(kept) < self._height:
            kept.insert(0, [0] * self._width)
        board[:] = kept
        changes: list[tuple[int, int, int]] = []
        for row in range(self._height):
            for x in range(self._width):
                if board[row][x] != before[row][x]:
                    changes.append((x, row, board[row][x]))
        self._touch()
        drafts = (
            make_draft(
                "board:collapsed",
                (("cellChanges", tuple(changes)),),
                self._frame,
                self._advance_metadata(),
            ),
        )
        return frame.StepOutcome(drafts, context)

    def step_spawn(self, context: frame.FrameContext) -> frame.StepOutcome:
        """第五步：出生。

        **只在没有活动方块时出生**——上一帧的锁定步刚把方块落盘（活动方块被
        清空），这一步才**补**一块新的。

        **无条件出生是错的**：出生步会把活动方块**换掉**，于是方块每帧被换新
        （`y` 恒为 0），永远落不到地板 → 永不锁定 → 棋盘不填、分数不动、
        局不结束。2026-10-24 批次 C 收口时发现：S14 的无头闭环第一次把帧
        模型整体跑起来，才把这处暴露出来（此前各步都是单步单测，没串过）。
        """
        if self._state.active is not None:
            # 活动方块还在（这一帧没锁定任何东西）→ **不出生**。
            return frame.StepOutcome((), context)
        metadata = self._advance_metadata()
        drafts = tuple(
            make_draft(event_type, payload, self._frame, metadata)
            for event_type, payload in self._spawn_piece()
        )
        return frame.StepOutcome(drafts, context)

    # ------------------------------------------------------------------
    # 内部：方块与棋盘
    # ------------------------------------------------------------------

    def _spawn_piece(self) -> list[RawEvent]:
        """出生一个方块。

        **出生是帧推进的一步（S12 调它）**——但**开局与暂存两条命令路径也直接
        调它**：这两处必须立刻给出一块方块（开局要有方块可动，暂存后要立刻
        换上新方块）。所以 `piece:spawned` / `queue:refilled` / `rng:next`
        **会出现在命令事件流里**，不只在帧推进的出生步里。
        """
        events: list[RawEvent] = []
        piece_type, queue, refilled = self._take_from_queue()
        if refilled:
            events.append(("queue:refilled", (("next", queue),)))
        if self._collides(piece_type, 0, SPAWN_X, SPAWN_Y):
            events.append(self._end_game("blocked"))
            return events
        self._state.active = ActivePiece(
            pieceType=piece_type, rotation=0, x=SPAWN_X, y=SPAWN_Y, lastCause="ground"
        )
        self._state.grounded = False
        # **重力计时随出生重置**：新方块先等满一个间隔才落第一格——落格间隔
        # 是节奏，节奏从这一块开始算，不接着上一块 leftover。计时记的是
        # 「还有几帧才落格」，所以出生时是 `间隔 − 1`（第 `间隔` 帧落第一格）。
        self._state.gravity_timer = max(1, self._rules.gravity_interval(self._rule_context())) - 1
        self._state.ghost_delta = self._compute_ghost_delta()
        events.append(
            (
                "piece:spawned",
                (
                    ("pieceType", piece_type),
                    ("x", SPAWN_X),
                    ("y", SPAWN_Y),
                    ("next", queue),
                ),
            )
        )
        events.extend(self._drain_rng_events())
        return events

    def _end_game(self, reason: str) -> RawEvent:
        """结束本局（顶出 / 溢出）。"""
        self._lifecycle.apply(TRANSITION_END)
        self._state.phase = PHASE_GAME_OVER
        self._state.active = None
        self._touch()
        return (
            "game:over",
            (
                ("score", self._state.score),
                ("lines", self._state.lines),
                ("reason", reason),
            ),
        )

    def _take_from_queue(self) -> tuple[str, tuple[str, ...], bool]:
        """从队列取下一个方块；队列空了就用 7-bag 补一袋（**返回是否补过**）。

        **队列里放的是"当前这一袋剩下的全部方块"**（最多 7 个）：少放的话，
        "这一袋还剩哪几个"就只活在内核的私有字段里，存档恢复后取片序列会分叉
        （FR-10.2）。渲染时取前 5 个当预览（契约 `next` 字段的说法）。
        """
        queue = list(self._state.next_queue)
        refilled = False
        if not queue:
            queue = list(self._rng.next_bag(self._piece_types))
            refilled = True
        piece_type = queue.pop(0)
        self._state.next_queue = tuple(queue)
        return piece_type, self._state.next_queue, refilled

    def _drain_rng_events(self) -> list[RawEvent]:
        """把随机源积压的 `rng:next` 草稿转成事件（**每次取值一条**）。"""
        return [("rng:next", draft.payload.fields()) for draft in self._rng.take_drafts()]

    def _shape_cells(self, piece_type: str, rotation: int) -> tuple[tuple[int, int], ...]:
        """取形状表（**整体由规则集提供**，内核不做变换）。"""
        return self._rules.shape_cells(self._rule_context(), piece_type, rotation)

    def _active_cells(self, active: ActivePiece) -> tuple[tuple[int, int], ...]:
        """活动方块占据的**绝对坐标**。"""
        return tuple(
            (active.x + cx, active.y + cy)
            for cx, cy in self._shape_cells(active.pieceType, active.rotation)
        )

    def _collides(self, piece_type: str, rotation: int, x: int, y: int) -> bool:
        """落点是否与棋盘冲突（越界或压到已有方块）。

        **棋盘之上（y < 0）不算冲突**——那是隐藏行之上的出生区。
        """
        for cx, cy in self._shape_cells(piece_type, rotation):
            ax = x + cx
            ay = y + cy
            if ax < 0 or ax >= self._width:
                return True
            if ay >= self._height:
                return True
            if ay >= 0 and self._state.board[ay][ax] != 0:
                return True
        return False

    def _compute_ghost_delta(self) -> tuple[int, int]:
        """幽灵落点相对活动方块的**位移向量**。"""
        active = self._state.active
        if active is None:
            return (0, 0)
        dx, dy = self._rules.gravity_vector(self._rule_context())
        x = active.x
        y = active.y
        steps = 0
        while not self._collides(active.pieceType, active.rotation, x + dx, y + dy):
            x += dx
            y += dy
            steps += 1
        return (dx * steps, dy * steps)

    def _rule_context(self) -> RuleContext:
        """递给规则集的运行时上下文（**随时间变化的一切都在这里**）。"""
        return RuleContext(
            frame=self._frame,
            level=self._state.level,
            piecesPlaced=self._state.pieces_placed,
            axis=GRAVITY_AXIS,
            stepMs=self._step_ms,
        )

    def _placement_info(self, active: ActivePiece) -> PlacementInfo:
        """本次放置的上下文（**T-Spin 判定的输入**）。"""
        return PlacementInfo(
            pieceType=active.pieceType,
            rotation=active.rotation,
            cells=self._active_cells(active),
            lastMoveWasRotation=active.lastMoveWasRotation,
            kickIndex=active.kickIndex,
            kickOffset=active.kickOffset,
        )

    def _score_state(self) -> ScoreState:
        """当前计分状态（**规则的 `prev` 参数**）。"""
        return ScoreState(
            score=self._state.score,
            lines=self._state.lines,
            combo=self._state.combo,
            b2b=self._state.b2b,
            level=self._state.level,
        )

    def _touch(self) -> None:
        """状态变更：版本号递增（脏标记载体）。"""
        self._state.version += 1

    def _set_extensions(
        self, entries: Iterable[tuple[str, int | float | str | bool | None]]
    ) -> None:
        """宿主合并变体的扩展槽（**内核不理解内容**）。"""
        self._state.extensions = build_extensions(entries)


__all__ = [
    "DEFAULT_BOARD_HEIGHT",
    "DEFAULT_BOARD_WIDTH",
    "DEFAULT_HIDDEN_ROWS",
    "GRAVITY_AXIS",
    "LEVEL_MAX",
    "LEVEL_MIN",
    "PREVIEW_SIZE",
    "ActivePiece",
    "Kernel",
    "KernelState",
]
