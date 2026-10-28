"""契约声明测试（S2 事件 / S3 命令 / S4 快照与放置 / S5 规则签名 · S15 补全 · M3-S1 系统声明面）。

当前覆盖：事件注册表（50 条）、命名空间、产出者分布、信封与元数据声明、
命令规格（17 条）与拒绝原因枚举（13 值）、快照字段声明（21 顶层 + board 5
子字段 + active 4 子字段）、坐标系约定、M0 内核状态清单、PlacementInfo、
规则接口签名（9 条）与四个中性交换结构、命名结构注册表、系统声明面（M3）、
类型文法回验与公共叶子纯净度。
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path
from typing import get_args

import pytest

import tetris_kernel.contracts as contracts_pkg
import tetris_kernel.rules as rules_pkg
from tetris_kernel.contracts import (
    ACTIVE_FIELDS,
    ARGS,
    ARGS_VALUE,
    AXIS_LITERAL,
    AXIS_VALUES,
    BOARD_CELLS,
    BOARD_FIELDS,
    BOARD_VIEW,
    CELLS,
    CLEAR_AXIS_LITERAL,
    CLEAR_AXIS_VALUES,
    COMMAND_TYPE_FIELDS,
    COMMAND_TYPE_LITERAL,
    COMMAND_TYPES,
    COMMANDS,
    CONTRACT_VERSION,
    COORD_CELLS,
    COORDINATE_CONVENTIONS,
    ENVELOPE_FIELDS,
    EVENT_TYPES,
    GLOBAL_SUBSCRIPTION,
    INT_TUPLE,
    JSON_VALUE,
    KERNEL_STATE_TO_SNAPSHOT,
    LIFECYCLE_REJECT_REASONS,
    METADATA_KEYS,
    NAMED_STRUCTS,
    NEXT_QUEUE,
    PHASE_LITERAL,
    PHASE_VALUES,
    PRODUCER_LABELS,
    REJECT_REASON_CATEGORIES,
    REJECT_REASON_CATEGORY_LABELS,
    REJECT_REASONS,
    RULE_SET_DEFINED_FIELDS,
    RULE_SIGNATURE_NAMES,
    RULE_SIGNATURES,
    RUNTIME_ONLY_STATE,
    SCALAR_MAP,
    SNAPSHOT_FIELD_NAMES,
    SNAPSHOT_FIELDS,
    SYSTEM_MANIFESTS,
    VECTOR2,
    ClearGroup,
    CommandSpec,
    EventSpec,
    PlacementClass,
    PlacementInfo,
    RuleContext,
    ScoreDelta,
    ScoreState,
    SystemManifest,
    check_manifest_types,
    is_lifecycle_gated,
    manifest_canonical_text,
    namespace_of,
    register_system_manifest,
    unregister_system_manifest,
)
from tetris_kernel.contracts.types import Phase, literal_enum
from tetris_kernel.host.assembly import assemble
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.core import Kernel, KernelState
from tetris_kernel.rules.standard import STANDARD_RULESET
from tetris_kernel.rules.tables import PIECE_TYPES
from tetris_kernel.systems.manifests import discover_manifests

# ---------------------------------------------------------------------------
# 取值域收口：四条（+事件侧同域）字段的类型串，**独立转录**一份做交叉核对。
#
# 转录的是「取值域收口后契约里该写什么」，不是「实现产出了什么」——两者相等
# 才有意义。**逐字相等**：不是"包含某个词"。
# ---------------------------------------------------------------------------

#: 生命周期取值的类型串（四个取值按契约的枚举顺序）。
EXPECTED_PHASE_LITERAL = "Literal['not_started', 'running', 'paused', 'game_over']"

#: 重力方向的类型串。
EXPECTED_AXIS_LITERAL = "Literal['down', 'left', 'right', 'up']"

#: 消行轴的类型串。
EXPECTED_CLEAR_AXIS_LITERAL = "Literal['row', 'column']"

#: 命令名的类型串（17 个命令名按需求 §8.2 的表格顺序）。
EXPECTED_COMMAND_TYPE_LITERAL = (
    "Literal['start', 'pause', 'resume', 'reset', 'moveLeft', 'moveRight', "
    "'softDrop', 'hardDrop', 'rotateCW', 'rotateCCW', 'rotate180', 'hold', "
    "'tick', 'setLevel', 'injectGarbage', 'writeCells', 'switchRuleSet']"
)

# ---------------------------------------------------------------------------
# 需求 §8.1 的事件目录，独立转录一份做交叉核对。
# 键 = 事件类型名，值 = (命名空间, 产出者代号)。
# 转录自 docs/需求与边界.md §8.1，与 contracts/events.py 的注册表互为对照。
# ---------------------------------------------------------------------------

EXPECTED_EVENTS: dict[str, tuple[str, str]] = {
    # kernel（6 条）
    "kernel:ready": ("kernel", "kernel"),
    "kernel:system:installed": ("kernel", "host"),
    "kernel:system:removed": ("kernel", "host"),
    "kernel:error": ("kernel", "bus"),
    "kernel:quota-exceeded": ("kernel", "kernel"),
    "kernel:rule-set-changed": ("kernel", "kernel"),
    # command（3 条）
    "command:received": ("command", "kernel"),
    "command:accepted": ("command", "kernel"),
    "command:rejected": ("command", "kernel"),
    # game（6 条）
    "game:created": ("game", "kernel"),
    "game:started": ("game", "kernel"),
    "game:paused": ("game", "kernel"),
    "game:resumed": ("game", "kernel"),
    "game:over": ("game", "kernel"),
    "game:reset": ("game", "kernel"),
    # clock（2 条）
    "clock:tick": ("clock", "kernel"),
    "clock:gravity": ("clock", "kernel"),
    # input（2 条）
    "input:raw": ("input", "input-adapter"),
    "input:intent": ("input", "input-adapter"),
    # rng（1 条）
    "rng:next": ("rng", "kernel"),
    # piece（11 条）
    "piece:spawned": ("piece", "kernel"),
    "piece:moved": ("piece", "kernel"),
    "piece:rotated": ("piece", "kernel"),
    "piece:kicked": ("piece", "kernel"),
    "piece:softdrop": ("piece", "kernel"),
    "piece:harddrop": ("piece", "kernel"),
    "piece:locked": ("piece", "kernel"),
    "piece:held": ("piece", "kernel"),
    "piece:hold-denied": ("piece", "kernel"),
    "piece:lock-delay-start": ("piece", "kernel"),
    "piece:lock-delay-reset": ("piece", "kernel"),
    # board（4 条）
    "board:changed": ("board", "kernel"),
    "board:lines-cleared": ("board", "kernel"),
    "board:collapsed": ("board", "kernel"),
    "board:garbage-received": ("board", "kernel"),
    # queue（1 条）
    "queue:refilled": ("queue", "kernel"),
    # score（3 条）
    "score:changed": ("score", "kernel"),
    "score:combo-changed": ("score", "kernel"),
    "score:b2b-changed": ("score", "kernel"),
    # progress（1 条）
    "progress:level-changed": ("progress", "kernel"),
    # state（1 条）
    "state:changed": ("state", "host"),
    # render（1 条）
    "render:frame": ("render", "renderer"),
    # log（1 条）
    "log:entry": ("log", "log"),
    # metric（1 条）
    "metric:sampled": ("metric", "metrics"),
    # achievement（1 条）
    "achievement:unlocked": ("achievement", "achievements"),
    # replay（5 条 · M2 新开命名空间 · ADR-075）
    "replay:recording-started": ("replay", "systems/replay/"),
    "replay:recording-stopped": ("replay", "systems/replay/"),
    "replay:playback-started": ("replay", "systems/replay/"),
    "replay:playback-stopped": ("replay", "systems/replay/"),
    "replay:frame-stepped": ("replay", "systems/replay/"),
}

#: 产出者分布（需求 §8.1 / 契约清单第 8 项）。
EXPECTED_PRODUCER_COUNTS: dict[str, int] = {
    "kernel": 35,
    "bus": 1,
    "host": 3,
    "input-adapter": 2,
    "renderer": 1,
    "log": 1,
    "metrics": 1,
    "achievements": 1,
    "systems/replay/": 5,
}


#: 每条事件的负载字段声明（字段名 → 类型），独立转录自 `contracts/events.py`。
#:
#: 这张表是**字段级的牙齿**：删掉一个字段、把一个类型改成别的、或悄悄加一个
#: 字段，这里都会变红。需求 §8.1 的「关键负载」列是"举例"，声明必须**自足**
#: ——回放校验要靠它，不能只写"够用就行"的半截字段（plan-M0.md S2 常见坑）。
EXPECTED_PAYLOAD: dict[str, dict[str, str]] = {
    "kernel:ready": {
        "seed": "int",
        "rule": "str",
        "mode": "str",
    },
    "kernel:system:installed": {
        "name": "str",
        "config": "tuple[tuple[str, int | float | str | bool | None], ...]",
    },
    "kernel:system:removed": {
        "name": "str",
        "clean": "bool",
    },
    "kernel:error": {
        "subscriber": "str",
        "eventType": "str",
        "eventSeq": "int",
        "error": "str",
    },
    "kernel:quota-exceeded": {
        "quota": "str",
        "limit": "int",
        "actual": "int",
    },
    "kernel:rule-set-changed": {
        "fromRule": "str",
        "toRule": "str",
    },
    "command:received": {
        "commandType": EXPECTED_COMMAND_TYPE_LITERAL,
        "source": "str",
        "args": (
            "tuple[tuple[str, int | str | bool | None | tuple[int, ...] "
            "| tuple[tuple[int, int, int], ...]], ...]"
        ),
    },
    "command:accepted": {
        "commandType": EXPECTED_COMMAND_TYPE_LITERAL,
        "commandSeq": "int",
        "args": (
            "tuple[tuple[str, int | str | bool | None | tuple[int, ...] "
            "| tuple[tuple[int, int, int], ...]], ...]"
        ),
    },
    "command:rejected": {
        "commandType": EXPECTED_COMMAND_TYPE_LITERAL,
        "reason": "str",
        "args": (
            "tuple[tuple[str, int | str | bool | None | tuple[int, ...] "
            "| tuple[tuple[int, int, int], ...]], ...]"
        ),
        "mergedCount": "int",
    },
    "game:created": {
        "seed": "int",
        "rule": "str",
    },
    "game:started": {
        "level": "int",
        "seed": "int",
    },
    "game:paused": {
        "phase": EXPECTED_PHASE_LITERAL,
    },
    "game:resumed": {
        "phase": EXPECTED_PHASE_LITERAL,
    },
    "game:over": {
        "score": "int",
        "lines": "int",
        "reason": "str",
    },
    "game:reset": {
        "seed": "int",
    },
    "clock:tick": {
        "frame": "int",
        "stepMs": "int",
    },
    "clock:gravity": {
        "pieceType": "str",
        "x": "int",
        "y": "int",
        "dx": "int",
        "dy": "int",
    },
    "input:raw": {
        "action": "str",
        "kind": "str",
    },
    "input:intent": {
        "action": "str",
        "commandType": EXPECTED_COMMAND_TYPE_LITERAL,
    },
    "rng:next": {
        "value": "float | int",
        "seq": "int",
        "stateFingerprint": "str",
    },
    "piece:spawned": {
        "pieceType": "str",
        "x": "int",
        "y": "int",
        "next": "tuple[str, ...]",
    },
    "piece:moved": {
        "fromX": "int",
        "fromY": "int",
        "toX": "int",
        "toY": "int",
        "blocked": "bool",
        "cause": "str",
    },
    "piece:rotated": {
        "fromRotation": "int",
        "toRotation": "int",
        "direction": "str",
        "blocked": "bool",
    },
    "piece:kicked": {
        "kickIndex": "int",
        "offsetX": "int",
        "offsetY": "int",
    },
    "piece:softdrop": {
        "pieceType": "str",
        "x": "int",
        "y": "int",
    },
    "piece:harddrop": {
        "pieceType": "str",
        "distance": "int",
        "x": "int",
        "y": "int",
        "cells": "tuple[tuple[int, int], ...]",
    },
    "piece:locked": {
        "pieceType": "str",
        "rotation": "int",
        "x": "int",
        "y": "int",
        "cells": "tuple[tuple[int, int], ...]",
        "tSpin": "str",
        "cause": "str",
    },
    "piece:held": {
        "stored": "str",
        "taken": "str | None",
    },
    "piece:hold-denied": {
        "reason": "str",
    },
    "piece:lock-delay-start": {
        "delayFrames": "int",
    },
    "piece:lock-delay-reset": {
        "resetCount": "int",
    },
    "board:changed": {
        "cellChanges": "tuple[tuple[int, int, int], ...]",
        "cause": "str",
    },
    "board:lines-cleared": {
        "rowIndexes": "tuple[int, ...]",
        "count": "int",
        "tSpin": "str",
        "classKey": "str",
    },
    "board:collapsed": {
        "cellChanges": "tuple[tuple[int, int, int], ...]",
    },
    "board:garbage-received": {
        "rows": "int",
        "holes": "tuple[int, ...]",
    },
    "queue:refilled": {
        "next": "tuple[str, ...]",
    },
    "score:changed": {
        "delta": "int",
        "before": "int",
        "after": "int",
        "cause": "str",
    },
    "score:combo-changed": {
        "combo": "int",
    },
    "score:b2b-changed": {
        "b2b": "bool",
    },
    "progress:level-changed": {
        "fromLevel": "int",
        "toLevel": "int",
        "gravityInterval": "int",
    },
    "state:changed": {
        "version": "int",
        "rule": "str",
        "phase": EXPECTED_PHASE_LITERAL,
        "score": "int",
        "lines": "int",
        "level": "int",
        "combo": "int",
        "b2b": "bool",
        "piecesPlaced": "int",
        "activeType": "str | None",
        "hold": "str | None",
        "holdUsed": "bool",
        "next": "tuple[str, ...]",
    },
    "render:frame": {
        "frame": "int",
        "durationUs": "int",
    },
    "log:entry": {
        "level": "str",
        "channel": "str",
        "message": "str",
        "data": "tuple[tuple[str, int | float | str | bool | None], ...]",
    },
    "metric:sampled": {
        "frame": "int",
        "metrics": "tuple[tuple[str, int | float], ...]",
    },
    "achievement:unlocked": {
        "id": "str",
        "title": "str",
        "triggerType": "str",
        "triggerSeq": "int",
    },
    "replay:recording-started": {
        "seed": "int",
        "rule": "str",
    },
    "replay:recording-stopped": {
        "commandCount": "int",
        "frameCount": "int",
    },
    "replay:playback-started": {
        "seed": "int",
        "rule": "str",
        "commandCount": "int",
    },
    "replay:playback-stopped": {
        "fingerprint": "str",
        "score": "int",
        "lines": "int",
        "piecesPlaced": "int",
    },
    "replay:frame-stepped": {
        "frame": "int",
        "commandCount": "int",
    },
}


def test_event_registry_size() -> None:
    """事件总数 = 50（需求 §8.1）。"""
    assert len(EVENT_TYPES) == 50


def test_event_registry_matches_requirement_table() -> None:
    """注册表与需求 §8.1 的事件目录逐条相等：名字、命名空间、产出者。

    **三张表的键集合必须相等**：`EXPECTED_EVENTS`（名字 + 命名空间 + 产出者）
    与 `EXPECTED_PAYLOAD`（字段级转录）是两张**各自独立**的表。键集合不等时，
    从 `EXPECTED_PAYLOAD` 里删掉一个事件 → 该事件的字段级用例不再生成 →
    **该事件静默失去字段级牙齿**（事件本身仍被另一张表守住，所以不是红灯，
    只是少了一层守护）。这类「删一个键就少守一处」的退化，正是契约面最怕的
    形状。
    """
    assert set(EVENT_TYPES) == set(EXPECTED_EVENTS)
    assert set(EXPECTED_PAYLOAD) == set(EVENT_TYPES)
    for name, (namespace, producer) in EXPECTED_EVENTS.items():
        spec = EVENT_TYPES[name]
        assert (spec.namespace, spec.producer) == (namespace, producer), name
        assert spec.namespace == namespace_of(name), name


def test_producer_distribution() -> None:
    """产出者分布：内核 34 · 总线 1 · host 3 · 输入适配器 2 · 渲染器 1。

    日志 / 指标 / 成就各 1（`replay:` 命名空间由回放系统产出 5 条 · ADR-075）。
    """
    counts: dict[str, int] = {}
    for spec in EVENT_TYPES.values():
        counts[spec.producer] = counts.get(spec.producer, 0) + 1
    assert counts == EXPECTED_PRODUCER_COUNTS


def test_producer_labels_cover_all_producers() -> None:
    """每个产出者代号都有对应名称，**且每个代号都有人用**（双向有牙）。

    **两类代号**（M3 增补第二类）：

    - **事件的产出者**（九个）：与需求 §8.1 的「产出者」列**逐条相等**；
    - **不发事件的系统**的产出者（`rotations`）：它的产出是**计数文件**
      而不是事件，**产出者代号取系统名**——需求 §8.2 的元数据 `producer`
      本来就允许「系统名」。

    **少一个该有的代号红**（往事件里填了个没登记的产出者），
    **多一个没人用的代号也红**（表里挂着一个谁都不用的名字）。
    """
    used = {spec.producer for spec in EVENT_TYPES.values()}
    assert used <= set(PRODUCER_LABELS), "事件的产出者不在代号表里"

    declared = {manifest.producer for manifest in discover_manifests().values()}
    assert set(PRODUCER_LABELS) == used | declared, "代号表与实际用到的代号对不上"


def test_namespace_matches_type_prefix() -> None:
    """命名空间 = 类型名第一个冒号之前的部分（`kernel:` 前缀不等于内核产出）。"""
    for name, spec in EVENT_TYPES.items():
        assert spec.namespace == name.split(":", 1)[0], name


@pytest.mark.parametrize("name", sorted(EXPECTED_EVENTS))
def test_event_payload_declared(name: str) -> None:
    """每条事件都有负载字段声明，且字段名在事件内不重复。"""
    spec: EventSpec = EVENT_TYPES[name]
    assert spec.payload, name
    field_names = [field.name for field in spec.payload]
    assert len(field_names) == len(set(field_names)), name
    for field in spec.payload:
        assert field.type, f"{name}.{field.name}"
        assert field.doc, f"{name}.{field.name}"


@pytest.mark.parametrize("name", sorted(EXPECTED_PAYLOAD))
def test_event_payload_fields_match_expectation(name: str) -> None:
    """负载字段名与类型逐条相等——字段级漂移必须变红（plan-M0.md S2 任务第 2 条）。"""
    spec: EventSpec = EVENT_TYPES[name]
    actual = {field.name: field.type for field in spec.payload}
    assert actual == EXPECTED_PAYLOAD[name], name


def test_command_events_carry_args() -> None:
    """命令三段生命周期都必须带不可变 args（架构 §7.2）。"""
    for name in ("command:received", "command:accepted", "command:rejected"):
        fields = {field.name for field in EVENT_TYPES[name].payload}
        assert "args" in fields, name


def test_args_payload_forbids_float() -> None:
    """命令 args 禁浮点（B-7 / ADR-025）——只有 args 受这条约束。"""
    for name in ("command:received", "command:accepted", "command:rejected"):
        args_field = next(f for f in EVENT_TYPES[name].payload if f.name == "args")
        assert "float" not in args_field.type, name


def test_envelope_fields() -> None:
    """信封五字段**及其类型**（需求 §8 / 架构 §5.1，契约清单第 1 项）。"""
    assert {field.name: field.type for field in ENVELOPE_FIELDS} == {
        "seq": "int",
        "frame": "int",
        "type": "str",
        "payload": "tuple[tuple[str, int | str | bool | float | None | tuple[...]], ...]",
        "metadata": "tuple[tuple[str, int | str | bool | float | None | tuple[...]], ...]",
    }


def test_metadata_keys() -> None:
    """元数据三键：source / producer / cause（ADR-062）。"""
    fields = {field.name: field.type for field in METADATA_KEYS}
    assert fields == {
        "source": "str",
        "producer": "str",
        "cause": "tuple[int, int] | None",
    }


def test_contract_version_declared() -> None:
    """契约版本号是机器可读的（契约清单第 13 项，plan-M0.md §5.3 归 S2）。"""
    assert CONTRACT_VERSION == "v1"


#: 同名但**跨域**的字段：同名不同型是有意为之，各自 doc 已消歧。
#:
#: - `level`：`game:started` / `state:changed` 是游戏等级（int），
#:   `log:entry` 是日志级别（str）。两个域各用各的惯例，改名反而会让
#:   日志字段偏离日志系统的通行叫法。
#: - `cause`：负载层是成因字符串（str），元数据层是命令追溯标识
#:   （`tuple[int, int] | None`）——**跨层**同名，断言不查元数据层。
SAME_NAME_DIFFERENT_TYPE_ALLOWED = {"level"}


def test_same_field_name_means_same_type() -> None:
    """**同层**同名负载字段必须同类型——跨事件同名不同型会让通用提取代码踩空。

    只查负载层（不含元数据）：元数据的 `cause` 是命令追溯标识，与负载的
    `cause`（成因字符串）同名不同义是**有意为之**，已在各自 doc 里消歧。
    """
    seen: dict[str, tuple[str, str]] = {}
    for name, spec in EVENT_TYPES.items():
        for field in spec.payload:
            previous = seen.get(field.name)
            if previous is not None:
                assert previous[1] == field.type or field.name in (
                    SAME_NAME_DIFFERENT_TYPE_ALLOWED
                ), f"{field.name} 在 {previous[0]} 是 {previous[1]}，在 {name} 却是 {field.type}"
            else:
                seen[field.name] = (name, field.type)


# ---------------------------------------------------------------------------
# S3：命令契约声明（需求 §8.2）
#
# 下面三张表**独立转录**自 `docs/需求与边界.md` §8.2，与 `contracts/commands.py`
# 的声明互为对照。转录的是「需求写了什么」，不是「实现写了什么」——
# 两者相等才有意义。
# ---------------------------------------------------------------------------

#: 17 条命令的参数规格。键 = 命令名，值 = ((参数名, 类型, 是否可空), ...)。
EXPECTED_COMMAND_ARGS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    # 生命周期（4 条）——全无参
    "start": (),
    "pause": (),
    "resume": (),
    "reset": (),
    # 移动与下落（4 条）——全无参
    "moveLeft": (),
    "moveRight": (),
    "softDrop": (),
    "hardDrop": (),
    # 旋转（3 条）——全无参
    "rotateCW": (),
    "rotateCCW": (),
    "rotate180": (),
    # 暂存与推进（2 条）——全无参
    "hold": (),
    "tick": (),
    # 参数类（1 条）
    "setLevel": (("level", "int", False),),
    # 外部写入（2 条）
    "injectGarbage": (("rows", "int", False), ("holes", "tuple[int, ...]", False)),
    "writeCells": (("cells", "tuple[tuple[int, int, int], ...]", False),),
    # 运行期（1 条）
    "switchRuleSet": (("rule", "str", False),),
}

#: 拒绝原因枚举（13 值），转录自需求 §8.2 的分类表。
EXPECTED_REJECT_REASONS: tuple[str, ...] = (
    "not_started",
    "paused",
    "game_over",
    "quota_exceeded",
    "invalid_argument",
    "level_out_of_range",
    "out_of_bounds",
    "illegal_transition",
    "no_active_piece",
    "hold_already_used",
    "unknown_rule_set",
    "not_authorized",
    "write_condition_failed",
)

#: 拒绝原因的分类（需求 §8.2 的「类」列）。键 = 分类代号。
EXPECTED_REJECT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "lifecycle": ("not_started", "paused", "game_over"),
    "quota": ("quota_exceeded",),
    "parameter": ("invalid_argument", "level_out_of_range", "out_of_bounds"),
    "state": (
        "illegal_transition",
        "no_active_piece",
        "hold_already_used",
        "unknown_rule_set",
    ),
    "controlled_write": ("not_authorized", "write_condition_failed"),
}

#: 每条命令的可拒绝原因。转录自需求 §8.2 的「可被拒绝的情形」列，
#: **加上三条跨切规则**（统一生命周期门禁 · 配额 · 参数不合规格）。
EXPECTED_COMMAND_REJECT_REASONS: dict[str, frozenset[str]] = {
    # 生命周期命令：状态问题走 illegal_transition，不是门禁三值
    "start": frozenset({"invalid_argument", "illegal_transition"}),
    "pause": frozenset({"invalid_argument", "illegal_transition"}),
    "resume": frozenset({"invalid_argument", "illegal_transition"}),
    "reset": frozenset({"invalid_argument", "illegal_transition"}),
    # 移动 / 下落 / 旋转：门禁三值 + 无活动方块
    "moveLeft": frozenset(
        {
            "not_started",
            "paused",
            "game_over",
            "quota_exceeded",
            "invalid_argument",
            "no_active_piece",
        }
    ),
    "moveRight": frozenset(
        {
            "not_started",
            "paused",
            "game_over",
            "quota_exceeded",
            "invalid_argument",
            "no_active_piece",
        }
    ),
    "softDrop": frozenset(
        {
            "not_started",
            "paused",
            "game_over",
            "quota_exceeded",
            "invalid_argument",
            "no_active_piece",
        }
    ),
    "hardDrop": frozenset(
        {
            "not_started",
            "paused",
            "game_over",
            "quota_exceeded",
            "invalid_argument",
            "no_active_piece",
        }
    ),
    "rotateCW": frozenset(
        {
            "not_started",
            "paused",
            "game_over",
            "quota_exceeded",
            "invalid_argument",
            "no_active_piece",
        }
    ),
    "rotateCCW": frozenset(
        {
            "not_started",
            "paused",
            "game_over",
            "quota_exceeded",
            "invalid_argument",
            "no_active_piece",
        }
    ),
    "rotate180": frozenset(
        {
            "not_started",
            "paused",
            "game_over",
            "quota_exceeded",
            "invalid_argument",
            "no_active_piece",
        }
    ),
    # 暂存：门禁三值 + 本块已用过暂存
    "hold": frozenset(
        {
            "not_started",
            "paused",
            "game_over",
            "quota_exceeded",
            "invalid_argument",
            "hold_already_used",
        }
    ),
    # 推进：未在运行中 = 门禁三值
    "tick": frozenset(
        {
            "not_started",
            "paused",
            "game_over",
            "quota_exceeded",
            "invalid_argument",
        }
    ),
    # 设置等级：不受门禁约束，只有越界
    "setLevel": frozenset({"quota_exceeded", "invalid_argument", "level_out_of_range"}),
    # 注入垃圾行：改棋盘内容 → 受门禁约束
    "injectGarbage": frozenset(
        {
            "not_started",
            "paused",
            "game_over",
            "quota_exceeded",
            "invalid_argument",
        }
    ),
    # 受控写入：门禁三值 + 越界 / 未授权 / 条件不满足（配额共用 quota_exceeded）
    "writeCells": frozenset(
        {
            "not_started",
            "paused",
            "game_over",
            "quota_exceeded",
            "invalid_argument",
            "out_of_bounds",
            "not_authorized",
            "write_condition_failed",
        }
    ),
    # 换规则集：不走统一生命周期门禁（局中切换由内核自判 illegal_transition）
    "switchRuleSet": frozenset({"invalid_argument", "illegal_transition", "unknown_rule_set"}),
}

#: 受统一生命周期门禁约束的命令（需求 §8.2 的门禁注）。
#: = 改变棋盘内容或活动方块位置、且不是生命周期命令本身的那批。
EXPECTED_GATED_COMMANDS: frozenset[str] = frozenset(
    {
        "moveLeft",
        "moveRight",
        "softDrop",
        "hardDrop",
        "rotateCW",
        "rotateCCW",
        "rotate180",
        "hold",
        "tick",
        "injectGarbage",
        "writeCells",
    }
)


def test_command_registry_size() -> None:
    """命令总数 = 17（需求 §8.2）。"""
    assert len(COMMANDS) == 17


def test_command_registry_is_closed_and_enumerable() -> None:
    """命令集**封闭且可枚举**（FR-2.1）：注册表就是全集，不留占位。"""
    assert set(COMMANDS) == set(EXPECTED_COMMAND_ARGS)
    assert COMMAND_TYPES == tuple(COMMANDS)
    assert len(COMMAND_TYPES) == len(set(COMMAND_TYPES))
    for name, spec in COMMANDS.items():
        assert spec.name == name, name
        assert spec.doc, name


def test_command_args_match_requirement_table() -> None:
    """每条命令的参数名 / 类型 / 可空性，与需求 §8.2 逐条相等。"""
    for name, expected in EXPECTED_COMMAND_ARGS.items():
        spec: CommandSpec = COMMANDS[name]
        actual = tuple((arg.name, arg.type, arg.nullable) for arg in spec.args)
        assert actual == expected, name
        arg_names = [arg.name for arg in spec.args]
        assert len(arg_names) == len(set(arg_names)), name
        for arg in spec.args:
            assert arg.doc, f"{name}.{arg.name}"


def test_only_four_commands_take_args() -> None:
    """四条**有参**（`setLevel` / `injectGarbage` / `writeCells` /
    `switchRuleSet`），其余 13 条无参。"""
    with_args = {name for name, spec in COMMANDS.items() if spec.args}
    assert with_args == {"setLevel", "injectGarbage", "writeCells", "switchRuleSet"}
    assert len(COMMANDS) - len(with_args) == 13


def test_reject_reason_enum() -> None:
    """拒绝原因集合 = 13 个值，与需求 §8.2 逐条相等（契约清单第 6 项）。"""
    assert len(REJECT_REASONS) == 13
    assert set(REJECT_REASONS) == set(EXPECTED_REJECT_REASONS)


def test_reject_reason_categories() -> None:
    """拒绝原因的分类与需求 §8.2 的「类」列相等，且不重不漏。"""
    assert {k: tuple(v) for k, v in REJECT_REASON_CATEGORIES.items()} == (
        EXPECTED_REJECT_CATEGORIES
    )
    covered: list[str] = []
    for reasons in REJECT_REASON_CATEGORIES.values():
        covered.extend(reasons)
    assert len(covered) == len(set(covered)), "分类之间有重叠"
    assert set(covered) == set(REJECT_REASONS), "分类没有覆盖全部拒绝原因"
    assert set(REJECT_REASON_CATEGORY_LABELS) == set(REJECT_REASON_CATEGORIES)
    for label in REJECT_REASON_CATEGORY_LABELS.values():
        assert label


def test_command_reject_reasons_match_expectation() -> None:
    """每条命令的可拒绝原因逐条相等——多一个少一个都要红。"""
    for name, expected in EXPECTED_COMMAND_REJECT_REASONS.items():
        spec = COMMANDS[name]
        assert set(spec.reject_reasons) == expected, name
        assert set(spec.reject_reasons) <= set(REJECT_REASONS), name


def test_command_args_forbid_float() -> None:
    """命令 `args` 禁浮点（B-7 / ADR-025）——写成断言，不靠眼睛看。

    `args` 参与重放，JSON 里 `1.0` 与 `1` 不可区分。**只有 `args` 受这条约束**：
    事件负载（如 `metric:sampled`）允许浮点，口径不同是对的。
    """
    assert "float" not in ARGS_VALUE
    assert "float" not in ARGS
    for name, spec in COMMANDS.items():
        for arg in spec.args:
            assert "float" not in arg.type, f"{name}.{arg.name}"


def test_command_arg_types_within_args_value_domain() -> None:
    """每个参数的类型必须是 `ARGS_VALUE` 取值域的成员。

    这条是**防分叉**的：参数类型若不在取值域里，说明两处各写了一份，
    改动时基线会显示全绿而实际已经分叉。
    """
    members = set(ARGS_VALUE.split(" | "))
    for name, spec in COMMANDS.items():
        for arg in spec.args:
            assert arg.type in members, f"{name}.{arg.name}: {arg.type}"


def test_lifecycle_gate_commands() -> None:
    """统一生命周期门禁：受门禁约束的命令**三个生命周期原因齐全**，
    不受门禁约束的一个都不带（需求 §8.2 的门禁注）。"""
    gated = {name for name in COMMANDS if is_lifecycle_gated(name)}
    assert gated == set(EXPECTED_GATED_COMMANDS)
    for name, spec in COMMANDS.items():
        has_all = set(LIFECYCLE_REJECT_REASONS) <= set(spec.reject_reasons)
        assert has_all == is_lifecycle_gated(name), name


def test_lifecycle_gate_is_explicit() -> None:
    """门禁归属是**必填的显式字段**——不是从拒绝原因反推出来的。

    反推的做法有个洞：加命令的人漏写三个生命周期原因，标志会安静地变成
    False，门禁随之失效（代价是「暂停期间命令照样执行」）。标志无默认值，
    漏表态直接 `TypeError`。
    """
    for name, spec in COMMANDS.items():
        assert spec.lifecycle_gated == (name in EXPECTED_GATED_COMMANDS), name


def test_command_type_fields_carry_command_names() -> None:
    """事件侧承载命令名的字段：**都在 `COMMAND_TYPE_FIELDS` 里，且类型一致**。

    取值域**写进类型串**了（形态 11）——字段的类型就是命令名的字面量枚举，
    基线因此看得见命令集变没变（M1 取值域收口）。
    """
    for event_type, field_name in COMMAND_TYPE_FIELDS:
        spec = EVENT_TYPES[event_type]
        field = next(f for f in spec.payload if f.name == field_name)
        assert field.type == EXPECTED_COMMAND_TYPE_LITERAL, f"{event_type}.{field_name}"
    listed = set(COMMAND_TYPE_FIELDS)
    actual = {
        (event_type, field.name)
        for event_type, spec in EVENT_TYPES.items()
        for field in spec.payload
        if field.name == "commandType"
    }
    assert actual == listed


def test_write_cells_cells_type() -> None:
    """机检：`writeCells` 的 `cells` 类型是 `tuple[tuple[int, int, int], ...]`。"""
    args = {arg.name: arg.type for arg in COMMANDS["writeCells"].args}
    assert args["cells"] == "tuple[tuple[int, int, int], ...]"


def test_inject_garbage_holes_type() -> None:
    """机检：`injectGarbage` 的 `holes` 是 `tuple[int, ...]`。"""
    args = {arg.name: arg.type for arg in COMMANDS["injectGarbage"].args}
    assert args["holes"] == "tuple[int, ...]"
    assert args["rows"] == "int"


def test_set_level_arg_type() -> None:
    """机检：`setLevel` 的 `level` 是 `int`。"""
    args = {arg.name: arg.type for arg in COMMANDS["setLevel"].args}
    assert args["level"] == "int"


def test_no_arg_is_nullable_in_v1() -> None:
    """契约 v1 的 17 条命令**没有可空参数**——`holes` 用空元组表示"无空洞"。"""
    for name, spec in COMMANDS.items():
        for arg in spec.args:
            assert not arg.nullable, f"{name}.{arg.name}"


# ---------------------------------------------------------------------------
# S4：快照契约声明（需求 §8.3）
#
# 下面几张表**独立转录**自 `docs/需求与边界.md` §8.3，与
# `contracts/snapshot.py` 的声明互为对照——转录的是「需求写了什么」，
# 不是「实现写了什么」，两者逐条相等才有意义（plan-M0.md S4 任务第 1 条）。
#
# **一处例外须知**：需求 §8.3 的 `extensions` 原文写作
# `tuple[tuple[str, JsonScalar], ...]`，`JsonScalar` 是**记号**（定义在架构
# §5.4），封闭文法里没有"记号"这一形态——所以下面转录成展开后的
# `int | float | str | bool | None`。展开值与 `types.SCALAR_MAP` 的相等性由
# `test_snapshot_types_reference_shared_constants` 单独钉住。
# ---------------------------------------------------------------------------

#: 22 个顶层快照字段（字段名 → 类型）。
EXPECTED_SNAPSHOT_FIELDS: dict[str, str] = {
    "version": "int",
    "rule": "str",
    "phase": EXPECTED_PHASE_LITERAL,
    "frame": "int",
    "board": "Board",
    "active": "Active | None",
    "ghostDelta": "tuple[int, int]",
    "hold": "str | None",
    "holdUsed": "bool",
    "next": "tuple[str, ...]",
    "score": "int",
    "lines": "int",
    "level": "int",
    "combo": "int",
    "b2b": "bool",
    "piecesPlaced": "int",
    "grounded": "bool",
    "lockTimer": "int",
    "gravityTimer": "int",
    "extensions": "tuple[tuple[str, int | float | str | bool | None], ...]",
    "extensionFingerprint": "str",
    "rngState": "tuple[int, ...] | None",
}

#: `board` 子结构的 5 个字段（需求 §8.3 的棋盘子表）。
EXPECTED_BOARD_FIELDS: dict[str, str] = {
    "width": "int",
    "height": "int",
    "hiddenRows": "int",
    "cells": "tuple[tuple[int, ...], ...]",
    "structureFingerprint": "str",
}

#: `Active` 结构的 4 个字段（需求 §8.3：`Active(pieceType, rotation, x, y)`）。
EXPECTED_ACTIVE_FIELDS: dict[str, str] = {
    "pieceType": "str",
    "rotation": "int",
    "x": "int",
    "y": "int",
}

#: 坐标系约定（需求 §8.3 的注，契约清单第 4 项）。
EXPECTED_COORDINATE_CONVENTIONS: dict[str, str] = {
    "x": "向右为正",
    "y": "向下为正",
    "row0": "棋盘第 0 行是顶部，行索引自上而下递增",
    "cells": "行优先（row-major），与架构 §5.4 的 TLV 编码口径同一条",
    "hiddenRows": "数量（顶部隐藏行数），不是可见起始行",
}

#: `PlacementInfo` 的 6 个字段（spec §6.5 / 架构 §7.3.1，契约清单第 10 项）。
EXPECTED_PLACEMENT_FIELDS: dict[str, str] = {
    "pieceType": "str",
    "rotation": "int",
    "cells": "tuple[tuple[int, int], ...]",
    "lastMoveWasRotation": "bool",
    "kickIndex": "int | None",
    "kickOffset": "tuple[int, int] | None",
}

#: 不进快照的运行时状态（spec §7.1 的六项）：标识符 → 中文说明。
EXPECTED_RUNTIME_ONLY_STATE: dict[str, str] = {
    "eventSequence": "事件序号（总线的唯一时间轴）",
    "roundCounter": "轮次计数",
    "commandCounter": "命令数计数",
    "rejectCoalescing": "同因拒绝合并表",
    "subscriberRegistry": "订阅注册表",
    "rngInstance": "随机源实例本体（快照只带走它的可序列化状态）",
}

#: 字段路径的写法：点分标识符（`board.cells` / `version`）——不许压行或散文。
_FIELD_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _snapshot_leaf_paths() -> set[str]:
    """快照的**叶子**字段路径集合（顶层 21 + `board` 5 + `active` 4 展开）。

    结构字段（`board` / `active`）本身不是叶子——它的内容由成员表展开。
    展开依据是**快照模块自己的成员表**（`BOARD_FIELDS` / `ACTIVE_FIELDS`），
    不是全局的 `NAMED_STRUCTS`：注册表现在有 8 个结构（快照 2 + 放置 1 +
    规则 5），拿"注册表里有没有这个名字"当"是不是快照子结构"的判据太宽——
    将来加一个类型为 `ScoreState` 的快照字段，它会被静默展开成子结构，
    叶子集合断言会以极难懂的方式变红。
    """
    snapshot_structs = {"Board": BOARD_FIELDS, "Active": ACTIVE_FIELDS}
    leaves: set[str] = set()
    for name, field in SNAPSHOT_FIELDS.items():
        members = snapshot_structs.get(field.type.removesuffix(" | None"))
        if members is None:
            leaves.add(name)
        else:
            leaves.update(f"{name}.{member}" for member in members)
    return leaves


def test_snapshot_field_count() -> None:
    """顶层 22 · `board` 子字段 5 · `Active` 子字段 4（需求 §8.3）。"""
    assert len(SNAPSHOT_FIELDS) == 22
    assert len(BOARD_FIELDS) == 5
    assert len(ACTIVE_FIELDS) == 4


def test_snapshot_fields_match_requirement() -> None:
    """顶层快照字段名与类型逐条相等——字段级漂移必须变红。"""
    assert {name: field.type for name, field in SNAPSHOT_FIELDS.items()} == (
        EXPECTED_SNAPSHOT_FIELDS
    )


def test_board_fields_match_requirement() -> None:
    """`board` 子字段名与类型逐条相等（含 hiddenRows 的"数量"语义，契约清单第 4 项）。"""
    assert {name: field.type for name, field in BOARD_FIELDS.items()} == (EXPECTED_BOARD_FIELDS)


def test_active_fields_match_requirement() -> None:
    """`Active` 子字段名与类型逐条相等。"""
    assert {name: field.type for name, field in ACTIVE_FIELDS.items()} == (EXPECTED_ACTIVE_FIELDS)


def test_snapshot_field_names_order_matches_requirement() -> None:
    """字段枚举顺序 = 需求 §8.3 的表格顺序——顺序进基线，重排 = 哈希全变。"""
    assert SNAPSHOT_FIELD_NAMES == tuple(EXPECTED_SNAPSHOT_FIELDS)


def test_coordinate_conventions_match_requirement() -> None:
    """坐标系约定与需求 §8.3 的注逐条相等（契约清单第 4 项，落声明便于复核）。"""
    assert dict(COORDINATE_CONVENTIONS) == EXPECTED_COORDINATE_CONVENTIONS


def test_snapshot_types_reference_shared_constants() -> None:
    """`extensions` / `rngState` / `board.cells` / `next` 引用 types.py 的取值域常量，不复制字面量。

    架构 §4.4 末条：同一形状只允许一处权威定义，引用处用常量——复制字面量
    会让两处各改各的，基线显示全绿而实际已经分叉。
    """
    types_map = {name: field.type for name, field in SNAPSHOT_FIELDS.items()}
    assert types_map["extensions"] == SCALAR_MAP
    assert types_map["rngState"] == f"{INT_TUPLE} | None"
    assert types_map["next"] == NEXT_QUEUE
    board_map = {name: field.type for name, field in BOARD_FIELDS.items()}
    assert board_map["cells"] == BOARD_CELLS


def test_next_queue_payload_fields_reference_shared_constant() -> None:
    """预览队列的 4 处声明引用 `types.NEXT_QUEUE`，不复制字面量。

    快照的 `next` 与三个事件的 `next` 负载是**同一份形状**（架构 §4.4 末条）：
    复制字面量的后果是常量改了、声明没跟着改，两处静默分叉而基线显示全绿。
    """
    for event_type in ("piece:spawned", "queue:refilled", "state:changed"):
        field = next(f for f in EVENT_TYPES[event_type].payload if f.name == "next")
        assert field.type == NEXT_QUEUE, event_type
    assert SNAPSHOT_FIELDS["next"].type == NEXT_QUEUE


def test_field_spec_names_match_their_keys() -> None:
    """字段声明表里，**键与 `FieldSpec.name` 必须是同一个名字**。

    表是"字段名 → 声明"的映射。键与声明里的名字分家时，声明面就自相矛盾：
    基线按键算哈希、按声明读名字，两边对不上，错误被摊平到看不见的地方
    （S5 反向验证 M9 实测：把 `Board.width` 的声明改名，全库仍然全绿）。
    """
    for table_name, table in (
        ("SNAPSHOT_FIELDS", SNAPSHOT_FIELDS),
        ("BOARD_FIELDS", BOARD_FIELDS),
        ("ACTIVE_FIELDS", ACTIVE_FIELDS),
    ):
        for key, spec in table.items():
            assert spec.name == key, f"{table_name}[{key}] 的声明里写着 {spec.name}"


def test_snapshot_struct_types_come_from_the_snapshot_module() -> None:
    """快照字段的类型串里出现的**结构名**必须来自快照模块自己的成员表。

    命名结构注册表现在有 8 个结构（快照 2 + 放置 1 + 规则 5），而快照的子结构
    只有 `board` / `active` 两个。将来加一个类型为 `ScoreState` 的快照字段，
    它会被"注册表命中"静默展开成子结构——叶子集合断言会以极难懂的方式变红。
    """
    snapshot_structs = {"Board", "Active"}
    for name, field in SNAPSHOT_FIELDS.items():
        base = field.type.removesuffix(" | None")
        if base in NAMED_STRUCTS:
            assert base in snapshot_structs, f"快照字段 {name} 用了非快照结构 {base}"


def test_named_structs_registry_matches_member_tables() -> None:
    """形态 9 的结构名 ↔ 成员表：**一张注册表说了算**（`NAMED_STRUCTS`）。

    命名结构不是自由别名——文法表附加规则 4 要求"没登记的结构名一律不合规"。
    成员表**从声明推导**：`Board` / `Active` 取自 `BOARD_FIELDS` /
    `ACTIVE_FIELDS`，其余取自 frozen dataclass 的字段注解（`PlacementInfo` 与
    规则侧的五个结构）——**注解即声明**，另抄一张表就是抄一份会漂移的副本。
    **回验从这张表推导结构名集合，不另抄一份名单**——抄一份的后果是成员表改了
    名，回验还认旧名，两边悄悄分叉。
    """
    assert set(NAMED_STRUCTS) == {
        "Board",
        "Active",
        "PlacementInfo",
        "RuleContext",
        "PlacementClass",
        "ClearGroup",
        "ScoreState",
        "ScoreDelta",
    }
    assert dict(NAMED_STRUCTS["Board"]) == {name: spec.type for name, spec in BOARD_FIELDS.items()}
    assert dict(NAMED_STRUCTS["Active"]) == {
        name: spec.type for name, spec in ACTIVE_FIELDS.items()
    }
    for name, cls in (
        ("PlacementInfo", PlacementInfo),
        ("RuleContext", RuleContext),
        ("PlacementClass", PlacementClass),
        ("ClearGroup", ClearGroup),
        ("ScoreState", ScoreState),
        ("ScoreDelta", ScoreDelta),
    ):
        assert dict(NAMED_STRUCTS[name]) == {
            field.name: str(field.type) for field in dataclasses.fields(cls)
        }, name
    for name, members in NAMED_STRUCTS.items():
        assert members, f"{name} 的成员表是空的"


def test_kernel_state_mapping_covers_every_snapshot_leaf() -> None:
    """M0 内核状态清单 ↔ 快照字段：**每个快照叶子字段恰好一个来源**。

    快照 = 内核状态全集（spec §7.1）：内核加状态字段，快照必须同步加字段。
    叶子集合由**快照模块自己的** `BOARD_FIELDS` / `ACTIVE_FIELDS` 展开算出——
    `board` / `active` 不是叶子，它们的 5 + 4 个成员才是。

    **不用 `NAMED_STRUCTS` 展开**：注册表是"哪些名字是命名结构"的判据，命中
    范围比快照子结构宽得多（规则侧的五个结构也在里面），拿它当"快照里什么是
    子结构"的判据，耦合一扩就变形（S5 报告 L3 的修订，见 `_snapshot_leaf_paths`）。
    """
    paths = [entry.snapshot_path for entry in KERNEL_STATE_TO_SNAPSHOT]
    assert len(paths) == len(set(paths)), "同一个快照字段被列了两次"
    assert set(paths) == _snapshot_leaf_paths()


def test_kernel_state_mapping_paths_are_machine_readable() -> None:
    """映射表两侧都是**点分路径**，不许压行、不许写散文。

    压行（`board.width/height/hiddenRows/cells`）和散文
    （`active（类型 / 朝向 / x / y，可空）`）都不是机器可读：机器没法把一行
    拆成几个源字段，"内核加字段 → 快照必须同步加字段"这条纪律就守不住。
    """
    for entry in KERNEL_STATE_TO_SNAPSHOT:
        assert _FIELD_PATH_RE.match(entry.kernel_field), entry.kernel_field
        assert _FIELD_PATH_RE.match(entry.snapshot_path), entry.snapshot_path


def test_kernel_state_mapping_derived_rows_have_a_real_source() -> None:
    """标了 `derived` 的行：来源要么是内核直接存着的字段，要么是运行时状态。

    结构指纹、扩展摘要是从别的状态算出来的；`rngState` 是运行时随机源实例的
    可序列化状态——三种来源都不该被当成"内核存着的字段"重复计入。
    """
    stored = {e.kernel_field for e in KERNEL_STATE_TO_SNAPSHOT if not e.derived}
    for entry in KERNEL_STATE_TO_SNAPSHOT:
        if entry.derived:
            assert entry.kernel_field in stored or entry.kernel_field in RUNTIME_ONLY_STATE, entry


def test_runtime_only_state_never_enters_the_snapshot() -> None:
    """运行时状态**不进快照**：标识符既不是快照字段，也不是内核存着的状态字段。

    标识符与快照字段、内核状态字段**同一套命名体系**（英文）——这条断言才
    可能真的报警。写成中文散文时它恒真（两边根本不是一个命名空间），是摆设。
    """
    runtime_ids = set(RUNTIME_ONLY_STATE)
    assert runtime_ids == set(EXPECTED_RUNTIME_ONLY_STATE), "运行时状态清单与转录表不一致"
    assert dict(RUNTIME_ONLY_STATE) == EXPECTED_RUNTIME_ONLY_STATE
    assert not runtime_ids & set(SNAPSHOT_FIELDS), "运行时状态混进了快照"
    assert not runtime_ids & _snapshot_leaf_paths(), "运行时状态混进了快照叶子"
    stored = {e.kernel_field for e in KERNEL_STATE_TO_SNAPSHOT if not e.derived}
    assert not runtime_ids & stored, "运行时状态混进了内核状态清单"


def test_placement_info_is_frozen_dataclass() -> None:
    """`PlacementInfo` 是 frozen dataclass（契约清单第 10 项）——内核递给
    规则集的一次性事实，规则集不可写。"""
    assert PlacementInfo.__dataclass_params__.frozen is True  # type: ignore[attr-defined]


def test_placement_info_fields_match_spec() -> None:
    """6 字段与 spec §6.5 / 架构 §7.3.1 逐条相等，**字段顺序也锁死**。

    注解在 `from __future__ import annotations` 下以源码字符串形式存进
    `__dataclass_fields__[*].type`，跨进程稳定——S6 基线脚本直接按它哈希。
    """
    actual = {field.name: field.type for field in dataclasses.fields(PlacementInfo)}
    assert actual == EXPECTED_PLACEMENT_FIELDS
    assert tuple(actual) == tuple(EXPECTED_PLACEMENT_FIELDS), "字段顺序不得重排"


def test_placement_cells_uses_cells_coordinate_constant() -> None:
    """`PlacementInfo.cells` 的类型与 `COORD_CELLS` 是同一条（架构 §4.4 末条）。

    dataclass 注解只能写真类型，不能引用字符串常量（PEP 563 会把注解存成
    `"COORD_CELLS"` 字面）——所以用断言把"注解 == 常量"钉死，常量一改这里红。
    """
    cells_type = {field.name: field.type for field in dataclasses.fields(PlacementInfo)}["cells"]
    assert cells_type == COORD_CELLS


# ---------------------------------------------------------------------------
# S5：规则接口签名（架构 §7.3.1 · 9 条）与中性交换结构
#
# 架构 §7.3.1 的签名表，独立转录一份做交叉核对。转录的是「架构写了什么」，
# 不是「声明写了什么」——拿声明自证，机检就只是证明自己没写错。
# ---------------------------------------------------------------------------

#: 键 = 签名名；值 = (参数声明 ((参数名, 类型串), ...), 返回类型串)。
EXPECTED_RULE_SIGNATURES: dict[str, tuple[tuple[tuple[str, str], ...], str]] = {
    "wrap_cell": (
        (("x", "int"), ("y", "int"), ("w", "int"), ("h", "int")),
        "tuple[int, int]",
    ),
    "gravity_vector": ((("ctx", "RuleContext"),), "tuple[int, int]"),
    "gravity_interval": ((("ctx", "RuleContext"),), "int"),
    "scan_clears": (
        (("board", "Sequence[Sequence[int]]"), ("ctx", "RuleContext")),
        "tuple[ClearGroup, ...]",
    ),
    "classify_placement": (
        (
            ("board", "Sequence[Sequence[int]]"),
            ("placement", "PlacementInfo"),
            ("ctx", "RuleContext"),
        ),
        "PlacementClass",
    ),
    "score_clear": (
        (
            ("groups", "Sequence[ClearGroup]"),
            ("ctx", "RuleContext"),
            ("prev", "ScoreState"),
        ),
        "ScoreDelta",
    ),
    "soft_drop_score": (
        (("ctx", "RuleContext"), ("distance", "int")),
        "int",
    ),
    "shape_cells": (
        (("ctx", "RuleContext"), ("pieceType", "str"), ("rotation", "int")),
        "tuple[tuple[int, int], ...]",
    ),
    "kick_offsets": (
        (
            ("ctx", "RuleContext"),
            ("pieceType", "str"),
            ("fromRotation", "int"),
            ("toRotation", "int"),
        ),
        "tuple[tuple[int, int], ...]",
    ),
}

#: 签名在架构 §7.3.1 表格里的顺序（**第 9 条紧跟 `score_clear` 之后**——
#: 计分组连着排；顺序进基线，重排 = major 变更）。
EXPECTED_RULE_SIGNATURE_ORDER = (
    "wrap_cell",
    "gravity_vector",
    "gravity_interval",
    "scan_clears",
    "classify_placement",
    "score_clear",
    "soft_drop_score",
    "shape_cells",
    "kick_offsets",
)

#: 9 条签名按能力分组：四项接口（边界 / 重力 / 消行 / 计分）+ 两张表
#: （形状 / 踢墙）。FR-4.9：规则模块按这四项分别可插拔，**不另开第五项**。
EXPECTED_SIGNATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "边界（拓扑）": ("wrap_cell",),
    "重力": ("gravity_vector", "gravity_interval"),
    "消行": ("scan_clears", "classify_placement"),
    "计分": ("score_clear", "soft_drop_score"),
    "形状": ("shape_cells",),
    "踢墙": ("kick_offsets",),
}

#: 四个中性交换结构的字段声明（字段名 + 类型串），**顺序也是契约**。
EXPECTED_EXCHANGE_STRUCTURE_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "PlacementClass": (("tSpin", "str"), ("classKey", "str")),
    "ClearGroup": (
        ("axis", EXPECTED_CLEAR_AXIS_LITERAL),
        ("index", "int"),
        ("cells", "tuple[tuple[int, int], ...]"),
        ("classification", "PlacementClass | None"),
    ),
    "ScoreState": (
        ("score", "int"),
        ("lines", "int"),
        ("combo", "int"),
        ("b2b", "bool"),
        ("level", "int"),
    ),
    "ScoreDelta": (
        ("scoreDelta", "int"),
        ("linesDelta", "int"),
        ("state", "ScoreState"),
    ),
}


def test_rule_signature_count_is_nine() -> None:
    """规则接口签名 = **9 条**（四项接口 + 形状表 / 踢墙表 + M1 的软降计分）。

    **条数进基线**（结构性计数键 `count/ruleSignature`）：整条新增一条签名只
    产生新键，按"新增键放行"的口径会一路绿灯——比改参数更大的接口变更反而
    更难被发现。
    """
    assert len(RULE_SIGNATURES) == 9
    assert len(RULE_SIGNATURE_NAMES) == 9
    assert len(set(RULE_SIGNATURE_NAMES)) == 9


def test_soft_drop_score_is_its_own_signature() -> None:
    """第 9 条 `soft_drop_score` 的**参数名与类型**逐条钉死。

    **为什么是独立一条签名**，而不是给 `score_clear` 加参数：消行结算在锁定
    时调一次，软降发生在放置途中，**时机与频率都不同**；软降距离是**单次
    命令的参数**，塞进跨帧的 `RuleContext` 会多一个"每帧重置"的瞬时量。

    **落位 = 紧跟 `score_clear` 之后**（计分组连着排）：声明顺序进基线，
    后补的插中间 = 已有键的顺序变了 = 撞红。
    """
    signature = RULE_SIGNATURES["soft_drop_score"]
    assert tuple((param.name, param.type) for param in signature.params) == (
        ("ctx", "RuleContext"),
        ("distance", "int"),
    )
    assert signature.returns == "int"
    order = RULE_SIGNATURE_NAMES
    assert order.index("soft_drop_score") == order.index("score_clear") + 1


def test_rule_signatures_match_architecture_table() -> None:
    """每条签名的**参数名与类型**与架构 §7.3.1 表格逐条相等。

    参数名即契约：改名 = major 变更（签名进基线，架构 §7.6）。
    """
    assert set(RULE_SIGNATURES) == set(EXPECTED_RULE_SIGNATURES)
    for name, (params, returns) in EXPECTED_RULE_SIGNATURES.items():
        signature = RULE_SIGNATURES[name]
        assert tuple((param.name, param.type) for param in signature.params) == params, name
        assert signature.returns == returns, name


def test_rule_signature_order_matches_architecture_table() -> None:
    """签名顺序 = 架构 §7.3.1 表格顺序（顺序进基线，不得重排）。"""
    assert RULE_SIGNATURE_NAMES == EXPECTED_RULE_SIGNATURE_ORDER


def test_rule_signatures_cover_four_capabilities_and_two_tables() -> None:
    """9 条 = 四项接口 + 两张表 + 软降计分，**不另开第五项**（FR-4.9）。"""
    grouped = tuple(name for group in EXPECTED_SIGNATURE_GROUPS.values() for name in group)
    assert sorted(grouped) == sorted(RULE_SIGNATURE_NAMES)
    assert len(grouped) == len(set(grouped)), "同一条签名被分到了两个能力下"


def test_gravity_vector_returns_a_vector_not_only_dy() -> None:
    """`gravity_vector` 返回**向量 (x, y)**，不能只有 dy——横向重力时 dy = 0。"""
    assert RULE_SIGNATURES["gravity_vector"].returns == "tuple[int, int]"
    assert RULE_SIGNATURES["gravity_vector"].returns != "int"


def test_shape_and_kick_tables_carry_the_axis_dimension() -> None:
    """形状表 / 踢墙表**必须带 `ctx`**（轴维度）。

    M4-A5 要求表随重力方向变换，而变换由规则集整体提供、内核不做变换——
    接口不带轴就选不出表（架构 §7.3.1 纪律 2）。**这是签名必须现在定的核心理由**。
    """
    for name in ("shape_cells", "kick_offsets"):
        params = RULE_SIGNATURES[name].params
        assert params[0].name == "ctx", name
        assert params[0].type == "RuleContext", name


def test_scan_clears_returns_groups_not_a_count() -> None:
    """`scan_clears` 返回 `tuple[ClearGroup, ...]`——**不能只返回数量**。

    只返回数量，内核就不知道消的是哪几行、哪几个格子，塌落动画与计分都失去
    素材（架构 §7.3.1）。
    """
    assert RULE_SIGNATURES["scan_clears"].returns == "tuple[ClearGroup, ...]"
    assert RULE_SIGNATURES["scan_clears"].returns != "int"


def test_score_clear_takes_previous_state_and_returns_delta() -> None:
    """`score_clear` 是纯函数：吃旧状态、吐增量与新状态，**落笔由内核做**。"""
    params = {param.name: param.type for param in RULE_SIGNATURES["score_clear"].params}
    assert params == {
        "groups": "Sequence[ClearGroup]",
        "ctx": "RuleContext",
        "prev": "ScoreState",
    }
    assert RULE_SIGNATURES["score_clear"].returns == "ScoreDelta"


def test_rule_context_fields_match_architecture() -> None:
    """`RuleContext` 5 字段，与架构 §7.3.1 逐条相等（**顺序也是契约**）。

    注解在 `from __future__ import annotations` 下以源码字符串形式存进
    `__dataclass_fields__[*].type`，跨进程稳定——S6 基线脚本直接按它哈希。
    """
    actual = tuple((field.name, str(field.type)) for field in dataclasses.fields(RuleContext))
    assert actual == (
        ("frame", "int"),
        ("level", "int"),
        ("piecesPlaced", "int"),
        ("axis", EXPECTED_AXIS_LITERAL),
        ("stepMs", "int"),
    )


def test_exchange_structure_fields_match_declaration() -> None:
    """四个中性交换结构的字段与声明逐条相等，**字段顺序也锁死**。"""
    for cls in (PlacementClass, ClearGroup, ScoreState, ScoreDelta):
        expected = EXPECTED_EXCHANGE_STRUCTURE_FIELDS[cls.__name__]
        actual = tuple((field.name, str(field.type)) for field in dataclasses.fields(cls))
        assert actual == expected, cls.__name__


def test_exchange_structures_are_frozen() -> None:
    """规则集不可变：`RuleContext` 与四个交换结构都是 frozen dataclass。

    规则集一旦持有可变状态，FR-10.2 的存档恢复就得额外序列化规则集状态
    （架构 §7.3.1 纪律 1）。
    """
    for cls in (RuleContext, PlacementClass, ClearGroup, ScoreState, ScoreDelta):
        assert cls.__dataclass_params__.frozen is True  # type: ignore[attr-defined]


def test_clear_group_cells_use_the_shared_coordinate_constant() -> None:
    """`ClearGroup.cells` 的类型与 `COORD_CELLS` 是同一条（架构 §4.4 末条）。"""
    cells_type = {field.name: str(field.type) for field in dataclasses.fields(ClearGroup)}["cells"]
    assert cells_type == COORD_CELLS


def test_clear_group_carries_the_placement_classification() -> None:
    """T-Spin 分类**有地方可去**：`ClearGroup.classification`。

    冻结的 `score_clear(groups, ctx, prev)` 只吃消行组，分类若无处安放，
    计分模块就看不见 T-Spin，T-Spin 的分值没法算（FR-4.6）。分类挂在组上，
    而不是给签名加参数——加参数就是改冻结签名。
    """
    fields = {field.name: str(field.type) for field in dataclasses.fields(ClearGroup)}
    assert fields["classification"] == "PlacementClass | None"


def test_rule_signature_types_reference_shared_constants() -> None:
    """签名里的共享形状引用 `types.py` 的常量，不复制字面量（架构 §4.4 末条）。

    复制字面量会两处各改各的，基线显示全绿而实际已经分叉。
    """
    scan_params = {param.name: param.type for param in RULE_SIGNATURES["scan_clears"].params}
    assert scan_params["board"] == BOARD_VIEW
    assert RULE_SIGNATURES["wrap_cell"].returns == VECTOR2
    assert RULE_SIGNATURES["gravity_vector"].returns == VECTOR2
    assert RULE_SIGNATURES["shape_cells"].returns == COORD_CELLS
    assert RULE_SIGNATURES["kick_offsets"].returns == COORD_CELLS


def test_axis_vocabularies_are_not_mixed() -> None:
    """重力方向与消行轴是**两套**词汇，别混成一套。

    `RuleContext.axis` 是重力方向（变体维度，契约层钉死）；`ClearGroup.axis`
    是"这组被消的是行还是列"。标准玩法下两者对应 `down` ↔ `row`。
    """
    assert AXIS_VALUES == ("down", "left", "right", "up")
    assert CLEAR_AXIS_VALUES == ("row", "column")
    assert not set(AXIS_VALUES) & set(CLEAR_AXIS_VALUES)


def test_contract_level_domains_are_enforced_at_construction() -> None:
    """契约层钉死的取值域**构造即校验**——域外的值当场被拒。

    取值域只活在散文里等于没有取值域：内核读 `ctx.axis` 才知道重力朝哪、
    读 `group.axis` 才知道消的是行还是列，规则集写错轴会一路飘到渲染层。
    """
    assert RuleContext(frame=0, level=1, piecesPlaced=0, axis="down", stepMs=0).axis == "down"
    with pytest.raises(ValueError):
        RuleContext(frame=0, level=1, piecesPlaced=0, axis="diagonal", stepMs=0)
    assert ClearGroup(axis="row", index=3, cells=()).index == 3
    with pytest.raises(ValueError):
        ClearGroup(axis="diagonal", index=3, cells=())


def test_value_domains_are_written_into_the_type_strings() -> None:
    """**取值域写进类型串**（M1 取值域收口）：**逐字相等**，不是"包含某个词"。

    收口的四条字段——快照 `phase` · `RuleContext.axis` · `ClearGroup.axis` ·
    事件 `commandType`——类型串必须与上面那份**独立转录**逐字相等。

    为什么是"逐字"：类型串进基线哈希（架构 §4.4 末条），取值域变了而类型串
    没变，基线就看不见——**基线绿不能证明取值域没被偷偷改宽**。
    """
    assert SNAPSHOT_FIELDS["phase"].type == EXPECTED_PHASE_LITERAL
    assert NAMED_STRUCTS["RuleContext"]["axis"] == EXPECTED_AXIS_LITERAL
    assert NAMED_STRUCTS["ClearGroup"]["axis"] == EXPECTED_CLEAR_AXIS_LITERAL
    for event_type, field_name in COMMAND_TYPE_FIELDS:
        spec = EVENT_TYPES[event_type]
        field = next(f for f in spec.payload if f.name == field_name)
        assert field.type == EXPECTED_COMMAND_TYPE_LITERAL, event_type


def test_the_type_strings_are_derived_from_the_value_domains() -> None:
    """类型串**由取值域派生**，不是手抄的第二份名单。

    `literal_enum(AXIS_VALUES)` 与 `AXIS_VALUES` 不可能分叉——手写类型串 +
    另抄一份取值域，两处静默分叉时基线显示全绿，而实际的值域已经变了。
    """
    assert AXIS_LITERAL == literal_enum(AXIS_VALUES)
    assert CLEAR_AXIS_LITERAL == literal_enum(CLEAR_AXIS_VALUES)
    assert PHASE_LITERAL == literal_enum(PHASE_VALUES)
    assert COMMAND_TYPE_LITERAL == literal_enum(COMMAND_TYPES)


def test_the_phase_type_alias_matches_the_phase_values() -> None:
    """生命周期的**取值类型**（typing 形态）与**取值域元组**是同一份词汇。

    契约层一个地方写了值（`PHASE_*` 常量），另一个地方写类型（`Phase` 别名）
    ——两份分叉时，内核的状态机会用着 A 的类型、契约声明着 B 的取值域。

    **注意 `get_args` 的陷阱**：PEP 695 的 `type` 别名是 `TypeAliasType`，
    `get_args(别名)` 返回**空元组**（不报错）——要取成员得先拆出
    `别名.__value__`（PEP 695 定义的属性）。这里写死拆法，免得以后有人
    直接 `get_args(Phase)` 拿到空元组还以为"成员为空"。
    """
    assert get_args(Phase.__value__) == PHASE_VALUES


def test_the_rule_set_defined_fields_stay_narrow() -> None:
    """**内核只透传的字段不许收口**——钉死会把变体挡在门外。

    判据（架构 §7.3.1）：内核要解读的 → 契约层钉死；内核只透传的 → 规则集
    定义。`tSpin` / `classKey` 是规则集自己的词汇，方块类型（`hold` / `next`）
    与规则集标识（`rule`）同理——收口它们等于告诉变体"你的词汇只能是这样"。
    """
    assert NAMED_STRUCTS["PlacementClass"]["tSpin"] == "str"
    assert NAMED_STRUCTS["PlacementClass"]["classKey"] == "str"
    assert SNAPSHOT_FIELDS["hold"].type == "str | None"
    assert SNAPSHOT_FIELDS["next"].type == NEXT_QUEUE
    assert SNAPSHOT_FIELDS["rule"].type == "str"


def test_rule_set_defined_fields_name_real_fields() -> None:
    """`RULE_SET_DEFINED_FIELDS` 点名的字段**必须真实存在**，且是 `str` 字段。

    这张表是"取值归属的表态"，表里的名字写错（结构名 / 字段名拼错）会让
    豁免失效——某个 `str` 字段既没校验也没被点名，却没人发现。
    顺带断言：规则集定义的取值**不被契约层拦**（换规则集就换词汇）。
    """
    structs = {
        "RuleContext": RuleContext,
        "PlacementClass": PlacementClass,
        "ClearGroup": ClearGroup,
        "ScoreState": ScoreState,
        "ScoreDelta": ScoreDelta,
    }
    for (struct_name, field_name), doc in RULE_SET_DEFINED_FIELDS.items():
        assert doc, (struct_name, field_name)
        assert struct_name in structs, struct_name
        fields = {f.name: str(f.type) for f in dataclasses.fields(structs[struct_name])}
        assert field_name in fields, (struct_name, field_name)
        assert fields[field_name] == "str", (struct_name, field_name)
        values = {f.name: "" for f in dataclasses.fields(structs[struct_name])}
        values[field_name] = "__不在取值域内__"
        structs[struct_name](**values)  # 规则集定义的取值，契约层不拦


def test_every_str_field_declares_its_domain() -> None:
    """规则侧结构里**每个 `str` 字段都必须表态**。

    要么在契约层钉死取值域并**构造即校验**，要么被 `RULE_SET_DEFINED_FIELDS`
    点名为"规则集定义"。两头都不沾的字段：新加一个 `str` 字段却忘了表态，
    这条断言会红。
    """
    structs = (RuleContext, PlacementClass, ClearGroup, ScoreState, ScoreDelta)
    for struct in structs:
        for field in dataclasses.fields(struct):
            if str(field.type) != "str":
                continue
            key = (struct.__name__, field.name)
            if key in RULE_SET_DEFINED_FIELDS:
                continue
            values = {f.name: "" for f in dataclasses.fields(struct)}
            values[field.name] = "__不在取值域内__"
            # 错误信息要指名**这个**字段——否则另一个字段的校验先炸，断言就白过。
            with pytest.raises(ValueError, match=field.name):
                struct(**values)  # type: ignore[arg-type]


def test_member_types_rejects_non_string_annotations() -> None:
    """成员表推导要求注解是**源码字符串**——否则会产出非法类型串。

    缺 `from __future__ import annotations` 的模块里，`field.type` 是运行时
    类型对象，`str()` 出来是 `"<class 'int'>"` 这种带模块前缀的写法：既不是
    形态 9 的标识符，也不是合法类型串，而它会被基线哈希进去。
    """
    from tetris_kernel.contracts.structures import _member_types

    no_future = dataclasses.make_dataclass("NoFuture", [("value", int)])
    with pytest.raises(TypeError):
        _member_types(no_future)


def test_contracts_never_import_kernel_or_rules() -> None:
    """`contracts/` 内**不 import `kernel` / `rules` 的任何 dataclass**（架构 §7.3）。

    规则接口只用中性类型——否则 `kernel > rules` 契约第一天就红，然后大概率被
    加豁免，契约就废了。**越级相对导入也要挡**：绝对导入被白名单挡下，
    `from ..kernel.snapshot import …` 却是包内相对导入，白名单看不见它。
    """
    contracts_dir = Path(contracts_pkg.__file__).parent
    for path in sorted(contracts_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level <= 1, f"{path.name}：越级相对导入（level={node.level}）"
                module = node.module or ""
                assert not module.startswith("tetris_kernel."), f"{path.name}：{module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("tetris_kernel."), f"{path.name}：{alias.name}"


# ---------------------------------------------------------------------------
# S4：类型文法回验（plan-M0.md 卡点：S4/S5 新增类型串要按新规则回验，别只写不验）
# ---------------------------------------------------------------------------

#: 标量名（形态 1）。
_SCALAR_NAMES = frozenset({"int", "str", "bool", "float"})

#: 形态 9 允许的命名结构——**从 `NAMED_STRUCTS` 推导**，不另抄一份名单。
#: 抄一份的后果：成员表改了名，回验还认旧名，规则与机检悄悄分叉
#: （文法表附加规则 4 要求"没登记的结构名一律不合规"，唯一守着它的只能是
#: 这张注册表本身）。
_NAMED_STRUCTS = frozenset(NAMED_STRUCTS)


class _GrammarError(ValueError):
    """类型串不符合封闭文法（文法表见 `tetris_kernel.contracts.types`）。"""


def _parse_type(text: str) -> set[str]:
    """解析一个类型表达式串，返回用到的文法形态集合（"1"–"9"）。

    只接受契约里实际使用的书写形式：联合成员之间 ` | `，元组元素之间 `, `。
    文法表里没有的形态一律 `_GrammarError`——"看着在基线里、实际没被哈希"
    的失效模式就是从认不出的串开始的。
    """
    forms: set[str] = set()
    pos = 0
    n = len(text)

    def skip_ws() -> None:
        nonlocal pos
        while pos < n and text[pos] == " ":
            pos += 1

    def expect(ch: str) -> None:
        nonlocal pos
        skip_ws()
        if pos >= n or text[pos] != ch:
            raise _GrammarError(f"{text!r} 位置 {pos}：期待 {ch!r}")
        pos += 1

    def parse_atom(*, allow_literal: bool = True) -> str:
        """解析一个原子（标量 / None / 命名结构 / 元组 / 字面量枚举），返回其种类。"""
        nonlocal pos
        skip_ws()
        if text.startswith("None", pos):
            pos += 4
            return "none"
        match = re.match(r"[A-Za-z_][A-Za-z_0-9]*", text[pos:])
        if match is None:
            raise _GrammarError(f"{text!r} 位置 {pos}：期待类型名")
        word = match.group(0)
        pos += len(word)
        if word == "tuple":
            parse_tuple()
            return "tuple"
        if word == "Sequence":
            parse_sequence()
            return "sequence"
        if word == "Literal":
            if not allow_literal:
                raise _GrammarError(
                    f"{text!r}：形态 11（字面量枚举）只能出现在叶子位置，不许嵌进容器形态"
                )
            parse_literal()
            return "literal"
        if word in _SCALAR_NAMES:
            forms.add("1")
            return word
        if word in _NAMED_STRUCTS:
            forms.add("9")
            return word
        raise _GrammarError(f"{text!r}：未知类型名 {word!r}")

    def parse_literal() -> None:
        """解析 `Literal["a", "b"]`（形态 11：成员 ≥ 1 个字符串字面量）。

        **单双引号都收**：引号风格是排版（PEP 563 存注解时编译器统一重加成
        单引号，源码里手写的可能是双引号）。
        """
        nonlocal pos
        if pos >= n or text[pos] != "[":
            raise _GrammarError(f"{text!r}：Literal 后必须是 [")
        pos += 1
        members: list[str] = []
        while True:
            skip_ws()
            if pos >= n or text[pos] not in ("'", '"'):
                raise _GrammarError(f"{text!r} 位置 {pos}：期待字符串字面量")
            quote = text[pos]
            pos += 1
            start = pos
            while pos < n and text[pos] != quote:
                pos += 2 if text[pos] == "\\" else 1
            if pos >= n:
                raise _GrammarError(f"{text!r}：字符串字面量没有闭合")
            members.append(text[start:pos])
            pos += 1
            skip_ws()
            if pos < n and text[pos] == ",":
                pos += 1
                continue
            break
        expect("]")
        if not members:
            raise _GrammarError(f"{text!r}：字面量枚举至少要有一个成员")
        if len(set(members)) != len(members):
            raise _GrammarError(f"{text!r}：字面量枚举的成员互不相同")
        forms.add("11")

    def parse_union(*, allow_literal: bool = True) -> list[str]:
        """解析 `T1 | T2 | ...`，返回成员种类列表（单原子也算长度 1 的联合）。"""
        nonlocal pos
        members = [parse_atom(allow_literal=allow_literal)]
        while True:
            save = pos
            skip_ws()
            if pos < n and text[pos] == "|":
                pos += 1
                members.append(parse_atom(allow_literal=allow_literal))
            else:
                pos = save
                return members

    def parse_tuple() -> None:
        """解析 `tuple[...]` 的四种形态（4 定长 / 5 变长 / 6·7 嵌套 / 8 递归）。"""
        nonlocal pos
        if pos >= n or text[pos] != "[":
            raise _GrammarError(f"{text!r}：tuple 后必须是 [")
        pos += 1
        skip_ws()
        if text.startswith("...", pos):  # 形态 8：递归元组
            pos += 3
            expect("]")
            forms.add("8")
            return
        # **容器形态的元素位不是叶子位置**——形态 11 不许嵌进来（文法附加规则 6）。
        elements = [parse_union(allow_literal=False)]
        skip_ws()
        if pos < n and text[pos] == ",":
            pos += 1
            skip_ws()
            if text.startswith("...", pos):  # 形态 5：变长元组
                if len(elements) != 1:
                    raise _GrammarError(f"{text!r}：变长元组只能有一个元素类型")
                pos += 3
                expect("]")
                forms.add("5")
                return
            while True:  # 形态 4 / 6 / 7：定长元组，元素可为联合或嵌套元组
                elements.append(parse_union(allow_literal=False))
                save = pos
                skip_ws()
                if pos < n and text[pos] == ",":
                    pos += 1
                else:
                    pos = save
                    break
        if len(elements) == 1:
            raise _GrammarError(f"{text!r}：单元素元组 tuple[T] 不在文法表里")
        expect("]")
        forms.add("4")

    def parse_sequence() -> None:
        """解析 `Sequence[T]`（形态 10：只读序列容器，T 为形态 1–9 的任意组合）。"""
        nonlocal pos
        if pos >= n or text[pos] != "[":
            raise _GrammarError(f"{text!r}：Sequence 后必须是 [")
        pos += 1
        parse_union(allow_literal=False)
        expect("]")
        forms.add("10")

    kinds = parse_union()
    if len(kinds) >= 2:
        if len(kinds) == 2 and kinds[1] == "none":
            forms.add("3")  # 形态 3：可空
        else:
            forms.add("2")  # 形态 2：联合
    if pos != n:  # 不再吞空白：尾随空格 = 没解析干净 = 不合规（串要进哈希）
        raise _GrammarError(f"{text!r} 位置 {pos}：有无法解析的尾巴")
    return forms


def _all_declared_type_strings() -> list[tuple[str, str]]:
    """收集契约层全部类型串：位置标签 → 类型串。新声明域加进这里。"""
    pairs: list[tuple[str, str]] = []
    for name, value in (
        ("JSON_VALUE", JSON_VALUE),
        ("ARGS_VALUE", ARGS_VALUE),
        ("ARGS", ARGS),
        ("CELLS", CELLS),
        ("INT_TUPLE", INT_TUPLE),
        ("SCALAR_MAP", SCALAR_MAP),
        ("COORD_CELLS", COORD_CELLS),
        ("BOARD_CELLS", BOARD_CELLS),
    ):
        pairs.append((f"types.{name}", value))
    for field in ENVELOPE_FIELDS:
        pairs.append((f"envelope.{field.name}", field.type))
    for field in METADATA_KEYS:
        pairs.append((f"metadata.{field.name}", field.type))
    for event_type, spec in EVENT_TYPES.items():
        for field in spec.payload:
            pairs.append((f"{event_type}.{field.name}", field.type))
    for command_name, spec in COMMANDS.items():
        for arg in spec.args:
            pairs.append((f"command:{command_name}.{arg.name}", arg.type))
    for name, field in SNAPSHOT_FIELDS.items():
        pairs.append((f"snapshot.{name}", field.type))
    for name, field in BOARD_FIELDS.items():
        pairs.append((f"board.{name}", field.type))
    for name, field in ACTIVE_FIELDS.items():
        pairs.append((f"active.{name}", field.type))
    # `PlacementInfo` 的 dataclass 注解也是声明的类型串（spec §6.5 定稿的 6 条），
    # 同样按封闭文法回验——S4 自称"全库类型串回验"，漏掉这 6 条就是漏了。
    for field in dataclasses.fields(PlacementInfo):
        pairs.append((f"placement.{field.name}", str(field.type)))
    # 规则接口签名的参数与返回类型（S5）：签名进基线，类型串同样要按文法回验。
    for name, signature in RULE_SIGNATURES.items():
        for param in signature.params:
            pairs.append((f"rule:{name}.{param.name}", param.type))
        pairs.append((f"rule:{name}.returns", signature.returns))
    # 命名结构的成员表（S5）：注册表里的每个成员类型都按文法回验——
    # 形态 9 的成员类型变了而没人验，基线展开时才会发现。
    for struct_name, members in NAMED_STRUCTS.items():
        for member_name, member_type in members.items():
            pairs.append((f"struct:{struct_name}.{member_name}", member_type))
    return pairs


def test_all_type_strings_match_grammar() -> None:
    """全部类型串按封闭文法回验（plan-M0.md S4 任务第 3 条 · S5 续）。

    - 解析失败 = 文法表里没有的形态，红；
    - 命名结构引用必须是**已登记**的结构名（形态 9，名单从 `NAMED_STRUCTS` 推导）；
    - 形态 8（递归元组）只允许 `JSON_VALUE` 与信封 `payload` / `metadata`
      两个容器串（开放取值域专用，文法附加规则第 3 条）；
    - 形态 10（只读序列）只允许棋盘视图与消行组序列（文法附加规则第 5 条）。
    """
    recursive_allowed = {
        JSON_VALUE,
        f"tuple[tuple[str, {JSON_VALUE}], ...]",
    }
    # 形态 10 的允许取值：棋盘只读视图 + 消行组序列。
    # 这里抄的是**限制**不是声明——声明侧的类型串各自有常量或唯一出处。
    sequence_allowed = {BOARD_VIEW, "Sequence[ClearGroup]"}
    used_sequences: set[str] = set()
    for where, type_string in _all_declared_type_strings():
        forms = _parse_type(type_string)
        assert forms, where
        if "8" in forms:
            assert type_string in recursive_allowed, (
                f"{where}：形态 8 只用于开放取值域（{type_string}）"
            )
        if "10" in forms:
            assert type_string in sequence_allowed, (
                f"{where}：形态 10 只用于棋盘视图 / 消行组序列（{type_string}）"
            )
            used_sequences.add(type_string)
    # 白名单不许有陈旧条目——形态 10 的用法变了（加了 / 删了），白名单要跟着改。
    assert used_sequences == sequence_allowed, (
        f"形态 10 白名单与实际用法不一致：多出 {used_sequences - sequence_allowed}，"
        f"少了 {sequence_allowed - used_sequences}"
    )


def test_grammar_rejects_unknown_forms() -> None:
    """文法是**封闭**的：表里没有的写法必须解析失败（负例抽查）。"""
    for bad in (
        "int32",  # 未知标量
        "Piece",  # 未声明的命名结构
        "tuple[int]",  # 单元素元组
        "tuple[int, str, ...]",  # 变长元组带多个元素类型
        "list[int]",  # 非元组容器
        "int |",  # 悬空联合
        "int ",  # 尾巴上的空格也算没解析干净
    ):
        with pytest.raises(_GrammarError):
            _parse_type(bad)


# ---------------------------------------------------------------------------
# S4：公共叶子纯净度（plan-M0.md S4 任务第 4 条 / 架构 §2.3 · §4.1）
# ---------------------------------------------------------------------------

#: `contracts/` 允许的绝对导入白名单——全是标准库。相对导入（包内）放行。
_ALLOWED_CONTRACTS_IMPORTS = frozenset(
    {"__future__", "collections.abc", "dataclasses", "types", "typing"}
)


def test_contracts_public_leaf_purity() -> None:
    """机检：`contracts/` 内的模块只 import 标准库或包内相对模块。

    公共叶子纪律：不许出现绝对路径的 `tetris_kernel.*` import，更不许依赖
    第三方——契约层是所有人共同 import 的底座，它反过来依赖任何东西，
    循环导入只是时间问题。
    """
    contracts_dir = Path(contracts_pkg.__file__).parent
    for path in sorted(contracts_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name in _ALLOWED_CONTRACTS_IMPORTS, (
                        f"{path.name}: import {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    continue  # 包内相对导入
                assert node.module in _ALLOWED_CONTRACTS_IMPORTS, (
                    f"{path.name}: from {node.module} import …"
                )


def test_package_all_matches_actual_exports() -> None:
    """`contracts/__init__.py` 的 `__all__` 与实际导出**一一对应**。

    漏一个导出名不报错——只是 `from tetris_kernel.contracts import X` 到时候
    才失败（或者名字进不了包级 API）。这条断言把它变成当场就红。
    """
    exported = list(contracts_pkg.__all__)
    assert len(exported) == len(set(exported)), "__all__ 里有重复名"
    for name in exported:
        assert hasattr(contracts_pkg, name), f"__all__ 声明了 {name}，包上却没有"
    submodule_names = {
        "commands",
        "events",
        "placement",
        "rules",
        "snapshot",
        "structures",
        "systems",
        "types",
    }
    public = {n for n in dir(contracts_pkg) if not n.startswith("_") and n not in submodule_names}
    assert public == set(exported), "包级公开名与 __all__ 不一致"


# ---------------------------------------------------------------------------
# S15 补全：契约面新守的三块
#
# ① `state:changed` 的负载是**快照的投影**——宿主在帧末发它（§1.4.4.1 ③），
#    这条契约面要有牙齿：快照字段改名 / 改型 → 红；
# ② 禁浮点（B-7 / ADR-025）**只约束 `args`**，事件负载不受约束；
# ③ 快照 = 内核状态全集（契约清单第 15 项）：内核加字段 → 快照必须同步加字段，
#    这条纪律**能反过来守**（契约表自己的注释就是这么写的）。
#
# 前两块是**补上没守的面**，第三块是把 S4 标注为"人检"的第 15 项落成机检。
# ---------------------------------------------------------------------------

#: `state:changed` 的负载字段 → 它投影的快照字段路径。
#:
#: 转录自需求 §8.1 的「轻量摘要（不含整张棋盘）」与契约 `state:changed` 的声明：
#: 12 个字段是快照的**同名投影**，只有 `activeType` 是**类型投影**（快照的
#: `active` 可空 → 事件侧给 `str | None`）。
EXPECTED_STATE_CHANGED_PROJECTION: dict[str, str] = {
    "version": "version",
    "rule": "rule",
    "phase": "phase",
    "score": "score",
    "lines": "lines",
    "level": "level",
    "combo": "combo",
    "b2b": "b2b",
    "piecesPlaced": "piecesPlaced",
    "activeType": "active.pieceType",
    "hold": "hold",
    "holdUsed": "holdUsed",
    "next": "next",
}

#: `state:changed` 的负载字段顺序（**顺序进基线**，重排 = 哈希全变）。
EXPECTED_STATE_CHANGED_ORDER: tuple[str, ...] = (
    "version",
    "rule",
    "phase",
    "score",
    "lines",
    "level",
    "combo",
    "b2b",
    "piecesPlaced",
    "activeType",
    "hold",
    "holdUsed",
    "next",
)


#: 内核字段名（snake_case）→ 契约映射表里的写法（camelCase），反之亦然。
#:
#: 契约表的 `kernel_field` 用的是**快照那侧的命名**（`ghostDelta` / `holdUsed`），
#: 内核的 dataclass 字段是 snake_case（`ghost_delta` / `hold_used`）——同一套
#: 命名体系的两种拼法，这里做一次机械转换，不另抄一份名单。
def _snake_to_camel(name: str) -> str:
    """内核字段名（snake_case）→ 契约映射表里的写法（camelCase）。"""
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _camel_to_snake(name: str) -> str:
    """契约映射表里的写法（camelCase）→ 内核字段名（snake_case）。

    与 `_snake_to_camel` 是同一套命名体系的反向转换——**机械规则，不抄名单**。
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


#: 内核**单独持有**、不在 `KernelState` 里的状态字段（**从契约推导**）。
#:
#: **不手写名单**（批次 C 审查的发现 C19）：这里原来写死
#: `frozenset({"ruleId", "frame"})`——手写名单会和契约分叉（契约再点名一个状态外
#: 字段，这里不会跟着改，守卫就守不住了）。现在由契约说了算：映射表里那些
#: **不在 `KernelState` 的 dataclass 字段里**的根名，就是「状态外字段」。
#:
#: 推出来的结果是 `{ruleId, frame, rngInstance}`：`ruleId` = `self._rule_id`、
#: `frame` = `self._frame`（内核用**普通属性**持有的两条状态），`rngInstance` 是
#: **派生**那条（随机源实例本体，契约声明它不进快照、只带走可序列化状态）。
KERNEL_ATTRIBUTES_OUTSIDE_STATE: frozenset[str] = frozenset(
    {entry.kernel_field.split(".")[0] for entry in KERNEL_STATE_TO_SNAPSHOT}
    - {_snake_to_camel(field.name) for field in dataclasses.fields(KernelState)}
)


def test_event_registry_order_matches_the_requirement_table() -> None:
    """事件注册表的枚举顺序 = 需求 §8.1 的表格顺序。

    **顺序进基线**（契约清单第 12 项：字段级哈希）——重排会让基线全变，却不
    改变任何字段的语义：这种改动**必须**是显式的，不该悄悄发生。
    """
    assert tuple(EVENT_TYPES) == tuple(EXPECTED_EVENTS)


def test_command_registry_order_matches_the_requirement_table() -> None:
    """命令注册表的枚举顺序 = 需求 §8.2 的表格顺序（同上：顺序进基线）。"""
    assert COMMAND_TYPES == tuple(EXPECTED_COMMAND_ARGS)


def test_state_changed_payload_is_a_projection_of_the_snapshot() -> None:
    """`state:changed` 的每个字段都投影一个快照字段——**同名同类型**。

    宿主在帧末发这条事件（plan-M0.md §1.4.4.1 ③），负载整份都是内核状态。
    所以契约面必须保证"事件字段 ↔ 快照字段"的对应关系：快照字段改名或改型，
    这里当场变红——**否则宿主会照着旧契约发出一条与快照对不上的事件**。
    """
    spec = EVENT_TYPES["state:changed"]
    payload = {field.name: field.type for field in spec.payload}
    assert set(payload) == set(EXPECTED_STATE_CHANGED_PROJECTION), "字段集合对不上"
    for name, snapshot_path in EXPECTED_STATE_CHANGED_PROJECTION.items():
        if name == "activeType":
            # 类型投影：快照的 `active` 可空 → 事件侧给 `str | None`。
            assert payload[name] == "str | None", name
            assert ACTIVE_FIELDS["pieceType"].type == "str"
            continue
        field = SNAPSHOT_FIELDS[snapshot_path]
        assert payload[name] == field.type, f"{name} 与快照 {snapshot_path} 的类型不一致"


def test_state_changed_payload_field_order_is_locked() -> None:
    """`state:changed` 的负载字段**顺序**也锁死。

    `make_draft` 按**顺序**逐条校验负载字段（架构 §4.4：字段名与顺序照抄契约），
    顺序错了当场报错——但那是在**运行期**；这里把它提到**契约期**。
    """
    assert tuple(field.name for field in EVENT_TYPES["state:changed"].payload) == (
        EXPECTED_STATE_CHANGED_ORDER
    )


def test_state_changed_does_not_carry_the_whole_board() -> None:
    """需求 §8.1：`state:changed` 是**轻量摘要（不含整张棋盘）**。

    整张棋盘走快照（`state:changed` + 快照是渲染器的两条入口，需求 §7）——
    事件负载里再塞一份棋盘，事件流会被每帧拖大。

    **判据用共享常量**（批次 C 审查的发现 C20）：原来只认 `tuple[tuple[int` 这一
    种**字符串拼法**，棋盘若被声明成别的形状就绕过去了。`BOARD_CELLS` 是契约里
    棋盘格子数组的类型串、`BOARD_VIEW` 是它的只读视图——声明侧引用这两个常量，
    这里就拿它们当判据（架构 §4.4 末条：引用处一律用常量，不复制字面量）。
    """
    fields = EVENT_TYPES["state:changed"].payload
    names = {field.name for field in fields}
    assert "board" not in names, names
    for field in fields:
        assert field.type != BOARD_CELLS, f"{field.name} 带着整张棋盘"
        assert field.type != BOARD_VIEW, f"{field.name} 带着棋盘视图"


def test_float_is_forbidden_in_args_but_allowed_in_event_payloads() -> None:
    """禁浮点**只约束 `args`**（B-7 / ADR-025）——事件负载不受这条约束。

    `args` 参与重放（JSON 里 `1.0` 与 `1` 不可区分）；事件负载不参与，所以
    `metric:sampled` 的聚合值带小数、`rng:next` 的取值可能是浮点。**口径不同是
    对的**：这条断言守住"只约束 args"这个范围——哪天有人把浮点全局禁掉（连事件
    负载也不许带），这里会红。
    """
    assert "float" not in ARGS_VALUE
    assert "float" not in ARGS
    for name, spec in COMMANDS.items():
        for arg in spec.args:
            assert "float" not in arg.type, f"{name}.{arg.name}"
    with_float = {
        name
        for name, spec in EVENT_TYPES.items()
        if any("float" in field.type for field in spec.payload)
    }
    assert with_float == {
        "kernel:system:installed",
        "log:entry",
        "metric:sampled",
        "rng:next",
    }
    assert "float" in JSON_VALUE, "事件负载的取值域里应当有浮点"


def test_every_kernel_state_field_is_mapped_to_the_snapshot() -> None:
    """内核**状态**的每个字段都在快照映射表里（快照 = 内核状态全集）。

    契约清单第 15 项原本是"人检"——这里落成机检：内核往 `KernelState` 里加一个
    字段而没同步加快照字段，这里当场变红（FR-10.2：存档恢复缺一块，回放就废了）。

    **「状态」在本仓库指什么**（批次 C 审查的发现 C19）：内核的**游戏状态**住在
    `KernelState` 这个 dataclass 里；契约另外点名了几个**普通属性**
    （`ruleId` = `self._rule_id`、`frame` = `self._frame`），由
    `test_every_contract_declared_state_field_exists_in_the_kernel` 单独守。
    其余普通属性（`_seed` / `_step_ms` / `_mode` / `_piece_types` / `_rules` …）是
    **配置**——整局不变，存档也不需要它们。

    **内核文件里多出一个字段**由冻结区哈希基线兜着（S17 生成）：内核是冻结区，
    内容一变就红——那是"多一个字段"唯一真正响得起来的机制。
    """
    mapped = {entry.kernel_field.split(".")[0] for entry in KERNEL_STATE_TO_SNAPSHOT}
    for field in dataclasses.fields(KernelState):
        camel = _snake_to_camel(field.name)
        assert camel in mapped, f"内核状态字段 {field.name}（{camel}）没进快照映射表"


def test_every_contract_declared_state_field_exists_in_the_kernel() -> None:
    """契约点名的每个状态字段，在内核里**真的存在**——**普通属性也算**。

    原来的守卫只遍历 `dataclasses.fields(KernelState)`，**普通属性没覆盖**：
    `ruleId` 与 `frame` 恰恰是内核用普通属性（`self._rule_id` / `self._frame`）
    持有的，两条都在契约的映射表里，却没人查它们在内核侧是不是真的存在。

    **解析规则是机械的**（不手写名单）：契约侧的 camelCase 名 → snake_case →
    内核的 `_` 私有属性。`derived` 的那几条（结构指纹 / 扩展摘要 / 随机源状态）是
    **算出来的**，不指向某个字段，跳过。
    """
    kernel = Kernel(seed=0, piece_types=PIECE_TYPES)
    plain = set(vars(kernel))
    dataclass_names = {_snake_to_camel(field.name) for field in dataclasses.fields(KernelState)}

    for entry in KERNEL_STATE_TO_SNAPSHOT:
        if entry.derived:
            continue
        root = entry.kernel_field.split(".")[0]
        if root in dataclass_names:
            continue
        attribute = f"_{_camel_to_snake(root)}"
        assert attribute in plain, f"契约点名的状态字段 {root} 在内核里没有对应属性 {attribute}"


def test_every_kernel_attribute_that_reaches_the_snapshot_is_contract_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """凡是**进了快照**的内核属性，都必须是契约声明过的。

    **哪些属性进了快照**：用 `__getattribute__` 探针记录 `snapshot()` 实际读了哪些
    普通属性——**机器推导**，不手写名单（批次 C 审查的发现 C19）。

    判据：读到的每个属性都要能对上契约——要么是 `KERNEL_STATE_TO_SNAPSHOT` 点名的
    状态字段，要么是已声明结构（`board` / `active`）的成员，要么就是装
    `KernelState` 的那个属性。**对不上，就是快照带了契约没声明的东西**。
    """
    kernel = Kernel(seed=0, piece_types=PIECE_TYPES)
    reads: set[str] = set()
    original = Kernel.__getattribute__

    def spy(self: Kernel, name: str) -> object:
        reads.add(name)
        return original(self, name)

    monkeypatch.setattr(Kernel, "__getattribute__", spy)
    kernel.snapshot()
    monkeypatch.undo()

    attributes = vars(kernel)
    plain_reads = {name for name in reads if name in attributes}
    assert plain_reads, "探针一个普通属性都没读到——这条守卫等于没跑"

    state_holder = next(
        name for name, value in attributes.items() if isinstance(value, KernelState)
    )
    declared_roots = {entry.kernel_field.split(".")[0] for entry in KERNEL_STATE_TO_SNAPSHOT}
    struct_members = set(BOARD_FIELDS) | set(ACTIVE_FIELDS)

    for name in sorted(plain_reads):
        if name == state_holder:
            continue
        camel = _snake_to_camel(name.lstrip("_"))
        assert camel in declared_roots or camel in struct_members, (
            f"内核属性 {name} 进了快照，但契约没声明它（快照带了契约之外的东西）"
        )


def test_every_mapped_kernel_field_exists_in_the_kernel() -> None:
    """映射表里的每个内核字段**都真实存在**——不许指着一个不存在的字段。

    反方向的那条纪律：映射表写错了名字（手滑），快照就会少一块或多一块，而
    **两边都"看起来有声明"**——不交叉核对就发现不了。
    """
    known = {_snake_to_camel(field.name) for field in dataclasses.fields(KernelState)}
    known |= KERNEL_ATTRIBUTES_OUTSIDE_STATE
    known |= set(RUNTIME_ONLY_STATE)
    for entry in KERNEL_STATE_TO_SNAPSHOT:
        root = entry.kernel_field.split(".")[0]
        assert root in known, f"映射表指着内核没有的字段：{entry.kernel_field}"


# ---------------------------------------------------------------------------
# S4：`RuleContext.stepMs` 的语义（ADR-067）——宿主逻辑步长 · 规则集只透传不用
# ---------------------------------------------------------------------------

#: 允许出现生命周期四个取值的**唯一**位置：`contracts/types.py` 的四个常量。
_PHASE_CONSTANT_NAMES = frozenset(
    {"PHASE_NOT_STARTED", "PHASE_RUNNING", "PHASE_PAUSED", "PHASE_GAME_OVER"}
)


def _ignored_string_constants(tree: ast.AST) -> set[int]:
    """**不算抄写**的字符串常量：docstring 与注解里的那些。

    散文（docstring）与类型串（注解）说的是"这几个值叫什么"，不是"运行时
    用哪个"——它们不是取值表。机器检查只看**代码里真正落下的字面量**。
    """
    ignored: set[int] = set()

    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ignored.add(id(first.value))

    annotations: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        elif isinstance(node, ast.arg) and node.annotation is not None:
            annotations.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            annotations.append(node.returns)
    for annotation in annotations:
        for sub in ast.walk(annotation):
            if isinstance(sub, ast.Constant):
                ignored.add(id(sub))

    return ignored


def _phase_literal_allowances(tree: ast.AST, path: Path) -> set[int]:
    """`contracts/types.py` 里**允许**出现这四个值的位置：四个常量与 `Phase` 别名。

    唯一来源内部也不许乱抄——别名与常量之外再写一遍，就是第二份取值表。
    """
    if path.parent.name != "contracts" or path.name != "types.py":
        return set()
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.TypeAlias):
            # `type Phase = Literal[...]`——别名与常量是同一份词汇。
            if isinstance(node.name, ast.Name) and node.name.id == "Phase":
                for sub in ast.walk(node.value):
                    if isinstance(sub, ast.Constant):
                        allowed.add(id(sub))
            continue
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if not any(
            isinstance(target, ast.Name) and target.id in _PHASE_CONSTANT_NAMES
            for target in targets
        ):
            continue
        for sub in ast.walk(value):
            if isinstance(sub, ast.Constant):
                allowed.add(id(sub))
    return allowed


def test_step_ms_is_the_step_the_host_gave_the_kernel() -> None:
    """宿主给 16，规则集在 `ctx` 里看到的就是 16（ADR-067 的「谁算 / 给谁用」）。

    **走宿主装配层那条真链路**：宿主给步长 → 时钟源带着它 → 内核把它递进
    `ctx`。绕开宿主直接造内核，只验了「内核会搬运」，没验「谁算」那一半。
    """
    seen: list[int] = []

    def spy_interval(ctx: RuleContext) -> int:
        seen.append(ctx.stepMs)
        return STANDARD_RULESET.gravity_interval(ctx)

    assembly = assemble(
        seed=0,
        step_ms=16,
        rule_set=dataclasses.replace(STANDARD_RULESET, gravity_interval=spy_interval),
    )
    # 宿主这一侧：时钟源带着这个步长（架构 §5.3）。
    assert assembly.clock.step_ms() == 16

    kernel = assembly.kernel
    kernel.execute(Command("start", source="test"))
    kernel.execute(Command("tick", source="test"))
    for _ in kernel.advance_frame():
        pass

    assert seen, "规则集一次都没被调用——这条守卫等于没跑"
    assert set(seen) == {16}, f"规则集看到的步长不是宿主给的 16：{seen}"


def test_the_rule_sets_never_read_the_step_ms() -> None:
    """规则集**只透传不用**——`rules/` 里不得读 `ctx.stepMs`（ADR-067）。

    「透传不用」是这条口径的一部分：标准玩法的重力间隔用逻辑帧数
    （`gravity_interval(ctx)` 返回帧数），不看毫秒。将来若有变体要按毫秒
    调手感，那是变体自己的事——但**仓里的规则集**不许悄悄用。
    """
    rules_dir = Path(rules_pkg.__file__).parent
    offenders: list[str] = []
    for path in sorted(rules_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "stepMs":
                offenders.append(f"{path.name}:{node.lineno}（属性访问）")
            elif isinstance(node, ast.Constant) and node.value == "stepMs":
                offenders.append(f"{path.name}:{node.lineno}（字符串）")
    assert not offenders, f"规则集读了 ctx.stepMs（透传不用）：{offenders}"


def test_the_phase_values_are_written_down_in_one_place_only() -> None:
    """生命周期四个取值**只有一处字面量表**：`contracts/types.py`（ADR-067 连带）。

    契约层与内核层都**不得再抄一遍**这四个值的字面量——抄第二份，两处分叉
    时无人报警（取值域收口把词汇表钉在契约层，正是为了这个）。

    **docstring 与注解不算**：散文与类型串说的是"这几个值叫什么"，不是取值
    表本身。`contracts/types.py` 内部也只允许四个常量与 `Phase` 别名。
    """
    src_root = Path(contracts_pkg.__file__).parent.parent
    offenders: list[str] = []
    for folder in ("contracts", "kernel"):
        for path in sorted((src_root / folder).glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            ignored = _ignored_string_constants(tree) | _phase_literal_allowances(tree, path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if node.value not in PHASE_VALUES or id(node) in ignored:
                    continue
                offenders.append(f"{folder}/{path.name}:{node.lineno} {node.value!r}")
    assert not offenders, f"生命周期取值被抄了第二份：{offenders}"


# ---------------------------------------------------------------------------
# M3-S1：系统声明面（FR-9.1 / FR-9.2 · spec-M3 §6.4）
#
# 下面这张表**独立转录**自 `docs/spec-M3.md` §6.4 的系统声明面——键 = 系统名，
# 值 = (版本, 订阅集, 命令集, 产出者, 是否需要 sink)。转录的是「规格写了什么」，
# 不是「实现写了什么」——两者相等才有意义。
# ---------------------------------------------------------------------------

#: 已声明系统的规格转录（系统名 → 规格）。
EXPECTED_SYSTEM_MANIFESTS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], str, bool]] = {
    # M2 的三个既有系统（本批次补上声明——**声明是新增文件，既有实现零改动**）
    "log": ("1", (GLOBAL_SUBSCRIPTION,), (), "log", True),
    "metrics": ("1", (GLOBAL_SUBSCRIPTION,), (), "metrics", True),
    "replay": (
        "1",
        ("command:accepted", "clock:tick", "game:over", "kernel:ready"),
        (),
        "systems/replay/",
        True,
    ),
    # M3 的两个示范系统（**只凭新增文件就接上了** —— 声明与工厂同源）
    "achievements": ("1", (GLOBAL_SUBSCRIPTION,), (), "achievements", False),
    "rotations": ("1", ("piece:rotated",), (), "rotations", True),
}


def test_switch_rule_set_is_not_lifecycle_gated() -> None:
    """`switchRuleSet` **不受统一生命周期门禁约束**——它有自己的门禁。

    通用门禁是「phase ≠ running 一律拒」，而换规则集**恰恰要在非 running 态
    被接受**（待开始 / 已结束）——写成 `lifecycle_gated=True` 就会在
    `not_started` 态被拒，那条命令就没用了。
    """
    spec: CommandSpec = COMMANDS["switchRuleSet"]
    assert spec.lifecycle_gated is False
    assert not set(spec.reject_reasons) & set(LIFECYCLE_REJECT_REASONS)
    assert set(spec.reject_reasons) == {
        "invalid_argument",
        "illegal_transition",
        "unknown_rule_set",
    }


def test_switch_rule_set_takes_one_str_arg() -> None:
    """`switchRuleSet` 只有一个参数 `rule: str`（禁浮点 · 只含 JSON 标量）。"""
    args = COMMANDS["switchRuleSet"].args
    assert [(arg.name, arg.type, arg.nullable) for arg in args] == [("rule", "str", False)]


def test_rule_set_changed_event_payload() -> None:
    """`kernel:rule-set-changed` 负载 = 新旧两个规则集标识（**产出者是内核**）。"""
    spec = EVENT_TYPES["kernel:rule-set-changed"]
    assert (spec.namespace, spec.producer) == ("kernel", "kernel")
    assert {field.name: field.type for field in spec.payload} == {
        "fromRule": "str",
        "toRule": "str",
    }


def test_discovered_manifests_match_specification() -> None:
    """发现到的系统声明**逐条等于规格转录**（FR-9.2 的依赖图靠它生成）。"""
    found = discover_manifests()
    actual = {
        name: (m.version, m.subscribes, m.commands, m.producer, m.needsSink)
        for name, m in found.items()
    }
    assert actual == EXPECTED_SYSTEM_MANIFESTS


def test_manifest_subscribes_and_commands_are_registered() -> None:
    """**自证**：声明里的事件 / 命令类型必须已登记（跨文件自证）。

    写错一个名字（`"piece:rotatd"`）当场被抓住——不然它会挂一个永远收不到
    事件的订阅，而系统看上去「装上了」。
    """
    for name, manifest in discover_manifests().items():
        problems = check_manifest_types(
            manifest,
            known_events=EVENT_TYPES.keys(),
            known_commands=COMMANDS.keys(),
        )
        assert not problems, f"{name}: {problems}"


def test_manifest_producers_are_known_producers() -> None:
    """声明里的产出者必须是**已登记的产出者代号**，或**这个系统自己的名字**。

    （**产出者 ≠ 命名空间**。）

    **为什么允许系统名**：一个**不发事件**的系统（`rotations`——它的产出是
    计数文件，不是事件）没有事件产出者可填；需求 §8.2 的元数据 `producer`
    本来就允许「系统名」——**代号取系统名**。
    """
    for name, manifest in discover_manifests().items():
        assert manifest.producer in PRODUCER_LABELS or manifest.producer == name, name


def test_manifest_canonical_text_is_stable_and_sensitive() -> None:
    """规范化串：同样的声明 → 同样的串；**改了订阅集 → 串就变**（基线的牙齿）。"""
    manifest = discover_manifests()["log"]
    assert manifest_canonical_text(manifest) == manifest_canonical_text(manifest)
    changed = SystemManifest(
        name=manifest.name,
        version=manifest.version,
        subscribes=("piece:locked",),
        commands=manifest.commands,
        producer=manifest.producer,
        needsSink=manifest.needsSink,
    )
    assert manifest_canonical_text(changed) != manifest_canonical_text(manifest)


def test_check_manifest_types_catches_unknown_types() -> None:
    """自证本身要**抓得住**：未登记的事件 / 命令类型必须被报出来。"""
    manifest = SystemManifest(
        name="probe",
        version="1",
        subscribes=("piece:rotatd",),
        commands=("noSuchCommand",),
        producer="log",
        needsSink=False,
    )
    problems = check_manifest_types(
        manifest,
        known_events=EVENT_TYPES.keys(),
        known_commands=COMMANDS.keys(),
    )
    assert any("piece:rotatd" in problem for problem in problems)
    assert any("noSuchCommand" in problem for problem in problems)


def test_system_manifest_rejects_invalid_declarations() -> None:
    """非法声明**当场报错**：空名字 · 空订阅集 · 重复项。"""
    with pytest.raises(ValueError):
        SystemManifest(
            name="", version="1", subscribes=("*",), commands=(), producer="log", needsSink=False
        )
    with pytest.raises(ValueError):
        SystemManifest(
            name="x", version="1", subscribes=(), commands=(), producer="log", needsSink=False
        )
    with pytest.raises(ValueError):
        SystemManifest(
            name="x",
            version="1",
            subscribes=("a", "a"),
            commands=(),
            producer="log",
            needsSink=False,
        )


def test_registered_system_manifest_container() -> None:
    """已登记系统清单容器：登记 → 可见 → 注销 → 消失；**重名即报错**。"""
    manifest = SystemManifest(
        name="probe-system",
        version="1",
        subscribes=("*",),
        commands=(),
        producer="log",
        needsSink=False,
    )
    try:
        register_system_manifest(manifest)
        assert SYSTEM_MANIFESTS["probe-system"] == manifest
        with pytest.raises(ValueError):
            register_system_manifest(manifest)
    finally:
        unregister_system_manifest("probe-system")
    assert "probe-system" not in SYSTEM_MANIFESTS
