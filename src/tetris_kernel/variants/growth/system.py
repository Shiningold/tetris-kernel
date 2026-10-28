"""变体 B「落地后生长」的**系统实现**（变体层 · M4-S4 · spec-M4 §7.3）。

## 它是什么

**一个订阅事件的系统**：方块落地（`piece:locked`）后，**经 `writeCells` 请求写入**
在它上方长出**晶体**（FR-11.1 · 外部改变棋盘的**唯一合法**通道）。

- **只读事件**（自己不写棋盘）· **只经命令通道写**（`submit_cells`）；
- **内核与规则集零改动**——**全部代码落在新增文件里**（M4-D1 / `G4-1`）。

## 变体层的三条硬约束（架构 §2.3 / §4.1 / §7.7）

**变体层禁止 import `kernel` / `bus` / `adapters` / `host`**（**传递依赖也禁**）→

| 要做的事 | 怎么做到（不 import 禁用层） |
| --- | --- |
| **订阅事件** | **注册表按声明接上**（`on_event` 是入口）——**不用碰总线** |
| **派命令** | **系统层代劳**：`CommandChannel.submit_cells`——**命令的形状在系统层拼** |
| **看事件负载** | **自己按字段名取**（`_payload_get`）——几行的事，不值得破红线 |

**事件与命令通道的形状由变体自己用 `Protocol` 声明**（`EventLike` / `CellSubmitter` /
`GrowthInstallContext`）——**结构化类型**：真实的信封与真实的通道都满足它们。

## 生长规则（**变体自定义语义** · 写在这个变体自己的 README 里）

- **触发**：方块锁定（`piece:locked`）；
- **长在哪**：**锁定方块占据的每一列**，**该列最高的锁定格正上方**，往上取**连续空格**；
- **长几格**：**`1 + 锁定次数 // 4`**（**每 4 次锁定多长一格** → 压力递增）；
- **写成什么**：**晶体值 `8`**（普通方块在棋盘上是 `1` → 渲染器与录像都分得清）；
- **只写空格**：不够就少长，**一格都不够就不写**——**不报错、不硬塞**。

## 为什么写入要"待写清单 + 每帧重试"

**内核的写入条件是"不得改到活动方块占据的格子"**（`write_condition_failed`），
**一批里有一格撞上就整批被拒**；而**命令在下一帧才执行**（分发期间派发的命令进入
队列）——**那时活动方块已经是新方块**，甚至被玩家挪过。

**所以**：算出的目标格**先进待写清单**，**每帧（`clock:tick`）重试一次**，
**写成功（`board:changed` 的 cause 是 `writeCells`）才从清单里划掉**，
**重试超过上限就放弃那一格**——**宁可少长，不写错**。
"""

from __future__ import annotations

from typing import Protocol

from ...rules.tables import SHAPE_TABLE

# ---------------------------------------------------------------------------
# 契约里的名字（**字面量只此一份** · 与 `systems/rotations` 同款做法）
# ---------------------------------------------------------------------------

#: 方块锁定（**触发生长**）。
PIECE_LOCKED_EVENT = "piece:locked"
#: 棋盘变化（**维持自己的盘面镜像** · 也是"写入成功"的确认）。
BOARD_CHANGED_EVENT = "board:changed"
#: 塌落（**盘面镜像要跟得上** —— 消行后的下移与补空都在这里）。
BOARD_COLLAPSED_EVENT = "board:collapsed"
#: 新方块出生（**活动方块占哪几格** → 写的时候要避开）。
PIECE_SPAWNED_EVENT = "piece:spawned"
#: 时钟滴答（**每帧重试一次待写清单**）。
CLOCK_TICK_EVENT = "clock:tick"

#: 受控写入的命令名（**从契约取**，不复制字面量 —— 架构 §4.4 末条）。
WRITE_CELLS_COMMAND: str = "writeCells"

#: 晶体在棋盘上的取值（**普通方块是 `1`** → 两者分得清）。
CRYSTAL_VALUE = 8

#: 压力曲线：**每 4 次锁定多长一格**。
GROWTH_STEP_LOCKS = 4

#: 待写清单的上限（**单帧写入配额之外再加一道自己的闸**）。
MAX_PENDING_CELLS = 32

#: 同一格最多重试几次（**超过就放弃那一格** —— 宁可少长，不写错）。
MAX_ATTEMPTS_PER_CELL = 3

#: 单批最多写几格（**一批撞上活动方块就整批被拒** → 批次别太大）。
MAX_CELLS_PER_BATCH = 8


# ---------------------------------------------------------------------------
# 变体自己声明的形状（**变体层不许 import 总线与内核**）
# ---------------------------------------------------------------------------


class EventLike(Protocol):
    """变体看到的**事件**的最小形状。

    **为什么自己声明**：变体层禁 import `bus`（架构 §2.3）→ 拿不到
    `bus.envelope.Envelope`。**结构化类型**：真实的信封满足这两个属性 →
    注册表把信封递过来时**类型对得上**。
    """

    @property
    def type(self) -> str:
        """事件类型名。"""
        ...

    @property
    def payload(self) -> tuple[tuple[str, object], ...]:
        """事件负载（**有序字段对**）。"""
        ...


class CellSubmitter(Protocol):
    """能派 `writeCells` 的东西（**最小形状**）。

    **真实实现是 `systems.context.CommandChannel`**——它自己把命令拼出来
    （**变体层拿不到内核的 `Command` 类型**）。
    """

    def submit_cells(
        self,
        cells: tuple[tuple[int, int, int], ...],
        source: str = ...,
    ) -> None:
        """派一次受控写入。"""
        ...


class GrowthInstallContext(Protocol):
    """装这个变体时递给工厂的**上下文**的最小形状（**只要命令通道**）。"""

    @property
    def commands(self) -> CellSubmitter:
        """派命令的通道。"""
        ...


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _payload_get(payload: tuple[tuple[str, object], ...], name: str) -> object | None:
    """按字段名取负载值（**找不到就是 `None`**）。

    **为什么自己写**：变体层禁 import 内核（`kernel.drafts.payload_get` 在冻结区
    的邻层）——**几行的事，不值得破红线**。
    """
    for key, value in payload:
        if key == name:
            return value
    return None


def _as_int(value: object | None) -> int | None:
    """把负载里的值取成 `int`（**不是 `int` 就是没有**）。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_cells(value: object | None) -> tuple[tuple[int, int], ...]:
    """把负载里的坐标清单取成 `(x, y)` 清单（**取不到就是空的**）。"""
    if not isinstance(value, tuple):
        return ()
    cells: list[tuple[int, int]] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        x, y = item
        if isinstance(x, int) and isinstance(y, int):
            cells.append((x, y))
    return tuple(cells)


def _as_write_cells(value: object | None) -> tuple[tuple[int, int, int], ...]:
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
# 系统本体
# ---------------------------------------------------------------------------


class GrowthSystem:
    """落地后生长系统（**只读事件 · 只经命令通道写**）。

    **跨帧状态自己持有**：盘面镜像 · 待写清单 · 重试计数 · 锁定次数。
    """

    def __init__(self, submit: CellSubmitter) -> None:
        self._submit = submit
        #: **盘面镜像**（`(x, y)` → 值）：**只记非 0 的格**（"没记"就是空）。
        self._board: dict[tuple[int, int], int] = {}
        #: **待写清单**：还没确认写进去的格子。
        self._pending: list[tuple[int, int, int]] = []
        #: 每个待写格已经重试过几次。
        self._attempts: dict[tuple[int, int], int] = {}
        #: **活动方块占的格**（出生时算一次 —— 玩家挪动它就不知道了，**宁可多避开**）。
        self._active: frozenset[tuple[int, int]] = frozenset()
        #: 已锁定的方块数（**压力曲线**的进度）。
        self._locked_count = 0
        #: 已写进棋盘的晶体格数。
        self._crystal_count = 0

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def crystal_count(self) -> int:
        """已写进棋盘的**晶体格数**。"""
        return self._crystal_count

    @property
    def locked_count(self) -> int:
        """已锁定的**方块数**。"""
        return self._locked_count

    @property
    def pending_count(self) -> int:
        """**待写清单**的长度（还没写进去的）。"""
        return len(self._pending)

    # ------------------------------------------------------------------
    # 订阅入口（**注册表按声明接上** —— 变体不碰总线）
    # ------------------------------------------------------------------

    def on_event(self, envelope: EventLike) -> None:
        """吃一条事件（**只认自己声明的那几条**）。"""
        event_type = envelope.type
        if event_type == BOARD_CHANGED_EVENT:
            self._on_board_changed(envelope)
        elif event_type == BOARD_COLLAPSED_EVENT:
            self._on_board_changed(envelope)
        elif event_type == PIECE_SPAWNED_EVENT:
            self._on_piece_spawned(envelope)
        elif event_type == PIECE_LOCKED_EVENT:
            self._on_piece_locked(envelope)
        elif event_type == CLOCK_TICK_EVENT:
            self._flush()

    # ------------------------------------------------------------------
    # 盘面镜像
    # ------------------------------------------------------------------

    def _on_board_changed(self, envelope: EventLike) -> None:
        """棋盘变了 → **更新镜像**；**写入成功** → **把格从待写清单里划掉**。"""
        payload = envelope.payload
        changes = _as_write_cells(_payload_get(payload, "cellChanges"))
        cause = _payload_get(payload, "cause")
        for x, y, value in changes:
            if value == 0:
                self._board.pop((x, y), None)
            else:
                self._board[(x, y)] = value
        if cause == WRITE_CELLS_COMMAND:
            # **写成功了** → 确认这些格（**晶体的计数也在这里**）。
            for x, y, _value in changes:
                self._pending = [cell for cell in self._pending if cell[:2] != (x, y)]
                self._attempts.pop((x, y), None)
                if _value == CRYSTAL_VALUE:
                    self._crystal_count += 1

    def _on_piece_spawned(self, envelope: EventLike) -> None:
        """新方块出生 → **记住它占哪几格**（写的时候要避开）。"""
        payload = envelope.payload
        piece_type = _payload_get(payload, "pieceType")
        x = _as_int(_payload_get(payload, "x"))
        y = _as_int(_payload_get(payload, "y"))
        if not isinstance(piece_type, str) or x is None or y is None:
            return
        cells = SHAPE_TABLE["down"].get(piece_type, {}).get(0, ())
        self._active = frozenset((x + cx, y + cy) for cx, cy in cells)

    # ------------------------------------------------------------------
    # 生长
    # ------------------------------------------------------------------

    def _on_piece_locked(self, envelope: EventLike) -> None:
        """方块锁定 → **算出目标格**，放进待写清单。"""
        cells = _as_cells(_payload_get(envelope.payload, "cells"))
        if not cells:
            return
        self._locked_count += 1
        height = 1 + self._locked_count // GROWTH_STEP_LOCKS
        # **每一列的最高的锁定格** → 从它正上方往上长。
        top_per_column: dict[int, int] = {}
        for x, y in cells:
            if x not in top_per_column or y < top_per_column[x]:
                top_per_column[x] = y
        for x, top in top_per_column.items():
            for target_y in range(top - 1, top - 1 - height, -1):
                if target_y < 0:
                    break
                if (x, target_y) in self._board or (x, target_y) in self._active:
                    break
                self._enqueue((x, target_y, CRYSTAL_VALUE))

    def _enqueue(self, cell: tuple[int, int, int]) -> None:
        """把一个格子放进待写清单（**去重 · 有上限**）。"""
        if cell[:2] in self._attempts:
            return
        if any(pending[:2] == cell[:2] for pending in self._pending):
            return
        if len(self._pending) >= MAX_PENDING_CELLS:
            return
        self._pending.append(cell)

    def _flush(self) -> None:
        """**每帧重试一次**待写清单（**写成功才算数**）。

        **为什么重试**：内核的写入条件是"不得改到活动方块占据的格子"——
        **一批里有一格撞上就整批被拒**；而命令**下一帧才执行**，那时活动方块
        已经被玩家挪过。**所以每帧再试一次**，**写成功（`board:changed`）才划掉**。
        """
        if not self._pending:
            return
        # **先放弃试满的**（宁可少长，不写错）——**必须在提交之前**：
        # 先提交再过滤，等于**多试了一次**。
        self._pending = [
            cell
            for cell in self._pending
            if self._attempts.get(cell[:2], 0) < MAX_ATTEMPTS_PER_CELL
        ]
        usable = [
            cell
            for cell in self._pending
            if cell[:2] not in self._board and cell[:2] not in self._active
        ]
        if not usable:
            return
        batch = tuple(usable[:MAX_CELLS_PER_BATCH])
        for cell in batch:
            self._attempts[cell[:2]] = self._attempts.get(cell[:2], 0) + 1
        self._submit.submit_cells(batch, source="variant:growth")


__all__ = [
    "BOARD_CHANGED_EVENT",
    "BOARD_COLLAPSED_EVENT",
    "CLOCK_TICK_EVENT",
    "CRYSTAL_VALUE",
    "CellSubmitter",
    "EventLike",
    "GrowthInstallContext",
    "GrowthSystem",
    "PIECE_LOCKED_EVENT",
    "PIECE_SPAWNED_EVENT",
    "WRITE_CELLS_COMMAND",
]
