# S2–S6 汇总代码审查报告 · 契约声明面与基线工具链

> **轮次**：第 2 轮（**复审** · 修订后重审）· 首轮结论见 §3，修订后的复核见 §1–§2
> 审查范围：`05bd643..888b16d`（S2 事件契约 → S6 基线脚本，含全部修订提交）
> 审查方式：`open-code-review-delegate`（OCR 只做文件选择与规则解析，判断由宿主 Agent 完成）
> 审查基线：`888b16d`（HEAD）
> 审查日期：2026-10-23

---

## 1. 复审结论（第 2 轮）

**修订已全部核实到位。本轮未发现任何新问题。**

| 项 | 结论 |
| --- | --- |
| **首轮 6 项新发现（N1–N6）** | **6/6 已落实**，逐条附代码证据（§2.1） |
| **S6 报告 L7（`tools/` 不在 pyright 覆盖内）** | **已落实，且比报告建议更严**：不只是改返回类型，而是把 `tools/` **整目录纳入 pyright**，并顺手修掉 IDE 报出的 **4 处真实类型不精确**（`88e96db`） |
| **S6 报告十项 + 前四轮 35 项** | 维持原判，**本轮回归验证无退化**（§2.2） |
| **此前 51 项发现（45 + 6）** | **已落实 48 · 半落实 1（S3-L3）· 主动推迟 2（S2-L5 / S4-L5）** |
| **需求文档 ↔ 机器声明** | **重跑仍逐条相等**（§3.2）——`NEXT_QUEUE` 重构没有引入任何偏差 |
| **契约基线** | **条目数仍 355**，`next` 四处的哈希与修订前**逐字节相同**（§2.3） |
| **质量门** | pytest **157 passed** · ruff check 全过 · ruff format **46 files** · **pyright（覆盖 `src/` + `tools/`）0 错** · lint-imports 4 kept |
| **本轮新发现** | **0 项** |

**总体判断：S2–S6 契约面可以进入 S7。** 唯一还开着的是 S4-L5 + S3-L3（取值域统一收口），两项都已挂在 `PROGRESS.md` 的待办里，作者明确要「只扩展文法一次、在同一次收口里做完」——这个决定是对的，本轮复审没有发现需要提前的理由。

---

## 2. 复审明细

### 2.1 首轮 6 项新发现的落实复核

| 编号 | 发现 | 状态 | 当前证据 |
| --- | --- | --- | --- |
| **N1** | `commands.py` 仍写「封闭文法只有 8 形」 | **已落实** | `commands.py:413` → 「封闭文法现有 **10 形**，仍没有"枚举"这一形） |
| **N2** | `snapshot.py` 指向全仓不存在的 `rngSource` | **已落实** | `snapshot.py:268` → 「见 `KERNEL_STATE_TO_SNAPSHOT` 里 **`rngInstance`** 那条」 |
| **N3** | 测试 docstring 说叶子由 `NAMED_STRUCTS` 展开，实现却用快照自己的成员表 | **已落实** | `tests:1120` → 「叶子集合由**快照模块自己的** `BOARD_FIELDS` / `ACTIVE_FIELDS` 展开算出」，并补了一句说明**为什么不用** `NAMED_STRUCTS`（注册表命中范围更宽，耦合一扩就变形） |
| **N4** | `types.py` 说形态 10 的串「只有三条」却只列两条 | **已落实** | `types.py:73-74` → 「**串只有两条**（`types.BOARD_VIEW` 与 `Sequence[ClearGroup]`），**引用处三处**：`scan_clears` / `classify_placement` 的 `board` 参数与 `score_clear` 的 `groups`」 |
| **N5** | `tuple[str, ...]` 字面量 4 处没有共用常量 | **已落实** | 新增 `types.py:99` `NEXT_QUEUE = "tuple[str, ...]"`；四处引用点全部改为常量（`events.py` 三处 · `snapshot.py` 一处）；**并补了断言钉住**：`tests:1030`（快照 `next` == `NEXT_QUEUE`）+ `tests:1035-1044`（三个事件的 `next` 负载 == `NEXT_QUEUE`） |
| **N6** | 测试缺「转录表 == 注册表」的集合断言 | **已落实** | `tests:402` → `assert set(EXPECTED_PAYLOAD) == set(EVENT_TYPES)`（挂在 `test_event_registry_matches_requirement_table` 里，docstring 写明了「删一个键就少守一处」这个退化形状） |

**复核方式**：逐条读当前代码（不只是读提交说明），并实跑验证 N5 / N6 的断言确实生效——`NEXT_QUEUE` 四处引用点全部返回常量（`all 4 match constant: True`）。

### 2.2 S6 报告 L7 的落实：比建议更严

首轮 S6 报告 L7 写的是「`tools/` 不在 pyright 覆盖范围内……建议届时纳入」。作者的处置**比建议更严**，值得单独记：

**① 把门补上**（`pyproject.toml:59`）：

```toml
include = ["src", "tools"]
```

命令从 `pyright src/tetris_kernel` 改为**不带路径的 `pyright`**——**受检目录只有 `include` 一处权威，不抄两份名单**（与 S6 报告里「键就是唯一权威」的同一条纪律）。

**② 把类型写对，不是加 `# type: ignore`**：

| 位置 | 原写法 | 现写法 |
| --- | --- | --- |
| `tools/_toolkit.py:61` | `stream.reconfigure(...)` + `except (AttributeError, OSError, ValueError)` | `if not isinstance(stream, io.TextIOWrapper): continue` → 异常收窄到 `(OSError, ValueError)`（不是 `TextIOWrapper` 就没有这个方法，`AttributeError` 不可能再发生） |
| `tools/contract_grammar.py:96,105,112` | `elements: tuple[object, ...]` · `element: object` · `members: tuple[object, ...]` | `tuple[TypeNode, ...]` · `TypeNode` · `tuple[TypeNode, ...]`——**递归引用**，靠 `from __future__ import annotations` 前向引用；**不用 `Any`**（`Any` 是放弃检查，比 `object` 更糟） |

**③ 门自己能不能触发？我实测了**（这是最要紧的一步——「门说自己守着、实际上没守」是本仓吃过亏的坑）：

| 探针 | 结果 |
| --- | --- |
| 干净树跑 `pyright`（无路径） | exit 0 · **0 errors, 0 warnings, 0 informations** ✅ |
| **往 `tools/_toolkit.py` 注入一处类型错误**（`def _probe_type_error() -> int: return 'not an int'`） | **exit 1** · `tools\_toolkit.py:115:12 - error: Type "Literal['not an int']" is not assignable to return type "int"` ✅ **门确实抓得住 `tools/`** |
| 还原后再跑 | exit 0 ✅ |

**④ 文档同步到位**：`AGENTS.md` · `README.md` · `docs/项目架构.md`（§1.2 命令行 + §8 质量门表）· `docs/spec-M0.md` · `docs/plan-M0.md` 的命令块全部改成 `pyright`。`PROGRESS.md` 把「`tools/` 尚未纳入 pyright 覆盖」从待办改成**已办**，并在卡点里记下教训：**「类型不精确只靠编辑器兜着，等于没有门」**——这 4 处错误 IDE 里看得见、项目自己的门看不见，从 S6 活到今天才被点出来。

### 2.3 回归验证：修订没有让任何守卫退化

**S6 十项 + 我加的回归项，全部重跑**（临时改动 → 实跑 → 还原）：

| 编号 | 破坏方式 | 期望 | 实测 |
| --- | --- | --- | --- |
| M1 | `import os, numpy` | 红 | **exit 1** ✅ |
| M1 加严 | `from time import time` → `time()` | 红 | **exit 1** ✅ |
| M1 加严 | `import datetime` → `datetime.datetime.now()` | 红 | **exit 1** ✅ |
| M2 | `--path src/typo_dir`（两个脚本） | exit 2 | **exit 2 / exit 2** ✅ |
| M3 | 新增第 9 条签名 | 红 | **exit 1**（`count/ruleSignature` 哈希变化）✅ |
| L1 | 字面量 `'nt'` | 放行 | **exit 0** ✅ |
| L2 | `"kernel:probe-unregistered"` | 红 | **exit 1** ✅ |
| L2 | `"note:todo"` | 只警告 | **exit 0** ✅ |

**基线不变**（`NEXT_QUEUE` 重构没动类型串）：

```
条目数: 355
event/piece:spawned/next   798a5e371cc23b411023b60b3a6a7cb2   tuple[str, ...]
event/queue:refilled/next  798a5e371cc23b411023b60b3a6a7cb2   tuple[str, ...]
event/state:changed/next   798a5e371cc23b411023b60b3a6a7cb2   tuple[str, ...]
snapshot/next               798a5e371cc23b411023b60b3a6a7cb2   tuple[str, ...]
count/ruleSignature         96e23f7b90ac8163d14936fbab23cb64   8
count/namedStruct           96e23f7b90ac8163d14936fbab23cb64   8
```

四个 `next` 键的哈希**完全相同**（同一个类型串）且与修订前一致——`NEXT_QUEUE` 只是把字面量换成了常量，**声明值一字未动**。

### 2.4 两项「主动推迟」的状态确认

| 来源 | 项 | 现状 |
| --- | --- | --- |
| **S4-L5** | 快照 `phase` / `hold` / `next` / `rule` 取值域无机器绑定 + 契约内生命周期两套写法 | **仍推迟**。`PROGRESS.md` 待办里挂着，理由是「要动就得给文法加『字面量枚举』形态，**只扩展一次**」。**复审同意这个决定**：文法扩展牵动基线口径，越早改越便宜，但**必须在 S17 生成基线之前**，不能拖到基线冻结之后 |
| **S3-L3** | 事件侧 `commandType` 的取值域（= 16 个命令名）无机器绑定 | **仍推迟**，与 S4-L5 合并成同一次收口。`commands.py:413` 的理由句已随 N1 改成「10 形，仍没有"枚举"这一形」——**理由依然成立** |
| **S2-L5** | 契约清单第 14 项（未知事件 / 未知元数据键容错）只有散文 | 随 **S11** 补，`PROGRESS.md` 已挂 |

---

## 3. 首轮结论与历史发现状态（供追溯）

首轮（基线 `f059c67`）结论：**无高危/严重问题**；6 项低优先级新发现；此前 45 项发现已落实 42 · 半落实 1 · 未落实 2。**本轮复审未推翻首轮的任何判断**。

### 3.1 全部 51 项发现的当前状态

| 来源 | 条数 | 已落实 | 半落实 | 主动推迟 / 挂账 |
| --- | --- | --- | --- | --- |
| S2 报告（事件契约声明） | 12 | 11 | 0 | 1（S2-L5，随 S11） |
| S3 报告（命令契约声明） | 8 | 7 | 1（S3-L3） | 0 |
| S4 报告（快照契约声明） | 9 | 8 | 0 | 1（S4-L5，取值域收口） |
| S5 报告（规则签名声明） | 6 | 6 | 0 | 0 |
| S6 报告（基线工具链） | 10 | 10 | 0 | 0 |
| 首轮汇总报告（本文件） | 6 | 6 | 0 | 0 |
| **合计** | **51** | **48** | **1** | **2** |

> 计数口径备注：S2 报告自述「1 高 + 5 中 + 5 低 = 11 项」实为 12 项；S3 报告 M1 的「11 处」实为 9 处。**报告自身的计数有两处不准**，不影响任何裁定。

### 3.2 契约声明面与需求文档的逐条比对（重跑仍全等）

| 需求文档 | 文档条数 | 代码条数 | 差异 |
| --- | --- | --- | --- |
| §8.1 事件目录（事件名 + 命名空间） | 44 | 44 | **无** |
| §8.1 产出者列（中文 → 代号） | 44 | 44 | **无** |
| §8.2 命令目录（命令名） | 16 | 16 | **无** |
| §8.2 命令参数规格（参数名 + 类型） | 3 条有参命令 | 3 | **无** |
| §8.2 拒绝原因枚举 | 12 | 12 | **无** |
| §8.2 拒绝原因分类（5 类，含成员顺序） | 5 类 | 5 类 | **无** |
| §8.3 快照顶层字段 + `board` 子字段 | 26 | 26 | **仅 `extensions` 一条**：文档写 `JsonScalar` 记号，代码写展开值 |

**`extensions` 那条不是缺陷**——`JsonScalar` 是架构 §5.4 定义的记号，封闭文法里没有「记号」形态；S4 报告已记为「转录已非需求字面」，`tests:874-878` 写了例外说明，并由 `test_snapshot_types_reference_shared_constants` 单独钉住展开值与 `types.SCALAR_MAP` 的相等性。

**这一层此前五轮都没做到**——五轮做的都是「代码 ↔ 测试里的转录表」，**没有任何断言把转录表与需求文档绑在一起**。首轮汇总补上，本轮重跑确认仍然全等。

---

## 4. 质量门（HEAD `888b16d`）

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **157 passed**（0.55s）——比首轮 +1，是 N5 新增的 `test_next_queue_payload_fields_reference_shared_constant` |
| `python -m ruff check .` | **All checks passed!** |
| `python -m ruff format --check .` | **46 files already formatted** |
| `python -m pyright`（**不带路径**，走 `include = ["src", "tools"]`） | **0 errors, 0 warnings, 0 informations** |
| `lint-imports` | **Contracts: 4 kept, 0 broken**（exit 0） |

> `ruff format` 的 46 = 29 个 `.py`（src 21 · tools 7 · tests 1）+ 17 份 `.md`（tests/ocr 6 · docs 6 · ai_docs 2 · 根目录 3）——该命令也统计 `.md`。首轮报告写作时是 45，多出来的 1 个是本报告入库。

五个脚本的默认行为（均符合各自 docstring）：`check_kernel_purity` 0 · `check_event_registry` 0 · `check_frozen_baseline` 0（基线未生成）· `contract_baseline` 0（基线未生成）· `measure_frame_cost` **2**（内核闭环未就绪，S14 后可用）。

---

## 5. 覆盖率

| 项 | 数 |
| --- | --- |
| OCR 预览文件总数 | 32 |
| 可审文件（`reviewable_files`） | **17**（含本轮新增的 `pyproject.toml`） |
| 已审查 | **17** |
| 跳过 | **0** |
| 额外审查（OCR `default_path` 排除） | **1**（`tests/test_contracts_declarations.py`，1825 行） |
| **覆盖率** | **18/18 = 100%** |

**排除文件处置**：

| 文件 | 排除原因 | 处置 |
| --- | --- | --- |
| `docs/需求与边界.md` · `docs/项目架构.md` · `docs/spec-M0.md` · `docs/plan-M0.md` · `docs/契约v1清单.md` · `PROGRESS.md` · `ai_docs/261003.md` · `AGENTS.md` · `README.md` | `unsupported_ext` | 作为审查**基准**逐节读取；本报告每条结论都指向具体章节行号 |
| `tests/ocr/S2-review.md` … `S6-review.md` · 本文件 | `unsupported_ext` | 逐份读完，作为 51 项复核的清单 |
| `tests/test_contracts_declarations.py` | `default_path` | **逐行审查并计入覆盖率**——机检担保几乎全在它身上 |

---

## 6. 残余风险（非缺陷，供作者决定）

三点是**设计上的取舍或已知边界**，代码与文档都写明了：

1. **新命名空间下私造事件类型不会被拦**。事件登记检查分两档：已登记命名空间下红、未登记命名空间下只警告。代价是「注册表是唯一权威」这条 ADR-020 在**新命名空间**上只靠警告维系（`check_event_registry.py:21-31` 写明了这个取舍）。**变体若要引入自己的事件命名空间，这条要重新想清楚。**
2. **局部变量遮蔽仍会误判**。纯净度检查靠导入绑定表还原调用形状，**局部变量遮蔽模块名**（`os = compute_os()` 后写 `os.name`）仍会被判成平台属性访问。作者在修订说明里认了这条边界。
3. **`tests/` 仍不在 pyright 覆盖内**（`include = ["src", "tools"]`）。`tests/` 有 1825 行测试代码没有类型检查——**首轮之前这是全仓无类型检查，现在是「除 tests 外全覆盖」**。测试代码的类型精度要求低于产品代码，但若将来 `tests/` 里出现复杂的泛型夹具，这个缺口会重新显形。

---

## 7. 给下一阶段的建议

### 7.1 S7（随机源与时间源）

- `kernel/rng.py` 必须落在 `RANDOM_SOURCE_MODULE = "kernel/rng.py"` 这个路径上——豁免是**按路径后缀匹配**的（`check_kernel_purity.py:376`），改模块名要同步改常量。
- 六种墙钟写法现在全被抓住；`random.Random(seed)` 只在 rng 模块放行；`getstate()` / `setstate()` 不受拦截（不是墙钟函数）。
- **`random` 模块级的 `random.seed()` / `random.random()` 一律红**（`canonical.startswith("random.")`）——随机源模块只能用实例方法。

### 7.2 S17（冻结基线）之前

1. **做取值域统一收口**（S4-L5 + S3-L3 合并，`PROGRESS.md` 已要求「只扩展文法一次」）：取值域常量归 `contracts/types.py` · 命令与快照共用同一套生命周期标识符 · 要让基线看见取值域就得给文法加**字面量枚举形态**（一次扩展；**必须在生成基线之前**，扩展完基线的哈希口径要重新定）。
2. 清单第 14 项随 **S11** 补（未知事件 / 未知元数据键容错的可机读声明）。
3. 生成基线时**条目数是 355**（353 条声明 + 2 条计数键），`docs/契约v1清单.md` 第 16 项在生成那次提交里勾，提交 message 写明「契约 v1 冻结」。

---

## 8. 未验证 / 不作评价的部分

- **基线文件本身尚未生成**（按计划 S17 生成）→ 本报告所有「进基线 / 不进基线」的判断都是**口径推导 + 临时基线实测**，没有真实基线文件的哈希可比。
- **架构 §7.3.1 的签名表是人工核对的**（S5 轮逐条核过，本轮复核未发现漂移）；§8.1 / §8.2 / §8.3 的三张需求表是**机器比对**的。
- **未复跑各阶段提交说明里的反向验证原文**——本轮做的是「发现落实裁定 + 独立回归」，不是重跑历史记录。
- **两个复核 Agent 的裁定**（首轮派出的 S2+S3 / S4+S5 复核）本轮未重跑，但涉及的代码文件（`events.py` / `commands.py` / `snapshot.py` / `rules.py` / `structures.py` / `types.py`）本轮**逐个 diff 读过**，改动只涉及 N1–N6 的六处，无回归。
- **本轮未审查 `ai_docs/` 与 `PROGRESS.md` 的内容质量**（只作为基准读取），它们不属于代码面。
- **历史验证记录里的旧数字**（`plan-M0.md` 里的「353 条」「44 files」、`ai_docs` 里的「156 passed」）**保持原样是刻意的**——那是「当时跑了什么」的记录，不是当前该跑什么的指令。作者这条判断是对的，复审确认没有把它误当成当前状态。

---

## 附：本次审查的自我约束

- 只报**有把握的真缺陷**；**本轮 0 项新发现**——修订到位就是到位，不硬凑。
- 每条历史发现的裁定都附**当前代码形态**或**可复现的实测**。
- 结论不夸大：契约面与需求文档逐条相等、51 项历史发现已落实 48 项、**新的 pyright 门实测能触发**——这些都是**实测出来的**；仍未解决的两项已标明是作者主动推迟，不是遗漏。

---

## 9. 作者复核与订正（2026-10-23 20:20）

这一节由**作者**（不是审查方）补记：收到本报告后逐条独立复核了一遍，**结论是报告可信**——报告给的文件行号、计数、哈希、退出码，全部实测复现：

| 报告的声明 | 作者的独立复核 | 结果 |
| --- | --- | --- |
| N1–N6 的证据行号 | `commands.py:413` · `snapshot.py:268` · `types.py:73-74` · `types.py:99` · `tests:402` / `1030` / `1035-1044` / `1120` 逐个打开核对 | **全部属实** |
| §3.1 的 51 项计数口径 | 逐份报告数标题：S2 = H1+M1–M6+L1–L5 = 12 · S3 = 8 · S4 = 9 · S5 = 6 · S6 = 10 · 本轮 6 | **全部属实**（含"S2 自述 11 实为 12""S3-M1 的 11 处实为 9 处"两处订正） |
| §2.3 的基线不变 | `contract_baseline.py --print-entries` 实跑 | **355 条**，四处 `next` 哈希仍是 `798a5e37…`，两条计数键仍是 `96e23f7b…` |
| §2.3 的 8 组回归探针 | 逐组实跑（M1 / M1a / M1b / M2 / M3 / L1 / L2） | **全部按报告所述触发**：M1 `import os, numpy` → exit 1 · `from time import time` → exit 1 · `datetime.datetime.now()` → exit 1 · M2 两个脚本 → exit 2 · M3 新增第 9 条签名 → exit 1（`count/ruleSignature` 哈希变化，新增键放行）· L1 字面量 `"nt"` → exit 0 · L2 `kernel:probe-unregistered` → exit 1、`note:todo` → exit 0 |
| §3.2 的需求 ↔ 机器声明 | 自己写临时脚本重跑比对（用完即删） | **逐条相等**：事件 44（含顺序、命名空间、产出者）· 命令 16（含 3 条有参命令的参数名与类型）· 拒绝原因 12 + 5 类（含成员顺序）· 快照 21 + 5 = 26；唯一差异仍是需求原文的 `JsonScalar` 记号 |
| §4 的五道门与五个脚本 | 全量实跑 | pytest **157 passed** · ruff check 全过 · ruff format 46 files · pyright 0 错 · lint-imports 4 kept；脚本退出码 0/0/0/0/**2** 全对 |

**订正报告自身两处笔误**（不影响任何裁定）：

1. **§1 的交叉引用指错了节**：原文「需求文档 ↔ 机器声明 …（§4）」，但 §4 是「质量门」，这份比对表在 **§3.2**。已改。
2. **§4 脚注的 46 文件拆分算错了**：「src 21 + tools 7 + tests 7 + docs 7 + ai_docs 2 + 根目录 3」加起来是 **47**。实际是 **29 个 `.py`**（src 21 · tools 7 · tests 1）+ **17 份 `.md`**（tests/ocr 6 · docs 6 · ai_docs 2 · 根目录 3）= 46。已改。

**顺带牵出一处仓库里的旧账**：报告 §3.1 说 S2 审查是 **12 项**，而 `PROGRESS.md` 仍写「14 项发现」——`ai_docs/261003.md` 早在 14:54 就订正过这一处（S3 报告 L5 点名的"日志说改了、文档没改"），**`PROGRESS.md` 漏了**。已改。**教训照旧：改完 A 要回头搜还有谁引用 A**，这次漏的是 `PROGRESS.md`，下次可能是别的。

**本轮没有新增代码改动**——报告的结论是「修订已全部到位」，作者复核后同意这个判断。
