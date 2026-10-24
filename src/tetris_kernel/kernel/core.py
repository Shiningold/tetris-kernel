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

## 纯度

零第三方依赖、禁读墙钟、禁未注入的随机源（架构 §5.3）；**不 import 总线**
（架构 §7.1）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..contracts.commands import COMMANDS, CommandSpec
from ..contracts.placement import PlacementInfo
from ..contracts.rules import RuleContext, ScoreState
from ..rules.interface import RuleSet
from ..rules.standard import STANDARD_RULESET
from .commands import Command, validate_args
from .drafts import METADATA_CAUSE, METADATA_SOURCE, EventDraft, Payload, make_draft
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

#: 预览队列的**契约说法**是 5 个（`contracts/snapshot.py` 的 `next` 字段说明）。
#:
#: 但内核放的是**当前这一袋剩下的全部方块**（最多 7 个 = 方块种类数）：少放两个
#: 的话，"这一袋还剩哪几个"就只活在内核的私有字段里，存档恢复后取片序列会分叉
#: （FR-10.2：恢复后的局面必须与未中断时一致）。**渲染时取前 5 个当预览**。
PREVIEW_SIZE = 5

#: 等级的取值域（越界即 `level_out_of_range`）。
LEVEL_MIN = 1
LEVEL_MAX = 20

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

#: `tick` 命令名（帧推进的开关）。
COMMAND_TICK = "tick"

#: 内部事件：`(事件类型, 负载字段)`。**由内核在拿到帧内序号之前产出**，
#: 元数据（`source` / `cause`）由内核统一补上。
RawEvent = tuple[str, Payload]


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
    phase: str = PHASE_NOT_STARTED
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
    extensions: tuple[ExtensionEntry, ...] = ()


@dataclass(frozen=True)
class _Outcome:
    """一条命令的执行结果（内部用）。"""

    accepted: bool
    reason: str = ""
    events: tuple[RawEvent, ...] = ()


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
    ) -> None:
        """构造内核（**创建**迁移：初始状态是"待开始"）。

        - `seed`：内核随机源种子（FR-1.4：可重置到初始状态）；
        - `piece_types`：方块类型清单——**由调用方给**：方块名是规则层的词汇，
          内核不抄一份名单（抄了就是两处会悄悄分叉的副本）；
        - `rule_set`：规则集（8 条接口，FR-4.9 可整体替换）；
        - `mode`：运行模式（只随 `kernel:ready` 透传，**内核不按它分支**）。
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
        """
        self._queue.append(command)

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
            return self._reject(command, outcome.reason)

        seq = self._seq
        self._seq += 1
        cause = (self._frame, seq)
        metadata = ((METADATA_SOURCE, command.source), (METADATA_CAUSE, cause))
        return (
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
        if phase == PHASE_PAUSED:
            return "paused"
        if phase == PHASE_GAME_OVER:
            return "game_over"
        return "not_started"

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
        """拒绝一条命令：**不改任何状态**，只发事件。"""
        metadata = ((METADATA_SOURCE, command.source), (METADATA_CAUSE, None))
        return (
            self._received_draft(command),
            make_draft(
                "command:rejected",
                (
                    ("commandType", command.type),
                    ("reason", reason),
                    ("args", command.args),
                    ("mergedCount", 1),
                ),
                self._frame,
                metadata,
            ),
        )

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
        """`reset`：结束当前局并开新局——**帧号 / 帧内序号 / 版本号一并归零**。

        归零后与首次启动完全等价（FR-1.4）：随机源也回到初始种子。
        """
        if not self._lifecycle.apply(TRANSITION_RESET):
            return _Outcome(False, REJECT_ILLEGAL_TRANSITION)
        self._state = KernelState(
            board=[[0] * self._width for _ in range(self._height)],
        )
        self._lifecycle = Lifecycle()
        self._rng = KernelRng(self._seed)
        self._frame = 0
        self._seq = 0
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
        """暂存当前方块并取出暂存槽里的方块。"""
        if self._state.hold_used:
            return _Outcome(False, REJECT_HOLD_ALREADY_USED)
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
        if rows < 0 or rows > visible_rows:
            return _Outcome(False, REJECT_OUT_OF_BOUNDS)
        if any(hole < 0 or hole >= self._width for hole in holes):
            return _Outcome(False, REJECT_OUT_OF_BOUNDS)
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
        if not self._write_allowed(cell_tuple):
            return _Outcome(False, "not_authorized")
        if self._write_condition_failed(cell_tuple):
            return _Outcome(False, "write_condition_failed")

        board = self._state.board
        changes: list[tuple[int, int, int]] = []
        for x, y, value in cell_tuple:
            board[y][x] = value
            changes.append((x, y, value))
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
    # 内部：方块与棋盘
    # ------------------------------------------------------------------

    def _spawn_piece(self) -> list[RawEvent]:
        """出生一个方块（**出生是帧推进的一步，S12 调它**）。"""
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
