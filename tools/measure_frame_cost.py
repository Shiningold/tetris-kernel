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

## 用法

```
python tools/measure_frame_cost.py --self-test          # 自检：合成样本，验统计口径
python tools/measure_frame_cost.py --frames 10000        # 采真实值
python tools/measure_frame_cost.py --frames 10000 --warmup 1000 --out out.json
```

退出码：0 = 采到了；2 = 用法错误或采样口径被破坏。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from _toolkit import SRC_ROOT, configure_output, relative_to_repo

sys.path.insert(0, str(SRC_ROOT))

from tetris_kernel.host import HeadlessLoop, assemble  # noqa: E402
from tetris_kernel.kernel import PHASE_GAME_OVER, PHASE_RUNNING  # noqa: E402

#: NFR-2 的单帧逻辑预算（微秒）：60fps 帧步长 16.67ms 的 10% = 1.6ms。
#: 需求 NFR-2 · spec-M0.md 验收表「性能」行。**M0 只标定真实值**，
#: 超预算之后的降级实现是 M2 的 X-3。
BUDGET_US = 1600

#: 默认预热帧数（不计入样本）。
DEFAULT_WARMUP = 1000


@dataclass(frozen=True)
class FrameSample:
    """一帧的样本。

    - `cost_us`：这一帧的墙钟耗时（微秒，整数）；
    - `events`：这一帧发出的事件条数；
    - `phase`：**该帧开始时**的 phase——门禁读的就是它，所以它决定这一帧的
      `tick` 是被接受还是被拒。
    """

    cost_us: int
    events: int
    phase: str

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


def _collect(*, seed: int, frames: int, warmup: int) -> list[FrameSample]:
    """跑真实的无头帧循环，收逐帧样本。

    **计时只包住 `run_frame()`**：phase 的读取在计时区外——它是分类依据，
    不是被测对象。

    **预热帧照跑但不收**：跑预热是为了让进程进入稳态，样本只取预热之后的
    那些帧。
    """
    assembly = assemble(seed=seed)
    loop = HeadlessLoop(assembly)
    loop.start()
    kernel = assembly.kernel
    for _ in range(warmup):
        loop.run_frame()

    samples: list[FrameSample] = []
    for index in range(frames):
        phase_before = kernel.phase
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
        samples.append(
            FrameSample(cost_us=round(elapsed_us), events=event_count, phase=phase_before)
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
    args = parser.parse_args(argv)

    if args.frames <= 0:
        print("--frames 必须为正", file=sys.stderr)
        return 2
    if args.warmup < 0:
        print("--warmup 不能为负", file=sys.stderr)
        return 2

    try:
        if args.self_test:
            samples = list(_synthetic_samples(args.frames, args.seed))
            source = "合成样本（自检口径，非内核真实值）"
            warmup = 0
        else:
            samples = _collect(seed=args.seed, frames=args.frames, warmup=args.warmup)
            source = "内核无头帧循环（tetris_kernel.host.HeadlessLoop）"
            warmup = args.warmup
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2

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
        seed=args.seed,
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
