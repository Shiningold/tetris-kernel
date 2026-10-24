# 批次 B 代码审查报告 · S9 命令通道 · S10 合并与配额 · S11 事件总线 · S12 帧模型

> **轮次**：第 1 轮（首审）
> 审查范围：`f89ce9a..feature/batch-b`（批次 A 的落笔提交 → 批次 B 落笔提交，含 5 笔阶段/集成提交 + 1 笔并入的批次 A 修订 + 1 笔按本报告做的修正）
> 审查方式：`open-code-review-delegate`（OCR 只做文件选择与规则解析，**判断全部由宿主 Agent 完成**）
> 审查基线：**`810e035`**（`feature/batch-b` HEAD）
> 审查日期：2026-10-24
>
> **基线变更说明**：审查过程中分支被并行会话推进了两次，本报告已按 **HEAD `810e035`** 重新核过：
>
> 1. **`96f5f50`** —— 并入批次 A 的修订 `7b9e9fc`（15 项发现的修正）。**`core.py` 在 `aab75cf` 与 `96f5f50` 之间逐字相同**（批次 A 修订只动了 `rng.py` / `kernel/snapshot.py` / `rules/standard.py` / `rules/tables.py`），因此所有指向 `core.py` 的结论**不受这次推进影响**；唯一受影响的是 **B3**（`b2b` 口径）——已由规则层修掉，本报告改为「已消解」。
> 2. **`810e035`** —— 「修复：落空也调 `score_clear`——内核侧对齐计分新口径」，**正是本报告 B4 的修法**（该提交在我写报告期间由并行会话做出）。B4 因此在提交态上**已消解**；B5 的两条计分事件**顺带被补上**，但 `piece:hold-denied` / `state:changed` 仍然缺。
>
> **仍然开着的是 B1（高）与 B2（契约越界）** —— 这两条在 `810e035` 上依然成立（§4.9 有逐条核对表）。

---

## 1. 结论

**批次 B 的架构骨架是稳的，契约对齐做得很硬；但有 1 条高优先级功能缺陷必须在合入批次 C 之前修掉，另有 5 处在 S9 / S12 消费契约之前就该定死。**

| 项 | 结论 |
| --- | --- |
| **发现总数** | **16 项**：高 **1** · 中 **5** · 低 **10** —— 其中 **B3、B4 已被上游/同步修正消解**，**14 项在基线 `810e035` 上仍成立** |
| **最要紧的一条** | **B1** —— `hold` 的「每块一次」限制被实现成「**每局一次**」：第一次暂存之后，**整局再也暂存不了**。需求 FR-4.5 / §8.2 / §8.3 三处都写的是「本块」 |
| **跨批次继承** | 批次 A 的 **A2（连击永不中断）被内核继承并放大**（B4）——已由 `810e035` 按报告建议修掉；**A1（`b2b` 永远为假）已由 `7b9e9fc` 在规则层修掉**（B3） |
| **契约面** | `contracts/` **一字未动**，基线仍 **355** 条 ✓；草稿负载字段名与顺序**逐条相等**（运行期硬门）✓；信封五字段导入期自检 ✓ |
| **五道门**（基线 `810e035`，整仓） | pytest **457 passed · 44 skipped** · ruff check 全过 · ruff format **72 files** · pyright **0 errors** · lint-imports **4 kept** ✓ |
| **机检** | 纯净度绿 · 事件登记绿（44 条）✓ · 基线 **355** 条 ✓ |
| **测试面** | 派子 Agent 做了 **8 组变异测试**：**6 组变异存活**（套件照样全绿）→ 「事件存在性」守得牢，「**行为语义**」守不住（B6 / B12–B16） |
| **仍然开着** | **B1（高）· B2（契约越界）** 在 `810e035` 上依然成立；B5 还差 `piece:hold-denied` / `state:changed` 两条；B6 / B7–B16 未动（对照见 §4.9） |

**为什么没有更多「高」**：除 B1 外，其余缺陷都不损坏状态、不影响回放比对、不让已冻结的契约分叉。B2 虽然是契约层面的越界（实现发出了契约没声明的拒绝原因），但发出的值仍在 12 值枚举内、行为确定可复现，所以定中而不定高——**但它必须走完「要么改实现、要么走 major 变更流程」这一步，不能带着走**。

---

## 2. 审查范围与文件处置

### 2.1 OCR 预览

```
ocr delegate preview --format json --from f89ce9a --to feature/batch-b
→ mode=range  merge_base=f89ce9a68b804fcad18a7ffdc7966fe3411053a9
→ total_files=27  reviewable_count=15  excluded_count=12
→ total_insertions=5969  total_deletions=103
```

> 首审时（基线 `aab75cf`）同一命令给的是 19 / 11 / 8。差额来自并入的批次 A 修订 `7b9e9fc`（4 个源文件 + 4 个测试文件 + 批次 A 的审查报告本身）。**本报告以 27 / 15 / 12 这版为准**。

规则解析：`ocr delegate rule --format json <15 个路径>` → **1 个规则组**（`system` 组，`**/*.{py,pyi,ipynb}`：边界与空输入 · 错误处理 · 死代码 · 可变默认值与共享状态 · 性能 · 并发 · 安全）。

**分支点取 `f89ce9a`（批次 A 的落笔提交）而不是 `master`** —— 按 `plan-M0.md` §1.4.5 规则 2「下一批从上一批的落笔提交起，不回 master」，批次 B 的代码基线就是批次 A 的产物。

### 2.2 可审文件（15 / 15 全部审查）

| 文件 | 状态 | 行数 | 读法 |
| --- | --- | --- | --- |
| `src/tetris_kernel/kernel/core.py` | added | 1564 | **全文逐行** |
| `src/tetris_kernel/bus/bus.py` | added | 296 | 全文逐行 |
| `src/tetris_kernel/bus/envelope.py` | added | 245 | 全文逐行 |
| `src/tetris_kernel/bus/subscriber.py` | added | 222 | 全文逐行 |
| `src/tetris_kernel/kernel/quota.py` | added | 230 | 全文逐行 |
| `src/tetris_kernel/kernel/commands.py` | added | 171 | 全文逐行 |
| `src/tetris_kernel/kernel/drafts.py` | added | 171 | 全文逐行 |
| `src/tetris_kernel/kernel/lifecycle.py` | added | 161 | 全文逐行 |
| `src/tetris_kernel/kernel/frame.py` | added | 123 | 全文逐行 |
| `src/tetris_kernel/kernel/__init__.py` | modified | 174 | 全文逐行 |
| `src/tetris_kernel/bus/__init__.py` | modified | 58 | 全文逐行 |
| `src/tetris_kernel/rules/standard.py` | modified | 302 | **复核修订 diff**（批次 A 的 A1 / A2 修在这里，见 B3 / B4） |
| `src/tetris_kernel/kernel/snapshot.py` | modified | 519 | 复核修订 diff（A3：扩展槽规范化 + 尺寸校验） |
| `src/tetris_kernel/kernel/rng.py` | modified | 118 | 复核修订 diff（批次 A 的随机源修订） |
| `src/tetris_kernel/rules/tables.py` | modified | 105 | 复核修订 diff（A4：形状表两层冻结） |

> 后四个文件是**批次 A 的修订被并入本分支**（`7b9e9fc`）带来的，不是批次 B 的交付物。本轮**逐行复核了它们的 diff**，确认对批次 B 的结论只有一处影响：`rules/standard.py` 的 `b2b` / `combo` 口径（见 B3 / B4）。其余三处（扩展槽规范化、棋盘尺寸校验、形状表内层冻结）**不触碰批次 B 的任何结论**。

### 2.3 排除文件（12，全部**手动审查并计入覆盖率**）

| 文件 | 排除原因 | 处置 |
| --- | --- | --- |
| `tests/test_bus.py`（514 行） | `default_path` | **逐行审查 + 变异测试**（子 Agent） |
| `tests/test_command_channel.py`（254 行） | `default_path` | **逐行审查 + 变异测试**（子 Agent）；其中 100–180 行我本人复核 |
| `tests/test_frame_model.py`（283 → 329 行） | `default_path` | **逐行审查 + 变异测试**（子 Agent）；其中 120–190 行我本人复核 |
| `tests/test_quota.py`（223 行） | `default_path` | **逐行审查 + 变异测试**（子 Agent） |
| `tests/test_package_exports.py`（55 行） | `default_path` | **逐行审查**（我本人读了本次改动的 diff） |
| `tests/test_rng.py`（+95 / −6） | `default_path` | **复核 diff**（批次 A 的修订） |
| `tests/test_rules.py`（+103 / −8） | `default_path` | **复核 diff**（批次 A 的修订） |
| `tests/test_snapshot.py`（+77） | `default_path` | **复核 diff**（批次 A 的修订） |
| `tests/ocr/batch-a-review.md`（584 行） | `unsupported_ext` | **读**（批次 A 的报告本体，是本报告 B3 / B4 的论证来源） |
| `PROGRESS.md`（+23 / −9） | `unsupported_ext` | 逐节读，与代码逐条对 |
| `ai_docs/261003.md`（+107 / −2） | `unsupported_ext` | 逐节读 |
| `docs/plan-M0.md`（+67 / −45） | `unsupported_ext` | 逐节读 S9 / S10 / S11 / S12 段，与实现逐条对 |

> **`tests/**` 被 OCR 的 `default_path` 排除是已知行为**（批次 A 轮同样如此）。本批次唯一的机器担保载体就是这五个测试文件，因此**逐行审查 + 变异测试**都做了。

### 2.4 两处执行环境上的处置（重要）

**（1）审查期间工作区被并行会话占用**（分支在 `feature/batch-a` 与 `feature/batch-b` 之间被切换）。为保证审查对象稳定且**不干扰并行会话**，批次 B 的提交树被导出到工作区内的独立快照目录 `.tmp_bb/`（基线 `aab75cf`），所有代码阅读、门禁运行与探针**都在快照里做**（`PYTHONPATH` 指向快照的 `src`，保证 import 的是批次 B 的代码而不是主工作区的批次 A 代码）。**主工作区全程未做任何 git 写操作**。

**（2）基线推进后重新导出 `.tmp_head/`**（基线 `96f5f50`），五道门与探针 H 都在这棵树上复跑（§6）；最终基线 `810e035` 的五道门直接在**整仓**跑（§6 第二张表）。两个快照目录**已在收尾删除**（`rm --` 走回收站）。

---

## 3. 发现清单

| 编号 | 位置 | 类别 | 级别 | 一句话 |
| --- | --- | --- | --- | --- |
| **B1** | `kernel/core.py:989` | bug | **高** | `hold_used` 只在开局时为 `False` —— 「每块一次」被实现成「**每局一次**」，第一次暂存后整局不能再暂存 |
| **B2** | `kernel/core.py:985` · `:1063` | bug | **中** | 实现发出了**契约未声明**的拒绝原因：`hold` → `no_active_piece`、`injectGarbage` → `out_of_bounds` |
| **B3** | `rules/standard.py:309` | bug | **中 → 已消解** | 批次 A 的 **A1（`b2b` 永远为假）**：规则层已由 `7b9e9fc` 改成「难消即在链中」，**不再成立**（保留条目只为记录来源） |
| **B4** | `kernel/core.py:1276-1277` + `rules/standard.py:293-294` | bug | **中 → 已消解** | 批次 A 的 **A2（连击永不中断）被内核继承并放大**：规则层能断连击、内核不走空组路径 → 已由 **`810e035`** 按本报告建议修掉 |
| **B5** | `kernel/core.py:1284` | other | **中（部分已修）** | `combo` 变了却**不发** `score:combo-changed`；`810e035` 已补上两条计分事件，**`piece:hold-denied` / `state:changed` 仍缺** |
| **B6** | `tests/test_frame_model.py:141-187` | test | **中** | 五步帧模型的**实际副作用无人守**：消行 / 塌落 / 计分被整段变异掉，套件照样全绿 |
| **B7** | `contracts/snapshot.py:183` · 需求 `:343` | documentation | 低 | 口径 ②：内核的 `next` 放 ≤7 个，需求 §8.3 与契约 docstring 仍写「5 个」——**措辞没收口** |
| **B8** | `kernel/core.py:625-645` | documentation | 低 | 口径 ①：被拒命令的 `received` 延后发——**实现可接受**，但 plan 的 S9 / S10 两句字面互相矛盾 |
| **B9** | `kernel/core.py:455` | bug | 低 | `kernel:quota-exceeded.actual` 把 `tick` 算进去了，与「tick 不计入命令数」差 1 |
| **B10** | `docs/plan-M0.md`（S9 段） | documentation | 低 | plan 仍写「除 `start/pause/resume/reset` 外一律拒绝」，与契约的 `setLevel` 豁免矛盾（**文档残留**） |
| **B11** | `kernel/core.py:1361-1362` | documentation | 低 | `_spawn_piece` 的 docstring 说「出生是帧推进的一步」，但 `_cmd_start` / `_cmd_hold` 也直接调它 |
| **B12** | `tests/test_bus.py:240-244` | test | 低 | 「总线不提供视图」只是两个属性名的**黑名单** |
| **B13** | `tests/test_command_channel.py:126-148` | test | 低 | 「拒绝不改变任何状态」只在门禁类拒绝上成立；参数类 / 状态类拒绝的状态不变性**零覆盖** |
| **B14** | `tests/test_quota.py` | test | 低 | 合并键的 `phase` / `reason` 两个分量**无人守**；`writeCellsPerFrame` 配额**零用例** |
| **B15** | `tests/test_bus.py:45-56` | test | 低 | 「草稿不含序号」守的是测试自造的 `FakeDraft`，不是内核真实的 `EventDraft` |
| **B16** | `tests/`（全套） | test | 低 | 门禁三值中 `paused` / `game_over` 的**原因取值无人断言**（互换后套件仍全绿） |

---

## 4. 发现明细

### B1（高）`hold` 的「每块一次」被实现成「每局一次」

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/kernel/core.py` |
| start_line / end_line | 981–982（检查）· 989（置位）· 1217–1266（`step_lock` 不复位） |
| category | bug |
| severity | high |

**问题**：`hold_used` 只在 `__init__` / `_cmd_reset` 新建 `KernelState` 时为 `False`；`hold` 成功后置 `True`（`core.py:989`），而**锁定步 `step_lock` 从不复位它**。于是「本块已用过暂存」变成了「**本局已用过暂存**」——第一次暂存之后，这局剩下的所有方块都再也暂存不了。

**需求与契约都是「本块」**（三处独立措辞）：

- `docs/需求与边界.md:155` FR-4.5：「实现暂存（Hold）与「**每块一次**」限制」（M 级）
- `docs/需求与边界.md:304` §8.2 `hold` 的可拒绝情形：「**本块**已使用过暂存」
- `docs/需求与边界.md:342` §8.3 快照字段 `holdUsed`：「**本块**是否已用过暂存」

**实测**（探针 E）：

```
phase: running holdUsed: False
hold#1 kinds: ['command:received', 'command:accepted', 'piece:held', 'piece:spawned'] reason: None
hold#1 snapshot hold/holdUsed: J True
after lock: piecesPlaced= 1 holdUsed= True          ← 新方块已出生，额度没复位
lock event kinds: ['clock:gravity', 'piece:locked', 'board:changed', 'piece:spawned']
hold#2 kinds: ['command:received', 'command:rejected'] reason: hold_already_used   ← 新的一块，却被拒
hold#3 kinds: ['command:received', 'command:rejected'] reason: hold_already_used
```

**后果**：

1. **暂存功能整局只生效一次** —— 核心玩法功能基本报废（M 级需求 FR-4.5）；
2. 快照字段 `holdUsed` 的**语义与契约不符**：它成了「本局是否用过暂存」，而契约写的是「本块」。任何按该字段做 UI 提示的渲染器都会显示错的状态；
3. `piece:hold-denied` 事件因此**只在开局第一次之后才会出现**（见 B5）。

**修法（很小）**：在 `step_lock` 里锁定方块时复位 `self._state.hold_used = False` —— 锁定即「本块结束」，下一块（出生步）重新获得额度。这与 FR-4.5 的「每块一次」和契约的「本块」都一致。

**测试为什么没抓到**：`tests/` 里 `hold` 只出现在两处 —— `test_command_channel.py:80`（造 `game_over` 的前置步骤）与契约转录表；**没有任何用例检查「锁定之后能不能再 hold」**。

---

### B2（中）实现发出了契约未声明的拒绝原因

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/kernel/core.py` |
| start_line / end_line | 985（`hold` → `no_active_piece`）· 1063 / 1065（`injectGarbage` → `out_of_bounds`） |
| category | bug |
| severity | medium |

**问题**：契约的 `CommandSpec.reject_reasons` 明确定义为「**该命令可能被拒绝的全部原因**」（`contracts/commands.py:103-104`），并且有**机器可读的转录表** `EXPECTED_COMMAND_REJECT_REASONS`（`tests/test_contracts_declarations.py:578`）逐条对照。实现里有两条命令发出的原因**不在自己那张表里**：

| 命令 | 契约声明的原因 | 实现实际发出的 | 状态 |
| --- | --- | --- | --- |
| `hold` | `not_started` / `paused` / `game_over` / `quota_exceeded` / `invalid_argument` / `hold_already_used` | 多了 **`no_active_piece`** | **越界** |
| `injectGarbage` | `not_started` / `paused` / `game_over` / `quota_exceeded` / `invalid_argument` | 多了 **`out_of_bounds`** | **越界** |

**实测**（探针 C：16 命令 × 4 phase + 9 条专项触发路径，逐条与契约对照）：

```
hold           declared=['game_over','hold_already_used','invalid_argument','not_started','paused','quota_exceeded']
               emitted =['game_over','hold_already_used','no_active_piece','not_started','paused']
               *** UNDECLARED: ['no_active_piece'] ***
injectGarbage  declared=['game_over','invalid_argument','not_started','paused','quota_exceeded']
               emitted =['game_over','not_started','out_of_bounds','paused']
               *** UNDECLARED: ['out_of_bounds'] ***
violating commands: 2
emitted reasons not in global enum: []      ← 12 值枚举本身没被突破
```

**为什么这条特别值得记**：契约在 `hold` 的 doc 里**专门警告过这个坑**（`contracts/commands.py:326-333`）——

> 需求 §8.2 给 hold 列的可拒绝情形只有这一条，因此这里不额外列 `no_active_piece`……该窗口是否存在要等实现期确认，**届时按 major 变更流程定，不要在实现期临时加一个拒绝原因**。

**批次 B 正是「在实现期临时加了一个拒绝原因」**。而 S3 报告的 **M3** 早就预警过这个失败模式（`tests/ocr/S3-review.md:131`：「要么临时给 `hold` 加拒绝原因——而那时候基线可能已经冻结（S17），就是一次 major 变更」）。**基线确实还没生成，所以现在补还来得及；等 S17 冻结后再补就是一次 major 变更。**

**建议**：

- `injectGarbage` 的行数 / 空洞列越界 → 改用 **`invalid_argument`**（契约的跨切规则写得很清楚：「**参数不合规格统一用 `invalid_argument`**」，越界的行数与列号就是参数不合规格）——**不动契约**；
- `hold` 的「running 态无活动方块」→ 这是一个**真实的口径缺口**（S3-M3 已挂账），要么走 major 变更流程给 `hold` 补声明，要么实现侧改用已声明的原因。**本报告不替项目决定走哪条**，但要求在 S17 之前定下来并写进 `contracts/commands.py` 的 doc + `PROGRESS.md`；
- **补一条守卫**（本条最有价值的部分）：把「实现发出的 `reason` ⊆ `spec.reject_reasons`」做成断言。现在的守卫链是「契约 ↔ 转录表」✓，但**缺了「实现 ↔ 契约」这一环**——这正是「少守一处比多守一处更难发现」的典型形状。

---

### B3（已消解）批次 A 的 A1（`b2b` 永远为假）——由 `7b9e9fc` 在规则层修掉

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/rules/standard.py` |
| start_line / end_line | 309（`b2b=class_key in DIFFICULT_CLASS_KEYS`）· `kernel/core.py:1285`（落笔） |
| category | bug |
| severity | ~~medium~~ → **已在基线 `96f5f50` 消解** |

**首审时的样子**：批次 A 报告的 **A1** 指出标准规则集的 `b2b` 与 `prev.b2b` 相与 → 从 `False` 起步恒为 `False`；批次 B 的 `step_clear` 把规则集返回的 `b2b` 原样落进内核状态 → 快照的 `b2b` 永远是契约默认值。

**现在**：批次 A 的修订 `7b9e9fc`（并入提交 `96f5f50`）把规则层的口径改成了「**本次难消就在链中，不与 `prev.b2b` 相与**」，并把 `b2b` 的语义写进 docstring（「这一位表示『当前处于背面连击链中』，不是『链长 ≥ 2』」）。

**实测（探针 H，基线 `96f5f50` 的树上）**：造一个四消（`classKey="tetris"`，属难消）：

```
S2 tetris clear        score=   800 lines=  4 combo=1 b2b=True
```

**结论：`b2b` 不再是恒假，本条在基线上不再成立** —— 记为「已消解」，不计缺陷。

**但要注意两条尾巴**（转成 B4 / B5 的组成部分）：

1. **落空不断链**：`b2b` 的「断链」条件写在规则层（空组 → `b2b=False`），但**内核在无消行组时直接 return，从不把空组交给规则层**（`core.py:1276-1277`）→ 落空之后 `b2b` 仍是 `True`（实测 S3：`b2b=True`）。这条已并入 **B4**。
2. **`score:b2b-changed` 事件内核不发**（`b2b` 变了却没有事件宣告）→ 见 **B5**。

---

### B4（中）批次 A 的 A2（连击永不中断）被内核继承并放大

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/kernel/core.py` |
| start_line / end_line | 1276–1277（有消行组才继续）· 1280（调 `score_clear`）· 1284（`self._state.combo = delta.state.combo`） |
| category | bug |
| severity | medium |

**问题**：规则层**已经能断连击**了（`rules/standard.py:293-294`：空组 → `combo=0` / `b2b=False`），但**内核只在有消行组时才调 `score_clear`**——`core.py:1276-1277` 在 `if not groups:` 处直接 `return`，**落空那一步根本没进计分路径**。于是内核的 `combo` 只会被消行推进，中间的「没消行的放置」绕过计分 → **连击跨着它们继续累加**。

**实测（探针 H，基线 `96f5f50`）**：四消 → 落空 → 再四消：

```
S2 tetris clear        score=   800 lines=  4 combo=1 b2b=True
S3 miss (no clear)     score=   800 lines=  4 combo=1 b2b=True   ← 没消行，连击没断、链也没断
S6 tetris clear        score=  1650 lines=  8 combo=2 b2b=True   ← 800 + 850（800 基分 + 50×1 陈旧连击）
```

**后果**：玩家放一块没消行的方块，下一块消行仍然拿连击奖励（+50）；`combo` 单调递增到整局结束。FR-4.6（M 级）含连击。`b2b` 同理——链不会因落空而断，B2B 倍率（M1）的「上一手也在链中」判断会被陈旧的链状态污染。

**建议**：内核**每次锁定后都调 `score_clear`（含空组）**，让「空组 = 落空 = 断连击断链」这条口径真正生效；**内核不自己改 `combo` / `b2b`**——计分口径归规则集（这也正是 `plan-M0.md` S13 段第三条约定与 `rules/standard.py` 现在的 docstring 所写的口径）。

> **本条在提交态 `96f5f50` 上成立**，并已由 **`810e035`** 按上面的口径修掉（提交说明：「落空也调 `score_clear`——内核侧对齐计分新口径」）——**与本报告的建议一字不差**：内核每次锁定都调 `score_clear`（含空组），内核不自己改 `combo` / `b2b`，计分口径归规则集。

**修后实测**（同一探针 H，基线 `810e035` 的树上）：

```
S2 tetris clear    score= 800 lines=4 combo=1 b2b=True
S3 miss (no clear) score= 800 lines=4 combo=0 b2b=False   ← 落空断连击断链 ✓
S6 tetris clear    score=1600 lines=8 combo=1 b2b=True     ← 800 + 800，没有陈旧的 50×1 奖励 ✓
```

> 改动幅度：`core.py` +78 / −33（`step_clear` 重排 + 两条事件）· `tests/test_frame_model.py` +46（两条新用例：落空断连击、落空不改状态）。

---

### B5（中）`combo` 变了却不发 `score:combo-changed`

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/kernel/core.py` |
| start_line / end_line | 1284–1285（状态落笔，无事件） |
| category | other |
| severity | medium |

**问题**：机器比对（探针 F）——**44 条已登记事件里，标注「产出者 = kernel」的 6 条，批次 B 的内核一条都不发**：

| 事件 | 产出者 | 基线 `96f5f50` 内核发了吗 | 有无「延后到 M1」的口径 |
| --- | --- | --- | --- |
| `piece:hold-denied` | kernel | ❌ | **无** |
| `piece:lock-delay-start` | kernel | ❌ | ✅ plan S12 明写「无锁定延迟（落即锁）」→ **合法延后** |
| `piece:lock-delay-reset` | kernel | ❌ | ✅ 同上 → **合法延后** |
| `score:combo-changed` | kernel | ❌ | **无** |
| `score:b2b-changed` | kernel | ❌ | **无** |
| `state:changed` | kernel | ❌ | **无** |

**实测**（探针 H，基线 `96f5f50` 的树上，单消与四消各一帧）：

```
S1 single clear  events=['clock:gravity','piece:locked','board:changed','board:lines-cleared','score:changed','board:collapsed','piece:spawned']
S2 tetris clear  events=['clock:gravity','piece:locked','board:changed','board:lines-cleared','score:changed','board:collapsed','piece:spawned']
```

`combo` 从 0 变成了 1、`b2b` 从 `False` 变成了 `True`，**没有任何事件宣告这两件事**。

**后果**：需求 §8.1 明确列了 `score:combo-changed`（产出者 = 内核，消费方 = UI、特效）与 `score:b2b-changed`；契约里两条的负载字段都已冻结（`combo` / `b2b`）。内核不主动发 → UI 拿不到连击状态变化的信号，**只能靠轮询快照**——而架构 §4.3 的原则是「渲染层不自行计算任何业务数值」，靠快照轮询不算违规，但**事件完备性检查会把这两条判成「事件缺失」**（总线侧的 `log:entry` / `metric:sampled` 那套完备性逻辑正是这么用的）。

**建议**：

- `step_clear` 里当 `combo` 真的变了（`delta.state.combo != before_combo`）就发一条 `score:combo-changed`；`b2b` 同理（**B3 已在规则层修掉，`b2b` 现在会变，这条事件才有得发** —— 两件事一起做才对得上）；
- `piece:hold-denied`：暂存被拒时发一条（负载是 `reason: str`）；
- `state:changed`（任一帧末状态变更 · 轻量摘要）：若确定本阶段不做，**把「延后到哪一阶段」写进 plan**，否则它和 `piece:lock-delay-*` 一样会成为「契约声明了、代码里没有、没人知道是延后还是漏掉」的一类。

---

### B6（中）五步帧模型的「实际副作用」无人守

| 字段 | 值 |
| --- | --- |
| path | `tests/test_frame_model.py` |
| start_line / end_line | 141–187 |
| category | test |
| severity | medium |

**问题**：`test_five_steps_have_a_fixed_relative_order` 只断言「五步都在、相对次序对」；`test_board_is_not_cleared_when_piece_locked_is_published` 只断言「`piece:locked` 那一刻那一行**还满着**」。**全套测试里没有任何一条断言**：那一行最后真的被清掉、棋盘真的塌落、分数真的加了。

**证据**（子 Agent 的变异测试，我复核了断言本身）：

| 变异 | 结果 |
| --- | --- |
| `step_collapse` 不塌落任何方块（仍照发 `board:collapsed`） | **441 passed** |
| `step_clear` 不更新 `score` / `lines` / `combo` / `b2b` / `level` | **441 passed** |

**我自己的复核**：`tests/` 全套里 `.score` / `.lines` 的断言**只出现在「拒绝不改变任何状态」那条用例里**（`test_command_channel.py:142-143`，比较 before == after），**没有一条断言消行后分数增加**。grep 结果：`\b\.score\b` 在批次 B 的五个测试文件里仅此两处。

**为什么重要**：帧模型是批次 B 的核心交付（ADR-023：「整批返回会让所有事件共享执行后的最终态」）。现在**事件次序守住了，事件背后的状态变化没守住** —— 塌落坏了、清行坏了、计分坏了，套件都是绿的。

**建议**：补两条用例——(1) 摆一个必消行的盘 → 推进一帧 → 断言那一行被清空、棋盘塌落；(2) 断言 `score` / `lines` 按规则集算出的值变化（**这两条同时也是 B3 / B4 的回归守卫**）。

---

### B7（低）口径 ②：`next` 放 ≤7 个，需求与契约的措辞没收口

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/snapshot.py`（183 行）· `docs/需求与边界.md`（343 行） |
| category | documentation |
| severity | low |

**问题**：内核的 `next` 放的是「当前这一袋剩下的全部方块」（≤7），而**需求 §8.3 与契约 docstring 都还写着「预览队列（5 个）」**：

- `docs/需求与边界.md:343`：`| next | tuple[str, ...] | 预览队列（5 个） |`
- `src/tetris_kernel/contracts/snapshot.py:183`：`"next": _field("next", NEXT_QUEUE, "预览队列（5 个）")`

**实测**（探针 P3）：`snapshot().next` 长度 = **6**（`('Z','S','I','O','J','L')`），`kernel.PREVIEW_SIZE = 5`。

**审查意见：写手的技术理由成立，但收口没做完。** 理由是 `FR-10.2`（存档恢复必须与未中断时一致）：少放两个的话「这袋还剩哪几个」只活在内核私有字段里，**恢复后的取片序列会分叉**——这个推理是对的，类型也没变（`tuple[str, ...]`），**基线不受影响**。

但**实现已经偏离了两处权威措辞，而偏离被留在了代码注释里、没同步回文档**。契约 docstring 属 `contracts/`（冻结区），改它要走流程；需求 §8.3 属需求层。**两条路都必须在 S17 生成基线前走完**，否则冻结下来的文档会与实现长期互相矛盾。

**建议**：把「本字段 = 当前一袋剩余的全部方块（≤7）；渲染取前 5 个当预览」写进**需求 §8.3 与契约 docstring 两处**，并在 `PROGRESS.md` 记一笔「措辞已对齐，实现不变」。`PREVIEW_SIZE` 这个常量目前内核自己不用（留给渲染层），建议同时在它的 docstring 里写明「**渲染层取前 5 个**」——否则「5」这件事散在三处。

---

### B8（低）口径 ①：被拒命令的 `received` 延后发——实现可接受，plan 的两句要改

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/kernel/core.py` |
| start_line / end_line | 625–645（`_reject`）· 647–682（`_group_drafts`） |
| category | documentation |
| severity | low |

**问题**：plan 的两段任务栏**字面互相矛盾**——S9 写「`command:received`（**出队执行前**发）」，S10 写「`received` 与 `rejected` **成对合并、计数一致**」。写手选了 S10，并在 `plan` 与 `ai_docs` 里都标了「与 S9 任务栏的字面口径有出入，**等外部审查拍板**」。

**审查意见：接受当前实现，但必须把 plan 的矛盾改掉。** 实测（探针 D）证明延后**没有破坏任何可观察的不变量**：

| 检查项 | 实测 | 判定 |
| --- | --- | --- |
| 帧号是否正确 | 被拒 `tick` 的 `received` 携带 `frame=1`（出队时已锁） | ✅ 正确 |
| `received` → `rejected` 是否成对相邻 | Q2：3 条同因拒绝 → **一对**事件，`mergedCount=3` 两边一致 | ✅ 正确 |
| 时序是否倒挂 | Q1：`[received(拒), rejected(拒), received(接), accepted(接), game:resumed]` | ✅ 顺序正确 |
| 计数是否一致 | `received` 的计数放在**元数据**的附加键 `mergedCount`（契约负载里没有这个字段） | ✅ 符合 NFR-9「未知键静默保留」 |

**建议**：把 S9 任务栏改成「`command:received` 的**帧号在出队时锁定**；**被拒命令**的 `received` 与 `rejected` 成对延后到合并窗口关闭时发（见 S10）」，S10 保持不变。**改的是文档，不是代码。**

**附带一条残余风险**（不单列为发现）：合并窗口关闭依赖宿主 —— 走 `run_rounds` 时内核每轮结束会冲刷，但**宿主自己管队列时必须在一轮结束时调 `flush_rejects()`**，否则拒绝事件一直攒着不发（事件不是不发，是晚点发）。这条已写进 plan 的实现补记 ✓，但**没有任何测试守它**。

---

### B9（低）`kernel:quota-exceeded.actual` 把 `tick` 算进去了

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/kernel/core.py` |
| start_line / end_line | 455 |
| category | bug |
| severity | low |

**问题**：`actual=self._quota.commands + len(batch) + len(dropped)`，而 `len(batch)` **含 tick**。配额自己的口径是「`tick` 自身不计入命令数」（`quota.py:12`、契约 `commands.py:344`）。

**实测**（探针 Q7 / P4）：限额 3，该帧真实命令数 4 → 事件报 `actual=5`（多 1）；另一例真实 6 → 报 `actual=7`（多 1）。**恒定差 1**。

**后果**：事件负载里的「实际值」比配额自己的计数多 1。消费方（指标 / 日志）按它算「超了多少」会偏。

**建议**：`actual` 改成 `self._quota.commands + len(batch) - (1 if tick 计入 batch else 0) + len(dropped)`，或直接用「本帧非 tick 命令数」。

---

### B10（低）plan 的 S9 任务栏与契约的 `setLevel` 豁免矛盾（文档残留）

| 字段 | 值 |
| --- | --- |
| path | `docs/plan-M0.md`（S9 段「统一 phase guard 前置」一行） |
| category | documentation |
| severity | low |

**问题**：plan 仍写「除 `start/pause/resume/reset` 外，`phase != running` 一律拒绝」，而**契约明写 `setLevel` 不受生命周期门禁约束**（`contracts/commands.py:354-357`：理由是「它不改棋盘内容也不改活动方块位置」）。

**行为是对的**（内核 `_guard_reason` 读契约的 `lifecycle_gated`，`setLevel` 的标志是 `False` → 不受门禁 ✓），**plan 的措辞是旧的**。

这与 S2–S6 汇总报告的 N1 / N2 / N3 是**同一族**：改了 A、没回头看还有谁引用 A。建议顺手改掉。

---

### B11（低）`_spawn_piece` 的 docstring 与实际调用点不符

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/kernel/core.py` |
| start_line / end_line | 1361–1362（docstring）· 747（`_cmd_start`）· 994（`_cmd_hold`） |
| category | documentation |
| severity | low |

**问题**：docstring 写「出生一个方块（**出生是帧推进的一步，S12 调它**）」，但 `_cmd_start`（开局）与 `_cmd_hold`（暂存后取新方块）**也直接调它**——于是 `piece:spawned` / `queue:refilled` / `rng:next` 会出现在**命令事件流**里，而不只在帧推进的出生步里。

**两次调用都是必要的**（开局必须有方块、暂存必须立刻给出新方块），所以这是**措辞不准**而不是行为错误。建议把 docstring 改成「出生是帧推进的一步；**开局与暂存两条命令路径也会直接调用**（这两处必须立刻给出一块方块）」，并把这条写进 plan 的 S12 段——否则下游 S14 装配层会以为「出生事件只在帧里出现」。

---

### B12–B16（低）测试守卫自身的缺口

这一组来自子 Agent 的**变异测试**（8 组变异，6 组存活）；我逐条复核了断言本身与 grep 证据，确认成立：

| 编号 | 位置 | 问题 | 证据 |
| --- | --- | --- | --- |
| **B12** | `tests/test_bus.py:240-244` | 「总线不提供视图」（ADR-021）只查 `hasattr(bus,"view")` / `hasattr(bus,"get_view")` 两个**属性名**——任何叫别的名字的视图入口都能过 | docstring 声称的机制（单参数回调拿不到上下文）没被断言 |
| **B13** | `tests/test_command_channel.py:126-148` | 「拒绝不改变任何状态」用的 `VALID_ARGS` 全是合法参数 → `validate_args` 永远走不到；矩阵里能被拒的组合**全是门禁 / 非法迁移** | 插桩统计：整套 tests/ 里 `invalid_argument` / `out_of_bounds` / `no_active_piece` / `hold_already_used` / `not_authorized` / `write_condition_failed` **一次都没走到** |
| **B14** | `tests/test_quota.py` | 合并键的 `phase` 与 `reason` 两个分量**无人守**（把键改成 `(ANY, commandType)` 仍 441 passed）；`writeCellsPerFrame` 这条配额**零用例** | 变异存活 |
| **B15** | `tests/test_bus.py:45-56` | 「草稿不含序号」守的是测试自己造的 `FakeDraft`（字段集还是写死的常量），不是内核真实的 `EventDraft` | 把 `kernel.drafts.EventDraft` 换成多一个 `seq` 字段的 dataclass → test_bus 仍 25 passed |
| **B16** | `tests/`（全套） | 门禁三值中 `paused` / `game_over` 的**原因取值**无人断言 | grep：批次 B 五个测试文件里对 reason **取值**的断言只有 4 处（`illegal_transition` / `not_started` / `quota_exceeded`）；把 `paused` ↔ `game_over` **互换** → 441 passed |

**这组问题的共同形状**：**事件「在不在」守得住，事件「对不对」守不住。** 批次 B 的事件完备性（44 条登记、两处导入期自检、负载字段逐条相等）做得很硬，但**行为语义那一层缺断言**——B6 / B13 / B14 / B16 都是它的具体形态。

---

### 4.9 同步修正对照（`810e035`，报告交付时已入库）

**这一节不是发现，是交付时的状态核对**：报告写完、分支上出现了一笔**按本报告口径做的修正**（并行会话在我写报告期间提交）。**提交态 `96f5f50` 上的结论见前文；下面只回答「哪些已经不用再修了」。**

**改动范围**：`src/tetris_kernel/kernel/core.py`（+78 / −33）· `tests/test_frame_model.py`（+46）——**只动了 `step_clear` 与两条新用例**，没有碰命令通道、总线、帧模型的其余部分。提交说明：「修复：落空也调 `score_clear`——内核侧对齐计分新口径」。

**逐条核对（探针 H 在该提交的树上复跑）**：

| 编号 | `96f5f50`（修正前） | `810e035`（修正后） | 判定 |
| --- | --- | --- | --- |
| **B1**（hold 每局一次） | 仍成立（`hold_used` 只在 `core.py:989` 置位，`step_lock` 不复位） | **仍成立**（`hold_used` 的置位 / 检查点行号未变） | ❌ **仍需修** |
| **B2**（未声明的拒绝原因） | 仍成立（`core.py:985` 发 `no_active_piece`、`:1063` / `:1065` 发 `out_of_bounds`） | **仍成立**（两处常量与用法都在） | ❌ **仍需修** |
| **B3**（`b2b` 恒假） | **已消解**（`7b9e9fc` 规则层修掉） | 已消解 | ✅ 不用修 |
| **B4**（连击永不中断） | 仍成立 | **已消解**：内核改为每次锁定都调 `score_clear`（含空组） | ✅ 不用修 |
| **B5**（计分事件不发） | 仍成立（6 条 kernel 产出者事件全不发） | **覆盖 2 / 4**：`score:combo-changed`、`score:b2b-changed` 已发；**`piece:hold-denied` 与 `state:changed` 仍未发** | ⚠️ **还差一半** |
| **B6**（帧模型副作用无人守） | 成立（`step_collapse` / `step_clear` 变异均存活） | **部分覆盖**：新用例钉住了 `combo` 的变化与事件，**`step_collapse` 不塌落仍然存活** | ⚠️ **还差一半** |
| **B7 / B8 / B10 / B11**（文档措辞） | 成立 | 未触及 | ❌ 仍需修（**S17 生成基线前**） |
| **B9**（`actual` 多算 tick） | 成立 | 未触及 | ❌ 仍需修 |
| **B12–B16**（测试守卫缺口） | 成立 | 只加了 2 条新用例 | ❌ 仍需修 |

**修正后实测**（同一探针，同一造盘）：

```
S2 tetris clear  score=800 lines=4 combo=1 b2b=True   events=[... 'score:changed','score:combo-changed','score:b2b-changed', ...]
S3 miss (no clear) score=800 lines=4 combo=0 b2b=False  events=[... 'score:combo-changed','score:b2b-changed', ...]
S6 tetris clear  score=1600 lines=8 combo=1 b2b=True  ← 800 + 800，不再有 50×1 的陈旧连击奖励
```

**结论**：**B4 与 B5 的两条计分事件已由 `810e035` 消解；B1 与 B2 两条最要紧的（高危 + 契约越界）依然开着。** 本报告其余条目均未受影响。

> 顺带记一条**修正里的口径**（值得作者确认）：`810e035` 的做法是「**空组 = 落空 = 断连击断链**，由规则集复位、内核不自己改 `combo` / `b2b`」——这与 `plan-M0.md` S13 段第三条约定、`rules/standard.py` 的 docstring 一致，**本报告 B4 的建议也是同一口径**，没有分歧。

---

## 5. 复核过、确认没问题的部分

### 5.1 契约对齐（本批次做得最硬的一块）

| 检查项 | 结论 |
| --- | --- |
| 草稿负载字段名与顺序 | ✓ `make_draft` **运行期逐条相等**校验（事件类型必须已登记 + 负载字段名/顺序与契约逐条相等）——这是一道很硬的门，字段名写错当场 `ValueError` |
| 事件类型必须已登记 | ✓ `make_draft` 查 `EVENT_TYPES`，未登记当场报错 |
| 信封五字段 | ✓ `bus/envelope.py` **导入期**逐字段自检（字段名 / 顺序 / `seq`·`frame`·`type` 的类型串） |
| 元数据三键 | ✓ 导入期自检 `METADATA_KEYS` 的键序 = (`source`,`producer`,`cause`) |
| 取值域对齐 | ✓ `drafts.py` 与 `commands.py` 各有一处**导入期逐字对齐**（`JSON_VALUE` / `ARGS_VALUE`），契约改了取值域而内核没跟上当场 `TypeError` |
| 门禁归属 | ✓ 从契约的 `CommandSpec.lifecycle_gated` **推导**，不手写名单 |
| 参数校验 | ✓ 照抄契约声明（参数名 / 顺序 / 类型），多余与类型不符一律 `invalid_argument` |
| `contracts/` 一字未动 | ✓ 批次 B 的 diff 里没有任何 `contracts/` 文件；基线仍 **355** 条（探针实测 `collect_entries()` = 355） |

### 5.2 帧模型与命令通道

| 检查项 | 结论 |
| --- | --- |
| 帧号推进与推进许可**分离** | ✓ 探针 Q4：无 tick → `advance_frame()` 产出 **0** 步；tick 被接受后 → **5** 步 |
| 推进前复查 phase | ✓ 探针 Q5：tick 与 pause 在同一轮内 → 事件序 `[received(tick), accepted(tick), clock:tick, received(pause), accepted(pause), game:paused]`，随后 `advance_frame()` → **0 步**（架构 §6.2 要求的不推进，成立） |
| `reset` 归零 | ✓ 探针 Q6：帧号 1→0、版本号 1→0、phase → `not_started`、`next` 清空、分数清零 |
| 拒绝不改任何状态 | ✓ 门禁类拒绝实测：版本号 / 棋盘指纹 / 分数 / 消行数 / 活动方块全部不变 |
| 帧内序号 | ✓ 只对被接受的命令编号，每帧归零 |
| tick 拒绝时帧号照走 | ✓ 探针 Q3：被拒 tick 的 `received` 携带 `frame=1`（出队时已 +1），与 ADR-022 一致 |
| 五步逐步产出 | ✓ `frame.advance_frame` 是生成器，逐步 yield；`FrameContext` 只在步骤间传递、不进内核状态 |
| 每步先算完再一次性写入 | ✓ 锁定步与塌落步都是先算 `changes` 再写 |

### 5.3 总线

| 检查项 | 结论 |
| --- | --- |
| 序号唯一权威 | ✓ 草稿不含序号；总线封装信封时分配；**未登记的事件类型不占号**（`producer_of` 返回 `None` → 丢弃并记 trace） |
| 序号顺序 == 分发顺序 | ✓ 待发布队列里的事件**在入队时就分配了序号**（`_envelope_of` 先调 `_next_seq`） |
| 异常隔离 | ✓ 订阅者抛错 → 转 `kernel:error`（负载四字段：`subscriber` / `eventType` / `eventSeq` / `error`），**其余订阅者不受影响**，内核不中断 |
| 错误事件不再产生错误事件 | ✓ `is_error_event` 分支只记 trace，不入队 → 必然抛错的订阅者不会造成无限循环 |
| 三种订阅模式 | ✓ 精确 / 命名空间通配（`piece:*`）· 全局（`*`） |
| 两个入口共享一张注册表 | ✓ `subscribe` 与 `subscribe_system` 写进同一个 `SubscriberRegistry`，**合并之后才排序**（`(-priority, order)`） |
| 视图只走上下文 | ✓ `SystemContext.view()` 惰性取 + **按发布点缓存**（同一次发布所有订阅者拿到同一份对象） |
| 不提供 `bus.view()` | ✓ 总线本体没有这个方法（守卫偏弱，见 B12） |
| 架构红线（内核↛总线 / 总线↛内核） | ✓ **实测能触发**：伪造 `from ..bus import EventBus` / `from ..kernel import Kernel` 两条违规，两条守卫都能抓到（相对导入解析经我独立复核，见 §8） |

### 5.4 流程与文档

| 检查项 | 结论 |
| --- | --- |
| **导出一致性守卫真的咬住了东西** | ✓ 本轮收口时它把 `kernel/` 新增的六个模块报了出来（包级导出面没跟上），并顺手把**手抄的子模块名单改成 `pkgutil` 推导**——「抄一份就是规则说有约束、机检看不见的温床」 |
| 44 个 skip 的成因 | ✓ **按设计**：两个用例只在「命令被拒」时成立，命令被接受就 skip。skip 的 44 条 = 22 个（命令 × phase）组合 × 2 个用例；**验证 ① 的 16 × 4 = 64 个组合一个都没跳**（断言「恰好一条 accepted 或 rejected」+「必须有 received」） |
| 门禁归属从契约推导 | ✓ `CommandSpec.lifecycle_gated` 必填无默认值，漏表态直接 `TypeError` |
| 五道门 | ✓ pytest **457 passed / 44 skipped**（基线 `96f5f50` 快照树 455 / 整仓 457）· ruff check **All checks passed** · ruff format **72 files**（整仓；快照树 60）· pyright **0 errors** · lint-imports **4 kept / 0 broken** |
| 机检 | ✓ 纯净度绿（冻结区 2 目录 / 违规 0）· 事件登记绿（44 条）· 基线 **355** 条 |

---

## 6. 质量门实跑

在**基线 `96f5f50` 的快照树**（`.tmp_head/`，只含 `src` / `tests` / `tools` / `pyproject.toml`）上实跑（`py312` 3.12.13）：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **455 passed, 44 skipped**（3.11s） |
| `python -m ruff check .` | **All checks passed!** |
| `python -m ruff format --check .` | **60 files already formatted** |
| `python -m pyright`（不带路径，走 `include = ["src","tools"]`） | **0 errors, 0 warnings, 0 informations** |
| `lint-imports` | **Contracts: 4 kept, 0 broken**（exit 0） |
| `python tools/check_kernel_purity.py` | 冻结区 2 目录 · **违规 0 · 警告 0**（exit 0） |
| `python tools/check_event_registry.py` | **违规 0**（exit 0） |
| `contract_baseline.collect_entries()` | **355** 条（与批次 A 修订后逐条相同） |

> 首审时（基线 `aab75cf`）同一组门禁是 **441 passed / 44 skipped / 70 files**。差额全部来自并入的批次 A 修订（`7b9e9fc` 新增的用例）。**60 与 70 的差是目录范围**：`.tmp_head/` 只含 `src`/`tests`/`tools`，整仓还含 `docs/`、`ai_docs/` 的 `.md`（`ruff format` 也统计 `.md` 里的代码块）。本报告入库后整仓会再 +1。

**基线 `810e035`（整仓，含同步修正 `810e035` 与本报告文件本身）**：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **457 passed, 44 skipped**（3.33s） |
| `python -m ruff check .` | **All checks passed!** |
| `python -m ruff format --check .` | **72 files already formatted** |
| `python -m pyright` | **0 errors, 0 warnings, 0 informations** |
| `lint-imports` | **Contracts: 4 kept, 0 broken** |
| `python tools/check_kernel_purity.py` | **违规 0 · 警告 0** |
| `python tools/check_event_registry.py` | **违规 0** |

> 72 files 这一数**含本报告自身**（`ruff format` 会统计 `.md` 里的 Python 代码块——本报告的代码块一律用 ` ```text ` 围栏，避免被重排）。

---

## 7. 覆盖率

| 项 | 数 |
| --- | --- |
| OCR 预览文件总数 | **27** |
| 可审文件（`reviewable_files`） | **15** |
| 已审查 | **15** |
| 跳过 | **0** |
| 额外审查（OCR `default_path` / `unsupported_ext` 排除） | **12**（8 个测试文件 + 4 份 `.md`） |
| **覆盖率** | **27/27 = 100%** |

**排除文件处置**：5 个批次 B 的测试文件**逐行审查 + 8 组变异测试**（这是本批次唯一的机器担保载体）；3 个批次 A 修订带来的测试文件（`test_rng.py` / `test_rules.py` / `test_snapshot.py`）**复核了 diff**；4 份 `.md` 作为**审查基准**读取，每条结论都指向具体章节行号。

---

## 8. 假阳性自查（本轮否掉的一条）

子 Agent 报了一条**高危**：「`tests/test_bus.py:73-122` 的架构红线守卫因相对导入解析差一级而形同虚设——`from ..bus import EventBus` 被解析成 `tetris_kernel.kernel.bus`，两个 `assert not module.startswith(...)` 恒成立」。

**我独立复核后否掉了这条**（探针 G，把 `test_bus.py` 的 `_imported_module` 原样抄出来，对**真实源码树**逐条解析）：

```
kernel/ 写 'from ..bus import EventBus'  -> tetris_kernel.bus      startswith('tetris_kernel.bus') = True
bus/    写 'from ..kernel import Kernel' -> tetris_kernel.kernel   startswith('tetris_kernel.kernel') = True
```

解析逻辑 `parts[: len(parts) - (node.level - 1)]` 对 `level=1`（同包内）与 `level=2`（上一级）**都是对的**：真实源码树里 60 余条相对导入逐条解析全部正确。子 Agent 的探针**把 `package` 参数传成了模块路径而不是包路径**（`tetris_kernel.kernel.core`），于是多砍/少砍一级——**探针用法错了，不是守卫坏了**。

**记录这一条，是因为它正是「误报比漏报更伤审查信誉」的那一类**：一条「高危」如果原样进报告，作者会去改一段**根本没坏**的代码。

---

## 9. 残余风险（非缺陷，供作者决定）

1. **合并窗口依赖宿主冲刷**。走 `run_rounds` 时内核每轮结束会 `flush_rejects()`；**宿主自己管队列时必须在一轮结束时调 `flush_rejects()`**，否则拒绝事件一直攒着（不是不发，是晚点发）。已写进 plan ✓，但**无测试守**。
2. **44 个 skip 的实际覆盖面**。被跳过的 22 个（命令 × phase）组合里，「该命令被接受」这件事**只由验证 ① 的「恰好一条 accepted」守住**，其**语义事件**（发了什么、负载对不对）无人断言。
3. **`tests/` 仍不在 pyright 覆盖内**（`include = ["src", "tools"]`）。批次 B 又新增了 **1329 行**测试代码没有类型检查。
4. **M0 的已知边界**（plan 已写明，**别当成漏做**）：无锁定延迟（落即锁，`lockTimer` 恒 0）· 出生列固定 · `writeCells` 默认放行（`not_authorized` 在 M0 不会触发，`write_condition_failed` 会）· 标准踢墙表仍是恒等偏移 · 形状表只填 `down` 轴。

---

## 10. 未验证 / 不作评价的部分

- **基线文件本身尚未生成**（按计划 S17 生成）→ 本报告所有「进基线 / 不进基线」的判断都是**口径推导**，没有真实基线文件的哈希可比。本轮只实测了**条目数仍为 355**。
- **子 Agent 的 8 组变异测试**：我复核了其中**每一条的断言本身与 grep 证据**（B6 / B13 / B14 / B15 / B16），并**独立否掉了它的一条「高危」**（§8）。未逐条重跑它的变异脚本。
- **`tests/test_contracts_declarations.py`（1590 行，批次 A 就有）本轮未逐行审** —— 只做了与批次 B 的交叉引用（`EXPECTED_COMMAND_REJECT_REASONS` 等表），它不在批次 B 的审查范围内。
- **批次 A 的 15 项发现已由 `7b9e9fc` 修正并入本分支**：其中 **A1（`b2b` 恒假）在基线上已消解**（B3）；**A2（连击永不中断）由 `810e035` 在内核侧补上最后一步**（B4 —— 规则层已改、内核改为每次锁定都调 `score_clear` 含空组）。
- **同步修正 `810e035`**（`core.py` +78 / −33、`test_frame_model.py` +46）**只逐条核对了它对本报告 16 条的影响**（§4.9），未把该修正本身当作审查对象逐行审。
- **批次 C（S14 · S15）不在本轮范围内**。
- **未复跑各阶段提交说明里的反向验证原文**——本轮做的是独立复跑 + 变异测试。

---

## 11. 给作者的修正建议（按优先级）

**第一批 · 合入批次 C 之前**

1. **B1** —— `step_lock` 里复位 `hold_used = False`（「每块一次」）。**这条修完要补一条用例**（锁定后能再 hold）。**基线 `810e035` 上仍开着——本报告最要紧的一条。**
2. **B2** —— `injectGarbage` 的越界改用 `invalid_argument`（不动契约）；`hold` 的 `no_active_piece` 走 major 变更流程补声明（或换用已声明的原因）；**并补一条「实现发出的 reason ⊆ `spec.reject_reasons`」的断言**。**基线 `810e035` 上仍开着。**
3. **B5** —— 补 `score:combo-changed` / `score:b2b-changed` / `piece:hold-denied`；`state:changed` 要么做要么写明延后到哪一阶段。（**前两条已由 `810e035` 补上，还差 `piece:hold-denied` 与 `state:changed` 两条。**）
4. **B4** —— 内核改为每次锁定都调 `score_clear`（含空组），让「落空断连击」真正生效。（**已由 `810e035` 按此建议修掉。**）

**第二批 · 成本极低，建议同批修掉**

5. **B6 + B14** —— 补「消行后那一行被清空 / 棋盘塌落 / 分数增加」三条断言（同时也是 B3 / B4 的回归守卫）；补 `writeCellsPerFrame` 的用例。（**`810e035` 只补了 combo 与事件两条断言，塌落仍未守。**）
6. **B9** —— `actual` 别把 tick 算进去。
7. **B7 + B8 + B10 + B11** —— 四处**文档措辞收口**（`next` 的「5 个」、`received` 延后发的表述、`setLevel` 的门禁豁免、`_spawn_piece` 的出生时机）。**这四条都必须在 S17 生成基线前做完**——基线冻结后再改文档，就是一次 major 变更。

**第三批 · 挂账或补强**

8. **B12 / B13 / B15 / B16** —— 测试守卫补强（总线不提供视图的机制、参数类与状态类拒绝的状态不变性、用内核真实 `EventDraft` 而不是 `FakeDraft`、门禁三值的原因取值）。
9. **B14（合并键）** —— 补一条能把「合并键的 phase / reason 分量」钉住的用例。

> 按 `plan-M0.md` §1.4 纪律 7：报告交回后**逐条核实**，成立项在 `feature/batch-b` 上修正，**修正提交单独一笔**（`修复：按 batch-b 审查报告修订…`），不与新功能混提。本报告 §3 的 16 条**建议逐条裁定**，不必照单全收——**每条都附了可复现的实测或可核对的行号**，不接受时也请写明理由。
>
> **注意**：报告交付时分支上已有一笔按本报告口径的修正（`810e035`）。**请逐条核对工作区状态后再提交**——避免把这笔修正与新的修正混进同一笔提交（纪律：一次提交 = 一个逻辑改动）。

---

## 附：本次审查的自我约束

- **只报有把握的真缺陷**：每一条都附了**可复现的探针输出**或**具体行号 + 机制说明**；风格类不阻塞。
- **不硬凑**：16 条里没有一条是「其实这样也行」的；每条都写清了「在什么条件下才会咬人」。
- **区分「缺陷」与「口径缺口」**：B7 / B8 明确标出「需要项目拍板」，没有替项目发明语义。
- **子 Agent 的结论一律复核**：8 组变异测试逐条核对了断言本身与 grep 证据；**一条「高危」被独立否掉并写进 §8**（探针用法错了，不是守卫坏了）。
- **假阳性自查**：`host/clock.py` 的墙钟读（批次 A 轮已澄清过一次）、`piece:lock-delay-*` 未产出（**有 plan 口径，属合法延后**）、`piece:hold-denied`（口径缺口，非死代码）——都先查了口径再决定报不报。
- **计数与行号全部复跑过**：pytest 条数、skip 条数与理由、基线条目数、五道门退出码、探针输出均来自本次实跑，不是转述提交说明。
- **没有动被审代码**：全程只读；所有反向验证都在快照目录里做，主工作区未做任何 git 写操作。
