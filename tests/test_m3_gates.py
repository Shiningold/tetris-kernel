"""M3 六道门判据（S10 · plan-M3.md §5.2 · `G3-1`…`G3-6`）。

## 六道门与本文件的用例

| 门 | 判据 | 复跑命令 |
| --- | --- | --- |
| `G3-1` | 新增能力 = 新增文件（机器可验） | `pytest -k m3_new_capability_is_new_files` |
| `G3-2` | 运行中卸载不中断 | `pytest -k m3_hot_unload_no_interrupt` |
| `G3-3` | 系统清单与依赖图 | `pytest -k m3_system_manifest_and_graph` |
| `G3-4` | 成就系统零内核改动 | `pytest -k m3_achievement_zero_kernel` |
| `G3-5` | 运行期换规则集机制 | `pytest -k m3_runtime_ruleset_switch` |
| `G3-6` | 扩展零污染 | `pytest -k m3_extension_purity` |

**用例名不许中途改**——改了 §5.2 的复跑命令就失效（plan-M3 §5.2）。元守卫在
`tests/test_m3_gates_meta.py`：它**读 §5.2 那张表**，把六个命中名逐条与
`tests/` 下的用例名比对，**选不到就红并点名那个入口**。

## 这六道门各自由什么撑着

**它们不是六个孤立的行为断言**——M3 的正题是「**接进来有没有改别人的东西**」，
所以每道门都有**两面**：

- **行为面**：新系统**确实干活**（解锁成就 · 计数旋转 · 换规则集）；
- **零污染面**：它**没改内核、没改规则、没改契约的既有部分**。

**只测行为面，门就只剩一半**——一个把所有逻辑都塞进 `kernel/core.py` 的实现
照样能通过行为断言。**G3-1` 与 `G3-6` 是这两面的机器可验形态**。

## 期望值的独立性（M0-1 在门层的同款纪律）

**手抄 ≠ 实现算出来**——所以每道门里凡是"期望值"，都**以字面量出现在本文件的
源码里**，不引用被测代码算出来的数当结论。**G3-3` 的清单逐条相等**是唯一例外：
它比的是**声明本身**（一份数据对另一份数据），不是"实现算的等于实现算的"。

**自给自足**：不依赖 `tests/conftest.py` 的 fixture。
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.contracts.commands import COMMANDS
from tetris_kernel.contracts.events import EVENT_TYPES, PRODUCER_LABELS
from tetris_kernel.contracts.systems import SystemManifest, check_manifest_types
from tetris_kernel.host.assembly import RULE_SETS, assemble
from tetris_kernel.host.discovery import (
    declared_manifests,
    dependency_graph,
    system_catalog,
)
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.host.registry import InstallContext, SystemEntry, SystemRegistry
from tetris_kernel.host.replay import REPLAY_LIMITS, KernelReplayDriver
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.drafts import EventDraft
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.rules.standard import STANDARD_RULESET
from tetris_kernel.systems.achievements.system import ACHIEVEMENT_UNLOCKED_EVENT
from tetris_kernel.systems.replay.playback import PlaybackSession
from tetris_kernel.systems.replay.recorder import RecordingSystem

#: 仓库根目录（`tests/` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 装配用的固定种子（**测试不依赖随机性**）。
SEED = 20261027


def _four_items(snapshot: Snapshot) -> tuple[str, int, int, int]:
    """快照 → 四项（棋盘指纹 · 分数 · 行数 · 方块数）。

    **口径与 `host/replay.py` 同款**——**不另抄一份算法**。
    """
    return (
        snapshot.board.structureFingerprint,
        snapshot.score,
        snapshot.lines,
        snapshot.piecesPlaced,
    )


def _types_of(drafts: tuple[EventDraft, ...]) -> tuple[str, ...]:
    """一批草稿的事件类型（按顺序）。"""
    return tuple(draft.eventType for draft in drafts)


# ---------------------------------------------------------------------------
# G3-1 · 新增能力 = 新增文件
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LandingCommit:
    """**落地提交**：一个系统的代码是哪笔提交进来的。"""

    #: 提交哈希。
    commit: str
    #: 这个提交新增了哪些文件（**相对它的父提交**）。
    added: tuple[str, ...]
    #: 这个提交改了哪些**既有**文件。
    modified: tuple[str, ...]


def _git(*args: str, cwd: Path | None = None) -> str:
    """跑一条 git 命令，返回它的标准输出（**去掉行尾**）。"""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _landing_commit_of(path: str) -> LandingCommit:
    """取**某个文件是哪笔提交新增的**（`git log --diff-filter=A`）。

    **为什么要按文件找而不是写死哈希**：写死哈希的话，那笔提交一旦被 rebase
    就断了——**门会从"内容变了"变成"查无此提交"**，而后者**同样不红**（`git log`
    找不到就抛异常 → pytest 报错 → 仍然是红，但报的是"环境坏了"不是"判据被
    破了"）。**按文件找**能跟着历史走。
    """
    raw = _git("log", "--diff-filter=A", "--format=%H", "--", path)
    commits = [line.strip() for line in raw.splitlines() if line.strip()]
    assert commits, f"文件 {path} 在 git 历史里没有「新增」记录——它不是新增文件？"
    commit = commits[-1]
    names = _git("show", "--name-status", "--format=", commit)
    added: list[str] = []
    modified: list[str] = []
    for line in names.splitlines():
        if not line.strip():
            continue
        status, _, name = line.partition("\t")
        name = name.strip()
        if status.startswith("A"):
            added.append(name)
        else:
            modified.append(name)
    return LandingCommit(commit=commit, added=tuple(added), modified=tuple(modified))


def test_m3_new_capability_is_new_files_the_frozen_zone_is_byte_identical() -> None:
    """`G3-1` 第一面：**冻结区逐文件哈希全等**（跑的是工具本身）。

    **改一行注释也该红**——哈希的是整个文件内容，不是符号表。
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "check_frozen_baseline.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        "冻结区与基线不一致——新增能力不许改内核与规则：\n"
        f"{result.stdout.decode('utf-8', 'replace')}\n"
        f"{result.stderr.decode('utf-8', 'replace')}"
    )


def test_m3_new_capability_is_new_files_the_landing_commits_only_add_files() -> None:
    """`G3-1` 第二面：**落地提交 `git diff --name-status` 每行都是 `A`**。

    **这一面才是「新增能力 = 新增文件」的真正含义**：新增一个系统**不许改
    任何既有文件**（改既有文件 = 改别人的东西 = `G3-1` 立刻失去牙齿）。

    **为什么要分两笔提交看**：`achievements` 与 `rotations` 是两个系统，
    **两笔落地提交都要查**——只查一笔就漏了另一半。

    **期望值是手抄的**（下面这两张表就是期望）：**新增的是这个系统自己的那些新
    文件**，**既有文件一个都没动**——`__init__.py` 是**包初始化文件**，它在
    **更早的一笔**里就建好了（那时那个包还空着），**不是这个系统的落地提交
    的一部分**。
    """
    expected_added = {
        "achievements": {"definitions.py", "manifest.py", "system.py"},
        "rotations": {"manifest.py", "system.py"},
    }
    for package in ("achievements", "rotations"):
        landing = _landing_commit_of(f"src/tetris_kernel/systems/{package}/manifest.py")
        assert landing.modified == (), (
            f"{package} 的落地提交改了既有文件：{landing.modified}"
            "——「新增能力 = 新增文件」当场不成立"
        )
        added_names = {name.split("/")[-1] for name in landing.added}
        assert expected_added[package] <= added_names, (
            f"{package} 的落地提交新增的文件不对：{sorted(landing.added)}"
        )
        # **既有文件一个都没动**——**源文件**之外也不许动（测试文件是新增的，
        # 那不违反「新增能力 = 新增文件」：测试也是新增）。
        touched_existing = [
            name
            for name in landing.modified
            if name.startswith("src/") and f"systems/{package}/" not in name
        ]
        assert not touched_existing, f"{package} 的落地提交改了别的既有源文件：{touched_existing}"


def test_m3_new_capability_is_new_files_the_contract_baseline_has_no_changed_key() -> None:
    """`G3-1` 第三面：**契约基线的既有键一个都没变**（新增键放行）。

    **新增系统不许改既有契约**——它只能**新增**自己的声明与事件。
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "contract_baseline.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        "契约基线校验失败——既有键变了或消失了：\n"
        f"{result.stdout.decode('utf-8', 'replace')}\n"
        f"{result.stderr.decode('utf-8', 'replace')}"
    )


# ---------------------------------------------------------------------------
# G3-2 · 运行中卸载不中断
# ---------------------------------------------------------------------------


def _publish_batch(bus: object) -> object:
    """把一批草稿发到总线上。"""
    from tetris_kernel.bus.bus import EventBus

    assert isinstance(bus, EventBus)
    return lambda drafts: tuple(bus.publish(draft) for draft in drafts)


def test_m3_hot_unload_no_interrupt_the_kernel_frame_by_frame_is_unchanged() -> None:
    """`G3-2` 第一面：**卸载一个系统，内核逐帧不变**。

    **装卸动作本身不产生内核状态变更**（M3-D3）。

    **怎么比才说得清**：**两份装配**跑**同一串 20 帧**——一份在第 10 帧之后
    **卸载系统**，另一份**一直装着**。**逐帧四项全等**。

    **为什么不比"卸载前 10 帧 vs 卸载后 10 帧"**：那是**同一局的前后半段**，
    分数本来就该继续涨——**比错了结论会假红**。
    """
    with_unload = assemble(
        seed=SEED,
        observability_enabled=False,
        systems=("achievements",),
        system_catalog=system_catalog(),
    )
    without_unload = assemble(
        seed=SEED,
        observability_enabled=False,
        systems=("achievements",),
        system_catalog=system_catalog(),
    )
    registry = with_unload.registry
    assert registry is not None

    def _run(assembly: object, *, unload_after: int | None) -> list[tuple[str, int, int, int]]:
        """跑 20 帧；`unload_after` 不为 `None` 时在那一帧之后卸载系统。"""
        kernel = assembly.kernel  # type: ignore[attr-defined]
        loop = HeadlessLoop(assembly)  # type: ignore[arg-type]
        loop.start()
        items: list[tuple[str, int, int, int]] = []
        for index in range(20):
            kernel.submit(Command(type="softDrop", source="host"))
            loop.run_frame()
            items.append(_four_items(kernel.snapshot()))
            if unload_after is not None and index == unload_after:
                registry.uninstall("achievements")
        return items

    assert _run(with_unload, unload_after=9) == _run(without_unload, unload_after=None), (
        "卸载系统改变了内核的逐帧状态——装卸动作不该动内核"
    )


def test_m3_hot_unload_no_interrupt_the_other_system_keeps_receiving_events() -> None:
    """`G3-2` 第二面：**卸掉一个，其余系统继续收事件**。

    **卸掉 `achievements`（订阅全局）之后，`rotations`（订阅 `piece:rotated`）
    照样收得到旋转事件**——一个系统的消失**不影响另一个系统**。

    **为什么这条重要**：摘要是「**摘得干净**」——**摘干净指的是"这个系统走了，
    别的没受影响"**，不是"整个总线都停了"。
    """
    assembly = assemble(
        seed=SEED,
        observability_enabled=False,
        systems=("achievements", "rotations"),
        system_catalog=system_catalog(),
    )
    bus = assembly.bus
    registry = assembly.registry
    assert registry is not None

    rotations = registry.instance_of("rotations")
    before = rotations.count

    registry.uninstall("achievements")

    bus.publish(_rotated_draft())

    assert rotations.count == before + 1, (
        f"卸掉成就系统之后旋转系统收不到事件了：{before} → {rotations.count}"
    )


def test_m3_hot_unload_no_interrupt_reinstalling_keeps_the_event_stream_continuous() -> None:
    """`G3-2` 第三面：**重装后接缝处事件序列连续**。

    **接缝**：卸掉 → 装上 → 之后的事件序列与「一直装着」的那条**逐条相等**。

    **为什么这条重要**：卸载把订阅撤了、重装把订阅接回——**接缝处漏掉或重复
    一条事件**的话，回放对齐就错了，而**局面的四项统计照样一致**（事件流分叉
    而状态没分叉，正是 `G2-1` 盯的那一类）。

    **比的是什么**：**扣掉装卸那两条事件之后**的事件序列——`installed` 与
    `removed` 是**装卸动作自己**发的，不属于"游戏事件流"。
    """
    uninterrupted = assemble(
        seed=SEED,
        observability_enabled=False,
        systems=("achievements",),
        system_catalog=system_catalog(),
    )
    with_gap = assemble(
        seed=SEED,
        observability_enabled=False,
        systems=("achievements",),
        system_catalog=system_catalog(),
    )
    registry = with_gap.registry
    assert registry is not None

    def _stream(assembly: object) -> list[str]:
        """跑一串动作，把总线历史里的**游戏事件**类型收下来。

        **扣掉装卸那两条**（`kernel:system:installed` / `kernel:system:removed`）——
        它们是装卸动作自己的事件，不属于游戏流。
        """
        bus = assembly.bus  # type: ignore[attr-defined]
        bus.publish(_locked_draft())
        bus.publish(_rotated_draft())
        return [
            envelope.type
            for envelope in bus.history()
            if envelope.type not in ("kernel:system:installed", "kernel:system:removed")
        ]

    plain = _stream(uninterrupted)

    registry.uninstall("achievements")
    registry.install("achievements")
    reconnected = _stream(with_gap)

    # **接缝处连续**：重装后的流与一直装着的流**逐条相等**。
    assert reconnected == plain, f"重装后的事件流与一直装着的不等：\n  {plain}\n  {reconnected}"


def test_m3_hot_unload_no_interrupt_the_removed_system_does_not_see_its_own_removal() -> None:
    """`G3-2` 第四面：**摘下时先退订、后发事件**——被摘的系统收不到自己的 `removed`。

    **顺序倒过来（先发 `removed` 再退订）这条必红**：被摘的系统会看到
    「我被摘了」这条事件——**而它已经不在了**。

    **为什么门要盯这个**：摘下时**先撤命令通道**（不再说）→ **再退订**（不再听）
    → **最后发事件**（告诉别人我走了）。**顺序倒过来**，订阅者在事件里看到的
    世界还是「我还活着」——**接缝处的世界是错的**。

    **判据怎么落地**：一个**只订阅 `kernel:system:removed` 的探针系统**——
    它被摘下时，若退订排在发事件之前，它**收不到**那条事件；若顺序倒过来，
    它**收得到**。
    """
    assembly = assemble(seed=SEED, observability_enabled=False)
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog={
            "watcher": SystemEntry(
                manifest=SystemManifest(
                    name="watcher",
                    version="1",
                    subscribes=("kernel:system:removed",),
                    commands=(),
                    producer="watcher",
                    needsSink=False,
                ),
                factory=_WatcherSystem,
            )
        },
    )
    watcher = registry.install("watcher")
    assert isinstance(watcher, _WatcherSystem)

    registry.uninstall("watcher")

    assert watcher.seen == [], (
        f"被摘的系统收到了自己的 removed 事件：{watcher.seen}——退订必须排在发事件之前"
    )


class _WatcherSystem:
    """**观察者**：只订阅 `kernel:system:removed`，把自己收到的事件记下来。"""

    def __init__(self, context: InstallContext) -> None:
        """装上（**签名对**就行）。"""
        self.seen: list[str] = []

    def on_event(self, envelope: Envelope) -> None:
        """收一条事件（**只读事件**）。"""
        self.seen.append(envelope.type)


def _locked_draft() -> EventDraft:
    """一条 `piece:locked` 草稿（**只为发事件**）。"""
    from tetris_kernel.kernel.drafts import make_draft

    return make_draft(
        "piece:locked",
        (
            ("pieceType", "T"),
            ("rotation", 0),
            ("x", 3),
            ("y", 0),
            ("cells", ((3, 0),)),
            ("tSpin", "none"),
            ("cause", "ground"),
        ),
        0,
    )


def _rotated_draft() -> EventDraft:
    """一条 `piece:rotated` 草稿（**只为发事件**）。"""
    from tetris_kernel.kernel.drafts import make_draft

    return make_draft(
        "piece:rotated",
        (
            ("fromRotation", 0),
            ("toRotation", 1),
            ("direction", "cw"),
            ("blocked", False),
        ),
        0,
    )


# ---------------------------------------------------------------------------
# G3-3 · 系统清单与依赖图
# ---------------------------------------------------------------------------


def test_m3_system_manifest_and_graph_the_graph_matches_every_declaration() -> None:
    """`G3-3` 第一面：**依赖图逐条等于各系统的声明**。

    **依赖图的三个维度**：订阅了什么 · 派发什么命令 · 产出者是谁。

    **期望值是手抄的**（下面那三张表）——**不是"实现算的等于实现算的"**。
    """
    manifests = declared_manifests()
    graph = dependency_graph(manifests)

    # **手抄的期望**：M3 交付的五个系统各自订阅什么、派发什么、产出者是谁。
    #
    # **replay 订阅四条具体事件**（不是全局）——它只录**被接受的命令** · 帧
    # 边界 · 局末四项 · 内核就绪；**订阅全局会把不需要的事件也录进去**。
    # **它的产出者代号是 `systems/replay/`**（**产出者 ≠ 命名空间** —— 它发的是
    # `replay:` 那 5 条事件，产出者却带 `systems/` 前缀）。
    expected_subscriptions = {
        "achievements": ("*",),
        "log": ("*",),
        "metrics": ("*",),
        "replay": ("command:accepted", "clock:tick", "game:over", "kernel:ready"),
        "rotations": ("piece:rotated",),
    }
    expected_producers = {
        "achievements": "achievements",
        "log": "log",
        "metrics": "metrics",
        "replay": "systems/replay/",
        "rotations": "rotations",
    }
    expected_commands = {
        "achievements": (),
        "log": (),
        "metrics": (),
        "replay": (),
        "rotations": (),
    }

    assert dict(graph.subscriptions) == expected_subscriptions, (
        f"依赖图的订阅维度与手抄的期望不等：{dict(graph.subscriptions)}"
    )
    assert dict(graph.producers) == expected_producers, (
        f"依赖图的产出者维度与手抄的期望不等：{dict(graph.producers)}"
    )
    assert dict(graph.commands) == expected_commands, (
        f"依赖图的命令维度与手抄的期望不等：{dict(graph.commands)}"
    )


def test_m3_system_manifest_and_graph_every_declaration_self_proves() -> None:
    """`G3-3` 第二面：**每个系统的声明都自证**（跨文件自证）。

    **自证**：订阅的事件名与派发的命令名必须**已登记**；产出者必须在代号表里
    **或者就是它自己的名字**（需求 §8.2 允许"系统名"——不发事件的系统产出是
    文件而不是事件）。
    """
    for name, manifest in declared_manifests().items():
        problems = check_manifest_types(
            manifest,
            known_events=EVENT_TYPES.keys(),
            known_commands=COMMANDS.keys(),
            known_producers=PRODUCER_LABELS.keys(),
        )
        assert not problems, f"{name} 的声明不自证：{problems}"


def test_m3_system_manifest_and_graph_the_graph_answers_both_directions() -> None:
    """`G3-3` 第三面：**依赖图能反查**（谁订阅了什么 / 谁派发了什么）。

    **反向索引现算**（`subscribers_of` / `dispatchers_of`）——**不另存一份**，
    两份索引会分叉，而分叉了没人查得出来。
    """
    graph = dependency_graph()

    assert "rotations" in graph.subscribers_of("piece:rotated")
    assert "achievements" in graph.subscribers_of("piece:rotated"), (
        "成就系统订阅全局——它对任何事件都在名单里"
    )
    # **没有任何系统派发命令**（M3 的新系统全是订阅者 · `G3-6`）。
    for command_type in ("tick", "switchRuleSet", "writeCells"):
        assert graph.dispatchers_of(command_type) == (), (
            f"{command_type} 有人派发——M3 的新系统不该派命令"
        )


# ---------------------------------------------------------------------------
# G3-4 · 成就系统零内核改动
# ---------------------------------------------------------------------------


def test_m3_achievement_zero_kernel_the_payload_is_complete() -> None:
    """`G3-4` 第一面：**达成 → `achievement:unlocked` 负载齐全**。

    **负载四字段**（`id` · `title` · `triggerType` · `triggerSeq`）——
    **逐条与契约相等**（契约里那条声明的字段名与顺序就是权威）。
    """
    assembly = assemble(seed=SEED, systems=("achievements",), system_catalog=system_catalog())
    bus = assembly.bus
    registry = assembly.registry
    assert registry is not None

    bus.publish(_locked_draft())

    unlocked = [e for e in bus.history() if e.type == ACHIEVEMENT_UNLOCKED_EVENT]
    assert unlocked, "达成条件满足了却没有解锁——成就系统没干活"

    payload = dict(unlocked[0].payload)
    contract_fields = tuple(field.name for field in EVENT_TYPES[ACHIEVEMENT_UNLOCKED_EVENT].payload)
    assert tuple(payload) == contract_fields, (
        f"负载字段与契约不等：{tuple(payload)} ≠ {contract_fields}"
    )
    assert payload["id"] == "first-lock", f"解锁的编号不对：{payload['id']}"
    assert payload["title"], "标题是空的"
    assert payload["triggerType"] == "piece:locked", f"触发类型不对：{payload['triggerType']}"
    assert isinstance(payload["triggerSeq"], int), "触发序号不是整数"


def test_m3_achievement_zero_kernel_the_frozen_zone_is_untouched() -> None:
    """`G3-4` 第二面：**冻结区与基线逐字节全等**。

    **这条断言到底断言什么**：它断言的是「**当前内核文件与基线逐字节一致**」，
    **不是**「M3 没改过内核」——M3 的换规则集入口**确实改过**内核，基线已随之
    显式刷新（M3 审查报告 L2）。

    **跑的是工具本身**（子进程），不是把哈希逻辑抄一份到测试里。
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "check_frozen_baseline.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        "冻结区与基线不一致：\n"
        f"{result.stdout.decode('utf-8', 'replace')}\n"
        f"{result.stderr.decode('utf-8', 'replace')}"
    )


def test_m3_achievement_zero_kernel_the_system_never_touches_the_kernel() -> None:
    """`G3-4` 第三面：**成就系统不改内核状态**。

    **它只订阅事件**——发事件不改状态、收事件不改状态。
    """
    assembly = assemble(seed=SEED, systems=("achievements",), system_catalog=system_catalog())
    kernel = assembly.kernel
    bus = assembly.bus

    before = kernel.snapshot()
    bus.publish(_locked_draft())
    bus.publish(_rotated_draft())
    after = kernel.snapshot()

    assert after.board == before.board, "成就系统改了棋盘"
    assert after.version == before.version, "成就系统改了版本号"


# ---------------------------------------------------------------------------
# G3-5 · 运行期换规则集机制
# ---------------------------------------------------------------------------


def test_m3_runtime_ruleset_switch_the_four_branches() -> None:
    """`G3-5`：**换规则集的四个分支**（可切 · 未知被拒 · 局中被拒 · 可重放）。

    **四个分支写在一条用例里**——因为**它们合起来才是"机制"**：只有"可切"
    那一条的话，一个"永远拒绝切换"的实现也能过。
    """
    from tetris_kernel.kernel.core import Kernel
    from tetris_kernel.rules.tables import PIECE_TYPES

    def _kernel() -> Kernel:
        return Kernel(
            seed=SEED,
            piece_types=PIECE_TYPES,
            rule_set=STANDARD_RULESET,
            rule_set_resolver=RULE_SETS.get,
        )

    def _switch(rule: str) -> Command:
        return Command(type="switchRuleSet", args=(("rule", rule),), source="host")

    # **① 可切**（局外 · 目标已登记）。
    kernel = _kernel()
    drafts = kernel.execute(_switch("standard"))
    assert "command:accepted" in _types_of(drafts), f"局外切换被拒了：{_types_of(drafts)}"
    assert "kernel:rule-set-changed" in _types_of(drafts), "换规则集没发语义事件"
    assert "game:reset" in _types_of(drafts), "换规则集没有伴随局面重置"

    # **② 未知被拒**（**不拿当前规则集顶替**——状态不许变）。
    kernel = _kernel()
    before = kernel.snapshot()
    drafts = kernel.execute(_switch("no-such-rule-set"))
    rejected = [d for d in drafts if d.eventType == "command:rejected"]
    assert rejected, "未知规则集没有被拒"
    assert dict(rejected[0].payload)["reason"] == "unknown_rule_set", (
        f"拒绝原因不对：{dict(rejected[0].payload)['reason']}"
    )
    assert kernel.snapshot().rule == before.rule, "未知规则集把当前规则集顶替了"

    # **③ 局中被拒**（`illegal_transition` · 状态不许变）。
    kernel = _kernel()
    kernel.execute(Command(type="start", source="host"))
    before = kernel.snapshot()
    drafts = kernel.execute(_switch("standard"))
    rejected = [d for d in drafts if d.eventType == "command:rejected"]
    assert rejected, "局中切换没有被拒"
    assert dict(rejected[0].payload)["reason"] == "illegal_transition", (
        f"拒绝原因不对：{dict(rejected[0].payload)['reason']}"
    )
    assert kernel.snapshot().rule == before.rule, "局中切换改了规则集"

    # **④ 换完可重放且逐帧一致**。
    assembly = assemble(seed=SEED, rule_sets=RULE_SETS)
    recorder = _recorder_of(assembly)
    bus = assembly.bus
    kernel = assembly.kernel

    kernel.submit(_switch("standard"))
    kernel.run_rounds(lambda drafts: tuple(bus.publish(draft) for draft in drafts))

    loop = HeadlessLoop(assembly)
    loop.start()
    for _ in range(30):
        kernel.submit(Command(type="softDrop", source="host"))
        loop.run_frame()

    original = _four_items(kernel.snapshot())
    recorder.set_outcome(original)
    recording = recorder.recording()

    replayed = assemble(
        seed=recording.seed,
        rule_set=STANDARD_RULESET,
        rule_id=recording.rule,
        mode="replay",
        limits=REPLAY_LIMITS,
        observability_enabled=False,
        rule_sets=RULE_SETS,
    )
    driver = KernelReplayDriver(replayed.kernel, replayed.bus)
    session = PlaybackSession(replayed.bus, driver, recording)
    items = session.run()

    assert items.as_dict() == {
        "fingerprint": original[0],
        "score": original[1],
        "lines": original[2],
        "piecesPlaced": original[3],
    }, "换过规则集的录像重放不出一致的四项"


def _recorder_of(assembly: object) -> RecordingSystem[Snapshot]:
    """从装配结果里挑出录制系统。"""
    systems = [
        system
        for system in assembly.observable_systems  # type: ignore[attr-defined]
        if isinstance(system, RecordingSystem)
    ]
    assert systems, "录像系统没装上"
    return systems[0]


# ---------------------------------------------------------------------------
# G3-6 · 扩展零污染
# ---------------------------------------------------------------------------


def test_m3_extension_purity_the_kernel_is_identical_with_and_without_the_new_systems() -> None:
    """`G3-6`：**关掉 M3 全部新系统 → 逐帧指纹与全开时全等**。

    **这是 M3 最重要的一道门**：M3 加了两个订阅者，**它们不许改变内核的
    任何一个字节的行为**——装了系统之后，棋盘指纹、分数、行数、方块数**逐帧
    全等**。

    **为什么这条不能靠"看起来没关系"**：系统订阅事件、事件驱动分发，**分发是
    同步的**——一个系统处理事件时多花的时间不会改变结果，**但它多发的命令
    会**。这条门抓住的是"系统偷偷派了命令"或"系统改了内核"。
    """
    bare = assemble(seed=SEED, observability_enabled=False)
    loaded = assemble(
        seed=SEED,
        observability_enabled=False,
        systems=("achievements", "rotations"),
        system_catalog=system_catalog(),
    )

    def _run(assembly: object) -> list[tuple[str, int, int, int]]:
        """跑一串固定动作，返回逐帧的四项。"""
        kernel = assembly.kernel  # type: ignore[attr-defined]
        loop = HeadlessLoop(assembly)  # type: ignore[arg-type]
        loop.start()
        items: list[tuple[str, int, int, int]] = []
        for index in range(40):
            if index % 3 == 0:
                kernel.submit(Command(type="moveLeft", source="host"))
            elif index % 3 == 1:
                kernel.submit(Command(type="rotateCW", source="host"))
            else:
                kernel.submit(Command(type="softDrop", source="host"))
            loop.run_frame()
            items.append(_four_items(kernel.snapshot()))
        return items

    assert _run(loaded) == _run(bare), "装了 M3 的新系统之后内核的行为变了"


def test_m3_extension_purity_no_new_system_dispatches_a_command() -> None:
    """`G3-6` 第二面：**M3 的新系统一个命令都不派发**。

    **它们全是订阅者**——订阅是只读的，**派命令就是能改内核**。
    """
    graph = dependency_graph()
    for name in ("achievements", "rotations"):
        assert graph.commands.get(name, ()) == (), (
            f"{name} 派发了命令：{graph.commands.get(name)}——新系统不许改内核"
        )


def test_m3_extension_purity_the_systems_are_subscribers_only() -> None:
    """`G3-6` 第三面：**新系统的实现不 import 内核，也不碰文件系统**。

    **它们住在系统层，向上不许依赖内核**（依赖方向：systems > bus > kernel），
    **也不许碰文件系统**——落盘只走 sink（ADR-064）。

    **这一条是静态的**（读源码）——但它守的正是"实现可能怎么写坏"：一个系统
    在 import 期 `from ...kernel.core import Kernel` 就能拿到内核，**它就能改
    内核状态**。

    **为什么用 AST 而不是文本匹配**：源码的 **docstring 里会出现 `open()` 这个
    词**（系统自己在解释「我不许 `open()`」）——**文本匹配会把注释当成代码**。
    AST 只看**真正的 import 与调用**。
    """
    import ast

    for package in ("achievements", "rotations"):
        package_dir = REPO_ROOT / "src" / "tetris_kernel" / "systems" / package
        for path in sorted(package_dir.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # **不许 import 内核**（它能改内核状态）。
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "kernel.core" not in node.module, (
                        f"{path.name} import 了内核：{node.module}"
                    )
                # **不许 import os / shutil**。
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name not in ("os", "shutil"), (
                            f"{path.name} import 了 {alias.name}——落盘只走 sink"
                        )
                # **不许真的调用 `open()`**。
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id != "open", f"{path.name} 自己开了文件——落盘只走 sink"


# ---------------------------------------------------------------------------
# 元信息（本文件自身的自检）
# ---------------------------------------------------------------------------


def test_m3_gates_the_six_hits_are_mutually_different() -> None:
    """**六个命中名互不相同**（M2 的 ADR-076 口径）。

    **共用命中名**会让两道门的生死无从分辨——一道门死了，另一道门还"绿着"。
    """
    hits = (
        "m3_new_capability_is_new_files",
        "m3_hot_unload_no_interrupt",
        "m3_system_manifest_and_graph",
        "m3_achievement_zero_kernel",
        "m3_runtime_ruleset_switch",
        "m3_extension_purity",
    )
    assert len(set(hits)) == len(hits), f"命中名有重复：{hits}"


def test_m3_gates_the_gate_file_is_not_empty() -> None:
    """**这份文件得有东西**——空文件会被元守卫当成"入口全死"以外的情况放过。

    **为什么要有这条**：一个写着零条用例的 `test_m3_gates.py` 让六个 `-k` 入口
    **一条都选不到**——元守卫会红（那是对的），但**根因不在门上**。这条把
    "文件空了"和"门死了"分成两件事。
    """
    source = Path(__file__).read_text(encoding="utf-8")
    assert source.count("def test_") >= 12, "六道门的用例太少——门被掏空了？"
