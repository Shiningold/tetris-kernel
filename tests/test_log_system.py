"""日志系统测试（M2-S6 · FR-7.1 · ADR-046）。

覆盖 plan-M2.md S6 的验证方案：

1. **普通订阅者**：**没有任何写内核状态的路径**（M2-D1）——总线注入的
   `view_provider` **一次都没被调用**（机制级：日志系统一旦改成
   `ctx.view()` 查内核状态，这条立刻红）；
2. **级别映射**：同一事件在两种 `level_map` 配置下产出**不同级别**
   （去掉级别映射、硬编码级别 → 这条红）；
3. **多输出目标**：两个目标都收到 · 一个目标不收某个频道时另一个不受影响；
4. **落盘经 sink**：sink 收到的是**字节**（不是路径、不是字符串），
   日志系统**一个文件都不碰**（M2-D2 · ADR-064）；
5. **不自激**：自产的 `log:entry` 不进日志；
6. **多频道**：一个事件进两个频道 → **两条条目**；
7. **频道过滤**与**阈值过滤**（降级第 ② 档 · ADR-031）；
8. **未知级别当场报错**（不糊弄过去）。

**级别映射的两条断言（2 与反向验证 B）是同一件事的两面**：映射表是本系统的
**配置**，配置变了产出就该变——硬编码级别等于把配置变成摆设。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import tetris_kernel.systems.log as log_package
from tetris_kernel.bus.bus import EventBus
from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.kernel.drafts import EventDraft, make_draft
from tetris_kernel.systems.log import (
    LOG_ENTRY_EVENT,
    LogSystem,
    MemoryTarget,
    SinkTarget,
)

# ---------------------------------------------------------------------------
# 夹具与替身
# ---------------------------------------------------------------------------

#: 几种事件的**占位负载**（字段名与顺序照抄契约声明）。
#:
#: **走 `make_draft` 造草稿**：负载字段与契约逐条相等由它当场校验——测试里
#: 用假负载造事件的话，契约改了测试也发现不了。
_SAMPLE_PAYLOADS: dict[str, tuple[tuple[str, object], ...]] = {
    "clock:tick": (("frame", 1), ("stepMs", 50)),
    "kernel:error": (
        ("subscriber", "坏订阅者"),
        ("eventType", "piece:locked"),
        ("eventSeq", 1),
        ("error", "RuntimeError: 炸了"),
    ),
    "piece:locked": (
        ("pieceType", "T"),
        ("rotation", 0),
        ("x", 3),
        ("y", 17),
        ("cells", ((3, 17),)),
        ("tSpin", "none"),
        ("cause", "ground"),
    ),
    "board:changed": (("cellChanges", ((1, 2, 1),)), ("cause", "lock")),
    "board:lines-cleared": (
        ("rowIndexes", (17,)),
        ("count", 1),
        ("tSpin", "none"),
        ("classKey", "single"),
    ),
}


def _draft(event_type: str, frame: int = 0) -> EventDraft:
    """造一条**合法**草稿（负载字段与契约逐条相等）。"""
    payload = _SAMPLE_PAYLOADS[event_type]
    return make_draft(event_type, payload, frame)  # type: ignore[arg-type]


def _bus(**kwargs: object) -> EventBus[object]:
    """一条不带视图的总线。"""
    return EventBus[object](**kwargs)  # type: ignore[arg-type]


def _counting_bus(calls: list[int]) -> EventBus[object]:
    """一条**带计数视图**的总线——`view_provider` 每被调一次就记一笔。"""

    def provider() -> object:
        calls.append(1)
        return object()

    return EventBus[object](view_provider=provider)


# ---------------------------------------------------------------------------
# 普通订阅者（M2-D1）
# ---------------------------------------------------------------------------


def test_the_log_system_never_touches_the_kernel_state() -> None:
    """普通订阅者：**没有任何写内核状态的路径**（M2-D1）。

    **机制级断言**：总线注入的 `view_provider` **一次都没被调用**——
    日志系统一旦改成 `ctx.view()` 查内核状态，这条立刻红。
    """
    calls: list[int] = []
    bus = _counting_bus(calls)
    target = MemoryTarget()
    LogSystem(bus, (target,))

    bus.publish(_draft("piece:locked", frame=7))

    assert calls == [], "日志系统取了内核视图——它必须是纯订阅者"
    assert len(target.entries) == 1, "事件照样进了日志"


def test_the_log_package_does_not_import_the_filesystem_or_the_kernel_state() -> None:
    """落盘只走 sink（M2-D2）· 只订阅事件不碰内核状态（M2-D1）。

    **机制级**：AST 扫描 `systems/log` 的**全部源文件**——

    - 不 import `pathlib` / `os` / `shutil` / `io`（自己 `open()` 就等于
      自己管路径）；
    - 不 import `kernel.core` / `kernel.snapshot`（内核状态的地盘）。
    """
    package_dir = Path(log_package.__file__).parent
    imported: set[str] = set()
    for source_path in sorted(package_dir.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported |= _imported_modules(tree, package="tetris_kernel.systems.log")

    roots = {name.split(".", 1)[0] for name in imported}
    assert not roots & {"pathlib", "os", "shutil", "io"}, f"日志系统碰了文件系统：{sorted(roots)}"
    assert "tetris_kernel.kernel.core" not in imported, "日志系统 import 了内核状态"
    assert "tetris_kernel.kernel.snapshot" not in imported, "日志系统 import 了内核快照"


def _imported_modules(tree: ast.AST, package: str) -> set[str]:
    """AST 里出现的**绝对**模块名（相对导入按包路径还原）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(_absolute_module(node, package))
    return names


def _absolute_module(node: ast.ImportFrom, package: str) -> str:
    """把相对导入还原成绝对模块名（`...bus.bus` → `tetris_kernel.bus.bus`）。"""
    if node.level == 0:
        return node.module or ""
    parts = package.split(".")
    base = parts[: len(parts) - (node.level - 1)]
    if node.module:
        base = [*base, *node.module.split(".")]
    return ".".join(base)


# ---------------------------------------------------------------------------
# 级别映射（ADR-046）
# ---------------------------------------------------------------------------


def test_the_same_event_gets_different_levels_under_two_level_maps() -> None:
    """级别是**映射出来的**：同一事件在两种配置下产出**不同级别**。

    **这条断言就是反向验证 B 的判据**——去掉级别映射、硬编码级别，它必红。
    """
    first = MemoryTarget()
    first_bus = _bus()
    LogSystem(first_bus, (first,), level_map={"piece:locked": "error"})

    second = MemoryTarget()
    second_bus = _bus()
    LogSystem(second_bus, (second,), level_map={"piece:locked": "debug"})

    first_bus.publish(_draft("piece:locked", frame=1))
    second_bus.publish(_draft("piece:locked", frame=1))

    assert first.entries[0].level == "error"
    assert second.entries[0].level == "debug", "级别必须跟着映射表走"


def test_the_exact_event_type_wins_over_the_namespace() -> None:
    """**精确优先于命名空间**（ADR-046）。

    先查命名空间会让精确映射永远轮不到——`piece:spawned` 是 `debug`，
    `piece:hold-denied` 可能是 `warn`，同一个命名空间不是一个量级。
    """
    target = MemoryTarget()
    bus = _bus()
    LogSystem(bus, (target,), level_map={"piece": "info", "piece:locked": "error"})

    bus.publish(_draft("piece:locked", frame=1))

    assert target.entries[0].level == "error"


def test_the_namespace_is_used_when_there_is_no_exact_match() -> None:
    """查不到精确项 → 落到**命名空间**。"""
    target = MemoryTarget()
    bus = _bus()
    LogSystem(bus, (target,), level_map={"piece": "warn"})

    bus.publish(_draft("piece:locked", frame=1))

    assert target.entries[0].level == "warn"


def test_the_default_level_is_used_when_nothing_matches() -> None:
    """精确与命名空间都查不到 → 落到**兜底级别**。"""
    target = MemoryTarget()
    bus = _bus()
    LogSystem(bus, (target,), level_map={}, default_level="warn")

    bus.publish(_draft("piece:locked", frame=1))

    assert target.entries[0].level == "warn"


def test_an_unknown_level_is_rejected_on_construction() -> None:
    """域外级别**当场报错**（不糊弄过去）。"""
    bus = _bus()

    with pytest.raises(ValueError):
        LogSystem(bus, (), level_map={"piece:locked": "verbose"})
    with pytest.raises(ValueError):
        LogSystem(bus, (), default_level="verbose")
    with pytest.raises(ValueError):
        LogSystem(bus, (), min_level="verbose")


def test_an_unknown_level_is_rejected_when_the_threshold_is_set() -> None:
    """调阈值时也当场校验。"""
    bus = _bus()
    system = LogSystem(bus, ())

    with pytest.raises(ValueError):
        system.set_min_level("verbose")

    assert system.min_level == "trace", "被拒的值不许生效"


# ---------------------------------------------------------------------------
# 阈值过滤（降级第 ② 档 · ADR-031）
# ---------------------------------------------------------------------------


def test_the_threshold_filters_out_the_lighter_levels() -> None:
    """阈值是**系统级**的：比阈值轻的级别不进日志。"""
    target = MemoryTarget()
    bus = _bus()
    LogSystem(bus, (target,), level_map={"piece:locked": "debug"}, min_level="warn")

    bus.publish(_draft("piece:locked", frame=1))

    assert target.entries == (), "debug 比 warn 轻，被阈值挡住了"


def test_the_threshold_keeps_the_levels_at_or_above_it() -> None:
    """等于阈值的级别**要留**（阈值是"最轻留哪个"）。"""
    target = MemoryTarget()
    bus = _bus()
    LogSystem(bus, (target,), level_map={"piece:locked": "warn"}, min_level="warn")

    bus.publish(_draft("piece:locked", frame=1))

    assert len(target.entries) == 1


def test_raising_the_threshold_takes_effect_immediately() -> None:
    """调阈值**立刻生效**——下一个事件就按新阈值走。"""
    target = MemoryTarget()
    bus = _bus()
    system = LogSystem(bus, (target,), level_map={"piece:locked": "info"})

    bus.publish(_draft("piece:locked", frame=1))
    system.set_min_level("error")
    bus.publish(_draft("piece:locked", frame=2))

    assert len(target.entries) == 1, "第二条被新阈值挡住了"
    assert target.entries[0].data == (
        ("eventType", "piece:locked"),
        ("frame", 1),
        ("seq", target.entries[0].data[2][1]),
    )


# ---------------------------------------------------------------------------
# 多输出目标
# ---------------------------------------------------------------------------


def test_two_targets_both_receive_the_entry() -> None:
    """多个输出目标：**两个都收到**。"""
    first = MemoryTarget("甲")
    second = MemoryTarget("乙")
    bus = _bus()
    LogSystem(bus, (first, second))

    bus.publish(_draft("piece:locked", frame=1))

    assert len(first.entries) == 1
    assert len(second.entries) == 1
    assert first.entries[0] == second.entries[0], "两个目标吃的是同一条条目"


def test_one_target_not_accepting_a_channel_leaves_the_other_untouched() -> None:
    """一个目标不收某频道 → **另一个照收**（目标之间互不影响）。"""
    every = MemoryTarget("全收")
    only_board = MemoryTarget("只收 board", channels=frozenset({"board"}))
    bus = _bus()
    LogSystem(bus, (every, only_board))

    bus.publish(_draft("piece:locked", frame=1))

    assert len(every.entries) == 1
    assert only_board.entries == (), "piece 频道不归它收"

    bus.publish(_draft("board:changed", frame=2))

    assert len(every.entries) == 2
    assert len(only_board.entries) == 1, "board 频道归它收"


def test_the_log_system_works_without_any_target() -> None:
    """**没有输出目标**也不报错——条目照样发到总线上。"""
    received: list[Envelope] = []
    bus = _bus()
    bus.subscribe(LOG_ENTRY_EVENT, received.append)
    LogSystem(bus, ())

    bus.publish(_draft("piece:locked", frame=1))

    assert len(received) == 1, "没有输出目标也照样发事件"


# ---------------------------------------------------------------------------
# 落盘经 sink（M2-D2 · ADR-064）
# ---------------------------------------------------------------------------


def test_the_sink_receives_the_bytes() -> None:
    """落盘经 sink：sink 收到的是**字节**（一行一条 JSON）。"""
    captured: list[bytes] = []
    bus = _bus()
    LogSystem(bus, (SinkTarget(captured.append),))

    bus.publish(_draft("kernel:error", frame=3))

    assert len(captured) == 1
    assert isinstance(captured[0], bytes), "交给 sink 的必须是字节"
    assert captured[0].endswith(b"\n"), "一行一条（JSON 行）"
    record = json.loads(captured[0].decode("utf-8"))
    assert record["level"] == "error"
    assert record["channel"] == "kernel"
    assert record["message"] == "kernel:error"
    assert record["data"]["eventType"] == "kernel:error"
    assert record["data"]["frame"] == 3


def test_the_log_system_never_touches_the_filesystem(tmp_path: Path) -> None:
    """落盘只走 sink：日志系统**一个文件都不碰**（路径是宿主的事）。

    **行为级**：跑完一整轮后，临时目录里**一个文件都没多**。
    """
    before = set(tmp_path.rglob("*"))
    captured: list[bytes] = []
    bus = _bus()
    LogSystem(bus, (SinkTarget(captured.append),))

    bus.publish(_draft("piece:locked", frame=1))
    bus.publish(_draft("board:changed", frame=2))

    assert set(tmp_path.rglob("*")) == before, "日志系统碰了文件系统"
    assert len(captured) == 2, "字节确实交给了 sink"


# ---------------------------------------------------------------------------
# 不自激
# ---------------------------------------------------------------------------


def test_the_log_entries_do_not_log_themselves() -> None:
    """自产的 `log:entry` **不进日志**（否则无限自激）。"""
    target = MemoryTarget()
    bus = _bus()
    LogSystem(bus, (target,))

    bus.publish(_draft("piece:locked", frame=1))

    assert len(target.entries) == 1, "一条事件一条条目——不是两条"


def test_the_log_entry_goes_to_the_bus_as_an_event() -> None:
    """日志条目**同时**进总线事件流（面板、轨迹导出都看得见它）。"""
    received: list[Envelope] = []
    bus = _bus()
    bus.subscribe(LOG_ENTRY_EVENT, received.append)
    LogSystem(bus, (MemoryTarget(),))

    bus.publish(_draft("piece:locked", frame=4))

    assert len(received) == 1
    payload = dict(received[0].payload)
    assert payload["level"] == "info"
    assert payload["channel"] == "piece"
    assert payload["message"] == "piece:locked"
    assert dict(payload["data"])["eventType"] == "piece:locked"
    assert dict(payload["data"])["frame"] == 4
    assert received[0].frame == 4, "条目跟着它发生的那一帧走"


def test_the_log_entry_carries_the_contract_fields_in_order() -> None:
    """`log:entry` 的负载字段**与契约逐条相等**（顺序也算）。"""
    received: list[Envelope] = []
    bus = _bus()
    bus.subscribe(LOG_ENTRY_EVENT, received.append)
    LogSystem(bus, (MemoryTarget(),))

    bus.publish(_draft("piece:locked", frame=1))

    assert received[0].type == "log:entry"
    assert tuple(name for name, _ in received[0].payload) == (
        "level",
        "channel",
        "message",
        "data",
    )


# ---------------------------------------------------------------------------
# 频道
# ---------------------------------------------------------------------------


def test_an_event_goes_to_one_entry_per_channel() -> None:
    """一个事件进两个频道 → **两条条目**（一个频道一条）。"""
    target = MemoryTarget()
    bus = _bus()
    LogSystem(bus, (target,), channel_map={"board:lines-cleared": ("board", "score")})

    bus.publish(_draft("board:lines-cleared", frame=1))

    assert len(target.entries) == 2
    assert [entry.channel for entry in target.entries] == ["board", "score"]


def test_the_default_channel_is_the_own_namespace() -> None:
    """默认频道 = **自己的命名空间**（"按命名空间过滤"是契约给的能力）。"""
    target = MemoryTarget()
    bus = _bus()
    LogSystem(bus, (target,))

    bus.publish(_draft("piece:locked", frame=1))

    assert target.entries[0].channel == "piece"


def test_the_namespace_channel_mapping_is_used_when_there_is_no_exact_match() -> None:
    """查不到精确项 → 落到**命名空间**的频道清单。"""
    target = MemoryTarget()
    bus = _bus()
    LogSystem(bus, (target,), channel_map={"board": ("grid", "board")})

    bus.publish(_draft("board:changed", frame=1))

    assert [entry.channel for entry in target.entries] == ["grid", "board"]


def test_an_empty_channel_list_is_rejected() -> None:
    """空频道清单当场报错——"进零个频道"等于这条事件白来了。"""
    bus = _bus()

    with pytest.raises(ValueError):
        LogSystem(bus, (), channel_map={"piece:locked": ()})


# ---------------------------------------------------------------------------
# 条目本身
# ---------------------------------------------------------------------------


def test_the_entry_is_immutable() -> None:
    """条目**不可变**——它会被交给多个目标，谁都不许就地改它。"""
    target = MemoryTarget()
    bus = _bus()
    LogSystem(bus, (target,))

    bus.publish(_draft("piece:locked", frame=1))
    entry = target.entries[0]

    with pytest.raises(AttributeError):
        entry.level = "error"  # type: ignore[misc]

    assert entry.level == "info"


def test_the_entry_data_is_sorted_when_rendered() -> None:
    """渲染时**数据按字段名排序**——同一批条目的渲染逐字可比。"""
    target = MemoryTarget()
    bus = _bus()
    LogSystem(bus, (target,))

    bus.publish(_draft("piece:locked", frame=1))

    rendered = target.entries[0]
    assert rendered.data == (
        ("eventType", "piece:locked"),
        ("frame", 1),
        ("seq", rendered.data[2][1]),
    )
