"""实时宿主（宿主层 · M4-S8 · FR-8.x · spec-M4 §7.5 · ADR-082）。

## 它是什么

**人真的能玩的那一层**：真实时钟 → 固定步长累积 → 每步推进一帧 → 渲染；
**输入在轮询间隙读**（**不阻塞推进**）。

## 它复用什么、不重写什么

**帧的那一套（tick → 命令轮次 → 帧推进五步 → 扩展槽合并 → `state:changed`）
是 `HeadlessLoop` 的**——**实时宿主不重写它**，**只是在外面套一层"什么时候
推进一帧"的节拍**。

**重写一遍就意味着两处真相**：**内核的帧协议只有一份**（架构 §6.2）。

## 三件外围的事

| 谁 | 干什么 | 归谁管 |
| --- | --- | --- |
| **时钟** | 真实时钟 → 固定步长累积 | **宿主层**（架构 §5.3：**内核不读墙钟**） |
| **输入** | 轮询间隙读键 → 翻译成命令 | **适配器**（`adapters/input`） |
| **渲染** | 帧末画一帧 | **适配器**（`adapters/render`） |

**渲染与输入都不计入逻辑帧预算**（NFR-2 明写：渲染 · 日志落盘 · 指标导出 ·
网络 IO 一律不计入）——**降级判据喂的是"推进那一帧"的耗时**。

## 降级接线（ADR-082 遗留）

**每帧喂一次逻辑耗时** → 控制器按 **600 帧滑动窗口 P99** 判档（**判据用 P99
不用均值** · 架构 §7.4）→ **三档顺序固定**：**关调试事件 → 降日志级别 → 关历史
缓冲**。

**不许引入"关闭事件分发"的快速模式**（架构 §7.4）——**那一档是"关历史缓冲"，
不是"关分发"**。

## 掉队怎么办

**真实时钟赶不上就少跑几帧**（**追帧上限**）——**不能因为某一帧慢了就疯狂补
帧**（那会让机器更慢，**越补越慢**）。

**积压直接丢弃**（`accumulator` 清零）——**宁可掉时间，也不越陷越深**。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..adapters.input import KeyboardInput
from ..adapters.render import TerminalRenderer
from ..kernel.drafts import payload_get
from ..kernel.snapshot import Snapshot
from .assembly import Assembly, FrameCounter
from .clock import Clock, RealClock
from .loop import HeadlessLoop
from .performance import DegradationController, measure_frame

#: **一次轮询里最多补几帧**（**掉队时的兜底** · 不追到天荒地老）。
MAX_CATCHUP_FRAMES = 5

#: 局结束事件（**订阅它比每帧拉快照省**）。
GAME_OVER_EVENT = "game:over"

#: 原始输入事件（**玩家按退出键** → **喊停**）。
INPUT_RAW_EVENT = "input:raw"

#: 退出动作名（**输入适配器的映射表里那一条**）。
QUIT_ACTION = "quit"


@dataclass(frozen=True)
class PlayReport:
    """**实时对局跑完的统计**（**跑完时的快照口径**）。

    字段与 `RunReport` 同源（**分数 / 行数 / 方块数 / 相位 / 结算三项**），
    **另加 `frames` 与 `fingerprint`**（**棋盘结构指纹**——**回放对得上就靠
    它**）。
    """

    frames: int
    events: int
    fingerprint: str
    score: int
    lines: int
    piecesPlaced: int
    phase: str
    combo: int
    b2b: bool
    level: int


class PlaySession:
    """**实时对局**：节拍 · 输入 · 渲染 · 降级喂帧。

    **它不碰内核状态**——**推进帧的是 `HeadlessLoop`**（**帧协议只有一份**）。
    """

    def __init__(
        self,
        *,
        assembly: Assembly,
        loop: HeadlessLoop,
        renderer: TerminalRenderer | None = None,
        input_adapter: KeyboardInput | None = None,
        degradation: DegradationController[Snapshot] | None = None,
        clock: Clock | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        frame_counter: FrameCounter | None = None,
        source: str = "host",
    ) -> None:
        """构造实时对局。

        - `assembly` / `loop`：**装配好的三件套与无头帧循环**（**帧协议归它**）；
        - `renderer`：**终端渲染器**（**没有就不画** —— **无头行为一个字节都不
          变**）；
        - `input_adapter`：**键盘适配器**（**没有就没有输入**）；
        - `degradation`：**降级控制器**（**没有就不喂帧开销**）；
        - `clock`：**时钟源**（默认真实时钟 · **回放与测试注入虚拟时钟**）；
        - `sleeper`：**等待函数**（默认 `time.sleep` · **测试注入空实现** →
          **不真的等**）；
        - `frame_counter`：**帧号载体**（**输入事件的帧号取它** · **装配层给的
          那个**）。
        """
        self._assembly = assembly
        self._loop = loop
        self._renderer = renderer
        self._input = input_adapter
        self._degradation = degradation
        self._clock = RealClock() if clock is None else clock
        self._sleeper = sleeper
        self._source = source
        self._frames = 0
        self._stopped = False
        self._game_over = False
        self._frame_counter = frame_counter
        # **局结束与退出都靠事件**（**不用每帧拉快照问一遍**）。
        assembly.bus.subscribe(GAME_OVER_EVENT, self._on_game_over)
        assembly.bus.subscribe(INPUT_RAW_EVENT, self._on_input_raw)

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def frames(self) -> int:
        """**已经推进的帧数**。"""
        return self._frames

    @property
    def stopped(self) -> bool:
        """**是不是已经喊停了**（**结束条件**：局结束 / 玩家退出 / 帧数到了）。"""
        return self._stopped

    def stop(self) -> None:
        """**喊停**（**玩家按退出键** · 下一轮轮询就收工）。"""
        self._stopped = True

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    def _on_game_over(self, envelope: object) -> None:
        """**局结束**（**内核发的** —— **宿主只是记住它**）。"""
        del envelope
        self._game_over = True

    def _on_input_raw(self, envelope: object) -> None:
        """**原始输入** → **玩家按了退出键就喊停**。"""
        payload = getattr(envelope, "payload", ())
        action = payload_get(payload, "action") if isinstance(payload, tuple) else None
        if action == QUIT_ACTION:
            self._stopped = True

    # ------------------------------------------------------------------
    # 跑
    # ------------------------------------------------------------------

    def run(self, *, frames: int | None = None, until_game_over: bool = True) -> PlayReport:
        """跑一局（**按真实时钟的节拍**）。

        - `frames`：**最多跑几帧**（`None` = 不限）；
        - `until_game_over`：**局结束就停**（**默认开**）。

        **返回跑完时的统计**。
        """
        step_ms = self._clock.step_ms()
        accumulator = 0
        last_ms = self._clock.now_ms()
        while not self._stopped:
            if frames is not None and self._frames >= frames:
                break
            now_ms = self._clock.now_ms()
            accumulator += now_ms - last_ms
            last_ms = now_ms
            ran = 0
            while accumulator >= step_ms and ran < MAX_CATCHUP_FRAMES:
                accumulator -= step_ms
                self._run_frame()
                ran += 1
                if until_game_over and self._game_over:
                    self._stopped = True
                    break
            if ran >= MAX_CATCHUP_FRAMES:
                # **积压太多 → 直接丢**（**追帧追到天荒地老就是死亡螺旋**）。
                accumulator = 0
            remaining = step_ms - accumulator
            if remaining > 0 and not self._stopped:
                self._sleeper(remaining / 1000)
        return self._report()

    def _run_frame(self) -> None:
        """推进一帧（**输入 → 推进 → 喂开销 → 渲染**）。

        **顺序是有讲究的**：

        1. **先读输入**（**这一帧刚按的键立刻生效**）；
        2. **再推进帧**（**内核那一套** · `HeadlessLoop`）；
        3. **喂帧开销**（**只算推进的耗时** —— **渲染不计入逻辑帧预算**）；
        4. **最后渲染**（**画面是这一帧的结果**）。
        """
        if self._input is not None:
            self._input.poll_keys(self._clock.now_ms())
        if self._frame_counter is not None:
            # **帧号先推进**：**这一帧读到的输入**带的帧号就是**这一帧的帧号**。
            self._frame_counter.advance()
        _, cost_ms = measure_frame(self._loop.run_frame)
        self._frames += 1
        if self._degradation is not None:
            self._degradation.observe_frame(cost_ms)
        if self._renderer is not None:
            self._sync_renderer()
            self._renderer.draw()

    def _sync_renderer(self) -> None:
        """**用帧末快照校正活动方块**（**内核的暂存换块路径不发 `piece:spawned`**
        ——**冻结区零改动下的结构性代价** · 渲染器模块头有细说）。"""
        if self._renderer is None:
            return
        active = self._loop.snapshot().active
        if active is None:
            self._renderer.sync_active(None, 0, 0, 0)
            return
        self._renderer.sync_active(active.pieceType, active.rotation, active.x, active.y)

    def _report(self) -> PlayReport:
        """跑完时的统计（**快照口径**）。"""
        snapshot = self._loop.snapshot()
        return PlayReport(
            frames=self._frames,
            events=self._assembly.bus.last_seq,
            fingerprint=snapshot.board.structureFingerprint,
            score=snapshot.score,
            lines=snapshot.lines,
            piecesPlaced=snapshot.piecesPlaced,
            phase=snapshot.phase,
            combo=snapshot.combo,
            b2b=snapshot.b2b,
            level=snapshot.level,
        )

    def close(self) -> None:
        """**收尾**（**恢复终端设置**）。"""
        if self._input is not None:
            self._input.close()


__all__ = [
    "GAME_OVER_EVENT",
    "INPUT_RAW_EVENT",
    "MAX_CATCHUP_FRAMES",
    "QUIT_ACTION",
    "PlayReport",
    "PlaySession",
]
