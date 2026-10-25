"""M1 内核改动测试：软降计分归规则集 · 锁定延迟 · 顶出判定（S11 · K1–K3）。

## 三条改动各自守什么

| 改动 | 守什么 |
| --- | --- |
| **K1** 软降计分归规则集 | 内核**不再写死** `+1`——分值来源是规则集的第 9 条签名 |
| **K2** 锁定延迟 | 30 帧 · 上限 15 次 · 移不动不重置 · 离地取消 · 照常锁定 |
| **K3** 顶出判定 + 出生位置 | 重叠即结束本局；出生横坐标按各自盒子居中 |

## 为什么"换规则集"必须用**替身**来证

G1-8 的核心判据是「**换规则集，软降得分可变**」。光断言"软降加 1 分"
**证不了任何事**——写死 `+1` 的内核照样通过。所以这里造一个**测试替身规则集**
（软降每格 5 分），内核的分数必须跟着变：**写死 `+1` 的内核当场报红**。

## 独立转录：出生横坐标的期望值来自 spec，不是实现

`EXPECTED_SPAWN_X` 的值**独立转录自 spec §7.7**（各按自己的盒子居中），
**不是**从内核的实现算出来的——否则这条用例就是同义反复。

**自给自足**：不依赖 `tests/conftest.py`。
"""

from __future__ import annotations

from dataclasses import replace

from tetris_kernel.contracts import RuleContext
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.core import LOCK_DELAY_FRAMES, LOCK_DELAY_MAX_RESETS, Kernel
from tetris_kernel.kernel.drafts import EventDraft
from tetris_kernel.rules import RuleSet
from tetris_kernel.rules.standard import STANDARD_RULESET
from tetris_kernel.rules.tables import PIECE_TYPES

#: 固定种子（测试不依赖随机性）。
SEED = 20261024

#: 棋盘尺寸（与内核默认一致）。
WIDTH = 10
HEIGHT = 22

#: 出生横坐标的**期望值**（**独立转录自 spec §7.7**）。
#:
#: spec §7.7：「7 种方块的水平居中位置按各自盒子算（**I 用 4×4 盒居中，其余
#: 3×3，O 用 2×2**）」。棋盘宽 10 → 盒子的左边框横坐标 = `(10 − 盒宽) // 2`：
#: 4 宽 → 3 · 3 宽 → 3 · 2 宽 → 4。
#:
#: **M0 是单一常量 `SPAWN_X = 3`**（所有方块同一列）——**O 的 2×2 盒居中后落在
#: x = 4**，那个常量没满足。这张表就是那一处的判据。
EXPECTED_SPAWN_X: dict[str, int] = {
    "I": 3,
    "J": 3,
    "L": 3,
    "O": 4,
    "S": 3,
    "T": 3,
    "Z": 3,
}

#: 锁定期望的**帧数上限**（防"永远不锁定"把测试挂死）。
LOCK_FRAME_LIMIT = 120


# ---------------------------------------------------------------------------
# 替身规则集（G1-8 的证法）
# ---------------------------------------------------------------------------


def _five_points_per_cell(ctx: RuleContext, distance: int) -> int:
    """**替身规则集**的软降计分：每下移一格 5 分（标准规则集是 1 分）。

    **`ctx` 用不上，但参数不能删**——签名是冻结的（ADR-061），变体规则集可以
    按 `ctx` 给不同的分。
    """
    return distance * 5


#: 替身规则集：**只有软降计分**与标准规则集不同，其余 8 条原样。
DOUBLE_RULE_SET: RuleSet = replace(STANDARD_RULESET, soft_drop_score=_five_points_per_cell)


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------


def _kernel() -> Kernel:
    """造一个内核（待开始态）。"""
    return Kernel(seed=SEED, piece_types=PIECE_TYPES)


def _kernel_with(rules: RuleSet) -> Kernel:
    """造一个内核，**注入指定规则集**（替身规则集走这里）。"""
    return Kernel(seed=SEED, piece_types=PIECE_TYPES, rule_set=rules)


def _running_kernel(rules: RuleSet | None = None) -> Kernel:
    """造一个**运行态**的内核（已开局，盘上有一块方块）。"""
    kernel = _kernel_with(rules) if rules is not None else _kernel()
    kernel.execute(Command("start", source="test"))
    return kernel


def _types(drafts: tuple[EventDraft, ...]) -> list[str]:
    """事件类型名序列。"""
    return [draft.eventType for draft in drafts]


def _payload(drafts: tuple[EventDraft, ...], event_type: str) -> dict[str, object]:
    """取某条事件的负载。"""
    for draft in drafts:
        if draft.eventType == event_type:
            return dict(draft.payload)
    raise AssertionError(f"没有 {event_type} 事件：{_types(drafts)}")


def _advance(kernel: Kernel) -> list[list[EventDraft]]:
    """完整消费帧推进的步骤序列（**装配层的正确做法**）。"""
    return [list(step) for step in kernel.advance_frame()]


def _advance_until_locked(
    kernel: Kernel, *, limit: int = LOCK_FRAME_LIMIT
) -> list[list[EventDraft]]:
    """推进到**方块真的锁定**为止，返回**锁定那一帧**的每一步事件。

    **M1 起锁定不是即时的**（spec §7.5：30 帧的锁定延迟，**硬降也一样**），
    M0 是落即锁——凡是「推进一帧就锁定」的旧写法都要改成「推进到锁定为止」。
    """
    for _ in range(limit):
        kernel.execute(Command("tick", source="test"))
        steps = _advance(kernel)
        if any("piece:locked" in _types(tuple(step)) for step in steps):
            return steps
    raise AssertionError(f"{limit} 帧内没有等到锁定事件")


def _spawn_positions(drafts: tuple[EventDraft, ...]) -> dict[str, int]:
    """从一批草稿里取出「方块类型 → 出生横坐标」。"""
    positions: dict[str, int] = {}
    for draft in drafts:
        if draft.eventType == "piece:spawned":
            payload = dict(draft.payload)
            positions[str(payload["pieceType"])] = int(payload["x"])
    return positions


def _step_with(steps: list[list[EventDraft]], event_type: str) -> list[EventDraft]:
    """挑出含某类事件的那一步。"""
    for step in steps:
        if event_type in _types(tuple(step)):
            return list(step)
    raise AssertionError(f"没有 {event_type} 事件：{[list(_types(tuple(s))) for s in steps]}")


# ---------------------------------------------------------------------------
# K1 · 软降计分归规则集
# ---------------------------------------------------------------------------


def test_a_soft_drop_scores_one_point_per_cell_in_the_standard_rule_set() -> None:
    """标准规则集：软降**下移一格**加 1 分（分值来自规则集，不是内核写死的）。"""
    kernel = _running_kernel()

    drafts = kernel.execute(Command("softDrop", source="test"))

    assert kernel.snapshot().score == 1, "软降没有按规则集加分"
    assert _payload(drafts, "score:changed")["delta"] == 1


def test_two_soft_drops_score_twice_not_once() -> None:
    """连续两次软降 → **两次各 1 分**（共 2 分），不是"一次 2 分"。

    **一次软降最多一格**（软降不是"按住就连续下"，那是另一种机制）。
    """
    kernel = _running_kernel()

    kernel.execute(Command("softDrop", source="test"))
    kernel.execute(Command("softDrop", source="test"))

    assert kernel.snapshot().score == 2, "两次软降没有各加 1 分"


def test_a_soft_drop_against_the_floor_scores_nothing() -> None:
    """**贴地时软降下不去** → `distance = 0` → **一分不加**。

    **被墙挡住的那次软降不发 `score:changed`**——没得分就没有"分数变了"这件事
    （架构 §6.2：状态没变就不该有"状态变更"事件）。
    """
    kernel = _running_kernel()
    kernel.execute(Command("hardDrop", source="test"))

    drafts = kernel.execute(Command("softDrop", source="test"))

    assert kernel.snapshot().score == 0, "下不去的软降不该加分"
    assert "score:changed" not in _types(drafts), _types(drafts)
    assert _payload(drafts, "piece:moved")["blocked"] is True


def test_the_soft_drop_score_comes_from_the_rule_set() -> None:
    """**换规则集，软降得分可变**（G1-8 的核心判据）。

    替身规则集每格 5 分 → 软降一次加 **5** 分。**写死 `+1` 的内核在这里当场
    报红**——这正是这条用例存在的意义（光断言"加 1 分"证不了任何事）。
    """
    kernel = _running_kernel(DOUBLE_RULE_SET)

    kernel.execute(Command("softDrop", source="test"))

    assert kernel.snapshot().score == 5, "软降分值没有跟着规则集走"


def test_the_soft_drop_score_uses_the_distance_actually_moved() -> None:
    """软降传给规则集的 `distance` 是**本次实际下移的格数**。

    替身规则集每格 5 分 → 两次软降 = 2 格 = **10 分**（一次 5 + 一次 5）。
    若内核传的是"恒 1"或"累计格数"的别的口径，这里就对不上。
    """
    kernel = _running_kernel(DOUBLE_RULE_SET)

    kernel.execute(Command("softDrop", source="test"))
    kernel.execute(Command("softDrop", source="test"))

    assert kernel.snapshot().score == 10, "distance 传的不是实际下移的格数"


# ---------------------------------------------------------------------------
# K2 · 锁定延迟
# ---------------------------------------------------------------------------


def test_the_lock_delay_starts_when_the_piece_reaches_the_floor() -> None:
    """方块**停在地板上**那一刻开始计时：发 `piece:lock-delay-start`（带时长）。

    **事件在契约里已登记**（M1 不许新增事件），负载字段是 `delayFrames`。
    """
    kernel = _running_kernel()
    kernel.execute(Command("hardDrop", source="test"))

    kernel.execute(Command("tick", source="test"))
    steps = _advance(kernel)

    start = _step_with(steps, "piece:lock-delay-start")
    assert _payload(tuple(start), "piece:lock-delay-start")["delayFrames"] == LOCK_DELAY_FRAMES


def test_the_piece_locks_when_the_lock_delay_expires() -> None:
    """锁定延迟 **30 帧**后锁定——**硬降也一样**。

    **硬降不跳过锁定延迟**（spec §7.5「方块落地后开始计时，到期锁定」这句话
    没有例外）。落地那一帧**起算但不消耗**，所以从落地到锁定是 31 帧。
    """
    kernel = _running_kernel()
    kernel.execute(Command("hardDrop", source="test"))

    frames = 0
    while kernel.snapshot().piecesPlaced == 0:
        kernel.execute(Command("tick", source="test"))
        _advance(kernel)
        frames += 1
        assert frames <= LOCK_FRAME_LIMIT, "锁定迟迟不来"

    assert frames == LOCK_DELAY_FRAMES + 1, f"锁定帧数不对：{frames}"


def test_a_successful_move_resets_the_lock_delay() -> None:
    """移动**成功**（且方块已落地）→ 计时重置，发 `piece:lock-delay-reset`。

    负载字段是 `resetCount`（**已重置次数**）。
    """
    kernel = _running_kernel()
    kernel.execute(Command("hardDrop", source="test"))

    # 第一帧：延迟开始。
    kernel.execute(Command("tick", source="test"))
    _advance(kernel)

    # 移动成功 → 登记重置。
    kernel.execute(Command("moveLeft", source="test"))

    kernel.execute(Command("tick", source="test"))
    steps = _advance(kernel)

    reset = _step_with(steps, "piece:lock-delay-reset")
    assert _payload(tuple(reset), "piece:lock-delay-reset")["resetCount"] == 1


def test_a_blocked_move_does_not_reset_the_lock_delay() -> None:
    """**移不动不重置**——被墙挡住的移动不算成功。

    否则玩家可以靠无效输入无限续命，"有限次重置"就废了（plan-M1.md S11 段
    的常见坑）。这里先贴到左墙，再让它**在已落地**的状态下撞墙：撞墙那一步
    **不得**发 `piece:lock-delay-reset`，也**不得**推迟到期帧。
    """
    kernel = _running_kernel()
    # 先贴到左墙（**在空中移**——不在地板上，不登记重置）。
    for _ in range(WIDTH):
        kernel.execute(Command("moveLeft", source="test"))
    kernel.execute(Command("hardDrop", source="test"))

    # 一帧一帧推进：落地那一帧之后立刻撞一次墙，**整局不许出现重置事件**。
    frames = 0
    resets: list[EventDraft] = []
    while kernel.snapshot().piecesPlaced == 0:
        kernel.execute(Command("tick", source="test"))
        steps = _advance(kernel)
        frames += 1
        for step in steps:
            resets.extend(draft for draft in step if draft.eventType == "piece:lock-delay-reset")
        if frames == 1:
            # 落地那一帧（延迟已起算）→ 现在贴着左墙且已落地 → 这次移动
            # **被挡住**（挡住的移动不算成功，不该登记重置）。
            blocked = kernel.execute(Command("moveLeft", source="test"))
            assert _payload(blocked, "piece:moved")["blocked"] is True, "这一步应该被墙挡住"
        assert frames <= LOCK_FRAME_LIMIT, "锁定迟迟不来"

    assert not resets, "被墙挡住的移动不该重置锁定延迟"
    # 计时没被推迟 → 还是在原定的第 31 帧锁定。
    assert frames == LOCK_DELAY_FRAMES + 1, f"锁定被推迟了：{frames}"


def test_the_lock_delay_reset_is_capped_at_fifteen() -> None:
    """重置**最多 15 次**：第 16 次移动**不再重置**，但**仍会到期锁定**。

    **不重置 ≠ 不锁定**（plan-M1.md S11 段的常见坑）：上限到了只是不再给
    续命的机会，方块照样落地。
    """
    kernel = _running_kernel()
    kernel.execute(Command("hardDrop", source="test"))

    kernel.execute(Command("tick", source="test"))
    _advance(kernel)  # 延迟开始

    # 15 次成功移动 → 15 次重置（左 / 右交替，永远撞不上墙）。
    for index in range(LOCK_DELAY_MAX_RESETS):
        direction = "moveLeft" if index % 2 == 0 else "moveRight"
        kernel.execute(Command(direction, source="test"))
        kernel.execute(Command("tick", source="test"))
        steps = _advance(kernel)
        reset = _step_with(steps, "piece:lock-delay-reset")
        assert _payload(tuple(reset), "piece:lock-delay-reset")["resetCount"] == index + 1, (
            f"第 {index + 1} 次重置的次数不对"
        )

    # 第 16 次 → **不再重置**（上限到了）。
    kernel.execute(Command("moveLeft", source="test"))
    kernel.execute(Command("tick", source="test"))
    steps = _advance(kernel)
    assert not any("piece:lock-delay-reset" in _types(tuple(step)) for step in steps), (
        "第 16 次移动还在重置（上限没生效）"
    )

    # **不重置 ≠ 不锁定**：照常到期锁定。
    frames = 0
    while kernel.snapshot().piecesPlaced == 0:
        kernel.execute(Command("tick", source="test"))
        _advance(kernel)
        frames += 1
        assert frames <= LOCK_FRAME_LIMIT, "上限到了之后方块再也不落地了"
    assert kernel.snapshot().piecesPlaced == 1, "上限到了之后没有锁定"


def test_the_lock_delay_is_cancelled_when_the_piece_leaves_the_floor() -> None:
    """方块**离开地板** → 计时取消（再次落地时重新起算）。

    做法：在第 3 行摆一块悬空的平台（**不成行**，不会被消掉），方块软降一格
    落在它上面 → 计时起算；再把平台**抽掉** → 方块悬空 → 计时取消。
    """
    kernel = _running_kernel()
    platform = tuple((x, 3, 1) for x in range(2, 7))
    kernel.execute(Command("writeCells", args=(("cells", platform),), source="test"))
    kernel.execute(Command("softDrop", source="test"))

    kernel.execute(Command("tick", source="test"))
    _advance(kernel)
    assert kernel.snapshot().lockTimer == LOCK_DELAY_FRAMES, "落地后计时没有起算"

    # **抽掉平台** → 方块悬空 → 计时取消。
    cleared = tuple((x, 3, 0) for x in range(2, 7))
    kernel.execute(Command("writeCells", args=(("cells", cleared),), source="test"))

    kernel.execute(Command("tick", source="test"))
    _advance(kernel)
    assert kernel.snapshot().lockTimer == 0, "离开地板后计时没有取消"


# ---------------------------------------------------------------------------
# K3 · 出生位置（spec §7.7）
# ---------------------------------------------------------------------------


def test_the_spawn_position_is_centered_per_piece_box() -> None:
    """出生横坐标**按各自的盒子居中**（spec §7.7）。

    **M0 是单一常量 `SPAWN_X = 3`**（所有方块同一列）——O 的 2×2 盒居中后落在
    x = 4，那个常量没满足。这条用例跑满一袋（7 种方块各一块），逐块核对
    `EXPECTED_SPAWN_X`（**独立转录自 spec**，不是从实现算出来的）。
    """
    kernel = _kernel()
    seen = _spawn_positions(kernel.execute(Command("start", source="test")))

    # 再放 6 块 → 一袋 7 种方块各一块（7-bag 保证一袋里每种各一块）。
    for _ in range(len(EXPECTED_SPAWN_X) - 1):
        kernel.execute(Command("hardDrop", source="test"))
        steps = _advance_until_locked(kernel)
        for step in steps:
            seen.update(_spawn_positions(tuple(step)))

    assert set(seen) == set(EXPECTED_SPAWN_X), f"没跑满一袋：{seen}"
    for piece_type, x in seen.items():
        assert x == EXPECTED_SPAWN_X[piece_type], f"{piece_type} 的出生列不对：{x}"


# ---------------------------------------------------------------------------
# K3 · 顶出判定
# ---------------------------------------------------------------------------


def test_a_spawn_blocked_by_the_stack_ends_the_game() -> None:
    """出生位置与已有方块**重叠** → 结束本局（`game:over`，带分数 / 行数 / 原因）。

    **堵法**：出生区（第 0、1 行）摆 7 个格子——**不成行**（第 0 行 3 格、第 1
    行 4 格），所以不会被消行顺带清掉；同时这 7 格**盖住了 7 种方块各自的
    出生占位**（I 占第 1 行的 3–6 列，其余六种至少占第 0 行的 3–5 列之一）。
    """
    kernel = _running_kernel()
    # 把活动方块送到地板，把**出生区腾空**出来（`writeCells` 不许改活动方块
    # 占据的格子）。
    kernel.execute(Command("hardDrop", source="test"))
    blocker = ((3, 0), (4, 0), (5, 0), (3, 1), (4, 1), (5, 1), (6, 1))
    kernel.execute(
        Command(
            "writeCells",
            args=(("cells", tuple((x, y, 1) for x, y in blocker)),),
            source="test",
        )
    )

    steps = _advance_until_locked(kernel)

    over = [draft for step in steps for draft in step if draft.eventType == "game:over"]
    assert over, "出生被挡住了却没有结束本局"
    assert kernel.snapshot().phase == "game_over", "局面没有变成 game_over"
    payload = dict(over[0].payload)
    assert payload["reason"] == "blocked", payload
    assert payload["score"] == kernel.snapshot().score, payload
    assert payload["lines"] == kernel.snapshot().lines, payload


def test_the_game_is_not_over_while_the_spawn_area_is_clear() -> None:
    """出生区空着 → **不结束本局**（顶出判定不许误伤正常对局）。"""
    kernel = _running_kernel()
    kernel.execute(Command("hardDrop", source="test"))

    steps = _advance_until_locked(kernel)

    events = [event_type for step in steps for event_type in _types(tuple(step))]
    assert "game:over" not in events, events
    assert kernel.snapshot().phase == "running"
    assert kernel.snapshot().active is not None, "锁定之后没有补上新方块"
