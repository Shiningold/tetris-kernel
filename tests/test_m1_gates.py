"""M1 九道门判据（S13 · plan-M1.md §5.2）。

## 九道门与本文件的用例

| 门 | 判据 | 复跑命令 |
| --- | --- | --- |
| G1-1 | 完整一局玩到顶出 · 结算与手算一致 | `pytest -k m1_full_game_to_topout` |
| G1-4 | 完整计分表全对（**端到端那一半**） | `pytest -k m1_scoring_table` |
| G1-5 | 锁定延迟与手感机制 | `pytest -k m1_lock_delay` |
| G1-6 | 契约基线跨文件自证 | `pytest -k m1_contract_expected_list` |
| G1-7 | 取值域统一收口 | `pytest -k m1_value_domains` |
| G1-8 | 软降计分归规则集 | `pytest -k m1_soft_drop_score` |
| G1-9 | 确定性回归 · **逐帧**指纹 | `pytest -k m1_frame_determinism` |

G1-2（形状表 / 踢墙表）与 G1-3（T-Spin 五档）在 `tests/test_rules_m1.py`
（批次 B 已交），本文件**不重复**——重复的用例会让人误以为两边都守着一道门。

## 手算的边界（M0-1 在测试层的翻版）

plan §5.3 的 S13 反向验证有一条：「**把手算的期望值改成实现算出来的值**」。
所以这一份文件里，**期望值分三层**，每一层都不许由实现算出：

1. `EXPECTED_CLEARS`——**手抄**的消行序列（行数 · 分类键 · 消行前的连击数）；
2. `_hand_score()`——**本文件自己写的**计分表与公式（**不 import
   `rules.standard`**）；
3. `EXPECTED_SCORE` 等——**人工推导**的常数。

三层互相交叉核对：观测到的序列 == 手抄的序列；手抄序列算出的分 == 人工推导
的常数；常数 == 内核给的结算分。**任何一层被改成"实现算出来的"，另外两层
立刻对不上**。

## 参考策略的三条纪律（spec §7.8 · N1）

- **只看快照**：策略读到的全部状态都来自 `Snapshot`；
- **只派发无参命令**：返回命令**类型名**，由驱动器送进命令通道；
- **不许直接改棋盘**：**单一状态所有者**是内核（架构 §7.1）。

第三条由两条断言守着：快照是**冻结 dataclass**（策略在语言层面就改不动
它读到的东西），策略模块的源码里**搜不到**它对内核的写入通道
（`submit(` / `injectGarbage` / `writeCells` / `Kernel(`）。

**自给自足**：不依赖 `tests/conftest.py` 的 fixture。
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import locale
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from support.driver import PlayOutcome, RunTrace, play_frames, play_to_game_over
from support.policy import ReferencePolicy
from tetris_kernel.contracts import (
    AXIS_LITERAL,
    AXIS_VALUES,
    CLEAR_AXIS_LITERAL,
    CLEAR_AXIS_VALUES,
    COMMAND_TYPES,
    NAMED_STRUCTS,
    RuleContext,
)
from tetris_kernel.contracts.commands import COMMANDS
from tetris_kernel.contracts.rules import ClearGroup
from tetris_kernel.host.assembly import Assembly, assemble
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.core import LOCK_DELAY_FRAMES, LOCK_DELAY_MAX_RESETS, Kernel
from tetris_kernel.kernel.lifecycle import PHASE_GAME_OVER
from tetris_kernel.kernel.snapshot import Active, Board, Snapshot
from tetris_kernel.rules import RuleSet
from tetris_kernel.rules.standard import STANDARD_RULESET

#: 仓库根目录（`tests/` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 契约基线与**跨文件期望清单**（第二层防线 · M1-S5）。
CONTRACT_BASELINE = REPO_ROOT / "tests" / "baseline" / "contracts_baseline.json"
CONTRACT_EXPECTED = REPO_ROOT / "tests" / "baseline" / "contracts_expected.json"

#: `tools/` 下的脚本**自己** import 同目录的 `_toolkit`，所以要把 `tools/`
#: 挂到 `sys.path` 上（与 `tests/test_baselines.py` 同一手法）。
sys.path.insert(0, str(REPO_ROOT / "tools"))
_grammar = importlib.import_module("contract_grammar")

# ---------------------------------------------------------------------------
# 种子
# ---------------------------------------------------------------------------

#: 机械场景用的固定种子（与仓库其余测试一致）。
SEED = 20261024

#: **整局**门判据用的种子。
#:
#: **为什么是 4**：同一个策略在别的种子上也能顶出，但这一局的消行序列
#: **带两次连击**（连续两次放置都消行）——手算才不是"只有基础分"的那种
#: 一加一等于二。种子是门判据的一部分，不是随手挑的。
GAME_SEED = 4

# ---------------------------------------------------------------------------
# 手抄的期望值（G1-1 / G1-4）
# ---------------------------------------------------------------------------

#: **手抄**的消行序列：`(行数, 计分类别键, 消行前的连击数)`。
#:
#: **转录来源**：同种子同策略跑一局，把 `board:lines-cleared` 事件流里的
#: 消行数与类别键、以及**放置序列**里"这次消行之前连着消了几次"抄下来。
#:
#: **不是实现算出来的**——它是**观测事实**的抄本；算分的那一步在
#: `_hand_score()` 里，是本文件**自己写的公式**。
#:
#: **这一局的类别键只有 `single` 与 `double`**：参考策略不产生 T-Spin
#: （它只在横向移动之前旋转，硬降不是旋转），也不凑得出四消。
EXPECTED_CLEARS: tuple[tuple[int, str, int], ...] = (
    (1, "single", 0),
    (1, "single", 0),
    (1, "single", 1),
    (2, "double", 0),
    (2, "double", 0),
    (1, "single", 0),
    (1, "single", 0),
    (1, "single", 1),
    (2, "double", 0),
    (2, "double", 0),
)

#: **人工推导**的分数：6 次单消 + 4 次双消 + 2 次连击奖励。
#:
#: `6 × 100 + 4 × 300 = 1800`，两次连击各加 `50 × 1 = 50` → `1900`。
EXPECTED_SCORE = 1900

#: **人工推导**的总行数：`6 × 1 + 4 × 2 = 14`。
EXPECTED_LINES = 14

#: **人工推导**的等级：每消 10 行升 1 级 → `14 // 10 + 1 = 2`。
EXPECTED_LEVEL = 2

#: **人工推导**的落盘方块数（这一局共 75 块）。
EXPECTED_PIECES = 75

#: **手抄**的锁定延迟时长（逻辑帧）——spec §7.5（FR-4.3）。
#:
#: **为什么写成字面量而不是 import 内核的常量**：期望值从实现里导入的话，
#: 常量一改期望值跟着改——**测试照样绿**，守的就不是"锁定延迟是 30 帧"这件事
#: 了（这就是 M0-1 在测试层的翻版）。**期望值必须是手抄的数**。
EXPECTED_LOCK_DELAY_FRAMES = 30

#: **手抄**的锁定延迟重置上限（次）——spec §7.5。
EXPECTED_LOCK_DELAY_MAX_RESETS = 15

#: 手抄的计分表（**独立转录**：不 import `rules.standard.SCORE_TABLE`）。
#:
#: **转录源**：spec §7.4 的计分表（基础分四档 + T-Spin 三档 + 不消行两档）。
HAND_SCORE_TABLE: Mapping[str, int] = MappingProxyType(
    {
        "single": 100,
        "double": 300,
        "triple": 500,
        "tetris": 800,
        "tspin-none": 400,
        "tspin-mini": 100,
        "tspin-single": 800,
        "tspin-double": 1200,
        "tspin-triple": 1600,
    }
)

#: 手抄的连击奖励：每次连击加 `50 × 消行前的连击数`。
HAND_COMBO_BONUS = 50

#: 手抄的升级阈值：每消 10 行升 1 级。
HAND_LINES_PER_LEVEL = 10

#: 手抄的"难消"集合（四消与 T-Spin 消行）——**只有它们能接上 B2B 链**。
HAND_DIFFICULT_KEYS = frozenset({"tetris", "tspin-single", "tspin-double", "tspin-triple"})


def _hand_score(
    clears: tuple[tuple[int, str, int], ...],
) -> tuple[int, int, int]:
    """按**手抄的表**算这局该得多少分、多少行、几级（**不碰实现**）。

    **这份公式是本文件自己写的**，不 import `rules.standard.score_clear`
    ——调用实现再断言等于自己，就是同义反复（M0-1 在测试层的翻版）。

    口径（与 spec §7.4 一致）：

    - 基础分按 `classKey` 查表；
    - **B2B 倍率 × 3/2 只作用于基础分**（不含连击奖励）；
    - 连击奖励 `50 × 消行前的连击数`；
    - 等级 = `总行数 // 10 + 1`。
    """
    score = 0
    lines = 0
    b2b = False
    for count, class_key, combo_before in clears:
        base = HAND_SCORE_TABLE[class_key]
        difficult = class_key in HAND_DIFFICULT_KEYS
        if difficult and b2b:
            base = base * 3 // 2
        score += base + HAND_COMBO_BONUS * combo_before
        lines += count
        b2b = difficult
    return score, lines, lines // HAND_LINES_PER_LEVEL + 1


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------


def _started_loop(seed: int = SEED) -> tuple[Assembly, HeadlessLoop]:
    """造一套**已开局**的装配与帧循环（机械场景用）。"""
    assembly = assemble(seed=seed)
    loop = HeadlessLoop(assembly)
    loop.start()
    return assembly, loop


def _run_tool(relative_path: str, *args: str) -> subprocess.CompletedProcess[str]:
    """跑 `tools/` 下的一个脚本（**按本机首选编码解码**——Windows 上管道走 GBK）。"""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / relative_path), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    encoding = locale.getpreferredencoding(False)
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=result.stdout.decode(encoding, errors="replace"),
        stderr=result.stderr.decode(encoding, errors="replace"),
    )


def _read_json(path: Path) -> dict:
    """读一份 JSON 文件。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, document: dict) -> Path:
    """写一份 JSON 文件（格式与工具写出来的一致）。"""
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


# ---------------------------------------------------------------------------
# 夹具（**模块级**：一局两千多帧，别每条用例重跑一遍）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def played() -> PlayOutcome:
    """按参考策略把一局玩到顶出（**G1-1 / G1-4 共用这一局**）。"""
    return play_to_game_over(assemble(seed=GAME_SEED), ReferencePolicy())


@pytest.fixture(scope="module")
def trace() -> RunTrace:
    """一小段跑的逐帧轨迹（快照 · 命令 · 指纹）。"""
    return play_frames(assemble(seed=SEED), ReferencePolicy(), 60)


# ---------------------------------------------------------------------------
# G1-1 · 完整一局玩到顶出，结算与手算逐项相等
# ---------------------------------------------------------------------------


def test_m1_full_game_to_topout_the_game_really_ends_in_a_topout(played: PlayOutcome) -> None:
    """这一局**真的顶出了**（不是撞上兜底帧数上限）。

    **顶出不依赖策略聪明**——依赖策略**不作弊**（不许直接改棋盘）。
    策略再笨，只要老实派命令，方块就会越堆越高，最后顶出。
    """
    assert played.game_over is True
    assert played.report.phase == PHASE_GAME_OVER
    assert played.frames > 0
    assert played.report.events > 0


def test_m1_full_game_to_topout_the_settlement_matches_the_hand_computation(
    played: PlayOutcome,
) -> None:
    """结算与**手算**逐项相等。

    **三层交叉核对**（见模块 docstring）：

    1. 观测到的消行序列 == 手抄的 `EXPECTED_CLEARS`；
    2. 手抄序列按本文件自己的公式算出的分 == 人工推导的常数；
    3. 人工推导的常数 == 内核给的结算分。
    """
    assert played.clears == EXPECTED_CLEARS, "消行序列与手抄的不一致——观测事实变了，手抄表要重新抄"

    score, lines, level = _hand_score(EXPECTED_CLEARS)
    assert score == EXPECTED_SCORE, "手抄的公式与人工推导的常数对不上"
    assert lines == EXPECTED_LINES
    assert level == EXPECTED_LEVEL

    assert played.report.score == EXPECTED_SCORE
    assert played.report.lines == EXPECTED_LINES
    assert played.report.level == EXPECTED_LEVEL
    assert played.report.piecesPlaced == EXPECTED_PIECES


def test_m1_full_game_to_topout_the_settlement_tail_is_a_miss(played: PlayOutcome) -> None:
    """结算末态的**连击与 B2B 都是断的**。

    **为什么**：最后一次消行之后还有 8 次放空（75 块里，最后一次消行在第 67 块）
    ——**放空把连击与 B2B 链都断掉**。所以末态是 `combo = 0` / `b2b = False`。

    **这一条不是"凑一个期望值"**：它守的是「落空真的断链」这条口径——
    连击与 B2B 只在**连续消行**时才会是别的值。
    """
    assert played.report.combo == 0
    assert played.report.b2b is False
    # 最后一次消行之后确实还有放空——否则这条断言就是靠"碰巧"成立的。
    assert played.placements[-1] == 0
    assert sum(1 for count in played.placements[-8:] if count) == 0


def test_m1_full_game_to_topout_the_policy_only_returns_contract_commands(
    trace: RunTrace,
) -> None:
    """策略给出的命令名**全在契约的命令表里**，且**不带参数**。

    **命令集是封闭且可枚举的**（FR-2.1）——策略给出表外的名字，内核会拒，
    那一局就悄悄少了几步动作，门判据的账本也跟着对不上。
    """
    names = {name for commands in trace.commands for name in commands}
    assert names, "策略一帧都没派命令——它根本没在玩"
    unknown = names - set(COMMAND_TYPES)
    assert not unknown, f"策略派了契约未登记的命令：{unknown}"
    assert names <= set(COMMANDS)


def test_m1_full_game_to_topout_the_policy_cannot_change_the_board_it_reads(
    trace: RunTrace,
) -> None:
    """策略**改不动**它读到的棋盘（N1 · 单一状态所有者）。

    三条一起守：

    1. 快照的三个 dataclass 都是**冻结**的——策略在**语言层面**就写不动它；
    2. 策略**拿不到内核**：构造它**不需要任何参数**，实例上也**不留任何状态**
       ——它手里只有一份改不动的快照；
    3. 策略跑完之后，棋盘**逐格未变**；
    4. 策略模块的源码里**搜不到**它对内核的写入通道。
    """
    assert Snapshot.__dataclass_params__.frozen is True
    assert Board.__dataclass_params__.frozen is True
    assert Active.__dataclass_params__.frozen is True

    # **策略拿不到内核**：构造不需要参数（拿不到内核的引用），实例上不留状态
    # （不记"上一步做了什么"，也就没有藏一块棋盘的地方）。
    assert not inspect.signature(ReferencePolicy).parameters
    assert vars(ReferencePolicy()) == {}

    for snapshot, board_before in zip(trace.snapshots, trace.boards, strict=True):
        ReferencePolicy().decide(snapshot)
        assert snapshot.board.cells == board_before, "策略跑完之后棋盘变了——它作弊了"

    # **再拿一份全新的快照**：夹具里的快照**已经被策略看过一轮**了，拿它做
    # "前后对比"比的是**改完之后**的东西——**比了个空**。这一份是策略
    # 从没碰过的，前后对比才是真的。
    assembly, _loop = _started_loop()
    fresh = assembly.kernel.snapshot()
    fresh_before = fresh.board.cells
    ReferencePolicy().decide(fresh)
    assert fresh.board.cells == fresh_before, "策略把没碰过的棋盘改了——它作弊了"

    source = inspect.getsource(importlib.import_module("support.policy"))
    for forbidden in ("submit(", "injectGarbage", "writeCells", "Kernel(", "_state"):
        assert forbidden not in source, (
            f"策略源码里出现了 {forbidden!r}——策略只能派命令，不能直接动内核"
        )


def test_m1_full_game_to_topout_the_policy_is_a_pure_function_of_the_snapshot(
    trace: RunTrace,
) -> None:
    """同一份快照 → 同一串命令（**不许用随机数、不许用时间**）。

    **纯函数**：策略不记"上一步做了什么"——所有判断都从当前快照算出来。
    同一份快照算两遍必须一模一样，否则回放与门判据全废。
    """
    policy = ReferencePolicy()
    for snapshot in trace.snapshots:
        assert policy.decide(snapshot) == policy.decide(snapshot)


# ---------------------------------------------------------------------------
# G1-4 · 完整计分表全对（端到端那一半）
# ---------------------------------------------------------------------------


def test_m1_scoring_table_the_end_to_end_score_equals_the_independent_table(
    played: PlayOutcome,
) -> None:
    """端到端跑出来的分 == 按**手抄计分表**算出来的分。

    **与 G1-1 的分工**：G1-1 验"整局的结算对不对"，这一条验"**计分表本身**
    在真实事件流上对不对"——观测到的类别键 → 查手抄的表 → 与内核的分数相等。

    **不用实现算期望**：`HAND_SCORE_TABLE` 是手抄的，`_hand_score()` 是本文件
    自己写的——**不 import `rules.standard`**。
    """
    observed = [(count, class_key) for count, class_key, _combo in played.clears]
    assert observed == [(count, class_key) for count, class_key, _combo in EXPECTED_CLEARS]

    score, lines, _level = _hand_score(EXPECTED_CLEARS)
    assert score == played.report.score
    assert lines == played.report.lines


def test_m1_scoring_table_the_hand_table_covers_every_class_key_the_run_produced(
    played: PlayOutcome,
) -> None:
    """手抄的表**盖得住**这一局出现过的每一个类别键（表缺一项就当场 KeyError）。

    **这一条防的是"表抄漏了"**：手抄的表少一个键，计分那条用例会红，但
    红得莫名其妙——这里让它红得**指名道姓**。
    """
    unknown = {class_key for _count, class_key, _combo in played.clears} - set(HAND_SCORE_TABLE)
    assert not unknown, f"手抄的计分表盖不住这些类别键：{unknown}"


# ---------------------------------------------------------------------------
# G1-5 · 锁定延迟与手感机制
# ---------------------------------------------------------------------------


def test_m1_lock_delay_the_kernel_exports_the_hand_transcribed_constants() -> None:
    """内核导出的两个常量 == **手抄**的数（30 帧 / 15 次）。

    **这条单独钉住常量本身**：行为那条（落地到锁定差 30 帧）用的是手抄的字面量，
    可内核把常量改成别的数、行为却不变时，行为那条抓不到——这条抓。
    """
    assert LOCK_DELAY_FRAMES == EXPECTED_LOCK_DELAY_FRAMES
    assert LOCK_DELAY_MAX_RESETS == EXPECTED_LOCK_DELAY_MAX_RESETS


def test_m1_lock_delay_the_piece_locks_30_frames_after_it_lands() -> None:
    """方块落地后**恰好 30 帧**锁定。

    **口径**（spec §7.5）：落地那一帧**起算但不消耗**计时 → 锁定帧与落地帧
    **相差 30**（含落地那一帧算进去是 31 帧）。

    **硬降也一样**：spec §7.5 没给硬降开口子，"落地后开始计时"这句话没有
    例外——所以这条用例走的是硬降路径。
    """
    assembly, loop = _started_loop()
    kernel = assembly.kernel
    landed: list[int] = []
    locked: list[int] = []
    assembly.bus.subscribe("piece:lock-delay-start", lambda envelope: landed.append(envelope.frame))
    assembly.bus.subscribe("piece:locked", lambda envelope: locked.append(envelope.frame))

    kernel.submit(Command(type="hardDrop", source="test"))
    for _ in range(EXPECTED_LOCK_DELAY_FRAMES + 10):
        loop.run_frame()

    assert landed, "方块落地后应当起算锁定延迟"
    assert locked, "方块应当在锁定延迟走完后锁定"
    assert locked[0] - landed[0] == EXPECTED_LOCK_DELAY_FRAMES


def test_m1_lock_delay_the_16th_move_does_not_reset_the_timer() -> None:
    """**第 16 次移动不再重置**计时（上限 15 次），但**照常锁定**。

    **为什么要盯住"照常锁定"**：不重置 ≠ 不锁定——上限到了以后计时照走，
    方块照落盘。

    **怎么证明第 16 次真的没重置**：把 16 次移动排到锁定之前，然后断言
    **锁定帧与最后一次重置帧相差恰好 30**。第 16 次若还能重置，锁定会被推后。
    """
    assembly, loop = _started_loop()
    kernel = assembly.kernel
    resets: list[tuple[int, int]] = []
    locked: list[int] = []
    assembly.bus.subscribe(
        "piece:lock-delay-reset",
        lambda envelope: resets.append((envelope.frame, int(dict(envelope.payload)["resetCount"]))),
    )
    assembly.bus.subscribe("piece:locked", lambda envelope: locked.append(envelope.frame))

    kernel.submit(Command(type="hardDrop", source="test"))
    loop.run_frame()

    # 每帧挪一格：左右交替，挪得动（**移不动不重置**，这条要靠"挪得动"成立）。
    for index in range(20):
        kernel.submit(Command(type="moveLeft" if index % 2 == 0 else "moveRight", source="test"))
        loop.run_frame()
    for _ in range(EXPECTED_LOCK_DELAY_FRAMES + 5):
        loop.run_frame()

    expected_resets = list(range(1, EXPECTED_LOCK_DELAY_MAX_RESETS + 1))
    assert [count for _frame, count in resets] == expected_resets, (
        f"重置次数应当是 1..{EXPECTED_LOCK_DELAY_MAX_RESETS}，实为 {resets}"
    )
    assert locked, "上限到了也必须照常锁定"
    assert locked[0] - resets[-1][0] == EXPECTED_LOCK_DELAY_FRAMES, (
        "最后一次重置之后应当恰好再走 30 帧锁定——第 16 次移动若还能重置，这里就会多出帧数"
    )


def test_m1_lock_delay_hold_is_usable_only_once_per_piece() -> None:
    """暂存**每块一次**：同一块再暂存 → 被拒（`hold_already_used`）。

    **拒绝也要看得见**：内核发 `command:rejected`（原因 `hold_already_used`）
    **并**发一条 `piece:hold-denied`——两条都在，断言才落得住。
    """
    assembly, loop = _started_loop()
    kernel = assembly.kernel
    denied: list[str] = []
    rejected: list[str] = []
    assembly.bus.subscribe(
        "piece:hold-denied", lambda envelope: denied.append(str(dict(envelope.payload)["reason"]))
    )
    assembly.bus.subscribe(
        "command:rejected",
        lambda envelope: rejected.append(
            str(dict(envelope.payload)["reason"])
            if dict(envelope.payload)["commandType"] == "hold"
            else ""
        ),
    )

    kernel.submit(Command(type="hold", source="test"))
    loop.run_frame()
    assert denied == [], "第一次暂存应当被接受"
    assert kernel.snapshot().holdUsed is True

    kernel.submit(Command(type="hold", source="test"))
    loop.run_frame()
    assert denied == ["hold_already_used"], "同一块第二次暂存必须被拒"
    assert "hold_already_used" in rejected

    # **这一块落盘之后额度复位**——"每块一次"不是"每局一次"。
    kernel.submit(Command(type="hardDrop", source="test"))
    for _ in range(EXPECTED_LOCK_DELAY_FRAMES + 5):
        loop.run_frame()
    assert kernel.snapshot().holdUsed is False


def test_m1_lock_delay_the_ghost_landing_is_where_the_piece_lands() -> None:
    """**幽灵落点就是方块真正落下来的位置**。

    **幽灵落点是给渲染层看的预告**：预告与实际不一致，玩家看到的和玩到的
    就是两回事。快照给的是**位移向量** `ghostDelta`（相对活动方块），
    所以落点 = 当前坐标 + 位移。
    """
    assembly, loop = _started_loop()
    kernel = assembly.kernel
    snapshot = kernel.snapshot()
    active = snapshot.active
    assert active is not None
    ghost = (active.x + snapshot.ghostDelta[0], active.y + snapshot.ghostDelta[1])

    landed: list[tuple[int, int]] = []
    assembly.bus.subscribe(
        "piece:locked",
        lambda envelope: landed.append(
            (int(dict(envelope.payload)["x"]), int(dict(envelope.payload)["y"]))
        ),
    )

    kernel.submit(Command(type="hardDrop", source="test"))
    for _ in range(EXPECTED_LOCK_DELAY_FRAMES + 5):
        loop.run_frame()

    assert landed, "方块应当落盘"
    assert landed[0] == ghost, f"幽灵落点 {ghost} 与实际落点 {landed[0]} 不一致"


# ---------------------------------------------------------------------------
# G1-6 · 契约基线跨文件自证
# ---------------------------------------------------------------------------


def test_m1_contract_expected_list_mirrors_the_baseline_entry_for_entry() -> None:
    """基线与**跨文件期望清单**逐条相等（键集与哈希都要一样）。

    **独立比对**：这里把两份文件的条目直接读出来比，**不采信工具自己算的
    退出码**——退出码是被测代码自己算的，它说"通过" pytest 就没话说。
    """
    assert CONTRACT_EXPECTED.exists(), f"期望清单不存在：{CONTRACT_EXPECTED}"

    baseline = _read_json(CONTRACT_BASELINE)
    expected = _read_json(CONTRACT_EXPECTED)

    assert set(expected["entries"]) == set(baseline["entries"]), (
        "两份清单的键集不相等——基线与期望清单必须逐条相等"
    )
    assert expected["entries"] == baseline["entries"], (
        "两份清单的哈希不相等——基线与期望清单必须逐条相等"
    )
    assert expected["meta"]["entryCount"] == len(expected["entries"])


def test_m1_contract_expected_list_rejects_one_entry_removed_and_the_count_synced(
    tmp_path: Path,
) -> None:
    """M0-1 的原题：**少一条契约并同步改计数**时必须报红。

    **为什么红的是跨文件那一层**：临时这一对清单原本与当前声明**完全一致**
    （工具自己说绿）——基线自述块、基线 vs 当前声明两层都是绿的，唯一起变化的
    是两份清单不再相等。

    **自述块挡不住这一条**：计数键与被计数的条目在同一个文件里，少一条同时
    改计数，基线仍然自洽。期望值移出基线文件才有第二层。
    """
    baseline = tmp_path / "contracts_baseline.json"
    expected = tmp_path / "contracts_expected.json"

    generated = _run_tool(
        "tools/contract_baseline.py",
        "--update-baseline",
        "--baseline",
        str(baseline),
        "--reason",
        "G1-6 反向验证用：造一份与当前声明一致的基线",
    )
    assert generated.returncode == 0, (
        f"造不出与当前声明一致的基线：\n{generated.stdout}\n{generated.stderr}"
    )

    mirrored = _run_tool(
        "tools/contract_baseline.py",
        "--update-expected",
        "--baseline",
        str(baseline),
        "--expected",
        str(expected),
        "--reason",
        "G1-6 反向验证用：造一份与基线一致的期望清单",
    )
    assert mirrored.returncode == 0, (
        f"造不出与基线一致的期望清单：\n{mirrored.stdout}\n{mirrored.stderr}"
    )

    document = _read_json(baseline)
    entries = document["entries"]
    removed = sorted(entries)[0]
    del entries[removed]
    # **同步改条数**——这是原题的关键：自述块照样自洽。
    document["meta"]["entryCount"] = len(entries)
    broken = _write_json(tmp_path / "broken_baseline.json", document)

    result = _run_tool(
        "tools/contract_baseline.py",
        "--baseline",
        str(broken),
        "--expected",
        str(expected),
    )

    assert result.returncode != 0, (
        "基线少一条契约、计数也同步改了，工具却放行——"
        "跨文件自证被绕过，'只增不改'退化成了'只查已有键'"
    )


# ---------------------------------------------------------------------------
# G1-7 · 取值域统一收口
# ---------------------------------------------------------------------------


def test_m1_value_domains_are_written_into_the_type_strings() -> None:
    """取值域**写进类型串**了（形态 11 · 字面量枚举）。

    **钉住"逐字相等"**：注解存下来的样子、生成器产出的样子、基线哈希的
    样子是同一串——三处对不上时，基线就看不见取值域变过。
    """
    assert RuleContext.__annotations__["axis"] == AXIS_LITERAL
    assert ClearGroup.__annotations__["axis"] == CLEAR_AXIS_LITERAL
    # 类型串**确实带着取值域**（不是裸 `str`）。
    assert AXIS_LITERAL != "str"
    assert "down" in AXIS_LITERAL


def test_m1_value_domains_out_of_domain_values_are_rejected_at_construction() -> None:
    """域外的值**当场被拒**——不是飘到渲染层才暴露。

    **为什么构造即拒**：内核读 `ctx.axis` 才知道重力朝哪。规则集写错轴，
    构造时不拦，一路飘到渲染层才炸——那时候已经不知道是谁的错了。
    """
    with pytest.raises(ValueError):
        RuleContext(frame=0, level=1, piecesPlaced=0, axis="diagonal", stepMs=1000)
    with pytest.raises(ValueError):
        ClearGroup(axis="sideways", index=0, cells=())

    # 域内的值构造得出来。
    assert RuleContext(frame=0, level=1, piecesPlaced=0, axis="down", stepMs=1000)
    assert set(AXIS_VALUES) == {"down", "left", "right", "up"}
    assert set(CLEAR_AXIS_VALUES) == {"row", "column"}


def test_m1_value_domains_the_grammar_accepts_form_11_and_rejects_the_illegal_ones() -> None:
    """文法**认得**形态 11（合法串收 · 非法串拒）。

    **非法串取形态 11 的三个反面样例**：`Literal[down]`（标识符不是字符串
    字面量）· `Literal["a", 1]`（成员不是字符串）· `Literal[]`（成员为空）。
    """
    canonicalize = _grammar.canonicalize

    assert canonicalize('Literal["a", "b"]', NAMED_STRUCTS) == "Literal['a', 'b']"
    assert canonicalize(AXIS_LITERAL, NAMED_STRUCTS) == AXIS_LITERAL

    for illegal in ("Literal[down]", 'Literal["a", 1]', "Literal[]"):
        with pytest.raises(_grammar.GrammarError):
            canonicalize(illegal, NAMED_STRUCTS)


# ---------------------------------------------------------------------------
# G1-8 · 软降计分归规则集
# ---------------------------------------------------------------------------


def _five_points_per_cell(ctx: RuleContext, distance: int) -> int:
    """**替身规则集**的软降计分：每下移一格 5 分（标准规则集是 1 分）。

    `ctx` 用不上，但参数不能删——签名是冻结的（ADR-061）。
    """
    return distance * 5


#: 替身规则集：**只有软降计分**与标准规则集不同，其余 8 条原样。
DOUBLE_RULE_SET: RuleSet = replace(STANDARD_RULESET, soft_drop_score=_five_points_per_cell)


def test_m1_soft_drop_score_follows_the_rule_set() -> None:
    """换规则集 → 软降得分**跟着变**（标准 1 分 / 替身 5 分）。

    **光断言"软降加 1 分"证不了任何事**——写死 `+1` 的内核照样通过。
    所以这里用**替身规则集**：写死 `+1` 的内核当场报红。
    """
    for rules, expected_delta in ((STANDARD_RULESET, 1), (DOUBLE_RULE_SET, 5)):
        assembly = assemble(seed=SEED, rule_set=rules)
        loop = HeadlessLoop(assembly)
        loop.start()
        kernel = assembly.kernel
        before = kernel.snapshot().score

        kernel.submit(Command(type="softDrop", source="test"))
        loop.run_frame()

        assert kernel.snapshot().score - before == expected_delta, (
            f"软降一格应当加 {expected_delta} 分，实加 "
            f"{kernel.snapshot().score - before} 分——得分没跟着规则集走"
        )


def _kernel_function_node(name: str) -> ast.FunctionDef:
    """从**内核模块的源码**里取出某个方法（按语法树找，不靠行号）。

    **为什么不直接 `inspect.getsource(Kernel._cmd_soft_drop)`**：那拿到的是
    **缩进过**的方法片段，`ast.parse` 收不了（`IndentationError`）。整份模块
    源码才是能解析的东西——**按名字找函数节点**，比按行号截取稳。
    """
    source_path = Path(inspect.getfile(Kernel))
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Kernel":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == name:
                    return child
    raise AssertionError(f"内核里找不到方法 {name}")


def test_m1_soft_drop_score_the_kernel_source_holds_no_hardcoded_value() -> None:
    """内核源码里**搜不到**「软降每格几分」。

    **怎么搜**：不是全文搜数字（会误伤），而是**把软降那条路径的源码解析成
    语法树**，找出所有**写到分数上**的赋值，断言它们的右值里**没有整数
    字面量**。

    - 写死的：`self._state.score = before + 1` → 右值里有 `Constant(1)` → 红；
    - 归规则集的：`self._state.score = before + delta` → 右值里没有整数字面量
      → 绿。**下移一格那个 `1` 是距离不是分数**，它在 `soft_drop_score(...)`
      的实参里，不在分数赋值的右值里。
    """
    function = _kernel_function_node("_cmd_soft_drop")

    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "soft_drop_score"
    ]
    assert calls, "软降得分必须来自规则集的第 9 条签名"

    offenders: list[str] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign):
            continue
        targets = [ast.unparse(target) for target in node.targets]
        if not any("score" in target for target in targets):
            continue
        for constant in ast.walk(node.value):
            if isinstance(constant, ast.Constant) and isinstance(constant.value, int):
                offenders.append(ast.unparse(node))
    assert not offenders, f"内核把软降的分值写死了：{offenders}——软降得分必须归规则集"


# ---------------------------------------------------------------------------
# 元守卫：§5.2 的九个入口必须都还活着
# ---------------------------------------------------------------------------

#: §5.2 的复跑命令在 plan 里的形态（**只认表格行**：`| G1-n | … | `pytest -k <命中名>` |`）。
#:
#: **为什么只认表格行**：全文搜 `pytest -k <名字>` 会把**正文里顺带提到的**
#: 那几个也算进来（§5.13 的发现段里就写着两条）——数出来是 11，不是 9。
#: **这张表才是契约**，散文里提到某个命令不算数。
_ENTRY_POINT_PATTERN = re.compile(
    r"^\|\s*G1-\d+\s*\|.*`pytest -k ([a-z0-9_]+)`",
    flags=re.MULTILINE,
)


def _collect_test_names() -> list[str]:
    """`tests/` 下所有测试函数的**名字**（按 AST 取，不靠正则扫全文）。"""
    names: list[str] = []
    for path in sorted((REPO_ROOT / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names.extend(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
    return names


def test_m1_gates_every_section_5_2_entry_point_selects_a_test() -> None:
    """§5.2 的**九个复跑命令**都要**选得到用例**。

    **为什么要有这条守卫**：§5.2 说「这九个命令是『门还在不在』的**唯一入口**」。
    实测发现**其中两个从来没绑定过任何用例**（G1-2 的 `m1_tables_transcription`
    与 G1-3 的 `m1_tspin_five_tiers`）——`pytest -k` 选到 **0 条**，而 **pytest
    对"一条都没选到"照样退出 0**（它只在"选中了、但有失败"时才非 0）→ **门从
    入口上消失了**，而且**没人会发现**。

    **这条守卫读的是 plan §5.2 的表**：表里的命中名 → 扫 `tests/` 下的用例名
    → 每一条都要**至少命中一个**。**表改了守卫跟着改**，两边不会各走各的。
    """
    plan = (REPO_ROOT / "docs" / "plan-M1.md").read_text(encoding="utf-8")
    names = _ENTRY_POINT_PATTERN.findall(plan)
    assert len(names) == 9, f"§5.2 的复跑命令应当是九条，实为 {names}"

    test_names = _collect_test_names()
    unbound = [name for name in names if not any(name in test for test in test_names)]
    assert not unbound, f"这些入口选不到任何用例：{unbound}"


# ---------------------------------------------------------------------------
# G1-9 · 确定性回归 · 逐帧指纹
# ---------------------------------------------------------------------------


def test_m1_frame_determinism_the_same_seed_gives_the_same_frame_by_frame_fingerprints() -> None:
    """同种子两遍，**每一帧**的棋盘指纹全等（**不是只看最终态**）。

    **逐帧比**：只看最终态的话，"中间某一帧分岔、后来又合回来"那种改动能
    溜过去——指纹序列全等才堵得住。

    **两遍跑的是同一条路径**：同一个种子 + 同一个策略 + 同一段帧数。
    """
    frames = 600
    first = play_frames(assemble(seed=SEED), ReferencePolicy(), frames)
    second = play_frames(assemble(seed=SEED), ReferencePolicy(), frames)

    assert len(first.fingerprints) == frames
    assert first.fingerprints == second.fingerprints, "同种子两遍的逐帧指纹不相等"
    assert first.commands == second.commands, "同种子两遍派出的命令不相等"


def test_m1_frame_determinism_the_two_runs_really_differ_from_each_other_in_what_they_check() -> (
    None
):
    """两遍跑**确实跑到了东西**（正向：不许把这条写成恒绿）。

    **防的是"空跑"**：帧数为 0 时两遍的指纹序列都是空的，逐帧相等照样成立——
    那条用例就成了一句空话。这里钉住"跑够了帧、指纹真的在变"。
    """
    run = play_frames(assemble(seed=SEED), ReferencePolicy(), 120)

    assert len(run.fingerprints) == 120
    assert len(set(run.fingerprints)) > 1, "棋盘指纹从头到尾没变过——这一遍没在玩"
