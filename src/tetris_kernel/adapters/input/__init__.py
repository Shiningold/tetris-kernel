"""输入适配层（M4-S7 · FR-8.3 / FR-8.4 · NFR-8 · spec-M4 §7.7）。

**输入适配器只做一件事**：**按键 → 命令的翻译**。

- **不碰内核**（M4-D2）——**命令由宿主递进来**（`submit` 回调）· **事件也由宿主
  递出去**（`publish` 回调）→ **这个层连内核与总线都不用 import**；
- **手感参数归这里**（ADR-044）——**DAS / ARR 是本层自己的字段**，**内核与规则集
  都不需要知道"多久能再按一次"**（架构 §7.3 末段）· **改参数不触发状态变更**
  （NFR-8）；
- **不引第三方依赖**（spec §7.7）——**标准库 + 平台分支**。
"""

from .graphics_input import (
    TK_KEYSYMS,
    GraphicsInput,
    InputAdapter,
    normalize_keysym,
)
from .keyboard import (
    DAS_ACTIONS,
    DEFAULT_ARR_MS,
    DEFAULT_DAS_MS,
    INPUT_INTENT_EVENT,
    INPUT_RAW_EVENT,
    KEY_BINDINGS,
    KIND_PRESSED,
    KIND_RELEASED,
    Binding,
    CommandSubmitter,
    EventPublisher,
    KeyboardInput,
)
from .reader import (
    DEFAULT_IDLE_MS,
    KIND_DOWN,
    KIND_UP,
    KeyEvent,
    KeyReader,
    PosixKeyReader,
    WindowsKeyReader,
    create_key_reader,
)

__all__ = [
    "DAS_ACTIONS",
    "DEFAULT_ARR_MS",
    "DEFAULT_DAS_MS",
    "DEFAULT_IDLE_MS",
    "INPUT_INTENT_EVENT",
    "INPUT_RAW_EVENT",
    "KEY_BINDINGS",
    "KIND_DOWN",
    "KIND_PRESSED",
    "KIND_RELEASED",
    "KIND_UP",
    "TK_KEYSYMS",
    "Binding",
    "CommandSubmitter",
    "EventPublisher",
    "GraphicsInput",
    "InputAdapter",
    "KeyEvent",
    "KeyReader",
    "KeyboardInput",
    "PosixKeyReader",
    "WindowsKeyReader",
    "create_key_reader",
    "normalize_keysym",
]
