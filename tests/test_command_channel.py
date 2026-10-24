"""命令通道测试（S9 · plan-M0.md S9 的五条验证）。

覆盖：

1. **每个命令在每个 phase 下被尝试** → 断言"要么被接受且有 `accepted`，要么被
   拒绝且有 `rejected`"，**不接受"什么都没发生"**；
2. **拒绝不改变任何状态**（版本号 / 棋盘指纹 / 分数都不变）；
3. 拒绝事件的 `args` 与原命令**完全一致**；
4. 非法迁移（未开局就 `pause`）→ 发出 `command:rejected` + 对应的 `game:*`
   **不发生**；
5. **帧内序号**——同帧内连续接受 3 条命令 → 序号 0/1/2；**被拒的那条不占号**。

**自给自足**：不依赖 `tests/conftest.py`（批次 B 的测试必须自给自足——那属于
批次 C 的 C2）。
"""

from __future__ import annotations

import pytest

from tetris_kernel.contracts.commands import COMMAND_TYPES
from tetris_kernel.kernel.commands import Args, Command
from tetris_kernel.kernel.core import Kernel
from tetris_kernel.kernel.drafts import EventDraft
from tetris_kernel.rules.tables import PIECE_TYPES

#: 固定种子（测试不依赖随机性：同种子同序列）。
SEED = 20261024

#: 四个 phase（契约的 `phase` 取值域由内核定义）。
PHASES = ("not_started", "running", "paused", "game_over")

#: 出生区被堵死时用的格子（**所有方块类型的出生格都在这个 4×2 块里**）。
SPAWN_BLOCK_CELLS = (
    (3, 0, 1),
    (4, 0, 1),
    (5, 0, 1),
    (6, 0, 1),
    (3, 1, 1),
    (4, 1, 1),
    (5, 1, 1),
    (6, 1, 1),
)

#: 每条命令的**合法参数**（按契约声明的参数名与顺序）。
VALID_ARGS: dict[str, Args] = {
    "setLevel": (("level", 3),),
    "injectGarbage": (("rows", 1), ("holes", (0,))),
    "writeCells": (("cells", ((0, 5, 1),)),),
}


def _args_for(command_type: str) -> Args:
    """命令的合法参数（无参命令是空元组）。"""
    return VALID_ARGS.get(command_type, ())


def _kernel() -> Kernel:
    """造一个内核（待开始态）。"""
    return Kernel(seed=SEED, piece_types=PIECE_TYPES)


def _kernel_in_phase(phase: str) -> Kernel:
    """造一个处在指定 phase 的内核。

    `game_over` 走的是真实路径：硬降把方块挪到底 → 写死出生区 → `hold` 让
    暂存槽为空时取新方块 → 出生被堵 → 本局结束。
    """
    kernel = _kernel()
    if phase == "not_started":
        return kernel
    kernel.execute(Command("start", source="test"))
    if phase == "running":
        return kernel
    if phase == "paused":
        kernel.execute(Command("pause", source="test"))
        return kernel
    kernel.execute(Command("hardDrop", source="test"))
    kernel.execute(Command("writeCells", args=(("cells", SPAWN_BLOCK_CELLS),), source="test"))
    kernel.execute(Command("hold", source="test"))
    assert kernel.phase == "game_over"
    return kernel


def _types(drafts: tuple[EventDraft, ...]) -> list[str]:
    """草稿序列的事件类型名。"""
    return [draft.eventType for draft in drafts]


def _payload(drafts: tuple[EventDraft, ...], event_type: str) -> dict[str, object]:
    """取某条事件的负载（字段名 → 值）。"""
    for draft in drafts:
        if draft.eventType == event_type:
            return dict(draft.payload)
    raise AssertionError(f"没有 {event_type} 事件：{_types(drafts)}")


# ---------------------------------------------------------------------------
# 验证 1：每个命令在每个 phase 下被尝试 → 要么接受要么拒绝
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("command_type", COMMAND_TYPES)
def test_every_command_in_every_phase_is_accepted_or_rejected(
    phase: str, command_type: str
) -> None:
    """每条命令在每个 phase 下都要有明确的归宿（**不接受"什么都没发生"**）。"""
    kernel = _kernel_in_phase(phase)
    command = Command(command_type, args=_args_for(command_type), source="test")

    drafts = kernel.execute(command)

    types = _types(drafts)
    assert types.count("command:accepted") + types.count("command:rejected") == 1, (
        f"{phase} / {command_type}：必须恰好一条 accepted 或 rejected，实际 {types}"
    )
    assert "command:received" in types, f"{phase} / {command_type}：缺少 command:received"


# ---------------------------------------------------------------------------
# 验证 2：拒绝不改变任何状态
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("command_type", COMMAND_TYPES)
def test_rejection_changes_no_state(phase: str, command_type: str) -> None:
    """被拒的命令**不改任何状态**（版本号 / 棋盘指纹 / 分数都不变）。"""
    kernel = _kernel_in_phase(phase)
    command = Command(command_type, args=_args_for(command_type), source="test")

    before = kernel.snapshot()
    drafts = kernel.execute(command)
    after = kernel.snapshot()

    if "command:rejected" not in _types(drafts):
        pytest.skip(f"{phase} / {command_type} 被接受，不在本验证范围内")

    assert after.version == before.version, "拒绝改了版本号"
    assert after.board.structureFingerprint == before.board.structureFingerprint, "拒绝改了棋盘内容"
    assert after.score == before.score, "拒绝改了分数"
    assert after.lines == before.lines, "拒绝改了消行数"
    assert after.board.cells == before.board.cells, "拒绝改了棋盘格子"
    assert after.active == before.active, "拒绝改了活动方块"
    # **帧号不在比较范围内**：帧号在 tick 出队时就 +1（ADR-022），那不是拒绝
    # 造成的状态变更——被拒的 tick 照样让帧号走（架构 §6.1：暂停期帧号照走）。
    assert after.phase == before.phase, "拒绝改了生命周期状态"


# ---------------------------------------------------------------------------
# 验证 3：拒绝事件的 args 与原命令完全一致
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("command_type", COMMAND_TYPES)
def test_rejected_args_match_the_original_command(phase: str, command_type: str) -> None:
    """拒绝事件带的 `args` 必须与原命令**完全一致**（架构 §7.2）。"""
    kernel = _kernel_in_phase(phase)
    command = Command(command_type, args=_args_for(command_type), source="test")

    drafts = kernel.execute(command)
    if "command:rejected" not in _types(drafts):
        pytest.skip(f"{phase} / {command_type} 被接受，不在本验证范围内")

    payload = _payload(drafts, "command:rejected")
    assert payload["args"] == command.args, "拒绝事件的 args 与原命令不一致"
    received = _payload(drafts, "command:received")
    assert received["args"] == command.args, "received 事件的 args 与原命令不一致"
    assert payload["commandType"] == command_type


# ---------------------------------------------------------------------------
# 验证 4：非法迁移被拒绝，且对应的 game:* 不发生
# ---------------------------------------------------------------------------


def test_illegal_transition_is_rejected_and_emits_no_game_event() -> None:
    """未开局就 `pause` → 发出 `command:rejected`，且 `game:paused` 不发生。"""
    kernel = _kernel()
    assert kernel.phase == "not_started"

    drafts = kernel.execute(Command("pause", source="test"))

    types = _types(drafts)
    assert "command:rejected" in types
    assert "command:accepted" not in types
    assert "game:paused" not in types, "非法迁移仍然发了 game:paused"
    assert kernel.phase == "not_started", "非法迁移改了生命周期状态"

    reason = _payload(drafts, "command:rejected")["reason"]
    assert reason == "illegal_transition"


def test_illegal_transition_of_resume_is_rejected() -> None:
    """运行中 `resume`（暂停才合法）→ 拒绝且 `game:resumed` 不发生。"""
    kernel = _kernel_in_phase("running")
    drafts = kernel.execute(Command("resume", source="test"))

    assert "command:rejected" in _types(drafts)
    assert "game:resumed" not in _types(drafts)
    assert kernel.phase == "running"


def test_legal_transition_emits_the_game_event() -> None:
    """合法迁移照发对应的 `game:*`（反向验证：不是"永远不发"）。"""
    kernel = _kernel()
    drafts = kernel.execute(Command("start", source="test"))

    assert "command:accepted" in _types(drafts)
    assert "game:started" in _types(drafts)
    assert kernel.phase == "running"


# ---------------------------------------------------------------------------
# 验证 5：帧内序号——只对被接受的命令编号，被拒的不占号
# ---------------------------------------------------------------------------


def test_frame_sequence_counts_only_accepted_commands() -> None:
    """同帧内连续接受 3 条命令 → 序号 0/1/2；被拒的那条**不占号**。"""
    kernel = _kernel_in_phase("running")

    tick = kernel.execute(Command("tick", source="test"))
    move = kernel.execute(Command("moveLeft", source="test"))
    rejected = kernel.execute(Command("setLevel", args=(("level", 999),), source="test"))
    rotate = kernel.execute(Command("rotateCW", source="test"))

    assert "command:accepted" in _types(tick)
    assert "command:rejected" in _types(rejected)

    seqs = [_payload(drafts, "command:accepted")["commandSeq"] for drafts in (tick, move, rotate)]
    assert seqs == [0, 1, 2], f"帧内序号不对：{seqs}"

    # 被拒的那条**没有** commandSeq（它不占号）。
    assert "commandSeq" not in _payload(rejected, "command:rejected")


def test_frame_sequence_resets_on_tick() -> None:
    """帧号 +1、帧内序号归零发生在 **tick 出队时**（ADR-022）。"""
    kernel = _kernel_in_phase("running")

    kernel.execute(Command("tick", source="test"))
    kernel.execute(Command("moveLeft", source="test"))
    assert kernel.snapshot().frame == 1

    second = kernel.execute(Command("tick", source="test"))
    assert kernel.snapshot().frame == 2
    assert _payload(second, "command:accepted")["commandSeq"] == 0, "新一帧的序号应归零"

    # tick 的 `received` 与 `accepted` **带同一个帧号**——一条命令不能有两个帧号。
    assert _payload(second, "command:received")["commandType"] == "tick"
    assert all(draft.frame == 2 for draft in second), "同一轮里出现了两个帧号"
