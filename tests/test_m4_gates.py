"""M4 九道门判据（S11 · plan-M4.md §5.2 · `G4-1`…`G4-9`）。

## 九道门与本文件的用例

| 门 | 判据 | 复跑命令（命中名） |
| --- | --- | --- |
| `G4-1` | 两个变体可完整游玩 · 内核差异为空 | `m4_two_variants_playable_without_kernel_change` |
| `G4-2` | 变体局回放第五项 | `m4_variant_replay_fifth_item` |
| `G4-3` | 实时宿主：渲染 + 输入同一条通道 | `m4_realtime_host_render_and_input` |
| `G4-4` | DAS / ARR 可调且不改状态 | `m4_das_arr_tunable_without_state_change` |
| `G4-5` | AI 与人类同通道 | `m4_ai_same_command_channel` |
| `G4-6` | 变体落在 `variants/` 不碰冻结区 | `m4_variants_directory_independence` |
| `G4-7` | 契约面零变更 | `m4_contract_surface_unchanged` |
| `G4-8` | 扩展指纹随变体状态变化 | `m4_extension_fingerprint_tracks_variant_state` |
| `G4-9` | 变体零污染 | `m4_variant_zero_pollution` |

复跑命令是 `pytest -k <命中名>`（完整的命令在 plan-M4 §5.2 那张表里）。

## **命中名放在类名上**（为什么）

**用例名不许中途改**——改了 §5.2 的复跑命令就失效（plan-M4 §5.2）。**而
`pytest -k` 匹配的是「模块名 / 类名 / 函数名」的子串**——**命中名放在类名上**，
**方法名就能各自说人话**（不必把一句判据塞进每个函数名里 → **函数名不必长**）。

元守卫在 `tests/test_m4_gates_meta.py`：它**读 §5.2 那张表**，把九个命中名逐条与
`tests/` 下的**类名与函数名**比对，**选不到就红并点名那个入口**。

## 这九道门各自由什么撑着

**M4 的正题是「换掉整套玩法还能玩、内核一个字节都没动」**——所以每道门都有
**两面**：

- **行为面**：变体**真的能玩完一局** · 变体局的录像**真的能回放** · 人**真的
  能按键玩** · AI **真的走同一条通道**；
- **差异面**：**没改内核** · **没改规则** · **没改契约的既有部分**。

**只测行为面，门就只剩一半**——一个把变体逻辑全塞进 `kernel/core.py` 的实现照样
能通过行为断言。**`G4-1` / `G4-6` / `G4-7` 是这几面的机器可验形态**。

## 期望值的独立性（M0-1 在门层的同款纪律）

**手抄 ≠ 实现算出来**——所以每道门里凡是"期望值"，都**以字面量出现在本文件的
源码里**，不引用被测代码算出来的数当结论。

**手抄的是这些**：

- **契约条数**（50 事件 · 17 命令 · 13 拒绝原因 · 22 快照字段 · 5 棋盘子字段 ·
  9 规则签名）——**从需求 §8 抄的**，不是从 `contracts/` 读出来的；
- **空扩展槽的摘要**（`2fe994d6…`）——**一个字面量**；
- **默认装配的前三帧棋盘指纹**——**一个字面量**（不是"跑一遍再拿它当期望"）；
- **落地提交的新增文件清单**——**手抄的表**。

元守卫还守着这一条（`test_m4_gate_expected_values_are_hand_copied_literals`）——
**期望值一旦变成"从实现算出来的"，当场红**。

## 自给自足

不依赖 `tests/conftest.py` 的 fixture。
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tetris_kernel.adapters.input import KeyboardInput
from tetris_kernel.adapters.render import TerminalRenderer
from tetris_kernel.contracts.commands import COMMANDS, REJECT_REASONS
from tetris_kernel.contracts.events import EVENT_TYPES
from tetris_kernel.contracts.rules import RULE_SIGNATURES
from tetris_kernel.contracts.snapshot import BOARD_FIELDS, SNAPSHOT_FIELDS
from tetris_kernel.host import assemble, assemble_play
from tetris_kernel.host.assembly import RULE_SETS
from tetris_kernel.host.builtin_variants import register_builtin_variants
from tetris_kernel.host.cli import main
from tetris_kernel.host.discovery import system_catalog
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.host.replay import EMPTY_EXTENSION_FINGERPRINT
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.rules.standard import STANDARD_RULESET
from tetris_kernel.systems.replay import Recording, recording_to_jsonl
from tetris_kernel.variants.gravity import gravity_rule_set

#: 仓库根目录（`tests/` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 源码根（`src/tetris_kernel`）。
SRC_ROOT = REPO_ROOT / "src" / "tetris_kernel"

#: **冻结区**（M4-D1：一个字节都不许动）。
FROZEN_DIRS = ("src/tetris_kernel/kernel", "src/tetris_kernel/rules")

#: **变体层**（M4-S6：变体实现落在 `variants/` 独立目录）。
VARIANTS_DIR = SRC_ROOT / "variants"

#: 装配用的固定种子（**测试不依赖随机性**）。
SEED = 20261110

#: 变体局那一局的种子。
VARIANT_SEED = 20261101

#: 最多跑多少帧（**够玩到顶出**）。
MAX_FRAMES = 4000

#: **默认装配前三帧的棋盘指纹**（**手抄的期望值** —— 不是"跑一遍再拿它当期望"）。
#:
#: **为什么手抄**：M0-1 的老坑——**"实现算的等于实现算的"就是自证**。这条是
#: **默认路径没被 M4 动过**的锚点（**内核与规则集逐字节未变** → 行为逐帧未变）。
EXPECTED_DEFAULT_FINGERPRINTS = (
    "d53a204b1444f206f0b58ad4dae415cd",
    "d53a204b1444f206f0b58ad4dae415cd",
    "d53a204b1444f206f0b58ad4dae415cd",
)

#: **空扩展槽的摘要**（**手抄的期望值** —— 第五项在无变体时的值）。
EXPECTED_EMPTY_EXTENSION_FINGERPRINT = "2fe994d63716624b6b9842d7bcdfdb9e"

#: **契约条数**（**手抄的期望值** —— 从需求 §8 抄的）。
EXPECTED_EVENT_COUNT = 50
EXPECTED_COMMAND_COUNT = 17
EXPECTED_REJECT_REASON_COUNT = 13
EXPECTED_SNAPSHOT_FIELD_COUNT = 22
EXPECTED_BOARD_FIELD_COUNT = 5
EXPECTED_RULE_SIGNATURE_COUNT = 9

#: **M4 的落地提交新增的文件清单**（**手抄的期望值**）。
#:
#: **为什么手抄**：门要看的是"**变体与 AI 的实现是不是落在自己的新目录里**"——
#: 这是**一份数据对另一份数据**的比对（**不是"实现算的等于实现算的"**）。
#:
#: **落地提交允许改既有文件**（M4 与 M3 不同）：变体要接线（宿主登记表 · CLI）·
#: AI 的声明要进契约面 · **刷基线要与改动同笔**（ADR-077）——**这些都要改既有
#: 文件**。**M4 的差异断言在冻结区**（**别处随便改**），不在"只许新增文件"。
EXPECTED_LANDING_ADDITIONS = {
    "src/tetris_kernel/variants/gravity/rules.py": (
        "src/tetris_kernel/variants/gravity/__init__.py",
        "src/tetris_kernel/variants/gravity/rules.py",
        "src/tetris_kernel/variants/gravity/tables.py",
    ),
    "src/tetris_kernel/variants/growth/manifest.py": (
        "src/tetris_kernel/variants/growth/__init__.py",
        "src/tetris_kernel/variants/growth/manifest.py",
        "src/tetris_kernel/variants/growth/system.py",
    ),
    "src/tetris_kernel/systems/ai/manifest.py": (
        # **AI 的 `__init__.py` 不在这笔提交里**——**包初始化文件在 M0 搭分层
        # 空壳时就建好了**（那时这个包还空着），**不是这个系统的落地提交的一部分**
        # （与 M3 的门同款：`achievements` / `rotations` 的 `__init__.py` 也一样）。
        "src/tetris_kernel/systems/ai/manifest.py",
        "src/tetris_kernel/systems/ai/search.py",
        "src/tetris_kernel/systems/ai/system.py",
    ),
}


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    """跑一条 git 命令，返回它的标准输出（**去掉行尾**）。"""
    result = subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _four_items(snapshot: Snapshot) -> tuple[str, int, int, int]:
    """快照 → 四项（棋盘指纹 · 分数 · 行数 · 方块数）。"""
    return (
        snapshot.board.structureFingerprint,
        snapshot.score,
        snapshot.lines,
        snapshot.piecesPlaced,
    )


def _run_frames(
    assembly: object,
    frames: int,
    source: str = "host",
) -> list[tuple[str, int, int, int]]:
    """跑一串固定动作，返回**逐帧的四项**。

    **动作是写死的**（第 0 帧左移 · 第 1 帧旋转 · 第 2 帧软降 · 循环）——**两边
    跑的是同一串动作**，逐帧四项才有得比。
    """
    kernel = assembly.kernel  # type: ignore[attr-defined]
    loop = HeadlessLoop(assembly)  # type: ignore[arg-type]
    loop.start()
    items: list[tuple[str, int, int, int]] = []
    for index in range(frames):
        if index % 3 == 0:
            kernel.submit(Command(type="moveLeft", source=source))
        elif index % 3 == 1:
            kernel.submit(Command(type="rotateCW", source=source))
        else:
            kernel.submit(Command(type="softDrop", source=source))
        loop.run_frame()
        items.append(_four_items(kernel.snapshot()))
    return items


def _play_variant_game(seed: int = VARIANT_SEED) -> Recording:
    """**真跑一局变体 B（落地后生长）**，返回录下来的录像。"""
    register_builtin_variants()
    assembly = assemble(
        seed=seed,
        rule_id="standard",
        systems=("growth",),
        system_catalog=system_catalog(),
        variant_id="growth",
    )
    recorder = _recorder_of(assembly)
    loop = HeadlessLoop(assembly)
    loop.start()
    kernel = assembly.kernel
    while kernel.phase != "game_over" and loop.frames < MAX_FRAMES:
        kernel.submit(Command(type="softDrop", source="human"))
        loop.run_frame()
    return recorder.recording()


def _recorder_of(assembly: object) -> object:
    """从装配结果里挑出录制系统。"""
    from tetris_kernel.systems.replay.recorder import RecordingSystem

    systems = [
        system
        for system in assembly.observable_systems  # type: ignore[attr-defined]
        if isinstance(system, RecordingSystem)
    ]
    assert systems, "录像系统没装上"
    return systems[0]


@dataclass(frozen=True)
class LandingCommit:
    """**落地提交**：一个能力是哪笔提交进来的。"""

    #: 提交哈希。
    commit: str
    #: 这笔提交**新增**了哪些文件。
    added: tuple[str, ...]
    #: 这笔提交**改了**哪些既有文件。
    modified: tuple[str, ...]


def _landing_commit_of(path: str) -> LandingCommit:
    """取**某个文件是哪笔提交新增的**（`git log --diff-filter=A`）。

    **为什么按文件找而不是写死哈希**：写死哈希的话，那笔提交一旦被 rebase 就断了
    ——**门会从"内容变了"变成"查无此提交"**。**按文件找能跟着历史走**。
    """
    raw = _git("log", "--diff-filter=A", "--format=%H", "--", path)
    commits = [line.strip() for line in raw.splitlines() if line.strip()]
    assert commits, f"文件 {path} 在 git 历史里没有「新增」记录——它不是新增文件？"
    commit = commits[-1]
    names = _git("show", "--name-status", "--format=", commit)
    added: list[str] = []
    modified: list[str] = []
    for line in names.splitlines():
        if not line.strip():
            continue
        status, _, name = line.partition("\t")
        name = name.strip()
        if status.startswith("A"):
            added.append(name)
        else:
            modified.append(name)
    return LandingCommit(commit=commit, added=tuple(added), modified=tuple(modified))


# ---------------------------------------------------------------------------
# G4-1 · 两个变体可完整游玩 · 内核差异为空
# ---------------------------------------------------------------------------


class Test_m4_two_variants_playable_without_kernel_change:
    """`G4-1`：**两个变体可完整游玩 · 内核差异为空**（M4-D1 的正题）。"""

    def test_the_frozen_zone_is_byte_identical(self) -> None:
        """第一面：**冻结区逐文件哈希全等**（跑的是工具本身）。

        **改一行注释也该红**——哈希的是整个文件内容，不是符号表。
        """
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "check_frozen_baseline.py")],
            cwd=str(REPO_ROOT),
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, (
            "冻结区与基线不一致——M4 一个字节都不许动内核与规则：\n"
            f"{result.stdout.decode('utf-8', 'replace')}\n"
            f"{result.stderr.decode('utf-8', 'replace')}"
        )

    def test_the_git_diff_of_the_frozen_zone_is_empty(self) -> None:
        """第二面：**git 变更清单里冻结区一个文件都没有**。

        **这一面才是「内核差异为空」的真正含义**：**冻结区基线**盯的是"内容没变"，
        **这条盯的是"没有人在冻结区里动过手脚又改回来"**。

        **比的是哪一段**：`master` 与 `HEAD` 的**共同祖先** = **M4 起点**（M3 终局）
        ——**M4 的全部提交都在它之后** → 这一段覆盖 M4 全程。

        **为什么用 `git diff --name-status` 而不是"人看一眼"**：**人只看一眼就会漏**。
        """
        m4_start = _git("merge-base", "HEAD", "master")
        names = _git("diff", "--name-status", f"{m4_start}..HEAD", "--", *FROZEN_DIRS)
        assert names == "", f"冻结区在 M4 期间被改过：\n{names}"

    def test_the_landing_commits_add_their_own_files(self) -> None:
        """第三面：**落地提交把实现落在自己的新目录里**（手抄的期望值）。

        **期望值是手抄的**（`EXPECTED_LANDING_ADDITIONS` 那张表）——**变体与 AI 的
        实现住在自己的新文件里**，**不是塞进某个既有文件**。

        **为什么按文件找落地提交**：写死哈希的话，那笔提交一旦被 rebase 就断了。

        **与 M3 的 `G3-1` 有一处不同**：M3 的门是"**新增能力 = 只许新增文件**"，
        **M4 不是**——变体要接线（宿主登记表 · CLI）· AI 的声明要进契约面 · **刷基线
        要与改动同笔**（ADR-077）——**这些都要改既有文件**。**M4 的差异断言长在冻结
        区上**（**上面那条**），不在"只许新增文件"。
        """
        for marker, expected_added in EXPECTED_LANDING_ADDITIONS.items():
            landing = _landing_commit_of(marker)
            added_names = {name.split("/")[-1] for name in landing.added}
            expected_names = {name.split("/")[-1] for name in expected_added}
            assert expected_names <= added_names, (
                f"{marker} 的落地提交新增的文件不对：{sorted(landing.added)}"
            )
            # **落地提交不许碰冻结区**——那正是 M4-D1 的正题。
            touched_frozen = [
                name
                for name in landing.modified
                if name.startswith("src/tetris_kernel/kernel")
                or name.startswith("src/tetris_kernel/rules")
            ]
            assert not touched_frozen, f"{marker} 的落地提交碰了冻结区：{touched_frozen}"

    def test_both_variants_can_be_played_to_game_over(self) -> None:
        """第四面：**两个变体都能玩完一局**（不是"手感好"，是"跑得完"）。

        - **变体 A（重力可旋转）**：横向重力 → 跑到顶出并出结算；
        - **变体 B（落地后生长）**：受控写入路径 → 跑到顶出，且**棋盘上真的长出
          晶体**（**那条路径是真的通了**，不是"跑完了但什么都没发生"）。
        """
        # **变体 A**：横向重力。
        assembly = assemble(
            seed=7,
            rule_set=gravity_rule_set("right"),
            rule_id="gravity_right",
        )
        loop = HeadlessLoop(assembly)
        loop.start()
        loop.run_frames(20000, until_game_over=True)
        assert assembly.kernel.snapshot().phase == "game_over", "变体 A 跑不完一局"

        # **变体 B**：落地后生长。
        growth = assemble(seed=7, systems=("growth",), system_catalog=system_catalog())
        growth_loop = HeadlessLoop(growth)
        growth_loop.start()
        growth_kernel = growth.kernel
        while growth_kernel.phase != "game_over" and growth_loop.frames < MAX_FRAMES:
            growth_kernel.submit(Command(type="softDrop", source="human"))
            growth_loop.run_frame()
        snapshot = growth.kernel.snapshot()
        assert snapshot.phase == "game_over", "变体 B 跑不完一局"
        crystals = sum(1 for row in snapshot.board.cells for value in row if value == 8)
        assert crystals > 0, "变体 B 跑完了但棋盘上一颗晶体都没长——受控写入路径没通"


# ---------------------------------------------------------------------------
# G4-2 · 变体局回放第五项
# ---------------------------------------------------------------------------


class Test_m4_variant_replay_fifth_item:
    """`G4-2`：**变体局回放校验多比一项**（扩展指纹）。"""

    def test_a_variant_game_replays_to_the_same_five_items(
        self, tmp_path: Path, capsys: object
    ) -> None:
        """第一面：**变体局录像回放 → 五项全等**（退出码 0）。

        **回放时装上变体**——否则扩展槽是空的 → **第五项必然不等**。
        """
        recording = _play_variant_game()
        path = tmp_path / "variant.jsonl"
        path.write_text(recording_to_jsonl(recording), encoding="utf-8")

        code = main(["replay", str(path)])

        output = capsys.readouterr().out  # type: ignore[attr-defined]
        assert code == 0, f"变体局回放不等：\n{output}"
        assert "五项比对：全等" in output

    def test_the_fifth_item_is_not_the_structure_fingerprint(self) -> None:
        """第二面：**第五项 ≠ 结构指纹**（两个哈希不是一回事）。

        **架构 §5.4**：结构指纹是**棋盘内容**的摘要，扩展指纹是**扩展槽**的摘要。
        **拿结构指纹当第五项** → **四项全绿而结果不同**（变体状态分叉查不出来）。
        """
        recording = _play_variant_game()
        assert recording.extension_fingerprint is not None, "变体局的录像没有第五项"
        assert recording.extension_fingerprint != recording.outcome.fingerprint, (
            "第五项就是结构指纹——两个哈希不是一回事"
        )
        assert recording.extension_fingerprint != EXPECTED_EMPTY_EXTENSION_FINGERPRINT, (
            "变体局的第五项就是空槽的摘要——扩展槽根本没被算进去"
        )

    def test_the_empty_slot_digest_is_the_hand_copied_one(self) -> None:
        """第三面：**空槽的摘要 = 手抄的那个字面量**。

        **手抄的期望值**——**不是"宿主算的等于宿主算的"**。
        """
        assert EMPTY_EXTENSION_FINGERPRINT == EXPECTED_EMPTY_EXTENSION_FINGERPRINT

    def test_a_diverged_extension_state_is_named(self, tmp_path: Path, capsys: object) -> None:
        """第四面：**扩展状态分叉 → 明确指认"第五项不等"**（不能笼统说"四项不等"）。

        **变体局最常见的分叉原因就是扩展状态**——**指认不到就等于让人猜谜**。
        """
        recording = _play_variant_game()
        diverged = Recording(
            seed=recording.seed,
            rule=recording.rule,
            commands=recording.commands,
            outcome=recording.outcome,
            extension_fingerprint="deadbeef" * 4,
            variant=recording.variant,
            version=recording.version,
        )
        path = tmp_path / "diverged.jsonl"
        path.write_text(recording_to_jsonl(diverged), encoding="utf-8")

        code = main(["replay", str(path)])

        output = capsys.readouterr().out  # type: ignore[attr-defined]
        assert code == 1, "第五项不等就是不等（退出码 1）"
        assert "五项比对：不等" in output
        assert "第五项（扩展指纹）不等" in output, "没指认到第五项——让人猜谜"


# ---------------------------------------------------------------------------
# G4-3 · 实时宿主：渲染 + 输入同一条通道
# ---------------------------------------------------------------------------


class Test_m4_realtime_host_render_and_input:
    """`G4-3`：**渲染器只消费事件 · 输入适配器把原始输入翻译成命令**。"""

    def test_the_renderer_only_consumes_events(self) -> None:
        """第一面：**渲染器只消费事件**（FR-8.2 · M4-D2）。

        **它拿不到内核**——**画面完全由事件推导**。**源码级断言**（读源码，不靠
        "看起来没关系"）。
        """
        render_dir = SRC_ROOT / "adapters" / "render"
        for path in sorted(render_dir.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    assert "kernel" not in name, f"{path.name} 不该 import 内核：{name}"

    def test_the_input_adapter_only_consumes_events(self) -> None:
        """第二面：**输入适配器只消费事件**（M4-D2）。

        **命令由宿主递进来**（`submit` 回调）→ **适配器连内核都不用 import**。
        """
        input_dir = SRC_ROOT / "adapters" / "input"
        for path in sorted(input_dir.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    assert "kernel" not in name, f"{path.name} 不该 import 内核：{name}"

    def test_both_go_through_the_same_command_channel(self) -> None:
        """第三面：**渲染与输入走同一条命令通道**。

        **怎么验「同一条」**：**关掉渲染器** → 内核逐帧指纹**一个字节都不变**——
        **渲染器只读不写**；**输入适配器派出去的命令** → **内核真的动了**（活动方块
        左移一格）。

        **两件事合起来 = 同一条通道**：**渲染器在通道的这一头（只读）· 输入适配器
        在通道的那一头（只写）**· **中间是同一个内核**。
        """
        # **渲染器在通道的这一头**：关掉它，内核逐帧不变。
        plain = assemble(seed=SEED, observability_enabled=False)
        rendered = assemble(seed=SEED, observability_enabled=False)
        renderer = TerminalRenderer(
            rule_set=STANDARD_RULESET,
            width=10,
            height=22,
            hidden_rows=2,
        )
        rendered.bus.subscribe("*", renderer.on_event)
        assert _run_frames(plain, 40) == _run_frames(rendered, 40), "渲染器改变了内核的行为"

        # **输入适配器在通道的那一头**：它派的命令内核真的执行了。
        play = assemble_play(seed=SEED, render=False)
        loop = HeadlessLoop(play.assembly)
        loop.start()
        before = loop.snapshot().active
        assert before is not None
        adapter = play.input_adapter
        assert isinstance(adapter, KeyboardInput)
        adapter.handle_key("left", "down")
        loop.run_frame()
        after = loop.snapshot().active
        assert after is not None
        assert after.x == before.x - 1, "输入适配器派的命令没推动内核——它不在同一条通道上"

    def test_the_renderer_draws_from_the_event_stream(self) -> None:
        """第四面：**画面由事件推导**（FR-8.2）。

        **渲染器的棋盘镜像 = 内核棋盘**（逐格相等）——**它没有从内核拿过棋盘**。
        """
        assembly = assemble(seed=SEED, observability_enabled=False)
        renderer = TerminalRenderer(
            rule_set=STANDARD_RULESET,
            width=10,
            height=22,
            hidden_rows=2,
        )
        assembly.bus.subscribe("*", renderer.on_event)
        loop = HeadlessLoop(assembly)
        loop.start()
        for _ in range(200):
            kernel = assembly.kernel
            kernel.submit(Command(type="softDrop", source="host"))
            loop.run_frame()

        snapshot = loop.snapshot()
        expected: dict[tuple[int, int], int] = {}
        for y, row in enumerate(snapshot.board.cells):
            for x, value in enumerate(row):
                if value != 0:
                    expected[(x, y)] = value
        assert renderer.board == expected, "渲染器的棋盘镜像与内核棋盘不一致——它不是从事件推导的"


# ---------------------------------------------------------------------------
# G4-4 · DAS / ARR 可调且不改状态
# ---------------------------------------------------------------------------


class Test_m4_das_arr_tunable_without_state_change:
    """`G4-4`：**DAS / ARR 可调 · 调整参数不触发状态变更**（NFR-8）。"""

    def test_changing_the_parameters_changes_nothing(self) -> None:
        """第一面：**改 DAS / ARR → 内核逐帧指纹全等**（NFR-8）。

        **手感参数归输入适配器**（ADR-044）——**改参数只是改它自己的字段**。
        """
        assembly = assemble(seed=SEED, observability_enabled=False)
        loop = HeadlessLoop(assembly)
        loop.start()
        before: list[tuple[str, int, int, int]] = []
        for _ in range(60):
            loop.run_frame()
            before.append(_four_items(loop.snapshot()))

        adapter = KeyboardInput(submit=lambda _type: None, publish=lambda _type, _payload: None)
        adapter.set_das_ms(10)
        adapter.set_arr_ms(5)

        after: list[tuple[str, int, int, int]] = []
        for _ in range(60):
            loop.run_frame()
            after.append(_four_items(loop.snapshot()))

        assert before == after, "改 DAS / ARR 改了内核状态——手感参数不该触发状态变更"

    def test_changing_the_parameters_dispatches_no_command(self) -> None:
        """第二面：**改参数不派任何命令**（NFR-8）。

        **"改参数"不是"让它生效"**——连发要等下一次按下。
        """
        dispatched: list[str] = []
        adapter = KeyboardInput(
            submit=dispatched.append,
            publish=lambda _type, _payload: None,
        )
        adapter.set_das_ms(10)
        adapter.set_arr_ms(5)
        assert dispatched == [], "改参数顺手派了命令"
        assert adapter.das_ms == 10
        assert adapter.arr_ms == 5

    def test_the_repeat_timing_follows_the_parameters(self) -> None:
        """第三面：**连发节奏真的按 DAS / ARR 走**。

        **两个时刻是手抄的**：DAS 时刻（150ms）第一次连发 · ARR 时刻（33ms）之后才
        再连发。**这条守的是"参数真的起作用"**——前两条只说"改参数不改别的"。
        """
        dispatched: list[str] = []
        adapter = KeyboardInput(
            submit=dispatched.append,
            publish=lambda _type, _payload: None,
            das_ms=150,
            arr_ms=33,
        )
        adapter.handle_key("left", "down")
        assert dispatched == ["moveLeft"]
        # **按住 149ms（不到 DAS）→ 不连发**。
        adapter.poll(149)
        assert dispatched == ["moveLeft"], "不到 DAS 就连发了"
        # **按住 150ms（= DAS）→ 第一次连发**。
        adapter.poll(150)
        assert dispatched == ["moveLeft", "moveLeft"], "到了 DAS 没连发"
        # **距上次 32ms（< ARR 33ms）→ 不连发**。
        adapter.poll(182)
        assert dispatched == ["moveLeft", "moveLeft"], "不到 ARR 就连发了"
        # **距上次 33ms（= ARR）→ 连发**。
        adapter.poll(183)
        assert dispatched == ["moveLeft", "moveLeft", "moveLeft"], "到了 ARR 没连发"


# ---------------------------------------------------------------------------
# G4-5 · AI 与人类同通道
# ---------------------------------------------------------------------------


class Test_m4_ai_same_command_channel:
    """`G4-5`：**AI 与人类走完全相同的命令通道**（FR-8.5 · M4-D5）。"""

    def test_the_kernel_does_not_branch_on_the_source(self) -> None:
        """第一面：**内核源码里没有 `source ==` 的分支**（FR-8.5 · M4-D5）。

        **`source` 只作元数据透传**——内核分不出命令是谁发的。
        """
        kernel_source = (SRC_ROOT / "kernel" / "core.py").read_text(encoding="utf-8")
        offenders = re.findall(r'source\s*==\s*["\']', kernel_source)
        assert not offenders, f"内核里出现了按 source 分支的代码：{offenders}"

    def test_the_same_commands_give_the_same_result_for_any_source(self) -> None:
        """第二面：**同一串命令换来源 → 逐帧四项全等**（AC-6）。

        **怎么比**：**同一串写死的命令**，分别用 `source="ai"` 与 `source="human"`
        派给两个内核 → **逐帧**四项全等。

        **为什么逐帧比而不是"最终态差不多"**：**最终态相等**可能掩盖了中间的差异。
        """
        ai = assemble(seed=SEED, observability_enabled=False)
        human = assemble(seed=SEED, observability_enabled=False)
        assert _run_frames(ai, 60, source="ai") == _run_frames(human, 60, source="human"), (
            "同一串命令换来源标记后结果不同——内核按 source 分支了（破 FR-8.5）"
        )

    def test_the_ai_dispatches_only_declared_commands(self) -> None:
        """第三面：**AI 只派它声明过的命令**（M3-D3 的延续）。

        **尤其不许有 `writeCells`**——AI 的产出是**命令**，不是棋盘上的格子。
        """
        from tetris_kernel.systems.ai import SYSTEM_MANIFEST

        allowed = set(SYSTEM_MANIFEST.commands)
        assert allowed <= set(COMMANDS), "AI 声明了契约里没有的命令"
        assert "writeCells" not in allowed, "AI 不许写棋盘"

    def test_the_ai_actually_plays_and_scores(self) -> None:
        """第四面：**AI 真的在玩**（分数不为零）。

        **"派过命令"不等于"会玩"**——**消行数与分数是它真的在堆方块的结果**。
        """
        assembly = assemble(
            seed=SEED,
            observability_enabled=False,
            systems=("ai",),
            system_catalog=system_catalog(),
            history_limit=200_000,
        )
        loop = HeadlessLoop(assembly)
        loop.start()
        kernel = assembly.kernel
        for _ in range(1200):
            loop.run_frame()
            if kernel.phase == "game_over":
                break
        snapshot = kernel.snapshot()
        assert snapshot.lines > 0, f"AI 一行都没消（分数 {snapshot.score}）——它没在玩"
        assert snapshot.score > 0, "AI 的分数是零——它没在玩"


# ---------------------------------------------------------------------------
# G4-6 · 变体落在 variants/ 独立目录，不碰冻结区
# ---------------------------------------------------------------------------


class Test_m4_variants_directory_independence:
    """`G4-6`：**变体实现落在 `variants/` 独立目录 · 不碰冻结区**（架构 §2.3）。"""

    def test_the_variant_code_lives_in_its_own_directory(self) -> None:
        """第一面：**变体实现住在 `variants/`**（独立目录）。

        **两个变体各在自己的包里**（`variants/gravity/` · `variants/growth/`）——
        **不是一个包塞两套玩法**（那样两套玩法会互相看见对方的私有结构）。
        """
        assert (VARIANTS_DIR / "gravity").is_dir(), "变体 A 不在 variants/ 下"
        assert (VARIANTS_DIR / "growth").is_dir(), "变体 B 不在 variants/ 下"
        assert (VARIANTS_DIR / "gravity" / "rules.py").is_file(), "变体 A 的规则集不在自己的包里"
        assert (VARIANTS_DIR / "growth" / "system.py").is_file(), "变体 B 的系统不在自己的包里"

    def test_the_variant_layer_imports_nothing_forbidden(self) -> None:
        """第二面：**变体层禁 import 内核 / 总线 / 适配器 / 宿主**（架构 §2.3）。

        **禁止契约是传递性的**——变体连 `systems.context` 都不能 import（那条链通向
        总线与内核）。

        **为什么用 AST 而不是文本匹配**：源码的 **docstring 里会出现这些词**（变体
        自己在解释「我不许 import 内核」）——**文本匹配会把注释当成代码**。
        """
        forbidden = ("kernel", "bus", "adapters", "host")
        offenders: list[str] = []
        for path in sorted(VARIANTS_DIR.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("tetris_kernel."):
                            parts = alias.name.split(".")
                            if len(parts) > 1 and parts[1] in forbidden:
                                offenders.append(f"{path.name}: import {alias.name}")
                    continue
                # **相对导入也要看**（`from ...kernel.snapshot import X` → 模块名是
                # `...kernel.snapshot`）→ **去掉空串后看第一段**。
                parts = [part for part in module.split(".") if part]
                if parts and parts[0] in forbidden:
                    offenders.append(f"{path.name}: from {module}")
        assert offenders == [], f"变体层 import 了不该 import 的东西：{offenders}"

    def test_the_variant_rule_set_matches_the_frozen_signatures(self) -> None:
        """第三面：**变体的规则集满足九条冻结签名**（参数名与个数逐条相等）。

        **签名不合约的规则集装不进内核**——**门在这里当场拦住**，不等玩到一半才炸。
        """
        from tetris_kernel.rules.interface import signature_mismatches

        assert signature_mismatches(gravity_rule_set("right")) == ()


# ---------------------------------------------------------------------------
# G4-7 · 契约面零变更
# ---------------------------------------------------------------------------


class Test_m4_contract_surface_unchanged:
    """`G4-7`：**契约面零变更**（事件 / 命令 / 拒绝原因 / 快照 / 规则签名）。"""

    def test_the_counts_match_the_hand_copied_numbers(self) -> None:
        """第一面：**条数逐条等于手抄的期望值**。

        **期望值从需求 §8 抄的**——**不是从 `contracts/` 读出来的**（那就是自证）。
        """
        assert len(EVENT_TYPES) == EXPECTED_EVENT_COUNT
        assert len(COMMANDS) == EXPECTED_COMMAND_COUNT
        assert len(REJECT_REASONS) == EXPECTED_REJECT_REASON_COUNT
        assert len(SNAPSHOT_FIELDS) == EXPECTED_SNAPSHOT_FIELD_COUNT
        assert len(BOARD_FIELDS) == EXPECTED_BOARD_FIELD_COUNT
        assert len(RULE_SIGNATURES) == EXPECTED_RULE_SIGNATURE_COUNT

    def test_the_baseline_has_no_changed_key(self) -> None:
        """第二面：**契约基线的既有键一个都没变**（新增键放行）。

        **跑的是工具本身**——它比的是"基线文件"与"当前声明"。
        """
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "contract_baseline.py")],
            cwd=str(REPO_ROOT),
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, (
            "契约基线校验失败——既有键变了或消失了：\n"
            f"{result.stdout.decode('utf-8', 'replace')}\n"
            f"{result.stderr.decode('utf-8', 'replace')}"
        )

    def test_the_frozen_baseline_is_green_too(self) -> None:
        """第三面：**冻结区基线也是绿的**（M4 全程不刷它）。

        **M4-D1**：「不刷就是成绩」——**刷过一次就说明有人动过冻结区**。
        """
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "check_frozen_baseline.py")],
            cwd=str(REPO_ROOT),
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, "冻结区基线不一致"


# ---------------------------------------------------------------------------
# G4-8 · 扩展指纹随变体状态变化
# ---------------------------------------------------------------------------


class Test_m4_extension_fingerprint_tracks_variant_state:
    """`G4-8`：**扩展槽与扩展指纹随变体状态变化**（架构 §5.4）。"""

    def test_the_fingerprint_follows_the_variant(self) -> None:
        """第一面：**变体状态变 → 扩展指纹变**（G4-8）。

        **变体 B 的晶体数变 → 扩展槽变 → 扩展指纹变**。**结构指纹是另一回事**。
        """
        assembly = assemble(seed=7, systems=("growth",), system_catalog=system_catalog())
        loop = HeadlessLoop(assembly)
        loop.start()
        fingerprints: list[str] = []
        counts: list[int] = []
        for _ in range(MAX_FRAMES):
            loop.run_frame()
            snapshot = loop.snapshot()
            fingerprints.append(snapshot.extensionFingerprint)
            counts.append(int(dict(snapshot.extensions).get("growth.crystalCount", 0)))

        assert len(set(fingerprints)) > 1, "扩展指纹从头到尾没变——扩展槽没被算进去"
        assert counts[-1] > 0, "变体状态压根没变——这条用例什么也没证明"
        # **扩展指纹变的那几个时刻，晶体数也变了**（两者是同一件事的两面）。
        changed = [
            index
            for index in range(1, len(fingerprints))
            if fingerprints[index] != fingerprints[index - 1]
        ]
        assert changed, "扩展指纹没变过"
        assert all(counts[index] != counts[index - 1] for index in changed)

    def test_the_two_hashes_are_different_hashes(self) -> None:
        """第二面：**结构指纹与扩展指纹不是同一个哈希**（架构 §5.4）。

        **同一块棋盘，只改扩展槽** → **结构指纹不变 · 扩展指纹变**。
        """
        from tetris_kernel.host.extensions import apply_extensions

        class _Provider:
            """**假提供者**（只有 `extension_entries` —— 结构化类型）。"""

            def __init__(self, value: int) -> None:
                self._value = value

            def extension_entries(self) -> tuple[tuple[str, int], ...]:
                return (("x", self._value),)

        assembly = assemble(seed=3)
        kernel = assembly.kernel
        apply_extensions(kernel, {"a": _Provider(1)})
        first = kernel.snapshot()
        apply_extensions(kernel, {"a": _Provider(2)})
        second = kernel.snapshot()
        assert first.board.structureFingerprint == second.board.structureFingerprint
        assert first.extensionFingerprint != second.extensionFingerprint

    def test_the_empty_slot_digest_is_stable(self) -> None:
        """第三面：**没有变体 → 扩展槽是空的 → 摘要恒等于手抄的那个值**。

        **恒等**（不是"每次算出来都一样"）——**空槽的摘要是内核那一份算法算的**。
        """
        assembly = assemble(seed=SEED, observability_enabled=False)
        loop = HeadlessLoop(assembly)
        loop.start()
        loop.run_frames(200)
        snapshot = loop.snapshot()
        assert snapshot.extensions == ()
        assert snapshot.extensionFingerprint == EXPECTED_EMPTY_EXTENSION_FINGERPRINT


# ---------------------------------------------------------------------------
# G4-9 · 变体零污染
# ---------------------------------------------------------------------------


class Test_m4_variant_zero_pollution:
    """`G4-9`：**变体零污染**（默认路径没被 M4 动过）。"""

    def test_the_default_path_is_unchanged(self) -> None:
        """第一面：**默认路径的前三帧指纹 = 手抄的期望值**。

        **M4 加了变体 · 渲染器 · 输入适配器 · AI**——**默认装配一个都不装** →
        **内核逐帧行为一个字节都没变**。

        **期望值是手抄的**（`EXPECTED_DEFAULT_FINGERPRINTS`）——**不是"跑一遍再拿它
        当期望"**（那就是自证）。
        """
        assembly = assemble(seed=7, observability_enabled=False)
        loop = HeadlessLoop(assembly)
        loop.start()
        actual: list[str] = []
        for _ in range(3):
            loop.run_frame()
            actual.append(loop.snapshot().board.structureFingerprint)
        assert tuple(actual) == EXPECTED_DEFAULT_FINGERPRINTS

    def test_the_new_capabilities_are_not_installed_by_default(self) -> None:
        """第二面：**M4 的新能力默认不进装配**。

        **默认装配装的是 M2/M3 那三个可观测系统**——**变体系统 · AI · 渲染器 ·
        输入适配器一个都不装**（**装哪些是宿主的决定** · FR-9.1）。
        """
        assembly = assemble(seed=SEED)
        assert assembly.registry is None, "默认装配不该建系统注册表——没有系统要装"
        installed = {
            type(system).__name__
            for system in assembly.observable_systems  # type: ignore[attr-defined]
        }
        assert installed == {"LogSystem", "MetricsSystem", "RecordingSystem"}, (
            f"默认装配的系统清单变了：{sorted(installed)}"
        )

    def test_turning_off_the_renderers_changes_nothing(self) -> None:
        """第三面：**关掉渲染器与输入适配器 → 内核逐帧全等**。

        **多渲染器同挂互不影响**（FR-9.4）——**纯订阅者不许改变内核**。
        """
        plain = assemble(seed=SEED, observability_enabled=False)
        loaded = assemble(seed=SEED, observability_enabled=False)
        first = TerminalRenderer(rule_set=STANDARD_RULESET, width=10, height=22, hidden_rows=2)
        second = TerminalRenderer(rule_set=STANDARD_RULESET, width=10, height=22, hidden_rows=2)
        loaded.bus.subscribe("*", first.on_event)
        loaded.bus.subscribe("*", second.on_event)
        assert _run_frames(plain, 60) == _run_frames(loaded, 60), "渲染器改变了内核的行为"

    def test_the_variant_system_is_not_installed_by_default(self) -> None:
        """第四面：**变体系统默认不装** → **扩展槽是空的**。

        **装了变体 B 的那一局扩展槽有晶体数**；**没装的那一局扩展槽是空的**——
        **默认路径没被变体污染**。
        """
        assembly = assemble(seed=SEED, observability_enabled=False)
        loop = HeadlessLoop(assembly)
        loop.start()
        loop.run_frames(200)
        snapshot = loop.snapshot()
        assert snapshot.extensions == (), "默认装配的扩展槽不空——变体污染了默认路径"
        assert snapshot.extensionFingerprint == EXPECTED_EMPTY_EXTENSION_FINGERPRINT

    def test_the_variant_identifier_is_not_the_rule_set_identifier(self) -> None:
        """第五面：**变体标识 ≠ 规则集标识**（ADR-090）。

        **变体 B 的规则集就是标准的**——**玩法差异来自它挂的那个系统**，不是来自
        规则集。**把两者混成一个字段，变体 B 就没法登记**。
        """
        register_builtin_variants()
        from tetris_kernel.host.variants import variant_of

        growth = variant_of("growth")
        assert growth is not None
        assert growth.name == "growth"
        assert growth.ruleId == "standard"
        assert growth.systems == ("growth",)
        assert "gravity-right" in RULE_SETS, "变体 A 的规则集没进接线表"

    def test_the_recording_header_carries_the_variant(self) -> None:
        """第六面：**录像头带变体标识**——**光有规则集装不回去那一局**。

        **扩展槽会是空的 → 第五项必然不等** → **报错看不出原因**。
        """
        recording = _play_variant_game()
        header = json.loads(recording_to_jsonl(recording).splitlines()[0])
        assert header["variant"] == "growth"
        assert header["rule"] == "standard"
        assert header["version"] == "v2"
