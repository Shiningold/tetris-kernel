"""终端渲染器（适配层 · M4-S6 · FR-8.1 / FR-8.2 · M4-D2 · spec-M4 §7.6）。

## 它是什么

**一个只消费事件与快照的绘图器**：把内核发生的事情画成终端画面。

- **不写任何状态**（M4-D2）——它连内核对象都拿不到；
- **不用只读视图通道**（架构 §4.1）——**它拿不到 `ctx.view()`**；
- **画面由事件推导**（FR-8.2）：棋盘由 `board:changed` / `board:collapsed` 累积，
  活动方块由 `piece:*` 事件累积，计分与相位由 `state:changed` 给出。

## 界面与内核之间只有三个接口

| 接口 | 从哪来 | 为什么 |
| --- | --- | --- |
| **事件** | 宿主把总线上的事件递进来（`on_event`） | 画面的一切都从事件来 |
| **规则集** | 构造时给一份 | **格子按规则集的 `shape_cells` 算**（FR-8.2） |
| **棋盘尺寸** | 构造时给 | **尺寸不是事件**（契约里没有）——**不是"问内核要"** |

**换一个渲染器不需要改内核**——这三个接口就够了（S6 完成判据）。

## 活动方块：事件推的，快照校正的

**方块的占位格子按规则集算**（`shape_cells(ctx, pieceType, rotation)`）——**纯
函数**，渲染器自己算。

**但有一处事件推不全**：内核的**暂存换块**路径（暂存槽非空时取回一块）**只发
`piece:held`，不发 `piece:spawned`**（`kernel/core.py` 的 `_cmd_hold`）→ **新
方块的位置没有事件告知**。**内核是冻结区**（M4-D1）→ **改不得**。

**所以宿主每帧把帧末快照里的活动方块校正一次**（`sync_active`）——**快照是
规格明写允许的输入**（spec §7.6：「只消费事件与快照」）· **这不是"只读视图
通道"**（那是一条通往内核的活通道 · 架构 §4.1）：**快照是一个值**，宿主手里
本来就有。

**不校正会怎样**：暂存换块之后，新方块会**停在上一块的位置**（甚至画错类型）
——**玩家每按一次暂存键就会看见一次**。

## 为什么要"能重建"这件事本身可测

**画面可以由事件重建**（FR-8.2）——所以**喂假事件流就能测画面**（N3），
**不必起内核**。`frame_text()` 是纯函数（**只读模型，不碰流**），`draw()` 才
写终端。
"""

from __future__ import annotations

import sys
from typing import Protocol, TextIO

from ...contracts.rules import RuleContext
from ...rules.interface import RuleSet

# ---------------------------------------------------------------------------
# 格子画法
# ---------------------------------------------------------------------------

#: 空格的画法（**两字符宽**，一个格子一格）。
CELL_EMPTY = "  "
#: 普通方块的画法（**棋盘值 `1`**）。
CELL_BLOCK = "[]"
#: **别的非零值**的画法（**比如变体 B 的晶体 `8`**）——**渲染器不认识具体玩法**
#: （M4-D4：变体自己玩自己的），**它只认"是不是普通方块"**。
CELL_SPECIAL = "**"
#: 活动方块的画法（**盖在棋盘上面**）。
CELL_ACTIVE = "@@"

#: 棋盘上"普通方块"的值（**别的非零值走 `CELL_SPECIAL`**）。
BLOCK_VALUE = 1

#: 重力轴（**内核恒传 `down`** · `kernel/core.py` 的 `GRAVITY_AXIS`）——
#: **规则集实例知道自己是什么轴**（变体 A 绑在实例上），**`ctx.axis` 只是
#: 契约要求的字段**。
DEFAULT_AXIS = "down"


# ---------------------------------------------------------------------------
# 输入形状
# ---------------------------------------------------------------------------


class EventLike(Protocol):
    """渲染器看到的**事件**的最小形状（**结构化类型**）。

    **为什么自己声明**：适配层虽然可以 import 总线，但**渲染器只需要"事件长
    什么样"**——**声明最小形状，它就能脱离总线单测**（N3：喂假事件流）。
    """

    @property
    def type(self) -> str:
        """事件类型名。"""
        ...

    @property
    def payload(self) -> tuple[tuple[str, object], ...]:
        """事件负载（**有序字段对**）。"""
        ...

    @property
    def frame(self) -> int:
        """事件所在的逻辑帧号。"""
        ...


def _payload_get(payload: tuple[tuple[str, object], ...], name: str) -> object | None:
    """按字段名取负载值（**找不到就是 `None`**）。"""
    for key, value in payload:
        if key == name:
            return value
    return None


def _as_int(value: object | None) -> int | None:
    """把负载里的值取成 `int`（**不是 `int` 就是没有**）。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_str(value: object | None) -> str | None:
    """把负载里的值取成 `str`（**不是 `str` 就是没有**）。"""
    return value if isinstance(value, str) else None


def _as_cells(value: object | None) -> tuple[tuple[int, int, int], ...]:
    """把负载里的变化清单取成 `(x, y, 新值)` 清单。"""
    if not isinstance(value, tuple):
        return ()
    cells: list[tuple[int, int, int]] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 3:
            continue
        x, y, new_value = item
        if (
            isinstance(x, int)
            and isinstance(y, int)
            and isinstance(new_value, int)
            and not isinstance(new_value, bool)
        ):
            cells.append((x, y, new_value))
    return tuple(cells)


# ---------------------------------------------------------------------------
# 渲染器
# ---------------------------------------------------------------------------


class TerminalRenderer:
    """终端渲染器（**纯订阅者**）。

    **模型**：棋盘镜像（`dict[(x, y)] = 值`）· 活动方块（类型 · 朝向 · 坐标）·
    计分与相位 · 预览队列 · 暂存槽。
    """

    def __init__(
        self,
        *,
        rule_set: RuleSet,
        width: int,
        height: int,
        hidden_rows: int = 0,
        stream: TextIO | None = None,
    ) -> None:
        """构造渲染器。

        - `rule_set`：**规则集**（算活动方块的占位格子 · FR-8.2）——**变体 A
          把它换成自己的实例即可**（渲染器跟着换规则集就行）；
        - `width` / `height`：**棋盘尺寸**（**构造参数**：契约里没有尺寸事件，
          **也不是"问内核要"**）；
        - `hidden_rows`：**隐藏行数**（出生区 · **不画**）；
        - `stream`：**输出流**（默认 `sys.stdout`）——**可注入**（测试用
          `io.StringIO`，**不碰真终端**）。
        """
        if width <= 0 or height <= 0:
            raise ValueError(f"棋盘尺寸必须为正：{width}×{height}")
        if hidden_rows < 0 or hidden_rows >= height:
            raise ValueError(f"隐藏行数不合法：{hidden_rows}（棋盘高 {height}）")
        self._rule_set = rule_set
        self._width = width
        self._height = height
        self._hidden_rows = hidden_rows
        self._stream = sys.stdout if stream is None else stream

        #: **棋盘镜像**（**只记非 0 的格**——"没记"就是空）。
        self._board: dict[tuple[int, int], int] = {}
        #: **活动方块**：`（类型 · 朝向 · x · y）`，**没有就是 `None`**。
        self._active: tuple[str, int, int, int] | None = None
        self._score = 0
        self._lines = 0
        self._level = 1
        self._phase = ""
        self._next: tuple[str, ...] = ()
        self._hold: str | None = None

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def board(self) -> dict[tuple[int, int], int]:
        """**棋盘镜像**（**只读视图**：返回副本，外部改不到里面去）。"""
        return dict(self._board)

    @property
    def active(self) -> tuple[str, int, int, int] | None:
        """**活动方块**（类型 · 朝向 · x · y）。"""
        return self._active

    @property
    def active_cells(self) -> frozenset[tuple[int, int]]:
        """**活动方块占的格**（**按规则集算** · 见 `_active_cells`）。"""
        return self._active_cells()

    @property
    def score(self) -> int:
        """当前分数（**由 `state:changed` 给**）。"""
        return self._score

    @property
    def lines(self) -> int:
        """已消行数（**由 `state:changed` 给**）。"""
        return self._lines

    @property
    def level(self) -> int:
        """当前等级（**由 `state:changed` 给**）。"""
        return self._level

    @property
    def phase(self) -> str:
        """当前相位（**由 `state:changed` 给**）。"""
        return self._phase

    @property
    def next_queue(self) -> tuple[str, ...]:
        """预览队列（**由 `state:changed` 给**）。"""
        return self._next

    @property
    def hold(self) -> str | None:
        """暂存槽里的方块（**由 `state:changed` 给**）。"""
        return self._hold

    # ------------------------------------------------------------------
    # 订阅入口
    # ------------------------------------------------------------------

    def on_event(self, envelope: EventLike) -> None:
        """吃一条事件（**只认自己用得上的那几条**）。

        **画面完全由事件推导**（FR-8.2）——**这里没有任何"只有内部才拿得到"的
        信息**。
        """
        event_type = envelope.type
        payload = envelope.payload
        if event_type == "board:changed":
            self._apply_cells(_as_cells(_payload_get(payload, "cellChanges")))
        elif event_type == "board:collapsed":
            self._apply_cells(_as_cells(_payload_get(payload, "cellChanges")))
        elif event_type == "piece:spawned":
            self._on_spawned(payload)
        elif event_type == "piece:moved":
            self._on_moved(payload)
        elif event_type == "piece:rotated":
            self._on_rotated(payload)
        elif event_type == "piece:kicked":
            self._on_kicked(payload)
        elif event_type == "piece:softdrop":
            self._on_softdrop(payload)
        elif event_type == "piece:harddrop":
            self._on_harddrop(payload)
        elif event_type == "piece:locked":
            # **锁定**：格子随 `board:changed`（cause=lock）进棋盘 → **活动方块
            # 没了**。
            self._active = None
        elif event_type == "piece:held":
            stored = _as_str(_payload_get(payload, "stored"))
            if stored is not None:
                self._hold = stored
        elif event_type == "state:changed":
            self._on_state_changed(payload)

    def _apply_cells(self, cells: tuple[tuple[int, int, int], ...]) -> None:
        """把一批格子变化并进棋盘镜像（**值为 0 = 清空**）。"""
        for x, y, value in cells:
            if value == 0:
                self._board.pop((x, y), None)
            else:
                self._board[(x, y)] = value

    def _on_spawned(self, payload: tuple[tuple[str, object], ...]) -> None:
        """新方块出生（**初始朝向恒为 0** —— 内核出生时就是 0）。"""
        piece_type = _as_str(_payload_get(payload, "pieceType"))
        x = _as_int(_payload_get(payload, "x"))
        y = _as_int(_payload_get(payload, "y"))
        if piece_type is None or x is None or y is None:
            return
        self._active = (piece_type, 0, x, y)

    def _on_moved(self, payload: tuple[tuple[str, object], ...]) -> None:
        """移动（**受阻时位置没变** —— 内核受阻时 `toX/toY` 就等于原坐标）。"""
        to_x = _as_int(_payload_get(payload, "toX"))
        to_y = _as_int(_payload_get(payload, "toY"))
        if self._active is None or to_x is None or to_y is None:
            return
        piece_type, rotation, _, _ = self._active
        self._active = (piece_type, rotation, to_x, to_y)

    def _on_rotated(self, payload: tuple[tuple[str, object], ...]) -> None:
        """旋转（**受阻时朝向没变**）。"""
        blocked = _payload_get(payload, "blocked")
        to_rotation = _as_int(_payload_get(payload, "toRotation"))
        if self._active is None or blocked or to_rotation is None:
            return
        piece_type, _, x, y = self._active
        self._active = (piece_type, to_rotation, x, y)

    def _on_kicked(self, payload: tuple[tuple[str, object], ...]) -> None:
        """踢墙（**位移是相对的** —— 内核把偏移加在原坐标上）。"""
        offset_x = _as_int(_payload_get(payload, "offsetX"))
        offset_y = _as_int(_payload_get(payload, "offsetY"))
        if self._active is None or offset_x is None or offset_y is None:
            return
        piece_type, rotation, x, y = self._active
        self._active = (piece_type, rotation, x + offset_x, y + offset_y)

    def _on_softdrop(self, payload: tuple[tuple[str, object], ...]) -> None:
        """软降（**给的是降完之后的坐标**）。"""
        x = _as_int(_payload_get(payload, "x"))
        y = _as_int(_payload_get(payload, "y"))
        if self._active is None or x is None or y is None:
            return
        piece_type, rotation, _, _ = self._active
        self._active = (piece_type, rotation, x, y)

    def _on_harddrop(self, payload: tuple[tuple[str, object], ...]) -> None:
        """硬降（**给的是落点坐标**）。"""
        x = _as_int(_payload_get(payload, "x"))
        y = _as_int(_payload_get(payload, "y"))
        if self._active is None or x is None or y is None:
            return
        piece_type, rotation, _, _ = self._active
        self._active = (piece_type, rotation, x, y)

    def _on_state_changed(self, payload: tuple[tuple[str, object], ...]) -> None:
        """`state:changed` → **计分与相位由它给**（spec §7.6）。"""
        score = _as_int(_payload_get(payload, "score"))
        if score is not None:
            self._score = score
        lines = _as_int(_payload_get(payload, "lines"))
        if lines is not None:
            self._lines = lines
        level = _as_int(_payload_get(payload, "level"))
        if level is not None:
            self._level = level
        phase = _as_str(_payload_get(payload, "phase"))
        if phase is not None:
            self._phase = phase
        hold = _payload_get(payload, "hold")
        if isinstance(hold, str) or hold is None:
            self._hold = hold
        queue = _payload_get(payload, "next")
        if isinstance(queue, tuple):
            self._next = tuple(item for item in queue if isinstance(item, str))

    # ------------------------------------------------------------------
    # 快照校正
    # ------------------------------------------------------------------

    def sync_active(self, piece_type: str | None, rotation: int, x: int, y: int) -> None:
        """**按帧末快照校正活动方块**（**可选** · 见模块头的"活动方块"一节）。

        **为什么需要**：内核的**暂存换块**路径只发 `piece:held`、**不发
        `piece:spawned`** → **新方块的位置没有事件告知**（**内核是冻结区**，
        改不得）。

        **为什么合法**：**快照是规格明写允许的输入**（spec §7.6「只消费事件与
        快照」）——**它是一个值**，**不是通往内核的活通道**（架构 §4.1 禁的是
        `ctx.view()` 那种通道）。
        """
        if piece_type is None:
            self._active = None
            return
        self._active = (piece_type, rotation, x, y)

    # ------------------------------------------------------------------
    # 画面
    # ------------------------------------------------------------------

    def _rule_context(self) -> RuleContext:
        """递给规则集的上下文（**形状表不依赖它** —— 只有轴是契约字段）。"""
        return RuleContext(
            frame=0,
            level=self._level,
            piecesPlaced=0,
            axis=DEFAULT_AXIS,
            stepMs=0,
        )

    def _active_cells(self) -> frozenset[tuple[int, int]]:
        """**活动方块占的格**（**按规则集算** · FR-8.2）。

        **规则集说了算**——**变体 A 的旋转重力下照样算对**（渲染器跟着换规则
        集即可，**内核一个字节都不用改**）。
        """
        if self._active is None:
            return frozenset()
        piece_type, rotation, x, y = self._active
        try:
            cells = self._rule_set.shape_cells(self._rule_context(), piece_type, rotation)
        except ValueError:
            # **规则集不认这个方块 / 朝向** → **不画**（**不炸**：渲染器不该
            # 因为画不出一块而把整局打断）。
            return frozenset()
        return frozenset((x + cx, y + cy) for cx, cy in cells)

    def frame_text(self) -> str:
        """**当前画面**（**纯函数**：只读模型，**不碰流**）。

        **这一层是"可测"的落点**（N3）：**喂假事件流 → 比对这段文本**，不必起
        内核。
        """
        active_cells = self._active_cells()
        lines: list[str] = [
            f"分数: {self._score}   行数: {self._lines}   等级: {self._level}"
            f"   相位: {self._phase}",
            f"下一个: {' '.join(self._next)}   暂存: {self._hold if self._hold else '-'}",
        ]
        for y in range(self._hidden_rows, self._height):
            lines.append(self._row_text(y, active_cells))
        return "\n".join(lines)

    def _row_text(self, y: int, active_cells: frozenset[tuple[int, int]]) -> str:
        """一行棋盘（**两个字符一格** · 行尾空白去掉）。"""
        parts: list[str] = []
        for x in range(self._width):
            if (x, y) in active_cells:
                parts.append(CELL_ACTIVE)
                continue
            value = self._board.get((x, y), 0)
            if value == 0:
                parts.append(CELL_EMPTY)
            elif value == BLOCK_VALUE:
                parts.append(CELL_BLOCK)
            else:
                parts.append(CELL_SPECIAL)
        return "".join(parts).rstrip()

    def draw(self) -> None:
        """把当前画面写进输出流（**ANSI 光标归位 + 画完清到行尾**）。

        **不用整屏清除**（`\x1b[2J`）——**每帧清屏会闪**；**归位重画 + 清到行尾**
        才是终端画面的常规做法。
        """
        text = self.frame_text()
        self._stream.write("\x1b[H")
        self._stream.write(text)
        self._stream.write("\x1b[J\n")
        self._stream.flush()


__all__ = [
    "BLOCK_VALUE",
    "CELL_ACTIVE",
    "CELL_BLOCK",
    "CELL_EMPTY",
    "CELL_SPECIAL",
    "DEFAULT_AXIS",
    "EventLike",
    "TerminalRenderer",
]
