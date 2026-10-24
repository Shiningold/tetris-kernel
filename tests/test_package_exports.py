"""包级导出面的一致性（批次 A 集成者收口时加）。

`contracts/__init__.py` 早有一条 `__all__` 与实际导出逐条相等的断言
（`tests/test_contracts_declarations.py`）。批次 A 起 `kernel/` `rules/` `host/`
`bus/` 也各有包级 `__init__.py`，**同一形状只允许一处权威定义**——所以这条
纪律跟着扩过来，而不是只守契约层。

漏一个导出名不报错，只是 `from tetris_kernel.kernel import X` 到时候才失败；
多一个导出名不报错，只是包级 API 里混进了不该公开的东西。两条都当场变红。

**子模块名从实际子模块推导，不抄名单**——抄一份就是"规则说有约束、机检看不见"
的温床：新增一个子模块却忘了把名字加进名单，守卫就会把子模块本身当成漏掉的
导出名，红得莫名其妙（批次 B 收口时踩过一次）。
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

#: 有包级 `__init__.py` 的子包。契约层另有一条同形断言（在
#: `tests/test_contracts_declarations.py`），那一份还额外管**公共叶子**纪律。
_PACKAGES: tuple[str, ...] = (
    "tetris_kernel.bus",
    "tetris_kernel.host",
    "tetris_kernel.kernel",
    "tetris_kernel.rules",
)


def _submodule_names(package: object) -> set[str]:
    """包目录下**实际存在**的子模块名（不抄名单）。"""
    paths = getattr(package, "__path__", None)
    if paths is None:
        return set()
    return {info.name for info in pkgutil.iter_modules(list(paths))}


@pytest.mark.parametrize("package_name", _PACKAGES)
def test_package_all_matches_actual_exports(package_name: str) -> None:
    """`__all__` 与包上的实际公开名**一一对应**。"""
    package = importlib.import_module(package_name)
    exported = list(package.__all__)

    assert len(exported) == len(set(exported)), f"{package_name}: __all__ 里有重复名"
    for name in exported:
        assert hasattr(package, name), f"{package_name}: __all__ 声明了 {name}，包上却没有"

    submodule_names = _submodule_names(package)
    public = {
        name for name in dir(package) if not name.startswith("_") and name not in submodule_names
    }
    assert public == set(exported), f"{package_name}: 包级公开名与 __all__ 不一致"
