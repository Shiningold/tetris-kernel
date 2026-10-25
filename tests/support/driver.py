"""驱动器：按参考策略把一局玩下去（**只经命令通道**）。

## 它守什么

**N1（单一状态所有者）**：驱动器**不碰内核的私有状态**——它只做两件事：
**派发命令**（`kernel.submit`）与**推进帧**（`HeadlessLoop.run_frame`）。
棋盘、分数、方块位置**全部由内核算**。

## 每帧的顺序

1. 问策略要命令（`policy.decide(snapshot)`）；
2. **派发**那些命令（进命令队列）；
3. **跑一帧**（`run_frame` 内部派发 tick → 跑命令轮次 → 推进五步）。

**命令在 tick 之后、帧推进之前执行**：内核把 tick 放在队首（ADR-022），所以
策略的指令落在**同一帧内**、帧推进**之前**——方块的移动当帧就生效。
"""

from __future__ import annotations

from dataclasses import dataclass

from tetris_kernel.bus.envelope import Envelope
from tetris_kernel.host.assembly import Assembly
from tetris_kernel.host.loop import HeadlessLoop, RunReport
from tetris_kernel.kernel.commands import Command
from tetris_kernel.kernel.lifecycle import PHASE_GAME_OVER
from tetris_kernel.kernel.snapshot import Snapshot

from .policy import POLICY_SOURCE, ReferencePolicy

#: 默认的帧数上限（**兜底**：万一顶不出，别挂死）。
DEFAULT_MAX_FRAMES = 200_000


@dataclass(frozen=True)
class PlayOutcome:
    """一局的账本（**驱动器记的**，不是内核说的）。

    - `frames`：宿主跑过的帧数；
    - `game_over`：这一局**真的顶出了**吗；
    - `placements`：**每次放置消掉的行数**（按放置顺序，`0` = 这一手没消行）——
      G1-1 的手算要用它；
    - `class_keys`：每次消行的**分类键**（G1-1 要验"这一局没有 T-Spin"）；
    - `clears`：每次消行的三件套（**行数 · 分类键 · 消行前的连击数**）——
      手算连击奖励要用的就是第三项；
    - `fingerprints`：**逐帧**的棋盘结构指纹（G1-9 要逐帧比，不只看最终态）；
    - `report`：宿主给的结算报告（**结算十项**）。
    """

    frames: int
    game_over: bool
    placements: tuple[int, ...]
    class_keys: tuple[str, ...]
    clears: tuple[tuple[int, str, int], ...]
    fingerprints: tuple[str, ...]
    report: RunReport


@dataclass(frozen=True)
class RunTrace:
    """一段固定长度的跑的**逐帧轨迹**。

    - `snapshots`：每帧开跑前的那份快照（**策略读的就是它**）；
    - `commands`：每帧策略给出的命令（**命令类型名**，不带参数）；
    - `boards`：每帧**策略动手之前**的棋盘格子（**逐帧留底**）；
    - `fingerprints`：每帧末的棋盘结构指纹。

    **`boards` 为什么单列一份**：门判据要验「策略没改棋盘」，可快照是**策略
    读过的对象**——策略要是动了它，事后再拿它跟"自己"比，比的是**改完之后**的
    东西，**比了个空**。所以棋盘得在**策略动手之前**留一份底。
    """

    snapshots: tuple[Snapshot, ...]
    commands: tuple[tuple[str, ...], ...]
    boards: tuple[tuple[tuple[int, ...], ...], ...]
    fingerprints: tuple[str, ...]


def play_to_game_over(
    assembly: Assembly,
    policy: ReferencePolicy,
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> PlayOutcome:
    """按策略把一局玩到顶出。

    **顶出就停**（`phase == game_over`）；到 `max_frames` 还没顶出也停（返回
    `game_over=False`）——**兜底，不是正常结局**。
    """
    loop = HeadlessLoop(assembly)
    loop.start()
    kernel = assembly.kernel
    collected: list[Envelope] = []
    assembly.bus.subscribe("*", collected.append)

    fingerprints: list[str] = []
    while kernel.phase != PHASE_GAME_OVER and loop.frames < max_frames:
        for name in policy.decide(kernel.snapshot()):
            kernel.submit(Command(type=name, source=POLICY_SOURCE))
        loop.run_frame()
        fingerprints.append(loop.snapshot().board.structureFingerprint)

    placements, class_keys, clears = _read_placements(collected)
    # **跑 0 帧只为拿一份报告**——`run_frames` 的报告口径 = 帧末快照。
    report = loop.run_frames(0, until_game_over=True)
    return PlayOutcome(
        frames=loop.frames,
        game_over=kernel.phase == PHASE_GAME_OVER,
        placements=placements,
        class_keys=class_keys,
        clears=clears,
        fingerprints=tuple(fingerprints),
        report=report,
    )


def play_frames(assembly: Assembly, policy: ReferencePolicy, frames: int) -> RunTrace:
    """按策略跑**固定帧数**，把逐帧轨迹收回来。

    **为什么另起一个函数**：`play_to_game_over` 只给账本；门判据要看的
    「每帧的快照」与「每帧的命令」得**逐帧**拿到——一局两千多帧，全留着
    太占内存，所以这里只跑一小段。
    """
    loop = HeadlessLoop(assembly)
    loop.start()
    kernel = assembly.kernel

    snapshots: list[Snapshot] = []
    commands: list[tuple[str, ...]] = []
    boards: list[tuple[tuple[int, ...], ...]] = []
    fingerprints: list[str] = []
    for _ in range(frames):
        snapshot = kernel.snapshot()
        # **棋盘要在策略动手之前留底**——策略拿到快照就有机会动手了。
        board = snapshot.board.cells
        names = policy.decide(snapshot)
        snapshots.append(snapshot)
        commands.append(names)
        boards.append(board)
        for name in names:
            kernel.submit(Command(type=name, source=POLICY_SOURCE))
        loop.run_frame()
        fingerprints.append(loop.snapshot().board.structureFingerprint)
    return RunTrace(
        snapshots=tuple(snapshots),
        commands=tuple(commands),
        boards=tuple(boards),
        fingerprints=tuple(fingerprints),
    )


def _read_placements(
    collected: list[Envelope],
) -> tuple[tuple[int, ...], tuple[str, ...], tuple[tuple[int, str, int], ...]]:
    """从事件流里读出「每次放置消了几行」「分类键」「消行前的连击数」。

    **配对靠顺序**：`piece:locked`（锁定步）在前，`board:lines-cleared`（消行
    步）在后——**同一帧内**。所以一条消行事件配的是**最近一次锁定**。

    **连击数是数出来的，不是内核说的**：连续两次放置都消行 → 连击 +1；
    放空一次就断。规则集的口径是「奖励按**消行前**的连击数给」
    （`rules/standard.py` 的 `score_clear`），所以这里记的是**消行前**的值。

    **为什么分两遍**：锁定事件先到，那时**还不知道这一手消不消行**——所以
    先把放置序列拼完整，再回头数连击。一遍写完会把"这一手消行"当成"上一手
    放空"，连击数永远停在 0。
    """
    placements: list[int] = []
    class_keys: list[str] = []
    for envelope in collected:
        if envelope.type == "piece:locked":
            placements.append(0)
        elif envelope.type == "board:lines-cleared":
            payload = dict(envelope.payload)
            if placements:
                placements[-1] = int(payload["count"])
            class_keys.append(str(payload["classKey"]))

    # 第二遍：数连击。`combo_before` = 这一手之前**连着**消了多少手。
    clears: list[tuple[int, str, int]] = []
    key_index = 0
    combo = 0
    for count in placements:
        if count:
            clears.append((count, class_keys[key_index], combo))
            key_index += 1
            combo += 1
        else:
            combo = 0
    return tuple(placements), tuple(class_keys), tuple(clears)


__all__ = [
    "DEFAULT_MAX_FRAMES",
    "PlayOutcome",
    "RunTrace",
    "play_frames",
    "play_to_game_over",
]
