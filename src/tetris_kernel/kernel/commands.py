"""命令数据结构与参数校验（内核层 · S9 · 架构 §7.1 / §7.2）。

## 命令是什么

一条命令 = **类型 + 参数 + 来源**三样：

- `type`：命令类型名，**必须已在 `contracts/commands.py` 登记**（FR-2.1：命令集
  封闭且可枚举）；
- `args`：不可变参数清单，**只含 JSON 标量与元组、禁浮点**（B-7 / ADR-025）；
- `source`：来源标记（human / ai / replay / tutorial / network / test），
  **内核不得因它分支**（架构 §7.1）。

## 参数取值域：与契约同一条

契约侧的取值域是**类型表达式字符串**（`contracts/types.py` 的 `ARGS_VALUE`），
不能直接当注解用。所以本模块把同一条取值域落成 Python 类型别名，并在**导入期**
与契约逐字对齐——契约改了取值域而内核没跟上，当场报错。

## 参数校验在哪儿做

**在命令通道里做，不在每条命令里各写一遍**（架构 §7.2「phase guard 前置」的
同一条纪律）：`validate_args` 拿契约声明（参数名 + 类型）逐条比对，对不上就
返回拒绝原因 `invalid_argument`（类型不对、参数多余都算这一条）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ..contracts.commands import COMMANDS, CommandSpec
from ..contracts.types import ARGS_VALUE, CELLS, INT_TUPLE

# ---------------------------------------------------------------------------
# 取值域
# ---------------------------------------------------------------------------

#: 命令参数的取值域：JSON 标量与元组，**禁浮点**（B-7 / ADR-025）。
#:
#: 与 `contracts/types.py` 的 `ARGS_VALUE` 同一条——契约侧是字符串，这里是
#: Python 类型别名；`_assert_args_value_alignment` 在导入期逐字对齐两边。
type ArgsValue = int | str | bool | None | tuple[int, ...] | tuple[tuple[int, int, int], ...]

#: 命令参数的容器：参数名 → 取值，顺序固定（不可变元组）。
type Args = tuple[tuple[str, ArgsValue], ...]


def _assert_args_value_alignment() -> None:
    """内核的参数取值域必须与契约的 `ARGS_VALUE` **同一条**。

    两边都是**字符串形状**的声明，所以这里逐字比：契约改了取值域而内核没
    跟上，当场报错，不等基线刷新才暴露（架构 §4.4 末条的同一条纪律）。
    """
    rendered = " | ".join(
        [
            int.__name__,
            str.__name__,
            bool.__name__,
            str(None),
            str(tuple[int, ...]),
            str(tuple[tuple[int, int, int], ...]),
        ]
    )
    if rendered != ARGS_VALUE:
        raise TypeError(f"命令参数取值域与契约不一致：{rendered!r} ≠ {ARGS_VALUE!r}")


_assert_args_value_alignment()

# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """一条待执行的命令（**不可变**）。

    字段：

    - `type`：命令类型名，**必须已在 `contracts/commands.py` 登记**；
    - `args`：参数清单，参数名与顺序**照抄契约声明**；无参命令用空元组；
    - `source`：来源标记，**只随事件透传，内核不得因它分支**（架构 §7.1）。
    """

    type: str
    args: Args = ()
    source: str = "human"


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------

#: 契约里的 `int` 类型表达式（`contracts/commands.py` 的 `CommandArg.type`）。
_INT = "int"

#: 契约里的 `str` 类型表达式（`contracts/commands.py` 的 `CommandArg.type`）。
#:
#: **M3 增补**：`switchRuleSet` 的参数是规则集标识（字符串）——契约面在 S1
#: 就登记了它，但内核的参数校验原本只认 `int` / `INT_TUPLE` / `CELLS`，
#: 于是那条命令的每一个参数都会被判 `invalid_argument`（**命令在契约里，
#: 却永远发不出去**）。这是 **M3 唯一一次冻结区破例**的一半（另一半是
#: `kernel/core.py` 的换规则集入口）。
_STR = "str"


def _is_int(value: object) -> bool:
    """是不是整数。**布尔不算整数**——`True` 进了录像会变成 `1`，回放就漂了。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str(value: object) -> bool:
    """是不是字符串。"""
    return isinstance(value, str)


def _is_int_tuple(value: object) -> bool:
    """是不是整数元组（`INT_TUPLE`）。"""
    if not isinstance(value, tuple):
        return False
    items = cast("tuple[object, ...]", value)
    return all(_is_int(item) for item in items)


def _is_cells(value: object) -> bool:
    """是不是格子清单（`CELLS`：`(x, y, 新值)` 的元组）。"""
    if not isinstance(value, tuple):
        return False
    items = cast("tuple[object, ...]", value)
    return all(
        isinstance(cell, tuple)
        and len(cast("tuple[object, ...]", cell)) == 3
        and all(_is_int(item) for item in cast("tuple[object, ...]", cell))
        for cell in items
    )


def _matches_type(value: object, type_expr: str) -> bool:
    """一个参数值是否符合契约声明的类型表达式。

    **只认契约用到的四种类型表达式**（`int` / `str` / `INT_TUPLE` / `CELLS`）：
    契约新增类型表达式时这里会落到"不匹配"——参数被拒，而不是悄悄按错的
    类型校验。
    """
    if type_expr == _INT:
        return _is_int(value)
    if type_expr == _STR:
        return _is_str(value)
    if type_expr == INT_TUPLE:
        return _is_int_tuple(value)
    if type_expr == CELLS:
        return _is_cells(value)
    return False


def validate_args(spec: CommandSpec, args: Args) -> str | None:
    """按契约声明校验命令参数。

    返回**拒绝原因**（`None` = 参数合法）：

    - 参数名 / 顺序 / 个数与契约声明不一致 → `invalid_argument`；
    - 参数值的类型与契约声明不一致 → `invalid_argument`。

    **参数多余也算 `invalid_argument`**（需求 §8.2：类型不对、参数多余都算）。
    """
    expected_names = tuple(arg.name for arg in spec.args)
    actual_names = tuple(name for name, _ in args)
    if actual_names != expected_names:
        return "invalid_argument"
    for (_name, value), declaration in zip(args, spec.args, strict=True):
        if not _matches_type(value, declaration.type):
            return "invalid_argument"
    return None


def is_known_command(command_type: str) -> bool:
    """命令类型是否已在契约中登记。"""
    return command_type in COMMANDS


__all__ = [
    "Args",
    "ArgsValue",
    "Command",
    "is_known_command",
    "validate_args",
]
