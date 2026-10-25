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
from tetris_kernel.kernel.core import LOCK_DELAY_FRAMES, Kernel
from tetris_kernel.kernel.drafts import EventDraft
from tetris_kernel.rules.tables import GRAVITY_INTERVAL_FRAMES, PIECE_TYPES, SCORE_TABLE

#: 固定种子（测试不依赖随机性）。
SEED = 20261024

#: 棋盘尺寸（与内核默认一致）。
WIDTH = 10
HEIGHT = 22

#: 标准规则集 1 级的重力间隔（**取自规则集的等级表**，不在测试里写死帧数）。
GRAVITY_INTERVAL = GRAVITY_INTERVAL_FRAMES[0]

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


def _advance_until_locked(
    kernel: Kernel, *, limit: int = LOCK_DELAY_FRAMES + 5
) -> list[list[EventDraft]]:
    """推进到**方块真的锁定**为止，返回**锁定那一帧**的每一步事件。

    **M1 起锁定不是即时的**（spec §7.5：30 帧的锁定延迟，**硬降也一样**——
    "方块落地后开始计时"这句话没有例外），M0 是落即锁。所以凡是「推进一帧就
    锁定」的旧写法，都要改成「推进到锁定为止」。

    **只返回锁定那一帧**：锁定之后棋盘还会继续变（消行、塌落、出生），多跑
    几帧就看不出「锁定那一刻」的盘面了。
    """
    for _ in range(limit):
        kernel.execute(Command("tick", source="test"))
        steps = _advance(kernel)
        if any("piece:locked" in _types(tuple(step)) for step in steps):
            return steps
    raise AssertionError(f"{limit} 帧内没有等到锁定事件")


def _advance_until_locked_with_boards(
    kernel: Kernel, *, limit: int = LOCK_DELAY_FRAMES + 5
) -> tuple[list[list[EventDraft]], list[list[list[int]]]]:
    """推进到**方块真的锁定**为止，返回（该帧每步的事件，该帧每一步**当时的盘面**）。

    **盘面必须在帧内取**：`piece:locked` 的订阅者读到的盘面**尚未消行**，而
    帧跑完再读就只剩塌落之后的样子了——"锁定那一刻"的样子就看不到了。

    **每步一盘**：步骤是**逐步产出**的（架构 §6.2），订阅者在**每一步**读到
    的盘面各不相同（锁定步：格子已落、尚未消行；消行步：还没塌落；塌落步：
    已经塌了）。所以这里按步存快照，而不是只存最后一帧的盘面。
    """
    for _ in range(limit):
        kernel.execute(Command("tick", source="test"))
        steps: list[list[EventDraft]] = []
        boards: list[list[list[int]]] = []
        for step in kernel.advance_frame():
            steps.append(list(step))
            boards.append([list(row) for row in kernel.snapshot().board.cells])
        if any("piece:locked" in _types(tuple(step)) for step in steps):
            return steps, boards
    raise AssertionError(f"{limit} 帧内没有等到锁定事件")


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

    steps = _advance_until_locked(kernel)
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

    # 第一块：锁定 → 消 1 行 → 连击变 1。
    first = _advance_until_locked(kernel)
    assert kernel.snapshot().combo == 1
    first_events = [event_type for step in first for event_type in _types(tuple(step))]
    assert "score:combo-changed" in first_events, first_events

    # 下一块硬降到底但不消行 → 落空 → 连击断回 0。
    kernel.execute(Command("hardDrop", source="test"))
    second = _advance_until_locked(kernel)
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


def test_combo_survives_a_frame_without_a_lock() -> None:
    """**没锁定的帧不该断连击**——`score_clear` 只在锁定后调。

    **回归守卫**（2026-10-24 批次 C 审查回合发现）：消行步曾**每帧**都调
    `score_clear`（含空组），而 plan-M0.md S13 段的第三条约定是「每次**放置
    锁定后**都要调 `score_clear`（含空组）」——**落空是"一次放置没有消行"，
    它发生在锁定时**。没锁定的帧里根本没有"落空"这回事，空组一调就等于
    **每帧都断一次连击**，连击与背面连击链活不过一帧（FR-4.6 静默失效）。

    后果还不止连击：没锁定的帧里若棋盘上有满行（`writeCells` 填的），那行
    也会被**无端消掉**。
    """
    kernel = _kernel_ready_to_clear()

    _advance_until_locked(kernel)
    assert kernel.snapshot().combo == 1, "消行后连击没有起来"

    # 下一帧**没有锁定**（新方块还在空中，够不着地板）→ 不是"落空"。
    kernel.execute(Command("tick", source="test"))
    _advance(kernel)

    assert kernel.snapshot().combo == 1, "没锁定的帧把连击断了"


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

    steps, boards = _advance_until_locked_with_boards(kernel)
    lock_index = next(
        index for index, step in enumerate(steps) if "piece:locked" in _types(tuple(step))
    )
    locked_board = boards[lock_index]

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
    _advance_until_locked(kernel)
    after = kernel.snapshot()

    assert after.lines == before.lines + 1, "消行数没跟着涨"
    assert after.score == before.score + SCORE_TABLE["single"], "分数没按规则集算"
    assert after.combo == before.combo + 1, "连击没跟着涨"


# ---------------------------------------------------------------------------
# 出生步：锁定之后才补一块（2026-10-24 批次 C 收口发现的回归）
# ---------------------------------------------------------------------------


def test_spawn_step_does_not_replace_the_active_piece() -> None:
    """出生步**不换掉**活动方块——它只在锁定步清空活动方块之后才补一块。

    **回归守卫**：出生步曾无条件调用 `_spawn_piece()`，把活动方块**换掉**。
    后果：方块每帧被换新（`y` 恒为 0），永远落不到地板 → 永不锁定 → 棋盘
    不填、分数不动、局不结束。**S14 的无头闭环第一次把帧模型整体跑起来，
    才把这处暴露出来**——此前各步都是单步单测，从没串起来跑过。
    """
    kernel = _running_kernel()
    kernel.execute(Command("tick", source="test"))

    drafts = [draft for step in _advance(kernel) for draft in step]

    assert "piece:spawned" not in _types(drafts), "出生步把活动方块换掉了"
    assert kernel.snapshot().active is not None, "活动方块不见了"


def test_gravity_alone_lets_a_piece_fall_and_lock() -> None:
    """只靠重力，方块也能落到地板、被锁定，锁定之后补上新的一块。

    这是上面那条缺陷的**后果级**守卫：缺陷在时方块每帧被换新，
    `piecesPlaced` 恒为 0、棋盘永远不填。

    **落格要等一个间隔**（规则集给的 `gravity_interval`，标准 1 级 = 60 帧），
    所以这里跑的帧数按间隔算，不是按"几帧"算。
    """
    kernel = _running_kernel()
    kernel.execute(Command("tick", source="test"))
    _advance(kernel)
    first = kernel.snapshot()
    assert first.active is not None, "开局没有活动方块"
    start_y = first.active.y

    # 跑满一个间隔：方块应当**往下走一格**（不是每帧被换成新方块）。
    for _ in range(GRAVITY_INTERVAL - 1):
        kernel.execute(Command("tick", source="test"))
        _advance(kernel)
    moved = kernel.snapshot()
    assert moved.active is not None, "活动方块不见了"
    assert moved.active.y == start_y + 1, "方块没有按间隔往下走"

    # 一直跑到落盘为止（每格一个间隔，所以帧数按"格数 × 间隔"给足）。
    for _ in range(GRAVITY_INTERVAL * 25):
        kernel.execute(Command("tick", source="test"))
        _advance(kernel)
        if kernel.snapshot().piecesPlaced > 0:
            break
    final = kernel.snapshot()
    assert final.piecesPlaced == 1, "方块没有落盘"
    assert any(cell != 0 for row in final.board.cells for cell in row), "棋盘上没有一个格子"
    assert final.active is not None, "锁定之后没有补一块新方块"


# ---------------------------------------------------------------------------
# 重力间隔：规则集的 `gravity_interval(ctx)` 由内核兑现（2026-10-24 契约 v1 增补）
# ---------------------------------------------------------------------------


def test_the_gravity_timer_starts_at_the_rule_set_interval() -> None:
    """开局时**重力计时 = 规则集给的间隔**——内核不写死帧数。

    计时记的是「还有几帧才落格」，所以出生时是 `间隔 − 1`（第 `间隔` 帧落
    第一格）。
    """
    kernel = _running_kernel()

    assert kernel.snapshot().gravityTimer == GRAVITY_INTERVAL - 1


def test_a_piece_does_not_fall_every_frame() -> None:
    """间隔没到，**方块不动**——「每帧落一格」的旧行为被换掉的守卫。"""
    kernel = _running_kernel()
    kernel.execute(Command("tick", source="test"))
    _advance(kernel)
    start_y = kernel.snapshot().active.y

    for _ in range(GRAVITY_INTERVAL - 2):
        kernel.execute(Command("tick", source="test"))
        _advance(kernel)

    mid = kernel.snapshot()
    assert mid.active is not None
    assert mid.active.y == start_y, "间隔没到方块就动了"
    assert mid.gravityTimer == 0, "重力计时没有逐帧递减"


def test_the_piece_falls_exactly_one_row_when_the_interval_expires() -> None:
    """间隔到期 → **恰好落一格**（不是落到底），且计时重置。"""
    kernel = _running_kernel()
    kernel.execute(Command("tick", source="test"))
    _advance(kernel)
    start_y = kernel.snapshot().active.y

    for _ in range(GRAVITY_INTERVAL - 1):
        kernel.execute(Command("tick", source="test"))
        _advance(kernel)

    after = kernel.snapshot()
    assert after.active is not None
    assert after.active.y == start_y + 1, "到期后没有恰好落一格"
    assert after.gravityTimer == GRAVITY_INTERVAL - 1, "落格后计时没有重置"


def test_a_grounded_piece_locks_after_the_lock_delay_despite_the_interval() -> None:
    """间隔**不拖慢锁定**：贴地的方块按**锁定延迟**锁定，不等重力间隔。

    **M0 是落即锁**（贴地那一帧就被锁死），**M1 起换成 30 帧的锁定延迟**
    （spec §7.5）——**意图不变**：锁定由"方块贴地"这件事触发，**不被重力
    间隔拖慢**（标准 1 级的重力间隔是 60 帧，比锁定延迟 30 帧还长；锁定要是
    被间隔拖住，这块方块得等 60 帧才锁）。

    **M1 口径的落点**：锁定延迟在**落地那一帧起算**（那一帧**不消耗**计时），
    之后 30 帧到期才锁定 → 从硬降到锁定**相隔 30 帧**（**含落地帧算进去共 31 帧**）。
    """
    kernel = _running_kernel()
    kernel.execute(Command("hardDrop", source="test"))

    frames = 0
    while kernel.snapshot().piecesPlaced == 0:
        kernel.execute(Command("tick", source="test"))
        _advance(kernel)
        frames += 1
        assert frames <= GRAVITY_INTERVAL, "锁定被重力间隔拖慢了（间隔不该拖慢锁定）"

    assert frames == LOCK_DELAY_FRAMES + 1, f"锁定帧数不对：{frames}"
    assert kernel.snapshot().piecesPlaced == 1, "贴地的方块没有锁定"


def test_a_newly_spawned_piece_restarts_the_gravity_timer() -> None:
    """锁定后补的新方块**先等满一个间隔**才落第一格。"""
    kernel = _running_kernel()
    kernel.execute(Command("hardDrop", source="test"))
    _advance_until_locked(kernel)

    snapshot = kernel.snapshot()
    assert snapshot.piecesPlaced == 1
    assert snapshot.active is not None, "锁定之后没有补新方块"
    assert snapshot.gravityTimer == GRAVITY_INTERVAL - 1, "新方块的计时没有随出生重置"


def test_the_interval_follows_the_rule_set_level_table() -> None:
    """间隔查的是**规则集的等级表**——换等级就换间隔（内核不写死 60）。"""
    kernel = _running_kernel()
    kernel.execute(Command("setLevel", args=(("level", 10),), source="test"))
    assert kernel.snapshot().level == 10

    # 间隔变短 → 计时**夹到新间隔**（别让节奏停在旧间隔上）。
    kernel.execute(Command("tick", source="test"))
    _advance(kernel)

    assert kernel.snapshot().gravityTimer == GRAVITY_INTERVAL_FRAMES[9] - 1


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
    _advance_until_locked(kernel)

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

    bottom = HEIGHT - 1
    steps, boards = _advance_until_locked_with_boards(kernel)
    locked_seen = False
    for step, board in zip(steps, boards, strict=True):
        types = _types(tuple(step))
        if "piece:locked" in types:
            locked_seen = True
            row = board[bottom]
            assert all(cell != 0 for cell in row), "锁定时那一行还没满"
        if "board:lines-cleared" in types:
            row = board[bottom]
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
