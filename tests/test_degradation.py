"""降级控制器测试（M2-S12 · ADR-031 · X-3 · NFR-2）。

覆盖 plan-M2.md §S12 的验证方案：

1. **降级顺序固定**：连续触发 → **依次** ① → ② → ③（**不是按影响大小排**）；
2. **滞回带**：P99 落在 [1.0, 1.6] → **不升不降**，且**升档计数清零**；
3. **升档要攒够 1800 帧**：不足就回落 → **不升**；
4. **手动只开放第 ① 档**：第 ② / ③ 档手关 → **当场报错**；
5. **手动是下限**：自动照样能在它之上继续降；手动**不能**把自动降下来的按回去；
6. **指标系统不受影响**：降到第 ③ 档 → **指标逐项与未降级时相等**；
7. **事件完备性不受损**：降到第 ③ 档 → **状态变更仍伴随事件**；
8. **判据用 P99 不用均值**：**尖峰**看得见，均值看不见。

## 反向验证的判据落在哪一条上

| 反向验证 | 破了哪条 | 为什么这条抓得住 |
| --- | --- | --- |
| A · 把降级顺序倒过来 | 第 1 条 | 第 ① 档里断言**日志阈值没抬**且**历史缓冲没关** |
| B · 把判据改成均值 | 第 8 条 | 尖峰占窗口 1.17% → **均值 0.59ms 在预算内、P99 50ms 在预算外** |
| C · 把第 ③ 档暴露给用户手关 | 第 4 条 | `set_manual_tier(3)` 必须**当场报错** |

## 为什么有一部分用例把窗口缩到 1 格

**滞回与升档计数是控制器的逻辑，不是窗口统计的逻辑**——窗口缩到 1 格之后
P99 就等于「最近一帧」，**没有滚动窗口的滞后**，用例只盯「攒够 1800 帧没有」
这一件事。**P99 口径本身由第 8 条用默认窗口验**（默认窗口的 600 格与 1 格
在这里不是一回事：第 8 条靠的正是「600 个样本里的前 1% 之外」这个口径）。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tetris_kernel.bus.bus import EventBus
from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.host import Assembly, HeadlessLoop, assemble
from tetris_kernel.host.performance import (
    DEGRADE_P99_MS,
    DEGRADE_WINDOW_FRAMES,
    DEGRADED_MIN_LEVEL,
    RECOVER_FRAMES,
    TIER_DEBUG_EVENTS,
    TIER_EVENT_HISTORY,
    TIER_LOG_LEVEL,
    TIER_NONE,
    DegradationController,
    FrameCostWindow,
)
from tetris_kernel.kernel.drafts import EventDraft, make_draft
from tetris_kernel.systems.log import DEBUG_LEVEL, LogSystem, MemoryTarget
from tetris_kernel.systems.metrics import MetricsSystem

#: 装配用的固定种子。
SEED = 20261026

#: **超预算**的帧开销（高于降档阈值 1.6ms）。
OVER_BUDGET_MS = 2.0

#: **低于升档阈值**的帧开销。
UNDER_BUDGET_MS = 0.5

#: **滞回带**里的帧开销（介于 1.0 与 1.6 之间）。
IN_BAND_MS = 1.2

#: 日志系统自己产的事件（**降级会改变它的条数**——那是降级的目的）。
LOG_ENTRY_EVENT = "log:entry"


# ---------------------------------------------------------------------------
# 夹具与替身
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rig:
    """一套最小装置：总线 · 日志系统 · 内存目标 · 降级控制器。"""

    bus: EventBus[object]
    log_system: LogSystem[object]
    target: MemoryTarget
    controller: DegradationController[object]


def _rig(*, window: FrameCostWindow | None = None) -> Rig:
    """造一套最小装置（**不带视图的总线**——降级用不到视图）。"""
    bus = EventBus[object]()
    target = MemoryTarget()
    log_system = LogSystem(bus, (target,))
    controller = DegradationController(bus=bus, log_system=log_system, window=window)
    return Rig(bus=bus, log_system=log_system, target=target, controller=controller)


def _board_changed(frame: int = 1) -> EventDraft:
    """一条 **debug 级**的事件（默认级别映射下 `board:changed` 是 debug）。"""
    return make_draft(
        "board:changed",
        (("cellChanges", ((0, 0, 1),)), ("cause", "lock")),
        frame,
    )


def _rng_next(frame: int = 1) -> EventDraft:
    """一条 **trace 级**的事件（`rng:next` 默认 trace · ADR-060）。"""
    return make_draft(
        "rng:next",
        (("value", 0.5), ("seq", 1), ("stateFingerprint", "fp")),
        frame,
    )


def _degrade_to(controller: DegradationController[object], target_tier: int) -> None:
    """把档位推到目标档（**喂超预算的帧开销**）。"""
    for _ in range(DEGRADE_WINDOW_FRAMES + target_tier + 2):
        if controller.tier >= target_tier:
            return
        controller.observe_frame(OVER_BUDGET_MS)
    raise AssertionError(f"降不到第 {target_tier} 档（当前第 {controller.tier} 档）")


def _kernel_event_types(assembly: Assembly, frames: int = 5) -> tuple[str, ...]:
    """跑几帧，取**内核与宿主**发的事件类型序列（**去掉 `log:entry`**）。

    **为什么去掉 `log:entry`**：日志条目**本来就会随降级变少**（第 ① 档关
    debug、第 ② 档抬阈值）——那是降级的**目的**。要验的是**内核事件一条不少**。
    """
    received: list[Envelope] = []
    assembly.bus.subscribe("*", received.append)
    loop = HeadlessLoop(assembly)
    loop.start()
    for _ in range(frames):
        loop.run_frame()
    return tuple(envelope.type for envelope in received if envelope.type != LOG_ENTRY_EVENT)


def _latest_metrics(assembly: Assembly, frames: int = 5) -> tuple[tuple[str, float], ...]:
    """跑几帧，取**最后一次采样**的指标。"""
    loop = HeadlessLoop(assembly)
    loop.start()
    for _ in range(frames):
        loop.run_frame()
    systems = [
        system for system in assembly.observable_systems if isinstance(system, MetricsSystem)
    ]
    assert systems, "指标系统没装上"
    latest = systems[0].latest()
    assert latest is not None, "指标系统一次都没采样"
    return latest


# ---------------------------------------------------------------------------
# 1 · 窗口没满不降档
# ---------------------------------------------------------------------------


def test_the_window_must_be_full_before_any_degradation() -> None:
    """**窗口没满就不给 P99**——半个窗口的 P99 不是 P99。

    **不拿半个窗口去降级**：那会让开局的几帧就把档位推上去。
    """
    rig = _rig()

    for _ in range(DEGRADE_WINDOW_FRAMES - 1):
        rig.controller.observe_frame(OVER_BUDGET_MS)

    assert rig.controller.p99 is None, "窗口没满就给出了 P99"
    assert rig.controller.tier == TIER_NONE, "窗口没满就降档了"

    rig.controller.observe_frame(OVER_BUDGET_MS)

    assert rig.controller.p99 == OVER_BUDGET_MS
    assert rig.controller.tier == TIER_DEBUG_EVENTS, "窗口满了还没降档"


# ---------------------------------------------------------------------------
# 2 · 降级顺序固定（反向验证 A 的判据）
# ---------------------------------------------------------------------------


def test_the_degradation_order_is_fixed() -> None:
    """降级**依次**是 ① → ② → ③——**顺序固定**（不是按影响大小排）。"""
    rig = _rig()
    received: list[Envelope] = []
    rig.bus.subscribe("*", received.append)

    # 降档前：debug 级事件**照常**产日志条目（先证明这条事件确实会被记）。
    rig.bus.publish(_board_changed())
    assert [entry for entry in rig.target.entries if entry.level == DEBUG_LEVEL], (
        "降级前就没有 debug 条目——这一条测不到降级"
    )

    _degrade_to(rig.controller, TIER_DEBUG_EVENTS)

    # 第 ① 档：debug 级事件**不再产日志条目**……
    entries_before = len(rig.target.entries)
    rig.bus.publish(_board_changed())
    assert len(rig.target.entries) == entries_before, "第 ① 档没关掉 debug 级日志"

    # ……但**事件照常派发**（分发不是这一档关的东西）。
    assert len([e for e in received if e.type == "board:changed"]) == 2, "第 ① 档把事件分发也关了"

    # 第 ① 档**不碰**日志阈值，也**不碰**历史缓冲（那是第 ② / ③ 档的事）。
    assert rig.log_system.min_level == "trace", "第 ① 档不该动日志阈值"
    assert rig.bus.history_enabled is True, "第 ① 档不该关历史缓冲"

    # 再超一次 → 第 ② 档：日志阈值抬到 info。
    _degrade_to(rig.controller, TIER_LOG_LEVEL)
    assert rig.log_system.min_level == DEGRADED_MIN_LEVEL, "第 ② 档没抬日志阈值"
    assert rig.bus.history_enabled is True, "第 ② 档不该关历史缓冲"

    # 再超一次 → 第 ③ 档：历史缓冲关闭。
    _degrade_to(rig.controller, TIER_EVENT_HISTORY)
    assert rig.bus.history_enabled is False, "第 ③ 档没关历史缓冲"


def test_the_first_tier_cuts_debug_but_not_trace() -> None:
    """第 ① 档只切 **debug 级**——**trace 级照常**。

    **这正是第 ① 档与第 ② 档的实质差别**：第 ② 档抬阈值到 `info`，**trace 级
    跟着一起关**（`rng:next` 是 trace 级 · ADR-060 明写它**不随调试事件一起
    关**）。第 ① 档是**类型级精确黑名单**，不是抬阈值。
    """
    rig = _rig()

    rig.bus.publish(_rng_next())
    assert [entry for entry in rig.target.entries if entry.message == "rng:next"], (
        "降级前 trace 级就不记了——用例前提变了"
    )

    _degrade_to(rig.controller, TIER_DEBUG_EVENTS)
    entries_before = len(rig.target.entries)
    rig.bus.publish(_rng_next())
    assert len(rig.target.entries) == entries_before + 1, "第 ① 档把 trace 级也关了"

    _degrade_to(rig.controller, TIER_LOG_LEVEL)
    entries_before = len(rig.target.entries)
    rig.bus.publish(_rng_next())
    assert len(rig.target.entries) == entries_before, "第 ② 档没把 trace 级关掉"


# ---------------------------------------------------------------------------
# 3 · 滞回带
# ---------------------------------------------------------------------------


def test_the_hysteresis_band_does_not_degrade() -> None:
    """滞回带里**不降档**——P99 落在 [1.0, 1.6] 之间就不动。"""
    rig = _rig()

    for _ in range(DEGRADE_WINDOW_FRAMES):
        rig.controller.observe_frame(IN_BAND_MS)

    assert rig.controller.p99 == IN_BAND_MS
    assert rig.controller.tier == TIER_NONE, "滞回带里降档了"
    assert rig.controller.recover_streak == 0, "滞回带里升档计数没清零"


def test_the_hysteresis_band_does_not_recover() -> None:
    """滞回带里**不升档**——P99 回到 1.2ms 也不许把档位推回去。"""
    rig = _rig()
    _degrade_to(rig.controller, TIER_EVENT_HISTORY)

    for _ in range(DEGRADE_WINDOW_FRAMES):
        rig.controller.observe_frame(IN_BAND_MS)

    assert rig.controller.p99 == IN_BAND_MS
    assert rig.controller.tier == TIER_EVENT_HISTORY, "滞回带里升档了"
    assert rig.controller.recover_streak == 0, "滞回带里升档计数没清零"


def test_recovering_needs_the_whole_streak_of_frames() -> None:
    """升档要**攒够 1800 帧**连续低于升档阈值——**不足就不升**。"""
    rig = _rig(window=FrameCostWindow(size=1))
    rig.controller.observe_frame(OVER_BUDGET_MS)
    assert rig.controller.tier == TIER_DEBUG_EVENTS

    for _ in range(RECOVER_FRAMES - 1):
        rig.controller.observe_frame(UNDER_BUDGET_MS)

    assert rig.controller.recover_streak == RECOVER_FRAMES - 1
    assert rig.controller.tier == TIER_DEBUG_EVENTS, "不足 1800 帧就升档了"

    rig.controller.observe_frame(UNDER_BUDGET_MS)

    assert rig.controller.tier == TIER_NONE, "攒够 1800 帧没升档"


def test_a_streak_that_breaks_does_not_recover() -> None:
    """升档计数**攒够之前回落** → 计数清零 → **不升档**。"""
    rig = _rig(window=FrameCostWindow(size=1))
    rig.controller.observe_frame(OVER_BUDGET_MS)
    assert rig.controller.tier == TIER_DEBUG_EVENTS

    for _ in range(100):
        rig.controller.observe_frame(UNDER_BUDGET_MS)
    assert rig.controller.recover_streak == 100

    rig.controller.observe_frame(IN_BAND_MS)

    assert rig.controller.recover_streak == 0, "回落了升档计数没清零"
    assert rig.controller.tier == TIER_DEBUG_EVENTS, "回落了还升档"


# ---------------------------------------------------------------------------
# 4 · 手动档位（反向验证 C 的判据）
# ---------------------------------------------------------------------------


def test_only_the_first_tier_is_open_to_the_user() -> None:
    """手动只开放第 ① 档——第 ② / ③ 档**不暴露给用户手关**。"""
    rig = _rig()

    rig.controller.set_manual_tier(TIER_NONE)
    assert rig.controller.tier == TIER_NONE

    rig.controller.set_manual_tier(TIER_DEBUG_EVENTS)
    assert rig.controller.tier == TIER_DEBUG_EVENTS
    assert rig.controller.manual_tier == TIER_DEBUG_EVENTS

    for tier in (TIER_LOG_LEVEL, TIER_EVENT_HISTORY):
        with pytest.raises(ValueError):
            rig.controller.set_manual_tier(tier)


def test_the_manual_tier_is_a_floor_not_a_ceiling() -> None:
    """手动档位是**下限**——自动照样能在它之上继续降。"""
    rig = _rig()
    rig.controller.set_manual_tier(TIER_DEBUG_EVENTS)

    _degrade_to(rig.controller, TIER_EVENT_HISTORY)

    assert rig.controller.manual_tier == TIER_DEBUG_EVENTS
    assert rig.controller.auto_tier == TIER_EVENT_HISTORY
    assert rig.controller.tier == TIER_EVENT_HISTORY, "手动档位不该挡住自动降级"


def test_the_manual_tier_cannot_push_back_an_automatic_degradation() -> None:
    """手动**不能**把自动降下来的第 ③ 档按回去。"""
    rig = _rig()
    _degrade_to(rig.controller, TIER_EVENT_HISTORY)

    rig.controller.set_manual_tier(TIER_NONE)

    assert rig.controller.manual_tier == TIER_NONE
    assert rig.controller.tier == TIER_EVENT_HISTORY, "手动把自动降下来的档按回去了"


# ---------------------------------------------------------------------------
# 5 · 判据用 P99 不用均值（反向验证 B 的判据）
# ---------------------------------------------------------------------------


def test_the_criterion_is_p99_not_the_mean() -> None:
    """判据用 **P99** 不用均值——**尖峰**看得见，均值看不见。

    **尖峰占窗口的 1.17%**（7 / 600）→ **P99 = 50ms**（超预算），
    **均值 = 0.59ms**（在预算内）。**换成均值就看不出这次尖峰**——这正是
    ADR-031 明写"判据用 P99 不用均值"的原因。
    """
    rig = _rig()

    for _ in range(593):
        rig.controller.observe_frame(0.01)
    for _ in range(7):
        rig.controller.observe_frame(50.0)

    assert rig.controller.p99 == 50.0, "P99 没看见尖峰"
    assert rig.controller.window.mean() < DEGRADE_P99_MS, "均值不该看见尖峰——用例前提变了"
    assert rig.controller.tier == TIER_DEBUG_EVENTS, "尖峰没触发降档（判据可能不是 P99）"


# ---------------------------------------------------------------------------
# 6 · 三档都不伤指标系统
# ---------------------------------------------------------------------------


def test_degrading_does_not_hurt_the_metrics_system() -> None:
    """降到第 ③ 档 → **指标系统照常工作**（ADR-031：三档都不伤指标系统）。

    **逐项相等**：不是"还在采样"，而是**采样出来的数字与没降级时一模一样**。
    """
    plain = assemble(seed=SEED)
    degraded = assemble(seed=SEED, degradation_enabled=True)
    controller = degraded.degradation_controller
    assert controller is not None, "装配层没装降级控制器"

    _degrade_to(controller, TIER_EVENT_HISTORY)
    assert degraded.bus.history_enabled is False

    assert _latest_metrics(degraded) == _latest_metrics(plain), "降级伤了指标系统"


# ---------------------------------------------------------------------------
# 7 · 事件完备性不受损
# ---------------------------------------------------------------------------


def test_degrading_keeps_the_events_complete() -> None:
    """降到第 ③ 档 → **状态变更仍伴随事件**（关的是历史缓冲，**不是分发**）。

    **逐条相等**：内核与宿主发的事件类型序列，降级前后**一模一样**。
    """
    plain = assemble(seed=SEED)
    degraded = assemble(seed=SEED, degradation_enabled=True)
    controller = degraded.degradation_controller
    assert controller is not None, "装配层没装降级控制器"

    _degrade_to(controller, TIER_EVENT_HISTORY)
    assert degraded.bus.history_enabled is False

    plain_types = _kernel_event_types(plain)
    degraded_types = _kernel_event_types(degraded)

    assert plain_types, "一帧都没跑到——用例前提变了"
    assert degraded_types == plain_types, "降级后事件流变了"


# ---------------------------------------------------------------------------
# 8 · 装配接线
# ---------------------------------------------------------------------------


def test_the_assembly_wires_the_controller_only_on_demand() -> None:
    """降级控制器**默认不装**——M2 是**无头批次**，喂帧开销的接线留给 M3。"""
    assert assemble(seed=SEED).degradation_controller is None, "降级控制器不该默认就装"

    assembly = assemble(seed=SEED, degradation_enabled=True)
    assert assembly.degradation_controller is not None, "显式要了却没装"
