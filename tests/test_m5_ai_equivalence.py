"""M5-S1 · AI 落点搜索的**等价安全网**（差分 + 手抄锚点 + 元守卫）。

对应门判据 **`G5-1`**（spec-M5 §9.1）：**提速不改决策**。

## 三层网

| 层 | 干什么 | 挡住谁 |
| --- | --- | --- |
| **差分测试** | 随机局面（**含悬空格子**）上产品与参照**逐条**比 | 改坏产品实现 |
| **手抄锚点表** | 固定局面的落点 · 分数 · 命令序列是**字面量** | **两份一起改坏** |
| **元守卫** | 锚点必须字面量 · 参照不许 import 产品 · 源码不许漂 | **自证被打破** |

## 为什么锚点要"手抄"

差分测试防不住「**产品实现与参照实现在同一笔提交里一起改**」——
那两边还是相等，差分照样绿。所以另有一张**与实现无关**的锚点表：
它的值是**源码里的字面量**，**任何一方被改坏，它都红**。

**这些字面量的来路**：M5-S1 冻结时，从**冻结参照实现**（M4 那版搜索）
在固定局面上的结果**逐条抄下来**（命令序列 · 分数 · 落点格子），
**抄完就钉死**——之后**不许再从任何实现重新生成**。

## 元守卫的具体口径

1. `ANCHORS` 必须是**字面量表达式**（AST 里只有常量与元组，**不许有调用**）；
2. 参照实现**不许 import 产品实现**（否则差分是自己比自己）；
3. 参照实现的**源码不许漂**（SHA-256 钉住 —— 要改就必须**显式**改这一条守卫）。
"""

from __future__ import annotations

import ast
import hashlib
import random
from pathlib import Path

from support.ai_reference import plan_placement as reference_plan
from tetris_kernel.rules.tables import PIECE_TYPES
from tetris_kernel.systems.ai.search import plan_placement as product_plan

#: 本文件自身的路径（**元守卫要读自己的源码**）。
THIS_FILE = Path(__file__).resolve()

#: 参照实现的路径（**元守卫要读它的源码**）。
REFERENCE_FILE = Path(__file__).resolve().parent / "support" / "ai_reference.py"

#: 随机局面的种子（**固定** —— 差分的输入必须每次都一样）。
RANDOM_SEED = 20261028

#: 随机局面的条数（**与方块类型、出生列交叉** —— 覆盖面够，又不至于慢）。
RANDOM_BOARD_COUNT = 40

#: 棋盘宽高（**标准玩法**）。
BOARD_WIDTH = 10
BOARD_HEIGHT = 20

#: 差分时试的出生列（**盒子原点的横坐标**）。
SPAWN_X_COLUMNS = (0, 3, 4, 9)

#: 差分时试的出生行（**-1 = 出生区在棋盘之上那一行**）。
SPAWN_Y_ROWS = (-1, 0)

#: 参照实现的**源码指纹**（**元守卫 3 · 参照实现不许漂**）。
#:
#: 口径：**去掉行尾差异**再算 SHA-256（换行风格不是漂移）。
REFERENCE_SOURCE_SHA256 = "29e3adba6cbb2718f7cb3ca8760fc36bbc294b2edb2d44ec8cf6c5e6fe3a5f53"

# ---------------------------------------------------------------------------
# 手抄锚点表（**源码字面量** —— 不许从任何实现重新生成）
# ---------------------------------------------------------------------------
#
# 每条：`(名字, 棋盘行, 方块类型, 出生列, 出生行, 命令序列, 分数, 落点格子)`。
#
# 棋盘行是 20 行 × 10 列的**字符串**（`.` = 空 · `#` = 有格），
# 从**顶部**往下数（第 0 行是顶部）。

ANCHORS: tuple[
    tuple[str, tuple[str, ...], str, int, int, tuple[str, ...], int, tuple[tuple[int, int], ...]],
    ...,
] = (
    (
        "空盘 · I 块",
        (
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
        ),
        "I",
        3,
        0,
        ("moveLeft", "moveLeft", "moveLeft", "hardDrop"),
        -145,
        ((0, 19), (1, 19), (2, 19), (3, 19)),
    ),
    (
        "空盘 · T 块",
        (
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
        ),
        "T",
        4,
        0,
        ("moveLeft", "moveLeft", "moveLeft", "moveLeft", "hardDrop"),
        -190,
        ((1, 18), (0, 19), (1, 19), (2, 19)),
    ),
    (
        "底行满 · I 块",
        (
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "##########",
        ),
        "I",
        3,
        0,
        ("moveLeft", "moveLeft", "moveLeft", "hardDrop"),
        550,
        ((0, 18), (1, 18), (2, 18), (3, 18)),
    ),
    (
        "底行满 + 半空行 · O 块",
        (
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "##........",
            "##########",
        ),
        "O",
        3,
        0,
        ("moveLeft", "hardDrop"),
        445,
        ((2, 17), (3, 17), (2, 18), (3, 18)),
    ),
    (
        "悬空一格 · L 块",
        (
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "....#.....",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
        ),
        "L",
        3,
        0,
        ("moveLeft", "moveLeft", "hardDrop"),
        -12445,
        ((3, 18), (1, 19), (2, 19), (3, 19)),
    ),
    (
        "阶梯 · S 块",
        (
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "###.......",
            "....####..",
            "..........",
        ),
        "S",
        4,
        0,
        ("moveRight", "moveRight", "moveRight", "rotateCW", "hardDrop"),
        -9615,
        ((8, 17), (8, 18), (9, 18), (9, 19)),
    ),
    (
        "空盘 · J 块 · 出生行 -1",
        (
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
        ),
        "J",
        3,
        -1,
        ("moveLeft", "moveLeft", "moveLeft", "hardDrop"),
        -170,
        ((0, 18), (0, 19), (1, 19), (2, 19)),
    ),
    (
        "双层洞 · Z 块",
        (
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "..........",
            "#.........",
            "#########.",
            ".........#",
        ),
        "Z",
        3,
        0,
        ("moveLeft", "moveLeft", "moveLeft", "hardDrop"),
        -8000,
        ((0, 16), (1, 16), (1, 17), (2, 17)),
    ),
)


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------


def _rows_to_board(rows: tuple[str, ...]) -> tuple[tuple[int, ...], ...]:
    """把「每行一个字符串」转成棋盘（**`.` = 空 · `#` = 有格**）。"""
    return tuple(tuple(0 if char == "." else 1 for char in row) for row in rows)


def _random_boards() -> list[tuple[tuple[int, ...], ...]]:
    """造一批**随机局面**（**含悬空格子**）。

    **为什么要悬空格子**：空洞是 AI 打分里权重最狠的一项（`WEIGHT_HOLES`），
    而"洞"的口径最容易在提速时算错（洞数只算一列 / 相邻对被算两遍 /
    越界格没排除）——**随机局面的密度分布要能造出这些形状**。

    **密度随行递增**（底部密 · 顶部稀）——真实对局的形状分布。
    """
    rng = random.Random(RANDOM_SEED)
    boards: list[tuple[tuple[int, ...], ...]] = []
    for _ in range(RANDOM_BOARD_COUNT):
        rows: list[tuple[int, ...]] = []
        for row_index in range(BOARD_HEIGHT):
            density = 0.05 + 0.55 * (row_index / (BOARD_HEIGHT - 1))
            rows.append(tuple(1 if rng.random() < density else 0 for _ in range(BOARD_WIDTH)))
        boards.append(tuple(rows))
    return boards


def _has_floating_cell(board: tuple[tuple[int, ...], ...]) -> bool:
    """这个局面里**有没有悬空的格子**（下面那一格是空的）。"""
    for row_index, row in enumerate(board):
        for column_index, value in enumerate(row):
            if value == 0:
                continue
            if row_index + 1 >= len(board) or board[row_index + 1][column_index] == 0:
                return True
    return False


def _outcome(placement: object) -> tuple[object, ...]:
    """把一次决策的结果压成**可比对的元组**（**没落点也是一种结果**）。"""
    if placement is None:
        return (None, None, None)
    return (placement.commands, placement.score, placement.cells)


# ---------------------------------------------------------------------------
# 差分测试（**G5-1 的主体**）
# ---------------------------------------------------------------------------


def test_diff_random_boards_matches_reference() -> None:
    """随机局面上，**产品实现与参照实现逐条相同**。

    **逐条比三样**：命令序列 · 分数 · 落点格子。
    少比一样就等于放走一种"偷偷改了决策"——**三样都要比**。
    """
    boards = _random_boards()
    compared = 0
    for board in boards:
        for piece_type in PIECE_TYPES:
            for spawn_x in SPAWN_X_COLUMNS:
                for spawn_y in SPAWN_Y_ROWS:
                    product = _outcome(product_plan(board, piece_type, spawn_x, spawn_y, 0))
                    reference = _outcome(reference_plan(board, piece_type, spawn_x, spawn_y, 0))
                    assert product == reference, (
                        f"决策不一致：局面={board} 方块={piece_type} "
                        f"出生=({spawn_x}, {spawn_y}) 产品={product} 参照={reference}"
                    )
                    compared += 1
    assert compared > 0


def test_random_boards_contain_floating_cells() -> None:
    """随机局面**确实含悬空格子**（**否则差分覆盖不到洞的口径**）。"""
    boards = _random_boards()
    assert any(_has_floating_cell(board) for board in boards)


def test_diff_empty_board_and_dense_board() -> None:
    """**空盘**与**满盘**两个极端也要逐条相同（**两端最容易走偏**）。"""
    empty = tuple(tuple(0 for _ in range(BOARD_WIDTH)) for _ in range(BOARD_HEIGHT))
    dense = tuple(tuple(1 for _ in range(BOARD_WIDTH)) for _ in range(BOARD_HEIGHT))
    for board in (empty, dense):
        for piece_type in PIECE_TYPES:
            for spawn_x in SPAWN_X_COLUMNS:
                for spawn_y in SPAWN_Y_ROWS:
                    product = _outcome(product_plan(board, piece_type, spawn_x, spawn_y, 0))
                    reference = _outcome(reference_plan(board, piece_type, spawn_x, spawn_y, 0))
                    assert product == reference, (
                        f"决策不一致：局面={board} 方块={piece_type} "
                        f"出生=({spawn_x}, {spawn_y}) 产品={product} 参照={reference}"
                    )


# ---------------------------------------------------------------------------
# 手抄锚点（**G5-1 的另一半** —— 挡住"两份一起改坏"）
# ---------------------------------------------------------------------------


def test_anchor_matches_product_implementation() -> None:
    """产品实现的落点 · 分数 · 命令序列 **逐条等于锚点**。"""
    for name, rows, piece_type, spawn_x, spawn_y, commands, score, cells in ANCHORS:
        board = _rows_to_board(rows)
        placement = product_plan(board, piece_type, spawn_x, spawn_y, 0)
        assert placement is not None, f"锚点「{name}」没有落点"
        assert placement.commands == commands, f"锚点「{name}」的命令序列变了"
        assert placement.score == score, f"锚点「{name}」的分数变了"
        assert placement.cells == cells, f"锚点「{name}」的落点变了"


def test_anchor_matches_reference_implementation() -> None:
    """参照实现也要**逐条等于锚点**（**锚点钉住的是两份，不是一份**）。"""
    for name, rows, piece_type, spawn_x, spawn_y, commands, score, cells in ANCHORS:
        board = _rows_to_board(rows)
        placement = reference_plan(board, piece_type, spawn_x, spawn_y, 0)
        assert placement is not None, f"锚点「{name}」在参照实现里没有落点"
        assert placement.commands == commands, f"锚点「{name}」在参照实现里的命令序列变了"
        assert placement.score == score, f"锚点「{name}」在参照实现里的分数变了"
        assert placement.cells == cells, f"锚点「{name}」在参照实现里的落点变了"


# ---------------------------------------------------------------------------
# 元守卫（**守住"网"本身**）
# ---------------------------------------------------------------------------


def _assignment_expression(file_path: Path, name: str) -> ast.expr:
    """在源码里找到 `name = ...` 的**右值表达式**（**找不到就是没找到**）。

    **带注解的赋值**（`name: T = ...`）也算 —— 锚点表就是这么写的。
    """
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == name:
                assert node.value is not None, f"`{name}` 没有右值"
                return node.value
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
    raise AssertionError(f"源码里找不到赋值 `{name}`：{file_path}")


def test_anchor_table_is_source_literal() -> None:
    """锚点表**必须是源码字面量**（**不许从实现算出来**）。"""
    expression = _assignment_expression(THIS_FILE, "ANCHORS")
    for node in ast.walk(expression):
        assert not isinstance(node, ast.Call), "锚点表里不许有调用 —— 必须是源码字面量"
        assert not isinstance(node, ast.BinOp), "锚点表里不许有运算 —— 必须是源码字面量"
        assert not isinstance(node, ast.Compare), "锚点表里不许有比较 —— 必须是源码字面量"


def test_reference_does_not_import_product_search() -> None:
    """参照实现**不许 import 产品实现**（**否则差分是自己比自己**）。"""
    tree = ast.parse(REFERENCE_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "tetris_kernel.systems" not in alias.name, (
                    f"参照实现不许 import 产品模块：{alias.name}"
                )
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("tetris_kernel.systems"), (
                f"参照实现不许 import 产品模块：{module}"
            )


def test_reference_source_is_frozen() -> None:
    """参照实现的源码**不许漂**（**指纹钉住**）。

    **为什么要钉**：参照实现"顺手改一下"会**悄悄换掉差分的基准**——
    差分还是绿的，但比对的是两个都不是 M4 的东西。
    """
    source = REFERENCE_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert digest == REFERENCE_SOURCE_SHA256, (
        f"参照实现源码变了：指纹 {digest} ≠ 钉住的 {REFERENCE_SOURCE_SHA256}"
    )
