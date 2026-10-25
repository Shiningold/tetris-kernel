"""M1 标准规则表测试：形状表与踢墙表的**独立转录比对**（S7 · S10）。

## 为什么必须逐条比对

形状表与踢墙表是**离散数据**：错一条**不会**让任何"跑通"类判据变红，
只会让某个角落转不进去——**静默的玩法错误**。这类 bug 只有逐条比对能抓到
（spec §7.1 · §7.2）。

## 转录必须是**独立的第二遍**

实现表从权威表抄一遍，转录表**再独立抄一遍**——两处抄错同一处的概率低。
反过来，**转录表从实现表复制生成 = 同义反复**，比对的价值归零。本文件的
转录表**没有一行是从实现表生成的**。

## 形状表：转录用「画」，实现用「坐标」

实现表里是坐标元组，转录表里是 **ASCII 画**（SRS 权威图的样子），测试再
把画转成坐标。**两种表示不同源**——画来自 SRS 的图形规范，坐标元组来自
另一遍转录，比对才有意义。

## 踢墙表：转录用「坐标」，但独立手抄

权威的踢墙表本身就是坐标表，没有画可抄，所以转录表是**手抄的第二遍
坐标**。权威表是 **y 上正**，本项目是 **y 下正** → **y 分量取反**；
这条换算写在两边的注释里（S10 补上）。
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from tetris_kernel.contracts import (
    RULE_SIGNATURES,
    ClearGroup,
    PlacementClass,
    PlacementInfo,
    RuleContext,
    ScoreState,
)
from tetris_kernel.rules import interface, standard, tables

#: 标准玩法的上下文（重力向下）。
CTX_DOWN = RuleContext(frame=10, level=1, piecesPlaced=3, axis="down", stepMs=16)

# ---------------------------------------------------------------------------
# 形状表的独立转录表（S7）
# ---------------------------------------------------------------------------

#: SRS 权威图的样子（`X` = 方块占据的格子，`.` = 空）。
#:
#: **I 用 4×4 盒，其余用 3×3 盒，O 用 2×2 盒**（SRS 的基准坐标，FR-4.2）。
#: 朝向编号与 `tables.ROTATIONS` 一致：0 = 初始态 · 1 = 顺时针 90° ·
#: 2 = 180° · 3 = 逆时针 90°。
#:
#: **来源**：Tetris Wiki 的 `Tetromino` 条目（「Orientation」一节按
#: "Point up / Point right / Point down / Point left" 列出四种朝向的图，
#: 与本表的 0 / 1 / 2 / 3 依次对应）。
#:
#: **这是从图形规范独立转录的第二遍**——实现表那边是坐标元组，两边不同源。
#:
#: **转录本身也会写错**：这张表第一次转录时把 Z 的朝向 3 画成了
#: `.X. / .X. / XX.`（那其实是 J/L 家族的形状），比对当场报红——**这正是
#: 独立转录的价值**：两遍不同源的记录，错的那遍会被另一遍抓住。
_SHAPE_ART: dict[str, dict[int, tuple[str, ...]]] = {
    "I": {
        0: ("....", "XXXX", "....", "...."),
        1: ("..X.", "..X.", "..X.", "..X."),
        2: ("....", "....", "XXXX", "...."),
        3: (".X..", ".X..", ".X..", ".X.."),
    },
    "J": {
        0: ("X..", "XXX", "..."),
        1: (".XX", ".X.", ".X."),
        2: ("...", "XXX", "..X"),
        3: (".X.", ".X.", "XX."),
    },
    "L": {
        0: ("..X", "XXX", "..."),
        1: (".X.", ".X.", ".XX"),
        2: ("...", "XXX", "X.."),
        3: ("XX.", ".X.", ".X."),
    },
    "O": {
        0: ("XX", "XX"),
        1: ("XX", "XX"),
        2: ("XX", "XX"),
        3: ("XX", "XX"),
    },
    "S": {
        0: (".XX", "XX.", "..."),
        1: (".X.", ".XX", "..X"),
        2: ("...", ".XX", "XX."),
        3: ("X..", "XX.", ".X."),
    },
    "T": {
        0: (".X.", "XXX", "..."),
        1: (".X.", ".XX", ".X."),
        2: ("...", "XXX", ".X."),
        3: (".X.", "XX.", ".X."),
    },
    "Z": {
        0: ("XX.", ".XX", "..."),
        1: ("..X", ".XX", ".X."),
        2: ("...", "XX.", ".XX"),
        3: (".X.", "XX.", "X.."),
    },
}


def _cells_from_art(art: tuple[str, ...]) -> tuple[tuple[int, int], ...]:
    """把 ASCII 画转成占位格子坐标（行优先，**x 右正、y 下正**）。

    **顺序 = 行优先**（上到下、每行左到右）——坐标元组的顺序不是语义，
    但**逐条相等**要比到顺序，所以转录与实现都用同一套排序口径。
    """
    cells: list[tuple[int, int]] = []
    for y, row in enumerate(art):
        for x, char in enumerate(row):
            if char == "X":
                cells.append((x, y))
    return tuple(cells)


#: 形状表的独立转录表：方块类型 → 朝向 → 占位格子（从 ASCII 画转出）。
_SHAPE_TRANSCRIPTION: dict[str, dict[int, tuple[tuple[int, int], ...]]] = {
    piece: {rotation: _cells_from_art(art) for rotation, art in rotations.items()}
    for piece, rotations in _SHAPE_ART.items()
}


def _implementation_shape_table() -> dict[str, dict[int, tuple[tuple[int, int], ...]]]:
    """把实现表摊成普通 dict（方便逐条比对，也方便"改一条"做反向验证）。"""
    axis_table = tables.SHAPE_TABLE["down"]
    return {piece: dict(orientations) for piece, orientations in axis_table.items()}


# ---------------------------------------------------------------------------
# S7 验证 1：形状表逐条相等（不是抽查）
# ---------------------------------------------------------------------------


def test_shape_table_matches_independent_transcription_entry_by_entry() -> None:
    """形状表与独立转录表**逐条相等**——不是"抽查若干条"。

    **逐条**：每种方块的每个朝向都单独比，**顺序也要对**（坐标元组的
    顺序是行优先，两边同一口径）。
    """
    implementation = _implementation_shape_table()

    assert set(implementation) == set(_SHAPE_TRANSCRIPTION), "方块类型集合不一致"
    for piece in sorted(_SHAPE_TRANSCRIPTION):
        expected = _SHAPE_TRANSCRIPTION[piece]
        actual = implementation[piece]
        assert set(actual) == set(expected), f"方块 {piece!r} 的朝向集合不一致"
        for rotation in sorted(expected):
            assert actual[rotation] == expected[rotation], (
                f"方块 {piece!r} 朝向 {rotation} 的占位格子不一致："
                f"实现 {actual[rotation]} ≠ 转录 {expected[rotation]}"
            )


def test_shape_table_has_28_entries() -> None:
    """形状表覆盖 **7 种方块 × 4 朝向 = 28 条**，一条不多一条不少。"""
    implementation = _implementation_shape_table()
    assert len(implementation) == 7
    assert sum(len(orientations) for orientations in implementation.values()) == 28
    for piece, orientations in implementation.items():
        assert sorted(orientations) == [0, 1, 2, 3], f"方块 {piece!r} 的朝向不是 0..3"
        for rotation, cells in orientations.items():
            assert len(cells) == 4, f"方块 {piece!r} 朝向 {rotation} 不是 4 格"
            assert len(set(cells)) == 4, f"方块 {piece!r} 朝向 {rotation} 有重复格"


def test_shape_table_only_fills_the_down_axis() -> None:
    """**其余三个轴留空**——填了会抢走 M4-A5 的判据（表随重力方向变换）。"""
    for axis in ("left", "right", "up"):
        assert tables.SHAPE_TABLE[axis] == {}, f"{axis} 轴不该有形状表（M4-A5 的活）"
    assert set(tables.SHAPE_TABLE) == {"down", "left", "right", "up"}


def test_shape_table_is_frozen_inside_and_out() -> None:
    """表**内外都冻**——只冻外层等于没冻。

    M0 的教训：外层是 `MappingProxyType`、内层是普通 `dict` 时，任何拿到
    `SHAPE_TABLE` 引用的代码都能在运行期改掉形状表——而回放校验用的是
    **快照指纹**，改表不会让任何门禁变红。
    """
    assert isinstance(tables.SHAPE_TABLE, MappingProxyType)
    axis_table = tables.SHAPE_TABLE["down"]
    assert isinstance(axis_table, MappingProxyType)
    for piece, orientations in axis_table.items():
        assert isinstance(orientations, MappingProxyType), f"{piece!r} 的朝向层没冻"
        for rotation, cells in orientations.items():
            assert isinstance(cells, tuple), f"{piece!r} 朝向 {rotation} 不是元组"

    # 改外层 → 报错；改内层 → 也报错（两层都冻了才算数）。
    with pytest.raises(TypeError):
        tables.SHAPE_TABLE["left"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        tables.SHAPE_TABLE["down"]["T"] = {}  # type: ignore[index]


# ---------------------------------------------------------------------------
# T-Spin 五档判定（S8）
# ---------------------------------------------------------------------------

#: 棋盘画的约定：`X` = 墙 · `T` = 本次放置的 T 方块 · `.` = 空。
#:
#: **棋盘是「已放置、待消行」的视图**——T 自己的格子在棋盘上也是实心
#: （内核把方块放上棋盘后才调 `classify_placement`）。
#:
#: **画里能一眼看出为什么是这一档**：贴头的两角（紧挨突出格的那两个角）
#: 与背头的两角，被占的用 `X` 标出来。
_BOARD_ART = {
    # 完整 T-Spin（头朝上）：贴头两角**都**被占 → 头被夹住 → 完整。
    "tspin_up": (
        ".XTX",
        ".TTT",
        ".X..",
        "....",
    ),
    # T-Spin Mini（头朝上）：贴头只占**一个** + 背头占满 → 头没楔进去。
    "mini_up": (
        ".XT.",
        ".TTT",
        ".X.X",
        "....",
    ),
    # 完整 T-Spin（头朝右）：贴头两角（右侧两个）都被占。
    "tspin_right": (
        ".....",
        ".XTX.",
        "..TT.",
        "..TX.",
        ".....",
    ),
    # T-Spin Mini（头朝右）：贴头只占一个（右下）+ 背头占满。
    "mini_right": (
        ".....",
        ".XT..",
        "..TT.",
        ".XTX.",
        ".....",
    ),
    # 只占了一个角 → 连 T-Spin 都不是。
    "one_corner": (
        ".XT.",
        ".TTT",
        "....",
        "....",
    ),
    # 完整 T-Spin + 消 1 行。
    "tspin_single": (
        ".XTX",
        ".TTT",
        "XXXX",
        "....",
    ),
    # 完整 T-Spin + 消 2 行。
    "tspin_double": (
        ".XTX",
        ".TTT",
        "XXXX",
        "XXXX",
    ),
    # 完整 T-Spin + 消 3 行。
    "tspin_triple": (
        ".XTX",
        ".TTT",
        "XXXX",
        "XXXX",
        "XXXX",
    ),
    # Mini + 消 1 行。
    "mini_single": (
        ".XT.",
        ".TTT",
        "XXXX",
        "....",
    ),
}


def _board_from_art(art: tuple[str, ...]) -> list[list[int]]:
    """把棋盘画转成行优先的格子值（`X` / `T` = 1，`.` = 0）。"""
    return [[1 if char in "XT" else 0 for char in row] for row in art]


def _placement(
    cells: tuple[tuple[int, int], ...],
    *,
    rotation: int = 0,
    last_move_was_rotation: bool = True,
    kick_index: int | None = None,
    kick_offset: tuple[int, int] | None = None,
) -> PlacementInfo:
    """造一次 T 放置的上下文。"""
    return PlacementInfo(
        pieceType="T",
        rotation=rotation,
        cells=cells,
        lastMoveWasRotation=last_move_was_rotation,
        kickIndex=kick_index,
        kickOffset=kick_offset,
    )


#: 头朝上的 T：突出格在枢纽格**上方**，横排三格在枢纽格那一行。
_CELLS_UP = ((2, 0), (1, 1), (2, 1), (3, 1))

#: 头朝右的 T：竖排三格 + 突出格在枢纽格**右边**。
_CELLS_RIGHT = ((2, 1), (2, 2), (3, 2), (2, 3))


def test_tspin_grades_cover_all_five_levels() -> None:
    """**五档各有用例**：`none` / `tspin` / `mini` / `single` / `double` / `triple`。

    每张棋盘都能一眼看出为什么是这一档（贴头 / 背头两对角占没占，写在
    `_BOARD_ART` 的注释里）。
    """
    # ① 不是 T-Spin：只占了一个角。
    result = standard.classify_placement(
        _board_from_art(_BOARD_ART["one_corner"]), _placement(_CELLS_UP), CTX_DOWN
    )
    assert (result.tSpin, result.classKey) == ("none", "none")

    # ② 完整 T-Spin，不消行 → `tspin` / `tspin-none`。
    result = standard.classify_placement(
        _board_from_art(_BOARD_ART["tspin_up"]), _placement(_CELLS_UP), CTX_DOWN
    )
    assert (result.tSpin, result.classKey) == ("tspin", "tspin-none")

    # ③ T-Spin Mini，不消行 → `mini` / `tspin-mini`。
    result = standard.classify_placement(
        _board_from_art(_BOARD_ART["mini_up"]), _placement(_CELLS_UP), CTX_DOWN
    )
    assert (result.tSpin, result.classKey) == ("mini", "tspin-mini")

    # ④ 完整 T-Spin，消 1 / 2 / 3 行 → `single` / `double` / `triple`。
    for key, grade in (
        ("tspin_single", "single"),
        ("tspin_double", "double"),
        ("tspin_triple", "triple"),
    ):
        result = standard.classify_placement(
            _board_from_art(_BOARD_ART[key]), _placement(_CELLS_UP), CTX_DOWN
        )
        assert (result.tSpin, result.classKey) == (grade, f"tspin-{grade}"), key

    # ⑤ Mini 消了行：**形态仍是 `mini`**，计分类别按行数取。
    result = standard.classify_placement(
        _board_from_art(_BOARD_ART["mini_single"]), _placement(_CELLS_UP), CTX_DOWN
    )
    assert (result.tSpin, result.classKey) == ("mini", "tspin-single")


def test_tspin_mini_needs_no_kick() -> None:
    """**踢过墙的 Mini 不是 Mini**——踢墙就不是「原地转进去」。

    `kickIndex` 为 0（第一个候选 `(0, 0)`）与 `None`（压根没踢）都算没踢。
    """
    board = _board_from_art(_BOARD_ART["mini_up"])

    # 没踢墙 → Mini。
    assert standard.classify_placement(board, _placement(_CELLS_UP), CTX_DOWN).tSpin == "mini"

    # 踢了墙 → 完整 T-Spin（形态与类别键都跟着变）。
    kicked = _placement(_CELLS_UP, kick_index=1, kick_offset=(0, 1))
    result = standard.classify_placement(board, kicked, CTX_DOWN)
    assert (result.tSpin, result.classKey) == ("tspin", "tspin-none")

    # `kickIndex = 0` / 偏移 `(0, 0)` → 仍然算没踢墙。
    assert (
        standard.classify_placement(
            board, _placement(_CELLS_UP, kick_index=0, kick_offset=(0, 0)), CTX_DOWN
        ).tSpin
        == "mini"
    )


def test_tspin_mini_boundary_is_the_head_corners() -> None:
    """**边界用例**：贴头两角都占 → 完整（头被夹住）；只占一个 → Mini。

    这一条是「前两角 / 后两角」口径的机器守卫——**换个朝向也成立**
    （头朝右时前后按**列**分）。
    """
    # 头朝上：贴头两角（外框上两个）都占 → 完整。
    result = standard.classify_placement(
        _board_from_art(_BOARD_ART["tspin_up"]), _placement(_CELLS_UP), CTX_DOWN
    )
    assert result.tSpin == "tspin"

    # 头朝上：贴头只占一个 → Mini。
    result = standard.classify_placement(
        _board_from_art(_BOARD_ART["mini_up"]), _placement(_CELLS_UP), CTX_DOWN
    )
    assert result.tSpin == "mini"

    # 头朝右：贴头两角（外框右两个）都占 → 完整。
    result = standard.classify_placement(
        _board_from_art(_BOARD_ART["tspin_right"]),
        _placement(_CELLS_RIGHT, rotation=1),
        CTX_DOWN,
    )
    assert result.tSpin == "tspin"

    # 头朝右：贴头只占一个 → Mini。
    result = standard.classify_placement(
        _board_from_art(_BOARD_ART["mini_right"]),
        _placement(_CELLS_RIGHT, rotation=1),
        CTX_DOWN,
    )
    assert result.tSpin == "mini"


def test_not_a_tspin_when_only_two_corners_are_occupied() -> None:
    """**占位只有两角 → 不是 T-Spin**（门槛是「至少三个」）。"""
    board = [
        [0, 1, 0, 0],
        [0, 1, 1, 1],
        [1, 0, 0, 1],
        [0, 0, 0, 0],
    ]
    result = standard.classify_placement(board, _placement(_CELLS_UP), CTX_DOWN)
    assert (result.tSpin, result.classKey) == ("none", "none")


def test_not_a_tspin_when_the_last_move_was_not_a_rotation() -> None:
    """**最后一手不是旋转 → 不是 T-Spin**（必要条件之一）。

    平移进来的 T 摆得再像 T-Spin 也不算——**旋转**才是 T-Spin 的定义。
    """
    board = _board_from_art(_BOARD_ART["tspin_up"])
    result = standard.classify_placement(
        board, _placement(_CELLS_UP, last_move_was_rotation=False), CTX_DOWN
    )
    assert (result.tSpin, result.classKey) == ("none", "none")


def test_not_a_tspin_for_a_non_t_piece() -> None:
    """**不是 T 一律 `none`**（必要条件之一）。"""
    board = _board_from_art(_BOARD_ART["tspin_up"])
    placement = PlacementInfo(
        pieceType="L",
        rotation=0,
        cells=_CELLS_UP,
        lastMoveWasRotation=True,
        kickIndex=None,
        kickOffset=None,
    )
    result = standard.classify_placement(board, placement, CTX_DOWN)
    assert (result.tSpin, result.classKey) == ("none", "none")


def test_placed_cells_do_not_count_as_walls() -> None:
    """**本次放置的格子自己不算墙**——方块自己的格子排除在占位统计外。

    **探针做法**：把一个外框角**塞进 `placement.cells`**（真实内核不会这么
    放，但判定逻辑必须认它），该角就**不算被占**——占位数从 3 掉到 2，
    于是不再是 T-Spin。

    **顺带一条事实**：T 自己的四个格子是枢纽格加三个**正交**邻格，而外框
    四角是枢纽格的**斜向**邻格，两组不重叠——所以这条排除对真正的 T 是
    **空操作**。它是**口径**，不是修 bug。
    """
    board = _board_from_art(_BOARD_ART["mini_up"])
    assert standard.classify_placement(board, _placement(_CELLS_UP), CTX_DOWN).tSpin == "mini"

    # 把贴头的角 (1,0) 也算进"本次放置的格子" → 它不再算墙 → 只剩两角。
    probed = _placement((*_CELLS_UP, (1, 0)))
    result = standard.classify_placement(board, probed, CTX_DOWN)
    assert (result.tSpin, result.classKey) == ("none", "none")


def test_classify_placement_is_pure() -> None:
    """**纯函数性**：同 `ctx` + 同 `placement` 两次结果相同，且不改棋盘。"""
    board = _board_from_art(_BOARD_ART["tspin_up"])
    snapshot = [row[:] for row in board]
    first = standard.classify_placement(board, _placement(_CELLS_UP), CTX_DOWN)
    second = standard.classify_placement(board, _placement(_CELLS_UP), CTX_DOWN)
    assert first == second
    assert board == snapshot, "规则集不许改棋盘"


def test_rule_set_holds_no_mutable_module_state() -> None:
    """**规则集不持有可变状态**——模块级不能有 list / dict / set。

    「规则集不自己记上一手」是硬要求（架构 §7.3.1 纪律 1）：一旦规则集
    持有可变状态，FR-10.2 的存档恢复就得多序列化一份，而且"上一手是不是
    旋转"这种信息会**藏在规则集里**而不是来自 `PlacementInfo`。
    """
    for name, value in vars(standard).items():
        if name.startswith("__"):
            continue
        assert not isinstance(value, (list, dict, set)), f"模块级 {name} 是可变状态"


# ---------------------------------------------------------------------------
# 完整计分表（S9）
# ---------------------------------------------------------------------------

#: 计分表的**独立转录**——**转录源是 guideline 的表**（Tetris Wiki `T-Spin`
#: 条目 §Scoring，表头 `! Lines !! Mini T-spin !! T-spin`：
#: `0 || 100 || 400` · `1 || 200 || 800` · `2 || 400 || 1200` ·
#: `3 || [Impossible.] || 1600`），**不是 spec §7.4**。
#:
#: **为什么不能照 spec 抄**（ADR-070 的根因）：spec §7.4 把 Mini 0 行写成
#: 400（guideline 原值是 100），**实现表与这张转录表都是照 spec 抄的**——
#: 同一个错抄了两遍，**比对变同义反复**，抓不住。**转录源必须指向权威源**。
#:
#: **手抄的第二遍**，不从实现表生成（从实现表抄 = 比对变同义反复）。
#:
#: **与 guideline 不同的一处**：Mini **消了行**按完整 T-Spin 计（800 / 1200 /
#: 1600），guideline 是 200 / 400 / Impossible——M1 拍板的项目口径（ADR-069）。
_EXPECTED_BASE_SCORE: dict[str, int] = {
    "none": 0,
    "single": 100,
    "double": 300,
    "triple": 500,
    "tetris": 800,
    "tspin-none": 400,
    "tspin-mini": 100,
    "tspin-single": 800,
    "tspin-double": 1200,
    "tspin-triple": 1600,
}

#: 每个 `classKey` 对应的消行数（**期望值独立手写**，不从实现算）。
_LINES_BY_CLASS_KEY: dict[str, int] = {
    "none": 0,
    "single": 1,
    "double": 2,
    "triple": 3,
    "tetris": 4,
    "tspin-none": 0,
    "tspin-mini": 0,
    "tspin-single": 1,
    "tspin-double": 2,
    "tspin-triple": 3,
}

#: 零状态计分。
SCORE_ZERO = ScoreState(score=0, lines=0, combo=0, b2b=False, level=1)


def _groups(class_key: str, lines: int) -> tuple[ClearGroup, ...]:
    """造 `lines` 个消行组，每个组挂上同一个分类。"""
    classification = PlacementClass(tSpin="none", classKey=class_key)
    return tuple(
        ClearGroup(
            axis="row",
            index=index,
            cells=((0, index),),
            classification=classification,
        )
        for index in range(lines)
    )


def test_score_table_matches_independent_transcription() -> None:
    """计分表与**独立转录表**逐条相等（10 个键一个不多一个不少）。"""
    assert dict(tables.SCORE_TABLE) == _EXPECTED_BASE_SCORE
    assert set(tables.SCORE_TABLE) == set(_EXPECTED_BASE_SCORE)
    assert set(tables.CLASS_KEYS) == set(_EXPECTED_BASE_SCORE)


def test_base_score_per_class_key_is_hand_checked() -> None:
    """**逐项手算**：每个类别键的基础分（期望值独立转录，不调实现算）。"""
    for class_key, expected in _EXPECTED_BASE_SCORE.items():
        lines = _LINES_BY_CLASS_KEY[class_key]
        if lines == 0:
            # **消 0 行的类别键送不到计分**：内核把分类挂在**消行组**上，
            # 0 行的放置根本没有组可挂——`score_clear` 收到的是空组（落空）。
            # 表里按 guideline 原值备着（`tspin-mini` = 100 · `tspin-none` = 400），
            # 但当前投递路径够不着，缺口记在 PROGRESS.md 的待办里。
            # **同一条缺口还导致另一处偏离**：0 行的 T-Spin 走「落空」分支 →
            # `b2b = False` → **断了 B2B 链**，而 guideline 明写 0 行的 T-Spin
            # 不断链（ADR-070）。
            continue
        groups = _groups(class_key, lines)
        delta = standard.score_clear(groups, CTX_DOWN, SCORE_ZERO)
        assert delta.scoreDelta == expected, class_key
        assert delta.linesDelta == lines, class_key


def test_b2b_chain_trajectory() -> None:
    """**B2B 链**：难消 → 难消 → 普通消行，逐手断言 `b2b` 的变化轨迹。

    **入链那一跳不吃倍率**——上一手不在链里（`prev.b2b` 为 `False`），
    倍率的条件是「本次难消 **∧** 上一手也难消」。
    """
    difficult = _groups("tetris", 4)
    ordinary = _groups("single", 1)

    # 起链：进链，但不吃倍率。
    first = standard.score_clear(difficult, CTX_DOWN, SCORE_ZERO)
    assert first.state.b2b is True
    assert first.scoreDelta == 800

    # 链中再来一次难消：仍在链中，**这次吃倍率**（800 → 1200）。
    second = standard.score_clear(difficult, CTX_DOWN, first.state)
    assert second.state.b2b is True
    assert second.scoreDelta == 1200 + tables.COMBO_BONUS * 1

    # 普通消行：断链，**倍率也不生效**（普通消行不是难消）。
    third = standard.score_clear(ordinary, CTX_DOWN, second.state)
    assert third.state.b2b is False
    assert third.scoreDelta == 100 + tables.COMBO_BONUS * 2


def test_b2b_multiplier_never_breaks_the_integer_type() -> None:
    """**B2B ×3/2 不许破 `int`**（`scoreDelta` 的类型是 `int`）。

    基础分全是 100 的倍数 → 乘完仍是整数。**写成浮点 1.5 就破类型了**。
    """
    groups = _groups("tspin-double", 2)
    cold = ScoreState(score=0, lines=0, combo=0, b2b=True, level=1)
    delta = standard.score_clear(groups, CTX_DOWN, cold)

    assert isinstance(delta.scoreDelta, int)
    assert not isinstance(delta.scoreDelta, float)
    # 1200 × 3/2 = 1800。
    assert delta.scoreDelta == 1800


def test_empty_groups_break_combo_and_b2b() -> None:
    """**落空**（空组）→ `combo == 0` 且 `b2b is False`。

    落空由**内核每次锁定后都调 `score_clear`（含空组）**表达——内核不自己
    改 `combo` / `b2b`，计分口径归规则集。
    """
    prev = ScoreState(score=800, lines=4, combo=3, b2b=True, level=2)
    delta = standard.score_clear((), CTX_DOWN, prev)

    assert delta.scoreDelta == 0
    assert delta.linesDelta == 0
    assert delta.state.combo == 0
    assert delta.state.b2b is False
    # 分数、行数、等级不动。
    assert delta.state.score == 800
    assert delta.state.lines == 4
    assert delta.state.level == 2


def test_level_rises_every_ten_lines() -> None:
    """**每消 10 行升 1 级**；一次消多行也能**跨档**。

    9 行时消 4 行 → 13 行 → **2 级**（不是"本次消行数 // 10"那种写法——
    那样这次消 4 行不掉级，跨档就丢了）。
    """
    groups = _groups("tetris", 4)

    almost = ScoreState(score=0, lines=9, combo=0, b2b=False, level=1)
    crossed = standard.score_clear(groups, CTX_DOWN, almost)
    assert crossed.state.lines == 13
    assert crossed.state.level == 2

    # 还没到 10 行 → 仍是 1 级。
    below = ScoreState(score=0, lines=5, combo=0, b2b=False, level=1)
    assert standard.score_clear(groups, CTX_DOWN, below).state.level == 1

    # **等级只升不降**：上一手等级更高就保持不变（不会因为行数少而降级）。
    higher = ScoreState(score=0, lines=0, combo=0, b2b=False, level=5)
    assert standard.score_clear(groups, CTX_DOWN, higher).state.level == 5


def test_rule_set_implements_all_nine_signatures() -> None:
    """**协议一致性由红转绿**：标准规则集补齐了第 9 条（软降计分）。

    S3 定了 `soft_drop_score(ctx, distance) -> int` 的签名并提前落了最小
    实现，这里断言**九条签名一条不缺、参数名逐字一致**。
    """
    assert interface.signature_mismatches(standard.STANDARD_RULESET) == ()
    assert len(RULE_SIGNATURES) == 9
    for name in RULE_SIGNATURES:
        assert hasattr(standard, name), f"标准实现缺少接口 {name}"


def test_soft_drop_score_is_one_point_per_cell() -> None:
    """**软降计分归规则集**：每实际下移一格加 1 分（被墙挡住 = 0 分）。

    `distance` 是本次**实际下移**的格数——被墙挡住时是 0，加 0 分。
    """
    assert standard.soft_drop_score(CTX_DOWN, 0) == 0
    assert standard.soft_drop_score(CTX_DOWN, 3) == 3


# ---------------------------------------------------------------------------
# 踢墙表的独立转录表（S10）
# ---------------------------------------------------------------------------

#: 踢墙表的**独立转录**（第二遍手抄）——**本项目坐标**（x 右正、**y 下正**）。
#:
#: **来源**：Tetris Wiki 的 `Super Rotation System` 条目（`J, L, S, T, Z
#: Tetromino Wall Kick Data` 与 `I Tetromino Wall Kick Data` 两张表）+
#: Hard Drop Tetris Wiki 的 `Rotation System in TETR.IO` 条目（180° 两套）。
#:
#: **坐标换算**：权威表是 **y 上正**，本项目是 **y 下正** → **y 分量取反**。
#:
#: **转录时直接手抄成 y 下正的坐标**（不是抄 y 上正再在测试里取反）——
#: 两边都取反的话，"整个踢墙方向上下颠倒"这种**系统性**错误比对抓不到
#: （两遍都错同一边）。所以换算在**抄的时候**做，抄完两边就是两遍独立的
#: 手抄。
#:
#: **顺序也是语义**：候选按**优先级**排，第一个能落下的就用它——所以比对
#: 必须逐条比到顺序，不能只比集合。
#:
#: **同向转向**（`from == to`）不是旋转，唯一合法结果就是"不动" → 恒等偏移。
_KICK_TRANSCRIPTION: dict[tuple[int, int], dict[str, tuple[tuple[int, int], ...]]] = {
    # —— 顺时针 90°（0→1 / 1→2 / 2→3 / 3→0）——
    (0, 1): {
        "JLSTZ": ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)),
        "I": ((0, 0), (-2, 0), (1, 0), (-2, 1), (1, -2)),
        "O": ((0, 0),),
    },
    (1, 2): {
        "JLSTZ": ((0, 0), (1, 0), (1, 1), (0, -2), (1, -2)),
        "I": ((0, 0), (-1, 0), (2, 0), (-1, -2), (2, 1)),
        "O": ((0, 0),),
    },
    (2, 3): {
        "JLSTZ": ((0, 0), (1, 0), (1, -1), (0, 2), (1, 2)),
        "I": ((0, 0), (2, 0), (-1, 0), (2, -1), (-1, 2)),
        "O": ((0, 0),),
    },
    (3, 0): {
        "JLSTZ": ((0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)),
        "I": ((0, 0), (1, 0), (-2, 0), (1, 2), (-2, -1)),
        "O": ((0, 0),),
    },
    # —— 逆时针 90°（0→3 / 1→0 / 2→1 / 3→2）——
    (0, 3): {
        "JLSTZ": ((0, 0), (1, 0), (1, -1), (0, 2), (1, 2)),
        "I": ((0, 0), (-1, 0), (2, 0), (-1, -2), (2, 1)),
        "O": ((0, 0),),
    },
    (1, 0): {
        "JLSTZ": ((0, 0), (1, 0), (1, 1), (0, -2), (1, -2)),
        "I": ((0, 0), (2, 0), (-1, 0), (2, -1), (-1, 2)),
        "O": ((0, 0),),
    },
    (2, 1): {
        "JLSTZ": ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)),
        "I": ((0, 0), (1, 0), (-2, 0), (1, 2), (-2, -1)),
        "O": ((0, 0),),
    },
    (3, 2): {
        "JLSTZ": ((0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)),
        "I": ((0, 0), (-2, 0), (1, 0), (-2, 1), (1, -2)),
        "O": ((0, 0),),
    },
    # —— 180°（0→2 / 1→3 / 2→0 / 3→1）——
    # SRS 原始规范**不含 180°**：采 TETR.IO / Jstris 口径（ADR-068）。
    (0, 2): {
        "JLSTZ": ((0, 0), (0, -1)),
        "I": ((0, 0), (0, -1)),
        "O": ((0, 0),),
    },
    (1, 3): {
        "JLSTZ": ((0, 0), (1, 0)),
        "I": ((0, 0), (1, 0)),
        "O": ((0, 0),),
    },
    (2, 0): {
        "JLSTZ": ((0, 0), (0, 1)),
        "I": ((0, 0), (0, 1)),
        "O": ((0, 0),),
    },
    (3, 1): {
        # JLSTZ 与 I **只差这一条**：JLSTZ 是 (-1, 0)，I 是 (1, 0)。
        "JLSTZ": ((0, 0), (-1, 0)),
        "I": ((0, 0), (1, 0)),
        "O": ((0, 0),),
    },
    # —— 同向（不是旋转：唯一合法结果是"不动"）——
    (0, 0): {"JLSTZ": ((0, 0),), "I": ((0, 0),), "O": ((0, 0),)},
    (1, 1): {"JLSTZ": ((0, 0),), "I": ((0, 0),), "O": ((0, 0),)},
    (2, 2): {"JLSTZ": ((0, 0),), "I": ((0, 0),), "O": ((0, 0),)},
    (3, 3): {"JLSTZ": ((0, 0),), "I": ((0, 0),), "O": ((0, 0),)},
}


def _assert_kick_table_matches(
    table: dict[tuple[int, int], dict[str, tuple[tuple[int, int], ...]]],
) -> None:
    """断言给定的踢墙表与独立转录表**逐条相等**（**顺序也要对**）。

    **逐条比顺序**：候选顺序就是优先级——顺序变了，"能不能转进去"的结果
    就变了，所以**只比集合不比顺序**等于没比。
    """
    for (from_rotation, to_rotation), groups in _KICK_TRANSCRIPTION.items():
        actual = table.get((from_rotation, to_rotation))
        assert actual is not None, f"缺少从朝向 {from_rotation} 到朝向 {to_rotation} 的转向"
        for group, expected in groups.items():
            assert actual.get(group) == expected, (
                f"从朝向 {from_rotation} 到朝向 {to_rotation} 的 {group} 组偏移不一致："
                f"实现 {actual.get(group)} ≠ 转录 {expected}"
            )


def test_kick_table_matches_independent_transcription_entry_by_entry() -> None:
    """踢墙表与独立转录表**逐条相等**（16 种转向 × 3 个踢墙组）。"""
    down = {key: dict(value) for key, value in tables.KICK_TABLE["down"].items()}
    _assert_kick_table_matches(down)


def test_kick_table_covers_all_sixteen_transitions_and_three_groups() -> None:
    """**表结构断言**：16 种转向 × 3 个组，一条不缺。

    16 种转向 = 顺时针 4 种 · 180° 4 种 · 逆时针 4 种 · 同向 4 种。
    """
    down = tables.KICK_TABLE["down"]
    assert len(down) == 16
    for from_rotation in tables.ROTATIONS:
        for to_rotation in tables.ROTATIONS:
            groups = down.get((from_rotation, to_rotation))
            assert groups is not None, f"缺少 {from_rotation} → {to_rotation}"
            assert set(groups) == set(tables.KICK_GROUPS), (
                f"{from_rotation} → {to_rotation} 的组不全"
            )
            for group, offsets in groups.items():
                assert offsets, f"{from_rotation} → {to_rotation} 的 {group} 组没有候选"
                assert offsets[0] == (0, 0), "第一个候选必须是恒等偏移（不位移）"


def test_o_group_never_kicks() -> None:
    """**O 组只有 `(0, 0)`**——SRS 里 O 旋转不位移。"""
    down = tables.KICK_TABLE["down"]
    for (from_rotation, to_rotation), groups in down.items():
        assert groups["O"] == ((0, 0),), f"{from_rotation} → {to_rotation} 的 O 组不该有偏移"


def test_kick_table_is_frozen_inside_and_out() -> None:
    """踢墙表**内外都冻**——只冻外层等于没冻。"""
    assert isinstance(tables.KICK_TABLE, MappingProxyType)
    down = tables.KICK_TABLE["down"]
    assert isinstance(down, MappingProxyType)
    for (from_rotation, to_rotation), groups in down.items():
        assert isinstance(groups, MappingProxyType), f"{from_rotation} → {to_rotation} 没冻"
        for group, offsets in groups.items():
            assert isinstance(offsets, tuple), f"{group} 组的候选不是元组"

    with pytest.raises(TypeError):
        tables.KICK_TABLE["left"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        tables.KICK_TABLE["down"][(0, 1)] = {}  # type: ignore[index]


def test_the_transcription_comparison_can_actually_fail() -> None:
    """**比对本身能失败**——守卫要能触发，否则守卫等于没守。

    抄一份实现表，**改一条**（偏移数值改错）与**删一条**（转向消失），
    断言比对当场报红。
    """
    down = {key: dict(value) for key, value in tables.KICK_TABLE["down"].items()}

    # 改一条：JLSTZ 的 0→1 最后一个候选从 (-1, 2) 改成 (-1, 3)。
    broken_value = dict(down[(0, 1)])
    broken_value["JLSTZ"] = ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 3))
    broken = dict(down)
    broken[(0, 1)] = broken_value
    with pytest.raises(AssertionError):
        _assert_kick_table_matches(broken)

    # 删一条：少一个转向。
    missing = dict(down)
    del missing[(0, 1)]
    with pytest.raises(AssertionError):
        _assert_kick_table_matches(missing)

    # 原表必须仍然通过（守卫不能是"永远红"）。
    _assert_kick_table_matches(down)
