"""快照与只读视图（内核层 · S8）。

**只读视图与快照是同一个 dataclass**（ADR-056）——不写两套"几乎相同"的
结构，两套结构长期必然漂移。本模块提供三个 `frozen=True` dataclass
（`Snapshot` / `Board` / `Active`）、构造入口与两个指纹的计算。

## 深拷贝：只冻结外层不够（ADR-058）

内核内部棋盘是**可变结构** `list[list[int]]`（架构 §7.1）。导出成快照时
**逐行深拷贝**成 `tuple[tuple[int, ...], ...]`——只冻结外层的话，快照与内核
仍然共享内层列表，渲染器改一下就破"只有内核能写状态"。

## 指纹：两个哈希不能混（架构 §5.4）

- `structureFingerprint`：**棋盘内容**的指纹（行优先 TLV + 域分隔前缀），
  **不含扩展字段**；
- `extensionFingerprint`：`extensions` 的**规范化摘要**（key 排序）。

变体局的回放校验拿错了这两个哈希，四项比对会全绿而结果不同。

## rngState 的两条构造路径（架构 §5.2）

`snapshot()` 路径**恒为 `None`**（`build_snapshot` **不接受**该参数）；存档
路径用 `dataclasses.replace(snapshot, rngState=...)` 填（`with_rng_state`
）——**不在 `frozen=True` 上惰性赋值**。

## 视图构造纯读（架构 §5.2）

构造路径只读入参：不碰随机源、不改任何状态。否则"关闭全部可观测系统后内核
行为逐帧一致"这条判据在概率上就废了。

## 与契约的关系

字段集合与 `tetris_kernel.contracts.snapshot` 的声明**完全相等**（不多不少）。
契约是权威，本模块在**导入期**逐字段自检（字段名 / 顺序 / 类型串）——契约与
实现分叉当场报错，不等基线刷新时才暴露。
"""

from __future__ import annotations

import dataclasses
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, get_args

from ..contracts.snapshot import (
    ACTIVE_FIELDS,
    BOARD_FIELDS,
    SNAPSHOT_FIELDS,
    FieldSpec,
)
from ..contracts.types import (
    BOARD_CELLS,
    INT_TUPLE,
    NEXT_QUEUE,
    SCALAR_MAP,
    VECTOR2,
    Phase,
)
from .fingerprint import blake2b_hex, tlv_digest

# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------

#: 扩展槽条目的标量联合的**规范文本**——kernel 侧唯一一处写法。
#:
#: `ExtensionEntry` 与 `contracts.types.SCALAR_MAP` 的元素类型是**同一条**：
#: 契约改了、别名没跟着改，两处静默分叉（架构 §4.4 末条）。别名不在字段
#: 注解的校验范围内（字段注解比的是 `SCALAR_MAP` 本身），所以这里**显式
#: 断言**把别名、文本、契约常量三者钉成一条。
_SCALAR_VALUE_TEXT = "int | float | str | bool | None"

#: 类型 → 规范文本（还原联合成员用，**不靠 `repr`**——`repr` 的形状随 Python
#: 版本漂）。
_TYPE_TEXT: Mapping[type, str] = MappingProxyType(
    {int: "int", float: "float", str: "str", bool: "bool", type(None): "None"}
)

#: 扩展槽条目：key → 标量值（架构 §5.4：只放标量，禁容器与嵌套）。
ExtensionEntry = tuple[str, int | float | str | bool | None]


# ---------------------------------------------------------------------------
# 契约对齐自检（导入期跑一次）
# ---------------------------------------------------------------------------


def _field_types(fields: tuple[dataclasses.Field[Any], ...]) -> dict[str, str]:
    """dataclass 字段 → 字段名 → 注解文本（**保持声明顺序**）。

    **注解必须是字符串**：没有 `from __future__ import annotations` 时它是运行时
    类型对象（`"<class 'int'>"`），基线算不出合法类型串——当场报错，不靠"每个
    模块都记得写 future import"。
    """
    types: dict[str, str] = {}
    for field in fields:
        if not isinstance(field.type, str):
            raise TypeError(
                f"{field.name} 的注解不是字符串（是 {field.type!r}）："
                "该模块缺 `from __future__ import annotations`"
            )
        types[field.name] = field.type
    return types


def _assert_same(name: str, actual: Mapping[str, str], table: Mapping[str, FieldSpec]) -> None:
    """一个 dataclass 与契约声明**逐字段**相等（字段名 / 顺序 / 类型串）。"""
    expected = [(key, spec.type) for key, spec in table.items()]
    actual_pairs = list(actual.items())
    if actual_pairs != expected:
        raise TypeError(f"{name} 与契约声明不一致：\n  实际：{actual_pairs}\n  契约：{expected}")


def _assert_value(name: str, actual: str, expected: str) -> None:
    """一个字段的注解文本与**契约常量**逐字相等。"""
    if actual != expected:
        raise TypeError(f"{name} 的类型与契约常量不一致：{actual!r} ≠ {expected!r}")


def _alias_text(alias: object) -> str:
    """类型别名的**规范文本**（`tuple[str, int | float | …]`）。

    **不靠 `repr`**——`repr` 的形状随 Python 版本漂；这里按 `get_args` 逐个
    成员还原，形状稳定。
    """
    args = get_args(alias)
    if len(args) != 2 or args[0] is not str:
        raise TypeError(f"扩展槽条目类型不是 `tuple[str, 标量联合]`：{alias!r}")
    try:
        members = " | ".join(_TYPE_TEXT[member] for member in get_args(args[1]))
    except KeyError as error:
        raise TypeError(f"扩展槽标量联合里有认不出的成员：{error.args[0]!r}") from error
    return f"tuple[str, {members}]"


# ---------------------------------------------------------------------------
# 三个 frozen dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Board:
    """棋盘子结构（契约 `BOARD_FIELDS` 的 5 个字段，顺序即声明顺序）。

    `cells` 是**行优先**的不可变棋盘（第 0 行是顶部，行索引自上而下递增），
    与架构 §5.4 的 TLV 编码口径同一条。

    `structureFingerprint` 是**棋盘内容**的指纹——**不含扩展字段**（架构
    §5.4）：变体局回放校验拿错了它，四项比对会全绿而结果不同。
    """

    width: int
    height: int
    hiddenRows: int
    cells: tuple[tuple[int, ...], ...]
    structureFingerprint: str

    def __post_init__(self) -> None:
        """指纹与棋盘内容**必须一致**——不一致说明有人手拼了一个假快照。"""
        expected = compute_structure_fingerprint(self.cells)
        if self.structureFingerprint != expected:
            raise ValueError(
                "Board.structureFingerprint 与 board.cells 不一致："
                f"{self.structureFingerprint!r} ≠ {expected!r}"
            )


@dataclass(frozen=True)
class Active:
    """活动方块（契约 `ACTIVE_FIELDS` 的 4 个字段，顺序即声明顺序）。

    快照顶层 `active` 的类型是 `Active | None`——`None` 表示等待出生。
    """

    pieceType: str
    rotation: int
    x: int
    y: int


@dataclass(frozen=True)
class Snapshot:
    """内核状态的**完整只读导出**（契约 `SNAPSHOT_FIELDS` 的 22 个字段）。

    **字段集合与契约声明完全相等**（不多不少）——内核每新增一个状态字段，
    这里必须同步新增一个字段，否则 FR-10.2 的存档恢复缺一块（架构 §5.2）。

    `rngState` 走**两条构造路径**：`snapshot()` 路径恒为 `None`；存档路径用
    `dataclasses.replace(snapshot, rngState=...)` 填（架构 §5.2）。
    """

    version: int
    rule: str
    # **取值域写进注解**：契约层的 `phase` 类型串是字面量枚举（`PHASE_LITERAL`），
    # 这里**逐字**写同一串——导入期的契约对齐自检会拿它与契约声明逐字比对，
    # 对不上当场报错（契约改了、注解没跟着改，就是这样被抓住的）。
    #
    # 注解存下来的样子由 PEP 563 决定（编译器把内嵌的字符串字面量统一重加成
    # 单引号），契约那一侧的类型串用的是同一个规范拼写。
    phase: Literal["not_started", "running", "paused", "game_over"]
    frame: int
    board: Board
    active: Active | None
    ghostDelta: tuple[int, int]
    hold: str | None
    holdUsed: bool
    next: tuple[str, ...]
    score: int
    lines: int
    level: int
    combo: int
    b2b: bool
    piecesPlaced: int
    grounded: bool
    lockTimer: int
    gravityTimer: int
    extensions: tuple[tuple[str, int | float | str | bool | None], ...]
    extensionFingerprint: str
    rngState: tuple[int, ...] | None

    def __post_init__(self) -> None:
        """扩展槽的 key 唯一、扩展指纹与扩展槽**必须一致**。"""
        _validate_unique_keys(self.extensions)
        expected = compute_extension_fingerprint(self.extensions)
        if self.extensionFingerprint != expected:
            raise ValueError(
                "Snapshot.extensionFingerprint 与 extensions 不一致："
                f"{self.extensionFingerprint!r} ≠ {expected!r}"
            )


# ---------------------------------------------------------------------------
# 扩展槽
# ---------------------------------------------------------------------------


def _entry_key(entry: ExtensionEntry) -> str:
    """扩展条目的排序键（**按 key 排序**是规范化摘要的口径）。"""
    return entry[0]


def _validate_unique_keys(extensions: tuple[ExtensionEntry, ...]) -> None:
    """扩展槽的 key **必须唯一**（架构 §5.4）。

    `extensions` 是 tuple 不是 dict，**可以写重复 key**——重复即结构错误并
    报错，**不"去重取末值"**（去重取末值会静默丢掉一个扩展）。
    """
    seen: set[str] = set()
    for key, _ in extensions:
        if key in seen:
            raise ValueError(f"扩展槽的 key 重复：{key!r}")
        seen.add(key)


def build_extensions(entries: Iterable[ExtensionEntry]) -> tuple[ExtensionEntry, ...]:
    """把扩展槽定形：校验 key 唯一性 + **按 key 排序**（规范化形态）。

    排序让"同一组扩展、不同合并顺序"产出**同一个快照**——回放比对的是内容，
    不是宿主合并扩展时的偶然顺序。
    """
    materialized = tuple(entries)
    _validate_unique_keys(materialized)
    return tuple(sorted(materialized, key=_entry_key))


# ---------------------------------------------------------------------------
# 指纹：稳定哈希 + TLV + 域分隔前缀（架构 §5.4）
# ---------------------------------------------------------------------------

#: TLV 类型标签（架构 §5.4：标签覆盖 None / bool / int / float / str / tuple）。
#:
#: **布尔与整数必须用不同标签**——同一个字节序列解出两种含义，摘要就不可能
#: 区分"扩展槽里写了 `True`"和"写了 `1`"。
_TAG_NONE = 0x00
_TAG_BOOL = 0x01
_TAG_INT = 0x02
_TAG_FLOAT = 0x03
_TAG_STR = 0x04
_TAG_TUPLE = 0x05

#: 两个指纹的**域分隔前缀**（架构 §5.4：输入前加域分隔前缀）。
#:
#: 结构指纹与扩展指纹**必须是两个哈希**：拿错了，变体局回放校验的第五项
#: 永远比对不上（或永远比得上）。
_DOMAIN_BOARD = b"tetris-kernel/board-structure/v1"
_DOMAIN_EXTENSION = b"tetris-kernel/extensions/v1"


def _domain_tag(domain: bytes) -> bytes:
    """域分隔标签：域名自身的 blake2b 摘要（固定长度，与内容字节不同域）。

    **标签写进"值"里，不当条目的"键"**：扩展域的条目键是宿主定的任意字符串
    （扩展槽的 key 没有名字限制），拿固定名字当键等于给扩展 key 加了一条契约
    里没写的限制——key 恰好叫 `domain` 就会撞键。写进值里则**任何 key 都不会
    撞**，域分隔照样成立。
    """
    return blake2b_hex(domain).encode("ascii")


def _tlv(tag: int, payload: bytes) -> bytes:
    """TLV 编码：**1 字节类型标签 + 4 字节小端长度 + 值**（架构 §5.4）。"""
    return bytes((tag,)) + len(payload).to_bytes(4, "little") + payload


def _int_byte_length(value: int) -> int:
    """整数所需的最小带符号字节数（0 → 1 字节；正负都不浪费）。"""
    return (value.bit_length() + 8) // 8


def _encode_int(value: int) -> bytes:
    """整数的规范字节（长度进 TLV，所以字节数可随值变化）。"""
    return _tlv(_TAG_INT, value.to_bytes(_int_byte_length(value), "little", signed=True))


def _encode_scalar(value: int | float | str | bool | None) -> bytes:
    """标量 → TLV 字节（**布尔与整数用不同标签**，绝不混编）。

    浮点按 **IEEE 754 双精度小端**落字节（架构 §5.4：指纹基于内存值，不基于
    JSON 文本）——`repr(0.1)` 那套文本化在跨版本/跨实现上不保证一致。
    """
    if value is None:
        return _tlv(_TAG_NONE, b"")
    if isinstance(value, bool):
        return _tlv(_TAG_BOOL, b"\x01" if value else b"\x00")
    if isinstance(value, int):
        return _encode_int(value)
    if isinstance(value, float):
        return _tlv(_TAG_FLOAT, struct.pack("<d", value))
    return _tlv(_TAG_STR, value.encode("utf-8"))


def _encode_int_tuple(values: tuple[int, ...]) -> bytes:
    """整数元组 → TLV 字节（**先写元素个数**，再写各元素 TLV——解决元素边界）。"""
    payload = len(values).to_bytes(4, "little") + b"".join(_encode_int(value) for value in values)
    return _tlv(_TAG_TUPLE, payload)


def _encode_cells(cells: tuple[tuple[int, ...], ...]) -> bytes:
    """棋盘内容 → TLV 字节（**行优先**：行 0 在前，行内从左到右）。"""
    rows = tuple(_encode_int_tuple(row) for row in cells)
    payload = len(rows).to_bytes(4, "little") + b"".join(rows)
    return _tlv(_TAG_TUPLE, payload)


def _encode_extension_entry(key: str, value: int | float | str | bool | None) -> bytes:
    """扩展条目的规范字节：**key 自己也写进去**。

    key 写进**值字节**（而不只是当条目的名字）——`tlv_digest` 的先验定签名只
    说了 `(items) -> 摘要`，没保证它把**名字**也计入摘要；key 进了摘要，换一
    组 key 一定换一个摘要（扩展指纹比的是内容，不是顺序）。
    """
    return _tlv(_TAG_STR, key.encode("utf-8")) + _encode_scalar(value)


def compute_structure_fingerprint(cells: tuple[tuple[int, ...], ...]) -> str:
    """棋盘内容的指纹（行优先 TLV + 域分隔前缀，**不含扩展字段**）。"""
    items = (("cells", _domain_tag(_DOMAIN_BOARD) + _encode_cells(cells)),)
    return tlv_digest(items)


def compute_extension_fingerprint(extensions: tuple[ExtensionEntry, ...]) -> str:
    """扩展槽的**规范化摘要**（**key 排序**）。

    **空扩展槽也带域标签**：否则它的摘要和"别的域的空内容"一样，域分隔就漏了。
    """
    tag = _domain_tag(_DOMAIN_EXTENSION)
    ordered = sorted(extensions, key=_entry_key)
    if not ordered:
        items: tuple[tuple[str, bytes], ...] = (("extensions", tag),)
    else:
        items = tuple((key, tag + _encode_extension_entry(key, value)) for key, value in ordered)
    return tlv_digest(items)


# ---------------------------------------------------------------------------
# 构造入口
# ---------------------------------------------------------------------------


def freeze_cells(cells: Sequence[Sequence[int]]) -> tuple[tuple[int, ...], ...]:
    """内核的可变棋盘 → 快照的不可变棋盘（**逐行深拷贝**，ADR-058）。

    每一行都**重新建成 tuple**：只把外层 list 换成 tuple 的话，内层仍是共享
    的可变 list，渲染器一改就破"只有内核能写状态"。
    """
    return tuple(tuple(row) for row in cells)


def build_board(
    width: int,
    height: int,
    hidden_rows: int,
    cells: Sequence[Sequence[int]],
) -> Board:
    """从内核的可变棋盘构造只读 `Board`（深拷贝 + 结构指纹）。

    **尺寸与内容必须对得上**：行数等于 `height`、每行长度等于 `width`，
    不一致当场报错。

    **为什么必须校验**：结构指纹只覆盖 `cells`（架构 §5.4 的口径本就如此），
    **不覆盖 `width` / `height` / `hiddenRows`**——尺寸与内容对不上时，指纹
    照样算得出来、快照照样发得出去，存档恢复后是一块尺寸不符的棋盘，而
    回放比对**四项全绿**。校验挡在构造入口，比事后查指纹便宜得多。
    """
    frozen = freeze_cells(cells)
    if len(frozen) != height:
        raise ValueError(f"棋盘行数与 height 不符：height={height}，实际 {len(frozen)} 行")
    for index, row in enumerate(frozen):
        if len(row) != width:
            raise ValueError(f"第 {index} 行的长度与 width 不符：width={width}，实际 {len(row)} 格")
    return Board(
        width=width,
        height=height,
        hiddenRows=hidden_rows,
        cells=frozen,
        structureFingerprint=compute_structure_fingerprint(frozen),
    )


def build_snapshot(
    *,
    version: int,
    rule: str,
    phase: Phase,
    frame: int,
    board: Board,
    active: Active | None,
    ghost_delta: tuple[int, int],
    hold: str | None,
    hold_used: bool,
    next_queue: tuple[str, ...],
    score: int,
    lines: int,
    level: int,
    combo: int,
    b2b: bool,
    pieces_placed: int,
    grounded: bool,
    lock_timer: int,
    gravity_timer: int,
    extensions: tuple[ExtensionEntry, ...],
) -> Snapshot:
    """构造只读快照（**`rngState` 恒为 `None`**）。

    存档路径用 `dataclasses.replace(snapshot, rngState=...)` 填（见
    `with_rng_state`）——**本函数不接受 `rngState` 参数**，从签名上就杜绝
    "快照路径顺手带了随机源状态"。

    **扩展槽在构造入口就规范化**（按 key 排序 + key 唯一性校验）——
    「同一组扩展 = 同一个快照」这条承诺不能只靠调用方记得先过
    `build_extensions`：直接传进来的扩展若顺序不同，两个快照就不相等
    （指纹相同，快照不同）。

    **纯读**：只读入参，不碰随机源、不改任何状态（架构 §5.2）。
    """
    normalized = build_extensions(extensions)
    return Snapshot(
        version=version,
        rule=rule,
        phase=phase,
        frame=frame,
        board=board,
        active=active,
        ghostDelta=ghost_delta,
        hold=hold,
        holdUsed=hold_used,
        next=next_queue,
        score=score,
        lines=lines,
        level=level,
        combo=combo,
        b2b=b2b,
        piecesPlaced=pieces_placed,
        grounded=grounded,
        lockTimer=lock_timer,
        gravityTimer=gravity_timer,
        extensions=normalized,
        extensionFingerprint=compute_extension_fingerprint(normalized),
        rngState=None,
    )


def with_rng_state(snapshot: Snapshot, rng_state: tuple[int, ...]) -> Snapshot:
    """存档路径：填入随机源状态（架构 §5.2）。

    **用 `dataclasses.replace`**——`frozen=True` 上不能惰性赋值（直接报错）。
    """
    return dataclasses.replace(snapshot, rngState=rng_state)


# ---------------------------------------------------------------------------
# 导入期自检：字段集合 / 顺序 / 类型串与契约声明完全相等
# ---------------------------------------------------------------------------


def _assert_contract_alignment() -> None:
    """三个 dataclass 与契约声明**逐字段**对齐。

    **契约是权威**：字段集合必须完全相等（不多不少），类型串引用**契约常量**
    （架构 §4.4 末条：引用处一律用常量，不复制字面量——常量改了、注解没跟着
    改，两处静默分叉，基线却显示全绿）。

    dataclass 注解写的是**真类型**（PEP 563 下把常量当注解会存成 `"SCALAR_MAP"`
    这样的字面串，基线算不出真类型），所以相等性在这里**逐字段钉死**。
    """
    board = _field_types(dataclasses.fields(Board))
    active = _field_types(dataclasses.fields(Active))
    snapshot = _field_types(dataclasses.fields(Snapshot))

    _assert_same("Board", board, BOARD_FIELDS)
    _assert_same("Active", active, ACTIVE_FIELDS)
    _assert_same("Snapshot", snapshot, SNAPSHOT_FIELDS)

    # 引用契约常量的五个字段：注解文本 ↔ 常量，逐条相等。
    _assert_value("Board.cells", board["cells"], BOARD_CELLS)
    _assert_value("Snapshot.ghostDelta", snapshot["ghostDelta"], VECTOR2)
    _assert_value("Snapshot.next", snapshot["next"], NEXT_QUEUE)
    _assert_value("Snapshot.extensions", snapshot["extensions"], SCALAR_MAP)
    _assert_value("Snapshot.rngState", snapshot["rngState"], f"{INT_TUPLE} | None")

    # 扩展槽的**标量联合**：契约常量 ↔ 内核别名，逐字相等。
    #
    # 别名不在字段注解的校验范围内（字段比的是 `SCALAR_MAP` 本身）——不钉住
    # 就会静默分叉：契约改了标量联合、别名没跟着改，两处各说各话。
    _assert_value("SCALAR_MAP", SCALAR_MAP, f"tuple[tuple[str, {_SCALAR_VALUE_TEXT}], ...]")
    _assert_value(
        "ExtensionEntry", _alias_text(ExtensionEntry), f"tuple[str, {_SCALAR_VALUE_TEXT}]"
    )


_assert_contract_alignment()


__all__ = [
    "Active",
    "Board",
    "ExtensionEntry",
    "Snapshot",
    "build_board",
    "build_extensions",
    "build_snapshot",
    "compute_extension_fingerprint",
    "compute_structure_fingerprint",
    "freeze_cells",
    "with_rng_state",
]
