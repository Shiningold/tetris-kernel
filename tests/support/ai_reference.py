"""AI 落点搜索的**冻结参照实现**（M5-S1 · spec-M5 §7.2 · plan-M5 S1）。

## 它是什么

**M4 那版搜索实现的逐字副本**（`src/tetris_kernel/systems/ai/search.py` ·
`cfec469` 时的样子）——**只用于差分测试**，**不进 `src/`**。

**为什么要有它**：M5 要把搜索改快（spec §7.1 的五招）。改快本身没有错，
**错的是"改快了但决策变了"**——那就不是提速，是换了一个 AI（录像分叉 ·
期望值重抄）。所以提速之前先把 M4 的行为**冻在这里**，让差分测试逐条比对。

## 它的纪律

1. **不许漂**：这份副本**只允许在 M5 里被"证明它错了"时**改，且改动必须
   单独成笔并说明理由（spec §7.2）；
2. **不许 import 产品实现**：它只从 `rules.tables` 取几何常量（**冻结区**），
   **不碰 `systems.ai.search`**——否则差分就是自己比自己（测试里有元守卫钉住）；
3. **不许在运行时重算锚点**：锚点表在 `tests/test_m5_ai_equivalence.py` 里是
   **源码字面量**（元守卫钉住），本模块只负责提供"另一种算法"的对照。

## 与产品实现的差异

**只有 import 路径**（`...rules.tables` → `tetris_kernel.rules.tables`）与
**模块名**。算法、枚举顺序、权重、打分口径、命令序列**一字未改**——
它就是 M4 的行为。
"""

from __future__ import annotations

from dataclasses import dataclass

from tetris_kernel.rules.tables import KICK_GROUP_BY_PIECE, KICK_TABLE, SHAPE_TABLE

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
    """**起手列**的枚举范围（**盒子原点**的横坐标）。"""
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
    # ① **平移过去**：起手那一列放得下吗？**中间每一列也得放得下**。
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
    """从 `x` 平移到 `pre_x` 的**每一格都得放得下**。"""
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
    """在 `(x, y)` 处转到 `target_rotation`（**转不动就是 `None`**）。"""
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
    """给一个落点打分（**越大越好**）。"""
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
