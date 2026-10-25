"""标准规则集的最小实现（M0 · plan-M0.md S13 · 架构 §7.3）。

## 最小到哪

M0 只保证**接口定稿 + 最小实现**（架构 §7.3 · spec-M0.md §7.5）：

- `wrap_cell` 恒等；`gravity_vector` 按轴给向量；`gravity_interval` 查最小表；
- `scan_clears` 整行 / 整列满即消，产出带**轴 + 索引 + 格子清单**的组；
- `classify_placement` 给 **T-Spin 五档**（`none` / `tspin` / `mini` /
  `single` / `double` / `triple`）+ 计分类别键；
- `score_clear` 基础分值 + 连击奖励；
- `shape_cells` 标准 7 种方块 × 4 朝向；`kick_offsets` **标准踢墙表**
  （SRS + 180° 扩展，ADR-068）。

**标准踢墙表与完整计分表是 M1**（spec-M1.md §7.2 / §7.4）——M0 只做到"接口
定稿 + 最小实现"，那些表的逐条验证留到 M1 做，正是为了让 M1 的判据有意义。

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

## 计分口径（连击 / 背面连击 / 消行数 / 等级）

- **连击**（`combo`）：连续消行的次数；**落空清零**。落空由**内核每次锁定后
  都调用 `score_clear`（含空组）**表达——空组即落空，**内核不自己改 `combo`**。
- **背面连击**（`b2b`）：这一位表示「当前处于背面连击链中」——本次消行是难消
  （四消 / T-Spin **消行**）就入链，普通消行或落空断链。**B2B 倍率**的条件是
  「本次难消 ∧ 上一手也难消」，`prev.b2b` 正是"上一手在链中"的答案；倍率
  **× 3/2 只作用于基础分**（整数运算，不含连击奖励与软降分）。
- **消行数**（`linesDelta`）：**组数就是消掉的线数**——重力向下时线是行，横向
  重力时线是列；`lines` 记的始终是"消掉了几条线"，与重力轴无关。
- **等级**（`level`）：**每消 10 行升 1 级**，**由规则集算**——内核不自己数
  行数定等级（换规则集就该能改阈值）。

## 一个已知缺口：**消 0 行的类别键送不到计分**

分类挂在**消行组**上（架构 §7.3.1 纪律 4），而**消 0 行的放置根本没有组**
——内核这时调的是 `score_clear((), …)`，也就是**落空**。所以
`tspin-mini`（不消行的 T-Spin Mini，guideline 口径 400 分）与 `tspin-none`
这两档**当前送不到计分**：表里按 spec §7.4 备着，但要真发这 400 分，得让
"没消行但有分类"这条信息也走通——那是**改冻结签名**的活（M1 的三次 major
变更里没有这一条），留到后续里程碑处理，缺口记在 `PROGRESS.md` 的待办里。

## T-Spin 分类怎么到达计分

`classify_placement` 的结果挂在 `ClearGroup.classification` 上，
`score_clear` 从组里读它（**内核只搬运、不重新判定**）。**不给签名加参数**
——签名是冻结的（ADR-061）。`scan_clears` 单独调用时不知道放置上下文，
所以它产出的组 `classification` 是 `None`。

## T-Spin 五档与「前两角 / 后两角」

`classify_placement` 判出 `tSpin` 的**五档**（`none` / `tspin` / `mini` /
`single` / `double` / `triple`）与**计分类别键**（`classKey`）——**两个字段
分开给**：形态与计分类别不是一回事（Mini 消行时前者是 `mini`、后者是
`tspin-single`），合成一个字段就丢信息。

「前两角 / 后两角」的口径写在 `classify_placement` 的 docstring 与
`_tspin_corner_flags` 里（**别猜**：那是这类 bug 的经典来源）。

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
    SoftDropScore,
    WrapCell,
)
from .tables import (
    B2B_MULTIPLIER_DENOMINATOR,
    B2B_MULTIPLIER_NUMERATOR,
    CLEAR_AXIS_BY_GRAVITY,
    CLEAR_NAMES,
    COMBO_BONUS,
    DIFFICULT_CLASS_KEYS,
    GRAVITY_INTERVAL_FRAMES,
    GRAVITY_VECTORS,
    KICK_GROUP_BY_PIECE,
    KICK_TABLE,
    LINES_PER_LEVEL,
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


def _plain_class_key(cleared: int) -> str:
    """**普通消行**的类别键：消 0 行 → `none`；1/2/3/4 行 → `single` /
    `double` / `triple` / `tetris`；5 行以上按 `tetris` 算（不另立类别）。

    **这是「不是 T-Spin」时的口径**——T-Spin 的类别键见 `_tspin_class_key`。
    """
    if cleared <= 0:
        return "none"
    return CLEAR_NAMES.get(cleared, "tetris")


def _tspin_line_grade(cleared: int) -> str:
    """T-Spin **消行后**的档位：1 / 2 / 3 行 → `single` / `double` / `triple`。

    **T 最多只能消 3 行**（T 方块本身最多跨 3 行），所以 3 行及以上都算
    `triple`——不另立 `tspin-tetris`（`classKey` 的取值域里没有它，
    计分表里也没有）。

    **消 0 行返回 `none`**（批次 B 审查 L-1）：三个调用点当前都在调用前先挡掉
    0 行，所以这条分支**当前不可达**。但计分表里 `tspin-single` = 800 分——
    **将来谁少写一句 `cleared <= 0` 的守卫，就会把"没消行的 T-Spin"算成
    800 分，而且不报错**（`tspin-none` 与 `tspin-mini` 分值都在表里）。
    **在函数边界上就暴露**，比指望每个调用点都记得挡更可靠。
    """
    if cleared <= 0:
        return "none"
    if cleared == 1:
        return "single"
    if cleared == 2:
        return "double"
    return "triple"


def _apply_b2b(base: int) -> int:
    """B2B 倍率：基础分 **× 3/2**（**整数运算，不许浮点**）。

    `ScoreDelta.scoreDelta` 的类型是 `int`——写成浮点 `1.5` 直接破类型。
    倍率记成整数比（`B2B_MULTIPLIER_NUMERATOR` / `B2B_MULTIPLIER_DENOMINATOR`），
    基础分全是 **100 的倍数** → **乘完仍是整数**（800 → 1200 · 1200 → 1800 ·
    1600 → 2400）。

    **乘不整就当场报错**：将来有人往计分表里加一条奇数基础分，这行会立刻
    炸掉，而不是悄悄截断（截断 = 分数与手算对不上，且没人知道差在哪）。
    """
    scaled = base * B2B_MULTIPLIER_NUMERATOR
    if scaled % B2B_MULTIPLIER_DENOMINATOR:
        raise ValueError(
            f"基础分 {base} 乘 B2B 倍率 {B2B_MULTIPLIER_NUMERATOR}/"
            f"{B2B_MULTIPLIER_DENOMINATOR} 不是整数：分数必须是 100 的倍数"
        )
    return scaled // B2B_MULTIPLIER_DENOMINATOR


def _class_key_for(spin: bool, cleared: int) -> str:
    """**没有分类时**的回退口径：按「是否 T-Spin + 消了几行」推类别键。

    普通消行走 `_plain_class_key`；T-Spin 走 `tspin-<档位>`（档位见
    `_tspin_line_grade`——T 最多消 3 行）。
    """
    if cleared <= 0:
        return "none"
    if spin:
        return f"tspin-{_tspin_line_grade(cleared)}"
    return _plain_class_key(cleared)


def _tspin_class_key(spin: str, cleared: int) -> str:
    """T-Spin 的**计分类别键**（与 `tSpin` 分开给）。

    | 情形 | `tSpin` | `classKey` |
    | --- | --- | --- |
    | T-Spin 消 0 行（非 Mini） | `tspin` | `tspin-none` |
    | T-Spin Mini 消 0 行 | `mini` | `tspin-mini` |
    | T-Spin 消 1 / 2 / 3 行 | `single` / `double` / `triple` | `tspin-single` / … |
    | **T-Spin Mini 消了行** | **`mini`** | 按行数取 `tspin-single` / … |

    **Mini 消行时 `tSpin` 仍是 `mini` 而 `classKey` 按行数取**——形态与
    计分类别不是一回事，合成一个字段就丢信息（spec §7.3）。
    """
    if cleared <= 0:
        return "tspin-mini" if spin == "mini" else "tspin-none"
    return f"tspin-{_tspin_line_grade(cleared)}"


def _first_classification(groups: Sequence[ClearGroup]) -> PlacementClass | None:
    """取本次消行的放置分类（内核把同一个分类挂到每个组上）。"""
    for group in groups:
        if group.classification is not None:
            return group.classification
    return None


# ---------------------------------------------------------------------------
# 九条接口
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
    """判定本次放置的分类：**T-Spin 档位** + **计分类别键**。

    ## `tSpin`（五档 · spec §7.3）

    | `tSpin` | 含义 |
    | --- | --- |
    | `none` | 不是 T-Spin |
    | `tspin` | 是 T-Spin，但**不消行**（非 Mini） |
    | `mini` | **T-Spin Mini**（只在消 0 行时是这个形态；消了行仍是 `mini`） |
    | `single` / `double` / `triple` | T-Spin 消 1 / 2 / 3 行 |

    ## 判定三步

    1. **必要条件**：`pieceType == "T"` 且 `lastMoveWasRotation`——平移过来的
       不算（spec §7.3）。
    2. **占位统计**：T 的 **3×3 外框四角**里被占**至少三个**。
    3. **Mini 与完整的分界**：**没有发生踢墙** 且 **背头两角都被占** 且
       **贴头两角没占满** → `mini`；其余 → 完整 T-Spin。

    ## 「前两角 / 后两角」的口径（写死在这，别猜）

    **「头」= T 的突出格**——三个格子成排之外的那一格（Tetris Wiki 叫
    "the sticking out mino"）。它指向哪一侧，**贴头的那两个角**就在哪一侧。

    ```
    头朝上（.X. / XXX）：   贴头两角 = 外框的**上**两个角
    头朝右（.X. / .XX / .X.）：贴头两角 = 外框的**右**两个角
    ```

    **Mini 的判据 = 背头两角占满 + 贴头两角没占满**：
    **头被两侧的角夹住**（贴头两角都占）是**完整** T-Spin；**头只被夹住
    一侧**（贴头只占一个）而背面被占满 → **Mini**——头没真正楔进去。

    **与两套外部口径的对照**（三者判出的结果完全一致）：
    - **guideline / Tetris Wiki**：把*贴头*那两个角叫 "front corners"，
      Mini 的定义是 "only one mino in two front corners and two minoes to the
      back corners"——即贴头只占一个 + 背头占两个。
    - **spec §7.3**：把「三格成排的那一侧」（= 远离头的方向）叫「正面」，
      Mini 的条件写成「前两角被占 且 后两角没有同时占满」——即背头占两个 +
      贴头没占满。**与上一条是同一个判据，只是把两对角换了名字**。

    **纯函数**：只读 `board` / `placement` / `ctx`，**不记任何"上一手干了
    什么"**——上一手信息只从 `PlacementInfo` 来（架构 §7.3.1 纪律 3）。
    """
    cleared = len(_full_indices(board, CLEAR_AXIS_BY_GRAVITY[ctx.axis]))
    spin = _tspin_grade(board, placement, ctx)
    if spin == "none":
        return PlacementClass(tSpin="none", classKey=_plain_class_key(cleared))
    if cleared <= 0:
        # 不消行的 T-Spin：Mini 与完整是两档（`tspin-mini` / `tspin-none`）。
        return PlacementClass(tSpin=spin, classKey=_tspin_class_key(spin, cleared))
    grade = _tspin_line_grade(cleared)
    # **Mini 消了行**：形态仍是 `mini`（Mini 与完整是两回事），计分类别按
    # 行数取——Mini 消行按完整 T-Spin 计分（spec §7.3 的表）。
    return PlacementClass(
        tSpin="mini" if spin == "mini" else grade,
        classKey=f"tspin-{grade}",
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
    T-Spin **消行**）时进入，普通消行或落空时断开。

    **B2B 倍率**：条件是「**本次难消 ∧ 上一手也难消**」——`prev.b2b` 正是
    "上一手在链中"的答案。倍率 **× 3/2 作用于基础分**（**不含连击奖励、
    不含软降分**）。

    **难消的定义**：四消与 T-Spin **消行**。**T-Spin 不消行不算难消**
    （`tspin-none` / `tspin-mini` 都不在难消表里）——它没消行，进链会让
    B2B 变成"转进去也算数"，与 guideline 不符。

    **这一位不是"链长 ≥ 2"**，它表示"链中"；链长由规则集在计分时用
    「本次难消 ∧ `prev.b2b`」当场判。

    ## 消行数（`linesDelta`）

    **组数就是消掉的线数**——重力向下时线是行，横向重力时线是列。`lines`
    记的始终是"消掉了几条线"，与重力轴无关（横向重力的口径见
    `docs/plan-M0.md` S13 段）。

    ## 等级（`level`）

    **每消 10 行升 1 级**（`LINES_PER_LEVEL`）——**由规则集算**，内核不自己
    数行数定等级（换规则集就该能改阈值，架构 §7.3.1）。内核只比较前后等级
    并发 `progress:level-changed`。

    等级 = `总行数 // 10 + 1`（0–9 行是 1 级）。**一次消多行也能跨档**：
    9 行时消 4 行 → 13 行 → 2 级（不是"除以 10 取整"那种按本次消行数算的
    写法）。**等级只升不降**：`prev.level` 更高时保持不变。
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
    base = SCORE_TABLE[class_key]
    difficult = class_key in DIFFICULT_CLASS_KEYS
    # **B2B 倍率只作用于基础分**——连击奖励不乘（口径写进 spec §7.4）。
    if difficult and prev.b2b:
        base = _apply_b2b(base)
    score_delta = base + COMBO_BONUS * prev.combo
    new_lines = prev.lines + cleared
    state = ScoreState(
        score=prev.score + score_delta,
        lines=new_lines,
        combo=prev.combo + 1,
        # 本次是难消就在链中，普通消行断链——**不与 `prev.b2b` 相与**：
        # 那样链永远起不来（从 `False` 起步的 AND 恒为 `False`）。
        b2b=difficult,
        # **等级由规则集算**：每消 10 行升 1 级；**只升不降**。
        level=max(prev.level, new_lines // LINES_PER_LEVEL + 1),
    )
    return ScoreDelta(scoreDelta=score_delta, linesDelta=cleared, state=state)


def soft_drop_score(ctx: RuleContext, distance: int) -> int:
    """软降得分：本次软降应加的分（标准规则集 = **每下移一格加 1 分**）。

    `distance` 是本次**实际下移**的格数（被墙挡住则为 0 → 加 0 分）。

    M0 把这条**写死在核心里**（`core.py` 的 `+1`）——换规则集就改不了软降得分，
    撞 G4-1（M4 落地变体时内核文件必须零差异）。M1 把它归规则集：内核在
    S11 改为调用这里，**删掉写死的 `+1`**。

    `ctx` 用不上（标准玩法的软降分不随等级 / 重力轴变），但**参数不能删**：
    参数表是冻结的（ADR-061），变体规则集可以按 `ctx` 给不同的分。
    """
    return distance


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
        raise ValueError(f"重力轴 {ctx.axis!r} 没有形状表（M1 只提供 down 轴，其余属 M4-A5）")
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

    **M1 起是标准踢墙表**（SRS 权威表 + 180° 扩展表，ADR-068）：候选按
    **优先级**排列，**第一个能落下的就用它**，全部落不下 → 旋转被拒
    （命令仍被接受，另发受阻事件，ADR-043）。

    **表的第三层是踢墙组**（`"I"` / `"JLSTZ"` / `"O"`）——方块类型 → 组的
    映射由规则集提供（`tables.KICK_GROUP_BY_PIECE`）：J / L / S / T / Z
    共用一套，I 独立一套，O 不踢墙。

    **坐标是本项目那套**（x 右正、**y 下正**）——权威表的 y 上正，落地时
    **y 分量取反**（换算关系写在 `tables.py` 的注释里）。
    """
    axis_table = KICK_TABLE.get(ctx.axis)
    if not axis_table:
        raise ValueError(f"重力轴 {ctx.axis!r} 没有踢墙表（M1 只提供 down 轴，其余属 M4-A5）")
    by_group = axis_table.get((fromRotation % ROTATION_COUNT, toRotation % ROTATION_COUNT))
    if by_group is None:
        raise ValueError(f"没有从朝向 {fromRotation} 到朝向 {toRotation} 的踢墙偏移")
    group = KICK_GROUP_BY_PIECE.get(pieceType)
    if group is None:
        raise ValueError(f"未知方块类型 {pieceType!r}")
    return by_group[group]


def _is_diagonal(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """两个格子是否**斜向**相邻（`|dx| == 1` 且 `|dy| == 1`）。"""
    return abs(a[0] - b[0]) == 1 and abs(a[1] - b[1]) == 1


def _tspin_frame(
    cells: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """T 形的**枢纽格**与**突出格（头）**；不是 T 形则返回 `None`。

    - **枢纽格**：与另外三格都正交相邻的那一格（正方形的中心）。
    - **突出格（头）**：T 从「三个格子成排」里**伸出来**的那一格——它是
      **唯一**与另外两格都**斜向**相邻的格子。

    **别用「只有一个正交邻居」去找头**：T 的三个非枢纽格里，**横排的两个
    端头也只有一个正交邻居**（它们挨着枢纽格）——照那个判据会把头认成
    端头，**方位一反，Mini 与完整的分界跟着反**（这个坑踩过一次）。

    两者都唯一即说明形状是 T（T 形枢纽唯一，找到即说明是 T）。
    """
    occupied = set(cells)
    junction: tuple[int, int] | None = None
    for x, y in cells:
        neighbors = sum(
            1 for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)) if (nx, ny) in occupied
        )
        if neighbors == 3:
            junction = (x, y)
            break
    if junction is None:
        return None
    others = [cell for cell in cells if cell != junction]
    for cell in others:
        rest = [other for other in others if other != cell]
        if rest and all(_is_diagonal(cell, other) for other in rest):
            return junction, cell
    return None


def _tspin_corner_flags(
    board: Sequence[Sequence[int]],
    placement: PlacementInfo,
) -> tuple[bool, bool, bool, bool] | None:
    """T 的 **3×3 外框四角**的占位情况：`(贴头左, 贴头右, 背头左, 背头右)`。

    **不是 T 形则返回 `None`**（连枢纽格都找不到，谈不上外框）。

    **本次放置的格子自己不算墙**（spec §7.3）：方块自己的格子排除在占位
    统计之外。**实际它对 T 自身是空操作**——T 的四个格子是枢纽格加三个
    **正交**邻格，而外框四角是枢纽格的**斜向**邻格，两组不重叠；这条排除
    留着是**口径**，不是修 bug。

    **越界视为空**（棋盘外面没有墙），与 `_cell_at` 同一口径。
    """
    frame = _tspin_frame(placement.cells)
    if frame is None:
        return None
    (jx, jy), (hx, hy) = frame
    if hy != jy:
        # 头在枢纽的**上方或下方** → 前后按**行**分。
        near_y = jy + (1 if hy > jy else -1)
        far_y = jy + (-1 if hy > jy else 1)
        near = ((jx - 1, near_y), (jx + 1, near_y))
        far = ((jx - 1, far_y), (jx + 1, far_y))
    else:
        # 头在枢纽的**左方或右方** → 前后按**列**分。
        near_x = jx + (1 if hx > jx else -1)
        far_x = jx + (-1 if hx > jx else 1)
        near = ((near_x, jy - 1), (near_x, jy + 1))
        far = ((far_x, jy - 1), (far_x, jy + 1))

    own = set(placement.cells)
    flags = []
    for cell in near + far:
        if cell in own:
            flags.append(False)
        else:
            flags.append(_cell_at(board, cell[0], cell[1]) != 0)
    return (flags[0], flags[1], flags[2], flags[3])


def _is_kickless(placement: PlacementInfo) -> bool:
    """本次旋转**没有发生踢墙**。

    三种情况都算"没踢墙"：`kickIndex` 为 `None`（压根没踢）· `kickIndex`
    为 0（用的是第一个候选 `(0, 0)`）· `kickOffset` 为 `(0, 0)`（没位移）。

    **口径来自 spec §7.3**：Mini 的前提就是"没踢墙"——踢过的 T-Spin 一律
    算完整（guideline 里还有一条"最后一次旋转位移是 1×2 时仍算完整"的
    例外，本项目按 spec 的粗口径处理：**任何踢墙都不算 Mini**）。
    """
    if placement.kickIndex is None:
        return True
    if placement.kickIndex == 0:
        return True
    return placement.kickOffset == (0, 0)


def _tspin_grade(
    board: Sequence[Sequence[int]],
    placement: PlacementInfo,
    ctx: RuleContext,
) -> str:
    """T-Spin 判定：返回 `"none"` / `"mini"` / `"tspin"`。

    **只判形态，不判消行数**——消行后的档位由 `classify_placement` 按本次
    消行数给（`tSpin` 与 `classKey` 分开）。
    """
    if ctx.axis != "down":
        # 横向重力下形状表还没有（见 tables.py），判不了 T-Spin。
        return "none"
    if placement.pieceType != "T" or not placement.lastMoveWasRotation:
        return "none"
    flags = _tspin_corner_flags(board, placement)
    if flags is None:
        return "none"
    head_left, head_right, tail_left, tail_right = flags
    occupied = sum(flags)
    if occupied < 3:
        return "none"
    # **Mini 与完整的分界**（口径见 `classify_placement` 的 docstring）：
    # 没踢墙 · **背头两角都被占** · **贴头两角没占满**。
    # **头被两侧的角夹住**（贴头两角都占）→ 完整 T-Spin。
    if _is_kickless(placement) and tail_left and tail_right and not (head_left and head_right):
        return "mini"
    return "tspin"


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
    return _class_key_for(spin, cleared)


# ---------------------------------------------------------------------------
# 整体替换入口
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StandardRuleSet:
    """标准规则集：9 条接口的组合入口（FR-4.9）。

    **不可变、无状态**：9 条实现全是模块级纯函数，本类只做组装——
    规则集一旦持有可变状态，FR-10.2 的存档恢复就得额外序列化它。
    """

    wrap_cell: WrapCell = wrap_cell
    gravity_vector: GravityVector = gravity_vector
    gravity_interval: GravityInterval = gravity_interval
    scan_clears: ScanClears = scan_clears
    classify_placement: ClassifyPlacement = classify_placement
    score_clear: ScoreClear = score_clear
    soft_drop_score: SoftDropScore = soft_drop_score
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
    "soft_drop_score",
    "wrap_cell",
]
