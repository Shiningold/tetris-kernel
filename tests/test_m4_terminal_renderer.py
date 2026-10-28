"""终端渲染器的用例（M4-S6 · FR-8.1 / FR-8.2 · M4-D2 · spec-M4 §7.6）。

**守的是口径，不是实现细节**：

- **画面完全由事件推导**（FR-8.2）——**喂假事件流就能测画面**（N3 ·
  **不必起内核**）；
- **活动方块的格子按规则集算**——**变体 A 的规则集接进去照样画对**
  （**这是"画面由事件推导"的实证**）；
- **不写内核状态**（M4-D2）——**挂着渲染器，内核逐帧指纹不变**；
- **不用只读视图通道**（架构 §4.1）——**渲染器不 import 内核**（源码级断言）。

**反向用例**（"让渲染器去问内核要棋盘" → 必红）在
`test_渲染器不碰内核`。
"""

from __future__ import annotations

import ast
import io
from pathlib import Path

from tetris_kernel.adapters.render import (
    CELL_ACTIVE,
    CELL_BLOCK,
    CELL_EMPTY,
    CELL_SPECIAL,
    TerminalRenderer,
)
from tetris_kernel.host import assemble
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.rules.standard import STANDARD_RULESET
from tetris_kernel.variants.gravity import gravity_rule_set

_RENDER_DIR = Path(__file__).resolve().parents[1] / "src" / "tetris_kernel" / "adapters" / "render"

#: 棋盘尺寸（**构造参数**：契约里没有尺寸事件）。
WIDTH = 10
HEIGHT = 4


class _Envelope:
    """**假信封**（结构上与真实信封一致 —— 渲染器只认结构）。"""

    def __init__(
        self,
        event_type: str,
        payload: tuple[tuple[str, object], ...],
        frame: int = 1,
    ) -> None:
        self.type = event_type
        self.payload = payload
        self.frame = frame


def _renderer(rule_set: object = STANDARD_RULESET) -> TerminalRenderer:
    """造一个渲染器（**输出流用 `StringIO`** —— **不碰真终端**）。"""
    return TerminalRenderer(
        rule_set=rule_set,  # type: ignore[arg-type]
        width=WIDTH,
        height=HEIGHT,
        stream=io.StringIO(),
    )


def _spawned(piece_type: str = "T", x: int = 3, y: int = 0) -> _Envelope:
    return _Envelope("piece:spawned", (("pieceType", piece_type), ("x", x), ("y", y), ("next", ())))


# ---------------------------------------------------------------------------
# 画面由事件推导
# ---------------------------------------------------------------------------


def test_棋盘由事件累积() -> None:
    """**棋盘由 `board:changed` 累积**（**初始是空的** —— 从空累积自洽）。"""
    renderer = _renderer()
    assert renderer.board == {}
    renderer.on_event(
        _Envelope("board:changed", (("cellChanges", ((0, 0, 1), (1, 0, 1))), ("cause", "lock")))
    )
    assert renderer.board == {(0, 0): 1, (1, 0): 1}
    # **值为 0 = 清空**。
    renderer.on_event(
        _Envelope("board:changed", (("cellChanges", ((0, 0, 0),)), ("cause", "writeCells")))
    )
    assert renderer.board == {(1, 0): 1}


def test_塌落的变化也并进棋盘() -> None:
    """**`board:collapsed` 的变化并进镜像**（消行后的下移与补空）。"""
    renderer = _renderer()
    renderer.on_event(_Envelope("board:collapsed", (("cellChanges", ((0, 1, 1), (0, 0, 0))),)))
    assert renderer.board == {(0, 1): 1}


def test_活动方块由_piece_事件累积() -> None:
    """**活动方块由 `piece:*` 事件累积**（出生 → 移动 → 旋转 → 踢墙）。"""
    renderer = _renderer()
    renderer.on_event(_spawned("T", 3, 0))
    assert renderer.active == ("T", 0, 3, 0)
    renderer.on_event(
        _Envelope(
            "piece:moved",
            (
                ("fromX", 3),
                ("fromY", 0),
                ("toX", 4),
                ("toY", 0),
                ("blocked", False),
                ("cause", "command"),
            ),
        )
    )
    assert renderer.active == ("T", 0, 4, 0)
    renderer.on_event(
        _Envelope(
            "piece:rotated",
            (("fromRotation", 0), ("toRotation", 1), ("direction", "cw"), ("blocked", False)),
        )
    )
    assert renderer.active == ("T", 1, 4, 0)
    renderer.on_event(
        _Envelope("piece:kicked", (("kickIndex", 1), ("offsetX", -1), ("offsetY", 0)))
    )
    assert renderer.active == ("T", 1, 3, 0)


def test_受阻的移动与旋转不改状态() -> None:
    """**受阻时内核没动** → 渲染器也不动（**别把画面画歪**）。"""
    renderer = _renderer()
    renderer.on_event(_spawned("T", 3, 0))
    renderer.on_event(
        _Envelope(
            "piece:moved",
            (
                ("fromX", 3),
                ("fromY", 0),
                ("toX", 3),
                ("toY", 0),
                ("blocked", True),
                ("cause", "command"),
            ),
        )
    )
    renderer.on_event(
        _Envelope(
            "piece:rotated",
            (("fromRotation", 0), ("toRotation", 1), ("direction", "cw"), ("blocked", True)),
        )
    )
    assert renderer.active == ("T", 0, 3, 0)


def test_锁定后活动方块清空() -> None:
    """**锁定**：格子进棋盘（`board:changed`），**活动方块没了**。"""
    renderer = _renderer()
    renderer.on_event(_spawned("T", 3, 0))
    renderer.on_event(
        _Envelope(
            "piece:locked",
            (
                ("pieceType", "T"),
                ("rotation", 0),
                ("x", 3),
                ("y", 2),
                ("cells", ((3, 2),)),
                ("tSpin", "none"),
                ("cause", "ground"),
            ),
        )
    )
    assert renderer.active is None


def test_计分与相位由_state_changed_给出() -> None:
    """**计分与相位由 `state:changed` 给出**（spec §7.6）。"""
    renderer = _renderer()
    renderer.on_event(
        _Envelope(
            "state:changed",
            (
                ("version", 3),
                ("rule", "standard"),
                ("phase", "running"),
                ("score", 120),
                ("lines", 2),
                ("level", 3),
                ("combo", 0),
                ("b2b", False),
                ("piecesPlaced", 5),
                ("activeType", "T"),
                ("hold", "J"),
                ("holdUsed", False),
                ("next", ("S", "Z")),
            ),
        )
    )
    assert renderer.score == 120
    assert renderer.lines == 2
    assert renderer.level == 3
    assert renderer.phase == "running"
    assert renderer.hold == "J"
    assert renderer.next_queue == ("S", "Z")


def test_画面文本反映模型() -> None:
    """**画面文本**：棋盘行是**两字符一格**，活动方块画成 `@`。"""
    renderer = _renderer()
    renderer.on_event(_spawned("T", 3, 0))
    renderer.on_event(
        _Envelope("board:changed", (("cellChanges", ((0, 0, 1),)), ("cause", "lock")))
    )
    lines = renderer.frame_text().split("\n")
    # **T 的初始朝向占 ((1,0),(0,1),(1,1),(2,1))** → 出生在 (3,0) → 画在
    # **第 0 行的第 4 格**（x=3+1）与**第 1 行的第 3–5 格**（x=3..5）。
    assert lines[2] == CELL_BLOCK + CELL_EMPTY * 3 + CELL_ACTIVE
    assert lines[3] == CELL_EMPTY * 3 + CELL_ACTIVE * 3


def test_非零值与普通方块画法不同() -> None:
    """**别的非零值画法不同**（**变体 B 的晶体 `8`** —— 渲染器不认玩法，
    **只认"是不是普通方块"**）。"""
    renderer = _renderer()
    renderer.on_event(
        _Envelope(
            "board:changed",
            (("cellChanges", ((0, 0, 1), (1, 0, 8))), ("cause", "writeCells")),
        )
    )
    row = renderer.frame_text().split("\n")[2]
    assert row.startswith(CELL_BLOCK + CELL_SPECIAL)


def test_画面可由事件重建且可重复() -> None:
    """**同一串事件喂两次 → 同样的画面**（**可重建**是 FR-8.2 的核心）。"""
    events = (
        _spawned("T", 3, 0),
        _Envelope("board:changed", (("cellChanges", ((0, 0, 1),)), ("cause", "lock"))),
    )
    first = _renderer()
    second = _renderer()
    for renderer in (first, second):
        for event in events:
            renderer.on_event(event)
    assert first.frame_text() == second.frame_text()


# ---------------------------------------------------------------------------
# 规则集：变体 A 接进去照样画对
# ---------------------------------------------------------------------------


def test_变体_a_的规则集接进去照样画对() -> None:
    """**活动方块的格子按规则集算**——**变体 A 的旋转重力下照样对**
    （**渲染器跟着换规则集即可**，**内核一个字节都不用改**）。"""
    standard = _renderer(STANDARD_RULESET)
    gravity = _renderer(gravity_rule_set("right"))
    for renderer in (standard, gravity):
        renderer.on_event(_spawned("T", 3, 0))
    standard_cells = standard.active_cells
    gravity_cells = gravity.active_cells
    assert standard_cells != gravity_cells
    # **变体 A 的表是标准表绕盒中心转 90°** → **占位列数一样、格子的位置不同**。
    assert len(standard_cells) == len(gravity_cells) == 4


# ---------------------------------------------------------------------------
# 与内核的关系
# ---------------------------------------------------------------------------


def test_挂着渲染器内核逐帧指纹不变() -> None:
    """**渲染器不改内核状态**（M4-D2 · G4-9 的子集）。

    **实证**：同一颗种子，一局挂渲染器、一局不挂 → **逐帧结构指纹全等**。
    """
    fingerprints = []
    for with_renderer in (False, True):
        assembly = assemble(seed=11)
        loop = HeadlessLoop(assembly)
        loop.start()
        renderer = _renderer()
        if with_renderer:
            assembly.bus.subscribe("*", renderer.on_event)
        seen = []
        for _ in range(120):
            loop.run_frame()
            seen.append(loop.snapshot().board.structureFingerprint)
        fingerprints.append(seen)
    assert fingerprints[0] == fingerprints[1]


def test_渲染器不碰内核() -> None:
    """**源码级断言**：渲染器**不 import 内核**（架构 §4.1 · M4-D2）。

    **理由**：渲染器**拿不到 `ctx.view()`**——**画面只能来自事件**。
    """
    for path in sorted(_RENDER_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert "kernel" not in name, f"{path.name} 不该 import 内核：{name}"
