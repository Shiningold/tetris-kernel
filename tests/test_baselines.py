"""两份基线的门禁（S17 冻结的两道裁判接进测试套件 · 批次 D 收口）。

## 为什么要这两条

`tools/` 下那两份基线（契约 v1 字段级哈希 · 冻结区 `kernel/` + `rules/` 内容哈希）
**是工具**：没人跑它，它就只是躺在仓库里的一份 JSON。基线纪律要成立，就得
**每一次跑测试都跑一遍它**。

- **契约基线**（`tools/contract_baseline.py`）：契约面**只增不改**——新增键放行，
  **已有键的哈希变化或消失报红**（改名 / 改型 / 删字段 = major 变更）；
- **冻结区基线**（`tools/check_frozen_baseline.py`）：`kernel/` + `rules/` 的
  **内容哈希**——变体落地时这两个目录的差异必须为空，**改一行注释也该红**。

## 冻结区那条补的是哪个洞

批次 C 的外部审查发现：给 `Kernel.__init__` 加两个普通属性，**全套测试仍然全绿**
——契约层的守卫只看**声明**，看不出"内核里多了一个字段"（属性是不是"状态"没法从
形状上判断）。真正能抓住它的是**冻结区内容哈希**：内核是冻结区，内容一变就红。

**跑的是工具本身**（子进程调 `python tools/xxx.py`），不是把哈希逻辑抄一份到测试
里——抄一份就是两处会悄悄分叉的副本，基线改了它没跟着改，守卫就成了摆设。
"""

from __future__ import annotations

import locale
import subprocess
import sys
from pathlib import Path

#: 仓库根目录（`tests/` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 两份基线文件（**必须存在**——否则工具会"基线尚未生成"然后放行，守卫等于没跑）。
CONTRACT_BASELINE = REPO_ROOT / "tests" / "baseline" / "contracts_baseline.json"
FROZEN_BASELINE = REPO_ROOT / "tests" / "baseline" / "kernel_hashes.json"


def _run_tool(relative_path: str) -> subprocess.CompletedProcess[str]:
    """跑 `tools/` 下的一个脚本，返回解码好的结果。

    **按本机首选编码解码**（Windows 上管道走本地代码页，本机是 GBK）——用 UTF-8
    硬解会把中文提示变成乱码，报错信息本身报错。
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / relative_path)],
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
