"""图形版实时宿主（宿主层 · M6-S3 · spec-M6 §7.3 / §7.5 · ADR-100）。

## 它是什么

**把内核的一局变成一个 tkinter 窗口**：窗口 · 棋盘 · 面板 · 键盘 · 真实
时钟。

## 三条接线纪律

| 纪律 | 做法 |
| --- | --- |
| **帧协议只有一份** | **推进帧的仍是 `HeadlessLoop`**（`PlaySession`）——**这里不重写一局** |
| **节拍归宿主** | 图形侧只注入"**泵 tkinter 事件循环的 `sleeper`**"；**不用 `after()`** |
| **手感不重写** | 输入走 `GraphicsInput` → **既有的 `KeyboardInput`**（DAS / ARR 仍在那边） |

## 难度怎么落地

**起始等级**用**既有的 `setLevel` 命令**（**不新增命令** · 契约面零变化）——
**开局后立刻发一条**（`setLevel` **不受生命周期门禁约束**）。

## 画面怎么来的

每帧末尾 `PlaySession` 调 `on_frame` 回调（**收到当帧快照**）→ 喂给
`GraphicsRenderer` → `TkPainter` 重画。**渲染不计入逻辑帧预算**（NFR-2）。
"""

from __future__ import annotations

import time

from ..adapters.input import GraphicsInput, InputAdapter, KeyboardInput
from ..adapters.render import GraphicsRenderer, TkPainter
from ..kernel.snapshot import Snapshot
from .assembly import assemble_play
from .loop import HeadlessLoop
from .menu import MenuChoice, play_options, start_level_command
from .play import PlaySession

#: 棋盘尺寸（**构造参数** · 契约里没有尺寸事件）。
DEFAULT_WIDTH = 10
DEFAULT_HEIGHT = 20

#: **一格多少像素**（**纯展示**）。
CELL_SIZE = 26

#: **画布边距**（像素）。
MARGIN = 18


class GraphicsSession:
    """**图形版的一局**（窗口 · 输入 · 画面 · 真实时钟）。"""

    def __init__(
        self,
        *,
        choice: MenuChoice,
        seed: int = 0,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        step_ms: int = 16,
    ) -> None:
        """构造图形会话。

        - `choice`：**菜单选择**（玩法 + 难度）；
        - `seed`：**随机种子**（**同种子同开局** → 可复现）；
        - `width` / `height`：**棋盘尺寸**；
        - `step_ms`：**一帧多少毫秒**（**节拍**）。
        """
        import tkinter

        self._tk = tkinter
        self._choice = choice
        self._options = play_options(choice.variant_id)
        self._root = tkinter.Tk()
        self._root.title("俄罗斯方块内核 · tetris-kernel")
        self._root.configure(bg="#101014")
        canvas = tkinter.Canvas(
            self._root,
            width=MARGIN * 2 + (width + 12) * CELL_SIZE,
            height=MARGIN * 2 + height * CELL_SIZE,
            bg="#101014",
            highlightthickness=0,
        )
        canvas.pack()
        self._canvas = canvas
        self._renderer = GraphicsRenderer(
            rule_set=self._options.rule_set,
            width=width,
            height=height,
        )
        self._painter = TkPainter(canvas, self._renderer, cell_size=CELL_SIZE, margin=MARGIN)

        self._bridge: GraphicsInput | None = None
        play = assemble_play(
            seed=seed,
            rule_set=self._options.rule_set,
            rule_id=self._options.rule_id,
            systems=self._options.systems,
            variant_id=self._options.variant_id,
            board_width=width,
            board_height=height,
            step_ms=step_ms,
            render=False,
            input_wrapper=self._wrap_input,
        )
        self._play = PlaySession(
            assembly=play.assembly,
            loop=HeadlessLoop(play.assembly),
            renderer=None,
            input_adapter=play.input_adapter,
            sleeper=self._sleeper,
            on_frame=self._on_frame,
        )
        # **难度 = 起始等级**（**用既有的 `setLevel` 命令** · 不新增命令）。
        play.assembly.kernel.submit(start_level_command(choice.start_level))
        self._bind_keys()

    # ------------------------------------------------------------------
    # 接线
    # ------------------------------------------------------------------

    def _wrap_input(self, keyboard: KeyboardInput) -> InputAdapter:
        """**把键盘适配器包成输入桥**（**顺手留一份引用** —— 按键要交给它）。"""
        bridge = GraphicsInput(keyboard)
        self._bridge = bridge
        return bridge

    def _bind_keys(self) -> None:
        """**把 tkinter 按键接到输入桥**（**按下与抬起都要** —— DAS 需要抬起）。"""
        self._canvas.bind("<KeyPress>", self._on_key_press)
        self._canvas.bind("<KeyRelease>", self._on_key_release)
        self._canvas.focus_set()

    def _on_key_press(self, event: object) -> None:
        """**按下**（**键名由输入桥翻译**）。"""
        self._dispatch_key(event, "down")

    def _on_key_release(self, event: object) -> None:
        """**抬起**（**DAS / ARR 要它**）。"""
        self._dispatch_key(event, "up")

    def _dispatch_key(self, event: object, kind: str) -> None:
        """**一个按键事件** → **输入桥**（**认不出来就忽略**）。"""
        keysym = getattr(event, "keysym", None)
        if not isinstance(keysym, str):
            return
        bridge = self._bridge
        if bridge is not None:
            bridge.handle_tk_key(keysym, kind)

    def _on_frame(self, snapshot: Snapshot) -> None:
        """**每帧末尾**（**拿到当帧快照**）→ **喂渲染器 → 重画**。"""
        self._renderer.update_snapshot(snapshot)
        self._painter.paint()

    def _sleeper(self, seconds: float) -> None:
        """**等待 = 泵 tkinter 事件循环**（**节拍仍归宿主**）。

        **为什么要泵**：tkinter 的事件循环**不跑就没有按键**——**不泵就等于
        没有输入**。**每 5 ms 醒一次**（**够灵敏 · 也不烧 CPU**）。
        """
        deadline = time.monotonic() + seconds
        while True:
            self._root.update()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.005, remaining))

    # ------------------------------------------------------------------
    # 跑
    # ------------------------------------------------------------------

    def run(self) -> None:
        """**跑一局**（**跑到局结束或玩家退出**）→ **窗口留着看结算**。"""
        try:
            self._play.run()
        finally:
            self._root.update()
        # **局结束后窗口不立刻关**（**玩家要看结算**）——等关窗或按 q。
        self._root.bind("<KeyPress>", self._on_key_press)
        self._root.mainloop()

    def close(self) -> None:
        """**收尾**（**关窗口**）。"""
        try:
            self._root.destroy()
        except Exception:  # pragma: no cover - 窗口已经没了
            pass


__all__ = [
    "CELL_SIZE",
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "MARGIN",
    "GraphicsSession",
]
