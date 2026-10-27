# M2 代码审查报告（可观测：录像与回放 · 日志与指标 · 事件轨迹 · 存档与恢复 · 降级控制器）

> 审查方式：`open-code-review-delegate` 委托模式——OCR 只负责**文件选择与规则解析**，审查判断由宿主 Agent 完成。
> 基线：`28994e7`（M1 收官 + M2 开工入档）→ `e99599f`（`feature/m2-batch-d`，含批次 A/B/C/D 四个批次）。
> 审查日期：2026-10-27。

---

## §1 概要

M2 兑现五件事：录像与回放（200 局四项全等 + 1000 局回放指纹零差异）· 结构化日志 · 按帧采样的指标 · 事件轨迹导出与只读事件流视图 · 局面序列化与恢复，另加降级三档与五道门 `G2-1`…`G2-5`。

**结论先说**：M2 的**架构纪律守得很干净**——可观测能力全部长在外围（`systems/` · `bus/` · `adapters/` · `host/`），冻结区**只解冻了 `kernel/core.py` 一个文件**（加 `export_state` / `restore_state`），`rules/` 一个字节没动；契约基线 360 → 383 条**只增不改**；`bus/` 另立独立基线（ADR-019），实测两道基线都绿：

```
[bus/ 独立基线]      bus/ 4 个文件：新增 0 · 变化 0 · 消失 0 → 校验通过
[冻结区基线]         冻结区 14 个文件：新增 0 · 变化 0 · 消失 0 → 校验通过
```

**问题出在"落盘这条线"的接线上**，不在内核与总线本体：

- **三个可观测系统共用同一个 sink，而 `FileSink` 是覆盖语义** → 日志、指标、录像**互相覆盖**，逐条写入退化成"只剩最后一条"（H1 · 高 · 已实测复现）。
- **装配层没有给录像系统注入 `outcome_provider`** → 宿主产出的录像头**没有局末四项** → ADR-078 要求的"四项比对自足"在生产路径上不成立（H2 · 高）。
- **`replay` 子命令在"录像没有基准"时退出码是 0** → 脚本把"没比"当成"过了"（H3 · 高 · 已实测复现）。

外加 4 条中危（重放规则集不跟随录像 · 录像参数不合契约时静默丢参 · `inspect --export` 与"完整轨迹"的说法不符 · 事件面板默认不限量）与 3 条低危。

**发现计数：高 3 · 中 4 · 低 3，合计 10 项。**

---

## §2 审查范围与基线

### 2.1 OCR 委托输出

```
ocr delegate preview --format json --from 28994e7 --to HEAD
```

| 项 | 值 |
| --- | --- |
| mode | `range` |
| merge_base | `28994e7bae0e4698ccb86d5162e3cea7c0db426c`（M1 收官） |
| total_files | 60 |
| reviewable_count | **36** |
| excluded_count | 24 |
| insertions / deletions | 18712 / 7691 |

**可审文件（36）**：`src/tetris_kernel/` 下 27 个（`bus/` 3 · `contracts/events.py` · `host/` 6 · `kernel/core.py` · `systems/log/` 4 · `systems/metrics/` 4 · `systems/replay/` 5 · `adapters/` 2）+ `tests/baseline/` 5 + `tools/` 4 + `.gitignore`。

**排除文件（24）**：`docs/` 与 `ai_docs/` 8 个（`unsupported_ext`）+ `tests/test_*.py` 16 个（`default_path`：`tests/` 默认排除）。

**规则组**：`ocr delegate rule --format json` → 2 组。

- 组 1（`**/*.{py,pyi,ipynb}`，9 个文件）：类型错配 / 死代码 / 可变默认值 / 边界与空输入 / 异常处理 / 资源管理 / 性能 / 并发 / 安全。
- 组 2（`default`，`systems/` · `adapters/` · `tools/`）：正确性 · 安全 · 性能 · 可维护性 · 测试覆盖。

### 2.2 逐文件处置（36 / 36 全部读过）

| 分组 | 文件 | 处置 |
| --- | --- | --- |
| 总线 | `bus/bus.py`（503 行）· `bus/subscriber.py`（278）· `bus/__init__.py` | 全文读 |
| 契约面 | `contracts/events.py` | 读 diff（新增 `replay:` 命名空间 5 条 + `PRODUCER_LABELS` 增一条） |
| 存储 | `adapters/storage.py`（86）· `adapters/__init__.py` | 全文读 |
| 宿主 | `host/assembly.py`（338）· `host/cli.py`（525）· `host/performance.py`（335）· `host/replay.py`（87）· `host/archive.py`（203）· `host/__init__.py` | 全文读 |
| 内核 | `kernel/core.py` | 读 diff（`KernelArchive` · `_copy_state` · `_rebuild_lifecycle` · `export_state` / `restore_state`） |
| 日志系统 | `systems/log/system.py`（214）· `entry.py`（114）· `targets.py`（148）· `__init__.py` | 全文读 |
| 指标系统 | `systems/metrics/aggregate.py`（242）· `system.py`（172）· `trace.py`（303）· `__init__.py` | 全文读 |
| 回放系统 | `systems/replay/recorder.py`（344）· `format.py`（353）· `playback.py`（287）· `archive.py`（499）· `__init__.py` | 全文读 |
| 基线 | `tests/baseline/` 5 个 | 读 diff + 结构化解析（契约 383 条 · 期望清单 383 条 · `bus_hashes.json` 4 文件 · `kernel_hashes.json` 14 文件 · `frame_events.json` meta：8000 帧 / 常态 7381 帧） |
| 工具 | `tools/_toolkit.py` · `check_frozen_baseline.py` · `contract_baseline.py` · `measure_frame_cost.py` | 全文读（`measure_frame_cost.py` 1044 行，分段读全） |
| 杂项 | `.gitignore` | 读 diff（新增 `.tetris-data/`） |

**覆盖率：total_files 36 · reviewed_files 36 · skipped_files 0 · coverage_rate 100%。**

### 2.3 配套上下文（读，不在改动范围）

`src/tetris_kernel/host/loop.py`（帧循环三段 · `RunReport`）· `src/tetris_kernel/kernel/drafts.py`（`make_draft` 现场校验）· `docs/spec-M2.md`（sink 约定 · M2-D1…D5 局部纪律）· `docs/决策记录.md` ADR-074/075/078/080/082/083/084（grep 定位）· `tests/test_m2_gates.py` · `tests/test_replay_playback.py` · `tests/test_storage_adapter.py`（追 H1/H2/H3 的调用点）。

### 2.4 未覆盖的部分（明确声明）

- **OCR 排除的 24 个文件没有逐字审**（`docs/` 8 个 + `tests/` 16 个）。其中 `docs/spec-M2.md` 只按 grep 读了 sink 约定与局部纪律两条；**16 个测试文件只在追调用点时读过片段**，**它们的断言强度未被审查**——本报告对 H2/H3 的判断依赖"生产路径没接线"这一事实，不依赖测试对错。
- **性能数字不可跨机比较**：M2 记录的单帧开销（P50 566 μs / P99 796 μs）是某台机某时段的数，**本报告不引用它作判据**，也不审它。
- **M1 遗留的三条真偏离**（0 行 T-Spin 断 B2B 链 · `tspin-mini` 送不到计分 · 事件数基线够不着 `piece:lock-delay-reset`）**不在本次范围**，M2 未修，本报告不重复计。
- **未做变异测试**：本报告的每条结论都给了实跑证据，但没有像批次 D 那样把"守卫被改坏"逐个反证。

---

## §3 发现清单

| 编号 | 严重度 | 一句话结论 | 位置 |
| --- | --- | --- | --- |
| **H1** | **高** | 三个可观测系统**共用同一个 sink**，而 `FileSink` 是**覆盖语义** → 日志 / 指标 / 录像**互相覆盖**，逐条写入只剩最后一条 | `host/assembly.py:301` · `adapters/storage.py:65` · `systems/log/targets.py:101` · `systems/metrics/system.py:132` |
| **H2** | **高** | 装配层**没给录像系统注入 `outcome_provider`** → 宿主产出的录像头没有局末四项 → ADR-078 的"四项比对自足"在生产路径上不成立 | `host/assembly.py:198` · `systems/replay/recorder.py:117` |
| **H3** | **高** | `replay` 子命令在**录像没有基准**时退出码是 **0** → 脚本把"没比"当成"过了"（`equal: null` 也算绿） | `host/cli.py:314` · `host/cli.py:341` |
| **M1** | 中 | 重放时**只换 `rule_id`**，规则集永远是标准规则集 → 变体录像被静默按标准规则重放，报出来的是"四项不等"而不是"规则集不对" | `host/cli.py:302` |
| **M2** | 中 | 录像参数**不合契约时静默丢参**（返回空 `()`）→ 录像与原局**静默分叉**，且文件本身看不出异常 | `systems/replay/recorder.py:264` |
| **M3** | 中 | `inspect --export` 声称导出"**完整轨迹**"，但 `--pause-at` 下导出的是**截断轨迹**（面板与导出器是同一个对象） | `host/cli.py:355` · `systems/metrics/trace.py:183` |
| **M4** | 中 | `EventPanel` **默认不限量**，长跑驻留百万级信封；`--limit` 只影响显示不影响留存 | `systems/metrics/trace.py:100` · `host/cli.py:376` |
| **L1** | 低 | 错误级联防线以**名字**为键，两个同名订阅者会互相抑制报错，ADR-083 退化成"同名只报一次" | `bus/bus.py:367` |
| **L2** | 低 | 空洞率的镜像维护里 `isinstance(value, int)` 对 `bool` 也成立 → `True` 被当成实心格值 | `systems/metrics/aggregate.py:178` |
| **L3** | 低 | 落盘目录的可写性判定用 `os.access`，Windows 上遇 ACL 时判断不可靠（真写时抛 `OSError`，不静默） | `host/assembly.py:97` |

---

## §4 逐条详情

### H1 · 三个可观测系统共用同一个 sink，而 `FileSink` 是覆盖语义 → 日志 / 指标 / 录像互相覆盖 · high

**位置**

- `host/assembly.py:298-301`（装配层把**同一个** `sink` 传给三个工厂）
- `host/assembly.py:182-200`（三个工厂的签名统一是 `(bus, sink)`）
- `adapters/storage.py:65-75`（`FileSink.__call__`：**每次调用把整个文件替换成给定的字节**，临时文件 + `os.replace` 原子替换）
- `systems/log/targets.py:101-112`（`SinkTarget.write`：**每条日志条目**交一次字节）
- `systems/metrics/system.py:132-141`（`MetricsSystem._write`：**每次采样**交一次字节）
- `systems/replay/recorder.py:240-245`（`RecordingSystem.flush`：**整份录像**交一次字节）

**证据（实跑）**

```python
assembly = assemble(seed=1, sink=FileSink(tmp / "out.bin"))
loop = HeadlessLoop(assembly); loop.start(); loop.run_frames(30)
```

```
size 131
'{"channel": "state", "data": {"eventType": "state:changed", "frame": 30, "seq": 387},
  "level": "info", "message": "state:changed"}\n'
```

跑完 30 帧，落盘文件里**只剩最后一条日志条目**：前面所有日志条目、所有指标采样、整份录像**全部被后写者覆盖掉**。

**为什么是问题**

两层语义对不上，且是**两边都自洽、拼起来出事**：

1. `FileSink` 的 docstring 明写自己的语义是**覆盖**：「**覆盖语义**：每次调用把整个文件替换成给定的字节。录像就是这种用法（ADR-030：内存 list 累积，局末**一次性**落盘）。**不追加**：追加是另一种语义，等真有消费者时再加——**不提前实现别的阶段的需求**（批次 C 的日志系统若要追加，届时再说）。」
2. 但**批次 C 的日志系统与指标系统正是那个消费者**——它们按**追加**语义用 sink：日志每条交一行、指标每次采样交一行，文件里应该是 N 行。

于是：**单系统单次写入时只有最后一条留下来；三个系统共用一个 sink 时连"各自一份"都没有。**

这是 M2-D2（落盘只走 sink）与 ADR-074（写入只有一处）的接线缺口：**"写入只有一处"被读成了"一个 sink"，而不是"一条落盘路径"**。`default_sink()` 是 `host/__init__.py` 的公开导出，接上就静默丢数据——**丢得没有任何报错**：录像文件存在、格式合法（只是内容是某条日志），"文件存在"这件事还会骗过"录像齐了吗"的检查（`G2-1` 的四项全等会拿它当基准，比出假红）。

**为什么现在没爆**：CLI 的三个子命令（`sim` / `replay` / `inspect`）**都没传 `sink`**，生产路径暂时不落盘。但 `default_sink()` 是公开 API，`assemble(sink=...)` 是文档写明的用法——**接上就坏**。

**修法方向（不代项目定）**：二选一——① 装配层给三个系统**各一个 sink**（各自的文件名，与录像/日志/指标三类产物一一对应）；② 让日志与指标**攒批一次性交出**（与录像同款"交整份"语义），并在 `FileSink` 的 docstring 里把这层约定写死。两条都要动 `host/assembly.py` 的工厂签名，属于**装配约定**、不占契约面、不占 major 变更预算。

---

### H2 · 装配层没有给录像系统注入 `outcome_provider` → 宿主产出的录像没有局末四项 · high

**位置**

- `host/assembly.py:198-200`（`_build_recording_system(bus, sink)` —— **只传两个参数，没有 provider**）
- `systems/replay/recorder.py:111-124`（`RecordingSystem.__init__` 有 `outcome_provider` 入口，**默认 `None`**）
- `systems/replay/recorder.py:222-234`（局末：只有 `outcome_provider is not None` 才取四项再落盘）

**证据（grep 全仓）**

```
src/tetris_kernel/systems/replay/recorder.py:117:        outcome_provider: OutcomeProvider | None = None,
src/tetris_kernel/systems/replay/recorder.py:230:        if self._outcome_provider is not None:
src/tetris_kernel/systems/replay/recorder.py:231:            self.set_outcome(self._outcome_provider())

tests/test_m2_gates.py:218:        recorder.set_outcome(original)
tests/test_replay_playback.py:137:    recorder.set_outcome(outcome)
```

**`host/assembly.py` 里零命中**——装配层是**唯一**的生产接线点，它不注入。提供四项的只有两处**测试里手动调用的** `set_outcome`。

**为什么是问题**

ADR-078（`docs/决策记录.md:600`）把这条定成：

> **决策**：录像头在**版本 · 种子 · 规则集标识**之外**追加「局末四项」**（棋盘指纹 · 分数 · 行数 · 方块数）。**四项由宿主在局末注入**（`outcome_provider`）……
> **理由**：**比对要能自足地做**。录像不带基准时，四项比对只能"打印不能比"——`replay` 子命令拿到的只是回放结果，拿不到"对不对"的判据。

「由宿主注入」这件事，系统层把入口留好了，**宿主没接**。后果：走装配层录出来的录像，头里 `outcome: null` → `replay` 子命令只能打印不能比 → **ADR-078 的承诺在生产路径上不成立**。`G2-1` 那 200 局比对之所以能跑，是因为**用例自己手工补了 `set_outcome`**——**门判据验的是"系统层能力在"，不是"宿主把它接上了"**。

四个字段里**棋盘指纹只能从内核快照拿**（指纹算法只有内核那一份，抄第二份就是第二个真相），所以这个 provider 必然是宿主的事——**没有第三条路**。

**修法方向（不代项目定）**：在 `host/assembly.py` 里给 `_build_recording_system` 补一个 provider——宿主拿 `assembly.kernel.snapshot()` 取四项（`KernelReplayDriver.four_items()` 已经有同款取法，可复用同一处算法）。这会牵动工厂签名（与 H1 是同一处改动，建议一起做）。

---

### H3 · `replay` 在"录像没有基准"时退出码是 0 → 脚本把"没比"当成"过了" · high

**位置**

- `host/cli.py:313-314`（`baseline = recording.outcome` · `equal = None if baseline is None else baseline.equals(items)`）
- `host/cli.py:341`（`return 0 if equal is not False else 1`）

**证据（实跑）**

用一份只有头、`outcome: null` 的合法录像跑 `replay`：

```
--- human ---
种子：7
规则集：standard
命令数：0
重放棋盘指纹：d53a204b1444f206f0b58ad4dae415cd
重放分数：0
重放行数：0
重放已放置方块数：0
录像里没有局末四项：无比对基准
exit = 0
--- json ---
{"seed": 7, ..., "recorded": null, "replayed": {...}, "equal": null}
exit = 0
```

**为什么是问题**

`equal` 有三个取值：`True`（全等）· `False`（不等）· `None`（**没比**）。退出码只区分了 `False` 与"其他"——**`None` 与 `True` 拿到的是同一个 0**。CLI 自己的 docstring 写着「**四项不等时退出码是 1**——脚本能直接判成败」，**"没比"的情况没被任何一方当作一种结果**。

配合 H2：**默认路径下的录像（装配层录出来的）就是 `outcome: null`** → `replay` 一律 exit 0 → **任何按退出码判成败的脚本，在"录像根本没带基准"时都会判成通过**。这条与 H2 是同一条链上的两个断点，**必须一起看**：H2 让录像不带基准，H3 让"不带基准"判成绿。

**修法方向（不代项目定）**：把"没有基准"当成**独立的结果**——要么退出码取非 0（`2`，与"录像读不了"区分开），要么至少让 `--json` 的 `equal: null` 在 CI 侧被显式判红。**当前 CLI 的 docstring 只描述了两种结果，这是文档与行为一起缺一种情形**。

---

### M1 · 重放时只换 `rule_id`，规则集永远是标准规则集 · medium

**位置**：`host/cli.py:302-308`

```python
assembly = assemble(
    seed=recording.seed,
    rule_id=recording.rule,      # ← 只换了标识
    mode="replay",
    limits=REPLAY_LIMITS,
    observability_enabled=False,
)
```

`rule_set` 用默认值 `STANDARD_RULESET`（`host/assembly.py:249`）。**标识换了，规则没换**。

**为什么是问题**：变体规则集的录像会被**静默按标准规则重放**——判定结果必然是"四项不等"，但报出来的理由是"四项不等"而不是"规则集不对"，**排查方向会被带偏**（人会去查指纹算法、去查录像，而不是去查规则集）。

规则集标识 → 规则集的映射**目前不存在**（变体属 M4，`variants/` 仍空），所以现在**无法**在 CLI 里正确地起一个变体内核。合理的做法是**显式拒绝**：`recording.rule != "standard"` 时明确报错说"变体规则的重放待 M4"，而不是拿标准规则硬跑一遍再报"四项不等"。

---

### M2 · 录像参数不合契约时静默丢参 → 录像与原局静默分叉 · medium

**位置**：`systems/replay/recorder.py:264-280`（`_args_of`）

```python
for key, value in payload:
    if key != "args":
        continue
    if not isinstance(value, tuple):
        return ()          # ← 参数整个丢掉
    ...
        if not isinstance(entry, tuple) or len(entry) != 2:
            return ()      # ← 参数整个丢掉
        if not isinstance(name, str):
            return ()      # ← 参数整个丢掉
```

**为什么是问题**：这条命令的参数**被无声替换成空参数**——录像文件本身看不出异常（它就是一条"没有参数"的合法命令），重放时这一手与原局不同，**录像与原局静默分叉**。而本项目的纪律恰恰是反过来的：

- 同一个文件的 `_args_value`（`:283-297`）对**浮点**是**当场报错**的：「浮点进了录像，重放时状态就可能对不上」；
- `systems/replay/format.py:268-283` 的 `_restore_args` 对**缺参数**也是**当场报错**的。

**同一个概念（录像里的参数必须符合契约）有三种处理：两处报错、一处静默丢参。** `_args_of` 那处应当同样当场抛错——它现在是最弱的一环，而它恰好是**唯一在"写"的方向上**的一环（`_restore_args` 在"读"的方向）。

---

### M3 · `inspect --export` 声称导出"完整轨迹"，`--pause-at` 下导出的是截断轨迹 · medium

**位置**

- `host/cli.py:355-358`（docstring：「**导出的是完整轨迹**（不过滤、不受 `--limit` 影响）——离线分析要的是全量，视图的截断只影响**显示**」）
- `host/cli.py:384-390`（导出用的是 `panel.export_bytes()`）
- `systems/metrics/trace.py:179-188`（面板暂停后**不再收事件**）

**为什么是问题**：ADR-081 定了「**面板与导出器是同一个对象**」——"完整轨迹 = 不暂停 + 不过滤的那一份"。而 `inspect --pause-at N` 让面板在那一帧**停住**，导出拿到的自然是**截断的**那份。文档说"完整"，行为给的是"截至暂停点"。

**这不是实现错误，是文档与行为不一致**：要么导出走一条独立于暂停的路径（要另存一份全量轨迹，与"同一个对象"的口径冲突），要么把 `--export` 的说明改成"导出的是**视图当前可见的那份**（`--pause-at` 会截断它）"，并在 `--pause-at` 与 `--export` 同时出现时**响亮地提示**。后者几行就够。

---

### M4 · `EventPanel` 默认不限量，长跑驻留百万级信封 · medium

**位置**：`systems/metrics/trace.py:100`（`self._events: deque[Envelope] = deque(maxlen=limit)`，`limit` 默认 `None`）· `host/cli.py:376`（`EventPanel(assembly.bus, pause_at=pause_at)` —— **不传 limit**）

**为什么是问题**：`--limit` 在 CLI 里只裁**显示**（`host/cli.py:392` 的 `shown = events[:limit]`），**不影响留存**。`inspect --frames` 可以给到几万帧（`sim --until game-over` 的上限是 50000 帧），每帧十几条事件 → 面板会驻留**百万级**信封对象，全程不释放。`EventPanel` 自己提供了 `limit` 参数，**CLI 不用**。

注意 `MetricsSystem` 就做对了：`_samples` 用 `deque(maxlen=sample_limit)`（默认 256），注释写明「环形——面板只看最近几条，全量留着是内存负担」。**同一族的两处，处置不一致**。

---

### L1 · 错误级联防线以名字为键 · low

**位置**：`bus/bus.py:367`（`if name in self._errored_subscribers:`）· `bus/bus.py:387`（`self._errored_subscribers.add(name)`）

`_errored_subscribers` 是 `set[str]`，键是**订阅者名**（中间件与订阅者都走 `default_name()` 或显式名）。**两个同名订阅者会互相抑制报错**：装两套日志系统（名字都是"日志系统"）时，第二套的异常会被当成"错误级联里的再次抛错"**只记 trace**——真正的异常被吞掉。ADR-083 的口径是"**同一个订阅者**在一轮排空里只报一次错"，**同名不等于同一个订阅者**。键换成订阅对象身份即可。

---

### L2 · 空洞率镜像里 `bool` 被当成整数 · low

**位置**：`systems/metrics/aggregate.py:178`

```python
if isinstance(x, int) and isinstance(y, int) and isinstance(value, int):
    self._cells[(x, y)] = value
```

`isinstance(True, int)` 为真——`value=True` 会被写进镜像，并在 `hole_stats()` 里被当作**实心格**（`if value == 0: continue`）。契约的格子取值是整数（0 = 空），出现 `True` 说明上游给了不合契约的值。**同项目其他地方都排了布尔**（`aggregate.py:85` 的 `_int` 明确 `isinstance(value, bool)` 排除；`archive.py:373` · `format.py:303` · `trace.py:283` 都排除布尔），**这一处漏了**。

---

### L3 · 落盘目录的可写性判定用 `os.access` · low

**位置**：`host/assembly.py:97-110`

`_is_writable_dir` 用 `os.access(probe, os.W_OK)` 判可写。在 Windows 上，**目录 ACL** 存在时 `os.access` 的结果不可靠（可能认为可写，真写时抛 `PermissionError`）；`Path.exists()` 对权限不足的目录也可能返回 `False`。

**为什么只算低危**：**失败不静默**——`FileSink.__call__` 的 `mkdir` / `write_bytes` 会当场抛 `OSError`（ADR-064 的口径："目录建不出来、磁盘满、权限不足——一律当场抛"），所以最坏情况是**回落路径没被选中、写入时才报错**，而不是"默默换地方"。但 ADR-074 承诺的是"不可写时**回落** home"，**这条承诺在 ACL 场景下兑现不了**。可考虑在判定失败时**直接试写一个临时文件**（真写一次，最准），或至少把 `os.access` 的不可靠写进 docstring。

---

## §5 覆盖与声明

| 项 | 值 |
| --- | --- |
| total_files | 36 |
| reviewed_files | 36 |
| skipped_files | 0 |
| **coverage_rate** | **100%** |

- 本报告的每条结论都附了**位置**（`文件:行`）与**证据**：H1 / H3 是实跑复现，H2 是全仓 grep 定位，其余为源码走查。
- **未做变异测试**：没有把"守卫被改坏"逐个反证（批次 D 做过），本报告的判断强度限于"当前代码确实这样写、实测确实这样表现"。
- **性能数字不引用**：M2 记录的单帧开销与千局基准是某台机某时段的数，**本报告不拿它作任何判据**。
- **OCR 排除的 24 个文件未逐字审**（`docs/` 8 · `tests/` 16）——其中 16 个测试文件的**断言强度**未被审查，H1/H2/H3 的判断不依赖它们。
- **M1 遗留的三条真偏离**不在本次范围，M2 未修，本报告不重复计。

### 建议的处理顺序

1. **H1 + H2 一起做**：两者都在 `host/assembly.py` 的工厂签名上（分 sink · 补 provider），是同一处改动。
2. **H3 与 H2 一起收尾**：H2 修好之前，"录像没基准"是默认状态，H3 的退出码语义没有意义；H2 修好后 H3 仍要修——否则任何没带基准的录像仍然判绿。
3. **M1 / M2 建议在 S15 收尾前处理**：两者都会让"四项全等"这条判据在非标准场景下给出**误导性结论**（M1：报"不等"而不是"规则集不对"；M2：录像静默丢参后仍然"四项全等"或"不等"，但原因不可查）。
4. **M3 / M4 与 L1–L3** 可进 S15 的收尾批次；它们不改变 M2 的交付结论，但 M3 是文档与行为不一致、M4 与同族实现不一致，都属"顺手就改"的量级。
