"""事件信封（总线层 · S11）。

信封**五字段**（架构 §5.1 / ADR-062）：`seq` · `frame` · `type` · `payload` ·
`metadata`；元数据**三键**：`source` · `producer` · `cause`。

## 序号不在草稿里（架构 §4.2 / ADR-062）

内核产出的**草稿**只有四个属性（`eventType` / `payload` / `frame` /
`metadata`）——**没有序号**。全局序号由总线在**封装信封时**分配，总线是
序号的唯一权威。`cause` 因此**不指向全局序号**：内核给的 `cause` 是
`(帧号, 帧内序号)`。

## 草稿按 Protocol 消费，不 import 内核

`Draft` 是一个 **Protocol**：总线只认"四个属性对得上"的结构，**不 import
内核的具体类**——内核与总线之间保持松耦合，总线不需要知道内核长什么样
（架构 §4.2）。

**成员声明成只读属性**：内核的 `EventDraft` 是 `frozen=True` dataclass（字段
只读）。Protocol 成员若声明成可写属性，冻结 dataclass **不满足**它——pyright
报 `reportArgumentType`（属性名与类型与先验定的一致，只有可写性不同）。
声明成只读属性后，冻结与非冻结两种 dataclass 都满足。

## 元数据三键与未知键（NFR-9）

`source` / `producer` / `cause` 三键**由总线整形**：`producer` 由总线在分发时
按注册表自动填（内核填的一律忽略——总线是产出者的唯一权威），`source` /
`cause` 取内核给的。**未知键静默保留但不解释**——原样跟在三键后面，不报错、
不删除。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..contracts.events import ENVELOPE_FIELDS, EVENT_TYPES, METADATA_KEYS

#: JSON 兼容取值（递归）。契约层的 `JSON_VALUE` 是**类型表达式字符串**（进
#: 基线用），这里是它在运行期的投影——注解要的是真类型，不是字符串。
type JSONValue = int | str | bool | float | None | tuple[JSONValue, ...]

#: 事件负载容器：字段名 → 值（顺序固定，不可变）。
type Payload = tuple[tuple[str, JSONValue], ...]

#: 事件元数据容器：键 → 值（顺序固定，不可变）。
type Metadata = tuple[tuple[str, JSONValue], ...]

#: 元数据三键的键名（ADR-062：v1 定死）。
SOURCE_KEY = "source"
PRODUCER_KEY = "producer"
CAUSE_KEY = "cause"


# ---------------------------------------------------------------------------
# 草稿：总线消费的唯一入口形状
# ---------------------------------------------------------------------------


@runtime_checkable
class Draft(Protocol):
    """内核产出的**事件草稿**（总线按 Protocol 消费，不 import 内核的类）。

    标了 `runtime_checkable`：形状对不对可以当场查一句，不必等第一次
    分发才炸（它只查属性**在不在**，不查类型）。
    """

    @property
    def eventType(self) -> str:
        """事件类型名——必须已在 `contracts/events.py` 的注册表里登记。"""
        ...

    @property
    def payload(self) -> Payload:
        """负载字段，顺序固定（字段名与类型一律照抄契约声明）。"""
        ...

    @property
    def frame(self) -> int:
        """宿主逻辑帧号（暂停期照走）。"""
        ...

    @property
    def metadata(self) -> Metadata:
        """内核侧给的元数据（`source` / `cause`，以及内核自己的附加键）。

        **不含序号**；总线会把 `producer` 补上，未知键原样保留。
        """
        ...


# ---------------------------------------------------------------------------
# 信封
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Envelope:
    """事件信封**五字段**（架构 §5.1 / ADR-062）。

    字段名与顺序**等于**契约 `ENVELOPE_FIELDS` 的声明——导入期自检，不一致
    当场报错。

    - `seq`：全局单调序号，**总线的唯一权威分配**；
    - `frame`：宿主逻辑帧号；
    - `type`：事件类型名，与注册表的键一致；
    - `payload`：负载，字段见对应事件声明；
    - `metadata`：元数据，三键 + 未知键（见 `build_metadata`）。

    **负载一律不可变**（架构 §5.1）：事件会被送到后台线程消费，可变负载会
    造成撕裂读。
    """

    seq: int
    frame: int
    type: str
    payload: Payload
    metadata: Metadata


# ---------------------------------------------------------------------------
# 元数据整形
# ---------------------------------------------------------------------------


def producer_of(event_type: str) -> str | None:
    """事件的产出者（取自注册表）。

    **未登记的事件类型返回 `None`**——总线据此静默丢弃，不中断分发
    （架构 §5.1「未知事件容错」）。
    """
    spec = EVENT_TYPES.get(event_type)
    return None if spec is None else spec.producer


def _as_source(value: JSONValue) -> str:
    """元数据的 `source` 必须是字符串——不是就当场报错，不糊弄。"""
    if not isinstance(value, str):
        raise TypeError(f"元数据的 {SOURCE_KEY} 必须是字符串，实为 {value!r}")
    return value


def _as_cause(value: JSONValue) -> tuple[int, int] | None:
    """元数据的 `cause` 必须是 `(帧号, 帧内序号)` 或 `None`。

    **不指向全局序号**（ADR-062）：内核生成草稿时还拿不到全局序号。

    元组与列表都收（都归一成元组）——形状对、语义不变。
    """
    if value is None:
        return None
    if isinstance(value, tuple | list) and len(value) == 2:
        first, second = value
        if isinstance(first, int) and isinstance(second, int):
            return (first, second)
    raise TypeError(f"元数据的 {CAUSE_KEY} 必须是 (帧号, 帧内序号) 或 None，实为 {value!r}")


def build_metadata(
    draft_metadata: Metadata,
    producer: str,
    default_source: str = "",
) -> Metadata:
    """把草稿元数据整形成**元数据三键 + 未知键**。

    - `source`：取内核给的；草稿没带时用 `default_source`（空串 = 无上游
      命令）；
    - `producer`：**总线填**（调用方按注册表查出后传进来），内核填的一律
      忽略——总线是产出者的唯一权威；
    - `cause`：取内核给的 `(帧号, 帧内序号)`，没有就是 `None`；
    - **未知键静默保留但不解释**（NFR-9）：原样跟在三键后面，顺序不变。

    三个已知键各出现一次，顺序固定为 `source` · `producer` · `cause`。
    """
    source = default_source
    cause: tuple[int, int] | None = None
    extras: list[tuple[str, JSONValue]] = []
    for key, value in draft_metadata:
        if key == SOURCE_KEY:
            source = _as_source(value)
        elif key == CAUSE_KEY:
            cause = _as_cause(value)
        elif key == PRODUCER_KEY:
            continue
        else:
            extras.append((key, value))
    return ((SOURCE_KEY, source), (PRODUCER_KEY, producer), (CAUSE_KEY, cause), *extras)


def metadata_value(metadata: Metadata, key: str) -> JSONValue | None:
    """取元数据里的一个键的值；**没有这个键返回 `None`**。"""
    for entry_key, value in metadata:
        if entry_key == key:
            return value
    return None


# ---------------------------------------------------------------------------
# 导入期自检：信封字段与契约声明逐条对齐
# ---------------------------------------------------------------------------


def _assert_contract_alignment() -> None:
    """信封字段与契约 `ENVELOPE_FIELDS` **逐字段**对齐。

    **契约是权威**：字段名与顺序必须完全相等（不多不少）。`seq` / `frame` /
    `type` 三个标量字段的类型串也逐字相等；`payload` / `metadata` 两个容器
    字段是契约里 `tuple[tuple[str, JSON_VALUE], ...]` 的**运行期投影**——
    契约那边写的是类型表达式字符串，这边写的是真类型，比不了字符串。
    """
    fields = dataclasses.fields(Envelope)
    actual = tuple(field.name for field in fields)
    expected = tuple(field.name for field in ENVELOPE_FIELDS)
    if actual != expected:
        raise TypeError(f"Envelope 字段与契约不一致：\n  实际：{actual}\n  契约：{expected}")

    types = {field.name: field.type for field in fields}
    for name in ("seq", "frame"):
        if types[name] != "int":
            raise TypeError(f"Envelope.{name} 的类型应为 int，实为 {types[name]!r}")
    if types["type"] != "str":
        raise TypeError(f"Envelope.type 的类型应为 str，实为 {types['type']!r}")

    keys = tuple(field.name for field in METADATA_KEYS)
    if keys != (SOURCE_KEY, PRODUCER_KEY, CAUSE_KEY):
        raise TypeError(f"元数据三键与契约不一致：{keys}")


_assert_contract_alignment()


__all__ = [
    "CAUSE_KEY",
    "PRODUCER_KEY",
    "SOURCE_KEY",
    "Draft",
    "Envelope",
    "JSONValue",
    "Metadata",
    "Payload",
    "build_metadata",
    "metadata_value",
    "producer_of",
]
