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

## 为什么还要验"装配层一个事件都不发"

那是宿主的一条不变量（"造零件不许造事件"）。它单列在
`tests/test_host_loop.py::test_the_assembly_layer_publishes_nothing_even_with_observability_enabled`
——**放在那条用例旁边**，不重复一份。
"""

from __future__ import annotations

import json

from tetris_kernel.host import Assembly, HeadlessLoop, assemble
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
    """
    captured: list[bytes] = []
    assembly = assemble(seed=SEED, sink=captured.append)
    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frame()

    assert captured, "sink 一个字节都没收到——装配层没把 sink 交给系统"

    lines: list[str] = []
    for chunk in captured:
        lines.extend(chunk.decode("utf-8").splitlines())

    entries = [json.loads(line) for line in lines]
    # 日志条目：级别 · 频道 · 消息 · 数据（经 sink 的 JSON 行）。
    logged = [item for item in entries if "message" in item]
    assert logged, "日志系统没往 sink 写条目"
    assert any(item["message"] == "clock:tick" for item in logged), (
        f"日志里没有 tick：{[item['message'] for item in logged][:5]}"
    )
    # 指标采样：一帧一条。
    sampled = [item for item in entries if "metrics" in item]
    assert sampled, "指标系统没往 sink 写采样"
    assert sampled[0]["metrics"], "采样是空的"
