"""契约声明测试（S2 骨架 · S15 补全）。

当前覆盖：事件注册表（44 条）、命名空间、产出者分布、信封与元数据声明。
S3–S5 落地后，命令 / 快照 / 规则签名的对应断言补进本文件。
"""

from __future__ import annotations

import pytest

from tetris_kernel.contracts import (
    ENVELOPE_FIELDS,
    EVENT_TYPES,
    METADATA_KEYS,
    PRODUCER_LABELS,
    EventSpec,
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
    """每个产出者代号都有中文名，反之亦然。"""
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
    """信封五字段：序号 / 帧号 / 类型 / 负载 / 元数据（需求 §8 / 架构 §5.1）。"""
    assert [field.name for field in ENVELOPE_FIELDS] == [
        "seq",
        "frame",
        "type",
        "payload",
        "metadata",
    ]


def test_metadata_keys() -> None:
    """元数据三键：source / producer / cause（ADR-062）。"""
    fields = {field.name: field.type for field in METADATA_KEYS}
    assert fields == {
        "source": "str",
        "producer": "str",
        "cause": "tuple[int, int] | None",
    }
