"""规则层：消行判定 / 重力 / 计分 / 边界四项可插拔规则 + 形状表与踢墙表。

冻结区（kernel/ + rules/），规则表落成 Python 字面量常量（ADR-032）。
接口只用中性类型，不 import kernel 的任何 dataclass（架构 §7.3）。

批次 A 落地后本包有四块内容：8 条接口的**中性签名**（`interface`）·
最小标准实现（`standard`）· 形状表 / 踢墙表（`tables`）· 冻结签名与交换结构
（`contracts`，从契约层来，规则层不复制）。
"""

from .interface import (
    RULE_INTERFACES,
    ClassifyPlacement,
    GravityInterval,
    GravityVector,
    KickOffsets,
    RuleSet,
    ScanClears,
    ScoreClear,
    ShapeCells,
    WrapCell,
    signature_mismatches,
)
from .standard import (
    STANDARD_RULESET,
    StandardRuleSet,
    classify_placement,
    gravity_interval,
    gravity_vector,
    kick_offsets,
    scan_clears,
    score_clear,
    shape_cells,
    wrap_cell,
)
from .tables import (
    CLASS_KEYS,
    CLEAR_AXIS_BY_GRAVITY,
    CLEAR_NAMES,
    COMBO_BONUS,
    DIFFICULT_CLASS_KEYS,
    GRAVITY_INTERVAL_FRAMES,
    GRAVITY_VECTORS,
    IDENTITY_KICK,
    KICK_TABLE,
    PIECE_TYPES,
    ROTATION_COUNT,
    ROTATIONS,
    SCORE_TABLE,
    SHAPE_TABLE,
    TSPIN_VALUES,
)

__all__ = [
    "CLASS_KEYS",
    "CLEAR_AXIS_BY_GRAVITY",
    "CLEAR_NAMES",
    "COMBO_BONUS",
    "DIFFICULT_CLASS_KEYS",
    "GRAVITY_INTERVAL_FRAMES",
    "GRAVITY_VECTORS",
    "IDENTITY_KICK",
    "KICK_TABLE",
    "PIECE_TYPES",
    "ROTATION_COUNT",
    "ROTATIONS",
    "RULE_INTERFACES",
    "SCORE_TABLE",
    "SHAPE_TABLE",
    "STANDARD_RULESET",
    "TSPIN_VALUES",
    "ClassifyPlacement",
    "GravityInterval",
    "GravityVector",
    "KickOffsets",
    "RuleSet",
    "ScanClears",
    "ScoreClear",
    "ShapeCells",
    "StandardRuleSet",
    "WrapCell",
    "classify_placement",
    "gravity_interval",
    "gravity_vector",
    "kick_offsets",
    "scan_clears",
    "score_clear",
    "shape_cells",
    "signature_mismatches",
    "wrap_cell",
]
