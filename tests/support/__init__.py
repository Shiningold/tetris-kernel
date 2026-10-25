"""M1 的测试支撑：确定性参考策略与它的驱动器（spec §7.8 · plan-M1.md S13 段）。

**为什么放在 `tests/support/` 而不是 `systems/ai/`**：`systems/ai/` 是 M3 的
产物，且 ADR-028（固定搜索预算）那套纪律要跟 AI 一起落地。M1 要的只是
**一个可复现的驱动**，不是**一个会下棋的系统**——放错位置的代价是 M3 要先
拆掉它再重做（spec §7.8）。

**这两个模块不碰产品代码**：`policy.py` 只读快照、只产出命令名；`driver.py`
只经**命令通道**驱动内核。
"""

from __future__ import annotations

from .driver import PlayOutcome, RunTrace, play_frames, play_to_game_over
from .policy import POLICY_SOURCE, ReferencePolicy

__all__ = [
    "POLICY_SOURCE",
    "PlayOutcome",
    "ReferencePolicy",
    "RunTrace",
    "play_frames",
    "play_to_game_over",
]
