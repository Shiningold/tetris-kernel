"""成就系统（系统层 · M3-S5 · FR-9.3 · `G3-4` 的实现体）。

## 它是什么

**一个普通订阅者**（M3-D3）：订阅全局事件，按**事件类型与计数**判定成就，
达成时发一条 **`achievement:unlocked`**。

- **只消费事件，不写状态**：它**没有**任何写内核状态的路径（连视图都不取）；
- **达成条件在系统内**（ADR-046 同一把尺子）：事件只说「发生了什么」，
  「什么算达成」是消费方的事；
- **不落盘**（`needsSink=False`）：成就是**事件**，不是文件——
  订阅者要存要记自己存，**路径是宿主的事**（M3-D5）。

## 订阅与解耦

**它自己不订阅**——订阅集写在**声明**里（`manifest.py` 的 `subscribes=("*",)`），
由**注册表按声明**接上（`host/registry.py` 的 `_subscribe`）。
**订阅集只有一个真相**：声明里写一份，系统里不另写一份。

## 自产事件不自激

成就系统订阅全局，而它自己发的 `achievement:unlocked` 也是全局事件——
**不跳过它就会自激**（解锁一条成就 → 触发一次计数 → 可能再解锁一条）。
所以：**`achievement:unlocked` 一律不计入计数**（与指标系统对
`metric:sampled` 的处理同款）。

## 判定口径

- **按事件条数计数**：每收到一条事件，该事件类型的计数 +1；
- **达成即解锁**：计数 **≥ 阈值**时解锁（不是「恰好等于」——阈值前少一条
  就不该永远差一步）；
- **只解锁一次**：解锁过的成就不再重复解锁（否则每条后续事件都发一遍
  `achievement:unlocked`）。
"""

from __future__ import annotations

from ...bus.bus import EventBus
from ...bus.envelope import Envelope
from ...kernel.drafts import make_draft
from .definitions import DEFAULT_ACHIEVEMENTS, Achievement

#: 成就解锁事件（契约已登记 · 产出者 `achievements`）。
ACHIEVEMENT_UNLOCKED_EVENT = "achievement:unlocked"


class AchievementsSystem[V]:
    """成就系统：**普通订阅者**（只消费事件 · M3-D3）。

    - `bus`：事件总线（**只订不写内核状态**）；
    - `definitions`：成就定义（**默认是内建清单**）。

    **实例不自己订阅**：订阅由**注册表按声明**接上（`on_event` 是它交给
    注册表的入口）——**订阅集只有一个真相**（声明）。
    """

    def __init__(
        self,
        bus: EventBus[V],
        definitions: tuple[Achievement, ...] = DEFAULT_ACHIEVEMENTS,
    ) -> None:
        self._bus = bus
        self._definitions = tuple(definitions)
        self._counts: dict[str, int] = {}
        self._unlocked: list[Achievement] = []

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def definitions(self) -> tuple[Achievement, ...]:
        """本系统的成就定义（**顺序固定**）。"""
        return self._definitions

    @property
    def unlocked(self) -> tuple[str, ...]:
        """已解锁的成就编号（**按解锁顺序**）。"""
        return tuple(item.id for item in self._unlocked)

    def count_of(self, event_type: str) -> int:
        """某个事件类型累计收到几条（**没收到过就是 0**）。"""
        return self._counts.get(event_type, 0)

    # ------------------------------------------------------------------
    # 订阅回调
    # ------------------------------------------------------------------

    def on_event(self, envelope: Envelope) -> None:
        """吃一条事件：**先计数，再看有没有成就达成**。

        **自产事件不计数**（不自激）。
        """
        if envelope.type == ACHIEVEMENT_UNLOCKED_EVENT:
            return
        count = self._counts.get(envelope.type, 0) + 1
        self._counts[envelope.type] = count
        for achievement in self._definitions:
            if achievement.trigger_type != envelope.type:
                continue
            if achievement.id in self.unlocked:
                continue
            if count < achievement.threshold:
                continue
            self._unlock(achievement, envelope)

    # ------------------------------------------------------------------
    # 解锁
    # ------------------------------------------------------------------

    def _unlock(self, achievement: Achievement, envelope: Envelope) -> None:
        """解锁一条成就并发事件（**负载四字段照抄契约声明**）。

        `triggerSeq` 记的是**触发它的那条事件的全局序号**——不是成就自己的
        序号（那会儿还没分配）。**序号是事件溯源的唯一时间轴**（架构 §4.2）。
        """
        self._unlocked.append(achievement)
        self._bus.publish(
            make_draft(
                ACHIEVEMENT_UNLOCKED_EVENT,
                (
                    ("id", achievement.id),
                    ("title", achievement.title),
                    ("triggerType", achievement.trigger_type),
                    ("triggerSeq", envelope.seq),
                ),
                envelope.frame,
            )
        )


__all__ = ["ACHIEVEMENT_UNLOCKED_EVENT", "AchievementsSystem"]
