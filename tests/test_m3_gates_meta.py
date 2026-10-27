"""M3 六道门的**元守卫**（S10 · plan-M3.md §5.2）。

## 它守什么

**门判据的入口不许悄悄消失**。

`pytest -k <名字>` **一条都没选到时退出码是 5**（`EXIT_NOTTESTSCOLLECTED`，
**不是 0、也不是失败码 1**）——非 0，脚本或 CI 抓得到；但**诊断只说"没收集到
用例"，不会告诉你哪个门死了**。手动复跑时那行「821 deselected」看着像"这里没活
干"，**很容易被当成正常结果滑过去**。

M1 的 `G1-2` 与 `G1-3` 就是这么在入口上消失的——**门还在文档里，入口没了**。

所以这个守卫**读 §5.2 那张表**（**单一权威**：计划里那张表就是六个入口的
定义处），把六个命中名**逐条**与 `tests/` 下的用例名比对——**每一条都要至少
命中一个**，**选不到就红，并点名那个入口**。

## 三条不变量

1. **每个命中名都要选得到用例**——选不到就红（**点名入口**）；
2. **六个门就是 `G3-1`…`G3-6`**——表里多一行少一行都红（**表是单一权威**，
   但它自己也要有人守）；
3. **命中名互不相同**（ADR-076 口径）——`G3-5` 与 `G3-1` **共用命中名**会让
   两道门的生死无从分辨。

**自给自足**：不依赖 `tests/conftest.py` 的 fixture。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

#: 仓库根目录（`tests/` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: `tests/` 目录（**用例都在这里**）。
TESTS_DIR = REPO_ROOT / "tests"

#: §5.2 那张表所在的计划文件（**单一权威**）。
PLAN_PATH = REPO_ROOT / "docs" / "plan-M3.md"

#: §5.2 的小节标题（**按标题找表格**，不按行号——行号会漂）。
SECTION_TITLE = "### 5.2 "

#: 复跑命令里命中名的写法。
HIT_PATTERN = re.compile(r"pytest\s+-k\s+([A-Za-z0-9_]+)")

#: 门编号的写法。
GATE_PATTERN = re.compile(r"^G3-\d$")

#: **六道门**（需求 §12 的门判据注册表）。
EXPECTED_GATES: tuple[str, ...] = ("G3-1", "G3-2", "G3-3", "G3-4", "G3-5", "G3-6")


@dataclass(frozen=True)
class Entry:
    """§5.2 表里的一行。"""

    gate: str
    hit: str
    path: str


def _section_5_2() -> str:
    """取 §5.2 那一小节的正文（**到下一个同级标题为止**）。"""
    text = PLAN_PATH.read_text(encoding="utf-8")
    start = text.index(SECTION_TITLE)
    rest = text[start + len(SECTION_TITLE) :]
    end = rest.find("\n### ")
    return rest if end == -1 else rest[:end]


def _entries() -> tuple[Entry, ...]:
    """读 §5.2 那张表 → 六行入口。"""
    entries: list[Entry] = []
    for line in _section_5_2().splitlines():
        stripped = line.strip()
        if not stripped.startswith("| G3-"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 4:
            continue
        gate = cells[0].strip("`")
        if not GATE_PATTERN.match(gate):
            continue
        match = HIT_PATTERN.search(cells[2])
        assert match is not None, f"§5.2 表里 {gate} 那行没有 `pytest -k <名字>`：{cells[2]}"
        entries.append(Entry(gate=gate, hit=match.group(1), path=cells[3].strip("`")))
    return tuple(entries)


def _test_ids() -> tuple[str, ...]:
    """`tests/` 下全部用例的**节点 id 近似**（模块名 + 函数名）。

    **为什么用 AST 扫而不是 `pytest --collect-only`**：收集整个套件要跑一遍
    导入（**千局基准那批夹具不会被触发**，但导入本身要几秒），而且**在测试
    进程里再跑一次收集**容易和当前会话打架。`pytest -k` 的匹配口径是
    "**子串**落在模块名 / 类名 / 函数名里"——这里照这个口径扫。
    """
    ids: list[str] = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        module = path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            ids.append(f"{module}::{node.name}")
    return tuple(ids)


def test_m3_gate_entry_points_all_select_at_least_one_test() -> None:
    """§5.2 的每个命中名，**都要能选中至少一条用例**——**选不到就红**。

    **报红要点名那个入口**：只说"有门死了"，人还得自己回去查是哪一道。
    """
    entries = _entries()
    assert entries, "§5.2 那张表一行都没读到——表的格式变了？"

    test_ids = _test_ids()
    assert test_ids, "tests/ 下一个用例都没扫到"

    unbound: list[str] = []
    for entry in entries:
        if not any(entry.hit in test_id for test_id in test_ids):
            unbound.append(f"{entry.gate}（命中名 {entry.hit!r}）")

    assert not unbound, (
        "以下门判据在 tests/ 下**选不到任何用例**——入口死了：\n  "
        + "\n  ".join(unbound)
        + "\n（`pytest -k` 一条都没选到时退出码是 5，诊断只说「没收集到用例」，"
        "不会告诉你哪个门死了。）"
    )


def test_m3_gate_entry_points_the_table_holds_exactly_six_gates() -> None:
    """§5.2 那张表就是**六道门**——多一行少一行都红（**表自己也要有人守**）。"""
    gates = tuple(entry.gate for entry in _entries())

    assert gates == EXPECTED_GATES, f"§5.2 表里的门是 {gates}，期望 {list(EXPECTED_GATES)}"


def test_m3_gate_entry_points_the_hit_names_are_all_different() -> None:
    """**命中名互不相同**（ADR-076 口径）。

    `G3-5` 与 `G3-1` **共用命中名**会让两道门的生死无从分辨——一道门死了，
    另一道门还"绿着"，看不出来。
    """
    hits = [entry.hit for entry in _entries()]

    assert len(set(hits)) == len(hits), f"命中名有重复：{hits}"


def test_m3_gate_entry_points_the_referenced_test_files_exist() -> None:
    """§5.2 表里写的**测试文件**都得存在（**表自己也要有人守**）。"""
    missing = [
        f"{entry.gate} → {entry.path}"
        for entry in _entries()
        if not (REPO_ROOT / entry.path).exists()
    ]

    assert not missing, "§5.2 表里指着不存在的文件：\n  " + "\n  ".join(missing)
