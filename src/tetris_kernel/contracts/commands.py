"""命令契约声明（契约 v1 · 17 条命令 + 13 值拒绝原因枚举）。

公共叶子——**不 import 任何 `tetris_kernel.*` 模块，也不依赖第三方**；只用标准库
（架构 §2.3 / §4.1）。本模块只做「声明」，不做「行为」。

## 声明了什么

- `COMMANDS`：命令名 → 命令规格（参数声明 + 是否受门禁 + 可拒绝原因）。
  **封闭且可枚举**（FR-2.1）——这张表就是 v1 的全集，不留「以后可能加」的占位。
- `REJECT_REASONS`：13 个拒绝原因，**只增不改**（需求 §8.2）。
- `REJECT_REASON_CATEGORIES`：拒绝原因的分类（需求 §8.2 的「类」列）。
- `COMMAND_TYPE_FIELDS`：事件侧承载**命令名**的负载字段——命令集在事件侧的落点。

命令名就是 `command:received` / `command:accepted` / `command:rejected` /
`input:intent` 四个事件负载里 `commandType` 的取值——两处必须是同一批名字，
所以那张字段表由本模块声明（见 `COMMAND_TYPE_FIELDS`）。

## args 的三条约束

1. **禁浮点**（B-7 / ADR-025）：`args` 参与重放，JSON 里 `1.0` 与 `1` 不可区分。
   **这条只约束 `args`**——事件负载不受约束：`extensions` 与 `metric:sampled`
   允许浮点，口径不同是对的（`extensions` 不参与重放判定）。
2. **只含 JSON 标量与元组**：容器类型是 `types.ARGS`，取值域是 `types.ARGS_VALUE`；
   每个参数的类型必须是这个取值域的成员。
3. **反序列化按命令类型还原**：每个命令的参数声明（参数名 + 类型）**就是**它的
   还原目标形状——`args` 进了录像，回放时按这条声明把元组还原成命令自己的参数。

## 拒绝原因怎么分配

需求 §8.2 的「可被拒绝的情形」列是按命令列的，但有三条**跨切规则**，
机器可读声明必须把它们显式化，否则实现期各写各的：

- **统一生命周期门禁**（需求 §8.2 的门禁注）：除 `start` / `pause` / `resume` /
  `reset` 外，所有**改变棋盘内容或活动方块位置**的命令，在 `phase != running`
  时一律被拒，原因取 `not_started` / `paused` / `game_over`。
  **门禁归属由 `CommandSpec.lifecycle_gated` 这一个必填字段决定**，三个门禁
  原因由它派生——手写它们会被 `_cmd` 拒绝。
- **配额是跨切的**（架构 §6.4）：命令数 / 轮次数配额超限时，被丢弃的**任何**
  命令都以 `quota_exceeded` 被拒——所以 17 条命令都带这个原因。`writeCells`
  的单帧写入格子数上限复用同一个原因值。
  > **M3-C2 的订正**：这句话**一直是对的（内核的行为确实如此）**，但**声明
  > 里有 5 条命令漏了它**（`start` / `pause` / `resume` / `reset` —— **M0
  > 时代就漏了**，因为当时没人查这条跨切规则；`switchRuleSet` 是 M3 新增，
  > 沿用了同一处遗漏）。M3 审查报告 M1 把它翻出来：**内核对它们全都发了
  > `quota_exceeded`，而契约没说**——消费方按 `reject_reasons` 枚举拒绝面会
  > **漏掉这一种**。**M3-C2 补齐这 5 条声明**，让契约与内核的实际行为对齐。
  > **教训**：跨切规则写在 docstring 里**不等于**它落进了每一条声明——
  > **「所有命令都带」这句话当时没有任何一处机器守着**。
- **参数不合规格统一用 `invalid_argument`**：类型不对、参数多余都算，17 条命令
  都可能因它被拒。

两个例外要记牢：

- **`setLevel` 不受生命周期门禁约束**：它不改棋盘内容也不改活动方块位置，
  需求 §8.2 给它的可拒绝情形只有「越界」。
- **`switchRuleSet` 同样不受生命周期门禁约束**，但它**有自己的门禁**：局中
  （`running`）切换由**内核**判 `illegal_transition`。它换的是规则集，**不是**
  棋盘内容或活动方块位置——通用门禁「phase ≠ running 一律拒」对它恰好是反的
  （换规则集要在待开始 / 已结束态被接受）。**门禁归属仍由契约的
  `lifecycle_gated` 字段决定**，不手写名单（架构 §7.1）。
- **`start` / `pause` / `resume` / `reset` 是生命周期命令本身**，它们的状态问题
  走 `illegal_transition`（状态类），不是门禁三值。

## `unknown_rule_set` 归在**状态**类

M3 新增的拒绝原因（12 → 13）：请求的规则集**未在宿主侧登记**。

**它不是「参数」类**：参数不合规格统一是 `invalid_argument`，而这一条的值
（规则集标识）**类型完全正确**——错的是「这个名字没有对应的规则集」，也就是
**当前状态里没有这个东西**，归状态类。

**明确拒绝，不拿标准规则集顶替**：M2 的回放宿主已是这个口径（录像头写着没登记
的规则集 → 拒绝），M3 沿用——顶替会让「四项不等」这个报错失去指向性。

## `no_active_piece` 是**防御性保留**

六条移动 / 旋转命令（`moveLeft` / `moveRight` / `softDrop` / `hardDrop` /
`rotateCW` / `rotateCCW` / `rotate180`）声明了 `no_active_piece`，**但正常执行
路径走不到它**——内核保证「命令执行时 `running` 态必然持有活动方块」
（架构 §6.8 的活动方块不变量）。它是**防御性保留**：契约把这一类情形也兜住，
免得消费方遇到没声明的原因时踩空。

**`hold` 没有声明它**，而且这不是疏漏——见 `hold` 那条的说明。

## 旋转失败不在拒绝原因里

旋转无合法落点时命令**仍被接受**，另发受阻事件（ADR-043）——拒绝通道只留给
「当前状态根本不该发这个命令」。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .types import (
    CELLS,
    INT_TUPLE,
    PHASE_GAME_OVER,
    PHASE_NOT_STARTED,
    PHASE_PAUSED,
    literal_enum,
)

# ---------------------------------------------------------------------------
# 声明结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandArg:
    """命令的一个参数声明。

    字段：参数名 · 类型表达式 · 是否可空 · 中文说明。**参数名即契约**，
    改名 = major 变更。

    `nullable` 在契约 v1 的 17 条命令上**全是 False**——`injectGarbage` 的
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

    字段：命令名 · 参数声明 · 是否受生命周期门禁 · 可拒绝原因 · 中文说明。

    `lifecycle_gated` 是**必填的显式表态**：加命令时必须对门禁说一句话，
    漏表态直接 `TypeError`，不会安静地当成"不受门禁"。

    它同时是门禁三值的**唯一来源**——`reject_reasons` 里的
    `not_started` / `paused` / `game_over` 由它派生（见 `_cmd`）。
    反过来做（手写三值、再推导标志）不行：推导的输入也是人手写的字面量，
    漏写三值时标志会安静地返回 False，门禁随之失效——而门禁失效的代价是
    "暂停期间命令照样执行"。

    `reject_reasons` 是**该命令可能被拒绝的全部原因**，不只是需求 §8.2
    那一列写着的——跨切的配额与参数原因也算在内（见模块 docstring）。

    **实现发出的原因必须落在这张表里**（架构 §6.8）：表外的值即便在 13 值
    枚举之内也是越界——消费方按"命令 × 原因"配对，没声明的配对不到。

    **`no_active_piece` 是防御性保留**：正常执行路径走不到它（架构 §6.8 的
    活动方块不变量），它只是把这一类情形也兜住。
    """

    name: str
    args: tuple[CommandArg, ...]
    lifecycle_gated: bool
    reject_reasons: tuple[str, ...]
    doc: str


# ---------------------------------------------------------------------------
# 拒绝原因枚举（契约清单第 6 项）
# ---------------------------------------------------------------------------

#: 拒绝原因枚举（v1 定死，**只增不改**，需求 §8.2）。顺序 = 需求分类表的顺序。
#:
#: **生命周期那三个值由 `types.PHASE_*` 派生**——门禁原因**就是三个状态名本身**
#: （架构 §7.3.1「门禁三值从状态派生」），抄第二份字面量表，两处分叉时无人报警。
REJECT_REASONS: tuple[str, ...] = (
    # 生命周期门禁
    PHASE_NOT_STARTED,
    PHASE_PAUSED,
    PHASE_GAME_OVER,
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
    "unknown_rule_set",
    # 受控写入
    "not_authorized",
    "write_condition_failed",
)

#: 拒绝原因的分类（需求 §8.2 的「类」列）。键 = 分类代号，值 = 该类的取值。
REJECT_REASON_CATEGORIES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "lifecycle": (PHASE_NOT_STARTED, PHASE_PAUSED, PHASE_GAME_OVER),
        "quota": ("quota_exceeded",),
        "parameter": ("invalid_argument", "level_out_of_range", "out_of_bounds"),
        "state": (
            "illegal_transition",
            "no_active_piece",
            "hold_already_used",
            "unknown_rule_set",
        ),
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
#:
#: **由 `types.PHASE_*` 派生**：门禁三值就是三个状态名（架构 §7.3.1），
#: 不另抄一份字面量表。
LIFECYCLE_REJECT_REASONS: tuple[str, ...] = (PHASE_NOT_STARTED, PHASE_PAUSED, PHASE_GAME_OVER)

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


def _cmd(
    name: str,
    doc: str,
    *,
    lifecycle_gated: bool,
    args: tuple[CommandArg, ...] = (),
    reject_reasons: tuple[str, ...] = (),
) -> CommandSpec:
    """构造命令声明（内部用，省掉重复写 CommandSpec(...)）。

    **门禁三值由 `lifecycle_gated` 派生**，手写它们会被拒——门禁的归属只有
    这一个来源，派生出来的原因不可能与标志分叉。`lifecycle_gated` 没有
    默认值：加命令时必须显式表态，漏了直接 `TypeError`。
    """
    stray = [reason for reason in reject_reasons if reason in LIFECYCLE_REJECT_REASONS]
    if stray:
        raise ValueError(
            f"{name}：{stray} 由 lifecycle_gated 派生，不要手写"
            "（受门禁约束就传 lifecycle_gated=True）"
        )
    reasons = tuple(reject_reasons)
    if lifecycle_gated:
        reasons += LIFECYCLE_REJECT_REASONS
    return CommandSpec(
        name=name,
        args=args,
        lifecycle_gated=lifecycle_gated,
        reject_reasons=_reasons(*reasons),
        doc=doc,
    )


# ---------------------------------------------------------------------------
# 命令目录（17 条，需求 §8.2）
# ---------------------------------------------------------------------------

_COMMAND_SPECS: tuple[tuple[str, CommandSpec], ...] = (
    # ---------------- 生命周期（4 条） ----------------
    (
        "start",
        _cmd(
            "start",
            "开始一局新游戏。已开局时拒绝（状态不合法）。",
            lifecycle_gated=False,
            reject_reasons=("quota_exceeded", "invalid_argument", "illegal_transition"),
        ),
    ),
    (
        "pause",
        _cmd(
            "pause",
            "暂停内核（游戏内暂停）。内核级暂停期间帧号照走，但推进许可被门禁收回。",
            lifecycle_gated=False,
            reject_reasons=("quota_exceeded", "invalid_argument", "illegal_transition"),
        ),
    ),
    (
        "resume",
        _cmd(
            "resume",
            "恢复运行。`resume` 生效的那一帧不推进，这是设计不是缺陷（架构 §6.7）。",
            lifecycle_gated=False,
            reject_reasons=("quota_exceeded", "invalid_argument", "illegal_transition"),
        ),
    ),
    (
        "reset",
        _cmd(
            "reset",
            "重置：结束当前局并开新局，帧号 / 帧内序号 / 版本号一并归零（架构 §6.1）。",
            lifecycle_gated=False,
            reject_reasons=("quota_exceeded", "invalid_argument", "illegal_transition"),
        ),
    ),
    # ---------------- 移动与下落（4 条） ----------------
    (
        "moveLeft",
        _cmd(
            "moveLeft",
            "活动方块左移一格。手感参数（DAS / ARR）归输入适配器，内核只认原子命令。",
            lifecycle_gated=True,
            reject_reasons=("quota_exceeded", "invalid_argument", "no_active_piece"),
        ),
    ),
    (
        "moveRight",
        _cmd(
            "moveRight",
            "活动方块右移一格。",
            lifecycle_gated=True,
            reject_reasons=("quota_exceeded", "invalid_argument", "no_active_piece"),
        ),
    ),
    (
        "softDrop",
        _cmd(
            "softDrop",
            "软降一格（给分不落锁）。",
            lifecycle_gated=True,
            reject_reasons=("quota_exceeded", "invalid_argument", "no_active_piece"),
        ),
    ),
    (
        "hardDrop",
        _cmd(
            "hardDrop",
            "硬降：直落到底并锁定。",
            lifecycle_gated=True,
            reject_reasons=("quota_exceeded", "invalid_argument", "no_active_piece"),
        ),
    ),
    # ---------------- 旋转（3 条） ----------------
    (
        "rotateCW",
        _cmd(
            "rotateCW",
            "顺时针旋转。**旋转无合法落点时命令仍被接受**，另发受阻事件（ADR-043）。",
            lifecycle_gated=True,
            reject_reasons=("quota_exceeded", "invalid_argument", "no_active_piece"),
        ),
    ),
    (
        "rotateCCW",
        _cmd(
            "rotateCCW",
            "逆时针旋转。同样：旋转无合法落点不拒绝，另发受阻事件。",
            lifecycle_gated=True,
            reject_reasons=("quota_exceeded", "invalid_argument", "no_active_piece"),
        ),
    ),
    (
        "rotate180",
        _cmd(
            "rotate180",
            "旋转 180 度。踢墙表必须覆盖 180°（架构 §7.3.1）。",
            lifecycle_gated=True,
            reject_reasons=("quota_exceeded", "invalid_argument", "no_active_piece"),
        ),
    ),
    # ---------------- 暂存与推进（2 条） ----------------
    (
        "hold",
        _cmd(
            "hold",
            "暂存当前方块并取出暂存槽里的方块。**本块已用过暂存时拒绝**"
            "（并发一条 `piece:hold-denied`）。"
            "需求 §8.2 给 hold 列的可拒绝情形只有这一条，因此这里**不列 "
            "`no_active_piece`**——实现期确认的结论是："
            "**「running 态没有活动方块」不是一个可拒绝情形，而是内核不变量破坏**"
            "（架构 §6.8：命令执行时 `running` 态必然持有活动方块）。"
            "真到了那个状态，契约没有可发的拒绝事件，内核以 "
            "`KernelInvariantError` 终止，**不硬造一条没声明的拒绝原因**。",
            lifecycle_gated=True,
            reject_reasons=("quota_exceeded", "invalid_argument", "hold_already_used"),
        ),
    ),
    (
        "tick",
        _cmd(
            "tick",
            "推进一帧。**帧号 +1 与推进许可是两件事**：帧号在 tick 出队时就 +1，"
            "推进许可（`clock:tick` + `advance_frame()`）在 tick 被接受时才授予"
            "（架构 §6.1）。tick 自身不计入命令数配额，但占帧内序号 0。",
            lifecycle_gated=True,
            reject_reasons=("quota_exceeded", "invalid_argument"),
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
            lifecycle_gated=False,
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
            lifecycle_gated=True,
            reject_reasons=("quota_exceeded", "invalid_argument"),
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
            lifecycle_gated=True,
            reject_reasons=(
                "quota_exceeded",
                "invalid_argument",
                "out_of_bounds",
                "not_authorized",
                "write_condition_failed",
            ),
        ),
    ),
    # ---------------- 运行期（1 条） ----------------
    (
        "switchRuleSet",
        _cmd(
            "switchRuleSet",
            "切换到指定的已登记规则集。**不受生命周期门禁约束**——换规则集恰恰要"
            "在非 running 态被接受（待开始 / 已结束），局中（running）切换由内核"
            "自判 `illegal_transition`（拒绝比半局规则不一致便宜得多）。"
            "规则集未登记 → `unknown_rule_set`（**明确拒绝，不拿标准规则集顶替**）。"
            "切换被接受后局面重置，并发 `kernel:rule-set-changed` 与 `game:reset`。",
            args=(CommandArg("rule", "str", False, "目标规则集标识；必须已在宿主侧登记"),),
            lifecycle_gated=False,
            reject_reasons=(
                "quota_exceeded",
                "invalid_argument",
                "illegal_transition",
                "unknown_rule_set",
            ),
        ),
    ),
)

#: 命令注册表：键 = 命令类型名，值 = 该命令的声明。**这是 v1 的全集**（FR-2.1）。
COMMANDS: Mapping[str, CommandSpec] = MappingProxyType(dict(_COMMAND_SPECS))

#: 命令类型名的稳定序列（枚举顺序 = 需求 §8.2 的表格顺序）。
COMMAND_TYPES: tuple[str, ...] = tuple(name for name, _ in _COMMAND_SPECS)

#: 命令名的**类型串**（形态 11 · 由 `COMMAND_TYPES` 派生）。
#:
#: **由注册表派生，不另抄一份名单**——手抄的那份与 `COMMANDS` 静默分叉时，
#: 基线显示全绿，而实际允许的命令名已经变了。事件侧的 `commandType` 字段
#: 引用它（`events.py`），取值域因此在类型串里看得见。
COMMAND_TYPE_LITERAL: str = literal_enum(COMMAND_TYPES)

#: 事件侧承载**命令名**的负载字段：`(事件类型, 字段名)`。
#:
#: 这些字段的取值域 = `COMMAND_TYPES`——命令集"封闭且可枚举"（FR-2.1）在
#: 事件侧的落点。**取值域写进类型串**（形态 11 · `COMMAND_TYPE_LITERAL`），
#: 基线才看得见它变过；这张表则声明"哪些字段承载命令名"，两边配合：字段的
#: **类型**由类型串说，字段的**位置**由这张表说。
#: **运行时的取值域校验归 S9/S11**——派发了表外的命令名，内核要拒。
#:
#: 声明放在命令侧而不是事件侧：命令集是命令面的东西，事件侧只负责承载。
COMMAND_TYPE_FIELDS: tuple[tuple[str, str], ...] = (
    ("command:accepted", "commandType"),
    ("command:received", "commandType"),
    ("command:rejected", "commandType"),
    ("input:intent", "commandType"),
)


def is_lifecycle_gated(command_type: str) -> bool:
    """该命令是否受**统一生命周期门禁**约束。

    门禁的归属是 `CommandSpec.lifecycle_gated` 这一个**必填字段**——加命令
    时漏表态会直接 `TypeError`，不会安静地当成"不受门禁"。

    未知命令名抛 `ValueError`（不是 `KeyError`）并带上可用名字：这是包级
    导出的公开 API，报错要能直接看懂的。
    """
    spec = COMMANDS.get(command_type)
    if spec is None:
        raise ValueError(f"未知命令类型：{command_type!r}（可用：{', '.join(COMMAND_TYPES)}）")
    return spec.lifecycle_gated


#: **跨切的拒绝原因**：不管哪条命令，**都可能被它拒**（架构 §6.4 / §6.5）。
#:
#: - `quota_exceeded`：命令数 / 轮次数配额超限时，被丢弃的**任何**命令；
#: - `invalid_argument`：参数不合规格（类型不对 / 参数多余）。
#:
#: **为什么这张表存在**：这两条跨切规则**写在 docstring 里不等于它们落进了
#: 每一条声明**——M3 审查报告 M1 就是这么翻出来的（`start` / `pause` /
#: `resume` / `reset` 四条**从 M0 起就没声明** `quota_exceeded`，而内核
#: 对它们全都发了）。**声明与内核行为分家**时，消费方按 `reject_reasons`
#: 枚举拒绝面就会漏。
#:
#: **它守什么**：每条命令的 `reject_reasons` **必须**带上这里列的全部原因。
#: **它不守什么**：命令**特有**的原因（门禁三值 / `no_active_piece` /
#: `hold_already_used` / `level_out_of_range` / `out_of_bounds` /
#: `not_authorized` / `write_condition_failed` / `illegal_transition` /
#: `unknown_rule_set`）——那些由各自的声明说。
CROSS_CUT_REJECT_REASONS: tuple[str, ...] = ("quota_exceeded", "invalid_argument")


def missing_cross_cut_reasons() -> tuple[str, ...]:
    """哪些命令**漏声明**了跨切的拒绝原因（**空 = 全都带了**）。

    **这是一条机器可验的不变式**——M3-C2 之前它只活在 docstring 里，
    没有任何一处守着，所以 5 条命令漏了四年。

    **用法**：构造命令目录时当场调（漏了当场炸），以及测试里当门。
    """
    missing: list[str] = []
    for name, spec in COMMANDS.items():
        absent = [
            reason for reason in CROSS_CUT_REJECT_REASONS if reason not in spec.reject_reasons
        ]
        if absent:
            missing.append(f"{name} 缺 {', '.join(absent)}")
    return tuple(missing)


def _assert_cross_cut_reasons_declared() -> None:
    """**跨切的拒绝原因必须每条命令都声明**——当场炸，不留到消费方踩空。

    **为什么放在导入期**：M3 之前这条规则只写在 docstring 里，**没有任何一处
    机器守着**——于是 `start` / `pause` / `resume` / `reset` 四条**从 M0 起
    就没声明** `quota_exceeded`，而内核对它们全都发了（审查报告 M1）。
    **声明与内核行为分家**时，消费方按 `reject_reasons` 枚举拒绝面就会漏。

    **当场炸的处置**：`ValueError` 带出漏了哪几条、缺了哪些原因——**报错要能
    直接看懂的**（与 `SystemManifest.__post_init__` 同一款纪律）。
    """
    missing = missing_cross_cut_reasons()
    if missing:
        raise ValueError(f"命令声明漏了跨切的拒绝原因：{'；'.join(missing)}")


_assert_cross_cut_reasons_declared()
