"""帧模型测试（S12 · plan-M0.md S12 的六条验证）。

覆盖：

1. **G0-4**——订阅者回调里派发命令 → 该命令产生的事件出现在**下一轮**，不出
   现在当前分发内；
2. **五步顺序固定**——`clock:gravity` → `piece:locked` → `board:lines-cleared`
   → `board:collapsed` → `piece:spawned` 的相对次序；
3. `piece:locked` 的订阅者读到的棋盘**尚未消行**（step 化的意义所在）；
4. 步骤序列被中途 `break` → 装配层仍耗尽（`finally` 生效），且**半帧状态不
   残留**；
5. tick 被接受后 phase 变 `paused` → 本帧**不推进**（推进前复查 phase）；
6. `reset` 后帧号 = 0、帧内序号 = 0、版本号 = 0。

外加三条**行为语义**的守卫（审查回合 B6 · B1 · B5）：消行步**真的**清掉了那一行、
塌落步**真的**把上面的行落下来、计分**真的**按规则集加了分；暂存额度**每块
一次**（锁定后复位）；暂存被拒时发 `piece:hold-denied`。

**自给自足**：不依赖 `tests/conftest.py`。
"""

from __future__ import annotations

from typing import cast

from tetris_kernel.kernel.commands import Args, Command
from tetris_kernel.kernel.core import Kernel
from tetris_kernel.kernel.drafts import EventDraft
from tetris_kernel.rules.tables import PIECE_TYPES, SCORE_TABLE

#: 固定种子（测试不依赖随机性）。
SEED = 20261024

#: 棋盘尺寸（与内核默认一致）。
WIDTH = 10
HEIGHT = 22

#: 五步的固定次序（架构 §6.2 / ADR-023）。
FIVE_STEPS = (
    "clock:gravity",
    "piece:locked",
    "board:lines-cleared",
    "board:collapsed",
    "piece:spawned",
)


def _kernel() -> Kernel:
    """造一个内核（待开始态）。"""
    return Kernel(seed=SEED, piece_types=PIECE_TYPES)


def _running_kernel() -> Kernel:
    """造一个运行态的内核。"""
    kernel = _kernel()
    kernel.execute(Command("start", source="test"))
    return kernel


def _types(drafts: tuple[EventDraft, ...]) -> list[str]:
    """事件类型名序列。"""
    return [draft.eventType for draft in drafts]


def _payload(drafts: tuple[EventDraft, ...], event_type: str) -> dict[str, object]:
    """取某条事件的负载。"""
    for draft in drafts:
        if draft.eventType == event_type:
            return dict(draft.payload)
    raise AssertionError(f"没有 {event_type} 事件：{_types(drafts)}")


def _kernel_ready_to_clear() -> Kernel:
    """造一个"这一帧会消行"的内核。

    做法：开局 → 硬降（方块落到地板）→ 把底行填满（**除了方块自己那几个
    格子**——写它们会被写入条件拒绝）→ 下一帧锁定时正好补满那一行。
    """
    kernel = _running_kernel()
    dropped = kernel.execute(Command("hardDrop", source="test"))
    landing = cast("tuple[tuple[int, int], ...]", _payload(dropped, "piece:harddrop")["cells"])
    occupied = set(landing)
    bottom = HEIGHT - 1
    writes: Args = (
        (
            "cells",
            tuple((x, bottom, 1) for x in range(WIDTH) if (x, bottom) not in occupied),
        ),
    )
    kernel.execute(Command("writeCells", args=writes, source="test"))
    return kernel


def _advance(kernel: Kernel) -> list[list[EventDraft]]:
    """完整消费帧推进的步骤序列（**装配层的正确做法**）。"""
    return [list(step) for step in kernel.advance_frame()]


# ---------------------------------------------------------------------------
# 验证 1：G0-4——命令入队不在本轮生效
# ---------------------------------------------------------------------------


def test_command_dispatched_by_a_subscriber_runs_in_the_next_round() -> None:
    """订阅者回调里派发的命令 → 事件出现在**下一轮**，不出现在当前分发内。"""
    kernel = _running_kernel()
    rounds: list[tuple[EventDraft, ...]] = []

    def publish(drafts: tuple[EventDraft, ...]) -> None:
        rounds.append(drafts)
        if len(rounds) == 1:
            # 分发期间派发命令 → **进队列**，不在本轮生效。
            kernel.submit(Command("moveLeft", source="test"))

    kernel.submit(Command("tick", source="test"))
    kernel.run_rounds(publish)

    assert len(rounds) == 2, "订阅者派发的命令应该触发新一轮"
    first = _types(rounds[0])
    second = _types(rounds[1])
    assert "command:accepted" in first
    assert "piece:moved" not in first, "本轮不该执行订阅者派发的命令"
    assert "piece:moved" in second, "下一轮才执行订阅者派发的命令"


def test_tick_is_the_first_command_of_a_frame() -> None:
    """帧首以 tick 为队首重建队列：tick 恒定是本帧第 1 条命令。"""
    kernel = _running_kernel()
    kernel.submit(Command("moveLeft", source="test"))  # 上帧遗留
    kernel.submit(Command("tick", source="test"))  # 本帧的 tick
    rounds: list[tuple[EventDraft, ...]] = []
    kernel.run_rounds(rounds.append)

    accepted = [draft for draft in rounds[0] if draft.eventType == "command:accepted"]
    assert accepted, "本轮没有接受任何命令"
    assert _payload((accepted[0],), "command:accepted")["commandType"] == "tick"
    assert _payload((accepted[0],), "command:accepted")["commandSeq"] == 0


# ---------------------------------------------------------------------------
# 验证 2：五步顺序固定
# ---------------------------------------------------------------------------


def test_five_steps_have_a_fixed_relative_order() -> None:
    """五步的事件**相对次序**固定。"""
    kernel = _kernel_ready_to_clear()
    kernel.execute(Command("tick", source="test"))

    steps = _advance(kernel)
    assert len(steps) == 5, f"帧推进必须正好五步，实际 {len(steps)}"

    events = [event_type for step in steps for event_type in _types(tuple(step))]
    for event_type in FIVE_STEPS:
        assert event_type in events, f"缺事件：{event_type}（实际 {events}）"
    positions = [events.index(event_type) for event_type in FIVE_STEPS]
    assert positions == sorted(positions), f"五步次序不对：{events}"


def test_a_frame_produces_five_steps_even_without_a_clear() -> None:
    """没有消行时，五步**照样是五步**（步骤序列不随事件数量伸缩）。"""
    kernel = _running_kernel()
    kernel.execute(Command("tick", source="test"))

    steps = _advance(kernel)

    assert len(steps) == 5


# ---------------------------------------------------------------------------
# 计分口径：落空断连击（plan-M0.md S13 段的第三条约定）
# ---------------------------------------------------------------------------


def test_combo_breaks_when_a_placement_clears_nothing() -> None:
    """落空（没消行）把**连击**断掉。

    **内核每次锁定后都调 `score_clear`，含空组**——空组即落空，连击由规则集
    断（**内核不自己改 `combo`**，口径见 `docs/plan-M0.md` S13 段）。
    """
    kernel = _kernel_ready_to_clear()

    # 第一帧：锁定 → 消 1 行 → 连击变 1。
    kernel.execute(Command("tick", source="test"))
    first = _advance(kernel)
    assert kernel.snapshot().combo == 1
    first_events = [event_type for step in first for event_type in _types(tuple(step))]
    assert "score:combo-changed" in first_events, first_events

    # 下一块硬降到底但不消行 → 落空 → 连击断回 0。
    kernel.execute(Command("hardDrop", source="test"))
    kernel.execute(Command("tick", source="test"))
    second = _advance(kernel)
    assert kernel.snapshot().combo == 0
    second_events = [event_type for step in second for event_type in _types(tuple(step))]
    assert "score:combo-changed" in second_events, second_events
    assert "board:lines-cleared" not in second_events, second_events


def test_a_clearless_frame_does_not_change_the_state() -> None:
    """本来就没有连击 / 链时，落空**什么都不改**——不发事件、不落笔。

    架构 §6.2：先算完再一次性写入；**状态没变就不该有"状态变更"事件**。
    """
    kernel = _running_kernel()
    kernel.execute(Command("tick", source="test"))

    steps = _advance(kernel)

    events = [event_type for step in steps for event_type in _types(tuple(step))]
    assert "score:combo-changed" not in events, events
    assert "score:b2b-changed" not in events, events
    assert "board:lines-cleared" not in events, events


# ---------------------------------------------------------------------------
# 行为语义：五步的**实际副作用**（审查回合 B6）
# ---------------------------------------------------------------------------


def test_a_clear_really_clears_the_row_and_collapses_the_board() -> None:
    """消行步**真的**清掉了那一行，塌落步**真的**把上面的行落下来。

    **只守"事件在不在"守不住这条**——把 `step_collapse` 改成不塌落、把
    `step_clear` 改成不清行，事件照发、套件照样全绿。这里断言的是**状态**：
    被消的那一行真的没了，上面的行真的落下来一格。
    """
    kernel = _kernel_ready_to_clear()
    # 标记格：放在被消行**上面**，塌落后它应该往下掉一格。
    kernel.execute(Command("writeCells", args=(("cells", ((0, 5, 1),)),), source="test"))

    kernel.execute(Command("tick", source="test"))
    locked_board: list[list[int]] | None = None
    for step in kernel.advance_frame():
        if "piece:locked" in _types(tuple(step)):
            locked_board = [list(row) for row in kernel.snapshot().board.cells]
    assert locked_board is not None, "这一帧没有锁定事件"

    bottom = HEIGHT - 1
    assert all(cell != 0 for cell in locked_board[bottom]), "锁定时那一行应该已经满了"

    after = [list(row) for row in kernel.snapshot().board.cells]
    # 塌落：被消的那一行消失，上面的行整体落下来一格，顶部补一行空的。
    assert after == [[0] * WIDTH] + locked_board[:-1], "棋盘没有按预期塌落"
    # 标记格跟着落了一格。
    assert after[6][0] == 1, "标记格没有随行下落"
    assert after[5][0] == 0, "标记格没有离开原来的行"
    # 那一行真的被清掉了：满行不再存在，且盘上少掉的正好是那一行的 WIDTH 格。
    assert not any(all(cell != 0 for cell in row) for row in after), "满行还在盘上"

    def filled(board: list[list[int]]) -> int:
        return sum(1 for row in board for cell in row if cell != 0)

    assert filled(locked_board) - filled(after) == WIDTH, "被消行的格子数不对"


def test_scoring_follows_the_rule_set() -> None:
    """消行后**分数与消行数**按规则集算出的值变化。

    期望值取自规则集自己的计分表（`SCORE_TABLE`），不是拍一个数字——规则集
    改分值时这条用例跟着改，而不是变成一个永远对的假断言。
    """
    kernel = _kernel_ready_to_clear()

    before = kernel.snapshot()
    kernel.execute(Command("tick", source="test"))
    _advance(kernel)
    after = kernel.snapshot()

    assert after.lines == before.lines + 1, "消行数没跟着涨"
    assert after.score == before.score + SCORE_TABLE["single"], "分数没按规则集算"
    assert after.combo == before.combo + 1, "连击没跟着涨"


# ---------------------------------------------------------------------------
# 暂存：每块一次（审查回合 B1）+ 被拒时发事件（B5）
# ---------------------------------------------------------------------------


def test_hold_can_be_used_again_after_the_piece_locks() -> None:
    """锁定后**能再暂存**——「每块一次」不是「每局一次」（FR-4.5）。

    锁定就是「本块结束」，下一块重新获得暂存额度。
    """
    kernel = _running_kernel()
    kernel.execute(Command("hold", source="test"))
    assert kernel.snapshot().holdUsed is True, "第一次暂存后额度没被占用"

    kernel.execute(Command("hardDrop", source="test"))
    kernel.execute(Command("tick", source="test"))
    _advance(kernel)

    assert kernel.snapshot().piecesPlaced == 1, "这一帧没有锁定方块"
    assert kernel.snapshot().holdUsed is False, "锁定后暂存额度没有复位"

    drafts = kernel.execute(Command("hold", source="test"))
    assert "command:rejected" not in _types(drafts), "新的一块又被拒了"
    assert "piece:held" in _types(drafts)


def test_hold_denied_is_published_when_hold_is_refused() -> None:
    """暂存被拒时发 `piece:hold-denied`（负载 `reason`）——契约 §8.1 声明了它。"""
    kernel = _running_kernel()
    kernel.execute(Command("hold", source="test"))

    drafts = kernel.execute(Command("hold", source="test"))

    assert "command:rejected" in _types(drafts)
    assert "piece:hold-denied" in _types(drafts), "暂存被拒却没有发 piece:hold-denied"
    assert _payload(drafts, "piece:hold-denied")["reason"] == "hold_already_used"


def test_hold_denied_is_not_published_when_the_command_never_reaches_the_hold_logic() -> None:
    """门禁类拒绝**不发** `piece:hold-denied`——那是"暂存被拒"，不是"没开局"。"""
    kernel = _kernel()
    drafts = kernel.execute(Command("hold", source="test"))

    assert "command:rejected" in _types(drafts)
    assert "piece:hold-denied" not in _types(drafts)


# ---------------------------------------------------------------------------
# 验证 3：piece:locked 时棋盘尚未消行
# ---------------------------------------------------------------------------


def test_board_is_not_cleared_when_piece_locked_is_published() -> None:
    """`piece:locked` 的订阅者读到的棋盘**尚未消行**（step 化的意义所在）。"""
    kernel = _kernel_ready_to_clear()
    kernel.execute(Command("tick", source="test"))

    bottom = HEIGHT - 1
    locked_seen = False
    for step in kernel.advance_frame():
        types = _types(tuple(step))
        if "piece:locked" in types:
            locked_seen = True
            row = kernel.snapshot().board.cells[bottom]
            assert all(cell != 0 for cell in row), "锁定时那一行还没满"
        if "board:lines-cleared" in types:
            row = kernel.snapshot().board.cells[bottom]
            assert all(cell != 0 for cell in row), "消行事件与塌落之间不该提前清掉"
    assert locked_seen, "这一帧没有锁定事件"


# ---------------------------------------------------------------------------
# 验证 4：中途 break 也不能留下半帧状态
# ---------------------------------------------------------------------------


def test_broken_step_sequence_is_drained_by_the_assembly_layer() -> None:
    """步骤序列被中途 `break` → 装配层仍耗尽（`finally`），半帧状态不残留。"""

    def drain(kernel: Kernel) -> list[EventDraft]:
        """装配层的正确做法：`try/finally` 保证步骤序列被耗尽。"""
        collected: list[EventDraft] = []
        steps = kernel.advance_frame()
        try:
            for step in steps:
                collected.extend(step)
                break  # 模拟装配层提前退出
        finally:
            for step in steps:  # 耗尽剩下的步骤
                collected.extend(step)
        return collected

    broken = _kernel_ready_to_clear()
    broken.execute(Command("tick", source="test"))
    broken_events = drain(broken)

    complete = _kernel_ready_to_clear()
    complete.execute(Command("tick", source="test"))
    complete_events = [draft for step in complete.advance_frame() for draft in step]

    assert _types(tuple(broken_events)) == _types(tuple(complete_events)), (
        "提前 break 后耗尽的结果与完整消费不一致"
    )
    assert broken.snapshot() == complete.snapshot(), "半帧状态残留了"


# ---------------------------------------------------------------------------
# 验证 5：推进前复查 phase
# ---------------------------------------------------------------------------


def test_paused_after_tick_stops_the_frame_advance() -> None:
    """tick 被接受后 phase 变 `paused` → 本帧**不推进**。"""
    kernel = _running_kernel()
    kernel.execute(Command("tick", source="test"))  # 被接受 → 授予推进许可
    kernel.execute(Command("pause", source="test"))  # 分发期间有人派发 pause

    steps = _advance(kernel)

    assert steps == [], "phase 不是 running 时本帧不推进"


def test_no_tick_means_no_advance() -> None:
    """没有推进许可（tick 没被接受）→ 本帧不推进。"""
    kernel = _running_kernel()

    steps = _advance(kernel)

    assert steps == []


# ---------------------------------------------------------------------------
# 验证 6：reset 归零帧号、帧内序号与版本号
# ---------------------------------------------------------------------------


def test_reset_zeroes_frame_sequence_and_version() -> None:
    """`reset` 后帧号 = 0、帧内序号 = 0、版本号 = 0。"""
    kernel = _running_kernel()
    kernel.execute(Command("tick", source="test"))
    kernel.execute(Command("moveLeft", source="test"))
    assert kernel.snapshot().frame == 1
    assert kernel.snapshot().version > 0

    kernel.execute(Command("reset", source="test"))

    snapshot = kernel.snapshot()
    assert snapshot.frame == 0, "reset 没归零帧号"
    assert snapshot.version == 0, "reset 没归零版本号"
    assert snapshot.phase == "not_started"

    # 帧内序号归零：下一条被接受的命令拿 seq 0。
    accepted = kernel.execute(Command("start", source="test"))
    assert _payload(accepted, "command:accepted")["commandSeq"] == 0, "帧内序号没归零"


def test_reset_does_not_put_one_command_in_two_frames() -> None:
    """`reset` 的 `received` 与 `accepted` **带同一个帧号**（ADR-022）。"""
    kernel = _running_kernel()
    kernel.execute(Command("tick", source="test"))

    drafts = kernel.execute(Command("reset", source="test"))

    frames = {draft.frame for draft in drafts}
    assert len(frames) == 1, f"一条命令带了两个帧号：{frames}"
