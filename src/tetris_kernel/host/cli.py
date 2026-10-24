"""命令行入口（S14 · plan-M0.md §1.4.4.1 ④）。

`pyproject.toml` 的 `tetris = "tetris_kernel.host.cli:main"` 已经钉死
`main` 这个名字与它的位置——**别改**。

## 四个子命令

| 子命令 | M0 的状态 |
| --- | --- |
| `sim` | **跑通**：无头批量模拟，出统计 |
| `play` | 占位（交互式游玩要渲染与输入适配器，M1 之后） |
| `replay` | 占位（录像回放要落盘与录像格式，M1 之后） |
| `inspect` | 占位 |

占位子命令**要能跑、不许报错**：M0 的门是「无头闭环」，占位命令只是先把
命令行骨架立起来。

## `sim` 的输出

必须含：**帧数 · 事件数 · 最终棋盘指纹 · 分数 / 行数 / 已放置方块数**
（§1.4.4.1 ④）。`--json` 给机器可读的同款字段。
"""

from __future__ import annotations

import argparse
import json
import sys

from .assembly import assemble
from .loop import HeadlessLoop, RunReport

#: `sim` 的默认帧数。
DEFAULT_FRAMES = 100

#: `sim` 的默认种子。
DEFAULT_SEED = 0


def main(argv: list[str] | None = None) -> int:
    """命令行入口（**返回退出码**，不抛异常）。

    `argv` 省略时取 `sys.argv[1:]`。
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    match args.command:
        case "sim":
            return _cmd_sim(args)
        case _:
            return _cmd_placeholder(args)


def _build_parser() -> argparse.ArgumentParser:
    """建解析器（四个子命令）。"""
    parser = argparse.ArgumentParser(
        prog="tetris",
        description="俄罗斯方块内核 · 命令行（M0）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sim = subparsers.add_parser("sim", help="无头批量模拟（M0 唯一跑通的子命令）")
    sim.add_argument(
        "--frames",
        type=int,
        default=DEFAULT_FRAMES,
        help=f"跑多少帧（默认 {DEFAULT_FRAMES}）",
    )
    sim.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"内核随机源种子（默认 {DEFAULT_SEED}）",
    )
    sim.add_argument("--json", action="store_true", help="机器可读输出（JSON）")

    for name, help_text in (
        ("play", "交互式游玩（M0 占位）"),
        ("replay", "录像回放（M0 占位）"),
        ("inspect", "查看内核状态（M0 占位）"),
    ):
        subparsers.add_parser(name, help=help_text)
    return parser


def _cmd_sim(args: argparse.Namespace) -> int:
    """`sim`：无头跑 n 帧，出统计。"""
    frames = int(args.frames)
    seed = int(args.seed)
    if frames < 0:
        print("帧数不能为负", file=sys.stderr)
        return 2

    assembly = assemble(seed=seed)
    loop = HeadlessLoop(assembly)
    loop.start()
    report = loop.run_frames(frames)

    if bool(args.json):
        print(json.dumps(_report_dict(report), ensure_ascii=False))
    else:
        for line in _report_lines(report):
            print(line)
    return 0


def _cmd_placeholder(args: argparse.Namespace) -> int:
    """占位子命令：**要能跑、不许报错**（M0 只要求 `sim` 跑通）。"""
    print(f"「{args.command}」在 M0 尚未实现（占位命令）。")
    return 0


def _report_dict(report: RunReport) -> dict[str, object]:
    """报告的机器可读形态（**字段名与 `RunReport` 一致**）。"""
    return {
        "frames": report.frames,
        "events": report.events,
        "fingerprint": report.fingerprint,
        "score": report.score,
        "lines": report.lines,
        "piecesPlaced": report.piecesPlaced,
        "phase": report.phase,
    }


def _report_lines(report: RunReport) -> tuple[str, ...]:
    """报告的人读形态（**六项统计一项都不能少**）。"""
    return (
        f"帧数：{report.frames}",
        f"事件数：{report.events}",
        f"棋盘指纹：{report.fingerprint}",
        f"分数：{report.score}",
        f"行数：{report.lines}",
        f"已放置方块数：{report.piecesPlaced}",
        f"阶段：{report.phase}",
    )


__all__ = [
    "DEFAULT_FRAMES",
    "DEFAULT_SEED",
    "main",
]
