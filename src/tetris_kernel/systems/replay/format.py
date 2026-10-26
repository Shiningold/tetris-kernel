"""录像格式（系统层 · 回放系统 · M2-S8 · FR-6.1 · ADR-045 · ADR-064）。

## 格式：JSON Lines（**第一行是头，之后每行一条命令**）

```
{"rule":"standard","seed":20261026,"version":"v1"}
{"args":[],"frame":3,"source":"host","type":"moveLeft"}
{"args":[],"frame":3,"source":"host","type":"hardDrop"}
```

**头三件套**：版本 · 种子 · 规则集标识（spec §7.1）。**体**：按帧记录的
命令序列——每条记**命令类型 · 参数 · 帧号 · 来源**。

**为什么是 JSON Lines 而不是一个大 JSON 文档**：两者都是"JSON 可读可 diff"
（ADR-045），但**一行一条**让录像的 diff 逐行可比（一个大 JSON 文档改一条
命令会让整行变），体积也不带缩进。

**为什么录命令而不是录事件**（ADR-045）：事件粒度会随版本调整而失效；命令
序列 + 种子就是**完整的输入**（spec §7.1）——重放不需要存任何状态。

## 版本号与兼容策略（FR-10.3 · NFR-9）

**不认识的头 → 明确报错**——不猜、不静默降级、不按旧版解析。理由很直白：
**猜错一次就是静默分叉**，而分叉比报错难查得多。

## 文件名（**不带墙钟 · 不带种子** · ADR-064）

**文件名是录像格式的一部分**（录像叫什么），**目录策略是宿主的**
（ADR-074：路径策略住在 `host/`，`systems/` 不该决定路径）。

**为什么不带墙钟**：录像头里已经有种子 + 规则集标识 + 版本——带时间戳会让
"同一局录两次"产生两份文件，AC-1 的 200 局比对分不清哪份是哪份。

**为什么不带种子**：契约里 `replay:recording-started` 的 `seed` 字段明写
"录像头里记它（**文件名不带它**）"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ...contracts.commands import COMMANDS
from ...contracts.types import CELLS, INT_TUPLE
from ...kernel.commands import Args, ArgsValue

#: 录像格式版本（**不认识的头 → 明确报错** · FR-10.3 · NFR-9）。
RECORDING_VERSION = "v1"

#: 录像文件名的**前缀**。
RECORDING_FILENAME_PREFIX = "recording"

#: 录像文件的**扩展名**（JSON Lines）。
RECORDING_SUFFIX = ".jsonl"


@dataclass(frozen=True)
class RecordedCommand:
    """录像里的一条命令（**四字段**：帧号 · 类型 · 参数 · 来源）。

    - `frame`：该命令所在帧号（**用于中途开始与逐帧步进**，不参与确定性）；
    - `type`：命令类型；
    - `args`：命令参数（**禁浮点**——参数参与重放，不能漂）；
    - `source`：**原始来源**（ADR-062：回放时**透传原值**，不改写成
      `replay`——改写会让事件流回归基线永久失配）。
    """

    frame: int
    type: str
    args: Args
    source: str


@dataclass(frozen=True)
class Recording:
    """一份录像（**头三件套 + 命令序列**）。

    - `seed`：随机源种子；
    - `rule`：规则集标识；
    - `commands`：**按录到顺序**排的命令序列。
    """

    seed: int
    rule: str
    commands: tuple[RecordedCommand, ...] = ()


# ---------------------------------------------------------------------------
# 文件名
# ---------------------------------------------------------------------------


def recording_filename(rule: str) -> str:
    """录像的**文件名**（**不带墙钟 · 不带种子** · ADR-064）。

    **纯函数**：同一个规则集 → 同一个文件名（"同一局录两次产生同一份"）。

    **为什么带规则集标识**：不同规则集的录像不互相覆盖——而它们本来就不是
    同一种东西（重放要按同一个规则集起内核）。
    """
    return f"{RECORDING_FILENAME_PREFIX}-{_slug(rule)}{RECORDING_SUFFIX}"


def _slug(text: str) -> str:
    """把规则集标识压成**文件名安全**的一小段（去掉路径分隔符等）。"""
    safe = [char if (char.isalnum() or char in "-_.") else "-" for char in text]
    return "".join(safe) or "rule"


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def recording_to_jsonl(recording: Recording) -> str:
    """录像 → JSON Lines 文本（**第一行是头，之后每行一条命令**）。"""
    lines = [
        json.dumps(
            {
                "version": RECORDING_VERSION,
                "seed": recording.seed,
                "rule": recording.rule,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    ]
    for command in recording.commands:
        lines.append(
            json.dumps(
                {
                    "frame": command.frame,
                    "type": command.type,
                    "args": dict(command.args),
                    "source": command.source,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return "\n".join(lines) + "\n"


def recording_from_jsonl(text: str) -> Recording:
    """JSON Lines 文本 → 录像（**不认识的头 → 明确报错**）。

    **不猜、不静默降级**：版本对不上当场抛 `ValueError`——猜错一次就是
    **静默分叉**，而分叉比报错难查得多（FR-10.3 · NFR-9）。
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("录像是空的：没有头")
    header = _json_object(lines[0], "头")
    version = header.get("version")
    if version != RECORDING_VERSION:
        raise ValueError(f"不认识录像格式版本：{version!r}（本版只认 {RECORDING_VERSION!r}）")
    seed = header.get("seed")
    rule = header.get("rule")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError(f"录像头的 seed 不是整数：{seed!r}")
    if not isinstance(rule, str):
        raise ValueError(f"录像头的 rule 不是字符串：{rule!r}")
    commands = tuple(_command_from_json(line) for line in lines[1:])
    return Recording(seed=seed, rule=rule, commands=commands)


def _json_object(text: str, what: str) -> dict[str, object]:
    """解析一个 JSON 对象（**不是对象就报错**，不糊弄）。"""
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"录像的{what}不是合法 JSON：{error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"录像的{what}不是 JSON 对象：{value!r}")
    return value


def _command_from_json(text: str) -> RecordedCommand:
    """一行 JSON → 一条录下来的命令。"""
    raw = _json_object(text, "命令")
    command_type = raw.get("type")
    if not isinstance(command_type, str):
        raise ValueError(f"录像里的命令没有类型：{raw!r}")
    if command_type not in COMMANDS:
        raise ValueError(f"录像里有未登记的命令类型：{command_type!r}")
    frame = raw.get("frame")
    if not isinstance(frame, int) or isinstance(frame, bool):
        raise ValueError(f"录像里的命令 {command_type!r} 没有帧号：{raw!r}")
    source = raw.get("source")
    if not isinstance(source, str):
        raise ValueError(f"录像里的命令 {command_type!r} 没有来源：{raw!r}")
    args_raw = raw.get("args")
    if not isinstance(args_raw, dict):
        raise ValueError(f"录像里的命令 {command_type!r} 的参数不是对象：{raw!r}")
    return RecordedCommand(
        frame=frame,
        type=command_type,
        args=_restore_args(command_type, args_raw),
        source=source,
    )


def _restore_args(command_type: str, raw: dict[str, object]) -> Args:
    """按**契约的参数声明**把 JSON 值还原成命令参数形状。

    **为什么按声明还原**：JSON 里元组与列表**不可区分**——"空洞所在列"是
    `tuple[int, ...]` 还是 `list[int]`，只有契约的声明说了算（需求 §8.2 的
    既有纪律："反序列化按命令类型还原"）。
    """
    spec = COMMANDS.get(command_type)
    if spec is None:
        raise ValueError(f"录像里有未登记的命令类型：{command_type!r}")
    restored: list[tuple[str, ArgsValue]] = []
    for arg in spec.args:
        if arg.name not in raw:
            raise ValueError(f"录像里的 {command_type} 少了参数 {arg.name!r}")
        restored.append((arg.name, _restore_value(raw[arg.name], arg.type)))
    return tuple(restored)


def _restore_value(value: object, type_expr: str) -> ArgsValue:
    """按契约的类型串还原一个参数值（**类型不合当场报错**）。"""
    if type_expr == "int":
        return _as_int(value)
    if type_expr == "str":
        return _as_str(value)
    if type_expr == "bool":
        return _as_bool(value)
    if type_expr == INT_TUPLE:
        return _as_int_tuple(value)
    if type_expr == CELLS:
        return _as_cells(value)
    raise ValueError(f"录像还原：不认识参数类型 {type_expr!r}")


def _as_int(value: object) -> int:
    """收成整数（**不是整数就当场报错**）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"录像里的参数不是整数：{value!r}")
    return value


def _as_str(value: object) -> str:
    """收成字符串（**不是字符串就当场报错**）。"""
    if not isinstance(value, str):
        raise ValueError(f"录像里的参数不是字符串：{value!r}")
    return value


def _as_bool(value: object) -> bool:
    """收成布尔（**不是布尔就当场报错**）。"""
    if not isinstance(value, bool):
        raise ValueError(f"录像里的参数不是布尔：{value!r}")
    return value


def _as_int_tuple(value: object) -> tuple[int, ...]:
    """收成整数清单（JSON 里的数组 → 元组）。"""
    if not isinstance(value, list):
        raise ValueError(f"录像里的参数不是清单：{value!r}")
    return tuple(_as_int(item) for item in value)


def _as_cells(value: object) -> tuple[tuple[int, int, int], ...]:
    """收成格子清单（`(x, y, 新值)`）。"""
    if not isinstance(value, list):
        raise ValueError(f"录像里的参数不是清单：{value!r}")
    return tuple(_as_cell(item) for item in value)


def _as_cell(value: object) -> tuple[int, int, int]:
    """收成一个格子 `(x, y, 新值)`。"""
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"录像里的参数不是格子：{value!r}")
    return (_as_int(value[0]), _as_int(value[1]), _as_int(value[2]))


__all__ = [
    "RECORDING_FILENAME_PREFIX",
    "RECORDING_SUFFIX",
    "RECORDING_VERSION",
    "RecordedCommand",
    "Recording",
    "recording_filename",
    "recording_from_jsonl",
    "recording_to_jsonl",
]
