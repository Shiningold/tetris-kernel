"""规则接口的 8 条签名（架构 §7.3.1 · ADR-061 · M0 定稿）。

## 这个模块是什么

规则集的**类型契约**：8 条接口各一个 `Protocol`——规则集只要结构上满足就能被
内核注入，换规则集不需要改内核（FR-4.9）。

签名的**权威声明**在 `contracts/rules.py` 的 `RULE_SIGNATURES`（进基线）；
本模块是它在代码侧的投影，**参数名与顺序必须与那里逐字一致**（参数名即契约，
改名 = major 变更）。

## 为什么只用中性类型

架构 §7.3：接口只用 `Sequence[Sequence[int]]` / `int` / `str` / `tuple`，
**不 import kernel 的任何 dataclass**——否则 `kernel > rules` 契约第一天就红，
然后大概率被加豁免，契约就废了。

本模块 import 的是 `contracts/` 里的**中性交换结构**（`RuleContext` /
`PlacementInfo` / `ClearGroup` / `PlacementClass` / `ScoreState` /
`ScoreDelta`）：它们是公共叶子，依赖方向 `rules > contracts`，合法。

## 自检

`signature_mismatches(rule_set)` 把规则集的实际参数名与冻结签名逐条比对，
返回问题清单（空 = 全对）。**只比参数名与参数个数**：类型是注解层的事，
契约基线已经钉住声明，这里再抄一份类型判据就是第二份真相。
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Protocol

from ..contracts.placement import PlacementInfo
from ..contracts.rules import (
    RULE_SIGNATURE_NAMES,
    RULE_SIGNATURES,
    ClearGroup,
    PlacementClass,
    RuleContext,
    ScoreDelta,
    ScoreState,
)

# ---------------------------------------------------------------------------
# 8 条接口：每条一个 Protocol
# ---------------------------------------------------------------------------


class RuleInterface(Protocol):
    """规则接口的**标记基类**（空协议）。

    **存在的理由只有一个**：`Protocol` 不能当类型实参用（pyright 的
    `reportInvalidTypeForm`），而注册表 `RULE_INTERFACES` 需要一个
    "接口类的类型"来标注值。所以给 8 条 Protocol 垫一个空协议基类：
    协议本身仍是**结构化**的（实现者不需要继承任何东西），这个基类只为
    标注服务。
    """


class WrapCell(RuleInterface, Protocol):
    """边界拓扑：坐标映射函数（ADR-037——是函数，不是参数）。"""

    def __call__(self, x: int, y: int, w: int, h: int) -> tuple[int, int]:
        """把坐标映射回棋盘内；标准玩法恒等。"""
        ...


class GravityVector(RuleInterface, Protocol):
    """重力方向向量：方向必须是 `(x, y)`，不能只有 `dy`。"""

    def __call__(self, ctx: RuleContext) -> tuple[int, int]:
        """返回当前重力方向的单位向量。"""
        ...


class GravityInterval(RuleInterface, Protocol):
    """重力间隔：多少逻辑帧落一格。"""

    def __call__(self, ctx: RuleContext) -> int:
        """返回落一格需要的逻辑帧数。"""
        ...


class ScanClears(RuleInterface, Protocol):
    """消行扫描：返回"轴 + 索引 + 格子清单"，不能只返回数量。"""

    def __call__(self, board: Sequence[Sequence[int]], ctx: RuleContext) -> tuple[ClearGroup, ...]:
        """扫描可消的行 / 列。"""
        ...


class ClassifyPlacement(RuleInterface, Protocol):
    """放置分类：T-Spin 标记 + 计分类别键。"""

    def __call__(
        self,
        board: Sequence[Sequence[int]],
        placement: PlacementInfo,
        ctx: RuleContext,
    ) -> PlacementClass:
        """判定本次放置的分类。"""
        ...


class ScoreClear(RuleInterface, Protocol):
    """计分：纯函数，返回增量与新状态，落笔由内核做。"""

    def __call__(
        self, groups: Sequence[ClearGroup], ctx: RuleContext, prev: ScoreState
    ) -> ScoreDelta:
        """按消行组计分。"""
        ...


class ShapeCells(RuleInterface, Protocol):
    """形状表：方块在当前重力轴下的占位格子。"""

    def __call__(
        self, ctx: RuleContext, pieceType: str, rotation: int
    ) -> tuple[tuple[int, int], ...]:
        """取形状表。"""
        ...


class KickOffsets(RuleInterface, Protocol):
    """踢墙表：旋转时的候选偏移（含 180°）。"""

    def __call__(
        self, ctx: RuleContext, pieceType: str, fromRotation: int, toRotation: int
    ) -> tuple[tuple[int, int], ...]:
        """取踢墙表。"""
        ...


# ---------------------------------------------------------------------------
# 注册表与整体替换入口
# ---------------------------------------------------------------------------

#: 接口名 → 对应的 Protocol 类。键与 `RULE_SIGNATURES` 的键**一一对应**。
#:
#: 值的类型标成 `type` 而不是 `type[RuleInterface]`：协议类是"抽象类"，
#: pyright 不让它们进 `type[...]`（`reportAssignmentType`）。这个注册表只
#: 用于**按名查接口**，不参与类型检查。
RULE_INTERFACES: Mapping[str, type] = MappingProxyType(
    {
        "wrap_cell": WrapCell,
        "gravity_vector": GravityVector,
        "gravity_interval": GravityInterval,
        "scan_clears": ScanClears,
        "classify_placement": ClassifyPlacement,
        "score_clear": ScoreClear,
        "shape_cells": ShapeCells,
        "kick_offsets": KickOffsets,
    }
)


class RuleSet(RuleInterface, Protocol):
    """一个规则集：8 条接口的整体替换入口（FR-4.9）。

    **只读**：内核拿到规则集只调用，不改它——规则集不可变、无状态
    （架构 §7.3.1 纪律 1）。属性声明成只读属性，是为了让冻结的规则集
    （frozen dataclass）也能满足它。
    """

    @property
    def wrap_cell(self) -> WrapCell:
        """边界拓扑。"""
        ...

    @property
    def gravity_vector(self) -> GravityVector:
        """重力方向向量。"""
        ...

    @property
    def gravity_interval(self) -> GravityInterval:
        """重力间隔。"""
        ...

    @property
    def scan_clears(self) -> ScanClears:
        """消行扫描。"""
        ...

    @property
    def classify_placement(self) -> ClassifyPlacement:
        """放置分类。"""
        ...

    @property
    def score_clear(self) -> ScoreClear:
        """计分。"""
        ...

    @property
    def shape_cells(self) -> ShapeCells:
        """形状表。"""
        ...

    @property
    def kick_offsets(self) -> KickOffsets:
        """踢墙表。"""
        ...


# ---------------------------------------------------------------------------
# 自检：实现与冻结签名逐条比对
# ---------------------------------------------------------------------------


def signature_mismatches(rule_set: object) -> tuple[str, ...]:
    """比对规则集的实现与冻结签名（`contracts/rules.py` 的 `RULE_SIGNATURES`）。

    返回问题清单，**空元组 = 8 条全对**。每条问题是一句中文说明，直接可读。

    **只比参数名与参数个数**：类型是注解层的事，契约基线已经钉住声明；
    这里再抄一份类型判据就是第二份真相（架构 §4.4 末条）。

    比参数名是因为**参数名即契约**（架构 §7.3.1 / ADR-061）：`pieceType` 写成
    `piece_type` 就不是「同一个接口」了，而是另一个接口——内核按名字调用，
    名字对不上会在运行期才炸。
    """
    problems: list[str] = []
    for name in RULE_SIGNATURE_NAMES:
        expected = tuple(param.name for param in RULE_SIGNATURES[name].params)
        implementation = getattr(rule_set, name, None)
        if implementation is None:
            problems.append(f"{name}：规则集缺少这条接口")
            continue
        if not callable(implementation):
            problems.append(f"{name}：不是可调用对象")
            continue
        try:
            parameters = inspect.signature(implementation).parameters
        except (TypeError, ValueError):
            problems.append(f"{name}：取不到调用签名")
            continue
        actual = tuple(parameters)
        if actual != expected:
            problems.append(f"{name}：参数名应为 {expected}，实为 {actual}")
    return tuple(problems)


__all__ = [
    "RULE_INTERFACES",
    "ClassifyPlacement",
    "GravityInterval",
    "GravityVector",
    "KickOffsets",
    "RuleSet",
    "ScanClears",
    "ScoreClear",
    "ShapeCells",
    "WrapCell",
    "signature_mismatches",
]
