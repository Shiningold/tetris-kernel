"""AI 系统（系统层 · M4-S9 · FR-8.5 · AC-6 · 架构 §7.5）。

**一个真的能替人玩的 AI**：订阅事件 · 派发命令 · **与人类走完全相同的通道**。

- **不写棋盘**（M3-D3）：它**只订阅事件 + 派命令**——没有第二条路；
- **预算固定**（M4-D3）：`search.py` 里**没有 `time`**——**按耗时自适应就是从
  那里进来的**，而它会让同一录像在不同机器上重放出不同结果（破 AC-1 / NFR-1）；
- **不引 numpy**：纯 Python 启发式（少一个依赖 · 天然确定）；
- **自建随机源**：**本系统不用随机源**（决策完全由局面决定）——
  架构 §5.3 的「内核随机源只服务内核」因此**用不上**；将来要用，
  必须显式 `random.Random(seed)` 自建。

## 三个模块

| 模块 | 是什么 |
| --- | --- |
| `manifest.py` | **对外声明 + 工厂**（契约面 · 进基线） |
| `system.py` | **系统本体**（订阅事件 · 派命令） |
| `search.py` | **落点搜索**（纯函数 · 可脱离内核单测） |
"""

from .manifest import SYSTEM_MANIFEST, create_system
from .search import MAX_CANDIDATES, SEARCH_DEPTH, Board, Placement, plan_placement
from .system import CLOCK_TICK_EVENT, PIECE_SPAWNED_EVENT, SOURCE_NAME, AiSystem

__all__ = [
    "CLOCK_TICK_EVENT",
    "MAX_CANDIDATES",
    "PIECE_SPAWNED_EVENT",
    "SEARCH_DEPTH",
    "SOURCE_NAME",
    "SYSTEM_MANIFEST",
    "AiSystem",
    "Board",
    "Placement",
    "create_system",
    "plan_placement",
]
