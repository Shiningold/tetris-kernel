"""回放宿主（宿主层 · M2-S9 · **M4-S10 加第五项** · FR-6.2）。

**起内核 · 派发命令 · 推进帧 · 取五项**——这些是**宿主**的事（架构 §4.1：
唯一知道"怎么把部件拼起来"的地方是装配层）。

**为什么驱动器在宿主层**：`systems/replay/` **不造内核**——它连
`kernel.core` 都不 import。它只管"录了什么、按什么顺序重放"，内核由宿主起、
命令由宿主派。

## 配额必须放大（B-6 · quota.py 的硬要求）

实时局按帧限额（单帧 100 条命令）。**重放不能按那个上限跑**——超限的命令
会被**丢弃**（`quota_exceeded`），而**录像不允许丢命令**（B-6）：丢一条 =
录像与回放对不上，重放静默分叉。

所以回放宿主注入**大得多的上限**（`REPLAY_LIMITS`）。

## 变体局的回放（spec-M4 §7.9 · `G4-2`）

**回放时变体也要装上**——否则扩展槽是空的 → **第五项必然不等**。

**帧末合并扩展槽**（与 `host/loop.py` 的无头帧循环**同一条路径**）：**扩展槽
是回放校验的第五项**，不合并就等于**拿空槽去比**。

**五项在「局末那一刻」取**（两边同口径）：录像的基准是**局末事件**那一刻取的
（`systems/replay/recorder.py`），重放这边也在**同一个事件**上取——
**扩展槽的合并发生在帧末**，取早一步取晚一步都可能差一帧的变体状态。
"""

from __future__ import annotations

from ..bus.bus import EventBus
from ..kernel.commands import Command
from ..kernel.core import Kernel
from ..kernel.drafts import EventDraft
from ..kernel.quota import QuotaLimits
from ..kernel.snapshot import Snapshot, compute_extension_fingerprint
from ..systems.replay.format import FiveItems, FourItems, RecordedCommand, Recording
from ..variants.extensions import ExtensionProvider
from .extensions import apply_extensions, providers_of
from .registry import SystemRegistry

#: 回放宿主的配额上限（**远大于实时局** · B-6 / quota.py 的硬要求）。
#:
#: `max_commands` 是**单帧**上限，`max_rounds` 是**单帧轮次**上限——回放把一帧
#: 的命令一次性铺进去，按实时局的上限会当场超限并**丢命令**。
REPLAY_LIMITS = QuotaLimits(
    max_rounds=64,
    max_commands=100_000,
    max_write_cells=None,
)

#: 局末事件（**五项在它那一刻取** —— 两边同口径）。
GAME_OVER_EVENT = "game:over"

#: **空扩展槽**的摘要（**第五项在无变体时的值** · M4-S10）。
#:
#: **为什么住在宿主层**：**空槽的摘要只有内核那一份算法算得出**
#: （`kernel.snapshot.compute_extension_fingerprint`）——抄第二份就是第二个
#: 真相（架构 §7.1），而**回放系统不许 import 内核状态**（M2-D1 的门禁）。
#: **宿主拿得到内核 → 宿主算**。
EMPTY_EXTENSION_FINGERPRINT: str = compute_extension_fingerprint(())


def four_items_of(snapshot: Snapshot) -> FourItems:
    """一份快照的**局末四项**（棋盘指纹 · 分数 · 行数 · 方块数）。

    **为什么抽成函数**：四项里最要紧的是**棋盘指纹**——它只有内核那一份
    算法（抄第二份就是第二个真相 · 架构 §7.1）。**重放驱动器**要它算重放
    出来的四项，**装配层**要它把局末四项交给录像系统当比对基准——两处
    必须算出同一个值，所以算法**只写这一份**。

    **为什么住在宿主层**：系统层**不碰内核状态**（M2-D1）——它只拿到宿主
    注入的一个"给我四项"的可调用对象（`OutcomeProvider`），**拿不到快照**。
    """
    return FourItems(
        fingerprint=snapshot.board.structureFingerprint,
        score=snapshot.score,
        lines=snapshot.lines,
        pieces_placed=snapshot.piecesPlaced,
    )


def five_items_of(snapshot: Snapshot) -> FiveItems:
    """一份快照的**局末五项**（四项 **+ 扩展指纹** · M4-S10 · `G4-2`）。

    **第五项 = `snapshot.extensionFingerprint`**（扩展槽的规范化摘要）——
    **结构指纹不含扩展字段**（架构 §5.4）→ 变体状态分叉时，**只比四项会全绿
    而结果不同**。
    """
    return FiveItems(
        fingerprint=snapshot.board.structureFingerprint,
        score=snapshot.score,
        lines=snapshot.lines,
        pieces_placed=snapshot.piecesPlaced,
        extension_fingerprint=snapshot.extensionFingerprint,
    )


def recorded_five_items(recording: Recording) -> FiveItems | None:
    """录像里的**基准五项**（**没有基准就是 `None`**）。

    **v1 录像没有第五项** → **按「无变体」处理**：第五项取**空槽的摘要**
    （**宿主拿得到内核那一份算法** → 这里是唯一能算的地方）。

    **"没有基准就不是全 0"**：录像头里的 `outcome` 缺失时，**没有基准可比**——
    "没比"不是"过了"（审查 H3）。
    """
    if recording.outcome is None:
        return None
    items = recording.outcome
    return FiveItems(
        fingerprint=items.fingerprint,
        score=items.score,
        lines=items.lines,
        pieces_placed=items.pieces_placed,
        extension_fingerprint=(
            recording.extension_fingerprint
            if recording.extension_fingerprint is not None
            else EMPTY_EXTENSION_FINGERPRINT
        ),
    )


class KernelReplayDriver:
    """回放驱动器（**宿主实现**）：把录下的命令派发给一个**全新的内核实例**。

    - `kernel`：**同种子 · 同规则集**的新内核（重放要按录像头的规则集起）；
    - `bus`：事件总线（回放期间的事件照常发出去）；
    - `registry`：**系统注册表**（`None` = 没有变体系统）——**帧末合并扩展槽
      要问它要变体系统的扩展条目**（spec-M4 §7.9）。
    """

    def __init__(
        self,
        kernel: Kernel,
        bus: EventBus[Snapshot],
        *,
        registry: SystemRegistry | None = None,
    ) -> None:
        self._kernel = kernel
        self._bus = bus
        self._registry = registry
        #: **局末那一刻的五项**（`None` = 这一局还没结束）。
        self._captured: FiveItems | None = None
        # **局末那一刻取五项**（两边同口径 —— 见模块 docstring）。
        bus.subscribe(GAME_OVER_EVENT, self._on_game_over, name="回放驱动器")

    def dispatch(self, commands: tuple[RecordedCommand, ...]) -> None:
        """派发一批命令（**原样**——类型 / 参数 / 来源都不改）。

        **来源透传原值**（ADR-062）：回放时**不改写成 `replay`**——改写会让
        FR-6.4 的事件流回归基线永久失配。
        """
        for command in commands:
            self._kernel.submit(
                Command(type=command.type, args=command.args, source=command.source)
            )
        self._kernel.run_rounds(self._publish_batch)

    def advance_frame(self) -> None:
        """推进一帧（**帧推进五步**——**生成器必须被耗尽**，B-9）。

        **帧末合并扩展槽**（与无头帧循环同一条路径 · spec-M4 §7.9）：
        **扩展槽是回放校验的第五项**，不合并就等于拿空槽去比。
        """
        for batch in self._kernel.advance_frame():
            self._publish_batch(batch)
        self._merge_extensions()

    def four_items(self) -> FourItems:
        """当前局面的四项（棋盘指纹 · 分数 · 行数 · 方块数）。"""
        return self.five_items().four()

    def five_items(self) -> FiveItems:
        """当前局面的**五项**（四项 **+ 扩展指纹** · M4-S10）。"""
        if self._captured is not None:
            return self._captured
        return five_items_of(self._kernel.snapshot())

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _on_game_over(self, envelope: object) -> None:
        """**局末那一刻**取五项（与录像的基准同口径）。

        **为什么不在回放结束时取**：扩展槽的合并发生在**帧末**——局末事件
        之后还有一次合并 → **晚一步取就可能差一帧的变体状态**（扩展槽里的
        晶体数会变）。**两边都在同一个事件上取** → 比的是同一时刻。
        """
        self._captured = five_items_of(self._kernel.snapshot())

    def _merge_extensions(self) -> None:
        """**帧末合并扩展槽**（与 `host/loop.py` 同一条路径）。

        **没有变体系统就不动扩展槽**：扩展条目是**可选能力**（变体 A 只换
        规则集，**没有系统** → 扩展槽本来就是空的）。
        """
        providers = self._extension_providers()
        if not providers:
            return
        apply_extensions(self._kernel, providers)

    def _extension_providers(self) -> dict[str, ExtensionProvider]:
        """**已装系统里谁提供扩展条目**（鸭子类型 · 系统名作命名空间）。"""
        if self._registry is None:
            return {}
        instances = {name: self._registry.instance_of(name) for name in self._registry.installed()}
        return providers_of(instances)

    def _publish_batch(self, drafts: tuple[EventDraft, ...]) -> None:
        """把一批草稿**逐条**转交给总线（宿主的活）。"""
        for draft in drafts:
            self._bus.publish(draft)


__all__ = [
    "EMPTY_EXTENSION_FINGERPRINT",
    "GAME_OVER_EVENT",
    "REPLAY_LIMITS",
    "KernelReplayDriver",
    "five_items_of",
    "four_items_of",
    "recorded_five_items",
]
