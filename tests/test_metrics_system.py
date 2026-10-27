"""指标系统测试（M2-S7 · FR-7.2 · M2-9 空洞率）。

覆盖 plan-M2.md S7 的验证方案：

1. **聚合正确**：喂一段已知事件流 → **各项统计与手工数出来的逐条一致**；
2. **按帧采样**：**每帧一条** `metric:sampled`（不多不少）；
3. **落盘经 sink**：sink 收到**字节**，指标系统**一个文件都不碰**；
4. **空洞率**：构造一段有空洞的棋盘 → **空洞率与手算一致**；
5. **普通订阅者**：总线注入的 `view_provider` **一次都没被调用**（M2-D1）；
6. **不自激**：自产的 `metric:sampled` 不进指标；
7. **指标名与顺序固定**：每次采样的指标集合一样，下游才能逐条对齐。

**第 1 条与第 2 条就是反向验证 A / B 的判据**：采样改成"每 N 帧一条"、
某个口径改成"含连击奖励"，这两条必红。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import tetris_kernel.systems.metrics as metrics_package
from tetris_kernel.bus.bus import EventBus
from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.kernel.drafts import EventDraft, make_draft
from tetris_kernel.systems.metrics import (
    METRIC_NAMES,
    METRIC_SAMPLED_EVENT,
    Metrics,
    MetricsSystem,
)
from tetris_kernel.systems.metrics.aggregate import CLEAR_METRIC_NAMES

# ---------------------------------------------------------------------------
# 夹具与替身
# ---------------------------------------------------------------------------


def _bus(**kwargs: object) -> EventBus[object]:
    """一条不带视图的总线。"""
    return EventBus[object](**kwargs)  # type: ignore[arg-type]


def _counting_bus(calls: list[int]) -> EventBus[object]:
    """一条**带计数视图**的总线——`view_provider` 每被调一次就记一笔。"""

    def provider() -> object:
        calls.append(1)
        return object()

    return EventBus[object](view_provider=provider)


def _tick(frame: int) -> EventDraft:
    return make_draft("clock:tick", (("frame", frame), ("stepMs", 50)), frame)


def _score_changed(delta: int, before: int = 0) -> EventDraft:
    return make_draft(
        "score:changed",
        (("delta", delta), ("before", before), ("after", before + delta), ("cause", "clear")),
        1,
    )


def _lines_cleared(count: int, class_key: str, frame: int = 1) -> EventDraft:
    return make_draft(
        "board:lines-cleared",
        (
            ("rowIndexes", tuple(range(17, 17 + count))),
            ("count", count),
            ("tSpin", "none"),
            ("classKey", class_key),
        ),
        frame,
    )


def _locked(frame: int = 1) -> EventDraft:
    return make_draft(
        "piece:locked",
        (
            ("pieceType", "T"),
            ("rotation", 0),
            ("x", 3),
            ("y", 17),
            ("cells", ((3, 17),)),
            ("tSpin", "none"),
            ("cause", "ground"),
        ),
        frame,
    )


def _combo(combo: int, frame: int = 1) -> EventDraft:
    return make_draft("score:combo-changed", (("combo", combo),), frame)


def _moved(blocked: bool, frame: int = 1) -> EventDraft:
    return make_draft(
        "piece:moved",
        (
            ("fromX", 3),
            ("fromY", 17),
            ("toX", 4),
            ("toY", 17),
            ("blocked", blocked),
            ("cause", "command"),
        ),
        frame,
    )


def _rotated(blocked: bool, frame: int = 1) -> EventDraft:
    return make_draft(
        "piece:rotated",
        (("fromRotation", 0), ("toRotation", 1), ("direction", "cw"), ("blocked", blocked)),
        frame,
    )


def _hard_drop(frame: int = 1) -> EventDraft:
    return make_draft(
        "piece:harddrop",
        (("pieceType", "T"), ("distance", 5), ("x", 3), ("y", 17), ("cells", ((3, 17),))),
        frame,
    )


def _soft_drop(frame: int = 1) -> EventDraft:
    return make_draft("piece:softdrop", (("pieceType", "T"), ("x", 3), ("y", 16)), frame)


def _board_changed(changes: tuple[tuple[int, int, int], ...], frame: int = 1) -> EventDraft:
    return make_draft("board:changed", (("cellChanges", changes), ("cause", "lock")), frame)


def _board_collapsed(changes: tuple[tuple[int, int, int], ...], frame: int = 1) -> EventDraft:
    return make_draft("board:collapsed", (("cellChanges", changes),), frame)


def _values(sample: Metrics) -> dict[str, int | float]:
    """一次采样 → 可按指标名取值的字典。"""
    return dict(sample)


# ---------------------------------------------------------------------------
# 聚合正确（逐条断言）
# ---------------------------------------------------------------------------


def test_the_aggregates_match_hand_counting() -> None:
    """喂一段已知事件流 → **各项统计与手工数出来的逐条一致**。"""
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_score_changed(100))
    bus.publish(_lines_cleared(1, "single"))
    bus.publish(_combo(1))
    bus.publish(_moved(blocked=False))
    bus.publish(_moved(blocked=True))
    bus.publish(_rotated(blocked=False))
    bus.publish(_hard_drop())
    bus.publish(_soft_drop())
    bus.publish(_locked())
    bus.publish(_tick(1))

    values = _values(system.latest() or ())
    assert values["score"] == 100, "分数 = score:changed 的 delta 之和"
    assert values["lines"] == 1, "消行行数 = lines-cleared 的 count 之和"
    assert values["clearCount"] == 1, "消行组数 = lines-cleared 的条数"
    assert values["clear.single"] == 1, "分类消行按 classKey 计数"
    assert values["clear.double"] == 0
    assert values["piecesPlaced"] == 1, "已放置方块数 = piece:locked 的条数"
    assert values["maxCombo"] == 1, "连击峰值"
    assert values["moves"] == 1, "被阻的移动不计"
    assert values["rotations"] == 1
    assert values["hardDrops"] == 1
    assert values["softDrops"] == 1
    assert values["operations"] == 4, "操作总数 = 四项之和"
    assert values["frames"] == 1, "帧数 = 收到的 tick 数"
    assert values["scorePerPiece"] == 100.0, "每块得分 = score ÷ piecesPlaced"
    assert values["operationsPerFrame"] == 4.0, "每帧操作数 = operations ÷ frames"


def test_the_combo_peak_never_goes_down() -> None:
    """连击取**峰值**——连击断了也不该把峰值拉下来。"""
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_combo(5))
    bus.publish(_combo(0))
    bus.publish(_tick(1))

    values = _values(system.latest() or ())
    assert values["maxCombo"] == 5


def test_the_every_class_key_has_its_own_counter() -> None:
    """**分类清单取自规则层**——每个 `classKey` 都有自己的一项（初始全 0）。"""
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_tick(1))

    values = _values(system.latest() or ())
    for name in CLEAR_METRIC_NAMES:
        assert name in values, f"少了分类指标 {name}"
        assert values[name] == 0


def test_the_metric_names_and_their_order_are_fixed() -> None:
    """**指标名与顺序固定**——每次采样的指标集合一样，下游才能逐条对齐。"""
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_tick(1))
    first = system.latest()
    bus.publish(_tick(2))
    second = system.latest()

    assert first is not None
    assert second is not None
    assert tuple(name for name, _ in first) == METRIC_NAMES
    assert tuple(name for name, _ in second) == METRIC_NAMES


def test_the_denominator_of_zero_is_recorded_as_zero() -> None:
    """**分母 0 记 0.0**——不抛异常，也不记成无穷。"""
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_tick(1))

    values = _values(system.latest() or ())
    assert values["scorePerPiece"] == 0.0
    assert values["operationsPerFrame"] == 0.0
    assert values["holeRate"] == 0.0


# ---------------------------------------------------------------------------
# 按帧采样
# ---------------------------------------------------------------------------


def test_exactly_one_sample_per_frame() -> None:
    """**每帧一条** `metric:sampled`（不多不少）。"""
    bus = _bus()
    received: list[Envelope] = []
    bus.subscribe(METRIC_SAMPLED_EVENT, received.append)
    system = MetricsSystem(bus)

    for frame in range(1, 6):
        bus.publish(_tick(frame))

    assert len(received) == 5, "五个 tick 五条采样"
    assert [envelope.frame for envelope in received] == [1, 2, 3, 4, 5]
    assert len(system.samples) == 5


def test_the_events_between_ticks_do_not_produce_samples() -> None:
    """**只有 tick 触发采样**——帧内的事件不额外产采样。"""
    bus = _bus()
    received: list[Envelope] = []
    bus.subscribe(METRIC_SAMPLED_EVENT, received.append)
    MetricsSystem(bus)

    bus.publish(_score_changed(10))
    bus.publish(_locked())
    bus.publish(_board_changed(((0, 0, 1),)))
    bus.publish(_tick(1))

    assert len(received) == 1


def test_the_sample_carries_the_accumulated_values_at_the_frame_boundary() -> None:
    """**采样值 = 进入该帧时的累计值**（tick 在帧首，帧推进还没跑）。"""
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_score_changed(10))
    bus.publish(_tick(1))

    assert _values(system.latest() or ())["score"] == 10


def test_the_sample_event_carries_the_contract_fields_in_order() -> None:
    """`metric:sampled` 的负载字段**与契约逐条相等**（顺序也算）。"""
    bus = _bus()
    received: list[Envelope] = []
    bus.subscribe(METRIC_SAMPLED_EVENT, received.append)
    MetricsSystem(bus)

    bus.publish(_tick(7))

    assert received[0].type == "metric:sampled"
    assert tuple(name for name, _ in received[0].payload) == ("frame", "metrics")
    assert dict(received[0].payload)["frame"] == 7
    assert received[0].frame == 7


def test_the_samples_are_not_counted_as_metrics() -> None:
    """自产的 `metric:sampled` **不进指标**（否则无限自激）。"""
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_tick(1))

    assert _values(system.latest() or ())["frames"] == 1, "自产采样没被计成第二帧"
    assert len(system.samples) == 1


# ---------------------------------------------------------------------------
# 落盘经 sink（M2-D2 · ADR-064）
# ---------------------------------------------------------------------------


def test_the_sink_receives_the_bytes() -> None:
    """落盘经 sink：sink 收到的是**字节**（一行一条 JSON）。"""
    captured: list[bytes] = []
    bus = _bus()
    MetricsSystem(bus, captured.append)

    bus.publish(_tick(1))

    assert len(captured) == 1
    assert isinstance(captured[0], bytes), "交给 sink 的必须是字节"
    assert captured[0].endswith(b"\n"), "一行一条（JSON 行）"
    record = json.loads(captured[0].decode("utf-8"))
    assert record["frame"] == 1
    assert record["metrics"]["frames"] == 1
    assert set(record["metrics"]) == set(METRIC_NAMES)


def test_the_metrics_system_never_touches_the_filesystem(tmp_path: Path) -> None:
    """落盘只走 sink：指标系统**一个文件都不碰**（路径是宿主的事）。"""
    before = set(tmp_path.rglob("*"))
    captured: list[bytes] = []
    bus = _bus()
    MetricsSystem(bus, captured.append)

    bus.publish(_tick(1))
    bus.publish(_tick(2))

    assert set(tmp_path.rglob("*")) == before, "指标系统碰了文件系统"
    assert len(captured) == 2, "字节确实交给了 sink"


# ---------------------------------------------------------------------------
# 空洞率（M2-9）
# ---------------------------------------------------------------------------


def test_the_hole_rate_matches_hand_counting() -> None:
    """有空洞的棋盘 → **空洞率与手算一致**。

    造一列：y=0 与 y=2 有实心格 → 中间 **1 个空格** → 1 个空洞。
    再造一列：只有 y=0 → 没有相邻实心格 → **0 个空洞**。
    """
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_board_changed(((0, 0, 1), (0, 2, 1), (1, 0, 1))))
    bus.publish(_tick(1))

    values = _values(system.latest() or ())
    assert values["filledCells"] == 3, "已填格数 = 实心格总数"
    assert values["holeCount"] == 1, "同列相邻实心格之间的空格"
    assert values["holeRate"] == pytest.approx(1 / 3)


def test_the_empty_space_below_the_lowest_cell_is_not_a_hole() -> None:
    """**棋盘最底下的空地不算空洞**（它不在两个实心格之间）。"""
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_board_changed(((0, 0, 1),)))
    bus.publish(_tick(1))

    values = _values(system.latest() or ())
    assert values["filledCells"] == 1
    assert values["holeCount"] == 0, "下面那一大片空格不是空洞"
    assert values["holeRate"] == 0.0


def test_the_collapsed_event_updates_the_mirror() -> None:
    """`board:collapsed` 也更新镜像（**塌落后上面的格子落下来了**）。"""
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_board_changed(((0, 5, 1), (0, 7, 1))))
    bus.publish(_tick(1))
    assert _values(system.latest() or ())["holeCount"] == 1, "y=5 与 y=7 之间 1 个空格"

    bus.publish(_board_collapsed(((0, 5, 0), (0, 4, 1))))
    bus.publish(_tick(2))

    values = _values(system.latest() or ())
    assert values["filledCells"] == 2, "y=5 变空了"
    assert values["holeCount"] == 2, "y=4 与 y=7 之间 2 个空格"


def test_the_emptied_cells_stop_counting_as_filled() -> None:
    """变空的格子**不再算已填**（镜像里的 0 就是空）。"""
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_board_changed(((0, 5, 1), (0, 7, 1))))
    bus.publish(_board_collapsed(((0, 5, 0),)))
    bus.publish(_tick(1))

    values = _values(system.latest() or ())
    assert values["filledCells"] == 1
    assert values["holeCount"] == 0, "只剩一个实心格，没有相邻对"


# ---------------------------------------------------------------------------
# 布尔不算整数（审查 L2）
# ---------------------------------------------------------------------------


def test_a_boolean_cell_value_is_not_written_into_the_mirror() -> None:
    """**布尔不算整数**（审查 L2）——真值不会被当成实心格。

    契约的格子取值是**整数**（0 = 空）——出现 `True` 说明上游给了不合契约的
    值，**不该进镜像**。
    """
    bus = _bus()
    system = MetricsSystem(bus)

    # **真值当格子值**：不合契约。
    bus.publish(_board_changed(((0, 0, True),)))  # type: ignore[arg-type]
    bus.publish(_tick(1))

    values = _values(system.latest() or ())
    assert values["filledCells"] == 0, "布尔被当成实心格写进镜像了"
    assert values["holeCount"] == 0


def test_a_boolean_coordinate_does_not_alias_onto_an_integer_cell() -> None:
    """**布尔坐标也不算数**（审查 L2）——`True == 1`，会**悄悄别名**到那一格。

    真值当 x → 镜像里落进的是 `(1, y)` 那一格——**坐标被改写了**。
    """
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_board_changed(((True, 0, 1),)))  # type: ignore[arg-type]
    bus.publish(_tick(1))

    values = _values(system.latest() or ())
    assert values["filledCells"] == 0, "布尔坐标被当成了整数坐标"


def test_an_integer_cell_value_is_still_mirrored() -> None:
    """**合契约的整数照旧进镜像**（L2 的反向判据）。

    **这条守着"排布尔"别变成"一律不收"**。
    """
    bus = _bus()
    system = MetricsSystem(bus)

    bus.publish(_board_changed(((0, 0, 1), (0, 2, 1))))
    bus.publish(_tick(1))

    values = _values(system.latest() or ())
    assert values["filledCells"] == 2
    assert values["holeCount"] == 1


# ---------------------------------------------------------------------------
# 普通订阅者（M2-D1）
# ---------------------------------------------------------------------------


def test_the_metrics_system_never_touches_the_kernel_state() -> None:
    """普通订阅者：**没有任何写内核状态的路径**（M2-D1）。

    **机制级断言**：总线注入的 `view_provider` **一次都没被调用**——
    指标系统一旦改成 `ctx.view()` 查内核状态算指标，这条立刻红。
    """
    calls: list[int] = []
    bus = _counting_bus(calls)
    system = MetricsSystem(bus)

    bus.publish(_board_changed(((0, 0, 1),)))
    bus.publish(_tick(1))

    assert calls == [], "指标系统取了内核视图——它必须是纯订阅者"
    assert len(system.samples) == 1


def test_the_metrics_package_does_not_import_the_filesystem_or_the_kernel_state() -> None:
    """落盘只走 sink（M2-D2）· 只订阅事件不碰内核状态（M2-D1）。

    **机制级**：AST 扫描 `systems/metrics` 的**全部源文件**——

    - 不 import `pathlib` / `os` / `shutil` / `io`（自己 `open()` 就等于
      自己管路径）；
    - 不 import `kernel.core` / `kernel.snapshot`（内核状态的地盘）。
    """
    package_dir = Path(metrics_package.__file__).parent
    imported: set[str] = set()
    for source_path in sorted(package_dir.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported |= _imported_modules(tree, package="tetris_kernel.systems.metrics")

    roots = {name.split(".", 1)[0] for name in imported}
    assert not roots & {"pathlib", "os", "shutil", "io"}, f"指标系统碰了文件系统：{sorted(roots)}"
    assert "tetris_kernel.kernel.core" not in imported, "指标系统 import 了内核状态"
    assert "tetris_kernel.kernel.snapshot" not in imported, "指标系统 import 了内核快照"


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
# 杂项
# ---------------------------------------------------------------------------


def test_the_system_works_without_a_sink() -> None:
    """**没有 sink** 也不报错——采样照样广播。"""
    bus = _bus()
    received: list[Envelope] = []
    bus.subscribe(METRIC_SAMPLED_EVENT, received.append)
    system = MetricsSystem(bus)

    bus.publish(_tick(1))

    assert len(received) == 1
    assert len(system.samples) == 1


def test_the_sample_limit_is_validated() -> None:
    """域外值当场报错。"""
    bus = _bus()

    with pytest.raises(ValueError):
        MetricsSystem(bus, None, sample_limit=0)


def test_the_kept_samples_are_ring_bounded() -> None:
    """保留的采样是**环形**的——超出上限就挤掉最旧的。"""
    bus = _bus()
    system = MetricsSystem(bus, None, sample_limit=2)

    for frame in range(1, 5):
        bus.publish(_tick(frame))

    assert len(system.samples) == 2
    assert [frame for frame, _ in system.samples] == [3, 4]
