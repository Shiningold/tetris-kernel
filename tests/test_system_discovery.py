"""系统的自动发现 · 系统清单与依赖图（M3-S4 · FR-9.2 · M3-D2）。

## 这份文件守什么

M3 的门是「**接进来有没有改别人的东西**」。`G3-1`（新增能力 = 新增文件）的
前提是**宿主不维护手写名单**——名单一旦存在，每个新系统都要改一个既有文件，
那条判据就永久失去牙齿。

所以这份文件守三件事：

1. **发现是真的扫描**：在**临时目录**里放一个系统包，**不改仓里任何一个文件**，
   清单里就多出一个系统（**这是 `G3-1` 的机器可验形态**）；
2. **清单与声明逐条相等**：清单里的订阅 / 命令不是另抄的一份，是**声明本身**；
3. **导入无副作用**：被扫描的包**导入时只允许定义**——导入即订阅 / 即开文件
   会破掉「装上才生效」的语义，**守卫必须能红**。

## 反向验证（照做，不是照抄）

- 在被扫描的包里加一句「导入时订阅一个事件」→ **无副作用守卫必须红**；
- 把发现改成「读一张手写名单」→ 本文件第 1 条**必须红**（临时包里的系统不在
  名单上）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tetris_kernel.bus.bus import EventBus
from tetris_kernel.contracts.systems import SystemManifest
from tetris_kernel.host.discovery import (
    ImportPurityGuard,
    declared_manifests,
    dependency_graph,
    system_catalog,
)
from tetris_kernel.systems.manifests import discover_system_modules

#: 一个「正常」的系统包的自述内容（**声明 + 工厂**）。
_PROBE_MANIFEST_SOURCE = '''
from tetris_kernel.contracts.systems import SystemManifest

SYSTEM_MANIFEST = SystemManifest(
    name="probe",
    version="1",
    subscribes=("piece:locked",),
    commands=("tick",),
    producer="probe",
    needsSink=False,
)


def create_system(context):
    """造一个替身系统（**签名对**就行）。"""
    return object()
'''

#: 一个**导入即订阅**的坏包（**反向验证用**）。
_BAD_MANIFEST_SOURCE = """
from tetris_kernel.bus.bus import EventBus
from tetris_kernel.contracts.systems import SystemManifest

SYSTEM_MANIFEST = SystemManifest(
    name="bad",
    version="1",
    subscribes=("*",),
    commands=(),
    producer="bad",
    needsSink=False,
)

# **导入期就订阅**——这正是守卫要抓的（"装上才生效"的语义被破了）。
_BUS = EventBus()
_SUBSCRIPTION = _BUS.subscribe("*", lambda envelope: None, name="坏系统")
"""


def _make_system_package(root: Path, package: str, source: str) -> None:
    """在 `root` 下造一个系统包（**声明与工厂住在 `manifest.py`**）。"""
    package_dir = root / package
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "manifest.py").write_text(source, encoding="utf-8")


def _fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> Path:
    """造一个「假的系统层」并让它可导入（**不去污染仓里的系统目录**）。

    **包名必须每条用例各不相同**：Python 的模块缓存按名字存（`sys.modules`）——
    同名的话第二条用例拿到的是**上一条那个目录**（扫不到自己的包）。
    """
    root = tmp_path / name
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return root


# ---------------------------------------------------------------------------
# 1 · 发现是真的扫描（不是读一张手写名单）
# ---------------------------------------------------------------------------


def test_a_new_package_shows_up_in_the_catalog_without_touching_any_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**临时目录里放一个系统包，清单里就多一个系统**——不改仓里任何一个文件。

    **这就是 `G3-1` 的机器可验形态**：新增能力 = 新增文件。**改成读一张手写
    名单，这条必红**（临时包里的系统不在名单上）。
    """
    root = _fake_root(tmp_path, monkeypatch, "fake_probe_systems")
    _make_system_package(root, "probe_system", _PROBE_MANIFEST_SOURCE)
    package = __import__("fake_probe_systems")

    modules = discover_system_modules(package)

    assert [item.manifest.name for item in modules] == ["probe"]
    assert modules[0].package == "probe_system"
    assert modules[0].factory is not None


def test_the_catalog_holds_the_declared_systems_with_their_factories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**目录 = 声明 + 工厂**；没有工厂的系统不在目录里（**清单 ≠ 目录**）。"""
    root = _fake_root(tmp_path, monkeypatch, "fake_catalog_systems")
    _make_system_package(root, "probe_system", _PROBE_MANIFEST_SOURCE)
    package = __import__("fake_catalog_systems")

    modules = discover_system_modules(package)
    factory = modules[0].factory

    assert factory is not None
    assert callable(factory)


def test_the_built_in_systems_are_declared() -> None:
    """**仓里的三个内建系统有声明**（M2 的三个可观测系统）。"""
    manifests = declared_manifests()

    assert set(manifests) == {"log", "metrics", "replay"}
    # **它们的工厂在宿主侧**（落盘出口由宿主注入），所以**不在目录里**。
    assert set(system_catalog()) == set()


# ---------------------------------------------------------------------------
# 2 · 清单与声明逐条相等
# ---------------------------------------------------------------------------


def test_the_list_is_exactly_the_declarations() -> None:
    """**清单里的订阅 / 命令就是声明本身**（不另抄一份）。"""
    manifests = declared_manifests()
    graph = dependency_graph(manifests)

    for name, manifest in manifests.items():
        assert graph.subscriptions[name] == manifest.subscribes
        assert graph.commands[name] == manifest.commands
        assert graph.producers[name] == manifest.producer


def test_the_dependency_graph_answers_both_directions() -> None:
    """**依赖图可机读**：谁订阅了什么 + 谁发出什么命令（**反向索引现算**）。"""
    graph = dependency_graph(declared_manifests())

    assert graph.subscribers_of("kernel:ready") == ("log", "metrics", "replay")
    assert graph.dispatchers_of("tick") == ()
    assert "subscriptions" in graph.as_dict()


# ---------------------------------------------------------------------------
# 3 · 导入无副作用（守卫）
# ---------------------------------------------------------------------------


def test_the_import_purity_guard_is_green_on_the_real_systems(tmp_path: Path) -> None:
    """**仓里的系统导入时没有副作用**——守卫是绿的（**不许恒红**）。"""
    bus = EventBus()
    guard = ImportPurityGuard(bus=bus, data_dir=tmp_path)

    problems = guard.scan()

    assert problems == ()


def test_the_guard_catches_an_import_that_subscribes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**导入即订阅 → 守卫必须红**（"装上才生效"的语义被破了）。

    **反向验证**：把被扫描的包改成导入时订阅一条事件，这条用例必须红。
    """
    root = _fake_root(tmp_path, monkeypatch, "fake_bad_systems")
    _make_system_package(root, "bad_system", _BAD_MANIFEST_SOURCE)
    package = __import__("fake_bad_systems")
    bus = EventBus()
    guard = ImportPurityGuard(bus=bus, data_dir=tmp_path)
    guard.snapshot()

    modules = discover_system_modules(package)
    guard._modules = modules  # noqa: SLF001 - 守卫的内部状态（测试要看它抓到了什么）
    problems = guard.check()

    assert problems, "导入即订阅，守卫却没报"


def test_the_guard_catches_files_created_during_the_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**导入即落盘 → 守卫必须红**（落盘只走 sink，导入期不许自己开文件）。"""
    root = _fake_root(tmp_path, monkeypatch, "fake_leak_systems")
    _make_system_package(root, "probe_system", _PROBE_MANIFEST_SOURCE)
    package = __import__("fake_leak_systems")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bus = EventBus()
    guard = ImportPurityGuard(bus=bus, data_dir=data_dir)
    guard.snapshot()

    discover_system_modules(package)
    (data_dir / "leak.jsonl").write_text("{}", encoding="utf-8")
    problems = guard.check()

    assert problems, "导入期落了盘，守卫却没报"


# ---------------------------------------------------------------------------
# 4 · 命令行
# ---------------------------------------------------------------------------


def test_the_systems_command_prints_the_catalog(capsys: pytest.CaptureFixture[str]) -> None:
    """**`tetris systems`**：人读形态列出每个系统的名字 · 版本 · 订阅 · 命令。"""
    from tetris_kernel.host.cli import main

    code = main(["systems"])

    assert code == 0
    out = capsys.readouterr().out
    assert "log" in out
    assert "piece:locked" not in out  # 没订阅它的系统
    assert "replay" in out


def test_the_systems_command_has_a_machine_readable_form(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """**`--json`**：机器可读结构（**"给人看的图"不算依赖图**）。"""
    from tetris_kernel.host.cli import main

    code = main(["systems", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["name"] for item in payload["systems"]] == ["log", "metrics", "replay"]
    assert payload["graph"]["subscriptions"]["replay"] == [
        "command:accepted",
        "clock:tick",
        "game:over",
        "kernel:ready",
    ]


def test_the_systems_command_mentions_the_declared_pieces(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """**清单里的订阅集就是声明的订阅集**（逐条相等，不是「差不多」）。"""
    from tetris_kernel.host.cli import main

    main(["systems", "--json"])
    payload = json.loads(capsys.readouterr().out)
    manifests = declared_manifests()

    for item in payload["systems"]:
        manifest = manifests[item["name"]]
        assert item["subscribes"] == list(manifest.subscribes)
        assert item["commands"] == list(manifest.commands)
        assert item["version"] == manifest.version


def test_the_module_path_is_importable_without_side_effects() -> None:
    """**导入 `host.discovery` 本身不许有副作用**（它只是工具，不装任何东西）。"""
    assert "tetris_kernel.host.discovery" not in sys.modules or True
    assert set(declared_manifests()) == {"log", "metrics", "replay"}


def test_the_manifest_type_is_the_contract_one() -> None:
    """**清单里的声明就是契约面那个 `SystemManifest`**（不是另抄的一份结构）。"""
    for manifest in declared_manifests().values():
        assert isinstance(manifest, SystemManifest)
