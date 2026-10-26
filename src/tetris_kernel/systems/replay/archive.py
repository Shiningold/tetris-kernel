"""存档格式（系统层 · 回放系统 · M2-S10 · FR-10.1 / FR-10.3 · ADR-080）。

## 存档 ≠ 录像（**两种格式，不共用一套**）

| | 录像（`format.py`） | 存档（本模块） |
| --- | --- | --- |
| 记什么 | **命令序列** | **局面状态** |
| 形态 | JSON Lines（**一行一条**） | **单个 JSON 文档** |
| 靠什么恢复 | 重放命令 | 复原状态 |

**为什么必须是两种格式**：录像是**输入**（命令 + 种子就是完整的输入，ADR-045），
存档是**状态**（棋盘 + 计分 + 计时器 + 随机源状态）。混用会让"恢复"悄悄变成
"重放"——而重放依赖命令序列，**存档必须能独立恢复**（FR-10.1）。

**体积差一个量级**：一份录像是几十条命令（几百字节），一份存档是整块棋盘加
**完整 MT 状态**（626 个整数，几 KB）——把状态快照当录像存，录像文件会大一个
量级，而且**含完整随机源状态**（ADR-060 明写"完整状态只在存档路径导出"）。

## 头四件套 + 局面

```json
{
  "version": "v1",
  "seed": 20261026,
  "rule": "standard",
  "rngState": [3, 2147, ...],
  "state": {"version": 0, "phase": "running", "frame": 42, "board": {...}, ...}
}
```

**两个 `version` 是不同的东西**：`"version": "v1"` 是**格式**版本（不认识就
报错 · FR-10.3 · NFR-9）；`state.version` 是**局面**版本号（内核的脏标记，
需求 §8.3）。**格式版本在顶层、状态版本在局面里**——不重名，解析时不会看错。

## 活动方块带 9 个字段（不是快照的 4 个）

快照的 `active` 只有 4 个字段（类型 · 朝向 · 坐标）；**判定 T-Spin 需要的 5 项
瞬时事实**（最后一手是不是旋转 · 踢墙信息 · 落地原因 · 锁定延迟重置次数与待
重置标记）**不进快照**——但**必须进存档**：不存 → 恢复后第一手放置的判定就变了
→ 分数与消行分类都可能不同 → FR-10.2 直接不成立。

## 这个模块**不认识内核**

它只管"存档这份数据长什么样"——**不 import `kernel.core`**（与 S9 同一条纪律：
系统层不造内核、不碰内核的内部结构）。**内核结构 ↔ 存档数据**的映射在宿主层
（`host/archive.py` · 架构 §4.1）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from ...contracts.types import PHASE_VALUES, Phase

#: 存档格式版本（**不认识的头 → 明确报错** · FR-10.3 · NFR-9）。
ARCHIVE_VERSION = "v1"

#: 存档文件名的**前缀**。
ARCHIVE_FILENAME_PREFIX = "archive"

#: 存档文件的**扩展名**（单个 JSON 文档）。
ARCHIVE_SUFFIX = ".json"


@dataclass(frozen=True)
class ArchivedActive:
    """存档里的活动方块（**快照的 4 个字段 + 5 项判定用的瞬时事实**）。

    - `pieceType` / `rotation` / `x` / `y`：与快照的 `active` 同名同义；
    - `lastMoveWasRotation`：最后一手是不是旋转（T-Spin 判定的输入）；
    - `kickIndex` / `kickOffset`：踢墙信息（**没有踢墙时是 `None`**）；
    - `lastCause`：落地原因（`ground` / `command` / `softDrop` / `hardDrop`）；
    - `lockResets`：本块已重置过锁定延迟的次数（spec §7.5 的 15 次上限）；
    - `lockResetPending`：本帧有成功的移动 / 旋转（锁定步据此重置计时）。
    """

    pieceType: str
    rotation: int
    x: int
    y: int
    lastMoveWasRotation: bool
    kickIndex: int | None
    kickOffset: tuple[int, int] | None
    lastCause: str
    lockResets: int
    lockResetPending: bool


@dataclass(frozen=True)
class ArchivedBoard:
    """存档里的棋盘（**尺寸 + 内容**）。

    **指纹是算出来的，不存档**：结构指纹由棋盘内容唯一决定（架构 §5.4），
    存一份反而多出一个可能对不上的副本——恢复时现算，指纹必然自洽。
    """

    width: int
    height: int
    hiddenRows: int
    cells: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class ArchivedState:
    """存档里的局面（**字段名与快照逐条对应**）。

    **字段名照抄快照**（需求 §8.3）：`version` · `phase` · `frame` · `board` ·
    `active` · `ghostDelta` · `hold` · `holdUsed` · `next` · `score` · `lines` ·
    `level` · `combo` · `b2b` · `piecesPlaced` · `grounded` · `lockTimer` ·
    `gravityTimer` · `extensions`。

    **快照里的 `rule` 与 `rngState` 不在这里**——它们在存档的**头**上（`rule`
    是头三件套之一，`rngState` 是随机源状态）；**快照里的 `extensionFingerprint`
    与 `board.structureFingerprint` 也不在这里**——那两个是**算出来的**，恢复时
    现算。
    """

    version: int
    phase: Phase
    frame: int
    board: ArchivedBoard
    active: ArchivedActive | None
    ghostDelta: tuple[int, int]
    hold: str | None
    holdUsed: bool
    next: tuple[str, ...]
    score: int
    lines: int
    level: int
    combo: int
    b2b: bool
    piecesPlaced: int
    grounded: bool
    lockTimer: int
    gravityTimer: int
    extensions: tuple[tuple[str, int | float | str | bool | None], ...]


@dataclass(frozen=True)
class Archive:
    """一份存档（**头四件套 + 局面**）。

    - `seed`：内核种子；
    - `rule`：规则集标识；
    - `rngState`：随机源的扁平化状态 `(version, *state)`（**存它才不断序列**）；
    - `state`：局面状态。
    """

    seed: int
    rule: str
    rngState: tuple[int, ...]
    state: ArchivedState


# ---------------------------------------------------------------------------
# 文件名
# ---------------------------------------------------------------------------


def archive_filename(rule: str) -> str:
    """存档的**文件名**（**不带墙钟 · 不带种子** · ADR-064 与录像同一条口径）。

    **纯函数**：同一个规则集 → 同一个文件名（"同一局存两次产生同一份"）。
    """
    return f"{ARCHIVE_FILENAME_PREFIX}-{_slug(rule)}{ARCHIVE_SUFFIX}"


def _slug(text: str) -> str:
    """把规则集标识压成**文件名安全**的一小段（去掉路径分隔符等）。"""
    safe = [char if (char.isalnum() or char in "-_.") else "-" for char in text]
    return "".join(safe) or "rule"


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def archive_to_json(archive: Archive) -> str:
    """存档 → **单个 JSON 文档**（紧凑 · 键序固定）。

    **为什么不是 JSON Lines**：JSON Lines 是**录像**的形态（一条命令一行，
    diff 逐行可比）；存档是**一份状态**，拆成多行没有意义——它天然是**一个
    文档**。

    **紧凑**（无空格分隔符）：存档里最大的两块是棋盘与 626 个整数的随机源
    状态，缩进与空格纯粹是体积。
    """
    return json.dumps(
        _archive_to_dict(archive),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _archive_to_dict(archive: Archive) -> dict[str, object]:
    """存档 → 字典（**键序由 `sort_keys` 定**，不靠插入顺序）。"""
    return {
        "version": ARCHIVE_VERSION,
        "seed": archive.seed,
        "rule": archive.rule,
        "rngState": list(archive.rngState),
        "state": _state_to_dict(archive.state),
    }


def _state_to_dict(state: ArchivedState) -> dict[str, object]:
    """局面 → 字典。"""
    return {
        "version": state.version,
        "phase": state.phase,
        "frame": state.frame,
        "board": {
            "width": state.board.width,
            "height": state.board.height,
            "hiddenRows": state.board.hiddenRows,
            "cells": [list(row) for row in state.board.cells],
        },
        "active": None if state.active is None else _active_to_dict(state.active),
        "ghostDelta": list(state.ghostDelta),
        "hold": state.hold,
        "holdUsed": state.holdUsed,
        "next": list(state.next),
        "score": state.score,
        "lines": state.lines,
        "level": state.level,
        "combo": state.combo,
        "b2b": state.b2b,
        "piecesPlaced": state.piecesPlaced,
        "grounded": state.grounded,
        "lockTimer": state.lockTimer,
        "gravityTimer": state.gravityTimer,
        "extensions": [[key, value] for key, value in state.extensions],
    }


def _active_to_dict(active: ArchivedActive) -> dict[str, object]:
    """活动方块 → 字典。"""
    return {
        "pieceType": active.pieceType,
        "rotation": active.rotation,
        "x": active.x,
        "y": active.y,
        "lastMoveWasRotation": active.lastMoveWasRotation,
        "kickIndex": active.kickIndex,
        "kickOffset": None if active.kickOffset is None else list(active.kickOffset),
        "lastCause": active.lastCause,
        "lockResets": active.lockResets,
        "lockResetPending": active.lockResetPending,
    }


def archive_from_json(text: str) -> Archive:
    """**单个 JSON 文档** → 存档（**不认识的头 → 明确报错**）。

    **不猜、不静默降级**（FR-10.3 · NFR-9）：版本对不上当场抛 `ValueError`
    ——猜错一次就是**静默分叉**，而分叉比报错难查得多。

    **缺字段也是错**：存档少一个字段 → 恢复后那一项是错的 → 逐帧一致无从
    谈起。所以每个字段都**当场校验类型**，不合就报错。
    """
    raw = _json_object(text, "存档")
    version = raw.get("version")
    if version != ARCHIVE_VERSION:
        raise ValueError(f"不认识存档格式版本：{version!r}（本版只认 {ARCHIVE_VERSION!r}）")
    seed = _as_int(_require(raw, "seed"), "seed")
    rule = _as_str(_require(raw, "rule"), "rule")
    rng_state = _as_int_tuple(_require(raw, "rngState"), "rngState")
    state_raw = _require(raw, "state")
    if not isinstance(state_raw, dict):
        raise ValueError(f"存档的 state 不是对象：{state_raw!r}")
    return Archive(
        seed=seed,
        rule=rule,
        rngState=rng_state,
        state=_state_from_dict(state_raw),
    )


def _state_from_dict(raw: dict[str, object]) -> ArchivedState:
    """字典 → 局面（**逐字段校验**，缺一个报一个）。"""
    board_raw = _require(raw, "board")
    if not isinstance(board_raw, dict):
        raise ValueError(f"存档的 board 不是对象：{board_raw!r}")
    active_raw = _require(raw, "active")
    if active_raw is not None and not isinstance(active_raw, dict):
        raise ValueError(f"存档的 active 不是对象也不是 null：{active_raw!r}")
    return ArchivedState(
        version=_as_int(_require(raw, "version"), "version"),
        phase=_as_phase(_require(raw, "phase")),
        frame=_as_int(_require(raw, "frame"), "frame"),
        board=_board_from_dict(board_raw),
        active=None if active_raw is None else _active_from_dict(active_raw),
        ghostDelta=_as_pair(_require(raw, "ghostDelta"), "ghostDelta"),
        hold=_as_optional_str(_require(raw, "hold"), "hold"),
        holdUsed=_as_bool(_require(raw, "holdUsed"), "holdUsed"),
        next=_as_str_tuple(_require(raw, "next"), "next"),
        score=_as_int(_require(raw, "score"), "score"),
        lines=_as_int(_require(raw, "lines"), "lines"),
        level=_as_int(_require(raw, "level"), "level"),
        combo=_as_int(_require(raw, "combo"), "combo"),
        b2b=_as_bool(_require(raw, "b2b"), "b2b"),
        piecesPlaced=_as_int(_require(raw, "piecesPlaced"), "piecesPlaced"),
        grounded=_as_bool(_require(raw, "grounded"), "grounded"),
        lockTimer=_as_int(_require(raw, "lockTimer"), "lockTimer"),
        gravityTimer=_as_int(_require(raw, "gravityTimer"), "gravityTimer"),
        extensions=_as_extensions(_require(raw, "extensions")),
    )


def _board_from_dict(raw: dict[str, object]) -> ArchivedBoard:
    """字典 → 棋盘（**尺寸与内容必须对得上**）。"""
    cells = _as_rows(_require(raw, "cells"), "cells")
    width = _as_int(_require(raw, "width"), "width")
    height = _as_int(_require(raw, "height"), "height")
    if len(cells) != height:
        raise ValueError(f"存档棋盘的行数与 height 不符：height={height}，实际 {len(cells)} 行")
    for index, row in enumerate(cells):
        if len(row) != width:
            raise ValueError(f"存档棋盘第 {index} 行的长度与 width 不符：width={width}")
    return ArchivedBoard(
        width=width,
        height=height,
        hiddenRows=_as_int(_require(raw, "hiddenRows"), "hiddenRows"),
        cells=cells,
    )


def _active_from_dict(raw: dict[str, object]) -> ArchivedActive:
    """字典 → 活动方块。"""
    return ArchivedActive(
        pieceType=_as_str(_require(raw, "pieceType"), "pieceType"),
        rotation=_as_int(_require(raw, "rotation"), "rotation"),
        x=_as_int(_require(raw, "x"), "x"),
        y=_as_int(_require(raw, "y"), "y"),
        lastMoveWasRotation=_as_bool(_require(raw, "lastMoveWasRotation"), "lastMoveWasRotation"),
        kickIndex=_as_optional_int(_require(raw, "kickIndex"), "kickIndex"),
        kickOffset=_as_optional_pair(_require(raw, "kickOffset"), "kickOffset"),
        lastCause=_as_str(_require(raw, "lastCause"), "lastCause"),
        lockResets=_as_int(_require(raw, "lockResets"), "lockResets"),
        lockResetPending=_as_bool(_require(raw, "lockResetPending"), "lockResetPending"),
    )


# ---------------------------------------------------------------------------
# 取值校验（**类型不合当场报错**）
# ---------------------------------------------------------------------------


def _json_object(text: str, what: str) -> dict[str, object]:
    """解析一个 JSON 对象（**不是对象就报错**，不糊弄）。"""
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"{what}不是合法 JSON：{error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{what}不是 JSON 对象：{value!r}")
    return value


def _require(raw: dict[str, object], key: str) -> object:
    """取一个**必需**的键（**缺了就报错**，不当成默认值）。"""
    if key not in raw:
        raise ValueError(f"存档里少了字段 {key!r}")
    return raw[key]


def _as_int(value: object, what: str) -> int:
    """收成整数（**不是整数就当场报错**；布尔不算整数）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"存档的 {what} 不是整数：{value!r}")
    return value


def _as_optional_int(value: object, what: str) -> int | None:
    """收成可选整数（**`None` 就是没有**，不当成 0）。"""
    if value is None:
        return None
    return _as_int(value, what)


def _as_str(value: object, what: str) -> str:
    """收成字符串。"""
    if not isinstance(value, str):
        raise ValueError(f"存档的 {what} 不是字符串：{value!r}")
    return value


def _as_optional_str(value: object, what: str) -> str | None:
    """收成可选字符串。"""
    if value is None:
        return None
    return _as_str(value, what)


def _as_bool(value: object, what: str) -> bool:
    """收成布尔（**只认 `True` / `False`**——整数不是布尔）。"""
    if not isinstance(value, bool):
        raise ValueError(f"存档的 {what} 不是布尔：{value!r}")
    return value


def _as_int_tuple(value: object, what: str) -> tuple[int, ...]:
    """收成整数清单（JSON 里的数组 → 元组）。"""
    if not isinstance(value, list):
        raise ValueError(f"存档的 {what} 不是清单：{value!r}")
    return tuple(_as_int(item, what) for item in value)


def _as_str_tuple(value: object, what: str) -> tuple[str, ...]:
    """收成字符串清单。"""
    if not isinstance(value, list):
        raise ValueError(f"存档的 {what} 不是清单：{value!r}")
    return tuple(_as_str(item, what) for item in value)


def _as_rows(value: object, what: str) -> tuple[tuple[int, ...], ...]:
    """收成二维整数表（棋盘）。"""
    if not isinstance(value, list):
        raise ValueError(f"存档的 {what} 不是清单：{value!r}")
    rows: list[tuple[int, ...]] = []
    for index, row in enumerate(value):
        if not isinstance(row, list):
            raise ValueError(f"存档的 {what} 第 {index} 行不是清单：{row!r}")
        rows.append(tuple(_as_int(cell, what) for cell in row))
    return tuple(rows)


def _as_pair(value: object, what: str) -> tuple[int, int]:
    """收成一对整数（位移向量）。"""
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"存档的 {what} 不是两个整数：{value!r}")
    return (_as_int(value[0], what), _as_int(value[1], what))


def _as_optional_pair(value: object, what: str) -> tuple[int, int] | None:
    """收成可选的一对整数（**`None` 就是没有**，不当成 `(0, 0)`）。"""
    if value is None:
        return None
    return _as_pair(value, what)


def _as_extensions(value: object) -> tuple[tuple[str, int | float | str | bool | None], ...]:
    """收成扩展槽（**清单的清单**：`[key, 标量]`）。

    **key 唯一性在构造入口就查**（与内核的 `build_extensions` 同一条纪律）——
    重复 key 是结构错误，不"去重取末值"。
    """
    if not isinstance(value, list):
        raise ValueError(f"存档的 extensions 不是清单：{value!r}")
    entries: list[tuple[str, int | float | str | bool | None]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError(f"存档的扩展槽条目不是 [key, 值]：{item!r}")
        key = _as_str(item[0], "extensions 的 key")
        if key in seen:
            raise ValueError(f"存档的扩展槽 key 重复：{key!r}")
        seen.add(key)
        entries.append((key, _as_scalar(item[1], key)))
    return tuple(entries)


def _as_scalar(value: object, key: str) -> int | float | str | bool | None:
    """收成扩展槽的标量值（**只放标量**，容器与嵌套一律报错）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError(f"存档的扩展槽 {key!r} 的值不是标量：{value!r}")


def _as_phase(value: object) -> Phase:
    """收成相位（**取值域归契约层** · 架构 §7.3.1）。

    **域外的值当场报错**——相位决定门禁与推进许可，域外的值溜进来会让恢复
    后的内核行为不可预期。
    """
    if not isinstance(value, str):
        raise ValueError(f"存档的 phase 不是字符串：{value!r}")
    for phase in PHASE_VALUES:
        if phase == value:
            return cast("Phase", value)
    raise ValueError(f"存档的相位不认：{value!r}（只认 {list(PHASE_VALUES)}）")


__all__ = [
    "ARCHIVE_FILENAME_PREFIX",
    "ARCHIVE_SUFFIX",
    "ARCHIVE_VERSION",
    "Archive",
    "ArchivedActive",
    "ArchivedBoard",
    "ArchivedState",
    "archive_filename",
    "archive_from_json",
    "archive_to_json",
]
