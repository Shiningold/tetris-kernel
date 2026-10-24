"""内核层：唯一的状态所有者。

只接受命令、只发出事件；对外接口只有 execute / advance_frame / snapshot。
不 import 总线、零第三方依赖、禁读墙钟（架构 §7.1、§5.3）。

批次 A 落地后本包的导出面是两块：随机源与指纹（S7）· 快照与只读视图（S8）。
`execute` / `advance_frame` 随 S9 / S12 补上——**不提前实现别的阶段的事**。

导出面由**集成者**统一收口（plan-M0.md §1.4.2）：包级 `__init__.py` 不归
写手，多个写手各写一行就是几份会漂的名单。
"""

from .fingerprint import (
    DOMAIN_PREFIX,
    HASH_ALGORITHM,
    HASH_SIZE,
    blake2b_hex,
    tlv_digest,
)
from .rng import (
    RNG_NEXT_EVENT,
    KernelRng,
    RngNextDraft,
    RngNextPayload,
)
from .snapshot import (
    Active,
    Board,
    ExtensionEntry,
    Snapshot,
    build_board,
    build_extensions,
    build_snapshot,
    compute_extension_fingerprint,
    compute_structure_fingerprint,
    freeze_cells,
    with_rng_state,
)

__all__ = [
    "DOMAIN_PREFIX",
    "HASH_ALGORITHM",
    "HASH_SIZE",
    "RNG_NEXT_EVENT",
    "Active",
    "Board",
    "ExtensionEntry",
    "KernelRng",
    "RngNextDraft",
    "RngNextPayload",
    "Snapshot",
    "blake2b_hex",
    "build_board",
    "build_extensions",
    "build_snapshot",
    "compute_extension_fingerprint",
    "compute_structure_fingerprint",
    "freeze_cells",
    "tlv_digest",
    "with_rng_state",
]
