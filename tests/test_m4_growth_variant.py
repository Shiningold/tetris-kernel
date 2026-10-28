"""变体 B（落地后生长）的用例（M4-S4 · spec-M4 §7.3）。

**守的是口径，不是实现细节**：

- **写入只走 `writeCells`**（FR-11.1 · 外部改变棋盘的唯一合法通道）——
  **变体不许自己写棋盘，也不许自己发事件**；
- **只写空格** · **避开活动方块占的格**（内核的写入条件：撞上就整批被拒）；
- **写成功才算数**（`board:changed` 的 cause 是 `writeCells`）· **重试有上限**
  （宁可少长，不写错）；
- **压力曲线**：每 4 次锁定多长一格；
- **变体层不 import 内核 / 总线**（架构 §2.3 / §4.1 / §7.7）——**源码级断言**；
- **端到端**：装上这个变体的系统跑一局 → **晶体真的长在棋盘上**。

**反向用例**（"让变体绕过 `writeCells` 直接改棋盘" → 必红）在
`test_variant_never_touches_the_kernel_directly`。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tetris_kernel.contracts.commands import COMMANDS
from tetris_kernel.contracts.events import EVENT_TYPES
from tetris_kernel.host import assemble
from tetris_kernel.host.discovery import system_catalog
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.variants.growth import (
    BOARD_CHANGED_EVENT,
    CRYSTAL_VALUE,
    PIECE_LOCKED_EVENT,
    PIECE_SPAWNED_EVENT,
    SYSTEM_MANIFEST,
    GrowthSystem,
)

_VARIANT_DIR = Path(__file__).resolve().parents[1] / "src" / "tetris_kernel" / "variants" / "growth"


class _Envelope:
    """**假信封**（结构上与真实的信封一致 —— 变体只认结构）。"""

    def __init__(self, event_type: str, payload: tuple[tuple[str, object], ...]) -> None:
        self.type = event_type
        self.payload = payload


class _Submitter:
    """**假命令通道**（记下每次写入）。"""

    def __init__(self) -> None:
        self.batches: list[tuple[tuple[int, int, int], ...]] = []

    def submit_cells(
        self,
        cells: tuple[tuple[int, int, int], ...],
        source: str = "system",
    ) -> None:
        self.batches.append(cells)


def _locked(cells: tuple[tuple[int, int], ...]) -> _Envelope:
    """一条 `piece:locked` 事件。"""
    return _Envelope(PIECE_LOCKED_EVENT, (("cells", cells),))


def _write_confirmed(cells: tuple[tuple[int, int, int], ...]) -> _Envelope:
    """一条"写入成功"的 `board:changed`。"""
    return _Envelope(BOARD_CHANGED_EVENT, (("cellChanges", cells), ("cause", "writeCells")))


# ---------------------------------------------------------------------------
# 声明
# ---------------------------------------------------------------------------


def test_manifest_declares_only_registered_events_and_commands() -> None:
    """**声明自证**：订阅的事件与派发的命令**都已在契约里登记**。"""
    for event_type in SYSTEM_MANIFEST.subscribes:
        assert event_type in EVENT_TYPES
    for command_type in SYSTEM_MANIFEST.commands:
        assert command_type in COMMANDS
    assert SYSTEM_MANIFEST.name == "growth"
    # **它一条事件都不发** → 产出者代号就是系统名（需求 §8.2 允许「系统名」）。
    assert SYSTEM_MANIFEST.producer == SYSTEM_MANIFEST.name
    # **它的产出是棋盘上的格子，不是文件** → 不需要落盘出口。
    assert SYSTEM_MANIFEST.needsSink is False


# ---------------------------------------------------------------------------
# 生长规则
# ---------------------------------------------------------------------------


def test_growth_writes_one_crystal_above_the_locked_cell() -> None:
    """**锁定格正上方长出晶体**（值 `8`），**经命令通道提交**。"""
    submitter = _Submitter()
    system = GrowthSystem(submitter)  # type: ignore[arg-type]
    system.on_event(_locked(((3, 10), (4, 10))))
    system.on_event(_Envelope("clock:tick", ()))
    assert len(submitter.batches) == 1
    assert submitter.batches[0] == ((3, 9, CRYSTAL_VALUE), (4, 9, CRYSTAL_VALUE))


def test_growth_never_writes_over_an_occupied_cell() -> None:
    """**只写空格**——已被占用的格不写，**且不越过它往上长**。"""
    submitter = _Submitter()
    system = GrowthSystem(submitter)  # type: ignore[arg-type]
    # (3, 9) 已被占（棋盘变化事件告知）。
    system.on_event(
        _Envelope(BOARD_CHANGED_EVENT, (("cellChanges", ((3, 9, 1),)), ("cause", "lock")))
    )
    system.on_event(_locked(((3, 10),)))
    system.on_event(_Envelope("clock:tick", ()))
    assert submitter.batches == []


def test_growth_avoids_the_active_piece_cells() -> None:
    """**避开活动方块占的格**（内核的写入条件：撞上就整批被拒）。"""
    submitter = _Submitter()
    system = GrowthSystem(submitter)  # type: ignore[arg-type]
    # 新方块 T 出生在 (3, 0) → 占 (4,0) (3,1) (4,1) (5,1)（标准 3×3 盒的朝向 0）。
    system.on_event(_Envelope(PIECE_SPAWNED_EVENT, (("pieceType", "T"), ("x", 3), ("y", 0))))
    system.on_event(_locked(((4, 0),)))
    system.on_event(_Envelope("clock:tick", ()))
    # **(4, -1) 在棋盘外**（y < 0）→ 不写；**(4, 0) 被活动方块占着** → 也不写。
    assert submitter.batches == []


def test_growth_length_grows_with_the_lock_count() -> None:
    """**压力曲线**：每 4 次锁定多长一格。"""
    submitter = _Submitter()
    system = GrowthSystem(submitter)  # type: ignore[arg-type]
    # **前 3 次锁定在别的列**（互不干扰），**第 4 次**在 (3, 10)。
    for index in range(3):
        system.on_event(_locked(((index, 15),)))
    system.on_event(_locked(((3, 10),)))
    system.on_event(_Envelope("clock:tick", ()))
    # **第 4 次锁定时**高度 = 1 + 4 // 4 = 2 → **两格**（3,9）与 (3,8) 都在这一批里。
    assert {(3, 9, CRYSTAL_VALUE), (3, 8, CRYSTAL_VALUE)} <= set(submitter.batches[0])


def test_written_cells_are_confirmed_and_not_written_again() -> None:
    """**写成功才算数**：`board:changed` 确认后**不再重复提交**。"""
    submitter = _Submitter()
    system = GrowthSystem(submitter)  # type: ignore[arg-type]
    system.on_event(_locked(((3, 10),)))
    system.on_event(_Envelope("clock:tick", ()))
    assert len(submitter.batches) == 1
    # **确认写入** → 待写清单划掉 → 再 tick 也不提交。
    system.on_event(_write_confirmed(((3, 9, CRYSTAL_VALUE),)))
    assert system.pending_count == 0
    system.on_event(_Envelope("clock:tick", ()))
    assert len(submitter.batches) == 1


def test_a_cell_is_abandoned_after_too_many_retries() -> None:
    """**重试有上限**——同一格试满次数就**放弃**（宁可少长，不写错）。"""
    submitter = _Submitter()
    system = GrowthSystem(submitter)  # type: ignore[arg-type]
    system.on_event(_locked(((3, 10),)))
    for _ in range(6):
        system.on_event(_Envelope("clock:tick", ()))
    # **同一格最多试 3 次**（上限之内）→ 试满就不再提交。
    assert len(submitter.batches) == 3


# ---------------------------------------------------------------------------
# 依赖方向
# ---------------------------------------------------------------------------


def test_variant_never_touches_the_kernel_directly() -> None:
    """**变体层不 import 内核 / 总线**（架构 §2.3 / §4.1 / §7.7）。

    **反向用例**：变体若绕过 `writeCells` 直接改棋盘，就得 import 内核 →
    **这条断言当场红**。
    """
    forbidden = ("kernel", "bus", "adapters", "host")
    offenders: list[str] = []
    for path in sorted(_VARIANT_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("tetris_kernel."):
                        parts = alias.name.split(".")
                        if len(parts) > 1 and parts[1] in forbidden:
                            offenders.append(f"{path.name}: import {alias.name}")
                continue
            # **相对导入也要看**（`from ...kernel.snapshot import X` → 模块名是
            # `...kernel.snapshot`）→ **去掉空串后看第一段**。
            parts = [part for part in module.split(".") if part]
            if parts and parts[0] in forbidden:
                offenders.append(f"{path.name}: from {module}")
    assert offenders == []


# ---------------------------------------------------------------------------
# 端到端
# ---------------------------------------------------------------------------


def test_growth_variant_actually_grows_crystals_on_the_board() -> None:
    """**端到端**：装上这个系统跑一局 → **棋盘上真的长出晶体**（值 `8`）。"""
    assembly = assemble(seed=7, systems=("growth",), system_catalog=system_catalog())
    causes: list[object] = []

    def _collect(envelope: object) -> None:
        """**当场收 cause**（环形历史会被后续帧覆盖 → 收不到）。"""
        payload = getattr(envelope, "payload", ())
        if getattr(envelope, "type", "") == BOARD_CHANGED_EVENT:
            causes.append(dict(payload).get("cause"))

    assembly.bus.subscribe(BOARD_CHANGED_EVENT, _collect)
    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frames(4000)
    board = assembly.kernel.snapshot().board.cells
    crystals = sum(1 for row in board for value in row if value == CRYSTAL_VALUE)
    assert crystals > 0
    # **它走的是受控写入** → 内核照常发 `board:changed`（cause = writeCells）。
    assert "writeCells" in causes
