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
- **基线校验脚本要能解析它**（S6）：字符串可直接正则解析，也能直接写进 JSON。

## 类型表达式的文法

下面是**封闭文法**——只有这几种形态，禁止自由发挥。S6 的基线脚本按这张表写
正则解析器，表里没有的形态一律视为**声明不合规**（而不是"尽量解析"）。

| # | 形态 | 写法 | 例 |
| --- | --- | --- | --- |
| 1 | 标量 | `int` · `str` · `bool` · `float` | `int` |
| 2 | 联合 | `T1 \| T2`（成员 ≥ 2；成员可为形态 1–8 的任意组合） | `float \| int` |
| 3 | 可空 | `T \| None`（联合恰两项，`None` 是末项） | `str \| None` |
| 4 | 定长元组 | `tuple[T1, T2]` | `tuple[int, int]` |
| 5 | 变长元组 | `tuple[T, ...]` | `tuple[str, ...]` |
| 6 | 嵌套元组 | 逐层展开 | `tuple[tuple[int, int], ...]` |
| 7 | 元组元素位含联合 | 元素的类型本身是联合 | `tuple[tuple[str, int \| float], ...]` |
| 8 | 递归元组 | `tuple[...]` | `tuple[...]` |

**取值域也是形态 2**：像 `ARGS_VALUE` / `JSON_VALUE` 这样的取值域是"标量 + 元组
形态"的联合，**不是新的形态**。

**三条附加规则**：

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

__all__ = [
    "ARGS",
    "ARGS_VALUE",
    "CELLS",
    "COORD_CELLS",
    "INT_TUPLE",
    "JSON_VALUE",
    "SCALAR_MAP",
]
