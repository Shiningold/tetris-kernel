"""AI 的落点搜索（系统层 · M4-S9 · spec-M4 §7.8 · 架构 §7.5 · **M5-S2 等价提速**）。

## 它是什么

**纯 Python 的启发式落点搜索**：枚举**固定**数量的候选落点 → 按**固定权重**打分
→ 取最高分那一个 → 换成一串命令。

- **不引 numpy**（M4-D3）：少一个依赖 · 天然确定；
- **不用随机源**：决策完全由局面决定 → **同一局面永远同一个决策**；
- **不碰内核**：本模块只认「一块棋盘 + 一块方块」→ **可以脱离内核单独测**
  （N3：可测试性）。

## 预算固定（M4-D3 · ADR-028 · 架构 §7.5）

| 预算项 | 取值 | 为什么固定 |
| --- | --- | --- |
| **候选数** | `MAX_CANDIDATES`（**枚举数** = 64） | 按耗时自适应 → 同一录像在不同机器上重放不同 |
| **搜索深度** | `SEARCH_DEPTH = 1`（只看当前这一块） | 前瞻越深越慢；**深度也是预算** |
| **权重** | 五个**整数**常量 | 浮点在不同平台上的舍入可能不同 → **同一局面算出不同分** |

**本模块没有 `time`**——**按耗时自适应就是从这里进来的**。搜完就出结果，
慢一点也只是慢一点，**结果不变**。

## 几何口径与内核一致（架构 §7.3）

- **形状表**取 `SHAPE_TABLE["down"]`（规则层的表，**内核与 AI 共用同一份**）；
- **碰撞**：越界（左右 / 下方）算冲突，**棋盘之上（y < 0）不算**（出生区）；
- **踢墙**：候选**按顺序**试，第一个落得下的就用它（与内核同一条纪律）。

**为什么 AI 只认 `down` 轴**：变体 A 的横向重力表在 `variants/gravity/tables.py`
里，而**变体层不许被系统层 import**（横向依赖 · 架构 §2.3）→ **AI 的几何按
标准玩法算**。M4 的 AI 与变体 A 的组合**不在本阶段范围内**（spec-M4 §7.8 只要求
AI 与人类同通道 · 固定预算 · 能把关）。

## M5-S2：等价提速（**结果逐条不变**）

M4 的实现是「**每个候选各算一遍**」：逐行试下落（≈ 20 次 `_fits`）· 每个候选
**复制整块棋盘**再全扫两遍打分。**实测决策 P50 ≈ 1.55 ms**（M4-S12）→ **全开档
逻辑帧 P99 越过了 NFR-2 的 1.6 ms 预算**。

M5 把这套算法**改写成等价的剖面式实现**（spec-M5 §7.1 的五招）：

| # | 招 | 原来 | 改成 |
| --- | --- | --- | --- |
| 1 | **剖面一次算好** | 每个候选各算一遍 | 一局算一次（每行填充数 · 每列有格行 / 顶行 / 洞数） |
| 2 | **解析式下落** | 逐行试 `_fits` | 每列二分查「下面第一个有格的行」→ **O(cells)** |
| 3 | **路径前缀和** | 每候选逐列扫 | 预计算「每列放不放得下」→ 区间判断 **O(1)** |
| 4 | **增量打分** | 每个候选**复制整块棋盘**再全扫 | 只算这块方块**影响的列**与它相邻的对 |
| 5 | **按朝向预计算 + 推迟构造** | 每个候选都造结果对象 | 形状按朝向预计算；**只在赢家身上造** |

**等价的四条纪律**（**每一条都有测试守着** · `tests/test_m5_ai_equivalence.py`）：

1. **越界的格子不进网格**（形状里**有负偏移的格子** —— J / L / S / Z / T 的某些朝向）；
2. **下落只看出生行以下**（起始位置是否重叠**不检查** —— 与内核的落子口径一致）；
3. **同分先枚举到的赢**（枚举顺序不变 → 平局口径不变）；
4. **每一对相邻列只修正一次**（相邻两列都被影响时，算两遍就偏了）。

**提速不是"重写算法"**：候选集、深度、枚举顺序、权重、打分口径**一字未改**
——**同一个 AI · 同一个局面 · 同一个决策**。改算法会改掉 AI 的选择，
**那不是提速，是换了一个 AI**（录像分叉 · 期望值重抄）——M5 明确不做。
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

from ...rules.tables import KICK_GROUP_BY_PIECE, KICK_TABLE, SHAPE_TABLE

#: 棋盘（**行优先**：第 0 行是顶部，行索引自上而下递增）——与内核快照同形状。
type Board = tuple[tuple[int, ...], ...]

# ---------------------------------------------------------------------------
# 预算（**固定** · M4-D3）
# ---------------------------------------------------------------------------

#: **枚举的候选数**（**上界** · **不是"最多搜到这么多"**）。
#:
#: **口径**：`ROTATION_COUNT` 个朝向 × 每个朝向（**棋盘宽度 + 两侧各 `COLUMN_MARGIN` 列**）
#: 个起手列 → **10 宽的标准棋盘上是 4 × 16 = 64**。
#:
#: **真正被打分的候选更少**：平移路径被挡住的候选**先被剔除**（`_path_is_clear`），
#: **那取决于局面**——实测 **0–36 个**（中位 32 · 均值 27 · 300 个随机局面 × 七种方块）。
#:
#: **两个数不是一回事**（M5-S3 订正 · ADR-097）：这个常量是**枚举数**，
#: **不是"会被评估的候选数"**——以前写 40，两边都不是（**它根本没参与剪枝**）。
MAX_CANDIDATES = 64

#: **枚举的朝向数**（**固定常量** · 不许按耗时自适应）。
ROTATION_COUNT = 4

#: **枚举时每侧多带的列数**（**固定常量**）：起手列可以落在棋盘之外一圈。
COLUMN_MARGIN = 3

#: 搜索的**深度**（只看当前这一块 · **不前瞻下一块**）。
SEARCH_DEPTH = 1

#: 评估权重（**整数** —— 整数运算在任何机器上都一样，不受浮点舍入影响）。
WEIGHT_LINES = 1000
WEIGHT_HOLES = -800
WEIGHT_AGGREGATE_HEIGHT = -30
WEIGHT_BUMPINESS = -20
WEIGHT_LANDING_HEIGHT = -5

# ---------------------------------------------------------------------------
# 命令名（**字面量只此一份** · 与契约登记的名字一致）
# ---------------------------------------------------------------------------

MOVE_LEFT = "moveLeft"
MOVE_RIGHT = "moveRight"
ROTATE_CW = "rotateCW"
ROTATE_180 = "rotate180"
ROTATE_CCW = "rotateCCW"
HARD_DROP = "hardDrop"

#: 目标朝向 → 用的旋转命令（**0 = 不旋转**）。
#:
#: **为什么 180° 走 `rotate180` 而不是两次顺时针**：两次顺时针走的是
#: `0→1→2` 两条踢墙，而 `rotate180` 走的是 `0→2` 那一条——**内核按转向查表**
#: （`kernel/core.py` 的 `_cmd_rotate`），**命令与表必须一一对应**。
ROTATION_COMMAND_BY_TARGET: dict[int, str] = {
    0: "",
    1: ROTATE_CW,
    2: ROTATE_180,
    3: ROTATE_CCW,
}


@dataclass(frozen=True)
class Placement:
    """一个候选落点：**命令序列 + 评分 + 落点格子**。

    - `commands`：**按派发顺序**的命令类型（**先左右移 · 再旋转 · 最后硬降**）；
    - `score`：评估分（**越大越好**）；
    - `cells`：落点占据的**绝对坐标**（`x, y`）。
    """

    commands: tuple[str, ...]
    score: int
    cells: tuple[tuple[int, int], ...]


class _RotationPlan:
    """**按朝向预计算**的形状信息（**这块方块这个朝向的静态形状**）。

    - `columns`：这一列占**哪些行偏移**（`cx` → `cy` 列表）；
    - `row_counts`：这一行占**几格**（`cy` → 格数）——**消行数只按行算**。
    """

    __slots__ = ("cells", "columns", "row_counts")

    def __init__(self, cells: tuple[tuple[int, int], ...]) -> None:
        self.cells = cells
        by_column: dict[int, list[int]] = {}
        by_row: dict[int, int] = {}
        for cell_x, cell_y in cells:
            by_column.setdefault(cell_x, []).append(cell_y)
            by_row[cell_y] = by_row.get(cell_y, 0) + 1
        self.columns = tuple((cx, tuple(sorted(ys))) for cx, ys in sorted(by_column.items()))
        self.row_counts = tuple(sorted(by_row.items()))


class _Profile:
    """一局的**剖面**：**只算一次**（**招 1**）。

    装着三类东西：

    | 装什么 | 给谁用 |
    | --- | --- |
    | 每行填充数 · 每列有格行 / 顶行 / 洞数 · 基准分项 | 增量打分（招 4） |
    | 每列「出生行上放不放得下」的前缀和 | 路径判断（招 3） |
    | 四个朝向的形状信息 | 按朝向预计算（招 5） |
    """

    __slots__ = (
        "width",
        "height",
        "row_fill",
        "col_rows",
        "col_top",
        "col_holes",
        "col_ok_prefix",
        "base_full_rows",
        "base_heights",
        "base_aggregate",
        "base_bumpiness",
        "base_holes_total",
        "base_max_height",
        "base_argmax",
        "scratch_heights",
        "scratch_columns",
        "plans",
    )

    def __init__(
        self,
        board: Board,
        shapes: tuple[tuple[tuple[int, int], ...], ...],
        y: int,
    ) -> None:
        width = len(board[0])
        height = len(board)
        self.width = width
        self.height = height
        # 每行**已有几格**（消行数只要这一项）。
        self.row_fill = [width - row.count(0) for row in board]
        # 每列**哪些行有格**（解析式下落要二分它）。
        col_rows: list[list[int]] = []
        col_top: list[int] = []
        col_holes: list[int] = []
        for column in zip(*board, strict=False):
            filled = [row for row, value in enumerate(column) if value != 0]
            col_rows.append(filled)
            if filled:
                top = filled[0]
                holes = (height - 1 - top) - (len(filled) - 1)
            else:
                top = height
                holes = 0
            col_top.append(top)
            col_holes.append(holes)
        self.col_rows = col_rows
        self.col_top = col_top
        self.col_holes = col_holes
        # 基准分项（**增量打分从这里加减**）。
        heights = [height - top for top in col_top]
        self.base_heights = heights
        self.base_full_rows = sum(1 for count in self.row_fill if count == width)
        self.base_aggregate = sum(heights)
        self.base_bumpiness = sum(
            abs(heights[index] - heights[index + 1]) for index in range(len(heights) - 1)
        )
        self.base_holes_total = sum(col_holes)
        self.base_max_height = max(heights)
        self.base_argmax = heights.index(self.base_max_height)
        # 每列「出生行上放不放得下」→ 前缀和（**招 3**）。
        self.col_ok_prefix = _build_ok_prefix(board, shapes[0], y, width, height)
        # 复用的暂存（**每个候选用完就清**）。
        self.scratch_heights = [0] * width
        self.scratch_columns: list[int] = []
        self.plans = tuple(_RotationPlan(cells) for cells in shapes)


def plan_placement(
    board: Board,
    piece_type: str,
    x: int,
    y: int,
    rotation: int = 0,
) -> Placement | None:
    """给一块方块选一个落点（**没有合法落点就是 `None`**）。

    - `board`：当前棋盘（**只读**）；
    - `piece_type`：方块类型；
    - `x` / `y`：方块**盒子原点**的当前坐标（与内核的 `active.x` / `active.y` 同义）；
    - `rotation`：当前朝向（**只处理 0**——AI 在方块刚出生时决策，那时必是 0）。

    **枚举顺序固定**（朝向在外 · 列在内）——**同分时先枚举到的赢**，
    所以结果与机器无关。

    **M5-S2**：这里**只造赢家那一个** `Placement` 与命令序列——
    候选循环里只留分数与几个坐标（**招 5**）。
    """
    if rotation != 0:
        # AI 只在方块刚出生（朝向必为 0）时决策；别的朝向下**不决策**
        # （硬转回去会让命令序列变长，而那一块已经被人动过了）。
        return None
    shapes = _shapes_of(piece_type)
    if shapes is None:
        return None
    width = len(board[0]) if board else 0
    height = len(board)
    if width <= 0 or height <= 0:
        return None

    profile = _Profile(board, shapes, y)
    best_score: int | None = None
    best_rotation = 0
    best_pre_x = 0
    best_post_x = 0
    best_landing_y = 0
    for target_rotation in range(ROTATION_COUNT):
        plan = profile.plans[target_rotation]
        for pre_x in _candidate_columns(width):
            # ① **平移过去**：起手那一列放得下吗？**中间每一列也得放得下**
            #    （人被墙挡在半路的场景是真实存在的）。
            if not _path_is_clear(profile, x, pre_x):
                continue
            # ② **旋转**（在起手列上转）：踢墙候选**按顺序**试，第一个落得下的就用它。
            if target_rotation == 0:
                post_x, post_y = pre_x, y
            else:
                kicked = _rotate_to(board, shapes, piece_type, pre_x, y, target_rotation)
                if kicked is None:
                    continue
                post_x, post_y = kicked
            # ③ **落到地面**（硬降落点）· ④ **打分**。
            landing_y = _drop_to(profile, plan, post_x, post_y)
            score = _score(profile, plan, post_x, landing_y)
            if best_score is None or score > best_score:
                best_score = score
                best_rotation = target_rotation
                best_pre_x = pre_x
                best_post_x = post_x
                best_landing_y = landing_y
    if best_score is None:
        return None
    # ⑤ **命令序列**：先平移 → 再旋转 → 最后硬降（**只在赢家身上造**）。
    cells = tuple((best_post_x + cx, best_landing_y + cy) for cx, cy in shapes[best_rotation])
    commands = _commands_for(x, best_pre_x, best_rotation)
    return Placement(commands=commands, score=best_score, cells=cells)


def _shapes_of(piece_type: str) -> tuple[tuple[tuple[int, int], ...], ...] | None:
    """取一个方块四个朝向的占位格子（**没有就是没有**）。"""
    by_rotation = SHAPE_TABLE["down"].get(piece_type)
    if not by_rotation:
        return None
    shapes: list[tuple[tuple[int, int], ...]] = []
    for rotation in (0, 1, 2, 3):
        cells = by_rotation.get(rotation)
        if not cells:
            return None
        shapes.append(tuple(cells))
    return tuple(shapes)


def _candidate_columns(width: int) -> range:
    """**起手列**的枚举范围（**盒子原点**的横坐标）。

    **比棋盘宽一圈**：盒子的占位格子从原点往右铺，原点可以在棋盘外
    （那样占位格子仍然全在棋盘里）——**内核的碰撞判定就是这么算的**
    （`kernel/core.py` 的 `_collides`：逐格判，不按盒子判）。
    """
    return range(-COLUMN_MARGIN, width + COLUMN_MARGIN)


def _build_ok_prefix(
    board: Board,
    shape: tuple[tuple[int, int], ...],
    y: int,
    width: int,
    height: int,
) -> list[int]:
    """每列「出生行上放不放得下」→ **前缀和**（**招 3**）。

    **为什么能这么判**：平移路径上的每一列都必须是**同一个形状**在**同一行**
    上的落点判定——**与列无关的部分（形状本身、行）完全一样**，
    所以**一次算好每列的可行性**，路径就变成**区间和为 0** 的问题。
    """
    prefix = [0] * (width + 1)
    for column in range(width):
        ok = True
        for cell_x, cell_y in shape:
            absolute_x = column + cell_x
            absolute_y = y + cell_y
            if absolute_x < 0 or absolute_x >= width or absolute_y >= height:
                ok = False
                break
            if absolute_y >= 0 and board[absolute_y][absolute_x] != 0:
                ok = False
                break
        prefix[column + 1] = prefix[column] + (0 if ok else 1)
    return prefix


def _path_is_clear(profile: _Profile, x: int, pre_x: int) -> bool:
    """从 `x` 平移到 `pre_x` 的**每一格都得放得下**（**前缀和 · O(1)**）。

    **为什么要逐格查**：只查终点的话，"挪到一半被墙挡住"会被漏掉——
    那条命令序列会让内核只挪一半，**落点与算出来的不一样**。

    **越界就是放不下**：路径的端点若在棋盘之外，这一列必定放不下
    （口径与 `_fits` 的左右越界判定一致）。
    """
    low = x if x < pre_x else pre_x
    high = x if x > pre_x else pre_x
    if low < 0 or high >= profile.width:
        return False
    prefix = profile.col_ok_prefix
    return prefix[high + 1] == prefix[low]


def _rotate_to(
    board: Board,
    shapes: tuple[tuple[tuple[int, int], ...], ...],
    piece_type: str,
    x: int,
    y: int,
    target_rotation: int,
) -> tuple[int, int] | None:
    """在 `(x, y)` 处转到 `target_rotation`（**转不动就是 `None`**）。

    **踢墙候选按顺序试**（与内核同一条纪律：第一个落得下的就用它）。
    """
    group = KICK_GROUP_BY_PIECE.get(piece_type, "JLSTZ")
    candidates = KICK_TABLE["down"].get((0, target_rotation), {}).get(group, ())
    for offset_x, offset_y in candidates:
        target_x = x + offset_x
        target_y = y + offset_y
        if _fits(board, shapes[target_rotation], target_x, target_y):
            return target_x, target_y
    return None


def _drop_to(profile: _Profile, plan: _RotationPlan, x: int, y: int) -> int:
    """从 `(x, y)` 往下落到**落不下去为止**（返回落点的纵坐标）。

    **解析式（招 2）**：不再逐行试 `_fits`，而是**每列二分**找
    「下面第一个有格的行」，落点 = 各列允许的最高落点的**最小值**。

    **为什么等价**：一块方块能往下走多远，只受**它自己占的那几列**限制；
    每一列的阻塞点是**那一列第一个有格的行**——**逐列取最紧的那个**，
    就是逐行试出来的同一个落点。
    """
    height = profile.height
    width = profile.width
    limit = height
    for cell_x, cell_y in plan.cells:
        column = x + cell_x
        if column < 0 or column >= width:
            # 这一列在棋盘外 → **放不下**（口径与 `_fits` 的左右越界一致）：
            # 逐行试的话，第一步就放不下，落点停在原地。
            return y
        filled = profile.col_rows[column]
        index = bisect_left(filled, y + 1 + cell_y)
        blocked = filled[index] if index < len(filled) else height
        candidate_limit = blocked - 1 - cell_y
        if candidate_limit < limit:
            limit = candidate_limit
    if limit > height - 1:
        limit = height - 1
    return limit if limit > y else y


def _fits(
    board: Board,
    shape: tuple[tuple[int, int], ...],
    x: int,
    y: int,
) -> bool:
    """这个位置**放得下吗**（口径与内核的 `_collides` 一致）。

    - 左右越界 → 放不下；
    - 下方越界 → 放不下；
    - **棋盘之上（y < 0）不算冲突**（那是出生区）；
    - 压到已有方块 → 放不下。
    """
    height = len(board)
    width = len(board[0]) if board else 0
    for cell_x, cell_y in shape:
        absolute_x = x + cell_x
        absolute_y = y + cell_y
        if absolute_x < 0 or absolute_x >= width:
            return False
        if absolute_y >= height:
            return False
        if absolute_y >= 0 and board[absolute_y][absolute_x] != 0:
            return False
    return True


def _commands_for(x: int, pre_x: int, target_rotation: int) -> tuple[str, ...]:
    """平移 + 旋转 → 命令序列（**最后一条永远是硬降**）。"""
    commands: list[str] = []
    if pre_x < x:
        commands.extend([MOVE_LEFT] * (x - pre_x))
    elif pre_x > x:
        commands.extend([MOVE_RIGHT] * (pre_x - x))
    rotation_command = ROTATION_COMMAND_BY_TARGET.get(target_rotation, "")
    if rotation_command:
        commands.append(rotation_command)
    commands.append(HARD_DROP)
    return tuple(commands)


def _score(profile: _Profile, plan: _RotationPlan, post_x: int, landing_y: int) -> int:
    """给一个落点打分（**越大越好**）——**增量算**（**招 4**）。

    **五项**（权重都是整数）：

    | 项 | 口径 | 为什么 |
    | --- | --- | --- |
    | 消行数 | 落上去之后**满行**的条数 | 消行是分数与空间的唯一来源 |
    | 空洞数 | 每列**被压住**的空格数 | 空洞最致命（填不回来） |
    | 高度和 | 各列高度之和 | 越低越好（留出空间） |
    | 起伏度 | 相邻两列高度差之和 | 平的盘面比参差的盘面好堆 |
    | 落点高度 | 落上去之后**最高**的列高 | 别把自己架在半空 |

    **为什么不复制棋盘**：一块方块只影响**它占的那几列**——
    其余列的洞数、高度、起伏**原封不动**。所以**从基准分加减**即可。
    """
    width = profile.width
    height = profile.height
    row_fill = profile.row_fill

    # ① **消行数**：只看这块方块占到的行（每行占几格**按朝向预计算**）。
    lines = profile.base_full_rows
    for cell_y, count in plan.row_counts:
        row = landing_y + cell_y
        if 0 <= row < height and row_fill[row] + count == width:
            lines += 1

    # ② **受影响的列**：洞数与高度逐列修正。
    base_heights = profile.base_heights
    updated = profile.scratch_heights
    touched = profile.scratch_columns
    del touched[:]
    holes_total = profile.base_holes_total
    aggregate = profile.base_aggregate
    for cell_x, ys in plan.columns:
        column = post_x + cell_x
        if column < 0 or column >= width:
            # **越界的格子不进网格**（纪律 ①）。
            continue
        top = profile.col_top[column]
        old_holes = profile.col_holes[column]
        lowest = height
        below_count = 0
        for cell_y in ys:
            row = landing_y + cell_y
            if row < 0 or row >= height:
                # **棋盘之上的格子不进网格**（纪律 ①）。
                continue
            if row < lowest:
                lowest = row
            if row > top:
                below_count += 1
        if lowest == height:
            # 这一列**一个格子都没进网格** → 原样不动。
            continue
        if lowest < top:
            new_top = lowest
            middle_count = 0
            for cell_y in ys:
                row = landing_y + cell_y
                if new_top < row < top:
                    middle_count += 1
            new_holes = (top - new_top - 1) - middle_count + old_holes - below_count
        else:
            new_top = top
            new_holes = old_holes - below_count
        holes_total += new_holes - old_holes
        updated[column] = height - new_top
        aggregate += updated[column] - base_heights[column]
        touched.append(column)

    # ③ **起伏度**：**每一对相邻列只算一次**（纪律 ④）——**归它的右列**。
    bumpiness = profile.base_bumpiness
    for column in touched:
        # 这一对归 `column`（它是右列）。
        if column > 0:
            left_height = updated[column - 1] or base_heights[column - 1]
            right_height = updated[column] or base_heights[column]
            bumpiness += abs(left_height - right_height) - abs(
                base_heights[column - 1] - base_heights[column]
            )
        # 右边那一对：**只有右邻没被影响时才归我**（否则归右邻）。
        right = column + 1
        if right < width and updated[right] == 0:
            left_height = updated[column] or base_heights[column]
            bumpiness += abs(left_height - base_heights[right]) - abs(
                base_heights[column] - base_heights[right]
            )

    # ④ **落点高度**（最高的一列）。
    if not touched:
        landing_height = profile.base_max_height
    elif profile.base_argmax in touched:
        landing_height = max(updated[column] or base_heights[column] for column in range(width))
    else:
        landing_height = profile.base_max_height
        for column in touched:
            if updated[column] > landing_height:
                landing_height = updated[column]

    # 暂存清零（**下一个候选接着用**）。
    for column in touched:
        updated[column] = 0

    return (
        WEIGHT_LINES * lines
        + WEIGHT_HOLES * holes_total
        + WEIGHT_AGGREGATE_HEIGHT * aggregate
        + WEIGHT_BUMPINESS * bumpiness
        + WEIGHT_LANDING_HEIGHT * landing_height
    )


__all__ = [
    "HARD_DROP",
    "MAX_CANDIDATES",
    "MOVE_LEFT",
    "MOVE_RIGHT",
    "ROTATE_180",
    "ROTATE_CCW",
    "ROTATE_CW",
    "SEARCH_DEPTH",
    "Board",
    "Placement",
    "plan_placement",
]
