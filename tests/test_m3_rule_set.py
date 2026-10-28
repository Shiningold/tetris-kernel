"""运行期换规则集测试（M3-S7 · ADR-061 · G3-5）。

覆盖 plan-M3.md S7 的验证方案：

1. **可切**：局外切换被接受 → 发 `kernel:rule-set-changed` + `game:reset`
   → **换了之后按新规则集跑**（替身规则集的软降得分立刻生效）；
2. **未知被拒**：`unknown_rule_set` · **状态未变**（**不拿当前规则集顶替**）；
3. **局中被拒**：`illegal_transition` · **状态未变**；
4. **参数错被拒**：`invalid_argument`（参数不是字符串）；
5. **重置局面**：棋盘空 · 相位回「待开始」· **扩展槽清空** · 随机源回初始种子；
6. **录像可重放**：换过规则集的录像 → 重放 → **逐帧一致**（四项全等）；
7. **没注入解析器 = 一条都换不了**（内核不知道有哪些规则集）。

**替身规则集**：行为与标准规则集相同、**标识不同**——它只把**软降得分**
换成 7 分（标准是每格 1 分），这样「换了之后按新规则集跑」是**看得见的**。
**替身不进仓、不改 `rules/`**（spec §4.2：真变体属 M4）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from tetris_kernel.contracts.rules import RuleContext
from tetris_kernel.host.assembly import assemble
from tetris_kernel.host.replay import REPLAY_LIMITS, KernelReplayDriver
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.core import Kernel
from tetris_kernel.kernel.drafts import EventDraft
from tetris_kernel.kernel.lifecycle import PHASE_GAME_OVER, PHASE_NOT_STARTED, PHASE_RUNNING
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.rules.interface import (
    ClassifyPlacement,
    GravityInterval,
    GravityVector,
    KickOffsets,
    ScanClears,
    ScoreClear,
    ShapeCells,
    SoftDropScore,
    WrapCell,
)
from tetris_kernel.rules.standard import STANDARD_RULESET
from tetris_kernel.rules.tables import PIECE_TYPES
from tetris_kernel.systems.replay.playback import PlaybackSession
from tetris_kernel.systems.replay.recorder import RecordingSystem

#: 替身规则集的标识（**与标准规则集不同**）。
STAND_IN_ID = "stand-in"

#: 替身规则集的软降得分（标准是每格 1 分）。
STAND_IN_SOFT_DROP_SCORE = 7


def _stand_in_soft_drop_score(ctx: RuleContext, distance: int) -> int:
    """替身规则集的软降得分：**每格 7 分**（看得见的差别）。"""
    return STAND_IN_SOFT_DROP_SCORE * distance


@dataclass(frozen=True)
class StandInRuleSet:
    """替身规则集：**9 条接口照抄标准规则集**，只有软降得分不同。

    **不可变、无状态**（与标准规则集同款纪律）——规则集一旦持有可变状态，
    存档恢复就得额外序列化它。
    """

    wrap_cell: WrapCell = STANDARD_RULESET.wrap_cell
    gravity_vector: GravityVector = STANDARD_RULESET.gravity_vector
    gravity_interval: GravityInterval = STANDARD_RULESET.gravity_interval
    scan_clears: ScanClears = STANDARD_RULESET.scan_clears
    classify_placement: ClassifyPlacement = STANDARD_RULESET.classify_placement
    score_clear: ScoreClear = STANDARD_RULESET.score_clear
    soft_drop_score: SoftDropScore = _stand_in_soft_drop_score
    shape_cells: ShapeCells = STANDARD_RULESET.shape_cells
    kick_offsets: KickOffsets = STANDARD_RULESET.kick_offsets


#: 替身规则集的单例。
STAND_IN_RULESET = StandInRuleSet()

#: 宿主的规则集登记表（**标识 → 规则集**）——内核只拿到它的解析器。
RULE_SETS = {
    "standard": STANDARD_RULESET,
    STAND_IN_ID: STAND_IN_RULESET,
}


def _kernel(*, resolver: object = None, rule_id: str = "standard") -> Kernel:
    """造一个内核（**默认带上替身规则集的登记表**）。"""
    if resolver is None:
        resolver = RULE_SETS.get
    return Kernel(
        seed=20261027,
        piece_types=PIECE_TYPES,
        rule_set=STANDARD_RULESET,
        rule_id=rule_id,
        rule_set_resolver=resolver,  # type: ignore[arg-type]
    )


def _switch(rule: str = STAND_IN_ID) -> Command:
    """一条 `switchRuleSet` 命令（**参数照抄契约**：一个 `rule: str`）。"""
    return Command(type="switchRuleSet", args=(("rule", rule),), source="host")


def _types_of(drafts: tuple[EventDraft, ...]) -> tuple[str, ...]:
    """一批草稿的事件类型（按顺序）。"""
    return tuple(draft.eventType for draft in drafts)


def _payload_of(drafts: tuple[EventDraft, ...], event_type: str) -> tuple:
    """某条事件的负载（**找不到就是空元组**）。"""
    for draft in drafts:
        if draft.eventType == event_type:
            return draft.payload
    return ()


# ---------------------------------------------------------------------------
# 1 · 可切
# ---------------------------------------------------------------------------


def test_the_switch_is_accepted_outside_the_running_phase() -> None:
    """局外切换**被接受** → 发 `kernel:rule-set-changed` + `game:reset`（各一条）。"""
    kernel = _kernel()

    drafts = kernel.execute(_switch())

    assert "command:accepted" in _types_of(drafts)
    assert "command:rejected" not in _types_of(drafts)
    assert _types_of(drafts).count("kernel:rule-set-changed") == 1
    assert _types_of(drafts).count("game:reset") == 1


def test_the_switch_changes_the_rule_set_of_the_snapshot() -> None:
    """切换后快照里的**规则集标识**变成新的那个（**内核换的是"当前这一套"**）。"""
    kernel = _kernel()

    kernel.execute(_switch())

    assert kernel.snapshot().rule == STAND_IN_ID


def test_the_switch_reports_both_rule_set_ids() -> None:
    """`kernel:rule-set-changed` 的负载 = **旧标识 + 新标识**（照抄契约）。"""
    kernel = _kernel()

    drafts = kernel.execute(_switch())

    payload = _payload_of(drafts, "kernel:rule-set-changed")
    assert dict(payload) == {"fromRule": "standard", "toRule": STAND_IN_ID}


def test_the_new_rule_set_is_used_after_the_switch() -> None:
    """**换了之后按新规则集跑**：替身规则集的软降得分立刻生效（7 分 / 格）。"""
    kernel = _kernel()
    kernel.execute(_switch())
    kernel.execute(Command(type="start", source="host"))

    kernel.execute(Command(type="softDrop", source="host"))

    assert kernel.snapshot().score == STAND_IN_SOFT_DROP_SCORE, "换完还在按旧规则集跑"


def _game_over_kernel() -> Kernel:
    """造一个**已结束**的内核（分数非零 · 棋盘有内容）。

    **走存档恢复这条路**：局结束的相位没法用一条命令造出来（要把棋盘堆满
    顶出），而**存档恢复是公开入口**（FR-10.2）——比造一个假状态更接近真实。
    """
    kernel = _kernel()
    archive = kernel.export_state()
    dirty = replace(
        archive.state,
        phase=PHASE_GAME_OVER,
        score=123,
        lines=4,
        pieces_placed=7,
        board=[[1] * archive.width for _ in range(archive.height)],
    )
    kernel.restore_state(replace(archive, state=dirty))
    return kernel


def test_the_switch_resets_the_position() -> None:
    """切换 → **重置局面**：棋盘空 · 相位回「待开始」· 分数归零。"""
    kernel = _game_over_kernel()
    assert kernel.snapshot().score == 123

    kernel.execute(_switch())

    snapshot = kernel.snapshot()
    assert snapshot.phase == PHASE_NOT_STARTED
    assert snapshot.score == 0
    assert snapshot.piecesPlaced == 0
    assert set(cell for row in snapshot.board.cells for cell in row) == {0}, "棋盘没清空"


def test_the_switch_clears_the_extension_slot() -> None:
    """**扩展槽清空**——变体状态与旧规则集绑定，带过去就串味。"""
    kernel = _game_over_kernel()
    # `_set_extensions` 是宿主侧的入口（M4 的变体层用它合并扩展槽）。
    kernel._set_extensions((("gravityAxis", "side"),))  # noqa: SLF001
    assert kernel.snapshot().extensions != ()

    kernel.execute(_switch())

    assert kernel.snapshot().extensions == ()


# ---------------------------------------------------------------------------
# 2 · 未知被拒（明确拒绝，不顶替）
# ---------------------------------------------------------------------------


def test_an_unknown_rule_set_is_rejected() -> None:
    """没登记的规则集 → `unknown_rule_set`（**不拿当前规则集顶替**）。"""
    kernel = _kernel()

    drafts = kernel.execute(_switch("no-such-rule"))

    assert "command:rejected" in _types_of(drafts)
    rejected = next(draft for draft in drafts if draft.eventType == "command:rejected")
    assert dict(rejected.payload)["reason"] == "unknown_rule_set"


def test_an_unknown_rule_set_leaves_the_state_untouched() -> None:
    """被拒 → **状态一个字都没变**（规则集标识与相位都不变）。"""
    kernel = _kernel()
    kernel.execute(Command(type="start", source="host"))
    before = kernel.snapshot()

    kernel.execute(_switch("no-such-rule"))

    after = kernel.snapshot()
    assert after.rule == before.rule
    assert after.phase == before.phase


def test_a_kernel_without_a_resolver_cannot_switch_at_all() -> None:
    """**没注入解析器 = 一条都换不了**（内核不知道有哪些规则集）。"""
    kernel = _kernel(resolver=None)
    # `None` 表示"不注入解析器"——这里显式造一个不带解析器的内核。
    kernel = Kernel(
        seed=20261027,
        piece_types=PIECE_TYPES,
        rule_set=STANDARD_RULESET,
        rule_id="standard",
    )

    drafts = kernel.execute(_switch())

    rejected = next(draft for draft in drafts if draft.eventType == "command:rejected")
    assert dict(rejected.payload)["reason"] == "unknown_rule_set"


# ---------------------------------------------------------------------------
# 3 · 局中被拒
# ---------------------------------------------------------------------------


def test_switching_while_running_is_rejected() -> None:
    """局中切换 → `illegal_transition`（半局换规则 → 录像重放静默分叉）。"""
    kernel = _kernel()
    kernel.execute(Command(type="start", source="host"))
    assert kernel.phase == PHASE_RUNNING

    drafts = kernel.execute(_switch())

    rejected = next(draft for draft in drafts if draft.eventType == "command:rejected")
    assert dict(rejected.payload)["reason"] == "illegal_transition"


def test_switching_while_running_leaves_the_state_untouched() -> None:
    """局中被拒 → **状态未变**（规则集没换，局面也没动）。"""
    kernel = _kernel()
    kernel.execute(Command(type="start", source="host"))
    before = kernel.snapshot()

    kernel.execute(_switch())

    after = kernel.snapshot()
    assert after.rule == before.rule
    assert after.phase == before.phase
    assert after.score == before.score


# ---------------------------------------------------------------------------
# 4 · 参数错被拒
# ---------------------------------------------------------------------------


def test_a_non_string_argument_is_rejected() -> None:
    """参数不是字符串 → `invalid_argument`（**参数校验在命令通道里做**）。"""
    kernel = _kernel()

    drafts = kernel.execute(Command(type="switchRuleSet", args=(("rule", 42),), source="host"))

    rejected = next(draft for draft in drafts if draft.eventType == "command:rejected")
    assert dict(rejected.payload)["reason"] == "invalid_argument"


# ---------------------------------------------------------------------------
# 5 · 录像可重放（逐帧一致）
# ---------------------------------------------------------------------------


def _four_items(snapshot: Snapshot) -> tuple[str, int, int, int]:
    """快照 → 四项（棋盘指纹 · 分数 · 行数 · 方块数）。"""
    return (
        snapshot.board.structureFingerprint,
        snapshot.score,
        snapshot.lines,
        snapshot.piecesPlaced,
    )


def test_a_switched_recording_replays_frame_by_frame() -> None:
    """换过规则集的录像 → 重放 → **逐帧一致**（四项全等）。

    **流程**：录制（开局前切换 → 按新规则集跑一局）→ 重放 → 四项相等。
    **重放要带同一张规则集登记表**——否则录像里的那条切换命令会被
    `unknown_rule_set` 拒绝（**明确拒绝，不顶替**）。
    """
    assembly = assemble(seed=20261027, rule_sets=RULE_SETS)
    recorder = _recorder_of(assembly)
    bus = assembly.bus
    kernel = assembly.kernel

    # **开局前切换**（局中切换会被拒）——这一条进录像（命令是被接受的）。
    kernel.submit(_switch())
    kernel.run_rounds(lambda drafts: tuple(bus.publish(draft) for draft in drafts))

    loop = _loop_of(assembly)
    loop.start()
    for _ in range(30):
        kernel.submit(Command(type="softDrop", source="host"))
        loop.run_frame()

    original = _four_items(kernel.snapshot())
    recorder.set_outcome(original)
    recording = recorder.recording()

    replayed = assemble(
        seed=recording.seed,
        rule_set=STANDARD_RULESET,
        rule_id=recording.rule,
        mode="replay",
        limits=REPLAY_LIMITS,
        observability_enabled=False,
        rule_sets=RULE_SETS,
    )
    driver = KernelReplayDriver(replayed.kernel, replayed.bus)
    session = PlaybackSession(replayed.bus, driver, recording)
    items = session.run()

    assert items.as_dict() == {
        "fingerprint": original[0],
        "score": original[1],
        "lines": original[2],
        "piecesPlaced": original[3],
    }


def _recorder_of(assembly: object) -> RecordingSystem[Snapshot]:
    """从装配结果里挑出录制系统。"""
    systems = [
        system
        for system in assembly.observable_systems  # type: ignore[attr-defined]
        if isinstance(system, RecordingSystem)
    ]
    assert systems, "录像系统没装上"
    return systems[0]


def _loop_of(assembly: object) -> object:
    """造一个无头帧循环。"""
    from tetris_kernel.host.loop import HeadlessLoop

    return HeadlessLoop(assembly)  # type: ignore[arg-type]


def test_the_game_over_phase_allows_switching() -> None:
    """局结束后**可以换**（`game_over` 态不是 `running`）。"""
    kernel = _game_over_kernel()

    drafts = kernel.execute(_switch())

    assert "command:accepted" in _types_of(drafts)
    assert kernel.snapshot().rule == STAND_IN_ID


def test_the_phase_constants_are_the_ones_the_kernel_uses() -> None:
    """相位常量与内核用的是同一套（**别抄第二份字面量**）。"""
    assert PHASE_NOT_STARTED == "not_started"
    assert PHASE_RUNNING == "running"
    assert PHASE_GAME_OVER == "game_over"
