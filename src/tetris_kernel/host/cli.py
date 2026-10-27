"""命令行入口（S14 · plan-M0.md §1.4.4.1 ④）。

`pyproject.toml` 的 `tetris = "tetris_kernel.host.cli:main"` 已经钉死
`main` 这个名字与它的位置——**别改**。

## 四个子命令

| 子命令 | 状态 |
| --- | --- |
| `sim` | **跑通**：无头批量模拟，出统计 |
| `play` | 占位（交互式游玩要渲染与输入适配器，M3–M4） |
| `replay` | **跑通**（M2）：读录像 → 重放 → 输出四项比对 |
| `inspect` | **跑通**（M2 · S11）：跑 n 帧 → 只读事件流视图（按命名空间过滤 · 可暂停） |

占位子命令**要能跑、不许报错**：M0 的门是「无头闭环」，占位命令只是先把
命令行骨架立起来。

## `inspect` 的输出

**只读事件流**：一行一条事件（序号 · 帧号 · 类型 · 负载）。

- **按命名空间过滤**（`--namespace`，可重复）：过滤的是**命名空间**（事件类型
  名里 `:` 前的那一段），**不是事件名**——需求 §8.1 的约定就是"事件按命名
  空间划分"，ADR-075 正是为了让 `replay:` 与 `game:` **在过滤时分开**；
- **可暂停**（`--pause-at`）：跑到第 N 帧就**暂停视图**——之后**不再有新事件
  进入视图**，**内核照跑**（订阅者拦不住总线，也拦不住内核）；
- **可导出**（`--export`）：把**完整轨迹**落成文件（JSON Lines）。

**面板的留存有界**（审查 M4）：面板是环形缓冲，默认只留**最新的 4096 条**
（`DEFAULT_PANEL_LIMIT`）——长跑不会驻留百万级信封。**`--export` 时放开成
不限**：那是用户自己点的"全量轨迹"，内存代价自己担。

**可观测系统一律不装配**（`observability_enabled=False`）：面板要看的是**内核
的事件流**——多几个订阅者只会多几条 `log:entry` / `metric:sampled` 混在里面。

**两种 JSON 形态的分工**：`--json` 的**视图**用**对象**（`jq` 好取），轨迹
**文件**用**有序的字段对**（读回来能**逐条还原**，重复键不丢——见
`systems/metrics/trace.py`）。

## `replay` 的输出

**四项比对**：录像里的局末四项 vs 重放出来的四项——**逐条相等**才算过
（AC-1 · spec §7.3）。

**退出码三条口径**：

| 情形 | 退出码 | 为什么 |
| --- | --- | --- |
| 四项全等 | **0** | 过了 |
| 四项不等 | **1** | 检查做了，没过 |
| **录像里没有局末四项** | **2** | **没比**——"没比"不是"过了"（审查 H3） |

**没有基准就没有比对**：录像里没有局末四项时，只输出重放的四项——**但那不
算通过**，退出码是 2。脚本把 0 当成"过了"，一条**没校验过**的录像就放过去
了。

## `sim` 的输出

必须含：**帧数 · 事件数 · 最终棋盘指纹 · 分数 / 行数 / 已放置方块数**
（§1.4.4.1 ④）。`--json` 给机器可读的同款字段。

## 两种输出形态（**M1 的 `--until` 把它分成两种**）

| 模式 | 人读形态 | `--json` 形态 |
| --- | --- | --- |
| **不带 `--until`**（M0 的口径） | 七行中文标签统计 | 七个键 |
| **带 `--until game-over`**（M1） | `key=value` 结算块 | 十个键 |

**不带 `--until` 时的输出与 M0 逐字一致**——**M0 的既有用例一条都不许动**
（plan-M1.md S12 段的验证方案第 3 条）。所以 `RunReport` 加了三个结算字段
（`combo` / `b2b` / `level`）之后，**M0 形态的输出仍然是那七项**；结算形态
才把十项全给出来。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from ..adapters.storage import FileSink
from ..bus.envelope import Envelope
from ..systems.metrics.trace import DEFAULT_PANEL_LIMIT, EventPanel
from ..systems.replay.format import recording_from_jsonl
from ..systems.replay.playback import PlaybackSession
from .assembly import assemble, rule_set_of
from .loop import HeadlessLoop, RunReport
from .replay import REPLAY_LIMITS, KernelReplayDriver

#: `sim` 的默认帧数（**不带 `--until` 时**）。
DEFAULT_FRAMES = 100

#: `sim --until game-over` 的**帧数上限**（没显式给 `--frames` 时用这个）。
#:
#: **为什么要有上限**：跑到顶出是"跑到某个时刻"，但**万一永远顶不出**（棋盘
#: 一直填不满）就得有个兜底——**没有上限就是挂死**。
#:
#: **这个数怎么定的**：实测 8 个种子，只靠重力堆到顶出分别是 7290–7951 帧
#: （**一局约 9 秒**——瓶颈在快照指纹的计算，见 `ai_docs/261003.md`）。取
#: **50000** 是约 6 倍余量：既够跑完一整局，又不会在"顶不出"时挂太久。
DEFAULT_FRAMES_UNTIL_GAME_OVER = 50_000

#: `sim` 的默认种子。
DEFAULT_SEED = 0

#: `inspect` 的默认帧数（**跑多少帧**）。
#:
#: **为什么比 `sim` 少**：事件流是**每帧十几条**——100 帧就是一千多条，打印
#: 出来没人看。**视图默认只看一小段**，要看全的用 `--export` 落文件。
DEFAULT_INSPECT_FRAMES = 20

#: `inspect` 默认最多显示多少条（`0` = 全部）。
DEFAULT_INSPECT_LIMIT = 100


def _force_utf8_output() -> None:
    """stdout / stderr **被管道接走**时显式切 UTF-8。

    Windows 的 stdout 在**不是 TTY** 时用**本地代码页**（本机 GBK / CP936），
    而这里的输出含中文——机器消费方按 UTF-8 读会炸（`UnicodeDecodeError`）。
    `--json` 路径不受影响（字段名与取值全是 ASCII），但人读路径是**第一个用户
    可见面**，机器消费方也常从它取数。

    **只对非 TTY 生效**：控制台自己那条路 Python 已经处理好了（Windows 控制台
    IO 走 Unicode API），硬切 UTF-8 反而可能让控制台显示乱码。

    `sys.stdout` 的类型是 `TextIO`，而 `reconfigure` 只在 `TextIOWrapper` 上——
    **用 isinstance 收窄**，而不是 `# type: ignore` 把这一行变成"永不检查的
    地方"。
    """
    for stream in (sys.stdout, sys.stderr):
        if not isinstance(stream, io.TextIOWrapper):
            continue
        try:
            if stream.isatty():
                continue
        except (OSError, ValueError):
            pass
        try:
            stream.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    """命令行入口（**返回退出码**）。

    `argv` 省略时取 `sys.argv[1:]`。

    **参数错误走 `SystemExit`**：argparse 的 `parser.error()` 会抛
    `SystemExit(2)`，穿出 `main()`——这是 argparse 的通行惯例，进程级行为是对的
    （`__main__.py` 的 `sys.exit(main())` 与 `SystemExit` 都让进程以 2 退出）。
    所以"返回退出码"说的是**本函数自己判定的那几条路径**（如帧数为负）。
    """
    _force_utf8_output()
    parser = _build_parser()
    args = parser.parse_args(argv)
    match args.command:
        case "sim":
            return _cmd_sim(args)
        case "replay":
            return _cmd_replay(args)
        case "inspect":
            return _cmd_inspect(args)
        case _:
            return _cmd_placeholder(args)


def _build_parser() -> argparse.ArgumentParser:
    """建解析器（四个子命令）。"""
    parser = argparse.ArgumentParser(
        prog="tetris",
        description="俄罗斯方块内核 · 命令行（M1 · 标准玩法可无头玩到底）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sim = subparsers.add_parser("sim", help="无头批量模拟（唯一跑通的子命令）")
    sim.add_argument(
        "--frames",
        type=int,
        default=None,
        help=(
            f"跑多少帧（不带 --until 时默认 {DEFAULT_FRAMES}；"
            f"带 --until game-over 时默认 {DEFAULT_FRAMES_UNTIL_GAME_OVER}）"
        ),
    )
    sim.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"内核随机源种子（默认 {DEFAULT_SEED}）",
    )
    sim.add_argument(
        "--until",
        choices=("game-over",),
        default=None,
        help="跑到顶出就停帧，并输出结算八项（M1 · spec §7.8）",
    )
    sim.add_argument("--json", action="store_true", help="机器可读输出（JSON）")

    replay = subparsers.add_parser(
        "replay",
        help="录像回放：读录像 → 重放 → 输出四项比对（M2）",
    )
    replay.add_argument("file", help="录像文件路径")
    replay.add_argument("--json", action="store_true", help="机器可读输出（JSON）")

    inspect = subparsers.add_parser(
        "inspect",
        help="只读事件流视图：跑 n 帧 → 按命名空间过滤事件流（M2 · FR-7.3）",
    )
    inspect.add_argument(
        "--frames",
        type=int,
        default=DEFAULT_INSPECT_FRAMES,
        help=f"跑多少帧（默认 {DEFAULT_INSPECT_FRAMES}）",
    )
    inspect.add_argument(
        "--namespace",
        action="append",
        default=None,
        help="只看这些命名空间的事件（可重复；省略 = 全部）",
    )
    inspect.add_argument(
        "--pause-at",
        type=int,
        default=None,
        help="跑到第几帧就暂停视图（内核照跑；视图只留该帧之前的事件）",
    )
    inspect.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_INSPECT_LIMIT,
        help=(f"最多显示多少条（0 = 面板留住的全部；默认 {DEFAULT_INSPECT_LIMIT}）"),
    )
    inspect.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"内核随机源种子（默认 {DEFAULT_SEED}）",
    )
    inspect.add_argument(
        "--export",
        default=None,
        help="把完整轨迹导出到这个文件（JSON Lines，一行一条）",
    )
    inspect.add_argument("--json", action="store_true", help="机器可读输出（JSON）")

    subparsers.add_parser(
        "play",
        help="交互式游玩（占位；输入适配器与渲染器属 M3–M4）",
    )
    return parser


def _cmd_sim(args: argparse.Namespace) -> int:
    """`sim`：无头跑 n 帧，出统计。

    **不带 `--until`**：M0 的口径——跑 `--frames` 帧（默认 100），出七项统计。
    **带 `--until game-over`**：跑到顶出就停帧，出**结算八项**（spec §7.8）。

    **默认帧数分两种**：`--frames` **没显式给**时，不带 `--until` 是 M0 的 100
    帧（**输出逐字一致**），带 `--until game-over` 是大得多的上限（要跑完整局）。
    """
    until = args.until
    frames = args.frames
    if frames is None:
        frames = DEFAULT_FRAMES_UNTIL_GAME_OVER if until else DEFAULT_FRAMES
    frames = int(frames)
    seed = int(args.seed)
    if frames < 0:
        print("帧数不能为负", file=sys.stderr)
        return 2

    assembly = assemble(seed=seed)
    loop = HeadlessLoop(assembly)
    loop.start()
    report = loop.run_frames(frames, until_game_over=until == "game-over")

    if bool(args.json):
        payload = _settlement_dict(report) if until else _report_dict(report)
        print(json.dumps(payload, ensure_ascii=False))
    else:
        lines = _settlement_lines(report) if until else _report_lines(report)
        for line in lines:
            print(line)
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    """`replay`：读录像 → 重放 → 输出四项比对。

    **重放要按录像头的种子与规则集起一个全新内核**（spec §7.2）——起错规则
    集，重放出来的就不是同一局。**规则集没登记就明确拒绝**（审查 M1）：不拿
    标准规则集顶替。

    **可观测系统一律不装配**（`observability_enabled=False`）：重放要的是
    **内核逐帧一致**，多几个订阅者只会多几条事件。

    **四项不等时退出码是 1**；**录像里没有局末四项时退出码是 2**（没比不是
    过了 · 审查 H3）；**规则集没登记时退出码是 2**（起错规则集 · 审查 M1）
    ——脚本能直接判成败。
    """
    path = Path(args.file)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"读录像失败：{error}", file=sys.stderr)
        return 2
    try:
        recording = recording_from_jsonl(text)
    except ValueError as error:
        print(f"录像读不了：{error}", file=sys.stderr)
        return 2

    # **按录像头的规则集起内核**——起错规则集，重放出来的就不是同一局。
    # **表里没有就明确拒绝**（审查 M1）：拿标准规则集硬跑，重放出来的四项
    # 必然不等，而报错只说"四项不等"，看不出"起错规则集了"。
    rule_set = rule_set_of(recording.rule)
    if rule_set is None:
        print(
            f"录像的规则集 {recording.rule!r} 没有登记的规则集："
            "变体规则的重放待 M4（M2 只认标准规则集）",
            file=sys.stderr,
        )
        return 2

    assembly = assemble(
        seed=recording.seed,
        rule_set=rule_set,
        rule_id=recording.rule,
        mode="replay",
        limits=REPLAY_LIMITS,
        observability_enabled=False,
    )
    driver = KernelReplayDriver(assembly.kernel, assembly.bus)
    session = PlaybackSession(assembly.bus, driver, recording)
    items = session.run()

    baseline = recording.outcome
    equal = None if baseline is None else baseline.equals(items)
    if bool(args.json):
        payload: dict[str, object] = {
            "seed": recording.seed,
            "rule": recording.rule,
            "commandCount": len(recording.commands),
            "recorded": None if baseline is None else baseline.as_dict(),
            "replayed": items.as_dict(),
            "equal": equal,
        }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"种子：{recording.seed}")
        print(f"规则集：{recording.rule}")
        print(f"命令数：{len(recording.commands)}")
        print(f"重放棋盘指纹：{items.fingerprint}")
        print(f"重放分数：{items.score}")
        print(f"重放行数：{items.lines}")
        print(f"重放已放置方块数：{items.pieces_placed}")
        if baseline is not None:
            print(f"录像棋盘指纹：{baseline.fingerprint}")
            print(f"录像分数：{baseline.score}")
            print(f"录像行数：{baseline.lines}")
            print(f"录像已放置方块数：{baseline.pieces_placed}")
            print(f"四项比对：{'全等' if equal else '不等'}")
    if baseline is None:
        # **没有基准就没有比对**——"没比"不是"过了"（审查 H3）。
        #
        # **退出码 2**：这次校验**没做成**（与"四项不等"的 1 区分开）——脚本
        # 看到 0 就当"过了"，一条**没校验过**的录像就这么放过去了。
        print(
            "录像里没有局末四项：无比对基准可比（这次校验没做成，不算通过）",
            file=sys.stderr,
        )
        return 2
    return 0 if equal else 1


def _cmd_inspect(args: argparse.Namespace) -> int:
    """`inspect`：跑 n 帧，把**只读**事件流打出来（FR-7.3 · FR-7.4）。

    **可观测系统一律不装配**（`observability_enabled=False`）：面板要看的
    是**内核的事件流**——多几个订阅者只会多几条 `log:entry` / `metric:sampled`
    混在里面，看的人分不清哪条是内核发的。

    **过滤按命名空间**（不是按事件名）：需求 §8.1 的约定就是"事件按命名空间
    划分"，ADR-075 正是为了让 `replay:` 与 `game:` **在过滤时分开**。

    **暂停的是视图，不是内核**：`--pause-at` 之后**不再有新事件进入视图**，
    内核照跑（订阅者拦不住总线，也拦不住内核）。

    **导出的是完整轨迹**（不过滤、不受 `--limit` 影响）——离线分析要的是
    全量，视图的截断只影响**显示**。
    """
    frames = int(args.frames)
    if frames < 0:
        print("帧数不能为负", file=sys.stderr)
        return 2
    limit = int(args.limit)
    if limit < 0:
        print("条数上限不能为负", file=sys.stderr)
        return 2
    pause_at = args.pause_at
    if pause_at is not None and int(pause_at) < 0:
        print("暂停帧号不能为负", file=sys.stderr)
        return 2
    export_path = args.export

    assembly = assemble(seed=int(args.seed), observability_enabled=False)
    # **面板要在事件开始流动之前订阅**——晚订阅的从 `bus.history()` 里取，
    # 面板**不自动补历史**（它是"从现在起看"的视图）。
    #
    # **留存上限必须显式给**（审查 M4）：面板是环形缓冲——"长跑驻留百万级信封"
    # 是内存负担，不是特性。**要导出就放开上限**（`limit=None`）：那是用户
    # 自己点的"全量轨迹"，代价自己担；**不导出就用默认上限**。
    panel = EventPanel(
        assembly.bus,
        limit=None if export_path else DEFAULT_PANEL_LIMIT,
        pause_at=pause_at,
    )
    loop = HeadlessLoop(assembly)
    loop.start()
    loop.run_frames(frames)

    namespaces = tuple(args.namespace) if args.namespace else ()
    events = panel.by_namespaces(namespaces) if namespaces else panel.events

    if export_path:
        try:
            FileSink(Path(export_path))(panel.export_bytes())
        except OSError as error:
            print(f"导出轨迹失败：{error}", file=sys.stderr)
            return 2

    shown = events if limit == 0 else events[:limit]
    if bool(args.json):
        payload: dict[str, object] = {
            "frames": frames,
            "events": len(events),
            "shown": len(shown),
            "paused": panel.paused,
            "namespaces": list(namespaces),
            "trace": [_event_dict(item) for item in shown],
        }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for item in shown:
            print(_render_event(item))
        if len(shown) < len(events):
            print(f"（共 {len(events)} 条，只显示前 {len(shown)} 条）")
    return 0


def _render_event(envelope: Envelope) -> str:
    """一条事件的人读形态：`序号 帧号 类型 负载`。"""
    payload = " ".join(
        f"{name}={json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
        for name, value in envelope.payload
    )
    return f"{envelope.seq:>6} {envelope.frame:>6}  {envelope.type:<22} {payload}".rstrip()


def _event_dict(envelope: Envelope) -> dict[str, object]:
    """一条事件的机器可读形态（**负载用对象**——`jq` 好取）。

    **与轨迹文件的"有序字段对"不同**：那是**格式**（读回来要逐条还原），这里
    是**视图**（给人与脚本看的）。
    """
    return {
        "seq": envelope.seq,
        "frame": envelope.frame,
        "type": envelope.type,
        "payload": {name: value for name, value in envelope.payload},
        "metadata": {name: value for name, value in envelope.metadata},
    }


def _cmd_placeholder(args: argparse.Namespace) -> int:
    """占位子命令：**要能跑、不许报错**（M0 只要求 `sim` 跑通）。"""
    print(f"「{args.command}」在 M0 尚未实现（占位命令）。")
    return 0


def _report_dict(report: RunReport) -> dict[str, object]:
    """报告的机器可读形态（**M0 的七项**：字段名与 `RunReport` 一致）。

    **只给 M0 的七项**——`RunReport` 在 M1 加了 `combo` / `b2b` / `level`
    三个结算字段，但**不带 `--until` 时的输出要与 M0 逐字一致**（plan-M1.md
    S12 段的验证方案第 3 条），所以那三项**只出现在结算形态里**。
    """
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


def _settlement_dict(report: RunReport) -> dict[str, object]:
    """结算的机器可读形态（**结算八项** + 阶段 + 帧数）。

    **结算八项**（spec §7.8）：分数 `score` · 行数 `lines` · 方块数
    `piecesPlaced` · 连击 `combo` · B2B `b2b` · 等级 `level` · 最终棋盘指纹
    `fingerprint` · 事件数 `events`。另附**阶段**与**帧数**——`phase=game_over`
    是"这一局真的玩到顶出了"的证据（plan-M1.md S12 段的完成判据）。

    **键序固定**（字典按插入序）：人读形态由它渲染，所以两种形态**逐项同源**。
    """
    return {
        "phase": report.phase,
        "frames": report.frames,
        "events": report.events,
        "fingerprint": report.fingerprint,
        "score": report.score,
        "lines": report.lines,
        "piecesPlaced": report.piecesPlaced,
        "combo": report.combo,
        "b2b": report.b2b,
        "level": report.level,
    }


def _settlement_lines(report: RunReport) -> tuple[str, ...]:
    """结算块的人读形态：**`key=value` 一行一项**。

    **为什么是 `key=value` 而不是中文标签**：这是**结算**输出（与 M0 的"统计"
    输出是两种东西），**机器消费方与人读方都要它**——`key=value` 两种都能直接
    取（人扫一眼就知道字段名，机器 split 一下就能用），中文标签只对人有用。

    **值的渲染**：布尔用 JSON 的 `true` / `false`（与 `--json` 形态一致），
    其余用 `str()`。
    """
    return tuple(
        f"{key}={_settlement_value(value)}" for key, value in _settlement_dict(report).items()
    )


def _settlement_value(value: object) -> str:
    """结算项的**值**渲染（布尔用 JSON 的 `true` / `false`）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


__all__ = [
    "DEFAULT_FRAMES",
    "DEFAULT_FRAMES_UNTIL_GAME_OVER",
    "DEFAULT_INSPECT_FRAMES",
    "DEFAULT_INSPECT_LIMIT",
    "DEFAULT_SEED",
    "main",
]
