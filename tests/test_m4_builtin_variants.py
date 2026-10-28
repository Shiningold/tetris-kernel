"""**内建变体的接线**用例（M4-S3 · 宿主层）。

守三件事：

- **登记是幂等的**（CLI 与测试都要能安全地反复调）；
- **清单真的有内容**（`tetris variants` 列出三个轴）；
- **不在册的变体明确报错**（**不拿标准玩法顶替**）。

外加一条**依赖方向的源码级断言**：**变体层不许 import 系统层**——
`variants/manifests.py` 曾经为了 `SystemFactory` 的类型 import 了
`systems/context.py`，而它 transitively 依赖总线与内核 → **横向依赖**
（架构 §2.3）。**这条断言把那扇门守住。**
"""

from __future__ import annotations

import ast
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from tetris_kernel.contracts.rules import RuleContext
from tetris_kernel.host.builtin_variants import (
    builtin_variant_specs,
    register_builtin_variants,
)
from tetris_kernel.host.cli import main
from tetris_kernel.host.variants import variant_of
from tetris_kernel.rules.interface import signature_mismatches

_VARIANTS_DIR = Path(__file__).resolve().parents[1] / "src" / "tetris_kernel" / "variants"


# ---------------------------------------------------------------------------
# 登记
# ---------------------------------------------------------------------------


def test_register_builtin_variants_is_idempotent() -> None:
    """**登记可以反复调**——已登记的跳过，不报错。"""
    first = register_builtin_variants()
    second = register_builtin_variants()
    assert set(first) == {"gravity-down", "gravity-right", "gravity-left"}
    assert second == ()


def test_builtin_variants_are_registered_with_matching_rule_ids() -> None:
    """**变体标识与规则集标识一一对应**（变体 A 的玩法差异全来自规则集）。"""
    register_builtin_variants()
    for spec in builtin_variant_specs():
        manifest = variant_of(spec.name)
        assert manifest is not None
        assert manifest.ruleId == spec.ruleId
        assert manifest.systems == ()
        assert signature_mismatches(manifest.ruleSet) == ()


def test_builtin_variant_rule_sets_use_distinct_axes() -> None:
    """**三份实例是三个不同的轴**（**"切轴" = 换规则集**）。"""
    ctx = RuleContext(frame=0, level=1, piecesPlaced=0, axis="down", stepMs=16)
    axes = {spec.ruleSet.gravity_vector(ctx) for spec in builtin_variant_specs()}
    assert axes == {(0, 1), (1, 0), (-1, 0)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_variants_lists_builtin_variants() -> None:
    """`tetris variants` **列出内建变体**。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert main(["variants"]) == 0
    output = buffer.getvalue()
    assert "gravity-right" in output
    assert "gravity-left" in output


def test_cli_variants_json_is_machine_readable() -> None:
    """`--json` 的清单**可机读**（名字 · 版本 · 规则集 · 附带系统）。"""
    import json

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert main(["variants", "--json"]) == 0
    payload = json.loads(buffer.getvalue())
    names = {item["name"] for item in payload["variants"]}
    assert {"gravity-down", "gravity-right", "gravity-left"} <= names
    for item in payload["variants"]:
        assert item["ruleId"]
        assert item["systems"] == []


def test_cli_sim_runs_a_variant_game() -> None:
    """`tetris sim --variant` **真跑一局变体**（跑到顶出并出结算）。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert main(["sim", "--variant", "gravity-right", "--until", "game-over"]) == 0
    output = buffer.getvalue()
    assert "phase=game_over" in output


def test_cli_sim_with_unknown_variant_fails_loudly() -> None:
    """**不在册的变体明确报错**（退出码 2）——**不拿标准玩法顶替**。"""
    stderr = io.StringIO()
    with pytest.raises(SystemExit) as raised:
        with redirect_stderr(stderr):
            main(["sim", "--variant", "no-such-variant"])
    assert raised.value.code == 2
    assert "未登记的变体" in stderr.getvalue()


# ---------------------------------------------------------------------------
# 依赖方向（源码级）
# ---------------------------------------------------------------------------


def test_variants_layer_does_not_import_systems_layer() -> None:
    """**变体层不 import 系统层**（横向依赖 · 架构 §2.3）。

    **`variants/manifests.py` 曾经 import `systems.context`**（只为
    `SystemFactory` 的类型）→ 那条链 transitively 依赖总线与内核 →
    **`lint-imports` 当场红**。**这条断言把那扇门守住。**
    """
    offenders: list[str] = []
    for path in sorted(_VARIANTS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("tetris_kernel.systems"):
                    offenders.append(f"{path.name}: from {module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("tetris_kernel.systems"):
                        offenders.append(f"{path.name}: import {alias.name}")
    assert offenders == []
