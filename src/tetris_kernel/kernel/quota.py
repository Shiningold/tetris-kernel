"""配额与同因拒绝合并（内核层 · S10 · ADR-038 / ADR-040 · 架构 §6.4 / §6.5）。

## 配额：上限是**构造参数**，不是写死的常量

回放宿主必须注入**大得多的**上限——写死成常量就是 B-6 违规（录像不允许丢
命令，回放超限丢命令 = 录像与回放对不上）。所以这三个上限住在
`QuotaLimits` 里，由宿主构造内核时给。

## 两个计数器**按帧累计**

每帧开始清零，**不是按单轮**（ADR-040）：按单轮算 8 × 100 = 800 条/帧，上限
形同虚设。`tick` 自身不计入命令数（那是**配额计数器**；帧内序号是另一套计
数器，tick 在那套里占 0）。

## 同因拒绝合并：一条事件承载 N 次拒绝

- 键 = **(phase, 命令类型, 拒绝原因)**；
- `received` 与 `rejected` **成对合并、计数一致**；
- `mergedCount` 起始值 = 1（"这条事件代表 1 次拒绝"）；
- phase 变化或 `resume` 后**重置计数**（合并组被冲刷，计数归零）。

**满足形式是"一条事件承载 N 次拒绝"，不是"没有事件"**（ADR-038）——否则事件
完备性检查会把合并误判成事件缺失。

合并窗口 = **一批命令**（一轮）。所以合并组里的 `frame` 是这一批的帧号：
一帧内的命令同帧（帧号只在 tick 出队时 +1）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .commands import Args

# ---------------------------------------------------------------------------
# 配额
# ---------------------------------------------------------------------------

#: 轮次上限（**实时局**的默认值）。
DEFAULT_MAX_ROUNDS = 8

#: 单帧命令数上限（**实时局**的默认值）。
DEFAULT_MAX_COMMANDS = 100

#: 配额名（`kernel:quota-exceeded` 的负载字段 `quota`）。
QUOTA_COMMAND_COUNT = "commandCount"
QUOTA_ROUNDS = "rounds"
QUOTA_WRITE_CELLS = "writeCellsPerFrame"


@dataclass(frozen=True)
class QuotaLimits:
    """内核的三个配额上限。

    **必须由宿主注入**（构造参数）——回放宿主注入大得多的上限，否则回放超限
    会丢命令，违反 B-6（录像不允许丢命令）。

    - `max_rounds`：单帧轮次上限；
    - `max_commands`：单帧命令数上限（`tick` 不计入）；
    - `max_write_cells`：单帧写入格子总数上限；`None` = 按棋盘面积取。
    """

    max_rounds: int = DEFAULT_MAX_ROUNDS
    max_commands: int = DEFAULT_MAX_COMMANDS
    max_write_cells: int | None = None


#: 默认上限（实时局）。
DEFAULT_LIMITS = QuotaLimits()


class FrameQuota:
    """按帧累计的计数器（ADR-040）。"""

    def __init__(self, limits: QuotaLimits) -> None:
        self._limits = limits
        self._commands = 0
        self._rounds = 0
        self._write_cells = 0

    @property
    def limits(self) -> QuotaLimits:
        """当前生效的上限。"""
        return self._limits

    @property
    def commands(self) -> int:
        """本帧已取队执行的命令数（`tick` 不计入）。"""
        return self._commands

    @property
    def rounds(self) -> int:
        """本帧已完成的轮次数。"""
        return self._rounds

    @property
    def write_cells(self) -> int:
        """本帧已写入的格子数。"""
        return self._write_cells

    @property
    def remaining_commands(self) -> int:
        """本帧还能取队执行多少条命令。"""
        return max(0, self._limits.max_commands - self._commands)

    @property
    def remaining_rounds(self) -> int:
        """本帧还能跑多少轮。"""
        return max(0, self._limits.max_rounds - self._rounds)

    def start_frame(self) -> None:
        """**每帧开始清零**（不是按单轮）。"""
        self._commands = 0
        self._rounds = 0
        self._write_cells = 0

    def take_command(self) -> None:
        """记一条已取队执行的命令。"""
        self._commands += 1

    def take_round(self) -> None:
        """记一轮。"""
        self._rounds += 1

    def take_write_cells(self, count: int) -> None:
        """记本帧写入的格子数。"""
        self._write_cells += count

    def with_write_cells_limit(self, limit: int) -> None:
        """把"按棋盘面积取"的写入上限落定。"""
        self._limits = replace(self._limits, max_write_cells=limit)


# ---------------------------------------------------------------------------
# 同因拒绝合并
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RejectGroup:
    """一组同因拒绝（**一条事件承载 N 次拒绝**）。

    字段：

    - `phase` / `commandType` / `reason`：合并键的三元组；
    - `frame`：这一组的帧号（组内命令同帧）；
    - `source` / `args`：**组内第一条命令**的来源与参数（合并事件的负载带着
      它们——拒绝事件的 `args` 必须与原命令一致，架构 §7.2）；
    - `count`：这一组代表多少次拒绝（起始值 1）。
    """

    phase: str
    commandType: str
    reason: str
    frame: int
    source: str
    args: Args
    count: int


class RejectCoalescer:
    """同因拒绝合并表（ADR-038）。

    **键 = (phase, 命令类型, 拒绝原因)**：同键的拒绝合并计数，键一变就冲刷
    旧组。**成对合并**：`received` 与 `rejected` 由同一组产出，计数一致。
    """

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str, str], RejectGroup] = {}

    @property
    def pending_count(self) -> int:
        """待冲刷的合并组数。"""
        return len(self._pending)

    def record(
        self,
        *,
        phase: str,
        command_type: str,
        reason: str,
        frame: int,
        source: str,
        args: Args,
    ) -> tuple[RejectGroup, ...]:
        """记录一次拒绝。

        返回**因键变化而冲刷出来的旧组**（可能为空）——键变了就意味着这一组
        已经结束，它的计数到此为止。
        """
        key = (phase, command_type, reason)
        group = self._pending.get(key)
        if group is None:
            flushed = self.flush()
            self._pending[key] = RejectGroup(
                phase=phase,
                commandType=command_type,
                reason=reason,
                frame=frame,
                source=source,
                args=args,
                count=1,
            )
            return flushed
        self._pending[key] = replace(group, count=group.count + 1)
        return ()

    def flush(self) -> tuple[RejectGroup, ...]:
        """冲刷全部待定合并组（**返回它们的最终计数**）。

        冲刷 = 计数到此为止 + 计数归零（下一组从 1 重新开始）——这正是
        "phase 变化或 `resume` 后重置计数"的落点。
        """
        groups = tuple(self._pending.values())
        self._pending.clear()
        return groups


__all__ = [
    "DEFAULT_LIMITS",
    "DEFAULT_MAX_COMMANDS",
    "DEFAULT_MAX_ROUNDS",
    "QUOTA_COMMAND_COUNT",
    "QUOTA_ROUNDS",
    "QUOTA_WRITE_CELLS",
    "FrameQuota",
    "QuotaLimits",
    "RejectCoalescer",
    "RejectGroup",
]
