"""事件草稿（内核 → 总线）的**正式形态**（S9 · plan-M0.md §1.4.4 · 架构 §4.2）。

## 为什么是这一份形状

`plan-M0.md` §1.4.4 把「事件草稿」列为跨写手的先验定接口：**总线按 Protocol
消费这份形状，不 import kernel 的具体类**。所以本模块只保证四件事：

- 属性名与类型对得上：`eventType` · `payload` · `frame` · `metadata`；
- `payload` 是**不可变**的元组容器（架构 §5.1「负载一律不可变」）；
- `eventType` **必须已在 `contracts/events.py` 登记**——没登记的事件类型当场报错；
- 负载字段名与顺序**与契约声明逐条相等**——照抄契约，不另发明字段名。

## 取值域与契约对齐

契约侧的取值域是**类型表达式字符串**（`contracts/types.py` 的 `JSON_VALUE`），
不是能直接当注解用的 Python 类型。所以这里把同一条取值域落成 Python 类型别名，
并在**导入期**与契约逐字对齐（`_assert_json_value_alignment`）：契约改了取值域
而内核没跟上，当场报错，不等基线刷新才暴露。

## 构造入口只有一个

`make_draft` 是**唯一**的构造入口（`EventDraft` 本身也公开，但走它就绕过了
契约校验）。校验两件事：事件类型已登记 · 负载字段名与顺序与契约逐条相等。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.events import EVENT_TYPES, METADATA_KEYS
from ..contracts.types import JSON_VALUE

# ---------------------------------------------------------------------------
# 取值域
# ---------------------------------------------------------------------------

#: 事件负载 / 元数据的取值域（与 `contracts.types.JSON_VALUE` 同一条）。
#:
#: 契约侧是**类型表达式字符串**，这里落成 Python 类型别名（递归形态 8：信封的
#: `payload` / `metadata` 容器就是开放取值域——架构 §5.1）。
type JsonValue = "int | str | bool | float | None | tuple[JsonValue, ...]"

#: 负载容器：字段名 → 取值，顺序固定（**不可变**）。
type Payload = tuple[tuple[str, JsonValue], ...]

#: 元数据容器：与负载同形（架构 §5.1 的元数据三键 + 内核侧附加键）。
type Metadata = Payload


def _assert_json_value_alignment() -> None:
    """内核的取值域必须与契约的 `JSON_VALUE` **同一条**。

    契约侧是字符串，这里比对的是**展开后的形状**：契约的开放形态
    `tuple[...]` 换成内核的递归别名写法后，两边必须逐字相等。
    """
    open_form = JSON_VALUE.rsplit("| ", 1)[-1]
    expanded = JSON_VALUE.replace(open_form, "tuple[JsonValue, ...]")
    scalars = " | ".join([int.__name__, str.__name__, bool.__name__, float.__name__, str(None)])
    expected = f"{scalars} | tuple[JsonValue, ...]"
    if expanded != expected:
        raise TypeError(f"事件取值域与契约不一致：{expanded!r} ≠ {expected!r}")


_assert_json_value_alignment()

# ---------------------------------------------------------------------------
# 元数据键
# ---------------------------------------------------------------------------

#: 元数据键 `source`（契约的元数据三键之一：命令来源，内核**不得因它分支**）。
METADATA_SOURCE = "source"

#: 元数据键 `cause`（契约的元数据三键之一：引发本事件的那条命令的
#: `(帧号, 帧内序号)`；`None` = 无上游命令）。
METADATA_CAUSE = "cause"

#: 元数据键 `mergedCount`（**内核侧附加键**）：同因拒绝合并的次数。
#:
#: `command:rejected` 的负载里已经有 `mergedCount`（契约字段），但
#: `command:received` 的负载里**没有**这个字段（契约已冻结）——成对合并要求
#: 两边的计数一致，所以 `received` 的计数放在元数据里。
METADATA_MERGED_COUNT = "mergedCount"


def _assert_metadata_keys_known() -> None:
    """内核用到的元数据键必须是契约登记过的键或内核侧附加键。

    契约三键（`source` / `producer` / `cause`）之外，内核只多一个
    `mergedCount`——**多出来的键要显式列出来**，否则"内核悄悄加了第四个键"
    这件事没人看得见（架构 §5.1：未知键静默保留但不解释）。
    """
    known = {field.name for field in METADATA_KEYS}
    unknown = {METADATA_SOURCE, METADATA_CAUSE} - known
    if unknown:
        raise TypeError(f"内核用了契约未登记的元数据键：{sorted(unknown)}")


_assert_metadata_keys_known()

# ---------------------------------------------------------------------------
# 草稿
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventDraft:
    """一条事件草稿（内核 → 总线）。

    字段：

    - `eventType`：事件类型名，**必须已在 `contracts/events.py` 登记**；
    - `payload`：负载字段，顺序固定，**不可变**；
    - `frame`：宿主逻辑帧号（架构 §6.1：暂停期照走）；
    - `metadata`：内核侧给的元数据（`source` / `cause` / `mergedCount`）。

    **草稿不含全局序号**：序号由总线分配（架构 §4.2）。内核生成草稿时还拿不到
    它，所以 `cause` 指向的是 `(帧号, 帧内序号)`，不是全局序号。
    """

    eventType: str
    payload: Payload
    frame: int
    metadata: Metadata = ()


def make_draft(
    event_type: str,
    payload: Payload,
    frame: int,
    metadata: Metadata = (),
) -> EventDraft:
    """构造事件草稿（**校验事件类型已登记 + 负载字段与契约逐条相等**）。

    字段名与顺序照抄契约——**不另发明字段名**：事件完备性检查与回放对齐都
    按契约字段名读，名字对不上时不会报错，只会静默读不到值。
    """
    spec = EVENT_TYPES.get(event_type)
    if spec is None:
        raise ValueError(f"事件类型未在契约中登记：{event_type!r}")
    expected = tuple(field.name for field in spec.payload)
    actual = tuple(name for name, _ in payload)
    if actual != expected:
        raise ValueError(
            f"{event_type} 的负载字段与契约不一致：\n  实际：{actual}\n  契约：{expected}"
        )
    return EventDraft(eventType=event_type, payload=payload, frame=frame, metadata=metadata)


def payload_get(payload: Payload, name: str) -> JsonValue | None:
    """按字段名取负载值；字段不存在返回 `None`。

    **字段名找不到与字段值就是 `None` 不可区分**——这是刻意的：调用方要区分
    两种情况就查契约的字段表，不要靠这个函数的返回值。
    """
    for key, value in payload:
        if key == name:
            return value
    return None


__all__ = [
    "METADATA_CAUSE",
    "METADATA_MERGED_COUNT",
    "METADATA_SOURCE",
    "EventDraft",
    "JsonValue",
    "Metadata",
    "Payload",
    "make_draft",
    "payload_get",
]
