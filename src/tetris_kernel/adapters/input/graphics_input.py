"""图形输入桥（适配层 · M6-S2 · spec-M6 §7.2 · ADR-100）。

## 它是什么

**把 tkinter 的按键翻译成"归一键名"**，再交给**既有的 `KeyboardInput`**。

## 为什么只做翻译（**不重写手感**）

`KeyboardInput` **只吃"键名 + 按下 / 抬起"，不读键**（读键是 `reader.py` 的
事）——所以**DAS / ARR / 命令翻译 / `input:raw` / `input:intent` 两条事件**，
**整套都在 `KeyboardInput` 里**。

**图形版要是自己写一套 DAS / ARR，两套手感迟早分叉**——而手感一旦分叉，
**同一个种子会走出不同的局**（`G6-5` 的"同种子同指纹"立刻不成立）。所以
**这里只做一层键名翻译**（M6-S2 的全部内容）。

## 归一键名

`left` · `right` · `up` · `down` · `space` · `z` · `x` · `c` · `p` · `escape` ·
`q`——**与 `KeyboardInput.KEY_BINDINGS` 的键名完全一致**（平台差异在
`reader.py` 里封掉 · 这里封的是 **tkinter 的键名**）。
"""

from __future__ import annotations

from typing import Protocol

from .keyboard import KIND_DOWN, KIND_UP, KeyboardInput

#: **tkinter 键名 → 归一键名**（**只列方向键与功能键**）。
#:
#: **字母键不在表里**（单字符 → 小写即可，见 `normalize_keysym`）——**表越
#: 小越不容易漂**。
#:
#: **注意大小写**：tkinter 的字母键在按住 Shift 时给的是**大写**（`"Z"`），
#: 而 `KEY_BINDINGS` 的键名是小写 —— 归一时统一压小写。
TK_KEYSYMS: dict[str, str] = {
    "Left": "left",
    "Right": "right",
    "Up": "up",
    "Down": "down",
    "space": "space",
    "Escape": "escape",
}


class InputAdapter(Protocol):
    """**输入适配器的最小面**（**宿主层只认这三个方法**）。

    **为什么用协议**：`PlaySession` 只需要"**轮询时取键**"与"**收尾**"——
    **它不该知道底下是终端读键器还是 tkinter 事件**。协议把这层依赖钉死，
    **新的输入来源不用改宿主**。
    """

    def poll_keys(self, now_ms: int) -> None:
        """**轮询时取键**（**宿主每帧调一次**）。"""
        ...

    def close(self) -> None:
        """**收尾**（**恢复终端设置 / 解绑事件**）。"""
        ...


def normalize_keysym(keysym: str) -> str | None:
    """**tkinter 键名 → 归一键名**（**认不出来就返回 `None`**）。

    - **方向键与功能键**：查 `TK_KEYSYMS`；
    - **字母键**：**单字符压小写**（`"z"` / `"Z"` → `"z"`）；
    - **别的**（`"Shift_L"` / `"F1"` / 多字符的 `"Control_L"` 等）→ **`None`**
      （**不认识的键不许猜**——猜错就是"按了没反应"或"按了不该有的反应"）。
    """
    mapped = TK_KEYSYMS.get(keysym)
    if mapped is not None:
        return mapped
    if len(keysym) == 1 and keysym.isalpha():
        return keysym.lower()
    return None


class GraphicsInput:
    """**图形输入桥**（tkinter 按键 → 归一键名 → `KeyboardInput`）。

    **它只做翻译**（见模块头）——**手感参数（DAS / ARR）仍是 `KeyboardInput`
    的**，**一条命令都不自己造**。
    """

    def __init__(self, keyboard: KeyboardInput) -> None:
        """构造输入桥。

        - `keyboard`：**既有的键盘适配器**（**翻译与命令造句都归它**）。
        """
        self._keyboard = keyboard

    @property
    def keyboard(self) -> KeyboardInput:
        """**底下那个键盘适配器**（**测试要能直接看它派了哪些命令**）。"""
        return self._keyboard

    def handle_tk_key(self, keysym: str, kind: str) -> bool:
        """**一个 tkinter 按键事件** → **归一键名** → 交给键盘适配器。

        **返回**：**认不认识这个键**（`False` = 忽略它 · **不影响别的键**）。
        """
        key = normalize_keysym(keysym)
        if key is None:
            return False
        if kind not in (KIND_DOWN, KIND_UP):
            return False
        self._keyboard.handle_key(key, kind)
        return True

    def poll_keys(self, now_ms: int) -> None:
        """**轮询时取键**（**转交给键盘适配器** —— DAS / ARR 在那边）。"""
        self._keyboard.poll_keys(now_ms)

    def close(self) -> None:
        """**收尾**（**转交**）。"""
        self._keyboard.close()


__all__ = [
    "TK_KEYSYMS",
    "GraphicsInput",
    "InputAdapter",
    "normalize_keysym",
]
