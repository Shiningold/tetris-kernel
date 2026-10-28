"""变体登记面（宿主层 · M4-S1 · spec-M4 §7.1 · 架构 §4.5）。

## 这份文件守什么

M4 的正题是「**换掉整套玩法，内核一个字节都没动**」。而变体要能被换上去，
**先得有一张登记表**——**登记一次 = 一次接线**（变体不是插件，不走 M3 的自动
发现）。这份文件守的是**这张接线面的正确性**。

## 八条判据（照「实现可能怎么写坏」写，不是照「实现现在怎么写」写）

1. **登记一次，两处生效**：进变体表 **且** 把它的规则集接进 `RULE_SETS`
   ——**两处不会分叉**（一处登记 = 一处真相）。
2. **变体标识 ≠ 规则集标识**：变体 B（落地后生长）**用的就是标准规则集**——
   玩法差异来自它挂的系统。**把两者混成一个字段，变体 B 就没法登记**。
3. **一个标识只能指向一个规则集**：标识已登记但指向**另一个**规则集 → 当场报错
   （否则重放出来的四项必然不等，而报错看不出原因）。
4. **重名登记 → 报错**（标识是键，后登记的会悄悄顶掉先登记的）。
5. **不在册 → 没有**：`variant_of` / `variant_rule_set` 返回 `None`，
   `variant_systems` 返回空元组——**不拿标准玩法顶替**。
6. **规则集必须满足冻结签名**（9 条 · 参数名与个数逐条相等）——不符当场拒。
7. **清单可机读且与登记表逐条相等**（`tetris variants --json`）——
   **"给人看的清单"不算**。
8. **注销是对称的原语**（给测试用，避免全局表被污染）。

## 反向验证（照做，不是照抄）

- **把「一处登记」拆成两处**（登记变体但不接规则集）→ 判据 1 必红；
- **把「标识已登记」当成「重复登记」直接覆盖** → 判据 3 必红；
- **不在册时拿标准规则集顶替** → 判据 5 必红。
"""

from __future__ import annotations

import copy

import pytest

from tetris_kernel.host.assembly import (
    RULE_SETS,
    register_rule_set,
    rule_set_of,
    unregister_rule_set,
)
from tetris_kernel.host.cli import main
from tetris_kernel.host.variants import (
    VARIANTS,
    VariantManifest,
    register_variant,
    unregister_variant,
    variant_catalog,
    variant_of,
    variant_rule_set,
    variant_systems,
)
from tetris_kernel.rules.standard import STANDARD_RULESET

# ---------------------------------------------------------------------------
# 夹具：登记与注销都要还原（全局表不许被测试污染）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registries():
    """每条用例前后把变体表与规则集表还原成原样。

    **还原只走公开原语**（登记 / 注销）——**改动路径只有一处**，测试也不例外
    （直接写只读视图会当场 `TypeError`，那正是这道门守着的东西）。
    """
    variants_before = dict(VARIANTS)
    rule_sets_before = dict(RULE_SETS)
    yield
    for name in list(VARIANTS):
        if name not in variants_before:
            unregister_variant(name)
    for rule_id in list(RULE_SETS):
        if rule_id not in rule_sets_before:
            unregister_rule_set(rule_id)
    # **还原被用例删掉的原有条目**：先规则集，后变体
    # （登记变体会连带登记它的规则集，顺序反了会撞"一个标识指向两个规则集"）。
    for rule_id, rule_set in rule_sets_before.items():
        if rule_id not in RULE_SETS:
            register_rule_set(rule_id, rule_set)
    for name, manifest in variants_before.items():
        if name not in VARIANTS:
            register_variant(manifest)


def _manifest(name: str = "demo", **overrides: object) -> VariantManifest:
    """造一个变体声明（**默认用标准规则集**——签名天然合规）。"""
    fields: dict[str, object] = {
        "name": name,
        "version": "0.1.0",
        "ruleId": "demo-rules",
        "ruleSet": STANDARD_RULESET,
        "systems": (),
        "description": "演示变体",
    }
    fields.update(overrides)
    return VariantManifest(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 判据 1：登记一次，两处生效
# ---------------------------------------------------------------------------


def test_registering_a_variant_also_wires_its_rule_set():
    """登记变体 → 规则集**同时**进了接线表（一处登记，两处生效）。"""
    manifest = _manifest()
    register_variant(manifest)
    assert variant_of("demo") is manifest
    assert RULE_SETS.get("demo-rules") is STANDARD_RULESET
    assert variant_rule_set("demo") is STANDARD_RULESET


def test_the_rule_set_view_sees_later_registrations():
    """`RULE_SETS` 是**只读视图**——登记后**看得见**（视图引用同一个存储）。"""
    register_variant(_manifest())
    assert "demo-rules" in RULE_SETS
    assert rule_set_of("demo-rules") is STANDARD_RULESET


# ---------------------------------------------------------------------------
# 判据 2：变体标识 ≠ 规则集标识
# ---------------------------------------------------------------------------


def test_a_variant_may_reuse_an_already_registered_rule_set():
    """变体 B 的情形：**玩法差异来自挂的系统**，规则集就是标准的。"""
    manifest = _manifest(name="growth", ruleId="standard", systems=("growth-system",))
    register_variant(manifest)
    assert variant_of("growth") is manifest
    assert variant_systems("growth") == ("growth-system",)
    # **标准规则集还是那一个**——没有被换掉，也没有被重复登记。
    assert RULE_SETS["standard"] is STANDARD_RULESET
    assert variant_rule_set("growth") is STANDARD_RULESET


def test_the_variant_name_and_the_rule_set_id_are_separate():
    """变体标识与规则集标识**是两个字段**（混成一个，变体 B 就没法登记）。"""
    manifest = _manifest(name="growth", ruleId="standard")
    assert manifest.name != manifest.ruleId


# ---------------------------------------------------------------------------
# 判据 3：一个标识只能指向一个规则集
# ---------------------------------------------------------------------------


def test_one_rule_set_id_cannot_point_at_two_rule_sets():
    """标识已登记却指向**另一个**规则集 → 当场报错。"""
    register_rule_set("shared", STANDARD_RULESET)
    other = copy.copy(STANDARD_RULESET)
    assert other is not STANDARD_RULESET
    with pytest.raises(ValueError, match="已经指向另一个规则集"):
        register_variant(_manifest(ruleId="shared", ruleSet=other))


def test_registering_the_same_rule_set_again_under_one_id_is_allowed():
    """**同一个**规则集实例挂同一个标识 → 允许（不是"另一个"）。"""
    register_rule_set("shared", STANDARD_RULESET)
    register_variant(_manifest(ruleId="shared", ruleSet=STANDARD_RULESET))
    assert variant_rule_set("demo") is STANDARD_RULESET


# ---------------------------------------------------------------------------
# 判据 4：重名登记 → 报错
# ---------------------------------------------------------------------------


def test_registering_the_same_variant_name_twice_is_rejected():
    """标识是键——后登记的会悄悄顶掉先登记的，当场炸。"""
    register_variant(_manifest(name="demo"))
    with pytest.raises(ValueError, match="变体标识已登记"):
        register_variant(_manifest(name="demo"))


# ---------------------------------------------------------------------------
# 判据 5：不在册 → 没有（不顶替）
# ---------------------------------------------------------------------------


def test_an_unregistered_variant_is_simply_absent():
    """不在册 → 取不到（**不拿标准玩法顶替**）。"""
    assert variant_of("nope") is None
    assert variant_rule_set("nope") is None
    assert variant_systems("nope") == ()
    assert "nope" not in variant_catalog()


# ---------------------------------------------------------------------------
# 判据 6：规则集必须满足冻结签名
# ---------------------------------------------------------------------------


def test_a_rule_set_that_violates_the_frozen_signature_is_rejected():
    """签名不符 → **当场拒**（规则集接错了要当场知道，不是玩到一半才炸）。"""

    class _BrokenRuleSet:
        """签名对不上：参数名写成 `piece_type`（契约里是 `pieceType`）。"""

        wrap_cell = staticmethod(lambda x, y, w, h: (x, y))
        gravity_vector = staticmethod(lambda ctx: (0, 1))
        gravity_interval = staticmethod(lambda ctx: 1)
        scan_clears = staticmethod(lambda board, ctx: ())
        classify_placement = staticmethod(lambda board, placement, ctx: None)
        score_clear = staticmethod(lambda groups, ctx, prev: None)
        soft_drop_score = staticmethod(lambda ctx, distance: 0)
        shape_cells = staticmethod(lambda ctx, piece_type, rotation: ())

    with pytest.raises(ValueError, match="冻结签名"):
        register_rule_set("broken", _BrokenRuleSet())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 判据 7：清单可机读且与登记表逐条相等
# ---------------------------------------------------------------------------


def test_the_variants_listing_matches_the_registry_line_by_line(capsys: pytest.CaptureFixture[str]):
    """`tetris variants --json` 的内容**与登记表逐条相等**（期望值**按登记表现取**）。

    **期望值不手抄**——登记表里有什么（**含内建变体**），清单就得有什么。
    **手抄的期望值会与"内建变体加进来"这件事绑死**（内建变体一多就得改一遍）。
    """
    register_variant(
        _manifest(name="demo", version="1.2.3", ruleId="demo-rules", systems=("sys-a", "sys-b"))
    )
    exit_code = main(["variants", "--json"])
    assert exit_code == 0
    payload = _read_json(capsys)
    assert payload == {
        "variants": [
            {
                "name": name,
                "version": manifest.version,
                "ruleId": manifest.ruleId,
                "systems": list(manifest.systems),
                "description": manifest.description,
            }
            for name, manifest in variant_catalog().items()
        ]
    }


def test_the_variants_listing_only_shows_registered_variants(
    capsys: pytest.CaptureFixture[str],
):
    """清单里出现的**都是登记过的**（**不拿没登记的变体顶替**）。

    **这条判据的原样是"一个变体都没登记 → 清单是空的"**——**进程启动时内建变体
    就接进登记表了**（`main()` 里登记 · spec-M4 §7.1「变体要登记一次」），
    所以那条"空清单"不再成立。**换成这条更强也更准的判据**：**清单 ≡ 登记表**
    （不多不少）。**"登记前为空"那条口径改写在这里**：空清单只在**还没接任何
    变体**的进程里成立，而 CLI 进程启动即接内建变体。
    """
    assert main(["variants", "--json"]) == 0
    payload = _read_json(capsys)
    listed = {item["name"] for item in payload["variants"]}
    assert listed == set(variant_catalog())
    assert "nope" not in listed


def test_the_human_readable_listing_shows_the_rule_set_id(capsys: pytest.CaptureFixture[str]):
    """人读形态：变体名 · 版本 · 规则集标识 · 附带系统。"""
    register_variant(_manifest(name="demo", ruleId="demo-rules"))
    assert main(["variants"]) == 0
    output = capsys.readouterr().out
    assert "demo" in output
    assert "demo-rules" in output


# ---------------------------------------------------------------------------
# 判据 8：注销是对称的原语
# ---------------------------------------------------------------------------


def test_unregistering_removes_the_variant_from_the_catalog():
    """注销 → 清单里没有了（**全局表不留痕**）。"""
    register_variant(_manifest(name="demo"))
    assert unregister_variant("demo") is not None
    assert variant_of("demo") is None
    assert unregister_variant("demo") is None


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _read_json(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    """读命令行吐出的 JSON（**自己解析，不引第三方**）。"""
    import json

    return json.loads(capsys.readouterr().out)
