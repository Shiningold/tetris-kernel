"""宿主时钟源：真实 / 虚拟两种实现，同一接口（架构 §5.3 · plan-M0.md S7 口径 2）。

**时钟落宿主层，不进 `kernel/`**：架构 §5.3 明说「内核侧只认逻辑帧」，而
纯净度脚本**禁止 `kernel/` 与 `rules/` 出现 `time.monotonic()`**——真实时钟
放不进内核。

**真实 / 虚拟是同一接口的两种实现，不拆到两层**：拆了就是两处维护同一对
签名。宿主装配时注入哪一个，回放 / 逐帧步进 / 倍速播放就走哪一个。

**内核侧只认逻辑帧**：帧号是内核的逻辑时间（逻辑时间 = 帧号 × 步长），时钟
只回答两件事——「现在过了多少毫秒」与「每帧固定多少毫秒」。

`kernel/` 里**不放时钟接口**：内核不用它，放进去是没人用的抽象。
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

#: 默认固定步长（毫秒）。逻辑时间 = 帧号 × 步长（架构 §6.1）。
DEFAULT_STEP_MS = 16


@runtime_checkable
class Clock(Protocol):
    """帧循环时钟源的接口——**宿主的注入点**。

    结构化子类型：真实时钟与虚拟时钟都满足它，宿主只认这一个接口。
    """

    def now_ms(self) -> int:
        """当前时刻（毫秒，单调不减；相对起点，不是墙上时刻）。"""
        ...

    def step_ms(self) -> int:
        """本帧的固定步长（毫秒）。"""
        ...


class RealClock:
    """真实时钟：读 `time.monotonic()`（宿主层，禁读墙钟的规矩管不到这里）。"""

    def __init__(self, step_ms: int = DEFAULT_STEP_MS) -> None:
        if step_ms <= 0:
            raise ValueError(f"步长必须为正：{step_ms}")
        self._step_ms = step_ms
        self._origin = time.monotonic()

    def now_ms(self) -> int:
        """相对起点的毫秒数（**整数**毫秒——浮点秒的尾数会随平台漂）。"""
        return int((time.monotonic() - self._origin) * 1000)

    def step_ms(self) -> int:
        """本帧的固定步长（毫秒）。"""
        return self._step_ms


class VirtualClock:
    """虚拟时钟：**外部显式推进**，回放 / 逐帧步进 / 倍速播放用。

    不读任何真实时间——同一串推进命令在任何机器上得到同一串时刻。
    """

    def __init__(self, start_ms: int = 0, step_ms: int = DEFAULT_STEP_MS) -> None:
        if step_ms <= 0:
            raise ValueError(f"步长必须为正：{step_ms}")
        self._now_ms = start_ms
        self._step_ms = step_ms
        self._frames = 0

    def now_ms(self) -> int:
        """当前时刻（毫秒）——**只随 `advance()` 变**。"""
        return self._now_ms

    def step_ms(self) -> int:
        """本帧的固定步长（毫秒）。"""
        return self._step_ms

    @property
    def frames(self) -> int:
        """已推进的逻辑帧数。"""
        return self._frames

    def advance(self, frames: int = 1) -> int:
        """推进若干逻辑帧（按固定步长），返回推进后的毫秒数。

        帧数不能为负——虚拟时钟只往前走（时间倒流没有对应的玩法语义）。
        """
        if frames < 0:
            raise ValueError(f"帧数不能为负：{frames}")
        self._frames += frames
        self._now_ms += frames * self._step_ms
        return self._now_ms


__all__ = [
    "DEFAULT_STEP_MS",
    "Clock",
    "RealClock",
    "VirtualClock",
]
