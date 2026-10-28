"""主菜单与装配的用例（M6-S3 · `G6-4` · spec-M6 §7.4 / §7.5 · ADR-100）。

**守的是口径，不是实现细节**：

- **玩法 → 装配**（`G6-4`）——**三个玩法逐个断言**：**规则集对不对** ·
  **规则集标识对不对** · **附带系统对不对**；
- **难度 = 起始等级**（`G6-4`）——**用既有的 `setLevel` 命令落地**，**内核
  认它**（**等级真的变成选的那一档**）；
- **不认识的玩法明确报错**（**不拿标准玩法顶替**）；
- **装配是纯逻辑**（**不弹窗口** → **无头环境就能跑**）。
"""

from __future__ import annotations

import pytest

from tetris_kernel.host import assemble
from tetris_kernel.host.loop import HeadlessLoop
from tetris_kernel.host.menu import (
    MENU_DIFFICULTIES,
    MENU_VARIANTS,
    menu_choice,
    play_options,
    start_level_command,
)
from tetris_kernel.rules.standard import STANDARD_RULESET

# ---------------------------------------------------------------------------
# 菜单选项本身
# ---------------------------------------------------------------------------


def test_菜单是三个玩法三档难度() -> None:
    """**菜单项就那么多**（**玩法三个 · 难度三档**）。"""
    assert len(MENU_VARIANTS) == 3
    assert len(MENU_DIFFICULTIES) == 3
    assert [level for level, _label in MENU_DIFFICULTIES] == [1, 5, 10]


# ---------------------------------------------------------------------------
# G6-4 · 玩法 → 装配
# ---------------------------------------------------------------------------


def test_标准玩法的装配() -> None:
    """**标准玩法**：标准规则集 · 无附带系统 · **变体标识是 `None`**。"""
    options = play_options(None)
    assert options.rule_set is STANDARD_RULESET
    assert options.rule_id == "standard"
    assert options.systems == ()
    assert options.variant_id is None


def test_重力旋转的装配() -> None:
    """**重力旋转**：**变体 A 的规则集**（轴 = right）· **不挂系统**。"""
    options = play_options("gravity-right")
    assert options.rule_id == "gravity-right"
    assert options.systems == ()
    assert options.variant_id == "gravity-right"
    assert getattr(options.rule_set, "axis", None) == "right"


def test_落地生长的装配() -> None:
    """**落地生长**：**规则集就是标准的** · **差异来自挂的系统**。

    **这是"变体标识 ≠ 规则集标识"那条口径的兑现**——**规则集一个字不改**，
    玩法差异来自 `growth` 那个系统。
    """
    options = play_options("growth")
    assert options.rule_set is STANDARD_RULESET
    assert options.rule_id == "standard"
    assert options.systems == ("growth",)
    assert options.variant_id == "growth"


def test_三个玩法逐个装配得出来() -> None:
    """**菜单里点哪一项都装配得出来**（**逐个断言**）。"""
    for variant_id, _label in MENU_VARIANTS:
        options = play_options(variant_id)
        assert options.rule_id
        assert options.variant_id == variant_id


def test_不认识的玩法明确报错() -> None:
    """**不认识的玩法 → 明确报错**（**不拿标准玩法顶替**）。"""
    with pytest.raises(ValueError):
        play_options("no-such-variant")


# ---------------------------------------------------------------------------
# G6-4 · 难度 = 起始等级
# ---------------------------------------------------------------------------


def test_难度落到起始等级() -> None:
    """**难度 = 起始等级**（**用既有的 `setLevel`** · 内核认它）。"""
    assembly = assemble(seed=7, rule_set=STANDARD_RULESET)
    loop = HeadlessLoop(assembly)
    loop.start()
    assembly.kernel.submit(start_level_command(5))
    loop.run_frame()
    assert loop.snapshot().level == 5


def test_难度三档都落得下去() -> None:
    """**三档难度逐个落到起始等级**（**不是只测中间那档**）。"""
    for level, _label in MENU_DIFFICULTIES:
        assembly = assemble(seed=7, rule_set=STANDARD_RULESET)
        loop = HeadlessLoop(assembly)
        loop.start()
        assembly.kernel.submit(start_level_command(level))
        loop.run_frame()
        assert loop.snapshot().level == level


def test_等级越界不静默夹取() -> None:
    """**起始等级非法 → 报错**（**不静默夹到 1**）。"""
    with pytest.raises(ValueError):
        start_level_command(0)
    with pytest.raises(ValueError):
        menu_choice(None, 0)


# ---------------------------------------------------------------------------
# 选择本身
# ---------------------------------------------------------------------------


def test_菜单选择越界报错() -> None:
    """**选了不在菜单里的玩法 → 报错**。"""
    with pytest.raises(ValueError):
        menu_choice("no-such-variant", 5)
