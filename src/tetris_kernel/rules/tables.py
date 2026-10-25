"""规则表：形状表 / 踢墙表 / 计分表 / 重力间隔表（ADR-032 · 架构 §7.3）。

## 表落成 Python 字面量常量

**不落外部数据文件**（ADR-032）：外部文件会让"内核文件差异为空"变成文字游戏
（规则被改到冻结区之外），还引入 IO 依赖撞上"内核不依赖文件系统专有能力"，
并给冻结基线开洞。

## 表由规则集整体提供，内核不做变换

形状表与踢墙表的**第一层键是重力轴**（架构 §7.3.1 纪律 2）：内核不拿
"标准表 + 方向参数"去转——转不转由规则集说了算（ADR-061）。

## M0 的表是最小版（spec-M0.md §7.5）

- **形状表**：标准 7 种方块 × 4 朝向（SRS 基准坐标），**只填 `down` 轴**——
  其余三个轴属 M4-A5（形状表 / 踢墙表随重力方向变换）。表里保留这三个轴的
  空位，是为了让"轴维度"在数据上先立住：将来补轴是填表，不是改结构。
- **踢墙表**：**恒等偏移**（唯一候选是 `(0, 0)`）——标准踢墙表逐条验证是 **M1**
  的开门判据，M0 不做（plan-M0.md S13 常见坑）。
- **计分表**：基础分值 + 连击奖励；B2B 倍率、T-Spin Mini 与三档精确判定是 M1。
- **重力间隔表**：前 10 级的最小曲线，按 60 逻辑帧 / 秒的基准（NFR-2）。

## 词汇表归规则集

`tSpin` 与 `classKey` 的取值域**由规则集定义**（`contracts/rules.py` 的
`RULE_SET_DEFINED_FIELDS` 点了名豁免）：契约层只声明类型，钉死会把变体挡在
门外。本模块给出的是**标准规则集自己的一套词汇**：

- `tSpin`：`none` / `tspin`——M0 只判"是不是 T-Spin"（一档）。
  **Mini 与三档区分是 M1**（架构 §7.3.1 纪律 3 · spec §7.5）。
- `classKey`：`none` / `single` / `double` / `triple` / `tetris` /
  `tspin-single` / `tspin-double` / `tspin-triple`——计分表的键。
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
#: 内核不做变换（架构 §7.3.1 纪律 2 / ADR-061）。M0 只填 `down` 轴，
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

#: M0 的唯一候选偏移：恒等偏移。
#:
#: **标准踢墙表不在这里做完**——逐条验证是 M1 的开门判据（plan-M0.md S13
#: 常见坑）。M0 的目标是接口定稿 + 能跑，不是把 M1 的判据提前用掉。
IDENTITY_KICK: tuple[tuple[int, int], ...] = ((0, 0),)

#: 踢墙表：重力轴 → (旋转前朝向, 旋转后朝向) → 候选偏移。
#:
#: 键覆盖**全部 16 种转向**：顺时针 4 种（0→1 / 1→2 / 2→3 / 3→0）、
#: 180° 4 种（0→2 / 1→3 / 2→0 / 3→1）、逆时针 4 种
#: （0→3 / 1→0 / 2→1 / 3→2），外加 4 个**同向**（0→0 / 1→1 / 2→2 / 3→3）
#: ——同向不是旋转（内核不会这样调），但表在朝向上全覆盖，
#: 别在一个无意义调用上炸。
#:
#: M0 的表**与方块类型无关**（恒等偏移对谁都一样）；M1 补标准表时 I / O 需要
#: 各自的偏移，届时的表结构会多一层方块类型——那是 M1 的改动，不是 M0 的。
KICK_TABLE: Mapping[str, Mapping[tuple[int, int], tuple[tuple[int, int], ...]]] = MappingProxyType(
    {
        "down": MappingProxyType(
            {
                (0, 1): IDENTITY_KICK,
                (1, 2): IDENTITY_KICK,
                (2, 3): IDENTITY_KICK,
                (3, 0): IDENTITY_KICK,
                (0, 2): IDENTITY_KICK,
                (1, 3): IDENTITY_KICK,
                (2, 0): IDENTITY_KICK,
                (3, 1): IDENTITY_KICK,
                (0, 3): IDENTITY_KICK,
                (1, 0): IDENTITY_KICK,
                (2, 1): IDENTITY_KICK,
                (3, 2): IDENTITY_KICK,
                (0, 0): IDENTITY_KICK,
                (1, 1): IDENTITY_KICK,
                (2, 2): IDENTITY_KICK,
                (3, 3): IDENTITY_KICK,
            }
        ),
        "left": MappingProxyType({}),
        "right": MappingProxyType({}),
        "up": MappingProxyType({}),
    }
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
#: M0 的最小曲线（1–10 级），按 60 逻辑帧 / 秒的基准（NFR-2）——
#: 1 级即每秒落一格。**表尾之后的等级取表尾值**；完整标准重力曲线是 M1 的事。
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
#: 5 行以上的清行 M0 不另立类别，按 `tetris` 算（见 `standard._class_key`）。
CLEAR_NAMES: Mapping[int, str] = MappingProxyType(
    {1: "single", 2: "double", 3: "triple", 4: "tetris"}
)

#: 计分表：`classKey` → 基础分值。
#:
#: 键是 `classKey` 的取值域（规则集定义的词汇，见模块 docstring）。
SCORE_TABLE: Mapping[str, int] = MappingProxyType(
    {
        "none": 0,
        "single": 100,
        "double": 300,
        "triple": 500,
        "tetris": 800,
        "tspin-single": 400,
        "tspin-double": 800,
        "tspin-triple": 1200,
    }
)

#: 连击奖励：每多一连击加的分。
COMBO_BONUS: int = 50

#: 难消类别键（B2B 的判定依据）：四消与 T-Spin 消行。
DIFFICULT_CLASS_KEYS: frozenset[str] = frozenset(
    {"tetris", "tspin-single", "tspin-double", "tspin-triple"}
)

#: `tSpin` 的取值域（规则集定义的词汇）：M0 只判"是不是 T-Spin"。
TSPIN_VALUES: tuple[str, ...] = ("none", "tspin")

#: `classKey` 的取值域（规则集定义的词汇）。
CLASS_KEYS: tuple[str, ...] = (
    "none",
    "single",
    "double",
    "triple",
    "tetris",
    "tspin-single",
    "tspin-double",
    "tspin-triple",
)

__all__ = [
    "CLASS_KEYS",
    "CLEAR_AXIS_BY_GRAVITY",
    "CLEAR_NAMES",
    "COMBO_BONUS",
    "DIFFICULT_CLASS_KEYS",
    "GRAVITY_INTERVAL_FRAMES",
    "GRAVITY_VECTORS",
    "IDENTITY_KICK",
    "KICK_TABLE",
    "PIECE_TYPES",
    "ROTATION_COUNT",
    "ROTATIONS",
    "SCORE_TABLE",
    "SHAPE_TABLE",
    "TSPIN_VALUES",
]
