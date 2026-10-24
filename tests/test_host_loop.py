"""宿主装配与无头闭环测试（S14 · plan-M0.md S14 的五条验证方案）。

覆盖：

1. **G0-1**：无头跑满 **10000 帧**——帧数 / 事件数 / 棋盘指纹 / 统计齐全，
   **中途无异常、无未消费步骤**；
2. **帧末才产出**一条 `state:changed`（**每帧至多一条**，版本没变就不发），
   负载 13 个字段照抄契约；
3. **装配层不提前 break**：中途抛异常时步骤序列仍被耗尽（`finally`），剩余步骤
   的事件**照发**，原始异常照常向外抛；
4. 构造 `HeadlessLoop` 时把 `kernel:ready` / `game:created` 发到总线上；
5. `sim` 子命令跑出统计（人读形态与 JSON 形态）。

## 一处**内核侧**的缺陷：已发现、已修（2026-10-24 批次 C 收口）

`kernel/core.py` 的 `step_spawn` 曾**无条件**调用 `_spawn_piece()`——出生步把活动
方块换掉，而不是「锁定步之后**补**一块」。后果：**只靠重力下落**时方块每帧都被
换新（`y` 恒为 0），永远落不到地板 → 不会被锁定 → `piecesPlaced` 恒为 0、
棋盘永远不填、局永远不会结束。

**本文件是第一次把帧模型整体跑起来的地方**（S14 的无头闭环），缺陷因此暴露——
此前各步都是单步单测，从没串起来跑过。缺陷归属内核（批次 B 的文件），宿主层
不碰（B-2 红线）：由**集成者**在批次 C 收口时修正 `kernel/core.py` 的
`step_spawn`，回归守卫落在 `tests/test_frame_model.py`（两条：出生步不换掉活动
方块 · 只靠重力也能落盘锁定）。

**自给自足**：不依赖 `tests/conftest.py`（那是 C2 的文件）。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from tetris_kernel.bus.envelope import Draft
from tetris_kernel.host.assembly import assemble
from tetris_kernel.host.cli import main
from tetris_kernel.host.clock import DEFAULT_STEP_MS, VirtualClock
from tetris_kernel.host.loop import HeadlessLoop, RunReport
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.drafts import EventDraft
from tetris_kernel.kernel.frame import FrameContext, StepOutcome
from tetris_kernel.kernel.lifecycle import PHASE_RUNNING, PHASES
from tetris_kernel.kernel.snapshot import Snapshot

#: 固定种子（测试不依赖随机性）。
SEED = 20261024

#: 长跑的帧数。
#:
#: **G0-1 的门判据用例在 `tests/test_kernel_lifecycle.py`**（plan-M0.md §5.2
#: 指定的归属文件）——那条跑满 10000 帧。本文件是 S14 的**实现侧**长跑，帧数
#: 取 2000：两条都跑 10000 帧会让这两个用例占掉整套的一半时间
#: （实测 4.3s + 3.9s），而实现侧要验的东西（帧数对得上、队列清空、指纹一致、
#: 局真的在打）2000 帧全都验得到。
FRAMES_LONG = 2000

#: `state:changed` 的负载字段（**照抄契约声明**，13 个）。
STATE_CHANGED_FIELDS = (
    "version",
    "rule",
    "phase",
    "score",
    "lines",
    "level",
    "combo",
    "b2b",
    "piecesPlaced",
    "activeType",
    "hold",
    "holdUsed",
    "next",
)

#: 五步的固定次序（架构 §6.2 / ADR-023）。
FIVE_STEPS = ("gravity", "lock", "clear", "collapse", "spawn")


def _recorder(
    name: str, original: Callable[[FrameContext], StepOutcome], executed: list[str]
) -> Callable[[FrameContext], StepOutcome]:
    """给内核的某一步套一层记录（**记录它跑过了**）。"""

    def step(context: FrameContext) -> StepOutcome:
        executed.append(name)
        return original(context)

    return step


# ---------------------------------------------------------------------------
# 验证 1：G0-1 无头跑满 10000 帧
# ---------------------------------------------------------------------------


def test_headless_loop_runs_a_long_burst() -> None:
    """长跑一段：中途无异常，统计齐全，**局真的在打**。

    **G0-1 的门判据用例在 `tests/test_kernel_lifecycle.py`**（§5.2 的归属），
    那条跑满 10000 帧；本条是 S14 的实现侧长跑。
    """
    assembly = assemble(seed=SEED)
    loop = HeadlessLoop(assembly)
    loop.start()

    report = loop.run_frames(FRAMES_LONG)

    assert isinstance(report, RunReport)
    assert report.frames == FRAMES_LONG, "帧数不对"
    assert report.events > 0, "一条事件都没发出去"
    assert report.fingerprint == loop.snapshot().board.structureFingerprint, "指纹不是帧末快照的"
    assert report.score >= 0 and report.lines >= 0 and report.piecesPlaced >= 0
    assert report.phase in PHASES, report.phase
    assert assembly.kernel.pending == 0, "命令队列没清空"
    # 帧号照走：一帧一个 tick，帧号与宿主帧数对得上（架构 §6.1）。
    assert assembly.kernel.frame == FRAMES_LONG, "帧号与宿主帧数对不上"
    # **闭环真的在打一局**：方块落下、被锁定（出生步不换掉活动方块——
    # 那个缺陷在时这一项恒为 0）。
    assert report.piecesPlaced > 0, "一块方块都没放——帧模型没有真正推进"


def test_the_game_really_progresses_and_the_frames_keep_going() -> None:
    """局结束也不提前退出：帧号照走，推进许可不再授予（§1.4.4.1 ②）。

    **`hardDrop` 驱动**（落即锁）——用命令通道把方块直接摁到地板上，逼出
    「顶出 → 局结束」这条路径（只靠重力时落得慢得多）。
    """
    assembly = assemble(seed=SEED)
    loop = HeadlessLoop(assembly)
    loop.start()
    initial = loop.snapshot().board.structureFingerprint

    for _ in range(40):
        assembly.kernel.submit(Command(type="hardDrop", source="test"))
        loop.run_frame()
        if assembly.kernel.phase != PHASE_RUNNING:
            break

    snapshot = loop.snapshot()
    assert snapshot.phase == "game_over", "方块放满之后局该结束"
    assert snapshot.piecesPlaced > 0, "一块方块都没放"
    assert snapshot.board.structureFingerprint != initial, "棋盘内容没变过"

    # 局结束后帧号照走：run_frames 跑满，帧数照加。
    before = assembly.kernel.frame
    report = loop.run_frames(5)
    assert report.frames == before + 5, "局结束也不该提前退出"
    assert assembly.kernel.frame == before + 5
    assert report.phase == "game_over"


def test_frames_leave_no_half_frame_state() -> None:
    """帧推进被**完整消费**：running 态必然持有活动方块（架构 §6.8）。

    **宿主提前 break 会破这条不变量**——半帧状态（锁定已完成、消行未做）后续
    全错，而且静默。
    """
    assembly = assemble(seed=SEED)
    loop = HeadlessLoop(assembly)
    loop.start()
    for _ in range(120):
        loop.run_frame()
        snapshot = loop.snapshot()
        if snapshot.phase == PHASE_RUNNING:
            assert snapshot.active is not None, "半帧状态残留：running 态没有活动方块"


def test_run_frames_advances_exactly_the_requested_number_of_frames() -> None:
    """`run_frames(n)` 跑满 n 帧：宿主帧数与内核帧号都对得上。"""
    assembly = assemble(seed=SEED)
    loop = HeadlessLoop(assembly)
    loop.start()
    report = loop.run_frames(200)
    assert report.frames == 200
    assert assembly.kernel.frame == 200, "帧号与宿主帧数对不上"


# ---------------------------------------------------------------------------
# 验证 2：帧末才产出一条 state:changed
# ---------------------------------------------------------------------------


def test_state_changed_is_emitted_once_per_frame_at_frame_end() -> None:
    """帧末才产出 `state:changed`：**每帧至多一条**，版本没变就不发。"""
    assembly = assemble(seed=SEED)
    published: list[tuple[int, str]] = []
    assembly.bus.subscribe("*", lambda envelope: published.append((envelope.frame, envelope.type)))

    loop = HeadlessLoop(assembly)
    loop.start()

    expected = 0
    previous = loop.snapshot().version
    for _ in range(60):
        loop.run_frame()
        current = loop.snapshot().version
        if current != previous:
            expected += 1
        previous = current

    changed = [item for item in published if item[1] == "state:changed"]
    assert len(changed) == expected, "state:changed 的条数与版本变更次数对不上"

    frames = [frame for frame, _ in changed]
    assert len(set(frames)) == len(frames), "同一帧产出了多条 state:changed"

    for index, (frame, event_type) in enumerate(published):
        if event_type != "state:changed":
            continue
        later = [other for other, _ in published[index + 1 :]]
        assert all(other != frame for other in later), "state:changed 不是帧末的最后一条事件"


def test_state_changed_is_not_emitted_when_the_version_did_not_change() -> None:
    """版本没变就不发（脏标记由 `version` 承担，架构 §6.6）。"""
    assembly = assemble(seed=SEED)
    published: list[str] = []
    assembly.bus.subscribe("state:changed", lambda envelope: published.append(envelope.type))

    def publish(drafts: tuple[EventDraft, ...]) -> None:
        for draft in drafts:
            assembly.bus.publish(draft)

    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frame()  # 先跑一帧，立住「上一帧末」的版本
    published.clear()

    # 暂停：版本在**帧外**变一次（phase 变了），下一帧末会补发一条 state:changed
    # ——这是对的（状态确实变了）。
    assembly.kernel.submit(Command(type="pause", source="test"))
    assembly.kernel.run_rounds(publish)
    loop.run_frame()
    assert len(published) == 1, "phase 变了却没发 state:changed"
    published.clear()

    # 此后每一帧的 tick 都被拒 → 状态不变 → 版本不变 → 一条都不发。
    for _ in range(10):
        loop.run_frame()

    assert published == [], "版本没变却发了 state:changed"


def test_state_changed_payload_matches_the_contract() -> None:
    """负载 13 个字段**照抄契约声明**（字段名与顺序）。"""
    assembly = assemble(seed=SEED)
    payloads: list[tuple[tuple[str, object], ...]] = []
    assembly.bus.subscribe("state:changed", lambda envelope: payloads.append(envelope.payload))

    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frame()

    assert payloads, "这一帧没有 state:changed"
    names = tuple(name for name, _ in payloads[0])
    assert names == STATE_CHANGED_FIELDS


# ---------------------------------------------------------------------------
# 验证 3：装配层不提前 break（finally 耗尽步骤序列）
# ---------------------------------------------------------------------------


def test_advance_frame_is_drained_when_publishing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """中途抛异常 → 剩余步骤**仍被耗尽**且事件**照发**，原始异常照常向外抛。

    **两行高的棋盘**：开局那块方块一出生就触底，所以第一帧的锁定步有事件可
    发——「照发」这条才验得到。
    """
    assembly = assemble(seed=SEED, board_height=2, hidden_rows=0)
    kernel = assembly.kernel
    executed: list[str] = []
    published: list[str] = []

    monkeypatch.setattr(kernel, "step_gravity", _recorder("gravity", kernel.step_gravity, executed))
    monkeypatch.setattr(kernel, "step_lock", _recorder("lock", kernel.step_lock, executed))
    monkeypatch.setattr(kernel, "step_clear", _recorder("clear", kernel.step_clear, executed))
    monkeypatch.setattr(
        kernel, "step_collapse", _recorder("collapse", kernel.step_collapse, executed)
    )
    monkeypatch.setattr(kernel, "step_spawn", _recorder("spawn", kernel.step_spawn, executed))

    original_publish = assembly.bus.publish

    def publish(draft: Draft) -> None:
        if draft.eventType == "clock:gravity":
            raise RuntimeError("发布炸了")
        published.append(draft.eventType)
        original_publish(draft)

    monkeypatch.setattr(assembly.bus, "publish", publish)

    loop = HeadlessLoop(assembly)
    loop.start()

    with pytest.raises(RuntimeError, match="发布炸了"):
        loop.run_frame()

    assert executed == list(FIVE_STEPS), "剩余步骤没有被耗尽——半帧状态残留了"
    assert "piece:locked" in published, "排空阶段的剩余事件没有照发"


def test_advance_frame_is_fully_consumed_without_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常路径：五步全跑完，宿主**不提前 break**。"""
    assembly = assemble(seed=SEED)
    kernel = assembly.kernel
    executed: list[str] = []

    monkeypatch.setattr(kernel, "step_gravity", _recorder("gravity", kernel.step_gravity, executed))
    monkeypatch.setattr(kernel, "step_lock", _recorder("lock", kernel.step_lock, executed))
    monkeypatch.setattr(kernel, "step_clear", _recorder("clear", kernel.step_clear, executed))
    monkeypatch.setattr(
        kernel, "step_collapse", _recorder("collapse", kernel.step_collapse, executed)
    )
    monkeypatch.setattr(kernel, "step_spawn", _recorder("spawn", kernel.step_spawn, executed))

    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frame()

    assert executed == list(FIVE_STEPS)


# ---------------------------------------------------------------------------
# 验证 4：构造时把内核的初始事件发到总线上
# ---------------------------------------------------------------------------


def test_initial_events_are_published_on_construction() -> None:
    """构造 `HeadlessLoop` 时把 `kernel:ready` · `game:created` 发到总线上。"""
    assembly = assemble(seed=SEED)
    assert assembly.bus.last_seq == 0, "装配层不该自己发事件"

    HeadlessLoop(assembly)

    types = [envelope.type for envelope in assembly.bus.history()]
    assert types == ["kernel:ready", "game:created"]
    assert assembly.bus.last_seq == 2


def test_assemble_defaults_to_a_virtual_clock() -> None:
    """省略时钟时用 `VirtualClock`——无头批次不读墙钟。"""
    assembly = assemble(seed=SEED)
    assert isinstance(assembly.clock, VirtualClock)
    assert assembly.clock.step_ms() == DEFAULT_STEP_MS


def test_bus_view_is_the_kernel_snapshot() -> None:
    """总线的视图提供者就是内核快照（ADR-056：视图 = 快照，同一个 dataclass）。"""
    assembly = assemble(seed=SEED)
    views: list[Snapshot] = []
    assembly.bus.subscribe_system("*", lambda envelope, ctx: views.append(ctx.view()))

    loop = HeadlessLoop(assembly)
    loop.start()

    assert views, "一条事件都没分发"
    assert views[-1] == assembly.kernel.snapshot()


# ---------------------------------------------------------------------------
# 验证 5：CLI 的 sim 跑出统计
# ---------------------------------------------------------------------------


def test_cli_sim_prints_the_statistics(capsys: pytest.CaptureFixture[str]) -> None:
    """`sim` 的人读输出必须含六项统计。"""
    code = main(["sim", "--frames", "5", "--seed", "0"])

    out = capsys.readouterr().out
    assert code == 0
    for label in ("帧数", "事件数", "棋盘指纹", "分数", "行数", "已放置方块数"):
        assert label in out, f"输出里没有「{label}」"


def test_cli_sim_json_outputs_the_same_fields(capsys: pytest.CaptureFixture[str]) -> None:
    """`sim --json` 给机器可读的同款字段。"""
    code = main(["sim", "--frames", "5", "--seed", "0", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["frames"] == 5
    assert set(payload) == {
        "frames",
        "events",
        "fingerprint",
        "score",
        "lines",
        "piecesPlaced",
        "phase",
    }


@pytest.mark.parametrize("command", ["play", "replay", "inspect"])
def test_placeholder_commands_run(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    """占位子命令**要能跑、不许报错**。"""
    assert main([command]) == 0
    assert command in capsys.readouterr().out
