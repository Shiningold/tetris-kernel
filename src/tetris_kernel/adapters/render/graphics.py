"""图形渲染器（适配层 · M6-S1 · FR-8.1 / FR-8.2 · spec-M6 §7.1 · ADR-100）。

## 它是什么

**把内核的快照画成一幅整画面**——棋盘 · 幽灵落点 · HOLD · NEXT · 分数 / 行数 /
等级 / 连击 · 锁定延迟进度 · 结算面板。

## 为什么是"快照驱动"，不是"事件驱动"

`adapters/render/__init__.py` 的模块头写明渲染器可以消费「**事件与快照**」。
**快照的 22 个字段已经带齐了画完整画面所需的一切**（`board` · `active` ·
`ghostDelta` · `hold` · `holdUsed` · `next` · `score` · `lines` · `level` ·
`combo` · `b2b` · `lockTimer` · `phase`）——所以图形版**每帧重画整幅画面**。

**为什么不复制终端版那套事件镜像**（`TerminalRenderer` 的 `_apply_cells` /
`_on_*`）：那是**两处真相**——改一处忘一处，图形版与终端版就会分叉，而
**分叉是静默的**（画面看着差不多，指纹已经不一样了）。**每帧重画 10×20 =
200 格**，在 tkinter 上就是几百个 item 的更新，**量级完全够**（spec-M6 §8）。

## 两层切分（**为了能无头单测**）

| 层 | 类 | 依赖 tkinter？ |
| --- | --- | --- |
| **纯逻辑** | `GraphicsRenderer` | **否**——快照进，`FrameModel` 出 |
| **画笔** | `TkPainter` | 是——`FrameModel` → Canvas |

**纯逻辑层不 import tkinter**——**测试里不起窗口**（CI 与无头环境没有显示器），
`G6-2` 的逐格比对也在这层做（**比对的是"画面模型"，不是像素**）。

## 方块的占位格子按规则集算

`shape_cells(ctx, pieceType, rotation)`——**规则集说了算**（FR-8.2）：变体 A
的旋转重力下照样算对，**内核一个字节都不用改**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from ...contracts.rules import RuleContext
from ...rules.interface import RuleSet

#: **默认轴**（**契约要求的字段值** · 内核恒传 `down`）。
#:
#: **为什么从终端渲染器那边拿**：这个常量**已经住在那一层**（`terminal.py`），
#: **复制一份就是两处真相**；而**挪进 `contracts/` 属于改契约面**（M6 的
#: `G6-1` 要求契约基线零变化）——所以**引用它，不搬家**。
from .terminal import BLOCK_VALUE, DEFAULT_AXIS


class BoardLike(Protocol):
    """**渲染器要用的棋盘那一面**（**只读**）。

    **为什么自己声明，不 import 内核**：**渲染层不许 import 内核**（架构 §4.1
    · M4-D2 · 有源码级断言守着）——所以**渲染器要什么，自己写出来**。
    """

    @property
    def cells(self) -> tuple[tuple[int, ...], ...]:
        """**行优先的棋盘内容**（**第 0 行是顶部**）。"""
        ...


class ActiveLike(Protocol):
    """**渲染器要用的活动方块那一面**（**只读**）。"""

    @property
    def pieceType(self) -> str:
        """**方块类型**。"""
        ...

    @property
    def rotation(self) -> int:
        """**当前朝向**。"""
        ...

    @property
    def x(self) -> int:
        """**横坐标**。"""
        ...

    @property
    def y(self) -> int:
        """**纵坐标**。"""
        ...


class SnapshotLike(Protocol):
    """**渲染器要用的快照那一面**（**只读**）。

    **这就是"渲染器只消费事件与快照"里"快照"的最小面**——**声明出来**，
    渲染层就**不必 import 内核**，而**要什么一目了然**。
    """

    @property
    def board(self) -> BoardLike: ...

    @property
    def active(self) -> ActiveLike | None: ...

    @property
    def ghostDelta(self) -> tuple[int, int]: ...

    @property
    def hold(self) -> str | None: ...

    @property
    def holdUsed(self) -> bool: ...

    @property
    def next(self) -> tuple[str, ...]: ...

    @property
    def score(self) -> int: ...

    @property
    def lines(self) -> int: ...

    @property
    def level(self) -> int: ...

    @property
    def combo(self) -> int: ...

    @property
    def b2b(self) -> bool: ...

    @property
    def piecesPlaced(self) -> int: ...

    @property
    def grounded(self) -> bool: ...

    @property
    def lockTimer(self) -> int: ...

    @property
    def phase(self) -> str: ...

    @property
    def frame(self) -> int:
        """**逻辑帧号**（**画面角标**）。"""
        ...


#: 画面里一格的**种类**（渲染器只认"是不是普通方块"，**不认具体玩法**）。
#:
#: - `empty`：空；
#: - `block`：普通方块（棋盘值 `1`）；
#: - `special`：**别的非零值**（比如变体 B 的晶体 `8`）；
#: - `active`：活动方块；
#: - `ghost`：幽灵落点。
CellKind = Literal["empty", "block", "special", "active", "ghost"]

#: **棋盘值 `1` = 普通方块**（与终端渲染器 `CELL_BLOCK` 同一口径）。
#: **它住在 `terminal.py`**（**引用它，不复制** —— 复制一份就是两处真相）。

#: 默认的**锁定延迟上限**（**展示用 · 不是规则**）。
#:
#: **为什么放在渲染器**：规则集接口里**没有"锁定延迟"这一条**（`RuleSet` 只
#: 暴露 `gravity_interval` / `shape_cells` / `kick_offsets` / `classify_placement`
#: / `score_clear` / `soft_drop_score` / `scan_clears` / `wrap_cell`）——渲染器
#: **不许自己去规则里翻常量**（那是"渲染器长出第二套规则"的开始）。**所以它
#: 是构造参数**：装配层给多少就画多少（默认 30 = 标准玩法的口径）。
DEFAULT_LOCK_DELAY_FRAMES = 30

#: NEXT 预览**取几个**（契约说快照给全袋，**渲染层取前 5 个当预览**）。
NEXT_PREVIEW_COUNT = 5


@dataclass(frozen=True)
class PiecePreview:
    """**一个预览方块**（HOLD 与 NEXT 用它）。

    - `label`：方块类型（`L` / `J` / `I` / `T` / `O` / `S` / `Z`）；
    - `cells`：**占位格**（以方块自身的 (0, 0) 为锚——**不是棋盘坐标**）。
    """

    label: str
    cells: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class FrameModel:
    """**一幅画面**（纯逻辑 · 可无头比对）。

    - `cells`：**可见行**的逐格画面（`(x, y, kind)` · **行优先**）——**隐藏行
      不画**（出生区）；
    - `previews`：**HOLD 在前**，其后是 NEXT 的前 5 个；
    - `lock_progress`：**锁定延迟进度**（0.0–1.0 · `1.0` = 锁满）。
    """

    width: int
    height: int
    hidden_rows: int
    cells: tuple[tuple[int, int, CellKind], ...]
    previews: tuple[PiecePreview, ...]
    hold: str | None
    hold_used: bool
    next_queue: tuple[str, ...]
    score: int
    lines: int
    level: int
    combo: int
    b2b: bool
    pieces_placed: int
    grounded: bool
    lock_timer: int
    lock_progress: float
    phase: str
    frame: int

    @property
    def game_over(self) -> bool:
        """**是不是已经结束**（结算面板据此弹出）。"""
        return self.phase == "game_over"


class GraphicsRenderer:
    """**图形渲染器的纯逻辑层**（快照 → 画面模型）。

    **它不订阅总线、不发事件、不碰内核**——**只读快照**。
    """

    def __init__(
        self,
        *,
        rule_set: RuleSet,
        width: int,
        height: int,
        hidden_rows: int = 0,
        lock_delay_frames: int = DEFAULT_LOCK_DELAY_FRAMES,
    ) -> None:
        """构造渲染器。

        - `rule_set`：**规则集**（算方块占位格子 · FR-8.2）——**变体 A 把它
          换成自己的实例即可**；
        - `width` / `height`：**棋盘尺寸**（构造参数 · 契约里没有尺寸事件）；
        - `hidden_rows`：**隐藏行数**（出生区 · **不画**）；
        - `lock_delay_frames`：**锁定延迟上限**（**展示用** · 见模块头）。
        """
        if width <= 0 or height <= 0:
            raise ValueError(f"棋盘尺寸必须为正：{width}×{height}")
        if hidden_rows < 0 or hidden_rows >= height:
            raise ValueError(f"隐藏行数不合法：{hidden_rows}（棋盘高 {height}）")
        if lock_delay_frames <= 0:
            raise ValueError(f"锁定延迟上限必须为正：{lock_delay_frames}")
        self._rule_set = rule_set
        self._width = width
        self._height = height
        self._hidden_rows = hidden_rows
        self._lock_delay_frames = lock_delay_frames
        self._model: FrameModel | None = None

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def model(self) -> FrameModel | None:
        """**当前画面模型**（**还没喂过快照就是 `None`**）。"""
        return self._model

    # ------------------------------------------------------------------
    # 喂快照
    # ------------------------------------------------------------------

    def update_snapshot(self, snapshot: SnapshotLike) -> FrameModel:
        """**喂一帧快照** → **返回这一帧的画面模型**（**只读快照，不改它**）。"""
        active_cells = self._active_cells(snapshot)
        ghost_cells = self._ghost_cells(snapshot, active_cells)
        model = FrameModel(
            width=self._width,
            height=self._height,
            hidden_rows=self._hidden_rows,
            cells=self._board_cells(snapshot, active_cells, ghost_cells),
            previews=self._previews(snapshot),
            hold=snapshot.hold,
            hold_used=snapshot.holdUsed,
            next_queue=tuple(snapshot.next),
            score=snapshot.score,
            lines=snapshot.lines,
            level=snapshot.level,
            combo=snapshot.combo,
            b2b=snapshot.b2b,
            pieces_placed=snapshot.piecesPlaced,
            grounded=snapshot.grounded,
            lock_timer=snapshot.lockTimer,
            lock_progress=self._lock_progress(snapshot),
            phase=snapshot.phase,
            frame=snapshot.frame,
        )
        self._model = model
        return model

    # ------------------------------------------------------------------
    # 画面各部分的算法
    # ------------------------------------------------------------------

    def _board_cells(
        self,
        snapshot: SnapshotLike,
        active_cells: frozenset[tuple[int, int]],
        ghost_cells: frozenset[tuple[int, int]],
    ) -> tuple[tuple[int, int, CellKind], ...]:
        """**棋盘逐格画面**（**可见行** · 行优先）。

        **覆盖顺序**：活动方块 > 幽灵 > 棋盘内容（活动方块压在最上面）。
        """
        rows = snapshot.board.cells
        result: list[tuple[int, int, CellKind]] = []
        for y in range(self._hidden_rows, self._height):
            row = rows[y] if y < len(rows) else ()
            for x in range(self._width):
                value = row[x] if x < len(row) else 0
                if (x, y) in active_cells:
                    kind: CellKind = "active"
                elif (x, y) in ghost_cells:
                    kind = "ghost"
                elif value == 0:
                    kind = "empty"
                elif value == BLOCK_VALUE:
                    kind = "block"
                else:
                    kind = "special"
                result.append((x, y, kind))
        return tuple(result)

    def _active_cells(self, snapshot: SnapshotLike) -> frozenset[tuple[int, int]]:
        """**活动方块占的格**（**按规则集算** · FR-8.2）。

        **规则集不认这个方块 / 朝向 → 不画**（不炸：渲染器不该因为画不出一块
        而把整局打断——与终端版同一口径）。
        """
        active = snapshot.active
        if active is None:
            return frozenset()
        cells = self._shape_cells(active.pieceType, active.rotation, snapshot.level)
        return frozenset((active.x + cx, active.y + cy) for cx, cy in cells)

    def _ghost_cells(
        self,
        snapshot: SnapshotLike,
        active_cells: frozenset[tuple[int, int]],
    ) -> frozenset[tuple[int, int]]:
        """**幽灵落点占的格**（= 活动方块 + `ghostDelta`）。

        **`ghostDelta` 是内核给的位移向量**（`(dx, dy)`）——**标准玩法恒为
        `(0, n)`；重力可旋转等变体下 `dx` 亦可为非零**（契约的 `VECTOR2` 口径）。
        """
        if not active_cells:
            return frozenset()
        dx, dy = snapshot.ghostDelta
        if dx == 0 and dy == 0:
            return frozenset()
        return frozenset((x + dx, y + dy) for x, y in active_cells)

    def _previews(self, snapshot: SnapshotLike) -> tuple[PiecePreview, ...]:
        """**HOLD + NEXT 预览**（HOLD 在前 · NEXT 取前 5 个）。"""
        previews: list[PiecePreview] = []
        if snapshot.hold:
            previews.append(
                PiecePreview(
                    label=snapshot.hold,
                    cells=self._shape_cells(snapshot.hold, 0, snapshot.level),
                )
            )
        for piece_type in tuple(snapshot.next)[:NEXT_PREVIEW_COUNT]:
            previews.append(
                PiecePreview(
                    label=piece_type,
                    cells=self._shape_cells(piece_type, 0, snapshot.level),
                )
            )
        return tuple(previews)

    def _lock_progress(self, snapshot: SnapshotLike) -> float:
        """**锁定延迟进度**（0.0–1.0）。

        **口径**：`1 - lockTimer / 上限`——**刚落地是 0**（还没开始锁），**锁满
        是 1.0**。**没落地就是 0.0**（`grounded` 为假时进度条不亮）。
        """
        if not snapshot.grounded:
            return 0.0
        remaining = max(0, snapshot.lockTimer)
        return max(0.0, min(1.0, 1.0 - remaining / self._lock_delay_frames))

    def _shape_cells(
        self,
        piece_type: str,
        rotation: int,
        level: int,
    ) -> tuple[tuple[int, int], ...]:
        """**方块的占位格**（**按规则集算** · 认不出来就当没有）。"""
        ctx = RuleContext(
            frame=0,
            level=level,
            piecesPlaced=0,
            axis=DEFAULT_AXIS,
            stepMs=0,
        )
        try:
            return tuple(self._rule_set.shape_cells(ctx, piece_type, rotation))
        except ValueError:
            return ()


# ---------------------------------------------------------------------------
# tkinter 画笔层
# ---------------------------------------------------------------------------

#: **方块类型 → 颜色**（**只给 HOLD / NEXT 预览用**）。
#:
#: **为什么棋盘上的方块不按类型上色**：快照的棋盘格**只有数值**（`1` = 普通
#: 方块），**不带类型**——要按类型上色就得让内核多发一条事件（**改契约**）。
#: **所以：棋盘上的方块一个颜色，预览按类型上色。**
PIECE_COLORS: dict[str, str] = {
    "I": "#29b8e6",
    "J": "#3a6fd8",
    "L": "#f0913a",
    "O": "#f2cd3a",
    "S": "#4caf50",
    "T": "#a55eea",
    "Z": "#e04a4a",
}

#: 画面配色（**纯展示 · 不是规则**）。
_COLOR_EMPTY = "#1b1b22"
_COLOR_GRID = "#2a2a33"
_COLOR_BLOCK = "#9aa0b5"
_COLOR_SPECIAL = "#b06cf0"
_COLOR_ACTIVE = "#ffd166"
_COLOR_GHOST = "#6b7280"
_COLOR_TEXT = "#e6e6ef"
_COLOR_OVER = "#ff6b6b"


class CanvasLike(Protocol):
    """**tkinter Canvas 的最小面**（**只声明画笔真用到的那几个**）。

    **为什么用协议而不是 `Any`**：协议**把这层能碰的东西钉死**——画笔只许
    建矩形 / 建文字 / 改属性 / 删元素，**碰不到别的**（也就不会顺手把窗口的
    状态改了）。**tkinter 的 `Canvas` 天然满足它**（方法签名一致）。
    """

    def create_rectangle(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        /,
        **kwargs: object,
    ) -> int: ...

    def create_text(self, x: float, y: float, /, **kwargs: object) -> int: ...

    def itemconfig(self, item: int, /, **kwargs: object) -> None: ...

    def delete(self, *tags: str) -> None: ...


class TkPainter:
    """**tkinter 画笔**（把 `FrameModel` 画到 Canvas 上）。

    **它是薄的一层**——**所有画面内容都由 `GraphicsRenderer` 算好**（`FrameModel`
    是纯数据）→ **这一层只负责"把数据变成像素"**。

    **tkinter 是延迟 import 的**：`import tetris_kernel` 在**没有显示器的机器上
    也必须能用**（CI · 无头环境）——所以**这个模块根本不 import tkinter**
    （只 import 标准库的 `tkinter` 类型也不做）。
    """

    def __init__(
        self,
        canvas: CanvasLike,
        renderer: GraphicsRenderer,
        *,
        cell_size: int = 26,
        margin: int = 18,
    ) -> None:
        """构造画笔。

        - `canvas`：**tkinter 的 Canvas**（**满足 `CanvasLike` 即可** · 测试可塞
          假画布）；
        - `renderer`：**纯逻辑渲染器**（画面内容由它算）；
        - `cell_size`：**一格多少像素**；
        - `margin`：**画布边距**。
        """
        self._canvas = canvas
        self._renderer = renderer
        self._cell_size = cell_size
        self._margin = margin
        self._cell_items: list[int] = []
        self._text_items: dict[str, int] = {}
        self._lock_bar: int | None = None
        self._preview_tag = "preview"

    # ------------------------------------------------------------------
    # 画
    # ------------------------------------------------------------------

    def paint(self) -> None:
        """**画一帧**（**读渲染器的画面模型**；还没喂过快照就什么都不画）。"""
        model = self._renderer.model
        if model is None:
            return
        self._ensure_cells(model)
        self._paint_cells(model)
        self._paint_panel(model)

    def _ensure_cells(self, model: FrameModel) -> None:
        """**格子与面板元素只建一次**（之后只改颜色与文字）。

        **为什么不每帧删了重画**：每帧 `delete("all")` 再建 300 个 item，
        **60 fps 下就是每秒 1.8 万次对象创建**——能跑，但纯属浪费。
        """
        if self._cell_items:
            return
        for x, y, _kind in model.cells:
            item = self._canvas.create_rectangle(
                self._cell_x(x),
                self._cell_y(y),
                self._cell_x(x + 1),
                self._cell_y(y + 1),
                fill=_COLOR_EMPTY,
                outline=_COLOR_GRID,
            )
            self._cell_items.append(item)
        for key, text, row in (
            ("score", "分数 0", 0),
            ("lines", "行数 0", 1),
            ("level", "等级 1", 2),
            ("combo", "连击 0", 3),
            ("b2b", "—", 4),
            ("lock", "锁定", 5),
            ("over", "", 7),
        ):
            item = self._canvas.create_text(
                self._panel_x(),
                self._panel_y(row),
                text=text,
                anchor="nw",
                fill=_COLOR_TEXT,
            )
            self._text_items[key] = item
        self._lock_bar = self._canvas.create_rectangle(
            self._panel_x(),
            self._panel_y(5) + 18,
            self._panel_x() + 120,
            self._panel_y(5) + 26,
            fill=_COLOR_GHOST,
            outline=_COLOR_GRID,
        )

    def _paint_cells(self, model: FrameModel) -> None:
        """**逐格上色**（**活动方块最上面 · 幽灵只画轮廓**）。"""
        for (_x, _y, kind), item in zip(model.cells, self._cell_items, strict=True):
            if kind == "empty":
                fill, outline = _COLOR_EMPTY, _COLOR_GRID
            elif kind == "block":
                fill, outline = _COLOR_BLOCK, _COLOR_GRID
            elif kind == "special":
                fill, outline = _COLOR_SPECIAL, _COLOR_GRID
            elif kind == "active":
                fill, outline = _COLOR_ACTIVE, _COLOR_GRID
            else:
                fill, outline = _COLOR_EMPTY, _COLOR_GHOST
            self._canvas.itemconfig(item, fill=fill, outline=outline)

    def _paint_panel(self, model: FrameModel) -> None:
        """**面板**（分数 · HOLD / NEXT · 锁定进度 · 结算）。"""
        self._canvas.itemconfig(self._text_items["score"], text=f"分数 {model.score}")
        self._canvas.itemconfig(self._text_items["lines"], text=f"行数 {model.lines}")
        self._canvas.itemconfig(self._text_items["level"], text=f"等级 {model.level}")
        self._canvas.itemconfig(self._text_items["combo"], text=f"连击 {model.combo}")
        self._canvas.itemconfig(self._text_items["b2b"], text="B2B" if model.b2b else "—")
        self._canvas.itemconfig(self._text_items["lock"], text=f"锁定 {model.lock_timer}")
        self._canvas.itemconfig(
            self._text_items["over"],
            text="游戏结束" if model.game_over else "",
            fill=_COLOR_OVER,
        )
        if self._lock_bar is not None:
            self._canvas.itemconfig(
                self._lock_bar,
                width=int(120 * model.lock_progress),
                fill=_COLOR_ACTIVE,
            )
        self._paint_previews(model)

    def _paint_previews(self, model: FrameModel) -> None:
        """**HOLD / NEXT 预览**（**按类型上色**）。"""
        self._canvas.delete(self._preview_tag)
        hold = model.previews[0] if model.hold else None
        nexts = model.previews[1:] if model.hold else model.previews
        if hold is not None:
            self._paint_preview(hold, self._panel_x(), self._panel_y(6))
        for index, preview in enumerate(nexts):
            self._paint_preview(
                preview,
                self._panel_x() + 150,
                self._panel_y(6) + index * 34,
            )

    def _paint_preview(self, preview: PiecePreview, x: int, y: int) -> None:
        """**画一个预览方块**（**缩略格** · 8 像素一格）。"""
        color = PIECE_COLORS.get(preview.label, _COLOR_BLOCK)
        unit = 8
        for cx, cy in preview.cells:
            self._canvas.create_rectangle(
                x + cx * unit,
                y + cy * unit,
                x + (cx + 1) * unit,
                y + (cy + 1) * unit,
                fill=color,
                outline=_COLOR_GRID,
                tags=self._preview_tag,
            )

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------

    def _cell_x(self, x: int) -> int:
        return self._margin + x * self._cell_size

    def _cell_y(self, y: int) -> int:
        """**棋盘行 → 画布纵坐标**（**隐藏行不画** → 可见行从 0 开始）。"""
        return self._margin + (y - self._hidden_rows()) * self._cell_size

    def _hidden_rows(self) -> int:
        model = self._renderer.model
        return 0 if model is None else model.hidden_rows

    def _panel_x(self) -> int:
        model = self._renderer.model
        width = 0 if model is None else model.width
        return self._margin + (width + 2) * self._cell_size

    def _panel_y(self, row: int) -> int:
        return self._margin + row * 22


__all__ = [
    "BLOCK_VALUE",
    "DEFAULT_LOCK_DELAY_FRAMES",
    "NEXT_PREVIEW_COUNT",
    "PIECE_COLORS",
    "ActiveLike",
    "BoardLike",
    "CanvasLike",
    "CellKind",
    "FrameModel",
    "GraphicsRenderer",
    "PiecePreview",
    "SnapshotLike",
    "TkPainter",
]
