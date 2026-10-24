"""内核随机源：可播种、可复现、可审计（架构 §5.3 · ADR-010 / ADR-011 / ADR-060）。

## 只用两个原语

只用 `random()` 与 `getrandbits()`（ADR-010）：`shuffle` / `sample` / `choice` /
`randrange` 的实现细节在 CPython 版本间变过——同样种子 + 同样状态取出的序列
可能变，**升级 Python 会让全部旧录像集体作废**。只用两个原语，MT 状态就是
序列的唯一决定因素。

7-bag 的 Fisher-Yates **自己实现**，下标用 `getrandbits()` + **拒绝采样**取
（`randrange` 是第三种取法，同样不用）。

## 状态进出：扁平化

`random.Random.getstate()` 返回三元组 `(version, state, gauss_next)`，**塞不进**
快照契约声明的 `rngState: tuple[int, ...] | None`。只用两个原语时 `gauss_next`
恒为 `None`，所以状态进出走**扁平化**编码 `(version, *state)`（626 个 int），
与契约天然吻合——**契约一个字不用改，不撞基线**（plan-M0.md S7 口径 1）。

导出时若 `gauss_next` 不是 `None` 就**报错**：不报错等于静默丢状态。

**种子状态恢复后作废**：`seed` 只表示"建实例时的种子"，恢复出来的这一局
不是它播出来的——留着会让人拿它去"复现"，复现出的是另一局（可审计）。

## 每次取值产出 `rng:next` 草稿

负载 = 随机值 + 序号 + **状态指纹短哈希**，**不含完整 MT 状态**（ADR-060：
一局几百方块就是几 MB 常驻负载）。状态指纹取「**该次取值之后**」的状态，
`seq` 与指纹一一对应。

指纹用 blake2b + TLV，**不用内置 `hash()`**——后者受 `PYTHONHASHSEED` 影响，
跨进程必变（ADR-026）。

## 事件草稿的形态

**载荷 dataclass 留在 kernel 内部**——`EventDraft` 的正式形态归 S9 / S11，
S7 不提前实现。这里只产出「事件类型 + 负载」，信封（全局序号 / 帧号 /
元数据）由总线补（架构 §4.2）。
"""

from __future__ import annotations

import random
import struct
from dataclasses import dataclass

from ..contracts.events import EVENT_TYPES
from .fingerprint import tlv_digest

#: `rng:next` 事件类型名——**必须与 `contracts/events.py` 的注册表一致**
#: （注册表在 `contracts/`，不在 `kernel/`；这里只放一个常量，不另立名单）。
RNG_NEXT_EVENT = "rng:next"

#: 状态指纹的域分隔键（与状态本身的键一起进 TLV）。
_FINGERPRINT_VERSION_KEY = "version"
_FINGERPRINT_STATE_KEY = "state"

#: MT 状态值的取值范围：32 位无符号整数（CPython 的随机状态容器就是这个
#: 宽度）。越界值交给 `random.setstate` 会抛 `OverflowError`——与本模块其它
#: 校验的 `ValueError` 不一致，调用方按 `except ValueError` 兜底会漏掉它。
_MT_STATE_LIMIT = 2**32


def _assert_event_registered() -> None:
    """事件类型名必须与契约注册表一致（**导入期自检**）。

    `contracts/events.py` 是事件类型名的唯一权威（架构 §4.4 末条：引用处
    一律用常量，不复制字面量）。契约层没有单事件常量可引用，所以内核侧留
    一份名字——**但导入期就把它与注册表对齐**：契约那边改名，内核当场拒绝
    加载，而不是静默发出一串没人认的事件。

    （注册表本身在 `contracts/`，**绝不进 `kernel/`**——这里只是读它。）
    """
    spec = EVENT_TYPES.get(RNG_NEXT_EVENT)
    if spec is None:
        raise TypeError(f"事件类型 {RNG_NEXT_EVENT!r} 不在契约注册表里")
    if spec.namespace != "rng":
        raise TypeError(
            f"事件类型 {RNG_NEXT_EVENT!r} 的命名空间是 {spec.namespace!r}，应当是 'rng'"
        )


_assert_event_registered()


@dataclass(frozen=True)
class RngNextPayload:
    """`rng:next` 的负载。

    字段名与类型**以 `contracts/events.py` 的声明为准**：`value`
    （`float | int`）· `seq`（取值序号）· `stateFingerprint`（状态短哈希，
    **不含完整 MT 状态**）。
    """

    value: float | int
    seq: int
    stateFingerprint: str

    def fields(self) -> tuple[tuple[str, float | int | str], ...]:
        """负载字段（元组形式，字段顺序固定）。"""
        return (
            ("value", self.value),
            ("seq", self.seq),
            ("stateFingerprint", self.stateFingerprint),
        )


@dataclass(frozen=True)
class RngNextDraft:
    """`rng:next` 事件草稿（**内核内部形态**）。

    只有**事件类型 + 负载**：信封的全局序号由总线分配（架构 §4.2），草稿
    不含它（plan-M0.md §1.4.4）。
    """

    eventType: str
    payload: RngNextPayload


class KernelRng:
    """内核随机源。

    **只服务内核**（FR-5.3）：AI 等外部系统必须自建实例并显式播种，不许取用
    内核这一份。

    **可播种、可复现、可审计**：同种子两次跑出的序列完全相同；状态可导出、
    可复原；每次取值产出一条带状态指纹的 `rng:next` 草稿。
    """

    def __init__(self, seed: int) -> None:
        """用种子建随机源。

        `random.Random(seed)` **只许出现在这一个文件**——纯净度脚本按路径
        后缀 `kernel/rng.py` 匹配豁免；别处的 `random.Random()` 就是未注入
        的随机源，红。
        """
        self._seed: int | None = seed
        self._rng = random.Random(seed)
        self._draws = 0
        self._drafts: list[RngNextDraft] = []

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def seed(self) -> int | None:
        """**建实例时**的种子；状态恢复后是 `None`。

        恢复出来的这一局不是这个种子播出来的——留着构造种子会让人拿它去
        "复现"，复现出的是**另一局**。这个类的卖点是「可播种、可复现、
        **可审计**」，一个会骗人的 `seed` 与可审计直接冲突。
        """
        return self._seed

    @property
    def draws(self) -> int:
        """已取值次数（等于最后一条草稿的 `seq` + 1）。

        **不随状态复原重置**——`seq` 是本进程内的取值计数，重置会让同一份
        日志里出现两个相同的 `seq`。存档恢复后它继续往上走。
        """
        return self._draws

    # ------------------------------------------------------------------
    # 取值
    # ------------------------------------------------------------------

    def next_float(self) -> float:
        """取一个 `[0, 1)` 的浮点随机值，并产出一条 `rng:next` 草稿。"""
        value = self._rng.random()
        self._record(value)
        return value

    def next_int(self, bits: int) -> int:
        """取一个 `bits` 位整数随机值，并产出一条 `rng:next` 草稿。"""
        if bits <= 0:
            raise ValueError(f"位数必须为正：{bits}")
        value = self._rng.getrandbits(bits)
        self._record(value)
        return value

    def next_bag(self, pieces: tuple[str, ...]) -> tuple[str, ...]:
        """取一袋（Fisher-Yates 洗牌），返回洗好的顺序。

        **袋里的方块类型由调用方给**——方块名是规则层的东西（形状表在
        `rules/`），内核不抄一份名单（抄了就是两处会悄悄分叉的副本）。

        洗牌**自己实现 Fisher-Yates**（不用 `shuffle` / `sample` / `choice` /
        `randrange`——ADR-010），下标用 `getrandbits()` + **拒绝采样**取。

        **每次「取下标」就是一次取值**，各产出一条 `rng:next` 草稿（`value`
        是这次取到的下标）。一条草稿对应一次取值，**不是**一个 MT 原语调用
        ——拒绝采样可能消耗多个 `getrandbits`，被拒的候选值不产草稿（它没有
        产生任何取值结果，只消耗了状态）。
        """
        if len(pieces) < 2:
            raise ValueError(f"一袋至少要有两种方块，收到 {len(pieces)} 种")
        bag = list(pieces)
        for i in range(len(bag) - 1, 0, -1):
            j = self._randbelow(i + 1)
            bag[i], bag[j] = bag[j], bag[i]
        return tuple(bag)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def state_fingerprint(self) -> str:
        """当前状态的短哈希（blake2b / 16 字节 / 小写 hex）。

        **跨进程稳定**——用 blake2b，不用内置 `hash()`（受 `PYTHONHASHSEED`
        影响，跨进程必变）。
        """
        version, state, _gauss_next = self._rng.getstate()
        items = (
            (_FINGERPRINT_VERSION_KEY, _encode_ints((version,))),
            (_FINGERPRINT_STATE_KEY, _encode_ints(state)),
        )
        return tlv_digest(items)

    def getstate(self) -> tuple[int, tuple[int, ...], float | None]:
        """MT 原始状态（三元组，与 `random.Random.getstate()` 同形）。

        存档 / 录像走的是 `export()`；这一对是 **ADR-011 的原始往返**。
        """
        return self._rng.getstate()

    def setstate(self, state: tuple[int, tuple[int, ...], float | None]) -> None:
        """复原 MT 原始状态（与 `random.Random.setstate()` 同形）。

        **种子随即作废**——状态已经不是这个种子播出来的了（见 `seed`）。
        """
        self._rng.setstate(state)
        self._seed = None

    def export(self) -> tuple[int, ...]:
        """导出扁平化状态 `(version, *state)`（626 个 int）——**直接填快照的 `rngState`**。

        `gauss_next` 不是 `None` 就**报错**：不报错等于静默丢状态。
        """
        version, state, gauss_next = self._rng.getstate()
        if gauss_next is not None:
            raise RuntimeError("随机源状态带高斯缓存（gauss_next 非 None），扁平化导出会丢状态")
        return (version, *state)

    def import_state(self, state: tuple[int, ...]) -> None:
        """从扁平化状态 `(version, *state)` 复原。

        复原成 `(version, tuple(state[1:]), None)`——`gauss_next` 恒为 `None`，
        与「只用两个原语」的前提一致。

        **version 取自导入的状态，不是当前实例的**：状态容器格式随 Python 版本
        变过（3.9 前是列表、之后是元组，版本号也不同），混用会**静默重放出
        不同结果**——ADR-011 记 `rngStateVersion` 与 `pythonVersion` 就是为了
        挡住这件事。版本对不上当场报错，不让它溜过去。

        **状态值先自己校验**（32 位无符号整数）：越界值交给 CPython 会抛
        `OverflowError`，与本模块其它校验的 `ValueError` 不一致——调用方按
        `except ValueError` 兜底会漏掉这一类。

        **种子随即作废**——恢复出来的这一局不是这个种子播出来的（见 `seed`）。
        """
        version, current_state, _gauss_next = self._rng.getstate()
        expected = 1 + len(current_state)
        if len(state) != expected:
            raise ValueError(f"状态长度不对：期望 {expected} 个整数，收到 {len(state)} 个")
        imported_version = state[0]
        if imported_version != version:
            raise ValueError(
                f"随机源状态版本不匹配：导入的是 {imported_version}，当前是 {version}"
                "（状态容器格式随 Python 版本变过，混用会静默重放出不同结果 —— ADR-011）"
            )
        for index, value in enumerate(state[1:]):
            if not 0 <= value < _MT_STATE_LIMIT:
                raise ValueError(
                    f"随机源状态第 {index + 1} 个值越界：{value} 不在 "
                    f"[0, {_MT_STATE_LIMIT}) 内（MT 状态是 32 位无符号整数）"
                )
        self._rng.setstate((imported_version, tuple(state[1:]), None))
        self._seed = None

    # ------------------------------------------------------------------
    # 草稿
    # ------------------------------------------------------------------

    def take_drafts(self) -> tuple[RngNextDraft, ...]:
        """取走（清空）积压的 `rng:next` 草稿。

        消费方（帧模型 / 装配层）在一帧的步骤跑完后取走它们，合并进本帧的
        草稿批次。
        """
        drafts = tuple(self._drafts)
        self._drafts.clear()
        return drafts

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _randbelow(self, bound: int) -> int:
        """`[0, bound)` 的均匀整数——**拒绝采样**，只用 `getrandbits()`。

        取 `bound.bit_length()` 位整数，超过 `bound` 的重取（拒绝）。取法固定，
        所以同一状态出的序列固定（ADR-010：序列只由 MT 状态决定）。
        """
        if bound <= 0:
            raise ValueError(f"上界必须为正：{bound}")
        bits = bound.bit_length()
        while True:
            value = self._rng.getrandbits(bits)
            if value < bound:
                self._record(value)
                return value

    def _record(self, value: float | int) -> None:
        """记一次取值：算状态指纹（取「该次取值之后」的状态）+ 追加草稿。"""
        self._drafts.append(
            RngNextDraft(
                eventType=RNG_NEXT_EVENT,
                payload=RngNextPayload(
                    value=value,
                    seq=self._draws,
                    stateFingerprint=self.state_fingerprint(),
                ),
            )
        )
        self._draws += 1


def _encode_ints(values: tuple[int, ...]) -> bytes:
    """整数序列的稳定字节编码。

    **优先定长小端 uint32 打包**（一次 C 调用，快，且与 MT 状态的范围一致）；
    状态值超出 uint32 范围时退回十进制 ASCII + `0x1f` 分隔（十进制数字里
    没有 `0x1f`，所以元素边界无歧义）——**任何整数都能编码，不静默丢信息**。

    两种编码都是确定性的：同一份状态永远编码成同一串字节，指纹跨进程稳定。
    """
    try:
        return struct.pack(f"<{len(values)}I", *values)
    except struct.error:
        return b"".join(str(value).encode("ascii") + b"\x1f" for value in values)


__all__ = [
    "RNG_NEXT_EVENT",
    "KernelRng",
    "RngNextDraft",
    "RngNextPayload",
]
