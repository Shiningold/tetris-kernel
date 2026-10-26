"""录像录制（系统层 · 回放系统 · M2-S8 · FR-6.1 · M2-D3 · ADR-030）。

## 只录**被接受**的命令（**不是 `command:received`**）

`command:received` 含**被拒的**命令——重放时内核状态未必相同，同一条命令
可能被接受也可能被拒 → **录像静默分叉**（M2-D3）。所以录的是
**`command:accepted`**：校验后被内核接受的那些。

## 不可丢（ADR-030）

**内存 list 累积**，**局末或周期点一次性落盘**。

**"不可丢"靠没有队列保证，不靠队列策略**——队列本身就是丢的来源（ADR-030
的原话：有界可丢队列被明确否决过）。所以这里**没有队列、没有丢弃、没有
容量上限**：只有一份 `list`，一条命令进一条命令出。

**落盘出口是宿主注入的 sink**（M2-D2 · ADR-064）：本系统**不知道路径**，只
把字节交出去。

## 周期点（**定义写死**）

**每 600 帧**落一次盘。写死是因为"不写死 = 各处实现不一致 = 录像格式不
一致"（spec §7.2）。

**周期点落的是"当前这份完整录像"**——不是"这一段"。理由是落盘出口
`FileSink` 是**覆盖语义**（每次调用把整个文件替换成给定的字节）：分段交出
会让盘上只剩最后一段，**录像残缺，重放必然分叉**。

**内存上界不由录制器保证**：由**对局本身的自然长度**保证（棋盘满了局就结束）
——这正是 ADR-030 的意思——**不靠队列策略**。

## 录什么字段

每条命令记：**帧号 · 命令类型 · 参数 · 来源**。

**来源透传原值**（ADR-062）：回放时**不改写成 `replay`**——改写会让 FR-6.4
的事件流回归基线永久失配。

## 种子与规则集标识**从 `kernel:ready` 事件里取**

**内核是那两项的权威**——它发 `kernel:ready` 时带着种子与规则集标识。所以本
系统**订阅那条事件**，而不是在构造时收一份拷贝。

**顺带落了一条宿主的不变量**：**装配层一个事件都不发**。录制器若在构造时发
`replay:recording-started`，等于让装配层自己造事件——那条不变量是宿主侧的
（"造零件不许造事件"），不能由零件自己破坏。
"""

from __future__ import annotations

from collections.abc import Callable

from ...bus.bus import EventBus
from ...bus.envelope import Envelope, JSONValue, Payload, metadata_value
from ...kernel.commands import Args, ArgsValue
from ...kernel.drafts import METADATA_CAUSE, EventDraft, make_draft
from .format import FourItems, RecordedCommand, Recording, recording_to_jsonl

#: 录制开始事件（契约已登记 · 产出者 `systems/replay/`）。
RECORDING_STARTED_EVENT = "replay:recording-started"

#: 录制停止事件（契约已登记 · 产出者 `systems/replay/`）。
RECORDING_STOPPED_EVENT = "replay:recording-stopped"

#: 被录的命令事件（**不是 `command:received`** · M2-D3）。
ACCEPTED_COMMAND_EVENT = "command:accepted"

#: 帧节拍事件（**周期点由它触发**）。
CLOCK_TICK_EVENT = "clock:tick"

#: 局末事件（**局末落盘由它触发**）。
GAME_OVER_EVENT = "game:over"

#: 内核就绪事件（**种子与规则集标识从它来**）。
KERNEL_READY_EVENT = "kernel:ready"

#: 周期点间隔（**帧** · 写死 · spec §7.2）。
CHECKPOINT_INTERVAL_FRAMES = 600

#: 落盘出口（**宿主注入的可调用对象** · ADR-064）。
#:
#: **为什么在这里再写一遍别名**：`systems/` **不能** import `host/`（依赖方向），
#: 也**不能** import `systems.log` / `systems.metrics`（系统之间互不直调）。
#: 它是**同一个可调用类型**（结构化类型），这里自己写一份即可——**别名不是
#: 契约，不进基线**。
type Sink = Callable[[bytes], None]

#: 局末四项的**取法**（**宿主注入**）。
#:
#: **为什么由宿主取**：四项里的**棋盘指纹**只能从内核快照拿——系统层**不碰
#: 内核状态**（M2-D1），指纹的算法也只有内核那一份（抄第二份就是第二个
#: 真相）。所以宿主注入一个"给我四项"的可调用对象，系统层在局末问它一声。
type OutcomeProvider = Callable[[], FourItems | None]


class RecordingSystem[V]:
    """录像录制系统（**普通订阅者** · 只录被接受的命令 · M2-D3）。

    **装配**：宿主造总线与 sink，再造本系统——它自己订阅总线。

    - `bus`：事件总线（**只订不写内核状态**）；
    - `sink`：**落盘出口**（`None` = 不落盘）；**路径是宿主的事**；
    - `checkpoint_interval`：**周期点间隔**（帧）；
    - `outcome_provider`：**局末四项的取法**（宿主注入；`None` = 不取，录像头
      里就没有比对基准）。

    **种子与规则集标识从 `kernel:ready` 事件里取**（内核是那两项的权威）——
    **装配层因此一个事件都不发**（"装配层不该自己发事件"是宿主的一条不变量）。
    """

    def __init__(
        self,
        bus: EventBus[V],
        sink: Sink | None = None,
        *,
        checkpoint_interval: int = CHECKPOINT_INTERVAL_FRAMES,
        outcome_provider: OutcomeProvider | None = None,
    ) -> None:
        if checkpoint_interval < 1:
            raise ValueError(f"checkpoint_interval 至少为 1，实为 {checkpoint_interval}")
        self._bus = bus
        self._sink = sink
        self._checkpoint_interval = checkpoint_interval
        self._outcome_provider = outcome_provider
        self._outcome: FourItems | None = None
        self._commands: list[RecordedCommand] = []
        self._frame_count = 0
        self._stopped = False
        self._seed = 0
        self._rule = "standard"
        self._announced = False
        # **只订阅被接受的命令**——`command:received` 含被拒的，录了会分叉。
        self._subscription = bus.subscribe(
            ACCEPTED_COMMAND_EVENT, self._on_command, name="录像系统"
        )
        bus.subscribe(CLOCK_TICK_EVENT, self._on_tick, name="录像系统")
        bus.subscribe(GAME_OVER_EVENT, self._on_game_over, name="录像系统")
        bus.subscribe(KERNEL_READY_EVENT, self._on_kernel_ready, name="录像系统")

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def commands(self) -> tuple[RecordedCommand, ...]:
        """已录到的命令（**按录到顺序**）。"""
        return tuple(self._commands)

    @property
    def frame_count(self) -> int:
        """**录到的帧数**（最后一个 `clock:tick` 的帧号；一个 tick 都没收到是 0）。"""
        return self._frame_count

    @property
    def stopped(self) -> bool:
        """录制是否已停止（**局末之后不再录**）。"""
        return self._stopped

    def recording(self) -> Recording:
        """当前这份录像（**头三件套 + 命令序列 + 局末四项**）。"""
        return Recording(
            seed=self._seed,
            rule=self._rule,
            commands=tuple(self._commands),
            outcome=self._outcome,
        )

    def set_outcome(self, outcome: FourItems | None) -> None:
        """**主动**写入局末四项（**比对基准**）。

        **局末自动写入**：构造时给了 `outcome_provider` 的，局末会自动取一次
        ——**先取四项再落盘**，否则录像头里就没有基准。
        """
        self._outcome = outcome

    # ------------------------------------------------------------------
    # 订阅回调
    # ------------------------------------------------------------------

    def _on_kernel_ready(self, envelope: Envelope) -> None:
        """**内核就绪才开录**：种子与规则集标识**从事件里取**（内核是权威）。

        **为什么不在构造时就开始**：装配层**一个事件都不该发**（宿主的一条
        不变量）——构造时就发 `replay:recording-started`，等于让装配层自己
        造事件。而"内核就绪"正是录制真正开始的时刻。
        """
        if self._announced:
            return
        self._announced = True
        self._seed = _int_of(envelope.payload, "seed")
        rule = _text_of(envelope.payload, "rule")
        if rule:
            self._rule = rule
        self._bus.publish(_recording_started_draft(self._seed, self._rule))

    def _on_command(self, envelope: Envelope) -> None:
        """记一条**被接受的**命令（**只读事件负载**）。"""
        if self._stopped:
            return
        command_type = _text_of(envelope.payload, "commandType")
        args = _args_of(envelope.payload)
        source = metadata_value(envelope.metadata, "source")
        self._commands.append(
            RecordedCommand(
                frame=envelope.frame,
                type=command_type,
                args=args,
                source=source if isinstance(source, str) else "",
            )
        )

    def _on_tick(self, envelope: Envelope) -> None:
        """记帧数；**到了周期点就落一次盘**。"""
        if self._stopped:
            return
        frame = _int_of(envelope.payload, "frame")
        if frame > self._frame_count:
            self._frame_count = frame
        if frame > 0 and frame % self._checkpoint_interval == 0:
            self.flush()

    def _on_game_over(self, envelope: Envelope) -> None:
        """**局末**：取局末四项 → 落盘 → 发 `replay:recording-stopped`，然后停录。

        **先取四项再落盘**——录像头里的局末四项是**四项比对的基准**，落盘时
        还没取到就白录了。
        """
        if self._stopped:
            return
        if self._outcome_provider is not None:
            self.set_outcome(self._outcome_provider())
        self.flush()
        self._stopped = True
        self._bus.publish(_recording_stopped_draft(len(self._commands), self._frame_count))

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """把**当前这份完整录像**一次性交给 sink（**覆盖语义**）。"""
        if self._sink is None:
            return
        text = recording_to_jsonl(self.recording())
        self._sink(text.encode("utf-8"))


def _text_of(payload: Payload, name: str) -> str:
    """取负载里的字符串字段。"""
    for key, value in payload:
        if key == name and isinstance(value, str):
            return value
    return ""


def _int_of(payload: Payload, name: str) -> int:
    """取负载里的整数字段。"""
    for key, value in payload:
        if key == name and isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


def _args_of(payload: Payload) -> Args:
    """取负载里的 `args`（**收进契约的取值域**——禁浮点，参数参与重放不能漂）。"""
    for key, value in payload:
        if key != "args":
            continue
        if not isinstance(value, tuple):
            return ()
        restored: list[tuple[str, ArgsValue]] = []
        for entry in value:
            if not isinstance(entry, tuple) or len(entry) != 2:
                return ()
            name, raw = entry
            if not isinstance(name, str):
                return ()
            restored.append((name, _args_value(raw)))
        return tuple(restored)
    return ()


def _args_value(raw: JSONValue) -> ArgsValue:
    """把一个参数值收进契约的取值域（**浮点当场报错**——契约禁浮点）。

    **为什么当场报错而不是放行**：参数参与重放，`1.0` 与 `1` 在 JSON 里不可
    区分——浮点进了录像，重放时状态就可能对不上。
    """
    if raw is None or isinstance(raw, bool | int | str):
        return raw
    if isinstance(raw, tuple):
        items = list(raw)
        if all(isinstance(item, int) and not isinstance(item, bool) for item in items):
            return tuple(_as_int(item) for item in items)
        if all(isinstance(cell, tuple) and len(cell) == 3 for cell in items):
            return tuple(_as_cell(cell) for cell in items)
    raise ValueError(f"命令参数的值不合契约：{raw!r}")


def _as_int(value: object) -> int:
    """把一个值收成整数（**不是整数就当场报错**）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"命令参数里出现非整数：{value!r}")
    return value


def _as_cell(value: object) -> tuple[int, int, int]:
    """把一个值收成格子 `(x, y, 新值)`。"""
    if not isinstance(value, tuple) or len(value) != 3:
        raise ValueError(f"命令参数里出现非法格子：{value!r}")
    return (_as_int(value[0]), _as_int(value[1]), _as_int(value[2]))


def _recording_started_draft(seed: int, rule: str) -> EventDraft:
    """构造 `replay:recording-started` 草稿（**负载两字段照抄契约声明**）。"""
    return make_draft(
        RECORDING_STARTED_EVENT,
        (("seed", seed), ("rule", rule)),
        0,
        ((METADATA_CAUSE, None),),
    )


def _recording_stopped_draft(command_count: int, frame_count: int) -> EventDraft:
    """构造 `replay:recording-stopped` 草稿（**负载两字段照抄契约声明**）。"""
    return make_draft(
        RECORDING_STOPPED_EVENT,
        (("commandCount", command_count), ("frameCount", frame_count)),
        0,
        ((METADATA_CAUSE, None),),
    )


__all__ = [
    "ACCEPTED_COMMAND_EVENT",
    "CHECKPOINT_INTERVAL_FRAMES",
    "CLOCK_TICK_EVENT",
    "GAME_OVER_EVENT",
    "RECORDING_STARTED_EVENT",
    "RECORDING_STOPPED_EVENT",
    "OutcomeProvider",
    "RecordingSystem",
    "Sink",
]
