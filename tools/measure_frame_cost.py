"""采集单帧逻辑开销（P50 / P99）与每帧事件数（S16 用 · 开放项 B / C）。

## 采什么

- **单帧逻辑开销**：一帧里内核逻辑耗多少微秒（**不含渲染**，渲染另有
  `render:frame.durationUs`）。开放项 B 要的就是这个真实值——不标定就没法
  验收性能预算与降级阈值。
- **每帧真实事件数**：一帧发出多少条事件。开放项 C 同理。

## 为什么现在还采不到真值

内核闭环在 **S14** 才落地（现在 `kernel/` 还是空壳）。本脚本先把**测量口径**
与**统计口径**定死，等 S14 的无头帧循环接上来就能直接采。

**口径**：

- 用 `time.perf_counter()` 量**墙钟耗时**——这是测量工具，不是内核，
  架构 §5.3 的禁读墙钟只约束 `kernel/` 与 `rules/`（本脚本在 `tools/`）。
- **百分位用最近秩（nearest-rank）**，不插值：口径确定、可复现，
  同一批样本两次算出来一样。浮点插值会引出"四舍五入到第几位"这种
  没意义的争论。
- 统计**每帧**的耗时与事件数，不做跨帧平滑——平滑会把尖峰藏起来，
  而尖峰正是降级阈值要防的东西。

## 帧循环的接口约定（S14 落地时照此实现）

```python
from tetris_kernel.host import headless

for elapsed_us, event_count in headless.run_frames(seed=1, frames=10000):
    ...
```

`run_frames(seed, frames)` 是**生成器**，逐帧吐出 `(耗时微秒, 事件条数)`。

## 用法

```
python tools/measure_frame_cost.py --self-test          # 自检：合成样本，验统计口径
python tools/measure_frame_cost.py --frames 10000        # 采内核闭环（S14 后可用）
python tools/measure_frame_cost.py --frames 10000 --out out.json
```

退出码：0 = 采到了；2 = 用法错误或内核闭环尚未就绪。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from _toolkit import configure_output, relative_to_repo

#: 帧循环的约定位置（S14 落地后才有）。
RUNNER_MODULE = "tetris_kernel.host.headless"
RUNNER_FUNCTION = "run_frames"


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
    )


def _load_runner() -> object:
    """取内核的无头帧循环（S14 之后才有）。"""
    import importlib

    try:
        module = importlib.import_module(RUNNER_MODULE)
    except ModuleNotFoundError as error:
        raise RuntimeError(
            f"内核闭环尚未就绪（{RUNNER_MODULE} 不存在）：无头帧循环在 S14 落地。"
            "现在只能用 --self-test 验证统计口径。"
        ) from error
    runner = getattr(module, RUNNER_FUNCTION, None)
    if runner is None:
        raise RuntimeError(f"{RUNNER_MODULE} 里没有 {RUNNER_FUNCTION}()")
    return runner


def _synthetic_samples(frames: int, seed: int) -> Iterator[tuple[int, int]]:
    """合成样本（自检用）：确定性的耗时与事件数。

    **只用来验统计口径**，不是内核的真实数据——报告里不能拿它当性能值。
    """
    generator = random.Random(seed)
    for _ in range(frames):
        cost_us = generator.randint(40, 400)
        events = generator.randint(0, 6)
        yield cost_us, events


def _measure(frames: int, seed: int) -> tuple[list[int], list[int]]:
    """跑帧循环，收样本。"""
    runner = _load_runner()
    costs: list[int] = []
    events: list[int] = []
    for sample in runner(seed=seed, frames=frames):  # type: ignore[operator]
        elapsed_us, event_count = sample
        costs.append(int(elapsed_us))
        events.append(int(event_count))
    return costs, events


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    configure_output()
    parser = argparse.ArgumentParser(description="采集单帧逻辑开销（P50/P99）与每帧事件数")
    parser.add_argument("--frames", type=int, default=10000, help="要采集的帧数")
    parser.add_argument("--seed", type=int, default=1, help="随机种子（复现用）")
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

    try:
        if args.self_test:
            costs: list[int] = []
            events: list[int] = []
            for cost_us, event_count in _synthetic_samples(args.frames, args.seed):
                costs.append(cost_us)
                events.append(event_count)
            source = "合成样本（自检口径，非内核真实值）"
        else:
            costs, events = _measure(args.frames, args.seed)
            source = "内核无头帧循环"
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2

    try:
        summary = summarize(costs, events)
    except ValueError as error:
        print(f"汇总失败：{error}", file=sys.stderr)
        return 2

    print(f"数据来源：{source}")
    print(f"帧数：{summary.frames}")
    print(
        f"单帧逻辑开销（微秒）：P50 {summary.cost_p50_us} · P99 {summary.cost_p99_us}"
        f" · 最大 {summary.cost_max_us} · 合计 {summary.cost_total_us}"
    )
    print(
        f"每帧事件数：P50 {summary.events_p50} · P99 {summary.events_p99}"
        f" · 最大 {summary.events_max} · 合计 {summary.events_total}"
    )

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        document = dict(summary.to_dict())
        document["source"] = source
        document["seed"] = args.seed
        args.out.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"已写入：{relative_to_repo(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
