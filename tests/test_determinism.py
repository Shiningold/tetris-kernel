"""确定性与属性测试（S15 · **G0-3** + 属性测试纪律 · ADR-013 / ADR-053 / ADR-038）。

覆盖：

- **G0-3**（`determinism_same_seed`）：同种子 **10 次**结果全等——(棋盘结构指纹,
  score, lines, piecesPlaced) 四项全等，**事件流序列亦全等**；
- **属性测试**：内核随机源固定种子；断言**不崩**、**无静默状态变更**、**同序列重跑
  指纹一致**、**拒绝不改状态**（用 hypothesis）。

## 为什么命令序列必须真的发命令

G0-3 判的是「同种子同序列 → 同结果」。**只跑帧不派发命令**测的是"空跑也一致"——
那种一致性太弱（随机源都没被消费过）。所以下面的序列把 17 条命令里的多数都用
上了：开局 · 移动 · 旋转 · 软降 · 硬降 · 暂存 · 推进 · 改等级 · 注入垃圾行 ·
受控写入，每条 `tick` 之后**完整消费**帧推进的五步。

## 属性测试的两条纪律（ADR-013）

1. **内核随机源固定种子**——属性测试的随机性只用来**挑命令序列**，内核自己的
   随机源永远是同一个种子；
2. **hypothesis 开 `derandomize=True`**（ADR-053）——落点在 `tests/conftest.py`，
   本文件不另设 profile。

**拒绝不改状态**这条在属性测试里是硬断言：被拒的命令执行前后，游戏状态（去掉
帧号与版本号）必须逐字段相等——**帧号除外**（ADR-022：帧号在 tick 出队时就 +1，
被拒的 tick 也一样）。
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from tetris_kernel.contracts.commands import COMMAND_TYPES
from tetris_kernel.kernel.commands import Args, Command
from tetris_kernel.kernel.core import LOCK_DELAY_FRAMES, Kernel
from tetris_kernel.kernel.drafts import EventDraft
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.rules.standard import STANDARD_RULESET
from tetris_kernel.rules.tables import PIECE_TYPES

#: 固定种子（属性测试与门测试共用一个种子）。
SEED = 20261024

#: G0-3 要跑的次数（需求 §12：同种子 10 次结果全等）。
RUNS = 10

#: 命令三段生命周期的事件（**不算语义事件**）。
COMMAND_LIFECYCLE_EVENTS = frozenset({"command:received", "command:accepted", "command:rejected"})

#: 固定命令序列（**真的发命令**，不是只跑帧）。
#:
#: 顺序有意为之：**换规则集** → 开局 → 操作 → 落地 → 推进（锁定一块）→ 换块
#: → 暂存 → 推进 → 参数类命令 → 再推进。每条 `tick` 之后**完整消费**帧推进
#: 的五步。
#:
#: **硬降之后那一串 tick 是 31 条不是 1 条**：M1 起有 **30 帧的锁定延迟**
#: （spec §7.5，硬降也一样——"方块落地后开始计时"没有例外），硬降只是把方块
#: 按到地板上，**要等延迟到期才锁定**。M0 是落即锁，推进一帧就锁上了——
#: 那时候这串命令里只需要 1 条 tick。
#:
#: **开头那条 `switchRuleSet`（M3 审查报告 M2）**：17 条命令里它**从来没进过
#: 这条序列**——确定性指纹与回放因此**完全不含运行期换规则集路径**。
#:
#: **为什么放在开头而不是末尾**：**局中切换必拒**（`illegal_transition`）——
#: 放在末尾只能覆盖「被拒」那一半；**开头是 `not_started` 态**，切换**被接
#: 受** → **接受路径的整段进了指纹**（参数校验 → 解析器命中 → 换规则集 →
#: **局面重置**（棋盘空 · 相位回待开始 · 随机源回种子）→ `kernel:rule-set-
#: changed` + `game:reset` 两条语义事件）。
#:
#: **为什么四项统计一个都不变**：切换发生在**开局之前**，重置的是**本来就空
#: 的棋盘**，随机源也回到初始种子——**后面的玩法逐帧照旧**（四项统计与事件
#: 流里多出来的只是开头那两条换规则集事件）。
#:
#: **为什么换回同一条规则**（`fromRule == toRule`）：确定性的门要的是
#: **这条命令的路径进了指纹**，不是「换成别的玩法」——**换回自己**时规则集
#: 对象与标识都不变，**四项统计因此逐字不变**。**变体规则集**（换完按新规则
#: 跑 + 录像重放逐帧一致）由 `tests/test_m3_rule_set.py` 的**替身规则集**验。
COMMAND_SEQUENCE: tuple[tuple[str, Args], ...] = (
    # **运行期换规则集**（M3 · ADR-061）——待开始态切换**被接受**。
    ("switchRuleSet", (("rule", "standard"),)),
    ("start", ()),
    ("moveLeft", ()),
    ("rotateCW", ()),
    ("softDrop", ()),
    ("hardDrop", ()),
    *(("tick", ()) for _ in range(LOCK_DELAY_FRAMES + 1)),
    ("moveRight", ()),
    ("rotateCCW", ()),
    ("hold", ()),
    ("tick", ()),
    ("setLevel", (("level", 4),)),
    ("injectGarbage", (("rows", 1), ("holes", (0,)))),
    ("writeCells", (("cells", ((1, 4, 1),)),)),
    ("tick", ()),
)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _game_state(snapshot: Snapshot) -> tuple[object, ...]:
    """**游戏状态**（去掉帧号与版本号）。"""
    return (
        snapshot.rule,
        snapshot.phase,
        snapshot.board,
        snapshot.active,
        snapshot.ghostDelta,
        snapshot.hold,
        snapshot.holdUsed,
        snapshot.next,
        snapshot.score,
        snapshot.lines,
        snapshot.level,
        snapshot.combo,
        snapshot.b2b,
        snapshot.piecesPlaced,
        snapshot.grounded,
        snapshot.lockTimer,
        snapshot.gravityTimer,
        snapshot.extensions,
    )


def _record(drafts: tuple[EventDraft, ...]) -> list[tuple[str, tuple[tuple[str, object], ...]]]:
    """把草稿压成**可比较**的序列（事件类型 + 负载）。"""
    return [(draft.eventType, draft.payload) for draft in drafts]


def _kernel() -> Kernel:
    """造一个内核（待开始态，固定种子）。

    **注入规则集解析器**（ADR-061）：`switchRuleSet` 的规则集**由宿主给**
    ——**没注入解析器 = 一条都换不了**（`unknown_rule_set`）。这里给的是
    **标准规则集自己**（换回同一条规则）——**确定性的门要的是「这条命令的
    路径进了指纹」，不是「换成别的玩法」**（变体规则集由
    `tests/test_m3_rule_set.py` 的替身规则集验）。
    """
    return Kernel(
        seed=SEED,
        piece_types=PIECE_TYPES,
        rule_set_resolver=lambda rule_id: STANDARD_RULESET if rule_id == "standard" else None,
    )


def _run(commands: tuple[tuple[str, Args], ...]) -> tuple[tuple[object, ...], list[object]]:
    """跑一串命令，返回 (四项统计, 事件流序列)。

    `tick` 之后**完整消费**帧推进的五步（架构 §6.2：步骤序列必须被耗尽）。
    """
    kernel = _kernel()
    events: list[object] = []
    for command_type, args in commands:
        events.extend(_record(kernel.execute(Command(command_type, args=args, source="test"))))
        if command_type == "tick":
            for batch in kernel.advance_frame():
                events.extend(_record(batch))
    snapshot = kernel.snapshot()
    stats = (
        snapshot.board.structureFingerprint,
        snapshot.score,
        snapshot.lines,
        snapshot.piecesPlaced,
    )
    return stats, events


# ---------------------------------------------------------------------------
# G0-3：同种子 10 次结果全等
# ---------------------------------------------------------------------------


def test_determinism_same_seed() -> None:
    """G0-3：同种子 **10 次**跑同一串命令，四项全等 + **事件流全等**。

    **四项**：棋盘结构指纹 · score · lines · piecesPlaced（需求 §12 的口径）。
    **事件流**：类型 + 负载逐条相等——只比四项会漏掉"状态一样但事件流分叉"的
    回归（回放对齐靠的是事件流）。
    """
    results = [_run(COMMAND_SEQUENCE) for _ in range(RUNS)]
    first_stats, first_events = results[0]

    # 这串命令**真的干活了**——不是空跑（否则测的是"什么都没发生也一致"）。
    assert first_stats[3] >= 1, f"命令序列没锁定过任何方块：{first_stats}"
    assert any(name == "piece:locked" for name, _ in first_events), "序列里没有锁定事件"

    for index, (stats, events) in enumerate(results[1:], start=2):
        assert stats == first_stats, f"第 {index} 次的四项与第 1 次不等：{stats} ≠ {first_stats}"
        assert events == first_events, f"第 {index} 次的事件流与第 1 次不等"


# ---------------------------------------------------------------------------
# 属性测试（ADR-013 的两条纪律）
# ---------------------------------------------------------------------------


def test_hypothesis_profile_is_derandomized() -> None:
    """ADR-053：hypothesis 的 profile **必须**开着 `derandomize=True`。

    **只固定内核种子不够**——hypothesis 自己的生成随机性也是变量，不关掉它，CI
    上的偶发失败无法复现。profile 在 `tests/conftest.py` 导入期加载（那里是
    **唯一**的落点，两个写手都不许在别处另设）；这条用例把"它确实生效了"钉住。
    """
    from hypothesis import settings

    assert settings.default.derandomize is True, "hypothesis 没开 derandomize（ADR-053）"


@st.composite
def _commands(draw: st.DrawFn) -> Command:
    """生成一条**合法参数**的命令（参数不合规格是另一条路径，不在这里混进来）。"""
    command_type = draw(st.sampled_from(COMMAND_TYPES))
    args: Args
    if command_type == "setLevel":
        args = (("level", draw(st.integers(min_value=1, max_value=20))),)
    elif command_type == "injectGarbage":
        args = (
            ("rows", draw(st.integers(min_value=0, max_value=2))),
            ("holes", draw(st.tuples(st.integers(min_value=0, max_value=9)))),
        )
    elif command_type == "writeCells":
        args = (
            (
                "cells",
                draw(
                    st.tuples(
                        st.tuples(
                            st.integers(min_value=0, max_value=9),
                            st.integers(min_value=0, max_value=21),
                            st.integers(min_value=0, max_value=1),
                        )
                    )
                ),
            ),
        )
    else:
        args = ()
    return Command(command_type, args=args, source="test")


@given(commands=st.lists(_commands(), max_size=25))
def test_property_same_sequence_gives_the_same_result(commands: list[Command]) -> None:
    """**同序列重跑指纹一致**：状态与事件流逐条相等。

    这是 G0-3 的**属性版**：门测试用一条固定序列，属性测试用随机序列——手写覆盖
    不到的空间靠它（ADR-013）。
    """
    plan = tuple((command.type, command.args) for command in commands)
    first_stats, first_events = _run(plan)
    second_stats, second_events = _run(plan)
    assert first_stats == second_stats, f"{first_stats} ≠ {second_stats}"
    assert first_events == second_events, "事件流分叉了"


@given(commands=st.lists(_commands(), max_size=25))
def test_property_execution_is_consistent(commands: list[Command]) -> None:
    """**不崩** + **无静默状态变更** + **拒绝不改状态**。

    - 不崩：内核对任何命令序列都不抛异常（命令全走契约声明的通道）；
    - 无静默状态变更：版本号递增了就必有语义事件，且递增次数 ≤ 语义事件条数；
    - 拒绝不改状态：被拒的命令执行前后游戏状态逐字段相等。
    """
    kernel = _kernel()
    for command in commands:
        before = kernel.snapshot()
        drafts = kernel.execute(command)
        after = kernel.snapshot()
        types = [draft.eventType for draft in drafts]
        semantic = [name for name in types if name not in COMMAND_LIFECYCLE_EVENTS]
        accepted = "command:accepted" in types
        rejected = "command:rejected" in types

        assert accepted != rejected, f"判决不明：{types}"
        if rejected:
            assert after.version == before.version, f"被拒的命令改了版本号：{types}"
            assert _game_state(after) == _game_state(before), f"被拒的命令改了状态：{types}"
            continue
        assert semantic, f"状态可能变了却一条语义事件都没有：{types}"
        increments = max(0, after.version - before.version)
        assert increments <= len(semantic), (
            f"版本号递增 {increments} 次，语义事件只有 {len(semantic)} 条：{types}"
        )

        # `tick` 之后完整消费帧推进的五步（架构 §6.2 / B-9）。
        if command.type == "tick":
            before_version = kernel.snapshot().version
            for batch in kernel.advance_frame():
                after_version = kernel.snapshot().version
                step_types = [draft.eventType for draft in batch]
                step_semantic = [
                    name for name in step_types if name not in COMMAND_LIFECYCLE_EVENTS
                ]
                increments = max(0, after_version - before_version)
                if increments > 0:
                    assert step_semantic, f"帧推进改了状态却没发事件：{step_types}"
                assert increments <= len(step_semantic), (
                    f"帧推进递增 {increments} 次，语义事件只有 {len(step_semantic)} 条"
                )
                before_version = after_version
