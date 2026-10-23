"""契约层：事件类型注册表与机器可读契约声明。

公共叶子——**不 import 任何东西**（架构 §2.3）。契约 v1 采用"只增不改"基线，
键 = 事件类型名 / 命令类型名 / 快照字段名 / 规则签名名，值 = 该声明的哈希。

本文件只做**包内**导出：跨包 import 一个模块都不许有，第三方更不许有。
- 事件目录：`tetris_kernel.contracts.events`
- 共用类型取值域与类型文法：`tetris_kernel.contracts.types`
"""

from .events import (
    CONTRACT_VERSION,
    ENVELOPE_FIELDS,
    EVENT_TYPES,
    METADATA_KEYS,
    PRODUCER_LABELS,
    EventSpec,
    PayloadField,
    namespace_of,
)
from .types import (
    ARGS,
    ARGS_VALUE,
    CELLS,
    COORD_CELLS,
    JSON_VALUE,
    SCALAR_MAP,
)

__all__ = [
    "ARGS",
    "ARGS_VALUE",
    "CELLS",
    "CONTRACT_VERSION",
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
