"""自动发现的系统的落盘出口测试（M3-S9 · ADR-064 / ADR-085 · M3-D5）。

覆盖批次 C 记下的那条缺口：**自动发现的系统拿不到落盘出口**。

1. **出口到位**：宿主给注册表一个「按系统名问」的工厂 → 声明 `needsSink=True`
   的系统（旋转统计）**拿到出口** → 计数**落盘**；
2. **每个系统一个文件**（`system-<名字>.jsonl`）——共用一个文件就是 ADR-085
   修过的坑；
3. **追加语义**：跑两轮 → 行数**累积**（不是覆盖）；
4. **不要出口的系统不接**：`needsSink=False` 的成就系统**拿不到出口**；
5. **不给工厂 = 不落盘**：宿主不给出口 → 系统只计数不落盘（**默认行为**）。
"""

from __future__ import annotations

import json
from pathlib import Path

from tetris_kernel.host.assembly import assemble, system_sink_factory
from tetris_kernel.host.discovery import system_catalog
from tetris_kernel.kernel.drafts import make_draft

#: 被统计的旋转事件（契约已登记）。
PIECE_ROTATED_EVENT = "piece:rotated"


def _rotated(frame: int) -> object:
    """造一条合法的 `piece:rotated` 草稿。"""
    return make_draft(
        PIECE_ROTATED_EVENT,
        (("fromRotation", 0), ("toRotation", 1), ("direction", "cw"), ("blocked", False)),
        frame,
    )


def _rotations_file(directory: Path) -> Path:
    """旋转统计的落盘文件。"""
    return directory / "system-rotations.jsonl"


def _lines(path: Path) -> list[dict[str, object]]:
    """读文件里的 JSON 行。"""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_the_discovered_system_writes_through_the_injected_sink(tmp_path: Path) -> None:
    """宿主给了出口 → 旋转统计**落盘**（它自己从不 `open()`）。"""
    assembly = assemble(
        systems=("rotations",),
        system_catalog=system_catalog(),
        system_sink=system_sink_factory(tmp_path),
    )

    assembly.bus.publish(_rotated(1))
    assembly.bus.publish(_rotated(2))

    path = _rotations_file(tmp_path)
    assert path.exists(), "旋转统计没有落盘"
    records = _lines(path)
    assert [record["rotations"] for record in records] == [1, 2]


def test_each_system_gets_its_own_file(tmp_path: Path) -> None:
    """**每个系统一个文件**——不是共用一个（ADR-085 的同款纪律）。"""
    factory = system_sink_factory(tmp_path)

    first = factory("rotations")
    second = factory("achievements")

    assert first is not second
    first(b'{"a":1}\n')
    second(b'{"b":2}\n')
    assert (tmp_path / "system-rotations.jsonl").read_text(encoding="utf-8") == '{"a":1}\n'
    assert (tmp_path / "system-achievements.jsonl").read_text(encoding="utf-8") == '{"b":2}\n'


def test_the_system_file_appends_instead_of_overwriting(tmp_path: Path) -> None:
    """**追加语义**：系统产出是一条一条往外冒的（不是"整份交出"）。"""
    assembly = assemble(
        systems=("rotations",),
        system_catalog=system_catalog(),
        system_sink=system_sink_factory(tmp_path),
    )

    assembly.bus.publish(_rotated(1))
    first = _lines(_rotations_file(tmp_path))
    assembly.bus.publish(_rotated(2))
    second = _lines(_rotations_file(tmp_path))

    assert len(second) == len(first) + 1, "系统落盘不是追加语义"


def test_a_system_that_needs_no_sink_gets_none(tmp_path: Path) -> None:
    """`needsSink=False` 的系统（成就）**不接出口**——出口给多了就是白给。"""
    assembly = assemble(
        systems=("achievements",),
        system_catalog=system_catalog(),
        system_sink=system_sink_factory(tmp_path),
    )

    assert assembly.registry is not None
    assert list(tmp_path.glob("*.jsonl")) == [], "不该落盘的系统落了盘"


def test_without_a_factory_the_system_only_counts(tmp_path: Path) -> None:
    """**宿主不给出口 = 不落盘**（默认行为）：系统只计数。"""
    assembly = assemble(
        systems=("rotations",),
        system_catalog=system_catalog(),
    )

    assembly.bus.publish(_rotated(1))
    assembly.bus.publish(_rotated(2))

    assert list(tmp_path.glob("*.jsonl")) == [], "没有出口却落了盘"
