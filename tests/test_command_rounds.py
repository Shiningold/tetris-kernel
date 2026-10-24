"""命令轮次测试（S15 · **G0-4** · ADR-039 · 架构 §6.4）。

G0-4 的判据：**命令入队不在本轮生效**——订阅者回调里派发的命令，它产生的事件
出现在**下一轮**，不出现在当前分发内；**同帧多轮顺序稳定**。

## 为什么用真的总线

内核的 `run_rounds(publish)` 收的是**一批草稿**，总线的 `publish` 吃**一条**——
中间那层"逐条转发"就是宿主做的事（plan-M0.md §1.4.4.1 ②）。所以这里照宿主的样子
搭：内核 → 轮次回调 → 逐条转发给总线 → 订阅者回调里 `kernel.submit(...)`。

**派发发生在分发期间**——这正是 G0-4 的要害：分发期间队列被改动，当前这一轮
**不得**执行新命令；当前分发结束后，FIFO 取队开下一轮。

## 同帧多轮

一轮里派发的命令**不开新帧**（帧号由 tick 给，ADR-022）——所以同一帧里可以有多轮，
轮次之间**帧号不变**、**帧内序号继续往下排**。这两条都是"同帧多轮顺序稳定"的
可机检形态。
"""

from __future__ import annotations

from tetris_kernel.bus.bus import EventBus
from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.core import Kernel
from tetris_kernel.kernel.drafts import EventDraft
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.rules.tables import PIECE_TYPES

#: 固定种子（测试不依赖随机性）。
SEED = 20261024


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _kernel() -> Kernel:
    """造一个运行态的内核（开局才有方块可动）。"""
    kernel = Kernel(seed=SEED, piece_types=PIECE_TYPES)
    kernel.execute(Command("start", source="test"))
    return kernel


def _types(drafts: tuple[EventDraft, ...]) -> list[str]:
    """草稿序列的事件类型名。"""
    return [draft.eventType for draft in drafts]


def _accepted_command_type(drafts: tuple[EventDraft, ...]) -> str | None:
    """本轮被接受的命令类型（没有就 `None`）。"""
    for draft in drafts:
        if draft.eventType == "command:accepted":
            return str(dict(draft.payload)["commandType"])
    return None


def _accepted_command_seq(drafts: tuple[EventDraft, ...]) -> int | None:
    """本轮被接受的命令的帧内序号（没有就 `None`）。"""
    for draft in drafts:
        if draft.eventType == "command:accepted":
            return int(dict(draft.payload)["commandSeq"])
    return None


# ---------------------------------------------------------------------------
# G0-4：命令入队不在本轮生效
# ---------------------------------------------------------------------------


def test_command_queued_next_round() -> None:
    """G0-4：订阅者回调里派发的命令 → 事件出现在**下一轮**，不在当前分发内。

    三件事都要成立：

    1. **不在当前轮**：本轮的事件里没有那条命令的 `command:received` /
       `command:accepted` / 语义事件；
    2. **在下一轮**：下一轮里那条命令被接受并产生语义事件；
    3. **同帧**：两轮在**同一帧**（帧号不变，帧内序号继续排）——轮次不是帧。
    """
    kernel = _kernel()
    bus: EventBus[Snapshot] = EventBus[Snapshot](view_provider=kernel.snapshot)
    rounds: list[tuple[EventDraft, ...]] = []
    dispatched: list[str] = []

    def subscriber(envelope: Envelope) -> None:
        """订阅者回调：**分发期间**派发命令。"""
        if envelope.type == "clock:tick" and not dispatched:
            dispatched.append("moveLeft")
            kernel.submit(Command("moveLeft", source="test"))

    bus.subscribe("clock:tick", subscriber, name="dispatcher")

    def publish(drafts: tuple[EventDraft, ...]) -> None:
        """轮次回调：宿主负责**逐条**转交给总线（§1.4.4.1 ②）。"""
        rounds.append(drafts)
        for draft in drafts:
            bus.publish(draft)

    kernel.submit(Command("tick", source="test"))
    rounds_run = kernel.run_rounds(publish)

    assert dispatched == ["moveLeft"], "订阅者没有派发命令"
    assert rounds_run == 2, f"应该跑了两轮，实际 {rounds_run} 轮"
    assert len(rounds) == 2

    first, second = rounds
    # ① 不在当前轮。
    assert _types(first) == ["command:received", "command:accepted", "clock:tick"], _types(first)
    assert "piece:moved" not in _types(first), "本轮执行了订阅者派发的命令"
    assert _accepted_command_type(first) == "tick"
    # ② 在下一轮。
    assert _types(second) == ["command:received", "command:accepted", "piece:moved"], _types(second)
    assert _accepted_command_type(second) == "moveLeft"
    # ③ 同帧：帧号不变，帧内序号继续排。
    assert {draft.frame for draft in first} == {1}
    assert {draft.frame for draft in second} == {1}
    assert _accepted_command_seq(first) == 0
    assert _accepted_command_seq(second) == 1


def test_multiple_rounds_keep_a_stable_order() -> None:
    """同帧多轮：每轮派发一条命令 → 命令**按轮次顺序**执行，顺序稳定。

    轮次之间的先后由"当前分发结束后 FIFO 取队"保证（ADR-039）——这条断言把
    那个顺序钉死：谁先派发谁先执行，不许插队、不许乱序。
    """
    kernel = _kernel()
    bus: EventBus[Snapshot] = EventBus[Snapshot](view_provider=kernel.snapshot)
    rounds: list[tuple[EventDraft, ...]] = []
    dispatched: list[str] = []

    def subscriber(envelope: Envelope) -> None:
        """每轮派发一条命令（最多两条，免得无限派发）。"""
        if envelope.type == "command:accepted" and len(dispatched) < 2:
            command_type = ("moveLeft", "moveRight")[len(dispatched)]
            dispatched.append(command_type)
            kernel.submit(Command(command_type, source="test"))

    bus.subscribe("command:accepted", subscriber, name="dispatcher")

    def publish(drafts: tuple[EventDraft, ...]) -> None:
        rounds.append(drafts)
        for draft in drafts:
            bus.publish(draft)

    kernel.submit(Command("tick", source="test"))
    rounds_run = kernel.run_rounds(publish)

    assert rounds_run == 3, f"应该跑了三轮，实际 {rounds_run} 轮"
    assert [_accepted_command_type(batch) for batch in rounds] == [
        "tick",
        "moveLeft",
        "moveRight",
    ], "同帧多轮的执行顺序不对"
    # 三轮都在同一帧，帧内序号 0 / 1 / 2 连号。
    assert [draft.frame for draft in rounds[0]] == [1, 1, 1]
    assert [draft.frame for draft in rounds[2]] == [1, 1, 1]
    assert [_accepted_command_seq(batch) for batch in rounds] == [0, 1, 2]


def test_a_command_queued_before_the_round_runs_in_that_round() -> None:
    """**轮次开始前**入队的命令在**本轮**执行——G0-4 的边界在"分发期间"。

    没有这条，G0-4 就可能被读成"命令永远推迟一轮"——那会把轮次循环变成空转。
    """
    kernel = _kernel()
    rounds: list[tuple[EventDraft, ...]] = []

    kernel.submit(Command("moveLeft", source="test"))
    kernel.submit(Command("tick", source="test"))
    kernel.run_rounds(rounds.append)

    # tick 插到队首（架构 §6.1），moveLeft 排其后——**同一轮**执行。
    assert len(rounds) == 1, f"应该只有一轮，实际 {len(rounds)} 轮"
    assert _types(rounds[0]) == [
        "command:received",
        "command:accepted",
        "clock:tick",
        "command:received",
        "command:accepted",
        "piece:moved",
    ], _types(rounds[0])
