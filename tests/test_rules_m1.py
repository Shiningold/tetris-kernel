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

from tetris_kernel.contracts import PlacementInfo, RuleContext
from tetris_kernel.rules import standard, tables

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
