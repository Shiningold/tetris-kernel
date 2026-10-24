"""快照契约声明（契约 v1 · 22 个顶层字段 + `board` 5 子字段）。

公共叶子——**不 import 任何 `tetris_kernel.*` 模块，也不依赖第三方**；只用
标准库（架构 §2.3 / §4.1）。本模块只做「声明」，不做「行为」：S8 才在这里的
声明之上实现 `Snapshot` / `Board` / `Active` 三个 frozen dataclass。

## 声明了什么

- `SNAPSHOT_FIELDS`：22 个顶层字段（字段名 → 类型声明）。**这张表是"至少
  包含"**（需求 §8.3）：内核每新增一个状态字段，快照必须同步新增一个字段，
  否则 FR-10.2 的存档恢复缺一块（架构 §5.2）——新增字段在这里加一行即可，
  改既有字段的类型 = major 变更。
- `BOARD_FIELDS`：`board` 子结构的 5 个字段（需求 §8.3 的棋盘子表）。
- `ACTIVE_FIELDS`：`Active` 结构的 4 个字段（需求 §8.3：
  `Active(pieceType, rotation, x, y)`）。
- `COORDINATE_CONVENTIONS`：坐标系约定（契约清单第 4 项）——落在声明里
  便于复核，不留在散文里。
- `KERNEL_STATE_TO_SNAPSHOT`：M0 内核状态清单（spec §7.1，契约清单第 15 项）
  ——内核状态字段与快照字段一一对应，两侧都是机器可读的路径。

## 两个指纹字段必须一眼可辨

`structureFingerprint`（棋盘内容）与 `extensionFingerprint`（扩展槽摘要）
是两个不同的哈希：**结构指纹不含扩展字段**（架构 §5.4），变体局回放校验
拿错了，四项比对会全绿而结果不同。两者命名已按需求原文区分，别合并。

## rngState 的两条构造路径

`snapshot()` 构造路径**恒为 `None`**；存档路径用
`dataclasses.replace(snapshot, rngState=...)` 填（架构 §5.2）。**不在
`frozen=True` 上惰性赋值**；`rngState` 也不进常规只读视图（它是存档用）。
类型是 `tuple[int, ...] | None`，pyright strict 强制消费方处理 `None`。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .types import BOARD_CELLS, INT_TUPLE, NEXT_QUEUE, SCALAR_MAP, VECTOR2

# ---------------------------------------------------------------------------
# 声明结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """快照里的一个字段声明。

    字段：字段名 · 类型表达式 · 中文说明。**字段名即契约**，改名 = major 变更。
    类型表达式文法见 `tetris_kernel.contracts.types`——本模块引用那里的取值域
    常量（`SCALAR_MAP` / `INT_TUPLE` / `BOARD_CELLS`），不复制字面量
    （架构 §4.4 末条）。
    """

    name: str
    type: str
    doc: str


def _field(name: str, type_: str, doc: str) -> FieldSpec:
    """构造字段声明（内部用，省掉重复写 FieldSpec(...)）。"""
    return FieldSpec(name=name, type=type_, doc=doc)


@dataclass(frozen=True)
class KernelStateEntry:
    """内核状态字段 ↔ 快照字段的一行对应（spec §7.1 的机器可读版）。

    字段：

    - `kernel_field`：内核侧的状态字段路径（点分，如 `board.cells`）。**一行一
      字段**——不许把多个字段压进一行（`board.width/height/...`），也不许写
      散文（`active（类型 / 朝向 / x / y，可空）`）。压行和散文都不是"机器可
      读"：机器没法把一行拆成几个源字段，"内核加字段 → 快照必须同步加字段"
      这条纪律就守不住。
    - `snapshot_path`：快照侧的字段路径（点分，如 `board.cells`）。顶层字段写
      字段名，子结构字段写 `结构.成员`（`board.width` / `active.pieceType`）。
    - `derived`：`True` = 快照字段是**算出来的**（结构指纹、扩展摘要、随机源
      的可序列化状态），内核不单独存它；`False` = 内核直接持有这个状态。

    `active.*` 在内核里整体可空（没有活动方块时四个成员都不存在）——可空性
    由快照侧的类型 `Active | None` 承载，不另开一列。
    """

    kernel_field: str
    snapshot_path: str
    derived: bool = False


# ---------------------------------------------------------------------------
# 坐标系约定（契约清单第 4 项，需求 §8.3 的注）
# ---------------------------------------------------------------------------

#: 坐标系（全项目唯一，v1 定死）。`writeCells` 的 `cells` 参数用同一套 (x, y)。
#: `hiddenRows` 用「数量」而不是「可见起始行」——后者有 0 / 1 起始歧义，
#: 一个错了整片错位，且错了很难发现（需求 §8.3 的注）。
COORDINATE_CONVENTIONS: Mapping[str, str] = MappingProxyType(
    {
        "x": "向右为正",
        "y": "向下为正",
        "row0": "棋盘第 0 行是顶部，行索引自上而下递增",
        "cells": "行优先（row-major），与架构 §5.4 的 TLV 编码口径同一条",
        "hiddenRows": "数量（顶部隐藏行数），不是可见起始行",
    }
)


# ---------------------------------------------------------------------------
# board 子结构（5 字段）与 Active 结构（4 字段）
# ---------------------------------------------------------------------------

#: `board` 子结构的字段声明（需求 §8.3 的棋盘子表）。
BOARD_FIELDS: Mapping[str, FieldSpec] = MappingProxyType(
    {
        "width": _field("width", "int", "列数"),
        "height": _field("height", "int", "总行数（**含**隐藏行）"),
        "hiddenRows": _field(
            "hiddenRows",
            "int",
            "顶部隐藏行的**数量**（不是可见起始行）；可见行数 = height − hiddenRows",
        ),
        "cells": _field(
            "cells",
            BOARD_CELLS,
            "格子数组，**行优先**；第 0 行是顶部，行索引自上而下递增",
        ),
        "structureFingerprint": _field(
            "structureFingerprint",
            "str",
            "棋盘内容的确定性哈希（blake2b 十六进制）；**不含扩展字段**——"
            "变体局的扩展槽另比对 extensionFingerprint（架构 §5.4）",
        ),
    }
)

#: `Active` 结构的字段声明（需求 §8.3：`Active(pieceType, rotation, x, y)`）。
#: 快照顶层 `active` 的类型是 `Active | None`——`None` 表示等待出生。
ACTIVE_FIELDS: Mapping[str, FieldSpec] = MappingProxyType(
    {
        "pieceType": _field("pieceType", "str", "方块类型"),
        "rotation": _field("rotation", "int", "当前朝向"),
        "x": _field("x", "int", "横坐标（x 向右为正）"),
        "y": _field("y", "int", "纵坐标（y 向下为正）"),
    }
)

#: **命名结构注册表**在 `tetris_kernel.contracts.structures`（类型文法形态 9）：
#: 它是**类型文法层面**的东西——快照的 `Board` / `Active`、放置的 `PlacementInfo`、
#: 规则的五个结构共用同一张表，所以它住在自己的模块里，不寄居在快照模块。
#: 结构名 → 成员表；成员表改名这里立刻跟着改。


# ---------------------------------------------------------------------------
# 顶层快照字段（21 个，需求 §8.3）
# ---------------------------------------------------------------------------

#: 顶层快照字段注册表：键 = 字段名，值 = 该字段的声明。**"至少包含"这张表**：
#: 内核加状态字段，这里同步加字段（契约清单第 15 项）。
#:
#: **不留可变别名**：字面量直接包进 `MappingProxyType`——留着底层 dict 等于在
#: "声明面"开一个不打算走基线的旁门（改动它，公开表会跟着变，基线却不报）。
SNAPSHOT_FIELDS: Mapping[str, FieldSpec] = MappingProxyType(
    {
        "version": _field(
            "version",
            "int",
            "状态版本号，每次变更递增；渲染可据此跳过重复绘制。"
            "**`reset` 后归零**——`reset` 把整局状态归零（帧号 / 帧内序号 / 版本号"
            "三件套一起归，见内核的 `reset` 口径），版本号跟着一起归",
        ),
        "rule": _field("rule", "str", "规则集标识，用于区分标准玩法与变体；录像与存档均须携带"),
        "phase": _field("phase", "str", "生命周期状态：待开始 / 运行 / 暂停 / 结束"),
        "frame": _field("frame", "int", "逻辑帧号（宿主逻辑帧，暂停期照走）"),
        "board": _field("board", "Board", "棋盘子结构，成员见 BOARD_FIELDS"),
        "active": _field(
            "active", "Active | None", "活动方块，成员见 ACTIVE_FIELDS；None 表示等待出生"
        ),
        "ghostDelta": _field(
            "ghostDelta",
            VECTOR2,
            "幽灵落点相对活动方块的**位移向量** (dx, dy)。标准玩法恒为 (0, n)；"
            "重力可旋转等变体下 dx 亦可为非零，故不能用单一纵坐标表达",
        ),
        "hold": _field("hold", "str | None", "暂存的方块类型"),
        "holdUsed": _field("holdUsed", "bool", "本块是否已用过暂存"),
        "next": _field(
            "next",
            NEXT_QUEUE,
            "当前这一袋剩下的全部方块（最多 7 个 = 方块种类数）；"
            "**渲染层取前 5 个当预览**。放全袋是为了存档恢复后取片序列不分叉"
            "（FR-10.2）",
        ),
        "score": _field("score", "int", "当前分数"),
        "lines": _field("lines", "int", "累计消行数"),
        "level": _field("level", "int", "当前等级"),
        "combo": _field("combo", "int", "当前连击数"),
        "b2b": _field("b2b", "bool", "当前背面连击状态"),
        "piecesPlaced": _field("piecesPlaced", "int", "已放置方块数"),
        "grounded": _field("grounded", "bool", "是否已落地"),
        "lockTimer": _field("lockTimer", "int", "锁定计时剩余帧数（用于 UI 提示）"),
        "gravityTimer": _field(
            "gravityTimer",
            "int",
            "重力计时剩余帧数：距下一次落格还有几帧（**0 = 本帧到期**）。"
            "间隔由规则集的 `gravity_interval(ctx)` 给（标准 1 级 = 60 帧落一格）；"
            "**随方块出生重置**——新方块先等满一个间隔才落第一格。"
            "**必须进快照**：它是跨帧的节奏状态，存档恢复后要接着数（FR-10.2）",
        ),
        "extensions": _field(
            "extensions",
            SCALAR_MAP,
            "变体扩展槽：只放标量，key 唯一，内核不理解内容；重复 key 由宿主校验",
        ),
        "extensionFingerprint": _field(
            "extensionFingerprint",
            "str",
            "extensions 的规范化摘要（blake2b 十六进制），与 extensions 同处构造；"
            "**与 structureFingerprint（棋盘内容）是两个哈希**，变体局回放校验的第五项",
        ),
        "rngState": _field(
            "rngState",
            f"{INT_TUPLE} | None",
            "随机源内部状态（存档用，保证恢复后序列连续）；"
            "**snapshot() 构造路径恒为 None**，存档路径用 dataclasses.replace 填",
        ),
    }
)

#: 顶层快照字段名的稳定序列（枚举顺序 = 需求 §8.3 的表格顺序）。
SNAPSHOT_FIELD_NAMES: tuple[str, ...] = tuple(SNAPSHOT_FIELDS)


# ---------------------------------------------------------------------------
# M0 内核状态清单（契约清单第 15 项，spec §7.1）
# ---------------------------------------------------------------------------

#: 内核状态字段 → 快照字段的对应表（spec §7.1 的机器可读版）。
#:
#: **快照 = 内核状态全集**：内核新增一个状态字段，快照必须同步新增一个字段，
#: 否则 FR-10.2 的存档恢复缺一块。**一行一字段**——`kernel_field` 是内核侧
#: 的字段路径，`snapshot_path` 是快照侧的字段路径（子结构成员写 `board.width`
#: 这种点分路径）；`derived` 标出"快照侧是算出来的"那几条。
#:
#: 两侧都是机器可读的，所以"内核加一个状态字段 → 快照必须同步加一个字段"
#: 这条纪律**能反过来守**：S8 落地内核状态时，把内核真实持有的字段与
#: `kernel_field` 的集合逐条比对，多一个就说明快照漏了。
#: **随实现同步**：S8 落地内核状态时逐行复核。
KERNEL_STATE_TO_SNAPSHOT: tuple[KernelStateEntry, ...] = (
    KernelStateEntry("version", "version"),
    KernelStateEntry("ruleId", "rule"),
    KernelStateEntry("phase", "phase"),
    KernelStateEntry("frame", "frame"),
    KernelStateEntry("board.width", "board.width"),
    KernelStateEntry("board.height", "board.height"),
    KernelStateEntry("board.hiddenRows", "board.hiddenRows"),
    KernelStateEntry("board.cells", "board.cells"),
    KernelStateEntry("board.cells", "board.structureFingerprint", derived=True),
    KernelStateEntry("active.pieceType", "active.pieceType"),
    KernelStateEntry("active.rotation", "active.rotation"),
    KernelStateEntry("active.x", "active.x"),
    KernelStateEntry("active.y", "active.y"),
    KernelStateEntry("ghostDelta", "ghostDelta"),
    KernelStateEntry("hold", "hold"),
    KernelStateEntry("holdUsed", "holdUsed"),
    KernelStateEntry("nextQueue", "next"),
    KernelStateEntry("score", "score"),
    KernelStateEntry("lines", "lines"),
    KernelStateEntry("level", "level"),
    KernelStateEntry("combo", "combo"),
    KernelStateEntry("b2b", "b2b"),
    KernelStateEntry("piecesPlaced", "piecesPlaced"),
    KernelStateEntry("grounded", "grounded"),
    KernelStateEntry("lockTimer", "lockTimer"),
    KernelStateEntry("gravityTimer", "gravityTimer"),
    KernelStateEntry("extensions", "extensions"),
    KernelStateEntry("extensions", "extensionFingerprint", derived=True),
    # 随机源**实例本体**是运行时状态（见 `RUNTIME_ONLY_STATE`），快照只带走
    # 它的可序列化状态——所以这一条是派生的，不是内核另存的一份字段。
    KernelStateEntry("rngInstance", "rngState", derived=True),
)

#: **不进快照的运行时状态**（spec §7.1）：标识符 → 中文说明。
#:
#: 它们只活在当前进程，存档与渲染都不需要——事件序号（总线的唯一时间轴）、
#: 轮次计数、命令数计数、同因拒绝合并表、订阅注册表、随机源实例本体（快照只
#: 带走它的可序列化状态，见 `KERNEL_STATE_TO_SNAPSHOT` 里 `rngInstance` 那条）。
#:
#: **用英文标识符，不用中文散文**：守卫要真的守得住，就得和快照字段、
#: 内核状态字段**同一套命名体系**——两边都是英文标识符，"跑错的东西混进
#: 快照"才可能被机器发现；写成中文，那条断言恒真，是摆设。
RUNTIME_ONLY_STATE: Mapping[str, str] = MappingProxyType(
    {
        "eventSequence": "事件序号（总线的唯一时间轴）",
        "roundCounter": "轮次计数",
        "commandCounter": "命令数计数",
        "rejectCoalescing": "同因拒绝合并表",
        "subscriberRegistry": "订阅注册表",
        "rngInstance": "随机源实例本体（快照只带走它的可序列化状态）",
    }
)

__all__ = [
    "ACTIVE_FIELDS",
    "BOARD_FIELDS",
    "COORDINATE_CONVENTIONS",
    "KERNEL_STATE_TO_SNAPSHOT",
    "RUNTIME_ONLY_STATE",
    "SNAPSHOT_FIELDS",
    "SNAPSHOT_FIELD_NAMES",
    "FieldSpec",
    "KernelStateEntry",
]
