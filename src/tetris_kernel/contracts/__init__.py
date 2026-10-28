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
- 系统声明面：`tetris_kernel.contracts.systems`
"""

from .commands import (
    COMMAND_TYPE_FIELDS,
    COMMAND_TYPE_LITERAL,
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
    AXIS_LITERAL,
    AXIS_VALUES,
    CLEAR_AXIS_LITERAL,
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
from .systems import (
    GLOBAL_SUBSCRIPTION,
    SYSTEM_MANIFESTS,
    SystemManifest,
    check_manifest_types,
    manifest_canonical_text,
    register_system_manifest,
    unregister_system_manifest,
)
from .types import (
    ARGS,
    ARGS_VALUE,
    BOARD_CELLS,
    BOARD_VIEW,
    CELLS,
    COORD_CELLS,
    INT_TUPLE,
    JSON_VALUE,
    NEXT_QUEUE,
    PHASE_GAME_OVER,
    PHASE_LITERAL,
    PHASE_NOT_STARTED,
    PHASE_PAUSED,
    PHASE_RUNNING,
    PHASE_VALUES,
    SCALAR_MAP,
    VECTOR2,
    Phase,
)

__all__ = [
    "ARGS",
    "ARGS_VALUE",
    "ACTIVE_FIELDS",
    "AXIS_LITERAL",
    "AXIS_VALUES",
    "BOARD_CELLS",
    "BOARD_FIELDS",
    "BOARD_VIEW",
    "CELLS",
    "CLEAR_AXIS_LITERAL",
    "CLEAR_AXIS_VALUES",
    "COMMANDS",
    "COMMAND_TYPES",
    "COMMAND_TYPE_FIELDS",
    "COMMAND_TYPE_LITERAL",
    "CONTRACT_VERSION",
    "COORDINATE_CONVENTIONS",
    "COORD_CELLS",
    "ENVELOPE_FIELDS",
    "EVENT_TYPES",
    "GLOBAL_SUBSCRIPTION",
    "INT_TUPLE",
    "JSON_VALUE",
    "KERNEL_STATE_TO_SNAPSHOT",
    "LIFECYCLE_REJECT_REASONS",
    "METADATA_KEYS",
    "NAMED_STRUCTS",
    "NEXT_QUEUE",
    "PHASE_GAME_OVER",
    "PHASE_LITERAL",
    "PHASE_NOT_STARTED",
    "PHASE_PAUSED",
    "PHASE_RUNNING",
    "PHASE_VALUES",
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
    "SYSTEM_MANIFESTS",
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
    "Phase",
    "RuleContext",
    "RuleParam",
    "RuleSignature",
    "ScoreDelta",
    "ScoreState",
    "SystemManifest",
    "check_manifest_types",
    "is_lifecycle_gated",
    "manifest_canonical_text",
    "namespace_of",
    "register_system_manifest",
    "unregister_system_manifest",
]
