"""录像格式（系统层 · 回放系统 · M2-S8 · **M4-S10 升到 v2** · FR-6.1 · ADR-045 · ADR-064）。

## 格式：JSON Lines（**第一行是头，之后每行一条命令**）

```
{"outcome":{...},"rule":"standard","seed":20261026,"variant":null,"version":"v2"}
{"args":[],"frame":3,"source":"host","type":"moveLeft"}
{"args":[],"frame":3,"source":"host","type":"hardDrop"}
```

**头**：版本 · 种子 · 规则集标识 · **变体标识** · 局末结算。**体**：按帧记录的
命令序列——每条记**命令类型 · 参数 · 帧号 · 来源**。

**为什么是 JSON Lines 而不是一个大 JSON 文档**：两者都是"JSON 可读可 diff"
（ADR-045），但**一行一条**让录像的 diff 逐行可比（一个大 JSON 文档改一条
命令会让整行变），体积也不带缩进。

**为什么录命令而不是录事件**（ADR-045）：事件粒度会随版本调整而失效；命令
序列 + 种子就是**完整的输入**（spec §7.1）——重放不需要存任何状态。

## v1 → v2（M4-S10 · spec-M4 §6.5 / §7.9）

| 项 | v1 | v2 |
| --- | --- | --- |
| 头 | `version` · `seed` · `rule` · `outcome` | 加 **`variant`**（**变体标识**） |
| 结算结构 | 四项（棋盘指纹 · 分数 · 行数 · 方块数） | **五项**：四项 **+ 扩展指纹** |
| 读入 | 只认 `v1` | **认 `v1` 与 `v2`** |
| 写出 | v1 | **永远是 v2** |
**为什么要加 `variant`**：**变体标识 ≠ 规则集标识**（变体 B 用的就是标准规则集，
玩法差异来自它挂的系统）→ 光有 `rule` **装不回那一局**（扩展槽会是空的 →
第五项必然不等）。

**为什么要加第五项**：**结构指纹不含扩展字段**（架构 §5.4）→ 变体局的扩展
状态分叉时，**只比四项会全绿而结果不同**。

## 兼容策略（NFR-9 · 架构 §5.5）：**v1 按「无变体」处理**，不是拒绝

**v1 录像必然是标准玩法**（M3 之前没有变体）→ **它没有第五项**（`None`）。

**明确处理 ≠ 静默损坏**（NFR-9 的原话）：**版本号记进结构**
（`Recording.version`）→ 输出里能标出"该录像无变体第五项"。

**为什么"没有"不是"空槽的摘要"**：**空槽的摘要只有内核那一份算法算得出**
（架构 §7.1）→ **系统层算不了**（回放系统**不许 import 内核状态** · M2-D1）→
**"没有"是能诚实记下来的事实**，而"按无变体处理"由**宿主**在比较时兑现
（宿主拿得到内核那份摘要）。

**未知版本 → 明确报错**：不猜、不静默降级、不按旧版解析——**猜错一次就是
静默分叉**，而分叉比报错难查得多（FR-10.3）。

## 文件名（**不带墙钟 · 不带种子** · ADR-064）

**文件名是录像格式的一部分**（录像叫什么），**目录策略是宿主的**
（ADR-074：路径策略住在 `host/`，`systems/` 不该决定路径）。

**为什么不带墙钟**：录像头里已经有种子 + 规则集标识 + 版本——带时间戳会让
"同一局录两次"产生两份文件，AC-1 的 200 局比对分不清哪份是哪份。

**为什么不带种子**：契约里 `replay:recording-started` 的 `seed` 字段明写
"录像头里记它（**文件名不带它**）"。

**为什么带变体标识**（M4-S10）：变体 B 的规则集标识是 `standard`——**不带
变体它与标准局的录像同名**，而录像是**覆盖语义**（ADR-085）→ **后写的把先写
的盖掉**，两局的录像互相顶掉。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ...contracts.commands import COMMANDS
from ...contracts.types import CELLS, INT_TUPLE
from ...kernel.commands import Args, ArgsValue

#: 录像格式版本（**写出永远是 v2** · M4-S10）。
RECORDING_VERSION = "v2"

#: **旧版**录像格式版本（**读入认它** · M4-S10）。
LEGACY_RECORDING_VERSION = "v1"

#: **认得的**录像格式版本（**别的一律明确报错** · FR-10.3 · NFR-9）。
SUPPORTED_RECORDING_VERSIONS: tuple[str, ...] = (LEGACY_RECORDING_VERSION, RECORDING_VERSION)

#: 录像文件名的**前缀**。
RECORDING_FILENAME_PREFIX = "recording"

#: 录像文件的**扩展名**（JSON Lines）。
RECORDING_SUFFIX = ".jsonl"


@dataclass(frozen=True)
class RecordedCommand:
    """录像里的一条命令（**四字段**：帧号 · 类型 · 参数 · 来源）。

    - `frame`：该命令所在帧号（**用于中途开始与逐帧步进**，不参与确定性）；
    - `type`：命令类型；
    - `args`：命令参数（**禁浮点**——参数参与重放，不能漂）；
    - `source`：**原始来源**（ADR-062：回放时**透传原值**，不改写成
      `replay`——改写会让事件流回归基线永久失配）。
    """

    frame: int
    type: str
    args: Args
    source: str


@dataclass(frozen=True)
class FourItems:
    """四项比对（**棋盘指纹 · 分数 · 行数 · 方块数** · AC-1）。

    - `fingerprint`：**棋盘内容的哈希**（不是棋盘本身）；
    - `score`：分数；
    - `lines`：累计消行数；
    - `pieces_placed`：已放置方块数。

    **"逐条相等"不是"最终态差不多"**：四项**各自**相等才算过（spec §7.3）。

    **它还在用**：事件 `replay:playback-stopped` 的负载是**契约面**（四字段，
    **不可加第五项**）→ 那条事件仍然发四项。
    """

    fingerprint: str
    score: int
    lines: int
    pieces_placed: int

    def equals(self, other: FourItems) -> bool:
        """**逐条相等**（不是"最终态差不多"）。"""
        return (
            self.fingerprint == other.fingerprint
            and self.score == other.score
            and self.lines == other.lines
            and self.pieces_placed == other.pieces_placed
        )

    def as_dict(self) -> dict[str, object]:
        """四项的字典形态（**键序固定**）。"""
        return {
            "fingerprint": self.fingerprint,
            "score": self.score,
            "lines": self.lines,
            "piecesPlaced": self.pieces_placed,
        }


@dataclass(frozen=True)
class FiveItems:
    """五项比对（**四项 + 扩展指纹** · M4-S10 · `G4-2`）。

    - `fingerprint` / `score` / `lines` / `pieces_placed`：**四项**（与
      `FourItems` 同口径，**前四项的语义一个字都没变**）；
    - `extension_fingerprint`：**第五项**——扩展槽的摘要（架构 §5.4）。

    **为什么第五项是"扩展指纹"而不是别的**：**结构指纹不含扩展字段**——
    只比四项，变体状态分叉时**会全绿而结果不同**。

    **标准局的第五项恒等**（空槽的摘要）→ **既有的四项比对语义不变**
    （AC-1 不受影响）。
    """

    fingerprint: str
    score: int
    lines: int
    pieces_placed: int
    extension_fingerprint: str

    def four(self) -> FourItems:
        """**前四项**（给契约面那条四字段事件用）。"""
        return FourItems(
            fingerprint=self.fingerprint,
            score=self.score,
            lines=self.lines,
            pieces_placed=self.pieces_placed,
        )

    def equals(self, other: FiveItems) -> bool:
        """**逐条相等**（五项各自相等才算过）。"""
        return self.differences(other) == ()

    def differences(self, other: FiveItems) -> tuple[str, ...]:
        """**哪几项不等**（**逐条点名** —— 空元组 = 全等）。

        **为什么要点名**：变体局最常见的分叉原因就是扩展状态——**只说"不等"
        就等于让人猜谜**（plan-M4 S10 的"常见坑"）。
        """
        names: list[str] = []
        if self.fingerprint != other.fingerprint:
            names.append("棋盘指纹")
        if self.score != other.score:
            names.append("分数")
        if self.lines != other.lines:
            names.append("行数")
        if self.pieces_placed != other.pieces_placed:
            names.append("方块数")
        if self.extension_fingerprint != other.extension_fingerprint:
            names.append("扩展指纹")
        return tuple(names)

    def as_dict(self) -> dict[str, object]:
        """五项的字典形态（**键序固定**）。"""
        return {
            "fingerprint": self.fingerprint,
            "score": self.score,
            "lines": self.lines,
            "piecesPlaced": self.pieces_placed,
            "extensionFingerprint": self.extension_fingerprint,
        }

    @classmethod
    def from_four(cls, items: FourItems, extension_fingerprint: str) -> FiveItems:
        """四项 + 扩展指纹 → 五项。"""
        return cls(
            fingerprint=items.fingerprint,
            score=items.score,
            lines=items.lines,
            pieces_placed=items.pieces_placed,
            extension_fingerprint=extension_fingerprint,
        )


@dataclass(frozen=True)
class RecordingOutcome:
    """局末基准：**四项 + 扩展指纹 + 变体标识**（宿主注入给录制系统的那一份）。

    - `items`：**四项**（棋盘指纹 · 分数 · 行数 · 方块数）；
    - `extension_fingerprint`：**第五项**（扩展槽的摘要）；
    - `variant`：**变体标识**（`None` = 标准玩法）——**录像头要带它**，
      否则变体局装不回去（spec-M4 §7.9）。
    """

    items: FourItems
    extension_fingerprint: str
    variant: str | None = None


@dataclass(frozen=True)
class Recording:
    """一份录像（**头 + 命令序列 + 局末结算**）。

    - `seed`：随机源种子；
    - `rule`：规则集标识；
    - `commands`：**按录到顺序**排的命令序列；
    - `outcome`：**局末四项**（`None` = 录的时候没取到）；
    - `extension_fingerprint`：**第五项**（扩展槽的摘要 · M4-S10）；
      **`None` = 该录像没有第五项**（v1 录像必然如此）；
    - `variant`：**变体标识**（`None` = 标准玩法 · M4-S10）；
    - `version`：**读到的格式版本**（**v1 的录像读进来就是 `v1`**）——
      输出里据此标出"该录像无变体第五项"。

    **为什么头里带局末结算**：五项比对（AC-1 · `G2-1`）需要**基准**——录像
    不带基准，比对就没法做，`replay` 子命令也只能打印不能比。带上它，**一份
    录像就能自证**：拿到文件就能验证它重放得对不对。

    **为什么第五项单独一个字段**：**v1 录像没有它**（那是"无变体"）——
    **"没有"与"空槽的摘要"在比较时等价**（v1 必然是标准玩法），但**"没有"
    这件事本身要说出来**（NFR-9 的明确处理）→ 用 `None` 记它。
    """

    seed: int
    rule: str
    commands: tuple[RecordedCommand, ...] = ()
    outcome: FourItems | None = None
    extension_fingerprint: str | None = None
    variant: str | None = None
    version: str = RECORDING_VERSION


# ---------------------------------------------------------------------------
# 文件名
# ---------------------------------------------------------------------------


def recording_filename(rule: str, variant: str | None = None) -> str:
    """录像的**文件名**（**不带墙钟 · 不带种子** · ADR-064）。

    **纯函数**：同一个规则集（+ 变体）→ 同一个文件名（"同一局录两次产生
    同一份"）。

    **为什么带规则集标识**：不同规则集的录像不互相覆盖——而它们本来就不是
    同一种东西（重放要按同一个规则集起内核）。

    **为什么带变体标识**（M4-S10）：**变体标识 ≠ 规则集标识**——变体 B 的
    规则集就是 `standard` → 不带变体，**变体局与标准局的录像同名**，而录像是
    **覆盖语义** → 后写的把先写的盖掉。
    """
    parts = [RECORDING_FILENAME_PREFIX, _slug(rule)]
    if variant:
        parts.append(_slug(variant))
    return "-".join(parts) + RECORDING_SUFFIX


def _slug(text: str) -> str:
    """把标识压成**文件名安全**的一小段（去掉路径分隔符等）。"""
    safe = [char if (char.isalnum() or char in "-_.") else "-" for char in text]
    return "".join(safe) or "rule"


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def recording_to_jsonl(recording: Recording) -> str:
    """录像 → JSON Lines 文本（**第一行是头，之后每行一条命令**）。

    **写出永远是 v2**（M4-S10）：`version` 写死 `v2`——**v1 只读不写**
    （旧格式不再产出，回放旧录像走读入兼容）。

    **结算结构是五项**（M4-S10）：四项 + `extensionFingerprint`——
    **第五项放在结算结构里**（不是头的另一个字段）——**结算结构就是要比的那
    几项**。
    """
    outcome = _outcome_dict(recording)
    lines = [
        json.dumps(
            {
                "version": RECORDING_VERSION,
                "seed": recording.seed,
                "rule": recording.rule,
                "variant": recording.variant,
                "outcome": outcome,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    ]
    for command in recording.commands:
        lines.append(
            json.dumps(
                {
                    "frame": command.frame,
                    "type": command.type,
                    "args": dict(command.args),
                    "source": command.source,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return "\n".join(lines) + "\n"


def _outcome_dict(recording: Recording) -> dict[str, object] | None:
    """录像的局末结算 → 头里的 `outcome`（**五项**）。

    **没有基准就没有 `outcome`**（不是全 0）——**"没比"不是"过了"**。
    """
    if recording.outcome is None:
        return None
    payload = dict(recording.outcome.as_dict())
    # **第五项永远是键**（值可以是 `null` = 该录像没有第五项）——
    # **键在不在是格式的事，值有没有是事实的事**。
    payload["extensionFingerprint"] = recording.extension_fingerprint
    return payload


def recording_from_jsonl(text: str) -> Recording:
    """JSON Lines 文本 → 录像（**不认识的头 → 明确报错**）。

    **不猜、不静默降级**：版本对不上当场抛 `ValueError`——猜错一次就是
    **静默分叉**，而分叉比报错难查得多（FR-10.3 · NFR-9）。

    **v1 按「无变体」处理**（M4-S10）：变体标识 `None` · **没有第五项**
    （`None`）——**不是拒绝**（v1 必然是标准玩法）。**版本号记进结构** →
    输出里能标出"该录像无变体第五项"（NFR-9 的明确处理）。
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("录像是空的：没有头")
    header = _json_object(lines[0], "头")
    version = header.get("version")
    if not isinstance(version, str) or version not in SUPPORTED_RECORDING_VERSIONS:
        raise ValueError(
            f"不认识录像格式版本：{version!r}"
            f"（本版认 {LEGACY_RECORDING_VERSION!r} 与 {RECORDING_VERSION!r}）"
        )
    seed = header.get("seed")
    rule = header.get("rule")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError(f"录像头的 seed 不是整数：{seed!r}")
    if not isinstance(rule, str):
        raise ValueError(f"录像头的 rule 不是字符串：{rule!r}")
    variant = _variant_of(header, version)
    commands = tuple(_command_from_json(line) for line in lines[1:])
    outcome, extension = _outcome_from_json(header.get("outcome"), version)
    return Recording(
        seed=seed,
        rule=rule,
        commands=commands,
        outcome=outcome,
        extension_fingerprint=extension,
        variant=variant,
        version=version,
    )


def _variant_of(header: dict[str, object], version: str) -> str | None:
    """头里的**变体标识**（**v1 没有这一项** → `None` = 标准玩法）。"""
    if version == LEGACY_RECORDING_VERSION:
        return None
    raw = header.get("variant")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"录像头的 variant 不是非空字符串：{raw!r}")
    return raw


def _outcome_from_json(
    raw: object,
    version: str,
) -> tuple[FourItems | None, str | None]:
    """头里的局末结算 → （四项 · 第五项）。

    **v1 没有第五项** → 返回 `None`——**按「无变体」处理**（**不是拒绝**：
    v1 必然是标准玩法 · NFR-9 的明确处理）。
    """
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        raise ValueError(f"录像头的 outcome 不是对象：{raw!r}")
    fingerprint = raw.get("fingerprint")
    if not isinstance(fingerprint, str):
        raise ValueError(f"录像头的 outcome 缺指纹：{raw!r}")
    extension = raw.get("extensionFingerprint")
    if version == LEGACY_RECORDING_VERSION:
        # **v1 录像没有第五项** → **没有就是没有**（不是"空槽的摘要"）——
        # **输出里会标出"该录像无变体第五项"**（NFR-9 的明确处理）。
        extension_fingerprint = None
    elif isinstance(extension, str):
        extension_fingerprint = extension
    else:
        # **v2 的第五项可以是 `null`**（该录像没有第五项：局末没取到基准，
        # 或它是手工拼出来的）——**"没有"与"空槽"在比较时等价**，
        # **由宿主在比较时兑现**（宿主拿得到内核那份摘要）。
        extension_fingerprint = None
    items = FourItems(
        fingerprint=fingerprint,
        score=_as_int(raw.get("score")),
        lines=_as_int(raw.get("lines")),
        pieces_placed=_as_int(raw.get("piecesPlaced")),
    )
    return items, extension_fingerprint


def _json_object(text: str, what: str) -> dict[str, object]:
    """解析一个 JSON 对象（**不是对象就报错**，不糊弄）。"""
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"录像的{what}不是合法 JSON：{error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"录像的{what}不是 JSON 对象：{value!r}")
    return value


def _command_from_json(text: str) -> RecordedCommand:
    """一行 JSON → 一条录下来的命令。"""
    raw = _json_object(text, "命令")
    command_type = raw.get("type")
    if not isinstance(command_type, str):
        raise ValueError(f"录像里的命令没有类型：{raw!r}")
    if command_type not in COMMANDS:
        raise ValueError(f"录像里有未登记的命令类型：{command_type!r}")
    frame = raw.get("frame")
    if not isinstance(frame, int) or isinstance(frame, bool):
        raise ValueError(f"录像里的命令 {command_type!r} 没有帧号：{raw!r}")
    source = raw.get("source")
    if not isinstance(source, str):
        raise ValueError(f"录像里的命令 {command_type!r} 没有来源：{raw!r}")
    args_raw = raw.get("args")
    if not isinstance(args_raw, dict):
        raise ValueError(f"录像里的命令 {command_type!r} 的参数不是对象：{raw!r}")
    return RecordedCommand(
        frame=frame,
        type=command_type,
        args=_restore_args(command_type, args_raw),
        source=source,
    )


def _restore_args(command_type: str, raw: dict[str, object]) -> Args:
    """按**契约的参数声明**把 JSON 值还原成命令参数形状。

    **为什么按声明还原**：JSON 里元组与列表**不可区分**——"空洞所在列"是
    `tuple[int, ...]` 还是 `list[int]`，只有契约的声明说了算（需求 §8.2 的
    既有纪律："反序列化按命令类型还原"）。
    """
    spec = COMMANDS.get(command_type)
    if spec is None:
        raise ValueError(f"录像里有未登记的命令类型：{command_type!r}")
    restored: list[tuple[str, ArgsValue]] = []
    for arg in spec.args:
        if arg.name not in raw:
            raise ValueError(f"录像里的 {command_type} 少了参数 {arg.name!r}")
        restored.append((arg.name, _restore_value(raw[arg.name], arg.type)))
    return tuple(restored)


def _restore_value(value: object, type_expr: str) -> ArgsValue:
    """按契约的类型串还原一个参数值（**类型不合当场报错**）。"""
    if type_expr == "int":
        return _as_int(value)
    if type_expr == "str":
        return _as_str(value)
    if type_expr == "bool":
        return _as_bool(value)
    if type_expr == INT_TUPLE:
        return _as_int_tuple(value)
    if type_expr == CELLS:
        return _as_cells(value)
    raise ValueError(f"录像还原：不认识参数类型 {type_expr!r}")


def _as_int(value: object) -> int:
    """收成整数（**不是整数就当场报错**）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"录像里的参数不是整数：{value!r}")
    return value


def _as_str(value: object) -> str:
    """收成字符串（**不是字符串就当场报错**）。"""
    if not isinstance(value, str):
        raise ValueError(f"录像里的参数不是字符串：{value!r}")
    return value


def _as_bool(value: object) -> bool:
    """收成布尔（**不是布尔就当场报错**）。"""
    if not isinstance(value, bool):
        raise ValueError(f"录像里的参数不是布尔：{value!r}")
    return value


def _as_int_tuple(value: object) -> tuple[int, ...]:
    """收成整数清单（JSON 里的数组 → 元组）。"""
    if not isinstance(value, list):
        raise ValueError(f"录像里的参数不是清单：{value!r}")
    return tuple(_as_int(item) for item in value)


def _as_cells(value: object) -> tuple[tuple[int, int, int], ...]:
    """收成格子清单（`(x, y, 新值)`）。"""
    if not isinstance(value, list):
        raise ValueError(f"录像里的参数不是清单：{value!r}")
    return tuple(_as_cell(item) for item in value)


def _as_cell(value: object) -> tuple[int, int, int]:
    """收成一个格子 `(x, y, 新值)`。"""
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"录像里的参数不是格子：{value!r}")
    return (_as_int(value[0]), _as_int(value[1]), _as_int(value[2]))


__all__ = [
    "LEGACY_RECORDING_VERSION",
    "RECORDING_FILENAME_PREFIX",
    "RECORDING_SUFFIX",
    "RECORDING_VERSION",
    "SUPPORTED_RECORDING_VERSIONS",
    "FiveItems",
    "FourItems",
    "RecordedCommand",
    "Recording",
    "RecordingOutcome",
    "recording_filename",
    "recording_from_jsonl",
    "recording_to_jsonl",
]
