"""局面序列化与恢复测试（M2-S10 · FR-10.1 / FR-10.2 / FR-10.3 · ADR-080）。

覆盖 plan-M2.md S10 的验证方案：

1. **自包含**：序列化 → 反序列化 → **逐字段相等**（不是"看起来差不多"）；
2. **恢复后一致**：存档 → 恢复 → 再跑 N 帧 → 与"不中断直接跑 N 帧"
   **逐帧指纹全等**；
3. **反向验证 A**：把 `rngState` 从恢复路径里去掉 → **后续帧会不同**
   （本文件里是它的一条**正向断言**：存档里必须带随机源状态）；
4. **反向验证 B**：把版本号去掉 → **必须红**（NFR-9：不认识的头 → 明确报错）；
5. **未知版本**：喂一个不认识的头 → **明确报错**；
6. **存档 ≠ 录像**：两种格式**不共用一套**（把状态快照当录像存会让"恢复"
   悄悄变成"重放"）；
7. **机检**：`ruff check .` · `pyright` 绿（仓库的质量门，不在本文件里重跑）。

**判据的落点**：第 2 条的"逐帧全等"比的是**棋盘结构指纹**（架构 §5.4 的
`structureFingerprint`）——它覆盖棋盘内容的每一个格子。另有两条把判据加严：

- **事件流逐条相等**（NFR-7：恢复路径逐条有事件）——恢复之后的帧发出的
  事件，**类型 / 帧号 / 负载 / 元数据**逐条与"不中断"的那一段相同；
- **快照逐字段相等**——恢复之后拉的那份快照与未中断时的那份**逐字段相同**。
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from support.policy import ReferencePolicy
from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.host.archive import KernelArchiveDriver
from tetris_kernel.host.assembly import Assembly, assemble
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.core import Kernel
from tetris_kernel.kernel.snapshot import Active, Board, Snapshot
from tetris_kernel.systems.replay import (
    ARCHIVE_VERSION,
    Archive,
    ArchivedActive,
    ArchivedBoard,
    ArchivedState,
    RecordedCommand,
    Recording,
    archive_from_json,
    archive_to_json,
    recording_to_jsonl,
)

# ---------------------------------------------------------------------------
# 常量与夹具
# ---------------------------------------------------------------------------

#: 固定种子（**同一个种子 → 同一局** · FR-1.4）。
SEED = 20261026

#: 存档前的帧数。
PRE_FRAMES = 40

#: 存档后继续跑的帧数。
POST_FRAMES = 30

#: **跨过取片袋补货**的存档前帧数（第一袋在第 237 帧补货 · 见下面那条用例）。
REFILL_PRE_FRAMES = 200

#: 跨过取片袋补货的存档后帧数。
REFILL_POST_FRAMES = 120

#: 策略派发的命令的来源标记（只随事件透传）。
SOURCE = "policy"


def _new_assembly(seed: int = SEED) -> Assembly:
    """造一份装配（**可观测系统全关**——本文件只关心内核的逐帧行为）。

    **关掉可观测系统**是为了让事件流干净：日志 / 指标 / 录制三个系统都会
    订阅事件，它们不影响内核的行为，但会让"事件流逐条相等"的比对多出噪声
    （录制系统还会往 sink 写文件）。
    """
    return assemble(seed=seed, observability_enabled=False)


def _started(assembly: Assembly) -> HeadlessLoop:
    """开局（**派发 `start`，不开帧**）。"""
    loop = HeadlessLoop(assembly)
    loop.start()
    return loop


def _play(assembly: Assembly, loop: HeadlessLoop, frames: int) -> tuple[str, ...]:
    """按参考策略跑 N 帧，返回**逐帧棋盘指纹**。

    **策略是确定性的**（`ReferencePolicy` 是纯函数）——同一份快照 → 同一串
    命令。所以"恢复后的局面与未中断时相同"能一路推到"恢复后的命令序列相同"。
    """
    kernel = assembly.kernel
    policy = ReferencePolicy()
    fingerprints: list[str] = []
    for _ in range(frames):
        for name in policy.decide(kernel.snapshot()):
            kernel.submit(Command(type=name, source=SOURCE))
        loop.run_frame()
        fingerprints.append(loop.snapshot().board.structureFingerprint)
    return tuple(fingerprints)


def _restored_loop(assembly: Assembly, archive: Archive) -> HeadlessLoop:
    """把存档恢复进内核并造好帧循环（**不开局**：局面已经在了）。

    **为什么不开局**：`start` 命令会**再出生一块方块**并把相位拉回 `running`
    ——那会把刚恢复的局面覆盖掉。**载入存档的流程就是"恢复完直接跑帧"**。

    **这条路径才真正依赖存档里的随机源状态**：新内核的随机源是**初始状态**
    （一袋都没抽过），不恢复的话，第一次补袋时抽到的袋序就与未中断时不同。
    """
    KernelArchiveDriver(assembly.kernel).restore(archive)
    return HeadlessLoop(assembly)


def _collect(assembly: Assembly) -> list[Envelope]:
    """订阅总线，把信封收下来（**只读事件，不改任何状态**）。"""
    collected: list[Envelope] = []
    assembly.bus.subscribe("*", collected.append)
    return collected


def _event_shape(envelope: Envelope) -> tuple[str, int, tuple[tuple[str, object], ...], tuple]:
    """事件的**可比形状**（**不含全局序号**——序号跨运行必然不同）。

    - 类型 · 帧号 · 负载 · 元数据；
    - **元数据全带上**：`cause` 是 `(帧号, 帧内序号)`，恢复后必须与未中断时
      一致——它一乱，说明帧内序号没归零。
    """
    return (envelope.type, envelope.frame, envelope.payload, envelope.metadata)


# ---------------------------------------------------------------------------
# 验证 1：自包含
# ---------------------------------------------------------------------------


def test_the_archive_round_trips_field_by_field() -> None:
    """序列化 → 反序列化 → **逐字段相等**（自包含 · FR-10.1）。

    **不只是"能读回来"**：每个字段都要与原值相等——少一个字段、错一个
    类型，恢复后那一项就是错的，而逐帧一致会**假装成立**（棋盘那一项错了
    照样能跑，只是走得不一样）。
    """
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    archive = KernelArchiveDriver(assembly.kernel).export()

    restored = archive_from_json(archive_to_json(archive))

    assert restored == archive
    # 逐字段再比一遍（`==` 已经是逐字段，但**显式列出**更能定位是哪一项坏了）。
    assert restored.seed == archive.seed
    assert restored.rule == archive.rule
    assert restored.rngState == archive.rngState
    assert restored.state == archive.state
    assert restored.state.board == archive.state.board
    assert restored.state.active == archive.state.active


def test_the_archive_covers_every_snapshot_field() -> None:
    """存档的字段清单与**快照逐条对得上**（plan-M2 §S10 的常见坑第三条）。

    **快照加了字段、存档没跟着加** → 恢复后缺一块 → FR-10.2 的判据形同
    虚设。这条用例把两条清单摆在一起比：

    - 快照的 22 个顶层字段，减去**两个算出来的**（`extensionFingerprint` ·
      `structureFingerprint`），**逐个**在存档里有对应；
    - 棋盘子结构同理；
    - 活动方块：快照的 4 个字段**一个不少**，另加 5 项判定用的瞬时事实。
    """
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)

    snapshot_fields = {field.name for field in dataclasses.fields(Snapshot)}
    state_fields = {field.name for field in dataclasses.fields(ArchivedState)}
    # 规则集标识与随机源状态在**存档的头**上（`Archive` 的字段）。
    archive_fields = state_fields | {"rule", "rngState"}
    derived = {"extensionFingerprint", "structureFingerprint"}
    assert snapshot_fields - derived == archive_fields
    assert derived.isdisjoint(archive_fields)

    board_fields = {field.name for field in dataclasses.fields(Board)}
    archived_board_fields = {field.name for field in dataclasses.fields(ArchivedBoard)}
    assert board_fields - {"structureFingerprint"} == archived_board_fields

    active_fields = {field.name for field in dataclasses.fields(Active)}
    archived_active_fields = {field.name for field in dataclasses.fields(ArchivedActive)}
    assert active_fields <= archived_active_fields
    # 快照没有的 5 项瞬时事实，存档里必须**一个不少**（不存 → 恢复后第一手
    # 放置的判定就变了 → 分数与消行分类都可能不同）。
    assert archived_active_fields - active_fields == {
        "lastMoveWasRotation",
        "kickIndex",
        "kickOffset",
        "lastCause",
        "lockResets",
        "lockResetPending",
    }


def test_the_archive_is_independent_of_the_live_kernel() -> None:
    """导出之后内核继续跑，**存档不变**（深拷贝 · ADR-058）。

    **不拷的话**，恢复之后内核改一下棋盘，存档里的棋盘也跟着改——那份存档
    就不再是"那一刻的局面"，自包含当场失效。
    """
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    driver = KernelArchiveDriver(assembly.kernel)
    archive = driver.export()
    before = archive_to_json(archive)

    _play(assembly, _started(assembly), 5)

    assert archive_to_json(archive) == before
    assert archive_to_json(driver.export()) != before


# ---------------------------------------------------------------------------
# 验证 2：恢复后逐帧全等
# ---------------------------------------------------------------------------


def test_the_restored_game_matches_the_uninterrupted_one_frame_by_frame() -> None:
    """存档 → 恢复 → 再跑 N 帧 → 与"不中断直接跑 N 帧"**逐帧指纹全等**。

    **恢复进同一个内核**（"存档后接着玩"这条路径）：内核的随机源是**同一份
    实例**，取值计数接着往上走——所以连事件流都逐条相等。
    """
    baseline = _new_assembly()
    baseline_fingerprints = _play(baseline, _started(baseline), PRE_FRAMES + POST_FRAMES)

    assembly = _new_assembly()
    loop = _started(assembly)
    _play(assembly, loop, PRE_FRAMES)
    driver = KernelArchiveDriver(assembly.kernel)
    archive = archive_from_json(archive_to_json(driver.export()))
    driver.restore(archive)
    tail = _play(assembly, loop, POST_FRAMES)

    assert tail == baseline_fingerprints[PRE_FRAMES:]


def test_the_restored_game_into_a_fresh_kernel_matches_frame_by_frame() -> None:
    """恢复到**全新内核**也逐帧全等（"载入一份存档"这条路径）。

    **这是 FR-10.2 的硬判据**：存档必须能**独立**恢复——不靠"上一个内核还
    在内存里"。

    **为什么种子要对得上**：存档头里带着种子与规则集标识，恢复时内核要核对
    ——**用存档里的种子建内核**才有意义（对不上当场报错，见另一条用例）。
    """
    baseline = _new_assembly()
    baseline_fingerprints = _play(baseline, _started(baseline), PRE_FRAMES + POST_FRAMES)

    source = _new_assembly()
    _play(source, _started(source), PRE_FRAMES)
    archive = archive_from_json(archive_to_json(KernelArchiveDriver(source.kernel).export()))

    fresh = _new_assembly(seed=archive.seed)
    loop = _restored_loop(fresh, archive)
    tail = _play(fresh, loop, POST_FRAMES)

    assert tail == baseline_fingerprints[PRE_FRAMES:]


def test_the_restored_game_survives_a_bag_refill() -> None:
    """恢复之后**跨过取片袋补货**仍然逐帧全等（随机源状态真的被用上了）。

    **为什么这条要跑 320 帧**：随机源**只在取片袋补货时**才被取用——一袋 7
    块方块，第 237 帧才补第二袋。**窗口不够长，随机源状态就永远没被碰过**，
    "存档里带没带 `rngState`"这条判据就**测不出来**（反向验证 A 就是这么被
    漏掉的：短窗口下把 `rngState` 从恢复路径里去掉，用例照样全绿）。

    **恢复进全新内核**：这条路径才真正依赖存档里的随机源状态——新内核的随机
    源是初始状态，不恢复的话，第 237 帧补货时抽到的袋序就与未中断时不同，
    **后面的每一帧都可能不同**（FR-10.2）。
    """
    baseline = _new_assembly()
    baseline_fingerprints = _play(
        baseline, _started(baseline), REFILL_PRE_FRAMES + REFILL_POST_FRAMES
    )

    source = _new_assembly()
    _play(source, _started(source), REFILL_PRE_FRAMES)
    archive = archive_from_json(archive_to_json(KernelArchiveDriver(source.kernel).export()))

    fresh = _new_assembly(seed=archive.seed)
    loop = _restored_loop(fresh, archive)
    tail = _play(fresh, loop, REFILL_POST_FRAMES)

    assert tail == baseline_fingerprints[REFILL_PRE_FRAMES:]


def test_the_snapshot_after_a_restore_matches_the_uninterrupted_one() -> None:
    """恢复之后的**快照逐字段相等**（不只是棋盘指纹相等）。

    **指纹只覆盖棋盘内容**——分数、连击、等级、暂存槽、取片序列、计时器
    都不在指纹里。这一条把它们**逐字段**也比上。
    """
    baseline = _new_assembly()
    baseline_loop = _started(baseline)
    _play(baseline, baseline_loop, PRE_FRAMES)

    assembly = _new_assembly()
    loop = _started(assembly)
    _play(assembly, loop, PRE_FRAMES)
    driver = KernelArchiveDriver(assembly.kernel)
    driver.restore(archive_from_json(archive_to_json(driver.export())))
    _play(assembly, loop, POST_FRAMES)

    _play(baseline, baseline_loop, POST_FRAMES)
    assert assembly.kernel.snapshot() == baseline.kernel.snapshot()


def test_the_event_streams_match_after_a_restore() -> None:
    """恢复之后的**事件流逐条相等**（NFR-7：恢复路径逐条有事件）。

    比的是**类型 · 帧号 · 负载 · 元数据**——**不含全局序号**（序号跨运行
    必然不同，那不是判据）。

    **这条比指纹更严**：指纹只看棋盘，事件流看的是**内核有没有在该发事件的
    地方发事件**——漏发一条、多发一条、负载差一点，都会被抓住。
    """
    baseline = _new_assembly()
    baseline_events = _collect(baseline)
    baseline_loop = _started(baseline)
    _play(baseline, baseline_loop, PRE_FRAMES + POST_FRAMES)

    assembly = _new_assembly()
    events = _collect(assembly)
    loop = _started(assembly)
    _play(assembly, loop, PRE_FRAMES)
    driver = KernelArchiveDriver(assembly.kernel)
    driver.restore(archive_from_json(archive_to_json(driver.export())))
    _play(assembly, loop, POST_FRAMES)

    baseline_tail = [_event_shape(item) for item in baseline_events[PRE_FRAMES:]]
    restored_tail = [_event_shape(item) for item in events[PRE_FRAMES:]]
    assert restored_tail == baseline_tail
    # 恢复之后的这一段**一条事件都没有少**。
    assert len(restored_tail) > 0


# ---------------------------------------------------------------------------
# 验证 3 / 4 / 5：随机源状态 · 版本号 · 未知版本
# ---------------------------------------------------------------------------


def test_the_archive_carries_the_random_source_state() -> None:
    """存档里**必须带随机源状态**（FR-10.1 · 反向验证 A 的正向断言）。

    **不存 → 恢复后的随机序列从头开始 → 后续每一帧都可能不同**。这条用例
    守住"存档里确实有它"，反向验证（把它从恢复路径里去掉）会让
    「恢复后逐帧全等」那条用例变红。
    """
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    archive = KernelArchiveDriver(assembly.kernel).export()

    # 扁平化状态 = `(version, *state)`（ADR-010 / ADR-011 的口径 1）。
    assert len(archive.rngState) > 1
    assert archive.rngState[0] == 3
    raw = json.loads(archive_to_json(archive))
    assert raw["rngState"] == list(archive.rngState)


def test_an_archive_without_a_version_is_rejected() -> None:
    """**去掉版本号** → 明确报错（NFR-9 · 反向验证 B 的正向断言）。

    **版本号是格式的身份证**：没有它，一份新格式的存档会被旧版解析器当成
    旧格式读——**静默分叉**比报错难查得多。
    """
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    raw = json.loads(archive_to_json(KernelArchiveDriver(assembly.kernel).export()))
    del raw["version"]

    with pytest.raises(ValueError, match="不认识存档格式版本"):
        archive_from_json(json.dumps(raw))


def test_an_unknown_archive_version_is_rejected() -> None:
    """喂一个**不认识的头** → 明确报错（FR-10.3 · NFR-9）。

    **不猜、不静默降级**：版本对不上当场抛错。
    """
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    raw = json.loads(archive_to_json(KernelArchiveDriver(assembly.kernel).export()))
    raw["version"] = "v999"

    with pytest.raises(ValueError, match="不认识存档格式版本"):
        archive_from_json(json.dumps(raw))


def test_the_current_archive_version_is_accepted() -> None:
    """本版存档**能被自己读回来**（版本号不是摆设）。"""
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    raw = json.loads(archive_to_json(KernelArchiveDriver(assembly.kernel).export()))
    assert raw["version"] == ARCHIVE_VERSION


def test_an_archive_missing_a_field_is_rejected() -> None:
    """存档**少一个字段** → 明确报错（缺字段 = 恢复后那一项是错的）。"""
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    raw = json.loads(archive_to_json(KernelArchiveDriver(assembly.kernel).export()))
    del raw["state"]["gravityTimer"]

    with pytest.raises(ValueError, match="少了字段"):
        archive_from_json(json.dumps(raw))


# ---------------------------------------------------------------------------
# 验证 6：存档 ≠ 录像
# ---------------------------------------------------------------------------


def test_the_archive_format_is_not_the_recording_format() -> None:
    """**存档 ≠ 录像**：两种格式不共用一套（plan-M2 §S10 的验证 6）。

    两条硬断言：

    - **形态不同**：存档是**一个 JSON 文档**（`json.loads` 读得出**一个
      对象**），录像是 **JSON Lines**（一行一条，`json.loads` 读第一行就
      够不着整份）；
    - **内容不同**：存档带**棋盘与随机源状态**，录像带**命令序列**——录像
      里**没有**棋盘，存档里**没有**命令。

    **混用会让"恢复"变成"重放"**：重放依赖命令序列，而**存档必须能独立
    恢复**（FR-10.1）。
    """
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    archive = KernelArchiveDriver(assembly.kernel).export()
    archive_text = archive_to_json(archive)
    recording_text = recording_to_jsonl(
        Recording(
            seed=SEED,
            rule="standard",
            commands=(RecordedCommand(frame=1, type="hardDrop", args=(), source="host"),),
        )
    )

    archive_raw = json.loads(archive_text)
    assert isinstance(archive_raw, dict)
    assert "state" in archive_raw
    assert "rngState" in archive_raw
    assert "board" in archive_raw["state"]
    assert "commands" not in archive_raw

    recording_lines = [line for line in recording_text.splitlines() if line.strip()]
    recording_head = json.loads(recording_lines[0])
    assert "commands" not in recording_head
    assert "state" not in recording_head
    assert "rngState" not in recording_head
    # **录像是一行一条**：整份不是单个 JSON 文档。
    with pytest.raises(json.JSONDecodeError):
        json.loads(recording_text)
    assert len(recording_lines) == 2


def test_the_archive_and_the_recording_use_different_filenames() -> None:
    """存档与录像**不是同一种文件**（扩展名与前缀都不同）。"""
    from tetris_kernel.systems.replay import archive_filename, recording_filename

    assert archive_filename("standard") != recording_filename("standard")
    assert archive_filename("standard").endswith(".json")
    assert recording_filename("standard").endswith(".jsonl")


# ---------------------------------------------------------------------------
# 恢复的前置条件：规则集 / 种子 / 尺寸
# ---------------------------------------------------------------------------


def test_restoring_into_a_kernel_with_another_rule_is_rejected() -> None:
    """**规则集对不上** → 当场报错（换规则集恢复不是同一局）。

    **不猜、不静默降级**（NFR-9）。
    """
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    archive = KernelArchiveDriver(assembly.kernel).export()

    other = assemble(seed=archive.seed, rule_id="other", observability_enabled=False)
    with pytest.raises(ValueError, match="规则集"):
        KernelArchiveDriver(other.kernel).restore(archive)


def test_restoring_into_a_kernel_with_another_seed_is_rejected() -> None:
    """**种子对不上** → 当场报错（这份存档不是这个内核的）。"""
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    archive = KernelArchiveDriver(assembly.kernel).export()

    other = _new_assembly(seed=SEED + 1)
    with pytest.raises(ValueError, match="种子"):
        KernelArchiveDriver(other.kernel).restore(archive)


def test_the_kernel_refuses_an_archive_with_a_broken_board_size() -> None:
    """**棋盘尺寸对不上** → 当场报错（尺寸不符的棋盘碰撞判定全错）。"""
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    archive = KernelArchiveDriver(assembly.kernel).export()
    broken = Archive(
        seed=archive.seed,
        rule=archive.rule,
        rngState=archive.rngState,
        state=dataclasses.replace(
            archive.state,
            board=ArchivedBoard(
                width=archive.state.board.width,
                height=archive.state.board.height,
                hiddenRows=archive.state.board.hiddenRows,
                cells=archive.state.board.cells[:-1],
            ),
        ),
    )

    with pytest.raises(ValueError, match="棋盘"):
        KernelArchiveDriver(assembly.kernel).restore(broken)


def test_the_archive_module_does_not_import_the_kernel_layer() -> None:
    """存档格式**不认识内核**（系统层不碰内核的内部结构 · 与 S9 同一条纪律）。

    **映射在宿主层**（`host/archive.py`）——系统层只管"这份数据长什么样"。

    **只看 import 语句**（用 `ast` 解析）——文档字符串里提到内核是**说明**，
    不是依赖。
    """
    import ast
    import inspect

    import tetris_kernel.systems.replay.archive as archive_module

    tree = ast.parse(inspect.getsource(archive_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert imported, "这个模块总得 import 点什么"
    assert all("kernel" not in name and "host" not in name for name in imported)


def test_the_archive_holds_the_five_transient_facts_of_the_active_piece() -> None:
    """存档里的活动方块**带着 5 项瞬时事实**（不存 → 恢复后判定就变了）。

    这条用例**逼出那 5 项**：先让方块**转一次**（`lastMoveWasRotation` 与
    踢墙信息才有值），存档，再看存档里那几项是不是真的有值。
    """
    assembly = _new_assembly()
    kernel = assembly.kernel
    loop = _started(assembly)
    kernel.submit(Command(type="rotateCW", source=SOURCE))
    kernel.run_rounds(lambda drafts: None)
    loop.run_frame()

    archive = KernelArchiveDriver(kernel).export()
    active = archive.state.active
    assert active is not None
    assert active.lastMoveWasRotation is True
    assert active.lastCause == "command"
    assert active.lockResets >= 0
    assert isinstance(active.lockResetPending, bool)
    assert active.kickIndex is None or isinstance(active.kickIndex, int)
    assert active.kickOffset is None or len(active.kickOffset) == 2


def test_the_archive_driver_does_not_touch_the_kernel_state() -> None:
    """导出是**纯读**：内核一个字节都没改（导出前后快照完全相同）。"""
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    kernel = assembly.kernel
    before = kernel.snapshot()

    KernelArchiveDriver(kernel).export()
    KernelArchiveDriver(kernel).export_text()

    assert kernel.snapshot() == before


def test_a_restored_kernel_keeps_its_own_random_source_counter() -> None:
    """恢复到**同一个内核**时，随机源的取值计数**接着往上走**。

    `KernelRng` 的 docstring 明写"存档恢复后它继续往上走"——这条用例守住
    那个承诺（**取值序号是实例内记账**，不是局面状态）。

    **恢复进新内核时那个计数从 0 起**——那是新实例自己的账，与局面无关，
    FR-10.2 的判据（逐帧指纹全等）不受影响。
    """
    assembly = _new_assembly()
    kernel = assembly.kernel
    loop = _started(assembly)
    _play(assembly, loop, 5)

    driver = KernelArchiveDriver(kernel)
    driver.restore(archive_from_json(archive_to_json(driver.export())))
    _play(assembly, loop, 1)

    rng_events = [item for item in _event_stream(assembly) if item.type == "rng:next"]
    assert rng_events, "这一帧里应当有 rng:next 事件"
    assert rng_events[-1].payload[1][1] > 0


def _event_stream(assembly: Assembly) -> list[Envelope]:
    """取总线的**全部信封**（总线自带环形历史）。"""
    return list(assembly.bus.history())


def test_the_kernel_archive_is_exported_without_touching_the_random_source() -> None:
    """导出存档**不消耗随机数**（导出不该改变这局的走向）。"""
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    kernel = assembly.kernel
    before = kernel.export_state().rng_state

    KernelArchiveDriver(kernel).export()
    KernelArchiveDriver(kernel).export()

    assert kernel.export_state().rng_state == before


def test_a_kernel_archive_is_a_frozen_dataclass() -> None:
    """存档点是**冻结的**（不可变）——它是一个"某一刻的局面"。"""
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    archive = assembly.kernel.export_state()
    with pytest.raises(dataclasses.FrozenInstanceError):
        archive.frame = 999  # type: ignore[misc]


def test_the_kernel_refuses_to_restore_into_a_running_game_without_a_piece() -> None:
    """存档说"在跑"却**没有活动方块** → 当场报错（这份存档不合法）。"""
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    archive = KernelArchiveDriver(assembly.kernel).export()
    broken = Archive(
        seed=archive.seed,
        rule=archive.rule,
        rngState=archive.rngState,
        state=dataclasses.replace(archive.state, active=None),
    )
    with pytest.raises(ValueError, match="活动方块"):
        KernelArchiveDriver(assembly.kernel).restore(broken)


def test_the_archive_is_restorable_into_a_kernel_that_never_ran() -> None:
    """**没开局的内核**也能被恢复（存档是完整的，不依赖之前的运行）。"""
    assembly = _new_assembly()
    _play(assembly, _started(assembly), 5)
    archive = archive_from_json(archive_to_json(KernelArchiveDriver(assembly.kernel).export()))

    fresh = _new_assembly()
    KernelArchiveDriver(fresh.kernel).restore(archive)

    assert fresh.kernel.phase == "running"
    assert fresh.kernel.frame == archive.state.frame
    assert fresh.kernel.snapshot().board.structureFingerprint == (
        assembly.kernel.snapshot().board.structureFingerprint
    )


def test_the_kernel_is_the_only_writer_of_the_state() -> None:
    """**宿主不写内核的私有状态**（架构 §7.1）。

    存档驱动器只做翻译：把存档交给内核的 `restore_state()`，**不碰内核的
    字段**。这条用例断言驱动器**没有**任何写内核状态的入口。
    """
    driver = KernelArchiveDriver(_new_assembly().kernel)
    forbidden = {"_state", "_frame", "_rng", "_lifecycle"}
    assert forbidden.isdisjoint({name for name in dir(driver) if not name.startswith("__")})
    assert isinstance(driver.kernel, Kernel)
