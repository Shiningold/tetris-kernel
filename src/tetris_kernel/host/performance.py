"""降级控制器（宿主层 · M2-S12 · X-3 · ADR-031 · NFR-2）。

## 三档固定顺序（ADR-031）

| 档 | 动作 | 谁执行 |
| --- | --- | --- |
| ① | **关闭调试事件** | 日志系统跳过 **debug 级**的事件 |
| ② | **降低日志级别** | 日志系统的阈值抬到 `info` |
| ③ | **关闭事件历史缓冲** | 总线不再留历史 |

**顺序固定，不按影响大小排**——降级是**逐级**的：超预算了先退一档，还超再退
一档。

**每一档都不伤指标系统**（ADR-031）：指标系统**一条事件都不少**——它聚合的是
**事件**，不是日志条目。

**③ 关的是缓冲，不是分发**（ADR-031 明写否决"关闭事件分发"）：订阅者照常收
到每一条事件，**状态变更仍伴随事件**——事件完备性不受损。

## 滞回（ADR-031）

- **降档**：P99 > **1.6ms**（**600 帧窗口**）→ 降一档；
- **升档**：P99 < **1.0ms** **持续 1800 帧** → 升一档；
- **滞回带**：P99 落在 **[1.0, 1.6]** 之间 → **什么都不做**（不升也不降）。

**判据用 P99 不用均值**（ADR-031）：均值会被**大部分快帧稀释**——一帧 50ms
的尖峰在 600 帧里的均值只有 0.08ms，**均值看不见它**，而用户看得见。

**阈值是工程预算，不是某台机的测量值**（NFR-2 的 1.6ms = 60fps 下帧步长的
10%）——**跨时段的绝对值不可引用**（plan-M1 §7 R10）。

## 手动档位：只开放第 ① 档

`set_manual_tier()` **只接受 0 与 1**。第 ③ 档**不暴露给用户手关**——用户手关
会让时间旅行调试"看起来坏了"（ADR-031 的原话）。

**手动档位是"下限"**：自动降级照样能在它之上继续降（用户说"至少关掉调试事
件"，没说"别再降了"）。**反过来不成立**——手动**不能**把自动降下来的第 ③ 档
按回去。

## 帧开销从哪来

`observe_frame(cost_ms)` —— **由调用方喂**。M2 是**无头批次**，没有实时宿主
去测每帧耗时；**接线留给 M3 的实时宿主**（那才是真正"用户手关"的地方）。

## M4-S8：喂帧开销的那条线接上了

**实时宿主每推进一帧就喂一次**（`measure_frame`）——**测的只是"推进"这一段的
耗时**：**渲染不计入逻辑帧预算**（NFR-2 明写：渲染 · 日志落盘 · 指标导出 · 网络
IO 一律不计入）。

**两个口径混了就是错账**：把渲染算进去，**画面慢就会误判成"内核慢"** → **降级
降错了地方**。
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable

from ..bus.bus import EventBus
from ..systems.log import LogSystem

#: 不降级。
TIER_NONE = 0

#: 第 ① 档：**关闭调试事件**。
TIER_DEBUG_EVENTS = 1

#: 第 ② 档：**降低日志级别**。
TIER_LOG_LEVEL = 2

#: 第 ③ 档：**关闭事件历史缓冲**。
TIER_EVENT_HISTORY = 3

#: 三档的稳定序列（**固定顺序** · ADR-031）。
TIERS: tuple[int, ...] = (TIER_NONE, TIER_DEBUG_EVENTS, TIER_LOG_LEVEL, TIER_EVENT_HISTORY)

#: 降档阈值：P99 > **1.6ms**（**600 帧窗口** · ADR-031）。
#:
#: **这是工程预算**（NFR-2：60fps 下单帧逻辑 ≤ 帧步长的 10% = 1.6ms），
#: **不是某台机的测量值**——跨时段的绝对值不可引用（plan-M1 §7 R10）。
DEGRADE_P99_MS = 1.6

#: 降档窗口的帧数（**600 帧** · ADR-031）。
DEGRADE_WINDOW_FRAMES = 600

#: 升档阈值：P99 < **1.0ms**。
RECOVER_P99_MS = 1.0

#: 升档要**持续**的帧数（**1800 帧** · ADR-031）。
RECOVER_FRAMES = 1800

#: 第 ② 档把日志阈值抬到哪一级。
DEGRADED_MIN_LEVEL = "info"

#: `G2-4` 的预算：千局模拟总时长 ≤ **30 分钟**（秒）。
#:
#: **这是工程预算，不是某台机的测量值**——同 `DEGRADE_P99_MS` 一个道理
#: （NFR-2 的 1.6ms 是预算，`measure_frame_cost.py` 量的是本机的真实值）。
#:
#: **它住在这里**：降级阈值（`DEGRADE_P99_MS`）与千局预算都是**性能预算**，
#: **一个地方一处真相**——测量工具与门判据用例都从这里取，谁也不许另抄一份。
THOUSAND_BUDGET_SECONDS = 30 * 60

#: 千局基准的**种子表起点**（**固定**：种子是基准的一部分，不是随手挑的）。
#:
#: **工具与门判据共用这一张种子表**：同一个起点 + 同一个局数 = **同一批局**。
#: **为什么必须同一批局**：一局的帧数随种子在 1755–3300 之间波动（参考策略
#: 局），种子表不同 → 总工作量不同 → 两个数**不可比**（跨批次的绝对值不可引用）。
#:
#: **它住在这里**的理由同 `THOUSAND_BUDGET_SECONDS`：**千局基准的定义**
#: （局数起点 · 预算 · 种子表）只有一处真相，测量工具与门判据用例都从这里取。
THOUSAND_BASE_SEED = 20261000


class FrameCostWindow:
    """帧开销的滚动窗口（**P99 的口径** · ADR-031）。

    - `size`：窗口最多留多少个样本（**环形**：新的进来，最旧的挤出去）。

    **窗口没满就不给 P99**——半个窗口的 P99 不是 P99（600 个样本的 P99 与 30
    个样本的 P99 不是一回事）。**不拿半个窗口去降级**——那会让开局的几帧就
    把档位推上去。
    """

    def __init__(self, size: int = DEGRADE_WINDOW_FRAMES) -> None:
        if size < 1:
            raise ValueError(f"窗口大小至少为 1，实为 {size}")
        self._size = size
        self._samples: deque[float] = deque(maxlen=size)

    @property
    def size(self) -> int:
        """窗口的容量（帧数）。"""
        return self._size

    @property
    def count(self) -> int:
        """当前样本数。"""
        return len(self._samples)

    @property
    def full(self) -> bool:
        """窗口**满**了没有。"""
        return len(self._samples) == self._size

    def add(self, cost_ms: float) -> None:
        """记一个样本（**最新的进来，最旧的挤出去**）。"""
        self._samples.append(cost_ms)

    def p99(self) -> float | None:
        """窗口的 **P99**（**没满就是 `None`**）。

        **口径**：排序后取第 `ceil(0.99 × n) − 1` 个（0 基）——600 个样本时是
        第 594 个（**最慢的那 1% 之外**）。
        """
        if not self.full:
            return None
        ordered = sorted(self._samples)
        index = min(len(ordered) - 1, math.ceil(0.99 * len(ordered)) - 1)
        return ordered[index]

    def mean(self) -> float | None:
        """窗口的**均值**（**只用于对照**——**判据不用它** · ADR-031）。

        **它在这里只是为了能被用例拿来对照**：均值会把尖峰稀释掉，正是 ADR-031
        明写"判据用 P99 不用均值"的原因。
        """
        if not self._samples:
            return None
        return sum(self._samples) / len(self._samples)


class DegradationController[V]:
    """降级控制器（**三档固定顺序 · 带滞回** · ADR-031）。

    - `bus`：事件总线（第 ③ 档关它的历史缓冲）；
    - `log_system`：日志系统（第 ① 档关 debug 事件 · 第 ② 档抬阈值）——
      **`None` = 没有日志系统**（那两档就无事可做，其余照常）；
    - `degrade_p99_ms` / `recover_p99_ms` / `recover_frames`：阈值与窗口
      （**可注入**：用例要能造出"刚好在阈值附近"的场景）；
    - `window`：帧开销窗口的大小。

    **它只管"现在该在第几档"，不管"怎么测帧开销"**——那由调用方喂
    （`observe_frame`）。

    **泛型参数 `V` 是总线的视图类型**——它必须与 `bus` / `log_system` 一致
    （`EventBus` 与 `LogSystem` 的 `V` 都是**不变**的，`EventBus[Snapshot]`
    不能当 `EventBus[object]` 用）。
    """

    def __init__(
        self,
        *,
        bus: EventBus[V],
        log_system: LogSystem[V] | None = None,
        window: FrameCostWindow | None = None,
        degrade_p99_ms: float = DEGRADE_P99_MS,
        recover_p99_ms: float = RECOVER_P99_MS,
        recover_frames: int = RECOVER_FRAMES,
    ) -> None:
        if degrade_p99_ms <= recover_p99_ms:
            raise ValueError(f"降档阈值必须高于升档阈值：{degrade_p99_ms} ≤ {recover_p99_ms}")
        if recover_frames < 1:
            raise ValueError(f"升档持续帧数至少为 1，实为 {recover_frames}")
        self._bus = bus
        self._log_system = log_system
        self._window = window if window is not None else FrameCostWindow()
        self._degrade_p99_ms = degrade_p99_ms
        self._recover_p99_ms = recover_p99_ms
        self._recover_frames = recover_frames
        self._auto_tier = TIER_NONE
        self._manual_tier = TIER_NONE
        self._recover_streak = 0
        # **原阈值**（第 ② 档抬的是它，不是"抬到 trace"）——恢复时还原回去。
        self._base_min_level = log_system.min_level if log_system is not None else ""
        self._apply()

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def tier(self) -> int:
        """**生效**的档位（手动下限与自动档位取大）。"""
        return max(self._manual_tier, self._auto_tier)

    @property
    def auto_tier(self) -> int:
        """**自动**判出来的档位（滞回规则管的）。"""
        return self._auto_tier

    @property
    def manual_tier(self) -> int:
        """**手动**设的档位（只可能是 0 或 1）。"""
        return self._manual_tier

    @property
    def p99(self) -> float | None:
        """当前窗口的 P99（**没满是 `None`**）。"""
        return self._window.p99()

    @property
    def recover_streak(self) -> int:
        """**连续**低于升档阈值的帧数（升档要攒够 1800 帧）。"""
        return self._recover_streak

    @property
    def window(self) -> FrameCostWindow:
        """帧开销窗口（**只读属性**：外部拿它看，不改它）。"""
        return self._window

    # ------------------------------------------------------------------
    # 喂帧开销
    # ------------------------------------------------------------------

    def observe_frame(self, cost_ms: float) -> None:
        """喂一帧的开销（**毫秒**），并按滞回规则调整档位。

        **判据用 P99 不用均值**（ADR-031）——见 `FrameCostWindow` 的说明。
        """
        if cost_ms < 0:
            raise ValueError(f"帧开销不能为负：{cost_ms}")
        self._window.add(cost_ms)
        self._evaluate()

    # ------------------------------------------------------------------
    # 手动档位
    # ------------------------------------------------------------------

    def set_manual_tier(self, tier: int) -> None:
        """手动设档（**只开放第 ① 档** · ADR-031）。

        **只接受 0 与 1**：手动只开放第 ① 档；**第 ③ 档不暴露给用户手关**
        ——用户手关会让时间旅行调试"看起来坏了"（ADR-031 的原话）。

        **它是"下限"**：自动降级照样能在它之上继续降。**反过来不成立**——
        手动**不能**把自动降下来的第 ③ 档按回去（那正是"第 ③ 档不手关"）。
        """
        if tier not in (TIER_NONE, TIER_DEBUG_EVENTS):
            raise ValueError(
                f"手动只开放第 ① 档（0 = 不降级 · 1 = 关闭调试事件），收到 {tier}"
                "（第 ② / ③ 档不开放给用户手关 · ADR-031）"
            )
        self._manual_tier = tier
        self._apply()

    # ------------------------------------------------------------------
    # 内部：判据与执行
    # ------------------------------------------------------------------

    def _evaluate(self) -> None:
        """按滞回规则调档（**一次一档**）。"""
        p99 = self._window.p99()
        if p99 is None:
            # **窗口没满**——半个窗口的 P99 不是 P99（不开局就降档）。
            return
        if p99 > self._degrade_p99_ms:
            self._recover_streak = 0
            if self._auto_tier < TIER_EVENT_HISTORY:
                self._auto_tier += 1
                self._apply()
            return
        if p99 < self._recover_p99_ms:
            self._recover_streak += 1
            if self._recover_streak >= self._recover_frames:
                self._recover_streak = 0
                if self._auto_tier > TIER_NONE:
                    self._auto_tier -= 1
                    self._apply()
            return
        # **滞回带**：不升也不降——但**升档计数要清零**（"回落就不升"）。
        self._recover_streak = 0

    def _apply(self) -> None:
        """把当前档位**落到各个部件上**（**逐档累加**）。"""
        tier = self.tier
        log_system = self._log_system
        if log_system is not None:
            log_system.set_debug_events_enabled(tier < TIER_DEBUG_EVENTS)
            log_system.set_min_level(
                self._base_min_level if tier < TIER_LOG_LEVEL else DEGRADED_MIN_LEVEL
            )
        # **③ 关的是历史缓冲，不是分发**（ADR-031）：订阅者照常收到事件。
        self._bus.set_history_enabled(tier < TIER_EVENT_HISTORY)


def measure_frame[T](action: Callable[[], T]) -> tuple[T, float]:
    """**测一帧的动作耗时**（**毫秒**）→ 返回 `(动作的返回值 · 耗时)`。

    **测的只是动作本身**：**渲染在动作之外** → **渲染不计入逻辑帧预算**
    （NFR-2）——**两个口径混了就是错账**（画面慢会被误判成"内核慢"）。

    **为什么在这里**：**喂帧开销的那条线归降级这一档管**（ADR-031 · M4-S8
    的遗留接线）——**测量与判档在同一个地方**，**别让两处各测各的**。
    """
    started = time.monotonic()
    result = action()
    return result, (time.monotonic() - started) * 1000


__all__ = [
    "DEGRADE_P99_MS",
    "DEGRADE_WINDOW_FRAMES",
    "DEGRADED_MIN_LEVEL",
    "RECOVER_FRAMES",
    "RECOVER_P99_MS",
    "THOUSAND_BASE_SEED",
    "THOUSAND_BUDGET_SECONDS",
    "TIERS",
    "TIER_DEBUG_EVENTS",
    "TIER_EVENT_HISTORY",
    "TIER_LOG_LEVEL",
    "TIER_NONE",
    "DegradationController",
    "FrameCostWindow",
    "measure_frame",
]
