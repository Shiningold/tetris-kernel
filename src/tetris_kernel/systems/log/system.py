"""日志系统（系统层 · M2-S6 · FR-7.1 · ADR-046）。

## 它是什么

**一个普通订阅者**：订阅全局，把每条事件变成**结构化日志条目**。

- **只消费事件，不写状态**（M2-D1）：它**没有任何写内核状态的路径**——连
  视图都不取（`subscribe` 单参数入口，**拿不到视图**）；
- **级别由本系统映射**（ADR-046）：事件**不自带级别**；
- **多频道 · 多输出目标**：一个事件可进多个频道，每个输出目标可只收某几个
  频道；
- **落盘经 sink**（M2-D2 · ADR-064）：**不知道路径**，只把字节交出去。

## 自产日志不再进日志（**不自激**）

日志系统订阅全局，而它自己发的 `log:entry` 也是全局事件——**不跳过它就会
无限自激**（每条日志产生一条日志，产生一条日志……）。所以：**`log:entry` 一律
不进日志**。

同理，指标系统**不把自己的 `metric:sampled` 算进指标**——同一条纪律。

## 一条事件 → 几条条目

**一个频道一条条目**（契约的 `log:entry` 只有一个 `channel` 字段）。默认每个
事件只进**自己命名空间**那一个频道；配置可以让它进多个。
"""

from __future__ import annotations

from ...bus.bus import EventBus
from ...bus.envelope import Envelope
from ...kernel.drafts import METADATA_CAUSE, EventDraft, make_draft
from .entry import (
    ChannelMap,
    LevelMap,
    LogEntry,
    Scalar,
    level_rank,
    resolve_channels,
    resolve_level,
)
from .targets import LogTarget

#: 日志条目的事件类型（契约已登记 · 产出者 `log`）。
LOG_ENTRY_EVENT = "log:entry"

#: 默认级别映射（**事件类型 → 级别**；查不到落到命名空间，再落到兜底级别）。
#:
#: **为什么这几条不一样级**：`kernel:error` 是出错 · `command:rejected` 与
#: `kernel:quota-exceeded` 是"出事了但还能跑" · `board:changed` 与
#: `piece:moved` 是**高频噪声**（默认 `debug`）· `rng:next` 更是逐块一条
#: （默认 `trace`）。**其余事件走兜底级别 `info`。**
DEFAULT_LEVEL_MAP: LevelMap = {
    "kernel:error": "error",
    "kernel:quota-exceeded": "warn",
    "command:rejected": "warn",
    "board:changed": "debug",
    "board:collapsed": "debug",
    "piece:moved": "debug",
    "piece:spawned": "debug",
    "rng:next": "trace",
}


class LogSystem[V]:
    """日志系统：**普通订阅者**（只消费事件 · M2-D1）。

    **装配**：宿主造总线与 sink，再造本系统——它自己订阅总线。

    - `bus`：事件总线（**只订不写内核状态**）；
    - `targets`：输出目标（**可插拔** · 控制台 / 文件（经 sink）/ 内存）；
    - `level_map`：**级别映射**（ADR-046）——**这是本系统的配置，不是事件的属性**；
    - `default_level`：兜底级别；
    - `min_level`：**阈值**（降级第 ② 档"降低日志级别"归本系统的配置项管 ·
      ADR-031）；
    - `channel_map`：频道映射（事件类型 / 命名空间 → 频道清单）。
    """

    def __init__(
        self,
        bus: EventBus[V],
        targets: tuple[LogTarget, ...] = (),
        *,
        level_map: LevelMap | None = None,
        default_level: str = "info",
        min_level: str = "trace",
        channel_map: ChannelMap | None = None,
    ) -> None:
        self._bus = bus
        self._targets = tuple(targets)
        self._level_map: LevelMap = dict(DEFAULT_LEVEL_MAP if level_map is None else level_map)
        self._default_level = default_level
        self._min_level = min_level
        self._channel_map: ChannelMap = dict({} if channel_map is None else channel_map)
        # 级别取值当场校验（**域外值不糊弄过去**）。
        level_rank(default_level)
        level_rank(min_level)
        for level in self._level_map.values():
            level_rank(level)
        for name, channels in self._channel_map.items():
            if not channels:
                raise ValueError(f"频道映射 {name!r} 给了一个空频道清单")
        # **订阅全局**：新事件自动进日志，**无需额外接线**（需求 §8.1 的约定）。
        self._subscription = bus.subscribe("*", self._on_event, name="日志系统")

    # ------------------------------------------------------------------
    # 只读属性与配置
    # ------------------------------------------------------------------

    @property
    def targets(self) -> tuple[LogTarget, ...]:
        """输出目标清单。"""
        return self._targets

    @property
    def min_level(self) -> str:
        """当前阈值（**降级第 ② 档调它**）。"""
        return self._min_level

    def set_min_level(self, level: str) -> None:
        """调阈值（**降级第 ② 档** · ADR-031）——**只降不升由降级控制器管**。

        这里**只管把阈值设成合法的级别**，滞回与升降档策略归降级控制器
        （S12）。
        """
        level_rank(level)
        self._min_level = level

    # ------------------------------------------------------------------
    # 订阅回调
    # ------------------------------------------------------------------

    def _on_event(self, envelope: Envelope) -> None:
        """把一条事件变成条目（**只读事件，不碰任何状态**）。"""
        # **自产日志不进日志**——否则无限自激。
        if envelope.type == LOG_ENTRY_EVENT:
            return
        level = resolve_level(envelope.type, self._level_map, self._default_level)
        if level_rank(level) < level_rank(self._min_level):
            return
        for channel in resolve_channels(envelope.type, self._channel_map):
            self._emit(self._entry_of(envelope, level, channel), envelope.frame)

    def _entry_of(self, envelope: Envelope, level: str, channel: str) -> LogEntry:
        """构造一条条目（**消息 = 事件类型名**；数据带帧号与全局序号）。"""
        data: tuple[tuple[str, Scalar], ...] = (
            ("eventType", envelope.type),
            ("frame", envelope.frame),
            ("seq", envelope.seq),
        )
        return LogEntry(level=level, channel=channel, message=envelope.type, data=data)

    def _emit(self, entry: LogEntry, frame: int) -> None:
        """交给输出目标，**并把条目作为 `log:entry` 事件发出去**。

        **两条去向**：输出目标是**日志系统自己的输出**（控制台 / 文件 / 内存），
        事件是**给别人的**（面板、指标、轨迹导出都看得见它）。

        `frame` 是**来源事件的帧号**——日志条目跟着它发生的那一帧走。
        """
        for target in self._targets:
            if target.accepts(entry):
                target.write(entry)
        self._bus.publish(_entry_draft(entry, frame))


def _entry_draft(entry: LogEntry, frame: int) -> EventDraft:
    """构造 `log:entry` 草稿（**负载四字段照抄契约声明**）。

    用 `make_draft` 构造——**字段名与顺序和契约逐条相等由它当场校验**（写错
    当场报错，不等面板上线才暴露）。
    """
    return make_draft(
        LOG_ENTRY_EVENT,
        (
            ("level", entry.level),
            ("channel", entry.channel),
            ("message", entry.message),
            ("data", entry.data),
        ),
        frame,
        ((METADATA_CAUSE, None),),
    )


__all__ = [
    "DEFAULT_LEVEL_MAP",
    "LOG_ENTRY_EVENT",
    "LogSystem",
]
