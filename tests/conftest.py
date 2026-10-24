"""测试骨架与公共 fixture（S15 · plan-M0.md §1.4.4.1 ⑤ · ADR-053）。

## 三个公共 fixture

| fixture | 给什么 |
| --- | --- |
| `fixed_kernel` | 固定种子（`seed=0`）的 `Kernel`，**未开局**（phase = `not_started`） |
| `fixed_bus` | 总线，`view_provider` 指向 `fixed_kernel.snapshot`（ADR-056） |
| `tmp_sink` | 临时目录（`pathlib.Path`），给后续落盘用（M0 只建不用） |

**名字与 §1.4.4.1 ⑤ 一字不差**：同批次的 C1（宿主装配与无头闭环）也用这三个
fixture，所以名字与语义都不能各写一份。

## hypothesis 的 `derandomize=True` 落点就在这里（硬要求）

ADR-053：**只固定内核种子不够**——hypothesis 自己的生成随机性也是变量，
不关掉它，CI 上的偶发失败无法复现。所以本模块在**导入期**注册并加载一个
带 `derandomize=True` 的 profile：

- **注册点**：`settings.register_profile("tetris", derandomize=True, …)`；
- **生效点**：`settings.load_profile("tetris")`。

pytest **先导入 `conftest.py` 再收集用例**，所以任何用例跑起来时 profile 已经
生效——包括别的写手写的用例（同一会话共用一份设置，两个写手都不许在别处另设）。

`deadline=None`：属性测试的用例会跑内核与事件分发，耗时随机器波动；时限失败
是**噪声**不是缺陷（ADR-013 的两条纪律是"固定种子"与"关掉生成随机性"，不是
"卡时限"）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

from tetris_kernel.bus.bus import EventBus
from tetris_kernel.kernel.core import Kernel
from tetris_kernel.kernel.snapshot import Snapshot
from tetris_kernel.rules.tables import PIECE_TYPES

#: 公共 fixture 用的固定种子（FR-1.4：可重置到初始状态）。
FIXED_SEED = 0

#: hypothesis profile 名（一个会话一份，两个写手共用）。
HYPOTHESIS_PROFILE = "tetris"

# ---------------------------------------------------------------------------
# hypothesis 设置（ADR-053）——**落点就在本模块**
# ---------------------------------------------------------------------------

settings.register_profile(
    HYPOTHESIS_PROFILE,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.load_profile(HYPOTHESIS_PROFILE)


# ---------------------------------------------------------------------------
# 公共 fixture（§1.4.4.1 ⑤）
# ---------------------------------------------------------------------------


@pytest.fixture
def fixed_kernel() -> Kernel:
    """固定种子（`seed=0`）的内核，**未开局**（phase = `not_started`）。

    固定种子 = 同序列重跑必得同结果（FR-1.4 · G0-3）。未开局 = 用例可以从
    「开局前」这个状态开始，不用先跑一段命令。
    """
    return Kernel(seed=FIXED_SEED, piece_types=PIECE_TYPES)


@pytest.fixture
def fixed_bus(fixed_kernel: Kernel) -> EventBus[Snapshot]:
    """总线，`view_provider` 指向 `fixed_kernel.snapshot`（ADR-056）。

    **视图 = 快照，同一个 dataclass**——不写两套。总线本身**不提供 `view()`**
    （ADR-021）：只读视图只走系统层入口的 `ctx.view()`。
    """
    return EventBus[Snapshot](view_provider=fixed_kernel.snapshot)


@pytest.fixture
def tmp_sink(tmp_path: Path) -> Path:
    """临时目录，给后续落盘用（**M0 只建不用**）。

    存档 / 录像的落盘在 M2；这里先把位置占住，免得那时再改 fixture 名字。
    """
    return tmp_path
