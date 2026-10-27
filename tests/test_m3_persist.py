"""命令行落盘接线测试（M3-S9 · ADR-074 / ADR-085）。

覆盖 plan-M3.md S9 的验证方案：

1. **默认不落盘**：不带 `--persist-*` → **一个文件都不产生**（**既有调用的
   默认行为一个字节都不变**）；
2. **三个产物各有各的出口**：三个开关都给 → **三个文件都出现** · 内容非空；
3. **语义正确**：日志与指标**追加**（跑两局 → 行数累积）· 录像**覆盖**
   （跑两局 → 文件**逐字节相同**，不是两份拼在一起）；
4. **分别可控**：只给一个开关 → **只有那一个产物**落盘。

**落盘路径沿用既有策略**（项目优先 → 不可写时回落 home · ADR-074）——
测试用 `monkeypatch.chdir` 把"项目目录"指到临时目录，**不另起一套路径**。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tetris_kernel.host.cli import main

#: 数据目录名（宿主层的默认落盘位置 · ADR-074）。
DATA_DIR = ".tetris-data"

#: 跑多少帧（**录像每 600 帧落一次盘** · 周期点写死在 spec §7.2）。
FRAMES = 700


def _data_dir(root: Path) -> Path:
    """临时目录下的数据目录。"""
    return root / DATA_DIR


def _log_lines(root: Path) -> int:
    """日志文件的行数（**追加语义**看这个）。"""
    text = (_data_dir(root) / "log-standard.jsonl").read_text(encoding="utf-8")
    return len([line for line in text.splitlines() if line])


def _metrics_lines(root: Path) -> int:
    """指标文件的行数（**追加语义**看这个）。"""
    text = (_data_dir(root) / "metrics-standard.jsonl").read_text(encoding="utf-8")
    return len([line for line in text.splitlines() if line])


def _recording_bytes(root: Path) -> bytes:
    """录像文件的字节（**覆盖语义**看这个）。"""
    return (_data_dir(root) / "recording-standard.jsonl").read_bytes()


def test_the_default_run_writes_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**默认不落盘**：不带任何 `--persist-*` → **一个文件都不产生**。"""
    monkeypatch.chdir(tmp_path)

    assert main(["sim", "--frames", "5"]) == 0

    assert list(tmp_path.rglob("*.jsonl")) == [], "默认就落盘了"


def test_the_three_products_each_get_their_own_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """三个开关都给 → **三个文件都出现**，内容非空（**各走各的出口**）。"""
    monkeypatch.chdir(tmp_path)

    code = main(
        [
            "sim",
            "--frames",
            str(FRAMES),
            "--persist-log",
            "--persist-metrics",
            "--persist-recording",
        ]
    )

    assert code == 0
    data = _data_dir(tmp_path)
    assert (data / "log-standard.jsonl").stat().st_size > 0
    assert (data / "metrics-standard.jsonl").stat().st_size > 0
    assert (data / "recording-standard.jsonl").stat().st_size > 0


def test_the_log_and_metrics_append_while_the_recording_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**两种语义是两个类**（ADR-085）：日志与指标**追加** · 录像**覆盖**。

    **同一局跑两遍**（同种子 → 内容逐字节相同）：
    - 日志 / 指标的行数**累积**（追加）；
    - 录像的字节**一模一样**（覆盖——**追加的话文件会变成两份拼在一起**）。
    """
    monkeypatch.chdir(tmp_path)
    argv = [
        "sim",
        "--frames",
        str(FRAMES),
        "--persist-log",
        "--persist-metrics",
        "--persist-recording",
    ]

    assert main(argv) == 0
    first_log = _log_lines(tmp_path)
    first_metrics = _metrics_lines(tmp_path)
    first_recording = _recording_bytes(tmp_path)

    assert main(argv) == 0

    assert _log_lines(tmp_path) == first_log * 2, "日志没有追加"
    assert _metrics_lines(tmp_path) == first_metrics * 2, "指标没有追加"
    assert _recording_bytes(tmp_path) == first_recording, "录像不是覆盖语义（文件变长了）"


def test_each_product_is_controllable_on_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**三个产物分别可控**：只给一个开关 → **只有那一个产物**落盘。"""
    monkeypatch.chdir(tmp_path)

    assert main(["sim", "--frames", "5", "--persist-log"]) == 0

    files = sorted(path.name for path in _data_dir(tmp_path).glob("*.jsonl"))
    assert files == ["log-standard.jsonl"], f"落盘的不是日志一个：{files}"


def test_the_recording_file_is_a_readable_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """录像文件**能被读回来**（一行一条的 JSONL · 录像格式的一部分）。"""
    monkeypatch.chdir(tmp_path)

    main(["sim", "--frames", str(FRAMES), "--persist-recording"])

    lines = [
        json.loads(line)
        for line in (_data_dir(tmp_path) / "recording-standard.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert lines, "录像文件是空的"
    assert "seed" in lines[0], "录像头缺种子"
