"""采集单帧逻辑开销（P50 / P99 / max）与每帧事件数（S16 用 · 开放项 B / C）。

## 采什么

- **单帧逻辑开销**：一帧里内核逻辑耗多少微秒（**不含渲染**，渲染另有
  `render:frame.durationUs`）。开放项 B 要的就是这个真实值——不标定就没法
  验收性能预算与降级阈值。
- **每帧真实事件数**：一帧发出多少条事件。开放项 C 同理。

## 采的是真实的宿主层

S14 落地了无头帧循环，本脚本直接驱动它（**不再依赖任何"约定中的"接口**）：

- `assemble(seed=…)` → 装配层（内核 / 总线 / 时钟）；
- `HeadlessLoop(assembly)` → 无头帧循环；
- `loop.start()` → 开局（派发 `start`，**不开帧**）；
- `loop.run_frame()` → 跑一帧，**返回本帧发出的事件数**。

**事件数不用自己数**：序号由总线分配，`run_frame()` 给的差值就是「本帧真的
发出了多少条事件」（架构 §4.2）。

## 口径

- 用 `time.perf_counter()` 量**墙钟耗时**——这是测量工具，不是内核，
  架构 §5.3 的禁读墙钟只约束 `kernel/` 与 `rules/`（本脚本在 `tools/`）。
- **计时只包住 `run_frame()` 调用本身**：判定用的 phase 读取放在计时区外，
  它是分类依据，不是被测对象。
- **百分位用最近秩（nearest-rank）**，不插值：口径确定、可复现，
  同一批样本两次算出来一样。浮点插值会引出"四舍五入到第几位"这种
  没意义的争论。
- 统计**每帧**的耗时与事件数，不做跨帧平滑——平滑会把尖峰藏起来，
  而尖峰正是降级阈值要防的东西。
- **只计内核推进与事件分发**：渲染 / 日志落盘 / 指标导出 / 网络 IO 一律不计
  （M0 本来也没有这些）。
- **预热帧不计入样本**（`--warmup`）：开头那几百帧明显偏慢（进程刚起、
  内存分配器还在铺页、CPU 还没升到稳态），把它们算进 P50 / P99 会把
  「内核每帧要多少」和「机器刚热起来要多少」混成一锅。

## 常态帧与拒绝路径（开放项 C 要的是常态帧）

内核按规则集的重力间隔落格（标准 1 级 = 60 帧落一格）。**无人操作**的一局
约 7000 帧后顶出（`phase` 变 `game_over`），此后每一帧的 `tick` 都被生命
周期门禁拒掉（理由 `game_over`）——帧号照走，但推进许可不再授予（ADR-022）。

**判定看「该帧开始时的 phase」**：`running` → tick 必被接受（**常态帧**）；
`game_over` → tick 必被门禁拒（**拒绝路径**）。判定写在 phase 上而不是数
事件，是因为帧前 phase 就是内核门禁自己读的那个值——判定与内核同源。

拒绝路径上的帧事件数明显偏低（tick 被拒只有 `command:received` +
`command:rejected` 两条，没有语义事件），**开放项 C 的数必须取常态帧那一段**。

## 每帧事件数是**行为基线**（开放项 C 的答案要有人守着）

契约基线只看**声明**、冻结区基线只看**文件内容**——「每帧事件数」恰好落在
两者的缝里：内核每帧多发两条事件（5 → 7），**全套测试仍然全绿**，而文档里
开放项 C 的答案会静静变成错的（批次 D 审查 D5）。

所以这里有第三份基线（`tests/baseline/frame_events.json`）：**固定场景**
（种子 / 预热 / 帧数写死）下的**逐帧事件数**。事件数是**确定性的**（宿主用
`VirtualClock`，内核不读墙钟），两次独立实跑逐帧相同——所以它适合当基线。

**只记条数不够——还要记类型**（M0 全量审查 M0-8）：内核把 `piece:spawned`
换成一条同条数的事件（5 → 5），**逐帧条数一模一样**，基线照样全绿——**行为
变了但基线看不见**。所以基线里除了 `frameEventCounts`（逐帧条数），还记
`frameEventTypeShapes`（逐帧**事件类型**，按"类型组合"归并）：**条数一样
而类型不一样**时它就红。

**类型表按形状归并**：8000 帧里只有 **5 种**类型组合（常态帧 · 锁定延迟起算帧 ·
锁定帧 · 锁定 + 消行帧 · 顶出帧），逐帧存 8000 份是浪费；归并成「形状 → 帧号
列表」既小，又**逐帧可还原**（形状 + 帧号 = 原来的逐帧清单）。

**耗时不进基线**：墙钟噪声跨度能到 3 倍（P99 490 → 1460 μs），拿它当基线
就是拿噪声当标准（批次 D 审查 D7）。耗时只用来对照预算。

**刷新理由落在哪里（别改错）**：`--baseline-out` **必须带 `--reason`**（不给就
用法错误退出），理由**只**打到终端输出与**提交正文**（`AGENTS.md` 要求的就是
提交正文那一处），**不写进基线文件的 `meta`**——基线文件承诺**逐字节可复现**
（同样的内容刷新两次，文件一个字节都不该变）。**别"顺手"把理由加进 `meta`**：
那会让每次刷新都产生一个字节差异，**幂等性当场破掉**。

## 自检要**验**，不是只跑一遍

`--self-test` 拿固定种子的合成样本走同一段统计代码，并把结果与**写死的期望
值**逐项比对——统计口径坏掉时它会当场报红。原实现只是"换一批样本再算一遍"，
`percentile()` 改成返回最小值它照样 exit 0（批次 D 审查 D11）。

## 用法

```
python tools/measure_frame_cost.py --self-test          # 自检：合成样本，验统计口径
python tools/measure_frame_cost.py --frames 10000        # 采真实值
python tools/measure_frame_cost.py --frames 10000 --warmup 1000 --out out.json
python tools/measure_frame_cost.py --baseline-check tests/baseline/frame_events.json
python tools/measure_frame_cost.py --baseline-out tests/baseline/frame_events.json --reason "<理由>"
python tools/measure_frame_cost.py --thousand-games 1000 # 千局基准（`G2-4`）
python tools/measure_frame_cost.py --frames 10000 --no-observability  # 内核裸跑（配对 A/B 用）
```

退出码：0 = 采到了 / 校验通过 / 自检通过 / 千局在预算内；
1 = 基线不一致 / 自检失败 / 千局超预算；
2 = 用法错误或采样口径被破坏。

## 千局基准（`G2-4` · ADR-018）

**多进程**：每局一个任务，进程池大小 = CPU 核数，`chunksize > 1`，
**汇总前按种子排序**（`pool.map` 保序）。

**"关闭事件分发"的快速模式被明确否决**（ADR-018 · B-1 · B-10）——那会破坏
**事件完备性**（AC-2）。

**判的是墙钟总时长**（并行跑完 N 局用了多久）。**每局耗时之和**也报出来，
**但它不是判据**：30 分钟的预算是"并行跑完 1000 局"的预算。**那串和还含
并行争用**（16 个进程抢同一台机器）——它是**串行时间的上界**，不是串行实测值。

### 局必须是**带策略的局**（纯重力局不行）

**纯重力局**（宿主只派 `tick`）每局 **7671 帧**——1000 局并行跑完约 **31 分钟**，
**卡在预算线上**（预算 30 分钟）。**参考策略局**每局约 **2800 帧** → 1000 局
约 **4 分钟**，**留足了余量**。

**更关键的是"同一批局"**：种子表起点从 `host/performance.py` 的
`THOUSAND_BASE_SEED` 取（**与门判据同一张**）——一局的帧数随种子在
1755–3300 之间波动，种子表不同 → 总工作量不同 → 两个数**不可比**。

**参考策略住在 `tests/support/policy.py`**——那是**测试件**，`src/` 里**不放**
它（库里不 shipping 一个"参考 AI"：`systems/ai` 是将来 AI 系统的位置，不是
现在塞一个参考策略的地方）。所以这里**动态载入**：载入不到就**明确报错**，
**不静默退化成"纯重力局"**——那量的是另一件事。

### 这个数与门判据那个数的关系

| | 量的是 | 用途 |
| --- | --- | --- |
| 本工具 | **玩**（标准装配 · 跑完整局） | 探机器的余量 |
| `tests/test_m2_gates.py` 的 `G2-4` | **录 + 放**（含重放） | **判据** |

**门判据那个数才是判据**（它把重放的时间也算进去了，**更保守**）；本工具
那个数是**同一批局的工作量子集**——它不重放，所以它量的是"玩完这 1000 局"
的机器余量。
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import random
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from _toolkit import REPO_ROOT, SRC_ROOT, configure_output, relative_to_repo

sys.path.insert(0, str(SRC_ROOT))

from tetris_kernel.host import HeadlessLoop, assemble  # noqa: E402
from tetris_kernel.host.performance import (  # noqa: E402
    THOUSAND_BASE_SEED,
    THOUSAND_BUDGET_SECONDS,
)
from tetris_kernel.kernel import PHASE_GAME_OVER, PHASE_RUNNING  # noqa: E402
from tetris_kernel.kernel.commands import Command  # noqa: E402
from tetris_kernel.kernel.snapshot import Snapshot  # noqa: E402

#: NFR-2 的单帧逻辑预算（微秒）：60fps 帧步长 16.67ms 的 10% = 1.6ms。
#: 需求 NFR-2 · spec-M0.md 验收表「性能」行。**M0 只标定真实值**，
#: 超预算之后的降级实现是 M2 的 X-3。
BUDGET_US = 1600

#: 默认预热帧数（不计入样本）。
DEFAULT_WARMUP = 1000

#: 千局基准的默认局数（`G2-4`）。
DEFAULT_THOUSAND_GAMES = 1000

#: 千局基准的**每局任务**进进程池时的分块大小（ADR-018：`chunksize > 1`）。
THOUSAND_CHUNKSIZE = 8

#: 测试树目录（**参考策略住在里面**：`tests/support/policy.py`）。
#:
#: **只在千局基准模式里用到**——参考策略是**测试件**，`src/` 里不放它。
TESTS_DIR = REPO_ROOT / "tests"

# ---------------------------------------------------------------------------
# 事件数基线的固定场景
# ---------------------------------------------------------------------------
#
# **场景写死**：种子 / 预热 / 帧数一变，基线就换了口径，那不叫基线。
#
# **帧数是按「分支覆盖」选的，不是随手定的**：采样必须走到**每一条事件构成
# 不同的分支**，否则那条分支的行为改动基线看不见。
#
# **6100 → 8000（M1 · S14）**：M1 引入 30 帧锁定延迟后方块锁定的节奏变慢，
# 6100 帧里**走不到「锁定 + 消行」那条分支**——按旧口径刷新等于**把 M0 基线
# 覆盖到的那条分支刷没了**（基线不是越新越强，**覆盖变窄就是变弱**）。
# 8000 帧覆盖到：常态帧（5 条）· 锁定延迟起算帧（6 条）· 锁定 + 出生帧（8 条）
# · 「锁定 + 消行」（15 条，@6209）· **顶出帧**（8 条，@7380，M1 新增的 K3）
# ——**比 M0 的覆盖面多两条**（锁定延迟起算帧 + 顶出帧），一条都没少。
#
# **没被覆盖的分支（写实记录）**：M1 新增了两条事件，`piece:lock-delay-start`
# **纳入了基线**，`piece:lock-delay-reset` **没有**——**不是漏了，是够不着**：
# 固定场景是**纯重力**（宿主只派 `tick`，不派任何移动 / 旋转命令），而"重置"
# 分支必须由一次**成功的移动或旋转**触发 → 该分支在这个场景里**不可达**
# （实测：8000 帧实采的构成集合里没有它）。**不是"覆盖变窄"**——M0 根本没有这条
# 分支，没有任何东西被刷掉。**该路径另有专门守卫**：G1-5 的
# `test_m1_lock_delay_the_16th_move_does_not_reset_the_timer` 等用例守着重置
# 次数上限与"上限到了照常锁定"。**要把它纳进基线就得让固定场景派命令**——那是
# **一次口径变更**（场景写死这条准则），**该单独立项，不在收尾时顺手做**。
#
# **顶出之后那一段是拒绝路径**（`tick` 被生命周期门禁拒掉，每帧只有 2 条事件），
# **不进基线**（混进来会把「每帧事件数」这个开放项的答案拉低一大截）；基线的
# `meta` 里 `frames`（采样帧数）与 `normalFrames`（记进基线的常态帧数）分开记。

#: 事件数基线的种子。
BASELINE_SEED = 1

#: 事件数基线的预热帧数（0 = 从第 1 帧就开始采）。
BASELINE_WARMUP = 0

#: 事件数基线的采样帧数（**按分支覆盖选的**——见上面的说明）。
BASELINE_FRAMES = 8000

# ---------------------------------------------------------------------------
# 自检的固定参数与期望值
# ---------------------------------------------------------------------------
#
# **种子固定 → 结果确定 → 期望值可以写死**：这正是自检能"验"的前提。
# 期望值不是抄实现的输出，是按「`random.Random(1)` 逐帧取两个整数」的
# 口径手算后复核的（`percentile` 用最近秩：rank = ceil(fraction × n)）。

#: 自检的合成样本帧数。
SELF_TEST_FRAMES = 500

#: 自检的合成样本种子。
SELF_TEST_SEED = 1

#: 自检的期望值（统计口径坏掉时这里对不上 → 自检报红）。
SELF_TEST_EXPECTED: dict[str, int] = {
    "耗时 P50": 213,
    "耗时 P99": 399,
    "耗时最大": 400,
    "事件数 P50": 3,
    "事件数 P99": 6,
    "事件数最大": 6,
}

#: 最近秩百分位的期望值（手算可验的小样本：一组一组验，不靠合成样本）。
SELF_TEST_PERCENTILE_CASES: tuple[tuple[tuple[int, ...], float, int], ...] = (
    ((1, 2, 3, 4), 0.50, 2),
    ((1, 2, 3, 4), 0.99, 4),
    ((1, 2, 3, 4, 5), 0.50, 3),
    ((1, 2, 3, 4, 5), 0.20, 1),
    ((10,), 0.50, 10),
    ((1, 2, 3, 4, 5, 6, 7, 8, 9, 10), 1.00, 10),
)


@dataclass(frozen=True)
class FrameSample:
    """一帧的样本。

    - `cost_us`：这一帧的墙钟耗时（微秒，整数）；
    - `events`：这一帧发出的事件条数；
    - `phase`：**该帧开始时**的 phase——门禁读的就是它，所以它决定这一帧的
      `tick` 是被接受还是被拒；
    - `event_types`：这一帧发出的事件**类型**（按发布顺序）——**条数一样
      而类型不一样，也是行为变了**（M0 全量审查 M0-8）。
    """

    cost_us: int
    events: int
    phase: str
    event_types: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        """这一帧的 `tick` 是否被接受（`running` 态必被接受）。"""
        return self.phase == PHASE_RUNNING


@dataclass(frozen=True)
class Summary:
    """一次采集的汇总。"""

    frames: int
    cost_p50_us: int
    cost_p99_us: int
    cost_max_us: int
    cost_total_us: int
    events_p50: int
    events_p99: int
    events_max: int
    events_total: int
    over_budget_frames: int

    def to_dict(self) -> dict[str, object]:
        """转成可写进 JSON 的结构。"""
        return {
            "frames": self.frames,
            "frameCostUs": {
                "p50": self.cost_p50_us,
                "p99": self.cost_p99_us,
                "max": self.cost_max_us,
                "total": self.cost_total_us,
            },
            "eventsPerFrame": {
                "p50": self.events_p50,
                "p99": self.events_p99,
                "max": self.events_max,
                "total": self.events_total,
            },
            "overBudgetFrames": self.over_budget_frames,
        }


@dataclass(frozen=True)
class Report:
    """一次采集的完整报告（常态帧一段 · 拒绝路径一段 · 全量一段）。"""

    source: str
    seed: int
    warmup: int
    total: Summary
    normal: Summary
    rejected: Summary | None
    rejected_phases: tuple[str, ...]
    rejected_from_frame: int

    def to_dict(self) -> dict[str, object]:
        """转成可写进 JSON 的结构。"""
        return {
            "source": self.source,
            "seed": self.seed,
            "warmupFrames": self.warmup,
            "budgetUs": BUDGET_US,
            "frames": self.total.frames,
            "frameCostUs": self.total.to_dict()["frameCostUs"],
            "eventsPerFrame": self.total.to_dict()["eventsPerFrame"],
            "overBudgetFrames": self.total.over_budget_frames,
            "normalFrames": self.normal.to_dict(),
            "rejectedFrames": None if self.rejected is None else self.rejected.to_dict(),
            "rejectedPhases": list(self.rejected_phases),
            "rejectedFromFrame": self.rejected_from_frame,
        }


def percentile(samples: list[int], fraction: float) -> int:
    """最近秩百分位（不插值）。

    **确定性**：同一批样本两次算出来一样——插值会引出浮点尾数的争论，
    而这个数字是要写进验收报告的。
    """
    if not samples:
        raise ValueError("没有样本，采不到百分位")
    ordered = sorted(samples)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def summarize(costs_us: list[int], event_counts: list[int]) -> Summary:
    """汇总一批逐帧样本。"""
    if len(costs_us) != len(event_counts):
        raise ValueError("耗时样本与事件样本长度不一致")
    if not costs_us:
        raise ValueError("没有帧样本")
    return Summary(
        frames=len(costs_us),
        cost_p50_us=percentile(costs_us, 0.50),
        cost_p99_us=percentile(costs_us, 0.99),
        cost_max_us=max(costs_us),
        cost_total_us=sum(costs_us),
        events_p50=percentile(event_counts, 0.50),
        events_p99=percentile(event_counts, 0.99),
        events_max=max(event_counts),
        events_total=sum(event_counts),
        over_budget_frames=sum(1 for cost in costs_us if cost > BUDGET_US),
    )


def _collect(
    *,
    seed: int,
    frames: int,
    warmup: int,
    observability: bool = True,
) -> list[FrameSample]:
    """跑真实的无头帧循环，收逐帧样本。

    **计时只包住 `run_frame()`**：phase 的读取在计时区外——它是分类依据，
    不是被测对象。

    **预热帧照跑但不收**：跑预热是为了让进程进入稳态，样本只取预热之后的
    那些帧。

    **订阅在构造帧循环之前挂上**：`HeadlessLoop` 构造时就把 `kernel:ready` ·
    `game:created` 发到总线上了，晚订阅的收不到。订阅用全局通配 `*`——
    记的是**每条事件的类型**，不是某几类事件。

    **采集到的类型条数必须与 `run_frame()` 给的事件数相等**：不等就说明
    采集口径坏了（漏订阅 / 多订阅）——当场报错，不给出一份自相矛盾的基线。

    **`observability=False`**：关掉三个可观测系统——**M2 才有这个开关**
    （M1 的代码里没有），所以**配对 A/B 的"内核裸跑"那一档只能跑 M2 一侧**。
    """
    assembly = assemble(seed=seed, observability_enabled=observability)
    published: list[str] = []
    assembly.bus.subscribe("*", lambda envelope: published.append(envelope.type))
    loop = HeadlessLoop(assembly)
    loop.start()
    kernel = assembly.kernel
    for _ in range(warmup):
        loop.run_frame()

    samples: list[FrameSample] = []
    for index in range(frames):
        phase_before = kernel.phase
        before = len(published)
        started = time.perf_counter()
        event_count = loop.run_frame()
        elapsed_us = (time.perf_counter() - started) * 1_000_000
        if phase_before not in (PHASE_RUNNING, PHASE_GAME_OVER):
            # 采样口径只认这两段：别的 phase（比如 paused）说明这一局的
            # 跑法变了，分段统计的前提不成立——当场喊停，别给出一组
            # 口径含混的数字。
            raise RuntimeError(
                f"第 {index} 帧开始时 phase 是 {phase_before!r}："
                "既不是常态帧（running）也不是拒绝路径（game_over）"
            )
        event_types = tuple(published[before:])
        if len(event_types) != event_count:
            raise RuntimeError(
                f"第 {index} 帧采集到 {len(event_types)} 条事件类型，"
                f"但总线发出的事件数是 {event_count}：采集口径坏了"
            )
        samples.append(
            FrameSample(
                cost_us=round(elapsed_us),
                events=event_count,
                phase=phase_before,
                event_types=event_types,
            )
        )
    return samples


def _synthetic_samples(frames: int, seed: int) -> Iterator[FrameSample]:
    """合成样本（自检用）：确定性的耗时与事件数。

    **只用来验统计口径**，不是内核的真实数据——报告里不能拿它当性能值。
    """
    generator = random.Random(seed)
    for _ in range(frames):
        cost_us = generator.randint(40, 400)
        events = generator.randint(0, 6)
        yield FrameSample(cost_us=cost_us, events=events, phase=PHASE_RUNNING)


def _self_test() -> list[str]:
    """自检：验统计口径。返回问题清单（**空 = 通过**）。

    **两件事都要验**：

    1. **最近秩百分位本身**——手算可验的小样本逐组对（`rank = ceil(f × n)`，
       取第 `rank` 个）；这一组能抓住 off-by-one 与"返回最小值"那类变异；
    2. **合成样本的汇总结果**——固定种子 → 期望值写死；`percentile()` 被改坏
       时它立刻对不上。

    **只跑一遍不算自检**：没有期望值的自检，统计口径坏掉时照样 exit 0
    （批次 D 审查 D11）。
    """
    problems: list[str] = []

    for samples, fraction, expected in SELF_TEST_PERCENTILE_CASES:
        actual = percentile(list(samples), fraction)
        if actual != expected:
            problems.append(f"percentile({list(samples)}, {fraction}) = {actual}，期望 {expected}")

    synthetic = list(_synthetic_samples(SELF_TEST_FRAMES, SELF_TEST_SEED))
    summary = summarize([s.cost_us for s in synthetic], [s.events for s in synthetic])
    actuals = {
        "耗时 P50": summary.cost_p50_us,
        "耗时 P99": summary.cost_p99_us,
        "耗时最大": summary.cost_max_us,
        "事件数 P50": summary.events_p50,
        "事件数 P99": summary.events_p99,
        "事件数最大": summary.events_max,
    }
    for name, expected in SELF_TEST_EXPECTED.items():
        if actuals[name] != expected:
            problems.append(f"{name} = {actuals[name]}，期望 {expected}")

    return problems


def _split(samples: list[FrameSample]) -> tuple[list[FrameSample], list[FrameSample]]:
    """按「tick 是否被接受」分两段：常态帧 / 拒绝路径。"""
    normal = [sample for sample in samples if sample.accepted]
    rejected = [sample for sample in samples if not sample.accepted]
    return normal, rejected


def _first_rejected_index(samples: list[FrameSample]) -> int:
    """拒绝路径的第一帧在采样窗口里的下标（`-1` = 没进过拒绝路径）。"""
    for index, sample in enumerate(samples):
        if not sample.accepted:
            return index
    return -1


def _budget_ratio(value_us: int) -> str:
    """占预算的百分比（一位小数）。"""
    return f"{value_us * 100 / BUDGET_US:.1f}%"


def _print_segment(title: str, summary: Summary) -> None:
    """打印一段样本的汇总。"""
    print(f"【{title}】{summary.frames} 帧")
    print(
        f"  单帧逻辑开销（微秒）：P50 {summary.cost_p50_us} · P99 {summary.cost_p99_us}"
        f" · 最大 {summary.cost_max_us} · 合计 {summary.cost_total_us}"
    )
    print(
        f"  每帧事件数：P50 {summary.events_p50} · P99 {summary.events_p99}"
        f" · 最大 {summary.events_max} · 合计 {summary.events_total}"
    )
    print(f"  超出 {BUDGET_US} 微秒预算的帧数：{summary.over_budget_frames} / {summary.frames}")


def _print_budget(summary: Summary) -> None:
    """打印一段样本与 NFR-2 预算的对照。"""
    print(
        f"  对照 NFR-2 预算 {BUDGET_US} 微秒："
        f"P50 {summary.cost_p50_us}（{_budget_ratio(summary.cost_p50_us)}）"
        f" · P99 {summary.cost_p99_us}（{_budget_ratio(summary.cost_p99_us)}）"
        f" · 最大 {summary.cost_max_us}（{_budget_ratio(summary.cost_max_us)}）"
    )
    over_p99 = summary.cost_p99_us > BUDGET_US
    over_max = summary.cost_max_us > BUDGET_US
    if over_p99:
        verdict = "P99 已超预算"
    elif over_max:
        verdict = "P50 / P99 在预算内；最大值超预算（尖峰）"
    else:
        verdict = "全部在预算内"
    print(f"  结论：{verdict}")


def _type_shapes(frames: list[tuple[str, ...]]) -> list[dict[str, object]]:
    """逐帧事件类型 → **按形状归并**（类型组合 → 帧号列表）。

    **归并不丢信息**：形状 + 帧号 = 原来的逐帧清单，**逐帧可还原**。
    8000 帧里只有五种组合，逐帧存 8000 份是纯浪费。
    """
    buckets: dict[tuple[str, ...], list[int]] = {}
    for index, shape in enumerate(frames):
        buckets.setdefault(shape, []).append(index)
    ordered = sorted(buckets.items(), key=lambda item: item[1][0])
    return [{"frames": indices, "types": list(shape)} for shape, indices in ordered]


def _types_from_shapes(shapes: object) -> list[tuple[str, ...]] | None:
    """把基线里的形状表**还原**成逐帧事件类型清单（还原不了 = 结构不对）。

    形状表里没被任何形状覆盖的帧还原成空清单——**基线自身的不自洽由
    比对层查**（每帧的类型条数必须等于该帧的事件数）。
    """
    if not isinstance(shapes, list):
        return None
    per_frame: list[tuple[str, ...]] = []
    for entry in shapes:
        if not isinstance(entry, dict):
            return None
        types = entry.get("types")
        indices = entry.get("frames")
        if not isinstance(types, list) or not isinstance(indices, list):
            return None
        if not all(isinstance(item, str) for item in types):
            return None
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in indices):
            return None
        shape = tuple(str(item) for item in types)
        for index in indices:
            while len(per_frame) <= index:
                per_frame.append(())
            per_frame[index] = shape
    return per_frame


def _baseline_document(samples: list[FrameSample]) -> dict[str, object]:
    """事件数基线文件的内容（**逐字节可复现**：不写时间戳）。

    **只记常态帧**（`tick` 被接受的那些帧）——拒绝路径每帧只有 2 条事件，
    混进来会把"每帧事件数"这个开放项的答案拉低一大截。

    **条数与类型都记**：只记条数的话，"条数一样、类型换了"的行为改动
    看不见（M0 全量审查 M0-8）。
    """
    normal = [sample for sample in samples if sample.accepted]
    counts = [sample.events for sample in normal]
    types = [sample.event_types for sample in normal]
    return {
        "meta": {
            "frames": BASELINE_FRAMES,
            "generatedBy": "tools/measure_frame_cost.py",
            "normalFrames": len(normal),
            "note": (
                "只记常态帧的逐帧事件数与事件类型；"
                "耗时不进基线（墙钟噪声跨度可达 3 倍，批次 D 审查 D7）"
            ),
            "seed": BASELINE_SEED,
            "source": "内核无头帧循环（tetris_kernel.host.HeadlessLoop）",
            "warmupFrames": BASELINE_WARMUP,
        },
        "eventsPerFrame": {
            "max": max(counts),
            "p50": percentile(counts, 0.50),
            "p99": percentile(counts, 0.99),
            "total": sum(counts),
        },
        "frameEventCounts": counts,
        "frameEventTypeShapes": _type_shapes(types),
    }


def _compare_baseline(
    document: dict[str, object],
    counts: list[int],
    types: list[tuple[str, ...]],
) -> list[str]:
    """比对当前事件数与基线，返回差异描述（**空 = 一致**）。

    **逐帧比**：只看 P50 / max 的话，"多出来的 3 条事件挪到了别的帧上"
    会溜过去——那正是行为变了。

    **条数与类型都比**：条数一样而类型换了，同样是行为变了（M0-8）。
    """
    problems: list[str] = []
    baseline_counts = document.get("frameEventCounts")
    if not isinstance(baseline_counts, list):
        return ["基线文件结构不对（缺 frameEventCounts）"]

    if len(baseline_counts) != len(counts):
        problems.append(f"帧数不同：基线 {len(baseline_counts)} 帧，当前 {len(counts)} 帧")
        return problems

    baseline_types = _types_from_shapes(document.get("frameEventTypeShapes"))
    if baseline_types is None:
        problems.append("基线文件结构不对（缺 frameEventTypeShapes）")
        return problems
    if len(baseline_types) != len(baseline_counts):
        problems.append(
            f"基线自述的类型形状还原出 {len(baseline_types)} 帧，"
            f"事件数基线却是 {len(baseline_counts)} 帧：基线自身不自洽"
        )
        return problems

    # **基线自身要自洽**：每一帧的类型条数必须等于该帧的事件数。
    for index, (shape, count) in enumerate(zip(baseline_types, baseline_counts, strict=True)):
        if len(shape) != count:
            problems.append(
                f"基线第 {index} 帧的事件类型 {len(shape)} 条 ≠ 事件数 {count} 条：基线自身不自洽"
            )
            return problems

    differences = [
        (index, old, new)
        for index, (old, new) in enumerate(zip(baseline_counts, counts, strict=True))
        if old != new
    ]
    for index, old, new in differences[:10]:
        problems.append(f"第 {index} 帧（0 起）的事件数不同：基线 {old} ≠ 当前 {new}")
    if len(differences) > 10:
        problems.append(f"……共 {len(differences)} 帧不同（只列前 10 帧）")

    type_differences = [
        (index, old, new)
        for index, (old, new) in enumerate(zip(baseline_types, types, strict=True))
        if old != new
    ]
    for index, old, new in type_differences[:10]:
        problems.append(f"第 {index} 帧（0 起）的事件类型不同：基线 {list(old)} ≠ 当前 {list(new)}")
    if len(type_differences) > 10:
        problems.append(f"……共 {len(type_differences)} 帧的事件类型不同（只列前 10 帧）")
    return problems


class _ReferencePolicy(Protocol):
    """参考策略的**最小接口**（只要求 `decide`）。

    **为什么写 Protocol 而不是直接 import 那个类**：`tools/` **不静态 import
    `tests/`**（那会让类型检查去管一棵测试树），动态载入的模块拿不到静态
    类型——所以这里**只声明用得到的那一个方法**。
    """

    def decide(self, snapshot: Snapshot) -> tuple[str, ...]:
        """按当前快照给出**要派发的命令类型名**（不带参数）。"""
        ...


def _load_reference_policy() -> tuple[_ReferencePolicy, str]:
    """从**测试树**里取参考策略（`tests/support/policy.py`）。

    **为什么从测试树取**：参考策略是**测试件**——门判据与基准都用它，但
    `src/` 里**不放**它（库里不 shipping 一个"参考 AI"：`systems/ai` 是将来
    AI 系统的位置，不是现在塞一个参考策略的地方）。

    **动态载入**：`tools/` 不静态 import `tests/`（那会让类型检查去管一棵
    测试树）。**载入不到就明确报错**——**不静默退化成"纯重力局"**：那量的是
    另一件事（每局 7671 帧 vs 参考策略的约 2800 帧），两个数对不上，
    `G2-4` 的预算就失去意义了。
    """
    if str(TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(TESTS_DIR))
    try:
        module = importlib.import_module("support.policy")
    except ImportError as error:  # pragma: no cover - 环境不对才会走到
        raise RuntimeError(
            f"取参考策略失败（{TESTS_DIR / 'support' / 'policy.py'}）：{error}"
        ) from error
    policy = cast(_ReferencePolicy, module.ReferencePolicy())
    return policy, cast(str, module.POLICY_SOURCE)


def _play_one_game(seed: int) -> tuple[int, float]:
    """**一局的任务**：按参考策略玩到顶出，返回（帧数 · 耗时秒）。

    **进程池里的那个任务**（ADR-018：每局一个任务）。

    **与门判据那一批同源**：同一份参考策略、同一张种子表（`THOUSAND_BASE_SEED`
    起）——所以工具量出来的数与 `G2-4` 判的那个数**可以互相印证**（spec §7.3：
    两档跑的是同一批局）。**少的那一段是"重放"**：工具只量"玩"，
    门判据量的是"录 + 放"——**工具的数一定不大于门判据的数**。
    """
    policy, source = _load_reference_policy()
    assembly = assemble(seed=seed)
    loop = HeadlessLoop(assembly)
    loop.start()
    kernel = assembly.kernel
    started = time.perf_counter()
    while kernel.phase != PHASE_GAME_OVER:
        for name in policy.decide(kernel.snapshot()):
            kernel.submit(Command(type=name, source=source))
        loop.run_frame()
    elapsed = time.perf_counter() - started
    return kernel.snapshot().frame, elapsed


def _thousand_games_mode(args: argparse.Namespace) -> int:
    """千局基准模式：跑 N 局无头模拟，报总时长与预算对照。

    **多进程**（ADR-018）：每局一个任务，进程池大小 = CPU 核数，
    `chunksize > 1`，**汇总前按种子排序**（`pool.map` 保序，结果天然按种子
    排好）。

    **"关闭事件分发"的快速模式被明确否决**（ADR-018 · B-1 · B-10）——那会
    破坏事件完备性。

    **判的是墙钟总时长**：并行跑完 N 局用了多久。**每局耗时之和**也报出来
    （那是串行跑同样的活要多久），**但它不是判据**——`G2-4` 的 30 分钟预算是
    "并行跑完 1000 局"的预算（串行要 54 分钟，超预算）。
    """
    games = args.thousand_games
    if games <= 0:
        print("--thousand-games 必须为正", file=sys.stderr)
        return 2
    budget = args.budget_seconds
    workers = min(games, args.workers if args.workers > 0 else (os.cpu_count() or 1))
    seeds = tuple(args.thousand_seed + index for index in range(games))

    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_play_one_game, seeds, chunksize=THOUSAND_CHUNKSIZE))
    elapsed = time.perf_counter() - started

    total_frames = sum(frames for frames, _seconds in results)
    serial_seconds = sum(seconds for _frames, seconds in results)
    per_game = serial_seconds / games

    print(f"局数：{games}（种子 {args.thousand_seed} 起 · 参考策略局）")
    print(f"进程池大小：{workers}（CPU 核数 {os.cpu_count()}）")
    print(f"每局帧数：合计 {total_frames} · 平均 {total_frames // games}")
    print(
        f"每局耗时：平均 {per_game:.3f}s · 合计 {serial_seconds:.1f}s"
        "（**含并行争用**——它是串行时间的上界，不是串行实测值）"
    )
    print(f"墙钟总时长（并行跑完 {games} 局）：{elapsed:.1f}s")
    print(f"对照 `G2-4` 预算 {budget}s：{'在预算内' if elapsed <= budget else '超预算'}")
    if elapsed > budget:
        print(
            f"千局基准超预算：{elapsed:.1f}s > {budget}s。",
            file=sys.stderr,
        )
        return 1
    return 0


def _baseline_mode(args: argparse.Namespace) -> int:
    """基线模式：跑固定场景，比对或写盘事件数基线。

    **比较逻辑住在工具里**：测试只调工具、只断言退出码——把比较逻辑抄一份
    进测试就是两处会悄悄分叉的副本（`ai_docs` 里否掉的"影子副本"）。
    """
    try:
        samples = _collect(seed=BASELINE_SEED, frames=BASELINE_FRAMES, warmup=BASELINE_WARMUP)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2

    normal = [sample for sample in samples if sample.accepted]
    counts = [sample.events for sample in normal]
    types = [sample.event_types for sample in normal]

    if args.baseline_check is not None:
        path = args.baseline_check
        if not path.exists():
            print(f"事件数基线不存在：{relative_to_repo(path)}", file=sys.stderr)
            return 2
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"读事件数基线失败：{error}", file=sys.stderr)
            return 2
        if not isinstance(document, dict):
            print(f"事件数基线结构不对：{relative_to_repo(path)}", file=sys.stderr)
            return 2
        problems = _compare_baseline(document, counts, types)
        if problems:
            for problem in problems:
                print(f"事件数基线不一致（红）：{problem}", file=sys.stderr)
            print("事件数基线校验失败：内核每帧发出的事件数变了。", file=sys.stderr)
            return 1
        print(
            f"事件数基线校验通过：{relative_to_repo(path)}"
            f"（{len(counts)} 帧常态帧 · P50 {percentile(counts, 0.50)}"
            f" · P99 {percentile(counts, 0.99)} · max {max(counts)}）"
        )
        return 0

    # --baseline-out：刷新（必须带理由）。
    path = args.baseline_out
    assert path is not None
    document = _baseline_document(samples)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"已写入事件数基线：{relative_to_repo(path)}（{len(counts)} 帧常态帧）")
    print(f"刷新理由：{args.reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    configure_output()
    parser = argparse.ArgumentParser(description="采集单帧逻辑开销（P50/P99）与每帧事件数")
    parser.add_argument("--frames", type=int, default=10000, help="要采集的帧数")
    parser.add_argument("--seed", type=int, default=1, help="随机种子（复现用）")
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP,
        help=f"预热帧数（不计入样本，默认 {DEFAULT_WARMUP}）",
    )
    parser.add_argument("--out", type=Path, default=None, help="把结果写成 JSON")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="用合成样本自检统计口径（不是内核的真实性能值）",
    )
    parser.add_argument(
        "--baseline-out",
        type=Path,
        default=None,
        help="生成 / 刷新事件数基线（固定场景；必须带 --reason）",
    )
    parser.add_argument(
        "--baseline-check",
        type=Path,
        default=None,
        help="校验事件数基线（固定场景重跑一遍，逐帧比对）",
    )
    parser.add_argument(
        "--reason",
        type=str,
        default=None,
        help="刷新基线的理由（--baseline-out 必须带）",
    )
    parser.add_argument(
        "--no-observability",
        action="store_true",
        help=(
            "关掉三个可观测系统（**M2 才有这个开关**；配对 A/B 的「内核裸跑」那一档"
            " · **只作用于单帧采样**，不影响千局基准）"
        ),
    )
    parser.add_argument(
        "--thousand-games",
        type=int,
        default=0,
        help=(
            "千局基准模式：跑这么多局无头模拟，报总时长与 `G2-4` 预算的对照"
            f"（默认局数 {DEFAULT_THOUSAND_GAMES}，给 0 表示不开这个模式）"
        ),
    )
    parser.add_argument(
        "--thousand-seed",
        type=int,
        default=THOUSAND_BASE_SEED,
        help=(
            "千局基准的种子表起点"
            f"（默认 {THOUSAND_BASE_SEED} = 门判据的 `THOUSAND_BASE_SEED`，**同一批局**）"
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="进程池大小（0 = CPU 核数 · ADR-018）",
    )
    parser.add_argument(
        "--budget-seconds",
        type=float,
        default=float(THOUSAND_BUDGET_SECONDS),
        help=f"千局基准的预算秒数（默认 {THOUSAND_BUDGET_SECONDS} = `G2-4` 的 30 分钟）",
    )
    args = parser.parse_args(argv)

    if args.baseline_out is not None and args.baseline_check is not None:
        print("--baseline-out 与 --baseline-check 不能同时给", file=sys.stderr)
        return 2
    if args.baseline_out is not None and not args.reason:
        parser.error('--baseline-out 必须带 --reason "<理由>"：基线刷新要写明理由，不做无声覆盖。')
    if args.baseline_out is not None or args.baseline_check is not None:
        return _baseline_mode(args)

    if args.thousand_games > 0:
        return _thousand_games_mode(args)

    if args.frames <= 0:
        print("--frames 必须为正", file=sys.stderr)
        return 2
    if args.warmup < 0:
        print("--warmup 不能为负", file=sys.stderr)
        return 2

    try:
        if args.self_test:
            # **自检固定参数**：期望值是写死的，参数也写死——
            # 否则"验"的就是本次随手给的参数，不是统计口径。
            samples = list(_synthetic_samples(SELF_TEST_FRAMES, SELF_TEST_SEED))
            source = "合成样本（自检口径，非内核真实值）"
            warmup = 0
        else:
            samples = _collect(
                seed=args.seed,
                frames=args.frames,
                warmup=args.warmup,
                observability=not args.no_observability,
            )
            source = "内核无头帧循环（tetris_kernel.host.HeadlessLoop）"
            warmup = args.warmup
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2

    if args.self_test:
        problems = _self_test()
        if problems:
            print("自检失败：统计口径与写死的期望值不符：", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        print(
            f"自检通过：最近秩百分位与合成样本统计口径均与期望值相等"
            f"（{SELF_TEST_FRAMES} 帧 · 种子 {SELF_TEST_SEED}）"
        )
        print()

    normal_samples, rejected_samples = _split(samples)
    try:
        total = summarize([s.cost_us for s in samples], [s.events for s in samples])
        normal = summarize([s.cost_us for s in normal_samples], [s.events for s in normal_samples])
        rejected = (
            summarize([s.cost_us for s in rejected_samples], [s.events for s in rejected_samples])
            if rejected_samples
            else None
        )
    except ValueError as error:
        print(f"汇总失败：{error}", file=sys.stderr)
        return 2

    report = Report(
        source=source,
        seed=SELF_TEST_SEED if args.self_test else args.seed,
        warmup=warmup,
        total=total,
        normal=normal,
        rejected=rejected,
        rejected_phases=tuple(sorted({s.phase for s in rejected_samples})),
        rejected_from_frame=_first_rejected_index(samples),
    )

    print(f"数据来源：{source}")
    print(f"种子：{report.seed}")
    print(f"预热帧：{report.warmup}（不计入样本）")
    print(f"采集帧数：{report.total.frames}")
    print()
    _print_segment("常态帧（tick 被接受 · phase=running）", report.normal)
    _print_budget(report.normal)
    print()
    if report.rejected is None:
        print("【拒绝路径（tick 被门禁拒）】本轮未进入")
    else:
        phases = " · ".join(report.rejected_phases)
        _print_segment(f"拒绝路径（tick 被门禁拒 · phase={phases}）", report.rejected)
        print(f"  起点：采样窗口第 {report.rejected_from_frame} 帧（0 起）")
        _print_budget(report.rejected)
    print()
    _print_segment("全量（常态帧 + 拒绝路径）", report.total)
    _print_budget(report.total)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"已写入：{relative_to_repo(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
