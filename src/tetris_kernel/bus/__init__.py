"""总线层：事件唯一的传输与分发中枢。

信封封装、全局序号（序号的唯一权威）、订阅模式、优先级、中间件、
环形历史、异常隔离。不被内核 import（架构 §4.2）。

批次 B 落地后本包有四块内容：信封与草稿协议（`envelope`）· 总线本体
（`bus`）· 订阅与优先级（`subscriber`）。**总线按 Protocol 消费内核的草稿，
不 import kernel 的具体类**——内核与总线之间保持松耦合。
"""

from .bus import (
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_TRACE_LIMIT,
    ERROR_EVENT_TYPE,
    EventBus,
)
from .envelope import (
    CAUSE_KEY,
    PRODUCER_KEY,
    SOURCE_KEY,
    Draft,
    Envelope,
    JSONValue,
    Metadata,
    Payload,
    build_metadata,
    metadata_value,
    producer_of,
)
from .subscriber import (
    SingleHandler,
    SubscriberRegistry,
    Subscription,
    SystemContext,
    SystemHandler,
    default_name,
    matches,
)

__all__ = [
    "CAUSE_KEY",
    "DEFAULT_HISTORY_LIMIT",
    "DEFAULT_TRACE_LIMIT",
    "ERROR_EVENT_TYPE",
    "PRODUCER_KEY",
    "SOURCE_KEY",
    "Draft",
    "Envelope",
    "EventBus",
    "JSONValue",
    "Metadata",
    "Payload",
    "SingleHandler",
    "SubscriberRegistry",
    "Subscription",
    "SystemContext",
    "SystemHandler",
    "build_metadata",
    "default_name",
    "matches",
    "metadata_value",
    "producer_of",
]
