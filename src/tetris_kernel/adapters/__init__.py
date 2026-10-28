"""适配层：输入（DAS/ARR）· 渲染 · 存储。

只做与外界交互的部件，全部降格为系统的一种；不得写内核状态。

## 存储（`storage`）

把字节落成文件的落盘出口。**它不知道默认路径**——路径由调用方给（默认
落盘路径策略是**宿主的装配决定**，ADR-074），也**不读任何环境变量**。

**两种写入语义**（ADR-085）：`FileSink`（**覆盖** · 录像整份交出）·
`AppendSink`（**追加** · 日志与指标一条一条）。**不是一个类的两种模式**——
写错了就是数据丢。

## 输入（`input` · M4-S7）

**按键 → 命令的翻译器**：读键（跨平台 · 标准库）· 翻译 · **DAS / ARR 可调** ·
发 `input:raw` 与 `input:intent` 两条事件。

**手感参数归这里**（ADR-044）——**内核只认原子命令**。

## 渲染（`render` · M4-S6）

**纯订阅者**：只消费事件与快照，**不写状态** · **不用只读视图通道** ·
**画面完全由事件推导**（FR-8.2）。

**界面与内核之间只有三个接口**：**事件** · **规则集** · **棋盘尺寸**——
**换一个渲染器不需要改内核**。
"""

from .input import (
    DAS_ACTIONS,
    DEFAULT_ARR_MS,
    DEFAULT_DAS_MS,
    INPUT_INTENT_EVENT,
    INPUT_RAW_EVENT,
    KEY_BINDINGS,
    Binding,
    KeyboardInput,
    KeyEvent,
    KeyReader,
    create_key_reader,
)
from .render import (
    CELL_ACTIVE,
    CELL_BLOCK,
    CELL_EMPTY,
    CELL_SPECIAL,
    TerminalRenderer,
)
from .storage import TEMP_SUFFIX, AppendSink, FileSink

__all__ = [
    "DAS_ACTIONS",
    "DEFAULT_ARR_MS",
    "DEFAULT_DAS_MS",
    "INPUT_INTENT_EVENT",
    "INPUT_RAW_EVENT",
    "KEY_BINDINGS",
    "TEMP_SUFFIX",
    "AppendSink",
    "Binding",
    "CELL_ACTIVE",
    "CELL_BLOCK",
    "CELL_EMPTY",
    "CELL_SPECIAL",
    "FileSink",
    "KeyEvent",
    "KeyReader",
    "KeyboardInput",
    "TerminalRenderer",
    "create_key_reader",
]
