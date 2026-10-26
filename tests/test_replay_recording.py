"""录像录制测试（M2-S8 · FR-6.1 · M2-D3 · ADR-030）。

覆盖 plan-M2.md S8 的验证方案：

1. **录的是被接受的命令**：有被拒命令的对局 → 录像里**只有被接受的**
   （被拒的那条**不在**录像里）；
2. **不可丢**：长局（数百条命令）→ **录像里的命令数 == 被接受的命令数**
   （逐条相等，不是"差不多"）；
3. **体积**：录像体积**显著小于**同样一局的逐帧快照（**明确的倍数关系**）；
4. **格式可读**：头里的种子与规则集标识**逐字等于**内核就绪事件里的值；
5. **落盘经 sink**：sink 收到**字节**，录制系统**一个文件都不碰**；
6. **未知版本**：喂一个不认识的头 → **明确报错**（不静默忽略、不按旧版解析）。

**第 1、2 条就是反向验证 A / B 的判据**：改成订阅 `command:received`、
或给录制加一个有界可丢队列，这两条必红。**文件名那一条是反向验证 C 的
判据**。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

import tetris_kernel.systems.replay as replay_package
from tetris_kernel.bus.bus import EventBus
from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.kernel.drafts import METADATA_SOURCE, EventDraft, make_draft
from tetris_kernel.systems.replay import (
    RECORDING_VERSION,
    RecordedCommand,
    Recording,
    RecordingSystem,
    recording_filename,
    recording_from_jsonl,
    recording_to_jsonl,
)

# ---------------------------------------------------------------------------
# 夹具与替身
# ---------------------------------------------------------------------------


def _bus(**kwargs: object) -> EventBus[object]:
    """一条不带视图的总线。"""
    return EventBus[object](**kwargs)  # type: ignore[arg-type]


def _accepted(
    command_type: str,
    frame: int = 1,
    source: str = "human",
    args: tuple[tuple[str, object], ...] = (),
) -> EventDraft:
    """一条**被接受**的命令（**来源在元数据里**——契约的负载不带 source）。"""
    return make_draft(
        "command:accepted",
        (("commandType", command_type), ("commandSeq", 0), ("args", args)),
        frame,
        ((METADATA_SOURCE, source),),
    )


def _received(
    command_type: str,
    frame: int = 1,
    source: str = "human",
    args: tuple[tuple[str, object], ...] = (),
) -> EventDraft:
    """一条**收到**的命令（**校验前**——含被拒的那些）。"""
    return make_draft(
        "command:received",
        (("commandType", command_type), ("source", source), ("args", args)),
        frame,
    )


def _rejected(command_type: str, frame: int = 1) -> EventDraft:
    """一条**被拒**的命令。"""
    return make_draft(
        "command:rejected",
        (
            ("commandType", command_type),
            ("reason", "not_started"),
            ("args", ()),
            ("mergedCount", 1),
        ),
        frame,
    )


def _tick(frame: int) -> EventDraft:
    return make_draft("clock:tick", (("frame", frame), ("stepMs", 50)), frame)


def _kernel_ready(seed: int = 0, rule: str = "standard", mode: str = "live") -> EventDraft:
    """一条**内核就绪**事件（**种子与规则集标识的权威来源**）。"""
    return make_draft(
        "kernel:ready",
        (("seed", seed), ("rule", rule), ("mode", mode)),
        0,
    )


def _game_over(frame: int = 1) -> EventDraft:
    return make_draft("game:over", (("score", 0), ("lines", 0), ("reason", "topout")), frame)


def _snapshot(frame: int) -> dict[str, object]:
    """一帧的逐帧快照（**体积对照用**——棋盘 20 行 × 10 列）。"""
    return {
        "frame": frame,
        "board": {"cells": [[0] * 10 for _ in range(20)]},
        "score": 0,
        "lines": 0,
        "piecesPlaced": 0,
        "active": None,
        "hold": None,
        "next": ["I", "O", "S"],
    }


# ---------------------------------------------------------------------------
# 录的是被接受的命令（M2-D3）
# ---------------------------------------------------------------------------


def test_the_rejected_command_is_not_in_the_recording() -> None:
    """**只有被接受的命令进录像**——被拒的那条**不在**录像里。

    **这条就是反向验证 A 的判据**：改成订阅 `command:received`（含被拒的），
    它必红。
    """
    bus = _bus()
    system = RecordingSystem(bus)

    bus.publish(_received("moveLeft"))
    bus.publish(_rejected("moveLeft"))
    bus.publish(_accepted("moveLeft", frame=2))

    commands = system.commands
    assert len(commands) == 1, "被拒的命令不该进录像"
    assert commands[0].type == "moveLeft"
    assert commands[0].frame == 2


def test_the_received_command_alone_records_nothing() -> None:
    """只发 `command:received`（没有对应的 accepted）→ **一条都不录**。"""
    bus = _bus()
    system = RecordingSystem(bus)

    bus.publish(_received("moveLeft"))
    bus.publish(_received("hardDrop"))

    assert system.commands == ()


# ---------------------------------------------------------------------------
# 不可丢（ADR-030）
# ---------------------------------------------------------------------------


def test_the_long_game_records_every_accepted_command() -> None:
    """长局（数百条命令）→ **录像里的命令数 == 被接受的命令数**（逐条相等）。"""
    bus = _bus()
    system = RecordingSystem(bus)
    accepted = 300

    for index in range(accepted):
        bus.publish(_accepted("moveLeft", frame=index // 3 + 1, source="human"))

    commands = system.commands
    assert len(commands) == accepted, "一条都不能少"
    for index, command in enumerate(commands):
        assert command.type == "moveLeft"
        assert command.frame == index // 3 + 1
        assert command.source == "human"


def test_the_order_of_the_recorded_commands_is_the_recording_order() -> None:
    """**顺序**也是录像的一部分——回放按原样派发，顺序变了状态就变了。"""
    bus = _bus()
    system = RecordingSystem(bus)

    bus.publish(_accepted("moveLeft", frame=1))
    bus.publish(_accepted("hardDrop", frame=1))
    bus.publish(_accepted("rotateCW", frame=2))

    assert [command.type for command in system.commands] == [
        "moveLeft",
        "hardDrop",
        "rotateCW",
    ]


# ---------------------------------------------------------------------------
# 体积
# ---------------------------------------------------------------------------


def test_the_recording_is_much_smaller_than_the_per_frame_snapshots() -> None:
    """录像体积**显著小于**同样一局的逐帧快照（**明确的倍数关系**）。"""
    frames = 100
    bus = _bus()
    system = RecordingSystem(bus)

    for frame in range(1, frames + 1):
        bus.publish(_accepted("moveLeft", frame=frame))

    recording_bytes = len(recording_to_jsonl(system.recording()).encode("utf-8"))
    snapshot_bytes = len(
        b"".join(json.dumps(_snapshot(frame)).encode("utf-8") for frame in range(frames))
    )

    assert recording_bytes * 5 < snapshot_bytes, (
        f"录像 {recording_bytes} 字节 vs 逐帧快照 {snapshot_bytes} 字节——"
        "体积优势不到 5 倍，不算'显著小于'"
    )


# ---------------------------------------------------------------------------
# 格式
# ---------------------------------------------------------------------------


def test_the_header_carries_the_seed_and_the_rule_verbatim() -> None:
    """**头里的种子与规则集标识逐字等于**内核就绪事件里的值。"""
    bus = _bus()
    system = RecordingSystem(bus)

    bus.publish(_kernel_ready(seed=20261026, rule="standard"))

    text = recording_to_jsonl(system.recording())
    header = json.loads(text.splitlines()[0])

    assert header["seed"] == 20261026
    assert header["rule"] == "standard"
    assert header["version"] == RECORDING_VERSION


def test_the_recording_round_trips_through_jsonl() -> None:
    """录像 → JSON Lines → 录像，**逐字相等**（参数按契约还原成元组）。"""
    recording = Recording(
        seed=7,
        rule="standard",
        commands=(
            RecordedCommand(frame=3, type="moveLeft", args=(), source="human"),
            RecordedCommand(frame=4, type="setLevel", args=(("level", 5),), source="ai"),
            RecordedCommand(
                frame=5,
                type="injectGarbage",
                args=(("rows", 2), ("holes", (3, 5))),
                source="human",
            ),
            RecordedCommand(
                frame=6,
                type="writeCells",
                args=(("cells", ((1, 2, 1), (3, 4, 1))),),
                source="replay",
            ),
        ),
    )

    restored = recording_from_jsonl(recording_to_jsonl(recording))

    assert restored == recording


def test_the_source_is_passed_through_verbatim() -> None:
    """**来源透传原值**（ADR-062）——回放时**不改写成 `replay`**。"""
    bus = _bus()
    system = RecordingSystem(bus)

    bus.publish(_accepted("moveLeft", source="ai"))
    bus.publish(_accepted("moveLeft", source="tutorial"))

    assert [command.source for command in system.commands] == ["ai", "tutorial"]


def test_an_unknown_version_is_rejected_with_a_clear_error() -> None:
    """**不认识的头 → 明确报错**（不静默忽略、不按旧版解析）。"""
    with pytest.raises(ValueError, match="版本"):
        recording_from_jsonl('{"rule":"standard","seed":1,"version":"v99"}\n')


def test_an_empty_recording_is_rejected() -> None:
    """空录像当场报错——不糊弄。"""
    with pytest.raises(ValueError):
        recording_from_jsonl("")


def test_an_unknown_command_type_is_rejected() -> None:
    """录像里有**未登记的命令类型** → 当场报错（不猜）。"""
    text = (
        '{"rule":"standard","seed":1,"version":"v1"}\n'
        '{"args":{},"frame":1,"source":"x","type":"fly"}\n'
    )

    with pytest.raises(ValueError, match="未登记"):
        recording_from_jsonl(text)


# ---------------------------------------------------------------------------
# 文件名（不带墙钟 · ADR-064）
# ---------------------------------------------------------------------------


def test_the_filename_is_a_pure_function_without_a_wall_clock() -> None:
    """**文件名不带墙钟**（ADR-064）——同一局录两次产生**同一份**。

    **这条就是反向验证 C 的判据**：改成带时间戳，它必红。
    """
    name = recording_filename("standard")

    assert name == recording_filename("standard"), "文件名必须是纯函数"
    assert not re.search(r"\d{8}", name), f"文件名带时间戳了：{name}"
    assert not re.search(r"\d{4}-\d{2}-\d{2}", name), f"文件名带日期了：{name}"
    assert "standard" in name, "文件名要能看出是哪个规则集的"


def test_the_filename_does_not_carry_the_seed() -> None:
    """**文件名不带种子**（契约里 `replay:recording-started` 的 `seed` 字段明写）。"""
    name = recording_filename("standard")

    assert "20261026" not in name


# ---------------------------------------------------------------------------
# 落盘经 sink（M2-D2 · ADR-064）
# ---------------------------------------------------------------------------


def test_the_sink_receives_the_bytes() -> None:
    """落盘经 sink：sink 收到的是**字节**（一行一条 JSON）。"""
    captured: list[bytes] = []
    bus = _bus()
    system = RecordingSystem(bus, captured.append)

    bus.publish(_accepted("moveLeft", frame=3))
    system.flush()

    assert len(captured) == 1
    assert isinstance(captured[0], bytes), "交给 sink 的必须是字节"
    lines = captured[0].decode("utf-8").splitlines()
    assert len(lines) == 2, "第一行是头，第二行是那条命令"
    assert json.loads(lines[1])["type"] == "moveLeft"


def test_the_recording_system_never_touches_the_filesystem(tmp_path: Path) -> None:
    """落盘只走 sink：录制系统**一个文件都不碰**（路径是宿主的事）。"""
    before = set(tmp_path.rglob("*"))
    captured: list[bytes] = []
    bus = _bus()
    system = RecordingSystem(bus, captured.append)

    bus.publish(_accepted("moveLeft"))
    system.flush()

    assert set(tmp_path.rglob("*")) == before, "录制系统碰了文件系统"
    assert len(captured) == 1, "字节确实交给了 sink"


def test_the_recording_package_does_not_import_the_filesystem_or_the_kernel_state() -> None:
    """落盘只走 sink（M2-D2）· 只订阅事件不碰内核状态（M2-D1）。

    **机制级**：AST 扫描 `systems/replay` 的**全部源文件**——

    - 不 import `pathlib` / `os` / `shutil` / `io`（自己 `open()` 就等于
      自己管路径）；
    - 不 import `kernel.core` / `kernel.snapshot`（内核状态的地盘）。
    """
    package_dir = Path(replay_package.__file__).parent
    imported: set[str] = set()
    for source_path in sorted(package_dir.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported |= _imported_modules(tree, package="tetris_kernel.systems.replay")

    roots = {name.split(".", 1)[0] for name in imported}
    assert not roots & {"pathlib", "os", "shutil", "io"}, f"录制系统碰了文件系统：{sorted(roots)}"
    assert "tetris_kernel.kernel.core" not in imported, "录制系统 import 了内核状态"
    assert "tetris_kernel.kernel.snapshot" not in imported, "录制系统 import 了内核快照"


def _imported_modules(tree: ast.AST, package: str) -> set[str]:
    """AST 里出现的**绝对**模块名（相对导入按包路径还原）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(_absolute_module(node, package))
    return names


def _absolute_module(node: ast.ImportFrom, package: str) -> str:
    """把相对导入还原成绝对模块名（`...bus.bus` → `tetris_kernel.bus.bus`）。"""
    if node.level == 0:
        return node.module or ""
    parts = package.split(".")
    base = parts[: len(parts) - (node.level - 1)]
    if node.module:
        base = [*base, *node.module.split(".")]
    return ".".join(base)


# ---------------------------------------------------------------------------
# 周期点与局末
# ---------------------------------------------------------------------------


def test_the_checkpoint_writes_the_recording_at_the_fixed_interval() -> None:
    """**周期点写死**：每 600 帧落一次盘（写死 = 各处实现一致）。

    **周期点落在帧边界**：`clock:tick` 在**帧首**派发（ADR-022），所以第 600
    帧的 tick 触发落盘时，**本帧的命令还没派发**——落的是**前 599 帧**的
    命令。真实帧循环就是这个顺序（先 tick 后命令）。
    """
    captured: list[bytes] = []
    bus = _bus()
    RecordingSystem(bus, captured.append, checkpoint_interval=600)

    for frame in range(1, 601):
        bus.publish(_tick(frame))
        bus.publish(_accepted("moveLeft", frame=frame))

    assert len(captured) == 1, "第 600 帧落一次盘"
    recorded = recording_from_jsonl(captured[0].decode("utf-8"))
    assert len(recorded.commands) == 599, "落的是当前这份完整录像（本帧命令还没派发）"


def test_the_checkpoint_fires_again_at_the_next_multiple() -> None:
    """**周期点是固定间隔**——第 1200 帧再落一次。"""
    captured: list[bytes] = []
    bus = _bus()
    RecordingSystem(bus, captured.append, checkpoint_interval=600)

    for frame in range(1, 1201):
        bus.publish(_tick(frame))
        bus.publish(_accepted("moveLeft", frame=frame))

    assert len(captured) == 2, "第 600 帧与第 1200 帧各落一次"


def test_the_checkpoint_interval_is_validated() -> None:
    """域外值当场报错。"""
    bus = _bus()

    with pytest.raises(ValueError):
        RecordingSystem(bus, None, checkpoint_interval=0)


def test_the_game_over_flushes_and_stops_the_recording() -> None:
    """**局末落盘**并发出 `replay:recording-stopped`；之后不再录。"""
    captured: list[bytes] = []
    received: list[Envelope] = []
    bus = _bus()
    bus.subscribe("replay:recording-stopped", received.append)
    system = RecordingSystem(bus, captured.append)

    bus.publish(_tick(10))
    bus.publish(_accepted("moveLeft", frame=10))
    bus.publish(_game_over(frame=10))

    assert len(captured) == 1, "局末落一次盘"
    assert len(received) == 1
    payload = dict(received[0].payload)
    assert payload["commandCount"] == 1, "录到的命令总数"
    assert payload["frameCount"] == 10, "录到的帧数"
    assert system.stopped

    bus.publish(_accepted("moveLeft", frame=11))
    assert len(system.commands) == 1, "局末之后不再录"


def test_the_recording_started_event_is_emitted_when_the_kernel_is_ready() -> None:
    """**内核就绪时**发出 `replay:recording-started`（种子 + 规则集标识）。"""
    bus = _bus()
    received: list[Envelope] = []
    bus.subscribe("replay:recording-started", received.append)

    RecordingSystem(bus)
    bus.publish(_kernel_ready(seed=99, rule="standard"))

    assert len(received) == 1
    payload = dict(received[0].payload)
    assert payload["seed"] == 99
    assert payload["rule"] == "standard"


def test_the_recording_system_publishes_nothing_on_construction() -> None:
    """**构造时一个事件都不发**——装配层不该自己造事件（宿主的一条不变量）。

    **这条就是"录制器改从 `kernel:ready` 取值"这条改动的判据**：把它改回
    构造即发 `replay:recording-started`，它必红。
    """
    bus = _bus()
    received: list[str] = []
    bus.subscribe("*", lambda envelope: received.append(envelope.type))

    RecordingSystem(bus)

    assert received == [], f"构造时就发了事件：{received}"
    assert bus.last_seq == 0, "构造时占了总线序号"
