"""实时宿主的用例（M4-S8 · FR-8.x · spec-M4 §7.5 · ADR-082）。

**守的是口径，不是实现细节**：

- **`play` 不再是占位**（M0 立的占位规矩到 M4 结束为止）；
- **无头不退化**：**关掉渲染与输入** → **内核逐帧指纹不变**（与无头帧循环
  逐字相同）；
- **降级接上了**：**每帧喂一次逻辑耗时** → **判据用 P99** · **滞回**；
- **多渲染器同挂**（FR-9.4）→ **互不影响**；
- **输入真的推动内核**（**按键 → 命令 → 内核**）；
- **渲染不计入逻辑帧预算**（NFR-2）——**画面慢不算在内核头上**。

**反向用例**（"把渲染耗时算进逻辑帧预算" → 必红）在
`test_渲染耗时不计入逻辑帧预算`。
"""

from __future__ import annotations

import io
import time

from tetris_kernel.adapters.input import KeyboardInput
from tetris_kernel.adapters.render import TerminalRenderer
from tetris_kernel.host import assemble, assemble_play
from tetris_kernel.host.cli import main
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.host.performance import DEGRADE_P99_MS, DegradationController
from tetris_kernel.host.play import PlaySession
from tetris_kernel.rules.standard import STANDARD_RULESET


class _FakeClock:
    """**假时钟**（**宿主给的时刻** —— 适配器与读取器都不读墙钟）。"""

    def __init__(self, step_ms: int = 16) -> None:
        self._now = 0
        self._step = step_ms

    def now_ms(self) -> int:
        return self._now

    def step_ms(self) -> int:
        return self._step

    def advance(self, ms: int) -> None:
        self._now += ms


def _sleeper_for(clock: _FakeClock):
    """**等待 = 推进假时钟**（**不真的睡** → **测试不耗时**）。"""

    def sleeper(seconds: float) -> None:
        clock.advance(int(seconds * 1000))

    return sleeper


def _session(
    *,
    frames: int,
    render: bool = False,
    input_adapter: KeyboardInput | None = None,
    degradation: DegradationController[object] | None = None,
) -> PlaySession:
    """造一个实时会话（**假时钟 + 不真的等**）。"""
    clock = _FakeClock()
    play = assemble_play(
        seed=3,
        render=render,
        stream=io.StringIO(),
        degradation_enabled=degradation is not None,
    )
    loop = HeadlessLoop(play.assembly)
    loop.start()
    return PlaySession(
        assembly=play.assembly,
        loop=loop,
        renderer=play.renderer,
        input_adapter=play.input_adapter if input_adapter is None else input_adapter,
        degradation=play.degradation if degradation is None else degradation,
        clock=clock,
        sleeper=_sleeper_for(clock),
        frame_counter=play.frame_counter,
    )


# ---------------------------------------------------------------------------
# 占位规矩到 M4 结束为止
# ---------------------------------------------------------------------------


def test_play_不再是占位() -> None:
    """**`tetris play --help` 不再是占位**（M0 立的占位规矩到 M4 结束为止）。"""
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            main(["play", "--help"])
        except SystemExit:
            pass
    text = buffer.getvalue()
    assert "usage" in text
    assert "尚未实现" not in text
    assert "--variant" in text
    assert "--das" in text
    assert "--arr" in text


# ---------------------------------------------------------------------------
# 无头不退化
# ---------------------------------------------------------------------------


def test_无头不退化_关掉渲染与输入内核指纹不变() -> None:
    """**关掉渲染与输入** → **内核逐帧指纹与无头帧循环逐字相同**。

    **"无头行为一个字节都不变"**（spec §7.5）——**实时宿主只是在外面套了一层
    节拍**，**帧协议仍是 `HeadlessLoop` 那一份**。
    """
    session = _session(frames=90)
    report = session.run(frames=90)

    headless = assemble(seed=3)
    loop = HeadlessLoop(headless)
    loop.start()
    fingerprints = []
    for _ in range(90):
        loop.run_frame()
        fingerprints.append(loop.snapshot().board.structureFingerprint)
    assert report.frames == 90
    assert report.fingerprint == fingerprints[-1]


def test_实时会话与无头循环的事件数一致() -> None:
    """**事件数一致**（**没有多发也没有少发** —— **渲染与输入不发内核事件**）。"""
    session = _session(frames=60)
    report = session.run(frames=60)

    headless = assemble(seed=3)
    loop = HeadlessLoop(headless)
    loop.start()
    loop.run_frames(60)
    assert report.events == headless.bus.last_seq


# ---------------------------------------------------------------------------
# 输入真的推动内核
# ---------------------------------------------------------------------------


def test_输入真的推动内核() -> None:
    """**按键 → 命令 → 内核**：按左 → **活动方块真的左移了**。"""
    session = _session(frames=30)
    adapter = session._input  # noqa: SLF001 —— 用例要拿到同一个适配器
    assert adapter is not None
    before = session._loop.snapshot().active  # noqa: SLF001
    assert before is not None
    adapter.handle_key("left", "down")
    session.run(frames=2)
    after = session._loop.snapshot().active  # noqa: SLF001
    assert after is not None
    assert after.x == before.x - 1


def test_输入事件带帧号() -> None:
    """**输入事件带帧号**（**帧号由宿主数** —— 适配器自己不碰内核）。

    **口径**：**这一帧读到的输入带的帧号就是这一帧的帧号**（**0 起**）。
    """
    session = _session(frames=10)
    seen: list[int] = []
    session._assembly.bus.subscribe(  # noqa: SLF001
        "input:raw", lambda envelope: seen.append(envelope.frame)
    )
    adapter = session._input  # noqa: SLF001
    assert adapter is not None
    adapter.handle_key("space", "down")
    session.run(frames=1)
    assert seen == [0]
    adapter.handle_key("space", "down")
    session.run(frames=1)
    assert seen == [0, 1]


# ---------------------------------------------------------------------------
# 渲染与降级
# ---------------------------------------------------------------------------


def test_渲染器跟着帧走() -> None:
    """**渲染器每帧画一次**（**画面是这一帧的结果**）。"""
    session = _session(frames=20, render=True)
    assert session._renderer is not None  # noqa: SLF001
    session.run(frames=20)
    assert session._renderer.active is not None  # noqa: SLF001


def test_多渲染器同挂互不影响() -> None:
    """**多渲染器同挂**（FR-9.4）→ **互不影响**（**内核逐帧指纹不变**）。"""
    play = assemble_play(seed=3, render=True, stream=io.StringIO())
    second = TerminalRenderer(
        rule_set=STANDARD_RULESET,
        width=10,
        height=22,
        hidden_rows=2,
        stream=io.StringIO(),
    )
    play.assembly.bus.subscribe("*", second.on_event)
    loop = HeadlessLoop(play.assembly)
    loop.start()
    fingerprints = []
    for _ in range(60):
        loop.run_frame()
        fingerprints.append(loop.snapshot().board.structureFingerprint)

    plain = assemble(seed=3)
    plain_loop = HeadlessLoop(plain)
    plain_loop.start()
    plain_fingerprints = []
    for _ in range(60):
        plain_loop.run_frame()
        plain_fingerprints.append(plain_loop.snapshot().board.structureFingerprint)
    assert fingerprints == plain_fingerprints


def test_降级接上了_每帧喂一次耗时() -> None:
    """**每帧喂一次逻辑耗时**（ADR-082 的遗留接线 · **降级判据用 P99**）。

    **窗口没满就没有 P99**（**半个窗口的 P99 不是 P99** · ADR-031）→ **这条
    用例断言的是"喂了几次"**。
    """
    controller = DegradationController(bus=assemble(seed=1).bus)
    session = _session(frames=40, degradation=controller)
    session.run(frames=40)
    assert controller.window.count == 40
    assert controller.window.mean() is not None


def test_降级按_p99_判档() -> None:
    """**判据用 P99 不用均值**（ADR-031）——**窗口没满就不降档**。"""
    assembly = assemble(seed=3)
    controller = DegradationController(bus=assembly.bus)
    for _ in range(10):
        controller.observe_frame(50.0)
    assert controller.auto_tier == 0, "窗口没满就不该降档（半个窗口的 P99 不是 P99）"
    for _ in range(700):
        controller.observe_frame(50.0)
    assert controller.auto_tier > 0, "窗口满了还超预算就该降档"
    assert DEGRADE_P99_MS < 50.0


def test_渲染耗时不计入逻辑帧预算() -> None:
    """**渲染不计入逻辑帧预算**（NFR-2）。

    **实证**：**渲染器每帧故意睡 20ms** → **喂给降级控制器的耗时仍然是"推进
    那一帧"的耗时**（**远小于 20ms**）。

    **两个口径混了就是错账**：**画面慢会被误判成"内核慢"**。
    """
    assembly = assemble(seed=3)
    controller = DegradationController(bus=assembly.bus)

    class _SlowRenderer(TerminalRenderer):
        """**每帧睡 20ms 的渲染器**（**模拟画面慢**）。"""

        def draw(self) -> None:
            time.sleep(0.02)
            super().draw()

    renderer = _SlowRenderer(
        rule_set=STANDARD_RULESET, width=10, height=22, hidden_rows=2, stream=io.StringIO()
    )
    assembly.bus.subscribe("*", renderer.on_event)
    loop = HeadlessLoop(assembly)
    loop.start()
    clock = _FakeClock()
    session = PlaySession(
        assembly=assembly,
        loop=loop,
        renderer=renderer,
        degradation=controller,
        clock=clock,
        sleeper=_sleeper_for(clock),
    )
    session.run(frames=20)
    assert controller.window.count == 20
    mean = controller.window.mean()
    assert mean is not None
    assert mean < 10.0, "渲染的 20ms 不该被算进逻辑帧耗时"


# ---------------------------------------------------------------------------
# 结束条件
# ---------------------------------------------------------------------------


def test_玩家按退出键就收工() -> None:
    """**按 `q` 退出**（**原始输入事件** → **宿主喊停**）。"""
    session = _session(frames=1000)
    adapter = session._input  # noqa: SLF001
    assert adapter is not None
    adapter.handle_key("q", "down")
    session.run(frames=1000)
    assert session.frames < 1000
    assert session.stopped
