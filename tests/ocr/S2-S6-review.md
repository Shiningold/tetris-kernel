# S2–S6 汇总代码审查报告 · 契约声明面与基线工具链

> 审查范围：`05bd643..f059c67`（S2 事件契约 → S6 基线脚本，含全部修订提交）
> 审查方式：`open-code-review-delegate`（OCR 只做文件选择与规则解析，判断由宿主 Agent 完成）
> 审查基线：`f059c67`（HEAD）
> 审查日期：2026-10-23

---

## 1. 结论摘要

**S2–S6 五个阶段的代码整体是可靠的：契约声明面与需求文档逐条相等，基线工具链的每个守卫都实测能触发，此前五轮审查提出的 45 项发现已有 42 项落实。本轮未发现高危（critical/high）问题。**

| 维度 | 结论 |
| --- | --- |
| **契约声明 vs 需求文档** | 事件 44 条（含产出者）、命令 16 条（含参数）、拒绝原因 12 值 + 5 分类、快照 26 字段（含 `board` 5 子字段）——**逐条相等**，唯一差异是需求原文的 `JsonScalar` 记号（已在 S4 记录并单独钉住） |
| **此前 45 项发现** | **已落实 42 · 半落实 1（S3-L3）· 未落实 2（S2-L5 按计划挂账 · S4-L5 作者主动推迟）** |
| **S6 十项发现** | **10/10 已由 `e69a561` 修掉**，本轮逐条回归验证通过 |
| **本轮新发现** | **6 项低优先级**（4 处跨阶段文档残留 + 1 处共用常量缺失 + 1 处测试断言缺口），无中危 |
| **质量门** | pytest 156 passed · ruff check 全过 · ruff format 45 files · pyright 0 错 · lint-imports 4 kept |

**核心判断：契约面可以进入 S7。** 唯一需要在 S17 之前处理的是 S4-L5（取值域统一收口），它已经挂在 `PROGRESS.md` 的待办里，且与 S3-L3 是同一件事——**建议合并成一次收口**（见 §7.2）。

---

## 2. 审查范围与方法

### 2.1 OCR delegate 选出的范围

```
ocr delegate preview --format json --from 05bd643 --to f059c67
→ mode=range · merge_base=05bd643 · total_files=28 · reviewable_count=16 · excluded_count=12
   total_insertions=7240 · total_deletions=72
```

| # | 路径 | 状态 | +/− | 规则组 |
| --- | --- | --- | --- | --- |
| 1 | `src/tetris_kernel/contracts/__init__.py` | modified | +125 | `system` |
| 2 | `src/tetris_kernel/contracts/commands.py` | added | +438 | `system` |
| 3 | `src/tetris_kernel/contracts/events.py` | added | +672 | `system` |
| 4 | `src/tetris_kernel/contracts/placement.py` | added | +43 | `system` |
| 5 | `src/tetris_kernel/contracts/rules.py` | added | +437 | `system` |
| 6 | `src/tetris_kernel/contracts/snapshot.py` | added | +294 | `system` |
| 7 | `src/tetris_kernel/contracts/structures.py` | added | +93 | `system` |
| 8 | `src/tetris_kernel/contracts/types.py` | added | +142 | `system` |
| 9 | `tests/baseline/.gitkeep` | added | 0 | `default` |
| 10 | `tools/_toolkit.py` | added | +105 | `system` |
| 11 | `tools/check_event_registry.py` | added | +185 | `system` |
| 12 | `tools/check_frozen_baseline.py` | added | +167 | `system` |
| 13 | `tools/check_kernel_purity.py` | added | +427 | `system` |
| 14 | `tools/contract_baseline.py` | added | +429 | `system` |
| 15 | `tools/contract_grammar.py` | added | +293 | `system` |
| 16 | `tools/measure_frame_cost.py` | added | +247 | `system` |

**被排除的 12 个文件**：11 个 `.md`（`unsupported_ext`：需求 / 架构 / spec / plan / 契约清单 / PROGRESS / ai_docs + 五份旧审查报告）· 1 个 `tests/test_contracts_declarations.py`（`default_path`）。

**排除文件的处置**：旧报告 `.md` 逐份读过（作为复核依据）；**`tests/test_contracts_declarations.py`（1798 行）虽被 `default_path` 排除，仍逐行审查并计入覆盖率**——机检担保几乎全在它身上，排除它等于没审。

### 2.2 上下文基准

`docs/需求与边界.md` §8.1 / §8.2 / §8.3 · `docs/项目架构.md` §4.1 / §4.4 / §5.3 / §7.3.1 / §7.6 / §8 · `docs/spec-M0.md` §6.5 / §7.1 / §7.5 · `docs/契约v1清单.md` · `docs/plan-M0.md` S2–S6 · `PROGRESS.md` · `ai_docs/261003.md`。

### 2.3 方法

- diff 走 `git --no-pager show` 直读，**未导出临时 diff 文件**。
- 35 项旧发现（S2–S5）→ 派两个独立复核 Agent（S2+S3、S4+S5）逐条裁定并附 `文件:行号` 证据；**S6 的 10 项由本 Agent 另做一轮独立回归验证**（不采信提交说明自陈）。
- 新增**需求文档 ↔ 机器声明**的机器比对（§6）——此前五轮审查都只做到「代码 ↔ 转录表」，**没有断言过「转录表 ↔ 需求文档」**，这是本轮补上的一层。
- 探针临时目录 `.tmp_ocr_s26/` 已 `rm --` 走回收站删除；工作区当前干净。

---

## 3. 此前 45 项发现的落实复核

### 3.1 总览

| 来源 | 条数 | 已落实 | 半落实 | 未落实 |
| --- | --- | --- | --- | --- |
| S2 报告（事件契约声明） | 12 | 11 | 0 | 1（S2-L5，按计划挂账） |
| S3 报告（命令契约声明） | 8 | 7 | 1（S3-L3） | 0 |
| S4 报告（快照契约声明） | 9 | 8 | 0 | 1（S4-L5，明确推迟） |
| S5 报告（规则签名声明） | 6 | 6 | 0 | 0 |
| S6 报告（基线工具链） | 10 | 10 | 0 | 0 |
| **合计** | **45** | **42** | **1** | **2** |

> 计数口径：S2 报告自述「1 高 + 5 中 + 5 低 = 11 项」，实际条目 12 项（S2-M3 被算作一条）；S3 报告 M1 的「11 处」实为 9 处。**报告自身的计数有两处不准**——不影响结论，但复核时容易被带偏，记在这里备查。

### 3.2 仍未解决 / 半落实的两项

| 来源 | 编号 | 状态 | 说明 |
| --- | --- | --- | --- |
| **S4-L5** | 快照 `phase` / `hold` / `next` / `rule` 取值域无机器绑定；契约内生命周期两套写法（命令侧英文 `not_started` / `paused` / `game_over` vs 快照侧中文散文「待开始 / 运行 / 暂停 / 结束」） | **未落实（作者主动推迟）** | `snapshot.py:169` 仍是 `_field("phase","str","生命周期状态：待开始 / 运行 / 暂停 / 结束")`；`commands.py:121-123` 仍是英文三值。**基线只哈希 `str`，消费方推不出该写哪一套。** 已挂 `PROGRESS.md:31`「取值域统一收口」 |
| **S3-L3** | 事件侧 `commandType` 的取值域（= 16 个命令名）没有机器绑定 | **半落实** | `commands.py:418-423` 已有 `COMMAND_TYPE_FIELDS` 表 + `tests:821-838` 断言「承载命令名的字段都在表里、类型是 `str`」，但**没有任何断言把事件侧取值域与 `COMMAND_TYPES` 绑定**。作者理由：类型语言无字面量枚举形态，运行时值域校验归 S9/S11（`ai_docs/261003.md:185`） |

**这两项是同一件事的两半**（取值域怎么绑到类型上），建议在同一次收口里做掉——`PROGRESS.md:31` 已经这么要求了（「只扩展文法一次」）。

### 3.3 S6 十项的独立回归验证（`e69a561` 修订后）

我对着**修订后的代码**重跑了全部相关探针，全部按预期：

| 编号 | 破坏方式 | 期望 | 实测 |
| --- | --- | --- | --- |
| M1 | `import os, numpy` | 红 | **红**（exit 1）✅ |
| M2 | `--path src/typo_dir`（两个脚本） | 退出码 2 | **exit 2** ✅ |
| M3 | 新增第 9 条签名 | 红 | **红**：`count/ruleSignature` 哈希变化 ✅ |
| L1 | 字面量 `'nt'` | 放行 | **绿**（exit 0）；`'linux'` 仍红 ✅ |
| L2 | `"note:todo"` / `"brand:new-namespace"` | 只警告 | **警告，exit 0** ✅；`"kernel:probe-unregistered"` → **红** ✅ |
| L4 | 事件负载字段名与结构性键同键 | 退出码 2 拒绝进基线 | **exit 2**：「基线键重复：event/kernel:ready/producer」✅ |
| L7 | `_load_runner` 返回类型 | 用协议替代 `object` | `measure_frame_cost.py:66-73` 已改为 `FrameRunner` Protocol ✅ |
| D7 加严 | 六种墙钟写法（`time.time()` / `t.time()` / `time()` / `datetime.datetime.now()` / `datetime.now()` / `r.random()`） | 全红 | **六种全红** ✅（作者比报告建议更严） |
| 回归 | 改字段类型 / 改名 / 新增快照字段 / 改注释 / 未登记事件 | 红 / 红 / 绿 / 红 / 红 | **全部仍按预期** ✅ |

**M3 的修复值得单独说一句**：作者没有加「事件数 / 命令数」计数键——架构 §4.4 明确允许新增事件与命令，加了就把正常成长变成 major。**只有冻结面才配计数键**（`tools/contract_baseline.py:220-231`）。实测「新增一条事件类型」仍然是绿的，边界守得住。

---

## 4. 本轮新发现（6 项，全部低优先级）

以下 4 项是**跨阶段文档残留**——每个阶段自己看都通顺，但阶段之间演进后没跟上。这类问题只有把五个阶段放在一起看才会露出来。

### N1 · `commands.py:413`：封闭文法的形态数还是 8，实际已 10

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/commands.py` |
| 行号 | 413 |
| category | documentation |
| severity | low |

原文：「**事件负载的类型语言里没有字面量枚举形态**（`types.py` 的封闭文法只有 8 形）」。

`types.py` 的文法表现在是 **10 形态**（S4 补形态 9、S5 补形态 10）。这句话是在 S3 写的，之后没跟着改。

**影响**：不改代码，但**它是 S3-L3 的理由**（"类型语言里没有枚举形态，所以只能这样声明"）。理由本身仍然成立（10 形里也确实没有字面量枚举），但引用一个过期的数字会让人以为文法没演进过。

### N2 · `snapshot.py:268`：指向的条目名不存在

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/snapshot.py` |
| 行号 | 268 |
| category | documentation |
| severity | low |

原文：「……见 `KERNEL_STATE_TO_SNAPSHOT` 里 `rngSource` 那条」。

那张表里的实际条目是 `KernelStateEntry("rngInstance", "rngState", derived=True)`（`snapshot.py:261`）——**内核侧字段名是 `rngInstance`，不是 `rngSource`**。`rngSource` 这个名字在整个仓库里都不存在。

### N3 · `tests/test_contracts_declarations.py:1097`：docstring 与实现说的不是一回事

| 字段 | 值 |
| --- | --- |
| path | `tests/test_contracts_declarations.py` |
| 行号 | 1097 |
| category | documentation |
| severity | low |

原文：「叶子集合由 `NAMED_STRUCTS` 展开成员表算出」。

实现（`_snapshot_leaf_paths`，`tests:956-974`）用的是**快照模块自己的** `BOARD_FIELDS` / `ACTIVE_FIELDS`——这是 S5 报告 L3 的修订（"拿注册表里有没有这个名字当是不是快照子结构的判据太宽"）。修订时改了 helper 的 docstring，**漏了调用方的 docstring**，于是同一个文件里两处说法打架。

**这条正好说明「文档残留」的成因**：修订只覆盖了出问题的那一处，没有回头看还有谁引用了它。

### N4 · `types.py:73-74`：形态 10 的串数与列举对不上

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/types.py` |
| 行号 | 73–74 |
| category | documentation |
| severity | low |

原文：「仓内形态 10 的串只有**三条**：`types.BOARD_VIEW` 与 `score_clear` 的 `groups: Sequence[ClearGroup]`」。

列出来的是**两条串**（`Sequence[Sequence[int]]` 与 `Sequence[ClearGroup]`），引用处是**三处**（`rules.py:349` / `362` / `376`）。「串」按类型串算就是 2 条。应为「两串三处」。

### N5 · `tuple[str, ...]` 字面量 4 处，没有共用常量

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/events.py` · `src/tetris_kernel/contracts/snapshot.py` |
| 行号 | `events.py:334` · `events.py:522` · `events.py:587` · `snapshot.py:183` |
| category | maintainability |
| severity | low |

架构 §4.4 末条：「**引用处一律用常量，不复制字面量**——复制字面量的后果是常量改了、声明没跟着改，两处静默分叉，基线却显示全绿。」

`types.py` 为 `CELLS` / `COORD_CELLS` / `BOARD_CELLS` / `INT_TUPLE` / `VECTOR2` / `SCALAR_MAP` / `BOARD_VIEW` 都定义了共用常量，**唯独预览队列的 `tuple[str, ...]` 没有**——于是它在四个地方各写一遍（`piece:spawned.next` · `queue:refilled.next` · `state:changed.next` · 快照 `next`）。

**当前四处取值相同，基线一致，所以不是缺陷**；但它是这条纪律的一个缺口：预览队列要是从 5 个变成别的形状，四处要各改一遍。补一个 `types.NEXT_QUEUE` 即可。

### N6 · 测试缺一条「转录表 == 注册表」的集合断言

| 字段 | 值 |
| --- | --- |
| path | `tests/test_contracts_declarations.py` |
| 行号 | 431–436（`test_event_payload_fields_match_expectation`） |
| category | test |
| severity | low |

`EXPECTED_PAYLOAD`（44 个事件）与 `EXPECTED_EVENTS`（44 个事件）是两张**各自独立**的表，但**没有任何断言说这两张表的键集合相等**。

后果：从 `EXPECTED_PAYLOAD` 里删掉一个事件的键 → 该事件的 `@pytest.mark.parametrize` 不再生成用例 → **该事件静默失去字段级牙齿**（事件本身仍被 `test_event_registry_size` / `test_event_registry_matches_requirement_table` 守住，所以不是红灯，只是少了一层守护）。这类「删一个键就少守一处」的退化，正是契约面最怕的形状。

**建议**：加一条 `assert set(EXPECTED_PAYLOAD) == set(EVENT_TYPES) == set(EXPECTED_EVENTS)`。

---

## 5. 质量门

在 HEAD（`f059c67`）实跑，全部通过：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **156 passed**（0.57s） |
| `python -m ruff check .` | **All checks passed!** |
| `python -m ruff format --check .` | **45 files already formatted** |
| `python -m pyright src/tetris_kernel` | **0 errors, 0 warnings, 0 informations** |
| `lint-imports` | **Contracts: 4 kept, 0 broken**（exit 0） |

> `ruff format` 的 45 = src 21 + tools 7 + tests 6 + docs 6 + ai_docs 2 + 根目录 3（该命令也统计 `.md`）。S6 报告写作时是 44，多出来的那 1 个就是 `tests/ocr/S6-review.md` 入库。

五个脚本的默认行为（均符合各自 docstring）：

| 脚本 | 退出码 | 输出 |
| --- | --- | --- |
| `check_kernel_purity.py` | 0 | 扫了 2 个目录：违规 0 条 · 警告 0 条 |
| `check_event_registry.py` | 0 | 注册表里有 44 条事件类型 · 检查通过 |
| `check_frozen_baseline.py` | 0 | 冻结区基线尚未生成（按计划 M0 收尾 / S17 生成） |
| `contract_baseline.py` | 0 | 契约基线尚未生成（按计划 S17 冻结契约 v1 时生成） |
| `measure_frame_cost.py` | **2** | 内核闭环尚未就绪（`tetris_kernel.host.headless` 不存在） |

---

## 6. 需求文档 ↔ 机器声明：逐条机器比对（本轮新增）

此前五轮审查做到的都是「**代码 ↔ 测试里的转录表**」——**没有任何断言把转录表与需求文档绑在一起**。需求文档改了而转录表没改，测试照样全绿。本轮补上这一层：直接解析 `docs/需求与边界.md` 的 §8.1 / §8.2 / §8.3 三张表，与 `contracts/` 的声明逐条比对。

| 需求文档 | 文档条数 | 代码条数 | 差异 |
| --- | --- | --- | --- |
| §8.1 事件目录（事件名 + 命名空间） | 44 | 44 | **无** |
| §8.1 产出者列（中文 → 代号） | 44 | 44 | **无**（逐条相等，含 `kernel:system:*` → host、`kernel:error` → bus 这两处反直觉映射） |
| §8.2 命令目录（命令名） | 16 | 16 | **无** |
| §8.2 命令参数规格（参数名 + 类型） | 3 条有参命令 | 3 | **无**（`setLevel.level` / `injectGarbage.rows+holes` / `writeCells.cells`） |
| §8.2 拒绝原因枚举 | 12 | 12 | **无** |
| §8.2 拒绝原因分类（5 类） | 5 类 | 5 类 | **无**（逐类成员顺序也相等） |
| §8.3 快照顶层字段 + `board` 子字段 | 26 | 26 | **仅 `extensions` 一条**：文档写 `tuple[tuple[str, JsonScalar], ...]`，代码写展开值 `tuple[tuple[str, int \| float \| str \| bool \| None], ...]` |

**`extensions` 那条不是缺陷**——`JsonScalar` 是架构 §5.4 定义的**记号**，封闭文法里没有「记号」形态，S4 报告已把它记为「转录已非需求字面」并在 `tests:874-878` 写了例外说明、用 `test_snapshot_types_reference_shared_constants` 单独钉住展开值与 `types.SCALAR_MAP` 的相等性。

**结论：契约声明面与需求文档逐条相符，没有隐藏偏差。** 这是本轮最有分量的一条正面结论——它把「声明 == 需求」这件事从人工核对变成了机器核对。

---

## 7. 覆盖率

| 项 | 数 |
| --- | --- |
| OCR 预览文件总数 | 28 |
| 可审文件（`reviewable_files`） | **16** |
| 已审查 | **16** |
| 跳过 | **0** |
| 额外审查（OCR `default_path` 排除） | **1**（`tests/test_contracts_declarations.py`，1798 行） |
| **覆盖率** | **17/17 = 100%**（代码 16/16，含测试 17/17） |

**排除文件处置**：

| 文件 | 排除原因 | 处置 |
| --- | --- | --- |
| `docs/需求与边界.md` · `docs/项目架构.md` · `docs/spec-M0.md` · `docs/plan-M0.md` · `docs/契约v1清单.md` · `PROGRESS.md` · `ai_docs/261003.md` | `unsupported_ext` | 作为审查**基准**逐节读取（§2.2）；本报告的每条结论都指向具体章节行号 |
| `tests/ocr/S2-review.md` … `S6-review.md` | `unsupported_ext` | 逐份读完，并作为 45 项复核的清单 |
| `tests/test_contracts_declarations.py` | `default_path` | **逐行审查并计入覆盖率**——机检担保几乎全在它身上 |

---

## 8. 残余风险（非缺陷，供作者决定）

以下三点是**设计上的取舍或已知边界**，代码与文档都写明了，不单列为发现：

1. **新命名空间下私造事件类型不会被拦**。事件登记检查分两档：已登记命名空间下红、未登记命名空间下只警告。代价是「注册表是唯一权威」这条 ADR-020 在**新命名空间**上只靠警告维系。docstring 写明了这个取舍（`check_event_registry.py:21-31`）。**如果将来变体要引入自己的事件命名空间，这条要重新想清楚。**
2. **局部变量遮蔽仍会误判**。纯净度检查靠导入绑定表还原调用形状，**局部变量遮蔽模块名**（`os = compute_os()` 后写 `os.name`）仍会被判成平台属性访问。作者在修订说明里认了这条边界。内核代码量小，暂可接受。
3. **`tools/` 不在 pyright 覆盖范围内**（`pyproject.toml:54` `include = ["src"]`）。S6 L7 把 `_load_runner()` 的返回类型从 `object` 改成了 `FrameRunner` 协议（比报告建议做得更好），但**整个 `tools/` 目录仍然没有类型检查**。S17 之后 `tools/` 会成为常驻质量门的一部分，届时建议纳入。

---

## 9. 给下一阶段的建议

### 9.1 S7（随机源与时间源）

- `kernel/rng.py` 必须落在 `RANDOM_SOURCE_MODULE = "kernel/rng.py"` 这个路径上——豁免是**按路径后缀匹配**的（`check_kernel_purity.py:376`），改了模块名要同步改常量。
- 六种墙钟写法现在全被抓住，`random.Random(seed)` 只在 rng 模块放行——**S7 写 `getstate()` / `setstate()` 不受拦截**（它们不是墙钟函数）。
- **实测提醒**：`random` 模块级的 `random.seed()` / `random.random()` 一律红（`canonical.startswith("random.")`），随机源模块只能用实例方法。

### 9.2 S17（冻结基线）之前

1. **先做取值域统一收口**（S4-L5 + S3-L3 合并做，`PROGRESS.md:31` 已要求「只扩展文法一次」）：取值域常量归 `contracts/types.py` · 命令与快照共用同一套生命周期标识符 · 要让基线看见取值域就得给文法加**字面量枚举形态**（一次扩展，扩展完基线的哈希口径要重新定）。
2. 顺手补 N5（`types.NEXT_QUEUE` 常量）与 N6（集合断言）——两处都是小改，趁基线还没生成做代价最低。
3. **基线生成时条目数是 355**（353 条声明 + 2 条计数键），`docs/契约v1清单.md` 第 16 项在生成那次提交里勾，提交 message 写明「契约 v1 冻结」。

---

## 10. 未验证 / 不作评价的部分

- **基线文件本身尚未生成**（按计划 S17 生成）→ 本报告所有「进基线 / 不进基线」的判断都是**口径推导 + 临时基线实测**，没有真实基线文件的哈希可比。
- **需求 / 架构 / spec 的逐条对齐**：§6 的机器比对覆盖了 §8.1 / §8.2 / §8.3 三张表（事件 / 命令 / 拒绝原因 / 快照），**架构 §7.3.1 的签名表是人工核对的**（S5 轮逐条核过，本轮复核未发现漂移）。
- **未复跑 S5 提交说明自陈的另外 6 组反向验证**，也未复跑 S4/S3/S2 提交说明里的反向验证原文——本轮只做了 35 项发现的落实裁定与 S6 的独立回归。
- **两个复核 Agent 的裁定我抽验了关键项**（S4-L5、S3-L3、S2-M3、L1 等均已亲自核对代码），其余条目采信其逐条附证；两 Agent 均只读、未改动工作区。
- **`tests/ocr/` 五份旧报告自身的计数有两处不准**（S2 自述 11 实为 12；S3-M1 的「11 处」实为 9 处）——不影响任何裁定，但报告本身不是完全可靠的清单。
- **本轮未审查 `ai_docs/` 与 `PROGRESS.md` 的内容质量**（只作为基准读取），它们不属于代码面。

---

## 附：本次审查的自我约束

- 只报**有把握的真缺陷**；N1–N6 全部是低优先级，风格类不单列。
- 每条发现都带 path、行号、category、severity，并附**当前代码形态**或**可复现的实测**。
- 结论不夸大：契约面与需求文档逐条相等、45 项历史发现已落实 42 项，这些是**实测出来的**；仍未解决的两项也已标明是作者主动推迟，不是遗漏。
