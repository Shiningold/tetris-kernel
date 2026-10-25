"""规则表：形状表 / 踢墙表 / 计分表 / 重力间隔表（ADR-032 · 架构 §7.3）。

## 表落成 Python 字面量常量

**不落外部数据文件**（ADR-032）：外部文件会让"内核文件差异为空"变成文字游戏
（规则被改到冻结区之外），还引入 IO 依赖撞上"内核不依赖文件系统专有能力"，
并给冻结基线开洞。

## 表由规则集整体提供，内核不做变换

形状表与踢墙表的**第一层键是重力轴**（架构 §7.3.1 纪律 2）：内核不拿
"标准表 + 方向参数"去转——转不转由规则集说了算（ADR-061）。

## M1 的表（spec-M1.md §7.1–§7.4）

- **形状表**：`down` 轴，标准 7 种方块 × 4 朝向 = **28 条**，SRS 基准坐标
  （I 用 4×4 盒，其余 3×3 盒，O 用 2×2 盒）。**M0 的表已是 SRS 基准坐标，
  M1 逐条核对后确认数值一条未改**——M1 把"核对"这件事做成机器可查的测试
  （`tests/test_rules_m1.py` 的独立转录表逐条比对）。其余三个轴**留空，
  属 M4-A5**（形状表 / 踢墙表随重力方向变换）。
- **踢墙表**：SRS 权威表（JLSTZ 一套 · I 一套 · O 不踢墙）+ **180° 扩展表
  两套**——SRS 原始规范**不含 180°**，采现代通行扩展口径，**不冒充 SRS
  正统**（来源与取舍见 ADR-068）。
- **计分表**：基础分 · 连击奖励 · B2B 倍率 · 升级阈值（§7.4）。
- **重力间隔表**：保留 M0 的 10 级最小曲线——**M1 只确立口径，不做手感标定**
  （spec §4.2）。

## 坐标约定：踢墙表的 y 分量与权威表**反号**

权威表（Tetris Wiki 的 `Super Rotation System` 条目与 Hard Drop Tetris Wiki
的 `SRS` 条目，两者数值逐条相同）用 **x 右正、y 上正**；本项目的坐标是
**x 右正、y 下正**（`PlacementInfo.cells` 与 `SHAPE_TABLE` 同一套坐标）。
**落地时 y 分量一律取反**——这是本模块最容易错的一处，所以转录表与注释
都写死了这条换算关系。

## 候选顺序就是优先级

踢墙候选**按顺序**试：**第一个能落下的就用它**，全部落不下 → **旋转被拒**
（命令仍被接受，另发受阻事件，ADR-043）。**顺序是语义的一部分**，
比对时必须逐条比顺序，不能只比集合。

## 词汇表归规则集

`tSpin` 与 `classKey` 的取值域**由规则集定义**（`contracts/rules.py` 的
`RULE_SET_DEFINED_FIELDS` 点了名豁免）：契约层只声明类型，钉死会把变体挡在
门外。本模块给出的是**标准规则集自己的一套词汇**：

- `tSpin`：`none` / `tspin` / `mini` / `single` / `double` / `triple`——
  **五档 T-Spin**（不含 `none`），见 spec §7.3。
- `classKey`：`none` / `single` / `double` / `triple` / `tetris` /
  `tspin-none` / `tspin-mini` / `tspin-single` / `tspin-double` /
  `tspin-triple`——**计分表的键**。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

# ---------------------------------------------------------------------------
# 方块与朝向
# ---------------------------------------------------------------------------

#: 标准 7 种方块（FR-4.1）。
PIECE_TYPES: tuple[str, ...] = ("I", "J", "L", "O", "S", "T", "Z")

#: 朝向编号：0 = 初始态，1 = 顺时针 90°，2 = 180°，3 = 逆时针 90°。
#:
#: 与 SRS 的状态命名一一对应：`0` = 初始态 · `R` = 顺时针 90°（= 本表的 1）·
#: `2` = 180°（= 本表的 2）· `L` = 逆时针 90°（= 本表的 3）。
ROTATIONS: tuple[int, ...] = (0, 1, 2, 3)

#: 朝向数——朝向编号按它取模。
ROTATION_COUNT: int = len(ROTATIONS)

# ---------------------------------------------------------------------------
# 形状表
# ---------------------------------------------------------------------------


def _freeze_shape_table(
    pieces: Mapping[str, Mapping[int, tuple[tuple[int, int], ...]]],
) -> Mapping[str, Mapping[int, tuple[tuple[int, int], ...]]]:
    """形状表的**两层内层**也包成只读映射（与 `KICK_TABLE` 同一口径）。

    **只冻外层等于没冻**：外层是 `MappingProxyType`、内层是普通 `dict` 时，
    任何拿到 `SHAPE_TABLE` 引用的代码都能在运行期改掉形状表——架构 §7.3.1
    纪律 1「规则集不可变、无状态」被绕过，而回放校验用的是**快照指纹**，
    改表不会让任何门禁变红。
    """
    return MappingProxyType(
        {piece: MappingProxyType(dict(orientations)) for piece, orientations in pieces.items()}
    )


#: 标准朝向（`down` 轴）的形状表：方块类型 → 朝向 → 占位格子。
#:
#: 坐标是**方块自身的基准坐标**（x 右正、y 下正），不是棋盘绝对坐标——
#: 棋盘位置由内核给的落点决定。I / O 用 4×4 / 2×2 盒，其余用 3×3 盒
#: （SRS 的基准坐标，标准旋转系统 FR-4.2）。
#:
#: **M1 逐条核对过**：28 条全部与 SRS 基准坐标一致（**占位格子一个没改**），
#: 并把 **Z 的朝向 3** 的格子顺序改回**行优先**（其余 27 条本来就是行优先，
#: 这一条不是——排序口径不自洽时，"逐条相等"就没法比）。核对方式不是"看着
#: 对"，而是与**独立转录表**逐条相等（`tests/test_rules_m1.py`）。
#:
#: **排序口径：行优先**（上到下，每行左到右）——28 条统一用它，比对才敢
#: 逐条比到顺序。格子**顺序本身不是语义**（碰撞遍历与占位判断都只看集合），
#: 但表的排序口径必须自洽。
_SHAPE_DOWN: Mapping[str, Mapping[int, tuple[tuple[int, int], ...]]] = _freeze_shape_table(
    {
        "I": {
            0: ((0, 1), (1, 1), (2, 1), (3, 1)),
            1: ((2, 0), (2, 1), (2, 2), (2, 3)),
            2: ((0, 2), (1, 2), (2, 2), (3, 2)),
            3: ((1, 0), (1, 1), (1, 2), (1, 3)),
        },
        "J": {
            0: ((0, 0), (0, 1), (1, 1), (2, 1)),
            1: ((1, 0), (2, 0), (1, 1), (1, 2)),
            2: ((0, 1), (1, 1), (2, 1), (2, 2)),
            3: ((1, 0), (1, 1), (0, 2), (1, 2)),
        },
        "L": {
            0: ((2, 0), (0, 1), (1, 1), (2, 1)),
            1: ((1, 0), (1, 1), (1, 2), (2, 2)),
            2: ((0, 1), (1, 1), (2, 1), (0, 2)),
            3: ((0, 0), (1, 0), (1, 1), (1, 2)),
        },
        "O": {
            0: ((0, 0), (1, 0), (0, 1), (1, 1)),
            1: ((0, 0), (1, 0), (0, 1), (1, 1)),
            2: ((0, 0), (1, 0), (0, 1), (1, 1)),
            3: ((0, 0), (1, 0), (0, 1), (1, 1)),
        },
        "S": {
            0: ((1, 0), (2, 0), (0, 1), (1, 1)),
            1: ((1, 0), (1, 1), (2, 1), (2, 2)),
            2: ((1, 1), (2, 1), (0, 2), (1, 2)),
            3: ((0, 0), (0, 1), (1, 1), (1, 2)),
        },
        "T": {
            0: ((1, 0), (0, 1), (1, 1), (2, 1)),
            1: ((1, 0), (1, 1), (2, 1), (1, 2)),
            2: ((0, 1), (1, 1), (2, 1), (1, 2)),
            3: ((1, 0), (0, 1), (1, 1), (1, 2)),
        },
        "Z": {
            0: ((0, 0), (1, 0), (1, 1), (2, 1)),
            1: ((2, 0), (1, 1), (2, 1), (1, 2)),
            2: ((0, 1), (1, 1), (1, 2), (2, 2)),
            3: ((1, 0), (0, 1), (1, 1), (0, 2)),
        },
    }
)

#: 形状表：重力轴 → 方块类型 → 朝向 → 占位格子。
#:
#: **第一层是重力轴**——形状表随重力方向变换，而变换由规则集整体提供，
#: 内核不做变换（架构 §7.3.1 纪律 2 / ADR-061）。M1 只填 `down` 轴，
#: 其余三个轴留空位待 M4-A5 补齐。
SHAPE_TABLE: Mapping[str, Mapping[str, Mapping[int, tuple[tuple[int, int], ...]]]] = (
    MappingProxyType(
        {
            "down": _SHAPE_DOWN,
            "left": MappingProxyType({}),
            "right": MappingProxyType({}),
            "up": MappingProxyType({}),
        }
    )
)

# ---------------------------------------------------------------------------
# 踢墙表
# ---------------------------------------------------------------------------

#: 踢墙组：SRS 的权威表就是这三套（J / L / S / T / Z 共用一套、I 独立一套、
#: O 不踢墙）。
#:
#: **踢墙组是表的第三层**：`KICK_TABLE[轴][(from, to)][组] -> 候选偏移`。
KICK_GROUPS: tuple[str, ...] = ("I", "JLSTZ", "O")

#: 方块类型 → 踢墙组（**方块类型 → 组的映射由规则集提供**，架构 §7.3.1）。
#:
#: O 组只有 `(0, 0)`——SRS 里 O 旋转不位移，所以它落在哪个转向都一样。
KICK_GROUP_BY_PIECE: Mapping[str, str] = MappingProxyType(
    {
        "I": "I",
        "J": "JLSTZ",
        "L": "JLSTZ",
        "O": "O",
        "S": "JLSTZ",
        "T": "JLSTZ",
        "Z": "JLSTZ",
    }
)

#: 恒等偏移：唯一的候选是"不动"。
#:
#: 用在三处：**O 组**（SRS 里 O 不踢墙）· **同向转向**（同向不是旋转，唯一
#: 合法结果就是不动）。
IDENTITY_KICK: tuple[tuple[int, int], ...] = ((0, 0),)

#: SRS 的 90° 踢墙候选（J / L / S / T / Z 共用这一套）。
#:
#: **来源**：Tetris Wiki 的 `Super Rotation System` 条目（J, L, S, T, Z
#: Tetromino Wall Kick Data 表）与 Hard Drop Tetris Wiki 的 `SRS` 条目——
#: 两处**逐条相同**，是本表的两份独立来源。
#:
#: **坐标换算**：权威表是 **y 上正**，本项目是 **y 下正** → **y 分量取反**。
#: 权威表的 0→R 行是 `(0,0) (-1,0) (-1,+1) (0,-2) (-1,-2)`，落地成
#: `(0,0) (-1,0) (-1,-1) (0,+2) (-1,+2)`。
#:
#: **候选顺序 = 优先级**：第一个能落下的就用它；全部落不下 → 旋转被拒。
_KICK_90_JLSTZ: Mapping[tuple[int, int], tuple[tuple[int, int], ...]] = MappingProxyType(
    {
        (0, 1): ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)),  # 0→R（顺时针）
        (1, 0): ((0, 0), (1, 0), (1, 1), (0, -2), (1, -2)),  # R→0（逆时针）
        (1, 2): ((0, 0), (1, 0), (1, 1), (0, -2), (1, -2)),  # R→2（顺时针）
        (2, 1): ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)),  # 2→R（逆时针）
        (2, 3): ((0, 0), (1, 0), (1, -1), (0, 2), (1, 2)),  # 2→L（逆时针）
        (3, 2): ((0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)),  # L→2（顺时针）
        (3, 0): ((0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)),  # L→0（逆时针）
        (0, 3): ((0, 0), (1, 0), (1, -1), (0, 2), (1, 2)),  # 0→L（逆时针）
    }
)

#: SRS 的 90° 踢墙候选（I 方块独立一套）。
#:
#: **来源与坐标换算**同 `_KICK_90_JLSTZ`。权威表的 0→R 行是
#: `(0,0) (-2,0) (+1,0) (-2,-1) (+1,+2)`，落地成
#: `(0,0) (-2,0) (+1,0) (-2,+1) (+1,-2)`。
_KICK_90_I: Mapping[tuple[int, int], tuple[tuple[int, int], ...]] = MappingProxyType(
    {
        (0, 1): ((0, 0), (-2, 0), (1, 0), (-2, 1), (1, -2)),  # 0→R（顺时针）
        (1, 0): ((0, 0), (2, 0), (-1, 0), (2, -1), (-1, 2)),  # R→0（逆时针）
        (1, 2): ((0, 0), (-1, 0), (2, 0), (-1, -2), (2, 1)),  # R→2（顺时针）
        (2, 1): ((0, 0), (1, 0), (-2, 0), (1, 2), (-2, -1)),  # 2→R（逆时针）
        (2, 3): ((0, 0), (2, 0), (-1, 0), (2, -1), (-1, 2)),  # 2→L（逆时针）
        (3, 2): ((0, 0), (-2, 0), (1, 0), (-2, 1), (1, -2)),  # L→2（顺时针）
        (3, 0): ((0, 0), (1, 0), (-2, 0), (1, 2), (-2, -1)),  # L→0（逆时针）
        (0, 3): ((0, 0), (-1, 0), (2, 0), (-1, -2), (2, 1)),  # 0→L（逆时针）
    }
)

#: 180° 踢墙候选（J / L / S / T / Z 一套）——**SRS 原始规范不含 180°**。
#:
#: **来源**：Hard Drop Tetris Wiki 的 `Rotation System in TETR.IO` 条目
#: （`L,J,T-180° rotation` 与 `S,Z,I-180° rotation` 两节）。该条目记的是
#: TETR.IO 的 180° 体系：T / L / J 用**六级优先**表（其中**只有前两级**是
#: TETR.IO 与 Jstris 共用的），S / Z / I 用**两级**表（Jstris 同款）。
#:
#: **为什么只取两级**：本项目的踢墙组是 `{I, O, JLSTZ}` 三组，**一组一套表**
#: ——而 TETR.IO 的六级表在 T / L / J 三者之间**各不相同**（各有"不可能"
#: 的档位），凑不成"JLSTZ 一套"。**Jstris 共用的两级表则对 JLSTZ 全体
#: 统一**，正好对上"JLSTZ 与 I 各一套"的结构（spec §7.2 · ADR-068）。
#:
#: **坐标换算**：权威表 y 上正 → 本项目 y 下正，**y 分量取反**。
_KICK_180_JLSTZ: Mapping[tuple[int, int], tuple[tuple[int, int], ...]] = MappingProxyType(
    {
        (0, 2): ((0, 0), (0, -1)),  # 0⇒2
        (1, 3): ((0, 0), (1, 0)),  # R⇒L
        (2, 0): ((0, 0), (0, 1)),  # 2⇒0
        (3, 1): ((0, 0), (-1, 0)),  # L⇒R
    }
)

#: 180° 踢墙候选（I 方块一套）——**SRS 原始规范不含 180°**。
#:
#: **来源**：Hard Drop Tetris Wiki 的 `Rotation System in TETR.IO` 条目的
#: `S,Z,I-180° rotation` 一节里 I 方块那一段。
#:
#: 与 JLSTZ 那套**只差一处**：`L⇒R` 的第二候选是 `(1, 0)` 而不是 `(-1, 0)`。
_KICK_180_I: Mapping[tuple[int, int], tuple[tuple[int, int], ...]] = MappingProxyType(
    {
        (0, 2): ((0, 0), (0, -1)),  # 0⇒2
        (1, 3): ((0, 0), (1, 0)),  # R⇒L
        (2, 0): ((0, 0), (0, 1)),  # 2⇒0
        (3, 1): ((0, 0), (1, 0)),  # L⇒R
    }
)


def _build_kick_table() -> Mapping[
    str, Mapping[tuple[int, int], Mapping[str, tuple[tuple[int, int], ...]]]
]:
    """把 90° 表与 180° 表摊成**16 种转向 × 3 个踢墙组**的完整表。

    **为什么用代码摊而不是手抄 48 条**：权威表只给了 8 种 90° 转向与 4 种
    180° 转向，手抄成 16 × 3 = 48 条时，**复制粘贴错一条不会被任何"跑通"类
    判据抓到**——而转录错一条就是"某个角落转不进去"的静默玩法错误。
    这里让**每个权威行只出现一次**（原样转录在 `_KICK_90_*` / `_KICK_180_*`
    里），摊开只是机械地补齐缺省档位：同向转向 → 恒等偏移，O 组 → 恒等偏移。

    **摊开的规则**：转向 `(from, to)` 落在
    - 同向（`from == to`）→ 三个组都是恒等偏移（**同向不是旋转**，唯一合法
      结果就是不动）；
    - 90°（8 种）→ 取 `_KICK_90_*` 的对应行；
    - 180°（4 种）→ 取 `_KICK_180_*` 的对应行。
    """
    down: dict[tuple[int, int], dict[str, tuple[tuple[int, int], ...]]] = {}
    for from_rotation in ROTATIONS:
        for to_rotation in ROTATIONS:
            key = (from_rotation, to_rotation)
            if from_rotation == to_rotation:
                groups = {group: IDENTITY_KICK for group in KICK_GROUPS}
            elif key in _KICK_90_JLSTZ:
                groups = {
                    "JLSTZ": _KICK_90_JLSTZ[key],
                    "I": _KICK_90_I[key],
                    "O": IDENTITY_KICK,
                }
            else:
                groups = {
                    "JLSTZ": _KICK_180_JLSTZ[key],
                    "I": _KICK_180_I[key],
                    "O": IDENTITY_KICK,
                }
            down[key] = {group: groups[group] for group in KICK_GROUPS}
    return MappingProxyType(
        {
            "down": MappingProxyType({key: MappingProxyType(value) for key, value in down.items()}),
            "left": MappingProxyType({}),
            "right": MappingProxyType({}),
            "up": MappingProxyType({}),
        }
    )


#: 踢墙表：重力轴 → (旋转前朝向, 旋转后朝向) → 踢墙组 → 候选偏移。
#:
#: **第三层是踢墙组**（`"I"` / `"JLSTZ"` / `"O"`）——SRS 的权威表就是这三套，
#: 方块类型 → 组的映射见 `KICK_GROUP_BY_PIECE`。
#:
#: **键覆盖全部 16 种转向**：顺时针 4 种（0→1 / 1→2 / 2→3 / 3→0）、180° 4 种
#: （0→2 / 1→3 / 2→0 / 3→1）、逆时针 4 种（0→3 / 1→0 / 2→1 / 3→2），
#: 外加 4 个**同向**（0→0 / 1→1 / 2→2 / 3→3）——同向不是旋转（内核不会
#: 这样调），但表在朝向上全覆盖，别在一个无意义调用上炸。
#:
#: **候选顺序有意义**：偏移按**优先级**排列，第一个能落下的就用它；全部
#: 落不下 → 旋转被拒（命令仍被接受，另发受阻事件，ADR-043）。
#:
#: **只填 `down` 轴**：其余三个轴属 M4-A5（表随重力方向变换）。
KICK_TABLE: Mapping[str, Mapping[tuple[int, int], Mapping[str, tuple[tuple[int, int], ...]]]] = (
    _build_kick_table()
)

# ---------------------------------------------------------------------------
# 重力表
# ---------------------------------------------------------------------------

#: 重力方向向量：重力轴 → `(x, y)`。
#:
#: **方向是向量不是 dy**——横向重力时 `dy = 0`（架构 §7.3.1）。
GRAVITY_VECTORS: Mapping[str, tuple[int, int]] = MappingProxyType(
    {
        "down": (0, 1),
        "left": (-1, 0),
        "right": (1, 0),
        "up": (0, -1),
    }
)

#: 重力间隔表：等级 → 落一格需要的逻辑帧数。
#:
#: **M1 只确立口径，不做手感标定**（spec §4.2）：保留 M0 的 10 级最小曲线，
#: 按 60 逻辑帧 / 秒的基准（NFR-2）——1 级即每秒落一格。
#:
#: **表尾行为**：等级**超过表尾**时取**表尾值**（10 级及以后都是 5 帧）。
#: **下限**：间隔**恒 ≥ 1 帧**——间隔为 0 会让方块一帧掉到底，那不是重力。
GRAVITY_INTERVAL_FRAMES: tuple[int, ...] = (60, 50, 40, 30, 20, 15, 10, 8, 6, 5)

# ---------------------------------------------------------------------------
# 消行轴与计分表
# ---------------------------------------------------------------------------

#: 重力轴 → 消行轴（这组被消的是行还是列）。
#:
#: **两处 `axis` 是两套词汇**（`contracts/rules.py`）：`RuleContext.axis` 是重力
#: 方向，这里是消行轴。标准玩法重力 `down` ↔ 消行 `row`；横向重力消列。
#:
#: **值类型写准到 `Literal["row", "column"]`**：契约层已把消行轴的取值域钉进
#: 类型串（`ClearGroup.axis` 的类型串），这张表的值就是那个取值域里的值——
#: 写成 `Mapping[str, str]` 的话，`ClearGroup(axis=…)` 传不进那个字面量枚举
#: （类型检查当场报错）。**把类型写准，不是加 ignore**（架构 §7.6 末条）。
CLEAR_AXIS_BY_GRAVITY: Mapping[str, Literal["row", "column"]] = MappingProxyType(
    {
        "down": "row",
        "up": "row",
        "left": "column",
        "right": "column",
    }
)

#: 清行数 → 计分类别名（普通消行）：1 / 2 / 3 / 4 行 → 单 / 双 / 三 / 四消。
#:
#: 5 行以上的清行不另立类别，按 `tetris` 算（见 `standard._class_key`）。
CLEAR_NAMES: Mapping[int, str] = MappingProxyType(
    {1: "single", 2: "double", 3: "triple", 4: "tetris"}
)

#: 计分表：`classKey` → 基础分值（guideline 口径，spec §7.4）。
#:
#: 键是 `classKey` 的取值域（规则集定义的词汇，见模块 docstring）。
#:
#: **基础分全是 100 的倍数**——这一条是 B2B 倍率"整数不破"的前提
#: （800 × 3/2 = 1200 · 1200 × 3/2 = 1800 · 1600 × 3/2 = 2400）。
#:
#: - 普通消行：单 100 / 双 300 / 三 500 / 四 800；
#: - T-Spin 消行：单 800 / 双 1200 / 三 1600；
#: - T-Spin Mini（不消行）400；
#: - T-Spin 不消行（非 Mini）0。
SCORE_TABLE: Mapping[str, int] = MappingProxyType(
    {
        "none": 0,
        "single": 100,
        "double": 300,
        "triple": 500,
        "tetris": 800,
        "tspin-none": 0,
        "tspin-mini": 400,
        "tspin-single": 800,
        "tspin-double": 1200,
        "tspin-triple": 1600,
    }
)

#: 连击奖励：每多一连击加的分（**乘以消行前的连击数**）。
COMBO_BONUS: int = 50

#: B2B（背面连击）倍率的**分子**——倍率是 `分子 / 分母`。
#:
#: **不许用浮点 1.5**：`ScoreDelta.scoreDelta` 的类型是 `int`，浮点会破类型。
#: 倍率写成整数比 `3 / 2`，基础分全是 100 的倍数 → **乘完仍是整数**。
B2B_MULTIPLIER_NUMERATOR: int = 3

#: B2B 倍率的**分母**（见 `B2B_MULTIPLIER_NUMERATOR`）。
B2B_MULTIPLIER_DENOMINATOR: int = 2

#: 难消类别键（B2B 的判定依据）：四消与 T-Spin **消行**。
#:
#: **T-Spin 不消行不算难消**（`tspin-none` / `tspin-mini` 都不在表里）——
#: 它没消行，进链会让 B2B 变成"转进去也算数"，与 guideline 不符。
DIFFICULT_CLASS_KEYS: frozenset[str] = frozenset(
    {"tetris", "tspin-single", "tspin-double", "tspin-triple"}
)

#: 升一级需要的消行数：**每消 10 行升 1 级**（spec §7.4）。
#:
#: 等级由**规则集**算（内核不自己数行数定等级）——换规则集就该能改阈值。
LINES_PER_LEVEL: int = 10

#: `tSpin` 的取值域（规则集定义的词汇）：**五档 T-Spin** + `none`。
#:
#: `none` 不是 T-Spin · `tspin` 是 T-Spin 但不消行 · `mini` 是 T-Spin Mini
#: （**只在消 0 行时存在**）· `single` / `double` / `triple` 是 T-Spin 消
#: 1 / 2 / 3 行。
TSPIN_VALUES: tuple[str, ...] = ("none", "tspin", "mini", "single", "double", "triple")

#: `classKey` 的取值域（规则集定义的词汇）：计分表的全部键。
CLASS_KEYS: tuple[str, ...] = (
    "none",
    "single",
    "double",
    "triple",
    "tetris",
    "tspin-none",
    "tspin-mini",
    "tspin-single",
    "tspin-double",
    "tspin-triple",
)

__all__ = [
    "B2B_MULTIPLIER_DENOMINATOR",
    "B2B_MULTIPLIER_NUMERATOR",
    "CLASS_KEYS",
    "CLEAR_AXIS_BY_GRAVITY",
    "CLEAR_NAMES",
    "COMBO_BONUS",
    "DIFFICULT_CLASS_KEYS",
    "GRAVITY_INTERVAL_FRAMES",
    "GRAVITY_VECTORS",
    "IDENTITY_KICK",
    "KICK_GROUP_BY_PIECE",
    "KICK_GROUPS",
    "KICK_TABLE",
    "LINES_PER_LEVEL",
    "PIECE_TYPES",
    "ROTATION_COUNT",
    "ROTATIONS",
    "SCORE_TABLE",
    "SHAPE_TABLE",
    "TSPIN_VALUES",
]
