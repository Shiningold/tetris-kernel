"""键盘输入适配器（适配层 · M4-S7 · FR-8.3 / FR-8.4 · NFR-8 · spec-M4 §7.7）。

## 它是什么

**按键 → 命令的翻译器**：读按键 · 翻译成命令 · 发两条事件。

- **只订阅/发布事件，不碰内核**（M4-D2）——**命令由宿主递进来**（`submit`
  回调），**事件也由宿主递出去**（`publish` 回调）→ **这个模块连内核与总线都
  不用 import**（**最窄能力原则**）；
- **手感参数归它**（ADR-044 · 契约里 `moveLeft` 的说明也写着"手感参数（DAS /
  ARR）归输入适配器，内核只认原子命令"）——**内核与规则集都不需要知道"多久能再
  按一次"**（架构 §7.3 末段）。

## DAS / ARR 是什么

- **DAS**（延迟自动移位）：**按住方向键**到**开始连发**之间的延迟；
- **ARR**（自动重复速率）：**连发之后**两次之间的间隔。

**只有水平移位吃 DAS / ARR**（标准手感）——**旋转 / 硬降 / 暂存 / 暂停是"按一下
做一次"**。

## 调整参数不触发状态变更（NFR-8）

**改 DAS / ARR 只是改本对象自己的字段**——**不发任何命令、不碰内核状态**。
**手感归输入适配器**（架构 §7.3 末段）。

## 两条事件（FR-8.4）

| 事件 | 负载 | 什么时候发 |
| --- | --- | --- |
| `input:raw` | 动作名 · 按下 / 抬起 | **每一次**按键（**包括终端的重复流**） |
| `input:intent` | 动作名 · 命令类型 | **每一次翻译出的意图**（含连发） |

**动作名是"翻译前"的名字**（`left` / `rotate_cw` …）——**命令类型才是翻译结果**
（`moveLeft` / `rotateCW` …）。**教学 / 录像 / 反作弊可以订阅这两条**。

**没有命令可翻译的键（退出）只发 `input:raw`**——**没有意图就没有意图事件**
（`input:intent` 的 `commandType` 是**命令类型取值域**· **不能塞别的**）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ...contracts.commands import COMMANDS
from .reader import KIND_DOWN, KIND_UP, KeyEvent, KeyReader

#: **命令提交**的形状（**宿主递给适配器的回调**）：命令类型 + 参数。
#:
#: **为什么是回调而不是通道**：**这样适配器连内核都不用 import**（M4-D2 的
#: 最强形态）——**命令的组装与提交归宿主**（宿主本来就有内核）。
type CommandSubmitter = Callable[[str, tuple[tuple[str, object], ...]], None]

#: **事件发布**的形状（**宿主递给适配器的回调**）：事件类型 + 负载。
#:
#: **为什么由宿主发**：**事件的契约校验（字段名与顺序）由宿主那一侧做**
#: （`make_draft`）→ **适配器写错字段名，当场报错**，**发不出去**。
type EventPublisher = Callable[[str, tuple[tuple[str, object], ...]], None]

#: 按键事件（**读键那一层的归一化输出**）。
type KeyHandler = Callable[[str, str], None]

#: DAS 的默认值（**毫秒**）：按住到开始连发的延迟。
DEFAULT_DAS_MS = 150
#: ARR 的默认值（**毫秒**）：连发的间隔。
DEFAULT_ARR_MS = 33

#: 事件类型（**从契约取** —— 不复制字面量）。
INPUT_RAW_EVENT: str = "input:raw"
INPUT_INTENT_EVENT: str = "input:intent"

#: `input:raw` 的 `kind` 字段取值（**契约只说是"按下 / 抬起"**）。
KIND_PRESSED = "按下"
KIND_RELEASED = "抬起"


@dataclass(frozen=True)
class Binding:
    """**一个键的翻译结果**：动作名 + 命令类型。

    - `action`：**动作名**（**翻译前**· 进 `input:raw` / `input:intent` 的
      `action` 字段）；
    - `commandType`：**命令类型**（**翻译后**）——**没有就是 `None`**（**这个键
      不对应任何命令**，比如退出）。
    """

    action: str
    commandType: str | None


#: **键名 → 翻译**（**映射表** · 手抄期望值 · 用例逐条对着它断言）。
#:
#: **键名是归一化的**（`left` / `right` / `up` / `down` / `space` / `z` / `x` /
#: `c` / `p` / `q` / `escape`）——**平台差异在 `reader.py` 里封掉**。
KEY_BINDINGS: Mapping[str, Binding] = MappingProxyType(
    {
        "left": Binding("left", "moveLeft"),
        "right": Binding("right", "moveRight"),
        "up": Binding("rotate_cw", "rotateCW"),
        "z": Binding("rotate_ccw", "rotateCCW"),
        "x": Binding("rotate_180", "rotate180"),
        "down": Binding("soft_drop", "softDrop"),
        "space": Binding("hard_drop", "hardDrop"),
        "c": Binding("hold", "hold"),
        "p": Binding("pause", "pause"),
        "escape": Binding("pause", "pause"),
        "q": Binding("quit", None),
    }
)

#: **吃 DAS / ARR 的动作**（**只有水平移位** —— 标准手感）。
#:
#: **为什么软降不吃**：**软降是"按住就一路掉"**——那是**另一种手感**
#: （多数俄罗斯方块里软降不自动连发，**自动连发的是水平移位**）。
DAS_ACTIONS = frozenset({"left", "right"})


class KeyboardInput:
    """键盘输入适配器（**翻译 + DAS / ARR + 两条事件**）。

    **它不读键**：读键是 `reader.py` 的事（**平台差异在那儿封掉**）——
    **这个类只吃"键名 + 按下 / 抬起"**，**所以它能脱离终端单测**（N3）。
    """

    def __init__(
        self,
        *,
        submit: CommandSubmitter,
        publish: EventPublisher,
        das_ms: int = DEFAULT_DAS_MS,
        arr_ms: int = DEFAULT_ARR_MS,
        source: str = "human",
    ) -> None:
        """构造适配器。

        - `submit`：**命令提交回调**（宿主递进来 · 命令由宿主组装）；
        - `publish`：**事件发布回调**（宿主递进来 · 契约校验在宿主那一侧）；
        - `das_ms` / `arr_ms`：**手感参数**（**可调** · NFR-8）；
        - `source`：**命令来源标记**（**只作元数据** · M4-D5 · 内核不得因它
          分支）。
        """
        if das_ms < 0:
            raise ValueError(f"DAS 不能为负：{das_ms}")
        if arr_ms <= 0:
            raise ValueError(f"ARR 必须为正：{arr_ms}")
        self._submit = submit
        self._publish = publish
        self._das_ms = das_ms
        self._arr_ms = arr_ms
        self._source = source
        #: **按住的键**（键名 → 按下时刻）。
        self._held: dict[str, int] = {}
        #: **每个动作上次派发的时刻**（**连发的节流靠它**）。
        self._last_dispatch: dict[str, int] = {}
        #: **当前时刻**（**宿主给的** —— **读取器不读墙钟**）。
        self._now_ms = 0
        #: **读键那一层**（**没接就是没有** —— **适配器自己能单测**）。
        self._reader: KeyReader | None = None

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def das_ms(self) -> int:
        """**DAS**（毫秒 · 按住到开始连发的延迟）。"""
        return self._das_ms

    @property
    def arr_ms(self) -> int:
        """**ARR**（毫秒 · 连发间隔）。"""
        return self._arr_ms

    @property
    def held(self) -> tuple[str, ...]:
        """**当前按住的键**（**只读视图**）。"""
        return tuple(self._held)

    @property
    def source(self) -> str:
        """**命令来源标记**（**只作元数据** · M4-D5）。"""
        return self._source

    # ------------------------------------------------------------------
    # 手感参数（**NFR-8：只改自己的字段**）
    # ------------------------------------------------------------------

    def set_das_ms(self, das_ms: int) -> None:
        """**改 DAS**（**只改本对象自己的字段** —— **不发命令、不碰内核**）。

        **NFR-8**：**调整参数不触发状态变更**。
        """
        if das_ms < 0:
            raise ValueError(f"DAS 不能为负：{das_ms}")
        self._das_ms = das_ms

    def set_arr_ms(self, arr_ms: int) -> None:
        """**改 ARR**（**只改本对象自己的字段** —— **不发命令、不碰内核**）。"""
        if arr_ms <= 0:
            raise ValueError(f"ARR 必须为正：{arr_ms}")
        self._arr_ms = arr_ms

    # ------------------------------------------------------------------
    # 按键
    # ------------------------------------------------------------------

    def handle_key(self, key: str, kind: str) -> None:
        """吃一次按键事件（**键名 + 按下 / 抬起**）。

        **按下**：发 `input:raw` → **翻译** → 发 `input:intent` + 派命令。
        **抬起**：发 `input:raw` → **松手**（**停连发**）。

        **按住不放时终端会重复发"按下"** → **重复的那次不再翻译**
        （**连发由本适配器的 DAS / ARR 计时器负责** —— **它是权威**）。
        """
        if kind == KIND_DOWN:
            self._on_down(key)
        elif kind == KIND_UP:
            self._on_up(key)

    def handle_events(self, events: tuple[KeyEvent, ...]) -> None:
        """吃一批按键事件（**读键那一层的输出**）。"""
        for event in events:
            self.handle_key(event.key, event.kind)

    def _on_down(self, key: str) -> None:
        """**按下**。"""
        binding = KEY_BINDINGS.get(key)
        if binding is None:
            # **不认识的键**：连 `input:raw` 都不发（**没有动作名**）。
            return
        self._publish(
            INPUT_RAW_EVENT,
            (("action", binding.action), ("kind", KIND_PRESSED)),
        )
        if binding.action in self._held:
            # **终端的重复流**：**不重复翻译**（**连发归本适配器**）。
            return
        self._held[binding.action] = self._now_ms
        self._dispatch(binding)

    def _on_up(self, key: str) -> None:
        """**抬起**。"""
        binding = KEY_BINDINGS.get(key)
        if binding is None:
            return
        self._publish(
            INPUT_RAW_EVENT,
            (("action", binding.action), ("kind", KIND_RELEASED)),
        )
        self._held.pop(binding.action, None)

    # ------------------------------------------------------------------
    # 连发（DAS / ARR）
    # ------------------------------------------------------------------

    def poll(self, now_ms: int) -> None:
        """**推进连发计时**（**宿主每帧调一次**）。

        **判据**：按住的时间 **≥ DAS**（**首次连发的延迟**）**且** 距上次派发
        **≥ ARR**（**连发间隔**）→ **再发一次同样的意图**。
        """
        self._now_ms = now_ms
        for action, pressed_at in list(self._held.items()):
            if action not in DAS_ACTIONS:
                continue
            if now_ms - pressed_at < self._das_ms:
                continue
            last = self._last_dispatch.get(action, pressed_at)
            if now_ms - last < self._arr_ms:
                continue
            binding = _binding_of_action(action)
            if binding is None:
                continue
            self._dispatch(binding)

    def _dispatch(self, binding: Binding) -> None:
        """**发意图事件 + 派命令**（**没有命令就不派**）。"""
        self._last_dispatch[binding.action] = self._now_ms
        if binding.commandType is None:
            # **没有命令可翻译**（退出）→ **只有原始事件**，**没有意图事件**
            # （`input:intent` 的 `commandType` 是命令类型取值域，**塞别的
            # 就是违约**）。
            return
        self._publish(
            INPUT_INTENT_EVENT,
            (("action", binding.action), ("commandType", binding.commandType)),
        )
        self._submit(binding.commandType, ())

    # ------------------------------------------------------------------
    # 接线（**宿主用**）
    # ------------------------------------------------------------------

    def bind_reader(self, reader: KeyReader) -> None:
        """**把读键那一层接上**（**宿主用**）。

        **接上之后**：宿主每帧调 `poll_keys(now_ms)` → **读键 → 翻译**。
        """
        self._reader = reader

    def poll_keys(self, now_ms: int) -> None:
        """**读一次键并翻译**（**宿主每帧调一次** · **非阻塞**）。

        **顺序**：**先读键**（把这一轮的新按下 / 抬起吃掉）→ **再跑连发计时**。
        **读键在前**是为了**这一帧刚按下的键立刻生效**。
        """
        if self._reader is not None:
            self.handle_events(self._reader.poll(now_ms))
        self.poll(now_ms)

    def close(self) -> None:
        """**收尾**（**恢复终端设置**）。"""
        if self._reader is not None:
            self._reader.close()


def _binding_of_action(action: str) -> Binding | None:
    """**按动作名找绑定**（**连发时要重新拿到命令类型**）。"""
    for binding in KEY_BINDINGS.values():
        if binding.action == action:
            return binding
    return None


def _assert_bindings_are_registered() -> None:
    """**映射表里的命令类型必须都已在契约里登记**（**导入期当场炸**）。

    **为什么**：**写错命令名不会当场报错**——**内核会把它当成未知命令拒掉**，
    而错误信息只说"命令不合法"，**不说是哪个名字错了**（架构 §4.4 的同一条
    纪律：**参数名从契约取，不复制字面量**）。
    """
    for key, binding in KEY_BINDINGS.items():
        if binding.commandType is None:
            continue
        if binding.commandType not in COMMANDS:
            raise ValueError(f"键 {key!r} 映射到未登记的命令：{binding.commandType!r}")


_assert_bindings_are_registered()


__all__ = [
    "DEFAULT_ARR_MS",
    "DEFAULT_DAS_MS",
    "DAS_ACTIONS",
    "INPUT_INTENT_EVENT",
    "INPUT_RAW_EVENT",
    "KIND_PRESSED",
    "KIND_RELEASED",
    "KEY_BINDINGS",
    "Binding",
    "CommandSubmitter",
    "EventPublisher",
    "KeyboardInput",
]
