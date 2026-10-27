"""总线增强测试（M2-S5 · **中间件 · 历史查询 · 计数** · 错误隔离）。

覆盖 plan-M2.md S5 的验证方案（基线那两条在 `tests/test_baselines.py`）：

1. **中间件**：每个事件过它一次 · **事件内容一个字节没变** · 它在订阅者之前；
2. **历史查询**：按命名空间过滤 → **返回的事件与订阅时收到的一致**（逐条相等）；
3. **计数**：计数结果与**手工数出来的**一致；
4. **错误隔离**：必然抛错的订阅者 → 其余订阅者不受影响 · 错误事件照常发出；
5. **中间件抛错**：与订阅者同款隔离（订阅者照常收事件 · 错误事件照常发出）；
6. **不做按全局序号的随机跳转**（spec §11.2 K1）。

**中间件改不动内容**不是靠自觉——信封是 `frozen=True` 的 dataclass、负载是
元组，"改内容"在结构上就做不到。这条用例守的就是这个结构事实。
"""

from __future__ import annotations

import dataclasses
import inspect
from dataclasses import dataclass

import pytest

from tetris_kernel.bus.bus import EventBus
from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.bus.subscriber import HistoryQuery

# ---------------------------------------------------------------------------
# 夹具与替身
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeDraft:
    """内核草稿的测试替身（形状与 `kernel/drafts.py` 的 `EventDraft` 一致）。

    **没有序号**——序号是总线分配的（架构 §4.2）。
    """

    eventType: str
    payload: tuple[tuple[str, object], ...] = ()
    frame: int = 0
    metadata: tuple[tuple[str, object], ...] = ()


def _bus(**kwargs: object) -> EventBus[object]:
    """一条不带视图的总线。"""
    return EventBus[object](**kwargs)  # type: ignore[arg-type]


def _payload(envelope: Envelope) -> dict[str, object]:
    """信封负载 → 可按字段名取值的字典。"""
    return dict(envelope.payload)


# ---------------------------------------------------------------------------
# 中间件
# ---------------------------------------------------------------------------


def test_every_event_passes_through_the_middleware_exactly_once() -> None:
    """每个事件过中间件**一次**（不多不少）。"""
    bus = _bus()
    seen: list[Envelope] = []
    bus.use(seen.append, name="记录器")

    bus.publish(FakeDraft(eventType="piece:locked", frame=1))
    bus.publish(FakeDraft(eventType="piece:spawned", frame=2))
    bus.publish(FakeDraft(eventType="board:changed", frame=3))

    assert [envelope.type for envelope in seen] == [
        "piece:locked",
        "piece:spawned",
        "board:changed",
    ]
    assert len(seen) == 3


def test_the_middleware_sees_the_very_same_envelope_as_the_subscribers() -> None:
    """中间件与订阅者拿到的是**同一个信封对象**。"""
    bus = _bus()
    via_middleware: list[Envelope] = []
    via_subscriber: list[Envelope] = []

    bus.use(via_middleware.append, name="中间件")
    bus.subscribe("*", via_subscriber.append, name="订阅者")

    bus.publish(FakeDraft(eventType="piece:locked", frame=4, payload=(("x", 1),)))

    assert len(via_middleware) == 1
    assert len(via_subscriber) == 1
    assert via_middleware[0] is via_subscriber[0], "中间件与订阅者拿到的不是同一个信封"
    assert via_middleware[0].payload == (("x", 1),)
    assert via_middleware[0].frame == 4


def test_the_event_content_is_frozen_so_the_middleware_cannot_change_it() -> None:
    """信封是冻结的——中间件**改不动内容**（结构上就不可能）。"""
    bus = _bus()
    captured: list[Envelope] = []
    bus.use(captured.append, name="中间件")
    bus.publish(FakeDraft(eventType="piece:locked", payload=(("x", 1),)))

    envelope = captured[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.frame = 99  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.payload = (("x", 2),)  # type: ignore[misc]

    assert envelope.frame == 0
    assert envelope.payload == (("x", 1),)
    assert isinstance(envelope.payload, tuple)


def test_the_middleware_runs_before_the_subscribers() -> None:
    """中间件在**分发链上、分发给订阅者之前**跑。"""
    bus = _bus()
    order: list[str] = []

    bus.use(lambda envelope: order.append("中间件"), name="中间件")
    bus.subscribe("*", lambda envelope: order.append("订阅者"), name="订阅者")

    bus.publish(FakeDraft(eventType="piece:locked"))

    assert order == ["中间件", "订阅者"]


def test_use_returns_the_middleware_name() -> None:
    """挂中间件时返回它的名字——报错时好指认是谁炸了。"""
    bus = _bus()
    assert bus.use(lambda envelope: None, name="甲") == "甲"


def test_a_failing_middleware_does_not_stop_the_subscribers() -> None:
    """中间件抛错 → 与订阅者同款隔离：订阅者照常收，错误事件照常发出。"""
    bus = _bus()
    received: list[str] = []
    errors: list[Envelope] = []

    def boom(envelope: Envelope) -> None:
        raise RuntimeError("中间件炸了")

    bus.use(boom, name="坏中间件")
    bus.subscribe("*", lambda envelope: received.append(envelope.type))
    bus.subscribe("kernel:error", errors.append)

    bus.publish(FakeDraft(eventType="piece:locked"))

    assert received == ["piece:locked", "kernel:error"], "订阅者照常收到原事件与错误事件"
    assert len(errors) == 1
    payload = _payload(errors[0])
    assert payload["subscriber"] == "坏中间件", payload
    assert payload["eventType"] == "piece:locked", payload
    assert payload["eventSeq"] == 1, "eventSeq 是原事件的全局序号"
    assert "中间件炸了" in str(payload["error"])


def test_a_failing_middleware_does_not_stop_the_next_middleware() -> None:
    """一个中间件炸了，**后面的中间件照跑**。"""
    bus = _bus()
    seen: list[str] = []

    def boom(envelope: Envelope) -> None:
        raise RuntimeError("中间件炸了")

    bus.use(boom, name="坏中间件")
    bus.use(lambda envelope: seen.append(envelope.type), name="好中间件")

    bus.publish(FakeDraft(eventType="piece:locked"))

    assert seen == ["piece:locked", "kernel:error"], "后面的中间件照跑（原事件 + 错误事件）"


def test_a_failing_middleware_does_not_loop_on_the_error_event() -> None:
    """错误事件自身不再产生错误事件——必然抛错的中间件不会拖总线进死循环。"""
    bus = _bus()
    errors: list[Envelope] = []

    def boom(envelope: Envelope) -> None:
        raise RuntimeError("一直炸")

    bus.use(boom, name="坏中间件")
    bus.subscribe("kernel:error", errors.append)

    bus.publish(FakeDraft(eventType="piece:locked"))

    assert len(errors) == 1, "只有一条错误事件"
    assert bus.traces(), "二次抛错要记 trace"


# ---------------------------------------------------------------------------
# 历史查询
# ---------------------------------------------------------------------------


def test_history_query_by_namespace_matches_what_the_subscriber_received() -> None:
    """按命名空间过滤 → **返回的事件与订阅时收到的逐条相等**。"""
    bus = _bus()
    received: list[Envelope] = []
    bus.subscribe("piece:*", received.append)

    bus.publish(FakeDraft(eventType="piece:locked", frame=1))
    bus.publish(FakeDraft(eventType="piece:spawned", frame=2))
    bus.publish(FakeDraft(eventType="board:changed", frame=3))

    queried = bus.query_history(HistoryQuery(pattern="piece:*"))

    assert len(queried) == len(received)
    assert queried == tuple(received), "查询结果与订阅时收到的不一致"


def test_history_query_by_exact_pattern() -> None:
    """精确模式同样可用（**同一套模式词汇**）。"""
    bus = _bus()
    bus.publish(FakeDraft(eventType="piece:locked"))
    bus.publish(FakeDraft(eventType="piece:spawned"))
    bus.publish(FakeDraft(eventType="board:changed"))

    queried = bus.query_history(HistoryQuery(pattern="piece:locked"))

    assert [envelope.type for envelope in queried] == ["piece:locked"]


def test_history_query_by_frame_range() -> None:
    """帧号区间过滤（**闭区间**）。"""
    bus = _bus()
    for frame in (1, 2, 3, 4):
        bus.publish(FakeDraft(eventType="piece:locked", frame=frame))

    queried = bus.query_history(HistoryQuery(frame_from=2, frame_to=3))

    assert [envelope.frame for envelope in queried] == [2, 3]


def test_history_query_limit_keeps_the_most_recent_events() -> None:
    """`limit` 取**最近** N 条（不是最旧的 N 条）。"""
    bus = _bus()
    for frame in range(1, 6):
        bus.publish(FakeDraft(eventType="piece:locked", frame=frame))

    queried = bus.query_history(HistoryQuery(limit=2))

    assert [envelope.frame for envelope in queried] == [4, 5]


def test_history_query_limit_zero_returns_nothing() -> None:
    """`limit=0` = 一条也不要（**不是**"全部"）。

    这条守的是一个真的坑：`matched[-0:]` 等于整段（`-0` 就是 `0`），
    不显式拦掉就会把"零条"变成"全部"。
    """
    bus = _bus()
    bus.publish(FakeDraft(eventType="piece:locked"))
    bus.publish(FakeDraft(eventType="piece:spawned"))

    assert bus.query_history(HistoryQuery(limit=0)) == ()


def test_history_query_without_a_query_is_the_whole_history() -> None:
    """不传条件 → 等价于 `history()`。"""
    bus = _bus()
    bus.publish(FakeDraft(eventType="piece:locked"))
    bus.publish(FakeDraft(eventType="board:changed"))

    assert bus.query_history() == bus.history()


def test_history_query_rejects_out_of_domain_conditions() -> None:
    """域外值当场报错，不糊弄过去。"""
    with pytest.raises(ValueError):
        HistoryQuery(frame_from=5, frame_to=1)
    with pytest.raises(ValueError):
        HistoryQuery(limit=-1)


def test_the_bus_offers_no_random_access_by_sequence() -> None:
    """**不做按全局序号的随机跳转**（spec §11.2 K1）。

    **机制级断言**：总线的公开方法里没有任何一个**按序号定位**的入口。
    查属性名不够（换个名字照样能过），查**参数**才靠谱。
    """
    bus = _bus()
    offenders: list[str] = []
    for name in dir(bus):
        if name.startswith("_"):
            continue
        member = getattr(bus, name)
        if not callable(member):
            continue
        if "seq" in inspect.signature(member).parameters:
            offenders.append(name)

    assert offenders == [], f"总线多了按序号随机访问的入口：{offenders}"


# ---------------------------------------------------------------------------
# 计数
# ---------------------------------------------------------------------------


def test_counts_match_hand_counting() -> None:
    """计数结果与**手工数出来的**一致（按类型 · 按命名空间）。"""
    bus = _bus()
    bus.publish(FakeDraft(eventType="piece:locked"))
    bus.publish(FakeDraft(eventType="piece:locked"))
    bus.publish(FakeDraft(eventType="piece:spawned"))
    bus.publish(FakeDraft(eventType="board:changed"))

    counts = bus.counts()

    assert counts.count_of("piece:locked") == 2
    assert counts.count_of("piece:spawned") == 1
    assert counts.count_of("board:changed") == 1
    assert counts.by_namespace["piece"] == 3
    assert counts.by_namespace["board"] == 1
    assert counts.total == 4


def test_counts_are_cumulative_even_after_the_history_forgets() -> None:
    """历史是环形缓冲（会忘），**计数不会**。"""
    bus = _bus(history_limit=2)
    for frame in range(1, 6):
        bus.publish(FakeDraft(eventType="piece:locked", frame=frame))

    assert len(bus.history()) == 2, "历史只留最近 2 条"
    assert bus.counts().count_of("piece:locked") == 5, "计数是累计的"
    assert bus.counts().total == 5


def test_dropped_event_types_are_not_counted() -> None:
    """被丢弃的未登记事件类型**不算数**（它没进总线）。"""
    bus = _bus()
    bus.publish(FakeDraft(eventType="piece:locked"))
    bus.publish(FakeDraft(eventType="game:not-a-real-event"))
    bus.publish(FakeDraft(eventType="piece:spawned"))

    counts = bus.counts()

    assert counts.total == 2
    assert "game" not in counts.by_namespace


def test_the_error_events_are_counted_too() -> None:
    """错误事件确实发出去了 → 算数。"""
    bus = _bus()

    def boom(envelope: Envelope) -> None:
        raise RuntimeError("订阅者炸了")

    bus.subscribe("*", boom, name="boom")
    bus.publish(FakeDraft(eventType="piece:locked"))

    counts = bus.counts()
    assert counts.count_of("piece:locked") == 1
    assert counts.count_of("kernel:error") == 1


def test_the_counted_mapping_cannot_be_mutated_from_outside() -> None:
    """计数结果是**只读快照**——外部改不动总线自己的账。"""
    bus = _bus()
    bus.publish(FakeDraft(eventType="piece:locked"))

    counts = bus.counts()
    with pytest.raises(TypeError):
        counts.by_type["piece:locked"] = 99  # type: ignore[index]

    assert bus.counts().count_of("piece:locked") == 1


# ---------------------------------------------------------------------------
# 错误隔离（AC-4）
# ---------------------------------------------------------------------------


def test_a_failing_subscriber_does_not_stop_the_others_and_the_error_is_emitted() -> None:
    """AC-4：抛错的订阅者被隔离——其余订阅者不受影响，错误事件照常发出。"""
    bus = _bus()
    received: list[str] = []
    errors: list[Envelope] = []

    def boom(envelope: Envelope) -> None:
        raise RuntimeError("订阅者炸了")

    bus.subscribe("*", boom, name="boom")
    bus.subscribe("*", lambda envelope: received.append(envelope.type), name="fine")
    bus.subscribe("kernel:error", errors.append, name="err")

    bus.publish(FakeDraft(eventType="piece:locked"))
    bus.publish(FakeDraft(eventType="piece:spawned"))

    assert received == ["piece:locked", "kernel:error", "piece:spawned", "kernel:error"]
    assert len(errors) == 2, "每条原事件对应一条错误事件"
    assert all(_payload(envelope)["subscriber"] == "boom" for envelope in errors)


# ---------------------------------------------------------------------------
# 错误级联防线按身份去重（审查 L1）
# ---------------------------------------------------------------------------


def test_two_subscribers_with_the_same_name_each_report_their_own_failure() -> None:
    """**同名不等于同一个订阅者**（审查 L1）。

    两个**同名**订阅者都抛错 → **两条 `kernel:error`**（各报各的）。原来级联
    防线按**名字**去重，第二个被当成"错误级联里的再次抛错"只记 trace——
    **真正的异常被吞掉**。
    """
    bus = _bus()
    errors: list[Envelope] = []

    def boom(envelope: Envelope) -> None:
        raise RuntimeError("订阅者炸了")

    bus.subscribe("*", boom, name="日志系统")
    bus.subscribe("*", boom, name="日志系统")
    bus.subscribe("kernel:error", errors.append, name="err")

    bus.publish(FakeDraft(eventType="piece:locked"))

    assert len(errors) == 2, f"只报了 {len(errors)} 条——第二个订阅者的异常被吞了"
    assert all(_payload(envelope)["subscriber"] == "日志系统" for envelope in errors)


def test_two_middlewares_with_the_same_name_each_report_their_own_failure() -> None:
    """**同名中间件也各报各的**（审查 L1）——中间件也在分发链上。"""
    bus = _bus()
    errors: list[Envelope] = []

    def boom(envelope: Envelope) -> None:
        raise RuntimeError("中间件炸了")

    bus.use(boom, name="坏中间件")
    bus.use(boom, name="坏中间件")
    bus.subscribe("kernel:error", errors.append, name="err")

    bus.publish(FakeDraft(eventType="piece:locked"))

    assert len(errors) == 2, f"只报了 {len(errors)} 条——第二个中间件的异常被吞了"


def test_the_same_subscriber_still_reports_only_once_in_a_round() -> None:
    """**同一个订阅者在一轮排空里仍然只报一次错**（ADR-083 的正向断言）。

    **这条是上一条的对偶**：级联防线不能被"按身份去重"改没了——**同一个**
    订阅者反复抛错仍然只报一次，否则错误事件会无限级联（ADR-083）。
    """
    bus = _bus()
    errors: list[Envelope] = []

    def boom(envelope: Envelope) -> None:
        raise RuntimeError("订阅者炸了")

    bus.subscribe("*", boom, name="boom")
    bus.subscribe("kernel:error", errors.append, name="err")

    bus.publish(FakeDraft(eventType="piece:locked"))

    assert len(errors) == 1, "同一个订阅者只报一次错"
    assert bus.traces(), "二次抛错要记 trace"
