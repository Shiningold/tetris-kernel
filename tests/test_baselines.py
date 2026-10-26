"""四份基线的门禁（S17 冻结的两道裁判 + S16 的事件数行为基线 + M2 的 bus 独立基线）。

## 为什么要这几道门

`tools/` 下那几份基线（契约 v1 字段级哈希 · 冻结区 `kernel/` + `rules/` 内容哈希 ·
每帧事件数与事件类型 · **`bus/` 独立内容哈希**）**是工具**：没人跑它，它就只是躺在
仓库里的一份 JSON。基线纪律要成立，就得**每一次跑测试都跑一遍它**。

- **契约基线**（`tools/contract_baseline.py`）：契约面**只增不改**——新增键放行，
  **已有键的哈希变化或消失报红**（改名 / 改型 / 删字段 = major 变更）；
- **冻结区基线**（`tools/check_frozen_baseline.py`）：`kernel/` + `rules/` 的
  **内容哈希**——变体落地时这两个目录的差异必须为空，**改一行注释也该红**；
- **事件数基线**（`tools/measure_frame_cost.py --baseline-check`）：固定场景下
  **逐帧发出多少条事件、分别是哪些类型**——契约基线只看**声明**、冻结区基线只看
  **文件内容**，**行为恰好落在两者的缝里**（批次 D 审查 D5）。`bus/` 与 `host/`
  **不在冻结区**，宿主层多发一条事件，声明面与文件哈希面都看不出来。
- **`bus/` 独立基线**（`tools/check_frozen_baseline.py --scope bus`）：**M2 总线封板
  后启用**（ADR-019）——封板之后**改动 `bus/` 任一文件必红**。**它与冻结区基线各管
  各的**：`bus/` 不许混进冻结区（冻结区严格对齐 G4-1），冻结区也不许混进 `bus/`。

## 每道门都要有**反向用例**

只断言"跑一遍工具，退出码是 0"证明的是"此刻基线与代码一致"，**没有证明"基线与
代码不一致时工具会拒绝"**。把 `compare()` 改成恒不报红、把冻结区的差异判定改成
恒假，**全套 pytest 仍然全绿**——这正是批次 D 审查 D2 抓到的洞。

所以这里**喂一份故意改坏的临时基线给工具，断言它退出码非 0**：测的是"工具会
拒绝"，**不复制任何哈希实现**（抄一份就是影子副本，两处会悄悄分叉——`ai_docs`
里否掉的正是这个方案，否掉的是"抄实现"，不是"验行为"）。

**跑的是工具本身**（子进程调 `python tools/xxx.py`），不是把逻辑抄一份到测试里。

## M0 全量审查补的五条（M0-2 / M0-3 / M0-4 / M0-5 / M0-8）

- **M0-2 `--reason` 必填**：三份基线的刷新**不带 `--reason` 一律拒绝，且不落盘**
  （原先零用例——把必填去掉，全套照样全绿）；
- **M0-3 自检要比对期望值**：退出码是**被测代码自己算的**，它说"通过"pytest 就
  没话说。这里**独立转录**一份期望值与工具的实测值交叉核对；
- **M0-4 冻结区自证块**：审查报告说它是"装饰性的"——**实测不成立**（自述块记着
  99 个文件而实际只有 14 个时它照样红）。保留它，并补一条反向用例钉住；
- **M0-5 性能预算阈值**：`BUDGET_US` 没有守卫——改成 10 亿，工具照样报"全部在
  预算内"，`PROGRESS.md` 里那组占比数字随之静默失真；
- **M0-8 事件数基线只记条数不记类型**：条数一样、类型换了的行为改动它看不见。
"""

from __future__ import annotations

import importlib
import json
import locale
import subprocess
import sys
from pathlib import Path

import pytest

#: 仓库根目录（`tests/` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 四份基线文件（**必须存在**——否则工具会"基线尚未生成"然后放行，守卫等于没跑）。
CONTRACT_BASELINE = REPO_ROOT / "tests" / "baseline" / "contracts_baseline.json"
FROZEN_BASELINE = REPO_ROOT / "tests" / "baseline" / "kernel_hashes.json"
FRAME_EVENTS_BASELINE = REPO_ROOT / "tests" / "baseline" / "frame_events.json"

#: `bus/` 独立基线（M2 总线封板后启用 · ADR-019）。
BUS_BASELINE = REPO_ROOT / "tests" / "baseline" / "bus_hashes.json"

#: 采集工具在 `tools/` 下，**它自己** import 同目录的 `_toolkit`，
#: 所以要把 `tools/` 挂到 `sys.path` 上（与 `tests/test_rng.py` 同一手法）。
sys.path.insert(0, str(REPO_ROOT / "tools"))
_measure_frame_cost = importlib.import_module("measure_frame_cost")


def _run_tool_bytes(relative_path: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    """跑 `tools/` 下的一个脚本，返回**原始字节**的结果（不按本地代码页解码）。

    **什么时候用它**：要断言"工具点了哪一条的名"的时候。中文提示在 Windows
    管道下是 GBK（批次 C 审查 C14 的同款编码坑），而按 GBK 解码 UTF-8 字节时
    **双字节字符的尾字节会吞掉后面那个 ASCII 字符**——键名 `command/…` 的头
    一个字母就是这样被吃掉的，断言"输出里有这个键"于是时灵时不灵。键名本身
    是 ASCII，**原始字节里它一定是连续的**，按字节查就稳。
    """
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / relative_path), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )


def _run_tool(relative_path: str, *args: str) -> subprocess.CompletedProcess[str]:
    """跑 `tools/` 下的一个脚本，返回解码好的结果。

    **按本机首选编码解码**（Windows 上管道走本地代码页，本机是 GBK）——用 UTF-8
    硬解会把中文提示变成乱码，报错信息本身报错。
    """
    result = _run_tool_bytes(relative_path, *args)
    encoding = locale.getpreferredencoding(False)
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=result.stdout.decode(encoding, errors="replace"),
        stderr=result.stderr.decode(encoding, errors="replace"),
    )


def _read_json(path: Path) -> dict:
    """读一份 JSON 基线文件。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, document: dict) -> Path:
    """写一份 JSON 基线文件（**格式与工具写出来的一致**）。"""
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


# ---------------------------------------------------------------------------
# 正向：三道门都要绿
# ---------------------------------------------------------------------------


def test_the_contract_baseline_has_not_changed_any_existing_key() -> None:
    """契约 v1 的**已有键**一个都没变（新增键放行，那是契约面只增不改的余量）。"""
    assert CONTRACT_BASELINE.exists(), f"契约基线不存在：{CONTRACT_BASELINE}"

    result = _run_tool("tools/contract_baseline.py")

    assert result.returncode == 0, (
        "契约基线校验失败——已有键的哈希变了或消失了（= major 变更）：\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_frozen_kernel_and_rules_have_not_changed() -> None:
    """冻结区（`kernel/` + `rules/`）的内容与基线**逐字节**一致。

    **哈希整个文件内容**，不是逐行、不是符号表：改一行注释也该红（架构 §4.4）。
    """
    assert FROZEN_BASELINE.exists(), f"冻结区基线不存在：{FROZEN_BASELINE}"

    result = _run_tool("tools/check_frozen_baseline.py")

    assert result.returncode == 0, (
        f"冻结区基线校验失败——内核 / 规则的内容变了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_frame_event_counts_match_the_baseline() -> None:
    """固定场景下**逐帧**发出的事件数与基线一致。

    **逐帧比，不只看 P50 / max**：多出来的几条事件挪到别的帧上也是行为变了
    （批次 D 审查 D5）。
    """
    assert FRAME_EVENTS_BASELINE.exists(), f"事件数基线不存在：{FRAME_EVENTS_BASELINE}"

    result = _run_tool(
        "tools/measure_frame_cost.py",
        "--baseline-check",
        str(FRAME_EVENTS_BASELINE),
    )

    assert result.returncode == 0, (
        f"事件数基线校验失败——内核每帧发出的事件数变了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_measurement_tool_passes_its_own_self_test() -> None:
    """采集工具的**自检**要真的验东西（写死期望值，不是"再跑一遍"）。

    `percentile()` 改成返回最小值时，自检必须红——否则真实采集会静默变错
    （批次 D 审查 D11）。
    """
    result = _run_tool("tools/measure_frame_cost.py", "--self-test")

    assert result.returncode == 0, (
        f"采集工具的自检没通过——统计口径坏了：\n{result.stdout}\n{result.stderr}"
    )


#: 自检实测值的**独立转录**（M0 全量审查 M0-3）。
#:
#: **这里另抄一份**，不 import 工具里的 `SELF_TEST_EXPECTED`：退出码是被测代码
#: 自己算的，工具说"通过"pytest 就没话说。**两份独立抄出来的期望值必须相等**——
#: 有人把工具的期望值改成"坏实现的实际输出"来消音时，这份转录会当场对不上。
#:
#: 数字的来路：合成样本是 `random.Random(1)` 逐帧取 `randint(40, 400)`（耗时）
#: 与 `randint(0, 6)`（事件数），共 **500** 帧；百分位用最近秩
#: `rank = ceil(分数 × 500)`，取排序后第 `rank` 个。
SELF_TEST_EXPECTED_COST = {"p50": 213, "p99": 399, "max": 400}
SELF_TEST_EXPECTED_EVENTS = {"p50": 3, "p99": 6, "max": 6}


def test_the_self_test_measures_the_values_the_test_transcribes_independently(
    tmp_path: Path,
) -> None:
    """自检的**实测值**必须与上面那份独立转录逐项相等（不只看退出码）。

    **退出码是被测代码自己算的**——它说"通过"，pytest 就没话说；断言"实测值
    等于我另抄的期望值"才是真交叉核对（M0 全量审查 M0-3）。
    """
    report = tmp_path / "self_test.json"

    result = _run_tool("tools/measure_frame_cost.py", "--self-test", "--out", str(report))

    assert result.returncode == 0, (
        f"采集工具的自检没通过——统计口径坏了：\n{result.stdout}\n{result.stderr}"
    )
    payload = _read_json(report)
    for key, expected in SELF_TEST_EXPECTED_COST.items():
        assert payload["frameCostUs"][key] == expected, (
            f"自检实测的耗时 {key} 与独立转录的期望值对不上："
            f"{payload['frameCostUs'][key]} ≠ {expected}"
        )
    for key, expected in SELF_TEST_EXPECTED_EVENTS.items():
        assert payload["eventsPerFrame"][key] == expected, (
            f"自检实测的事件数 {key} 与独立转录的期望值对不上："
            f"{payload['eventsPerFrame'][key]} ≠ {expected}"
        )


#: 最近秩百分位的**独立转录**（M0-3）：`rank = ceil(分数 × 样本数)`，取第 `rank` 个。
#:
#: 工具自己也有同一张表（`SELF_TEST_PERCENTILE_CASES`），但那是在**被测代码
#: 内部**——它被改成返回最小值时，工具会把自己的表一起改掉。这里是**另一份**。
PERCENTILE_CASES = (
    ((1, 2, 3, 4), 0.50, 2),
    ((1, 2, 3, 4), 0.99, 4),
    ((1, 2, 3, 4, 5), 0.50, 3),
    ((1, 2, 3, 4, 5), 0.20, 1),
    ((10,), 0.50, 10),
    ((1, 2, 3, 4, 5, 6, 7, 8, 9, 10), 1.00, 10),
)


@pytest.mark.parametrize(("samples", "fraction", "expected"), PERCENTILE_CASES)
def test_the_percentile_matches_the_hand_computed_rank(
    samples: tuple[int, ...], fraction: float, expected: int
) -> None:
    """百分位按**最近秩**算——`rank = ceil(分数 × n)`，取排序后第 `rank` 个。

    离插入插值只差一句话，但**这一句话就是验收口径**（架构 §5.3 的确定性要求），
    插值会让"两次算出来一样"不成立。
    """
    assert _measure_frame_cost.percentile(list(samples), fraction) == expected


def test_the_frame_cost_budget_is_the_one_nfr_2_declares() -> None:
    """性能预算阈值**钉死在 NFR-2 的 1.6 毫秒**上，不许被悄悄改掉。

    `BUDGET_US` 改成 10 亿，工具照样报"全部在预算内"——而 `PROGRESS.md` 里
    那组"占预算 24%–27%"的数字会随之静默失真（M0 全量审查 M0-5）。

    **出处**：需求 §9 的 NFR-2（单帧逻辑开销 ≤ 1.6ms = 60fps 帧步长 16.67ms 的
    10%）· `docs/spec-M0.md` 验收表「性能」行。**1.6ms = 1600μs**。
    """
    assert _measure_frame_cost.BUDGET_US == 1600, (
        f"单帧逻辑预算应当是 NFR-2 的 1600 微秒，实为 {_measure_frame_cost.BUDGET_US}"
    )


# ---------------------------------------------------------------------------
# 反向：基线坏了，工具必须拒绝（批次 D 审查 D2）
# ---------------------------------------------------------------------------


def test_the_contract_baseline_rejects_a_baseline_with_one_entry_removed(tmp_path: Path) -> None:
    """基线**少一条**时必须报红。

    少了的那一条会落进"新增键放行"那一支——**「只增不改」那一刻静默退化成
    「只查已有键」**，而工具与 pytest 都还全绿（批次 D 审查 D1）。
    """
    document = _read_json(CONTRACT_BASELINE)
    entries = document["entries"]
    removed = sorted(entries)[0]
    del entries[removed]
    broken = _write_json(tmp_path / "contracts_baseline.json", document)

    result = _run_tool("tools/contract_baseline.py", "--baseline", str(broken))

    assert result.returncode != 0, (
        f"基线少了 {removed} 条，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_contract_baseline_rejects_an_emptied_baseline(tmp_path: Path) -> None:
    """基线**被清空**时必须报红（全部 356 条都成了"新增键"）。"""
    document = _read_json(CONTRACT_BASELINE)
    document["entries"] = {}
    broken = _write_json(tmp_path / "contracts_baseline.json", document)

    result = _run_tool("tools/contract_baseline.py", "--baseline", str(broken))

    assert result.returncode != 0, f"基线被清空了，工具却放行了：\n{result.stdout}\n{result.stderr}"


def test_the_contract_baseline_rejects_a_changed_entry_hash(tmp_path: Path) -> None:
    """基线里**某一条的哈希**被改动时必须报红（= major 变更）。"""
    document = _read_json(CONTRACT_BASELINE)
    entries = document["entries"]
    changed = sorted(entries)[0]
    entries[changed] = "0" * 32
    broken = _write_json(tmp_path / "contracts_baseline.json", document)

    result = _run_tool("tools/contract_baseline.py", "--baseline", str(broken))

    assert result.returncode != 0, (
        f"基线里 {changed} 的哈希被改了，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_contract_baseline_rejects_a_tampered_entry_count(tmp_path: Path) -> None:
    """基线**自述块里的条数**被改动时必须报红（基线自己得证明没被动过）。"""
    document = _read_json(CONTRACT_BASELINE)
    document["meta"]["entryCount"] = 1
    broken = _write_json(tmp_path / "contracts_baseline.json", document)

    result = _run_tool("tools/contract_baseline.py", "--baseline", str(broken))

    assert result.returncode != 0, (
        f"基线自述的条数被改了，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_contract_baseline_rejects_a_mismatched_contract_version(tmp_path: Path) -> None:
    """基线自述的**契约版本**与当前不符时必须报红（版本升级 = major 变更）。

    `meta.contractVersion` 原来只写不读——**基线自述块在撒谎**：读的人会以为
    基线认版本（批次 D 审查 D6）。
    """
    document = _read_json(CONTRACT_BASELINE)
    document["meta"]["contractVersion"] = "v2"
    broken = _write_json(tmp_path / "contracts_baseline.json", document)

    result = _run_tool("tools/contract_baseline.py", "--baseline", str(broken))

    assert result.returncode != 0, (
        f"基线自述的契约版本被改了，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_frozen_baseline_rejects_a_baseline_with_one_file_removed(tmp_path: Path) -> None:
    """冻结区基线**少一个文件**时必须报红。"""
    document = _read_json(FROZEN_BASELINE)
    files = document["files"]
    removed = sorted(files)[0]
    del files[removed]
    broken = _write_json(tmp_path / "kernel_hashes.json", document)

    result = _run_tool("tools/check_frozen_baseline.py", "--baseline", str(broken))

    assert result.returncode != 0, (
        f"冻结区基线少了 {removed}，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_frozen_baseline_rejects_a_changed_file_hash(tmp_path: Path) -> None:
    """冻结区基线里**某个文件的哈希**被改动时必须报红（内容变了）。"""
    document = _read_json(FROZEN_BASELINE)
    files = document["files"]
    changed = sorted(files)[0]
    files[changed] = "0" * 32
    broken = _write_json(tmp_path / "kernel_hashes.json", document)

    result = _run_tool("tools/check_frozen_baseline.py", "--baseline", str(broken))

    assert result.returncode != 0, (
        f"冻结区基线里 {changed} 的哈希被改了，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_frame_event_baseline_rejects_a_changed_frame(tmp_path: Path) -> None:
    """事件数基线里**某一帧**的事件数被改动时必须报红（行为变了）。"""
    document = _read_json(FRAME_EVENTS_BASELINE)
    counts = document["frameEventCounts"]
    changed = counts.index(5) if 5 in counts else 0
    counts[changed] = counts[changed] + 2
    broken = _write_json(tmp_path / "frame_events.json", document)

    result = _run_tool(
        "tools/measure_frame_cost.py",
        "--baseline-check",
        str(broken),
    )

    assert result.returncode != 0, (
        f"事件数基线里第 {changed} 帧被改了，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_frame_event_baseline_rejects_a_changed_event_type(tmp_path: Path) -> None:
    """事件**类型**被换掉时必须报红——**条数一样也不行**（M0-8）。

    原先基线只记条数：把 `piece:spawned` 换成一条同条数的事件，逐帧条数
    **一模一样**，基线照样全绿——**行为变了但基线看不见**。
    """
    document = _read_json(FRAME_EVENTS_BASELINE)
    shapes = document["frameEventTypeShapes"]
    assert shapes, "基线里没有事件类型形状——M0-8 的守卫无从谈起"
    types = list(shapes[0]["types"])
    # **条数不变，只换类型**——这正是"只记条数的基线看不见"的那一类改动。
    types[0] = "piece:moved"
    shapes[0]["types"] = types
    broken = _write_json(tmp_path / "frame_events.json", document)

    result = _run_tool(
        "tools/measure_frame_cost.py",
        "--baseline-check",
        str(broken),
    )

    assert result.returncode != 0, (
        f"基线里第 0 帧的事件类型被换了，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 反向：刷新基线必须写明理由（M0 全量审查 M0-2）
# ---------------------------------------------------------------------------
#
# **「要求改了没人拦」和「没有要求」是同一种失守**——三份基线的刷新都要求
# `--reason`，但原先**零用例**：把必填去掉，全套 pytest 照样全绿。
#
# 断言两件事：**退出码非 0**（拒绝刷新）**且基线文件没被写出来**（真的没刷新）。
# 目标路径指向**临时目录里一个不存在的文件**——工具要是没守住，它就会把基线
# 写到那儿，这条用例当场红。


def test_the_contract_baseline_refresh_requires_a_reason(tmp_path: Path) -> None:
    """契约基线刷新**不带 `--reason`** 时必须拒绝，且不落盘。"""
    target = tmp_path / "contracts_baseline.json"

    result = _run_tool(
        "tools/contract_baseline.py",
        "--update-baseline",
        "--baseline",
        str(target),
    )

    assert result.returncode != 0, (
        f"刷新基线没带 --reason，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )
    assert not target.exists(), "没带 --reason 却把基线写出来了"


def test_the_frozen_baseline_refresh_requires_a_reason(tmp_path: Path) -> None:
    """冻结区基线刷新**不带 `--reason`** 时必须拒绝，且不落盘。"""
    target = tmp_path / "kernel_hashes.json"

    result = _run_tool(
        "tools/check_frozen_baseline.py",
        "--update-baseline",
        "--baseline",
        str(target),
    )

    assert result.returncode != 0, (
        f"刷新基线没带 --reason，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )
    assert not target.exists(), "没带 --reason 却把基线写出来了"


def test_the_frame_event_baseline_refresh_requires_a_reason(tmp_path: Path) -> None:
    """事件数基线刷新**不带 `--reason`** 时必须拒绝，且不落盘。"""
    target = tmp_path / "frame_events.json"

    result = _run_tool(
        "tools/measure_frame_cost.py",
        "--baseline-out",
        str(target),
    )

    assert result.returncode != 0, (
        f"刷新基线没带 --reason，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )
    assert not target.exists(), "没带 --reason 却把基线写出来了"


# ---------------------------------------------------------------------------
# 反向：冻结区基线的自述块不是装饰品（M0 全量审查 M0-4）
# ---------------------------------------------------------------------------


def test_the_frozen_baseline_rejects_a_tampered_file_count(tmp_path: Path) -> None:
    """冻结区基线**自述块里的文件数**被改动时必须报红。

    审查报告说这块是"装饰性的"（去掉后判定一字不变）——**实测不成立**：
    差异检查兜不住"自述块与实际内容对不上"这一条，正是它守的
    （自述 99 个文件 · 实际 14 个 → 红）。保留它，并把它钉住。
    """
    document = _read_json(FROZEN_BASELINE)
    document["meta"]["fileCount"] = 1
    broken = _write_json(tmp_path / "kernel_hashes.json", document)

    result = _run_tool("tools/check_frozen_baseline.py", "--baseline", str(broken))

    assert result.returncode != 0, (
        f"基线自述的文件数被改了，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 跨文件期望清单（第二层防线 · M1-S5）
# ---------------------------------------------------------------------------
#
# **M0-1 的原题**：基线少一条契约、**同时**把自述块里的条数改成新条数——基线
# 仍然自洽，工具照样放行（少掉的那条落进"新增键放行"那一支）。第一层（自述块）
# 挡不住，因为**计数键与被计数的条目在同一个文件里**。
#
# **第二层**：期望值移出基线文件，另立一份期望清单，两份**逐条**相等。守的是
# "**只改一处**"——两份都在仓内，同时改两份仍能骗过工具（残余上限写实）。

#: 跨文件期望清单（**期望值不在基线文件里**）。
CONTRACT_EXPECTED = REPO_ROOT / "tests" / "baseline" / "contracts_expected.json"


def test_the_contract_expected_list_mirrors_the_baseline_entry_for_entry() -> None:
    """期望清单与基线**逐条相等**——键集与哈希都要一样。

    **独立转录**：这里把两份文件的条目直接读出来比对，不采信工具自己算的
    退出码（退出码是被测代码自己算的，它说"通过"pytest 就没话说）。
    """
    assert CONTRACT_EXPECTED.exists(), f"期望清单不存在：{CONTRACT_EXPECTED}"

    baseline = _read_json(CONTRACT_BASELINE)
    expected = _read_json(CONTRACT_EXPECTED)

    assert set(expected["entries"]) == set(baseline["entries"]), (
        "两份清单的键集不相等——基线与期望清单必须逐条相等"
    )
    assert expected["entries"] == baseline["entries"], (
        "两份清单的哈希不相等——基线与期望清单必须逐条相等"
    )


def _consistent_pair(tmp_path: Path) -> tuple[Path, Path]:
    """造一对**与当前声明完全一致**的清单文件（基线 + 期望清单）。

    **为什么要在临时目录里另造**：仓里的基线此刻还没按 M1-C1 / M1-C2 刷新，
    它与当前声明本来就对不上——直接拿它做反向验证，红项会被「基线 vs 当前声明」
    那一层盖住，测的就不是跨文件那一层了。临时这一对是干净的：**只有跨文件
    自证能红**。
    """
    baseline = tmp_path / "contracts_baseline.json"
    expected = tmp_path / "contracts_expected.json"

    generated = _run_tool(
        "tools/contract_baseline.py",
        "--update-baseline",
        "--baseline",
        str(baseline),
        "--reason",
        "反向验证用：造一份与当前声明一致的基线",
    )
    assert generated.returncode == 0, (
        f"造不出与当前声明一致的基线：\n{generated.stdout}\n{generated.stderr}"
    )

    mirrored = _run_tool(
        "tools/contract_baseline.py",
        "--update-expected",
        "--baseline",
        str(baseline),
        "--expected",
        str(expected),
        "--reason",
        "反向验证用：造一份与基线一致的期望清单",
    )
    assert mirrored.returncode == 0, (
        f"造不出与基线一致的期望清单：\n{mirrored.stdout}\n{mirrored.stderr}"
    )

    return baseline, expected


def test_the_two_lists_agree_when_nothing_is_tampered(tmp_path: Path) -> None:
    """两份清单**一致**时校验通过（正向：不许把跨文件自证写成恒红）。"""
    baseline, expected = _consistent_pair(tmp_path)

    result = _run_tool(
        "tools/contract_baseline.py",
        "--baseline",
        str(baseline),
        "--expected",
        str(expected),
    )

    assert result.returncode == 0, f"两份清单一致，工具却报红：\n{result.stdout}\n{result.stderr}"


def test_the_contract_baseline_rejects_a_baseline_with_one_entry_removed_and_the_count_synced(
    tmp_path: Path,
) -> None:
    """M0-1 的原题：基线**少一条**并**同步改 `meta.entryCount`** 时必须报红。

    **M0 时它是绿的**：自述块与实际条数对得上，工具就放行——少掉的那条落进
    「新增键放行」那一支，「只增不改」那一刻静默退化成「只查已有键」。

    **为什么红的是跨文件那一层**：临时这一对清单原本与当前声明**完全一致**
    （`test_the_two_lists_agree_when_nothing_is_tampered` 断言它绿）——基线自述
    块、基线 vs 当前声明两层都是绿的，唯一起变化的是两份清单不再相等。
    """
    baseline, expected = _consistent_pair(tmp_path)

    document = _read_json(baseline)
    entries = document["entries"]
    removed = sorted(entries)[0]
    del entries[removed]
    # **同步改条数**——这是原题的关键：自述块照样自洽。
    document["meta"]["entryCount"] = len(entries)
    broken = _write_json(tmp_path / "broken_baseline.json", document)

    result = _run_tool_bytes(
        "tools/contract_baseline.py",
        "--baseline",
        str(broken),
        "--expected",
        str(expected),
    )

    raw = result.stdout + result.stderr
    assert result.returncode != 0, f"基线少了 {removed} 条并同步改了条数，工具却放行了：\n{raw!r}"
    assert removed.encode("ascii") in raw, "工具报红了但没点名少掉的那一条"


def test_the_contract_baseline_rejects_an_expected_list_with_one_entry_removed(
    tmp_path: Path,
) -> None:
    """**期望清单**少一条（并同步改条数）时也必须报红——反方向同样要拦。

    只改期望清单而不改基线，两份对不上 → 红。**双向都拦**：不然"只改一份"
    可以挑软的那一侧下手。
    """
    baseline, expected = _consistent_pair(tmp_path)

    document = _read_json(expected)
    entries = document["entries"]
    removed = sorted(entries)[0]
    del entries[removed]
    document["meta"]["entryCount"] = len(entries)
    broken = _write_json(tmp_path / "broken_expected.json", document)

    result = _run_tool_bytes(
        "tools/contract_baseline.py",
        "--baseline",
        str(baseline),
        "--expected",
        str(broken),
    )

    raw = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"期望清单少了 {removed} 条并同步改了条数，工具却放行了：\n{raw!r}"
    )
    assert removed.encode("ascii") in raw, "工具报红了但没点名少掉的那一条"


def test_the_contract_baseline_rejects_a_digest_that_differs_between_the_two_lists(
    tmp_path: Path,
) -> None:
    """两份清单**同一条的哈希不一样**时必须报红（条数一样也不行）。"""
    baseline, expected = _consistent_pair(tmp_path)

    document = _read_json(expected)
    entries = document["entries"]
    changed = sorted(entries)[0]
    entries[changed] = "0" * 32
    broken = _write_json(tmp_path / "broken_expected.json", document)

    result = _run_tool_bytes(
        "tools/contract_baseline.py",
        "--baseline",
        str(baseline),
        "--expected",
        str(broken),
    )

    raw = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"期望清单里 {changed} 的哈希与基线不一样，工具却放行了：\n{raw!r}"
    )
    assert changed.encode("ascii") in raw, "工具报红了但没点名对不上的那一条"


def test_the_contract_expected_list_refresh_requires_a_reason(tmp_path: Path) -> None:
    """期望清单刷新**不带 `--reason`** 时必须拒绝，且不落盘。

    与基线刷新同一套纪律：**"要求改了没人拦"和"没有要求"是同一种失守**。
    """
    target = tmp_path / "contracts_expected.json"

    result = _run_tool(
        "tools/contract_baseline.py",
        "--update-expected",
        "--expected",
        str(target),
    )

    assert result.returncode != 0, (
        f"刷新期望清单没带 --reason，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )
    assert not target.exists(), "没带 --reason 却把期望清单写出来了"


# ---------------------------------------------------------------------------
# `bus/` 独立基线（M2-S5 · ADR-019）
# ---------------------------------------------------------------------------
#
# **封板的含义是"此后改动 `bus/` 任一文件必红"**——所以基线与**给它上牙齿的
# 门禁用例**必须同笔落笔，否则中间那一刻基线只是躺在仓库里的一份 JSON。
#
# 反向用例一律**喂一份故意改坏的临时基线**给工具（不复制任何哈希实现），
# 断言它退出码非 0——与本文件其余几节同一手法。


def test_the_bus_baseline_matches_the_current_bus_sources() -> None:
    """`bus/` 的内容与**独立基线**逐字节一致（封板之后不许随手改）。"""
    assert BUS_BASELINE.exists(), f"bus/ 独立基线不存在：{BUS_BASELINE}"

    result = _run_tool("tools/check_frozen_baseline.py", "--scope", "bus")

    assert result.returncode == 0, (
        f"bus/ 独立基线校验失败——总线封板后 bus/ 的内容变了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_bus_baseline_rejects_a_changed_file_hash(tmp_path: Path) -> None:
    """`bus/` 基线里**某个文件的哈希**被改动时必须报红（内容变了）。"""
    document = _read_json(BUS_BASELINE)
    files = document["files"]
    changed = sorted(files)[0]
    files[changed] = "0" * 32
    broken = _write_json(tmp_path / "bus_hashes.json", document)

    result = _run_tool(
        "tools/check_frozen_baseline.py",
        "--scope",
        "bus",
        "--baseline",
        str(broken),
    )

    assert result.returncode != 0, (
        f"bus/ 基线里 {changed} 的哈希被改了，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_bus_baseline_rejects_a_baseline_with_one_file_removed(tmp_path: Path) -> None:
    """`bus/` 基线**少一个文件**时必须报红。

    **同步改 `fileCount`**——这样红的是"文件消失"那一层，而不是自述块那一层
    （自述块那一层另有用例钉着）。
    """
    document = _read_json(BUS_BASELINE)
    files = document["files"]
    removed = sorted(files)[0]
    del files[removed]
    document["meta"]["fileCount"] = len(files)
    broken = _write_json(tmp_path / "bus_hashes.json", document)

    result = _run_tool(
        "tools/check_frozen_baseline.py",
        "--scope",
        "bus",
        "--baseline",
        str(broken),
    )

    assert result.returncode != 0, (
        f"bus/ 基线少了 {removed}，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_bus_baseline_rejects_a_tampered_file_count(tmp_path: Path) -> None:
    """`bus/` 基线**自述块里的文件数**被改动时必须报红。"""
    document = _read_json(BUS_BASELINE)
    document["meta"]["fileCount"] = 1
    broken = _write_json(tmp_path / "bus_hashes.json", document)

    result = _run_tool(
        "tools/check_frozen_baseline.py",
        "--scope",
        "bus",
        "--baseline",
        str(broken),
    )

    assert result.returncode != 0, (
        f"bus/ 基线自述的文件数被改了，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )


def test_the_bus_baseline_refresh_requires_a_reason(tmp_path: Path) -> None:
    """`bus/` 基线刷新**不带 `--reason`** 时必须拒绝，且不落盘。"""
    target = tmp_path / "bus_hashes.json"

    result = _run_tool(
        "tools/check_frozen_baseline.py",
        "--scope",
        "bus",
        "--update-baseline",
        "--baseline",
        str(target),
    )

    assert result.returncode != 0, (
        f"刷新 bus/ 基线没带 --reason，工具却放行了：\n{result.stdout}\n{result.stderr}"
    )
    assert not target.exists(), "没带 --reason 却把 bus/ 基线写出来了"


def test_the_bus_baseline_and_the_frozen_baseline_never_overlap() -> None:
    """**两份基线各管各的**——`bus/` 不许混进冻结区，冻结区也不许混进 `bus/`。

    ADR-019 的原话是"`bus/` 另列独立基线"，而 plan-M2 S5 的常见坑写着
    **"把 `bus/` 并进冻结区基线"**——这条用例就是那颗钉子。
    """
    bus_files = _read_json(BUS_BASELINE)["files"]
    frozen_files = _read_json(FROZEN_BASELINE)["files"]

    assert bus_files, "bus/ 基线一个文件都没有——它被清空了"
    assert all(key.startswith("bus/") for key in bus_files), sorted(bus_files)
    assert not [key for key in frozen_files if key.startswith("bus/")], (
        "bus/ 混进了冻结区基线——冻结区严格对齐 G4-1"
    )
    assert all(key.startswith(("kernel/", "rules/")) for key in frozen_files), sorted(frozen_files)
