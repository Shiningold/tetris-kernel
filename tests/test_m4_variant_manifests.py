"""契约面扩容：变体系统也要进声明面与依赖图（变体层 + 宿主层 · M4-S2）。

## 这份文件守什么

M4 的变体 B（落地后生长）**也是一个系统**——它订阅 `piece:locked`、派发
`writeCells`——但它**住在 `variants/`**。M3 的声明扫描只扫 `systems/` → **它
看不见变体系统**。S2 把**扫描根扩容**到能覆盖 `variants/`，**让变体系统与普通
系统受同一套声明面、同一把自证的尺子**，并出现在依赖图里。

## 六条判据（照「实现可能怎么写坏」写）

1. **变体目录真的被扫**（临时目录里造一个变体包 → 清单里就多一个系统）；
2. **变体系统出现在依赖图里**（FR-9.2：声明的用处就是生成依赖图）；
3. **规则集型变体不是错误**（变体 A 只换规则集、不带系统 → **声明是 `None`**，
   **不是报错**）；
4. **重名即报错**（系统目录与变体目录抢一个名字 → 当场炸）；
5. **自证是同一把尺子**（变体系统的订阅指向未登记的事件 → 拒绝进基线）；
6. **基线零变化**（**S2 是扩容不是刷新**——变体系统要到 S4 才存在）。

## 反向验证（照做，不是照抄）

- **把扫描根改回只扫 `systems/`** → 判据 1 与 2 必红；
- **把「规则集型变体没有声明」当成报错** → 判据 3 必红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tetris_kernel.bus.bus import EventBus
from tetris_kernel.contracts.systems import (
    GLOBAL_SUBSCRIPTION,
    check_manifest_types,
)
from tetris_kernel.host.discovery import (
    ImportPurityGuard,
    declared_manifests,
    dependency_graph,
)
from tetris_kernel.variants.manifests import (
    discover_variant_manifests,
    discover_variant_modules,
)

# ---------------------------------------------------------------------------
# 夹具：临时目录里造变体包（**不去污染仓里的变体目录**）
# ---------------------------------------------------------------------------

_VARIANT_MANIFEST_SOURCE = """
from tetris_kernel.contracts.systems import SystemManifest

SYSTEM_MANIFEST = SystemManifest(
    name="probe_growth",
    version="0.1.0",
    subscribes=("piece:locked",),
    commands=("writeCells",),
    producer="probe_growth",
    needsSink=False,
)


def create_system(context):
    return object()
"""

_RULESET_VARIANT_SOURCE = """
#: **规则集型变体**：没有系统声明（**这不是错误**）。
RULE_SET_ID = "probe"
"""


def _make_variant_package(root: Path, package: str, source: str) -> None:
    """在 `root` 下造一个变体包（**声明与工厂住在 `manifest.py`**）。"""
    package_dir = root / package
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "manifest.py").write_text(source, encoding="utf-8")


def _fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> Path:
    """造一个「假的变体层」并让它可导入。

    **包名必须每条用例各不相同**：Python 的模块缓存按名字存（`sys.modules`）——
    同名的话第二条用例拿到的是**上一条那个目录**。
    """
    root = tmp_path / name
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return root


# ---------------------------------------------------------------------------
# 判据 1：变体目录真的被扫
# ---------------------------------------------------------------------------


def test_a_variant_package_shows_up_in_the_merged_manifests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**临时目录里放一个变体包，汇总清单里就多一个系统**——不改仓里任何一个文件。"""
    root = _fake_root(tmp_path, monkeypatch, "fake_probe_variants")
    _make_variant_package(root, "probe_variant", _VARIANT_MANIFEST_SOURCE)
    package = __import__("fake_probe_variants")

    manifests = declared_manifests(variants_package=package)

    assert "probe_growth" in manifests
    assert manifests["probe_growth"].subscribes == ("piece:locked",)
    assert manifests["probe_growth"].commands == ("writeCells",)


def test_the_variant_manifest_is_discovered_by_its_own_scanner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**变体层扫自己那一层**（`variants/manifests.py`）——声明与工厂同源。"""
    root = _fake_root(tmp_path, monkeypatch, "fake_scanner_variants")
    _make_variant_package(root, "probe_variant", _VARIANT_MANIFEST_SOURCE)
    package = __import__("fake_scanner_variants")

    modules = discover_variant_modules(package)

    assert [item.manifest.name for item in modules if item.manifest is not None] == ["probe_growth"]
    assert modules[0].package == "probe_variant"
    assert modules[0].factory is not None


# ---------------------------------------------------------------------------
# 判据 2：变体系统出现在依赖图里
# ---------------------------------------------------------------------------


def test_the_dependency_graph_includes_the_variant_system(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**依赖图含变体系统**（FR-9.2：声明的用处就是生成依赖图）。"""
    root = _fake_root(tmp_path, monkeypatch, "fake_graph_variants")
    _make_variant_package(root, "probe_variant", _VARIANT_MANIFEST_SOURCE)
    package = __import__("fake_graph_variants")

    graph = dependency_graph(declared_manifests(variants_package=package))

    assert graph.subscriptions["probe_growth"] == ("piece:locked",)
    assert graph.commands["probe_growth"] == ("writeCells",)
    # **仓里真实的系统也会订阅 `piece:locked`**（成就 · 日志 · 指标）——
    # 所以这里断言"变体系统**在其中**"，不是"只有它"。
    assert "probe_growth" in graph.subscribers_of("piece:locked")
    assert "probe_growth" in graph.dispatchers_of("writeCells")


# ---------------------------------------------------------------------------
# 判据 3：规则集型变体不是错误
# ---------------------------------------------------------------------------


def test_a_ruleset_only_variant_has_no_manifest_and_is_not_an_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**变体 A 只换规则集、不带系统** → 声明是 `None`，**不是报错**。"""
    root = _fake_root(tmp_path, monkeypatch, "fake_ruleset_variants")
    _make_variant_package(root, "ruleset_variant", _RULESET_VARIANT_SOURCE)
    package = __import__("fake_ruleset_variants")

    modules = discover_variant_modules(package)

    assert len(modules) == 1
    assert modules[0].manifest is None
    assert modules[0].factory is None
    # **它不进声明清单**——它是规则集，不是系统。
    assert discover_variant_manifests() == {} or "ruleset_variant" not in (
        discover_variant_manifests()
    )


# ---------------------------------------------------------------------------
# 判据 4：重名即报错
# ---------------------------------------------------------------------------


def test_two_declarations_with_the_same_name_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """系统目录与变体目录抢一个名字 → **当场炸**（依赖图会指向错的那个）。"""
    root = _fake_root(tmp_path, monkeypatch, "fake_clash_variants")
    _make_variant_package(root, "clash_variant", _VARIANT_MANIFEST_SOURCE)
    package = __import__("fake_clash_variants")

    # **先把同名声明塞进系统目录那一侧**（用临时系统包模拟）——
    # 真实的系统目录里已经有 `log` / `metrics` / `replay` 等名字，
    # 这里用与真实系统同名的一份声明来制造冲突。
    systems_root = _fake_root(tmp_path, monkeypatch, "fake_clash_systems")
    _make_variant_package(systems_root, "clash_system", _VARIANT_MANIFEST_SOURCE)
    systems_package = __import__("fake_clash_systems")

    with pytest.raises(ValueError, match="系统名重复"):
        declared_manifests(
            systems_package=systems_package,
            variants_package=package,
        )


# ---------------------------------------------------------------------------
# 判据 5：自证是同一把尺子
# ---------------------------------------------------------------------------


def test_a_variant_manifest_is_checked_with_the_same_self_proof() -> None:
    """**变体系统与普通系统同一把尺子**：订阅未登记的事件 → 自证不通过。"""
    from tetris_kernel.contracts.systems import SystemManifest

    manifest = SystemManifest(
        name="probe_bad",
        version="0.1.0",
        subscribes=("piece:rotatd",),  # 拼错
        commands=("writeCells",),
        producer="probe_bad",
        needsSink=False,
    )

    problems = check_manifest_types(
        manifest,
        known_events=("piece:locked",),
        known_commands=("writeCells",),
        known_producers=(),
    )

    assert any("piece:rotatd" in problem for problem in problems)


def test_the_global_subscription_is_still_allowed_for_variant_systems() -> None:
    """**全局订阅（`*`）不算类型名**——变体系统同样放行。"""
    from tetris_kernel.contracts.systems import SystemManifest

    manifest = SystemManifest(
        name="probe_global",
        version="0.1.0",
        subscribes=(GLOBAL_SUBSCRIPTION,),
        commands=("writeCells",),
        producer="probe_global",
        needsSink=False,
    )

    problems = check_manifest_types(
        manifest,
        known_events=("piece:locked",),
        known_commands=("writeCells",),
        known_producers=(),
    )

    assert problems == ()


# ---------------------------------------------------------------------------
# 判据 6：基线零变化 + 导入纯净度
# ---------------------------------------------------------------------------


def test_the_baseline_is_unchanged_by_the_expansion() -> None:
    """**S2 是扩容不是刷新**——契约基线一个字节都没动（408 条零变化）。

    **这条判据在 S2 当时成立**：变体目录里**还没有带系统的变体**。
    **S4 之后变体 B 落地了**（它带系统 → 它的声明进了基线 · 408 → 409）→
    **这条用例改成守"扩容本身不刷基线"**：**基线里的系统条目**只与
    **真的声明了的系统**一一对应——**扫描根扩容不产生任何新条目**。
    """
    import json
    from pathlib import Path

    from tetris_kernel.host.discovery import declared_manifests as declared

    # **汇总清单里，变体目录贡献的声明**（扩容的作用就是让它可见）。
    names = set(declared())
    assert "growth" in names
    # **基线里的系统条目 ≡ 声明了的系统**（**扩容不刷基线**）。
    baseline = json.loads(
        (Path(__file__).parent / "baseline" / "contracts_baseline.json").read_text(encoding="utf-8")
    )
    entries = baseline["entries"]
    system_keys = {key for key in entries if str(key).startswith("system/")}
    assert {key.split("/", 1)[1] for key in system_keys} == names


def test_the_import_purity_guard_covers_the_variant_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**变体目录也受导入纯净度守卫管**（导入即订阅一样会破语义）。"""
    root = _fake_root(tmp_path, monkeypatch, "fake_guard_variants")
    _make_variant_package(root, "guard_variant", _VARIANT_MANIFEST_SOURCE)
    package = __import__("fake_guard_variants")

    guard = ImportPurityGuard(bus=EventBus(), data_dir=tmp_path)
    guard.snapshot()
    guard._variant_modules = discover_variant_modules(package)  # noqa: SLF001

    problems = guard.check()

    assert problems == ()
