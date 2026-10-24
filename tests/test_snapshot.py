"""S8 快照与只读视图测试。

覆盖 plan-M0.md S8 的五条验证：

1. 取快照 → 改快照的内层 → 内核棋盘**不变**（深拷贝生效，ADR-058）；
2. 快照字段集合与 `contracts/snapshot.py` 声明**完全相等**（不多不少）；
3. `rngState` 在 `snapshot()` 路径恒为 `None`，走存档路径（`dataclasses.replace`）
   后为非 `None`；
4. `structureFingerprint` **跨进程稳定**（子进程 + 两个不同的 `PYTHONHASHSEED`）；
5. `extensions` 的 key **唯一性**校验生效（重复 key → 报错）。

外加几条护栏：视图构造**纯读**、快照**不可变**、内核模块**不碰随机源**。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import operator
import os
import subprocess
import sys
from pathlib import Path

import pytest

# A1 的 `kernel/fingerprint.py` 是**跨写手依赖**（签名先验定，见
# plan-M0.md §1.4.4）：它没落笔时本文件整体跳过——那不是本阶段的红，
# 由集成者在 A1 落笔后复跑。
pytest.importorskip(
    "tetris_kernel.kernel.fingerprint",
    reason="A1 的 kernel/fingerprint.py 尚未落笔（跨写手依赖，见 plan-M0.md §1.4.4）",
)

import tetris_kernel  # noqa: E402
from tetris_kernel.contracts import (  # noqa: E402
    ACTIVE_FIELDS,
    BOARD_FIELDS,
    KERNEL_STATE_TO_SNAPSHOT,
    SNAPSHOT_FIELD_NAMES,
    SNAPSHOT_FIELDS,
)
from tetris_kernel.kernel import snapshot as snapshot_module  # noqa: E402
from tetris_kernel.kernel.snapshot import (  # noqa: E402
    Active,
    Board,
    Snapshot,
    build_board,
    build_extensions,
    build_snapshot,
    compute_extension_fingerprint,
    freeze_cells,
    with_rng_state,
)

# ---------------------------------------------------------------------------
# 样例数据
# ---------------------------------------------------------------------------

#: 样例棋盘（行优先，第 0 行是顶部）：4 列 3 行，顶部 1 行隐藏。
SAMPLE_CELLS: list[list[int]] = [
    [0, 1, 0, 0],
    [2, 0, 0, 3],
    [0, 0, 0, 0],
]


def _sample_snapshot() -> Snapshot:
    """一份样例快照（字段值固定，便于跨进程比对）。"""
    return build_snapshot(
        version=7,
        rule="standard",
        phase="running",
        frame=42,
        board=build_board(4, 3, 1, SAMPLE_CELLS),
        active=Active(pieceType="T", rotation=1, x=3, y=5),
        ghost_delta=(0, 4),
        hold="I",
        hold_used=False,
        next_queue=("O", "S", "Z", "L", "J"),
        score=1200,
        lines=7,
        level=3,
        combo=2,
        b2b=True,
        pieces_placed=31,
        grounded=False,
        lock_timer=5,
        gravity_timer=12,
        extensions=(("mode", "marathon"), ("seedTag", 7)),
    )


#: 子进程里算同一棋盘的结构指纹（跨进程稳定性用）。
_CHILD_CODE = """
from tetris_kernel.kernel.snapshot import build_board, compute_structure_fingerprint

cells = [[0, 1, 0, 0], [2, 0, 0, 3], [0, 0, 0, 0]]
board = build_board(4, 3, 1, cells)
print(compute_structure_fingerprint(board.cells))
"""


def _child_fingerprint(hash_seed: str) -> str:
    """起一个子进程算同一棋盘的结构指纹，返回其输出。

    **故意给子进程不同的 `PYTHONHASHSEED`**——Python 内置 `hash()` 受它影响，
    摘要一旦用上 `hash()`，两个子进程就会给出不同的值（架构 §5.4）。
    """
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    src_root = str(Path(tetris_kernel.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = src_root + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_CODE],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return completed.stdout.strip()


def _snapshot_with_extensions(
    extensions: tuple[tuple[str, int | float | str | bool | None], ...],
) -> Snapshot:
    """带指定扩展槽的快照（其余字段固定，便于逐条比对）。"""
    return build_snapshot(
        version=1,
        rule="standard",
        phase="running",
        frame=0,
        board=build_board(2, 2, 0, [[0, 0], [0, 0]]),
        active=None,
        ghost_delta=(0, 0),
        hold=None,
        hold_used=False,
        next_queue=(),
        score=0,
        lines=0,
        level=1,
        combo=0,
        b2b=False,
        pieces_placed=0,
        grounded=False,
        lock_timer=0,
        gravity_timer=0,
        extensions=extensions,
    )


# ---------------------------------------------------------------------------
# 验证 2：字段集合与契约声明完全相等
# ---------------------------------------------------------------------------


def test_snapshot_fields_equal_contract() -> None:
    """`Snapshot` 的字段名、顺序、类型与契约声明**逐条相等**。

    **不多不少**：多出来的字段是"改契约"（major 变更），少掉的字段是"存档
    恢复缺一块"（架构 §5.2）——两种都要当场变红。
    """
    assert tuple(f.name for f in dataclasses.fields(Snapshot)) == SNAPSHOT_FIELD_NAMES
    assert {f.name: f.type for f in dataclasses.fields(Snapshot)} == {
        name: spec.type for name, spec in SNAPSHOT_FIELDS.items()
    }


def test_board_fields_equal_contract() -> None:
    """`Board` 的 5 个子字段与契约声明逐条相等。"""
    assert tuple(f.name for f in dataclasses.fields(Board)) == tuple(BOARD_FIELDS)
    assert {f.name: f.type for f in dataclasses.fields(Board)} == {
        name: spec.type for name, spec in BOARD_FIELDS.items()
    }


def test_active_fields_equal_contract() -> None:
    """`Active` 的 4 个子字段与契约声明逐条相等。"""
    assert tuple(f.name for f in dataclasses.fields(Active)) == tuple(ACTIVE_FIELDS)
    assert {f.name: f.type for f in dataclasses.fields(Active)} == {
        name: spec.type for name, spec in ACTIVE_FIELDS.items()
    }


def test_kernel_state_paths_resolve_to_snapshot_fields() -> None:
    """契约的**内核状态清单**里每条快照路径都真实存在（机器可读 → 可核对）。

    清单是"内核加字段 → 快照必须同步加字段"这条纪律的机器可读版：路径指向的
    字段不存在，就是快照漏了一块。
    """
    board_names = {f.name for f in dataclasses.fields(Board)}
    active_names = {f.name for f in dataclasses.fields(Active)}
    snapshot_names = {f.name for f in dataclasses.fields(Snapshot)}

    for entry in KERNEL_STATE_TO_SNAPSHOT:
        head, _, tail = entry.snapshot_path.partition(".")
        if head == "board":
            names = board_names
        elif head == "active":
            names = active_names
        else:
            names = snapshot_names
        if tail:
            assert tail in names, entry.snapshot_path
        else:
            assert head in names, entry.snapshot_path


# ---------------------------------------------------------------------------
# 验证 1：深拷贝（ADR-058）
# ---------------------------------------------------------------------------


def test_snapshot_cells_are_deep_copied() -> None:
    """验证 1：快照与内核棋盘**不共享内层**，改哪边都不影响另一边。

    **只冻结外层不够**（ADR-058）：`tuple(kernel_cells)` 那种写法留下的仍是
    共享的可变内层，渲染器一改就破"只有内核能写状态"。
    """
    kernel_cells: list[list[int]] = [[0, 1], [2, 0]]
    board = build_board(2, 2, 0, kernel_cells)

    # 内层必须是 tuple（不是 list）——这正是"深拷贝"的可观察证据
    assert isinstance(board.cells, tuple)
    assert all(isinstance(row, tuple) for row in board.cells)
    assert board.cells[0] == (0, 1)

    # 改内核的棋盘 → 快照不变
    kernel_cells[0][0] = 9
    assert board.cells[0] == (0, 1)

    # 改快照的内层 → 报错（不可变），内核棋盘不受影响
    with pytest.raises(TypeError):
        operator.setitem(board.cells[0], 0, 9)
    assert kernel_cells[0][0] == 9


def test_freeze_cells_copies_every_row() -> None:
    """`freeze_cells` 逐行拷贝，改原行不影响冻结结果。"""
    source: list[list[int]] = [[1, 2], [3, 4]]
    frozen = freeze_cells(source)
    source[0][1] = 99
    assert frozen == ((1, 2), (3, 4))


def test_build_board_does_not_mutate_source() -> None:
    """构造棋盘**不改**内核给过来的可变结构。"""
    source: list[list[int]] = [[1, 0], [0, 2]]
    build_board(2, 2, 0, source)
    assert source == [[1, 0], [0, 2]]


# ---------------------------------------------------------------------------
# 验证 3：rngState 的两条构造路径
# ---------------------------------------------------------------------------


def test_rng_state_is_none_on_snapshot_path() -> None:
    """验证 3：`snapshot()` 路径的 `rngState` **恒为 `None`**。"""
    assert _sample_snapshot().rngState is None


def test_snapshot_path_cannot_carry_rng_state() -> None:
    """`build_snapshot` 的签名里**没有** `rngState`——从签名上杜绝"顺手带上"。

    只断言 `rngState is None` 是不够的：加个带默认值的参数就能绕过去。
    """
    assert "rngState" not in inspect.signature(build_snapshot).parameters


def test_archive_path_fills_rng_state() -> None:
    """验证 3：存档路径（`dataclasses.replace`）后 `rngState` 为非 `None`。"""
    snapshot = _sample_snapshot()
    state = (3, 1, 4, 1, 5)

    archived = with_rng_state(snapshot, state)
    assert archived.rngState == state

    # 裸的 `dataclasses.replace` 路径同样成立（契约原文写的就是它）
    assert dataclasses.replace(snapshot, rngState=state).rngState == state

    # 原快照不受影响（frozen 语义：replace 造的是新对象）
    assert snapshot.rngState is None


# ---------------------------------------------------------------------------
# 验证 4：结构指纹跨进程稳定
# ---------------------------------------------------------------------------


def test_structure_fingerprint_is_stable_across_processes() -> None:
    """验证 4：同一棋盘在**别的进程**里算得同一个结构指纹。

    两个子进程给不同的 `PYTHONHASHSEED`——摘要一旦用上 Python 内置 `hash()`，
    跨进程就不稳（架构 §5.4）。
    """
    board = build_board(4, 3, 1, SAMPLE_CELLS)
    expected = board.structureFingerprint

    first = _child_fingerprint("0")
    second = _child_fingerprint("1")

    assert first == expected
    assert second == expected
    assert first == second


def test_structure_fingerprint_depends_on_board_content() -> None:
    """棋盘内容不同 → 结构指纹不同（指纹得真的"认"内容）。"""
    a = build_board(2, 1, 0, [[1, 0]]).structureFingerprint
    b = build_board(2, 1, 0, [[0, 1]]).structureFingerprint
    c = build_board(2, 1, 0, [[1, 0]]).structureFingerprint
    assert a != b
    assert a == c


def test_structure_fingerprint_is_row_major() -> None:
    """行优先：第 0 行是顶部，行内从左到右（架构 §5.4 的编码口径）。

    转置后的棋盘**指纹不同**——行序变了就是内容变了。
    """
    rows = build_board(2, 2, 0, [[1, 2], [3, 4]]).structureFingerprint
    transposed = build_board(2, 2, 0, [[1, 3], [2, 4]]).structureFingerprint
    assert rows != transposed


# ---------------------------------------------------------------------------
# 验证 5：扩展槽的 key 唯一性
# ---------------------------------------------------------------------------


def test_duplicate_extension_key_is_rejected() -> None:
    """验证 5：重复 key **报错**——不"去重取末值"（架构 §5.4）。

    `extensions` 是 tuple 不是 dict，**能写重复 key**——重复即结构错误。
    """
    with pytest.raises(ValueError):
        build_extensions((("mode", "marathon"), ("mode", "rush")))


def test_duplicate_extension_key_rejected_on_direct_construction() -> None:
    """直接构造 `Snapshot` 也拦得住重复 key（不只 `build_extensions` 拦）。"""
    with pytest.raises(ValueError):
        Snapshot(
            version=1,
            rule="standard",
            phase="running",
            frame=0,
            board=build_board(1, 1, 0, [[0]]),
            active=None,
            ghostDelta=(0, 0),
            hold=None,
            holdUsed=False,
            next=(),
            score=0,
            lines=0,
            level=1,
            combo=0,
            b2b=False,
            piecesPlaced=0,
            grounded=False,
            lockTimer=0,
            gravityTimer=0,
            extensions=(("k", 1), ("k", 2)),
            extensionFingerprint="whatever",
            rngState=None,
        )


def test_extensions_are_sorted_by_key() -> None:
    """扩展槽按 key 排序（规范化形态）：合并顺序不同 → 同一个快照。"""
    ordered = build_extensions((("a", 1), ("b", 2)))
    shuffled = build_extensions((("b", 2), ("a", 1)))
    assert ordered == shuffled
    assert tuple(key for key, _ in ordered) == ("a", "b")


def test_build_snapshot_normalizes_extensions() -> None:
    """构造入口**自己规范化**扩展槽：同一组扩展 = 同一个快照。

    「合并顺序不同 → 同一个快照」这条承诺不能只靠调用方记得先过
    `build_extensions`——快照的 `==` 不该被扩展合并的偶然顺序影响（指纹
    本来就与顺序无关，快照却不相等，两条口径会分叉）。
    """
    ordered = _snapshot_with_extensions((("a", 1), ("b", 2)))
    shuffled = _snapshot_with_extensions((("b", 2), ("a", 1)))

    assert ordered == shuffled
    assert ordered.extensions == (("a", 1), ("b", 2))
    assert shuffled.extensions == (("a", 1), ("b", 2))
    assert ordered.extensionFingerprint == shuffled.extensionFingerprint


def test_build_snapshot_rejects_duplicate_extension_key() -> None:
    """构造入口也拦重复 key（不只 `build_extensions` 拦）。"""
    with pytest.raises(ValueError):
        _snapshot_with_extensions((("a", 1), ("a", 2)))


def test_build_board_rejects_size_mismatch() -> None:
    """尺寸与内容对不上 → **当场报错**。

    结构指纹只覆盖 `cells`，**不覆盖 `width` / `height`**——尺寸错了而指纹
    照样算得出来、快照照样发得出去，回放比对**四项全绿**。校验要挡在构造
    入口，而不是事后查指纹。
    """
    with pytest.raises(ValueError, match="行数"):
        build_board(4, 3, 0, ((0, 0),))
    with pytest.raises(ValueError, match="行数"):
        build_board(4, 3, 0, ())
    with pytest.raises(ValueError, match="长度"):
        build_board(4, 2, 0, ((0, 0, 0, 0), (0, 0)))


def test_extension_entry_alias_matches_contract_scalar_map() -> None:
    """扩展槽的标量联合：内核别名 ↔ 契约常量**同一条**。

    别名不在字段注解的校验范围内（字段比的是 `SCALAR_MAP` 本身）——不钉住
    就会静默分叉：契约改了标量联合、别名没跟着改，两处各说各话。
    """
    from tetris_kernel.contracts.types import SCALAR_MAP

    alias_text = snapshot_module._alias_text(snapshot_module.ExtensionEntry)
    assert alias_text == "tuple[str, int | float | str | bool | None]"
    assert SCALAR_MAP == "tuple[tuple[str, int | float | str | bool | None], ...]"


# ---------------------------------------------------------------------------
# 两个指纹不能混（架构 §5.4）
# ---------------------------------------------------------------------------


def test_extension_fingerprint_is_key_order_independent() -> None:
    """扩展指纹是**规范化**摘要：key 顺序不同 → 同值。"""
    first = compute_extension_fingerprint((("a", 1), ("b", 2)))
    second = compute_extension_fingerprint((("b", 2), ("a", 1)))
    assert first == second


def test_extension_fingerprint_depends_on_key_and_value() -> None:
    """key 变了、值变了 → 摘要都变。"""
    base = compute_extension_fingerprint((("a", 1),))
    assert base != compute_extension_fingerprint((("b", 1),))
    assert base != compute_extension_fingerprint((("a", 2),))


def test_extension_fingerprint_distinguishes_bool_from_int() -> None:
    """布尔与整数**必须区分编码**——`True` 和 `1` 不是同一个值。"""
    assert compute_extension_fingerprint((("k", True),)) != compute_extension_fingerprint(
        (("k", 1),)
    )


def test_extension_key_named_domain_does_not_collide() -> None:
    """扩展 key 叫 `domain` 也**不能撞键**。

    扩展槽的 key 没有名字限制（契约只要求唯一）；域分隔标签写进**值**里，
    不占条目的"键"位——否则这个合法的 key 会把指纹计算撞报错。
    """
    digest = compute_extension_fingerprint((("domain", 1),))
    assert digest
    assert digest != compute_extension_fingerprint((("domain", 2),))


def test_structure_and_extension_fingerprints_are_distinct() -> None:
    """结构指纹与扩展指纹是**两个哈希**（架构 §5.4）。

    拿错了，变体局回放校验的第五项永远比对不上（或永远比得上）。
    """
    board = build_board(2, 2, 0, [[1, 0], [0, 2]])
    extension = compute_extension_fingerprint((("a", 1),))
    assert board.structureFingerprint != extension


# ---------------------------------------------------------------------------
# 护栏：不可变 / 纯读 / 不碰随机源
# ---------------------------------------------------------------------------


def test_snapshot_is_frozen() -> None:
    """快照不可变——改字段直接报错（`frozen=True`）。"""
    snapshot = _sample_snapshot()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.score = 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.rngState = (1, 2, 3)


def test_view_construction_is_pure_read() -> None:
    """视图构造**纯读**：不动状态、不碰随机源（架构 §5.2）。

    同入参构造两次 → 同一个快照；构造过程不改内核给过来的棋盘。
    """
    first = _sample_snapshot()
    second = _sample_snapshot()
    assert first == second
    assert SAMPLE_CELLS == [[0, 1, 0, 0], [2, 0, 0, 3], [0, 0, 0, 0]]


def test_snapshot_module_does_not_import_random() -> None:
    """内核模块**不 import 随机源**——视图构造不碰 RNG 是硬约束。

    "惰性初始化 RNG"会让"关闭全部可观测系统后内核逐帧一致"这条判据在概率上
    就废了（架构 §5.2）。
    """
    source = Path(snapshot_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "random" not in imported
