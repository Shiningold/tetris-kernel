"""指标系统：按事件聚合统计，每帧采样广播一次。

**只消费事件，不写状态**（M2-D1）：它连只读视图都不取（单参数订阅入口）。
**落盘经 sink**（M2-D2 · ADR-064）：它**知道有 sink，不知道路径**。

**口径写在指标系统内**（与 ADR-046 同一把尺子）——事件只说"发生了什么"，
"怎么统计"是消费方的事。
"""

from .aggregate import (
    CLEAR_METRIC_NAMES,
    METRIC_NAMES,
    Aggregate,
    Metrics,
    MetricValue,
)
from .system import (
    CLOCK_TICK_EVENT,
    DEFAULT_SAMPLE_LIMIT,
    METRIC_SAMPLED_EVENT,
    MetricsSystem,
    Sink,
)

__all__ = [
    "CLEAR_METRIC_NAMES",
    "CLOCK_TICK_EVENT",
    "DEFAULT_SAMPLE_LIMIT",
    "METRIC_NAMES",
    "METRIC_SAMPLED_EVENT",
    "Aggregate",
    "MetricValue",
    "Metrics",
    "MetricsSystem",
    "Sink",
]
