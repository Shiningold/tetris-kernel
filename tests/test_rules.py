"""规则接口与最小标准实现测试（S13 · 脱离事件系统单测）。

**每个规则函数都能脱离事件系统单独测**：直接喂 `Sequence[Sequence[int]]` +
`RuleContext`，断言返回值——不经过总线、不经过内核、不经过快照。

覆盖 plan-M0.md S13 的六条验证：

1. 8 条接口可单测（逐条直接调用）；
2. 纯函数性——同 `ctx` 同输入两次结果相同；不同 `ctx`（不同 `axis`）结果可以不同；
3. `gravity_vector` 返回 `(0, 1)`，`wrap_cell` 是恒等；
4. `scan_clears` 返回的组含**行索引与格子清单**；
5. `rules/` 不 import `kernel`（`rules > contracts` 方向允许）；
6. `rules/` 内无第三方 import、无墙钟读、无模块级 `random.*`。
"""

from __future__ import annotations

import ast
import copy
import operator
import sys
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

import tetris_kernel.rules as rules_pkg
from tetris_kernel.contracts import (
    RULE_SIGNATURES,
    ClearGroup,
    PlacementClass,
    PlacementInfo,
    RuleContext,
    ScoreDelta,
    ScoreState,
)
from tetris_kernel.rules import interface, standard, tables

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

#: 标准玩法的上下文（重力向下）。
CTX_DOWN = RuleContext(frame=10, level=1, piecesPlaced=3, axis="down", stepMs=16)


def _ctx(axis: str = "down", level: int = 1, frame: int = 10) -> RuleContext:
    """造一个上下文（轴 / 等级可换，其余固定）。"""
    return RuleContext(frame=frame, level=level, piecesPlaced=3, axis=axis, stepMs=16)


#: 3 列 × 4 行的棋盘，中间一行被 T 方块填满：
#:
#: ```
#: 0 0 0
#: 1 0 1
#: 1 1 1   ← T 方块（朝向 0，占格 (1,1) (0,2) (1,2) (2,2)）
#: 1 0 0
#: ```
#:
#: T 的枢纽格是 `(1, 2)`，外框四角 `(0,1) (2,1) (0,3) (2,3)` 里有三个被占
#: → 满足最小 T-Spin 判定；同时第 2 行满 → 消 1 行。
TSPIN_BOARD: list[list[int]] = [
    [0, 0, 0],
    [1, 0, 1],
    [1, 1, 1],
    [1, 0, 0],
]

#: 上面那次放置的上下文：T 方块、朝向 0、最后一手是旋转。
TSPIN_PLACEMENT = PlacementInfo(
    pieceType="T",
    rotation=0,
    cells=((1, 1), (0, 2), (1, 2), (2, 2)),
    lastMoveWasRotation=True,
    kickIndex=None,
    kickOffset=None,
)

#: 3 列 × 3 行的棋盘，第 0 列满、每行都不满（横向重力下消列）。
COLUMN_BOARD: list[list[int]] = [
    [1, 0, 0],
    [1, 0, 0],
    [1, 0, 0],
]

#: 零状态计分。
SCORE_ZERO = ScoreState(score=0, lines=0, combo=0, b2b=False, level=1)


# ---------------------------------------------------------------------------
# 验证 1：8 条接口可脱离事件系统单测
# ---------------------------------------------------------------------------


def test_standard_implementation_matches_frozen_signatures() -> None:
    """实现的参数名与 `contracts/rules.py` 的冻结签名逐字一致。"""
    assert interface.signature_mismatches(standard.STANDARD_RULESET) == ()
    for name in RULE_SIGNATURES:
        assert hasattr(standard, name), f"标准实现缺少接口 {name}"


def test_signature_checker_can_actually_fail() -> None:
    """守卫要能触发——参数名写错必须被报出来（否则守卫等于没守）。"""

    def bad_shape_cells(
        ctx: RuleContext, piece_type: str, rotation: int
    ) -> tuple[tuple[int, int], ...]:
        return ()

    broken = SimpleNamespace(
        wrap_cell=standard.wrap_cell,
        gravity_vector=standard.gravity_vector,
        gravity_interval=standard.gravity_interval,
        scan_clears=standard.scan_clears,
        classify_placement=standard.classify_placement,
        score_clear=standard.score_clear,
        soft_drop_score=standard.soft_drop_score,
        shape_cells=bad_shape_cells,
        kick_offsets=standard.kick_offsets,
    )
    problems = interface.signature_mismatches(broken)
    assert len(problems) == 1
    assert "shape_cells" in problems[0]


def test_signature_checker_reports_missing_interface() -> None:
    """少一条接口必须被报出来。"""
    incomplete = SimpleNamespace(wrap_cell=standard.wrap_cell)
    problems = interface.signature_mismatches(incomplete)
    assert len(problems) == 8
    assert all("缺少这条接口" in problem for problem in problems)


def test_standard_soft_drop_score_is_one_point_per_cell() -> None:
    """标准规则的软降得分：**每实际下移一格加 1 分**。

    `distance` 是本次**实际下移**的格数——被墙挡住就是 0，加 0 分。

    这条口径在 M1 开工前就定死了（plan-M1.md §1.4.4 的预验定约定）：内核
    原先写死的 `+1` 在 S11 迁到这里，**换规则集就能改软降得分**（G4-1）。
    """
    assert standard.soft_drop_score(CTX_DOWN, 0) == 0
    assert standard.soft_drop_score(CTX_DOWN, 3) == 3


def test_wrap_cell_is_identity() -> None:
    """验证 3：恒等映射（坐标原样返回）。"""
    assert standard.wrap_cell(3, 4, 10, 20) == (3, 4)
    assert standard.wrap_cell(0, 0, 10, 20) == (0, 0)
    assert standard.wrap_cell(-1, -2, 10, 20) == (-1, -2)


def test_gravity_vector_is_down_by_default() -> None:
    """验证 3：标准玩法重力向下，方向是向量 `(0, 1)`。"""
    assert standard.gravity_vector(CTX_DOWN) == (0, 1)


def test_gravity_vector_follows_axis() -> None:
    """横向重力时 `dy = 0`——方向是向量不是 dy。"""
    assert standard.gravity_vector(_ctx("left")) == (-1, 0)
    assert standard.gravity_vector(_ctx("right")) == (1, 0)
    assert standard.gravity_vector(_ctx("up")) == (0, -1)


def test_gravity_interval_uses_level() -> None:
    """重力间隔查等级表；等级越高间隔越短，但恒 ≥ 1 帧。"""
    first = standard.gravity_interval(_ctx(level=1))
    later = standard.gravity_interval(_ctx(level=5))
    assert first == tables.GRAVITY_INTERVAL_FRAMES[0]
    assert later < first
    assert standard.gravity_interval(_ctx(level=99)) == tables.GRAVITY_INTERVAL_FRAMES[-1]
    assert standard.gravity_interval(_ctx(level=0)) == tables.GRAVITY_INTERVAL_FRAMES[0]
    assert all(frames >= 1 for frames in tables.GRAVITY_INTERVAL_FRAMES)


def test_scan_clears_returns_index_and_cells() -> None:
    """验证 4：组里带**行索引与格子清单**，不只是数量。"""
    groups = standard.scan_clears(TSPIN_BOARD, CTX_DOWN)
    assert len(groups) == 1
    group = groups[0]
    assert group.axis == "row"
    assert group.index == 2
    assert group.cells == ((0, 2), (1, 2), (2, 2))
    assert group.classification is None


def test_scan_clears_axis_column() -> None:
    """横向重力 → 消行轴是 `column`（两处 `axis` 是两套词汇）。"""
    groups = standard.scan_clears(COLUMN_BOARD, _ctx("left"))
    assert len(groups) == 1
    assert groups[0].axis == "column"
    assert groups[0].index == 0
    assert groups[0].cells == ((0, 0), (0, 1), (0, 2))


def test_scan_clears_empty_board() -> None:
    """没有满行 → 没有组。"""
    assert standard.scan_clears([[0, 0], [0, 0]], CTX_DOWN) == ()
    assert standard.scan_clears([], CTX_DOWN) == ()


def test_scan_clears_line_length_follows_the_axis() -> None:
    """一条线多长：**行路径 = 宽度，列路径 = 高度**（M0 全量审查 M0-9）。

    原 docstring 写的是"一条线 = 棋盘宽度那么长"，可列路径的组是**整列**——
    它的格子清单是**每行一格**，长度等于**棋盘高度**。口径写错时，塌落动画
    与事件负载会按错的长度算——这里把口径钉死。
    """
    board = [
        [1, 1, 1],
        [1, 0, 0],
        [1, 0, 0],
        [1, 0, 0],
    ]

    rows = standard.scan_clears(board, CTX_DOWN)
    columns = standard.scan_clears(board, _ctx("left"))

    assert rows, "第 0 行是满的，却没有行组"
    assert len(rows[0].cells) == 3, "行路径的组 = 整行，长度 = 宽度"
    assert columns, "第 0 列是满的，却没有列组"
    assert len(columns[0].cells) == 4, "列路径的组 = 整列，长度 = 高度"


def test_scan_clears_on_a_zero_width_board_finds_no_line() -> None:
    """零宽棋盘（每行 0 格）→ **一条满线都没有**（M0 全量审查 M0-10）。

    `all()` 对空切片**恒真**：不挡的话「零列的棋盘」会凭空冒出 N 条满行，
    每条的格子清单还是空的——塌落动画按 0 个格子算，还凭空多出 N 次消行。
    """
    board = [[], [], []]

    assert standard.scan_clears(board, CTX_DOWN) == ()
    assert standard.scan_clears(board, _ctx("left")) == ()

    # **分类那侧同样**：消行数是 0，类别键是 `none`（不是"消了 3 行"）。
    classification = standard.classify_placement(board, TSPIN_PLACEMENT, CTX_DOWN)
    assert classification.classKey == "none"
    assert classification.tSpin == "none"


def test_classify_placement_detects_tspin() -> None:
    """T-Spin 判定：T 型 + 最后一手是旋转 + 外框四角占其三。

    **M1 起 `tSpin` 是五档**：这次放置消了 1 行 → 档位是 `single`
    （`tspin` 那一档是「是 T-Spin 但不消行」）。计分类别键仍是
    `tspin-single`——**形态与计分类别分开给**。
    """
    classification = standard.classify_placement(TSPIN_BOARD, TSPIN_PLACEMENT, CTX_DOWN)
    assert isinstance(classification, PlacementClass)
    assert classification.tSpin == "single"
    assert classification.classKey == "tspin-single"


def test_classify_placement_without_rotation_is_not_tspin() -> None:
    """最后一手不是旋转 → 不是 T-Spin（必要条件）。"""
    placement = PlacementInfo(
        pieceType="T",
        rotation=0,
        cells=TSPIN_PLACEMENT.cells,
        lastMoveWasRotation=False,
        kickIndex=None,
        kickOffset=None,
    )
    classification = standard.classify_placement(TSPIN_BOARD, placement, CTX_DOWN)
    assert classification.tSpin == "none"
    assert classification.classKey == "single"


def test_classify_placement_class_key_counts_cleared_lines() -> None:
    """类别键按消行数推：0 行 → none，1 行 → single。"""
    empty = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
    classification = standard.classify_placement(empty, TSPIN_PLACEMENT, CTX_DOWN)
    assert classification.classKey == "none"


def test_score_clear_basic_scoring() -> None:
    """基础计分：1 行普通消行 = 100 分，消行数与连击数各进 1。"""
    groups = standard.scan_clears(TSPIN_BOARD, CTX_DOWN)
    delta = standard.score_clear(groups, CTX_DOWN, SCORE_ZERO)
    assert isinstance(delta, ScoreDelta)
    assert delta.scoreDelta == 100
    assert delta.linesDelta == 1
    assert delta.state.score == 100
    assert delta.state.lines == 1
    assert delta.state.combo == 1
    assert delta.state.level == SCORE_ZERO.level


def test_score_clear_combo_bonus() -> None:
    """连击奖励按消行前的连击数给。"""
    groups = standard.scan_clears(TSPIN_BOARD, CTX_DOWN)
    prev = ScoreState(score=0, lines=9, combo=2, b2b=False, level=1)
    delta = standard.score_clear(groups, CTX_DOWN, prev)
    assert delta.scoreDelta == 100 + tables.COMBO_BONUS * 2
    assert delta.state.combo == 3


def test_score_clear_b2b_flag() -> None:
    """背面连击：本次是难消就入链，普通消行断链。

    **`False → True` 那一跳必须守住**：旧写法把 `b2b` 与 `prev.b2b` 相与，
    从 `False` 起步的 AND 恒为 `False`——链永远起不来，FR-4.6 静默失效。
    """
    difficult = (
        ClearGroup(
            axis="row",
            index=0,
            cells=((0, 0), (1, 0)),
            classification=PlacementClass(tSpin="none", classKey="tetris"),
        ),
    )
    ordinary = (ClearGroup(axis="row", index=0, cells=((0, 0),)),)
    cold = ScoreState(score=0, lines=0, combo=0, b2b=False, level=1)

    # 起链那一跳：上一手不在链中，本次是难消 → 进链。
    entered = standard.score_clear(difficult, CTX_DOWN, cold).state
    assert entered.b2b is True

    # 链中再来一次难消 → 仍在链中。
    assert standard.score_clear(difficult, CTX_DOWN, entered).state.b2b is True

    # 普通消行 → 断链。
    assert standard.score_clear(ordinary, CTX_DOWN, entered).state.b2b is False


def test_score_clear_breaks_combo_when_nothing_cleared() -> None:
    """落空（没消行）把**连击与背面连击链**都断掉。

    标准玩法里"没消行"就该中断连击——旧写法空组早返回原状态，连击跨着落空
    继续累加，奖励按**已经断了的**连击数发。
    """
    prev = ScoreState(score=500, lines=3, combo=2, b2b=True, level=1)
    delta = standard.score_clear((), CTX_DOWN, prev)

    assert delta.scoreDelta == 0
    assert delta.linesDelta == 0
    assert delta.state.combo == 0
    assert delta.state.b2b is False
    # 分数、行数、等级不动。
    assert delta.state.score == 500
    assert delta.state.lines == 3
    assert delta.state.level == 1


def test_combo_bonus_is_not_paid_after_a_broken_combo() -> None:
    """落空之后的那一次消行**不拿连击奖励**。"""
    groups = standard.scan_clears(TSPIN_BOARD, CTX_DOWN)

    first = standard.score_clear(groups, CTX_DOWN, SCORE_ZERO)
    assert first.scoreDelta == tables.SCORE_TABLE["single"]

    broken = standard.score_clear((), CTX_DOWN, first.state)
    again = standard.score_clear(groups, CTX_DOWN, broken.state)

    assert again.scoreDelta == tables.SCORE_TABLE["single"]
    assert again.state.combo == 1


def test_lines_delta_counts_cleared_lines_under_lateral_gravity() -> None:
    """横向重力下消的是**列**，但 `lines` 记的仍是**消掉的线数**。

    组数 = 消掉的线数：重力向下时线是行，横向重力时线是列——`linesDelta` 与
    重力轴无关（口径写进 `docs/plan-M0.md` 的 S13 段）。
    """
    groups = standard.scan_clears(COLUMN_BOARD, _ctx("left"))
    delta = standard.score_clear(groups, _ctx("left"), SCORE_ZERO)

    assert len(groups) == 1
    # 消掉的是 1 条线（3 格），不是 3 行。
    assert delta.linesDelta == 1
    assert delta.state.lines == 1


def test_ragged_board_uses_one_width_for_both_axes() -> None:
    """对不齐的棋盘：行路径与列路径**用同一个宽度口径**。

    宽度取各行的最小值（2 格），两条路径都按它判满——同一个"不整齐"的棋盘，
    按行判和按列判必须得出同一个答案。
    """
    ragged: list[list[int]] = [[1, 1, 1], [1, 1]]

    row_groups = standard.scan_clears(ragged, CTX_DOWN)
    column_groups = standard.scan_clears(ragged, _ctx("left"))

    assert len(row_groups) == 2
    assert len(column_groups) == 2
    # 每条线的格子清单都按 2 格宽给（不是第一行自己的 3 格）。
    assert row_groups[0].cells == ((0, 0), (1, 0))
    assert column_groups[0].cells == ((0, 0), (0, 1))


def test_score_clear_without_groups_is_noop() -> None:
    """没有消行组 → 不加分、不加行（连击与链在另一条用例里断）。"""
    delta = standard.score_clear((), CTX_DOWN, SCORE_ZERO)
    assert delta.scoreDelta == 0
    assert delta.linesDelta == 0
    assert delta.state == SCORE_ZERO


def test_tspin_classification_reaches_scoring() -> None:
    """T-Spin 分类经 `ClearGroup.classification` 到达计分。

    内核只搬运、不重新判定：分类挂在组上，`score_clear` 从组里读。
    同一个消行，挂上分类是 T-Spin 分值，不挂就是普通分值。
    """
    groups = standard.scan_clears(TSPIN_BOARD, CTX_DOWN)
    classification = standard.classify_placement(TSPIN_BOARD, TSPIN_PLACEMENT, CTX_DOWN)
    classified = tuple(
        ClearGroup(
            axis=group.axis,
            index=group.index,
            cells=group.cells,
            classification=classification,
        )
        for group in groups
    )
    delta = standard.score_clear(classified, CTX_DOWN, SCORE_ZERO)
    assert delta.scoreDelta == tables.SCORE_TABLE["tspin-single"]
    assert delta.scoreDelta != standard.score_clear(groups, CTX_DOWN, SCORE_ZERO).scoreDelta


def test_shape_cells_has_four_rotations() -> None:
    """标准 7 种方块 × 4 朝向，每格 4 个占位。"""
    for piece_type in tables.PIECE_TYPES:
        rotations = [standard.shape_cells(CTX_DOWN, piece_type, r) for r in tables.ROTATIONS]
        assert all(len(cells) == 4 for cells in rotations)
        assert all(len(set(cells)) == 4 for cells in rotations)
        if piece_type != "O":
            # O 块四个朝向相同，其余方块朝向之间必须不同。
            assert len({rotations[r] for r in tables.ROTATIONS}) == 4


def test_shape_cells_normalizes_rotation() -> None:
    """朝向按 4 取模——负朝向与越界朝向都归一。"""
    assert standard.shape_cells(CTX_DOWN, "T", 4) == standard.shape_cells(CTX_DOWN, "T", 0)
    assert standard.shape_cells(CTX_DOWN, "T", -1) == standard.shape_cells(CTX_DOWN, "T", 3)


def test_shape_cells_rejects_unknown_piece() -> None:
    """未知方块类型当场报错，不返回空格子糊弄过去。"""
    with pytest.raises(ValueError):
        standard.shape_cells(CTX_DOWN, "X", 0)


def test_shape_cells_without_axis_table() -> None:
    """M0 只提供 `down` 轴的形状表——其他轴当场报错（M4-A5 待补）。"""
    with pytest.raises(ValueError):
        standard.shape_cells(_ctx("left"), "T", 0)


def test_kick_offsets_follow_the_piece_kick_group() -> None:
    """踢墙表按**踢墙组**取：J/L/S/T/Z 共用一套 · I 独立一套 · O 不踢墙。

    M1 起踢墙表是标准表（SRS + 180° 扩展，ADR-068），表结构多了一层
    **踢墙组**——M0 的"恒等偏移最小表"已经退场。
    """
    for piece_type in tables.PIECE_TYPES:
        group = tables.KICK_GROUP_BY_PIECE[piece_type]
        for from_rotation in tables.ROTATIONS:
            for to_rotation in tables.ROTATIONS:
                offsets = standard.kick_offsets(CTX_DOWN, piece_type, from_rotation, to_rotation)
                expected = tables.KICK_TABLE["down"][(from_rotation, to_rotation)][group]
                assert offsets == expected, (
                    f"方块 {piece_type!r} 从 {from_rotation} 到 {to_rotation} 的偏移取错了组"
                )

    # **O 不踢墙**（SRS 里 O 旋转不位移）：任何转向都只有恒等偏移。
    for from_rotation in tables.ROTATIONS:
        for to_rotation in tables.ROTATIONS:
            offsets = standard.kick_offsets(CTX_DOWN, "O", from_rotation, to_rotation)
            assert offsets == ((0, 0),)


def test_kick_offsets_rejects_unknown_piece() -> None:
    """未知方块类型当场报错——不返回空偏移糊弄过去。"""
    with pytest.raises(ValueError):
        standard.kick_offsets(CTX_DOWN, "X", 0, 1)


def test_kick_offsets_without_axis_table() -> None:
    """M0 只提供 `down` 轴的踢墙表——其他轴当场报错（M4-A5 待补）。"""
    with pytest.raises(ValueError):
        standard.kick_offsets(_ctx("left"), "T", 0, 1)


# ---------------------------------------------------------------------------
# 验证 2：纯函数性
# ---------------------------------------------------------------------------


def test_rule_functions_are_pure() -> None:
    """同 `ctx` 同输入，两次结果相同。"""
    board = copy.deepcopy(TSPIN_BOARD)
    snapshot = copy.deepcopy(board)
    for _ in range(2):
        assert standard.scan_clears(board, CTX_DOWN) == standard.scan_clears(board, CTX_DOWN)
        assert standard.classify_placement(
            board, TSPIN_PLACEMENT, CTX_DOWN
        ) == standard.classify_placement(board, TSPIN_PLACEMENT, CTX_DOWN)
    groups = standard.scan_clears(board, CTX_DOWN)
    first = standard.score_clear(groups, CTX_DOWN, SCORE_ZERO)
    second = standard.score_clear(groups, CTX_DOWN, SCORE_ZERO)
    assert first == second
    assert board == snapshot, "规则集不许改棋盘"


def test_same_input_different_axis_may_differ() -> None:
    """同输入不同 `ctx`（不同 `axis`）结果可以不同——轴是规则维度。"""
    assert standard.gravity_vector(_ctx("down")) != standard.gravity_vector(_ctx("left"))
    rows = standard.scan_clears(COLUMN_BOARD, _ctx("down"))
    columns = standard.scan_clears(COLUMN_BOARD, _ctx("left"))
    assert rows == ()
    assert len(columns) == 1
    assert columns[0].axis == "column"


def test_score_clear_does_not_mutate_previous_state() -> None:
    """规则集不落笔：结算后的状态是新对象，旧状态原样不动。"""
    prev = ScoreState(score=10, lines=2, combo=1, b2b=False, level=1)
    groups = standard.scan_clears(TSPIN_BOARD, CTX_DOWN)
    delta = standard.score_clear(groups, CTX_DOWN, prev)
    assert prev == ScoreState(score=10, lines=2, combo=1, b2b=False, level=1)
    assert delta.state is not prev


def test_standard_rule_set_holds_no_mutable_state() -> None:
    """规则集不可变、无状态：模块里不许有可变容器。"""
    for name, value in vars(standard).items():
        if name.startswith("__"):
            continue
        assert not isinstance(value, (list, dict, set, bytearray)), name


def test_tables_are_read_only() -> None:
    """规则表是只读常量——规则集不该在运行期被改写。

    **内层也要只读**：外层是 `MappingProxyType`、内层是普通 `dict` 时，
    拿到表引用的代码照样能在运行期改掉形状表（`SHAPE_TABLE` 与
    `KICK_TABLE` 必须是同一口径）。
    """
    assert isinstance(tables.SHAPE_TABLE, MappingProxyType)
    assert isinstance(tables.KICK_TABLE, MappingProxyType)
    assert isinstance(tables.SCORE_TABLE, MappingProxyType)
    with pytest.raises(TypeError):
        operator.setitem(tables.SHAPE_TABLE, "down", {})

    # 形状表的内层两层：「方块 → 朝向」与「朝向 → 格子」都不可改写。
    for piece_table in tables.SHAPE_TABLE["down"].values():
        assert isinstance(piece_table, MappingProxyType)
        for cells in piece_table.values():
            assert isinstance(cells, tuple)
    with pytest.raises(TypeError):
        tables.SHAPE_TABLE["down"]["I"][0] = ((0, 0),)
    with pytest.raises(TypeError):
        tables.SHAPE_TABLE["down"]["I"] = {0: ((0, 0),)}

    # 踢墙表的内层同样只读。
    assert isinstance(tables.KICK_TABLE["down"], MappingProxyType)
    with pytest.raises(TypeError):
        tables.KICK_TABLE["down"][(0, 1)] = ((1, 1),)


def test_tables_carry_axis_dimension() -> None:
    """形状表 / 踢墙表的第一层键是重力轴——变换由规则集整体提供。"""
    for axis in ("down", "left", "right", "up"):
        assert axis in tables.SHAPE_TABLE
        assert axis in tables.KICK_TABLE


# ---------------------------------------------------------------------------
# 契约层的取值域（规则集依赖它，域外值当场被拒）
# ---------------------------------------------------------------------------


def test_contract_value_domains_are_checked_at_construction() -> None:
    """`RuleContext.axis` / `ClearGroup.axis` 域外值当场 `ValueError`。"""
    with pytest.raises(ValueError):
        RuleContext(frame=1, level=1, piecesPlaced=0, axis="sideways", stepMs=16)
    with pytest.raises(ValueError):
        ClearGroup(axis="diagonal", index=0, cells=())


def test_score_state_field_names_match_snapshot() -> None:
    """`ScoreState` 与快照的计分字段同名同义。"""
    from tetris_kernel.contracts import SNAPSHOT_FIELD_NAMES

    assert set(ScoreState.__dataclass_fields__) <= set(SNAPSHOT_FIELD_NAMES)


# ---------------------------------------------------------------------------
# 验证 5 / 6：rules/ 的依赖方向与纯净度
# ---------------------------------------------------------------------------


def _imported_module(node: ast.ImportFrom, package: str) -> str:
    """相对导入还原成绝对模块名。"""
    if node.level == 0:
        return node.module or ""
    parts = package.split(".")
    base = parts[: len(parts) - (node.level - 1)]
    prefix = ".".join(base)
    return f"{prefix}.{node.module}" if node.module else prefix


def test_rules_do_not_import_kernel() -> None:
    """`rules/` 不 import `kernel`（`rules > contracts` 方向允许）。"""
    package = "tetris_kernel.rules"
    rules_dir = Path(rules_pkg.__file__).parent
    for path in sorted(rules_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("tetris_kernel.kernel"), path.name
            elif isinstance(node, ast.ImportFrom):
                module = _imported_module(node, package)
                assert not module.startswith("tetris_kernel.kernel"), path.name


def test_rules_have_no_third_party_imports() -> None:
    """`rules/` 内只准标准库与本包（禁第三方 import）。"""
    package = "tetris_kernel.rules"
    rules_dir = Path(rules_pkg.__file__).parent
    for path in sorted(rules_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root in sys.stdlib_module_names or root == "tetris_kernel", path.name
            elif isinstance(node, ast.ImportFrom):
                root = _imported_module(node, package).split(".")[0]
                assert root in sys.stdlib_module_names or root == "tetris_kernel", path.name
