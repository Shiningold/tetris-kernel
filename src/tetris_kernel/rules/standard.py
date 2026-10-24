"""标准规则集的最小实现（M0 · plan-M0.md S13 · 架构 §7.3）。

## 最小到哪

M0 只保证**接口定稿 + 最小实现**（架构 §7.3 · spec-M0.md §7.5）：

- `wrap_cell` 恒等；`gravity_vector` 按轴给向量；`gravity_interval` 查最小表；
- `scan_clears` 整行 / 整列满即消，产出带**轴 + 索引 + 格子清单**的组；
- `classify_placement` 给 T-Spin 标记 + 计分类别键（**一档 T-Spin**）；
- `score_clear` 基础分值 + 连击奖励；
- `shape_cells` 标准 7 种方块 × 4 朝向；`kick_offsets` **恒等偏移**。

**标准踢墙表逐条验证、T-Spin 三类全对、完整计分表是 M1**（spec-M0.md §7.5
与 M1 开门判据）——这里一次做完，M1 的判据就失去意义了。

## 规则集不可变、无状态

八个函数全是**纯函数**：随时间变化的量（帧号、等级、已放置方块数、重力轴、
步进时长）全部从 `RuleContext` 进来；规则集自己**不持有任何可变状态**
（架构 §7.3.1 纪律 1）——否则 FR-10.2 的存档恢复要多序列化一份规则集状态，
纯函数则天然可恢复。

## 棋盘语义（**给内核的约定**）

`board` 是**行优先**的只读视图：第 0 行在最上面，格子值 `0` 表示空。
`scan_clears` 与 `classify_placement` 拿到的都是**已放置、待消行**的同一视图
——`classify_placement` 要靠它数出"这次消几行"（T-Spin 档位与计分类别键都
依赖这个数）。

## 计分口径（连击 / 背面连击 / 消行数）

- **连击**（`combo`）：连续消行的次数；**落空清零**。落空由**内核每次锁定后
  都调用 `score_clear`（含空组）**表达——空组即落空，**内核不自己改 `combo`**。
- **背面连击**（`b2b`）：这一位表示「当前处于背面连击链中」——本次消行是难消
  （四消 / T-Spin）就入链，普通消行或落空断链。**B2B 倍率**（M1）的条件是
  「本次难消 ∧ 上一手也难消」，`prev.b2b` 正是"上一手在链中"的答案。
- **消行数**（`linesDelta`）：**组数就是消掉的线数**——重力向下时线是行，横向
  重力时线是列；`lines` 记的始终是"消掉了几条线"，与重力轴无关。

## T-Spin 分类怎么到达计分

`classify_placement` 的结果挂在 `ClearGroup.classification` 上，
`score_clear` 从组里读它（**内核只搬运、不重新判定**）。**不给签名加参数**
——签名是冻结的（ADR-061）。`scan_clears` 单独调用时不知道放置上下文，
所以它产出的组 `classification` 是 `None`。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..contracts.placement import PlacementInfo
from ..contracts.rules import (
    ClearGroup,
    PlacementClass,
    RuleContext,
    ScoreDelta,
    ScoreState,
)
from .interface import (
    ClassifyPlacement,
    GravityInterval,
    GravityVector,
    KickOffsets,
    RuleSet,
    ScanClears,
    ScoreClear,
    ShapeCells,
    WrapCell,
)
from .tables import (
    CLEAR_AXIS_BY_GRAVITY,
    CLEAR_NAMES,
    COMBO_BONUS,
    DIFFICULT_CLASS_KEYS,
    GRAVITY_INTERVAL_FRAMES,
    GRAVITY_VECTORS,
    KICK_TABLE,
    ROTATION_COUNT,
    SCORE_TABLE,
    SHAPE_TABLE,
)

# ---------------------------------------------------------------------------
# 内部小工具（棋盘只读访问）
# ---------------------------------------------------------------------------


def _cell_at(board: Sequence[Sequence[int]], x: int, y: int) -> int:
    """读一个格子；越界（含负坐标）视为空。"""
    if y < 0 or y >= len(board):
        return 0
    row = board[y]
    if x < 0 or x >= len(row):
        return 0
    return row[x]


def _board_width(board: Sequence[Sequence[int]]) -> int:
    """棋盘宽度：取各行长度的最小值（对不齐的棋盘按最窄的一行算）。

    **行路径与列路径用同一个宽度**——同一份"不整齐"的棋盘，按行判和按列判
    必须得出同一个答案（两条路径口径不一致本身就是隐患）。
    """
    if not board:
        return 0
    return min(len(row) for row in board)


def _full_indices(board: Sequence[Sequence[int]], clear_axis: str) -> tuple[int, ...]:
    """已满的行 / 列的索引（升序）。

    **判满用同一个宽度口径**（`_board_width`）：行按「本行前 `width` 格全非空」
    判满，列按「各行该列全非空」判满。对不齐的棋盘按最窄的一行算，两条路径
    得出同一个答案。

    **零宽棋盘没有满线**：宽度为 0 时一行都判不满——`all()` 对空切片恒真，
    不挡的话「零列的棋盘」会凭空冒出 N 条满行（每条的格子清单还是空的）。
    """
    width = _board_width(board)
    if width == 0:
        return ()
    if clear_axis == "row":
        return tuple(
            index for index, row in enumerate(board) if all(cell != 0 for cell in row[:width])
        )
    height = len(board)
    return tuple(
        index
        for index in range(width)
        if all(_cell_at(board, index, y) != 0 for y in range(height))
    )


def _junction_cell(cells: tuple[tuple[int, int], ...]) -> tuple[int, int] | None:
    """T 形的"枢纽格"：与另外三格都正交相邻的那一格。

    找不到就是形状不对（T 形枢纽唯一，找到即说明是 T）。
    """
    occupied = set(cells)
    for x, y in cells:
        neighbors = sum(
            1 for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)) if (nx, ny) in occupied
        )
        if neighbors == 3:
            return (x, y)
    return None


def _class_key(spin: bool, cleared: int) -> str:
    """计分类别键：按"是否 T-Spin + 消了几行"推出 `classKey`。

    消 0 行 → `none`；1/2/3/4 行 → `single` / `double` / `triple` / `tetris`；
    5 行以上按 `tetris` 算（M0 不另立类别）。
    """
    if cleared <= 0:
        return "none"
    name = CLEAR_NAMES.get(cleared, "tetris")
    return f"tspin-{name}" if spin else name


def _first_classification(groups: Sequence[ClearGroup]) -> PlacementClass | None:
    """取本次消行的放置分类（内核把同一个分类挂到每个组上）。"""
    for group in groups:
        if group.classification is not None:
            return group.classification
    return None


# ---------------------------------------------------------------------------
# 八条接口
# ---------------------------------------------------------------------------


def wrap_cell(x: int, y: int, w: int, h: int) -> tuple[int, int]:
    """边界拓扑：恒等映射（标准玩法是硬边界，不环绕、不翻转）。

    `w` / `h` 用不上——恒等映射不依赖棋盘尺寸。**参数不能删**：签名是冻结的
    （ADR-061），拓扑是一个函数而不是参数（ADR-037）。圆柱 / 莫比乌斯变体
    各出自己的实现。
    """
    return (x, y)


def gravity_vector(ctx: RuleContext) -> tuple[int, int]:
    """重力方向向量 `(x, y)`——方向必须是向量，不能只有 `dy`（架构 §7.3.1）。"""
    return GRAVITY_VECTORS[ctx.axis]


def gravity_interval(ctx: RuleContext) -> int:
    """重力间隔：多少逻辑帧落一格。

    查等级表：1 级用表头，表尾之后的等级用表尾值。**下限 1 帧**——
    间隔为 0 会让方块一帧掉到底，那不是重力。
    """
    if ctx.level < 1:
        return GRAVITY_INTERVAL_FRAMES[0]
    index = min(ctx.level, len(GRAVITY_INTERVAL_FRAMES)) - 1
    return max(1, GRAVITY_INTERVAL_FRAMES[index])


def scan_clears(board: Sequence[Sequence[int]], ctx: RuleContext) -> tuple[ClearGroup, ...]:
    """扫描可消的行 / 列：整行（整列）满即消。

    返回的每个组带**轴 + 索引 + 格子清单**——不能只返回数量（架构 §7.3.1）：
    格子清单是整条线的几何（这条线上的所有格子，不只是有方块的那些），
    塌落动画与事件负载都要它。

    **分类是 `None`**：本函数不知道放置上下文（签名里没有 `PlacementInfo`），
    分类由内核拿到 `classify_placement` 的结果后挂上来（内核只搬运）。

    **一条线多长**：行路径的组是「整行」——格子清单 = 棋盘**宽度**那么多个
    （每行取前 `width` 格）；列路径的组是「整列」——格子清单 = 棋盘**高度**
    那么多个（每行一格，共 `height` 个）。

    **判满**用同一个宽度口径（`_board_width`）：列路径只考察前 `width` 列，
    对不齐的棋盘按最窄的一行算，不按行自身的长度算。
    """
    clear_axis = CLEAR_AXIS_BY_GRAVITY[ctx.axis]
    width = _board_width(board)
    groups: list[ClearGroup] = []
    for index in _full_indices(board, clear_axis):
        if clear_axis == "row":
            cells = tuple((x, index) for x in range(width))
        else:
            cells = tuple((index, y) for y in range(len(board)))
        groups.append(ClearGroup(axis=clear_axis, index=index, cells=cells))
    return tuple(groups)


def classify_placement(
    board: Sequence[Sequence[int]],
    placement: PlacementInfo,
    ctx: RuleContext,
) -> PlacementClass:
    """判定本次放置的分类：T-Spin 标记 + 计分类别键。

    M0 的 T-Spin 判定是**最小版**：三个必要条件（T 型 · 最后一手是旋转 ·
    3×3 外框四角里有三个被占）加一次占位统计。**Mini 与三档区分是 M1**
    （架构 §7.3.1 纪律 3 · spec §7.5）。

    占位统计把**本次放置的格子自己排除掉**——方块自己的格子不算墙。
    """
    cleared = len(_full_indices(board, CLEAR_AXIS_BY_GRAVITY[ctx.axis]))
    spin = _is_tspin(board, placement, ctx)
    return PlacementClass(
        tSpin="tspin" if spin else "none",
        classKey=_class_key(spin, cleared),
    )


def score_clear(
    groups: Sequence[ClearGroup],
    ctx: RuleContext,
    prev: ScoreState,
) -> ScoreDelta:
    """按消行组计分（纯函数：返回增量与新状态，**落笔由内核做**）。

    分类从 `ClearGroup.classification` 读——内核把 `classify_placement` 的结果
    挂到本次消行的每个组上，本函数只搬运不重新判定（架构 §7.3.1）。

    没有分类时按"普通消行"算（组数就是消行数）；分类里的 `classKey` 命中计分表
    时以分类为准（规则集自己的判断优先）。`classKey == "none"` 不算数——
    那表示"没有类别"，不是"零分"。

    ## 连击（`combo`）

    **连续消行的次数**：本次消行 `combo + 1`，奖励按**消行前**的连击数给。
    **没消行的放置（落空）把连击清零**——放了一块没消行的方块，下一块消行
    不该接着拿连击奖励。

    **落空由内核每次锁定后都调用本函数（含空组）来表达**：空组 = 落空 =
    连击断。**内核不自己改 `combo`**——计分口径归规则集。

    ## 背面连击（`b2b`）

    这一位表示「**当前处于背面连击链中**」：链在**本次消行是难消**（四消 /
    T-Spin）时进入，普通消行或落空时断开。

    **B2B 倍率**（M1）的条件是「本次难消 ∧ 上一手也难消」——`prev.b2b`
    正是"上一手在链中"的答案（M0 的计分表里没有 B2B 倍率，见 `tables.py`）。

    **这一位不是"链长 ≥ 2"**，它表示"链中"；链长由规则集在计分时用
    「本次难消 ∧ `prev.b2b`」当场判。

    ## 消行数（`linesDelta`）

    **组数就是消掉的线数**——重力向下时线是行，横向重力时线是列。`lines`
    记的始终是"消掉了几条线"，与重力轴无关（横向重力的口径见
    `docs/plan-M0.md` S13 段）。
    """
    if not groups:
        # 落空：连击与背面连击链都断在这里（内核每次锁定后都调本函数）。
        return ScoreDelta(
            scoreDelta=0,
            linesDelta=0,
            state=ScoreState(
                score=prev.score,
                lines=prev.lines,
                combo=0,
                b2b=False,
                level=prev.level,
            ),
        )
    cleared = len(groups)
    classification = _first_classification(groups)
    spin = classification is not None and classification.tSpin != "none"
    class_key = _effective_class_key(classification, spin, cleared)
    score_delta = SCORE_TABLE[class_key] + COMBO_BONUS * prev.combo
    state = ScoreState(
        score=prev.score + score_delta,
        lines=prev.lines + cleared,
        combo=prev.combo + 1,
        # 本次是难消就在链中，普通消行断链——**不与 `prev.b2b` 相与**：
        # 那样链永远起不来（从 `False` 起步的 AND 恒为 `False`）。
        b2b=class_key in DIFFICULT_CLASS_KEYS,
        level=prev.level,
    )
    return ScoreDelta(scoreDelta=score_delta, linesDelta=cleared, state=state)


def shape_cells(
    ctx: RuleContext,
    pieceType: str,
    rotation: int,
) -> tuple[tuple[int, int], ...]:
    """取形状表：方块在当前重力轴下的占位格子。

    朝向按 4 取模（负朝向也归一）。**表整体由规则集提供，内核不做变换**
    （架构 §7.3.1 纪律 2）——所以这里是查表，不是"标准表 + 方向参数去转"。
    """
    axis_table = SHAPE_TABLE.get(ctx.axis)
    if not axis_table:
        raise ValueError(f"重力轴 {ctx.axis!r} 没有形状表（M0 只提供 down 轴）")
    piece_table = axis_table.get(pieceType)
    if piece_table is None:
        raise ValueError(f"未知方块类型 {pieceType!r}")
    cells = piece_table.get(rotation % ROTATION_COUNT)
    if cells is None:
        raise ValueError(f"方块 {pieceType!r} 没有朝向 {rotation % ROTATION_COUNT}")
    return cells


def kick_offsets(
    ctx: RuleContext,
    pieceType: str,
    fromRotation: int,
    toRotation: int,
) -> tuple[tuple[int, int], ...]:
    """取踢墙表：旋转时的候选偏移（含 180°）。

    M0 是**恒等偏移的最小表**：唯一候选是 `(0, 0)`。标准踢墙表逐条验证是
    **M1 的开门判据**（plan-M0.md S13 常见坑）。

    `pieceType` 在 M0 用不上（恒等偏移与方块无关）；M1 补标准表时 I / O 会
    各有偏移，届时的表结构会多一层方块类型。
    """
    axis_table = KICK_TABLE.get(ctx.axis)
    if not axis_table:
        raise ValueError(f"重力轴 {ctx.axis!r} 没有踢墙表（M0 只提供 down 轴）")
    offsets = axis_table.get((fromRotation % ROTATION_COUNT, toRotation % ROTATION_COUNT))
    if offsets is None:
        raise ValueError(f"没有从朝向 {fromRotation} 到朝向 {toRotation} 的踢墙偏移")
    return offsets


def _is_tspin(
    board: Sequence[Sequence[int]],
    placement: PlacementInfo,
    ctx: RuleContext,
) -> bool:
    """最小 T-Spin 判定：T 型 · 最后一手是旋转 · 外框四角占其三。"""
    if ctx.axis != "down":
        # M0 只判标准朝向下的 T-Spin：横向重力下形状表还没有（见 tables.py）。
        return False
    if placement.pieceType != "T" or not placement.lastMoveWasRotation:
        return False
    junction = _junction_cell(placement.cells)
    if junction is None:
        return False
    jx, jy = junction
    occupied = 0
    for corner in ((jx - 1, jy - 1), (jx + 1, jy - 1), (jx - 1, jy + 1), (jx + 1, jy + 1)):
        if corner in placement.cells:
            continue
        if _cell_at(board, corner[0], corner[1]) != 0:
            occupied += 1
    return occupied >= 3


def _effective_class_key(
    classification: PlacementClass | None,
    spin: bool,
    cleared: int,
) -> str:
    """本次消行实际用的计分类别键。

    分类里的 `classKey` 命中计分表就用它（规则集自己的判断优先）；
    `none` 与未命中都退回"按消行数推"。
    """
    if classification is not None:
        key = classification.classKey
        if key != "none" and key in SCORE_TABLE:
            return key
    return _class_key(spin, cleared)


# ---------------------------------------------------------------------------
# 整体替换入口
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StandardRuleSet:
    """标准规则集：8 条接口的组合入口（FR-4.9）。

    **不可变、无状态**：8 条实现全是模块级纯函数，本类只做组装——
    规则集一旦持有可变状态，FR-10.2 的存档恢复就得额外序列化它。
    """

    wrap_cell: WrapCell = wrap_cell
    gravity_vector: GravityVector = gravity_vector
    gravity_interval: GravityInterval = gravity_interval
    scan_clears: ScanClears = scan_clears
    classify_placement: ClassifyPlacement = classify_placement
    score_clear: ScoreClear = score_clear
    shape_cells: ShapeCells = shape_cells
    kick_offsets: KickOffsets = kick_offsets


#: 标准规则集的单例（不可变、无状态）。
STANDARD_RULESET = StandardRuleSet()

#: **编译期自证**：标准规则集满足 `RuleSet` 协议（结构化类型检查）。
#:
#: 这一行不是装饰——协议是结构化的，实现与协议对不上时 pyright 会当场报错。
#: 少一条接口、参数名写错、类型对不上，都在这一行暴露。
_RULE_SET_CONFORMANCE: RuleSet = STANDARD_RULESET


__all__ = [
    "STANDARD_RULESET",
    "StandardRuleSet",
    "classify_placement",
    "gravity_interval",
    "gravity_vector",
    "kick_offsets",
    "scan_clears",
    "score_clear",
    "shape_cells",
    "wrap_cell",
]
