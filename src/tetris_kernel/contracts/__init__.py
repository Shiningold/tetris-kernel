"""契约层：事件类型注册表与机器可读契约声明。

公共叶子——**不 import 任何东西**（架构 §2.3）。契约 v1 采用"只增不改"基线，
键 = 事件类型名 / 命令类型名 / 快照字段名 / 规则签名名，值 = 该声明的哈希。

本文件只做**包内**导出：跨包 import 一个模块都不许有，第三方更不许有。
- 事件目录：`tetris_kernel.contracts.events`
- 命令目录：`tetris_kernel.contracts.commands`
- 快照字段声明与内核状态清单：`tetris_kernel.contracts.snapshot`
- T-Spin 判定输入结构：`tetris_kernel.contracts.placement`
- 规则接口签名与中性交换结构：`tetris_kernel.contracts.rules`
- 命名结构注册表：`tetris_kernel.contracts.structures`
- 共用类型取值域与类型文法：`tetris_kernel.contracts.types`
"""

from .commands import (
    COMMAND_TYPE_FIELDS,
    COMMAND_TYPES,
    COMMANDS,
    LIFECYCLE_REJECT_REASONS,
    REJECT_REASON_CATEGORIES,
    REJECT_REASON_CATEGORY_LABELS,
    REJECT_REASONS,
    CommandArg,
    CommandSpec,
    is_lifecycle_gated,
)
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
from .placement import PlacementInfo
from .rules import (
    AXIS_VALUES,
    CLEAR_AXIS_VALUES,
    RULE_SET_DEFINED_FIELDS,
    RULE_SIGNATURE_NAMES,
    RULE_SIGNATURES,
    ClearGroup,
    PlacementClass,
    RuleContext,
    RuleParam,
    RuleSignature,
    ScoreDelta,
    ScoreState,
)
from .snapshot import (
    ACTIVE_FIELDS,
    BOARD_FIELDS,
    COORDINATE_CONVENTIONS,
    KERNEL_STATE_TO_SNAPSHOT,
    RUNTIME_ONLY_STATE,
    SNAPSHOT_FIELD_NAMES,
    SNAPSHOT_FIELDS,
    FieldSpec,
    KernelStateEntry,
)
from .structures import NAMED_STRUCTS
from .types import (
    ARGS,
    ARGS_VALUE,
    BOARD_CELLS,
    BOARD_VIEW,
    CELLS,
    COORD_CELLS,
    INT_TUPLE,
    JSON_VALUE,
    SCALAR_MAP,
    VECTOR2,
)

__all__ = [
    "ARGS",
    "ARGS_VALUE",
    "ACTIVE_FIELDS",
    "AXIS_VALUES",
    "BOARD_CELLS",
    "BOARD_FIELDS",
    "BOARD_VIEW",
    "CELLS",
    "CLEAR_AXIS_VALUES",
    "COMMANDS",
    "COMMAND_TYPES",
    "COMMAND_TYPE_FIELDS",
    "CONTRACT_VERSION",
    "COORDINATE_CONVENTIONS",
    "COORD_CELLS",
    "ENVELOPE_FIELDS",
    "EVENT_TYPES",
    "INT_TUPLE",
    "JSON_VALUE",
    "KERNEL_STATE_TO_SNAPSHOT",
    "LIFECYCLE_REJECT_REASONS",
    "METADATA_KEYS",
    "NAMED_STRUCTS",
    "PRODUCER_LABELS",
    "REJECT_REASONS",
    "REJECT_REASON_CATEGORIES",
    "REJECT_REASON_CATEGORY_LABELS",
    "RULE_SET_DEFINED_FIELDS",
    "RULE_SIGNATURES",
    "RULE_SIGNATURE_NAMES",
    "RUNTIME_ONLY_STATE",
    "SCALAR_MAP",
    "SNAPSHOT_FIELDS",
    "SNAPSHOT_FIELD_NAMES",
    "VECTOR2",
    "ClearGroup",
    "CommandArg",
    "CommandSpec",
    "EventSpec",
    "FieldSpec",
    "KernelStateEntry",
    "PayloadField",
    "PlacementClass",
    "PlacementInfo",
    "RuleContext",
    "RuleParam",
    "RuleSignature",
    "ScoreDelta",
    "ScoreState",
    "is_lifecycle_gated",
    "namespace_of",
]
