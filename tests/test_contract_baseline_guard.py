"""契约基线与期望清单的 **git 背书守卫**（ADR-077 · M2 的 S2）。

## 这道门守什么

前两层防线守的是**文件内部**（自述块）与**两份文件之间**（逐条相等）。
它们有个共同的盲区：**基线文件本身可以被直接改**——直接编辑基线文件就能让
工具变绿，而没人检查这次编辑是不是走了显式刷新流程。

**第三层把"谁动了这两份文件"交给 git 回答**：契约基线与期望清单**必须与
`git show HEAD:<路径>` 的内容一致**。工作区改了但没提交 → 红；提交了 → 绿。

## 它不守什么（残余上限照旧写实）

**它不区分"谁写的"**，只区分"改了但没提交"与"改了且已提交"——**能把两个文件
改好再提交的人，也能改好 git 历史**。所以这一层是把"**静默绕过**"变成
"**留痕绕过**"，**不是"防住了"**（ADR-077 的残余上限照旧写实，不吹大）。

## 为什么门长在这个工具里

守卫的判据是**"这份文件与 `HEAD` 一致吗"**——它需要 git 历史，那是工具层
的事（`tools/_toolkit.py` 提供取内容的低层能力）。放在测试里另抄一份就是
**影子副本**，两处会悄悄分叉。

## 这道门自己也要有反向用例

**"要求改了没人拦"和"没有要求"是同一种失守**（M0 全量审查的原题）——
所以下面每条正向用例都配一条反向用例：把文件改坏，断言守卫**必须红**，
而且**必须点名是哪一份文件**。

**改动的是仓库里真实的那两份文件**（守卫守的就是它们，绕到临时目录去测就
测不到守卫了）——所以每条反向用例都用 `try/finally` **精确复原**，并断言
复原后逐字节相同。
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

#: 仓库根目录（`tests/` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 守卫守着那两份文件（**只管这两个**，别的一概不看）。
CONTRACT_BASELINE = REPO_ROOT / "tests" / "baseline" / "contracts_baseline.json"
CONTRACT_EXPECTED = REPO_ROOT / "tests" / "baseline" / "contracts_expected.json"

#: 采集工具在 `tools/` 下，**它自己** import 同目录的 `_toolkit`，
#: 所以要把 `tools/` 挂到 `sys.path` 上（与 `tests/test_baselines.py` 同一手法）。
sys.path.insert(0, str(REPO_ROOT / "tools"))
_contract_baseline = importlib.import_module("contract_baseline")

#: 红项的机器可辨标记（ASCII）——Windows 的 GBK 管道会把中文提示打散，
#: **按字节查标记才稳**（与 `tests/test_baselines.py` 的 `_run_tool_bytes` 同款）。
MARKER = b"git-backing"


def _run_tool_bytes(*args: str) -> subprocess.CompletedProcess[bytes]:
    """跑基线工具，返回**原始字节**的结果（不按本地代码页解码）。"""
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "contract_baseline.py"), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )


@contextmanager
def _temporarily_replaced(path: Path, content: bytes) -> Iterator[None]:
    """临时把文件换成给定内容，退出时**逐字节复原**。

    **复原放在 `finally` 里**：断言失败、pytest 中途退出，都不能把仓库留脏——
    **留在工作区里的脏文件会跟着下一次提交走**。
    """
    original = path.read_bytes()
    try:
        path.write_bytes(content)
        yield
    finally:
        path.write_bytes(original)
        assert path.read_bytes() == original, f"{path} 没能逐字节复原"


def _with_one_hash_faked(path: Path) -> bytes:
    """把一份清单里的**某一条**哈希改成假值，返回改完的字节。"""
    document = json.loads(path.read_text(encoding="utf-8"))
    key = sorted(document["entries"])[0]
    document["entries"][key] = "0" * 32
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


# ---------------------------------------------------------------------------
# 正向：工作区干净时，守卫必须是绿的
# ---------------------------------------------------------------------------


def test_the_git_backing_guard_is_green_on_a_clean_working_tree() -> None:
    """两份文件与 `HEAD` 一致时，校验通过（**不许把守卫写成恒红**）。

    **"不许恒红"和"不许恒绿"同样重要**：一道永远红的门等于没有门。
    """
    result = _run_tool_bytes()

    assert result.returncode == 0, (
        "工作区干净，基线校验却红了：\n"
        f"{result.stdout.decode(errors='replace')}\n{result.stderr.decode(errors='replace')}"
    )
    assert MARKER not in result.stdout, "干净工作区却报了 git 背书红项——守卫写反了"


def test_the_two_guarded_files_are_backed_by_head() -> None:
    """两份文件都真的能在 `HEAD` 里找到对应内容（**背书真的存在**）。

    这条守的是"守卫自己的前提"：哪天 `HEAD` 里查不到那份文件，守卫报的就不是
    "内容不一致"而是"无从背书"——**两种红的含义完全不同，不能混**。
    """
    for path in (CONTRACT_BASELINE, CONTRACT_EXPECTED):
        assert path.exists(), f"{path} 不存在"
        relative = path.relative_to(REPO_ROOT).as_posix()
        head = _contract_baseline.git_head_bytes(REPO_ROOT, relative)
        assert head is not None, f"HEAD 里没有 {relative}——背书的前提没了"
        assert _contract_baseline.git_backing_problem(path) is None, (
            f"{relative} 与 HEAD 一致的文件却报了背书红项"
        )


# ---------------------------------------------------------------------------
# 反向：把文件改坏，守卫必须红，而且必须点名是哪一份
# ---------------------------------------------------------------------------


def test_the_guard_rejects_a_baseline_file_edited_in_the_working_tree() -> None:
    """**手改基线文件**（只改一条哈希）→ 守卫必须红，并点名是基线文件。

    **只改一条、别的不动**：红项必须精确指向那一处，不能是"整个文件都不一样"
    这种没法定位的说法。
    """
    with _temporarily_replaced(CONTRACT_BASELINE, _with_one_hash_faked(CONTRACT_BASELINE)):
        result = _run_tool_bytes()

    raw = result.stdout + result.stderr
    assert result.returncode != 0, f"改了基线文件，守卫却放行了：\n{raw!r}"
    assert MARKER in raw, f"报红了但看不出是 git 背书那一层：\n{raw!r}"
    assert b"contracts_baseline.json" in raw, f"报红了但没点名是基线文件：\n{raw!r}"
    assert b"--update-baseline" in raw, f"没告诉人该怎么走：\n{raw!r}"


def test_the_guard_rejects_an_expected_list_edited_in_the_working_tree() -> None:
    """**手改期望清单**（只改一条）→ 守卫必须红，并点名是期望清单。

    **双向都拦**：不然"只改一份"可以挑软的那一侧下手。
    """
    with _temporarily_replaced(CONTRACT_EXPECTED, _with_one_hash_faked(CONTRACT_EXPECTED)):
        result = _run_tool_bytes()

    raw = result.stdout + result.stderr
    assert result.returncode != 0, f"改了期望清单，守卫却放行了：\n{raw!r}"
    assert MARKER in raw, f"报红了但看不出是 git 背书那一层：\n{raw!r}"
    assert b"contracts_expected.json" in raw, f"报红了但没点名是期望清单：\n{raw!r}"
    assert b"--update-expected" in raw, f"没告诉人该怎么走：\n{raw!r}"


def test_the_guard_rejects_a_deleted_baseline_file() -> None:
    """**把基线文件删掉** → 守卫必须红（`git show` 取不到工作区那份）。

    **"文件整个不见了"是另一条红**：不是"内容不一致"，是"没东西可比"。
    """
    original = CONTRACT_BASELINE.read_bytes()
    try:
        CONTRACT_BASELINE.unlink()
        result = _run_tool_bytes()
    finally:
        CONTRACT_BASELINE.write_bytes(original)
        assert CONTRACT_BASELINE.read_bytes() == original, "基线文件没能逐字节复原"

    raw = result.stdout + result.stderr
    assert result.returncode != 0, f"基线文件被删了，守卫却放行了：\n{raw!r}"
    assert MARKER in raw, f"报红了但看不出是 git 背书那一层：\n{raw!r}"


# ---------------------------------------------------------------------------
# 反向：非 git 工作区不许静默跳过
# ---------------------------------------------------------------------------


def test_a_file_outside_any_git_worktree_is_reported_not_skipped(tmp_path: Path) -> None:
    """**不在 git 工作区里**的文件 → 必须报红并说清原因，**不许静默跳过**。

    **静默跳过是最坏的结果**：守卫在别人机器上、在 CI 里、在解包的源码目录里
    **悄悄不生效**，而所有人看到的是"校验通过"。**没扫到 ≠ 扫了没问题**。
    """
    outside = tmp_path / "contracts_baseline.json"
    outside.write_text("{}\n", encoding="utf-8")

    problem = _contract_baseline.git_backing_problem(outside)

    assert problem is not None, "不在 git 工作区里的文件，守卫却当它没问题"
    assert "git" in problem, f"红项没说清是 git 背书这一层：{problem}"


def test_a_file_missing_from_head_is_reported_not_skipped(tmp_path: Path) -> None:
    """**`HEAD` 里没有这个文件** → 必须报红（它从没被提交过）。"""
    outside = tmp_path / "never_committed.json"
    outside.write_text("{}\n", encoding="utf-8")

    problem = _contract_baseline.git_backing_problem(outside)

    assert problem is not None, "没被提交过的文件，守卫却当它没问题"


# ---------------------------------------------------------------------------
# 守卫自己的边界：不区分"谁写的"
# ---------------------------------------------------------------------------


def test_the_guard_only_distinguishes_committed_from_uncommitted() -> None:
    """守卫的判据**只有一条**：文件与 `HEAD` 一不一致。

    **它不区分"谁写的"**——改坏文件的人和顺手刷新基线的人，在它眼里没有
    分别。这条用例把这条边界写死：**别让下一个人以为它能挡蓄意绕过**
    （ADR-077 的残余上限照旧写实）。

    **断言前先折叠空白**：docstring 是**折行排版**的，"留痕绕过"这个词组
    很可能被折行拆开（实测就撞上过一次）——**断言不该对排版敏感**。
    """
    source = "".join((_contract_baseline.__doc__ or "").split())

    assert "不区分" in source, "守卫的 docstring 没写明它不区分谁写的"
    assert "留痕绕过" in source, "守卫的 docstring 没写明它挡不住蓄意绕过"


def test_the_guard_watches_exactly_the_two_contract_files() -> None:
    """守卫**只管那两份文件**——别的文件改了它不吭声。

    **为什么这条要钉住**：守卫若盯整个工作区，"改声明、还没刷基线"的正常
    流程会被拦下——那是 S1 到 S3 之间的常态。**它守的是"这两份文件有没有被
    悄悄改过"，不是"工作区干不干净"**。
    """
    watched = {path.name for path in _contract_baseline.GIT_BACKED_FILES}

    assert watched == {"contracts_baseline.json", "contracts_expected.json"}


@pytest.mark.parametrize("watched", ["contracts_baseline.json", "contracts_expected.json"])
def test_each_guarded_file_is_reported_by_name(watched: str) -> None:
    """两份文件各自的红项**都带自己的文件名**——不许含糊成"某个文件"。"""
    paths = {
        "contracts_baseline.json": CONTRACT_BASELINE,
        "contracts_expected.json": CONTRACT_EXPECTED,
    }

    with _temporarily_replaced(paths[watched], _with_one_hash_faked(paths[watched])):
        result = _run_tool_bytes()

    raw = result.stdout + result.stderr
    assert watched.encode("ascii") in raw, f"红项没点名 {watched}：\n{raw!r}"
