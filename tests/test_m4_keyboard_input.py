"""键盘输入适配器的用例（M4-S7 · FR-8.3 / FR-8.4 · NFR-8 · spec-M4 §7.7）。

**守的是口径，不是实现细节**：

- **映射表逐条等于手抄期望值**（方向键 / 旋转 / 硬降 / 暂存 / 暂停 各自对应
  哪条命令）；
- **单按 / 连按 / DAS 生效 / ARR 生效**四种情形 → **派出的命令序列逐条相等**；
- **调整 DAS / ARR 不触发状态变更**（NFR-8）——**改参数前后内核逐帧指纹全等**；
- **两条事件都发**（FR-8.4）——`input:raw` 与 `input:intent`，**负载字段齐全**；
- **输入适配器不碰内核**（M4-D2）——**源码级断言**。

**反向用例**（"让改参数顺手发个命令" → 必红）在
`test_改参数不改内核状态`。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tetris_kernel.adapters.input import (
    DAS_ACTIONS,
    INPUT_INTENT_EVENT,
    INPUT_RAW_EVENT,
    KEY_BINDINGS,
    KIND_DOWN,
    KIND_PRESSED,
    KIND_RELEASED,
    KIND_UP,
    KeyboardInput,
)
from tetris_kernel.host import assemble
from tetris_kernel.host.loop import HeadlessLoop

_INPUT_DIR = Path(__file__).resolve().parents[1] / "src" / "tetris_kernel" / "adapters" / "input"


class _Recorder:
    """**记下命令与事件**（**替身**：不碰内核、不碰总线）。"""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.events: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    def submit(self, command_type: str) -> None:
        self.commands.append(command_type)

    def publish(self, event_type: str, payload: tuple[tuple[str, str], ...]) -> None:
        self.events.append((event_type, payload))

    @property
    def command_types(self) -> list[str]:
        """**派出去的命令类型序列**（**逐条比对的抓手**）。"""
        return list(self.commands)

    @property
    def intents(self) -> list[tuple[str, str]]:
        """**意图事件序列**（动作名 · 命令类型）。"""
        return [
            (payload[0][1], payload[1][1])
            for event_type, payload in self.events
            if event_type == INPUT_INTENT_EVENT
        ]


def _adapter(recorder: _Recorder, *, das_ms: int = 150, arr_ms: int = 33) -> KeyboardInput:
    return KeyboardInput(
        submit=recorder.submit,
        publish=recorder.publish,
        das_ms=das_ms,
        arr_ms=arr_ms,
    )


# ---------------------------------------------------------------------------
# 映射表
# ---------------------------------------------------------------------------


def test_映射表逐条等于手抄期望值() -> None:
    """**每个键对应哪条命令**（**手抄期望值** · 逐条相等）。"""
    assert {key: binding.commandType for key, binding in KEY_BINDINGS.items()} == {
        "left": "moveLeft",
        "right": "moveRight",
        "up": "rotateCW",
        "z": "rotateCCW",
        "x": "rotate180",
        "down": "softDrop",
        "space": "hardDrop",
        "c": "hold",
        "p": "pause",
        "escape": "pause",
        "q": None,
    }


def test_映射表里的命令都已在契约里登记() -> None:
    """**写错命令名不会当场报错**——**内核会拒**，而错误信息不说是哪个名字错了
    → **导入期就对着契约查一遍**。"""
    from tetris_kernel.contracts.commands import COMMANDS

    for binding in KEY_BINDINGS.values():
        if binding.commandType is not None:
            assert binding.commandType in COMMANDS


def test_只有水平移位吃_das_arr() -> None:
    """**DAS / ARR 只给水平移位**（**标准手感**）。"""
    assert DAS_ACTIONS == frozenset({"left", "right"})


# ---------------------------------------------------------------------------
# 单按 / 连按
# ---------------------------------------------------------------------------


def test_单按一次派一条命令() -> None:
    """**单按**：一条命令。"""
    recorder = _Recorder()
    adapter = _adapter(recorder)
    adapter.handle_key("left", KIND_DOWN)
    assert recorder.command_types == ["moveLeft"]
    adapter.handle_key("left", KIND_UP)
    assert recorder.command_types == ["moveLeft"]


def test_连按两次派两条命令() -> None:
    """**连按**（按下 → 抬起 → 再按下）：两条命令。"""
    recorder = _Recorder()
    adapter = _adapter(recorder)
    adapter.handle_key("right", KIND_DOWN)
    adapter.handle_key("right", KIND_UP)
    adapter.handle_key("right", KIND_DOWN)
    assert recorder.command_types == ["moveRight", "moveRight"]


def test_终端的重复流不重复翻译() -> None:
    """**按住不放时终端会重复发"按下"** → **不重复翻译**（**连发归 DAS / ARR
    计时器** —— **它是权威**）。"""
    recorder = _Recorder()
    adapter = _adapter(recorder)
    adapter.handle_key("left", KIND_DOWN)
    adapter.handle_key("left", KIND_DOWN)
    adapter.handle_key("left", KIND_DOWN)
    assert recorder.command_types == ["moveLeft"]


# ---------------------------------------------------------------------------
# DAS / ARR
# ---------------------------------------------------------------------------


def test_das_生效_按住不到_das_不连发() -> None:
    """**DAS**：按住不到延迟 → **不连发**。"""
    recorder = _Recorder()
    adapter = _adapter(recorder, das_ms=150, arr_ms=33)
    adapter.handle_key("left", KIND_DOWN)
    adapter.poll(100)
    assert recorder.command_types == ["moveLeft"]


def test_das_生效_按住过了_das_开始连发() -> None:
    """**DAS**：按住过了延迟 → **开始连发**（**第一次连发在 DAS 时刻**）。"""
    recorder = _Recorder()
    adapter = _adapter(recorder, das_ms=150, arr_ms=33)
    adapter.handle_key("left", KIND_DOWN)
    adapter.poll(150)
    assert recorder.command_types == ["moveLeft", "moveLeft"]


def test_arr_生效_连发间隔按_arr() -> None:
    """**ARR**：连发之间的间隔按 ARR 走（**不是 DAS**）。"""
    recorder = _Recorder()
    adapter = _adapter(recorder, das_ms=100, arr_ms=50)
    adapter.handle_key("left", KIND_DOWN)
    adapter.poll(100)
    assert recorder.command_types == ["moveLeft", "moveLeft"]
    adapter.poll(140)  # **距上次派发 40ms < ARR 50ms** → 不发
    assert recorder.command_types == ["moveLeft", "moveLeft"]
    adapter.poll(150)  # **距上次派发 50ms = ARR** → 发
    assert recorder.command_types == ["moveLeft", "moveLeft", "moveLeft"]


def test_松手就停连发() -> None:
    """**抬起** → **不再连发**。"""
    recorder = _Recorder()
    adapter = _adapter(recorder, das_ms=100, arr_ms=20)
    adapter.handle_key("left", KIND_DOWN)
    adapter.poll(100)
    adapter.handle_key("left", KIND_UP)
    adapter.poll(500)
    assert recorder.command_types == ["moveLeft", "moveLeft"]


def test_非水平动作不吃_das() -> None:
    """**旋转 / 硬降 / 暂存 / 暂停是"按一下做一次"**（**不吃 DAS / ARR**）。"""
    recorder = _Recorder()
    adapter = _adapter(recorder, das_ms=100, arr_ms=20)
    adapter.handle_key("space", KIND_DOWN)
    adapter.poll(500)
    assert recorder.command_types == ["hardDrop"]


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------


def test_两条事件都发且负载字段齐全() -> None:
    """**`input:raw` 与 `input:intent` 都发**（FR-8.4 · **负载字段齐全**）。"""
    recorder = _Recorder()
    adapter = _adapter(recorder)
    adapter.handle_key("left", KIND_DOWN)
    adapter.handle_key("left", KIND_UP)
    assert recorder.events[0] == (INPUT_RAW_EVENT, (("action", "left"), ("kind", KIND_PRESSED)))
    assert recorder.events[1] == (
        INPUT_INTENT_EVENT,
        (("action", "left"), ("commandType", "moveLeft")),
    )
    assert recorder.events[2] == (INPUT_RAW_EVENT, (("action", "left"), ("kind", KIND_RELEASED)))


def test_没有命令的键只发原始事件() -> None:
    """**退出键没有命令可翻译** → **只有 `input:raw`**（`input:intent` 的
    `commandType` 是命令类型取值域，**塞别的就是违约**）。"""
    recorder = _Recorder()
    adapter = _adapter(recorder)
    adapter.handle_key("q", KIND_DOWN)
    assert recorder.events == [(INPUT_RAW_EVENT, (("action", "quit"), ("kind", KIND_PRESSED)))]
    assert recorder.commands == []


def test_不认识的键连原始事件都不发() -> None:
    """**不认识的键**：连 `input:raw` 都不发（**没有动作名**）。"""
    recorder = _Recorder()
    adapter = _adapter(recorder)
    adapter.handle_key("f1", KIND_DOWN)
    assert recorder.events == []
    assert recorder.commands == []


# ---------------------------------------------------------------------------
# 与内核的关系
# ---------------------------------------------------------------------------


def test_改参数不改内核状态() -> None:
    """**调整 DAS / ARR 不触发状态变更**（NFR-8 · FR-8.3）。

    **实证**：改参数前后，**内核逐帧指纹全等**（**改参数只是改适配器自己的
    字段**）。
    """
    assembly = assemble(seed=5)
    loop = HeadlessLoop(assembly)
    loop.start()
    recorder = _Recorder()
    adapter = _adapter(recorder)
    before = []
    for _ in range(60):
        loop.run_frame()
        before.append(loop.snapshot().board.structureFingerprint)

    adapter.set_das_ms(10)
    adapter.set_arr_ms(5)
    # **改参数之后**：一条命令都不该派（**改参数不是"让它生效"**）。
    assert recorder.commands == []
    assert adapter.das_ms == 10
    assert adapter.arr_ms == 5

    after = []
    for _ in range(60):
        loop.run_frame()
        after.append(loop.snapshot().board.structureFingerprint)
    assert before == after


def test_输入适配器不碰内核() -> None:
    """**源码级断言**：输入适配器**不 import 内核**（M4-D2）。

    **理由**：**命令由宿主递进来**（`submit` 回调）→ **适配器连内核都不用
    import**。
    """
    for path in sorted(_INPUT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert "kernel" not in name, f"{path.name} 不该 import 内核：{name}"


def test_按键事件能直接喂进适配器() -> None:
    """**读键那一层的输出能直接喂进适配器**（**接线不缺一环**）。"""
    from tetris_kernel.adapters.input import KeyEvent

    recorder = _Recorder()
    adapter = _adapter(recorder)
    adapter.handle_events((KeyEvent("space", KIND_DOWN),))
    assert recorder.command_types == ["hardDrop"]
    assert KIND_UP == "up"
