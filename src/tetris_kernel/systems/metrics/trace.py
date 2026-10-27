"""事件轨迹与只读事件流面板（系统层 · M2-S11 · FR-7.3 · FR-7.4 · FR-6.4）。

## 它是什么

**一个普通订阅者**：订阅总线，把事件流**原样**留下来。

- **只读**：不改任何事件，也不碰内核状态（M2-D1）——信封是冻结的、负载是
  元组，**它连改都改不了**；
- **可暂停**：暂停后**不再有新事件进入视图**（**内核照跑**——订阅者拦不住
  总线，也拦不住内核）；
- **按命名空间过滤**：过滤的是**命名空间**（事件类型名里 `:` 前的那一段），
  **不是事件名**——需求 §8.1 的约定就是"事件按命名空间划分"，而 ADR-075 正是
  为了让 `replay:` 与 `game:` 在过滤时**不搅在一起**；
- **可导出**：JSON Lines，**一行一条**——离线分析用（FR-7.4），也当**回归
  断言的基线**（FR-6.4：把事件历史而不是命令当基线）。

## 导出格式：JSON Lines

```
{"seq":1,"frame":0,"type":"kernel:ready","payload":[["seed",0],["rule","standard"]],"metadata":[["source",""],["producer","kernel"],["cause",null]]}
```

**负载与元数据落成「有序的字段对」**（不是字典）：内存里它们就是
`tuple[tuple[str, 值], ...]`，**用同样的形状落盘，读回来逐字段相等是构造
出来的，不是比出来的**。字典会静默吞掉重复键，字段对不会。

**JSON 数组 ↔ 元组是一一对应的还原**：契约的取值域
（`int | str | bool | float | None | tuple[...]`）里**容器只可能是元组**——
所以"每个数组还原成元组"就是**无损**的，导出的轨迹能逐条与源事件流相等。

## 为什么住在 `systems/metrics/` 里

导出与面板同属**可观测面**（FR-7.3 / FR-7.4），与指标系统是同一族：**只
订阅事件流、只产出派生文件**。

**不另开一个系统包**：系统之间互不直调（import-linter 的独立性契约），新开
一个包要多一条契约与一份导出清单，**收益不抵成本**——plan-M2 §S11 的任务项
写的就是"`systems/metrics/`（或独立模块，按职责归位）"。

## 落盘只走 sink

本模块**不 `open()`、不用 `Path.home()`**（M2-D2 · ADR-064）：它**知道有
sink，不知道路径**。路径是**宿主**的装配决定（ADR-074）。
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from ...bus.bus import EventBus
from ...bus.envelope import Envelope, JSONValue
from ...contracts.events import namespace_of

#: 轨迹文件的扩展名（JSON Lines · 一行一条）。
TRACE_SUFFIX = ".jsonl"

#: 落盘出口（**宿主注入的可调用对象** · ADR-064）。
#:
#: **为什么在这里再写一遍别名**：`systems/` **不能** import `host/`（依赖方向），
#: 拿不到 `host.assembly.Sink`；它也**不能** import `systems.log`（系统之间互不
#: 直调）。它是**同一个可调用类型**（结构化类型），这里自己写一份即可
#: ——**别名不是契约，不进基线**。
type Sink = Callable[[bytes], None]

#: JSON 形态的取值（**数组代替元组**——JSON 没有元组）。
type TraceValue = int | str | bool | float | None | list["TraceValue"]


#: 面板默认保留的事件条数（**环形**——留最新的，全量留着是内存负担）。
#:
#: **与 `MetricsSystem` 同族口径**（那边的 `DEFAULT_SAMPLE_LIMIT` = 256）：
#: 面板是"**从现在起看**"的视图，不是**全量存档**——要全量就**显式**说
#: （`limit=None`），不留"默认无限留存"这个口子（审查 M4）。
DEFAULT_PANEL_LIMIT = 4096


class EventPanel[V]:
    """事件面板：**只读**的事件流视图（FR-7.3），顺带能导出（FR-7.4）。

    - `bus`：事件总线（**只订不写内核状态** · M2-D1）；
    - `limit`：视图最多留多少条（`None` = 不限）；**留最新的**（环形）。
      **默认有界**（`DEFAULT_PANEL_LIMIT`）——"长跑驻留百万级信封"是内存
      负担，不是特性（审查 M4）；
    - `pause_at`：**跑到第几帧就自动暂停**（`None` = 不自动暂停）。自动暂停
      的口径是「视图只留 `frame < pause_at` 的事件」。

    **面板与导出器是同一个对象**：视图里有的就是能导出的——**"完整轨迹" =
    不暂停 + 不过滤的那一份**。
    """

    def __init__(
        self,
        bus: EventBus[V],
        *,
        limit: int | None = DEFAULT_PANEL_LIMIT,
        pause_at: int | None = None,
    ) -> None:
        """建面板并**订阅总线**。

        **订阅要在事件开始流动之前**：晚订阅的从 `bus.history()` 里取——本面板
        **不自动补历史**（它是"从现在起看"的视图）。
        """
        if limit is not None and limit < 1:
            raise ValueError(f"limit 至少为 1，实为 {limit}")
        if pause_at is not None and pause_at < 0:
            raise ValueError(f"pause_at 不能为负，实为 {pause_at}")
        self._events: deque[Envelope] = deque(maxlen=limit)
        self._paused = False
        self._pause_at = pause_at
        self._subscription = bus.subscribe("*", self._on_event, name="事件面板")

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def events(self) -> tuple[Envelope, ...]:
        """视图里的事件（**由旧到新**）。"""
        return tuple(self._events)

    @property
    def paused(self) -> bool:
        """视图是不是**暂停**着。"""
        return self._paused

    @property
    def pause_at(self) -> int | None:
        """自动暂停的帧号（`None` = 不自动暂停）。"""
        return self._pause_at

    # ------------------------------------------------------------------
    # 暂停
    # ------------------------------------------------------------------

    def pause(self) -> None:
        """暂停视图（**内核照跑**——订阅者拦不住总线，也拦不住内核）。

        暂停**只是不再往视图里放**：已经收下的事件一条不少。
        """
        self._paused = True

    def resume(self) -> None:
        """恢复视图（之后的事件重新进视图）。"""
        self._paused = False

    # ------------------------------------------------------------------
    # 过滤
    # ------------------------------------------------------------------

    def by_namespaces(self, namespaces: Iterable[str]) -> tuple[Envelope, ...]:
        """只取**这些命名空间**的事件（**按命名空间，不是按事件名**）。

        **命名空间取自契约层的那一份**（`contracts.events.namespace_of`）——
        架构 §4.4：引用处一律用单一权威，不复制字面量。

        **为什么按命名空间而不是按事件名**：需求 §8.1 明写"事件按命名空间
        划分"，而 ADR-075 正是为了让 `replay:` 与 `game:` **在过滤时分开**——
        两种时间轴混在一个命名空间里，按名字匹配更分不开。
        """
        wanted = frozenset(namespaces)
        return tuple(item for item in self._events if namespace_of(item.type) in wanted)

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    def export_text(self) -> str:
        """导出成 **JSON Lines 文本**（一行一条）。"""
        return trace_to_jsonl(self._events)

    def export_bytes(self) -> bytes:
        """导出成字节（UTF-8）。"""
        return self.export_text().encode("utf-8")

    def write_to(self, sink: Sink) -> None:
        """把轨迹交给 sink（**落盘只走 sink** · M2-D2 · ADR-064）。

        **它不知道路径**：路径是宿主的事。
        """
        sink(self.export_bytes())

    # ------------------------------------------------------------------
    # 订阅回调
    # ------------------------------------------------------------------

    def _on_event(self, envelope: Envelope) -> None:
        """收一条事件（**只读**：不改信封、不碰内核）。"""
        if self._paused:
            return
        if self._pause_at is not None and envelope.frame >= self._pause_at:
            # 自动暂停：**触发它的那条事件不进视图**（口径是"视图只留
            # `frame < pause_at` 的事件"）。
            self._paused = True
            return
        self._events.append(envelope)


# ---------------------------------------------------------------------------
# 轨迹的序列化
# ---------------------------------------------------------------------------


def trace_to_jsonl(events: Iterable[Envelope]) -> str:
    """事件信封 → **JSON Lines**（一行一条，键序固定）。"""
    lines = [
        json.dumps(
            {
                "seq": envelope.seq,
                "frame": envelope.frame,
                "type": envelope.type,
                "payload": _payload_to_json(envelope.payload),
                "metadata": _payload_to_json(envelope.metadata),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for envelope in events
    ]
    return "".join(line + "\n" for line in lines)


def trace_from_jsonl(text: str) -> tuple[Envelope, ...]:
    """**JSON Lines** → 事件信封（**逐条还原**）。

    **不认识的一律报错**：字段缺失 · 类型不对 · 容器还原不了——**当场抛
    `ValueError`**，不静默丢字段（静默丢字段会让"导出完整"这条判据假绿）。
    """
    events: list[Envelope] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        raw = _json_object(line, number)
        events.append(
            Envelope(
                seq=_as_int(raw.get("seq"), number, "seq"),
                frame=_as_int(raw.get("frame"), number, "frame"),
                type=_as_str(raw.get("type"), number, "type"),
                payload=_payload_from_json(raw.get("payload"), number),
                metadata=_payload_from_json(raw.get("metadata"), number),
            )
        )
    return tuple(events)


def _payload_to_json(payload: Iterable[tuple[str, JSONValue]]) -> list[list[Any]]:
    """负载 / 元数据 → JSON 的「字段对」清单（**顺序照旧**）。"""
    return [[name, _value_to_json(value)] for name, value in payload]


def _value_to_json(value: JSONValue) -> Any:
    """取值 → JSON 形态（**元组变数组**，其余照旧）。"""
    if isinstance(value, tuple):
        return [_value_to_json(item) for item in value]
    return value


def _payload_from_json(raw: object, number: int) -> tuple[tuple[str, JSONValue], ...]:
    """JSON 的「字段对」清单 → 负载 / 元数据（**每个数组还原成元组**）。"""
    if not isinstance(raw, list):
        raise ValueError(f"轨迹第 {number} 行的 payload 不是字段对清单：{raw!r}")
    pairs: list[tuple[str, JSONValue]] = []
    for item in raw:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError(f"轨迹第 {number} 行的字段对不是 [名字, 值]：{item!r}")
        pairs.append((_as_str(item[0], number, "字段名"), _value_from_json(item[1], number)))
    return tuple(pairs)


def _value_from_json(value: object, number: int) -> JSONValue:
    """JSON 形态 → 取值（**数组还原成元组**——契约的取值域里容器只有元组）。"""
    if isinstance(value, list):
        return tuple(_value_from_json(item, number) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError(f"轨迹第 {number} 行的取值不是 JSON 标量或数组：{value!r}")


def _json_object(line: str, number: int) -> dict[str, Any]:
    """解析一行 JSON 对象（**不是对象就报错**）。"""
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError(f"轨迹第 {number} 行不是合法 JSON：{error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"轨迹第 {number} 行不是 JSON 对象：{value!r}")
    return value


def _as_int(value: object, number: int, what: str) -> int:
    """收成整数（**不是整数就当场报错**；布尔不算整数）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"轨迹第 {number} 行的 {what} 不是整数：{value!r}")
    return value


def _as_str(value: object, number: int, what: str) -> str:
    """收成字符串。"""
    if not isinstance(value, str):
        raise ValueError(f"轨迹第 {number} 行的 {what} 不是字符串：{value!r}")
    return value


__all__ = [
    "DEFAULT_PANEL_LIMIT",
    "TRACE_SUFFIX",
    "EventPanel",
    "Sink",
    "TraceValue",
    "trace_from_jsonl",
    "trace_to_jsonl",
]
