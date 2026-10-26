"""事件轨迹导出与只读事件流面板测试（M2-S11 · FR-7.3 · FR-7.4 · FR-6.4）。

覆盖 plan-M2.md S11 的验证方案：

1. **导出完整**：导出后逐条回放 → **与源事件流逐条相等**（类型 + 负载 + 序号）；
2. **只读**：导出**不改变**事件流（导出前后事件流逐条相等）；
3. **过滤**：按**命名空间**过滤 → **只返回该命名空间的事件**；**`replay:` 与
   `game:` 分开**（ADR-075 的兑现点——**过滤时不会搅在一起**）；
4. **暂停**：暂停后**不再有新事件进入视图**（但**内核照跑**）；
5. **反向验证 A**：把过滤改成"按事件名模糊匹配" → 用例**必须红**（过滤是
   **按命名空间**的）；
6. **反向验证 B**：让导出**顺手改一下事件内容** → 用例**必须红**（只读）；
7. **CLI**：`tetris inspect` 真跑并能按命名空间过滤。

**第 5、6 条的判据是本文件里的正向断言**：模糊匹配**必须匹配不到**
（`by_namespaces(("reco",))` 是空的），导出**必须逐条还原**（读回来的信封与
源信封相等）。它们与实现无关——实现怎么写，这两条都成立。
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import Any

from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.contracts.events import namespace_of
from tetris_kernel.host.assembly import assemble
from tetris_kernel.host.cli import main
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.systems.metrics.trace import EventPanel, trace_from_jsonl, trace_to_jsonl

#: 固定种子（**同一个种子 → 同一串事件**）。
SEED = 20261026

#: 跑多少帧。
FRAMES = 5


def _panel(
    frames: int = FRAMES,
    *,
    observability: bool = False,
    limit: int | None = None,
    pause_at: int | None = None,
) -> tuple[EventPanel[Any], HeadlessLoop]:
    """装一份装配 + 一个面板，跑若干帧。

    **`observability=True` 才看得见 `replay:` 事件**——录制系统是它唯一的
    产出者（ADR-075）。默认关掉是为了让"导出逐条相等"那条判据干净。
    """
    assembly = assemble(seed=SEED, observability_enabled=observability)
    # **面板要在事件开始流动之前订阅**（晚订阅的从 `bus.history()` 里取）。
    panel = EventPanel(assembly.bus, limit=limit, pause_at=pause_at)
    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frames(frames)
    return panel, loop


# ---------------------------------------------------------------------------
# 验证 1：导出完整
# ---------------------------------------------------------------------------


def test_the_exported_trace_replays_event_by_event() -> None:
    """导出后逐条回放 → **与源事件流逐条相等**（类型 + 负载 + 序号）。

    **不只是"条数一样"**：类型 · 帧号 · 负载 · 元数据 · 全局序号**逐条相等**。
    """
    panel, _ = _panel()
    source = panel.events
    assert source, "这一轮总该有事件"

    restored = trace_from_jsonl(panel.export_text())

    assert restored == source
    assert [item.type for item in restored] == [item.type for item in source]
    assert [item.frame for item in restored] == [item.frame for item in source]
    assert [item.payload for item in restored] == [item.payload for item in source]
    assert [item.metadata for item in restored] == [item.metadata for item in source]
    assert [item.seq for item in restored] == [item.seq for item in source]


def test_every_exported_line_is_one_event() -> None:
    """轨迹文件是 **JSON Lines**：**一行一条**（逐行可比）。

    **不是一个大 JSON 文档**——一行一条才能逐行 diff（与录像同一条理由，
    ADR-045）。
    """
    panel, _ = _panel()
    lines = panel.export_text().splitlines()
    assert len(lines) == len(panel.events)
    for line, envelope in zip(lines, panel.events, strict=True):
        raw = json.loads(line)
        assert raw["type"] == envelope.type
        # **逐条还原**：这一行读回来就是那一条信封。
        assert trace_from_jsonl(line) == (envelope,)


def test_the_export_can_serve_as_a_regression_baseline() -> None:
    """FR-6.4：把**事件历史**（而不是命令）当回归断言的基线。

    **两次一模一样的跑，导出逐字相同**——这就是"能当基线"的前提。
    """
    first, _ = _panel()
    second, _ = _panel()
    assert first.export_text() == second.export_text()


# ---------------------------------------------------------------------------
# 验证 2：只读
# ---------------------------------------------------------------------------


def test_the_export_does_not_change_the_event_stream() -> None:
    """导出**不改变**事件流（导出前后事件流逐条相等 · 只读导出）。

    **导出不是"顺手改一下内容"的机会**——那就不是原轨迹了。
    """
    panel, _ = _panel()
    before = panel.events
    sequence_before = panel.events[-1].seq

    panel.export_text()
    panel.export_bytes()
    panel.write_to(lambda data: None)
    trace_to_jsonl(before)

    assert panel.events == before
    assert panel.events[-1].seq == sequence_before


def test_the_panel_cannot_modify_the_events_it_holds() -> None:
    """面板**改不动**信封（信封是冻结的 · 架构 §5.1）。

    **"只读"不是靠自觉**：信封本身就是冻结的、负载是元组——**想改都改不了**。
    """
    import dataclasses

    panel, _ = _panel()
    envelope = panel.events[0]
    try:
        envelope.type = "篡改"
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("信封居然能被改——那「只读」就是假的")


def test_the_trace_module_never_opens_a_file() -> None:
    """M2-D2：落盘只走 sink——**部件不许自己 `open()`**。

    **用 AST 看代码，不看文档字符串**（文档里写着"`open()`"是说明，不是调用）。
    """
    import tetris_kernel.systems.metrics.trace as trace_module

    tree = ast.parse(inspect.getsource(trace_module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "open", "轨迹模块不许自己 open()"
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"open", "write_text", "write_bytes"}


# ---------------------------------------------------------------------------
# 验证 3：过滤
# ---------------------------------------------------------------------------


def test_the_filter_selects_by_namespace() -> None:
    """按**命名空间**过滤 → **只返回该命名空间的事件**。

    **`replay:` 与 `game:` 分开**（ADR-075 的兑现点）：两种时间轴混在一个
    命名空间里的话，过滤时会被搅在一起。
    """
    panel, _ = _panel(observability=True)
    replay_events = panel.by_namespaces(("replay",))
    game_events = panel.by_namespaces(("game",))

    assert replay_events, "开着可观测系统就该看得见 replay: 事件"
    assert game_events
    assert all(namespace_of(item.type) == "replay" for item in replay_events)
    assert all(namespace_of(item.type) == "game" for item in game_events)
    # **两组不重叠**。
    assert not {item.type for item in replay_events} & {item.type for item in game_events}


def test_the_filter_is_not_a_fuzzy_event_name_match() -> None:
    """**按事件名模糊匹配不成立**（反向验证 A 的正向断言）。

    `"reco"` 看着像 `replay:recording-started` 的前缀——**按名字模糊匹配会
    把它捞出来，按命名空间不会**（`reco` 不是任何事件类型的命名空间）。
    """
    panel, _ = _panel(observability=True)
    assert panel.by_namespaces(("replay",))
    assert panel.by_namespaces(("reco",)) == ()
    assert panel.by_namespaces(("game:started",)) == ()
    assert panel.by_namespaces(("piece", "board")) != ()


# ---------------------------------------------------------------------------
# 验证 4：暂停
# ---------------------------------------------------------------------------


def test_pausing_the_panel_stops_new_events_but_not_the_kernel() -> None:
    """暂停后**不再有新事件进入视图**（但**内核照跑**）。

    **订阅者拦不住总线，也拦不住内核**——暂停的是**视图**，不是这一局。
    """
    assembly = assemble(seed=SEED, observability_enabled=False)
    panel = EventPanel(assembly.bus)
    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frames(2)
    recorded = len(panel.events)
    assert recorded > 0
    sequence_before = assembly.bus.last_seq

    panel.pause()
    loop.run_frames(3)

    assert panel.paused is True
    assert len(panel.events) == recorded
    # **内核照跑**：帧数照涨，事件照发（只是不进视图）。
    assert loop.frames == 5
    assert assembly.bus.last_seq > sequence_before

    panel.resume()
    loop.run_frames(1)
    assert panel.paused is False
    assert len(panel.events) > recorded


def test_the_panel_can_auto_pause_at_a_frame() -> None:
    """**自动暂停**：视图只留该帧之前的事件（触发它的那条不进视图）。"""
    panel, _ = _panel(FRAMES, pause_at=2)
    assert panel.paused is True
    assert all(item.frame < 2 for item in panel.events)


# ---------------------------------------------------------------------------
# 验证 7：CLI
# ---------------------------------------------------------------------------


def test_the_inspect_command_runs_and_filters_by_namespace(capsys) -> None:
    """`tetris inspect` **真跑**并能按命名空间过滤（FR-7.3）。"""
    exit_code = main(["inspect", "--frames", "3", "--namespace", "game", "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["namespaces"] == ["game"]
    assert payload["events"] > 0
    assert all(item["type"].startswith("game:") for item in payload["trace"])


def test_the_inspect_command_shows_the_event_stream(capsys) -> None:
    """`tetris inspect` 的**人读形态**：一行一条事件。"""
    exit_code = main(["inspect", "--frames", "2", "--limit", "5"])
    assert exit_code == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) >= 5
    assert "kernel:ready" in lines[0]


def test_the_inspect_command_exports_the_complete_trace(tmp_path: Path, capsys) -> None:
    """`--export` 把**完整轨迹**落成文件（**不受显示条数上限影响**）。"""
    target = tmp_path / "trace.jsonl"
    exit_code = main(["inspect", "--frames", "3", "--export", str(target), "--limit", "1"])
    assert exit_code == 0
    capsys.readouterr()
    events = trace_from_jsonl(target.read_text(encoding="utf-8"))
    assert len(events) > 1
    assert events[0].type == "kernel:ready"


def test_the_inspect_command_can_pause_the_view(capsys) -> None:
    """`--pause-at` 暂停**视图**（内核照跑，事件照发）。"""
    exit_code = main(["inspect", "--frames", "5", "--pause-at", "2", "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["paused"] is True
    assert all(item["frame"] < 2 for item in payload["trace"])


def test_the_inspect_command_rejects_a_negative_frame_count(capsys) -> None:
    """帧数为负 → **退出码 2**（与 `sim` 同一条口径）。"""
    assert main(["inspect", "--frames", "-1"]) == 2
    assert "帧数不能为负" in capsys.readouterr().err


def test_the_play_command_is_still_a_placeholder(capsys) -> None:
    """**`play` 仍是占位**（交互式游玩属 M3–M4）。"""
    assert main(["play"]) == 0
    assert "占位" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 事件面板与信封
# ---------------------------------------------------------------------------


def test_the_panel_holds_the_envelopes_the_bus_published() -> None:
    """面板拿到的就是**总线发出去的那些信封**（不是复制的、改过的）。

    **序号是总线的唯一权威**（架构 §4.2）——面板只是收下它。
    """
    assembly = assemble(seed=SEED, observability_enabled=False)
    panel = EventPanel(assembly.bus)
    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frames(3)

    history = assembly.bus.history()
    assert panel.events == tuple(history)
    assert all(isinstance(item, Envelope) for item in panel.events)


def test_the_panel_limit_keeps_the_newest_events() -> None:
    """面板有上限时**留最新的**（环形）——旧的不是被改，是被挤出去。"""
    panel, _ = _panel(FRAMES, limit=3)
    assert len(panel.events) == 3
    assert panel.events[-1].seq > panel.events[0].seq
