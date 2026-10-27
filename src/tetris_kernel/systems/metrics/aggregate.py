"""指标聚合（系统层 · 指标系统 · M2-S7 · FR-7.2）。

## 口径写死在本模块里（**不塞进事件**）

事件只说"发生了什么"，"怎么统计"是消费方的事（与 ADR-046 同一把尺子）。
所以**每条口径都写死在这里**，并逐条被用例断言——口径漂了，测试立刻红。

| 指标 | 口径 |
| --- | --- |
| `score` | 累计 `score:changed` 的 `delta` 之和 |
| `lines` | 累计 `board:lines-cleared` 的 `count` 之和（**行数**） |
| `clearCount` | `board:lines-cleared` 的**条数**（**组数**——一次消 3 行是 1 组） |
| `piecesPlaced` | `piece:locked` 的条数 |
| `maxCombo` | `score:combo-changed` 的 `combo` **峰值**（初值 0） |
| `moves` / `rotations` | `piece:moved` / `piece:rotated` 中 **`blocked` 为假**的条数 |
| `hardDrops` / `softDrops` | `piece:harddrop` / `piece:softdrop` 的条数 |
| `operations` | 上面四项之和 |
| `frames` | 采样过的帧数（**每收到一个 `clock:tick` 加一**） |
| `filledCells` | 镜像里**实心格**的总数 |
| `holeCount` | **同列相邻实心格之间的空格**总数 |
| `holeRate` | `holeCount ÷ filledCells`（**分母 0 记 0.0**） |
| `scorePerPiece` | `score ÷ piecesPlaced`（**分母 0 记 0.0**） |
| `operationsPerFrame` | `operations ÷ frames`（**分母 0 记 0.0**） |
| `clear.<classKey>` | 各类消行**组数**——类别清单**取自 `rules.tables.CLASS_KEYS`** |

**为什么分类清单取自规则层**：`classKey` 的取值域是**规则集定义的词汇**
（架构 §7.3.1）——指标系统**不另抄一份**名单（抄了就是第二个真相，换规则集
就会分叉）。

## 空洞率（M2-9）的口径

**空洞 = 同列相邻实心格之间的空格**；**棋盘最底下的空地不算**（它不在两个
实心格之间）。分母取**已填格数**，分母 0 记 0.0。

**为什么不用"整块棋盘的空格数"**：事件流给的是**稀疏棋盘镜像**——没被触碰过
的格子**不可知**，稀疏镜像算不出"整块棋盘有多少空格"。而"同列相邻实心格
之间的空格"**只需事件即可算出**，也不必把棋盘尺寸塞进装配配置。

**镜像是怎么维护的**：只靠 `board:changed` 与 `board:collapsed` 两条事件的
`cellChanges`——它们给的是 **(x, y, 新值)**，新值 0 就是"这格空了"。塌落是
整行下移，而**值没变的格子不需要记**（镜像本来就对），所以逐格赋值即可跟住
棋盘。`piece:locked` 的 `cells` 是同一批格子的另一面（只给坐标、不给值），
**不重复维护**——口径单一，镜像只有一条来源。
"""

from __future__ import annotations

from typing import TypeGuard

from ...bus.envelope import Envelope
from ...kernel.drafts import Payload, payload_get
from ...rules.tables import CLASS_KEYS

#: 指标值（契约 `metric:sampled` 的 `metrics` 取值域：`int | float`）。
type MetricValue = int | float

#: 一次采样的全部指标（**指标名 → 值**，顺序固定）。
type Metrics = tuple[tuple[str, MetricValue], ...]

#: 分类消行计数的指标名（**清单取自规则层，不另抄**）。
CLEAR_METRIC_NAMES: tuple[str, ...] = tuple(f"clear.{key}" for key in CLASS_KEYS)

#: 指标名清单（**固定顺序**——每次采样的指标集合与顺序都一样，下游才能逐条对齐）。
METRIC_NAMES: tuple[str, ...] = (
    "score",
    "lines",
    "clearCount",
    "piecesPlaced",
    "maxCombo",
    "moves",
    "rotations",
    "hardDrops",
    "softDrops",
    "operations",
    "frames",
    "filledCells",
    "holeCount",
    "scorePerPiece",
    "operationsPerFrame",
    "holeRate",
) + CLEAR_METRIC_NAMES


def _is_int(value: object) -> TypeGuard[int]:
    """是不是**整数**（**布尔不算**——`isinstance(True, int)` 为真）。

    **同项目其它取整处都排了布尔**（`archive.py` · `format.py` · `trace.py`
    各自的那一处），**这里原来漏了**（审查 L2）。
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _int(payload: Payload, name: str) -> int:
    """取负载里的整数字段；不是整数就当 0（**不糊弄，也不崩**）。"""
    value = payload_get(payload, name)
    return value if _is_int(value) else 0


def _str(payload: Payload, name: str) -> str:
    """取负载里的字符串字段；不是字符串就当空串。"""
    value = payload_get(payload, name)
    return value if isinstance(value, str) else ""


def _flag(payload: Payload, name: str) -> bool:
    """取负载里的布尔字段；不是布尔就当假。"""
    value = payload_get(payload, name)
    return value if isinstance(value, bool) else False


def _ratio(numerator: int, denominator: int) -> float:
    """比值（**分母 0 记 0.0**——不抛异常，也不记成无穷）。"""
    if denominator == 0:
        return 0.0
    return numerator / denominator


class Aggregate:
    """聚合器：把事件流累加成一份指标（**只读事件，不碰任何状态**）。

    **它自己不知道棋盘有多大**——空洞率只用**稀疏镜像**算得出（见模块
    文档）。**它不查内核状态**（M2-D1）：每个数字都来自事件负载。
    """

    def __init__(self) -> None:
        self._score = 0
        self._lines = 0
        self._clear_count = 0
        self._pieces_placed = 0
        self._max_combo = 0
        self._moves = 0
        self._rotations = 0
        self._hard_drops = 0
        self._soft_drops = 0
        self._frames = 0
        self._clears: dict[str, int] = {key: 0 for key in CLASS_KEYS}
        # 稀疏棋盘镜像：(x, y) → 当前值（**0 = 空**）。
        self._cells: dict[tuple[int, int], int] = {}

    # ------------------------------------------------------------------
    # 吃事件
    # ------------------------------------------------------------------

    def observe(self, envelope: Envelope) -> None:
        """吃一条事件，更新累计值（**只读事件负载**）。"""
        event_type = envelope.type
        payload = envelope.payload
        if event_type == "score:changed":
            self._score += _int(payload, "delta")
        elif event_type == "board:lines-cleared":
            self._lines += _int(payload, "count")
            self._clear_count += 1
            class_key = _str(payload, "classKey")
            self._clears[class_key] = self._clears.get(class_key, 0) + 1
        elif event_type == "piece:locked":
            self._pieces_placed += 1
        elif event_type == "score:combo-changed":
            combo = _int(payload, "combo")
            if combo > self._max_combo:
                self._max_combo = combo
        elif event_type == "piece:moved":
            if not _flag(payload, "blocked"):
                self._moves += 1
        elif event_type == "piece:rotated":
            if not _flag(payload, "blocked"):
                self._rotations += 1
        elif event_type == "piece:harddrop":
            self._hard_drops += 1
        elif event_type == "piece:softdrop":
            self._soft_drops += 1
        elif event_type == "board:changed" or event_type == "board:collapsed":
            self._apply_cells(payload)

    def count_frame(self) -> None:
        """帧计数 +1（**每收到一个 `clock:tick` 调一次**）。"""
        self._frames += 1

    def _apply_cells(self, payload: Payload) -> None:
        """把 `cellChanges` 逐格写进镜像（**(x, y, 新值)**；新值 0 = 这格空了）。"""
        changes = payload_get(payload, "cellChanges")
        if not isinstance(changes, tuple):
            return
        for change in changes:
            if not isinstance(change, tuple) or len(change) != 3:
                continue
            x, y, value = change
            # **布尔不算整数**（审查 L2）：`isinstance(True, int)` 为真——真值
            # 会被当成实心格写进镜像，**而契约的格子取值是整数**（0 = 空）。
            # 坐标也一样：`True == 1`，布尔坐标会**悄悄别名**到整数那一格。
            if _is_int(x) and _is_int(y) and _is_int(value):
                self._cells[(x, y)] = value

    # ------------------------------------------------------------------
    # 出数
    # ------------------------------------------------------------------

    def sample(self) -> Metrics:
        """当前累计值（**指标名与顺序固定**）。"""
        holes, filled = self.hole_stats()
        operations = self._moves + self._rotations + self._hard_drops + self._soft_drops
        values: dict[str, MetricValue] = {
            "score": self._score,
            "lines": self._lines,
            "clearCount": self._clear_count,
            "piecesPlaced": self._pieces_placed,
            "maxCombo": self._max_combo,
            "moves": self._moves,
            "rotations": self._rotations,
            "hardDrops": self._hard_drops,
            "softDrops": self._soft_drops,
            "operations": operations,
            "frames": self._frames,
            "filledCells": filled,
            "holeCount": holes,
            "scorePerPiece": _ratio(self._score, self._pieces_placed),
            "operationsPerFrame": _ratio(operations, self._frames),
            "holeRate": _ratio(holes, filled),
        }
        for key in CLASS_KEYS:
            values[f"clear.{key}"] = self._clears.get(key, 0)
        return tuple((name, values[name]) for name in METRIC_NAMES)

    def hole_stats(self) -> tuple[int, int]:
        """**（空洞数 · 已填格数）**。

        **空洞 = 同列相邻实心格之间的空格**——棋盘最底下空地不算（它不在两个
        实心格之间）。

        **为什么这样定义**：事件流给的是**稀疏镜像**——没被触碰过的格子
        **不可知**，整块棋盘的空格数算不出来；而"相邻实心格之间的空格"**只
        需事件即可算出**，也不必知道棋盘有多大。
        """
        columns: dict[int, list[int]] = {}
        filled = 0
        for (x, y), value in self._cells.items():
            if value == 0:
                continue
            filled += 1
            columns.setdefault(x, []).append(y)
        holes = 0
        for rows in columns.values():
            rows.sort()
            for index in range(1, len(rows)):
                holes += rows[index] - rows[index - 1] - 1
        return holes, filled


__all__ = [
    "CLEAR_METRIC_NAMES",
    "METRIC_NAMES",
    "Aggregate",
    "MetricValue",
    "Metrics",
]
