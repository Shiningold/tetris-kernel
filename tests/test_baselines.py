"""三份基线的门禁（S17 冻结的两道裁判 + S16 的事件数行为基线 · 批次 D 收口与修订）。

## 为什么要这三道

`tools/` 下那三份基线（契约 v1 字段级哈希 · 冻结区 `kernel/` + `rules/` 内容哈希 ·
每帧事件数）**是工具**：没人跑它，它就只是躺在仓库里的一份 JSON。基线纪律要成立，
就得**每一次跑测试都跑一遍它**。

- **契约基线**（`tools/contract_baseline.py`）：契约面**只增不改**——新增键放行，
  **已有键的哈希变化或消失报红**（改名 / 改型 / 删字段 = major 变更）；
- **冻结区基线**（`tools/check_frozen_baseline.py`）：`kernel/` + `rules/` 的
  **内容哈希**——变体落地时这两个目录的差异必须为空，**改一行注释也该红**；
- **事件数基线**（`tools/measure_frame_cost.py --baseline-check`）：固定场景下
  **逐帧发出多少条事件**——契约基线只看**声明**、冻结区基线只看**文件内容**，
  **行为恰好落在两者的缝里**（批次 D 审查 D5）。`bus/` 与 `host/` **不在冻结区**，
  宿主层多发一条事件，声明面与文件哈希面都看不出来。

## 每道门都要有**反向用例**

只断言"跑一遍工具，退出码是 0"证明的是"此刻基线与代码一致"，**没有证明"基线与
代码不一致时工具会拒绝"**。把 `compare()` 改成恒不报红、把冻结区的差异判定改成
恒假，**全套 pytest 仍然全绿**——这正是批次 D 审查 D2 抓到的洞。

所以这里**喂一份故意改坏的临时基线给工具，断言它退出码非 0**：测的是"工具会
拒绝"，**不复制任何哈希实现**（抄一份就是影子副本，两处会悄悄分叉——`ai_docs`
里否掉的正是这个方案，否掉的是"抄实现"，不是"验行为"）。

**跑的是工具本身**（子进程调 `python tools/xxx.py`），不是把逻辑抄一份到测试里。
"""

from __future__ import annotations

import json
import locale
import subprocess
import sys
from pathlib import Path

#: 仓库根目录（`tests/` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 三份基线文件（**必须存在**——否则工具会"基线尚未生成"然后放行，守卫等于没跑）。
CONTRACT_BASELINE = REPO_ROOT / "tests" / "baseline" / "contracts_baseline.json"
FROZEN_BASELINE = REPO_ROOT / "tests" / "baseline" / "kernel_hashes.json"
FRAME_EVENTS_BASELINE = REPO_ROOT / "tests" / "baseline" / "frame_events.json"


def _run_tool(relative_path: str, *args: str) -> subprocess.CompletedProcess[str]:
    """跑 `tools/` 下的一个脚本，返回解码好的结果。

    **按本机首选编码解码**（Windows 上管道走本地代码页，本机是 GBK）——用 UTF-8
    硬解会把中文提示变成乱码，报错信息本身报错。
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / relative_path), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
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
