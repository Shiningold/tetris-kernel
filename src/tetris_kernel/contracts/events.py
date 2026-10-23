"""事件契约声明（契约 v1 · 44 条事件 + 信封 + 元数据）。

公共叶子——**不 import 任何 `tetris_kernel.*` 模块，也不依赖第三方**；只用标准库
（架构 §2.3 / §4.1）。本模块只做"声明"，不做"行为"：没有任何 dataclass 之外的
运行时对象被构造出来。

## 为什么字段类型用字符串

负载字段的类型声明写成**规范类型表达式字符串**（`"int"`、`"tuple[int, int]"`、
`"tuple[tuple[str, int], ...]"`），而不是 `typing` 对象：

- **哈希要跨进程稳定**。契约基线（架构 §4.4）按字段级哈希类型声明，
  `hash(int)` 是按对象地址算的，跨进程必变；`typing` 对象的 `repr` 也会随
  Python 版本漂移。规范字符串没有这两个问题。
- **基线校验脚本要能解析它**（S6）：字符串可直接正则解析，也能直接写进 JSON。

表达式的文法（只有这几种形态，禁止自由发挥）：

| 形态 | 写法 |
| --- | --- |
| 标量 | `int` · `str` · `bool` · `float` |
| 可空 | `T | None` |
| 定长元组 | `tuple[T1, T2]` |
| 变长元组 | `tuple[T, ...]` |
| 嵌套元组 | 逐层展开，如 `tuple[tuple[int, int], ...]` |
| 递归元组 | `tuple[...]`——表示"元素结构与外层同构的嵌套元组"，仅用于开放取值域 |

## 禁浮点只约束命令 `args`

`args` 参与**重放**（参数不能漂），所以取值域里没有 `float`（B-7 / ADR-025）。
事件负载不受这条约束——`render:frame` 的耗时、`metric:sampled` 的聚合值
本来就带小数，按需求原文用 `float`。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

# ---------------------------------------------------------------------------
# 通用取值域
# ---------------------------------------------------------------------------

#: 任意 JSON 兼容取值（含浮点）：用于信封的负载 / 元数据容器。
JSON_VALUE = "int | str | bool | float | None | tuple[...]"

#: 命令 `args` 的取值域：**禁浮点**（B-7 / ADR-025），只含 JSON 标量与元组。
#: 变体写法按契约 v1 的 16 条命令取并集：`setLevel` 的 int、`injectGarbage`
#: 的 int 与 `tuple[int, ...]`、`writeCells` 的 `tuple[tuple[int, int, int], ...]`。
ARGS_VALUE = "int | str | bool | None | tuple[int, ...] | tuple[tuple[int, int, int], ...]"

#: 命令 `args` 的容器类型：字段名 → 取值，顺序稳定（不可变元组）。
ARGS = f"tuple[tuple[str, {ARGS_VALUE}], ...]"

#: 扁平 JSON 标量映射（插件配置、日志数据）：字段名 → 标量。
SCALAR_MAP = "tuple[tuple[str, int | float | str | bool | None], ...]"

#: 格子清单：`(x, y, 新值)`。
CELLS = "tuple[tuple[int, int, int], ...]"

#: 格子坐标清单：`(x, y)`。
COORD_CELLS = "tuple[tuple[int, int], ...]"


# ---------------------------------------------------------------------------
# 声明结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PayloadField:
    """负载里的一个字段声明。

    字段：字段名 · 类型表达式 · 中文说明。**字段名即契约**，改名 = major 变更。
    """

    name: str
    type: str
    doc: str


@dataclass(frozen=True)
class EventSpec:
    """一个事件类型的声明。

    字段：命名空间 · 产出者 · 负载字段。**命名空间 ≠ 产出者**——
    `kernel:` 前缀说的是"这件事属于内核实例"，不必然是内核干的（ADR-063）。
    """

    namespace: str
    producer: str
    payload: tuple[PayloadField, ...]


def _spec(
    namespace: str,
    producer: str,
    payload: tuple[PayloadField, ...],
) -> EventSpec:
    """构造事件声明（内部用，省掉重复写 EventSpec(...)）。"""
    return EventSpec(namespace=namespace, producer=producer, payload=payload)


# ---------------------------------------------------------------------------
# 信封与元数据（契约清单第 1、2 项）
# ---------------------------------------------------------------------------

#: 信封五字段（需求 §8 / 架构 §5.1）。**不设逻辑时间戳**（ADR-062）——
#: 逻辑时间 = 帧号 × 步长，派生即可。
ENVELOPE_FIELDS: tuple[PayloadField, ...] = (
    PayloadField("seq", "int", "全局单调序号，总线的唯一权威分配（架构 §4.2）"),
    PayloadField("frame", "int", "宿主逻辑帧号；暂停期照走（架构 §6.1）"),
    PayloadField("type", "str", "事件类型名，与本注册表的键一致"),
    PayloadField(
        "payload", f"tuple[tuple[str, {JSON_VALUE}], ...]", "事件负载，字段见对应事件声明"
    ),
    PayloadField(
        "metadata", f"tuple[tuple[str, {JSON_VALUE}], ...]", "事件元数据，见 METADATA_KEYS"
    ),
)

#: 元数据三键（v1 定死，ADR-062）。未知键静默保留但不解释（NFR-9）。
METADATA_KEYS: tuple[PayloadField, ...] = (
    PayloadField(
        "source",
        "str",
        "命令来源：human / ai / replay / tutorial / network / test；内核不得因它分支",
    ),
    PayloadField("producer", "str", "产出者，由总线在分发时自动填，与事件声明的 producer 一致"),
    PayloadField(
        "cause",
        "tuple[int, int] | None",
        "引发本事件的那条命令的 (帧号, 帧内序号)；None = 无上游命令",
    ),
)


# ---------------------------------------------------------------------------
# 事件目录（44 条，需求 §8.1）
# ---------------------------------------------------------------------------

_EVENT_SPECS: tuple[tuple[str, EventSpec], ...] = (
    # ---------------- kernel（5 条） ----------------
    (
        "kernel:ready",
        _spec(
            "kernel",
            "kernel",
            (
                PayloadField("seed", "int", "内核随机源种子"),
                PayloadField("rule", "str", "规则集标识，用于区分标准玩法与变体"),
                PayloadField("mode", "str", "运行模式，如 live / replay"),
            ),
        ),
    ),
    (
        "kernel:system:installed",
        _spec(
            "kernel",
            "host",
            (
                PayloadField("name", "str", "插件名"),
                PayloadField(
                    "config", SCALAR_MAP, "扁平 JSON 标量配置，不可序列化对象一律不进事件"
                ),
            ),
        ),
    ),
    (
        "kernel:system:removed",
        _spec(
            "kernel",
            "host",
            (
                PayloadField("name", "str", "插件名"),
                PayloadField("clean", "bool", "卸载是否干净；不干净记错，但不占用 kernel:error"),
            ),
        ),
    ),
    (
        "kernel:error",
        _spec(
            "kernel",
            "bus",
            (
                PayloadField("subscriber", "str", "抛错的订阅者名"),
                PayloadField("eventType", "str", "原事件类型"),
                PayloadField("eventSeq", "int", "原事件的全局序号"),
                PayloadField("error", "str", "错误摘要"),
            ),
        ),
    ),
    (
        "kernel:quota-exceeded",
        _spec(
            "kernel",
            "kernel",
            (
                PayloadField(
                    "quota", "str", "配额名，如 commandCount / rounds / writeCellsPerFrame"
                ),
                PayloadField("limit", "int", "限额"),
                PayloadField("actual", "int", "实际值"),
            ),
        ),
    ),
    # ---------------- command（3 条） ----------------
    (
        "command:received",
        _spec(
            "command",
            "kernel",
            (
                PayloadField("commandType", "str", "命令类型"),
                PayloadField("source", "str", "命令来源标记，随事件透传"),
                PayloadField("args", ARGS, "命令参数，不可变；只含 JSON 标量与元组，禁浮点"),
            ),
        ),
    ),
    (
        "command:accepted",
        _spec(
            "command",
            "kernel",
            (
                PayloadField("commandType", "str", "命令类型"),
                PayloadField("commandSeq", "int", "关联的帧内序号（只对被接受的命令编号）"),
                PayloadField("args", ARGS, "命令参数，不可变；与 received 一致"),
            ),
        ),
    ),
    (
        "command:rejected",
        _spec(
            "command",
            "kernel",
            (
                PayloadField("commandType", "str", "命令类型"),
                PayloadField("reason", "str", "拒绝原因，取 12 值枚举之一"),
                PayloadField("args", ARGS, "命令参数，不可变；拒绝不改状态但参数必须齐全"),
                PayloadField("mergedCount", "int", "本次代表多少次同因拒绝，起始值 1"),
            ),
        ),
    ),
    # ---------------- game（6 条） ----------------
    (
        "game:created",
        _spec(
            "game",
            "kernel",
            (
                PayloadField("seed", "int", "随机源种子"),
                PayloadField("rule", "str", "规则集标识"),
            ),
        ),
    ),
    (
        "game:started",
        _spec(
            "game",
            "kernel",
            (
                PayloadField("level", "int", "开局等级"),
                PayloadField("seed", "int", "随机源种子"),
            ),
        ),
    ),
    (
        "game:paused",
        _spec("game", "kernel", (PayloadField("phase", "str", "当前生命周期状态"),)),
    ),
    (
        "game:resumed",
        _spec("game", "kernel", (PayloadField("phase", "str", "当前生命周期状态"),)),
    ),
    (
        "game:over",
        _spec(
            "game",
            "kernel",
            (
                PayloadField("score", "int", "结束时的分数"),
                PayloadField("lines", "int", "结束时的累计消行数"),
                PayloadField("reason", "str", "结束原因"),
            ),
        ),
    ),
    (
        "game:reset",
        _spec("game", "kernel", (PayloadField("seed", "int", "重置后的随机源种子"),)),
    ),
    # ---------------- clock（2 条） ----------------
    (
        "clock:tick",
        _spec(
            "clock",
            "kernel",
            (
                PayloadField("frame", "int", "逻辑帧号"),
                PayloadField("stepMs", "int", "固定步长（毫秒），逻辑时间 = 帧号 × 步长"),
            ),
        ),
    ),
    (
        "clock:gravity",
        _spec(
            "clock",
            "kernel",
            (
                PayloadField("pieceType", "str", "下落方块类型"),
                PayloadField("x", "int", "重力作用后的横坐标"),
                PayloadField("y", "int", "重力作用后的纵坐标"),
                PayloadField("dx", "int", "本次重力位移的横向分量"),
                PayloadField("dy", "int", "本次重力位移的纵向分量"),
            ),
        ),
    ),
    # ---------------- input（2 条） ----------------
    (
        "input:raw",
        _spec(
            "input",
            "input-adapter",
            (
                PayloadField("action", "str", "原始动作名"),
                PayloadField("kind", "str", "按下 / 抬起"),
            ),
        ),
    ),
    (
        "input:intent",
        _spec(
            "input",
            "input-adapter",
            (
                PayloadField("action", "str", "翻译前的动作名"),
                PayloadField("commandType", "str", "对应派发的命令类型"),
            ),
        ),
    ),
    # ---------------- rng（1 条） ----------------
    (
        "rng:next",
        _spec(
            "rng",
            "kernel",
            (
                PayloadField(
                    "value", "float | int", "随机取值；random() 得浮点，getrandbits() 得整数"
                ),
                PayloadField("seq", "int", "取值序号"),
                PayloadField("stateFingerprint", "str", "随机源状态的短哈希，不含完整 MT 状态"),
            ),
        ),
    ),
    # ---------------- piece（11 条） ----------------
    (
        "piece:spawned",
        _spec(
            "piece",
            "kernel",
            (
                PayloadField("pieceType", "str", "方块类型"),
                PayloadField("x", "int", "出生横坐标"),
                PayloadField("y", "int", "出生纵坐标"),
                PayloadField("next", "tuple[str, ...]", "预览队列"),
            ),
        ),
    ),
    (
        "piece:moved",
        _spec(
            "piece",
            "kernel",
            (
                PayloadField("fromX", "int", "移动起始横坐标"),
                PayloadField("fromY", "int", "移动起始纵坐标"),
                PayloadField("toX", "int", "移动目标横坐标"),
                PayloadField("toY", "int", "移动目标纵坐标"),
                PayloadField("blocked", "bool", "是否受阻；受阻时实际未移动"),
                PayloadField("cause", "str", "成因，如 command / gravity / softDrop"),
            ),
        ),
    ),
    (
        "piece:rotated",
        _spec(
            "piece",
            "kernel",
            (
                PayloadField("fromRotation", "int", "旋转前朝向"),
                PayloadField("toRotation", "int", "旋转后朝向"),
                PayloadField("direction", "str", "旋转方向，如 cw / ccw / 180"),
                PayloadField("blocked", "bool", "是否受阻；受阻时实际未旋转"),
            ),
        ),
    ),
    (
        "piece:kicked",
        _spec(
            "piece",
            "kernel",
            (
                PayloadField("kickIndex", "int", "命中的踢墙偏移序号"),
                PayloadField("offsetX", "int", "踢墙偏移的横向分量"),
                PayloadField("offsetY", "int", "踢墙偏移的纵向分量"),
            ),
        ),
    ),
    (
        "piece:softdrop",
        _spec(
            "piece",
            "kernel",
            (
                PayloadField("pieceType", "str", "方块类型"),
                PayloadField("x", "int", "软降后横坐标"),
                PayloadField("y", "int", "软降后纵坐标"),
            ),
        ),
    ),
    (
        "piece:harddrop",
        _spec(
            "piece",
            "kernel",
            (
                PayloadField("pieceType", "str", "方块类型"),
                PayloadField("distance", "int", "下落距离"),
                PayloadField("x", "int", "落点横坐标"),
                PayloadField("y", "int", "落点纵坐标"),
                PayloadField("cells", COORD_CELLS, "落点占据的格子坐标 (x, y)"),
            ),
        ),
    ),
    (
        "piece:locked",
        _spec(
            "piece",
            "kernel",
            (
                PayloadField("pieceType", "str", "方块类型"),
                PayloadField("rotation", "int", "锁定时的朝向"),
                PayloadField("x", "int", "锁定位置横坐标"),
                PayloadField("y", "int", "锁定位置纵坐标"),
                PayloadField("cells", COORD_CELLS, "占据的格子坐标 (x, y)"),
                PayloadField("tSpin", "str", "T-Spin 标记，取值域由规则集定义"),
                PayloadField("cause", "str", "成因，如 ground / hardDrop"),
            ),
        ),
    ),
    (
        "piece:held",
        _spec(
            "piece",
            "kernel",
            (
                PayloadField("stored", "str", "存入暂存槽的方块类型"),
                PayloadField("taken", "str", "取出暂存槽的方块类型"),
            ),
        ),
    ),
    (
        "piece:hold-denied",
        _spec("piece", "kernel", (PayloadField("reason", "str", "暂存被拒原因"),)),
    ),
    (
        "piece:lock-delay-start",
        _spec("piece", "kernel", (PayloadField("delayFrames", "int", "锁定延迟的逻辑帧数"),)),
    ),
    (
        "piece:lock-delay-reset",
        _spec("piece", "kernel", (PayloadField("resetCount", "int", "已重置次数"),)),
    ),
    # ---------------- board（4 条） ----------------
    (
        "board:changed",
        _spec(
            "board",
            "kernel",
            (
                PayloadField("cells", CELLS, "变化格子清单 (x, y, 新值)"),
                PayloadField(
                    "cause", "str", "成因，如 lock / clear / collapse / writeCells / garbage"
                ),
            ),
        ),
    ),
    (
        "board:lines-cleared",
        _spec(
            "board",
            "kernel",
            (
                PayloadField("rows", "tuple[int, ...]", "被消行的行号"),
                PayloadField("count", "int", "消行数"),
                PayloadField("tSpin", "str", "T-Spin 标记"),
                PayloadField("classKey", "str", "计分类别键，如 single / double / triple / tetris"),
            ),
        ),
    ),
    (
        "board:collapsed",
        _spec(
            "board", "kernel", (PayloadField("cells", CELLS, "塌落后变化的格子清单 (x, y, 新值)"),)
        ),
    ),
    (
        "board:garbage-received",
        _spec(
            "board",
            "kernel",
            (
                PayloadField("rows", "int", "注入的垃圾行数"),
                PayloadField("holes", "tuple[int, ...]", "空洞所在列"),
            ),
        ),
    ),
    # ---------------- queue（1 条） ----------------
    (
        "queue:refilled",
        _spec("queue", "kernel", (PayloadField("next", "tuple[str, ...]", "补充后的预览队列"),)),
    ),
    # ---------------- score（3 条） ----------------
    (
        "score:changed",
        _spec(
            "score",
            "kernel",
            (
                PayloadField("delta", "int", "分数增量"),
                PayloadField("before", "int", "变化前分数"),
                PayloadField("after", "int", "变化后分数"),
                PayloadField("cause", "str", "成因，如 clear / softDrop"),
            ),
        ),
    ),
    (
        "score:combo-changed",
        _spec("score", "kernel", (PayloadField("combo", "int", "当前连击数"),)),
    ),
    (
        "score:b2b-changed",
        _spec("score", "kernel", (PayloadField("b2b", "bool", "当前背面连击状态"),)),
    ),
    # ---------------- progress（1 条） ----------------
    (
        "progress:level-changed",
        _spec(
            "progress",
            "kernel",
            (
                PayloadField("fromLevel", "int", "原等级"),
                PayloadField("toLevel", "int", "新等级"),
                PayloadField("gravityInterval", "int", "新等级对应的重力间隔（逻辑帧数）"),
            ),
        ),
    ),
    # ---------------- state（1 条） ----------------
    (
        "state:changed",
        _spec(
            "state",
            "kernel",
            (
                PayloadField("version", "int", "状态版本号，每次变更递增"),
                PayloadField("phase", "str", "生命周期状态"),
                PayloadField("score", "int", "当前分数"),
                PayloadField("lines", "int", "累计消行数"),
                PayloadField("level", "int", "当前等级"),
                PayloadField("combo", "int", "当前连击数"),
                PayloadField("b2b", "bool", "当前背面连击状态"),
                PayloadField("piecesPlaced", "int", "已放置方块数"),
                PayloadField("activeType", "str | None", "活动方块类型；None 表示等待出生"),
                PayloadField("hold", "str | None", "暂存的方块类型"),
                PayloadField("holdUsed", "bool", "本块是否已用过暂存"),
                PayloadField("next", "tuple[str, ...]", "预览队列"),
            ),
        ),
    ),
    # ---------------- render（1 条） ----------------
    (
        "render:frame",
        _spec(
            "render",
            "renderer",
            (
                PayloadField("frame", "int", "渲染的帧号"),
                PayloadField("durationUs", "int", "渲染耗时（微秒整数，避免浮点）"),
            ),
        ),
    ),
    # ---------------- log（1 条） ----------------
    (
        "log:entry",
        _spec(
            "log",
            "log",
            (
                PayloadField("level", "str", "日志级别"),
                PayloadField("channel", "str", "日志频道"),
                PayloadField("message", "str", "日志消息"),
                PayloadField("data", SCALAR_MAP, "附加数据，扁平 JSON 标量"),
            ),
        ),
    ),
    # ---------------- metric（1 条） ----------------
    (
        "metric:sampled",
        _spec(
            "metric",
            "metrics",
            (
                PayloadField("frame", "int", "采样对应的帧号"),
                PayloadField(
                    "metrics", "tuple[tuple[str, int | float], ...]", "本次采样的全部聚合指标"
                ),
            ),
        ),
    ),
    # ---------------- achievement（1 条） ----------------
    (
        "achievement:unlocked",
        _spec(
            "achievement",
            "achievements",
            (
                PayloadField("id", "str", "成就编号"),
                PayloadField("title", "str", "成就标题"),
                PayloadField("triggerType", "str", "触发该成就的事件类型"),
                PayloadField("triggerSeq", "int", "触发该成就的事件序号"),
            ),
        ),
    ),
)

#: 事件类型注册表：键 = 事件类型名，值 = 该类型的声明（ADR-020）。
EVENT_TYPES: Mapping[str, EventSpec] = MappingProxyType(dict(_EVENT_SPECS))


# ---------------------------------------------------------------------------
# 产出者
# ---------------------------------------------------------------------------

#: 产出者代号 → 需求 §8.1「产出者」列的中文名。两边必须逐条相等。
PRODUCER_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "kernel": "内核",
        "bus": "总线",
        "host": "host",
        "input-adapter": "输入适配器",
        "renderer": "渲染器",
        "log": "日志系统",
        "metrics": "指标系统",
        "achievements": "成就系统",
    }
)


def namespace_of(event_type: str) -> str:
    """取事件类型所属的命名空间（类型名第一个冒号之前的部分）。"""
    return event_type.split(":", 1)[0]
