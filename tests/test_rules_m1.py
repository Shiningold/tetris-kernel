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

from tetris_kernel.rules import tables

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
