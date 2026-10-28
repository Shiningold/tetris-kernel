"""M4 的单帧成本分档记账（S12 · plan-M4 §4 S12 · NFR-2 · spec-M4 §7.10）。

## 它做什么

**把 M4 新增的每一样东西各归各的因**——不是「跑一遍看看总耗时多少」。

## 分档（plan-M4 §4 S12 的四档 + 一档对照腿）

| 档 | 装了什么 | 谁驱动 | 这一档的账 |
| --- | --- | --- | --- |
| `m3-baseline` | **M3 终局树**的默认装配 | 参考策略 | **基准**（M3 收官那次的对照腿） |
| `m4-default` | **M4 树**的默认装配 | 参考策略 | **对照腿**（M4 这棵树自己的开销 · 应当 ≈ 0） |
| `m4-variant` | 默认装配 + 变体 B（`growth`） | 参考策略 | **变体的账** |
| `m4-realtime` | 默认装配 + 渲染器 + 输入适配器 | 参考策略 | **适配器的账** |
| `m4-full` | 默认装配 + 变体 + 渲染/输入 + AI | **AI** | **全开的账** |

**为什么多一档 `m4-default`**：四档里的每一档都与 `m3-baseline` 比差值，
**差值里混着两样东西**——「M4 新加的那个部件」与「M4 这棵树本身的开销」。
不把后者单独量出来，**「变体的账」就不纯**。M4 全程零改冻结区（`G4-1`），
**默认路径理应零成本**——这一档就是去验这句话的。

## 三条口径（不许混）

1. **逻辑帧预算只算「推进那一帧」**（NFR-2）：内核推进 + 事件分发。
   **渲染 / 落盘 / 导出 / 网络 IO 一律不进预算**——**但它们占玩家帧时间**，
   所以**单独记**（`renderCostUs` / `inputCostUs`）。
2. **AI 的决策耗时算逻辑帧的一部分**（NFR-2 只排除渲染那一类）→
   **AI 的决策时间单列一条**（`aiDecisionCostUs`）——超预算时才知道该优化谁。
   **它怎么量出来的**：把 `systems/ai/system.py` 里的 `plan_placement` 换成
   一个计时包装器 → **量到的是纯决策耗时**（不含事件分发与派命令）。
3. **统计口径从 `measure_frame_cost.py` 借**（`percentile` · 最近秩）——
   **两处算出不同的 P99 就没法比**（不另抄一份统计）。

## 配对 A/B：同机同时段，交替采样

**跨时段的绝对值不可引用**（M1 的 S14 栽过这一回）——所以本工具**一次只测一档
一棵树**，**编排由调用方交替做**（A → B → A → B …，S12 跑三轮）。

**配对靠三件事一样**：**同种子 · 同帧数 · 同一份驱动代码**。

- **驱动代码是参考策略**（`tests/support/policy.py`）——**从被测的那棵树里取**
  （M3 树与 M4 树的副本逐字节相同 · 哈希记进输出 → **配对这件事可核**）。
- **M3 树与 M4 默认档**：棋盘与规则完全一样 → **命令流逐条相同**。
- **变体档**：棋盘规则本就不同（那就是变体）→ 命令流必然分叉，
  **差值里含变体自己的影响**——这是它该归的因。

## 跑哪棵树的代码

`--src <目录>` 换掉导入路径 → **同一个脚本、同一个口径，跑两棵树的代码**。
**M3 终局的对照树**用 `git worktree` 开一份（**建在工作区外**）。

**换树后当场验**：`tetris_kernel` 的实际加载路径必须在 `--src` 底下——
**量的是哪棵树这件事不许靠信任**。

## 帧窗口

**四档用同一个窗口**（`--warmup` + `--frames`）：变体 B（落地生长）**约 600 帧
就顶出**（实测），窗口开太大 → 后半截帧全落在 `game_over` 的拒绝路径上，
量到的就不是「一帧要多少」了。**顶出即停**（实测帧数记进输出）。
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import io
import json
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import measure_frame_cost as frame_cost
from _toolkit import SRC_ROOT, blake2b_hex, configure_output, git_repo_root

#: **分档**（plan-M4 §4 S12 的四档 + 一档对照腿）。
TIERS = ("m3-baseline", "m4-default", "m4-variant", "m4-realtime", "m4-full")

#: NFR-2 的单帧逻辑预算（微秒）：60fps 帧步长 16.67ms 的 10% = 1.6ms。
BUDGET_US = frame_cost.BUDGET_US

#: 配对 A/B 的固定种子（**两边一样**）。
SEED = 20261120

#: 采样帧数（**预热帧不计入样本**）。
FRAMES = 400

#: 预热帧数（进程刚起时偏慢——**把它们算进 P99 会把「内核每帧要多少」与「机器
#: 刚热起来要多少」混成一锅**）。
WARMUP = 100

#: 参考策略的文件位置（**在被测的那棵树里** · `src` 的上一级）。
POLICY_RELATIVE = ("tests", "support", "policy.py")


@dataclass(frozen=True)
class TierSpec:
    """一档的装配口径。"""

    tier: str
    #: 走实时宿主（`assemble_play` + `PlaySession`）还是无头帧循环。
    realtime: bool
    #: 装不装变体 B（`growth`）。
    variant: bool
    #: 装不装 AI（**装了就由 AI 驱动**——人和 AI 抢方向盘没有意义）。
    ai: bool

    @property
    def driver(self) -> str:
        """谁派命令（`policy` = 参考策略 · `ai` = AI 系统）。"""
        return "ai" if self.ai else "policy"


#: 每一档的装配口径（**一张表 · 不用散在代码里**）。
TIER_SPECS: dict[str, TierSpec] = {
    "m3-baseline": TierSpec(tier="m3-baseline", realtime=False, variant=False, ai=False),
    "m4-default": TierSpec(tier="m4-default", realtime=False, variant=False, ai=False),
    "m4-variant": TierSpec(tier="m4-variant", realtime=False, variant=True, ai=False),
    "m4-realtime": TierSpec(tier="m4-realtime", realtime=True, variant=False, ai=False),
    "m4-full": TierSpec(tier="m4-full", realtime=True, variant=True, ai=True),
}


@dataclass(frozen=True)
class RawSamples:
    """一档采到的原始样本（**未汇总**）。"""

    frame_cost_us: tuple[int, ...]
    events: tuple[int, ...]
    render_us: tuple[int, ...]
    input_us: tuple[int, ...]
    ai_decision_us: tuple[int, ...]
    game_over: bool
    policy_digest: str


class _ReferencePolicy(Protocol):
    """参考策略的**最小接口**（只要求 `decide`）。

    **为什么写 Protocol 而不是直接 import 那个类**：`tools/` **不静态 import
    `tests/`**（那会让类型检查去管一棵测试树），动态载入的模块拿不到静态
    类型——所以这里**只声明用得到的那一个方法**。
    """

    def decide(self, snapshot: object) -> tuple[str, ...]: ...


# ---------------------------------------------------------------------------
# 换树
# ---------------------------------------------------------------------------


def _install_tree(src: Path) -> Path:
    """把导入路径换成**要测的那棵树的 `src`**，返回那棵树的根目录。

    **必须在 import `tetris_kernel` 之前调用**——否则量的是别的树。

    **顺带把别的树留下的路径踢掉**：`measure_frame_cost` 在导入时把本仓的
    `src` 塞进了 `sys.path`（它要 import 本仓的包）——**不踢掉的话，
    `import tetris_kernel` 可能命中本仓而不是被测的那棵树**。
    """
    resolved = src.resolve()
    if not (resolved / "tetris_kernel").is_dir():
        raise RuntimeError(f"那棵树里没有包目录：{resolved / 'tetris_kernel'}")
    sys.path[:] = [item for item in sys.path if item not in (str(SRC_ROOT), str(SRC_ROOT / ""))]
    for name in list(sys.modules):
        if name == "tetris_kernel" or name.startswith("tetris_kernel."):
            del sys.modules[name]
    sys.path.insert(0, str(resolved))
    return resolved.parent


def _assert_tree(src: Path) -> str:
    """**当场验**：加载到的 `tetris_kernel` 确实来自被测的那棵树。

    **返回包的实际路径**（记进输出 → 「量的是哪棵树」可核）。

    **为什么要有这一步**：换树靠的是 `sys.path` 的顺序 + 清 `sys.modules`——
    **两处都可能没按预期生效**（比如本仓被 `pip install -e` 装进了环境）。
    量错树而不报错，比量错树还糟：**它会给出看起来很像真的数**。
    """
    package = importlib.import_module("tetris_kernel")
    location = cast(str, getattr(package, "__file__", "") or "")
    root = str(src.resolve())
    if not location.startswith(root):
        raise RuntimeError(f"加载到的包不在被测的树里：{location}（期望在 {root} 下）")
    return location


# ---------------------------------------------------------------------------
# 参考策略（从被测的那棵树里取）
# ---------------------------------------------------------------------------


def _load_reference_policy(tree_root: Path) -> tuple[_ReferencePolicy, str, str]:
    """从**被测的那棵树**里取参考策略（`tests/support/policy.py`）。

    **为什么从测试树取**：参考策略是**测试件**——门判据与基准都用它，
    `src/` 里**不放**它（库里不 shipping 一个"参考 AI"）。

    **为什么从被测的树取而不是从本仓取**：配对要求**两棵树的驱动代码一样**。
    从被测的树取 → 输出里记它的**内容哈希** → 「配对」这件事**可核**
    （M3 树与 M4 树的副本逐字节相同 → 哈希相同）。

    **载入不到就明确报错**——**不静默退化成"纯重力局"**：那量的是另一件事。
    """
    tests_dir = tree_root.joinpath(*POLICY_RELATIVE[:-1]).parent
    policy_path = tree_root.joinpath(*POLICY_RELATIVE)
    if not policy_path.is_file():
        raise RuntimeError(f"那棵树里没有参考策略：{policy_path}")
    sys.path[:] = [item for item in sys.path if item != str(tests_dir)]
    for name in list(sys.modules):
        if name == "support" or name.startswith("support."):
            del sys.modules[name]
    sys.path.insert(0, str(tests_dir))
    module = importlib.import_module("support.policy")
    policy = cast(_ReferencePolicy, module.ReferencePolicy())
    source = cast(str, module.POLICY_SOURCE)
    digest = blake2b_hex(policy_path.read_text(encoding="utf-8"))
    return policy, source, digest


def _patch_ai_timer() -> Callable[[], int]:
    """把 AI 的 `plan_placement` 换成计时包装器 → **量到纯决策耗时**。

    **为什么能换**：AI 系统是 `from .search import plan_placement`——
    **调用点读的是模块里的那个名字** → 换掉模块属性就换掉了它。

    **返回**：一个「取回本帧累计决策耗时并清零」的取数器。
    """
    module: Any = importlib.import_module("tetris_kernel.systems.ai.system")
    original = module.plan_placement
    accumulator = [0.0]

    def timed(*args: object, **kwargs: object) -> object:
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            accumulator[0] += time.perf_counter() - started

    module.plan_placement = timed

    def take() -> int:
        """取回本帧累计的 AI 决策耗时（微秒）并清零。"""
        elapsed = accumulator[0]
        accumulator[0] = 0.0
        return int(elapsed * 1_000_000)

    return take


# ---------------------------------------------------------------------------
# 采样
# ---------------------------------------------------------------------------


def _assembly_kwargs(spec: TierSpec, seed: int) -> dict[str, object]:
    """这一档的装配参数（**按被测树的装配签名过滤**——M3 树没有 `variant_id`）。"""
    kwargs: dict[str, object] = {"seed": seed, "observability_enabled": True}
    systems: list[str] = []
    if spec.variant:
        systems.append("growth")
    if spec.ai:
        systems.append("ai")
    if systems:
        from tetris_kernel.host.discovery import system_catalog

        kwargs["systems"] = tuple(systems)
        kwargs["system_catalog"] = system_catalog()
    if spec.variant:
        from tetris_kernel.host.builtin_variants import register_builtin_variants
        from tetris_kernel.host.variants import variant_of

        register_builtin_variants()
        manifest = variant_of("growth")
        if manifest is None:
            raise RuntimeError("变体 B（growth）没登记——这棵树不支持变体？")
        kwargs["rule_set"] = manifest.ruleSet
        kwargs["rule_id"] = manifest.ruleId
        kwargs["variant_id"] = manifest.name
    signature = inspect.signature(_import_assemble()).parameters
    return {name: value for name, value in kwargs.items() if name in signature}


def _import_assemble() -> Any:
    """取被测树的 `assemble`（**动态取**——树换了，函数也跟着换）。"""
    module = importlib.import_module("tetris_kernel.host.assembly")
    return module.assemble


def _measure_headless(
    spec: TierSpec,
    *,
    tree_root: Path,
    seed: int,
    frames: int,
    warmup: int,
) -> RawSamples:
    """无头档：默认装配 + 参考策略驱动。"""
    from tetris_kernel.host.loop import HeadlessLoop
    from tetris_kernel.kernel import PHASE_GAME_OVER
    from tetris_kernel.kernel.commands import Command

    assemble = _import_assemble()
    assembly = assemble(**_assembly_kwargs(spec, seed))
    kernel = assembly.kernel
    loop = HeadlessLoop(assembly)
    loop.start()

    policy, source, digest = _load_reference_policy(tree_root)
    take_ai = _patch_ai_timer() if spec.ai else None

    costs: list[int] = []
    events: list[int] = []
    ai_costs: list[int] = []
    game_over = False
    for _ in range(warmup + frames):
        if kernel.phase == PHASE_GAME_OVER:
            game_over = True
            break
        for name in policy.decide(kernel.snapshot()) if spec.driver == "policy" else ():
            kernel.submit(Command(type=name, source=source))
        started = time.perf_counter()
        count = loop.run_frame()
        elapsed = int((time.perf_counter() - started) * 1_000_000)
        costs.append(elapsed)
        events.append(count)
        if take_ai is not None:
            ai_costs.append(take_ai())
        if kernel.phase == PHASE_GAME_OVER:
            game_over = True
            break

    return RawSamples(
        frame_cost_us=tuple(costs[warmup:]),
        events=tuple(events[warmup:]),
        render_us=(),
        input_us=(),
        ai_decision_us=tuple(value for value in ai_costs[warmup:] if value),
        game_over=game_over,
        policy_digest=digest,
    )


def _measure_realtime(
    spec: TierSpec,
    *,
    tree_root: Path,
    seed: int,
    frames: int,
    warmup: int,
) -> RawSamples:
    """实时档：真实装配 + 真实 `PlaySession`（**虚拟时钟 · 不真的等**）。

    **走的是产品自己的实时路径**（`assemble_play` + `PlaySession`）——
    **不重写一遍帧序**（重写就是两处真相）。

    **计时怎么插进去**：

    - **逻辑帧**：`PlaySession` 调 `host.play.measure_frame(loop.run_frame)`
      → **换掉模块里的那个函数** → 量到的是**推进那一帧**的真实耗时；
    - **渲染 / 输入**：换掉渲染器的 `draw` 与输入适配器的 `poll_keys`
      （实例属性）→ **单独记，不进预算**；
    - **参考策略**：在计时区**之外**派命令（**它是驱动，不是被测对象**）。
    """
    play_module: Any = importlib.import_module("tetris_kernel.host.play")
    from tetris_kernel.host.clock import DEFAULT_STEP_MS, VirtualClock
    from tetris_kernel.host.loop import HeadlessLoop
    from tetris_kernel.host.play import PlaySession
    from tetris_kernel.kernel import PHASE_GAME_OVER
    from tetris_kernel.kernel.commands import Command

    kwargs: dict[str, Any] = dict(_assembly_kwargs(spec, seed))
    kwargs.pop("observability_enabled", None)
    from tetris_kernel.host.assembly import assemble_play

    signature = inspect.signature(assemble_play).parameters
    kwargs = {name: value for name, value in kwargs.items() if name in signature}
    kwargs["render"] = True
    kwargs["stream"] = io.StringIO()
    kwargs["degradation_enabled"] = True

    play: Any = assemble_play(**kwargs)
    loop = HeadlessLoop(play.assembly)
    loop.start()
    kernel = play.assembly.kernel

    policy, source, digest = _load_reference_policy(tree_root)
    take_ai = _patch_ai_timer() if spec.ai else None

    render_us: list[int] = []
    input_us: list[int] = []
    ai_costs: list[int] = []
    frame_samples: list[tuple[int, int]] = []

    # **先把原方法抓在手里**——包装器里再调 `play.renderer.draw()` 调到的是
    # **自己**（实例属性已被换掉）→ 无限递归。
    renderer: Any = play.renderer
    adapter: Any = play.input_adapter
    original_draw: Any = renderer.draw if renderer is not None else None
    original_poll: Any = adapter.poll_keys if adapter is not None else None

    def timed_draw() -> None:
        started = time.perf_counter()
        try:
            original_draw()
        finally:
            render_us.append(int((time.perf_counter() - started) * 1_000_000))

    def timed_poll(now_ms: int) -> None:
        started = time.perf_counter()
        try:
            original_poll(now_ms)
        finally:
            input_us.append(int((time.perf_counter() - started) * 1_000_000))

    if renderer is not None:
        renderer.draw = timed_draw
    if adapter is not None:
        adapter.poll_keys = timed_poll

    def patched_measure(frame: Any) -> tuple[Any, float]:
        """换掉 `measure_frame`：计时区**只包住推进那一帧**。

        **签名照抄被换掉的那个**（`measure_frame(action) -> (结果 · 毫秒)`）。
        """
        for name in policy.decide(kernel.snapshot()) if spec.driver == "policy" else ():
            kernel.submit(Command(type=name, source=source))
        started = time.perf_counter()
        result = frame()
        elapsed = time.perf_counter() - started
        frame_samples.append((int(elapsed * 1_000_000), int(result)))
        if take_ai is not None:
            ai_costs.append(take_ai())
        return result, elapsed * 1000.0

    play_module.measure_frame = patched_measure

    clock = VirtualClock(step_ms=DEFAULT_STEP_MS)

    def sleeper(seconds: float) -> None:
        """**不真的等**——推进虚拟时钟，让节拍器按固定步长放帧。"""
        del seconds
        clock.advance(1)

    session = PlaySession(
        assembly=play.assembly,
        loop=loop,
        renderer=play.renderer,
        input_adapter=play.input_adapter,
        degradation=play.degradation,
        clock=clock,
        sleeper=sleeper,
        frame_counter=play.frame_counter,
        source="host",
    )
    session.run(frames=warmup + frames, until_game_over=True)

    costs = tuple(item[0] for item in frame_samples[warmup:])
    events = tuple(item[1] for item in frame_samples[warmup:])
    return RawSamples(
        frame_cost_us=costs,
        events=events,
        render_us=tuple(render_us),
        input_us=tuple(input_us),
        ai_decision_us=tuple(value for value in ai_costs[warmup:] if value),
        game_over=kernel.phase == PHASE_GAME_OVER,
        policy_digest=digest,
    )


def _measure(
    tier: str,
    *,
    src: Path,
    seed: int,
    frames: int,
    warmup: int,
) -> RawSamples:
    """测一档（**在给定的树上**）。"""
    tree_root = _install_tree(src)
    _assert_tree(src)
    spec = TIER_SPECS[tier]
    if spec.realtime:
        return _measure_realtime(spec, tree_root=tree_root, seed=seed, frames=frames, warmup=warmup)
    return _measure_headless(spec, tree_root=tree_root, seed=seed, frames=frames, warmup=warmup)


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def _stats(values: tuple[int, ...]) -> dict[str, int | float]:
    """一组样本的汇总（**P50 / P99 / max / mean**）。

    **P99 用最近秩**——直接调 `measure_frame_cost.percentile`（**同款口径**，
    **不另抄一份**）。**空样本**返回零值（那一档没跑那项 → **不是错误**）。
    """
    if not values:
        return {"p50": 0, "p99": 0, "max": 0, "mean": 0.0, "total": 0, "samples": 0}
    ordered = sorted(values)
    return {
        "p50": frame_cost.percentile(ordered, 0.50),
        "p99": frame_cost.percentile(ordered, 0.99),
        "max": ordered[-1],
        "mean": round(statistics.fmean(ordered), 2),
        "total": sum(ordered),
        "samples": len(ordered),
    }


def _git_head(tree_root: Path) -> str:
    """被测树的 `HEAD`（**记进输出** → 「量的是哪一版」可核）。"""
    repo = git_repo_root(tree_root)
    if repo is None:
        return ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            check=False,
        )
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="replace").strip()


def _payload(
    tier: str,
    *,
    src: Path,
    seed: int,
    warmup: int,
    sample: RawSamples,
) -> dict[str, object]:
    """一档的输出（**可写进 JSON**）。"""
    return {
        "tier": tier,
        "tree": str(src.resolve()),
        "treeHead": _git_head(src.resolve().parent),
        "seed": seed,
        "warmupFrames": warmup,
        "budgetUs": BUDGET_US,
        "frames": len(sample.frame_cost_us),
        "gameOver": sample.game_over,
        "driver": TIER_SPECS[tier].driver,
        "policyDigest": sample.policy_digest,
        "frameCostUs": _stats(sample.frame_cost_us),
        "eventsPerFrame": _stats(sample.events),
        "renderCostUs": _stats(sample.render_us),
        "inputCostUs": _stats(sample.input_us),
        "aiDecisionCostUs": _stats(sample.ai_decision_us),
        "overBudgetFrames": sum(1 for value in sample.frame_cost_us if value > BUDGET_US),
    }


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    configure_output()
    parser = argparse.ArgumentParser(description="M4 单帧成本分档记账（配对 A/B 的一条腿）")
    parser.add_argument("--tier", choices=TIERS, required=True, help="测哪一档")
    parser.add_argument(
        "--src",
        type=Path,
        required=True,
        help="要测的那棵树的 `src` 目录（**换树就换这里**）",
    )
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--frames", type=int, default=FRAMES)
    parser.add_argument("--warmup", type=int, default=WARMUP)
    parser.add_argument("--out", type=Path, default=None, help="把结果写成 JSON")
    args = parser.parse_args(argv)

    sample = _measure(
        args.tier,
        src=args.src,
        seed=args.seed,
        frames=args.frames,
        warmup=args.warmup,
    )
    payload = _payload(args.tier, src=args.src, seed=args.seed, warmup=args.warmup, sample=sample)

    frame_stats = cast(dict[str, int | float], payload["frameCostUs"])
    tree_head = cast(str, payload["treeHead"])
    print(f"档位：{args.tier}（树：{tree_head[:8] or '（非 git 树）'}）")
    print(f"采样帧数：{payload['frames']}（顶出：{'是' if sample.game_over else '否'}）")
    print(f"逻辑帧 P50 / P99：{frame_stats['p50']}μs / {frame_stats['p99']}μs")
    print(f"超预算帧数（>{BUDGET_US}μs）：{payload['overBudgetFrames']}")
    render_stats = cast(dict[str, int | float], payload["renderCostUs"])
    if render_stats["samples"]:
        print(f"渲染耗时（不进预算）：P50 {render_stats['p50']}μs / P99 {render_stats['p99']}μs")
    input_stats = cast(dict[str, int | float], payload["inputCostUs"])
    if input_stats["samples"]:
        print(f"输入耗时（不进预算）：P50 {input_stats['p50']}μs / P99 {input_stats['p99']}μs")
    ai_stats = cast(dict[str, int | float], payload["aiDecisionCostUs"])
    if ai_stats["samples"]:
        print(
            f"AI 决策耗时（**算逻辑帧** · 每次决策）：P50 {ai_stats['p50']}μs"
            f" / P99 {ai_stats['p99']}μs（决策 {ai_stats['samples']} 次）"
        )

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"已写入：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
