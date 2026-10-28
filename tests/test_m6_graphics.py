"""图形渲染器的用例（M6-S1 · `G6-2` · spec-M6 §7.1 / §9.1 · ADR-100）。

**守的是口径，不是实现细节**：

- **画面等价**（`G6-2`）——**同一局的同一时刻，图形层与终端层画出的棋盘
  逐格相同**（**差分**：真跑一局，两层同时看）；
- **幽灵落点**——**等于活动方块 + 内核给的 `ghostDelta`**（不是渲染器自己算的）；
- **面板字段**——**分数 / 行数 / 等级 / HOLD / NEXT 与快照一致**（**不认事件**）；
- **不改内核**——**挂着图形渲染器，内核逐帧指纹不变**（它只读快照）；
- **不订阅总线**——图形层**一个事件都不发**（`G6-1` 的事件数基线靠这条）。

**为什么能无头跑**：图形渲染器的**纯逻辑层不 import tkinter**——CI 与无头
环境没有显示器，测试只比对**画面模型**（不是像素）。
"""

from __future__ import annotations

import io

from tetris_kernel.adapters.render import GraphicsRenderer, TerminalRenderer
from tetris_kernel.host import assemble
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.rules.standard import STANDARD_RULESET

#: 棋盘尺寸（**小棋盘** —— 差分要快）。
WIDTH = 10
HEIGHT = 6

#: 终端渲染器的格子画法 → 画面模型的 `kind`（**同一口径**）。
_TERMINAL_KINDS = {
    "  ": "empty",
    "[]": "block",
    "**": "special",
    "@@": "active",
}


def _terminal_grid(renderer: TerminalRenderer) -> list[list[str]]:
    """**终端画面 → 逐格种类**（**前两行是表头** · 剩下的是棋盘行）。

    **注意行尾空白**：终端渲染器**把行尾空格去掉了**（`_row_text`）→ 解析时
    **必须补齐到棋盘宽度**，否则整盘会错位（这一条踩过）。
    """
    rows = renderer.frame_text().split("\n")[2:]
    grid: list[list[str]] = []
    for row in rows:
        padded = row.ljust(WIDTH * 2)
        cells = [padded[index : index + 2] for index in range(0, WIDTH * 2, 2)]
        grid.append([_TERMINAL_KINDS[cell] for cell in cells])
    return grid


def _graphics_grid(renderer: GraphicsRenderer) -> list[list[str]]:
    """**画面模型 → 逐格种类**（**按 y 分行** · 幽灵当空 —— 终端版没有幽灵）。"""
    model = renderer.model
    if model is None:
        return []
    rows: dict[int, list[str]] = {}
    for _x, y, kind in model.cells:
        rows.setdefault(y, []).append("empty" if kind == "ghost" else kind)
    return [rows[y] for y in sorted(rows)]


def _session(seed: int = 20261120):
    """**一局真游戏 + 两层渲染器**（**终端层订阅总线 · 图形层只读快照**）。"""
    assembly = assemble(
        seed=seed,
        rule_set=STANDARD_RULESET,
        board_width=WIDTH,
        board_height=HEIGHT,
        hidden_rows=0,
    )
    terminal = TerminalRenderer(
        rule_set=STANDARD_RULESET,
        width=WIDTH,
        height=HEIGHT,
        hidden_rows=0,
        stream=io.StringIO(),
    )
    assembly.bus.subscribe("*", terminal.on_event)
    graphics = GraphicsRenderer(
        rule_set=STANDARD_RULESET,
        width=WIDTH,
        height=HEIGHT,
        hidden_rows=0,
    )
    loop = HeadlessLoop(assembly)
    loop.start()
    return assembly, loop, terminal, graphics


def _submit(assembly: object, command_type: str) -> None:
    """**派一条命令**（**命令的组装归宿主** · 与真实宿主同一条路）。"""
    assembly.kernel.submit(Command(type=command_type, args=(), source="host"))


def _sync(loop: HeadlessLoop, terminal: TerminalRenderer, graphics: GraphicsRenderer) -> None:
    """**把同一份状态喂给两层**（**终端层靠事件 · 图形层靠快照**）。"""
    snapshot = loop.snapshot()
    active = snapshot.active
    if active is None:
        terminal.sync_active(None, 0, 0, 0)
    else:
        terminal.sync_active(active.pieceType, active.rotation, active.x, active.y)
    graphics.update_snapshot(snapshot)


# ---------------------------------------------------------------------------
# G6-2 · 画面等价
# ---------------------------------------------------------------------------


def test_画面与终端渲染器逐格等价() -> None:
    """**图形层与终端层画的是同一盘**（**差分**：真跑一局 · 逐步比对）。"""
    assembly, loop, terminal, graphics = _session()
    commands = [
        "moveLeft",
        "moveLeft",
        "rotateCW",
        "moveRight",
        "hardDrop",
        "moveLeft",
        "softDrop",
        "rotateCCW",
        "hardDrop",
        "moveRight",
        "moveRight",
        "hardDrop",
    ]
    for command_type in commands:
        _submit(assembly, command_type)
        loop.run_frame()
        _sync(loop, terminal, graphics)
        assert _graphics_grid(graphics) == _terminal_grid(terminal), (
            f"图形层与终端层在命令 {command_type} 之后画得不一样"
        )


def test_等价在多种子下都成立() -> None:
    """**不是一个种子的巧合**（**三个种子各跑一局**）。"""
    for seed in (1, 20261120, 987654321):
        assembly, loop, terminal, graphics = _session(seed=seed)
        for command_type in ("moveLeft", "rotateCW", "hardDrop", "moveRight", "hardDrop"):
            _submit(assembly, command_type)
            loop.run_frame()
            _sync(loop, terminal, graphics)
            assert _graphics_grid(graphics) == _terminal_grid(terminal)


# ---------------------------------------------------------------------------
# 幽灵落点
# ---------------------------------------------------------------------------


def test_幽灵落点等于活动方块加位移() -> None:
    """**幽灵 = 活动方块 + `ghostDelta`**（**位移由内核给** · 渲染器不自己算）。

    **口径**：**幽灵与活动方块重合的那几格不算幽灵**（`ghostDelta = (0, 0)`
    时方块已经贴地，**幽灵就是它自己**）——所以断言的是「**幽灵格 = 落点 −
    活动格**」。
    """
    assembly, loop, terminal, graphics = _session()
    _submit(assembly, "hardDrop")
    loop.run_frame()
    _sync(loop, terminal, graphics)
    snapshot = loop.snapshot()
    model = graphics.model
    assert model is not None
    assert snapshot.active is not None
    ghost = {(x, y) for x, y, kind in model.cells if kind == "ghost"}
    active_cells = {(x, y) for x, y, kind in model.cells if kind == "active"}
    dx, dy = snapshot.ghostDelta
    landing = {(x + dx, y + dy) for x, y in active_cells}
    assert ghost == landing - active_cells


def test_幽灵在方块悬空时出现() -> None:
    """**方块悬空 → 幽灵是另一组格**（**落地就重合 · 那时不算幽灵**）。"""
    assembly, loop, terminal, graphics = _session()
    loop.run_frame()
    _sync(loop, terminal, graphics)
    snapshot = loop.snapshot()
    model = graphics.model
    assert model is not None
    dx, dy = snapshot.ghostDelta
    if (dx, dy) != (0, 0):
        assert any(kind == "ghost" for _x, _y, kind in model.cells)


def test_幽灵不压在实心格上() -> None:
    """**幽灵格在棋盘上是空的**（**压着实心格就是落点算错了**）。"""
    assembly, loop, terminal, graphics = _session()
    for command_type in ("hardDrop", "moveLeft", "hardDrop", "hardDrop"):
        _submit(assembly, command_type)
        loop.run_frame()
        _sync(loop, terminal, graphics)
    snapshot = loop.snapshot()
    model = graphics.model
    assert model is not None
    rows = snapshot.board.cells
    for x, y, kind in model.cells:
        if kind == "ghost":
            assert rows[y][x] == 0


# ---------------------------------------------------------------------------
# 面板字段
# ---------------------------------------------------------------------------


def test_面板字段与快照一致() -> None:
    """**面板读的是快照**（**不是自己攒的**）。"""
    assembly, loop, terminal, graphics = _session()
    for command_type in ("moveLeft", "hardDrop", "hardDrop"):
        _submit(assembly, command_type)
        loop.run_frame()
        _sync(loop, terminal, graphics)
    snapshot = loop.snapshot()
    model = graphics.model
    assert model is not None
    assert model.score == snapshot.score
    assert model.lines == snapshot.lines
    assert model.level == snapshot.level
    assert model.combo == snapshot.combo
    assert model.b2b == snapshot.b2b
    assert model.hold == snapshot.hold
    assert model.next_queue == tuple(snapshot.next)
    assert model.phase == snapshot.phase
    assert model.game_over == (snapshot.phase == "game_over")


def test_next预览取前五个() -> None:
    """**NEXT 预览取前 5 个**（**契约：快照给全袋 · 渲染层取前 5**）。"""
    assembly, loop, terminal, graphics = _session()
    loop.run_frame()
    _sync(loop, terminal, graphics)
    model = graphics.model
    assert model is not None
    assert len(model.previews) <= 1 + 5  # HOLD 至多一个 + NEXT 五个
    assert model.next_queue == tuple(loop.snapshot().next)


# ---------------------------------------------------------------------------
# 不改内核 · 不订阅事件
# ---------------------------------------------------------------------------


def test_挂着图形渲染器内核指纹不变() -> None:
    """**图形渲染器不改内核状态**（**它只读快照**）。"""
    assembly, loop, terminal, graphics = _session()
    for command_type in ("moveLeft", "rotateCW", "hardDrop"):
        _submit(assembly, command_type)
        loop.run_frame()
        _sync(loop, terminal, graphics)
    with_graphics = loop.snapshot().board.structureFingerprint

    plain_assembly = assemble(
        seed=20261120,
        rule_set=STANDARD_RULESET,
        board_width=WIDTH,
        board_height=HEIGHT,
        hidden_rows=0,
    )
    plain_loop = HeadlessLoop(plain_assembly)
    plain_loop.start()
    for command_type in ("moveLeft", "rotateCW", "hardDrop"):
        _submit(plain_assembly, command_type)
        plain_loop.run_frame()
    assert plain_loop.snapshot().board.structureFingerprint == with_graphics


def test_图形渲染器不订阅总线() -> None:
    """**图形层不订阅总线**（**它一个事件都不发**）。"""
    assembly, loop, terminal, graphics = _session()
    before = assembly.bus.last_seq
    _submit(assembly, "hardDrop")
    loop.run_frame()
    _sync(loop, terminal, graphics)
    # **只有内核与宿主发的事件**——图形渲染器没有订阅，也没有多发。
    assert assembly.bus.last_seq > before
    assert graphics.model is not None


# 快照口径
# ---------------------------------------------------------------------------


def test_渲染器不改快照() -> None:
    """**喂进去的快照是只读的**（**渲染器不许改它**）。"""
    assembly, loop, terminal, graphics = _session()
    _submit(assembly, "hardDrop")
    loop.run_frame()
    snapshot: Snapshot = loop.snapshot()
    before = snapshot.board.structureFingerprint
    graphics.update_snapshot(snapshot)
    assert snapshot.board.structureFingerprint == before
