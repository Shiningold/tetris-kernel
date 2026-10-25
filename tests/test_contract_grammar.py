"""类型文法形态 11（字面量枚举）的用例（S1 · plan-M1.md S1 · S2 收口订正）。

## 这份文件守什么

形态 11 是 M1 **唯一的一次文法扩展**（`contracts/types.py` 的文法表 · 附加规则
第 6 条）。它承载的是**取值域**：把取值域写进类型串，契约基线才看得见取值域
变过（架构 §7.3.1 末条）。

**新增一种形态 = 既有解析器要么认识它、要么当场报错**——"看着在基线里、实际
没被哈希"的失效模式就是从认不出的串开始的。所以这份用例钉三件事：

1. **合法串必须被解析器接受**，且规范化后与声明写法一致；
2. **非法串必须被解析器拒绝**（`Literal[down]` · `Literal["a", 1]` ·
   `Literal[]` ——形态 11 的三个反面样例）；
3. **既有类型串一条不落地仍然合规**（形态数从 10 变 11，**旧串不许被牵连**）。

## 规范拼写是**单引号**（S2 收口时订正）

PEP 563 把注解存成字符串，而编译器会把内嵌的字符串字面量**统一重加成单
引号**——源码写 `Literal["a", "b"]`，注解存下来是 `Literal['a', 'b']`。

而契约的类型串要跟注解**逐字相等**（内核的导入期契约对齐自检就是这么比
的；取值域收口的验收也是"逐字等于生成器派生值"）——所以**规范拼写取单引
号**：注解存下来的样子、生成器产出的样子、基线哈希的样子，三者是同一串。

**双引号也能写**，那是**排版**：基线哈希的是规范化后的串（解析器统一渲染
成单引号），两种拼写哈希相同。

## 为什么解析器用的是 `tools/` 那一份

测试里另有一份回验解析器（`tests/test_contracts_declarations.py` 的
`_parse_type`）——那是**独立转录**的规格校验器，两份实现可以各写各的，但
**规格书只有一份**（`contracts/types.py` 的文法表）。

这里用 `tools/contract_grammar.py` 的**生产解析器**，因为 S1 的核心主张是
"**文法表与解析器同时回验**"（plan-M0.md S5 踩过的坑：形态表写"T 为形态
1–9"，示例自己的 `T` 就是形态 10）——文法表新加一形态，生产解析器必须当场
认出来，而不是靠测试里的另一份实现替它兜着。
"""

from __future__ import annotations

import dataclasses
import importlib
import sys
from pathlib import Path

import pytest

from tetris_kernel.contracts import (
    AXIS_VALUES,
    CLEAR_AXIS_VALUES,
    COMMAND_TYPE_FIELDS,
    COMMANDS,
    ENVELOPE_FIELDS,
    EVENT_TYPES,
    METADATA_KEYS,
    NAMED_STRUCTS,
    PHASE_LITERAL,
    PHASE_VALUES,
    RULE_SIGNATURES,
    SNAPSHOT_FIELDS,
    PlacementInfo,
)
from tetris_kernel.contracts.types import escape_literal_member, literal_enum

#: 仓库根目录（`tests/` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 采集工具在 `tools/` 下，**它自己** import 同目录的 `_toolkit`，
#: 所以要把 `tools/` 挂到 `sys.path` 上（与 `tests/test_baselines.py` 同一手法）。
sys.path.insert(0, str(REPO_ROOT / "tools"))
_grammar = importlib.import_module("contract_grammar")

GrammarError = _grammar.GrammarError
canonicalize = _grammar.canonicalize


def _parse(type_text: str) -> str:
    """用生产解析器规范化一条类型串（不合规就抛 `GrammarError`）。"""
    return canonicalize(type_text, NAMED_STRUCTS)


# ---------------------------------------------------------------------------
# 生成器：类型串由取值域派生
# ---------------------------------------------------------------------------


def test_literal_enum_derives_the_type_string_from_the_values() -> None:
    """类型串是**派生**出来的，不是手写的。

    `literal_enum(("a", "b"))` 的结果逐字等于 `Literal['a', 'b']`。
    """
    assert literal_enum(("a", "b")) == "Literal['a', 'b']"
    assert literal_enum(("a",)) == "Literal['a']"


def test_literal_enum_keeps_the_order_of_the_values() -> None:
    """成员顺序**照原样保留**——它进基线哈希，重排 = major 变更。

    顺序不参与语义，但一经写入不得重排（文法表末段，与联合成员同一条口径）。
    """
    assert literal_enum(("b", "a")) != literal_enum(("a", "b"))


def test_literal_enum_rejects_an_empty_domain() -> None:
    """空取值域不是合法声明——`Literal[]` 会被解析器拒绝，生成器也要先拦。"""
    with pytest.raises(ValueError):
        literal_enum(())


def test_literal_enum_rejects_duplicated_members() -> None:
    """重复成员是**静默的笔误**，而且会让两个语义相同的取值域派生出两种
    类型串（假 major 变更）——生成器当场拒绝。"""
    with pytest.raises(ValueError):
        literal_enum(("a", "a"))


def test_phase_literal_is_derived_from_phase_values() -> None:
    """生命周期的取值域与类型串**同源**（`PROGRESS.md` 待办：取值域收口）。

    断言是**逐字相等**，不是"包含某个词"——派生关系断了也要红。
    """
    assert PHASE_VALUES == ("not_started", "running", "paused", "game_over")
    assert PHASE_LITERAL == literal_enum(PHASE_VALUES)
    assert PHASE_LITERAL == "Literal['not_started', 'running', 'paused', 'game_over']"


def test_the_generated_type_string_round_trips_through_the_parser() -> None:
    """**生成器与解析器必须互相认得**（S5 踩过的坑：规则与实例没同时回验）。

    每一份仓内的取值域，派生出的类型串都要能被生产解析器解析，且规范化之后
    **逐字不变**——规范化会改的串说明生成器与解析器对"规范形态"的理解不一致。
    """
    domains = (PHASE_VALUES, AXIS_VALUES, CLEAR_AXIS_VALUES)
    for values in domains:
        type_string = literal_enum(values)
        assert _parse(type_string) == type_string, values


# ---------------------------------------------------------------------------
# 形态 11：合法串
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_text", "expected"),
    [
        ("Literal['a']", "Literal['a']"),
        ("Literal['a', 'b']", "Literal['a', 'b']"),
        # 联合成员可以是形态 11 本身（文法附加规则第 6 条）。
        ("Literal['a'] | None", "Literal['a'] | None"),
        ("None | Literal['a']", "None | Literal['a']"),
        # 空白归一：排版变了不算声明变化。
        ("Literal[ 'a' , 'b' ]", "Literal['a', 'b']"),
        # 双引号是**排版**：规范化后与单引号版本同形。
        ('Literal["a", "b"]', "Literal['a', 'b']"),
    ],
)
def test_the_grammar_accepts_the_legal_form_11_strings(type_text: str, expected: str) -> None:
    """形态 11 的合法写法必须被解析器接受，并规范化成规范拼写。"""
    assert _parse(type_text) == expected


def test_the_grammar_accepts_form_11_as_a_named_struct_member() -> None:
    """形态 11 可以是命名结构的一个成员类型（**叶子位置**）。

    命名结构的成员表就是取值域的落点（`RuleContext.axis` 那样），所以这一条
    必须通——不通的话「取值域写进类型串」在结构体里根本没法用。
    """
    structs = dict(NAMED_STRUCTS)
    structs["Probe"] = {"phase": PHASE_LITERAL}
    assert canonicalize("Probe", structs) == f"Probe{{phase:{PHASE_LITERAL}}}"


# ---------------------------------------------------------------------------
# 形态 11：非法串（反向验证 —— 打坏必须变红）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_text", "why"),
    [
        ("Literal[down]", "标识符不是字符串字面量"),
        ('Literal["a", 1]', "成员不是字符串"),
        ("Literal[]", "空成员"),
        ("Literal['a',]", "尾随逗号"),
        ("Literal['a', 'a']", "成员重复"),
        ("Literal['a", "引号没闭合"),
        (r"Literal['a\nb']", "不支持的转义"),
        ("Literal['a'] int", "少个分隔符"),
    ],
)
def test_the_grammar_rejects_the_illegal_form_11_strings(type_text: str, why: str) -> None:
    """非法串必须**当场报错**——封闭文法不"尽量解析"。

    `Literal[down]` / `Literal["a", 1]` / `Literal[]` 是形态 11 的三个反面
    样例（plan-M1.md S1 验证方案第 2 条点名的就是这三个）。

    **报错信息要指向真正的原因**：`Literal[down]` 必须报"期待字符串字面量"，
    而不是"未知类型名"——后者会把改的人引到错误的排查方向（那条另有一条
    用例钉住）。
    """
    with pytest.raises(GrammarError):
        _parse(type_text)


def test_the_grammar_rejects_form_11_inside_a_container() -> None:
    """形态 11 **只出现在叶子位置**——不许嵌进容器形态。

    `tuple[Literal['a'], ...]` 与 `Sequence[Literal['a']]` 一律不合规：
    容器里的元素是"一堆同类型的东西"，而字面量枚举是**单个字段**的取值域
    （架构 §7.3.1 末条）。
    """
    for bad in ("tuple[Literal['a'], ...]", "Sequence[Literal['a']]"):
        with pytest.raises(GrammarError):
            _parse(bad)


def test_the_grammar_rejects_a_literal_member_that_is_not_quoted() -> None:
    """`Literal[down]` 里 `down` 是标识符——形态 11 的成员**只能是字符串字面量**。

    这条与"未知类型名"是两回事：解析器必须**先认出这是形态 11**，再报成员
    不合法，而不是把它当成未知类型名糊弄过去（那样错误信息会指向错误的
    原因，改的人会查错方向）。
    """
    with pytest.raises(GrammarError) as failure:
        _parse("Literal[down]")
    assert "字符串字面量" in str(failure.value)


# ---------------------------------------------------------------------------
# 转义：生成器与解析器互为逆运算
# ---------------------------------------------------------------------------


def test_the_escape_rule_round_trips() -> None:
    """含引号与反斜杠的成员**转义后仍能解析回原值**。

    转义规则只有一份（契约层的 `escape_literal_member`）——生成器与解析器
    各写一份的后果是"生成器产出的串解析器读不懂"，而那不报错，只是基线里
    多了些看不懂的差异。
    """
    value = "a'b\\c"
    escaped = escape_literal_member(value)
    assert escaped == "a\\'b\\\\c"
    type_string = f"Literal['{escaped}']"
    assert _parse(type_string) == type_string


# ---------------------------------------------------------------------------
# 回验：既有类型串一条不落地仍然合规
# ---------------------------------------------------------------------------


def _all_declared_type_strings() -> list[tuple[str, str]]:
    """仓内**全部**声明的类型串（位置 → 类型串）。

    覆盖契约面的四部分（架构 §7.6）：信封与元数据、事件负载、命令参数、
    快照字段、规则签名、命名结构成员表。
    """
    pairs: list[tuple[str, str]] = []
    for field in ENVELOPE_FIELDS:
        pairs.append((f"envelope/{field.name}", field.type))
    for field in METADATA_KEYS:
        pairs.append((f"metadata/{field.name}", field.type))
    for event_type, spec in EVENT_TYPES.items():
        for field in spec.payload:
            pairs.append((f"event/{event_type}/{field.name}", field.type))
    for command_name, spec in COMMANDS.items():
        for arg in spec.args:
            pairs.append((f"command/{command_name}/{arg.name}", arg.type))
    for field_name, field in SNAPSHOT_FIELDS.items():
        pairs.append((f"snapshot/{field_name}", field.type))
    for signature_name, signature in RULE_SIGNATURES.items():
        for param in signature.params:
            pairs.append((f"rule/{signature_name}/{param.name}", param.type))
        pairs.append((f"rule/{signature_name}/returns", signature.returns))
    for struct_name, members in NAMED_STRUCTS.items():
        for member_name, member_type in members.items():
            pairs.append((f"struct/{struct_name}/{member_name}", member_type))
    # `PlacementInfo` 的 dataclass 注解也是声明的类型串（它登记在命名结构
    # 注册表里，但注解本体是另一处声明——两处都要验）。
    for field in dataclasses.fields(PlacementInfo):
        pairs.append((f"placement/{field.name}", str(field.type)))
    return pairs


def test_every_declared_type_string_still_matches_the_grammar() -> None:
    """**形态数从 10 变 11，既有串必须一条不落地仍然合规**（S1 验证方案第 1 条）。

    文法扩展是**加法**：新形态进来，老声明一个字没改，就不许有任何一条解析
    不出来——解析器改动把旧形态认坏了，是这次扩展最容易犯的错。
    """
    pairs = _all_declared_type_strings()
    assert pairs, "仓内一条类型串都没有——回验本身失效了"
    problems: list[str] = []
    for where, type_string in pairs:
        try:
            _parse(type_string)
        except GrammarError as error:
            problems.append(f"{where}：{type_string!r} —— {error}")
    assert not problems, "既有类型串不再合规：\n" + "\n".join(problems)


def test_the_command_type_fields_are_still_declared() -> None:
    """事件侧承载命令名的字段**仍在表里**（形态 11 落地前的既有纪律）。

    这条与形态 11 无关，但它是"回验清单"的一部分：收口取值域时，
    `commandType` 的类型串会变（S2），**承载它的字段表不能同时消失**。
    """
    assert COMMAND_TYPE_FIELDS
    for event_type, field_name in COMMAND_TYPE_FIELDS:
        spec = EVENT_TYPES[event_type]
        assert any(field.name == field_name for field in spec.payload), event_type
