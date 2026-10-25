"""确定性参考策略（spec §7.8 · plan-M1.md S13 段）。

## 它是什么

M1 的验收载体要「**真的玩一局**」，但 M1 不做渲染器、不做输入适配器、不做 AI。
所以这里放一个**测试内的确定性参考策略**：它**只看快照、只派发命令**，
**不许直接改棋盘**（N1：唯一的状态所有者是内核）。

## 三条纪律

1. **只看快照**：策略读到的全部状态都来自 `Snapshot`——**不碰内核**。
2. **只派发无参命令**：返回**命令类型名**的元组（`moveLeft` / `moveRight` /
   `rotateCW` / `rotateCCW` / `hardDrop`），**不带参数**。
3. **必须确定性**：**不许用随机数、不许用时间**。所以 `decide` 是**纯函数**
   ——同一份快照 → 同一个答案（`tests/test_m1_gates.py` 里有这条守卫）。

## 它不聪明（也不需要聪明）

目标只是「**把方块放到能消行的位置，消不掉就硬降**」。**最终必然顶出**——
顶出不依赖策略聪明，**依赖策略不作弊**（不许直接改棋盘）。

## 三条判定规则（每条都对应一个踩过的坑）

### 一、转向走**最短路**，不许来回振荡

旋转方向按**步数差**算，不按「下一步会不会正好落到目标」算：

- 顺时针步数 = `(目标朝向 - 当前朝向) % 4`；
- 逆时针步数 = `(当前朝向 - 目标朝向) % 4`；
- 顺时针不多于逆时针就顺时针，否则逆时针。

**为什么**：早先的写法是「下一步正好落到目标就转逆时针，否则顺时针」——
目标在当前朝向**对面**时它会**转过去又转回来**，方块在原地永远转不停：
锁定延迟被反复重置，一块 I 悬空转了 1111 帧才落盘，整局 5920 帧只落下
15 块。按步数差走，每一步都**离目标更近**，不会回头。

### 二、贴地就**别再派命令**

方块**已经贴在地板上**（它自己落点的高度 == 它的当前高度）→ **一条命令都不派**，
等内核把锁定延迟走完。

**为什么**：策略每帧都重新算落点，而**棋盘在方块锁定前不变**——算出来的目标
一直一样，于是策略会对着一个**已经就位**的方块反复派 `hardDrop` / 转向。
那些转向**每次成功都重置锁定延迟**（ADR 口径），方块就永远锁不下去。
贴地就停手，是唯一能同时满足「目标不变」与「锁定延迟不被重置」的写法。

### 三、横向可达性按**目标朝向**判断

旋转发生在横向移动**之前**，所以移动时方块用的是**目标朝向**的形状——
可达性必须按**目标朝向**算，不能按当前朝向算。

## 落点怎么算

枚举 **4 个朝向 × 每一列**，按「消行数 → 空洞数 → 总高度 → 最高列 → 起伏 → 列 →
朝向」排序，取最小的一手；**够不着的落点跳过**，取下一个够得着的。

**为什么必须能旋转**：S / Z / T / J / L 的某些朝向**自带悬空**——落到底上
会在**最底行**留一个永久填不上的洞，底行从此消不掉，**一行都消不了**。
能旋转就能把悬空挪到侧面或高处。**这是实测出来的**（只转 0 时整局 0 行）。

**旋转怎么落地**：先在**出生高度**转到位，再横向挪到目标列，最后硬降。
**踢墙表不重写**：策略**不判断"这个旋转能不能转成"**——它只管说"转"，
**转不成是内核的事**（内核会接受命令但不动方块）。所以策略里**没有第二份
踢墙规则**（那是最容易分叉的地方）。

**落点从棋盘顶算**（不是从方块当前的位置算）：这样**目标与方块当前的高度
无关**——方块被硬降之后，目标不变，策略不会"降完又挪走"。
"""

from __future__ import annotations

from tetris_kernel.kernel.core import SPAWN_Y
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.rules.tables import SHAPE_TABLE

#: 策略派发的命令的来源标记（只随事件透传，内核不因它分支）。
POLICY_SOURCE = "policy"

#: 形状表取哪一轴（标准玩法：重力向下）。
_AXIS = "down"

#: 朝向数（4）。
_ROTATION_COUNT = 4


def _shape_cells(piece_type: str, rotation: int) -> tuple[tuple[int, int], ...]:
    """某个朝向的占位格子（**方块自身坐标**，不是棋盘绝对坐标）。"""
    return tuple(SHAPE_TABLE[_AXIS][piece_type][rotation % _ROTATION_COUNT])


def _cells_at(piece_type: str, rotation: int, x: int, y: int) -> tuple[tuple[int, int], ...]:
    """把占位格子平移到棋盘坐标 `(x, y)`。"""
    return tuple((cx + x, cy + y) for cx, cy in _shape_cells(piece_type, rotation))


def _fits(
    board: tuple[tuple[int, ...], ...], piece_type: str, rotation: int, x: int, y: int
) -> bool:
    """`(x, y)` 处放得下这块方块吗。

    **标准规则集的碰撞**：越界或撞到已落盘的格子都算放不下。

    **这是一份"重写"而不是"调用内核"**：内核的碰撞逻辑在 `kernel/core.py`
    里（私有方法），**夹具不能去调内核的私有实现**——那是把内核的内部结构
    变成夹具的依赖。规则本身很简单（越界 / 撞格），重写一遍不会分叉。
    """
    height = len(board)
    width = len(board[0]) if height else 0
    for cell_x, cell_y in _cells_at(piece_type, rotation, x, y):
        if not (0 <= cell_x < width and 0 <= cell_y < height):
            return False
        if board[cell_y][cell_x] != 0:
            return False
    return True


def _landing_row(
    board: tuple[tuple[int, ...], ...], piece_type: str, rotation: int, x: int
) -> int | None:
    """从**棋盘顶**落到底的落点行；出生就放不下则返回 `None`。

    **从棋盘顶算**（不是从方块当前位置算）：目标要与方块当前的高度无关
    （见模块 docstring）。
    """
    if not _fits(board, piece_type, rotation, x, SPAWN_Y):
        return None
    row = SPAWN_Y
    while _fits(board, piece_type, rotation, x, row + 1):
        row += 1
    return row


def _heights_and_holes(board: tuple[tuple[int, ...], ...]) -> tuple[tuple[int, ...], int]:
    """每列的高度与**空洞**数（被盖住的空格）。"""
    height = len(board)
    width = len(board[0]) if height else 0
    heights: list[int] = []
    holes = 0
    for x in range(width):
        top = height
        for y in range(height):
            if board[y][x] != 0:
                top = y
                break
        heights.append(height - top)
        for y in range(top + 1, height):
            if board[y][x] == 0:
                holes += 1
    return tuple(heights), holes


def _placement_score(
    board: tuple[tuple[int, ...], ...], piece_type: str, rotation: int, x: int
) -> tuple[int, ...] | None:
    """这一手落子的评分（**越小越好**）；出生就放不下则 `None`。

    排序键（**逐条独立**）：

    1. `-消行数`——能消行优先（目标就是"把方块放到能消行的位置"）；
    2. `空洞数`——空洞是"填不满的死角"，越少越好；
    3. `总高度`（各列高度之和）——**堆得越矮越平越好**；它比「最高列」更能
       把方块摊开（贴着墙堆的方块总高度一样，但那一列会越堆越高）；
    4. `最高列高度`——最高的单列也别太高；
    5. `起伏`（相邻列高度差之和）——堆得越平越好；
    6. `x`——**并列时取最左**（保证确定性）；
    7. `rotation`——再并列时取朝向 0（同样是为了确定性）。
    """
    landing = _landing_row(board, piece_type, rotation, x)
    if landing is None:
        return None
    filled = [list(row) for row in board]
    for cell_x, cell_y in _cells_at(piece_type, rotation, x, landing):
        filled[cell_y][cell_x] = 1
    lines = sum(1 for row in filled if all(cell != 0 for cell in row))
    heights, holes = _heights_and_holes(tuple(tuple(row) for row in filled))
    total_height = sum(heights)
    bumpiness = sum(abs(heights[i] - heights[i + 1]) for i in range(len(heights) - 1))
    return (-lines, holes, total_height, max(heights), bumpiness, x, rotation)


def _reachable(
    board: tuple[tuple[int, ...], ...],
    piece_type: str,
    rotation: int,
    from_x: int,
    to_x: int,
    y: int,
) -> bool:
    """横向从 `from_x` 走到 `to_x`（同一高度 `y`）**中间不撞**吗。

    **扫过的区域 = 途经的每一格**（横向移动是逐格走的），所以沿途每一格都
    要放得下。

    **`rotation` 传的是目标朝向**：旋转发生在横向移动之前，移动时方块已经
    换了形状（见模块 docstring 第三条）。
    """
    step = 1 if to_x > from_x else -1
    x = from_x
    while x != to_x:
        x += step
        if not _fits(board, piece_type, rotation, x, y):
            return False
    return True


class ReferencePolicy:
    """确定性参考策略（**纯函数式**：同一份快照 → 同一个答案）。

    **无状态**：不记"上一步做了什么"——所有的判断都从当前快照算出来。所以
    同一个棋盘**必然**给出同一串命令（FR-1.4 的确定性子集）。
    """

    def decide(self, snapshot: Snapshot) -> tuple[str, ...]:
        """按当前快照给出**要派发的命令类型名**（**不带参数**）。

        - 无活动方块 → **什么都不派**（等下一块出生）；
        - **已经贴地** → **什么都不派**（等内核走完锁定延迟，见模块 docstring）；
        - 朝向不对 → 转向（**按步数差走最短路**）；
        - 列不对 → `moveLeft` / `moveRight`；
        - 都对 → **硬降**（"消不掉就硬降"）。
        """
        active = snapshot.active
        if active is None:
            return ()
        board = snapshot.board.cells
        if _resting(board, active.pieceType, active.rotation, active.x, active.y):
            return ()
        target = self._target(board, active.pieceType, active.x, active.y, active.rotation)
        if target is None:
            return ("hardDrop",)
        target_rotation, target_x = target
        if target_rotation != active.rotation:
            clockwise = (target_rotation - active.rotation) % _ROTATION_COUNT
            counter_clockwise = (active.rotation - target_rotation) % _ROTATION_COUNT
            return ("rotateCW",) if clockwise <= counter_clockwise else ("rotateCCW",)
        if target_x != active.x:
            return ("moveLeft",) if target_x < active.x else ("moveRight",)
        return ("hardDrop",)

    def _target(
        self,
        board: tuple[tuple[int, ...], ...],
        piece_type: str,
        current_x: int,
        current_y: int,
        current_rotation: int,
    ) -> tuple[int, int] | None:
        """最好的落点（**朝向, 列**）；一个都放不下则 `None`。

        **落点只看棋盘**（`current_x` / `current_y` 只用于"够不够得着"的
        判断）：目标要与方块当前的位置无关（见模块 docstring）。

        **够不着的落点跳过**，取下一个够得着的——宁可落次优的位置，也不
        落在一个走不到的位置上。
        """
        width = len(board[0]) if board else 0
        candidates: list[tuple[tuple[int, ...], int, int]] = []
        for rotation in range(_ROTATION_COUNT):
            for x in range(width):
                score = _placement_score(board, piece_type, rotation, x)
                if score is not None:
                    candidates.append((score, rotation, x))
        if not candidates:
            return None
        candidates.sort()
        for _score, rotation, x in candidates:
            if _reachable(board, piece_type, rotation, current_x, x, current_y):
                return (rotation, x)
        # **一个都够不着** → 退回当前位置（→ 硬降）。
        return (current_rotation, current_x)


def _resting(
    board: tuple[tuple[int, ...], ...],
    piece_type: str,
    rotation: int,
    x: int,
    y: int,
) -> bool:
    """方块**已经贴在地板上**吗（它自己落点的高度 == 它的当前高度）。

    **贴地就该停手**：策略每帧重算落点，而棋盘在锁定前不变——对着一个已经
    就位的方块继续派转向命令会**反复重置锁定延迟**，方块就永远锁不下去。
    """
    landing = _landing_row(board, piece_type, rotation, x)
    return landing is not None and landing == y


__all__ = [
    "POLICY_SOURCE",
    "ReferencePolicy",
]
