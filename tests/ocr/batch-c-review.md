# 批次 C 代码审查报告（S14 宿主装配与无头闭环 · S15 测试骨架与五道门 · 重力间隔增补）

> **审查方**：外部审查对话（`/open-code-review-delegate` 委托模式）
> **审查方式**：OCR 只做**文件选择与规则解析**，审查判断全部由宿主 Agent 完成
> **基线**：`feature/batch-c` @ **`bd1a91a`**（分支点 = 批次 B 尖端 `50aebea`）
> **审查日期**：2026-10-24
> **审查范围**：批次 C 的 **24** 个改动文件（9 可审 + 15 排除，**全部逐一读过**）

---

## §1 概要

批次 C 落了三件事：**S14 宿主装配与无头闭环**（`host/assembly.py` · `host/loop.py` · `host/cli.py` · `__main__.py`）、**S15 测试骨架与五道门**（`tests/conftest.py` + 五个门测试文件 + 契约声明测试补全），以及收口时并入的**重力间隔兑现**（`ab6211d` · ADR-066 · **契约增补一个快照字段 `gravityTimer`**）。

**代码本身质量高**：模块边界干净（宿主层是唯一知道"怎么把部件拼起来"的地方）、注释与理由密度都到位、`finally` 排空帧推进序列这一手写得对、重力间隔的兑现走的是"允许新增键"那一侧（既有键的哈希一个没动）。**没有发现会导致数据错误或崩溃的代码缺陷。**

**问题集中在"牙齿"上**：本轮共记 **20 项发现**——**高 5 · 中 7 · 低 8**。其中 **9 项是门判据本身可以被"破坏 → 必绿"绕过**：G0-1 的「中途无异常」断言**恒真**（环形历史把 10000 帧的异常截断成看不见）、G0-2 的「对应语义事件」用**集合交集**判定（"改了分数却没发事件"照样全绿）、G0-1 在**批次 C 自己刚修掉的那个缺陷**下仍然全绿、宿主「逐条转发」被截断成"每批只发第一条"无人发现、`state:changed` 的**负载值**一个都没有守卫。

**另有 1 项有硬截止期**：C1（`state:changed` 的契约产出者是 `kernel`、实际发出者是宿主）——**产出者进契约基线**，而契约 v1 基线**已经在 `feature/batch-d` 上冻结**（`1eae538` · 356 条），纠正它现在要走 major 变更流程。

**另有 6 条疑似问题经复核后否掉**，逐条写在 §6。

> **审查基线在审查期间动过两次**：开工时是 `93e9956`，审查中被并发会话推进到 `bd1a91a`（并入重力间隔增补），同时 `feature/batch-d` 上**冻结了契约 v1 基线**。本报告的所有结论都**按最终基线 `bd1a91a` 复核过**，详见 §2.4。

---

## §2 审查范围与基线

### 2.1 OCR 委托输出（按最终基线重跑）

```text
ocr delegate preview --format json --from feature/batch-b --to feature/batch-c
```

| 项 | 值 |
| --- | --- |
| mode | `range` |
| from / to | `feature/batch-b` / `feature/batch-c` |
| merge_base | `50aebea50ce37486fa05b5f14d7ada0607262020` |
| total_files | **24** |
| reviewable_count | 9 |
| excluded_count | 15 |
| total_insertions / deletions | 2579 / 45 |

**批次 C 的提交链**（`50aebea..feature/batch-c`，9 笔）：

| 提交 | 类型 | 内容 |
| --- | --- | --- |
| `8ed41bc` | 文档 | 先在 §1.4.4.1 钉死宿主 ↔ 内核 ↔ 总线三条跨写手接口（102 行） |
| `ab4ca47` | 新功能 | S14 宿主装配与无头闭环 |
| `d396e2d` | 修复 | **内核侧修正**：帧推进的出生步无条件换掉活动方块 |
| `5c2e249` | 测试 | S15 测试骨架与五道门 |
| `f90e7a2` | 新功能 | 收口批次 C 的包级导出面 |
| `3e065f2` | 文档 | 勾掉契约 v1 冻结清单的第 14 项 |
| `93e9956` | 文档 | 同步批次 C 的状态与哈希（**审查开工时的基线**） |
| `ab6211d` | 新功能! | **兑现重力间隔并给契约增补 `gravityTimer` 字段**（ADR-066） |
| `bd1a91a` | 文档 | 同步重力间隔增补后的状态与口径（**审查最终基线**） |

### 2.2 逐文件处置（24 / 24 全部读过）

**可审文件（OCR 给出，9 个）**

| # | path | status | ± | 处置 |
| --- | --- | --- | --- | --- |
| 1 | `src/tetris_kernel/__main__.py` | added | +14/-0 | reviewed |
| 2 | `src/tetris_kernel/contracts/snapshot.py` | modified | +11/-2 | reviewed（`gravityTimer` 增补） |
| 3 | `src/tetris_kernel/host/__init__.py` | modified | +17/-1 | reviewed |
| 4 | `src/tetris_kernel/host/assembly.py` | added | +113/-0 | reviewed |
| 5 | `src/tetris_kernel/host/cli.py` | added | +143/-0 | reviewed |
| 6 | `src/tetris_kernel/host/loop.py` | added | +254/-0 | reviewed |
| 7 | `src/tetris_kernel/kernel/core.py` | modified | +49/-9 | reviewed（出生步修正 + 重力计时） |
| 8 | `src/tetris_kernel/kernel/snapshot.py` | modified | +4/-1 | reviewed |
| 9 | `tests/conftest.py` | added | +93/-0 | reviewed |

**排除文件（OCR 标 `unsupported_ext` / `default_path`，15 个 —— 审查方仍然逐一读过）**

| # | path | status | ± | 排除理由 | 处置 |
| --- | --- | --- | --- | --- | --- |
| 10 | `docs/plan-M0.md` | modified | +124/-16 | unsupported_ext | reviewed（§1.4.4.1 接口先验定 + S12/S15 段口径） |
| 11 | `docs/决策记录.md` | modified | +9/-0 | unsupported_ext | reviewed（**ADR-066**） |
| 12 | `docs/需求与边界.md` | modified | +1/-0 | unsupported_ext | reviewed（快照表增补 `gravityTimer`） |
| 13 | `docs/spec-M0.md` | modified | +3/-2 | unsupported_ext | reviewed |
| 14 | `docs/契约v1清单.md` | modified | +2/-2 | unsupported_ext | reviewed（第 3 / 14 项） |
| 15 | `PROGRESS.md` | modified | +16/-8 | unsupported_ext | reviewed |
| 16 | `ai_docs/261003.md` | modified | +51/-0 | unsupported_ext | reviewed |
| 17 | `tests/test_host_loop.py` | added | +400/-0 | default_path | reviewed |
| 18 | `tests/test_kernel_lifecycle.py` | added | +358/-0 | default_path | reviewed（G0-1 / G0-2） |
| 19 | `tests/test_determinism.py` | added | +264/-0 | default_path | reviewed（G0-3） |
| 20 | `tests/test_command_rounds.py` | added | +186/-0 | default_path | reviewed（G0-4） |
| 21 | `tests/test_bus_isolation.py` | added | +132/-0 | default_path | reviewed（G0-5） |
| 22 | `tests/test_contracts_declarations.py` | modified | +184/-3 | default_path | reviewed |
| 23 | `tests/test_frame_model.py` | modified | +148/-1 | default_path | reviewed（出生步守卫 + 重力间隔守卫） |
| 24 | `tests/test_snapshot.py` | modified | +3/-0 | default_path | reviewed |

**覆盖率：24 / 24 = 100%**（9 可审 + 15 排除，全部读过并给出处置；排除文件是 OCR 的**扩展名 / 默认路径规则**所致，**不是审查方跳过的文件**——其中 **8 个是本批次新增或修改的测试文件**，占改动量的 75%）。

### 2.3 快照与并行会话的处理

审查期间工作区被并发会话推进（`feature/batch-c` → `bd1a91a`）**且主工作区分支被切走**（当前在 `feature/batch-d`）。审查方**全程不切主工作区分支**，一律用 `git archive feature/batch-c` 导出只读快照到工作区内临时目录，在快照上做阅读、跑门禁、跑探针、跑变异；临时产物在收尾时删除。

### 2.4 基线推进对本报告结论的影响（逐条核对）

| 推进 | 内容 | 对结论的影响 |
| --- | --- | --- |
| `93e9956` → `bd1a91a` | 并入重力间隔增补（`ab6211d`）：`kernel/core.py` 的重力步改为计时驱动、契约增补 `gravityTimer` | **19 项结论逐条重跑，全部仍成立**；C1 的严重度**上调**（见下）；新增 C21（重力间隔与 `state:changed` 的交互，已核对无缺陷） |
| `feature/batch-d` 的 `1eae538` | **契约 v1 基线已冻结**（356 条） | C1 的"截止期"从"S17 之前"变成"**已经过了**"——纠正产出者现在要走 major 变更流程 |

---

## §3 发现清单

| 编号 | path | 行号 | category | severity | 一句话 |
| --- | --- | --- | --- | --- | --- |
| **C1** | `src/tetris_kernel/host/loop.py` | 219–247 | documentation / bug | **high** | `state:changed` 契约声明产出者是 `kernel`，实际发出者是宿主；**产出者已进冻结的契约基线** |
| **C2** | `tests/test_kernel_lifecycle.py` | 278–280 | test | **high** | G0-1 的「中途无异常」扫描被**环形历史截断** → 断言**恒真**（10000 帧里的异常一条都看不见） |
| **C3** | `tests/test_kernel_lifecycle.py` | 330–338 | test | **high** | G0-2 的「对应语义事件」用**集合交集**判定 → "改了分数却没发事件"**全套测试全绿** |
| **C4** | `tests/test_kernel_lifecycle.py` | 268–286 | test | **high** | G0-1 在**批次 C 自己刚修掉的那个缺陷**下仍然全绿（统计断言写成 `>= 0`） |
| **C5** | `src/tetris_kernel/host/loop.py` | 196–204 | test | **high** | 宿主「**逐条转发**」被截断成"每批只发第一条"**无人发现**（守卫恰好只查每批第一条） |
| **C6** | `src/tetris_kernel/host/loop.py` | 228–247 | test | **medium** | **`state:changed` 的负载值一个都没有守卫**：5 组变异全部存活 |
| **C7** | `src/tetris_kernel/host/assembly.py` | 98–102 | bug | **medium** | 宿主发的 `state:changed` 的 `source` 恒为空串，与同帧内核事件的 `source='host'` 不一致 |
| **C8** | `src/tetris_kernel/host/loop.py` | 231 | documentation / bug | **medium** | `state:changed.version` 在 `reset` 后归零 → **非单调**，与契约"每次变更递增"的措辞冲突 |
| **C9** | `tests/test_kernel_lifecycle.py` | 330–341 | test | **medium** | 「**状态变了但版本号没递增**」全程无声——而版本号就是宿主的脏标记 |
| **C10** | `tests/test_kernel_lifecycle.py` | 282–286 | test | **medium** | G0-1 的「无未消费步骤」是**条件断言**且只看终态——docstring 比断言强得多 |
| **C11** | `src/tetris_kernel/host/loop.py` | 116–125 | test | **medium** | `run_frame()` 的返回值（**先验定签名里的公开接口**）无人断言，恒返回 0 也全绿 |
| **C12** | `src/tetris_kernel/host/cli.py` | 126–136 | test | **medium** | CLI `sim` 的输出**值**无人断言（人读形态只查标签在不在，JSON 形态只查字段集合） |
| **C13** | `src/tetris_kernel/host/assembly.py` | 103–107 | maintainability | low | `Assembly.clock` 是**死字段**——帧循环完全不看时钟，`step_ms` 对节奏零影响 |
| **C14** | `src/tetris_kernel/host/cli.py` | 99–103 | bug | low | CLI 人类可读输出在 Windows 管道下按**本地代码页（GBK）**编码，按 UTF-8 读会炸 |
| **C15** | `src/tetris_kernel/host/cli.py` | 40–45 | documentation | low | `main()` 的 docstring 说"返回退出码，不抛异常"，但 argparse 的参数错误路径抛 `SystemExit` |
| **C16** | `tests/conftest.py` | 53–59 | maintainability | low | 导入期无条件 `settings.load_profile` → 命令行 `--hypothesis-profile=xxx` 被静默覆盖 |
| **C17** | `src/tetris_kernel/host/loop.py` | 127–143 | bug | low | `run_frames(-1)` 静默跑 0 帧并返回一份"跑了 0 帧"的报告（CLI 挡了负数，API 没挡） |
| **C18** | `tests/test_kernel_lifecycle.py` | 249–256 | test | low | G0-1 用 `pytest.importorskip` 兜底宿主层 → 依赖缺失时**静默跳过（绿）而不是变红** |
| **C19** | `tests/test_contracts_declarations.py` | 1982–2005 | test | low | 「快照 = 内核状态全集」两条新守卫只覆盖 `KernelState` 的 **dataclass 字段**，内核的普通属性不在内 |
| **C20** | `tests/test_contracts_declarations.py` | 1942–1952 | test | low | 「`state:changed` 不带整张棋盘」的辅判据是**字符串启发式**（只认 `tuple[tuple[int` 一种拼法） |

**分布**：**高 5 · 中 7 · 低 8 = 20 项**。

**观察（不计入发现）**

| 编号 | 观察 |
| --- | --- |
| O1 | 局结束（`game_over`）之后宿主仍每帧派发 `tick` → 每帧 2 条事件（`command:received` + `command:rejected`）。重力间隔兑现后，10000 帧里前 ~8000 帧在真打（ADR-066 记的"前 ~250 帧"是兑现前的数），**这个比例已改善**，但拒绝路径仍在尾部存在。 |

---

## §4 逐条详情

### C1 · `state:changed` 的契约产出者与实际发出者不符（且已冻结） · high

**位置**：`src/tetris_kernel/host/loop.py:219–247`（宿主构造并发布）· `src/tetris_kernel/contracts/events.py:565–568`（契约声明 `producer="kernel"`）· `src/tetris_kernel/contracts/events.py:570`（`version` 字段措辞）· `tools/contract_baseline.py:190`（产出者进基线）

**事实**：

- 需求 §8.1 的事件表里 `state:changed` 的**产出者列写的是「内核」**，而该列的定义是"**「产出者」列 = 谁发出这个事件**，与信封元数据里的 `producer` 字段一致（由总线在分发时自动填）"。
- 但批次 C 把这条事件的**发出方放到了宿主**（`host/loop.py` 的 `_emit_state_changed()` → `bus.publish(...)`），而总线按注册表填 `producer`（内核填的一律忽略）→ **每一条帧末事件的信封元数据都是 `producer='kernel'`**。实测：

```text
command:accepted   metadata=(('source', 'host'), ('producer', 'kernel'), ('cause', (1, 0)))
clock:gravity      metadata=(('source', 'host'), ('producer', 'kernel'), ('cause', (1, 0)))
state:changed      metadata=(('source', ''),   ('producer', 'kernel'), ('cause', None))
```

- **ADR-063 的先例没有沿用**：那条 ADR 定的正是"插件生命周期事件由 host 发出，类型名不动"，`kernel:system:installed` / `kernel:system:removed` 的 `producer` 因此写成 `host`。同一类情形（宿主发出）这里却保留了 `kernel`。
- **而且已经冻结了**：`tools/contract_baseline.py:190` 把产出者收进基线——

```python
add(f"event/{event_type}/producer", spec.producer)
```

  而 `feature/batch-d` 的 `1eae538` **已经冻结了契约 v1 基线**（356 条），其中就有 `event/state:changed/producer`。按 S6 定的基线口径"禁止已有键的哈希变化"，**现在改产出者 = 一次 major 变更**。

**为什么是缺陷**：需求 §8.1 的「产出者」列（其定义是"谁发出这个事件"）失真；`spec-M0.md` §6.1 的产出者分布（内核 35 · host 2）同样失真；**FR-9.2 的依赖图生成按 `producer` 画归属**，帧末的状态变化会被画成"内核产生的"。

**修法建议（必须拍板）**：

- **甲**：产出者改成 `host`。要动：① `contracts/events.py` 的 `state:changed` 产出者；② 需求 §8.1 的产出者列 + `spec-M0.md` §6.1 的分布（内核 35 → 34，host 2 → 3）；③ `tests/test_contracts_declarations.py` 的 `EXPECTED_EVENTS` 与 `EXPECTED_PRODUCER_COUNTS` 两张转录表；④ **契约基线要重生成**（`event/state:changed/producer` 的哈希变了）——**这是一次 major 变更**，要走变更流程并重新冻结。
- **乙**：保留 `kernel`，把"宿主代发、产出者仍记内核"写进 ADR（理由：负载整份是内核状态），并在需求 §8.1 的表注里点明这一条例外。**代价**：这张表从此有一条例外，下游必须知道。

**未验证**：没有查下游（M1 的渲染器 / 回放系统）是否已经按 `producer` 分流——M1 还没写，**现在改代价最小**。

---

### C2 · G0-1 的「中途无异常」断言恒真（环形历史截断） · high

**位置**：`tests/test_kernel_lifecycle.py:278–280`

**断言原文**：

```python
errors = [envelope for envelope in assembly.bus.history() if envelope.type == "kernel:error"]
assert not errors, f"无头跑批里出现了 {len(errors)} 条 kernel:error：{stats}"
```

**事实**：`bus.history()` 是**环形缓冲**（`bus/bus.py:88` 的 `deque(maxlen=history_limit)`，默认 `DEFAULT_HISTORY_LIMIT = 1024`）。10000 帧实测共发 **5 万余条**事件（重力间隔兑现后约 5.0 万条），跑完后 `history()` 只有 **1024** 条——**最老一条的帧号在 9000 之后**。也就是说这条断言只扫描了**最后约 200 帧**。

**宿主复核（独立复现）**：在**第 0 帧**就挂一个抛错的订阅者，跑满 10000 帧后照抄 G0-1 的原断言：

```text
G0-1 原断言看到的 kernel:error 条数 = 0
G0-1 原断言是否通过 = True
```

**断言恒真**——10000 帧里的任何异常都看不见，而"中途无异常"正是 AC-5 / G0-1 的明文判据（plan §5.2 指定的门复跑入口就是这一条）。

**修法建议**：改成**过程中**判定——订阅 `kernel:error` 计数，结束时断言计数为 0；或者 `assemble(..., history_limit=<足够大>)`（但 10000 帧需要 5 万条以上，得是个魔数，不如前者）。

---

### C3 · G0-2 的「对应语义事件」用集合交集判定 · high

**位置**：`tests/test_kernel_lifecycle.py:330–338`（期望表在 93–110）

**断言原文**：

```python
assert set(semantic) & EXPECTED_SEMANTIC_EVENTS[command_type], (
    f"{command_type} 的语义事件对不上：{types}"
)
```

**事实**：`EXPECTED_SEMANTIC_EVENTS` 是**每条命令一个集合**，断言只要求**交集非空**。`softDrop` 同时改了「位置」和「分数」两件事，期望集合 `{"piece:softdrop", "piece:moved"}` 里**根本没有 `score:changed`**——少发分数事件没有任何断言会红。

**宿主复核（独立复现）**：把 `kernel/core.py::_cmd_soft_drop` 的 `score:changed` 事件整块删掉（**分数照样 +1 = 改了状态却没发事件**）：

```text
-k event_completeness → exit 0 | 96 passed, 569 deselected
全套 tests/          → exit 0 | 621 passed, 44 skipped
```

**这直接违反需求 AC-2 的原话「不存在"改了状态没发事件"」**。同族问题：`injectGarbage` 的期望集合 `{"board:garbage-received", "board:changed"}` 也是二选一。

**修法建议**：期望表改成**命令 → 必须全发的语义事件集合**（`⊆` 而非 `∩`），或对"多副作用命令"逐个副作用设断言。

---

### C4 · G0-1 在批次 C 自己刚修掉的缺陷下仍然全绿 · high

**位置**：`tests/test_kernel_lifecycle.py:268–286`（关键在 272 与 283–286）

**断言原文**：

```python
assert report.score >= 0 and report.lines >= 0 and report.piecesPlaced >= 0, stats
...
if report.phase == "running":
    assert snapshot.active is not None, "running 态没有活动方块——步骤序列没被耗尽"
```

**事实**：`0 >= 0` 恒真；"无未消费步骤"那条是**条件断言**，且缺陷态下 `active` 恰好非空（方块每帧被换新），条件满足、断言通过。

**宿主复核（独立复现）**：把 `kernel/core.py::step_spawn` 改回**无条件出生**（即 `d396e2d` 修掉的那处缺陷）→

```text
-k headless_10000_frames          → exit 0 | 1 passed
tests/test_kernel_lifecycle.py     → exit 0 | 98 passed
全套 tests/                       → exit 1 | 5 failed, 616 passed   ← 是别的文件抓住的
```

**门判据文件本身完全绿**。抓住它的是 `tests/test_host_loop.py:121` 的 `report.piecesPlaced > 0`——**那个文件不在 plan §5.2 指定的 G0-1 门文件里**。

**修法建议**：统计断言改成 `piecesPlaced > 0`（或与已知种子的期望值相等）；"无未消费步骤"去掉 phase 条件分支，改为逐帧检查。

---

### C5 · 宿主「逐条转发」被截断无人发现 · high

**位置**：`src/tetris_kernel/host/loop.py:196–204`（缺口在守卫侧：`tests/test_host_loop.py:301–302` · `323`）

**事实**：plan §1.4.4.1 ② 把「宿主负责**逐条**转发」写成宿主的核心职责（内核 `publish` 吃一批、总线 `publish` 吃一条）。现有守卫**恰好只查每批的第一条**——`assert "piece:locked" in published`，而 `piece:locked` 正是锁定批次的第一条。

**宿主复核（独立复现）**：把 `_publish_batch` 的循环改成只发每批第一条 →

```text
全套 tests/ → exit 0 | 621 passed, 44 skipped
```

后果实测（200 帧）：只发 **489** 条事件（正常约 2000+），`piece:locked` 之后的 `board:changed`、`score:changed`、各 `rng:next` **全部静默丢失**——订阅者与回放对齐都会缺事件，而回放对齐正是需求 S-3 / AC-2 的根基。

**修法建议**：加一条"宿主转发后总线上收到的事件数 == 内核产出的草稿总数"的断言。

---

### C6 · `state:changed` 的负载值一个都没有守卫 · medium

**位置**：`src/tetris_kernel/host/loop.py:228–247` · `tests/test_host_loop.py:246–258` · `tests/test_contracts_declarations.py`（S15 新增的三条契约用例）

**事实**：`state:changed` 的负载有 **13 个字段**，但**值**没有任何守卫：

- 实现侧：`make_draft`（`kernel/drafts.py:126–146`）**只校验负载字段名与顺序**，不校验值；
- 测试侧：`test_state_changed_payload_matches_the_contract` **只断言字段名与顺序**；
- 契约侧：S15 新加的 `test_state_changed_payload_is_a_projection_of_the_snapshot` **只比类型串**，也不比值。

**实测（变异测试，5 组全部存活）**：

| 编号 | 变异 | 结果 |
| --- | --- | --- |
| M1 | `("score", snapshot.score)` → `("score", 0)` | 🟢 **存活** |
| M2 | `("holdUsed", snapshot.holdUsed)` → `("holdUsed", False)` | 🟢 **存活** |
| M3 | `("next", snapshot.next)` → `("next", ())` | 🟢 **存活** |
| M13 | `("activeType", ...)` → `("activeType", None)` | 🟢 **存活** |
| M14 | `("version", snapshot.version)` → `("version", 0)` | 🟢 **存活** |

对照：同一批变异里，**信封帧号恒 0（M4）、去掉版本判断（M5）、去掉 `finally` 排空（M6）、总线视图不是快照（M7）、不建虚拟时钟（M8）、CLI 少输出一行（M9）、不派发 tick（M11）全部变红**——说明测试面**不是**整体失守，缺口**只**在"负载的值"这一层。

**当前行为是对的**（探针逐字段核对 13 个值，全部与帧末快照一致）——**问题不是现在是错的，是"现在是对的"没有牙齿**。

**修法建议**：在 `tests/test_host_loop.py` 补一条**值级**用例——跑若干帧后，把末条 `state:changed` 的 13 个字段与**同一时刻的帧末快照**逐项比对（探针 P3 的做法就是模板）。这条用例能同时守住 C8 的 `version` 语义。

---

### C7 · 宿主发的 `state:changed` 的 `source` 恒为空串 · medium

**位置**：`src/tetris_kernel/host/assembly.py:98–102`（总线构造）· `src/tetris_kernel/host/loop.py:246`（元数据只带 `cause`）

**事实**：

- 宿主有明确的 source 概念：`HeadlessLoop.__init__(assembly, *, source="host")`，注释写"**`source` 是命令的来源标记（只随事件透传）**"。它进了每一条内核事件的元数据（实测 `source='host'`）。
- 但 `assemble()` 构造总线时**没有传 `default_source`**，`EventBus` 的默认值是 `""`；而帧末事件**元数据只带 `cause=None`** → 总线按 `default_source` 填 → **`source=''`**。
- 于是同一条事件流里，帧末的状态变化与 `kernel:ready` / `game:created` 落在同一个"无上游命令"桶里，而同帧的内核事件全是 `source='host'`。

**为什么是缺陷**：`source` 是契约的三键之一（ADR-062），语义是"命令来源"。帧末的状态变化**确实**有上游（宿主驱动的 tick），把它记成"无上游命令"会让按 `source` 分组/过滤的消费方漏掉它。

**修法建议**：二选一。甲：`HeadlessLoop.__init__` 里给草稿补 `METADATA_SOURCE`（草稿带 `source` 时总线优先取草稿的）；乙：给 `assemble()` 加 `source` 参数并传给 `EventBus(default_source=...)`，与循环的 source 对齐。

**守卫缺口（实证）**：变异 **M15**（在 `assemble()` 里给总线接上 `default_source="host"`）**存活**——全套测试仍然全绿。**没有任何测试断言 `state:changed` 的 `source` 是什么**。

---

### C8 · `state:changed.version` 在 `reset` 后归零，不单调 · medium

**位置**：`src/tetris_kernel/host/loop.py:231`（`("version", snapshot.version)`）· `src/tetris_kernel/kernel/core.py:617`（`reset` 归零版本号，批次 B 的代码）· `src/tetris_kernel/contracts/events.py:570`

**事实**：契约里 `version` 的说明是"**状态版本号，每次变更递增**"。但内核的 `reset` 命令把版本号**归零**（批次 B 的设计："`reset` 归零帧号、帧内序号与版本号"）。批次 C 之前**没有人发过 `state:changed`**，这条不一致是第一次被暴露出来。

实测（探针）：

```text
跑 3 帧 → pause → resume → reset，逐帧记 state:changed 的 version：
  事件 version 序列: [2, 3, 4, 5, 6, 0]      ← 归零，不是递增
```

**影响**：契约明说"每次变更递增"，下游完全可以据此做"版本比较 / 去重 / 判断是否回退"。

**没有发现的更坏情形**（复核过，**不要按"漏事件"上报**）：`reset` 之后宿主仍会发一条 `state:changed`（version 0 ≠ 上一帧末的 N），**不存在"状态变了但没发事件"**。唯一的例外是"reset 之后版本号恰好等于上一帧末"（连续两次 reset），但那时状态确实没有净变化，不发是对的。

**修法建议**：改契约措辞，不改代码——把 `contracts/events.py:570` 的字段说明改成"状态版本号，每次变更递增；**`reset` 后归零**"。**基线哈希的是类型串、不是说明文字**，所以这条改动**不撞基线**。

---

### C9 · 「状态变了但版本号没递增」全程无声 · medium

**位置**：`tests/test_kernel_lifecycle.py:330–341`（关键 331 与 340）

**断言原文**：

```python
increments = max(0, after.version - before.version)
assert increments <= len(semantic), ...
...
if increments == 0 and _game_state(after) == _game_state(before):
    assert not (set(semantic) & STATE_CHANGING_EVENTS), ...
```

**事实**：`increments` 变 0 之后，②的 `increments <= len(semantic)` 退化成恒真（0 ≤ n）；④因为 `_game_state` 确实变了而**整条被跳过**。于是"改了状态但脏标记没更新"全程无声。

**宿主复核（独立复现）**：把 `kernel/core.py::_cmd_move` 的 `self._touch()` 去掉（方块位置真的变了，版本号不动）→ **全套 `tests/` 全绿**（621 passed）。

**后果**：版本号是宿主的脏标记载体（架构 §6.6，`host/loop.py::_emit_state_changed` 靠它决定发不发 `state:changed`）。漏更 ⇒ `state:changed` 不发 ⇒ 渲染器不重绘，而**无任何报警**。

**同类**：`tests/test_determinism.py` 的属性测试用同一条 `increments <= len(semantic)`，同样不设防。

---

### C10 · G0-1 的「无未消费步骤」是条件断言、且只看终态 · medium

**位置**：`tests/test_kernel_lifecycle.py:282–286`

**断言原文**：

```python
if report.phase == "running":
    assert snapshot.active is not None, "running 态没有活动方块——步骤序列没被耗尽"
if report.phase == "game_over":
    assert snapshot.active is None, "game_over 态还挂着活动方块"
```

**事实**：phase 落在两者之外（例如 `paused`）时**整条检查被跳过**；且只看**终态快照**，中途帧的半帧残留（"锁定已完成、消行未做"，架构 §6.2 明令禁止的形状）不在检查范围。docstring 声称的"**无未消费步骤**：帧推进的步骤序列被完整消费"比断言强得多。

**旁证**：逐帧版本确实存在（`tests/test_host_loop.py:154–167`），但它不在 §5.2 的门入口里。

---

### C11 · `run_frame()` 的返回值无人断言 · medium

**位置**：`src/tetris_kernel/host/loop.py:116–125`（返回值）· 调用处 `tests/test_host_loop.py` 九处全部丢弃返回值

**事实**：plan §1.4.4.1 ② 把 `run_frame(self) -> int`（"**本帧发出的事件数**"）写进**先验定签名**；`host/loop.py` 的 docstring 也明写这个语义。

**宿主复核（独立复现）**：把 `return bus.last_seq - before` 改成 `return 0` → **全套 `tests/` 全绿**（621 passed）。公开 API 的返回值可以静默恒为 0 而无人察觉。

**修法建议**：在 `test_run_frames_advances_exactly_the_requested_number_of_frames` 旁边补一条——逐帧断言 `run_frame()` 的返回值等于该帧内核实际产出的草稿条数（与 C5 的修法可以合并成同一条断言）。

---

### C12 · CLI `sim` 的输出值无人断言 · medium

**位置**：`src/tetris_kernel/host/cli.py:126–136`（`_report_lines`）· `tests/test_host_loop.py:368–393`

**事实**：人读形态只查**标签字符串在不在**（`sim --frames 5` 打印「帧数：99999」照样过）；JSON 形态只查**字段集合** + `frames` 一个值。`score` / `lines` / `piecesPlaced` / `events` / `fingerprint` 的**值**无人守。

**宿主复核（独立复现）**：把 `_report_dict` 的 `"score"` 与 `"events"` 写死 → **全套 `tests/` 全绿**。

**口径说明**：plan §1.4.4.1 ④ 只要求"**必须含**"这些项，所以这是**规格口径偏弱**而非测试违背规格；但"正确统计"（AC-5 原文）这一层确实无人守。

---

### C13 · `Assembly.clock` 是死字段 · low

**位置**：`src/tetris_kernel/host/assembly.py:103–107` · `src/tetris_kernel/host/loop.py`（全文）

**事实**：`Assembly` 有 `clock` 字段，`assemble()` 在省略时建 `VirtualClock(step_ms=step_ms)`，但**帧循环一次都没读它**——`HeadlessLoop` 跑帧不 sleep、不看时间。全库检索 `assembly.clock` 只有两处命中：装配层自己 + `tests/test_host_loop.py:346` 的一条"断言它是 VirtualClock"的用例。

**为什么只算 low**：plan §1.4.4.1 ① 把 `clock` 写进了**先验定接口**，它本来就是给 M1 的回放 / 倍速预留的——字段本身符合规格。问题只在于**它现在看起来像"生效了"**。

**修法建议**：在 `Assembly` 的 docstring 上写明"**M0 尚无消费方**（回放 / 倍速在 M1）"，或让 `HeadlessLoop.run_frame()` 真的走一次时钟。**别让"预留"看起来像"已实现"**。

---

### C14 · CLI 的人类可读输出在 Windows 管道下是 GBK · low

**位置**：`src/tetris_kernel/host/cli.py:99–103`（`print`）· `126–136`（文案含中文）

**事实**：`tetris sim` 的输出含中文（"帧数：5" 等）。在 Windows 上，stdout **被管道接走时**用的是**本地代码页**（本机实测 = GBK/CP936），不是 UTF-8。实测字节：

```text
b'\xd6\xa1\xca\xfd\xa3\xba5\r\n\xca\xc2\xbc\xfe\xca\xfd\xa3\xba38\r\n...'
→ GBK 解码 = 帧数：5 / 事件数：38 / 棋盘指纹：...
→ UTF-8 解码 = UnicodeDecodeError
```

`--json` 路径不受影响（字段名与取值全是 ASCII）。

**修法建议**：要么在 `main()` 里对 stdout 显式 `reconfigure(encoding="utf-8")`，要么在 README / `--help` 里写明"人读路径按本地代码页编码"。**宿主层允许碰 I/O**（不违反 B-2）。

---

### C15 · `main()` 的 docstring 与 argparse 的实际行为不符 · low

**位置**：`src/tetris_kernel/host/cli.py:40–45`

**事实**：docstring 写"**返回退出码**，不抛异常"。但 argparse 的参数错误路径是 `parser.error()` → `SystemExit(2)`，**异常会穿出 `main()`**。实测 `main(["sim", "--frames", "abc"])` 抛 `SystemExit`，不是返回 2。

进程级行为是对的（`__main__.py` 的 `sys.exit(main())` 与 `SystemExit` 都能让进程以 2 退出），所以**这是措辞与实现的出入，不是行为缺陷**。

---

### C16 · conftest 无条件加载 hypothesis profile · low

**位置**：`tests/conftest.py:53–59`

**事实**：导入期无条件执行 `settings.load_profile(HYPOTHESIS_PROFILE)`。pytest 的 `--hypothesis-profile=xxx` 选项在**收集阶段**生效，而 conftest 的导入发生在收集**之前**——命令行指定的 profile 会被这次 `load_profile` **静默覆盖**。

**修法建议**：在 docstring 里写明"**本仓库不使用 `--hypothesis-profile`**"，或让命令行选项优先。**不改也行**——但要写清楚。

---

### C17 · `run_frames(-1)` 静默跑 0 帧 · low

**位置**：`src/tetris_kernel/host/loop.py:127–143`

**事实**：`for _ in range(frames)` 对负数是空循环 → 返回一份"跑了 0 帧"的 `RunReport`，**不报错**。CLI 层挡住了（`frames < 0` → 打印"帧数不能为负"并返回 2），**API 层没挡**。

**修法建议**：与 CLI 口径一致，在 `run_frames` 开头加一句 `if frames < 0: raise ValueError(...)`。

---

### C18 · G0-1 的门判据用 `importorskip` 兜底宿主层 · low

**位置**：`tests/test_kernel_lifecycle.py:249–256`

**事实**：G0-1 用两段 `pytest.importorskip` 导入宿主装配层与帧循环，理由写的是"它没落地，这道门就验不了"——这是**并行开发期的脚手架**。问题在于：**`importorskip` 失败时该用例是「跳过」而不是「失败」**——门判据变绿，但它**什么都没验**。这正是 plan 的 S15「常见坑」点名的那一类："完备性检查写成'没报错就算过' → 必须在人为破坏时红，否则它什么也没检查"。

**为什么只算 low**：这个洞**目前被另一个文件堵着**——`tests/test_host_loop.py` 在模块顶层直接 `from tetris_kernel.host.assembly import assemble`，宿主层一旦消失，**它会在收集期直接报错**，整套变红。

**修法建议**：改成直接 import，让"宿主层没了"变成**红灯**而不是"绿灯 + 一条 skip"（本仓库已常驻 44 条 skip，再加一条很容易被忽略）。

---

### C19 · 「快照 = 内核状态全集」只覆盖 dataclass 字段 · low

**位置**：`tests/test_contracts_declarations.py:1982–2005`（新守卫）· `1883`（手写例外名单）

**事实**：docstring 的 headline 是「内核**真实持有的**每个状态字段都在快照映射表里」，但实现只遍历 `dataclasses.fields(KernelState)`。内核本来就用**普通属性**持有 `_seed` / `_step_ms` / `_mode` / `_piece_types` / `_rules`——这五个**都不在** `KERNEL_STATE_TO_SNAPSHOT` 里，说明"状态"在本仓库并不等于"dataclass 字段"。

**实测**：给 `Kernel.__init__` 加两个普通属性（`self._gravity_interval = 60` / `self._hold_timer = 0`）→ **全套 `tests/` 全绿**，两条契约用例也全绿。

**附带**：`KERNEL_ATTRIBUTES_OUTSIDE_STATE = frozenset({"ruleId", "frame"})` 是**手写例外名单**，与仓库反复强调的"门禁归属从契约推导、不手写名单"是同一种漂移源。

---

### C20 · 「`state:changed` 不带整张棋盘」的辅判据是字符串启发式 · low

**位置**：`tests/test_contracts_declarations.py:1942–1952`

**事实**：主断言 `assert "board" not in names` 是有效的；但辅判据只认 `tuple[tuple[int` 这一种拼法，棋盘若被声明成别的形状就绕过去了。

**诚实补充**：同文件的 `test_state_changed_payload_is_a_projection_of_the_snapshot` 会因字段集合对不上而红，**所以目前不构成实际漏洞**——报它是因为这条断言本身守不住它声称的东西。

---

## §5 探针与变异测试记录

审查方**自写探针**（不照抄任何既有结论），全部在只读快照上跑；**另派一个 verifier 子 Agent 独立审测试面**（35 组变异），其结论**逐条由宿主复核**——**6 条关键结论全部独立复现成立，1 条与宿主自己的发现重合（payload 值无守卫），其余为静态复核通过**。

### 5.1 宿主自跑的探针

| 探针 | 内容 | 关键输出 |
| --- | --- | --- |
| **P1** | 事件流逐帧拆解 + `state:changed` 负载与帧末快照逐字段核对 | 一帧 5 条事件；13 个字段**全部与快照一致**；`state:changed` 的 `source=''`、内核事件 `source='host'` |
| **P2** | CLI 四个子命令 + 错误路径（原始字节） | 占位命令退出码 0；`--frames abc` → argparse 退出码 2；人读输出是 **GBK** 字节 |
| **P3** | `pause` / `resume` / `reset` 路径下的 `state:changed` | version 序列 `[2,3,4,5,6,0]` → **reset 后归零** |
| **P4 / P5** | **15 组变异测试**（改快照里的被审源码 → 跑测试 → 字节级还原） | **存活 6 组**（M1/M2/M3/M13/M14 负载的值 + M15 `default_source`）；其余变红 |

### 5.2 宿主对子 Agent 结论的独立复核（**逐条复现**）

| 子 Agent 的结论 | 宿主复核 | 结果 |
| --- | --- | --- |
| F1：G0-1 的「中途无异常」被环形历史截断 → 断言恒真 | 挂一个**第 0 帧就抛错**的订阅者跑满 10000 帧，照抄原断言 | ✅ **成立**：断言看到 0 条错误、**通过** |
| F2：G0-2 的「对应语义事件」用交集 → 删掉 `score:changed` 全绿 | 删掉 `softDrop` 的 `score:changed` 事件块 | ✅ **成立**：`-k event_completeness` 96 passed，**全套 621 passed** |
| F3：出生步改回无条件出生 → G0-1 仍绿 | 把 `step_spawn` 的守卫删掉 | ✅ **成立**：`-k headless_10000_frames` **1 passed**，`test_kernel_lifecycle.py` **98 passed**（全套靠别的文件抓住，5 failed） |
| F4：`state:changed` 负载值无守卫 | 宿主自己的 M1/M2/M3/M13/M14 **全部存活** | ✅ **成立**（与宿主 C6 重合） |
| F5：宿主逐条转发被截断 → 全绿 | `_publish_batch` 只发每批第一条 | ✅ **成立**：**621 passed** |
| F6：`move` 改了状态但不递增版本号 → 全绿 | 去掉 `_cmd_move` 的 `self._touch()` | ✅ **成立**：**621 passed** |
| F8：`run_frame()` 恒返回 0 → 全绿 | `return bus.last_seq - before` → `return 0` | ✅ **成立**：**621 passed** |
| F7 / F9 / F10 / F11 | 静态复核断言原文与被测实现，逻辑成立 | ✅ 成立（未逐条跑变异） |
| F10 的附带观察：`KERNEL_ATTRIBUTES_OUTSIDE_STATE` 是手写例外名单 | 读代码确认 | ✅ 成立（并入 C19） |

**子 Agent 自查否掉的 12 条疑似问题**（快照深拷贝不可信 · `pytest.raises` 过宽 · `expected` 恒真 · hypothesis profile 恒真 · G0-4 无牙 · fixture 泄漏 等）——宿主复核**同意其中大部分**，并把「G0-4 无牙」这条**驳回**（子 Agent 自己后来用有效变异复现了它有牙，见其报告的假阳性表）。

### 5.3 变异存活总表（= 守卫缺口）

| # | 变异位置 | 破坏的行为 | 存活? | 关联发现 |
| --- | --- | --- | --- | --- |
| M1 | `host/loop.py` | `state:changed` 的 `score` 值写死 0 | **存活** | C6 |
| M2 | `host/loop.py` | `state:changed` 的 `holdUsed` 写死 False | **存活** | C6 |
| M3 | `host/loop.py` | `state:changed` 的 `next` 写死 `()` | **存活** | C6 |
| M13 | `host/loop.py` | `state:changed` 的 `activeType` 恒 None | **存活** | C6 |
| M14 | `host/loop.py` | `state:changed` 的 `version` 写死 0 | **存活** | C6 |
| M15 | `host/assembly.py` | 把总线的 `default_source` 接上 `'host'` | **存活** | C7 |
| N1 | `kernel/core.py` | 删掉 `softDrop` 的 `score:changed`（改了分数没事件） | **存活** | **C3** |
| N2 | `kernel/core.py` | `moveLeft` 改了状态但版本号不递增 | **存活** | **C9** |
| N3 | `host/loop.py` | 宿主逐条转发只发每批第一条 | **存活** | **C5** |
| N4 | `host/loop.py` | `run_frame()` 恒返回 0 | **存活** | **C11** |
| N5 | `host/cli.py` | CLI JSON 的 `score` / `events` 写死 0 | **存活** | **C12** |
| N6 | `kernel/core.py` | 出生步改回无条件出生 | **门文件绿** | **C4** |
| N7 | `kernel/core.py` | 内核新增普通属性（不在 `KernelState`） | **存活** | **C19** |

**被杀（守卫有效）的变异**，说明测试面**不是**整体失守：`state:changed` 无条件发 / 永不发 · `_drain` 变空操作 · 总线的 `view_provider` 不是快照 · 不建虚拟时钟 · CLI 少输出一行 · 不派发 tick · 信封帧号恒 0 · 主机帧数不累加 · 随机源不播种 · `kernel:error` 的 `eventSeq` 改错 · 契约 `state:changed` 类型改错 · 快照表删 `version` · 映射表指向不存在的字段 · phase 门禁失效。

---

## §6 假阳性自查（复核后否掉的疑似问题）

审查方对自己的每一条"疑似"都做了独立复核，以下**不计为发现**：

1. **「`state:changed` 少发了 / 多发了」**——复核：宿主每帧至多一条、版本不变则一条不发，**实测与版本变更次数逐帧对齐**（`test_state_changed_is_emitted_once_per_frame_at_frame_end` 也守着这条）。**否掉**。
2. **「reset 后漏发 `state:changed`」**——构造了"reset 之后版本号恰好等于上一帧末"的场景，看着像会漏发。复核：该场景下状态**确实没有净变化**（连续两次 reset），不发是对的。**否掉**（真正的缺陷是版本号不单调，见 C8）。
3. **「M12 证明 `source` 有守卫」**——第一次跑 M12 时测试变红，看似"有人守 `source`"。复核：变红原因是**我的探针把 `METADATA_SOURCE` 写进了 `loop.py` 而那里没导入它** → `NameError`，与 `source` 无关。改用 M15（接上总线的 `default_source`）复测 → **存活**，`source` 确实**无守卫**。**结论反转，C7 成立。**
4. **「`step_spawn` 的修正（`d396e2d`）会让方块永远不再出生」**——复核：出生步只在**活动方块被清空**（锁定步刚跑完）时才补一块；开局与 `hold` 两条命令路径各自直接调 `_spawn_piece()`，不受影响。实测 2000 帧：`piecesPlaced` 正常增长、局正常结束。**否掉**。
5. **「`step_spawn` 的修正漏掉了 `queue:refilled` / `rng:next` 事件」**——复核：这两个事件由 `_spawn_piece()` 产出，而 `_spawn_piece()` 在下一次真正出生时照常被调（RNG 事件是**延迟冲刷**，不丢）。**否掉**。
6. **「重力间隔的实现把 `grounded` 判错」**——复核：新判据 `grounded = (ghost_delta == (0,0))` 与旧判据"沿重力方向再走一格会不会撞"**等价**（`_compute_ghost_delta` 用的就是 `gravity_vector`，方向感知 ✓）；计时只管"什么时候下移"，触地每帧判，**落即锁不变**。**否掉**。

---

## §7 覆盖率

| 项 | 值 |
| --- | --- |
| `total_files` | **24** |
| `reviewed_files` | **24** |
| `skipped_files` | **0** |
| `coverage_rate` | **100%** |

**排除文件为何仍然计入**：OCR 的 `unsupported_ext`（`.md`）与 `default_path`（`tests/**` 默认路径）是**工具的文件选择规则**，不是审查方的取舍。批次 C 的 15 个排除文件里**有 8 个是本批次新增或修改的测试文件**（占改动量的 75%）——**跳过它们等于跳过本批次四分之三的内容**，因此审查方全部读完并逐条处置（§2.2）。

---

## §8 门禁实跑结果（基线 `bd1a91a` 快照）

| 门 | 命令 | 结果 |
| --- | --- | --- |
| 测试 | `python -m pytest -q` | **621 passed · 44 skipped**（13.3s） |
| 最慢用例 | `--durations=5` | `test_headless_10000_frames` 6.2s · `test_headless_loop_runs_a_long_burst` 1.1s |
| 静态检查 | `python -m ruff check .` | All checks passed! |
| 格式 | `python -m ruff format --check .` | 82 files already formatted |
| 类型 | `python -m pyright` | **0 errors, 0 warnings, 0 informations** |
| 分层 | `lint-imports` | **Contracts: 4 kept, 0 broken** |
| 纯净度 | `python tools/check_kernel_purity.py` | 绿（扫描 2 个目录，违规 0） |
| 事件登记 | `python tools/check_event_registry.py` | 绿（44 条事件登记） |
| 契约基线 | `tools/contract_baseline.py` | **356 条**（355 + `gravityTimer`） |

**与 PROGRESS 记录一致**（批次 C 落笔时是 615 passed / 355 条；重力间隔增补后 621 / 356），无出入。

---

## §9 建议与待裁定项

### 9.1 必须拍板的

| 项 | 内容 | 截止期 |
| --- | --- | --- |
| **C1** | `state:changed` 的契约产出者：改成 `host`（甲），还是保留 `kernel` 并写进 ADR（乙）？**产出者已进冻结的契约基线（`event/state:changed/producer`）——改 = major 变更 + 重生成基线** | **已经过了 S17 的窗口**（基线在 `feature/batch-d` 的 `1eae538` 冻结）——要么走一次 major 变更重生成基线，要么把乙写进 ADR |

### 9.2 建议直接修的（不涉及契约面）

| 项 | 建议 |
| --- | --- |
| **C2** | G0-1 的「中途无异常」改成**过程中**判定（订阅 `kernel:error` 计数），别扫环形历史 |
| **C3** | G0-2 的期望表改成**必须全发**（`⊆`），或对多副作用命令逐个副作用设断言 |
| **C4** | G0-1 的统计断言改成 `piecesPlaced > 0`；「无未消费步骤」逐帧查 |
| **C5 + C11** | 补一条"宿主转发后的事件数 == 内核产出的草稿总数"，同时守住「逐条转发」与 `run_frame()` 的返回值 |
| **C6** | 补一条 `state:changed` 负载**值**与帧末快照逐项比对的用例 |
| **C7** | 决定 `state:changed` 的 `source` 怎么带，并补一条断言 |
| **C9** | G0-2 的 `increments` 判据补一条"状态变了 → 版本号必须递增" |
| **C10** | 「无未消费步骤」去掉 phase 条件分支 |
| **C12** | CLI 输出补值级断言（用已知的种子比对） |
| **C8** | `contracts/events.py:570` 的 `version` 措辞改成"每次变更递增；`reset` 后归零"（**不动类型，不撞基线**） |
| **C13–C20** | 措辞 / 死字段标注 / 负帧数 / `importorskip` / dataclass 字段覆盖 / 字符串启发式——见各条 |

### 9.3 复核后确认**不需要动**的

- 批次 C 暴露并修掉的 `step_spawn` 缺陷（`d396e2d`）：**修法正确，回归守卫两条到位**，本轮复核未发现遗漏。
- **重力间隔的兑现**（ADR-066）：**走的是"允许新增键"那一侧**（既有键的哈希一个没动，基线 355 → 356），触地判定与旧判据等价，事件面零改动、旧录像仍可回放——**本轮复核未发现缺陷**。
- `state:changed` 由宿主在帧末发、内核零改动：**符合 §1.4.4.1 ③ 与 B-2 红线**，实现无误。
- 五道门的**方向**正确：G0-3 / G0-4 / G0-5 与契约声明面（字段名 / 顺序 / 类型、注册表顺序、快照映射双向）**确有牙齿**（15 组变异被杀）；问题集中在 G0-1 / G0-2 的判据强度。

---

## §10 附录：复现命令

```powershell
cd 'D:\PythonProject\Tetris'
$env:PYTHONDONTWRITEBYTECODE='1'

# 只读快照（并发会话期间不切主工作区分支）
New-Item -ItemType Directory -Path 'D:\PythonProject\Tetris\.tmp_bc2' | Out-Null
git archive feature/batch-c | tar -xf - -C 'D:\PythonProject\Tetris\.tmp_bc2'
cd 'D:\PythonProject\Tetris\.tmp_bc2'
$env:PYTHONPATH='D:\PythonProject\Tetris\.tmp_bc2\src'

# 五道门 + 两个脚本门 + 基线条目数
& 'D:\miniconda3\envs\py312\python.exe' -m pytest -q
& 'D:\miniconda3\envs\py312\python.exe' -m ruff check .
& 'D:\miniconda3\envs\py312\python.exe' -m ruff format --check .
& 'D:\miniconda3\envs\py312\python.exe' -m pyright
& 'D:\miniconda3\envs\py312\Scripts\lint-imports.exe'
& 'D:\miniconda3\envs\py312\python.exe' tools\check_kernel_purity.py
& 'D:\miniconda3\envs\py312\python.exe' tools\check_event_registry.py
& 'D:\miniconda3\envs\py312\python.exe' -c "import sys;sys.path.insert(0,'tools');import contract_baseline as cb;print(len(cb.collect_entries()))"
```

**OCR 委托命令**：`ocr delegate preview --format json --from feature/batch-b --to feature/batch-c`
