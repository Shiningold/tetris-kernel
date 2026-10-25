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

import io
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from typing import cast

import pytest

from tetris_kernel.bus.envelope import Draft, Envelope, Metadata, metadata_value
from tetris_kernel.host.assembly import Assembly, assemble
from tetris_kernel.host.cli import main
from tetris_kernel.host.clock import DEFAULT_STEP_MS, VirtualClock
from tetris_kernel.host.loop import HeadlessLoop, RunReport
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.core import LOCK_DELAY_FRAMES, Kernel
from tetris_kernel.kernel.drafts import EventDraft
from tetris_kernel.kernel.frame import FrameContext, StepOutcome
from tetris_kernel.kernel.lifecycle import PHASE_GAME_OVER, PHASE_RUNNING, PHASES
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
        # **锁定延迟 30 帧（spec §7.5）**：硬降只是把方块按到地板上，**照样要
        # 等延迟到期才锁定**（M0 是落即锁，推进一帧就锁上了）——不补这段帧，
        # 方块永远锁不上、棋盘永远填不满，这条用例顶不到「顶出 → 局结束」那条
        # 路径，后面的断言就全成了空跑。
        loop.run_frames(LOCK_DELAY_FRAMES + 1)
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


def test_the_host_forwards_every_draft_one_by_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """宿主把内核产出的草稿**逐条**转交给总线——一条都不能少（C5）。

    plan-M0.md §1.4.4.1 ② 把「宿主负责**逐条**转发」写成宿主的核心职责（内核的
    `publish` 吃**一批**草稿，总线的 `publish` 吃**一条**）。原来的守卫只查每批的
    **第一条**——把转发改成"每批只发第一条"全套测试仍然全绿：`piece:locked` 之后
    的 `board:changed` / `score:changed` 全被静默丢掉，订阅者与回放对齐都会缺事件
    （需求 S-3 / AC-2 的根基）。

    **两侧对拍**（只数"宿主发了多少"不够——截断之后那个数也自洽）：

    - **内核侧**：内核交给宿主的草稿（命令轮次 + 帧推进五步 + 构造时那两条初始
      事件）——从 `run_rounds` / `advance_frame` 两个出口数；
    - **宿主侧**：宿主交给总线的草稿——从 `bus.publish` 数；
    - **订阅者侧**：真正收到的事件。

    帧末那条 `state:changed` 是**宿主自己**造的，不在内核产出的草稿里，两侧
    对拍时剔掉它。
    """
    assembly = assemble(seed=SEED)
    kernel = assembly.kernel

    produced: list[str] = []
    original_run_rounds = kernel.run_rounds

    def counting_run_rounds(publish: Callable[[tuple[EventDraft, ...]], None]) -> int:
        def counted(drafts: tuple[EventDraft, ...]) -> None:
            produced.extend(draft.eventType for draft in drafts)
            publish(drafts)

        return original_run_rounds(counted)

    monkeypatch.setattr(kernel, "run_rounds", counting_run_rounds)

    original_advance_frame = kernel.advance_frame

    def counting_advance_frame() -> Iterator[tuple[EventDraft, ...]]:
        for batch in original_advance_frame():
            produced.extend(draft.eventType for draft in batch)
            yield batch

    monkeypatch.setattr(kernel, "advance_frame", counting_advance_frame)

    forwarded: list[str] = []
    original_publish = assembly.bus.publish

    def publish(draft: Draft) -> None:
        forwarded.append(draft.eventType)
        original_publish(draft)

    monkeypatch.setattr(assembly.bus, "publish", publish)

    received: list[str] = []
    assembly.bus.subscribe("*", lambda envelope: received.append(envelope.type))

    loop = HeadlessLoop(assembly)
    produced.extend(draft.eventType for draft in kernel.initial_events())
    loop.start()
    for _ in range(60):
        loop.run_frame()

    kernel_side = [name for name in forwarded if name != "state:changed"]

    assert produced, "内核一条草稿都没产出"
    assert Counter(kernel_side) == Counter(produced), (
        f"内核产出 {len(produced)} 条草稿，宿主只转发了 {len(kernel_side)} 条——转发被截断了"
    )
    assert len(received) == len(forwarded), "有草稿没送到订阅者"
    assert len(received) == assembly.bus.last_seq, "总线分配的序号与订阅者收到的条数对不上"


def test_run_frame_returns_the_number_of_events_it_published() -> None:
    """`run_frame()` 的返回值 = **本帧发出的事件数**（C11）。

    plan-M0.md §1.4.4.1 ② 把 `run_frame(self) -> int` 写进**先验定签名**，
    `host/loop.py` 的 docstring 也明写这个语义——但原来**无人断言**它：把它改成
    恒返回 0，全套测试仍然全绿。公开 API 的返回值可以静默变成 0 而无人察觉。

    这里**逐帧**对拍：返回值 == 该帧订阅者收到的条数。
    """
    assembly = assemble(seed=SEED)
    received: list[str] = []
    assembly.bus.subscribe("*", lambda envelope: received.append(envelope.type))

    loop = HeadlessLoop(assembly)
    loop.start()
    for _ in range(60):
        before = len(received)
        count = loop.run_frame()
        assert count == len(received) - before, (
            f"run_frame 报告 {count} 条事件，订阅者这一帧收到 {len(received) - before} 条"
        )
    assert received, "一条事件都没收到——这条对拍等于没比"


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


def _payload_of(drafts: tuple[EventDraft, ...], event_type: str) -> dict[str, object]:
    """取某条事件的负载。"""
    for draft in drafts:
        if draft.eventType == event_type:
            return dict(draft.payload)
    raise AssertionError(f"没有 {event_type} 事件")


def _fill_bottom_rows(kernel: Kernel, rows: int, occupied: set[tuple[int, int]]) -> None:
    """把底 `rows` 行填满（**除了方块自己占的那几格**——写它们会被写入条件拒绝）。"""
    board = kernel.snapshot().board
    cells = tuple(
        (x, board.height - 1 - offset, 1)
        for offset in range(rows)
        for x in range(board.width)
        if (x, board.height - 1 - offset) not in occupied
    )
    kernel.execute(Command("writeCells", args=(("cells", cells),), source="test"))


def test_state_changed_payload_matches_the_frame_end_snapshot_by_value() -> None:
    """`state:changed` 的 13 个字段**逐个值**与帧末快照相等。

    **只守字段名与顺序是不够的**（批次 C 审查的发现 C4）：宿主把整份内核状态
    投影成一条事件，任何一处投影写错（字段名写对、值取错字段、值取旧值）测试
    都不会响——审查方的变异测试里，`score` 恒 0 / `holdUsed` 恒 False /
    `next` 恒空 / `activeType` 恒 None / `version` 恒 0 这五种改法**全部存活**。

    **局面必须每一项都非平凡**，否则守不住：开局几帧里 score / lines / combo /
    b2b 全是 0，"恒 0"的变异照样对得上——那种局面下这两种写法**根本不可区分**。
    所以这里造一个**四消**的局面（分数、行数、连击、背面连击全有值——背面连击
    只在**难消**时入链，四消才算），再叠上暂存与落盘。

    比对**逐帧**做，不只比末条：版本号每帧递增，比一条等于只守了一个时刻。
    """
    assembly = assemble(seed=SEED)
    captured: list[tuple[dict[str, object], Snapshot]] = []

    def capture(envelope: Envelope) -> None:
        # 回调发生在帧末——此刻的快照就是这条事件投影的那一份。
        captured.append((dict(envelope.payload), assembly.kernel.snapshot()))

    assembly.bus.subscribe("state:changed", capture)
    loop = HeadlessLoop(assembly)
    loop.start()

    kernel = assembly.kernel
    dropped = kernel.execute(Command("hardDrop", source="test"))
    landing = _payload_of(dropped, "piece:harddrop")["cells"]
    _fill_bottom_rows(
        kernel,
        4,
        {tuple(cell) for cell in cast("tuple[tuple[int, int], ...]", landing)},
    )
    # **锁定延迟 30 帧（spec §7.5）**：硬降之后要等延迟到期方块才锁定，锁定
    # 那一帧才消行——M0 是推进一帧就锁上并消行。
    loop.run_frames(LOCK_DELAY_FRAMES + 1)
    # 消行之后暂存一下：hold / holdUsed 也得有非平凡取值。
    kernel.execute(Command("hold", source="test"))
    loop.run_frame()

    assert captured, "没有收到 state:changed"
    for payload, snapshot in captured:
        active = snapshot.active
        assert payload == {
            "version": snapshot.version,
            "rule": snapshot.rule,
            "phase": snapshot.phase,
            "score": snapshot.score,
            "lines": snapshot.lines,
            "level": snapshot.level,
            "combo": snapshot.combo,
            "b2b": snapshot.b2b,
            "piecesPlaced": snapshot.piecesPlaced,
            "activeType": None if active is None else active.pieceType,
            "hold": snapshot.hold,
            "holdUsed": snapshot.holdUsed,
            "next": snapshot.next,
        }, "负载与帧末快照对不上"

    # 局面确实非平凡——否则上面的比对守不住"恒值"变异（这是本用例的前提）。
    final = captured[-1][1]
    assert final.score > 0, "没消行，分数恒 0——比对等于没比"
    assert final.lines > 0, "没消行"
    assert final.combo > 0, "没消行"
    assert final.b2b is True, "没做难消，背面连击恒 False——比对等于没比"
    assert final.piecesPlaced > 0, "方块没落盘"
    assert final.hold is not None, "没暂存"
    assert final.holdUsed is True, "没暂存"


def test_state_changed_metadata_has_no_upstream_command() -> None:
    """`state:changed` 的元数据是 `source=''` + `cause=None`——**没有上游命令**。

    它是对**整帧**的观察，不是某条命令的结果：ADR-062 说 `cause=None` 就是
    "无上游命令"，总线的 `default_source` 默认空串、语义相同——**这两条是配套
    的**，不是"漏了 source"。

    同帧的 `command:accepted` / `clock:gravity` 有上游（tick 命令），所以它们
    带 `source='host'`、`cause=(帧号, 帧内序号)`。**两条路径各有各的上游，
    不该被拉平成同一个值**（批次 C 审查的发现 C2：这条口径此前无任何守卫）。

    产出者由总线按注册表填（ADR-063）——宿主发出，所以是 `host`（需求 §8.1 的
    「产出者」列定义就是"谁发出这个事件"）。
    """
    assembly = assemble(seed=SEED)
    metadata: list[Metadata] = []

    def capture(envelope: Envelope) -> None:
        metadata.append(envelope.metadata)

    assembly.bus.subscribe("state:changed", capture)
    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frame()

    assert metadata, "没有收到 state:changed"
    assert metadata_value(metadata[-1], "source") == "", "帧末事件不该有上游命令来源"
    assert metadata_value(metadata[-1], "cause") is None, "帧末事件不该有上游命令"
    assert metadata_value(metadata[-1], "producer") == "host", "产出者与实际发出者不符"


def test_run_frames_rejects_a_negative_frame_count() -> None:
    """负帧数直接报错——不静默返回一份"跑了 0 帧"的报告（批次 C 审查的发现 C9）。"""
    assembly = assemble(seed=SEED)
    loop = HeadlessLoop(assembly)
    loop.start()

    with pytest.raises(ValueError, match="不能为负"):
        loop.run_frames(-1)


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
    # **锁定步的事件**在 M1 起是 `piece:lock-delay-start`（spec §7.5 的锁定
    # 延迟：方块落地先起算 30 帧，不当场锁定）——**要验的是「排空阶段的事件
    # 照发」**，所以认锁定步发出的那条事件即可。
    assert "piece:lock-delay-start" in published, "排空阶段的剩余事件没有照发"


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


def test_cli_sim_output_values_match_a_real_run(capsys: pytest.CaptureFixture[str]) -> None:
    """`sim` 输出的**值**要对得上真实运行（C12）。

    原来人读形态只查「标签字符串在不在」（打印「帧数：99999」照样过），JSON 形态
    只查字段集合 + `frames` 一个值——把 `_report_dict` 的 `score` 与 `events` 写死，
    全套测试仍然全绿。

    这里做两次对拍：

    1. JSON 形态的**每个值** == 同种子同帧数**真实跑一遍**的报告；
    2. 人读形态的**每个值** == JSON 形态的同名值（两种形态不许各说各话）。

    **帧数取 1300**：不少于一块方块落盘（重力间隔 60 帧，20 行可见区），这样
    事件数 / 已放置方块数 / 棋盘指纹**都不是平凡值**——否则"写死一个恰好等于
    真值的数"照样能过，对拍等于没比。

    > **一处诚实的弱项**：`sim` 不注入垃圾行、消行要堆满一整行，所以这个场景里
    > **分数恒为 0**——分数那条对拍在数值上是弱的（写死 `score: 0` 抓不到）。
    > 抓得住的是事件数 / 已放置方块数 / 棋盘指纹 / 阶段这四项。
    """
    frames = 1300

    code = main(["sim", "--frames", str(frames), "--seed", "0", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0

    # 真实跑一遍（同种子同帧数 = 同一局，事件流是确定的）。
    assembly = assemble(seed=0)
    loop = HeadlessLoop(assembly)
    loop.start()
    report = loop.run_frames(frames)

    assert payload["frames"] == report.frames, "帧数与真实运行对不上"
    assert payload["events"] == report.events, "事件数与真实运行对不上"
    assert payload["fingerprint"] == report.fingerprint, "棋盘指纹与真实运行对不上"
    assert payload["score"] == report.score, "分数与真实运行对不上"
    assert payload["lines"] == report.lines, "行数与真实运行对不上"
    assert payload["piecesPlaced"] == report.piecesPlaced, "已放置方块数与真实运行对不上"
    assert payload["phase"] == report.phase, "阶段与真实运行对不上"

    # 对拍的前提：这些值**不平凡**，否则"写死一个恰好等于真值的数"照样能过。
    assert payload["events"] > 0, "事件数是平凡值——对拍等于没比"
    assert payload["piecesPlaced"] > 0, "已放置方块数是平凡值——对拍等于没比"
    assert payload["fingerprint"], "棋盘指纹是空串——对拍等于没比"

    # 人读形态 vs JSON 形态：同一个报告，两种呈现，值必须一致。
    code = main(["sim", "--frames", str(frames), "--seed", "0"])
    out = capsys.readouterr().out
    assert code == 0
    assert f"帧数：{payload['frames']}" in out
    assert f"事件数：{payload['events']}" in out
    assert f"棋盘指纹：{payload['fingerprint']}" in out
    assert f"分数：{payload['score']}" in out
    assert f"行数：{payload['lines']}" in out
    assert f"已放置方块数：{payload['piecesPlaced']}" in out
    assert f"阶段：{payload['phase']}" in out


def test_cli_sim_rejects_a_negative_frame_count(capsys: pytest.CaptureFixture[str]) -> None:
    """负帧数 → CLI 层**以 2 退出**（不是只有 API 层挡）。

    API 层的 `run_frames` 早就挡了负帧数（`ValueError`），CLI 是**另一条路**：
    `main(["sim", "--frames", "-1"])` 的退出码原先**没人验**——它要是返回 0，
    进程级行为就是"跑成功了"（M0 全量审查 M0-11）。

    **成功路径的退出码另有用例守着**（`code == 0`），这里只补**失败路径**。
    """
    code = main(["sim", "--frames", "-1", "--seed", "0"])

    assert code == 2, f"负帧数应当以 2 退出，实为 {code}"
    assert "帧数不能为负" in capsys.readouterr().err


def test_cli_output_stream_is_switched_to_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """被**管道接走**的 stdout 被显式切到 UTF-8（批次 C 审查的发现 C6）。

    Windows 的 stdout 在不是 TTY 时用**本地代码页**（本机 GBK / CP936），而
    人读输出含中文——机器消费方按 UTF-8 读会炸（`UnicodeDecodeError`）。

    这里造一个 **GBK** 的流来验：跑之前是 `gbk`，跑完 `main()` 之后必须是
    `utf-8`，且按 UTF-8 解得出来。**控制台那条路不动**（Windows 控制台 IO 走
    Unicode API，Python 已经处理好了，硬切反而可能显示乱码）。
    """
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="gbk", errors="replace")
    monkeypatch.setattr(sys, "stdout", stream)
    assert stream.encoding == "gbk"

    code = main(["sim", "--frames", "2", "--seed", "0"])
    stream.flush()

    assert code == 0
    assert stream.encoding == "utf-8", "非 TTY 的输出流没有被切到 UTF-8"
    assert "帧数" in buffer.getvalue().decode("utf-8")


@pytest.mark.parametrize("command", ["play", "replay", "inspect"])
def test_placeholder_commands_run(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    """占位子命令**要能跑、不许报错**。"""
    assert main([command]) == 0
    assert command in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 验证 6：M1 · 跑到顶出（spec §7.8 · plan-M1.md S12 段）
# ---------------------------------------------------------------------------

#: **小棋盘**（高 6 · 隐藏 2 → 可见 4 行）：顶出快得多。
#:
#: **为什么用小棋盘**：这些用例要验的是**宿主的停帧语义**，不是棋盘尺寸。
#: 实测：默认棋盘一局约 **7500 帧（约 9 秒）**，小棋盘约 **450 帧（0.5 秒）**
#: ——后者能把"跑到顶出"这条路径**测得很密**（默认棋盘只测得起一次）。
SMALL_BOARD_HEIGHT = 6
SMALL_HIDDEN_ROWS = 2

#: 小棋盘上跑到顶出所需的帧数上限（**给足**：真跑不到顶出就是用例前提不成立）。
FRAMES_TO_GAME_OVER = 5000

#: **结算八项**（spec §7.8：分数 / 行数 / 方块数 / 连击 / B2B / 等级 / 最终
#: 棋盘指纹 / 事件数）。
SETTLEMENT_KEYS = (
    "score",
    "lines",
    "piecesPlaced",
    "combo",
    "b2b",
    "level",
    "fingerprint",
    "events",
)


def _small_loop() -> tuple[Assembly, HeadlessLoop]:
    """小棋盘上的帧循环（**已开局**）。"""
    assembly = assemble(
        seed=SEED,
        board_height=SMALL_BOARD_HEIGHT,
        hidden_rows=SMALL_HIDDEN_ROWS,
    )
    loop = HeadlessLoop(assembly)
    loop.start()
    return assembly, loop


def test_run_frames_stops_at_the_game_over_when_asked() -> None:
    """`until_game_over=True` → **跑到顶出就停帧**（spec §7.8）。

    **停的是宿主**：宿主不再派发后面的 tick，所以后面的帧根本没开始。
    """
    _assembly, loop = _small_loop()

    report = loop.run_frames(FRAMES_TO_GAME_OVER, until_game_over=True)

    assert report.phase == PHASE_GAME_OVER, "没跑到顶出（用例前提不成立）"
    assert report.frames < FRAMES_TO_GAME_OVER, "跑到顶出后没有停帧"


def test_run_frames_without_the_flag_keeps_running_after_the_game_over() -> None:
    """**不带 `until_game_over` 时，行为与 M0 逐字一致**：局结束也不提前退出。

    **M0 的口径是"跑满帧数"**——局结束只是不再推进，宿主照跑（plan-M1.md S12
    段的验证方案第 3 条）。
    """
    _assembly, loop = _small_loop()

    report = loop.run_frames(FRAMES_TO_GAME_OVER)

    assert report.phase == PHASE_GAME_OVER, "这一局没跑到顶出（用例前提不成立）"
    assert report.frames == FRAMES_TO_GAME_OVER, "局结束后提前退出了（M0 的口径是跑满帧数）"


def test_the_frame_counter_keeps_going_after_the_game_is_over() -> None:
    """局结束后**帧号照走**（推进许可不再授予）——M0 的既有行为，M1 不许改。

    **不许改成"局结束就停帧号"**（plan-M1.md S12 段的验证方案第 2 条）：帧号是
    **宿主逻辑帧**，局结束不代表帧号停（架构 §6.1）。

    推进许可不再授予 → **棋盘与分数不再变**；但帧号照涨。
    """
    assembly, loop = _small_loop()
    loop.run_frames(FRAMES_TO_GAME_OVER)
    assert loop.snapshot().phase == PHASE_GAME_OVER, "这一局没跑到顶出（用例前提不成立）"

    frame_before = assembly.kernel.frame
    board_before = loop.snapshot().board.cells
    score_before = loop.snapshot().score

    loop.run_frames(10)

    assert assembly.kernel.frame == frame_before + 10, "局结束后帧号没有照走"
    assert loop.snapshot().board.cells == board_before, "局结束后棋盘还在变"
    assert loop.snapshot().score == score_before, "局结束后分数还在变"


def test_the_report_carries_the_settlement_numbers() -> None:
    """报告增**结算字段**（连击 / B2B / 等级）——值取自帧末快照。

    **加字段不改已有字段的含义**（plan-M1.md S12 段）：前七项是 M0 的口径，
    一字未动。
    """
    _assembly, loop = _small_loop()

    report = loop.run_frames(200)
    snapshot = loop.snapshot()

    assert report.combo == snapshot.combo, "连击与帧末快照对不上"
    assert report.b2b == snapshot.b2b, "B2B 与帧末快照对不上"
    assert report.level == snapshot.level, "等级与帧末快照对不上"
    # **M0 的七项一个都没变**：值仍取自帧末快照。
    assert report.score == snapshot.score
    assert report.lines == snapshot.lines
    assert report.piecesPlaced == snapshot.piecesPlaced
    assert report.phase == snapshot.phase


def test_cli_sim_until_game_over_prints_the_settlement(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`sim --until game-over` → 跑到底出，输出 `phase=game_over` 与结算八项。

    **默认棋盘的一整局约 7500 帧（约 9 秒）**——这是"真的玩到顶出"的代价，
    绕不开：验收载体就是要**真的跑一局**（spec §7.8）。
    """
    code = main(["sim", "--until", "game-over", "--seed", "0"])

    out = capsys.readouterr().out
    assert code == 0, f"退出码不对：{code}"
    assert "phase=game_over" in out, out
    for key in SETTLEMENT_KEYS:
        assert f"{key}=" in out, f"结算里没有「{key}」：{out}"


def test_cli_sim_until_game_over_json_has_the_settlement_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`sim --until game-over --json` → 机器可读的同款结算（**十项**）。

    **与 M0 的七项形态的区别**：结算形态多给 `combo` / `b2b` / `level` 三项
    （M0 形态**保持七项不动**——见下面那条用例）。
    """
    code = main(["sim", "--until", "game-over", "--seed", "0", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0, f"退出码不对：{code}"
    assert payload["phase"] == PHASE_GAME_OVER, payload
    assert set(payload) == {
        "phase",
        "frames",
        "events",
        "fingerprint",
        "score",
        "lines",
        "piecesPlaced",
        "combo",
        "b2b",
        "level",
    }
    for key in SETTLEMENT_KEYS:
        assert key in payload, f"结算里没有「{key}」：{payload}"


def test_cli_sim_without_until_keeps_the_m0_output_shape(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """**不带 `--until` 时，输出与 M0 逐字一致**（plan-M1.md S12 段的验证方案第 3 条）。

    `RunReport` 加了三个结算字段，但 **M0 形态的输出仍然是那七项**——M0 的
    既有用例一条都不许动（`test_cli_sim_json_outputs_the_same_fields` 断言的
    正是那七个键）。
    """
    code = main(["sim", "--frames", "5", "--seed", "0", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(payload) == {
        "frames",
        "events",
        "fingerprint",
        "score",
        "lines",
        "piecesPlaced",
        "phase",
    }

    code = main(["sim", "--frames", "5", "--seed", "0"])
    out = capsys.readouterr().out
    assert code == 0
    for label in ("帧数", "事件数", "棋盘指纹", "分数", "行数", "已放置方块数", "阶段"):
        assert label in out, f"输出里没有「{label}」"
    # **七行，一行不多**：结算形态那三项**不进 M0 形态**。
    assert len(out.strip().splitlines()) == 7, out


def test_cli_sim_until_game_over_rejects_a_negative_frame_count(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--until game-over` **不豁免**负帧数的校验（退出码 2）。

    M0 的既有口径（`test_cli_sim_rejects_a_negative_frame_count`）**不因为新
    开关而失效**——新参数不能顺手把旧校验绕过去。
    """
    code = main(["sim", "--until", "game-over", "--frames", "-1", "--seed", "0"])

    assert code == 2, f"负帧数应当以 2 退出，实为 {code}"
    assert "帧数不能为负" in capsys.readouterr().err
