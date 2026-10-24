"""内核生命周期与事件完备性测试（S15 · **G0-1** / **G0-2** · plan-M0.md §5.2）。

覆盖两道门：

- **G0-1**（`headless_10000_frames`）：无头跑通 10000 帧（AC-5）——不挂任何渲染 /
  输入模块，跑满 10000 帧，输出帧数、事件数、最终棋盘指纹与统计；**中途无异常、
  无未消费步骤**（架构 §6.2 / B-9）。
- **G0-2**（`event_completeness`）：状态变更 100% 伴随事件（AC-2）——遍历
  「命令类型 × phase × 有无活动方块」，每次 `execute` 后断言：判决明确、状态变更
  必有语义事件、拒绝不改状态。

## G0-2 的判据怎么落到代码里

需求 AC-2 的原话是「**不存在"改了状态没发事件"**」——这是门的主干。本文件把它
拆成四条可机检的断言（外加一条"对应语义事件"的收紧）：

1. **判决明确**：`command:accepted` 与 `command:rejected` **恰好有一个**——
   「什么都没发生」不算数（这正是 plan-M0.md S9 验证 1 的口径）。
2. **状态变更 100% 伴随事件**：版本号递增了，就必有**语义事件**（命令三段生命周期
   之外的事件）；且 **版本号递增次数 ≤ 语义事件条数**。
3. **拒绝不改状态**：被拒的命令执行前后**游戏状态**逐字段相等（**帧号除外**——
   帧号在 tick 出队时就 +1，被拒的 tick 也一样，这是 ADR-022 的明文口径，帧号不是
   游戏状态）。
4. **无静默状态变更的反面**：被接受但游戏状态没变时，**不得**发出"宣称状态已变"的
   事件类别。

> **为什么是四条而不是计划里的两句**：计划里写的是「状态未变 → 必有
> `command:rejected`；状态变了 → 必有语义事件」。真实内核里还有**第三种**合法情形：
> **被接受但状态没变**——`tick`（推进许可，ADR-022）、**受阻的移动 / 旋转**
> （ADR-043：命令仍被接受，另发受阻事件）、以及**状态本来就在初值时的 `reset`**
> （只宣告「已重置」）。硬套那两句会把这些合法路径判成红——所以这里按三条分桶
> （被拒 / 变了 / 没变），三条桶各自都有断言，没有一条是"没报错就算过"。

## 遍历表

「命令类型 × phase × 有无活动方块」= 16 × 4 × 2 = 128 种组合。其中 **32 种不可达**：
`not_started` 与 `game_over` 两态**必然没有活动方块**（状态机决定的：`start` 才
会给方块，`_end_game` 会清空方块），所以这两态的"有活动方块"变体**构造不出来**。
可达的 **6 种状态 × 16 条命令 = 96** 个组合全部逐个执行并断言；不可达的两种由
`test_unreachable_phase_and_active_combinations_are_really_unreachable` 守住。

**`hold` 在「running 态没有活动方块」时抛 `KernelInvariantError`**——那是**故意的**
（架构 §6.8）：`hold` 没声明 `no_active_piece`，那个状态没有可发的拒绝事件，内核
以异常终止而不是硬造一条没声明的拒绝原因。测试把它当成**契约的一部分**对待。
"""

from __future__ import annotations

import pytest

from tetris_kernel.contracts.commands import COMMAND_TYPES
from tetris_kernel.kernel.commands import Args, Command
from tetris_kernel.kernel.core import Kernel, KernelInvariantError
from tetris_kernel.kernel.drafts import EventDraft
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.rules.tables import PIECE_TYPES

#: 固定种子（测试不依赖随机性：同种子同序列）。
SEED = 20261024

#: 四个 phase（契约的取值域由内核定义）。
PHASES = ("not_started", "running", "paused", "game_over")

#: G0-1 要跑的帧数（需求 AC-5 / G0-1）。
HEADLESS_FRAMES = 10000

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

#: 命令三段生命周期的事件（**不算语义事件**）。
COMMAND_LIFECYCLE_EVENTS = frozenset({"command:received", "command:accepted", "command:rejected"})

#: 每条命令被接受时**应当**出现的语义事件（「对应语义事件」的判据）。
#:
#: `softDrop` 额外列了 `piece:moved`：受阻的软降走的是"移动受阻"那条事件
#: （ADR-043：命令仍被接受，另发受阻事件）。
EXPECTED_SEMANTIC_EVENTS: dict[str, frozenset[str]] = {
    "start": frozenset({"game:started"}),
    "pause": frozenset({"game:paused"}),
    "resume": frozenset({"game:resumed"}),
    "reset": frozenset({"game:reset"}),
    "moveLeft": frozenset({"piece:moved"}),
    "moveRight": frozenset({"piece:moved"}),
    "softDrop": frozenset({"piece:softdrop", "piece:moved"}),
    "hardDrop": frozenset({"piece:harddrop"}),
    "rotateCW": frozenset({"piece:rotated"}),
    "rotateCCW": frozenset({"piece:rotated"}),
    "rotate180": frozenset({"piece:rotated"}),
    "hold": frozenset({"piece:held"}),
    "tick": frozenset({"clock:tick"}),
    "setLevel": frozenset({"progress:level-changed"}),
    "injectGarbage": frozenset({"board:garbage-received", "board:changed"}),
    "writeCells": frozenset({"board:changed"}),
}

#: "宣称状态已变"的事件类别——**状态没变时它们不该出现**。
#:
#: `game:reset` **不在表里**：它把版本号**归零**（不是递增），状态本来就在初值时
#: 它只是宣告"已重置"，不算"宣称状态已变"。
STATE_CHANGING_EVENTS = frozenset(
    {
        "game:started",
        "game:paused",
        "game:resumed",
        "game:over",
        "score:changed",
        "score:combo-changed",
        "score:b2b-changed",
        "board:changed",
        "board:collapsed",
        "board:lines-cleared",
        "board:garbage-received",
        "piece:held",
        "piece:softdrop",
        "piece:harddrop",
        "piece:locked",
        "progress:level-changed",
    }
)

#: 遍历表里**可达**的 (phase, 有无活动方块) 组合。
TRAVERSAL_STATES: tuple[tuple[str, bool], ...] = (
    ("not_started", False),
    ("running", True),
    ("running", False),
    ("paused", True),
    ("paused", False),
    ("game_over", False),
)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _types(drafts: tuple[EventDraft, ...]) -> list[str]:
    """草稿序列的事件类型名。"""
    return [draft.eventType for draft in drafts]


def _game_state(snapshot: Snapshot) -> tuple[object, ...]:
    """**游戏状态**（去掉帧号与版本号）。

    - **帧号不算状态**：ADR-022 明说帧号在 tick **出队时**就 +1，被拒的 tick 也
    一样；"拒绝不改状态"比的是游戏状态，不是时间轴。
    - **版本号不算状态**：它是脏标记（每次变更递增），单独断言。
    """
    return (
        snapshot.rule,
        snapshot.phase,
        snapshot.board,
        snapshot.active,
        snapshot.ghostDelta,
        snapshot.hold,
        snapshot.holdUsed,
        snapshot.next,
        snapshot.score,
        snapshot.lines,
        snapshot.level,
        snapshot.combo,
        snapshot.b2b,
        snapshot.piecesPlaced,
        snapshot.grounded,
        snapshot.lockTimer,
        snapshot.gravityTimer,
        snapshot.extensions,
    )


def _kernel() -> Kernel:
    """造一个内核（待开始态）。"""
    return Kernel(seed=SEED, piece_types=PIECE_TYPES)


def _kernel_in_state(phase: str, has_active: bool) -> Kernel:
    """造一个处在指定 phase 与「有无活动方块」状态的内核。

    **「running 态没有活动方块」是半帧状态**（架构 §6.2 禁止的形状）：帧推进的
    步骤序列被**故意不耗尽**——只消费重力与锁定两步，锁定步把方块放进棋盘并清空
    活动方块，出生步没跑，所以 `running` 态没有活动方块。

    这个状态**只能这样造**：内核正常路径下 `running` 态必然持有活动方块
    （架构 §6.8），而宿主提前 `break` 掉步骤序列正是它唯一的成因。
    """
    kernel = _kernel()
    if phase == "not_started":
        assert not has_active, "not_started 态拿不到活动方块"
        return kernel
    if phase == "game_over":
        assert not has_active, "game_over 态必然没有活动方块"
        # 开局 → 硬降腾出出生区 → 堵死出生区 → 暂存换新方块 → 出生被堵 → 本局结束。
        kernel.execute(Command("start", source="test"))
        kernel.execute(Command("hardDrop", source="test"))
        kernel.execute(Command("writeCells", args=(("cells", SPAWN_BLOCK_CELLS),), source="test"))
        kernel.execute(Command("hold", source="test"))
        assert kernel.phase == "game_over", "game_over 状态没造出来"
        return kernel

    # running / paused：先开局（开局必有方块）。
    kernel.execute(Command("start", source="test"))
    if not has_active:
        # 半帧：硬降 → 落地 → tick 授予推进许可 → 只消费重力与锁定两步。
        kernel.execute(Command("hardDrop", source="test"))
        kernel.execute(Command("tick", source="test"))
        steps = kernel.advance_frame()
        next(steps)  # 重力步
        next(steps)  # 锁定步：方块落盘，活动方块清空
        assert kernel.snapshot().active is None, "半帧状态没造出来"
    if phase == "paused":
        kernel.execute(Command("pause", source="test"))
    return kernel


# ---------------------------------------------------------------------------
# G0-1：无头跑通 10000 帧（AC-5）
# ---------------------------------------------------------------------------


def test_headless_10000_frames() -> None:
    """G0-1：无头跑通 10000 帧——**不挂任何渲染 / 输入模块**。

    用的是宿主装配层与无头帧循环（plan-M0.md §1.4.4.1 ① ② 的**先验定签名**）：
    `assemble(...)` 拼出装配，`HeadlessLoop.run_frames(n)` 跑满 n 帧并给出统计。

    判据（AC-5 + 架构 §6.2）：

    - 帧数**跑满** 10000（局结束也不提前退出，帧号照走）；
    - 事件数不为零，最终棋盘指纹与统计齐全；
    - **中途无异常**：总线历史里没有 `kernel:error`；
    - **无未消费步骤**：帧推进的步骤序列被完整消费——`running` 态必然持有活动方块
      （架构 §6.8），宿主提前 `break` 会留下"锁定已完成、消行未做"的半帧。
    """
    assembly_module = pytest.importorskip(
        "tetris_kernel.host.assembly",
        reason="G0-1 依赖宿主装配层（plan-M0.md §1.4.4.1 ①）；它没落地，这道门就验不了",
    )
    loop_module = pytest.importorskip(
        "tetris_kernel.host.loop",
        reason="G0-1 依赖宿主帧循环（plan-M0.md §1.4.4.1 ②）；它没落地，这道门就验不了",
    )

    assembly = assembly_module.assemble(seed=0)
    loop = loop_module.HeadlessLoop(assembly, source="host")
    loop.start()
    report = loop.run_frames(HEADLESS_FRAMES)

    stats = (
        f"帧数={report.frames} 事件数={report.events} 指纹={report.fingerprint} "
        f"分数={report.score} 行数={report.lines} 已放置={report.piecesPlaced} "
        f"phase={report.phase}"
    )
    assert report.frames == HEADLESS_FRAMES, f"没跑满 {HEADLESS_FRAMES} 帧：{stats}"
    assert report.events > 0, f"一条事件都没有：{stats}"
    assert report.fingerprint, f"没有最终棋盘指纹：{stats}"
    assert report.phase in PHASES, f"phase 不在取值域内：{stats}"
    assert report.score >= 0 and report.lines >= 0 and report.piecesPlaced >= 0, stats

    snapshot = assembly.kernel.snapshot()
    assert snapshot.frame == HEADLESS_FRAMES, f"帧号没照走：{stats}"
    assert report.fingerprint == snapshot.board.structureFingerprint, stats

    # 中途无异常：订阅者抛错会转成 `kernel:error`（架构 §4.2 · FR-3.4）。
    errors = [envelope for envelope in assembly.bus.history() if envelope.type == "kernel:error"]
    assert not errors, f"无头跑批里出现了 {len(errors)} 条 kernel:error：{stats}"

    # 无未消费步骤：帧推进被完整消费（架构 §6.2 / B-9）。
    if report.phase == "running":
        assert snapshot.active is not None, "running 态没有活动方块——步骤序列没被耗尽"
    if report.phase == "game_over":
        assert snapshot.active is None, "game_over 态还挂着活动方块"


# ---------------------------------------------------------------------------
# G0-2：状态变更 100% 伴随事件（AC-2）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command_type", COMMAND_TYPES)
@pytest.mark.parametrize(("phase", "has_active"), TRAVERSAL_STATES)
def test_event_completeness(phase: str, has_active: bool, command_type: str) -> None:
    """G0-2：遍历「命令类型 × phase × 有无活动方块」，状态变更必有事件。

    每条命令在每个状态下都要有明确的归宿：**要么接受、要么拒绝**；状态变了就
    必须伴随语义事件；被拒就不许改状态。
    """
    kernel = _kernel_in_state(phase, has_active)
    command = Command(command_type, args=VALID_ARGS.get(command_type, ()), source="test")
    before = kernel.snapshot()

    # 「running 态没有活动方块」时 `hold` 抛 KernelInvariantError——**契约的一部分**
    # （架构 §6.8）：`hold` 没声明 `no_active_piece`，那个状态没有可发的拒绝事件。
    if command_type == "hold" and phase == "running" and not has_active:
        with pytest.raises(KernelInvariantError):
            kernel.execute(command)
        assert kernel.snapshot().version == before.version, "抛异常前已经改了状态"
        return

    drafts = kernel.execute(command)
    after = kernel.snapshot()
    types = _types(drafts)
    semantic = [name for name in types if name not in COMMAND_LIFECYCLE_EVENTS]
    accepted = "command:accepted" in types
    rejected = "command:rejected" in types

    # ① 判决明确：恰好一个（"什么都没发生"不算数）。
    assert accepted != rejected, f"判决不明（accepted={accepted}）：{types}"
    if rejected:
        # ③ 拒绝不改状态（帧号除外——ADR-022：帧号在 tick 出队时就 +1）。
        assert after.version == before.version, f"被拒的命令改了版本号：{types}"
        assert _game_state(after) == _game_state(before), f"被拒的命令改了状态：{types}"
        return

    # ② 状态变更 100% 伴随事件。
    assert semantic, f"被接受却一条语义事件都没有：{types}"
    increments = max(0, after.version - before.version)
    assert increments <= len(semantic), (
        f"版本号递增 {increments} 次，语义事件只有 {len(semantic)} 条：{types}"
    )
    # 「对应语义事件」：这条命令该发的事件必须真的发了。
    assert set(semantic) & EXPECTED_SEMANTIC_EVENTS[command_type], (
        f"{command_type} 的语义事件对不上：{types}"
    )
    # ④ 状态没变就不许发"宣称状态已变"的事件。
    if increments == 0 and _game_state(after) == _game_state(before):
        assert not (set(semantic) & STATE_CHANGING_EVENTS), f"状态没变却发了状态变更事件：{types}"


def test_unreachable_phase_and_active_combinations_are_really_unreachable() -> None:
    """`not_started` 与 `game_over` 态**必然没有活动方块**。

    遍历表里这两个格子空着是有理由的：`start` 才给方块，`_end_game` 清空方块——
    所以"这两态 + 有活动方块"**构造不出来**。这条用例把那个理由钉住，免得有人
    哪天改状态机时 unnoticed 地改了它。
    """
    fresh = _kernel()
    assert fresh.snapshot().phase == "not_started"
    assert fresh.snapshot().active is None, "not_started 态居然有活动方块"

    over = _kernel_in_state("game_over", False)
    assert over.snapshot().phase == "game_over"
    assert over.snapshot().active is None, "game_over 态居然有活动方块"
