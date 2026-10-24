"""总线异常隔离测试（S15 · **G0-5** · AC-4 · FR-3.4 · 架构 §4.2）。

G0-5 的判据：**注入抛错订阅者，内核存活**——订阅者抛错 → 转 `kernel:error`，
**不中断内核、不影响其它订阅者**（需求 AC-4）。

## 四件事都要成立

1. **产生错误事件**：抛错的订阅者被转成一条 `kernel:error`，负载四字段齐全
   （`subscriber` 订阅者名 · `eventType` 原事件类型 · `eventSeq` 原事件的全局
   序号 · `error` 错误摘要）；
2. **不影响其它订阅者**：其它订阅者**一条不少**地收到了原事件；
3. **不中断内核**：内核还活着——快照照读、命令照发、状态照常推进；
4. **不无限循环**：错误事件自身不再产生错误事件（否则"必然抛错"的订阅者会让
   总线陷入死循环——总线把二次抛错记进 trace）。

**用的就是 `tests/conftest.py` 的公共 fixture**（`fixed_kernel` / `fixed_bus`）——
它们是跨写手共享设施，本文件顺带证明它们可用。
"""

from __future__ import annotations

from tetris_kernel.bus.bus import EventBus
from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.core import Kernel
from tetris_kernel.kernel.snapshot import Snapshot

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _payload(envelope: Envelope) -> dict[str, object]:
    """信封负载 → 可按字段名取值的字典。"""
    return dict(envelope.payload)


def _types(envelopes: tuple[Envelope, ...]) -> list[str]:
    """信封序列的事件类型名。"""
    return [envelope.type for envelope in envelopes]


# ---------------------------------------------------------------------------
# G0-5：注入抛错订阅者内核存活
# ---------------------------------------------------------------------------


def test_subscriber_error_isolated(fixed_kernel: Kernel, fixed_bus: EventBus[Snapshot]) -> None:
    """G0-5：订阅者抛错 → `kernel:error`；内核存活，其它订阅者不受影响。"""
    healthy: list[Envelope] = []

    def boom(envelope: Envelope) -> None:
        """必然抛错的订阅者（AC-4 的"必然抛错"）。"""
        raise RuntimeError("订阅者炸了")

    def fine(envelope: Envelope) -> None:
        """正常的订阅者——**一条都不许少**。"""
        healthy.append(envelope)

    fixed_bus.subscribe("*", boom, name="boom")
    fixed_bus.subscribe("*", fine, name="fine")

    drafts = fixed_kernel.execute(Command("start", source="test"))
    for draft in drafts:
        fixed_bus.publish(draft)

    # ① 错误事件产生了，四字段齐全。
    errors = [envelope for envelope in fixed_bus.history() if envelope.type == "kernel:error"]
    assert errors, "抛错的订阅者没有产生 kernel:error"
    error = _payload(errors[0])
    assert error["subscriber"] == "boom", error
    assert error["eventType"] == drafts[0].eventType, error
    assert error["eventSeq"] == 1, f"错误事件没指向原事件的全局序号：{error}"
    assert "订阅者炸了" in str(error["error"]), error

    # ④ 不无限循环：每条原事件产生**恰好一条**错误事件（二次抛错只记 trace）。
    assert len(errors) == len(drafts), (
        f"发了 {len(drafts)} 条事件，却产生了 {len(errors)} 条错误事件——有循环"
    )
    assert fixed_bus.traces(), "二次抛错应当被记进 trace"

    # ② 其它订阅者不受影响：原事件一条不少（错误事件另算）。
    received = [envelope for envelope in healthy if envelope.type != "kernel:error"]
    assert _types(tuple(received)) == [draft.eventType for draft in drafts], _types(tuple(received))

    # ③ 内核存活：快照照读、状态照常。
    snapshot = fixed_kernel.snapshot()
    assert snapshot.phase == "running", "内核被订阅者的异常带走了"
    assert snapshot.active is not None, "内核的活动方块没了"

    # ③（续）：内核照发命令，总线照常分发。
    after = fixed_kernel.execute(Command("moveLeft", source="test"))
    assert "command:accepted" in [draft.eventType for draft in after], "内核不再接受命令"
    for draft in after:
        fixed_bus.publish(draft)
    assert "command:accepted" in _types(tuple(healthy)), "总线不再分发事件"


def test_a_failing_subscriber_does_not_stop_the_others(
    fixed_kernel: Kernel, fixed_bus: EventBus[Snapshot]
) -> None:
    """抛错的订阅者**只影响自己**——同一模式（`piece:*`）下别的订阅者照收不误。

    比 G0-5 更细一层：G0-5 说"内核存活"，这条说"**订阅者之间**也互不影响"
    （需求 §7 的"所有订阅者地位平等"）。
    """
    seen: list[str] = []

    def boom(envelope: Envelope) -> None:
        raise ValueError("boom")

    fixed_bus.subscribe("piece:*", boom, name="boom")
    fixed_bus.subscribe("piece:*", lambda envelope: seen.append(envelope.type), name="fine")

    # 两条真的 `piece:*` 事件：出生一条、锁定一条。
    drafts = list(fixed_kernel.execute(Command("start", source="test")))
    fixed_kernel.execute(Command("hardDrop", source="test"))
    fixed_kernel.execute(Command("tick", source="test"))
    for batch in fixed_kernel.advance_frame():
        drafts.extend(batch)
    piece_drafts = [draft for draft in drafts if draft.eventType.startswith("piece:")]
    assert len(piece_drafts) >= 2, (
        f"没造出足够的 piece:* 事件：{[d.eventType for d in piece_drafts]}"
    )

    for draft in piece_drafts:
        fixed_bus.publish(draft)

    assert seen == [draft.eventType for draft in piece_drafts], seen
    errors = [envelope for envelope in fixed_bus.history() if envelope.type == "kernel:error"]
    assert len(errors) == len(piece_drafts), f"错误事件条数不对：{len(errors)}"
    assert all(_payload(envelope)["subscriber"] == "boom" for envelope in errors)
