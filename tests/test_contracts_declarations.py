"""契约声明测试（S2 事件 / S3 命令 · S15 补全）。

当前覆盖：事件注册表（44 条）、命名空间、产出者分布、信封与元数据声明、
命令规格（16 条）与拒绝原因枚举（12 值）。
S4–S5 落地后，快照 / 规则签名的对应断言补进本文件。
"""

from __future__ import annotations

import pytest

from tetris_kernel.contracts import (
    ARGS,
    ARGS_VALUE,
    COMMAND_TYPE_FIELDS,
    COMMAND_TYPES,
    COMMANDS,
    CONTRACT_VERSION,
    ENVELOPE_FIELDS,
    EVENT_TYPES,
    LIFECYCLE_REJECT_REASONS,
    METADATA_KEYS,
    PRODUCER_LABELS,
    REJECT_REASON_CATEGORIES,
    REJECT_REASON_CATEGORY_LABELS,
    REJECT_REASONS,
    CommandSpec,
    EventSpec,
    is_lifecycle_gated,
    namespace_of,
)

# ---------------------------------------------------------------------------
# 需求 §8.1 的事件目录，独立转录一份做交叉核对。
# 键 = 事件类型名，值 = (命名空间, 产出者代号)。
# 转录自 docs/需求与边界.md §8.1，与 contracts/events.py 的注册表互为对照。
# ---------------------------------------------------------------------------

EXPECTED_EVENTS: dict[str, tuple[str, str]] = {
    # kernel（5 条）
    "kernel:ready": ("kernel", "kernel"),
    "kernel:system:installed": ("kernel", "host"),
    "kernel:system:removed": ("kernel", "host"),
    "kernel:error": ("kernel", "bus"),
    "kernel:quota-exceeded": ("kernel", "kernel"),
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
    "state:changed": ("state", "kernel"),
    # render（1 条）
    "render:frame": ("render", "renderer"),
    # log（1 条）
    "log:entry": ("log", "log"),
    # metric（1 条）
    "metric:sampled": ("metric", "metrics"),
    # achievement（1 条）
    "achievement:unlocked": ("achievement", "achievements"),
}

#: 产出者分布（需求 §8.1 / 契约清单第 8 项）。
EXPECTED_PRODUCER_COUNTS: dict[str, int] = {
    "kernel": 35,
    "bus": 1,
    "host": 2,
    "input-adapter": 2,
    "renderer": 1,
    "log": 1,
    "metrics": 1,
    "achievements": 1,
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
    "command:received": {
        "commandType": "str",
        "source": "str",
        "args": (
            "tuple[tuple[str, int | str | bool | None | tuple[int, ...] "
            "| tuple[tuple[int, int, int], ...]], ...]"
        ),
    },
    "command:accepted": {
        "commandType": "str",
        "commandSeq": "int",
        "args": (
            "tuple[tuple[str, int | str | bool | None | tuple[int, ...] "
            "| tuple[tuple[int, int, int], ...]], ...]"
        ),
    },
    "command:rejected": {
        "commandType": "str",
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
        "phase": "str",
    },
    "game:resumed": {
        "phase": "str",
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
        "commandType": "str",
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
        "phase": "str",
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
}


def test_event_registry_size() -> None:
    """事件总数 = 44（需求 §8.1）。"""
    assert len(EVENT_TYPES) == 44


def test_event_registry_matches_requirement_table() -> None:
    """注册表与需求 §8.1 的事件目录逐条相等：名字、命名空间、产出者。"""
    assert set(EVENT_TYPES) == set(EXPECTED_EVENTS)
    for name, (namespace, producer) in EXPECTED_EVENTS.items():
        spec = EVENT_TYPES[name]
        assert (spec.namespace, spec.producer) == (namespace, producer), name
        assert spec.namespace == namespace_of(name), name


def test_producer_distribution() -> None:
    """产出者分布：内核 35 · 总线 1 · host 2 · 输入适配器 2 · 渲染器 1 · 日志/指标/成就各 1。"""
    counts: dict[str, int] = {}
    for spec in EVENT_TYPES.values():
        counts[spec.producer] = counts.get(spec.producer, 0) + 1
    assert counts == EXPECTED_PRODUCER_COUNTS


def test_producer_labels_cover_all_producers() -> None:
    """每个产出者代号都有对应名称（`host` 在需求 §8.1 里就是 `host`），反之亦然。"""
    used = {spec.producer for spec in EVENT_TYPES.values()}
    assert used == set(PRODUCER_LABELS)


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

#: 16 条命令的参数规格。键 = 命令名，值 = ((参数名, 类型, 是否可空), ...)。
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
}

#: 拒绝原因枚举（12 值），转录自需求 §8.2 的分类表。
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
    "not_authorized",
    "write_condition_failed",
)

#: 拒绝原因的分类（需求 §8.2 的「类」列）。键 = 分类代号。
EXPECTED_REJECT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "lifecycle": ("not_started", "paused", "game_over"),
    "quota": ("quota_exceeded",),
    "parameter": ("invalid_argument", "level_out_of_range", "out_of_bounds"),
    "state": ("illegal_transition", "no_active_piece", "hold_already_used"),
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
    """命令总数 = 16（需求 §8.2）。"""
    assert len(COMMANDS) == 16


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


def test_only_three_commands_take_args() -> None:
    """三条**有参**（`setLevel` / `injectGarbage` / `writeCells`），其余 13 条无参。"""
    with_args = {name for name, spec in COMMANDS.items() if spec.args}
    assert with_args == {"setLevel", "injectGarbage", "writeCells"}
    assert len(COMMANDS) - len(with_args) == 13


def test_reject_reason_enum() -> None:
    """拒绝原因集合 = 12 个值，与需求 §8.2 逐条相等（契约清单第 6 项）。"""
    assert len(REJECT_REASONS) == 12
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

    事件负载的类型语言里没有字面量枚举形态，所以取值域（= `COMMAND_TYPES`）
    只能这样落地：声明 + 断言「凡是承载命令名的字段都在表里」。
    """
    for event_type, field_name in COMMAND_TYPE_FIELDS:
        spec = EVENT_TYPES[event_type]
        field = next(f for f in spec.payload if f.name == field_name)
        assert field.type == "str", f"{event_type}.{field_name}"
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
    """契约 v1 的 16 条命令**没有可空参数**——`holes` 用空元组表示"无空洞"。"""
    for name, spec in COMMANDS.items():
        for arg in spec.args:
            assert not arg.nullable, f"{name}.{arg.name}"
