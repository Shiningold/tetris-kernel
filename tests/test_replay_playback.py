"""录像回放测试（M2-S9 · FR-6.2 · FR-6.3 · AC-1 · ADR-033）。

覆盖 plan-M2.md S9 的验证方案：

1. **四项全等**：录一局 → 重放 → **四项逐条相等**（不是"最终态差不多"）；
2. **中途开始**：从第 N 帧开始 → **跑到终点的四项与从头重放相同**；
3. **逐帧步进**：步进到第 K 帧 → 此时的**棋盘指纹 ==** 从头跑到第 K 帧的指纹；
4. **暂停**：**宿主级暂停**（ADR-033）——帧号冻结 · **不产生任何命令**；
5. **反向验证 A**：重放时**跳过一条命令** → 四项比对**必须红**；
6. **反向验证 B**：把中途开始改成"从存档跳过去" → 用例**必须红**（判据是
   "中途开始必须**从头重放**"——派发过的命令条数逐条相等）；
7. **反向验证 C**：给步进**新增一条命令** → **命令目录条数断言**必须红
   （§6.2：M2 不新增命令）；
8. **CLI**：`tetris replay <文件>` 真跑并输出四项比对。

**第 6、7 条的判据是本文件里的两条断言**，它们与实现无关：
"中途开始派发过的命令条数 == 从头到第 N 帧的命令条数"与"命令目录仍 17 条"。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tetris_kernel.bus.bus import EventBus
from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.contracts.commands import COMMAND_TYPES, COMMANDS
from tetris_kernel.host.assembly import assemble, rule_set_of
from tetris_kernel.host.cli import main
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.host.replay import REPLAY_LIMITS, KernelReplayDriver
from tetris_kernel.kernel.commands import Command
from tetris_kernel.rules.standard import STANDARD_RULESET
from tetris_kernel.systems.replay import (
    FourItems,
    PlaybackSession,
    RecordedCommand,
    Recording,
    RecordingSystem,
    recording_from_jsonl,
    recording_to_jsonl,
)

# ---------------------------------------------------------------------------
# 夹具与替身
# ---------------------------------------------------------------------------


class FakeDriver:
    """回放驱动器的测试替身（**只记派发了什么**）。

    **它不重放任何东西**——所以它能回答"中途开始到底从头重放了没有"。
    """

    def __init__(self, items: FourItems | None = None) -> None:
        self.dispatched: list[RecordedCommand] = []
        self.advanced = 0
        self._items = items or FourItems(fingerprint="fp", score=0, lines=0, pieces_placed=0)

    def dispatch(self, commands: tuple[RecordedCommand, ...]) -> None:
        self.dispatched.extend(commands)

    def advance_frame(self) -> None:
        self.advanced += 1

    def four_items(self) -> FourItems:
        return self._items


class CapturingDriver:
    """包一层真实驱动器，**记下到底派发了什么**。"""

    def __init__(self, inner: KernelReplayDriver) -> None:
        self._inner = inner
        self.dispatched: list[RecordedCommand] = []

    def dispatch(self, commands: tuple[RecordedCommand, ...]) -> None:
        self.dispatched.extend(commands)
        self._inner.dispatch(commands)

    def advance_frame(self) -> None:
        self._inner.advance_frame()

    def four_items(self) -> FourItems:
        return self._inner.four_items()


def _bus() -> EventBus[object]:
    return EventBus[object]()


def _recording(commands: tuple[RecordedCommand, ...], *, outcome: FourItems | None = None):
    return Recording(seed=7, rule="standard", commands=commands, outcome=outcome)


def _command(frame: int, command_type: str = "moveLeft", source: str = "human"):
    return RecordedCommand(frame=frame, type=command_type, args=(), source=source)


def _four_items(snapshot: object) -> FourItems:
    """快照 → 四项（**宿主的活**：系统层不碰内核状态）。"""
    board = snapshot.board  # type: ignore[attr-defined]
    return FourItems(
        fingerprint=board.structureFingerprint,
        score=snapshot.score,  # type: ignore[attr-defined]
        lines=snapshot.lines,  # type: ignore[attr-defined]
        pieces_placed=snapshot.piecesPlaced,  # type: ignore[attr-defined]
    )


def _record_a_game(seed: int, frames: int) -> tuple[Recording, FourItems]:
    """**真跑一局**并录下来（真内核 · 真命令 · 真录像）。

    **用 `writeCells`**：受控写入立刻改棋盘（受控写入是外部改变棋盘的**唯一**
    合法通道）——几帧之内就有确定的内容，**四项全等才有区分力**（两边都是空
    棋盘的话，用例等于没测）。

    **可观测总开关关掉**：这条**自己造**一个录制系统，装配层再装一个就成了
    两个录制器同时录同一局——没必要的重复。

    **种子与规则集标识从 `kernel:ready` 取**：录制器在 `HeadlessLoop` 构造
    之前就订阅好了，`kernel:ready` 一到，录像头里的种子与规则集标识就位。
    """
    assembly = assemble(seed=seed, mode="live", observability_enabled=False)
    recorder = RecordingSystem(assembly.bus, None)
    loop = HeadlessLoop(assembly)
    loop.start()
    for index in range(frames):
        # **每帧写不同的行**——写同样的格子会被后几帧覆盖，棋盘就"回到原样"，
        # 四项全等就分不出"少了一条命令"与"没少"。
        row = 19 - index
        cells = tuple((x, row, 1) for x in range(5))
        assembly.kernel.submit(Command(type="writeCells", args=(("cells", cells),), source="human"))
        loop.run_frame()
    outcome = _four_items(assembly.kernel.snapshot())
    recorder.set_outcome(outcome)
    return recorder.recording(), outcome


def _replay(recording: Recording) -> FourItems:
    """**真重放**：按录像头的种子与规则集起一个新内核，原样派发命令。"""
    assembly = assemble(
        seed=recording.seed,
        rule_id=recording.rule,
        mode="replay",
        limits=REPLAY_LIMITS,
        observability_enabled=False,
    )
    driver = KernelReplayDriver(assembly.kernel, assembly.bus)
    session = PlaybackSession(assembly.bus, driver, recording)
    return session.run()


# ---------------------------------------------------------------------------
# 四项全等（AC-1）
# ---------------------------------------------------------------------------


def test_the_replayed_four_items_equal_the_original() -> None:
    """录一局 → 重放 → **四项逐条相等**。"""
    recording, original = _record_a_game(seed=20261026, frames=8)

    # **先确认这一局真有内容**——两边都是空棋盘的话，四项全等没有区分力。
    fresh = assemble(seed=recording.seed).kernel.snapshot()
    assert original.fingerprint != fresh.board.structureFingerprint, (
        "这一局的棋盘还是空的，用例就没有区分力"
    )

    replayed = _replay(recording)

    assert replayed.fingerprint == original.fingerprint, "棋盘指纹"
    assert replayed.score == original.score, "分数"
    assert replayed.lines == original.lines, "行数"
    assert replayed.pieces_placed == original.pieces_placed, "方块数"
    assert replayed.equals(original)


def test_the_four_items_comparison_is_item_by_item() -> None:
    """**四项各自**相等才算过——只比最终分数不算。"""
    baseline = FourItems(fingerprint="fp", score=100, lines=2, pieces_placed=5)
    same_score_only = FourItems(fingerprint="别的", score=100, lines=2, pieces_placed=5)

    assert baseline.equals(baseline)
    assert not baseline.equals(same_score_only), "指纹不等就不算过"


def test_the_replay_dispatches_no_extra_commands() -> None:
    """**四项比对走的是不插额外命令的普通回放路径**。

    **机制级断言**：重放派发的命令**逐条等于**录下的命令（一条不多、一条不少、
    顺序不变）。
    """
    recording, _ = _record_a_game(seed=11, frames=12)

    assembly = assemble(
        seed=recording.seed,
        rule_id=recording.rule,
        mode="replay",
        limits=REPLAY_LIMITS,
        observability_enabled=False,
    )
    driver = CapturingDriver(KernelReplayDriver(assembly.kernel, assembly.bus))
    session = PlaybackSession(assembly.bus, driver, recording)
    session.run()

    assert driver.dispatched == list(recording.commands)


# ---------------------------------------------------------------------------
# 中途开始（spec §7.4 选①）
# ---------------------------------------------------------------------------


def test_the_mid_game_start_reaches_the_same_four_items() -> None:
    """从第 N 帧开始重放 → **跑到终点的四项与从头重放相同**。"""
    recording, original = _record_a_game(seed=303, frames=25)

    assembly = assemble(
        seed=recording.seed,
        rule_id=recording.rule,
        mode="replay",
        limits=REPLAY_LIMITS,
        observability_enabled=False,
    )
    driver = KernelReplayDriver(assembly.kernel, assembly.bus)
    session = PlaybackSession(assembly.bus, driver, recording)
    session.run_until(10)
    while session.next_frame is not None:
        session.step()

    assert session.four_items().equals(original)


def test_the_mid_game_start_replays_from_the_beginning() -> None:
    """**中途开始必须从头重放**（spec §7.4 选①——不用存档跳过去）。

    **这条就是反向验证 B 的判据**：把中途开始改成"跳过去"，派发过的命令条数
    就对不上，红。
    """
    commands = (
        _command(0, "start"),
        _command(1, "tick"),
        _command(1, "moveLeft"),
        _command(2, "tick"),
        _command(3, "tick"),
    )
    recording = _recording(commands)
    driver = FakeDriver()
    session = PlaybackSession(_bus(), driver, recording)

    session.run_until(2)

    dispatched = [command.type for command in driver.dispatched]
    assert dispatched == ["start", "tick", "moveLeft", "tick"], (
        "中途开始必须从头重放——只派发第 2 帧起的命令就是'跳过去'"
    )


# ---------------------------------------------------------------------------
# 逐帧步进
# ---------------------------------------------------------------------------


def test_the_stepped_fingerprint_equals_the_played_one() -> None:
    """步进到第 K 帧 → 此时的**棋盘指纹 ==** 从头跑到第 K 帧的指纹。"""
    recording, _ = _record_a_game(seed=404, frames=20)

    def _assembly():
        return assemble(
            seed=recording.seed,
            rule_id=recording.rule,
            mode="replay",
            limits=REPLAY_LIMITS,
            observability_enabled=False,
        )

    played = _assembly()
    played_session = PlaybackSession(
        played.bus, KernelReplayDriver(played.kernel, played.bus), recording
    )
    played_session.run_until(8)
    played_fingerprint = played_session.four_items().fingerprint

    stepped = _assembly()
    stepped_session = PlaybackSession(
        stepped.bus, KernelReplayDriver(stepped.kernel, stepped.bus), recording
    )
    while stepped_session.next_frame is not None and stepped_session.next_frame <= 8:
        stepped_session.step()

    assert stepped_session.four_items().fingerprint == played_fingerprint


def test_each_step_emits_a_frame_stepped_event() -> None:
    """**每步发一条** `replay:frame-stepped`。"""
    recording = _recording(
        (
            _command(0, "start"),
            _command(1, "tick"),
            _command(2, "tick"),
        )
    )
    bus = _bus()
    received: list[Envelope] = []
    bus.subscribe("replay:frame-stepped", received.append)
    session = PlaybackSession(bus, FakeDriver(), recording)

    session.step()
    session.step()

    assert len(received) == 2
    assert [dict(envelope.payload)["frame"] for envelope in received] == [0, 1]


# ---------------------------------------------------------------------------
# 暂停（ADR-033）
# ---------------------------------------------------------------------------


def test_the_pause_freezes_the_frame_and_dispatches_nothing() -> None:
    """**宿主级暂停**（ADR-033）：帧号冻结 · **不产生任何命令**。

    **为什么这条重要**：内核级 `pause` 是被接受的命令 → 会被录进录像 → **回放
    自己污染录像**（ADR-033 的原话）；而且它会让内核进入 `paused`，之后录下
    的命令全被生命周期门禁拒掉——重放当场分叉。
    """
    recording = _recording(
        (
            _command(0, "start"),
            _command(1, "tick"),
            _command(2, "tick"),
        )
    )
    bus = _bus()
    driver = FakeDriver()
    session = PlaybackSession(bus, driver, recording)

    session.step()
    session.step()
    session.pause()
    assert session.paused
    dispatched_before = len(driver.dispatched)

    with pytest.raises(RuntimeError):
        session.step()

    assert len(driver.dispatched) == dispatched_before, "暂停期间不派发任何命令"
    assert driver.advanced == 1, "暂停期间不推进帧"

    session.resume()
    session.step()
    assert driver.advanced == 2, "恢复后能接着走"


# ---------------------------------------------------------------------------
# 命令目录（反向验证 C 的判据）
# ---------------------------------------------------------------------------


def test_the_command_catalog_still_has_seventeen_commands() -> None:
    """**命令目录仍 17 条**（M2 不新增命令 · M3 新增 `switchRuleSet` 一条）。

    **这条就是反向验证 C 的判据**：给步进新增一条命令，它必红。
    """
    assert len(COMMANDS) == 17
    assert len(COMMAND_TYPES) == 17


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------


def test_the_playback_lifecycle_events_are_emitted() -> None:
    """**开始 / 每帧 / 停止**三条事件都发（ADR-075）。"""
    outcome = FourItems(fingerprint="fp", score=3, lines=1, pieces_placed=2)
    recording = _recording(
        (
            _command(0, "start"),
            _command(1, "tick"),
        ),
        outcome=outcome,
    )
    bus = _bus()
    started: list[Envelope] = []
    stopped: list[Envelope] = []
    bus.subscribe("replay:playback-started", started.append)
    bus.subscribe("replay:playback-stopped", stopped.append)
    session = PlaybackSession(bus, FakeDriver(outcome), recording)

    session.run()

    assert len(started) == 1
    payload = dict(started[0].payload)
    assert payload["seed"] == 7
    assert payload["rule"] == "standard"
    assert payload["commandCount"] == 2

    assert len(stopped) == 1
    stopped_payload = dict(stopped[0].payload)
    assert stopped_payload["fingerprint"] == "fp"
    assert stopped_payload["score"] == 3
    assert stopped_payload["lines"] == 1
    assert stopped_payload["piecesPlaced"] == 2


# ---------------------------------------------------------------------------
# CLI（`replay` 子命令从占位变成真跑）
# ---------------------------------------------------------------------------


def test_the_cli_replay_command_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`tetris replay <文件>` **真跑**并输出四项比对。"""
    recording, _ = _record_a_game(seed=505, frames=20)
    path = tmp_path / "recording.jsonl"
    path.write_text(recording_to_jsonl(recording), encoding="utf-8")

    code = main(["replay", str(path)])

    assert code == 0
    output = capsys.readouterr().out
    assert "重放棋盘指纹：" in output
    assert "四项比对：全等" in output


def test_the_cli_replay_command_can_output_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` 给机器可读形态。"""
    recording, _ = _record_a_game(seed=606, frames=15)
    path = tmp_path / "recording.jsonl"
    path.write_text(recording_to_jsonl(recording), encoding="utf-8")

    code = main(["replay", str(path), "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["equal"] is True
    assert payload["seed"] == recording.seed
    assert payload["recorded"]["fingerprint"] == payload["replayed"]["fingerprint"]


def test_the_cli_replay_command_reports_a_broken_recording(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**不认识的头 → 明确报错**（不静默忽略）。"""
    path = tmp_path / "broken.jsonl"
    path.write_text('{"rule":"standard","seed":1,"version":"v99"}\n', encoding="utf-8")

    code = main(["replay", str(path)])

    assert code == 2
    assert "录像读不了" in capsys.readouterr().err


def test_the_cli_replay_command_fails_when_the_recording_has_no_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """录像**没有局末四项** → **退出码非 0**（审查 H3）。

    **"没比"不是"过了"**：没有基准就没有比对。退出码 0 会让脚本把一条
    **没校验过**的录像放过去——所以这里必须非 0（**2** = 这次校验没做成）。
    """
    path = tmp_path / "no-baseline.jsonl"
    path.write_text(
        json.dumps({"version": "v1", "seed": 1, "rule": "standard", "outcome": None}) + "\n",
        encoding="utf-8",
    )

    code = main(["replay", str(path)])

    assert code != 0, "没有基准也退出 0——脚本会把「没比」当成「过了」"
    assert code == 2, "「没比」要与「四项不等」（1）区分开"
    assert "没有局末四项" in capsys.readouterr().err


def test_the_cli_replay_command_fails_when_the_recording_has_no_baseline_in_json_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**JSON 形态也一样非 0**——`equal` 是 `null` 就是"没比"。"""
    path = tmp_path / "no-baseline.jsonl"
    path.write_text(
        json.dumps({"version": "v1", "seed": 1, "rule": "standard", "outcome": None}) + "\n",
        encoding="utf-8",
    )

    code = main(["replay", str(path), "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["equal"] is None, "没有基准就没有可比的东西"


def test_the_cli_replay_command_exits_one_when_the_four_items_differ(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**四项不等 → 退出码 1**（与"没比"的 2 区分开）。

    **这条是 H3 的反向判据**：把"没比"也当成 1 或者 0，这条与上面那条就
    分不开了——脚本需要能分辨"没比"与"比了没过"。
    """
    path = tmp_path / "mismatch.jsonl"
    path.write_text(
        json.dumps(
            {
                "version": "v1",
                "seed": 1,
                "rule": "standard",
                "outcome": {
                    "fingerprint": "这不是这一局的指纹",
                    "score": 0,
                    "lines": 0,
                    "piecesPlaced": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    code = main(["replay", str(path)])

    assert code == 1, "四项不等是 1，不是 2"
    assert "四项比对：不等" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 规则集接线（审查 M1）
# ---------------------------------------------------------------------------


def test_the_rule_set_registry_knows_the_standard_rule_set() -> None:
    """**标识 → 规则集**的接线表：标准规则集在表里，且就是那一份。"""
    assert rule_set_of("standard") is STANDARD_RULESET
    assert rule_set_of("没登记的规则集") is None


def test_the_cli_replay_command_refuses_a_rule_set_it_does_not_know(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """录像写的是**没登记的规则集** → **明确拒绝**，不拿标准规则集硬跑。

    **这条就是 M1 的判据**：原来只换 `rule_id`、`rule_set` 永远是标准规则集
    ——重放出来的四项必然不等，而 CLI 只报"四项不等"，**看不出"起错规则集
    了"**。变体规则的重放属 M4。
    """
    path = tmp_path / "variant.jsonl"
    path.write_text(
        json.dumps({"version": "v1", "seed": 1, "rule": "变体", "outcome": None}) + "\n",
        encoding="utf-8",
    )

    code = main(["replay", str(path)])

    assert code == 2, "规则集没登记要明确拒绝，不是拿标准规则集硬跑"
    err = capsys.readouterr().err
    assert "变体" in err, "报错要说得清是哪个规则集"
    assert "M4" in err, "报错要说得清变体规则的重放待到哪个里程碑"


def test_the_cli_replay_command_still_replays_the_standard_rule_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**标准规则集照常重放**（M1 的反向判据）。

    **接线表不是把重放堵死**：标准规则集必须在表里，标准录像照常能放。
    """
    recording, _ = _record_a_game(seed=707, frames=12)
    path = tmp_path / "standard.jsonl"
    path.write_text(recording_to_jsonl(recording), encoding="utf-8")

    code = main(["replay", str(path)])

    assert code == 0
    assert "四项比对：全等" in capsys.readouterr().out


def test_the_recording_round_trip_keeps_the_outcome() -> None:
    """录像 → JSON Lines → 录像，**局末四项也要带回来**。"""
    recording = _recording(
        (_command(1, "moveLeft"),),
        outcome=FourItems(fingerprint="fp", score=10, lines=2, pieces_placed=3),
    )

    restored = recording_from_jsonl(recording_to_jsonl(recording))

    assert restored.outcome == recording.outcome
    assert restored == recording
