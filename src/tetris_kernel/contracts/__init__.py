"""契约层：事件类型注册表与机器可读契约声明。

公共叶子——**不 import 任何东西**（架构 §2.3）。契约 v1 采用"只增不改"基线，
键 = 事件类型名 / 命令类型名 / 快照字段名 / 规则签名名，值 = 该声明的哈希。

本文件只做**包内**导出：跨包 import 一个模块都不许有，第三方更不许有。
事件目录见 `tetris_kernel.contracts.events`。
"""

from .events import (
    ARGS,
    ARGS_VALUE,
    CELLS,
    COORD_CELLS,
    ENVELOPE_FIELDS,
    EVENT_TYPES,
    JSON_VALUE,
    METADATA_KEYS,
    PRODUCER_LABELS,
    SCALAR_MAP,
    EventSpec,
    PayloadField,
    namespace_of,
)

__all__ = [
    "ARGS",
    "ARGS_VALUE",
    "CELLS",
    "COORD_CELLS",
    "ENVELOPE_FIELDS",
    "EVENT_TYPES",
    "JSON_VALUE",
    "METADATA_KEYS",
    "PRODUCER_LABELS",
    "SCALAR_MAP",
    "EventSpec",
    "PayloadField",
    "namespace_of",
]
