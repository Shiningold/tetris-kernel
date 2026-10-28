"""成就定义（系统自己的数据 · M3-S5 · FR-9.3）。

## 定义为什么住在这儿，不进 `contracts/`

**契约面描述「对外承诺」，成就条目是内容不是承诺**（plan-M3 S5 的常见坑）。
把成就条目放进 `contracts/` 会让「成就多了一个」变成「契约变了」——
**那是另一回事**：契约基线钉的是「这个系统对外承诺了什么」，
成就是**它自己数出来的结果**。

## 定义按事件类型与计数写（不写死玩法名）

一条成就 = **触发事件类型 + 计数阈值**（外加编号与标题）。
**不写死「标准玩法」的名字**（plan-M3 S5 的常见坑）：变体复用时
「落子一百次」照样成立，而「标准玩法里落子一百次」不成立。
**口径**：数的是**事件条数**，不是别的——事件只说「发生了什么」，
「怎么算」是消费方的事（与 ADR-046 同一把尺子）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Achievement:
    """一条成就定义：**编号 · 标题 · 触发事件类型 · 计数阈值**。

    - `id`：编号（**全局唯一**，进 `achievement:unlocked` 的负载）；
    - `title`：标题（进负载）；
    - `trigger_type`：**触发的事件类型名**（必须是已登记的事件类型——
      写错名字的成就会挂一个永远不触发的条件）；
    - `threshold`：计数阈值（**达成即解锁**，达成后不再重复解锁）。

    **构造时就地校验**：空编号 / 空标题 / 空事件类型 / 阈值小于 1 一律报错——
    **当场炸**，不留到跑起来才发现有个永不触发的成就。
    """

    id: str
    title: str
    trigger_type: str
    threshold: int

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("成就编号不能为空")
        if not self.title:
            raise ValueError(f"成就 {self.id}：标题不能为空")
        if not self.trigger_type:
            raise ValueError(f"成就 {self.id}：触发事件类型不能为空")
        if self.threshold < 1:
            raise ValueError(f"成就 {self.id}：计数阈值至少为 1，实为 {self.threshold}")


#: 内建成就清单（**顺序固定**——解锁顺序与清单一致，结果可复现）。
#:
#: **都是「事件条数」**：落子 / 硬降 / 暂存 / 出块——**按事件类型与计数写**，
#: 不认「标准玩法」也不认任何玩法名。
DEFAULT_ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement(id="first-lock", title="首次落子", trigger_type="piece:locked", threshold=1),
    Achievement(id="lock-100", title="落子百次", trigger_type="piece:locked", threshold=100),
    Achievement(id="harddrop-50", title="硬降五十次", trigger_type="piece:harddrop", threshold=50),
    Achievement(id="hold-10", title="暂存十次", trigger_type="piece:held", threshold=10),
    Achievement(id="spawn-200", title="出块两百次", trigger_type="piece:spawned", threshold=200),
)

__all__ = ["DEFAULT_ACHIEVEMENTS", "Achievement"]
