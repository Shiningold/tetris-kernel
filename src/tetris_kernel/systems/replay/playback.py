"""录像回放（系统层 · 回放系统 · M2-S9 · FR-6.2 · FR-6.3 · ADR-033）。

## 重放就是"原样派发录下的命令"

**不重新决策**——只重放录下来的命令，所以它测的是"录像能不能还原这一局"，
不是"策略好不好"（spec §7.3）。

**帧边界由录下来的 `tick` 决定**：每一帧的命令组里**有 `tick` 才推进一帧**。
这不是实现细节——**帧号是 tick 推进的**（ADR-022），不按它分帧，重放的帧号
就和原局对不上。

## 中途开始（spec §7.4 选①）

**从头重放、到第 N 帧再进入步进模式**——**状态自然正确**，代价是慢。

**不用存档跳过去**（②）：② 的"快"是**用一条新的正确性赌注换的**，而
FR-10.2 的存档一致性本来就是 M2 要验的东西——**不要在同一批次里让两条互相
依赖的路径互相验证**。

## 暂停是**宿主级**（ADR-033）

**宿主级暂停 = 不跑帧循环、帧号冻结、不产生任何命令。**

**为什么不派发内核级 `pause` 命令**：① `pause` 是被接受的命令 → 会被录进录像
→ **回放自己污染录像**（ADR-033 的原话）；② 它会让内核进入 `paused`，之后
录下的命令全被生命周期门禁拒掉——**重放当场分叉**。

**所以"不为步进新增命令"的落点是**：步进与暂停**一个命令都不新增**，也
**一个命令都不多派**。

## 四项比对（AC-1 · spec §7.3）与**第五项**（M4-S10 · `G4-2`）

**四项**：棋盘指纹 · 分数 · 行数 · 方块数。**逐条相等**才算过——不是"最终态
差不多"。

**第五项 = 扩展指纹**：**结构指纹不含扩展字段**（架构 §5.4）→ 变体局的扩展
状态分叉时，**只比四项会全绿而结果不同**。**标准局的第五项恒等**（空槽的
摘要）→ **既有的四项比对语义不变**（AC-1 不受影响）。

**五项由宿主注入的驱动器取**——棋盘指纹只能从内核快照拿，系统层**不碰内核
状态**（M2-D1），指纹的算法也只有内核那一份（抄第二份就是第二个真相）。
"""

from __future__ import annotations

from typing import Protocol

from ...bus.bus import EventBus
from ...kernel.drafts import METADATA_CAUSE, EventDraft, make_draft
from .format import FiveItems, FourItems, RecordedCommand, Recording

#: 回放开始事件（契约已登记 · 产出者 `systems/replay/`）。
PLAYBACK_STARTED_EVENT = "replay:playback-started"

#: 回放停止事件（契约已登记 · 产出者 `systems/replay/`）。
PLAYBACK_STOPPED_EVENT = "replay:playback-stopped"

#: 帧步进事件（契约已登记 · 产出者 `systems/replay/`）。
FRAME_STEPPED_EVENT = "replay:frame-stepped"

#: 帧推进的那条命令（**它决定帧边界**）。
TICK_COMMAND = "tick"


class PlaybackDriver(Protocol):
    """回放驱动器（**宿主注入**）。

    **为什么是注入的**：起内核 · 派发命令 · 推进帧 · 取五项——都是**宿主**的
    事（架构 §4.1：唯一知道"怎么把部件拼起来"的地方是装配层）。系统层**不造
    内核**，也**不 import `kernel.core`**。

    - `dispatch`：派发一批命令（**原样**——类型 / 参数 / 来源都不改）；
    - `advance_frame`：推进一帧（**帧推进五步**）；
    - `four_items`：取当前局面的**四项**（契约面那条四字段事件用）；
    - `five_items`：取当前局面的**五项**（**四项 + 扩展指纹** · M4-S10）。
    """

    def dispatch(self, commands: tuple[RecordedCommand, ...]) -> None:
        """派发一批命令（**原样**——不改类型、不改参数、不改来源）。"""
        ...

    def advance_frame(self) -> None:
        """推进一帧（**帧推进五步**）。"""
        ...

    def four_items(self) -> FourItems:
        """当前局面的四项（**棋盘指纹 · 分数 · 行数 · 方块数**）。"""
        ...

    def five_items(self) -> FiveItems:
        """当前局面的**五项**（四项 **+ 扩展指纹** · M4-S10）。"""
        ...


class PlaybackSession[V]:
    """回放会话（**普通订阅者** + 命令派发者）。

    **它自己不发命令给内核**——它把录下的命令**交给驱动器**，驱动器负责派发
    （架构 §4.1：派发是宿主的事）。

    - `bus`：事件总线（回放生命周期事件走它）；
    - `driver`：**宿主注入**的驱动器；
    - `recording`：要重放的录像。
    """

    def __init__(
        self,
        bus: EventBus[V],
        driver: PlaybackDriver,
        recording: Recording,
    ) -> None:
        self._bus = bus
        self._driver = driver
        self._recording = recording
        self._groups = _group_by_frame(recording.commands)
        self._index = 0
        self._paused = False
        self._started = False
        self._stopped = False

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def paused(self) -> bool:
        """当前是否**宿主级暂停**（帧号冻结）。"""
        return self._paused

    @property
    def finished(self) -> bool:
        """回放是否已结束。"""
        return self._stopped

    @property
    def frame_count(self) -> int:
        """录像里的**帧组数**（每组 = 一帧的命令）。"""
        return len(self._groups)

    @property
    def next_frame(self) -> int | None:
        """**下一个**要走的帧号（`None` = 走完了）。"""
        if self._index >= len(self._groups):
            return None
        return self._groups[self._index][0]

    def four_items(self) -> FourItems:
        """当前局面的四项。"""
        return self._driver.four_items()

    def five_items(self) -> FiveItems:
        """当前局面的**五项**（四项 **+ 扩展指纹** · M4-S10 · `G4-2`）。

        **第五项是"扩展指纹"**：**结构指纹不含扩展字段**（架构 §5.4）——
        变体局的扩展状态分叉时，**只比四项会全绿而结果不同**。
        """
        return self._driver.five_items()

    # ------------------------------------------------------------------
    # 走
    # ------------------------------------------------------------------

    def run(self) -> FourItems:
        """**从头重放到录制的终点**，返回重放后的四项。

        **这是四项比对走的那条路径**——它**不插任何额外命令**。
        """
        self._start()
        while self._index < len(self._groups):
            self._advance_one()
        return self._stop()

    def run_until(self, frame: int) -> None:
        """**从头重放到第 `frame` 帧**（含），之后可以 `step()` 接着走。

        **中途开始就是它**（spec §7.4 选①）：从头重放到第 N 帧，状态自然正确。
        """
        self._start()
        while self._index < len(self._groups) and self._groups[self._index][0] <= frame:
            self._advance_one()

    def step(self) -> int:
        """**走一帧**，返回步到的帧号（每步发 `replay:frame-stepped`）。"""
        if self._paused:
            raise RuntimeError("已暂停：先 resume() 再步进")
        self._start()
        if self._index >= len(self._groups):
            raise RuntimeError("录像已经走完了")
        return self._advance_one()

    def pause(self) -> None:
        """**宿主级暂停**（ADR-033）：不跑帧循环、帧号冻结、**不产生任何命令**。

        **为什么不派发内核级 `pause` 命令**：它会被录进录像（**回放自己污染
        录像**），而且会让内核进入 `paused`，之后录下的命令全被生命周期门禁
        拒掉——重放当场分叉。
        """
        self._paused = True

    def resume(self) -> None:
        """解除**宿主级暂停**（帧号继续走）。"""
        self._paused = False

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _start(self) -> None:
        """发 `replay:playback-started`（**只发一次**）。"""
        if self._started:
            return
        self._started = True
        self._bus.publish(
            _playback_started_draft(
                self._recording.seed,
                self._recording.rule,
                len(self._recording.commands),
            )
        )

    def _advance_one(self) -> int:
        """走一帧组：派发它的命令，**有 `tick` 才推进一帧**，然后发事件。

        **每走一组发一条 `replay:frame-stepped`**——"每步一条"，下游按它对齐
        帧号最省事。
        """
        frame, commands = self._groups[self._index]
        self._index += 1
        self._driver.dispatch(commands)
        if any(command.type == TICK_COMMAND for command in commands):
            self._driver.advance_frame()
        self._bus.publish(_frame_stepped_draft(frame, len(commands)))
        return frame

    def _stop(self) -> FourItems:
        """发 `replay:playback-stopped`（**带四项**），返回四项。"""
        items = self._driver.four_items()
        self._stopped = True
        self._bus.publish(_playback_stopped_draft(items))
        return items


def _group_by_frame(
    commands: tuple[RecordedCommand, ...],
) -> tuple[tuple[int, tuple[RecordedCommand, ...]], ...]:
    """按帧号分组（**帧号必须非递减**——录到顺序就是帧顺序）。

    **帧号倒退当场报错**：那说明录像坏了，**继续跑只会得到静默分叉的结果**。
    """
    groups: list[tuple[int, list[RecordedCommand]]] = []
    for command in commands:
        if groups and groups[-1][0] == command.frame:
            groups[-1][1].append(command)
            continue
        if groups and command.frame < groups[-1][0]:
            raise ValueError(f"录像里的帧号倒退了：{groups[-1][0]} → {command.frame}")
        groups.append((command.frame, [command]))
    return tuple((frame, tuple(items)) for frame, items in groups)


def _playback_started_draft(seed: int, rule: str, command_count: int) -> EventDraft:
    """构造 `replay:playback-started` 草稿（**负载三字段照抄契约声明**）。"""
    return make_draft(
        PLAYBACK_STARTED_EVENT,
        (("seed", seed), ("rule", rule), ("commandCount", command_count)),
        0,
        ((METADATA_CAUSE, None),),
    )


def _playback_stopped_draft(items: FourItems) -> EventDraft:
    """构造 `replay:playback-stopped` 草稿（**负载四字段照抄契约声明**）。"""
    return make_draft(
        PLAYBACK_STOPPED_EVENT,
        (
            ("fingerprint", items.fingerprint),
            ("score", items.score),
            ("lines", items.lines),
            ("piecesPlaced", items.pieces_placed),
        ),
        0,
        ((METADATA_CAUSE, None),),
    )


def _frame_stepped_draft(frame: int, command_count: int) -> EventDraft:
    """构造 `replay:frame-stepped` 草稿（**负载两字段照抄契约声明**）。"""
    return make_draft(
        FRAME_STEPPED_EVENT,
        (("frame", frame), ("commandCount", command_count)),
        frame,
        ((METADATA_CAUSE, None),),
    )


__all__ = [
    "FRAME_STEPPED_EVENT",
    "PLAYBACK_STARTED_EVENT",
    "PLAYBACK_STOPPED_EVENT",
    "TICK_COMMAND",
    "FourItems",
    "PlaybackDriver",
    "PlaybackSession",
]
