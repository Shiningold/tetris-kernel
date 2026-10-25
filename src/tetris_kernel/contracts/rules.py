"""规则接口签名声明（M0 定稿 · 架构 §7.3.1 · ADR-061）。

公共叶子——**不 import 任何 `tetris_kernel.*` 模块，也不依赖第三方**；只用标准库
（架构 §2.3 / §4.1）。本模块只做「声明」，不做「行为」：`rules/` 里的标准规则
实现是 M1 的事。

## 声明了什么

- `RuleContext`：内核递给规则集的运行时上下文（5 字段）。
- `PlacementClass` · `ClearGroup` · `ScoreState` · `ScoreDelta`：四个**中性交换
  结构**——内核与规则集之间传的东西。
- `RULE_SIGNATURES`：9 条接口签名的机器可读声明（参数名 + 类型 + 返回类型）。

## 为什么签名必须在 M0 定死

`rules/` 自 M0 进冻结区（架构 §4.4 / §7.6），而标准规则是 M 级需求、M1 就要
实现——**签名晚于实现必被实现反向塑形**，补维度即改冻结区（ADR-061）。所以前
8 条在 M0 定稿、第 9 条 `soft_drop_score` 在 M1 定稿（见下），签名进
`contracts/` 基线（第四部分）。

四条纪律（架构 §7.3.1）：规则集不可变 · 形状表 / 踢墙表必须带轴维度 ·
T-Spin 判定需要 `PlacementInfo` · 交换结构放 `contracts/` 不放 `rules/`。

## 为什么交换结构放 contracts/ 不放 rules/

`contracts/` 是公共叶子，`rules/` 可以 import `contracts/`，反过来不行（架构
§2.3）。放 `rules/` 就进了冻结区——变体想改 T-Spin 的判定定义或计分结构，会
直接撞 G4-1（架构 §7.3.1 纪律 4）。放 `contracts/` 则受"只增不改"基线保护：
改字段会红一次，而这正是想要的显式 major 提醒。

## 规则接口只认中性类型

接口只用 `int` / `str` / `tuple` / `Sequence` 与本模块的中性结构，**不 import
kernel 的任何 dataclass**（架构 §7.3）。否则 `kernel > rules` 契约第一天就红，
然后大概率被加豁免——契约就废了。

## 第 9 条签名：`soft_drop_score`（M1 新增）

M0 把软降得分写死在核心里（`kernel/core.py` 的 `+1`）——**换规则集就改不了软降
得分**，撞 G4-1（M4 落地变体时内核文件必须零差异）。所以 M1 把它**归规则集**：

```
soft_drop_score(ctx: RuleContext, distance: int) -> int
```

**为什么是独立一条签名，而不是给 `score_clear` 加参数**：

- **时机与频率都不同**：`score_clear` 只在**锁定结算**时调用一次，软降发生在
  **放置途中**（每条软降命令一次）。塞进 `score_clear` 会让"消行结算"背上
  "移动计分"的语义——两件事混成一件，计分表就说不清了。
- **不是 `RuleContext` 的字段**：`RuleContext` 描述的是**跨帧的运行时上下文**
  （帧号 / 等级 / 已放置数 / 轴 / 步长），而软降距离是**单次命令的参数**。
  塞进上下文会让它多一个"每帧都要重置"的瞬时量。
- **参数名与顺序进基线**：声明顺序紧跟 `score_clear` 之后（计分组连着排），
  **不得重排**——重排就是已有键的哈希变化 = major 变更。

**M1 的标准规则集实现它**（`distance × 1`），内核在 S11 改为调用它。

## T-Spin 分类怎么到达计分
`classify_placement` 的结果（`PlacementClass`）必须到达 `score_clear`——否则
计分模块看不见 T-Spin，T-Spin 的分值就没法算（FR-4.6 的计分表）。

**冻结的签名里没有给它的位置**：`score_clear(groups, ctx, prev)` 只吃消行组。
所以分类**挂在组上**——`ClearGroup.classification`：

- **不新增签名参数**：`score_clear` 的参数表是架构 §7.3.1 定死的 9 条之一，
  加参数就是改冻结签名（要走 major 变更流程）。
- **不另开第五项接口**：FR-4.9 仍是四项。
- 内核只做**搬运**，不做判断：分类是规则集给的，内核不重新判定 T-Spin。

`scan_clears` 单独调用时不知道放置上下文（它的参数里没有 `PlacementInfo`），
所以它产出的组 `classification` 是 `None`；内核拿到分类后把它挂到本次消行的
每个组上，再把组交给 `score_clear`。

## 两处词汇表不要混

- `RuleContext.axis` = **重力方向**（`down` / `left` / `right` / `up`，见
  `AXIS_VALUES`）——变体维度，契约层钉死。
- `ClearGroup.axis` = **消行轴**（`row` / `column`，见 `CLEAR_AXIS_VALUES`）——
  这组被消的是行还是列。标准玩法（重力向下）下两者对应 `down` ↔ `row`。

## 取值域怎么绑到字段上

**取值域只活在散文里，等于没有取值域。** 两条判据：

- **内核要解读的字段 → 契约层钉死**：内核读 `ctx.axis` 才知道重力朝哪、读
  `group.axis` 才知道消的是行还是列。这种字段的取值域由契约定死，并在
  **构造时校验**（`RuleContext` / `ClearGroup` 的 `__post_init__`）——域外的值
  当场被拒，规则集写错轴不会一路飘到渲染层。
- **内核只透传的字段 → 规则集定义**：`tSpin` / `classKey` 内核不认识，只原样
  带进事件，取值域由规则集说了算（换规则集就换词汇，钉死会把变体挡在门外）。
  这两个字段登记在 `RULE_SET_DEFINED_FIELDS` 里——**取值归属必须表态**，
  不表态的字段不许悄悄长出来。

**取值域现在写进类型串了**（M1 · 取值域统一收口）：`RuleContext.axis` 与
`ClearGroup.axis` 的类型串是**字面量枚举**（形态 11），由 `AXIS_VALUES` /
`CLEAR_AXIS_VALUES` 派生（`AXIS_LITERAL` / `CLEAR_AXIS_LITERAL`）。
**取值域常量本身不进基线**（架构 §4.4 末条）——进基线的是**引用处展开后的
类型串**：常量改了而引用处没跟着改，基线就会红（这正是要的效果）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .types import BOARD_VIEW, COORD_CELLS, VECTOR2, literal_enum

# ---------------------------------------------------------------------------
# 取值域
# ---------------------------------------------------------------------------

#: 重力方向（`RuleContext.axis` 的取值域，架构 §7.3.1 定死）。
#:
#: 这是**契约层**的词汇：重力方向是变体维度（变体 A「重力可旋转」换的就是
#: 它），规则集按它选表，所以取值域由契约钉死，不留给规则集自定义。
#: **内核要解读这个字段**（重力朝哪），所以它在 `RuleContext` 构造时校验。
AXIS_VALUES: tuple[str, ...] = ("down", "left", "right", "up")

#: 消行轴（`ClearGroup.axis` 的取值域）：这组被消的是**行**还是**列**。
#:
#: 重力向下时消行（`"row"`），重力横向时消列（`"column"`）——横向重力是变体
#: 维度，"轴"这一维在数据上表达不出来，M4 的变体 A 就没法落地。
#: **内核也要解读这个字段**（消的是行还是列），所以同样在构造时校验。
CLEAR_AXIS_VALUES: tuple[str, ...] = ("row", "column")

#: 重力方向的**类型串**（形态 11 · 由 `AXIS_VALUES` 派生）。
#:
#: **取值域写进类型串**，基线才看得见它变过（架构 §7.3.1 末条）。类型串由
#: 取值域派生（`literal_enum`），**不手写**——手写类型串 + 另抄一份取值域，
#: 两处静默分叉时基线显示全绿，而实际的值域已经变了。
#:
#: `RuleContext.axis` 的注解**逐字**等于这个常量（契约层测试钉住这一条），
#: 这样"类型串"与"运行时校验用的取值域"指向同一份词汇。
AXIS_LITERAL: str = literal_enum(AXIS_VALUES)

#: 消行轴的**类型串**（形态 11 · 由 `CLEAR_AXIS_VALUES` 派生）。
CLEAR_AXIS_LITERAL: str = literal_enum(CLEAR_AXIS_VALUES)

#: 取值域**由规则集定义**（契约层不钉死）的字段：`(结构名, 字段名)` → 说明。
#:
#: **取值归属必须表态**：规则侧结构里每个 `str` 字段，要么在
#: `RuleContext` / `ClearGroup` 那样有契约层取值域并**构造即校验**，要么像这里
#: 一样被点名豁免。新加一个 `str` 字段却既不表态也不校验，测试会红。
#:
#: 这两个字段的共同点：**内核只透传、不解读**——内核不认识 T-Spin 与计分类别，
#: 只把它们原样带进事件 `piece:locked.tSpin` / `board:lines-cleared.classKey`。
#: 换规则集就换词汇，契约层钉死会把变体挡在门外。
RULE_SET_DEFINED_FIELDS: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        (
            "PlacementClass",
            "tSpin",
        ): "T-Spin 标记：FR-4.7 要求含 Mini 区分；架构 §7.3.1 说 T-Spin '三分' + Mini"
        "（建议取值 none / mini / single / double / triple，具体由 M1 的标准规则定稿）",
        (
            "PlacementClass",
            "classKey",
        ): "计分类别键：计分表的键（建议取值 single / double / triple / tetris 等，"
        "具体由 M1 的标准规则定稿）",
    }
)


# ---------------------------------------------------------------------------
# 中性交换结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleContext:
    """内核递给规则集的运行时上下文（架构 §7.3.1 · 5 字段）。

    **规则集不可变，随时间的一切都是关于 `ctx` 的纯函数**——规则集一旦持有可变
    状态，FR-10.2 的存档恢复就得额外序列化规则集状态（架构 §7.3.1 纪律 1）。

    字段：

    - `frame`：逻辑帧号（宿主逻辑帧，暂停期照走）。
    - `level`：当前等级。
    - `piecesPlaced`：已放置方块数。
    - `axis`：重力方向，取值域 `AXIS_VALUES`（`down` / `left` / `right` / `up`），
      **构造时校验**。
    - `stepMs`：**宿主逻辑步长（毫秒）**（架构 §7.3.1 · ADR-067）。

      **谁算**：**宿主**——时钟源在宿主层（真实时钟 / 虚拟时钟，架构 §5.3），
      内核不读墙钟；宿主把它当**固定步长**递进来（逻辑时间 = 帧号 × 步长，
      与事件 `clock:tick` 的负载同一口径）。
      **给谁用**：**规则集**（经 `ctx` 递到规则集手里）。

      **规则集只透传不用**：标准玩法的重力间隔用**逻辑帧数**
      （`gravity_interval(ctx)` 返回帧数），不看毫秒——将来若有变体按毫秒
      调手感，那是变体自己的事，契约层不替它决定。

    字段顺序即声明顺序（进基线，不得重排）。
    """

    frame: int
    level: int
    piecesPlaced: int
    # **取值域写进注解**：契约的类型串是字面量枚举（`AXIS_LITERAL`），注解要跟
    # 它**逐字相等**（内核的导入期契约对齐自检就是这么比的）——注解存下来是
    # 什么样子，取决于 PEP 563：编译器会把内嵌的字符串字面量统一重加成
    # **单引号**，所以 `Literal['down', ...]` 才是存下来的那一串。
    axis: Literal["down", "left", "right", "up"]
    stepMs: int

    def __post_init__(self) -> None:
        """校验契约层钉死的取值域：重力方向。

        **内核要解读这个字段**（重力朝哪），所以取值域由契约钉死，域外的值
        当场被拒——否则规则集写错轴，会一路飘到渲染层才暴露。
        """
        if self.axis not in AXIS_VALUES:
            raise ValueError(f"axis 必须是 {AXIS_VALUES} 之一，收到 {self.axis!r}")


@dataclass(frozen=True)
class PlacementClass:
    """一次放置的分类结果（`classify_placement` 的返回值）。

    字段：

    - `tSpin`：T-Spin 标记。**取值域由规则集定义**（FR-4.7 要求含 Mini 区分，
      架构 §7.3.1 说 T-Spin "三分" + Mini）——登记在 `RULE_SET_DEFINED_FIELDS`。
      契约只声明类型，不钉死词汇：换规则集就换词汇，钉死会把变体挡在门外。
    - `classKey`：计分类别键，与事件 `board:lines-cleared.classKey` **同名同义**
      （它是计分表的键）。**取值域同样由规则集定义**——登记在
      `RULE_SET_DEFINED_FIELDS`。

    这两个字段的共同点：**内核只透传、不解读**——内核不认识 T-Spin 与计分类别，
    只把它们原样带进事件，所以词汇表归规则集，不归契约层。

    `classKey` 与 `tSpin` 放在一起，是因为它们是**同一次消行的同一份分类**：
    事件 `board:lines-cleared` 的这两个字段同源，计分模块也按 `classKey` 查表。
    """

    tSpin: str
    classKey: str


@dataclass(frozen=True)
class ClearGroup:
    """一组被消的行 / 列（`scan_clears` 的返回单元）。

    字段：

    - `axis`：消行轴，取值域 `CLEAR_AXIS_VALUES`（`row` / `column`），
      **构造时校验**。**与 `RuleContext.axis` 不是一回事**——那里是重力方向，
      这里是"这组被消的是行还是列"（标准玩法：重力 `down` ↔ 消行 `row`）。
    - `index`：行号（`axis` 为 `row`）或列号（`axis` 为 `column`）。
    - `cells`：该行 / 列的**全部**格子坐标清单（整条线一起消失，格子清单给的是
      这条线的几何，不是其中有方块的那些）。
    - `classification`：本次消行对应的放置分类。`scan_clears` 单独调用时不知道
      放置上下文（它的参数里没有 `PlacementInfo`），所以是 `None`；内核拿到
      `classify_placement` 的结果后把它挂上来，再交给 `score_clear`
      （见模块 docstring「T-Spin 分类怎么到达计分」）。

    **不能只返回数量**（架构 §7.3.1）：有了轴 / 索引 / 格子清单，塌落动画、
    计分与事件负载才有素材。
    """

    axis: Literal["row", "column"]
    index: int
    cells: tuple[tuple[int, int], ...]
    classification: PlacementClass | None = None

    def __post_init__(self) -> None:
        """校验契约层钉死的取值域：消行轴。

        **内核要解读这个字段**（消的是行还是列），所以取值域由契约钉死，
        域外的值当场被拒——规则集吐出一个内核读不懂的轴，比报错更难查。
        """
        if self.axis not in CLEAR_AXIS_VALUES:
            raise ValueError(f"axis 必须是 {CLEAR_AXIS_VALUES} 之一，收到 {self.axis!r}")


@dataclass(frozen=True)
class ScoreState:
    """计分状态（`score_clear` 的 `prev` 参数）。

    字段与快照的计分字段**同名同义**：`score` / `lines` / `combo` / `b2b` /
    `level`——同一套命名体系，跨面改一个名字会立刻露馅（架构 §4.4 末条）。

    规则集持有的是**这份状态的副本**：它算完返回新状态，**落笔由内核做**
    （架构 §7.3.1）。规则集不改内核的状态，它只说"新状态应该是什么"。
    """

    score: int
    lines: int
    combo: int
    b2b: bool
    level: int


@dataclass(frozen=True)
class ScoreDelta:
    """一次结算的结果（`score_clear` 的返回值）。

    字段：

    - `scoreDelta`：分数增量。
    - `linesDelta`：消行增量。
    - `state`：结算后的**新状态**（内核据此落笔）。

    **纯函数**：规则集不落笔，只返回增量与新状态（架构 §7.3.1）。
    """

    scoreDelta: int
    linesDelta: int
    state: ScoreState


# ---------------------------------------------------------------------------
# 签名声明结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleParam:
    """规则接口的一个参数声明。

    字段：参数名 · 类型表达式 · 中文说明。**参数名即契约**，改名 = major 变更。
    类型表达式文法见 `tetris_kernel.contracts.types`。
    """

    name: str
    type: str
    doc: str


@dataclass(frozen=True)
class RuleSignature:
    """一条规则接口签名的声明。

    字段：签名名 · 参数声明（顺序即调用顺序）· 返回类型 · 中文说明。

    **签名进基线**（架构 §7.6 的第四部分）——改签名 = 改已有键的哈希 = major
    变更，只能走显式 `--update-baseline` 并写明理由。
    """

    name: str
    params: tuple[RuleParam, ...]
    returns: str
    doc: str


# ---------------------------------------------------------------------------
# 九条签名（架构 §7.3.1 · 四项接口 + 形状表 / 踢墙表 + M1 新增软降计分）
# ---------------------------------------------------------------------------

_RULE_SIGNATURES: tuple[tuple[str, RuleSignature], ...] = (
    (
        "wrap_cell",
        RuleSignature(
            name="wrap_cell",
            params=(
                RuleParam("x", "int", "格子横坐标（x 向右为正）"),
                RuleParam("y", "int", "格子纵坐标（y 向下为正）"),
                RuleParam("w", "int", "棋盘宽（列数）"),
                RuleParam("h", "int", "棋盘高（行数，含隐藏行）"),
            ),
            returns=VECTOR2,
            doc="坐标映射函数（拓扑边界）：把坐标映射回棋盘内。"
            "标准玩法恒等；圆柱 = x 环绕；莫比乌斯 = x 环绕 + y 翻转。"
            "**是函数不是参数**（ADR-037）——内核在所有坐标访问点都过一遍。",
        ),
    ),
    (
        "gravity_vector",
        RuleSignature(
            name="gravity_vector",
            params=(RuleParam("ctx", "RuleContext", "运行时上下文"),),
            returns=VECTOR2,
            doc="重力方向向量 `(x, y)`。**方向必须是向量，不能只有 dy**——"
            "横向重力时 dy = 0（架构 §7.3.1）。",
        ),
    ),
    (
        "gravity_interval",
        RuleSignature(
            name="gravity_interval",
            params=(RuleParam("ctx", "RuleContext", "运行时上下文"),),
            returns="int",
            doc="重力间隔：多少逻辑帧落一格。",
        ),
    ),
    (
        "scan_clears",
        RuleSignature(
            name="scan_clears",
            params=(
                RuleParam("board", BOARD_VIEW, "棋盘只读视图（行优先）"),
                RuleParam("ctx", "RuleContext", "运行时上下文"),
            ),
            returns="tuple[ClearGroup, ...]",
            doc="扫描可消的行 / 列。**返回'轴 + 行/列索引 + 格子清单'，"
            "不能只返回数量**（架构 §7.3.1）。",
        ),
    ),
    (
        "classify_placement",
        RuleSignature(
            name="classify_placement",
            params=(
                RuleParam("board", BOARD_VIEW, "棋盘只读视图（行优先）"),
                RuleParam("placement", "PlacementInfo", "本次放置的上下文"),
                RuleParam("ctx", "RuleContext", "运行时上下文"),
            ),
            returns="PlacementClass",
            doc="判定本次放置的分类：T-Spin 三分 + Mini（FR-4.7）。"
            "**归消行判定模块，不另开第五项**（FR-4.9 仍是四项）。",
        ),
    ),
    (
        "score_clear",
        RuleSignature(
            name="score_clear",
            params=(
                RuleParam("groups", "Sequence[ClearGroup]", "本次消行的分组"),
                RuleParam("ctx", "RuleContext", "运行时上下文"),
                RuleParam("prev", "ScoreState", "结算前的计分状态"),
            ),
            returns="ScoreDelta",
            doc="按消行组计分。**纯函数**：返回增量与新状态，**落笔由内核做**（架构 §7.3.1）。",
        ),
    ),
    (
        "soft_drop_score",
        RuleSignature(
            name="soft_drop_score",
            params=(
                RuleParam("ctx", "RuleContext", "运行时上下文"),
                RuleParam(
                    "distance",
                    "int",
                    "本次软降实际下移的格数（被墙挡住则为 0）",
                ),
            ),
            returns="int",
            doc="软降得分：本次软降应加的分。**独立一条签名**，不是 `score_clear` "
            "的参数——消行结算在锁定时调一次，软降发生在放置途中，"
            "**时机与频率都不同**（塞进 `score_clear` 会让「消行结算」背上"
            "「移动计分」的语义）。",
        ),
    ),
    (
        "shape_cells",
        RuleSignature(
            name="shape_cells",
            params=(
                RuleParam("ctx", "RuleContext", "运行时上下文（**必须带 axis**）"),
                RuleParam("pieceType", "str", "方块类型"),
                RuleParam("rotation", "int", "朝向"),
            ),
            returns=COORD_CELLS,
            doc="取形状表：方块在当前重力轴下的占位格子。**必须带 `ctx.axis`**——"
            "形状表随重力方向变换，而变换由规则集整体提供，内核不做变换"
            "（架构 §7.3.1 纪律 2）。",
        ),
    ),
    (
        "kick_offsets",
        RuleSignature(
            name="kick_offsets",
            params=(
                RuleParam("ctx", "RuleContext", "运行时上下文（**必须带 axis**）"),
                RuleParam("pieceType", "str", "方块类型"),
                RuleParam("fromRotation", "int", "旋转前朝向"),
                RuleParam("toRotation", "int", "旋转后朝向"),
            ),
            returns=COORD_CELLS,
            doc="取踢墙表：旋转时的候选偏移。同形状表，**必须带 `ctx.axis`**；"
            "**含 180°**（命令目录里有 `rotate180`）。",
        ),
    ),
)

#: 规则接口签名注册表：键 = 签名名，值 = 该签名的声明。**9 条**（M0 定稿 8 条
#: + M1 新增第 9 条 `soft_drop_score`）。
#:
#: 顺序 = 架构 §7.3.1 表格的顺序；**第 9 条紧跟 `score_clear` 之后**
#: （计分组连着排）。顺序进基线，**不得重排**。
RULE_SIGNATURES: Mapping[str, RuleSignature] = MappingProxyType(dict(_RULE_SIGNATURES))

#: 签名名的稳定序列（枚举顺序 = 架构 §7.3.1 表格顺序）。
RULE_SIGNATURE_NAMES: tuple[str, ...] = tuple(name for name, _ in _RULE_SIGNATURES)

__all__ = [
    "AXIS_LITERAL",
    "AXIS_VALUES",
    "CLEAR_AXIS_LITERAL",
    "CLEAR_AXIS_VALUES",
    "RULE_SET_DEFINED_FIELDS",
    "RULE_SIGNATURES",
    "RULE_SIGNATURE_NAMES",
    "ClearGroup",
    "PlacementClass",
    "RuleContext",
    "RuleParam",
    "RuleSignature",
    "ScoreDelta",
    "ScoreState",
]
