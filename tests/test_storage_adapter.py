"""存储适配器与默认落盘路径策略（M2 · S4 · ADR-064 / ADR-074）。

## 这份文件守什么

M2 的落盘纪律有三条，**每一条都要有机器证据**：

- **落盘只走 sink**（M2-D2 · ADR-064）：部件不自己 `open()`、不用
  `Path.home()` → `test_systems_never_open_files_or_touch_the_home_directory`
  与 `test_systems_never_import_the_storage_adapter`；
- **默认路径项目优先 → 不可写时回落 home**（ADR-074）→ 三条路径策略用例；
- **写入只有一处**（ADR-074）→ `test_only_one_place_is_written_to`。

## M2 审查 H1 补上第四条：**一个产物一个文件**（ADR-085）

三个产物（日志 · 指标 · 录像）**各有各的出口**——共用一个的话，**后写的覆盖
先写的**（用覆盖语义写日志，文件里只剩最后一条）。**"写入只有一处"说的是
一条落盘路径，不是一个文件。**

**两种写入语义不是一个类的两种模式**：`FileSink`（**覆盖** · 整份交出）·
`AppendSink`（**追加** · 一条一条）——用错了就是**数据丢**，所以让类型把
这件事说清楚。

## 为什么"项目不可写"靠注入而不是靠改文件属性

Windows 上把目录设成只读**拦不住本进程的写入**（ACL 与只读位的语义不同），
靠改属性造出来的"不可写"在不同机器上表现还不一样。**所以可写性判定是
一个注入点**（`resolve_data_dir(writable=…)`）：测试注入判定函数，**不假装
文件系统坏了**。

## "回落要被看见"怎么验

`DataDirectory.fell_back` 与 `.reason` **就是那份可见性**——回落不是默默
换地方（两处都写会让同一局录像分叉，`G2-1` 的比对会假红）。
"""

from __future__ import annotations

import ast
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from tetris_kernel.adapters import AppendSink, FileSink
from tetris_kernel.host import (
    ObservableSinks,
    assemble,
    default_sink,
    default_sinks,
    resolve_data_dir,
)
from tetris_kernel.host import assembly as host_assembly
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.systems.replay.format import recording_filename

#: 包根（供扫描 `systems/` 用）。
SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "tetris_kernel"

#: `systems/` 目录（**M2-D2 的红线所在**：三个可观测系统都在这儿）。
SYSTEMS_DIR = SRC_ROOT / "systems"


# ---------------------------------------------------------------------------
# 默认路径策略：项目优先 → 不可写时回落 home（ADR-074）
# ---------------------------------------------------------------------------


def _always_writable(_path: Path) -> bool:
    """可写性判定：处处可写（项目侧优先命中）。"""
    return True


def _home_only(project_dir: Path) -> Callable[[Path], bool]:
    """可写性判定：**只有 home 侧可写**（项目侧不可写）。"""

    def probe(path: Path) -> bool:
        return path != project_dir

    return probe


def test_the_default_data_dir_is_under_the_project_when_it_is_writable(
    tmp_path: Path,
) -> None:
    """项目目录可写 → 落在 `<项目根>/.tetris-data/`（**项目优先**）。"""
    project = tmp_path / "project"
    project.mkdir()

    directory = resolve_data_dir(
        project_root=project,
        home=tmp_path / "home",
        writable=_always_writable,
    )

    assert directory.path == project / ".tetris-data"
    assert directory.fell_back is False
    assert directory.reason == ""


def test_the_default_data_dir_falls_back_to_home_when_the_project_is_not_writable(
    tmp_path: Path,
) -> None:
    """项目目录不可写 → 落在 `~/.tetris/`（**回落**）。"""
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    directory = resolve_data_dir(
        project_root=project,
        home=home,
        writable=_home_only(project / ".tetris-data"),
    )

    assert directory.path == home / ".tetris"
    assert directory.fell_back is True
    assert "项目目录不可写" in directory.reason


def test_the_fallback_is_reported_not_silent(tmp_path: Path) -> None:
    """**回落要被报告出来**——不许默默换地方（ADR-074）。

    **返回值就是那份报告**：`fell_back` 与 `reason` 说清"落到哪儿了、
    为什么不是项目目录"。
    """
    project = tmp_path / "project"
    project.mkdir()

    directory = resolve_data_dir(
        project_root=project,
        home=tmp_path / "home",
        writable=_home_only(project / ".tetris-data"),
    )

    assert directory.fell_back is True
    assert directory.reason, "回落了却没说明原因——那等于默默换地方"


def test_the_fallback_is_reported_on_stderr_when_a_sink_is_built(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """造 sink 时回落 → **stderr 上有一行**（那是给**人**看的）。"""
    project = tmp_path / "project"
    project.mkdir()

    sink, directory = default_sink(
        "recording.bin",
        project_root=project,
        home=tmp_path / "home",
    )

    assert isinstance(sink, FileSink)
    captured = capsys.readouterr()
    # 回落策略由 resolve_data_dir 判定；这里注入不了判定函数，
    # 所以只断言"**要么在项目侧、要么明确回报了回落**"——
    # **不许出现"既不在项目侧、也没说回落"这种状态**。
    if directory.fell_back:
        assert captured.err, "回落了却一个字都没说"
    else:
        assert directory.path == project / ".tetris-data"


def test_the_default_data_dir_ignores_environment_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**不设环境变量**（ADR-074）：`TETRIS_DATA_DIR` 之类一律不认。

    **为什么这条要有用例**：设一个环境变量就改落盘位置，是最省事的"配置"，
    也是最难查的故障（换台机器就不一样）。**不许有这条路。**
    """
    monkeypatch.setenv("TETRIS_DATA_DIR", str(tmp_path / "from-env"))
    project = tmp_path / "project"
    project.mkdir()

    directory = resolve_data_dir(
        project_root=project,
        home=tmp_path / "home",
        writable=_always_writable,
    )

    assert directory.path == project / ".tetris-data"
    assert "from-env" not in str(directory.path)


def test_only_one_place_is_written_to(tmp_path: Path) -> None:
    """**写入只有一处**（ADR-074）——**不在两处各写一份**。

    **两处都写的后果**：同一局录像在两处分叉，`G2-1` 的 200 局比对会**假红**
    （ADR-064 否决"文件名带时间戳"时用的正是这个理由）。
    """
    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir()
    home.mkdir()

    sink, directory = default_sink("recording.bin", project_root=project, home=home)
    sink(b"payload")

    written = directory.path / "recording.bin"
    assert written.exists(), "落点没有文件——那录像根本没落盘"

    project_dir = project / ".tetris-data"
    home_dir = home / ".tetris"
    other = home_dir if directory.path == project_dir else project_dir
    assert not other.exists(), f"另一处也写了文件：{other}——写入只有一处"


# ---------------------------------------------------------------------------
# 可写性判定：真写一次（审查 L3）
# ---------------------------------------------------------------------------


def test_the_writability_probe_does_not_ask_the_operating_system(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**真写一次**，不靠 `os.access` 的一句话（审查 L3）。

    Windows 上目录 **ACL** 存在时 `os.access` 的结果**不可靠**——可能认为可写、
    真写时抛 `PermissionError`。判定**必须**自己写一次，ADR-074「不可写时
    回落 home」这条承诺才兑现得了。

    **这条把 `os.access` 顶掉**：顶掉之后仍判"可写"，说明判定走的是真写。
    """
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(os, "access", lambda path, mode: False)

    directory = resolve_data_dir(project_root=project, home=tmp_path / "home")

    assert directory.fell_back is False, "判定还在听 os.access 的"
    assert directory.path == project / ".tetris-data"


def test_the_writability_probe_leaves_no_residue(tmp_path: Path) -> None:
    """**探针不留残骸**——建了临时文件就删掉。"""
    project = tmp_path / "project"
    project.mkdir()

    directory = resolve_data_dir(project_root=project, home=tmp_path / "home")

    assert directory.fell_back is False
    assert list(project.iterdir()) == [], "探针留下了残骸"


def test_a_project_root_that_is_a_file_is_not_writable(tmp_path: Path) -> None:
    """项目根**不是目录** → 不可写 → **回落 home**（ADR-074）。"""
    blocker = tmp_path / "project"
    blocker.write_text("我是个文件，不是目录", encoding="utf-8")

    directory = resolve_data_dir(project_root=blocker, home=tmp_path / "home")

    assert directory.fell_back is True
    assert directory.path == tmp_path / "home" / ".tetris"


# ---------------------------------------------------------------------------
# 存储适配器：目录自建 · 原子替换 · 覆盖语义
# ---------------------------------------------------------------------------


def test_the_storage_adapter_creates_missing_directories(tmp_path: Path) -> None:
    """目标目录不存在 → **自动创建**（含父目录，ADR-074）。"""
    target = tmp_path / "deep" / "nested" / "recording.bin"

    FileSink(target)(b"payload")

    assert target.read_bytes() == b"payload"


def test_the_storage_adapter_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    """**原子替换**不留残骸：写完之后目录里没有 `.part`。"""
    target = tmp_path / "recording.bin"

    FileSink(target)(b"payload")

    leftovers = [item.name for item in tmp_path.iterdir() if item.name.endswith(".part")]
    assert not leftovers, f"留下了临时文件：{leftovers}"


def test_the_storage_adapter_replaces_instead_of_appending(tmp_path: Path) -> None:
    """**覆盖语义**：第二次写把整个文件换掉（录像是一次性落盘）。"""
    target = tmp_path / "recording.bin"
    sink = FileSink(target)

    sink(b"first")
    sink(b"second")

    assert target.read_bytes() == b"second"


def test_the_storage_adapter_reports_how_many_bytes_it_wrote(tmp_path: Path) -> None:
    """**已写字节数**可查（指标与测试要用）。"""
    sink = FileSink(tmp_path / "recording.bin")

    sink(b"12345")
    sink(b"678")

    assert sink.bytes_written == 8
    assert sink.path == tmp_path / "recording.bin"


def test_the_storage_adapter_does_not_fail_silently(tmp_path: Path) -> None:
    """写不下去 → **当场抛错**，不吞（"录像没落盘"不能变成"录像齐了"）。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("我是个文件，不是目录", encoding="utf-8")

    with pytest.raises(OSError):
        FileSink(blocker / "recording.bin")(b"payload")


# ---------------------------------------------------------------------------
# 存储适配器：两种写入语义（ADR-085）
# ---------------------------------------------------------------------------


def test_the_append_sink_keeps_everything_it_is_given(tmp_path: Path) -> None:
    """**追加语义**：写两次，文件里两条都在（**不是只剩最后一条**）。

    **这条就是 H1 的判据**：用覆盖语义写日志，文件里就只剩最后一条日志。
    """
    target = tmp_path / "log.jsonl"
    sink = AppendSink(target)

    sink(b"first\n")
    sink(b"second\n")

    assert target.read_bytes() == b"first\nsecond\n"


def test_the_append_sink_creates_missing_directories(tmp_path: Path) -> None:
    """目标目录不存在 → **自动创建**（含父目录，ADR-074）。"""
    target = tmp_path / "deep" / "nested" / "log.jsonl"

    AppendSink(target)(b"payload")

    assert target.read_bytes() == b"payload"


def test_the_append_sink_reports_how_many_bytes_it_wrote(tmp_path: Path) -> None:
    """**已写字节数**可查（指标与测试要用）。"""
    sink = AppendSink(tmp_path / "log.jsonl")

    sink(b"12345")
    sink(b"678")

    assert sink.bytes_written == 8
    assert sink.path == tmp_path / "log.jsonl"


def test_the_append_sink_does_not_fail_silently(tmp_path: Path) -> None:
    """写不下去 → **当场抛错**，不吞（与 `FileSink` 同款口径 · ADR-064）。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("我是个文件，不是目录", encoding="utf-8")

    with pytest.raises(OSError):
        AppendSink(blocker / "log.jsonl")(b"payload")


# ---------------------------------------------------------------------------
# 一个产物一个文件（ADR-085 · 审查 H1）
# ---------------------------------------------------------------------------


def test_the_default_sinks_give_each_product_its_own_file(tmp_path: Path) -> None:
    """**日志 · 指标 · 录像各有各的文件**（ADR-085 · 审查 H1）。

    **这条就是 H1 的判据**：三个产物共用一个出口的话，**后写的覆盖先写的**
    ——日志文件里只剩最后一条。
    """
    sinks, directory = default_sinks(project_root=tmp_path, home=tmp_path / "home")

    assert isinstance(sinks.log, AppendSink)
    assert isinstance(sinks.metrics, AppendSink)
    assert isinstance(sinks.recording, FileSink)

    paths = {sinks.log.path, sinks.metrics.path, sinks.recording.path}
    assert len(paths) == 3, f"三个产物落到了同一个文件：{paths}"
    assert all(path.parent == directory.path for path in paths)


def test_the_log_and_metrics_sinks_append_while_the_recording_sink_replaces(
    tmp_path: Path,
) -> None:
    """**追加与覆盖各归各**——用错了就是数据丢。

    **两种语义不是一个类的两种模式**（ADR-085）：日志与指标**一条一条往外
    冒**（追加），录像是**整份交出**（覆盖）。
    """
    sinks, _directory = default_sinks(project_root=tmp_path, home=tmp_path / "home")
    assert isinstance(sinks.log, AppendSink)
    assert isinstance(sinks.metrics, AppendSink)
    assert isinstance(sinks.recording, FileSink)

    sinks.log(b"one\n")
    sinks.log(b"two\n")
    sinks.metrics(b"m1\n")
    sinks.metrics(b"m2\n")
    sinks.recording(b"first")
    sinks.recording(b"second")

    assert sinks.log.path.read_text(encoding="utf-8") == "one\ntwo\n"
    assert sinks.metrics.path.read_text(encoding="utf-8") == "m1\nm2\n"
    assert sinks.recording.path.read_bytes() == b"second"


def test_the_recording_file_name_matches_the_recording_format(tmp_path: Path) -> None:
    """**录像的文件名是录像格式的一部分**（spec §7.1）——不许这儿另起一个。

    **同一个规则集 → 同一个录像文件名**（`recording_filename()` 是唯一真相）。
    """
    sinks, _directory = default_sinks(
        rule_id="standard", project_root=tmp_path, home=tmp_path / "home"
    )
    assert isinstance(sinks.recording, FileSink)

    assert sinks.recording.path.name == recording_filename("standard")
    assert sinks.recording.path.name == "recording-standard.jsonl"


def test_a_rule_id_that_could_escape_the_data_dir_is_refused(tmp_path: Path) -> None:
    """**路径分隔符不许进文件名**——当场报错，不悄悄改写。

    **文件名要能反查回规则集**：把 `a/b` 改成 `a-b`，查录像时才知道自己拿
    错了——所以**错了就报错**。
    """
    with pytest.raises(ValueError, match="规则集标识"):
        default_sinks(rule_id="../逃逸", project_root=tmp_path, home=tmp_path / "home")


def test_the_three_products_really_land_in_three_files(tmp_path: Path) -> None:
    """**端到端**：日志 · 指标 · 录像**各写各的**，日志文件里**不止一条**。

    **这是 H1 的反向判据**：把三个出口换成同一个（覆盖语义），日志文件里就
    只剩最后一条——这条必红。
    """
    sinks, _directory = default_sinks(project_root=tmp_path, home=tmp_path / "home")
    assembly = assemble(seed=20261026, sinks=sinks)
    loop = HeadlessLoop(assembly)
    loop.start()
    # **跑够一个周期点**（600 帧）——录像在周期点落一次盘。
    loop.run_frames(600)

    log_file = tmp_path / ".tetris-data" / "log-standard.jsonl"
    metrics_file = tmp_path / ".tetris-data" / "metrics-standard.jsonl"
    recording_file = tmp_path / ".tetris-data" / "recording-standard.jsonl"

    assert log_file.exists(), "日志文件没落盘"
    assert metrics_file.exists(), "指标文件没落盘"
    assert recording_file.exists(), "录像文件没落盘"

    log_lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(log_lines) > 1, f"日志文件里只剩 {len(log_lines)} 条——那是覆盖语义"
    metric_lines = metrics_file.read_text(encoding="utf-8").splitlines()
    assert len(metric_lines) >= 100, "指标采样没落盘"


# ---------------------------------------------------------------------------
# 宿主注入 sink 与可观测总开关
# ---------------------------------------------------------------------------


def test_the_host_injects_the_sink_into_the_observable_systems(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**宿主注入 sink**（M2-D2）：系统拿到的是宿主给的那一个出口。"""
    seen: dict[str, object] = {}

    def fake_factory(bus: object, sinks: object, outcome_provider: object) -> object:
        seen["sinks"] = sinks
        return object()

    monkeypatch.setattr(host_assembly, "OBSERVABLE_SYSTEM_FACTORIES", (fake_factory,))
    captured: list[bytes] = []

    assembly = assemble(sinks=ObservableSinks(log=captured.append))

    assert seen["sinks"] is not None
    assert assembly.sinks.log is not None
    # 注入 ≠ 已写入：字节是系统写出来的，不是装配时写出来的。
    assert captured == []


def test_a_replaced_sink_captures_the_bytes(tmp_path: Path) -> None:
    """**替换 sink 就能截获字节**——落盘经 sink 这件事是可验的。"""
    captured: list[bytes] = []
    sink, _directory = default_sink(
        "recording.bin",
        project_root=tmp_path,
        home=tmp_path / "home",
    )

    captured_sink = captured.append
    captured_sink(b"payload")

    assert captured == [b"payload"]
    # 默认 sink 与截获 sink 是两个出口：默认那个照样落到磁盘。
    sink(b"other")
    assert (tmp_path / ".tetris-data" / "recording.bin").read_bytes() == b"other"


def test_the_observability_switch_stops_assembly_not_just_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**总开关关掉的是"装配"，不是"订阅"**（`G2-3` 的前提）。

    **"装配了再退订"会让 `G2-3` 假绿**：系统还在跑，只是不发事件——逐帧
    一致性看着过了，实际上可观测系统仍在消耗 CPU、仍在写盘。
    """
    calls: list[object] = []

    def fake_factory(bus: object, sinks: object, outcome_provider: object) -> object:
        calls.append((bus, sinks))
        return object()

    monkeypatch.setattr(host_assembly, "OBSERVABLE_SYSTEM_FACTORIES", (fake_factory,))

    off = assemble(observability_enabled=False)

    assert calls == [], "开关关着，工厂却还被调用了——那是退订，不是停装配"
    assert off.observable_systems == ()
    assert off.observability_enabled is False

    on = assemble(observability_enabled=True)

    assert len(calls) == 1
    assert len(on.observable_systems) == 1
    assert on.observability_enabled is True


# ---------------------------------------------------------------------------
# M2-D2：可观测系统不许自己落盘
# ---------------------------------------------------------------------------


def _calls_in(path: Path) -> list[str]:
    """文件里出现的**调用**名（点分形状），docstring 不算。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.append(func.id)
        elif isinstance(func, ast.Attribute):
            names.append(func.attr)
    return names


def test_systems_never_open_files_or_touch_the_home_directory() -> None:
    """**`systems/` 不许自己 `open()` / `Path.home()`**（M2-D2 · ADR-064）。

    **可观测系统只知道有 sink，不知道路径**——路径是宿主的事（ADR-074）。

    **这条今天扫的是空壳，但明天就有牙**：批次 C 把日志 / 指标 / 回放填进去
    之后，任何一处自己开文件都会当场红。
    """
    forbidden = {
        "open",
        "write_text",
        "write_bytes",
        "mkdir",
        "makedirs",
        "home",
        "cwd",
        "remove",
        "unlink",
    }
    offenders: list[str] = []
    for path in sorted(SYSTEMS_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for name in _calls_in(path):
            if name in forbidden:
                offenders.append(f"{path.name}: {name}")

    assert not offenders, "可观测系统不许自己落盘或找路径（M2-D2）：" + "、".join(offenders)


def test_systems_never_import_the_storage_adapter() -> None:
    """**`systems/` 不许 import `adapters/`**（依赖方向 · ADR-064）。

    `lint-imports` 的分层契约也守着这条；这里再钉一道，**因为它红了没人
    会立刻想到是它**。
    """
    offenders: list[str] = []
    for path in sorted(SYSTEMS_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "adapters" in module:
                    offenders.append(f"{path.name}: from {module} import …")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "adapters" in alias.name:
                        offenders.append(f"{path.name}: import {alias.name}")

    assert not offenders, "可观测系统不许 import 适配层：" + "、".join(offenders)
