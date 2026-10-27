"""指标系统（系统层 · M2-S7 · FR-7.2）。

## 它是什么

**一个普通订阅者**：订阅全局，把事件流累加成指标，**每帧采样广播一次**。

- **只消费事件，不写状态**（M2-D1）：它**没有任何写内核状态的路径**——
  连视图都不取（`subscribe` 单参数入口，**拿不到视图**）；
- **口径写在指标系统内**（与 ADR-046 同一把尺子）：事件只说"发生了什么"，
  "怎么统计"是消费方的事；
- **落盘经 sink**（M2-D2 · ADR-064）：**不知道路径**，只把字节交出去。

## 采样：每个 `clock:tick` 一条

**有 tick 的帧一条，一条不多**（plan-M2 S7：「采样必须每帧一条，否则按帧对齐
的下游全乱」）。暂停期没有 tick，也就**没有采样**——那不是"少采了"，是
"那一帧本来就没开始"。

**采样值 = 进入该帧时的累计值**：`clock:tick` 在**帧首**派发，帧推进五步
还没跑，所以这条采样反映的是**上一帧末**的局面。

**不设 flush**：采样本身就是逐帧的，没有"攒一批再发"的需要。

## 自产采样不再进采样（**不自激**）

指标系统订阅全局，而它自己发的 `metric:sampled` 也是全局事件——**不跳过它
就会无限自激**（每条采样产生一条采样，产生一条采样……）。所以：
**`metric:sampled` 一律不计入指标**。
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable

from ...bus.bus import EventBus
from ...bus.envelope import Envelope
from ...bus.subscriber import Subscription
from ...kernel.drafts import METADATA_CAUSE, EventDraft, make_draft
from .aggregate import METRIC_NAMES, Aggregate, Metrics

#: 采样事件（契约已登记 · 产出者 `metrics`）。
METRIC_SAMPLED_EVENT = "metric:sampled"

#: 帧节拍事件（**采样的触发源**）。
CLOCK_TICK_EVENT = "clock:tick"

#: 落盘出口（**宿主注入的可调用对象** · ADR-064）。
#:
#: **为什么在这里再写一遍别名**：`systems/` **不能** import `host/`（依赖方向），
#: 拿不到 `host.assembly.Sink`；它也**不能** import `systems.log`（系统之间
#: 互不直调）。它是**同一个可调用类型**（结构化类型），这里自己写一份即可
#: ——**别名不是契约，不进基线**。
type Sink = Callable[[bytes], None]

#: 默认保留的采样条数（**环形**——面板只看最近几条，全量留着是内存负担）。
DEFAULT_SAMPLE_LIMIT = 256


class MetricsSystem[V]:
    """指标系统：**普通订阅者**（只消费事件 · M2-D1）。

    **装配**：宿主造总线与 sink，再造本系统——它自己订阅总线。

    - `bus`：事件总线（**只订不写内核状态**）；
    - `sink`：**落盘出口**（`None` = 不落盘）；**路径是宿主的事**；
    - `sample_limit`：保留多少条采样（**环形**）。
    """

    def __init__(
        self,
        bus: EventBus[V],
        sink: Sink | None = None,
        *,
        sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    ) -> None:
        if sample_limit < 1:
            raise ValueError(f"sample_limit 至少为 1，实为 {sample_limit}")
        self._bus = bus
        self._sink = sink
        self._aggregate = Aggregate()
        self._samples: deque[tuple[int, Metrics]] = deque(maxlen=sample_limit)
        # **订阅全局**：新事件自动计入指标，**无需额外接线**。
        self._subscription = bus.subscribe("*", self._on_event, name="指标系统")

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def samples(self) -> tuple[tuple[int, Metrics], ...]:
        """最近的采样（**由旧到新**；每条是「帧号 · 指标」）。"""
        return tuple(self._samples)

    @property
    def subscriptions(self) -> tuple[Subscription[V], ...]:
        """本系统的订阅句柄（**M3 摘系统时要退订的就是它**）。

        **暴露它不是为了方便**——是 M3 的硬要求：**退订是系统级语义**，注册表
        得拿得到句柄才摘得干净（只退订不到 = 摘不干净）。
        """
        return (self._subscription,)

    @property
    def metric_names(self) -> tuple[str, ...]:
        """指标名清单（**固定顺序**）。"""
        return METRIC_NAMES

    def latest(self) -> Metrics | None:
        """最近一次采样的指标（**还没采样过就是 `None`**）。"""
        if not self._samples:
            return None
        return self._samples[-1][1]

    # ------------------------------------------------------------------
    # 订阅回调
    # ------------------------------------------------------------------

    def _on_event(self, envelope: Envelope) -> None:
        """吃一条事件；**遇到 tick 就采样**（**只读事件，不碰任何状态**）。"""
        # **自产采样不进采样**——否则无限自激。
        if envelope.type == METRIC_SAMPLED_EVENT:
            return
        self._aggregate.observe(envelope)
        if envelope.type == CLOCK_TICK_EVENT:
            self._sample(envelope)

    def _sample(self, envelope: Envelope) -> None:
        """采一次样：**先广播，再落盘**。

        **先广播**：总线事件是主输出，落盘是副输出——副输出坏了不该把主输出
        一起带走。
        """
        self._aggregate.count_frame()
        frame = _frame_of(envelope)
        metrics = self._aggregate.sample()
        self._samples.append((frame, metrics))
        self._bus.publish(_sampled_draft(frame, metrics))
        self._write(frame, metrics)

    def _write(self, frame: int, metrics: Metrics) -> None:
        """把一次采样交给 sink（**JSON 行**——一行一条，机器可读、可 diff）。"""
        if self._sink is None:
            return
        line = json.dumps(
            {"frame": frame, "metrics": dict(metrics)},
            ensure_ascii=False,
            sort_keys=True,
        )
        self._sink((line + "\n").encode("utf-8"))


def _frame_of(envelope: Envelope) -> int:
    """采样帧号 = **`clock:tick` 负载里的 `frame`**（取不到就用信封的帧号）。"""
    for name, value in envelope.payload:
        if name == "frame" and isinstance(value, int) and not isinstance(value, bool):
            return value
    return envelope.frame


def _sampled_draft(frame: int, metrics: Metrics) -> EventDraft:
    """构造 `metric:sampled` 草稿（**负载两字段照抄契约声明**）。

    用 `make_draft` 构造——**字段名与顺序和契约逐条相等由它当场校验**（写错
    当场报错，不等面板上线才暴露）。
    """
    return make_draft(
        METRIC_SAMPLED_EVENT,
        (("frame", frame), ("metrics", metrics)),
        frame,
        ((METADATA_CAUSE, None),),
    )


__all__ = [
    "CLOCK_TICK_EVENT",
    "DEFAULT_SAMPLE_LIMIT",
    "METRIC_SAMPLED_EVENT",
    "MetricsSystem",
    "Sink",
]
