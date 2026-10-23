"""契约类型文法的解析与规范化（`contracts/types.py` 文法表的机器实现）。

## 这份解析器以文法表为准

文法表在 `tetris_kernel.contracts.types` 的模块 docstring 里（十种形态 +
五条附加规则）。**这里的实现按那张表重写**，不是抄测试里的回验解析器——
测试里那份是回验工具，两份实现可以各写各的，但**规格书只有一份**。

## 为什么基线脚本需要解析器

基线哈希的是**声明处引用的类型串**，而形态 9（命名结构引用）必须**按成员表
展开**才算得出哈希（文法附加规则第 4 条）：`Board` 不是别名，它代表
`BOARD_FIELDS` 那张表。展开就得先解析——所以解析器是基线脚本的必需件，
顺带把"文法表里没有的形态"当场挡掉：**看着在基线里、实际没被哈希**的失效模式，
就是从认不出的串开始的。

## 规范化（canonicalization）做什么

解析之后重新序列化一遍，顺带做两件事：

- **展开形态 9**：命名结构引用换成它的成员表，形如
  `Board{width:int;height:int;...}`。于是"改了 `BOARD_FIELDS` 里某个成员的类型"
  会同时改掉引用它的快照字段的哈希——成员表改名也会（键消失 = major 变更）。
- **归一空白**：源码里 `tuple[int, int]` 与 `tuple[int,int]` 是同一个声明。
  排版变了不该撞基线——**声明一个字没变**（架构 §4.4 末条）。

**联合的成员顺序照原样保留**：类型文法表写明"联合的成员顺序不参与语义，
但一经写入不得重排——它会进基线哈希"。所以顺序变了就是哈希变了，这是有意的。

## 用法

```python
from contract_grammar import canonicalize
canonicalize("Board", NAMED_STRUCTS)
```
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from _toolkit import blake2b_hex

#: 形态 1：标量类型名。
SCALAR_TYPES = frozenset({"int", "str", "bool", "float"})

#: 形态 8：递归元组的唯一写法。
RECURSIVE_TUPLE = "tuple[...]"

#: 展开嵌套的最大深度。**防环**：命名结构的成员表理论上可以自引用，
#: 一旦自引用就是展开的死循环——当场报错，不让它变成挂死的脚本。
MAX_EXPANSION_DEPTH = 32


class GrammarError(ValueError):
    """类型串不符合封闭文法（文法表见 `tetris_kernel.contracts.types`）。"""


# ---------------------------------------------------------------------------
# 语法树
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scalar:
    """形态 1：标量（`int` / `str` / `bool` / `float`）。"""

    name: str


@dataclass(frozen=True)
class NoneType:
    """联合里的 `None`（形态 2 的普通成员 / 形态 3 的末项）。"""


@dataclass(frozen=True)
class NamedStruct:
    """形态 9：命名结构引用（`Board` / `Active` / `RuleContext` …）。"""

    name: str


@dataclass(frozen=True)
class TupleType:
    """形态 4–8：元组。

    `variadic` = 形态 5（`tuple[T, ...]`）；`recursive` = 形态 8（`tuple[...]`）。
    形态 6 / 7 是形态 4 的嵌套与元素含联合，不另设节点。
    """

    elements: tuple[object, ...] = ()
    variadic: bool = False
    recursive: bool = False


@dataclass(frozen=True)
class SequenceType:
    """形态 10：只读序列 `Sequence[T]`。"""

    element: object


@dataclass(frozen=True)
class UnionType:
    """形态 2 / 3：联合。**成员顺序照原样保留**（顺序进基线哈希）。"""

    members: tuple[object, ...]


#: 语法树节点的并集。
TypeNode = Scalar | NoneType | NamedStruct | TupleType | SequenceType | UnionType

#: 命名结构注册表：结构名 → 成员表（成员名 → 类型串）。
StructTable = Mapping[str, Mapping[str, str]]


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


class _Parser:
    """递归下降解析器：把类型串拆成语法树。

    文法表里没有的写法一律 `GrammarError`——**封闭文法**，不"尽量解析"。
    """

    def __init__(self, text: str, structs: StructTable) -> None:
        self._text = text
        self._structs = structs
        self._pos = 0

    # -- 基础动作 ---------------------------------------------------------

    def _skip_spaces(self) -> None:
        while self._pos < len(self._text) and self._text[self._pos] == " ":
            self._pos += 1

    def _expect(self, char: str) -> None:
        self._skip_spaces()
        if self._pos >= len(self._text) or self._text[self._pos] != char:
            raise GrammarError(f"{self._text!r} 位置 {self._pos}：期待 {char!r}")
        self._pos += 1

    def _peek(self) -> str:
        self._skip_spaces()
        if self._pos >= len(self._text):
            return ""
        return self._text[self._pos]

    def _take_word(self) -> str:
        self._skip_spaces()
        start = self._pos
        while self._pos < len(self._text):
            char = self._text[self._pos]
            if not (char.isascii() and (char.isalnum() or char == "_")):
                break
            self._pos += 1
        if start == self._pos:
            raise GrammarError(f"{self._text!r} 位置 {self._pos}：期待类型名")
        return self._text[start : self._pos]

    # -- 语法 -------------------------------------------------------------

    def parse(self) -> TypeNode:
        node = self._parse_union()
        if self._pos != len(self._text):
            raise GrammarError(f"{self._text!r} 位置 {self._pos}：有解析不掉的尾巴")
        return node

    def _parse_union(self) -> TypeNode:
        members = [self._parse_atom()]
        while self._peek() == "|":
            self._pos += 1
            members.append(self._parse_atom())
        if len(members) == 1:
            return members[0]
        return UnionType(tuple(members))

    def _parse_atom(self) -> TypeNode:
        word = self._take_word()
        if word == "None":
            return NoneType()
        if word == "tuple":
            return self._parse_tuple()
        if word == "Sequence":
            return self._parse_sequence()
        if word in SCALAR_TYPES:
            return Scalar(word)
        if word in self._structs:
            return NamedStruct(word)
        raise GrammarError(f"{self._text!r}：未知类型名 {word!r}")

    def _parse_tuple(self) -> TupleType:
        self._expect("[")
        if self._text.startswith("...", self._pos):
            self._pos += 3
            self._expect("]")
            return TupleType(recursive=True)
        elements = [self._parse_union()]
        if self._peek() == ",":
            self._pos += 1
            self._skip_spaces()
            if self._text.startswith("...", self._pos):
                if len(elements) != 1:
                    raise GrammarError(f"{self._text!r}：变长元组只能有一个元素类型")
                self._pos += 3
                self._expect("]")
                return TupleType(tuple(elements), variadic=True)
            while True:
                elements.append(self._parse_union())
                if self._peek() != ",":
                    break
                self._pos += 1
        if len(elements) == 1:
            raise GrammarError(f"{self._text!r}：单元素元组 tuple[T] 不在文法表里")
        self._expect("]")
        return TupleType(tuple(elements))

    def _parse_sequence(self) -> SequenceType:
        self._expect("[")
        element = self._parse_union()
        self._expect("]")
        return SequenceType(element)


# ---------------------------------------------------------------------------
# 规范化（展开形态 9）
# ---------------------------------------------------------------------------


class _Renderer:
    """把语法树重新序列化成**规范化**的声明串。

    规范化 = 展开命名结构引用 + 归一空白 + 保留联合成员顺序。
    """

    def __init__(self, structs: StructTable) -> None:
        self._structs = structs

    def render(self, node: TypeNode, depth: int = 0) -> str:
        if depth > MAX_EXPANSION_DEPTH:
            raise GrammarError("命名结构展开过深（成员表自引用？）")
        if isinstance(node, Scalar):
            return node.name
        if isinstance(node, NoneType):
            return "None"
        if isinstance(node, NamedStruct):
            return self._render_struct(node.name, depth)
        if isinstance(node, TupleType):
            if node.recursive:
                return RECURSIVE_TUPLE
            parts = [self.render(element, depth + 1) for element in node.elements]
            if node.variadic:
                return f"tuple[{parts[0]}, ...]"
            return f"tuple[{', '.join(parts)}]"
        if isinstance(node, SequenceType):
            return f"Sequence[{self.render(node.element, depth + 1)}]"
        if isinstance(node, UnionType):
            return " | ".join(self.render(member, depth + 1) for member in node.members)
        raise GrammarError(f"不认识的语法树节点：{node!r}")

    def _render_struct(self, name: str, depth: int) -> str:
        members = self._structs[name]
        parts = [
            f"{member_name}:{self.render_member(member_type, depth + 1)}"
            for member_name, member_type in members.items()
        ]
        return f"{name}{{{';'.join(parts)}}}"

    def render_member(self, type_text: str, depth: int) -> str:
        """渲染命名结构成员的类型（成员类型本身就是一条类型串，要再解析一遍）。"""
        node = _Parser(type_text, self._structs).parse()
        return self.render(node, depth)


def canonicalize(type_text: str, structs: StructTable) -> str:
    """把一条类型声明规范化成可哈希的串。

    - 形态 9 的命名结构引用**按成员表展开**（文法附加规则第 4 条）；
    - 空白归一（排版变化不算声明变化）；
    - 联合成员顺序保留（顺序进哈希，文法表明写不得重排）。

    解析失败抛 `GrammarError`——**不合规的声明不许悄悄进基线**。
    """
    node = _Parser(type_text, structs).parse()
    return _Renderer(structs).render(node)


def hash_declaration(canonical_text: str) -> str:
    """声明的哈希（blake2b，跨进程稳定）。"""
    return blake2b_hex(canonical_text)
