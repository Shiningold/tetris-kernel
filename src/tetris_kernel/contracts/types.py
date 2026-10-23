r"""契约层共用的类型取值域。

这些常量**不属于任何单一事件**——命令 `args`（S3）、快照字段（S4）、规则签名
（S5）都要用。放在这里而不是 `events.py` 里，是为了让"事件负载声明"与
"通用类型域"两件事各归其位：`events.py` 只描述事件，类型域在这里。

公共叶子——**不 import 任何 `tetris_kernel.*` 模块，也不依赖第三方**。

## 为什么字段类型用字符串

类型声明写成**规范类型表达式字符串**（`"int"`、`"tuple[int, int]"`、
`"tuple[tuple[str, int], ...]"`），而不是 `typing` 对象：

- **哈希要跨进程稳定**。契约基线（架构 §4.4）按字段级哈希类型声明，
  `hash(int)` 是按对象地址算的，跨进程必变；`typing` 对象的 `repr` 也会随
  Python 版本漂移。规范字符串没有这两个问题。
- **基线校验脚本要能解析它**（S6）：字符串可直接解析，也能直接写进 JSON。

## 类型表达式的文法

下面是**封闭文法**——只有这几种形态，禁止自由发挥。S6 的基线脚本按这张表写
解析器（实作是**递归下降**解析器，不是正则——正则表达不了嵌套元组与联合），
表里没有的形态一律视为**声明不合规**（而不是"尽量解析"）。

| # | 形态 | 写法 | 例 |
| --- | --- | --- | --- |
| 1 | 标量 | `int` · `str` · `bool` · `float` | `int` |
| 2 | 联合 | `T1 \| T2`（成员 ≥ 2；成员可为形态 1–9 的任意组合） | `float \| int` |
| 3 | 可空 | `T \| None`（联合恰两项，`None` 是末项） | `str \| None` |
| 4 | 定长元组 | `tuple[T1, T2]` | `tuple[int, int]` |
| 5 | 变长元组 | `tuple[T, ...]` | `tuple[str, ...]` |
| 6 | 嵌套元组 | 逐层展开 | `tuple[tuple[int, int], ...]` |
| 7 | 元组元素位含联合 | 元素的类型本身是联合 | `tuple[tuple[str, int \| float], ...]` |
| 8 | 递归元组 | `tuple[...]` | `tuple[...]` |
| 9 | 命名结构 | `Board` · `Active`（大写开头的标识符） | `Board` |
| 10 | 只读序列 | `Sequence[T]`（T 为形态 1–10，可再嵌套） | `Sequence[Sequence[int]]` |

**取值域也是形态 2**：像 `ARGS_VALUE` / `JSON_VALUE` 这样的取值域是"标量 + 元组
形态"的联合，**不是新的形态**。

**五条附加规则**：

1. **形态 2 与 3 分开**：`float | int` 是联合，`str | None` 是可空。`None` 表示
   JSON null，**可以作为联合的普通成员**（与其它成员并列，如
   `int | str | bool | None | tuple[int, ...]`）；形态 3「可空」特指那种写法——
   联合恰两项、`None` 在末位，表示"该类型的可空版本"。**两种写法都要认**：
   前者是"这几类之一"，后者是"这个类型可为 null"——把后者的成员顺序套到前者
   身上会读成"前面的类型可空"，那是误读。
2. **形态 7 允许任意深度的嵌套**：元组的元素位可以是形态 1–7 的任意组合，
   如 `tuple[tuple[str, int | str | bool | None | tuple[int, ...]], ...]`。
3. **形态 8 只用于开放取值域**——元素结构与外层同构的嵌套元组，目前只有
   信封的 `payload` / `metadata` 容器用它。
4. **形态 9 只用于 `contracts/` 内登记过的命名结构**——快照的 `Board` / `Active`
   （需求 §8.3 就是这么写的：`board: Board`、`active: Active | None`），以及规则侧的
   六个结构。命名结构
   **不是自由命名的别名**：成员表必须在 `contracts/` 内另行声明
   （`snapshot.py` 的 `BOARD_FIELDS` / `ACTIVE_FIELDS`），并登记在
   `structures.py` 的 `NAMED_STRUCTS`（结构名 → 成员表）里，S6 解析器把它当
   引用处，哈希按成员表展开计算——**`NAMED_STRUCTS` 里没登记的结构名一律
   不合规**（回验从 `NAMED_STRUCTS` 推导结构名集合，不另抄一份名单）。
   **形态 9 是 S4 落地快照声明时补的**（此前 S3 曾把"加第 9 形态"记为 S6
   的决定范围，那指的是"字面量枚举"形态；命名结构是 S4 自己的声明刚需，
   不补就得写文法认不出的串）。
   **S5 又把形态 9 的适用范围从快照扩到规则侧**：`RuleContext` /
   `PlacementClass` / `ClearGroup` / `ScoreState` / `ScoreDelta` 与
   `PlacementInfo` 同样是命名结构，同样登记在同一张注册表里——注册表是
   **类型文法层面**的东西，不是快照模块的私产。
5. **形态 10 只用于规则接口的棋盘参数与消行组序列**：内核内部棋盘是可变结构
   `list[list[int]]`（架构 §7.1），规则只读。写成 `tuple[tuple[int, ...], ...]`
   会逼内核在**每次调用规则接口时**深拷贝一份棋盘——消行判定是逐方块调用的
   热路径，拷贝会把规则模块变成性能瓶颈。`Sequence[T]` 表达的是"只读视图"。
   **形态 10 是 S5 落地规则签名时补的**（架构 §7.3.1 的签名表原文写的就是
   `Sequence[Sequence[int]]`）。仓内形态 10 的**串只有两条**
   （`types.BOARD_VIEW` 与 `Sequence[ClearGroup]`），**引用处三处**：
   `scan_clears` / `classify_placement` 的 `board` 参数与 `score_clear` 的
   `groups`。

**联合的成员顺序不参与语义，但一经写入不得重排**——它会进基线哈希，重排
等于"已有键的哈希变化"。
"""

from __future__ import annotations

#: 任意 JSON 兼容取值（含浮点）：用于信封的负载 / 元数据容器（形态 1 + 2 + 8）。
JSON_VALUE = "int | str | bool | float | None | tuple[...]"

#: 格子清单：`(x, y, 新值)`。
CELLS = "tuple[tuple[int, int, int], ...]"

#: 整数清单：列号 / 行号等一维整数序列。
INT_TUPLE = "tuple[int, ...]"

#: 预览队列：方块类型的清单。
#:
#: 快照的 `next` 与三个事件的 `next` 负载（`piece:spawned` · `queue:refilled` ·
#: `state:changed`）是**同一份形状**，所以引用同一个常量——**引用处一律用
#: 常量，不复制字面量**（架构 §4.4 末条）：复制字面量的后果是常量改了、声明
#: 没跟着改，两处静默分叉，基线却显示全绿。
NEXT_QUEUE = "tuple[str, ...]"

#: 命令 `args` 的取值域：**禁浮点**（B-7 / ADR-025），只含 JSON 标量与元组。
#: 覆盖契约 v1 的 16 条命令：`setLevel` 的 int、`injectGarbage` 的 int 与
#: `INT_TUPLE`、`writeCells` 的 `CELLS`。
#:
#: **取值域是"命令通道允许什么"，不是"v1 的命令用到了什么"**——`str` 与
#: `bool` 在契约 v1 的 16 条命令里没被用到，是**有意的余量**：变体新增命令时
#: 可能要带字符串或布尔，而通道的取值域一旦收窄，加命令就得改契约（major 变更）。
#:
#: **引用处一律用常量，不复制字面量**（架构 §4.4 末条）——这里的成员就是下面
#: 两个常量，声明侧引用它们即可，改一处不会与声明静默分叉。
ARGS_VALUE = f"int | str | bool | None | {INT_TUPLE} | {CELLS}"

#: 命令 `args` 的容器类型：字段名 → 取值，顺序稳定（不可变元组）。
ARGS = f"tuple[tuple[str, {ARGS_VALUE}], ...]"

#: 扁平 JSON 标量映射（插件配置、日志数据）：字段名 → 标量。
SCALAR_MAP = "tuple[tuple[str, int | float | str | bool | None], ...]"

#: 格子坐标清单：`(x, y)`。
COORD_CELLS = "tuple[tuple[int, int], ...]"

#: 棋盘格子数组：**行优先**的每行格子值（需求 §8.3 的 `board.cells`）。
#: 与 `COORD_CELLS` 不是一回事——后者是坐标对清单，这里是"每行放什么"的值
#: 数组（行优先，第 0 行是顶部）。快照的 `board.cells` 引用它。
BOARD_CELLS = "tuple[tuple[int, ...], ...]"

#: 二维向量（坐标 / 位移）：`(x, y)`。
#:
#: 快照的 `ghostDelta` 与规则接口的向量返回类型（`wrap_cell` / `gravity_vector`）
#: 是同一形状——**引用处一律用常量，不复制字面量**（架构 §4.4 末条）。
VECTOR2 = "tuple[int, int]"

#: 只读棋盘视图：行优先的每行格子值，**只读序列**（类型文法形态 10）。
#:
#: 规则接口拿到的是**视图**，不是拷贝：内核内部棋盘是可变结构
#: `list[list[int]]`（架构 §7.1），规则只读。写 `tuple[tuple[int, ...], ...]`
#: 会逼内核在**每次调用规则接口时**深拷贝一份棋盘——消行判定是逐方块调用的
#: 热路径。规则签名的 `board` 参数引用它。
BOARD_VIEW = "Sequence[Sequence[int]]"

__all__ = [
    "ARGS",
    "ARGS_VALUE",
    "BOARD_CELLS",
    "BOARD_VIEW",
    "CELLS",
    "COORD_CELLS",
    "INT_TUPLE",
    "JSON_VALUE",
    "NEXT_QUEUE",
    "SCALAR_MAP",
    "VECTOR2",
]
