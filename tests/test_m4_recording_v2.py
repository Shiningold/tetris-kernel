"""M4-S10 · 录像格式 v2 + 变体局第五项比对。

对应门判据 **`G4-2`**（变体局的回放校验多比一项：结构指纹之外另比对**扩展
指纹**；**旧 v1 录像仍能读**）。

五条验证（plan-M4 S10 的验证方案）：

1. **v1 录像样例** → 读入成功 → 四项比对正常 → **输出标明"无变体第五项"**；
2. **v2 往返**：录 → 存 → 读 → **五项全等**；
3. **变体局**：录像回放 → **五项全等**；**故意让扩展状态分叉 → 报"第五项
   不等"**；
4. **坏版本号** → 明确报错；
5. **AC-1 回归**：标准局四项全等（**旧判据不退化**）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tetris_kernel.host.assembly import assemble
from tetris_kernel.host.builtin_variants import register_builtin_variants
from tetris_kernel.host.cli import main
from tetris_kernel.host.discovery import system_catalog
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.host.replay import EMPTY_EXTENSION_FINGERPRINT
from tetris_kernel.kernel.commands import Command
from tetris_kernel.systems.replay import (
    LEGACY_RECORDING_VERSION,
    RECORDING_VERSION,
    Recording,
    RecordingSystem,
    recording_from_jsonl,
    recording_to_jsonl,
)

#: 变体局那一局的种子。
VARIANT_SEED = 20261101

#: 标准局那一局的种子。
STANDARD_SEED = 20261102

#: 最多跑多少帧（**够玩到顶出**）。
MAX_FRAMES = 4000


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------


def _play_variant_game(seed: int = VARIANT_SEED) -> Recording:
    """**真跑一局变体 B（落地后生长）**，返回录下来的录像。

    **变体 B 的规则集就是标准的**（玩法差异来自它挂的 `growth` 系统）→
    **录像头必须记变体标识**——**变体标识 ≠ 规则集标识**。
    """
    register_builtin_variants()
    assembly = assemble(
        seed=seed,
        rule_id="standard",
        systems=("growth",),
        system_catalog=system_catalog(),
        variant_id="growth",
    )
    recorder = _recorder_of(assembly)
    loop = HeadlessLoop(assembly)
    loop.start()
    kernel = assembly.kernel
    while kernel.phase != "game_over" and loop.frames < MAX_FRAMES:
        kernel.submit(Command(type="softDrop", source="human"))
        loop.run_frame()
    return recorder.recording()


def _play_standard_game(seed: int = STANDARD_SEED) -> Recording:
    """**真跑一局标准玩法**，返回录下来的录像。"""
    assembly = assemble(seed=seed, rule_id="standard")
    recorder = _recorder_of(assembly)
    loop = HeadlessLoop(assembly)
    loop.start()
    kernel = assembly.kernel
    while kernel.phase != "game_over" and loop.frames < MAX_FRAMES:
        kernel.submit(Command(type="softDrop", source="human"))
        loop.run_frame()
    return recorder.recording()


def _recorder_of(assembly: object) -> RecordingSystem[object]:
    """从装配结果里挑出录制系统。"""
    systems = [
        system
        for system in assembly.observable_systems  # type: ignore[attr-defined]
        if isinstance(system, RecordingSystem)
    ]
    assert systems, "录像系统没装上"
    return systems[0]


def _write(path: Path, recording: Recording) -> Path:
    path.write_text(recording_to_jsonl(recording), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 2 · v2 往返
# ---------------------------------------------------------------------------


def test_m4_recording_v2_the_written_recording_is_always_v2() -> None:
    """**写出永远是 v2**（M4-S10）——v1 只读不写。"""
    recording = _play_standard_game()
    header = json.loads(recording_to_jsonl(recording).splitlines()[0])
    assert header["version"] == RECORDING_VERSION == "v2"
    assert LEGACY_RECORDING_VERSION == "v1"


def test_m4_recording_v2_the_header_carries_the_variant_identifier() -> None:
    """**录像头带变体标识**（M4-S10）——**变体标识 ≠ 规则集标识**。"""
    recording = _play_variant_game()
    header = json.loads(recording_to_jsonl(recording).splitlines()[0])
    assert header["variant"] == "growth", "录像头没记变体标识——那一局装不回去"
    assert header["rule"] == "standard", "变体 B 的规则集就是标准的"


def test_m4_recording_v2_the_settlement_carries_the_fifth_item() -> None:
    """**结算结构带第五项**（扩展指纹 · M4-S10）。"""
    recording = _play_variant_game()
    assert recording.extension_fingerprint is not None, "录像没有第五项"
    header = json.loads(recording_to_jsonl(recording).splitlines()[0])
    assert header["outcome"]["extensionFingerprint"] == recording.extension_fingerprint


def test_m4_recording_v2_a_round_trip_keeps_all_five_items() -> None:
    """**v2 往返**：录 → 存 → 读 → **五项全等**（M4-S10）。"""
    recording = _play_variant_game()
    restored = recording_from_jsonl(recording_to_jsonl(recording))
    assert restored.outcome == recording.outcome
    assert restored.extension_fingerprint == recording.extension_fingerprint
    assert restored.variant == recording.variant
    assert restored.version == "v2"


# ---------------------------------------------------------------------------
# 1 · v1 兼容
# ---------------------------------------------------------------------------


def test_m4_recording_v2_a_v1_recording_is_still_readable(tmp_path: Path) -> None:
    """**v1 录像仍能读**（M4-S10 · NFR-9）——**按「无变体」处理，不是拒绝**。

    **明确处理 = 不静默损坏**：输出里标出"该录像无变体第五项"。
    """
    path = tmp_path / "legacy.jsonl"
    path.write_text(
        json.dumps(
            {
                "version": "v1",
                "seed": 7,
                "rule": "standard",
                "outcome": {"fingerprint": "fp", "score": 3, "lines": 1, "piecesPlaced": 2},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    recording = recording_from_jsonl(path.read_text(encoding="utf-8"))

    assert recording.version == "v1", "版本号要记进结构"
    assert recording.variant is None, "v1 录像没有变体"
    assert recording.extension_fingerprint is None, "v1 录像没有第五项"
    assert recording.outcome is not None
    assert recording.outcome.fingerprint == "fp"


def test_m4_recording_v2_the_v1_output_marks_the_missing_fifth_item(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**输出里标出"该录像无变体第五项"**（NFR-9 的明确处理）。"""
    path = tmp_path / "legacy.jsonl"
    path.write_text(
        json.dumps(
            {
                "version": "v1",
                "seed": 7,
                "rule": "standard",
                "outcome": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    code = main(["replay", str(path)])

    output = capsys.readouterr().out
    assert code == 2, "没有基准就是没做成校验（退出码 2）"
    assert "无变体第五项" in output, "输出没标出「该录像无变体第五项」"


# ---------------------------------------------------------------------------
# 4 · 坏版本号
# ---------------------------------------------------------------------------


def test_m4_recording_v2_an_unknown_version_is_rejected(tmp_path: Path) -> None:
    """**未知版本 → 明确报错**（不许静默当 v1）。"""
    path = tmp_path / "broken.jsonl"
    path.write_text('{"rule":"standard","seed":1,"version":"v99"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="不认识录像格式版本"):
        recording_from_jsonl(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 3 · 变体局：五项全等 / 第五项不等
# ---------------------------------------------------------------------------


def test_m4_recording_v2_a_variant_game_replays_to_the_same_five_items(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**变体局回放 → 五项全等**（M4-S10 · `G4-2`）。

    **回放时装上变体**——否则扩展槽是空的 → **第五项必然不等**。
    """
    recording = _play_variant_game()
    path = _write(tmp_path / "variant.jsonl", recording)

    code = main(["replay", str(path)])

    output = capsys.readouterr().out
    assert code == 0, f"变体局回放不等：\n{output}"
    assert "五项比对：全等" in output
    assert "第五项（扩展指纹）不等" not in output


def test_m4_recording_v2_a_diverged_extension_state_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**扩展状态分叉 → 明确指认"第五项不等"**（不能笼统说"四项不等"）。"""
    recording = _play_variant_game()
    # **故意让扩展状态分叉**：把录像里的第五项改成别的值。
    diverged = Recording(
        seed=recording.seed,
        rule=recording.rule,
        commands=recording.commands,
        outcome=recording.outcome,
        extension_fingerprint="deadbeef" * 4,
        variant=recording.variant,
        version=recording.version,
    )
    path = _write(tmp_path / "diverged.jsonl", diverged)

    code = main(["replay", str(path)])

    output = capsys.readouterr().out
    assert code == 1, "第五项不等就是不等（退出码 1）"
    assert "五项比对：不等" in output
    assert "第五项（扩展指纹）不等" in output, "没指认到第五项——让人猜谜"


def test_m4_recording_v2_a_variant_game_without_the_variant_fails_the_fifth_item(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**变体没装上 → 第五项不等**（这条守的是"装上变体"这件事真的有用）。

    **把录像头的变体标识抹掉** → 回放按标准玩法起 → 扩展槽是空的 →
    **第五项必然不等**——**而前四项可能全等**（**结构指纹不含扩展字段**）。
    """
    recording = _play_variant_game()
    stripped = Recording(
        seed=recording.seed,
        rule=recording.rule,
        commands=recording.commands,
        outcome=recording.outcome,
        extension_fingerprint=recording.extension_fingerprint,
        variant=None,
        version=recording.version,
    )
    path = _write(tmp_path / "stripped.jsonl", stripped)

    code = main(["replay", str(path)])

    output = capsys.readouterr().out
    assert code == 1
    assert "第五项（扩展指纹）不等" in output


# ---------------------------------------------------------------------------
# 5 · AC-1 回归：标准局四项不变
# ---------------------------------------------------------------------------


def test_m4_recording_v2_the_standard_game_still_replays_to_equal_items(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**标准局四项全等**（AC-1 回归 · M4-S10 不许让旧判据退化）。

    **第五项也全等**——标准局没有变体 → 扩展槽是空的 → **恒等**。
    """
    recording = _play_standard_game()
    path = _write(tmp_path / "standard.jsonl", recording)

    code = main(["replay", str(path)])

    output = capsys.readouterr().out
    assert code == 0, f"标准局回放不等：\n{output}"
    assert "五项比对：全等" in output
    assert recording.extension_fingerprint == EMPTY_EXTENSION_FINGERPRINT, (
        "标准局没有变体 → 第五项就是空槽的摘要"
    )
