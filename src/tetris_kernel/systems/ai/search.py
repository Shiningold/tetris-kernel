"""AI 的落点搜索（系统层 · M4-S9 · spec-M4 §7.8 · 架构 §7.5）。

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
| **候选数** | `MAX_CANDIDATES`（4 朝向 × 棋盘宽度） | 按耗时自适应 → 同一录像在不同机器上重放不同 |
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
"""

from __future__ import annotations

from dataclasses import dataclass

from ...rules.tables import KICK_GROUP_BY_PIECE, KICK_TABLE, SHAPE_TABLE

#: 棋盘（**行优先**：第 0 行是顶部，行索引自上而下递增）——与内核快照同形状。
type Board = tuple[tuple[int, ...], ...]

# ---------------------------------------------------------------------------
# 预算（**固定** · M4-D3）
# ---------------------------------------------------------------------------

#: 搜索的**候选数上限**（4 个朝向 × 棋盘宽度）。
#:
#: **固定值，不是"最多搜到这么多"**——枚举本身就是这个规模（见 `plan_placement`），
#: 所以这个常量是**预算的声明**，不是截断器。
MAX_CANDIDATES = 40

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

    best: Placement | None = None
    for target_rotation in (0, 1, 2, 3):
        for pre_x in _candidate_columns(width):
            candidate = _candidate(
                board,
                shapes,
                piece_type,
                x,
                y,
                pre_x,
                target_rotation,
            )
            if candidate is None:
                continue
            if best is None or candidate.score > best.score:
                best = candidate
    return best


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
    return range(-3, width + 3)


def _candidate(
    board: Board,
    shapes: tuple[tuple[tuple[int, int], ...], ...],
    piece_type: str,
    x: int,
    y: int,
    pre_x: int,
    target_rotation: int,
) -> Placement | None:
    """算一个候选（**落不下就是 `None`**）。"""
    width = len(board[0])
    height = len(board)
    # ① **平移过去**：起手那一列放得下吗？**中间每一列也得放得下**
    #    （人被墙挡在半路的场景是真实存在的）。
    if not _path_is_clear(board, shapes[0], x, y, pre_x):
        return None
    # ② **旋转**（在起手列上转）：踢墙候选**按顺序**试，第一个落得下的就用它。
    if target_rotation == 0:
        post_x, post_y = pre_x, y
    else:
        kicked = _rotate_to(board, shapes, piece_type, pre_x, y, target_rotation)
        if kicked is None:
            return None
        post_x, post_y = kicked
    # ③ **落到地面**（硬降落点）。
    landing_y = _drop_to(board, shapes[target_rotation], post_x, post_y)
    cells = tuple((post_x + cx, landing_y + cy) for cx, cy in shapes[target_rotation])
    # ④ **打分**。
    score = _evaluate(board, cells, width, height)
    # ⑤ **命令序列**：先平移 → 再旋转 → 最后硬降。
    commands = _commands_for(x, pre_x, target_rotation)
    return Placement(commands=commands, score=score, cells=cells)


def _path_is_clear(
    board: Board,
    shape: tuple[tuple[int, int], ...],
    x: int,
    y: int,
    pre_x: int,
) -> bool:
    """从 `x` 平移到 `pre_x` 的**每一格都得放得下**。

    **为什么要逐格查**：只查终点的话，"挪到一半被墙挡住"会被漏掉——
    那条命令序列会让内核只挪一半，**落点与算出来的不一样**。
    """
    step = 1 if pre_x >= x else -1
    for current in range(x, pre_x + step, step):
        if not _fits(board, shape, current, y):
            return False
    return True


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


def _drop_to(
    board: Board,
    shape: tuple[tuple[int, int], ...],
    x: int,
    y: int,
) -> int:
    """从 `(x, y)` 往下落到**落不下去为止**（返回落点的纵坐标）。"""
    landing = y
    while _fits(board, shape, x, landing + 1):
        landing += 1
    return landing


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


def _evaluate(
    board: Board,
    cells: tuple[tuple[int, int], ...],
    width: int,
    height: int,
) -> int:
    """给一个落点打分（**越大越好**）。

    **五项**（权重都是整数）：

    | 项 | 口径 | 为什么 |
    | --- | --- | --- |
    | 消行数 | 落上去之后**满行**的条数 | 消行是分数与空间的唯一来源 |
    | 空洞数 | 每列**被压住**的空格数 | 空洞最致命（填不回来） |
    | 高度和 | 各列高度之和 | 越低越好（留出空间） |
    | 起伏度 | 相邻两列高度差之和 | 平的盘面比参差的盘面好堆 |
    | 落点高度 | 落上去之后**最高**的列高 | 别把自己架在半空 |
    """
    grid = [list(row) for row in board]
    for cell_x, cell_y in cells:
        if 0 <= cell_y < height and 0 <= cell_x < width:
            grid[cell_y][cell_x] = 1
    lines = sum(1 for row in grid if all(value != 0 for value in row))
    heights: list[int] = []
    holes = 0
    for column in range(width):
        top = height
        for row in range(height):
            if grid[row][column] != 0:
                top = row
                break
        heights.append(height - top)
        if top < height:
            holes += sum(1 for row in range(top + 1, height) if grid[row][column] == 0)
    aggregate_height = sum(heights)
    bumpiness = sum(abs(heights[index] - heights[index + 1]) for index in range(len(heights) - 1))
    landing_height = max(heights) if heights else 0
    return (
        WEIGHT_LINES * lines
        + WEIGHT_HOLES * holes
        + WEIGHT_AGGREGATE_HEIGHT * aggregate_height
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
