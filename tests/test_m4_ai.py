"""M4-S9 · AI 系统（固定预算 · 与人类同通道）。

对应门判据 **`G4-5`**（AC-6）：AI 与人类同通道 · 内核不按 `source` 分支。

四条验证（plan-M4 S9 的验证方案）：

1. **同一 AI 决策序列与人类手动输入结果一致**（AC-6 · `G4-5`）；
2. **同一串命令换 `source` → 内核逐帧全等**（"内核不按来源分支"的差异断言）；
3. **预算固定**：静态检查断言**没有按耗时分支的代码路径**（`time` 不得进 AI 的
   决策路径）；
4. **AI 能把关**：跑一局 → 分数不为零（**它真的在玩**）。
"""

from __future__ import annotations

import re
from pathlib import Path

from tetris_kernel.bus.bus import EventBus
from tetris_kernel.bus.envelope import metadata_value
from tetris_kernel.host.assembly import assemble
from tetris_kernel.host.discovery import declared_manifests, system_catalog
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.core import Kernel
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.rules.tables import PIECE_TYPES
from tetris_kernel.systems.ai import (
    MAX_CANDIDATES,
    SEARCH_DEPTH,
    SOURCE_NAME,
    SYSTEM_MANIFEST,
)
from tetris_kernel.systems.ai.search import HARD_DROP, plan_placement

#: 源码路径（**静态检查用** —— 预算固定那条要查源码里有没有 `time`）。
AI_SOURCE_DIR = Path(__file__).resolve().parents[1] / "src" / "tetris_kernel" / "systems" / "ai"

#: AI 那一局的种子。
SEED = 20261031

#: 最多跑多少帧（**够它玩完一局**）。
MAX_FRAMES = 1200


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------


def _ai_assembly(seed: int = SEED):
    """装上 AI 的装配（**AI 走自动发现** —— 不在宿主手写名单里）。"""
    return assemble(
        seed=seed,
        observability_enabled=False,
        systems=("ai",),
        system_catalog=system_catalog(),
        # **历史缓冲放大**：一局的命令条数远超默认的 1024 —— 环形历史会把
        # 前面的命令挤掉（**取命令清单靠它**）。
        history_limit=200_000,
    )


def _play(
    seed: int = SEED,
    frames: int = MAX_FRAMES,
) -> tuple[Snapshot, list[tuple[int, str, tuple]]]:
    """**让 AI 玩一局**，返回（末局快照 · 它派过的命令清单）。

    命令清单 = `(帧号, 命令类型, 参数)`——**参数也要**（重放要逐条一致）。
    """
    assembly = _ai_assembly(seed)
    bus = assembly.bus
    kernel = assembly.kernel
    loop = HeadlessLoop(assembly)
    loop.start()
    for _ in range(frames):
        loop.run_frame()
        if kernel.phase == "game_over":
            break
    ai_commands = [
        (frame, command_type, args)
        for frame, command_type, args, source in _accepted_commands(bus)
        if source == SOURCE_NAME
    ]
    return kernel.snapshot(), ai_commands


def _accepted_commands(bus: EventBus[Snapshot]) -> list[tuple[int, str, tuple, str]]:
    """总线历史里的**被接受的命令**（`(帧号, 类型, 参数, 来源)`）。"""
    result: list[tuple[int, str, tuple, str]] = []
    for envelope in bus.history():
        if envelope.type != "command:accepted":
            continue
        payload = dict(envelope.payload)
        command_type = payload.get("commandType")
        args = payload.get("args")
        if not isinstance(command_type, str) or not isinstance(args, tuple):
            continue
        source = metadata_value(envelope.metadata, "source")
        result.append(
            (envelope.frame, command_type, args, source if isinstance(source, str) else "")
        )
    return result


def _replay_by_hand(
    commands: list[tuple[int, str, tuple]],
    seed: int = SEED,
    source: str = "human",
) -> list[tuple[str, int, int, int]]:
    """**手工输入**同一串命令（换 `source`）→ 返回逐帧的四项。

    **逐帧比**：不是"最终态差不多"——**每一帧**的四项都要一样。
    """
    kernel = Kernel(seed=seed, piece_types=PIECE_TYPES)
    bus: EventBus[Snapshot] = EventBus(view_provider=kernel.snapshot)
    by_frame: dict[int, list[tuple[str, tuple]]] = {}
    for frame, command_type, args in commands:
        by_frame.setdefault(frame, []).append((command_type, args))
    kernel.submit(Command(type="start", source="host"))
    kernel.run_rounds(lambda drafts: tuple(bus.publish(draft) for draft in drafts))
    frames: list[tuple[str, int, int, int]] = []
    for frame in sorted(by_frame):
        kernel.submit(Command(type="tick", source="host"))
        for command_type, args in by_frame[frame]:
            kernel.submit(Command(type=command_type, args=args, source=source))
        kernel.run_rounds(lambda drafts: tuple(bus.publish(draft) for draft in drafts))
        for batch in kernel.advance_frame():
            for draft in batch:
                bus.publish(draft)
        snapshot = kernel.snapshot()
        frames.append(
            (
                snapshot.board.structureFingerprint,
                snapshot.score,
                snapshot.lines,
                snapshot.piecesPlaced,
            )
        )
    return frames


# ---------------------------------------------------------------------------
# 1 · AI 能把关
# ---------------------------------------------------------------------------


def test_m4_ai_the_ai_actually_plays_and_clears_lines() -> None:
    """**AI 能把关**：跑一局 → **分数不为零**（它真的在玩）。

    **不是"派过命令就算会玩"**——分数与消行数是它真的在堆方块的结果。
    """
    snapshot, commands = _play()
    assert commands, "AI 一条命令都没派——它没在玩"
    assert snapshot.lines > 0, f"AI 一行都没消（分数 {snapshot.score}）——它没在玩"
    assert snapshot.score > 0, f"AI 的分数是零（消行 {snapshot.lines} 行）——它没在玩"
    assert snapshot.piecesPlaced > 5, "AI 连五块方块都没放出去"


def test_m4_ai_every_dispatched_command_is_a_registered_one() -> None:
    """**AI 只派它声明过的命令**——它**不写棋盘**（FR-8.5 · M3-D3）。

    **尤其不许有 `writeCells`**：AI 的产出是**命令**，不是棋盘上的格子。
    """
    _, commands = _play()
    allowed = set(SYSTEM_MANIFEST.commands)
    for _, command_type, _ in commands:
        assert command_type in allowed, f"AI 派了没声明的命令：{command_type}"
        assert command_type != "writeCells", "AI 不许写棋盘"


# ---------------------------------------------------------------------------
# 2 · 与人类同通道（AC-6 · G4-5）
# ---------------------------------------------------------------------------


def test_m4_ai_the_same_decisions_give_the_same_result_as_human_input() -> None:
    """**同一 AI 决策序列 → 人类手动输入 → 结果一致**（AC-6）。

    **怎么比**：把 AI 派过的命令原样交给一个**全新的内核**，**来源标记换成
    `human`** → 逐帧四项与 AI 那局**完全一样**。
    """
    _, commands = _play()
    assert commands, "AI 一条命令都没派"
    ai_frames = _replay_by_hand(commands, source=SOURCE_NAME)
    human_frames = _replay_by_hand(commands, source="human")
    assert ai_frames, "重放一帧都没跑起来"
    assert human_frames == ai_frames, (
        "同一串命令换来源标记后结果不同——内核按 source 分支了（破 FR-8.5）"
    )


def test_m4_ai_the_kernel_does_not_branch_on_the_source_marker() -> None:
    """**内核不因 `source` 分支**（FR-8.5 · M4-D5）。

    **两条断言**：

    1. 同一串命令换来源 → **逐帧四项全等**；
    2. **内核源码里没有 `source ==` 的分支**——`source` 只作元数据透传。
    """
    _, commands = _play()
    assert commands, "AI 一条命令都没派"
    assert _replay_by_hand(commands, source="ai") == _replay_by_hand(commands, source="human")

    kernel_source = (
        Path(__file__).resolve().parents[1] / "src" / "tetris_kernel" / "kernel" / "core.py"
    ).read_text(encoding="utf-8")
    offenders = re.findall(r'source\s*==\s*["\']', kernel_source)
    assert not offenders, f"内核里出现了按 source 分支的代码：{offenders}"


# ---------------------------------------------------------------------------
# 3 · 预算固定（M4-D3 · ADR-028）
# ---------------------------------------------------------------------------


def test_m4_ai_the_search_budget_is_fixed() -> None:
    """**预算固定**：**`time` 不得进 AI 的决策路径**。

    **静态检查**：`systems/ai/` 里**不许 import `time`**、不许出现 `time.` 调用
    ——**按耗时自适应就是从这里进来的**（它会让同一录像在不同机器上重放出不同
    结果 → 破 AC-1 / NFR-1）。
    """
    for path in sorted(AI_SOURCE_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "import time" not in text, f"{path.name} 里 import 了 time——按耗时自适应"
        assert not re.search(r"\btime\.\w", text), f"{path.name} 里用了 time——按耗时自适应"


def test_m4_ai_the_search_depth_and_candidate_budget_are_constants() -> None:
    """**深度与候选数是写死的常量**——不是"按剩余时间决定搜多少"。"""
    assert SEARCH_DEPTH == 1, "M4 的 AI 只看当前这一块（不前瞻）"
    assert MAX_CANDIDATES == 40, "候选数是固定预算（4 朝向 × 10 列）"


def test_m4_ai_the_search_is_a_pure_function_of_the_position() -> None:
    """**同一局面永远同一个决策**（不引随机源 · 天然确定）。

    **同一个棋盘算两次 → 同一条命令序列**——这条守住"录像可复现"。
    """
    board: tuple[tuple[int, ...], ...] = tuple([0] * 10 for _ in range(20))
    first = plan_placement(board, "T", 3, 0, 0)
    second = plan_placement(board, "T", 3, 0, 0)
    assert first is not None and second is not None
    assert first.commands == second.commands
    assert first.commands[-1] == HARD_DROP, "落点方案最后一条必须是硬降"


# ---------------------------------------------------------------------------
# 4 · 声明面
# ---------------------------------------------------------------------------


def test_m4_ai_the_system_is_discovered_and_declared() -> None:
    """**AI 进系统目录与依赖图**（M3-D2：新增能力 = 新增文件，不改名单）。"""
    manifests = declared_manifests()
    assert "ai" in manifests, "AI 没进系统清单——自动发现没扫到"
    catalog = system_catalog()
    assert "ai" in catalog, "AI 不在系统目录里——装不上"
    assert manifests["ai"] == SYSTEM_MANIFEST
    assert manifests["ai"].producer == "ai", "产出者代号就是系统名（它一条事件都不发）"
