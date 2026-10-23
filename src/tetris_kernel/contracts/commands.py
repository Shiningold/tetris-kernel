"""命令契约声明（契约 v1 · 16 条命令 + 12 值拒绝原因枚举）。

公共叶子——**不 import 任何 `tetris_kernel.*` 模块，也不依赖第三方**；只用标准库
（架构 §2.3 / §4.1）。本模块只做「声明」，不做「行为」。

## 声明了什么

- `COMMANDS`：命令名 → 命令规格（参数声明 + 可拒绝原因）。**封闭且可枚举**
  （FR-2.1）——这张表就是 v1 的全集，不留「以后可能加」的占位。
- `REJECT_REASONS`：12 个拒绝原因，**只增不改**（需求 §8.2）。
- `REJECT_REASON_CATEGORIES`：拒绝原因的分类（需求 §8.2 的「类」列）。

命令名就是 `command:received` / `command:accepted` / `command:rejected` 三个事件
负载里 `commandType` 的取值——两处必须是同一批名字。

## args 的三条约束

1. **禁浮点**（B-7 / ADR-025）：`args` 参与重放，JSON 里 `1.0` 与 `1` 不可区分。
   **这条只约束 `args`**——事件负载不受约束：`extensions` 与 `metric:sampled`
   允许浮点，口径不同是对的（`extensions` 不参与重放判定）。
2. **只含 JSON 标量与元组**：容器类型是 `COMMAND_ARGS_TYPE`（= `ARGS`），取值域
   是 `ARGS_VALUE`；每个参数的类型必须是这个取值域的成员。
3. **反序列化按命令类型还原**：每个命令的参数声明（参数名 + 类型）**就是**它的
   还原目标形状——`args` 进了录像，回放时按这条声明把元组还原成命令自己的参数。

## 拒绝原因怎么分配

需求 §8.2 的「可被拒绝的情形」列是按命令列的，但有三条**跨切规则**，
机器可读声明必须把它们显式化，否则实现期各写各的：

- **统一生命周期门禁**（需求 §8.2 的门禁注）：除 `start` / `pause` / `resume` /
  `reset` 外，所有**改变棋盘内容或活动方块位置**的命令，在 `phase != running`
  时一律被拒，原因取 `not_started` / `paused` / `game_over`。
- **配额是跨切的**（架构 §6.4）：命令数 / 轮次数配额超限时，被丢弃的**任何**
  命令都以 `quota_exceeded` 被拒——所以 16 条命令都带这个原因。`writeCells`
  的单帧写入格子数上限复用同一个原因值。
- **参数不合规格统一用 `invalid_argument`**：类型不对、参数多余都算，16 条命令
  都可能因它被拒。

两个例外要记牢：

- **`setLevel` 不受生命周期门禁约束**：它不改棋盘内容也不改活动方块位置，
  需求 §8.2 给它的可拒绝情形只有「越界」。
- **`start` / `pause` / `resume` / `reset` 是生命周期命令本身**，它们的状态问题
  走 `illegal_transition`（状态类），不是门禁三值。

## 旋转失败不在拒绝原因里

旋转无合法落点时命令**仍被接受**，另发受阻事件（ADR-043）——拒绝通道只留给
「当前状态根本不该发这个命令」。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .types import ARGS, CELLS, INT_TUPLE

# ---------------------------------------------------------------------------
# 声明结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandArg:
    """命令的一个参数声明。

    字段：参数名 · 类型表达式 · 是否可空 · 中文说明。**参数名即契约**，
    改名 = major 变更。

    `nullable` 在契约 v1 的 16 条命令上**全是 False**——`injectGarbage` 的
    `holes` 用空元组表示「无空洞」，不是 `None`。字段留着是因为它属于契约
    面的固有维度：将来新增参数时「能不能不传」必须显式写出来，不能靠约定。
    """

    name: str
    type: str
    nullable: bool
    doc: str


@dataclass(frozen=True)
class CommandSpec:
    """一条命令的声明。

    字段：命令名 · 参数声明 · 可拒绝原因 · 中文说明。

    `reject_reasons` 是**该命令可能被拒绝的全部原因**，不只是需求 §8.2
    那一列写着的——跨切的配额与参数原因也算在内（见模块 docstring）。
    """

    name: str
    args: tuple[CommandArg, ...]
    reject_reasons: tuple[str, ...]
    doc: str


# ---------------------------------------------------------------------------
# 拒绝原因枚举（契约清单第 6 项）
# ---------------------------------------------------------------------------

#: 拒绝原因枚举（v1 定死，**只增不改**，需求 §8.2）。顺序 = 需求分类表的顺序。
REJECT_REASONS: tuple[str, ...] = (
    # 生命周期门禁
    "not_started",
    "paused",
    "game_over",
    # 配额
    "quota_exceeded",
    # 参数
    "invalid_argument",
    "level_out_of_range",
    "out_of_bounds",
    # 状态
    "illegal_transition",
    "no_active_piece",
    "hold_already_used",
    # 受控写入
    "not_authorized",
    "write_condition_failed",
)

#: 拒绝原因的分类（需求 §8.2 的「类」列）。键 = 分类代号，值 = 该类的取值。
REJECT_REASON_CATEGORIES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "lifecycle": ("not_started", "paused", "game_over"),
        "quota": ("quota_exceeded",),
        "parameter": ("invalid_argument", "level_out_of_range", "out_of_bounds"),
        "state": ("illegal_transition", "no_active_piece", "hold_already_used"),
        "controlled_write": ("not_authorized", "write_condition_failed"),
    }
)

#: 分类代号 → 中文名（需求 §8.2 的「类」列）。
REJECT_REASON_CATEGORY_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "lifecycle": "生命周期门禁",
        "quota": "配额",
        "parameter": "参数",
        "state": "状态",
        "controlled_write": "受控写入",
    }
)

#: 生命周期门禁的三值（需求 §8.2 的门禁注）。**受门禁约束的命令必须三个全带**，
#: 不受门禁约束的一个都不能带——这条不变式由测试守着。
LIFECYCLE_REJECT_REASONS: tuple[str, ...] = ("not_started", "paused", "game_over")

_REASON_INDEX: Mapping[str, int] = MappingProxyType(
    {name: index for index, name in enumerate(REJECT_REASONS)}
)


def _reasons(*names: str) -> tuple[str, ...]:
    """归一化一条命令的拒绝原因：去重 + 按枚举顺序排列。

    写死的名字在这里就会炸（`ValueError`），拼错的原因不会静默进声明。
    排序而不是原样保留，是为了让**顺序不携带语义**——顺序一变就是一次
    基线哈希变化，而它本不该是。
    """
    unknown = [name for name in names if name not in _REASON_INDEX]
    if unknown:
        raise ValueError(f"未知拒绝原因：{unknown}")
    return tuple(sorted(set(names), key=_REASON_INDEX.__getitem__))


#: 命令 `args` 的容器类型（引用 `types.py` 的常量，不复制字面量——架构 §4.4 末条）。
#: 16 条命令的 `args` 都是这个形状；`command:*` 三个事件的 `args` 负载同源。
COMMAND_ARGS_TYPE = ARGS


def _cmd(
    name: str,
    doc: str,
    args: tuple[CommandArg, ...] = (),
    reject_reasons: tuple[str, ...] = (),
) -> CommandSpec:
    """构造命令声明（内部用，省掉重复写 CommandSpec(...)）。"""
    return CommandSpec(
        name=name,
        args=args,
        reject_reasons=_reasons(*reject_reasons),
        doc=doc,
    )


# ---------------------------------------------------------------------------
# 命令目录（16 条，需求 §8.2）
# ---------------------------------------------------------------------------

_COMMAND_SPECS: tuple[tuple[str, CommandSpec], ...] = (
    # ---------------- 生命周期（4 条） ----------------
    (
        "start",
        _cmd(
            "start",
            "开始一局新游戏。已开局时拒绝（状态不合法）。",
            reject_reasons=("invalid_argument", "illegal_transition"),
        ),
    ),
    (
        "pause",
        _cmd(
            "pause",
            "暂停内核（游戏内暂停）。内核级暂停期间帧号照走，但推进许可被门禁收回。",
            reject_reasons=("invalid_argument", "illegal_transition"),
        ),
    ),
    (
        "resume",
        _cmd(
            "resume",
            "恢复运行。`resume` 生效的那一帧不推进，这是设计不是缺陷（架构 §6.7）。",
            reject_reasons=("invalid_argument", "illegal_transition"),
        ),
    ),
    (
        "reset",
        _cmd(
            "reset",
            "重置：结束当前局并开新局，帧号 / 帧内序号 / 版本号一并归零（架构 §6.1）。",
            reject_reasons=("invalid_argument", "illegal_transition"),
        ),
    ),
    # ---------------- 移动与下落（4 条） ----------------
    (
        "moveLeft",
        _cmd(
            "moveLeft",
            "活动方块左移一格。手感参数（DAS / ARR）归输入适配器，内核只认原子命令。",
            reject_reasons=(
                "not_started",
                "paused",
                "game_over",
                "quota_exceeded",
                "invalid_argument",
                "no_active_piece",
            ),
        ),
    ),
    (
        "moveRight",
        _cmd(
            "moveRight",
            "活动方块右移一格。",
            reject_reasons=(
                "not_started",
                "paused",
                "game_over",
                "quota_exceeded",
                "invalid_argument",
                "no_active_piece",
            ),
        ),
    ),
    (
        "softDrop",
        _cmd(
            "softDrop",
            "软降一格（给分不落锁）。",
            reject_reasons=(
                "not_started",
                "paused",
                "game_over",
                "quota_exceeded",
                "invalid_argument",
                "no_active_piece",
            ),
        ),
    ),
    (
        "hardDrop",
        _cmd(
            "hardDrop",
            "硬降：直落到底并锁定。",
            reject_reasons=(
                "not_started",
                "paused",
                "game_over",
                "quota_exceeded",
                "invalid_argument",
                "no_active_piece",
            ),
        ),
    ),
    # ---------------- 旋转（3 条） ----------------
    (
        "rotateCW",
        _cmd(
            "rotateCW",
            "顺时针旋转。**旋转无合法落点时命令仍被接受**，另发受阻事件（ADR-043）。",
            reject_reasons=(
                "not_started",
                "paused",
                "game_over",
                "quota_exceeded",
                "invalid_argument",
                "no_active_piece",
            ),
        ),
    ),
    (
        "rotateCCW",
        _cmd(
            "rotateCCW",
            "逆时针旋转。同样：旋转无合法落点不拒绝，另发受阻事件。",
            reject_reasons=(
                "not_started",
                "paused",
                "game_over",
                "quota_exceeded",
                "invalid_argument",
                "no_active_piece",
            ),
        ),
    ),
    (
        "rotate180",
        _cmd(
            "rotate180",
            "旋转 180 度。踢墙表必须覆盖 180°（架构 §7.3.1）。",
            reject_reasons=(
                "not_started",
                "paused",
                "game_over",
                "quota_exceeded",
                "invalid_argument",
                "no_active_piece",
            ),
        ),
    ),
    # ---------------- 暂存与推进（2 条） ----------------
    (
        "hold",
        _cmd(
            "hold",
            "暂存当前方块并取出暂存槽里的方块。**本块已用过暂存时拒绝**。"
            "需求 §8.2 给 hold 列的可拒绝情形只有这一条，因此这里不额外列 "
            "`no_active_piece`——若实现期发现 running 态确有「无活动方块」的窗口，"
            "再按 major 变更流程补。",
            reject_reasons=(
                "not_started",
                "paused",
                "game_over",
                "quota_exceeded",
                "invalid_argument",
                "hold_already_used",
            ),
        ),
    ),
    (
        "tick",
        _cmd(
            "tick",
            "推进一帧。**帧号 +1 与推进许可是两件事**：帧号在 tick 出队时就 +1，"
            "推进许可（`clock:tick` + `advance_frame()`）在 tick 被接受时才授予"
            "（架构 §6.1）。tick 自身不计入命令数配额，但占帧内序号 0。",
            reject_reasons=(
                "not_started",
                "paused",
                "game_over",
                "quota_exceeded",
                "invalid_argument",
            ),
        ),
    ),
    # ---------------- 参数类（1 条） ----------------
    (
        "setLevel",
        _cmd(
            "setLevel",
            "设置当前等级。越界时拒绝。**不受生命周期门禁约束**——它不改棋盘内容"
            "也不改活动方块位置，需求 §8.2 给它的可拒绝情形只有「越界」。",
            args=(CommandArg("level", "int", False, "目标等级；取值域由规则集定义，越界即拒绝"),),
            reject_reasons=("quota_exceeded", "invalid_argument", "level_out_of_range"),
        ),
    ),
    # ---------------- 外部写入（2 条） ----------------
    (
        "injectGarbage",
        _cmd(
            "injectGarbage",
            "注入垃圾行。改棋盘内容 → **受生命周期门禁约束**。",
            args=(
                CommandArg("rows", "int", False, "注入的垃圾行数（行数，不是行号）"),
                CommandArg("holes", INT_TUPLE, False, "空洞所在列；空元组 = 每行不挖空洞"),
            ),
            reject_reasons=(
                "not_started",
                "paused",
                "game_over",
                "quota_exceeded",
                "invalid_argument",
            ),
        ),
    ),
    (
        "writeCells",
        _cmd(
            "writeCells",
            "受控写入：请求变更指定格子。**外部改变棋盘的唯一合法通道**"
            "（B-5 / FR-11.1）。改棋盘内容 → **受生命周期门禁约束**；同帧内的多个"
            "写入请求按到达顺序串行执行，命中同一格默认后者覆盖；单帧写入格子总数"
            "有上限，超限整批拒绝（原因同样是 `quota_exceeded`）。",
            args=(
                CommandArg(
                    "cells",
                    CELLS,
                    False,
                    "待变更格子清单 (x, y, 新值)；内核校验后落笔并照常发出 board:changed",
                ),
            ),
            reject_reasons=(
                "not_started",
                "paused",
                "game_over",
                "quota_exceeded",
                "invalid_argument",
                "out_of_bounds",
                "not_authorized",
                "write_condition_failed",
            ),
        ),
    ),
)

#: 命令注册表：键 = 命令类型名，值 = 该命令的声明。**这是 v1 的全集**（FR-2.1）。
COMMANDS: Mapping[str, CommandSpec] = MappingProxyType(dict(_COMMAND_SPECS))

#: 命令类型名的稳定序列（枚举顺序 = 需求 §8.2 的表格顺序）。
COMMAND_TYPES: tuple[str, ...] = tuple(name for name, _ in _COMMAND_SPECS)


def is_lifecycle_gated(command_type: str) -> bool:
    """该命令是否受**统一生命周期门禁**约束。

    这是**推导值**，不是另写一份名单：门禁三值齐全 ⇔ 受门禁约束。
    推导的好处是两份名单不可能分叉——另写一份的话，加命令的人漏改名单，
    门禁就会静默失效（而门禁失效的代价是「暂停期间命令照样执行」）。
    """
    return set(LIFECYCLE_REJECT_REASONS) <= set(COMMANDS[command_type].reject_reasons)
