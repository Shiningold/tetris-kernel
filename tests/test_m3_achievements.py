"""成就系统测试（M3-S5 · FR-9.3 · `G3-4`）。

覆盖 plan-M3.md S5 的验证方案：

1. **触发**：喂一条达成条件的假事件流 → 发出 `achievement:unlocked`，
   **负载四字段齐全**（尤其 `triggerType` 与 `triggerSeq`）；
2. **负载逐条相等**：字段名与顺序**照抄契约**（改一个字段名 → 红）；
3. **反向验证 A**：把成就条件改成**永远不满足** → 一条都不许解锁
   （这条用例就是「条件不满足」的守卫）；
4. **只解锁一次**：达成之后不再重复发；
5. **不自激**：自产的 `achievement:unlocked` 不进计数；
6. **普通订阅者**：**没有写内核状态的路径**（`view_provider` 一次都没被调用）；
7. **冻结区基线同步**：内核文件与基线逐字节全等（**注意**：这守的是「改动
   与基线同步」，不是「M3 没改内核」——M3 的换规则集入口**确实改过**内核，
   基线已随之刷新 · M3 审查报告 L2）；
8. **声明自证**：声明里的订阅类型必须已登记（跨文件自证）。

**单测不起内核**：整份文件只用一条**不带视图**的总线喂假事件流——
「单元测可脱离内核」是硬要求（达成条件判定不该需要一局真游戏）。
"""

from __future__ import annotations

import locale
import subprocess
import sys
from pathlib import Path

from tetris_kernel.bus.bus import EventBus
from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.contracts.events import EVENT_TYPES
from tetris_kernel.contracts.systems import GLOBAL_SUBSCRIPTION, check_manifest_types
from tetris_kernel.kernel.drafts import EventDraft, make_draft
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.systems.achievements.definitions import DEFAULT_ACHIEVEMENTS, Achievement
from tetris_kernel.systems.achievements.manifest import SYSTEM_MANIFEST, create_system
from tetris_kernel.systems.achievements.system import ACHIEVEMENT_UNLOCKED_EVENT, AchievementsSystem
from tetris_kernel.systems.context import CommandChannel, InstallContext

#: 仓库根目录（`tests/` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 几种事件的**占位负载**（字段名与顺序照抄契约声明）。
#:
#: **走 `make_draft` 造草稿**：负载字段与契约逐条相等由它当场校验——
#: 测试里用假负载造事件的话，契约改了测试也发现不了。
_SAMPLE_PAYLOADS: dict[str, tuple[tuple[str, object], ...]] = {
    "piece:locked": (
        ("pieceType", "T"),
        ("rotation", 0),
        ("x", 3),
        ("y", 17),
        ("cells", ((3, 17),)),
        ("tSpin", "none"),
        ("cause", "ground"),
    ),
    "piece:harddrop": (
        ("pieceType", "T"),
        ("distance", 4),
        ("x", 3),
        ("y", 17),
        ("cells", ((3, 17),)),
    ),
    "piece:held": (("stored", "T"), ("taken", None)),
    "piece:spawned": (
        ("pieceType", "T"),
        ("x", 3),
        ("y", 0),
        ("next", ("I", "O")),
    ),
}


def _draft(event_type: str, frame: int = 1) -> EventDraft:
    """造一条**合法**草稿（负载字段与契约逐条相等）。"""
    payload = _SAMPLE_PAYLOADS[event_type]
    return make_draft(event_type, payload, frame)  # type: ignore[arg-type]


def _bus(**kwargs: object) -> EventBus[object]:
    """一条**不带视图**的总线——成就系统不该看视图（M3-D3）。"""
    return EventBus[object](**kwargs)  # type: ignore[arg-type]


def _counting_bus(calls: list[int]) -> EventBus[object]:
    """一条**带计数视图**的总线——`view_provider` 每被调一次就记一笔。"""

    def provider() -> object:
        calls.append(1)
        return object()

    return EventBus[object](view_provider=provider)


def _unlocked_events(bus: EventBus[object]) -> tuple[Envelope, ...]:
    """总线历史里**成就解锁事件**（按发布顺序）。"""
    return tuple(item for item in bus.history() if item.type == ACHIEVEMENT_UNLOCKED_EVENT)


# ---------------------------------------------------------------------------
# 触发与负载
# ---------------------------------------------------------------------------


def test_the_achievement_unlocks_when_the_condition_is_met() -> None:
    """达成条件时**照发** `achievement:unlocked`（`G3-4` 的兑现）。"""
    bus = _bus()
    system = AchievementsSystem(bus, (Achievement("first-lock", "首次落子", "piece:locked", 1),))
    bus.subscribe(GLOBAL_SUBSCRIPTION, system.on_event)

    bus.publish(_draft("piece:locked"))

    events = _unlocked_events(bus)
    assert len(events) == 1, "条件达成了却没发解锁事件"
    payload = dict(events[0].payload)
    assert payload["id"] == "first-lock"
    assert payload["title"] == "首次落子"
    assert payload["triggerType"] == "piece:locked"


def test_the_payload_matches_the_contract_field_by_field() -> None:
    """解锁事件的负载**字段名与顺序照抄契约**（声明与实现不许分叉）。

    **反向验证**：把负载删一个字段 → `make_draft` 当场报错——
    这条断言就是那道牙（plan-M3 S5 验证方案第 5 条）。
    """
    bus = _bus()
    system = AchievementsSystem(bus, (Achievement("first-lock", "首次落子", "piece:locked", 1),))
    bus.subscribe(GLOBAL_SUBSCRIPTION, system.on_event)

    bus.publish(_draft("piece:locked"))

    events = _unlocked_events(bus)
    assert len(events) == 1
    expected = tuple(field.name for field in EVENT_TYPES[ACHIEVEMENT_UNLOCKED_EVENT].payload)
    actual = tuple(name for name, _ in events[0].payload)
    assert actual == expected, f"负载字段与契约不一致：{actual} ≠ {expected}"


def test_the_trigger_seq_is_the_seq_of_the_triggering_event() -> None:
    """`triggerSeq` 记的是**触发它的那条事件的全局序号**（事件溯源的时间轴）。"""
    bus = _bus()
    system = AchievementsSystem(bus, (Achievement("first-lock", "首次落子", "piece:locked", 1),))
    bus.subscribe(GLOBAL_SUBSCRIPTION, system.on_event)

    bus.publish(_draft("piece:locked"))
    bus.publish(_draft("piece:locked"))

    locked = [item for item in bus.history() if item.type == "piece:locked"]
    events = _unlocked_events(bus)
    assert len(events) == 1
    assert dict(events[0].payload)["triggerSeq"] == locked[0].seq


def test_a_condition_that_is_never_met_unlocks_nothing() -> None:
    """**反向验证**：条件永远不满足 → **一条都不许解锁**。

    这条用例守的是「达成判定真的看条件」——把判定写死成「收到就解锁」，
    这条立刻红。
    """
    bus = _bus()
    system = AchievementsSystem(bus, (Achievement("never", "永不达成", "piece:locked", 1000),))
    bus.subscribe(GLOBAL_SUBSCRIPTION, system.on_event)

    for _ in range(5):
        bus.publish(_draft("piece:locked"))

    assert _unlocked_events(bus) == (), "条件没满足却解锁了"
    assert system.unlocked == ()


def test_an_achievement_unlocks_only_once() -> None:
    """达成之后**不再重复解锁**（否则每条后续事件都发一遍）。"""
    bus = _bus()
    system = AchievementsSystem(bus, (Achievement("first-lock", "首次落子", "piece:locked", 1),))
    bus.subscribe(GLOBAL_SUBSCRIPTION, system.on_event)

    for _ in range(3):
        bus.publish(_draft("piece:locked"))

    assert len(_unlocked_events(bus)) == 1
    assert system.unlocked == ("first-lock",)


def test_the_system_does_not_count_its_own_events() -> None:
    """**不自激**：自产的 `achievement:unlocked` 不进计数。"""
    bus = _bus()
    system = AchievementsSystem(bus, (Achievement("first-lock", "首次落子", "piece:locked", 1),))
    bus.subscribe(GLOBAL_SUBSCRIPTION, system.on_event)

    bus.publish(_draft("piece:locked"))

    assert system.count_of(ACHIEVEMENT_UNLOCKED_EVENT) == 0, "自产事件被自己计数了"
    assert len(_unlocked_events(bus)) == 1


def test_the_system_never_touches_the_kernel_state() -> None:
    """普通订阅者：**没有写内核状态的路径**（M3-D3）。

    **机制级断言**：`view_provider` **一次都没被调用**——成就系统一旦改成
    `ctx.view()` 查内核状态，这条立刻红。
    """
    calls: list[int] = []
    bus = _counting_bus(calls)
    system = AchievementsSystem(bus, (Achievement("first-lock", "首次落子", "piece:locked", 1),))
    bus.subscribe(GLOBAL_SUBSCRIPTION, system.on_event)

    bus.publish(_draft("piece:locked"))

    assert calls == [], "成就系统取了内核视图——它必须是纯订阅者"


def test_the_achievement_system_never_touches_the_filesystem(tmp_path: Path) -> None:
    """成就是**事件**，不是文件——**一个文件都不碰**（`needsSink=False`）。"""
    before = set(tmp_path.rglob("*"))
    bus = _bus()
    system = AchievementsSystem(bus, (Achievement("first-lock", "首次落子", "piece:locked", 1),))
    bus.subscribe(GLOBAL_SUBSCRIPTION, system.on_event)

    bus.publish(_draft("piece:locked"))

    assert set(tmp_path.rglob("*")) == before, "成就系统碰了文件系统"


# ---------------------------------------------------------------------------
# 声明
# ---------------------------------------------------------------------------


def test_the_manifest_declares_what_the_system_does() -> None:
    """声明**逐条等于**这个系统实际做的事（订阅全局 · 不派命令 · 不落盘）。"""
    assert SYSTEM_MANIFEST.name == "achievements"
    assert SYSTEM_MANIFEST.version == "1"
    assert SYSTEM_MANIFEST.subscribes == (GLOBAL_SUBSCRIPTION,)
    assert SYSTEM_MANIFEST.commands == ()
    assert SYSTEM_MANIFEST.producer == "achievements"
    assert SYSTEM_MANIFEST.needsSink is False


def test_the_declaration_self_proves() -> None:
    """**自证**：声明里的事件 / 命令类型必须已登记（跨文件自证）。"""
    problems = check_manifest_types(
        SYSTEM_MANIFEST,
        known_events=EVENT_TYPES.keys(),
        known_commands=(),
    )
    assert not problems, f"声明不自证：{problems}"


def test_the_factory_builds_a_system_without_the_kernel() -> None:
    """工厂**只拿总线**就能造出系统（上下文里没有内核 · M3-D3）。"""
    system = create_system(
        InstallContext(
            name="achievements",
            bus=EventBus[Snapshot](),
            commands=CommandChannel(lambda command: None),
            config=(),
        )
    )
    assert isinstance(system, AchievementsSystem)


# ---------------------------------------------------------------------------
# 冻结区基线同步
# ---------------------------------------------------------------------------


def test_the_frozen_zone_matches_the_baseline() -> None:
    """**冻结区与基线逐文件哈希全等**（`G3-4`）。

    **这条断言到底断言什么**（M3 审查报告 L2 指出的命名误导）：它断言的是
    **「当前工作区的内核文件与 `kernel_hashes.json` 基线逐字节一致」**——
    **不是**「M3 没改过内核」。

    **M3 确实改过内核**（`kernel/core.py` · `kernel/commands.py`）——那是
    **M3 唯一一次冻结区破例**（ADR-061 的运行期换规则集入口），**基线已随之
    显式刷新**（S8 · `c81d0cc`）。**基线绿 = 改动与基线同步了**，不等于
    「M3 没改内核」。

    **成就系统本身确实是零内核改动**——那是**它的实现**（`systems/
    achievements/` 三个新增文件）的性质；**这条断言守的是「基线同步」**。

    跑的是**工具本身**（子进程调 `python tools/check_frozen_baseline.py`），
    不是把哈希逻辑抄一份到测试里。
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "check_frozen_baseline.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    encoding = locale.getpreferredencoding(False)
    message = result.stdout.decode(encoding, errors="replace") + result.stderr.decode(
        encoding, errors="replace"
    )
    assert result.returncode == 0, f"冻结区变了：\n{message}"


def test_the_built_in_definitions_unlock_as_written() -> None:
    """**内建成就清单**照写的那样能解锁（不是只校验字段合法）。

    **为什么这条不许省**：只校验「编号非空 · 事件类型已登记」的话，
    清单里写一个**永远达不到的阈值**照样全绿——**内建清单自己就废了**，
    而没人看得出来。这条用例拿**内建清单**跑一遍最浅的那个成就。
    """
    bus = _bus()
    system = AchievementsSystem(bus)
    bus.subscribe(GLOBAL_SUBSCRIPTION, system.on_event)

    bus.publish(_draft("piece:locked"))

    assert "first-lock" in system.unlocked, "内建成就清单照写的那样解锁不了"
    events = _unlocked_events(bus)
    assert len(events) == 1
    assert dict(events[0].payload)["id"] == "first-lock"


def test_the_definitions_are_written_by_event_type_and_count() -> None:
    """成就定义**按事件类型与计数写**（不写死「标准玩法」的名字）。

    **变体复用**：定义里出现玩法名 → 变体那边就不成立了（plan-M3 S5 常见坑）。
    """
    for achievement in DEFAULT_ACHIEVEMENTS:
        assert achievement.trigger_type in EVENT_TYPES, achievement.id
        assert achievement.threshold >= 1, achievement.id
        assert "标准" not in achievement.title, achievement.id
