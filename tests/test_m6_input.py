"""图形输入桥的用例（M6-S2 · `G6-3` · spec-M6 §7.2 / §9.1 · ADR-100）。

**守的是口径，不是实现细节**：

- **归一表逐条**（`G6-3`）——**tkinter 键名 → 归一键名**；**认不出来就是
  `None`**（不认识的键不许猜）；
- **命令逐条相同**（`G6-3`）——**同一串按键**，经输入桥与直接喂
  `KeyboardInput`，**产出的命令序列逐条相同**（**手感不许漂**：漂了同一个
  种子就走不出同一局 · `G6-5` 立刻不成立）；
- **不认识的键不发命令**、**轮询只是转交**（**DAS / ARR 在键盘适配器那边**）。
"""

from __future__ import annotations

from tetris_kernel.adapters.input import (
    KIND_DOWN,
    KIND_UP,
    GraphicsInput,
    KeyboardInput,
    normalize_keysym,
)

# ---------------------------------------------------------------------------
# G6-3 · 输入等价
# ---------------------------------------------------------------------------


def _recording_keyboard() -> tuple[KeyboardInput, list[str]]:
    """**一个会记账的键盘适配器**（**记它派了哪些命令**）。"""
    commands: list[str] = []

    def submit(command_type: str) -> None:
        commands.append(command_type)

    def publish(event_type: str, payload: tuple[tuple[str, str], ...]) -> None:
        del event_type, payload

    keyboard = KeyboardInput(submit=submit, publish=publish)
    return keyboard, commands


def test_键名归一表逐条() -> None:
    """**tkinter 键名 → 归一键名**（**认不出来就是 `None`** —— 不许猜）。"""
    assert normalize_keysym("Left") == "left"
    assert normalize_keysym("Right") == "right"
    assert normalize_keysym("Up") == "up"
    assert normalize_keysym("Down") == "down"
    assert normalize_keysym("space") == "space"
    assert normalize_keysym("Escape") == "escape"
    # **字母键压小写**（按住 Shift 时 tkinter 给的是大写）。
    assert normalize_keysym("z") == "z"
    assert normalize_keysym("Z") == "z"
    assert normalize_keysym("c") == "c"
    assert normalize_keysym("q") == "q"
    # **不认识的键 → `None`**（Shift_L / F1 / Control_L 之类）。
    assert normalize_keysym("Shift_L") is None
    assert normalize_keysym("F1") is None
    assert normalize_keysym("Control_L") is None


def test_图形输入与键盘适配器命令逐条相同() -> None:
    """**同一串按键 → 同样的命令序列**（**图形层只做翻译**）。

    **这是 `G6-3`**：两条路（**直接喂 `KeyboardInput`** 与
    **经输入桥喂**）产出的命令**逐条相同**——**手感不许漂**（漂了同种子就
    走不出同一局 · `G6-5` 会立刻不成立）。
    """
    direct, direct_commands = _recording_keyboard()
    bridged, bridged_commands = _recording_keyboard()
    bridge = GraphicsInput(bridged)

    sequence = [
        ("Left", KIND_DOWN),
        ("Left", KIND_UP),
        ("space", KIND_DOWN),
        ("space", KIND_UP),
        ("Up", KIND_DOWN),
        ("Up", KIND_UP),
        ("z", KIND_DOWN),
        ("z", KIND_UP),
        ("c", KIND_DOWN),
        ("c", KIND_UP),
        ("p", KIND_DOWN),
        ("p", KIND_UP),
        ("q", KIND_DOWN),
    ]
    for keysym, kind in sequence:
        key = normalize_keysym(keysym)
        assert key is not None
        direct.handle_key(key, kind)
        assert bridge.handle_tk_key(keysym, kind)

    assert bridged_commands == direct_commands
    assert "hardDrop" in direct_commands
    assert "rotateCW" in direct_commands
    assert "rotateCCW" in direct_commands
    assert "hold" in direct_commands
    assert "pause" in direct_commands


def test_不认识的键不产生命令() -> None:
    """**不认识的键 → 一条命令都不发**（**也不许报错**）。"""
    keyboard, commands = _recording_keyboard()
    bridge = GraphicsInput(keyboard)
    assert bridge.handle_tk_key("Shift_L", KIND_DOWN) is False
    assert commands == []


def test_输入桥的轮询转交给键盘适配器() -> None:
    """**轮询转交**（**DAS / ARR 在键盘适配器那边** · 桥不自己实现）。"""
    keyboard, commands = _recording_keyboard()
    bridge = GraphicsInput(keyboard)
    bridge.handle_tk_key("Left", KIND_DOWN)
    bridge.poll_keys(0)
    assert "moveLeft" in commands
