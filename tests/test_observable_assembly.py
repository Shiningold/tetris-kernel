"""可观测系统的装配接线（M2 批次 C · S6–S9 装进宿主）。

## 这份文件守什么

批次 C 的三个系统（日志 · 指标 · 录像）**装进了 `host/assembly.py` 的装配表**。
接线本身也要有**机器证据**——`tests/test_storage_adapter.py` 那两条验的是
**被替换掉的假工厂**（"总开关关掉时工厂一个都不被调用"），**真表里装了什么、
装的那些是不是真的接上了总线与 sink**，没人守。

## 三条接线不变量

1. **默认装上三个系统**（S4 定的默认：可观测**默认开着**）——装配表里三个
   工厂都在，且产出的是**真的那三个系统**（不是占位对象）；
2. **总开关关掉时一个都不装**（`G2-3` 的前提）——**关的是"装配"，不是
   "订阅"**；
3. **sink 真到了系统手里**（M2-D2）——装配层给的就是宿主注入的那一个出口，
   **不另造一个**。

## M2 审查补上两条（ADR-085）

4. **三个产物各有各的出口**（审查 H1）——共用一个的话，**后写的覆盖先写的**
   （用覆盖语义写日志，文件里只剩最后一条）；
5. **局末四项由宿主注入**（审查 H2 · ADR-078）——系统层**不碰内核状态**
   （M2-D1），棋盘指纹的算法也只有内核那一份，所以**provider 只能由宿主
   给**；不注入 → 录像里没有比对基准。

## 为什么还要验"装配层一个事件都不发"

那是宿主的一条不变量（"造零件不许造事件"）。它单列在
`tests/test_host_loop.py::test_the_assembly_layer_publishes_nothing_even_with_observability_enabled`
——**放在那条用例旁边**，不重复一份。
"""

from __future__ import annotations

import json
from pathlib import Path

from support.driver import play_to_game_over
from support.policy import ReferencePolicy
from tetris_kernel.host import Assembly, HeadlessLoop, ObservableSinks, assemble, default_sinks
from tetris_kernel.systems.log import LogSystem
from tetris_kernel.systems.metrics import MetricsSystem
from tetris_kernel.systems.replay import RecordingSystem

#: 装配用的固定种子。
SEED = 20261026


def _by_type(assembly: Assembly, kind: type) -> list[object]:
    """从装配结果里挑出某一类的可观测系统。"""
    return [system for system in assembly.observable_systems if isinstance(system, kind)]


def test_the_three_observable_systems_are_assembled_by_default() -> None:
    """**默认装上三个系统**——日志 · 指标 · 录像，各一个。

    **这条就是"批次 C 的装配接线"的判据**：把装配表清空，它必红。
    """
    assembly = assemble(seed=SEED)

    assert len(_by_type(assembly, LogSystem)) == 1, "日志系统没装上"
    assert len(_by_type(assembly, MetricsSystem)) == 1, "指标系统没装上"
    assert len(_by_type(assembly, RecordingSystem)) == 1, "录像系统没装上"


def test_the_switch_off_assembles_nothing_at_all() -> None:
    """**总开关关掉时一个都不装**（`G2-3` 的前提 · ADR-031）。

    **真表**（不是替换掉的假工厂）：装配表里那三个工厂**一个都不许被调用**——
    "装配了再退订"会让 `G2-3` 假绿（系统还在跑，只是不发事件）。
    """
    assembly = assemble(seed=SEED, observability_enabled=False)

    assert assembly.observable_systems == (), "关掉开关却还装着系统"
    assert assembly.observability_enabled is False


def test_the_assembled_recorder_records_the_accepted_commands() -> None:
    """装上的录像系统**真的在录**——录的是**被接受的**命令。

    **端到端**：命令进 → 内核接受 → 事件上总线 → 录像系统录到。
    """
    assembly = assemble(seed=SEED)
    loop = HeadlessLoop(assembly)
    loop.start()

    recorders = _by_type(assembly, RecordingSystem)
    assert recorders, "录像系统没装上"
    recorder = recorders[0]

    assert [command.type for command in recorder.commands] == ["start"], (
        f"录到的命令不对：{[command.type for command in recorder.commands]}"
    )


def test_the_sink_reaches_the_log_and_the_metrics_systems() -> None:
    """**sink 真到了系统手里**（M2-D2）——落盘只有这一个出口。

    **不另造出口**：装配层给的就是宿主注入的那一个可调用对象——日志条目与
    指标采样都从它出去。

    **两个产物两个出口**（ADR-085 · 审查 H1）：日志走 `sinks.log`、指标走
    `sinks.metrics`——**共用一个的话，后写的覆盖先写的**。
    """
    logged: list[bytes] = []
    sampled: list[bytes] = []
    assembly = assemble(
        seed=SEED,
        sinks=ObservableSinks(log=logged.append, metrics=sampled.append),
    )
    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frame()

    assert logged, "sink 一个字节都没收到——装配层没把 sink 交给系统"
    assert sampled, "指标系统没往 metrics 出口写采样"

    lines: list[str] = []
    for chunk in logged:
        lines.extend(chunk.decode("utf-8").splitlines())

    entries = [json.loads(line) for line in lines]
    # 日志条目：级别 · 频道 · 消息 · 数据（经 sink 的 JSON 行）。
    assert entries, "日志系统没往 sink 写条目"
    assert any(item["message"] == "clock:tick" for item in entries), (
        f"日志里没有 tick：{[item['message'] for item in entries][:5]}"
    )
    # 指标采样：一帧一条。
    samples = [json.loads(chunk.decode("utf-8")) for chunk in sampled]
    assert samples, "指标系统没往 sink 写采样"
    assert samples[0]["metrics"], "采样是空的"


def test_the_recording_goes_to_its_own_sink() -> None:
    """**录像走自己的出口**（ADR-085 · 审查 H1）——不跟日志挤一个文件。"""
    logged: list[bytes] = []
    recorded: list[bytes] = []
    assembly = assemble(
        seed=SEED,
        sinks=ObservableSinks(log=logged.append, recording=recorded.append),
    )
    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frame()

    assert logged, "日志没写出去"
    assert recorded == [], "录像不该在局末之前落盘（周期点还没到）"


# ---------------------------------------------------------------------------
# 局末四项由宿主注入（审查 H2 · ADR-078）
# ---------------------------------------------------------------------------


def test_the_assembled_recording_carries_the_four_items() -> None:
    """装配层产出的录像**带局末四项**（审查 H2 · ADR-078）。

    **为什么这条重要**：局末四项是**四项比对的基准**——录像不带基准，
    `G2-1` 的「录一局 → 重放 → 四项全等」就**没有基准可比**，永远"通过"。
    """
    assembly = assemble(seed=SEED)
    played = play_to_game_over(assembly, ReferencePolicy())
    assert played.game_over, "这一局没顶出——用例的前置条件不成立"

    recorders = _by_type(assembly, RecordingSystem)
    assert recorders, "录像系统没装上"
    recording = recorders[0].recording()

    assert recording.outcome is not None, "录像里没有局末四项——没有比对基准"
    final = assembly.kernel.snapshot()
    assert recording.outcome.fingerprint == final.board.structureFingerprint, "棋盘指纹"
    assert recording.outcome.score == final.score, "分数"
    assert recording.outcome.lines == final.lines, "行数"
    assert recording.outcome.pieces_placed == final.piecesPlaced, "方块数"


def test_the_recording_file_on_disk_carries_the_four_items(tmp_path: Path) -> None:
    """**落盘的录像**也带局末四项（生产路径 · 审查 H2）。

    **这条是上面那条的落盘版**：报告说的是"装配层产出的录像没有局末四项"——
    **在生产路径上**（文件里），不只是内存里的对象。
    """
    sinks, _directory = default_sinks(project_root=tmp_path, home=tmp_path / "home")
    assembly = assemble(seed=SEED, sinks=sinks)
    played = play_to_game_over(assembly, ReferencePolicy())
    assert played.game_over, "这一局没顶出——用例的前置条件不成立"

    path = tmp_path / ".tetris-data" / "recording-standard.jsonl"
    assert path.exists(), "录像文件没落盘"
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    assert header["outcome"] is not None, "落盘的录像里没有局末四项——没有比对基准"
    assert header["outcome"]["fingerprint"], "局末四项里没有棋盘指纹"


def test_a_recording_without_the_provider_has_no_baseline() -> None:
    """**反向验证**：不注入 provider → 录像里**没有**局末四项。

    **这条是 H2 的反向判据**：把装配层注入 provider 那一步去掉，录像头的
    `outcome` 就是 `None`——"四项全等"没有基准可比。
    """
    assembly = assemble(seed=SEED, observability_enabled=False)
    recorder = RecordingSystem(assembly.bus, None)
    played = play_to_game_over(assembly, ReferencePolicy())
    assert played.game_over, "这一局没顶出——用例的前置条件不成立"

    assert recorder.recording().outcome is None, "没有 provider 却有基准——那基准是编的"
