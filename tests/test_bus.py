"""事件总线测试（S11 · 信封 / 序号 / 订阅 / 优先级 / 隔离）。

覆盖 plan-M0.md S11 的七条验证：

1. **内核不 import 总线**——扫 `kernel/` 全部源码，断言没有 `bus` 引用；
2. 序号**全局单调递增**、跨帧连续；事件草稿里**不含序号**；
3. 三种订阅：精确 / 命名空间通配 / 全局；
4. 优先级——业务订阅者先于日志订阅者（**跨两个入口**也成立）；
5. 订阅者抛错 → 产生 `kernel:error`，**其余订阅者事件一条不少**，总线继续分发；
6. `ctx.view()` 在分发期间返回**同一份**状态；
7. 未知事件类型 → 静默丢弃并记 trace，**不中断分发**。

外加信封元数据三键、待发布队列、环形历史与"没有 `bus.view()`"的断言。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, fields
from pathlib import Path
from types import MappingProxyType

import pytest

import tetris_kernel.bus as bus_pkg
import tetris_kernel.kernel as kernel_pkg
from tetris_kernel.bus.bus import EventBus
from tetris_kernel.bus.envelope import (
    CAUSE_KEY,
    PRODUCER_KEY,
    SOURCE_KEY,
    Draft,
    Envelope,
    build_metadata,
    metadata_value,
)
from tetris_kernel.bus.subscriber import SystemContext, matches
from tetris_kernel.contracts.events import ENVELOPE_FIELDS, EVENT_TYPES

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


def _bus() -> EventBus[object]:
    """一条不带视图的总线（视图另有专门的用例）。"""
    return EventBus()


def _payload(envelope: Envelope) -> MappingProxyType[str, object]:
    """负载 → 只读映射（便于按字段名断言）。"""
    return MappingProxyType(dict(envelope.payload))


# ---------------------------------------------------------------------------
# 验证 1：内核不 import 总线
# ---------------------------------------------------------------------------


def _imported_module(node: ast.ImportFrom, module_path: str) -> str:
    """相对导入还原成绝对模块名。"""
    if node.level == 0:
        return node.module or ""
    parts = module_path.split(".")
    base = parts[: len(parts) - (node.level - 1)]
    prefix = ".".join(base)
    return f"{prefix}.{node.module}" if node.module else prefix


def test_kernel_does_not_import_bus() -> None:
    """`kernel/` 内没有一处 `bus` 引用（架构 §4.2 / §7.1）。"""
    kernel_dir = Path(kernel_pkg.__file__).parent
    src_root = kernel_dir.parents[1]
    files = sorted(kernel_dir.rglob("*.py"))
    assert files, "kernel/ 下没有源文件——扫描没扫到 ≠ 扫过没问题"
    for path in files:
        relative = path.relative_to(src_root).with_suffix("")
        module_path = ".".join(relative.parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("tetris_kernel.bus"), path.name
            elif isinstance(node, ast.ImportFrom):
                module = _imported_module(node, module_path)
                assert not module.startswith("tetris_kernel.bus"), path.name


def test_bus_does_not_import_kernel() -> None:
    """`bus/` **不 import `kernel`**——总线只吃草稿的形状，不知道内核长什么样。

    这比 `lint-imports` 的 layers 契约**更严**（契约允许 `bus > kernel`），
    是刻意的：内核与总线之间保持松耦合，总线换掉内核也不必改。
    """
    bus_dir = Path(bus_pkg.__file__).parent
    src_root = bus_dir.parents[1]
    files = sorted(bus_dir.rglob("*.py"))
    assert files, "bus/ 下没有源文件——扫描没扫到 ≠ 扫过没问题"
    for path in files:
        relative = path.relative_to(src_root).with_suffix("")
        module_path = ".".join(relative.parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("tetris_kernel.kernel"), path.name
            elif isinstance(node, ast.ImportFrom):
                module = _imported_module(node, module_path)
                assert not module.startswith("tetris_kernel.kernel"), path.name


# ---------------------------------------------------------------------------
# 验证 2：序号全局单调、跨帧连续；草稿不含序号
# ---------------------------------------------------------------------------


def test_sequence_is_global_monotonic_across_frames() -> None:
    """序号全局单调递增，跨帧连续（帧号会重复，序号不会）。"""
    bus = _bus()
    seen: list[Envelope] = []
    bus.subscribe("*", seen.append)

    bus.publish(FakeDraft(eventType="clock:tick", frame=1))
    bus.publish(FakeDraft(eventType="piece:spawned", frame=2))
    bus.publish(FakeDraft(eventType="board:changed", frame=3))
    bus.publish(FakeDraft(eventType="clock:tick", frame=4))

    assert [envelope.seq for envelope in seen] == [1, 2, 3, 4]
    assert [envelope.frame for envelope in seen] == [1, 2, 3, 4]
    assert bus.last_seq == 4


def test_draft_carries_no_sequence_number() -> None:
    """草稿只有四个属性，**没有序号**——序号由总线分配（架构 §4.2）。"""
    draft = FakeDraft(eventType="piece:locked", frame=1)
    assert not hasattr(draft, "seq")
    assert {field.name for field in fields(draft)} == {
        "eventType",
        "payload",
        "frame",
        "metadata",
    }


def test_draft_protocol_matches_the_kernel_draft_shape() -> None:
    """总线按 Protocol 消费草稿——`FakeDraft` 满足 `Draft` 的四个属性。"""
    draft = FakeDraft(eventType="piece:locked", frame=1)
    assert isinstance(draft, Draft)
    assert draft.eventType == "piece:locked"
    assert draft.payload == ()
    assert draft.frame == 1
    assert draft.metadata == ()


def test_envelope_fields_match_the_contract() -> None:
    """信封字段名与顺序**等于**契约声明（架构 §4.4）。"""
    assert tuple(field.name for field in fields(Envelope)) == tuple(
        field.name for field in ENVELOPE_FIELDS
    )


# ---------------------------------------------------------------------------
# 验证 3：三种订阅模式
# ---------------------------------------------------------------------------


def test_three_subscription_patterns() -> None:
    """精确 / 命名空间通配 / 全局三种模式各命中各的（FR-3.1）。"""
    bus = _bus()
    exact: list[Envelope] = []
    wildcard: list[Envelope] = []
    everything: list[Envelope] = []

    bus.subscribe("piece:locked", exact.append)
    bus.subscribe("piece:*", wildcard.append)
    bus.subscribe("*", everything.append)

    bus.publish(FakeDraft(eventType="piece:locked"))
    bus.publish(FakeDraft(eventType="piece:spawned"))
    bus.publish(FakeDraft(eventType="board:changed"))

    assert [envelope.type for envelope in exact] == ["piece:locked"]
    assert [envelope.type for envelope in wildcard] == ["piece:locked", "piece:spawned"]
    assert [envelope.type for envelope in everything] == [
        "piece:locked",
        "piece:spawned",
        "board:changed",
    ]


def test_namespace_wildcard_matches_nested_namespaces() -> None:
    """通配是前缀匹配：`kernel:system:*` 命中 `kernel:system:installed`。"""
    assert matches("kernel:system:*", "kernel:system:installed")
    assert matches("kernel:*", "kernel:system:installed")
    assert not matches("kernel:system:*", "kernel:ready")
    assert matches("*", "任何:类型")
    assert not matches("piece:locked", "piece:spawned")


# ---------------------------------------------------------------------------
# 验证 4：优先级（跨两个入口）
# ---------------------------------------------------------------------------


def test_priority_orders_across_both_entries() -> None:
    """业务订阅者先于日志订阅者——**跨两个入口**也成立（ADR-057）。"""
    bus = _bus()
    order: list[str] = []

    bus.subscribe("*", lambda envelope: order.append("log"), priority=0)
    bus.subscribe_system("piece:*", lambda envelope, ctx: order.append("business"), priority=10)

    bus.publish(FakeDraft(eventType="piece:locked"))
    assert order == ["business", "log"]


def test_equal_priority_keeps_registration_order() -> None:
    """同优先级按订阅先后（FIFO）。"""
    bus = _bus()
    order: list[str] = []
    bus.subscribe("*", lambda envelope: order.append("first"))
    bus.subscribe_system("*", lambda envelope, ctx: order.append("second"))
    bus.publish(FakeDraft(eventType="piece:locked"))
    assert order == ["first", "second"]


def test_single_entry_subscribers_cannot_reach_the_view() -> None:
    """单参数入口**拿不到视图**——渲染器不许用只读视图通道（ADR-059）。"""
    bus = EventBus[dict[str, int]](view_provider=lambda: {"frame": 1})
    assert not hasattr(bus, "view")
    assert not hasattr(bus, "get_view")


# ---------------------------------------------------------------------------
# 验证 5：异常隔离
# ---------------------------------------------------------------------------


def test_failing_subscriber_is_isolated() -> None:
    """订阅者抛错 → `kernel:error`；其余订阅者一条不少；总线继续分发。"""
    bus = _bus()
    received: list[str] = []
    errors: list[Envelope] = []

    def boom(envelope: Envelope) -> None:
        raise RuntimeError("故意炸")

    bus.subscribe("piece:*", boom)
    bus.subscribe("piece:*", lambda envelope: received.append(envelope.type))
    bus.subscribe("piece:*", lambda envelope: received.append(envelope.type))
    bus.subscribe("kernel:error", errors.append)

    bus.publish(FakeDraft(eventType="piece:locked"))

    assert received == ["piece:locked", "piece:locked"], "其余订阅者的事件一条不少"
    assert len(errors) == 1
    payload = _payload(errors[0])
    assert payload["eventType"] == "piece:locked"
    assert payload["eventSeq"] == 1, "eventSeq 是原事件的全局序号"
    assert payload["subscriber"]
    assert "故意炸" in str(payload["error"])

    bus.publish(FakeDraft(eventType="piece:spawned"))
    assert received == ["piece:locked", "piece:locked", "piece:spawned", "piece:spawned"]


def test_error_event_does_not_loop_forever() -> None:
    """错误事件自身不再产生错误事件——必然抛错的订阅者不会把总线拖进死循环。"""
    bus = _bus()
    errors: list[Envelope] = []

    def boom(envelope: Envelope) -> None:
        raise RuntimeError("一直炸")

    bus.subscribe("*", boom)
    bus.subscribe("kernel:error", errors.append)

    bus.publish(FakeDraft(eventType="piece:locked"))

    assert len(errors) == 1, "只有一条错误事件"
    assert bus.traces(), "二次抛错要记 trace"


def test_error_event_payload_matches_the_contract() -> None:
    """`kernel:error` 的负载四字段与契约声明逐字相等。"""
    spec = EVENT_TYPES["kernel:error"]
    assert tuple(field.name for field in spec.payload) == (
        "subscriber",
        "eventType",
        "eventSeq",
        "error",
    )


# ---------------------------------------------------------------------------
# 验证 6：ctx.view() 在分发期间返回同一份状态
# ---------------------------------------------------------------------------


def test_view_is_the_same_object_within_a_publish() -> None:
    """同一次发布里，所有订阅者拿到**同一份**视图（惰性 + 按发布点缓存）。"""
    calls = {"count": 0}

    def provider() -> dict[str, int]:
        calls["count"] += 1
        return {"frame": calls["count"]}

    bus = EventBus[dict[str, int]](view_provider=provider)
    views: list[dict[str, int]] = []
    bus.subscribe_system("*", lambda envelope, ctx: views.append(ctx.view()))
    bus.subscribe_system("*", lambda envelope, ctx: views.append(ctx.view()))

    bus.publish(FakeDraft(eventType="piece:locked"))

    assert len(views) == 2
    assert views[0] is views[1], "分发期间所有订阅者看到一致快照"
    assert calls["count"] == 1, "视图惰性取一次，按发布点缓存"

    bus.publish(FakeDraft(eventType="piece:spawned"))
    assert calls["count"] == 2, "下一次发布重新取视图"


def test_view_without_provider_raises() -> None:
    """没注入视图提供者时 `ctx.view()` 当场报错——不给来路不明的状态。"""
    bus = _bus()
    errors: list[Envelope] = []

    def handler(envelope: Envelope, ctx: SystemContext[object]) -> None:
        ctx.view()

    bus.subscribe_system("*", handler)
    bus.subscribe("kernel:error", errors.append)
    bus.publish(FakeDraft(eventType="piece:locked"))

    assert len(errors) == 1, "订阅者抛错被隔离成错误事件，不外泄"
    assert "视图" in str(_payload(errors[0])["error"])


# ---------------------------------------------------------------------------
# 验证 7：未知事件类型
# ---------------------------------------------------------------------------


def test_unknown_event_type_is_dropped_without_breaking_dispatch() -> None:
    """未登记的事件类型 → 静默丢弃并记 trace，**不中断分发**。"""
    bus = _bus()
    received: list[str] = []
    bus.subscribe("*", lambda envelope: received.append(envelope.type))

    bus.publish(FakeDraft(eventType="piece:locked"))
    bus.publish(FakeDraft(eventType="game:not-a-real-event"))
    bus.publish(FakeDraft(eventType="piece:spawned"))

    assert received == ["piece:locked", "piece:spawned"]
    assert bus.traces(), "丢弃要记 trace"
    assert bus.last_seq == 2, "被丢弃的事件不占序号"


# ---------------------------------------------------------------------------
# 元数据三键与未知键
# ---------------------------------------------------------------------------


def test_metadata_carries_three_keys_and_keeps_unknown_ones() -> None:
    """三键齐全；未知键**静默保留但不解释**（NFR-9 / ADR-062）。"""
    bus = _bus()
    seen: list[Envelope] = []
    bus.subscribe("*", seen.append)

    bus.publish(
        FakeDraft(
            eventType="piece:locked",
            frame=3,
            metadata=(("source", "human"), ("cause", (3, 0)), ("自定义键", "留着")),
        )
    )

    metadata = seen[0].metadata
    assert metadata_value(metadata, SOURCE_KEY) == "human"
    assert metadata_value(metadata, PRODUCER_KEY) == "kernel", "producer 由总线按注册表填"
    assert metadata_value(metadata, CAUSE_KEY) == (3, 0)
    assert metadata_value(metadata, "自定义键") == "留着", "未知键原样保留"
    assert tuple(key for key, _ in metadata)[:3] == (SOURCE_KEY, PRODUCER_KEY, CAUSE_KEY)


def test_producer_comes_from_the_registry_not_from_the_kernel() -> None:
    """内核填的 `producer` 一律忽略——总线是产出者的唯一权威。"""
    bus = _bus()
    seen: list[Envelope] = []
    bus.subscribe("*", seen.append)

    bus.publish(
        FakeDraft(
            eventType="piece:locked",
            metadata=(("producer", "我瞎填的"),),
        )
    )

    assert metadata_value(seen[0].metadata, PRODUCER_KEY) == EVENT_TYPES["piece:locked"].producer


def test_cause_points_to_frame_and_in_frame_sequence() -> None:
    """`cause` 是 `(帧号, 帧内序号)`，**不指向全局序号**（ADR-062 / R5）。"""
    bus = _bus()
    seen: list[Envelope] = []
    bus.subscribe("*", seen.append)

    bus.publish(FakeDraft(eventType="piece:locked", frame=7, metadata=(("cause", (7, 2)),)))

    cause = metadata_value(seen[0].metadata, CAUSE_KEY)
    assert cause == (7, 2)
    assert cause != seen[0].seq, "cause 不指向全局序号"


def test_build_metadata_rejects_malformed_values() -> None:
    """域外值当场报错，不糊弄过去。"""
    with pytest.raises(TypeError):
        build_metadata((("source", 42),), "kernel")
    with pytest.raises(TypeError):
        build_metadata((("cause", (1, 2, 3)),), "kernel")


# ---------------------------------------------------------------------------
# 待发布队列：分发期间的自产事件延后 FIFO 发出
# ---------------------------------------------------------------------------


def test_self_produced_events_are_deferred_until_dispatch_ends() -> None:
    """分发期间产生的自产事件**延后到分发结束后**发出（架构 §5.1）。"""
    bus = _bus()
    order: list[str] = []

    def first(envelope: Envelope) -> None:
        order.append(f"first:{envelope.type}")

    def second(envelope: Envelope) -> None:
        order.append(f"second:{envelope.type}")
        if envelope.type == "piece:locked":
            bus.publish(FakeDraft(eventType="log:entry", frame=envelope.frame))

    bus.subscribe("*", first)
    bus.subscribe("*", second)

    bus.publish(FakeDraft(eventType="piece:locked", frame=1))

    assert order == [
        "first:piece:locked",
        "second:piece:locked",
        "first:log:entry",
        "second:log:entry",
    ], "自产事件在当前分发结束后才发"


def test_pending_queue_keeps_fifo_order() -> None:
    """待发布队列 FIFO。"""
    bus = _bus()
    order: list[str] = []

    def producer(envelope: Envelope) -> None:
        if envelope.type == "piece:locked":
            bus.publish(FakeDraft(eventType="log:entry"))
            bus.publish(FakeDraft(eventType="metric:sampled"))

    bus.subscribe("*", producer)
    bus.subscribe("log:entry", lambda envelope: order.append(envelope.type))
    bus.subscribe("metric:sampled", lambda envelope: order.append(envelope.type))

    bus.publish(FakeDraft(eventType="piece:locked"))

    assert order == ["log:entry", "metric:sampled"]
    assert bus.pending_count == 0, "分发结束后队列排空"


# ---------------------------------------------------------------------------
# 环形历史与注销
# ---------------------------------------------------------------------------


def test_history_keeps_the_last_n_events() -> None:
    """环形历史保留最近 N 条（由旧到新）。"""
    bus = EventBus(history_limit=3)
    for index in range(5):
        bus.publish(FakeDraft(eventType="clock:tick", frame=index))

    history = bus.history()
    assert [envelope.seq for envelope in history] == [3, 4, 5]


def test_unsubscribe_stops_delivery() -> None:
    """订阅句柄可注销（ADR-063：卸载时校验订阅全部注销）。"""
    bus = _bus()
    received: list[str] = []
    subscription = bus.subscribe("*", lambda envelope: received.append(envelope.type))

    bus.publish(FakeDraft(eventType="piece:locked"))
    assert subscription.active
    subscription.unsubscribe()
    assert not subscription.active
    bus.publish(FakeDraft(eventType="piece:spawned"))

    assert received == ["piece:locked"]
