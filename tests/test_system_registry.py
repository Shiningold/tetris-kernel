"""系统注册表与运行时热插拔（M3-S3 · FR-9.1 / FR-9.2 · AC-8 · M3-D1）。

## 这份文件守什么

M3 的正题是「**接进来有没有改别人的东西**」。注册表是这套机制的第一块砖：
**装上 · 摘下 · 重装**，而且**摘得干净**。

## 十条判据（照「实现可能怎么写坏」写，不是照「实现现在怎么写」写）

1. **装上**：订阅数 +1 · `kernel:system:installed` 发出（负载两字段齐全）；
2. **摘下**：订阅数 −1 · `kernel:system:removed` 发出 · **该系统此后收不到事件**；
3. **M3-D1（头号坑）**：**卸载后该系统派发的命令不再被内核接受**——用例直接断言
   命令没进内核的队列（不是断言某个计数器）；
4. **队列口径**：卸载时**队列里已入队的命令照常执行**（丢弃它会让内核行为依赖
   卸载时机 → 破坏确定性）；
5. **错误语义**：卸载不干净 → **记错**，但**不占用 `kernel:error`**（架构 §6.4）；
6. **顺序**：摘下时**先撤命令通道、后发事件**——订阅者在 `removed` 里看到的
   世界已经是「我不在了」（顺序倒过来这条必红）；
7. **不在册 → 明确报错**（不静默跳过）；
8. **重装**：卸载后重新装上 → 事件流恢复（**接缝处序列连续**）；
9. **装两遍 → 报错**（名字是键）；
10. **装卸动作本身不产生内核状态变更**（M3-D3：装系统不改棋盘）。

## 反向验证（照做，不是照抄）

- **去掉「撤命令通道」那一步** → 判据 3 必红；
- **把顺序倒过来（先发 `removed` 再撤通道）** → 判据 6 必红。
"""

from __future__ import annotations

import pytest

from tetris_kernel.bus.bus import EventBus
from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.contracts.systems import SystemManifest
from tetris_kernel.host.assembly import assemble
from tetris_kernel.host.registry import (
    SystemAlreadyInstalledError,
    SystemContext,
    SystemEntry,
    SystemNotInstalledError,
    SystemRegistry,
    UnknownSystemError,
)
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.drafts import make_draft
from tetris_kernel.kernel.snapshot import Snapshot

#: 装配用的固定种子。
SEED = 20261027

#: 测试系统的声明（**与契约的同款形状**：订阅集 · 命令集 · 产出者）。
PROBE_MANIFEST = SystemManifest(
    name="probe",
    version="1",
    subscribes=("piece:locked",),
    commands=("tick",),
    producer="probe",
    needsSink=False,
)


class ProbeSystem:
    """**测试用的替身系统**：按声明订阅一条事件，并能派命令。

    **它带 `on_event`** —— 注册表按**声明**接订阅（订阅集只有一个真相）。
    """

    def __init__(self, context: SystemContext) -> None:
        self.context = context
        self.received: list[str] = []
        #: 收到 `kernel:system:removed` 时，命令通道**是不是还开着**——
        #: 摘下的顺序倒过来时，这里会记下 `True`（**订阅者看到自己还活着**）。
        self.channel_active_when_removed: bool | None = None

    def on_event(self, envelope: Envelope) -> None:
        """收一条事件（**只读事件，不碰内核状态**）。"""
        self.received.append(envelope.type)
        if envelope.type == "kernel:system:removed":
            self.channel_active_when_removed = self.context.commands.active

    def send(self, command: Command) -> None:
        """派一条命令（**走通道** —— 通道撤销后这条命令哪儿也不去）。"""
        self.context.commands.submit(command)


def _catalog() -> dict[str, SystemEntry]:
    """测试用的目录（**一个替身系统**）。"""
    return {"probe": SystemEntry(manifest=PROBE_MANIFEST, factory=ProbeSystem)}


def _registry() -> tuple[SystemRegistry, object]:
    """造一个注册表 + 一个已装好的替身系统。"""
    assembly = assemble(seed=SEED)
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog=_catalog(),
    )
    instance = registry.install("probe")
    return registry, instance


def _types_of(bus: EventBus[Snapshot]) -> list[str]:
    """总线历史里的事件类型序列（**按全局序号**）。"""
    return [envelope.type for envelope in bus.history()]


def _locked_draft() -> object:
    """造一条 `piece:locked` 草稿（**只为发事件**，不参与契约断言）。"""
    return make_draft(
        "piece:locked",
        (
            ("pieceType", "T"),
            ("rotation", 0),
            ("x", 3),
            ("y", 0),
            ("cells", ((3, 0),)),
            ("tSpin", "none"),
            ("cause", "move"),
        ),
        0,
    )


def _publish_batch(bus: EventBus[Snapshot]):  # type: ignore[no-untyped-def]
    """内核的 `publish` 吃一批草稿，总线的吃一条——**宿主负责逐条转发**。"""

    def publish(drafts: tuple[object, ...]) -> None:
        for draft in drafts:
            bus.publish(draft)  # type: ignore[arg-type]

    return publish


# ---------------------------------------------------------------------------
# 1 · 装上
# ---------------------------------------------------------------------------


def test_installing_adds_a_subscription_and_emits_the_installed_event() -> None:
    """**装上**：订阅数 +1 · `kernel:system:installed` 发出（负载两字段齐全）。"""
    assembly = assemble(seed=SEED)
    before = assembly.bus.subscriber_count
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog=_catalog(),
    )

    registry.install("probe")

    assert assembly.bus.subscriber_count == before + 1
    installed = [e for e in assembly.bus.history() if e.type == "kernel:system:installed"]
    assert len(installed) == 1
    payload = dict(installed[0].payload)
    assert payload["name"] == "probe"
    assert payload["config"] == ()
    # **产出者是 host**（契约 §8.1：装卸事件的产出者是 host，不是内核）。
    assert installed[0].metadata[1] == ("producer", "host")


def test_installing_subscribes_exactly_the_declared_events() -> None:
    """**按声明订阅**——订阅集只有一个真相（`SystemManifest.subscribes`）。"""
    assembly = assemble(seed=SEED)
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog=_catalog(),
    )
    instance = registry.install("probe")
    assert isinstance(instance, ProbeSystem)

    assembly.bus.publish(_locked_draft())

    # **声明订阅的那条收得到**。
    assert instance.received == ["piece:locked"]
    # **没声明的一条收不到**（订阅集是声明说了算，不是「全都要」）。
    assembly.bus.publish(_start_draft())
    assert instance.received == ["piece:locked"]


def _start_draft() -> object:
    """造一条 `game:started` 草稿（**没声明的事件**）。"""
    return make_draft("game:started", (("level", 1), ("seed", 0)), 0)


# ---------------------------------------------------------------------------
# 2 · 摘下
# ---------------------------------------------------------------------------


def test_uninstalling_removes_the_subscription_and_stops_the_events() -> None:
    """**摘下**：订阅数 −1 · `kernel:system:removed` 发出 · **此后收不到事件**。"""
    assembly = assemble(seed=SEED)
    before = assembly.bus.subscriber_count
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog=_catalog(),
    )
    instance = registry.install("probe")
    assert isinstance(instance, ProbeSystem)

    registry.uninstall("probe")

    assert assembly.bus.subscriber_count == before
    removed = [e for e in assembly.bus.history() if e.type == "kernel:system:removed"]
    assert len(removed) == 1
    payload = dict(removed[0].payload)
    assert payload["name"] == "probe"
    assert payload["clean"] is True
    # **摘下之后它就收不到事件了**（订阅真退了）。
    received_before = len(instance.received)
    assembly.bus.publish(_locked_draft())
    assert len(instance.received) == received_before


# ---------------------------------------------------------------------------
# 3 · M3-D1（头号坑）：卸载后命令通道确已撤
# ---------------------------------------------------------------------------


def test_the_command_channel_is_revoked_on_uninstall() -> None:
    """**M3-D1**：卸载后该系统派发的命令**不再被内核接受**。

    **断言的是「命令没进内核的队列」**，不是某个计数器——命令根本没到内核，
    就没有任何执行痕迹。
    """
    assembly = assemble(seed=SEED)
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog=_catalog(),
    )
    instance = registry.install("probe")
    assert isinstance(instance, ProbeSystem)

    registry.uninstall("probe")

    pending_before = assembly.kernel.pending
    instance.send(Command("tick"))
    assert assembly.kernel.pending == pending_before, "卸载后派出的命令进了内核——通道没撤干净"


def test_the_revoked_channel_counts_the_dropped_commands() -> None:
    """**撤销后派出的命令被丢弃并计数**（可观测：被摘下的系统还在派命令就看这里）。"""
    registry, instance = _registry()
    assert isinstance(instance, ProbeSystem)
    channel = registry.channel_of("probe")

    registry.uninstall("probe")
    instance.send(Command("tick"))

    assert channel.active is False
    assert channel.revoked_count == 1


# ---------------------------------------------------------------------------
# 4 · 队列口径：已入队的命令照常执行
# ---------------------------------------------------------------------------


def test_the_queued_commands_still_run_after_the_uninstall() -> None:
    """**队列里已入队的命令照常执行**——丢弃它会让内核行为依赖卸载时机。"""
    assembly = assemble(seed=SEED)
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog=_catalog(),
    )
    instance = registry.install("probe")
    assert isinstance(instance, ProbeSystem)

    # **入队**（还没执行）：`start` 的效果是开局，跑完轮次看得出来。
    instance.send(Command("start"))
    assert assembly.kernel.pending == 1

    registry.uninstall("probe")

    # **命令照常执行**：它是「已发生的事实」，不因卸载而消失。
    assembly.kernel.run_rounds(_publish_batch(assembly.bus))
    assert assembly.kernel.phase == "running"


# ---------------------------------------------------------------------------
# 5 · 错误语义：卸载不干净 → 记错，但不占用 kernel:error
# ---------------------------------------------------------------------------


def test_an_unclean_uninstall_is_recorded_without_using_kernel_error() -> None:
    """**卸载不干净 → 记错**，但**不发 `kernel:error`**（架构 §6.4）。"""

    class StubbornSystem:
        """**释放资源时抛错**的系统（模拟卸载不干净）。"""

        def __init__(self, context: SystemContext) -> None:
            """装上（**不需要上下文里的东西**，但签名得对）。"""

        def on_event(self, envelope: Envelope) -> None:
            """收事件（**什么都不做**）。"""

        def close(self) -> None:
            """释放资源——**这里炸了**。"""
            raise RuntimeError("还有资源没释放")

    assembly = assemble(seed=SEED)
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog={
            "stubborn": SystemEntry(
                manifest=SystemManifest(
                    name="stubborn",
                    version="1",
                    subscribes=("piece:locked",),
                    commands=(),
                    producer="probe",
                    needsSink=False,
                ),
                factory=StubbornSystem,
            )
        },
    )
    registry.install("stubborn")

    registry.uninstall("stubborn")

    # **记错**：注册表里留着一笔。
    assert registry.problems()
    assert "stubborn" in registry.problems()[0]
    # **但不占用 `kernel:error`**——那一条只留给订阅者异常隔离。
    assert not [e for e in assembly.bus.history() if e.type == "kernel:error"]
    # **卸载事件照发**（载荷里 `clean=False`）。
    removed = [e for e in assembly.bus.history() if e.type == "kernel:system:removed"]
    assert len(removed) == 1
    assert dict(removed[0].payload)["clean"] is False


# ---------------------------------------------------------------------------
# 6 · 顺序：先撤通道，后发事件
# ---------------------------------------------------------------------------


def test_the_channel_is_already_revoked_when_the_removed_event_arrives() -> None:
    """**摘下时先撤通道、后发事件**——订阅者看到的世界已经是「我不在了」。

    **顺序倒过来（先发 `removed` 再撤通道）这条必红**：观察者收到 `removed` 时
    会看到被摘系统的通道**还开着**（= 它还活着）。
    """
    assembly = assemble(seed=SEED)
    observed: dict[str, object] = {}

    class ObserverSystem:
        """**观察者**：收到 `removed` 时看一眼被摘系统的通道是不是还开着。"""

        def __init__(self, context: SystemContext) -> None:
            """装上（**不需要上下文里的东西**，但签名得对）。"""

        def on_event(self, envelope: Envelope) -> None:
            """收一条事件（**只读事件**）。"""
            if envelope.type == "kernel:system:removed":
                probe = observed.get("probe")
                assert isinstance(probe, ProbeSystem)
                observed["channel_active"] = probe.context.commands.active

    catalog = _catalog()
    catalog["observer"] = SystemEntry(
        manifest=SystemManifest(
            name="observer",
            version="1",
            subscribes=("kernel:system:removed",),
            commands=(),
            producer="probe",
            needsSink=False,
        ),
        factory=ObserverSystem,
    )
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog=catalog,
    )
    probe = registry.install("probe")
    observed["probe"] = probe
    registry.install("observer")

    registry.uninstall("probe")

    # **撤通道在发事件之前**（顺序倒过来这里会是 `True`）。
    assert observed["channel_active"] is False


def test_the_uninstalled_system_does_not_receive_its_own_removal_event() -> None:
    """**退订在发事件之前**——被摘的系统收不到自己的 `removed` 事件。

    **它已经不在了**：还收得到就说明退订没做（或者做得太晚）。
    """
    registry, instance = _registry()
    assert isinstance(instance, ProbeSystem)

    registry.uninstall("probe")

    assert instance.channel_active_when_removed is None


# ---------------------------------------------------------------------------
# 7 / 9 · 名字不在册 · 装两遍
# ---------------------------------------------------------------------------


def test_an_unknown_system_name_is_an_explicit_error() -> None:
    """**名字不在册 → 明确报错**（不静默跳过）。"""
    assembly = assemble(seed=SEED)
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog=_catalog(),
    )

    with pytest.raises(UnknownSystemError):
        registry.install("not-registered")


def test_installing_the_same_system_twice_is_an_error() -> None:
    """**装两遍 → 报错**（名字是键：装两遍会让「摘下」摘不干净）。"""
    registry, _ = _registry()

    with pytest.raises(SystemAlreadyInstalledError):
        registry.install("probe")


def test_uninstalling_a_system_that_is_not_installed_is_an_error() -> None:
    """**要摘的系统没装 → 明确报错**。"""
    assembly = assemble(seed=SEED)
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog=_catalog(),
    )

    with pytest.raises(SystemNotInstalledError):
        registry.uninstall("probe")


# ---------------------------------------------------------------------------
# 8 · 重装
# ---------------------------------------------------------------------------


def test_reinstalling_restores_the_event_stream() -> None:
    """**重装**：卸载后重新装上 → 事件流恢复（**接缝处序列连续**）。"""
    assembly = assemble(seed=SEED)
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog=_catalog(),
    )
    registry.install("probe")
    registry.uninstall("probe")
    instance = registry.install("probe")
    assert isinstance(instance, ProbeSystem)

    assembly.bus.publish(_locked_draft())

    assert instance.received == ["piece:locked"]
    # **事件流没有断**：装 → 卸 → 装，三条事件都在流里，序号连续。
    types = _types_of(assembly.bus)
    assert types.count("kernel:system:installed") == 2
    assert types.count("kernel:system:removed") == 1


# ---------------------------------------------------------------------------
# 10 · 装卸不产生内核状态变更（M3-D3）
# ---------------------------------------------------------------------------


def test_installing_and_uninstalling_do_not_change_the_kernel_state() -> None:
    """**装卸动作本身不产生内核状态变更**（M3-D3：系统只订阅事件 + 派发命令）。"""
    assembly = assemble(seed=SEED)
    before = assembly.kernel.snapshot()
    registry = SystemRegistry(
        bus=assembly.bus,
        kernel=assembly.kernel,
        catalog=_catalog(),
    )

    registry.install("probe")
    registry.uninstall("probe")

    after = assembly.kernel.snapshot()
    assert after == before


# ---------------------------------------------------------------------------
# 装配层接线
# ---------------------------------------------------------------------------


def test_the_assembly_can_install_systems_through_the_registry() -> None:
    """**装哪些系统由宿主参数决定**（FR-9.1）——`assemble(systems=(...))`。"""
    assembly = assemble(
        seed=SEED,
        observability_enabled=False,
        systems=("probe",),
        system_catalog=_catalog(),
    )

    assert assembly.registry is not None
    assert assembly.registry.installed() == ("probe",)
    assert assembly.bus.subscriber_count == 1


def test_the_assembly_without_systems_has_no_registry() -> None:
    """**默认一个系统都不装**（空 = 行为与 M2 一致）。"""
    assembly = assemble(seed=SEED)

    assert assembly.registry is None
