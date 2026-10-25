"""宿主装配层（S14 · plan-M0.md §1.4.4.1 ① · 架构 §4.1）。

**唯一知道"怎么把部件拼起来"的地方**（架构 §4.1）：内核、总线、规则集三样
在这里拼成一件成品，其余模块只管用。

## 装配层做三件事

1. **造内核**：方块类型清单**从规则层取**（`rules.tables.PIECE_TYPES`）——
   内核不抄名单，抄了就是两处会悄悄分叉的副本（架构 §7.1）；规则集经
   `Kernel(rule_set=…)` 注入，换规则集不改编配层以外的东西（FR-4.9）；
2. **造总线**：`view_provider` 必须是 `kernel.snapshot`（ADR-056：视图 = 快照，
   **同一个 dataclass**，不写两套）；
3. **造时钟**：省略时用 `VirtualClock(step_ms=step_ms)`——**无头批次不读墙钟**
   （架构 §5.3：时钟源落宿主层，内核只认逻辑帧）。

## 默认值从各自的地盘取

棋盘尺寸取 `kernel.core` 的常量、步长取 `host.clock` 的常量、历史容量取
`bus.bus` 的常量——**不在这里另抄一份**，抄了就是第二个真相。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..bus.bus import DEFAULT_HISTORY_LIMIT, EventBus
from ..kernel.core import (
    DEFAULT_BOARD_HEIGHT,
    DEFAULT_BOARD_WIDTH,
    DEFAULT_HIDDEN_ROWS,
    Kernel,
)
from ..kernel.quota import DEFAULT_LIMITS, QuotaLimits
from ..kernel.snapshot import Snapshot
from ..rules.interface import RuleSet
from ..rules.standard import STANDARD_RULESET
from ..rules.tables import PIECE_TYPES
from .clock import DEFAULT_STEP_MS, Clock, VirtualClock


@dataclass(frozen=True)
class Assembly:
    """装配好的三件套（**内核 / 总线 / 时钟**）。

    - `kernel`：唯一的状态所有者（架构 §7.1）；
    - `bus`：事件的传输与分发中枢（序号由它分配）；
    - `clock`：帧循环的时钟源（**可注入**——回放 / 逐帧步进 / 倍速播放走同
      一个接口，架构 §5.3）。**M0 尚无消费方**：帧循环跑帧不 sleep、不看时间，
      `step_ms` 对 M0 的节奏零影响——这个口是给 M1 的回放 / 倍速预留的，别把
      "预留"当成"已实现"。
    """

    kernel: Kernel
    bus: EventBus[Snapshot]
    clock: Clock


def assemble(
    *,
    seed: int = 0,
    piece_types: tuple[str, ...] = PIECE_TYPES,
    rule_set: RuleSet = STANDARD_RULESET,
    rule_id: str = "standard",
    board_width: int = DEFAULT_BOARD_WIDTH,
    board_height: int = DEFAULT_BOARD_HEIGHT,
    hidden_rows: int = DEFAULT_HIDDEN_ROWS,
    step_ms: int = DEFAULT_STEP_MS,
    mode: str = "live",
    limits: QuotaLimits = DEFAULT_LIMITS,
    clock: Clock | None = None,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> Assembly:
    """把内核、总线、规则集与时钟拼成一件成品。

    - `seed`：内核随机源种子（FR-1.4）；
    - `piece_types`：方块类型清单——**默认从规则层取**，内核自己不抄；
    - `rule_set`：规则集（9 条接口，FR-4.9 可整体替换）；
    - `rule_id`：规则集标识（进快照与 `kernel:ready`）；
    - `board_width` / `board_height` / `hidden_rows`：棋盘尺寸（隐藏行是**数量**，
      不是可见起始行）；
    - `step_ms`：固定步长（毫秒，逻辑时间 = 帧号 × 步长）；
    - `mode`：运行模式（只随 `kernel:ready` 透传，**内核不按它分支**）；
    - `limits`：配额上限（**必须是构造参数**——回放宿主注入大得多的上限）；
    - `clock`：时钟源；**省略时用 `VirtualClock(step_ms=step_ms)`**——无头批次
      不读墙钟；
    - `history_limit`：总线环形历史保留条数。
    """
    kernel = Kernel(
        seed=seed,
        piece_types=piece_types,
        rule_set=rule_set,
        rule_id=rule_id,
        board_width=board_width,
        board_height=board_height,
        hidden_rows=hidden_rows,
        step_ms=step_ms,
        mode=mode,
        limits=limits,
    )
    bus: EventBus[Snapshot] = EventBus[Snapshot](
        # 视图 = 快照，**同一个 dataclass**（ADR-056）。
        view_provider=kernel.snapshot,
        history_limit=history_limit,
    )
    return Assembly(
        kernel=kernel,
        bus=bus,
        clock=VirtualClock(step_ms=step_ms) if clock is None else clock,
    )


__all__ = [
    "Assembly",
    "assemble",
]
