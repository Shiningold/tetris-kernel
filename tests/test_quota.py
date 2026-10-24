"""合并防抖与配额测试（S10 · plan-M0.md S10 的六条验证）。

覆盖：

1. 同一 phase 下连续 10 条同因拒绝 → **只发 1 组** `received`/`rejected`，
   `mergedCount` = 10；
2. phase 变化后再次同因拒绝 → **重新发首条**（计数重置）；
3. `received` 与 `rejected` 的 `mergedCount` **相等**；
4. 命令数超限 → 剩余命令被拒（`quota_exceeded`）+ 发出 `kernel:quota-exceeded`，
   **下一帧计数清零**（不顺延）；
5. 轮次超限 → 同样走 `kernel:quota-exceeded`，**配额名区分开**；
6. `kernel:error` 在本阶段**不被使用**（只有订阅者抛错才发）。

**自给自足**：不依赖 `tests/conftest.py`。
"""

from __future__ import annotations

from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.core import Kernel
from tetris_kernel.kernel.drafts import EventDraft
from tetris_kernel.kernel.quota import (
    QUOTA_COMMAND_COUNT,
    QUOTA_ROUNDS,
    QuotaLimits,
)
from tetris_kernel.rules.tables import PIECE_TYPES

#: 固定种子（测试不依赖随机性）。
SEED = 20261024


def _kernel(limits: QuotaLimits | None = None) -> Kernel:
    """造一个内核（待开始态）。"""
    if limits is None:
        return Kernel(seed=SEED, piece_types=PIECE_TYPES)
    return Kernel(seed=SEED, piece_types=PIECE_TYPES, limits=limits)


def _running_kernel(limits: QuotaLimits | None = None) -> Kernel:
    """造一个运行态的内核。"""
    kernel = _kernel(limits)
    kernel.execute(Command("start", source="test"))
    return kernel


def _publish_all(kernel: Kernel) -> list[EventDraft]:
    """跑完轮次循环，把发布的草稿全收下来。"""
    published: list[EventDraft] = []
    kernel.run_rounds(published.extend)
    return published


def _types(drafts: list[EventDraft]) -> list[str]:
    """事件类型名序列。"""
    return [draft.eventType for draft in drafts]


def _payload(drafts: list[EventDraft], event_type: str) -> dict[str, object]:
    """取某条事件的负载。"""
    for draft in drafts:
        if draft.eventType == event_type:
            return dict(draft.payload)
    raise AssertionError(f"没有 {event_type} 事件：{_types(drafts)}")


def _metadata(drafts: list[EventDraft], event_type: str) -> dict[str, object]:
    """取某条事件的元数据。"""
    for draft in drafts:
        if draft.eventType == event_type:
            return dict(draft.metadata)
    raise AssertionError(f"没有 {event_type} 事件：{_types(drafts)}")


# ---------------------------------------------------------------------------
# 验证 1 + 3：同因拒绝合并成一条，计数一致
# ---------------------------------------------------------------------------


def test_ten_same_cause_rejections_merge_into_one_pair() -> None:
    """连续 10 条同因拒绝 → **只发 1 组** `received`/`rejected`，计数 = 10。"""
    kernel = _kernel()
    for _ in range(10):
        kernel.submit(Command("moveLeft", source="test"))

    published = _publish_all(kernel)

    assert _types(published).count("command:received") == 1, "同因拒绝不该刷屏"
    assert _types(published).count("command:rejected") == 1, "同因拒绝不该刷屏"
    rejected = _payload(published, "command:rejected")
    assert rejected["mergedCount"] == 10, f"合并计数不对：{rejected['mergedCount']}"
    assert rejected["reason"] == "not_started"


def test_received_and_rejected_carry_the_same_merged_count() -> None:
    """`received` 与 `rejected` 的 `mergedCount` **相等**（成对合并）。"""
    kernel = _kernel()
    for _ in range(4):
        kernel.submit(Command("moveLeft", source="test"))

    published = _publish_all(kernel)

    rejected_count = _payload(published, "command:rejected")["mergedCount"]
    received_count = _metadata(published, "command:received")["mergedCount"]
    assert received_count == rejected_count, (
        f"两边的计数不一致：received={received_count} rejected={rejected_count}"
    )


def test_merged_pair_keeps_the_original_args() -> None:
    """合并事件的 `args` 与原命令一致（架构 §7.2：拒绝也要带参数）。"""
    kernel = _kernel()
    kernel.submit(Command("moveLeft", source="human"))

    published = _publish_all(kernel)

    assert _payload(published, "command:rejected")["args"] == ()
    assert _payload(published, "command:received")["args"] == ()


# ---------------------------------------------------------------------------
# 验证 2：phase 变化后计数重置
# ---------------------------------------------------------------------------


def test_phase_change_resets_the_merge_count() -> None:
    """phase 变化后再次同因拒绝 → **重新发首条**（计数重置，不接着累加）。"""
    kernel = _running_kernel()

    # 第一段：暂停态下 3 条同因拒绝。
    kernel.execute(Command("pause", source="test"))
    for _ in range(3):
        kernel.submit(Command("moveLeft", source="test"))
    first = _publish_all(kernel)
    assert _payload(first, "command:rejected")["mergedCount"] == 3

    # phase 变化：resume → running，再 pause → paused（回到同一个 phase）。
    kernel.execute(Command("resume", source="test"))
    kernel.execute(Command("pause", source="test"))

    # 第二段：同样的键（paused / moveLeft / paused）再来 3 条。
    for _ in range(3):
        kernel.submit(Command("moveLeft", source="test"))
    second = _publish_all(kernel)

    assert _payload(second, "command:rejected")["mergedCount"] == 3, "计数没有重置"
    assert _types(second).count("command:rejected") == 1


# ---------------------------------------------------------------------------
# 验证 4：命令数配额
# ---------------------------------------------------------------------------


def test_command_count_quota_drops_the_rest_and_resets_next_frame() -> None:
    """命令数超限 → 剩余命令被拒 + `kernel:quota-exceeded`；下一帧计数清零。"""
    kernel = _running_kernel(QuotaLimits(max_commands=2, max_rounds=8))
    # 开一帧（tick 出队时计数器清零），让本帧的命令额度从零开始算。
    kernel.execute(Command("tick", source="test"))
    for _ in range(5):
        kernel.submit(Command("moveLeft", source="test"))

    published = _publish_all(kernel)

    rejected = _payload(published, "command:rejected")
    assert rejected["reason"] == "quota_exceeded", "超限的剩余命令必须被拒"
    assert rejected["mergedCount"] == 3, "被丢弃的命令要逐条计入合并计数"

    quota = _payload(published, "kernel:quota-exceeded")
    assert quota["quota"] == QUOTA_COMMAND_COUNT, "配额名不对"
    assert quota["limit"] == 2
    assert quota["actual"] == 5

    # 下一帧：tick 出队时计数器清零 → 同样的命令不再被丢（**不顺延**）。
    kernel.submit(Command("tick", source="test"))
    for _ in range(2):
        kernel.submit(Command("moveLeft", source="test"))
    next_frame = _publish_all(kernel)

    assert "kernel:quota-exceeded" not in _types(next_frame), "计数没有清零"
    assert _types(next_frame).count("command:accepted") == 3, "下一帧的命令不该被丢"


# ---------------------------------------------------------------------------
# 验证 5：轮次配额
# ---------------------------------------------------------------------------


def test_round_quota_uses_the_same_event_with_a_different_quota_name() -> None:
    """轮次超限 → 同样走 `kernel:quota-exceeded`，**配额名区分开**。"""
    kernel = _running_kernel(QuotaLimits(max_commands=100, max_rounds=1))
    kernel.submit(Command("moveLeft", source="test"))

    def publish(drafts: tuple[EventDraft, ...]) -> None:
        published.extend(drafts)
        # 分发期间派发命令 → 队列非空 → 触发第二轮（**下一轮**才生效）。
        kernel.submit(Command("moveLeft", source="test"))

    published: list[EventDraft] = []
    kernel.run_rounds(publish)

    quota = _payload(published, "kernel:quota-exceeded")
    assert quota["quota"] == QUOTA_ROUNDS, "配额名不对"
    assert quota["limit"] == 1
    rejected = _payload(published, "command:rejected")
    assert rejected["reason"] == "quota_exceeded"


# ---------------------------------------------------------------------------
# 验证 6：kernel:error 只留给订阅者异常
# ---------------------------------------------------------------------------


def test_kernel_error_is_not_used_in_this_stage() -> None:
    """本阶段**不许**用 `kernel:error`——它只留给订阅者异常。"""
    kernel = _running_kernel(QuotaLimits(max_commands=1, max_rounds=1))
    for _ in range(4):
        kernel.submit(Command("moveLeft", source="test"))
    kernel.submit(Command("tick", source="test"))

    published = _publish_all(kernel)

    assert "kernel:error" not in _types(published), "本阶段不该发 kernel:error"
