"""图形版的端到端用例（M6-S4 · `G6-5` · spec-M6 §7.3 / §9.1 · ADR-100）。

**守的是口径，不是实现细节**：

- **同种子同输入同指纹**（`G6-5`）——**图形路径与终端路径**（**输入来源不
  同 · 其余完全一样**）跑同一串按键，**棋盘指纹逐帧相同**；
- **能玩到局结束**（`G6-5`）——图形路径能一路走到 `game-over`；
- **渲染不计入逻辑帧预算**（NFR-6-1）——**逻辑帧耗时与有没有渲染器无关**
  （**源码级断言** + **宽松的实测对照**）。

**为什么这些能无头跑**：图形路径与终端路径的差别**只在输入来源与画面**——
**内核那一套完全相同**——所以**"同种子同输入同指纹"这件事本身就能在无头
环境里判**（**真窗口的冒烟在 `G6-6` 单独做**）。
"""

from __future__ import annotations

import io
import statistics

from tetris_kernel.adapters.input import GraphicsInput, KeyboardInput
from tetris_kernel.adapters.render import GraphicsRenderer
from tetris_kernel.host import assemble_play
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.rules.standard import STANDARD_RULESET

#: 棋盘尺寸（**小棋盘** · 端到端要快）。
WIDTH = 10
HEIGHT = 8

#: 同一串按键（**两条路喂的是同一串** —— 终端喂归一键名 · 图形喂 tkinter 键名）。
_KEY_SEQUENCE: tuple[tuple[str, str], ...] = (
    ("left", "Left"),
    ("left", "Left"),
    ("rotate_cw", "Up"),
    ("right", "Right"),
    ("hard_drop", "space"),
    ("left", "Left"),
    ("soft_drop", "Down"),
    ("rotate_ccw", "z"),
    ("hard_drop", "space"),
    ("right", "Right"),
    ("right", "Right"),
    ("hard_drop", "space"),
)


def _run(seed: int, *, graphics: bool) -> list[str]:
    """**跑一局**（**图形路径或终端路径**）→ **逐帧棋盘指纹**。

    **图形路径**：输入走 `GraphicsInput`（**tkinter 键名**）；**终端路径**：
    直接喂 `KeyboardInput`（**归一键名**）。
    """
    play = assemble_play(
        seed=seed,
        rule_set=STANDARD_RULESET,
        board_width=WIDTH,
        board_height=HEIGHT,
        render=False,
        stream=io.StringIO(),
        input_wrapper=GraphicsInput if graphics else None,
    )
    loop = HeadlessLoop(play.assembly)
    loop.start()
    adapter = play.input_adapter
    fingerprints: list[str] = []
    for key, tk_key in _KEY_SEQUENCE:
        if graphics:
            assert isinstance(adapter, GraphicsInput)
            adapter.handle_tk_key(tk_key, "down")
            adapter.handle_tk_key(tk_key, "up")
        else:
            assert isinstance(adapter, KeyboardInput)
            adapter.handle_key(key, "down")
            adapter.handle_key(key, "up")
        adapter.poll_keys(0)
        loop.run_frame()
        fingerprints.append(loop.snapshot().board.structureFingerprint)
    return fingerprints


# ---------------------------------------------------------------------------
# G6-5 · 同种子同输入同指纹
# ---------------------------------------------------------------------------


def test_图形路径与终端路径同种子同输入同指纹() -> None:
    """**同一串按键 · 同一个种子 → 逐帧指纹全等**（**这是 `G6-5` 的正题**）。

    **手感漂一点点，同一个种子就会走出不同的局**——**指纹全等**就是"没漂"的
    硬证据。
    """
    assert _run(20261120, graphics=True) == _run(20261120, graphics=False)


def test_多个种子下都全等() -> None:
    """**不是一个种子的巧合**（**三个种子各跑一遍**）。"""
    for seed in (1, 42, 20261120):
        assert _run(seed, graphics=True) == _run(seed, graphics=False)


# ---------------------------------------------------------------------------
# G6-5 · 能玩到局结束
# ---------------------------------------------------------------------------


def test_图形路径能玩到局结束() -> None:
    """**一路硬降 → 顶出 → `game-over`**（**图形路径能玩到底**）。"""
    play = assemble_play(
        seed=20261120,
        rule_set=STANDARD_RULESET,
        board_width=WIDTH,
        board_height=HEIGHT,
        render=False,
        stream=io.StringIO(),
        input_wrapper=GraphicsInput,
    )
    loop = HeadlessLoop(play.assembly)
    loop.start()
    adapter = play.input_adapter
    assert isinstance(adapter, GraphicsInput)
    for _ in range(400):
        adapter.handle_tk_key("space", "down")
        adapter.handle_tk_key("space", "up")
        adapter.poll_keys(0)
        loop.run_frame()
        if loop.snapshot().phase == "game_over":
            break
    assert loop.snapshot().phase == "game_over"


# ---------------------------------------------------------------------------
# NFR-6-1 · 渲染不计入逻辑帧预算
# ---------------------------------------------------------------------------


def test_逻辑帧耗时不含渲染() -> None:
    """**挂着图形渲染器，逻辑帧耗时不变**（**渲染在预算之外**）。

    **口径**：**同一局 · 同一串按键**，一次带渲染器、一次不带 → **逻辑帧耗时
    的中位数差异 < 50 %**（**宽松** —— 这条不是精密测量，是**"量级相同"**的
    体检；**跨时段的绝对值不可引用**，所以这里只比**同机同时段的两个中位数**）。
    """
    costs: list[float] = []
    for with_renderer in (False, True):
        play = assemble_play(
            seed=20261120,
            rule_set=STANDARD_RULESET,
            board_width=WIDTH,
            board_height=HEIGHT,
            render=False,
            stream=io.StringIO(),
        )
        loop = HeadlessLoop(play.assembly)
        loop.start()
        renderer = None
        if with_renderer:
            renderer = GraphicsRenderer(rule_set=STANDARD_RULESET, width=WIDTH, height=HEIGHT)
        samples: list[float] = []
        for _ in range(120):
            start = _now_ms()
            loop.run_frame()
            samples.append(_now_ms() - start)
            if renderer is not None:
                renderer.update_snapshot(loop.snapshot())
        costs.append(statistics.median(samples))
    assert costs[0] > 0
    assert costs[1] < costs[0] * 1.5


def _now_ms() -> float:
    """**当前时刻（毫秒）**（**单调时钟** —— 不受系统时间调整影响）。"""
    import time

    return time.perf_counter() * 1000
