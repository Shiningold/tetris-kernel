"""包级导出面的一致性（批次 A 集成者收口时加）。

`contracts/__init__.py` 早有一条 `__all__` 与实际导出逐条相等的断言
（`tests/test_contracts_declarations.py`）。批次 A 起 `kernel/` `rules/` `host/`
也各有包级 `__init__.py`，**同一形状只允许一处权威定义**——所以这条纪律
跟着扩过来，而不是只守契约层。

漏一个导出名不报错，只是 `from tetris_kernel.kernel import X` 到时候才失败；
多一个导出名不报错，只是包级 API 里混进了不该公开的东西。两条都当场变红。
"""

from __future__ import annotations

import importlib

import pytest

#: 包名 → 该包的子模块名（子模块本身不是"公开名"，不算进 `__all__`）。
_PACKAGES: dict[str, frozenset[str]] = {
    "tetris_kernel.kernel": frozenset({"fingerprint", "rng", "snapshot"}),
    "tetris_kernel.rules": frozenset({"interface", "standard", "tables"}),
    "tetris_kernel.host": frozenset({"clock"}),
}


@pytest.mark.parametrize("package_name", sorted(_PACKAGES))
def test_package_all_matches_actual_exports(package_name: str) -> None:
    """`__all__` 与包上的实际公开名**一一对应**。"""
    package = importlib.import_module(package_name)
    exported = list(package.__all__)

    assert len(exported) == len(set(exported)), f"{package_name}: __all__ 里有重复名"
    for name in exported:
        assert hasattr(package, name), f"{package_name}: __all__ 声明了 {name}，包上却没有"

    submodule_names = _PACKAGES[package_name]
    public = {
        name for name in dir(package) if not name.startswith("_") and name not in submodule_names
    }
    assert public == set(exported), f"{package_name}: 包级公开名与 __all__ 不一致"
