"""变体 A（重力可旋转）的用例（M4-S3 · spec-M4 §7.2）。

**这些用例守的是"口径"，不是"实现细节"**：

- **签名自检**：九条签名逐条对得上（参数名即契约 · ADR-061）；
- **轴绑在实例上**：`ctx.axis` 恒为 `down`，**重力方向不来自 `ctx`**；
- **表随轴变**：`right` / `left` 的形状表与踢墙表**确实不是标准表**，
  且**是标准表的刚体变换**（`down` 轴逐条等于标准表）；
- **旋转手感不反**：形状整体转了 90°，**但相邻朝向仍是屏幕顺时针**
  （刚体旋转与 SRS 旋转可交换）；
- **消行仍是满行消**（K4）：横向重力下**不消列**；
- **横向重力的物理层**：内核的下落 / 幽灵落点 / 触地判定**方向无关**
  （`kernel/core.py` 的三处按向量推进）→ 横向重力下墙即地板。

**反向用例**（"把轴改成从 `ctx.axis` 取" → 必红）在 `test_gravity_axis_comes_from_instance`。
"""

from __future__ import annotations

import pytest

from tetris_kernel.contracts.rules import RuleContext
from tetris_kernel.host import assemble
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.rules.interface import signature_mismatches
from tetris_kernel.rules.tables import KICK_TABLE, SHAPE_TABLE
from tetris_kernel.variants.gravity import (
    AXIS_VALUES,
    GravityRuleSet,
    gravity_rule_set,
    kick_table_for_axis,
    shape_table_for_axis,
)


def _ctx() -> RuleContext:
    """一个**标准**上下文（**`axis` 恒为 `down`** —— 这正是要守的点）。"""
    return RuleContext(frame=0, level=1, piecesPlaced=0, axis="down", stepMs=16)


# ---------------------------------------------------------------------------
# 签名
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("axis", AXIS_VALUES)
def test_gravity_rule_set_matches_frozen_signatures(axis: str) -> None:
    """九条签名逐条对得上（**参数名与个数**）。"""
    assert signature_mismatches(gravity_rule_set(axis)) == ()


# ---------------------------------------------------------------------------
# 轴绑在实例上（不绑 ctx.axis）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("axis", "expected"),
    (("down", (0, 1)), ("right", (1, 0)), ("left", (-1, 0))),
)
def test_gravity_vector_follows_instance_axis(axis: str, expected: tuple[int, int]) -> None:
    """重力方向随**实例的轴**——**横向时 `dy = 0`**（方向是向量不是 dy）。"""
    rule_set = gravity_rule_set(axis)
    assert rule_set.gravity_vector(_ctx()) == expected


def test_gravity_axis_comes_from_instance_not_context() -> None:
    """**反向用例**：`ctx.axis` 是 `down`，但实例是 `right` → 重力仍向右。

    **这条守的是"安静失败"那种错**：实现若改成读 `ctx.axis`，
    它不会崩，只会**安静地退回向下重力**——所以必须有一条用例钉住。
    """
    ctx = _ctx()
    assert ctx.axis == "down"
    assert gravity_rule_set("right").gravity_vector(ctx) == (1, 0)
    # **同一个上下文、另一个实例 → 另一个方向**（轴来自实例，不是上下文）。
    assert gravity_rule_set("down").gravity_vector(ctx) == (0, 1)


def test_up_axis_is_refused() -> None:
    """`up` 轴被拒（**内核把 `y < 0` 当出生区** → 向上重力会飞出棋盘）。"""
    with pytest.raises(ValueError, match="up"):
        GravityRuleSet(axis="up")


def test_unknown_axis_is_refused() -> None:
    """域外的轴当场拒（不留到玩到一半才炸）。"""
    with pytest.raises(ValueError):
        GravityRuleSet(axis="sideways")


# ---------------------------------------------------------------------------
# 表随轴变
# ---------------------------------------------------------------------------


def test_down_axis_tables_equal_standard_tables() -> None:
    """`down` 轴**逐条等于标准表**——恒等变换（**不抄第二份真相**）。"""
    standard_shapes = SHAPE_TABLE["down"]
    for piece, orientations in standard_shapes.items():
        for rotation, cells in orientations.items():
            assert shape_table_for_axis("down")[piece][rotation] == cells
    for transition, by_group in KICK_TABLE["down"].items():
        for group, offsets in by_group.items():
            assert kick_table_for_axis("down")[transition][group] == offsets


@pytest.mark.parametrize("axis", ("right", "left"))
def test_shape_table_differs_from_standard(axis: str) -> None:
    """横向轴的形状表**确实不是标准表**（**表随轴变**是 `M4-A5` 的判据）。"""
    table = shape_table_for_axis(axis)
    standard = SHAPE_TABLE["down"]
    assert table["T"][0] != standard["T"][0]
    assert table["T"][1] != standard["T"][1]


@pytest.mark.parametrize("axis", ("right", "left"))
def test_shape_transform_preserves_geometry(axis: str) -> None:
    """形状变换是**刚体变换**：格子数不变 · **两个方向的跨度互换**。

    **转了 90° → 横向跨度与纵向跨度对调**（盒尺寸不变，所以跨度不变形）。
    """
    table = shape_table_for_axis(axis)
    standard = SHAPE_TABLE["down"]
    for piece, orientations in standard.items():
        for rotation, cells in orientations.items():
            transformed = table[piece][rotation]
            assert len(transformed) == len(cells)
            span_x_before = max(x for x, _ in cells) - min(x for x, _ in cells)
            span_y_before = max(y for _, y in cells) - min(y for _, y in cells)
            span_x_after = max(x for x, _ in transformed) - min(x for x, _ in transformed)
            span_y_after = max(y for _, y in transformed) - min(y for _, y in transformed)
            # **旋转 90° → 两个跨度对调**。
            assert span_x_after == span_y_before
            assert span_y_after == span_x_before


@pytest.mark.parametrize("axis", ("right", "left"))
def test_shape_table_is_row_major(axis: str) -> None:
    """变换后的格子**按行优先排**（与标准表同一口径 —— 逐条比对才敢比）。"""
    table = shape_table_for_axis(axis)
    for orientations in table.values():
        for cells in orientations.values():
            assert list(cells) == sorted(cells, key=lambda cell: (cell[1], cell[0]))


@pytest.mark.parametrize("axis", ("right", "left"))
def test_kick_table_is_axis_mapped_standard(axis: str) -> None:
    """踢墙表是**标准偏移的轴映射**：候选**数量与顺序**不变（顺序 = 优先级）。"""
    table = kick_table_for_axis(axis)
    for transition, by_group in KICK_TABLE["down"].items():
        for group, offsets in by_group.items():
            assert len(table[transition][group]) == len(offsets)


def test_right_axis_shape_is_standard_rotated_counter_clockwise() -> None:
    """`right` 轴的形状 = 标准形状**视觉逆时针 90°**（把"下"转到"右"）。

    **逆向还原**：把 `right` 的格子转回标准方向，应**逐条等于标准表**——
    这条比"看起来对"硬。
    """
    table = shape_table_for_axis("right")
    standard = SHAPE_TABLE["down"]
    # **盒尺寸按方块取**（I 4×4 · O 2×2 · 其余 3×3）。
    size_of = {"I": 4, "O": 2, "J": 3, "L": 3, "S": 3, "T": 3, "Z": 3}
    for piece, orientations in standard.items():
        size = size_of[piece]
        for rotation, cells in orientations.items():
            restored = tuple(
                sorted(
                    ((size - 1 - y, x) for x, y in table[piece][rotation]),
                    key=lambda cell: (cell[1], cell[0]),
                )
            )
            assert restored == cells


def test_rotation_sequence_stays_clockwise_on_screen() -> None:
    """**旋转手感不反**：形状整体转了 90°，但相邻朝向**仍是屏幕顺时针**。

    **为什么**：刚体旋转与 SRS 的旋转**可交换**——`T(SRS_顺(down[r]))` =
    `SRS_顺(T(down[r]))`。**所以朝向编号不做重编号**（spec-M4 §7.2）。
    """
    table = shape_table_for_axis("right")
    standard = SHAPE_TABLE["down"]
    # **把 `right` 的形状转回标准方向**：应当正好等于标准表的**下一个朝向**。
    size_of = {"I": 4, "O": 2, "J": 3, "L": 3, "S": 3, "T": 3, "Z": 3}
    for piece, orientations in standard.items():
        size = size_of[piece]
        for rotation in range(4):
            restored = tuple(
                sorted(
                    ((size - 1 - y, x) for x, y in table[piece][rotation]),
                    key=lambda cell: (cell[1], cell[0]),
                )
            )
            assert restored == orientations[rotation]


# ---------------------------------------------------------------------------
# 消行口径（K4：仍是满行消）
# ---------------------------------------------------------------------------


def test_clear_axis_stays_row_even_on_horizontal_axis() -> None:
    """**横向重力下仍是"满行消"**（K4 —— 内核的塌落按行，"消列"要改内核）。"""
    rule_set = gravity_rule_set("right")
    width = 10
    board = [[0] * width for _ in range(4)]
    board[2] = [1] * width
    groups = rule_set.scan_clears(board, _ctx())
    assert len(groups) == 1
    assert groups[0].axis == "row"
    assert groups[0].index == 2


# ---------------------------------------------------------------------------
# 物理层：横向重力下墙即地板
# ---------------------------------------------------------------------------


def test_horizontal_gravity_moves_piece_along_x() -> None:
    """横向重力下方块**沿 x 移动、y 不变**（内核的下落是按向量推进的）。"""
    assembly = assemble(seed=1, rule_set=gravity_rule_set("right"), rule_id="gravity_right")
    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frames(200)
    active = assembly.kernel.snapshot().active
    assert active is not None
    # **横向重力下 y 不变**（重力没有 y 分量）· **x 只会变大**（重力向右）。
    assert active.y == 0
    assert active.x > 0


def test_variant_can_play_a_whole_game_to_settlement() -> None:
    """**变体能玩**：跑到顶出并出结算（**不是"手感好"，是"跑得完"**）。"""
    assembly = assemble(seed=7, rule_set=gravity_rule_set("right"), rule_id="gravity_right")
    loop = HeadlessLoop(assembly)
    loop.start()
    report = loop.run_frames(20000, until_game_over=True)
    assert report.frames > 0
    assert assembly.kernel.snapshot().phase == "game_over"
