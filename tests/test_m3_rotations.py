"""旋转统计系统测试（M3-S6 · AC-3 · `G3-1`）。

覆盖 plan-M3.md S6 的验证方案：

1. **计数**：喂 N 条 `piece:rotated` → 计数为 N；
2. **口径**：**受阻的旋转不算**（`blocked=True` 不计）——**反向验证**：
   把计数改成「受阻也计」→ 这条立刻红；
3. **落盘**：注入 sink → 计数落盘成功；**且 `systems/rotations/` 里没有
   `open(`**（AST 扫源码，不看文档字符串）；
4. **一个文件都不碰**：临时目录里不多一个文件；
5. **普通订阅者**：`view_provider` 一次都没被调用；
6. **声明**：订阅集 = `("piece:rotated",)` · `needsSink=True` · 产出者代号；
7. **冻结区基线同步**：内核文件与基线逐字节全等（**注意**：这守的是「改动
   与基线同步」，不是「M3 没改内核」——M3 的换规则集入口**确实改过**内核，
   基线已随之刷新 · M3 审查报告 L2）。

**单测不起内核**：整份文件只用一条**不带视图**的总线喂假事件流。
"""

from __future__ import annotations

import ast
import json
import locale
import subprocess
import sys
from pathlib import Path

from tetris_kernel.bus.bus import EventBus
from tetris_kernel.contracts.events import EVENT_TYPES, PRODUCER_LABELS
from tetris_kernel.contracts.systems import check_manifest_types
from tetris_kernel.kernel.drafts import EventDraft, make_draft
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.systems.context import CommandChannel, InstallContext
from tetris_kernel.systems.rotations import system as rotations_module
from tetris_kernel.systems.rotations.manifest import SYSTEM_MANIFEST, create_system
from tetris_kernel.systems.rotations.system import PIECE_ROTATED_EVENT, RotationsSystem

#: 仓库根目录（`tests/` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]


def _rotated(blocked: bool = False, frame: int = 1) -> EventDraft:
    """造一条**合法**的 `piece:rotated` 草稿（负载字段与契约逐条相等）。"""
    return make_draft(
        PIECE_ROTATED_EVENT,
        (("fromRotation", 0), ("toRotation", 1), ("direction", "cw"), ("blocked", blocked)),
        frame,
    )


def _bus() -> EventBus[object]:
    """一条**不带视图**的总线——旋转统计不该看视图（M3-D3）。"""
    return EventBus[object]()


def _counting_bus(calls: list[int]) -> EventBus[object]:
    """一条**带计数视图**的总线——`view_provider` 每被调一次就记一笔。"""

    def provider() -> object:
        calls.append(1)
        return object()

    return EventBus[object](view_provider=provider)


# ---------------------------------------------------------------------------
# 计数
# ---------------------------------------------------------------------------


def test_every_successful_rotation_is_counted() -> None:
    """喂 N 条成功的旋转 → 计数为 N。"""
    bus = _bus()
    system = RotationsSystem(bus)
    bus.subscribe(PIECE_ROTATED_EVENT, system.on_event)

    for _ in range(5):
        bus.publish(_rotated())

    assert system.count == 5


def test_a_blocked_rotation_is_not_counted() -> None:
    """**口径**：**受阻的旋转不算**（`blocked=True` 不计）。

    **反向验证**：把计数改成「受阻也计」→ 这条立刻红
    （plan-M3 S6 验证方案第 5 条 · 口径不写死的后果）。
    """
    bus = _bus()
    system = RotationsSystem(bus)
    bus.subscribe(PIECE_ROTATED_EVENT, system.on_event)

    bus.publish(_rotated(blocked=False))
    bus.publish(_rotated(blocked=True))
    bus.publish(_rotated(blocked=False))

    assert system.count == 2, "受阻的旋转被算进去了"
    assert system.skipped == 1, "受阻的旋转连「发生过」都没记下来"


def test_other_events_are_ignored() -> None:
    """只数 `piece:rotated`——别的事件一律不看。"""
    bus = _bus()
    system = RotationsSystem(bus)
    bus.subscribe("*", system.on_event)

    bus.publish(
        make_draft(
            "piece:locked",
            (
                ("pieceType", "T"),
                ("rotation", 0),
                ("x", 3),
                ("y", 17),
                ("cells", ((3, 17),)),
                ("tSpin", "none"),
                ("cause", "ground"),
            ),
            1,
        )
    )

    assert system.count == 0


def test_the_system_never_touches_the_kernel_state() -> None:
    """普通订阅者：**没有写内核状态的路径**（M3-D3）。"""
    calls: list[int] = []
    bus = _counting_bus(calls)
    system = RotationsSystem(bus)
    bus.subscribe(PIECE_ROTATED_EVENT, system.on_event)

    bus.publish(_rotated())

    assert calls == [], "旋转统计系统取了内核视图——它必须是纯订阅者"


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------


def test_the_count_reaches_the_sink() -> None:
    """产出**经 sink 落盘**（M3-D5）：sink 收到的是**字节**。"""
    captured: list[bytes] = []
    bus = _bus()
    system = RotationsSystem(bus, captured.append)
    bus.subscribe(PIECE_ROTATED_EVENT, system.on_event)

    bus.publish(_rotated(frame=3))
    bus.publish(_rotated(frame=4))

    assert len(captured) == 2, "计数没有交给 sink"
    records = [json.loads(line.decode("utf-8")) for line in captured]
    assert [record["rotations"] for record in records] == [1, 2]
    assert [record["frame"] for record in records] == [3, 4]


def test_the_system_never_touches_the_filesystem(tmp_path: Path) -> None:
    """落盘只走 sink：**一个文件都不碰**（路径是宿主的事）。"""
    before = set(tmp_path.rglob("*"))
    captured: list[bytes] = []
    bus = _bus()
    system = RotationsSystem(bus, captured.append)
    bus.subscribe(PIECE_ROTATED_EVENT, system.on_event)

    bus.publish(_rotated())

    assert set(tmp_path.rglob("*")) == before, "旋转统计系统碰了文件系统"
    assert len(captured) == 1


def test_the_package_never_opens_a_file() -> None:
    """**它自己不许 `open()`**（M3-D5）。

    **用 AST 看代码，不看文档字符串**（文档里写着"`open()`"是说明，不是调用）。
    """
    package_dir = Path(rotations_module.__file__).parent
    files = sorted(package_dir.glob("*.py"))
    assert files, "systems/rotations/ 下没有源文件——扫描没扫到 ≠ 扫过没问题"
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "open", f"{path.name}：不许自己 open()"
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"open", "write_text", "write_bytes"}, f"{path.name}"


def test_the_package_does_not_import_the_filesystem() -> None:
    """系统层**不碰文件系统**（自己管路径 = 自己落盘 = 破了 M3-D5）。"""
    package_dir = Path(rotations_module.__file__).parent
    imported: set[str] = set()
    for path in sorted(package_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    roots = {name.split(".", 1)[0] for name in imported}
    assert not roots & {"pathlib", "os", "shutil", "io"}, (
        f"旋转统计系统碰了文件系统：{sorted(roots)}"
    )


# ---------------------------------------------------------------------------
# 声明
# ---------------------------------------------------------------------------


def test_the_manifest_declares_what_the_system_does() -> None:
    """声明**逐条等于**这个系统实际做的事。"""
    assert SYSTEM_MANIFEST.name == "rotations"
    assert SYSTEM_MANIFEST.version == "1"
    assert SYSTEM_MANIFEST.subscribes == (PIECE_ROTATED_EVENT,)
    assert SYSTEM_MANIFEST.commands == ()
    assert SYSTEM_MANIFEST.producer == "rotations"
    assert SYSTEM_MANIFEST.needsSink is True


def test_the_declaration_self_proves() -> None:
    """**自证**：声明里的事件 / 命令类型必须已登记（跨文件自证）。"""
    problems = check_manifest_types(
        SYSTEM_MANIFEST,
        known_events=EVENT_TYPES.keys(),
        known_commands=(),
        known_producers=PRODUCER_LABELS.keys(),
    )
    assert not problems, f"声明不自证：{problems}"


def test_the_factory_builds_a_system_without_the_kernel() -> None:
    """工厂**只拿总线**就能造出系统（上下文里没有内核 · M3-D3）。"""
    system = create_system(
        InstallContext(
            name="rotations",
            bus=EventBus[Snapshot](),
            commands=CommandChannel(lambda command: None),
            config=(),
        )
    )
    assert isinstance(system, RotationsSystem)


# ---------------------------------------------------------------------------
# 冻结区基线同步
# ---------------------------------------------------------------------------


def test_the_frozen_zone_matches_the_baseline() -> None:
    """**冻结区与基线逐文件哈希全等**（`G3-1`）。

    **这条断言到底断言什么**（M3 审查报告 L2 指出的命名误导）：它断言的是
    **「当前工作区的内核文件与 `kernel_hashes.json` 基线逐字节一致」**——
    **不是**「M3 没改过内核」。

    **M3 确实改过内核**（`kernel/core.py` · `kernel/commands.py`）——那是
    **M3 唯一一次冻结区破例**（ADR-061 的运行期换规则集入口），**基线已随之
    显式刷新**（S8 · `c81d0cc`）。**基线绿 = 改动与基线同步了**，不等于
    「M3 没改内核」。

    **为什么这条断言仍然有意义**：**它守的是「改动与基线同步」**——内核文件
    在**基线刷新之后**再被改动（哪怕只是一个字节），这条立刻红。**M4 的
    `G4-1`** 靠它保证「变体落地时内核差异为空」。

    跑的是**工具本身**（子进程调 `python tools/check_frozen_baseline.py`）。
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "check_frozen_baseline.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    encoding = locale.getpreferredencoding(False)
    message = result.stdout.decode(encoding, errors="replace") + result.stderr.decode(
        encoding, errors="replace"
    )
    assert result.returncode == 0, f"冻结区变了：\n{message}"
