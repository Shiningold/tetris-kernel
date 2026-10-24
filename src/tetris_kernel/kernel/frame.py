"""帧模型（内核层 · S12 · 架构 §6.1 / §6.2 · ADR-022 / ADR-023）。

## 五步：重力 → 锁定 → 消行 → 塌落 → 出生

**必须 step 化**：整批返回会让所有事件共享"执行后的最终态"——`piece:locked`
的订阅者读到的是已消行、已塌落、已出生的棋盘（ADR-023）。

**加一步的充要条件**：某个订阅者需要在两个既有步骤之间观察一个稳定状态，
**且**该状态无法由任一既有事件的负载自足表达。两问都答不上来就不加步。

## 步骤之间传的数据不进内核状态

`FrameContext` 是**步骤之间传的袋子**（本次放置的上下文 · 放置分类 · 被消的行
号），**刻意不放进内核状态**：半帧状态不能残留——装配层提前 `break` 之后，
下一次帧推进不能捡到上一次留下的半截数据（架构 §6.2）。

## 推进许可与 phase 复查

- **帧号推进与推进许可是两件事**（ADR-022）：帧号 +1、帧内序号归零在 tick
  **出队时**；推进许可（`clock:tick` + `advance_frame()`）在 tick **被接受**时
  授予；
- **推进前要复查 phase**（架构 §6.2）：`clock:tick` 分发期间若有人派发
  `pause`，它会在下一轮被执行——首个步骤前必须复查，不是 running 就直接结束
  本帧。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from ..contracts.placement import PlacementInfo
from ..contracts.rules import PlacementClass
from .drafts import EventDraft

# ---------------------------------------------------------------------------
# 步骤之间传递的数据
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameContext:
    """帧推进过程中**在步骤之间传递**的数据。

    - `placement`：本次放置的上下文（T-Spin 判定的输入）；
    - `classification`：规则集给出的放置分类（**内核只搬运、不重新判定**——
      架构 §7.3.1）；
    - `cleared_rows`：本次被消的行号（塌落步要用）。

    **不是内核状态**：它只活在一轮帧推进里。
    """

    placement: PlacementInfo | None = None
    classification: PlacementClass | None = None
    cleared_rows: tuple[int, ...] = ()


@dataclass(frozen=True)
class StepOutcome:
    """一步的产出：这一步的草稿 + 传给下一步的上下文。"""

    drafts: tuple[EventDraft, ...]
    context: FrameContext


class FrameStepper(Protocol):
    """内核实现这五条步骤（**结构化类型**：内核不需要继承本协议）。"""

    def step_gravity(self, context: FrameContext) -> StepOutcome:
        """第一步：重力。"""
        ...

    def step_lock(self, context: FrameContext) -> StepOutcome:
        """第二步：锁定。"""
        ...

    def step_clear(self, context: FrameContext) -> StepOutcome:
        """第三步：消行。"""
        ...

    def step_collapse(self, context: FrameContext) -> StepOutcome:
        """第四步：塌落。"""
        ...

    def step_spawn(self, context: FrameContext) -> StepOutcome:
        """第五步：出生。"""
        ...


#: 帧推进五步的顺序（架构 §6.2 / ADR-023）。
FRAME_STEPS: tuple[str, ...] = ("gravity", "lock", "clear", "collapse", "spawn")


def advance_frame(kernel: FrameStepper) -> Iterator[tuple[EventDraft, ...]]:
    """帧推进五步，**逐步产出草稿**。

    **装配层必须完整消费**（架构 §6.2）：提前 `break` 会留下"锁定已完成、消行
    未做"的静默半帧——所以宿主要用 `try/finally` 把序列耗尽。

    每一步内部**先算完再一次性写入**（架构 §6.2）。
    """
    context = FrameContext()
    steps = (
        kernel.step_gravity,
        kernel.step_lock,
        kernel.step_clear,
        kernel.step_collapse,
        kernel.step_spawn,
    )
    for step in steps:
        outcome = step(context)
        context = outcome.context
        yield outcome.drafts


__all__ = [
    "FRAME_STEPS",
    "FrameContext",
    "FrameStepper",
    "StepOutcome",
    "advance_frame",
]
