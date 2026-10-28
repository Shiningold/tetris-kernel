"""扩展槽回写接线的用例（M4-S5 · spec-M4 §7.4 · 架构 §4.5 / §5.4）。

**守的是口径，不是实现细节**：

- **帧末合并**：变体的跨帧状态**由宿主合并进 `extensions`**（M4-D4）——
  **变体自己不许往内核写**（变体层连 import 内核都不行）；
- **只放标量**：容器 / 嵌套**当场拒**（架构 §5.4：复杂状态由变体自己持有，
  进槽的只是**摘要**）；
- **key 唯一**：重复即**结构错误并记错**，**不"去重取末值"**（架构 §5.4）；
- **key 的命名空间由宿主加**（`<提供者名>.<裸名>`）——两个变体的同名 key
  不互相顶掉；
- **扩展槽变更不另发事件**（S5 修订）：`state:changed` 的判据是
  `snapshot.version`，**扩展槽合并不改版本号** → **不发**；**扩展槽的观测面
  是快照**（`extensions` / `extensionFingerprint`）；
- **两个哈希不是一回事**：扩展槽变了 → **扩展指纹变** · **结构指纹不变**。

**反向用例**（"让扩展槽塞容器" / "让重复 key 静默去重" → 必红）在
`test_合并器拒绝容器值` 与 `test_合并器拒绝重复_key`。
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from tetris_kernel.host import assemble
from tetris_kernel.host.discovery import system_catalog
from tetris_kernel.host.extensions import (
    ExtensionMergeError,
    apply_extensions,
    merge_extension_entries,
    namespace_key,
    providers_of,
)
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.variants.extensions import ExtensionEntry, ExtensionProvider
from tetris_kernel.variants.growth import GrowthSystem

#: 扩展槽里**晶体数**的 key（**带命名空间** · 宿主拼的）。
_CRYSTAL_KEY = "growth.crystalCount"


class _Provider:
    """**假提供者**（只有 `extension_entries` —— 结构化类型，不 import 协议也行）。"""

    def __init__(self, entries: Iterable[ExtensionEntry]) -> None:
        self._entries = tuple(entries)

    def extension_entries(self) -> tuple[ExtensionEntry, ...]:
        return self._entries


class _Submitter:
    """**假命令通道**（变体 B 的系统要一条才能造出来）。"""

    def submit_cells(
        self,
        cells: tuple[tuple[int, int, int], ...],
        source: str = "system",
    ) -> None:
        """**什么都不做**（这些用例只关心扩展槽自述）。"""


def _growth_loop(seed: int = 7) -> HeadlessLoop:
    """装上变体 B 的系统的帧循环。"""
    assembly = assemble(seed=seed, systems=("growth",), system_catalog=system_catalog())
    loop = HeadlessLoop(assembly)
    loop.start()
    return loop


# ---------------------------------------------------------------------------
# 协议
# ---------------------------------------------------------------------------


def test_变体实现扩展槽自述面() -> None:
    """**变体自己说得出要放什么**（协议在变体层 · 宿主层靠鸭子类型认出它）。"""
    system = GrowthSystem(_Submitter())
    assert isinstance(system, ExtensionProvider)
    entries = system.extension_entries()
    assert entries == (("crystalCount", 0),)
    assert all(isinstance(value, (int, float, str, bool)) or value is None for _, value in entries)


def test_协议不要求实现方继承任何东西() -> None:
    """**结构化类型**：实现了同名方法就算数——**不必 import 协议模块**。"""
    provider = _Provider((("x", 1),))
    assert isinstance(provider, ExtensionProvider)
    # **没有这个方法的实例不是提供者**（不是错误 · 扩展条目是可选能力）。
    assert not isinstance(object(), ExtensionProvider)


# ---------------------------------------------------------------------------
# 合并器
# ---------------------------------------------------------------------------


def test_合并器加命名空间前缀() -> None:
    """**key 的命名空间由宿主加**（变体只给裸名）。"""
    merged = merge_extension_entries({"growth": _Provider((("crystalCount", 3),))})
    assert merged == (("growth.crystalCount", 3),)
    assert namespace_key("growth", "crystalCount") == "growth.crystalCount"


def test_合并器按_key_排序() -> None:
    """**合并结果按 key 排序**（同一组扩展、不同合并顺序 → 同一个快照）。"""
    merged = merge_extension_entries(
        {
            "beta": _Provider((("z", 1),)),
            "alpha": _Provider((("a", 2), ("b", 3))),
        }
    )
    assert [key for key, _ in merged] == ["alpha.a", "alpha.b", "beta.z"]


def test_合并器拒绝容器值() -> None:
    """**只放标量**——容器当场拒（架构 §5.4：禁止容器与嵌套）。"""
    with pytest.raises(ExtensionMergeError):
        merge_extension_entries({"growth": _Provider((("crystalCount", (1, 2)),))})
    with pytest.raises(ExtensionMergeError):
        merge_extension_entries({"growth": _Provider((("crystalCount", [1]),))})
    with pytest.raises(ExtensionMergeError):
        merge_extension_entries({"growth": _Provider((("crystalCount", {"a": 1}),))})


def test_合并器拒绝重复_key() -> None:
    """**key 重复即结构错误**——**不"去重取末值"**（架构 §5.4）。

    两种重复都要挡住：

    - **同一提供者**给了两个一样的裸名；
    - **两个提供者的裸名拼完撞了**（`a` + `b.c` 与 `a.b` + `c` → 都是 `a.b.c`）。
    """
    with pytest.raises(ExtensionMergeError):
        merge_extension_entries({"growth": _Provider((("x", 1), ("x", 2)))})
    with pytest.raises(ExtensionMergeError):
        merge_extension_entries(
            {
                "a": _Provider((("b.c", 1),)),
                "a.b": _Provider((("c", 2),)),
            }
        )


def test_合并器接受布尔与_none() -> None:
    """**布尔与 `None` 都是合法标量**（内核的 TLV 给它们单独的标签）。"""
    merged = merge_extension_entries(
        {"p": _Provider((("flag", True), ("empty", None), ("ratio", 0.5)))}
    )
    assert merged == (("p.empty", None), ("p.flag", True), ("p.ratio", 0.5))


def test_没有提供者就是空槽() -> None:
    """**没有提供者不是错误**（变体 A 只换规则集 → 扩展槽本来就是空的）。"""
    assert merge_extension_entries({}) == ()


def test_挑提供者靠鸭子类型() -> None:
    """**只有实现了 `extension_entries` 的实例才算提供者**。"""
    providers = providers_of({"growth": _Provider((("x", 1),)), "other": object()})
    assert list(providers) == ["growth"]


# ---------------------------------------------------------------------------
# 帧末接线
# ---------------------------------------------------------------------------


def test_帧末合并_变体状态变则扩展槽变() -> None:
    """**变体状态变 → 快照的 `extensions` 变 → 扩展指纹变**（G4-8）。"""
    loop = _growth_loop()
    seen: list[tuple[ExtensionEntry, ...]] = []
    for _ in range(4000):
        loop.run_frame()
        snapshot = loop.snapshot()
        if not seen or seen[-1] != snapshot.extensions:
            seen.append(snapshot.extensions)
    assert seen[0] == (("growth.crystalCount", 0),)
    assert len(seen) > 1
    assert seen[-1][0][0] == _CRYSTAL_KEY
    assert seen[-1][0][1] > 0


def test_两个哈希不是一回事() -> None:
    """**结构指纹是棋盘内容，扩展指纹是扩展槽摘要**——不是同一个哈希（G4-8）。"""
    assembly = assemble(seed=3)
    kernel = assembly.kernel
    apply_extensions(kernel, {"a": _Provider((("x", 1),))})
    first = kernel.snapshot()
    apply_extensions(kernel, {"a": _Provider((("x", 2),))})
    second = kernel.snapshot()
    assert first.board.structureFingerprint == second.board.structureFingerprint
    assert first.extensionFingerprint != second.extensionFingerprint


def test_扩展槽变更不另发事件() -> None:
    """**合并一条事件都不发**（S5 修订口径）。

    `state:changed` 的判据是 `snapshot.version`，**扩展槽合并不改版本号**
    （内核的 `_set_extensions` 不 `_touch()`）→ **不发**；**扩展槽的观测面是
    快照**。
    """
    assembly = assemble(seed=3)
    bus = assembly.bus
    events: list[str] = []
    bus.subscribe("*", lambda envelope: events.append(envelope.type))
    before_seq = bus.last_seq
    before_version = assembly.kernel.snapshot().version
    apply_extensions(assembly.kernel, {"a": _Provider((("x", 1),))})
    assert bus.last_seq == before_seq
    assert events == []
    assert assembly.kernel.snapshot().version == before_version


def test_没装变体时扩展槽是空的() -> None:
    """**标准玩法没有扩展条目**（扩展槽空 → 扩展指纹是空槽的摘要）。"""
    assembly = assemble(seed=3)
    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frames(200)
    snapshot = loop.snapshot()
    assert snapshot.extensions == ()
    assert snapshot.extensionFingerprint == assembly.kernel.snapshot().extensionFingerprint
