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
拆成四条可机检的断言（外加两条收紧）：

1. **判决明确**：`command:accepted` 与 `command:rejected` **恰好有一个**——
   「什么都没发生」不算数（这正是 plan-M0.md S9 验证 1 的口径）。
2. **状态变更 100% 伴随事件**：版本号递增了，就必有**语义事件**（命令三段生命周期
   之外的事件）；且 **版本号递增次数 ≤ 语义事件条数**。
3. **拒绝不改状态**：被拒的命令执行前后**游戏状态**逐字段相等（**帧号除外**——
   帧号在 tick 出队时就 +1，被拒的 tick 也一样，这是 ADR-022 的明文口径，帧号不是
   游戏状态）。
4. **无静默状态变更的反面**：被接受但游戏状态没变时，**不得**发出"宣称状态已变"的
   事件类别。
5. **对应语义事件必须全发**（收紧）：每条命令该发的语义事件**一个都不能少**
   （批次 C 审查的发现 C3）。
6. **状态变了 → 版本号必须变**（收紧）：版本号是脏标记载体（架构 §6.6），漏更
   ⇒ 帧末不发 `state:changed` ⇒ 渲染器不重绘（批次 C 审查的发现 C9）。

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

## 批次 C 审查修订（2026-10-24）

本文件是 **plan-M0.md §5.2 指定的 G0-1 / G0-2 门判据文件**——外部审查指出这里
有几条断言"看起来在守、其实守不住"，逐条改成真能触发的写法：

- **C2**：原来扫 `bus.history()` 找 `kernel:error`（环形缓冲，5 万条事件早被挤掉）
  → 改成**过程中**订阅计数，结束时必须是 0；
- **C3**：语义事件原来用**集合交集**判定（删掉 `softDrop` 的 `score:changed` 全绿）
  → 改成**必须全发** + 二选一分支 + 伴随关系；
- **C4**：`piecesPlaced >= 0` 是恒真 → 改成 `piecesPlaced > 0`（方块真的放了）；
- **C9**：「状态变了但版本号没递增」全程无声 → 状态真变了，版本号必须变；
- **C10**：「无未消费步骤」只看终态、还带 phase 条件 → 改成**逐帧**查；
- **C18**：`pytest.importorskip` 兜底宿主层（没了就"绿 + 跳过"）→ 改成**直接
  import**，宿主层没了就是红。

"""

from __future__ import annotations

import pytest

from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.contracts.commands import COMMAND_TYPES
from tetris_kernel.host.assembly import assemble
from tetris_kernel.host.loop import HeadlessLoop
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

#: 每条命令被接受时**必须全发**的语义事件（「必须全发」= 子集，不是交集）。
#:
#: **为什么拆成三张表**（批次 C 审查的发现 C3）：原来只有一张「期望集合」，断言
#: 只要求**交集非空**——`softDrop` 期望集合里根本没有 `score:changed`，把那条
#: 事件整块删掉（分数照样 +1 = 改了状态却没发事件）全套测试仍然全绿。所以：
#:
#: - **必须全发**：这条命令的语义后果，一个都不能少；
#: - **二选一**：互斥分支（成功 / 受阻），命中其中之一即可；
#: - **伴随关系**：某条事件出现时必须同时出现哪些事件——软降给分是软降的一半
#:   语义，`piece:softdrop` 一定伴着 `score:changed`。
#:
#: **逐条对着内核实现核对过**（`kernel/core.py` 的 `_cmd_*`）：受阻的移动 / 旋转
#: 发 `piece:moved` / `piece:rotated`（`blocked=True`），成功的软降发
#: `piece:softdrop` + `score:changed`。
REQUIRED_SEMANTIC_EVENTS: dict[str, frozenset[str]] = {
    "start": frozenset({"game:started", "piece:spawned"}),
    "pause": frozenset({"game:paused"}),
    "resume": frozenset({"game:resumed"}),
    "reset": frozenset({"game:reset"}),
    "moveLeft": frozenset({"piece:moved"}),
    "moveRight": frozenset({"piece:moved"}),
    "softDrop": frozenset(),
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

#: **二选一**的语义事件分支：命中其中之一即可（成功 / 受阻互斥）。
ALTERNATIVE_SEMANTIC_EVENTS: dict[str, frozenset[str]] = {
    "softDrop": frozenset({"piece:softdrop", "piece:moved"}),
}

#: **伴随关系**：键出现 ⇒ 值必须同时出现（多副作用命令的另一半语义）。
SEMANTIC_EVENT_COMPANIONS: dict[str, frozenset[str]] = {
    "piece:softdrop": frozenset({"score:changed"}),
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
    - 事件数不为零，最终棋盘指纹与统计齐全，**方块真的放了**；
    - **中途无异常**：**过程中**订阅 `kernel:error` 计数，结束时必须是 0；
    - **无未消费步骤**：**逐帧**查「running 态必然持有活动方块」（架构 §6.8）。

    > **宿主层直接 import，不走 `importorskip`**（批次 C 审查的发现 C18）：
    > 门判据用 `importorskip` 兜底，宿主层没了这条用例是「跳过」而不是「失败」
    > ——门变绿了，但它什么都没验。
    """
    assembly = assemble(seed=0)

    # **过程中**订阅，不查历史（批次 C 审查的发现 C2）：`bus.history()` 是环形
    # 缓冲（默认 1024 条），10000 帧的五万余条事件早被挤掉——扫历史只看得到最后
    # 约 200 帧，那条「中途无异常」恒真。
    errors: list[Envelope] = []
    assembly.bus.subscribe("kernel:error", errors.append)

    # 通配订阅顺带当**对照**：它证明"订阅机制本身是活的"（真的收到了事件），
    # 也证明总线把发出去的事件都送到了订阅者手里。
    seen_count = 0
    changed: list[tuple[int, dict[str, object]]] = []

    def watch(envelope: Envelope) -> None:
        nonlocal seen_count
        seen_count += 1
        if envelope.type == "state:changed":
            changed.append((envelope.frame, dict(envelope.payload)))

    assembly.bus.subscribe("*", watch)

    loop = HeadlessLoop(assembly)
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
    assert report.score >= 0 and report.lines >= 0, stats
    # **方块真的放了**（批次 C 审查的发现 C4）：`>= 0` 是恒真的——出生步无条件
    # 出生那个缺陷在时，这条断言照样过，而门判据文件自己全绿。
    assert report.piecesPlaced > 0, f"一块方块都没放——帧模型没有真正推进：{stats}"

    snapshot = assembly.kernel.snapshot()
    assert snapshot.frame == HEADLESS_FRAMES, f"帧号没照走：{stats}"
    assert report.fingerprint == snapshot.board.structureFingerprint, stats

    # 订阅机制本身可证伪：通配订阅收到了事件，且条数与总线分配的序号一致。
    assert seen_count > 0, "一条事件都没分发到订阅者"
    assert seen_count == assembly.bus.last_seq, "有事件没送到订阅者"
    # 中途无异常：订阅者抛错会转成 `kernel:error`（架构 §4.2 · FR-3.4）。
    assert not errors, f"无头跑批里出现了 {len(errors)} 条 kernel:error：{stats}"

    # 无未消费步骤：**逐帧**查（架构 §6.2 / B-9 / §6.8）。
    # 每帧末的 `state:changed` 负载带 `phase` 与 `activeType`——那是帧末快照的
    # 投影，所以「每个状态变了的帧末」都被查到了。**没变化的帧不用查**：版本号
    # 不变就意味着状态没变（脏标记载体反过来用——G0-2 那条「状态变了 → 版本号
    # 必须变」正是这个推论的依据），活动方块与上一帧末相同。
    for frame, payload in changed:
        phase = payload["phase"]
        if phase == "running":
            assert payload["activeType"] is not None, (
                f"半帧状态残留：第 {frame} 帧末 running 态没有活动方块"
            )
        elif phase == "game_over":
            assert payload["activeType"] is None, f"第 {frame} 帧末 game_over 态还挂着活动方块"

    # 终态再查一遍（与逐帧那条互为对照）。
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
    # 「对应语义事件」：**必须全发**（批次 C 审查的发现 C3：原来只要求交集非空，
    # 删掉 `softDrop` 的 `score:changed` 全套测试仍然全绿）。
    emitted = set(semantic)
    missing = REQUIRED_SEMANTIC_EVENTS[command_type] - emitted
    assert not missing, f"{command_type} 少了语义事件 {sorted(missing)}：{types}"
    alternatives = ALTERNATIVE_SEMANTIC_EVENTS.get(command_type)
    if alternatives is not None:
        assert emitted & alternatives, (
            f"{command_type} 的语义事件一个都没对上 {sorted(alternatives)}：{types}"
        )
    for event_type, companions in SEMANTIC_EVENT_COMPANIONS.items():
        if event_type in emitted:
            absent = companions - emitted
            assert not absent, f"{event_type} 缺伴随事件 {sorted(absent)}：{types}"

    # 状态真变了 → 版本号必须变（批次 C 审查的发现 C9：版本号是宿主的脏标记载体，
    # 漏更 ⇒ 帧末不发 `state:changed` ⇒ 渲染器不重绘，而**全程无声**）。
    #
    # **取「必须变」而不是「必须递增」**：`reset` 把版本号**归零**（契约写定的
    # 口径），写成递增就得给 `reset` 单开一条例外名单——这里两种口径都盖得住，
    # 而"改了状态却不动版本号"照样红。
    if _game_state(after) != _game_state(before):
        assert after.version != before.version, f"状态变了但版本号没动：{types}"

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
