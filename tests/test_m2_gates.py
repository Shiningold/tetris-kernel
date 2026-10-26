"""M2 五道门判据（S13 · plan-M2.md §5.2 · `G2-1`…`G2-5`）。

## 五道门与本文件的用例

| 门 | 判据 | 复跑命令 |
| --- | --- | --- |
| `G2-1` | 200 局回放 · 四项全等 | `pytest -k m2_replay_four_items` |
| `G2-2` | 抛错订阅者被隔离 | `pytest -k m2_subscriber_isolation` |
| `G2-3` | 关闭全部可观测系统后内核逐帧一致 | `pytest -k m2_observability_purity` |
| `G2-4` | 千局模拟总时长 ≤30 分钟 | `pytest -k m2_thousand_games_budget` |
| `G2-5` | 连续 1000 局回放指纹零差异 | `pytest -k m2_replay_1000_zero_diff` |

**用例名不许中途改**——改了 §5.2 的复跑命令就失效（plan-M2 §5.2）。元守卫在
`tests/test_m2_gates_meta.py`：它**读 §5.2 那张表**，把五个命中名逐条与
`tests/` 下的用例名比对，**选不到就红并点名那个入口**。

## 千局基准怎么跑（ADR-018）

**多进程**：`ProcessPoolExecutor`，**每局一个任务**，进程池大小 = CPU 核数，
`chunksize > 1`，**汇总前按种子排序**。

**"关闭事件分发"的快速模式被明确否决**（ADR-018 · B-1 · B-10）——那会破坏
**事件完备性**（AC-2）：状态变更不再伴随事件。

**为什么必须多进程**：本机一局（参考策略跑到顶出）约 **3.3 秒** → **串行跑
1000 局要 54 分钟**，超 `G2-4` 的 30 分钟预算；**并行跑约 3.5 分钟**。

**`G2-4` 判的是进程池那 1000 局的墙钟总时长**——这是**保守**读法：它把
**重放**的时间也算进去了，而预算只要求"模拟"的时长。

## 两批局是同一批（spec §7.3）

`G2-1` 的 200 局与 `G2-5` 的 1000 局**用同一张种子表**——前 200 局就是同一
批（同种子 → 同结果，确定性）。**同机同时段一起跑**，**不许互相引用对方的
数字当结论**（跨时段的绝对值不可引用 · plan-M1 §7 R10）。

## 期望值的独立性（M0-1 在测试层的翻版）

`G2-1` 除了"200 局逐局四项全等"，还有**一条手抄的期望值**（固定种子那一局
的四项）。**手抄 ≠ 实现算出来**——所以另有一条**独立性守卫**：那条期望值必须
**以字面量出现在本文件的源码里**。把它改成"实现算出来的"，守卫当场变红
（plan-M2 §5.3 的 S13 反向验证之一）。

**计分表本身**由 M1 的 `G1-4` 守着（手抄计分表 + 本文件自己写的公式），本
文件不重复。

**自给自足**：不依赖 `tests/conftest.py` 的 fixture。
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

from support.driver import play_to_game_over
from support.policy import POLICY_SOURCE, ReferencePolicy
from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.host.assembly import Assembly, assemble
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.host.replay import REPLAY_LIMITS, KernelReplayDriver
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.lifecycle import PHASE_GAME_OVER
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.systems.replay import (
    FourItems,
    PlaybackSession,
    Recording,
    RecordingSystem,
)

#: 本文件自己的源码（**独立性守卫**要读它）。
SOURCE_PATH = Path(__file__)

#: 千局基准的种子表起点（**固定**：种子是门判据的一部分，不是随手挑的）。
BASE_SEED = 20261000

#: `G2-1` 的局数（spec §7.3 · AC-1）。
GATE_GAMES = 200

#: `G2-4` / `G2-5` 的局数（spec §7.3 · NFR-1）。
THOUSAND_GAMES = 1000

#: `G2-4` 的预算：千局模拟总时长 ≤ **30 分钟**（秒）。
THOUSAND_BUDGET_SECONDS = 30 * 60

#: 逐帧比对与隔离用例用的种子。
SCENARIO_SEED = 20261000

#: 隔离用例跑的帧数。
ISOLATION_FRAMES = 240

#: **手抄**期望值那一局的种子。
HAND_SEED = 20261000

#: **手抄**的四项期望值（种子 `HAND_SEED` 那一局）。
#:
#: **转录来源**：同种子同策略跑一局，把四项抄下来。
#:
#: **不是实现算出来的**——它是**观测事实**的抄本。plan-M2 §5.3 那条 S13 反向
#: 验证（"把手算的期望值改成实现算出来的值"）要守的就是这一层。
HAND_EXPECTED_FOUR_ITEMS = FourItems(
    fingerprint="1091742c78711d1642db22ac147029e0",
    score=650,
    lines=6,
    pieces_placed=54,
)

#: **手抄**期望值那一条的源码字面量（**独立性守卫**按它匹配）。
HAND_EXPECTED_LITERAL = re.compile(
    r'fingerprint="1091742c78711d1642db22ac147029e0",\s*'
    r"score=650,\s*"
    r"lines=6,\s*"
    r"pieces_placed=54,",
)


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------


def _seeds(count: int) -> tuple[int, ...]:
    """种子表（前 `count` 局）。"""
    return tuple(BASE_SEED + index for index in range(count))


def _four_items(snapshot: Snapshot) -> FourItems:
    """快照 → 四项（棋盘指纹 · 分数 · 行数 · 方块数）。"""
    return FourItems(
        fingerprint=snapshot.board.structureFingerprint,
        score=snapshot.score,
        lines=snapshot.lines,
        pieces_placed=snapshot.piecesPlaced,
    )


def _recorder_of(assembly: Assembly) -> RecordingSystem[Snapshot]:
    """从装配结果里挑出录制系统（**标准装配表装的那一个**）。"""
    systems = [
        system for system in assembly.observable_systems if isinstance(system, RecordingSystem)
    ]
    assert systems, "录像系统没装上"
    return systems[0]


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


@dataclass(frozen=True)
class GameResult:
    """一局的门判据结果。"""

    seed: int
    original: FourItems
    replayed: FourItems
    frames: int


@dataclass(frozen=True)
class Batch:
    """一批局的门判据结果 + **跑完它的墙钟时长**。"""

    results: tuple[GameResult, ...]
    seconds: float


def _record_and_replay(seed: int) -> GameResult:
    """**一局的任务**：录 → 重放 → 取两项四元组。

    **进程池里的那个任务**（ADR-018：每局一个任务）。

    **用标准装配**（日志 · 指标 · 录像三个系统都在）——门判据要验的是**真东西**。
    """
    assembly = assemble(seed=seed)
    recorder = _recorder_of(assembly)
    loop = HeadlessLoop(assembly)
    loop.start()
    kernel = assembly.kernel
    while kernel.phase != PHASE_GAME_OVER:
        for name in ReferencePolicy().decide(kernel.snapshot()):
            kernel.submit(Command(type=name, source=POLICY_SOURCE))
        loop.run_frame()
    original = _four_items(kernel.snapshot())
    recorder.set_outcome(original)
    replayed = _replay(recorder.recording())
    return GameResult(
        seed=seed,
        original=original,
        replayed=replayed,
        frames=kernel.snapshot().frame,
    )


def _run_batch(seeds: tuple[int, ...]) -> Batch:
    """用**进程池**跑一批局（ADR-018 · 每局一个任务 · 汇总前按种子排序）。"""
    workers = min(len(seeds), os.cpu_count() or 1)
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_record_and_replay, seeds, chunksize=8))
    elapsed = time.perf_counter() - started
    return Batch(
        results=tuple(sorted(results, key=lambda item: item.seed)),
        seconds=elapsed,
    )


# ---------------------------------------------------------------------------
# 夹具（**模块级**：一批局很贵，别每条用例重跑一遍）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gate_batch() -> Batch:
    """`G2-1` 那一批 200 局。"""
    return _run_batch(_seeds(GATE_GAMES))


@pytest.fixture(scope="module")
def thousand_batch() -> Batch:
    """`G2-4` / `G2-5` 那一批 1000 局（**含 `G2-1` 的前 200 局**）。"""
    return _run_batch(_seeds(THOUSAND_GAMES))


# ---------------------------------------------------------------------------
# G2-1 · 200 局回放 · 四项全等
# ---------------------------------------------------------------------------


def test_m2_replay_four_items_every_game_replays_to_the_same_four_items(
    gate_batch: Batch,
) -> None:
    """200 局**逐局**四项全等（棋盘指纹 · 分数 · 行数 · 方块数）。

    **逐条相等**才算过——不是"最终态差不多"（AC-1 · spec §7.3）。
    """
    assert len(gate_batch.results) == GATE_GAMES, "这一批的局数不对"

    mismatches: list[str] = []
    for result in gate_batch.results:
        if not result.original.equals(result.replayed):
            mismatches.append(
                f"种子 {result.seed}：原局 {result.original.as_dict()} ≠ "
                f"重放 {result.replayed.as_dict()}"
            )

    assert not mismatches, "有局重放后四项不等：\n  " + "\n  ".join(mismatches)


def test_m2_replay_four_items_the_replayed_games_are_not_trivially_empty(
    gate_batch: Batch,
) -> None:
    """这一批局**真的有内容**——两边都是空棋盘的话，四项全等没有区分力。

    **这一条防的是"用空局糊弄过去"**：指纹全等 + 分数全等 + 行数全等，
    而每一局其实只走了一帧。
    """
    assert gate_batch.results, "这一批一局都没跑"
    assert all(result.frames > 100 for result in gate_batch.results), (
        "有的局不到 100 帧——这一批不是「跑到顶出」的完整局"
    )
    assert any(result.original.lines > 0 for result in gate_batch.results), (
        "这一批一局都没消过行——四项全等没有区分力"
    )
    assert len({result.original.fingerprint for result in gate_batch.results}) > 1, (
        "这一批的棋盘指纹全都一样——种子表没起作用"
    )


def test_m2_replay_four_items_the_hand_transcribed_expectation_holds(
    gate_batch: Batch,
) -> None:
    """**一条手抄的期望值**：固定种子那一局的四项 == 手抄的常量。

    **手抄，不是实现算出来的**（M0-1 在测试层的翻版）——见下一条守卫。
    """
    results = [result for result in gate_batch.results if result.seed == HAND_SEED]
    assert results, "这一批里没有手抄那一局的种子"

    result = results[0]
    assert result.original == HAND_EXPECTED_FOUR_ITEMS, (
        f"手抄的四项对不上：实际 {result.original.as_dict()}"
    )
    assert result.replayed == HAND_EXPECTED_FOUR_ITEMS, (
        f"重放的四项对不上：实际 {result.replayed.as_dict()}"
    )


def test_m2_replay_four_items_the_expectation_is_transcribed_not_computed() -> None:
    """**独立性守卫**：那条期望值必须**以字面量出现在本文件的源码里**。

    **plan-M2 §5.3 的 S13 反向验证**："把手算的期望值改成实现算出来的值"
    → **自证被打破** → 本守卫当场变红。

    **为什么查源码**：期望值改成"实现算出来的"之后，**它与实现永远相等**——
    门判据从此什么都不验。要抓住这种破坏，只能查**它是不是还在源码里写着**
    （写字面量 = 手抄；调用实现 = 不手抄）。
    """
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert HAND_EXPECTED_LITERAL.search(source), (
        "手抄的四项期望值没有以字面量出现在本文件里——它被改成实现算出来的了（自证被打破）"
    )


# ---------------------------------------------------------------------------
# G2-2 · 抛错订阅者被隔离
# ---------------------------------------------------------------------------


def test_m2_subscriber_isolation_the_kernel_survives_a_throwing_subscriber() -> None:
    """订阅者抛错 → **内核照跑** · **其余订阅者不受影响** · **错误事件照常发出**。

    **三件事都要成立**（AC-4 · FR-3.4 · 架构 §4.2）：

    1. 抛错的订阅者被转成一条 `kernel:error`（负载四字段齐全）；
    2. 其它订阅者**一条不少**地收到了原事件（与"没有抛错订阅者"的那一趟
       **事件类型序列逐条相等**）；
    3. **内核照常推进**——帧照跑、状态照变。
    """

    def _collect(with_thrower: bool) -> tuple[list[str], list[Envelope], HeadlessLoop]:
        assembly = assemble(seed=SCENARIO_SEED)
        bus = assembly.bus
        healthy: list[Envelope] = []

        def boom(envelope: Envelope) -> None:
            """必然抛错的订阅者。"""
            raise RuntimeError("这个订阅者必然抛错")

        if with_thrower:
            bus.subscribe("*", boom, name="必然抛错的订阅者")
        bus.subscribe("*", healthy.append, name="正常订阅者")

        loop = HeadlessLoop(assembly)
        loop.start()
        for _ in range(ISOLATION_FRAMES):
            loop.run_frame()
        return [envelope.type for envelope in healthy], healthy, loop

    baseline_types, _baseline, baseline_loop = _collect(with_thrower=False)
    broken_types, healthy, loop = _collect(with_thrower=True)

    # ③ 内核照跑：帧照走、状态照变。
    assert loop.frames == ISOLATION_FRAMES, "内核没跑满帧——被订阅者的异常带走了"
    assert baseline_loop.frames == ISOLATION_FRAMES

    # ① 错误事件照常发出，负载四字段齐全。
    errors = [envelope for envelope in healthy if envelope.type == "kernel:error"]
    assert errors, "抛错的订阅者没有产生 kernel:error"
    payload = dict(errors[0].payload)
    assert payload["subscriber"] == "必然抛错的订阅者", payload
    assert payload["eventType"], payload
    assert "这个订阅者必然抛错" in str(payload["error"]), payload

    # ② 其余订阅者不受影响：**内核事件一条不少、类型序列逐条相等**。
    #    （`kernel:error` 与 `log:entry` 是"多出来的那些"，不比。）
    def _kernel_events(types: list[str]) -> list[str]:
        return [name for name in types if not name.startswith(("kernel:", "log:", "metric:"))]

    assert _kernel_events(broken_types) == _kernel_events(baseline_types), (
        "抛错的订阅者影响了其它订阅者收到的事件"
    )
    assert _kernel_events(broken_types), "正常订阅者一条内核事件都没收到"


# ---------------------------------------------------------------------------
# G2-3 · 关闭全部可观测系统后内核逐帧一致
# ---------------------------------------------------------------------------


def test_m2_observability_purity_the_kernel_runs_the_same_with_and_without_it() -> None:
    """开着全部可观测系统 vs 全关 → **逐帧棋盘指纹序列全等**。

    **可观测是订阅者，不是内核的一部分**（M2-D1 · N7）：三个系统只订阅事件、
    不写状态——**关掉它们，内核的行为一个字节都不变**。

    **逐帧比而不是只看最终态**（spec §9.1 `G2-3` 的判据）：把两条事件从一帧挪
    到另一帧，最终态照样一样。
    """
    with_observability = play_to_game_over(assemble(seed=SCENARIO_SEED), ReferencePolicy())
    without = play_to_game_over(
        assemble(seed=SCENARIO_SEED, observability_enabled=False),
        ReferencePolicy(),
    )

    assert with_observability.fingerprints, "一帧都没跑到"
    assert with_observability.fingerprints == without.fingerprints, (
        "关掉可观测系统后内核的逐帧指纹变了——可观测污染了内核"
    )


# ---------------------------------------------------------------------------
# G2-4 · 千局模拟总时长 ≤ 30 分钟
# ---------------------------------------------------------------------------


def test_m2_thousand_games_budget_the_batch_fits_in_thirty_minutes(
    thousand_batch: Batch,
) -> None:
    """1000 局模拟的**墙钟总时长 ≤ 30 分钟**（`G2-4` · NFR-2 · AC-5 · AC-7）。

    **为什么是多进程**（ADR-018）：本机一局约 3.3 秒 → **串行跑 1000 局要
    54 分钟**，超预算；**并行跑约 3.5 分钟**。**"关闭事件分发"的快速模式被
    明确否决**（那会破坏事件完备性）。

    **这个数是保守的**：它把**重放**的时间也算进去了，而预算只要求"模拟"。
    """
    assert len(thousand_batch.results) == THOUSAND_GAMES, "这一批的局数不对"
    assert thousand_batch.seconds <= THOUSAND_BUDGET_SECONDS, (
        f"1000 局跑了 {thousand_batch.seconds:.1f}s，超预算 {THOUSAND_BUDGET_SECONDS}s"
    )


# ---------------------------------------------------------------------------
# G2-5 · 连续 1000 局回放指纹零差异
# ---------------------------------------------------------------------------


def test_m2_replay_1000_zero_diff_every_replayed_board_matches(
    thousand_batch: Batch,
) -> None:
    """连续 1000 局回放 → **逐局棋盘指纹零差异**（NFR-1 · ADR-076）。

    **零差异，不是"差异在容差内"**：指纹是棋盘结构的哈希，一格不同就是不同。

    **这一门单独立**（ADR-076）：`G2-1` 的 200 局**四项**全等（更强但只覆盖
    200 局），`G2-5` 的 1000 局**只比指纹**（更宽）。**两道门的命中名不许
    共用**——共用会让两道门的生死无从分辨。

    **与 `G2-4` 共用同一批局**（spec §7.3）：同机同时段一起跑。
    """
    assert len(thousand_batch.results) == THOUSAND_GAMES, "这一批的局数不对"

    mismatches = [
        f"种子 {result.seed}：原局 {result.original.fingerprint} ≠ "
        f"重放 {result.replayed.fingerprint}"
        for result in thousand_batch.results
        if result.original.fingerprint != result.replayed.fingerprint
    ]
    assert not mismatches, "有局重放后指纹不同：\n  " + "\n  ".join(mismatches)
