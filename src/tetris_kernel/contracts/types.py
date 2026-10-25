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
| 2 | 联合 | `T1 \| T2`（成员 ≥ 2；成员可为形态 1–11 的任意组合） | `float \| int` |
| 3 | 可空 | `T \| None`（联合恰两项，`None` 是末项） | `str \| None` |
| 4 | 定长元组 | `tuple[T1, T2]` | `tuple[int, int]` |
| 5 | 变长元组 | `tuple[T, ...]` | `tuple[str, ...]` |
| 6 | 嵌套元组 | 逐层展开 | `tuple[tuple[int, int], ...]` |
| 7 | 元组元素位含联合 | 元素的类型本身是联合 | `tuple[tuple[str, int \| float], ...]` |
| 8 | 递归元组 | `tuple[...]` | `tuple[...]` |
| 9 | 命名结构 | `Board` · `Active`（大写开头的标识符） | `Board` |
| 10 | 只读序列 | `Sequence[T]`（T 为形态 1–10，可再嵌套） | `Sequence[Sequence[int]]` |
| 11 | 字面量枚举 | `Literal["a", "b"]`（成员 ≥ 1 个字符串字面量） | `Literal["down", "up"]` |

**取值域也是形态 2**：像 `ARGS_VALUE` / `JSON_VALUE` 这样的取值域是"标量 + 元组
形态"的联合，**不是新的形态**。

**形态 11 是另一回事**：它承载的是**单个字段**的取值域（生命周期 · 重力轴 ·
消行轴 · 命令名）——**把取值域写进类型串，基线才看得见它变了**（架构 §7.3.1
「取值域归谁定」末条：取值域常量不进基线，光有常量等于没有取值域）。

**六条附加规则**：

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
6. **形态 11 只出现在叶子位置**：整条类型串本身、联合的一个成员、或命名结构的
   一个成员类型——**不许嵌进容器形态**（`tuple[Literal["a"], ...]` 与
   `Sequence[Literal["a"]]` 一律不合规）。形态 11 内部**只有字符串字面量**，
   不参与任何形态的嵌套；**成员互不相同**（重复成员是静默的笔误，还会让两个
   语义相同的取值域派生出两种类型串 → 假 major 变更）。
   **字符串字面量只认 `\\` 与**当前引号**两种转义**（成员内容里出现反斜杠或
   当前引号时必须转义）——**当前引号**指这条成员自己的定界引号：单引号成员
   只认 `\'`，双引号成员只认 `\"`。**规范化一律渲染成单引号成员**（PEP 563
   的写法），所以生成器只产出 `\'` 这一种转义；解析器两种都收（引号风格是
   排版，不是语义）。**这张表是解析器的规格书，表与实现必须同口径**——表写
   "只认双引号"而实现转义单引号时，往返实测仍然全一致（双引号在单引号成员
   里不必转义），缺陷要到下一次照着表扩展文法才会暴露（批次 A 审查中 1）。
   **用 `Literal[...]` 而不是自造语法**：它是 Python 生态里这个概念的既有名字，
   `typing.get_origin` / `get_args` 认得，读代码的人不用查本项目的私有文法。
   **这是 M1 唯一的一次文法扩展**（`PROGRESS.md`：别分两次改文法）——将来要新
   形态，一次改到位。

**联合的成员顺序不参与语义，但一经写入不得重排**——它会进基线哈希，重排
等于"已有键的哈希变化"。**形态 11 的成员顺序同理**：它进的是同一份哈希，
重排 = 哈希变 = major 变更。
"""

from __future__ import annotations

from typing import Final, Literal

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

# ---------------------------------------------------------------------------
# 形态 11：字面量枚举（取值域写进类型串）
# ---------------------------------------------------------------------------


def escape_literal_member(value: str) -> str:
    """转义一个字面量枚举成员的**内容**（不含两侧的单引号）。

    **只认两种转义**：`\\`（反斜杠）与 `\'`（单引号）——成员内容里出现这两种
    字符时必须转义，否则解析器会在成员边界上读错。

    **这是公开的**：文法解析器（`tools/contract_grammar.py`）渲染规范化声明时
    要用**同一份**转义规则。**转义规则只写一处**——生成器与解析器各写一份
    后果是"生成器产出的串解析器读不懂"，或"同一取值域派生出两种写法"，
    而这两种失效模式都不报错，只是基线里多了些看不懂的差异。
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def literal_enum(values: tuple[str, ...]) -> str:
    """由**取值域元组**派生字面量枚举的类型串（形态 11）。

    **类型串由取值域派生，不是手写**：`literal_enum(AXIS_VALUES)` 与
    `AXIS_VALUES` 不可能分叉——手写类型串 + 另抄一份取值域，两处静默分叉时
    基线显示全绿，而实际的值域已经变了（架构 §4.4 末条）。

    - **成员 ≥ 1 个**：空取值域不是合法声明；
    - **成员互不相同**：重复成员是静默的笔误，且会让两个语义相同的取值域
      派生出两种类型串（假 major 变更）。

    ## 成员用**单引号**（规范拼写）

    **不是因为单引号好看，是因为注解只能长这样**：PEP 563 把注解存成字符串，
    而编译器会把内嵌的字符串字面量**重新加引号**——源码写
    `Literal["down", ...]`，存下来是 `Literal['down', ...]`。

    而契约的类型串要跟注解**逐字相等**（内核的导入期契约对齐自检就是这么
    比的；取值域收口的验收也是"逐字等于生成器派生值"）——所以**规范拼写
    取单引号**：注解存下来的样子、生成器产出的样子、基线哈希的样子，三者
    是同一串。双引号也能写，但那是**排版**：基线哈希的是规范化后的串
    （解析器统一渲染成单引号），两种拼写哈希相同。
    """
    if not values:
        raise ValueError("字面量枚举至少要有一个成员")
    if len(set(values)) != len(values):
        raise ValueError(f"字面量枚举的成员互不相同，收到 {values!r}")
    members = ", ".join(f"'{escape_literal_member(value)}'" for value in values)
    return f"Literal[{members}]"


#: 生命周期取值域（快照 `phase` 的取值域）。
#:
#: **内核要解读这个字段**（阶段决定门禁与推进许可），所以取值域归**契约层**
#: 钉死（架构 §7.3.1「取值域归谁定」）。内核的 `PHASE_*` 常量**从这里取**
#: ——抄第二份字面量表，两处分叉时无人报警。
#:
#: **四个值逐个声明，而不是写成一个元组**：值必须是 `Final` 的**字面量类型**
#: （`Literal["not_started"]`），这样内核拿到的仍是精确类型；元组里的元素
#: 只能是 `str`，精确类型会丢。`Phase` 别名与 `PHASE_VALUES` 都由这四个
#: 常量派生——**一处声明，四处引用**。
PHASE_NOT_STARTED: Final = "not_started"

#: 运行中。
PHASE_RUNNING: Final = "running"

#: 暂停（内核级暂停）。
PHASE_PAUSED: Final = "paused"

#: 结束。
PHASE_GAME_OVER: Final = "game_over"

#: 生命周期的**取值类型**（typing 形态）——内核用它把 `phase` 的类型写准。
#:
#: **与上面四个常量是同一份词汇**（契约层测试逐字钉住）——类型串那一侧
#: （`PHASE_LITERAL`）与这个类型也必须是同一份词汇。
#:
#: **用 `type` 关键字声明**（PEP 695）：pyright 不把 `X = Literal[...]` 自动
#: 认成类型别名（成员的右边是常量而不是字面量表达式），不标就报「变量不能
#: 用在类型表达式里」——那条报错会指向用它的每一行，把真正的原因藏起来。
#:
#: 成员写**字面量**而不是常量：pyright 求值不出 `Literal[常量]`（成员是变量
#: 而不是字面量表达式），别名会变成"未知类型"，报错同样指不到根上。
type Phase = Literal["not_started", "running", "paused", "game_over"]

#: 四个生命周期取值的稳定序列（**由上面四个常量派生**）。
PHASE_VALUES: tuple[str, ...] = (
    PHASE_NOT_STARTED,
    PHASE_RUNNING,
    PHASE_PAUSED,
    PHASE_GAME_OVER,
)

#: 生命周期取值域的类型串（**由 `PHASE_VALUES` 派生**，不手写）。
PHASE_LITERAL: str = literal_enum(PHASE_VALUES)

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
    "PHASE_GAME_OVER",
    "PHASE_LITERAL",
    "PHASE_NOT_STARTED",
    "PHASE_PAUSED",
    "PHASE_RUNNING",
    "PHASE_VALUES",
    "SCALAR_MAP",
    "VECTOR2",
    "Phase",
    "escape_literal_member",
    "literal_enum",
]
