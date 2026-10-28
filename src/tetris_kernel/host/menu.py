"""图形版主菜单（宿主层 · M6-S3 · `G6-4` · spec-M6 §7.4 / §7.5 · ADR-100）。

## 它是什么

**双击之后先看到的那个窗口**：选**玩法**（标准玩法 / 重力旋转 / 落地生长）
+ 选**难度**（起始等级 1 / 5 / 10），选完就开局。

## 两层切分（**为了能无头单测**）

| 层 | 东西 | 依赖 tkinter？ |
| --- | --- | --- |
| **纯逻辑** | `MenuChoice` · `MENU_VARIANTS` · `MENU_DIFFICULTIES` · `play_options()` | **否** |
| **窗口** | `MenuSession` | 是 |

**装配（"这个玩法对应哪个规则集与哪些系统"）是纯逻辑**——**它不弹窗口**，
所以 `G6-4` 的机检**在无头环境里就能跑**。

## 难度口径：**起始等级**

难度 = **开局时的等级**（1 / 5 / 10）——**等级决定重力间隔**（标准 1 级 =
60 帧落一格）。**用既有的 `setLevel` 命令落地**（**不新增命令** · 契约面零
变化 · `G6-1`）。

## 三个玩法分别对应什么（**别弄混**）

| 菜单项 | 变体标识 | 规则集 | 附带系统 |
| --- | --- | --- | --- |
| 标准玩法 | `None`（**不是变体**） | 标准规则集 | — |
| 重力旋转 | `gravity-right` | **变体 A 的规则集**（轴 = right） | — |
| 落地生长 | `growth` | **标准规则集**（变体 B 的规则集就是标准的） | `growth` |

**变体 B 的规则集是标准的**——它靠**挂的那个系统**造出玩法差异（**变体标识
≠ 规则集标识**那条口径的兑现）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..kernel.commands import Command
from ..rules.interface import RuleSet
from ..rules.standard import STANDARD_RULESET
from ..variants.gravity import gravity_rule_set

#: **菜单里的玩法选项**（`(变体标识 · 显示名)`）。
#:
#: **标准玩法的变体标识是 `None`**——它**不是变体**（变体是有玩法差异的扩展
#: 槽与规则集路径的标准玩法延伸），**登记成变体就是多此一举**。
#:
#: **重力旋转只列一个轴**（`right`）：`left` 与 `down` 也在册（**CLI 的
#: `--variant` 照样能选**）——**菜单只给三个选项**（用户要的是三个）。
MENU_VARIANTS: tuple[tuple[str | None, str], ...] = (
    (None, "标准玩法"),
    ("gravity-right", "重力旋转"),
    ("growth", "落地生长"),
)

#: **难度档**（`(起始等级 · 显示名)`）。
#:
#: **为什么只三档**：难度 = **起始等级**（规则集按等级给重力间隔）——**加档
#: 就是加"起始多快"**，不是加新的玩法维度。**三档够用**（休闲 / 标准 / 硬）。
MENU_DIFFICULTIES: tuple[tuple[int, str], ...] = (
    (1, "休闲（等级 1）"),
    (5, "标准（等级 5）"),
    (10, "硬（等级 10）"),
)


@dataclass(frozen=True)
class MenuChoice:
    """**一次菜单选择**（**纯数据**）。

    - `variant_id`：**变体标识**（`None` = 标准玩法）；
    - `start_level`：**起始等级**（**难度就是这个**）。
    """

    variant_id: str | None
    start_level: int


@dataclass(frozen=True)
class PlayOptions:
    """**装配结果**（**菜单选择 → 怎么装这一局**）。

    - `rule_set`：**这一局用哪个规则集**（**变体 A 换成自己的**）；
    - `rule_id`：**规则集标识**（**录像头要记它**）；
    - `systems`：**附带系统**（**变体 B 挂 `growth`**）；
    - `variant_id`：**变体标识**（**`None` = 标准玩法** · 录像头也记它）。
    """

    rule_set: RuleSet
    rule_id: str
    systems: tuple[str, ...]
    variant_id: str | None


def play_options(variant_id: str | None) -> PlayOptions:
    """**玩法 → 装配结果**（**纯逻辑** · **不弹窗口**）。

    **不认识的变体标识 → 明确报错**（**不顶替成标准玩法** —— 顶替就是"偷偷
    换了个游戏"）。
    """
    if variant_id is None:
        return PlayOptions(
            rule_set=STANDARD_RULESET,
            rule_id="standard",
            systems=(),
            variant_id=None,
        )
    if variant_id == "gravity-right":
        return PlayOptions(
            rule_set=gravity_rule_set("right"),
            rule_id="gravity-right",
            systems=(),
            variant_id="gravity-right",
        )
    if variant_id == "growth":
        return PlayOptions(
            rule_set=STANDARD_RULESET,
            rule_id="standard",
            systems=("growth",),
            variant_id="growth",
        )
    raise ValueError(f"不认识的玩法：{variant_id!r}")


def menu_choice(variant_id: str | None, start_level: int) -> MenuChoice:
    """**菜单选择 → `MenuChoice`**（**越界就报错** —— 不静默夹取）。"""
    if start_level < 1:
        raise ValueError(f"起始等级必须为正：{start_level}")
    known_variants = {identifier for identifier, _label in MENU_VARIANTS}
    if variant_id not in known_variants:
        raise ValueError(f"菜单里没有这个玩法：{variant_id!r}")
    return MenuChoice(variant_id=variant_id, start_level=start_level)


def start_level_command(start_level: int) -> Command:
    """**难度 → 起始等级命令**（**用既有的 `setLevel`** · 不新增命令）。

    **为什么单独一个函数**：**难度的落地口径要能无头验证**（`G6-4`）——
    命令造得出来、且内核认它，才是"难度真的生效"；**它不该藏在 tkinter
    窗口里**。
    """
    if start_level < 1:
        raise ValueError(f"起始等级必须为正：{start_level}")
    return Command(type="setLevel", args=(("level", start_level),), source="host")


class MenuSession:
    """**主菜单窗口**（tkinter）。

    **它只产出 `MenuChoice`**——**装配不归它管**（装配在 `play_options()`）。
    """

    def __init__(self, *, title: str = "俄罗斯方块内核 · 主菜单") -> None:
        """构造菜单窗口（**tkinter 延迟 import** —— 无头环境不 import 它）。"""
        import tkinter

        self._tk = tkinter
        self._root = tkinter.Tk()
        self._root.title(title)
        self._variant_var = tkinter.StringVar(value="standard")
        self._level_var = tkinter.IntVar(value=5)
        self._choice: MenuChoice | None = None
        self._build()

    def _build(self) -> None:
        """**摆控件**（**玩法三选一 + 难度三档 + 开始**）。"""
        tk = self._tk
        frame = tk.Frame(self._root, padx=24, pady=18)
        frame.pack()
        tk.Label(frame, text="玩法", font=("", 12, "bold")).grid(row=0, column=0, sticky="w")
        for index, (variant_id, label) in enumerate(MENU_VARIANTS, start=1):
            value = variant_id if variant_id is not None else "standard"
            tk.Radiobutton(
                frame,
                text=label,
                value=value,
                variable=self._variant_var,
            ).grid(row=index, column=0, sticky="w")
        tk.Label(frame, text="难度", font=("", 12, "bold")).grid(
            row=0, column=1, sticky="w", padx=24
        )
        for index, (level, label) in enumerate(MENU_DIFFICULTIES, start=1):
            tk.Radiobutton(
                frame,
                text=label,
                value=level,
                variable=self._level_var,
            ).grid(row=index, column=1, sticky="w", padx=24)
        tk.Button(frame, text="开始", command=self._on_start, width=12).grid(
            row=5, column=0, columnspan=2, pady=(18, 0)
        )

    def _on_start(self) -> None:
        """**「开始」被点了**（**越界就报错** —— 不静默夹取）。"""
        raw = self._variant_var.get()
        variant_id = None if raw == "standard" else raw
        self._choice = menu_choice(variant_id, int(self._level_var.get()))
        self._root.quit()

    def choose(self) -> MenuChoice:
        """**跑菜单直到玩家点了「开始」**（**返回选择**）。"""
        self._root.mainloop()
        self._root.destroy()
        if self._choice is None:
            raise RuntimeError("菜单没有拿到选择（窗口被关掉了？）")
        return self._choice


__all__ = [
    "MENU_DIFFICULTIES",
    "MENU_VARIANTS",
    "MenuChoice",
    "MenuSession",
    "PlayOptions",
    "menu_choice",
    "play_options",
    "start_level_command",
]
