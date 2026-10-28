"""统计旋转次数系统（系统层 · M3-S6 · AC-3 · `G3-1` 的实现体）。

## 它是什么

**一个普通订阅者**（M3-D3）：订阅 `piece:rotated`，**数成功的旋转次数**，
产出**经 sink 落盘**。

- **只消费事件，不写状态**（连视图都不取）；
- **落盘只走 sink**（M3-D5 · ADR-064）：**不知道路径**，只把字节交出去——
  **不许自己 `open()`**（那是宿主的活）；
- **零内核改动**：这个系统的全部代码落在**新增文件**里，既有文件一个字节
  都没动（M3-D2 / `G3-1`）。

## 口径：**只算成功的旋转**

`piece:rotated` 的负载里有 `blocked`（**这次旋转被墙挡住了没有**）。

- **成功**（`blocked=False`）→ **计一次**；
- **受阻**（`blocked=True`）→ **不计**。

**口径写死在这里，也写死在这里的用例里**（plan-M3 S6 的常见坑：
「受阻的旋转也算一次」→ 口径不写死，各处实现不一致）。
**受阻的次数照样记着**（`skipped`）——它不是「没发生」，是「没转成」。

## 落盘出口从哪来

**宿主注入**（ADR-064）。工厂从**安装上下文**取出口（`context.sink`）——
`None` 时它**只计数不落盘**（宿主没给出口就是这个意思）。
**它自己不许 `open()`**——路径是宿主的事。
"""

from __future__ import annotations

import json
from collections.abc import Callable

from ...bus.bus import EventBus
from ...bus.envelope import Envelope
from ...kernel.drafts import payload_get

#: 落盘出口（**宿主注入的可调用对象** · ADR-064）。
#:
#: **为什么在这里再写一遍别名**：`systems/` **不能** import `host/`
#: （依赖方向），拿不到 `host.assembly.Sink`；它也**不能** import
#: `systems.metrics`（系统之间互不直调）。它是**同一个可调用类型**
#: （结构化类型），这里自己写一份即可——**别名不是契约，不进基线**。
type Sink = Callable[[bytes], None]

#: 被统计的旋转事件（契约已登记 · 产出者是内核）。
PIECE_ROTATED_EVENT = "piece:rotated"


class RotationsSystem[V]:
    """旋转统计系统：**普通订阅者**（只消费事件 · M3-D3）。

    - `bus`：事件总线（**只订不写内核状态**）；
    - `sink`：**落盘出口**（`None` = 不落盘）；**路径是宿主的事**。

    **实例不自己订阅**：订阅由**注册表按声明**接上（`on_event` 是它交给
    注册表的入口）——**订阅集只有一个真相**（声明）。
    """

    def __init__(self, bus: EventBus[V], sink: Sink | None = None) -> None:
        self._bus = bus
        self._sink = sink
        self._count = 0
        self._skipped = 0

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        """**成功**的旋转次数（**受阻的不算**——口径见模块 docstring）。"""
        return self._count

    @property
    def skipped(self) -> int:
        """**受阻**的旋转次数（**没转成**，但确实发生过）。"""
        return self._skipped

    # ------------------------------------------------------------------
    # 订阅回调
    # ------------------------------------------------------------------

    def on_event(self, envelope: Envelope) -> None:
        """吃一条事件：**只认 `piece:rotated`**；受阻的不计。"""
        if envelope.type != PIECE_ROTATED_EVENT:
            return
        if _is_blocked(envelope):
            self._skipped += 1
            return
        self._count += 1
        self._write(envelope.frame)

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------

    def _write(self, frame: int) -> None:
        """把当前计数交给 sink（**JSON 行**——一行一条，机器可读、可 diff）。

        **先计数再落盘**：落的是**这次旋转之后**的累计值。
        """
        if self._sink is None:
            return
        line = json.dumps(
            {"rotations": self._count, "frame": frame},
            ensure_ascii=False,
            sort_keys=True,
        )
        self._sink((line + "\n").encode("utf-8"))


def _is_blocked(envelope: Envelope) -> bool:
    """这次旋转**被墙挡住了没有**（负载里的 `blocked`）。

    **缺字段按「不算受阻」处理**（拿不到就是不知道，不猜）——
    但**正常路径上它一定在**（`piece:rotated` 的负载是契约写死的四字段）。
    """
    return bool(payload_get(envelope.payload, "blocked"))


__all__ = ["PIECE_ROTATED_EVENT", "RotationsSystem", "Sink"]
