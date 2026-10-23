# S3 阶段代码审查报告（委托模式）

> **审查对象**：M0 阶段 B 的 S3 —— 命令契约声明（16 条命令 + 12 值拒绝原因）。
> **代码范围**：`5fffbbb`「重构：统一 tuple[int, ...] 取值域常量的引用」+ `e4b4c63`「新功能：新增 16 条命令与拒绝原因的契约声明」（range `bd54e9d..e4b4c63`）。
> **审查方式**：`ocr delegate` 委托模式（OCR 只做文件选择与规则解析，审查判断由本 Agent 完成）。
> **审查日期**：2026-10-23
> **审查基线**：需求 `docs/需求与边界.md` §8.2 · 架构 `docs/项目架构.md` §4.4 / §6.1 / §6.3 / §6.4 / §6.5 · 契约清单 `docs/契约v1清单.md` 第 5/6/13 项 · 计划 `docs/plan-M0.md` S3 / §5.3 / §5.4

---

## 1. 结论

**16 条命令与 12 值拒绝原因的声明，逐条对得上需求 §8.2；三条跨切规则（生命周期门禁 / 配额 / 参数）的显式化是这份声明最有价值的部分——它把「实现期各写各的」的三处风险提前钉死了。可以进基线。**

但有 **3 项中等**需要在 S6 之前处理，其中 **1 项是 S2 审查报告 M1 的残留**（类型文法表扩到 8 形了，但新增的规则 1 与项目实际使用的类型串仍然冲突），另外 **1 项属于流程性**（决策日志声称已修的一处措辞，代码里没改）。

- 四条质量门全绿：`pytest` 113 passed · `ruff check` 全过 · `ruff format --check` 30 files · `pyright src/tetris_kernel` 0 错 · `lint-imports` 4 kept。
- 需求 §8.2 的 16 个命令名、参数名、参数类型、可拒绝情形**逐条核对一致**；拒绝原因 12 值与分类表不重不漏；命令顺序与需求表格顺序一致。

---

## 2. 覆盖统计

| 项 | 值 |
| --- | --- |
| total_files（preview 检出） | 10 |
| reviewable_files | 4 |
| excluded_files | 6（5 个 Markdown + `tests/test_contracts_declarations.py`） |
| reviewed_files | 5（4 个 reviewable + 被 `default_path` 排除的测试文件） |
| skipped_files | 0 |
| coverage_rate | **100%（代码文件）** |

| 文件 | 状态 | 行数 | OCR 判定 |
| --- | --- | --- | --- |
| `src/tetris_kernel/contracts/commands.py` | 新增 | +443 | reviewable |
| `src/tetris_kernel/contracts/__init__.py` | 修改 | +25 | reviewable |
| `src/tetris_kernel/contracts/types.py` | 修改 | +12 −5 | reviewable |
| `src/tetris_kernel/contracts/events.py` | 修改 | +3 −3 | reviewable |
| `tests/test_contracts_declarations.py` | 修改 | +345 −3 | excluded（`default_path`）——**本报告仍逐行审查了它**：S3 的机检担保全在这个文件里 |
| `PROGRESS.md` / `ai_docs/261003.md` / `docs/plan-M0.md` / `docs/契约v1清单.md` / `docs/项目架构.md` | 修改 | — | excluded（`unsupported_ext`）——文档改动，**按 S2 报告的口径单独核对**（见 §6） |

**OCR 命令留痕**：`ocr delegate preview --format json --from bd54e9d --to e4b4c63` → `ocr delegate rule --format json <4 个路径>`（规则组 `system`，覆盖 `**/*.{py,pyi,ipynb}`）。

---

## 3. 发现清单

### 3.1 Medium

#### M1 · 类型文法的规则 1，与项目实际使用的 5 个类型串冲突（11 处）—— **S2 报告 M1 的残留**

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/types.py` |
| start_line / end_line | 37–38（规则 1）、24–33（形态表） |
| category | documentation / bug |
| severity | medium |

**问题**：`types.py` 的文法表在 S2 修订时从 6 形扩到 8 形，并新增了「三条附加规则」。其中**规则 1 与项目自己的类型串直接冲突**：

> 规则 1（`types.py:37-38`）：**形态 2 与 3 分开**：`float | int` 是联合，`str | None` 是可空。**`None` 只出现在可空形态里**，且**永远是联合的最后一项**。

实测全库的类型串（临时脚本按表逐条校验，用完即删）：

| 类型串 | 出现位置 | 冲突点 |
| --- | --- | --- |
| `ARGS_VALUE` = `int \| str \| bool \| None \| tuple[int, ...] \| tuple[tuple[int, int, int], ...]` | `types.py:65` | **None 在第 4 位，不是末位**；且联合成员含元组形态 → **落不进形态 2（标量联合）的定义**，也落不进形态 3 |
| `JSON_VALUE` = `int \| str \| bool \| float \| None \| tuple[...]` | `types.py:51` | **None 不在末位**；成员含 `tuple[...]`（形态 8）→ 同样落不进任何形态 |
| `ARGS` = `tuple[tuple[str, {ARGS_VALUE}], ...]` | `types.py:68` | 内层联合同上（继承 `ARGS_VALUE`） |
| 信封 `payload` / `metadata` | `events.py` 的 `ENVELOPE_FIELDS` | 内层联合同上（继承 `JSON_VALUE`） |
| `command:received/accepted/rejected` 的 `args` 负载 | `events.py` | 内层联合同上（继承 `ARGS`）——3 处 |
| `SCALAR_MAP` = `tuple[tuple[str, int \| float \| str \| bool \| None], ...]` | `types.py:71` | 成员全是标量 ✓ 落进形态 2，但**None 出现在普通联合里**，违反规则 1 前半句 |

合计 **5 个类型串定义、11 处出现**。其中 `ARGS_VALUE`（命令 args 的取值域）与 `ARGS`（args 容器类型）是整个契约面最核心的两个串。

**为什么是问题**：文法表自己写着「表里没有的形态一律视为**声明不合规**（而不是"尽量解析"）」，而它同时又是「S6 的基线脚本按这张表写正则解析器」的规格书。S6 照规则 1 实现（None 只能作末项、且只在可空形态里），**上面 11 处会被判"声明不合规"**——正是 S2 报告 M1 担心的那种"看着在基线里、实际没被哈希"。S2 那次修订扩了形态表（加了形态 2 标量联合、形态 7 元组元素位含联合），**但没解决"规则与实例打架"这一半**。

**建议**：改**规则**而不是改声明（成本更低、语义更准）——把规则 1 改成：

> `None` 表示 JSON null，可作为联合的**普通成员**（与其它成员并列）；可空形态 `T | None` 特指"该类型的可空版本"，此时 `None` 必须是末项。

并把形态 2 的定义从「标量联合」改为「联合（成员为形态 1–8 的任意组合）」，同时给形态 3（可空）一个明确区分。**不建议**把 `None` 强行挪到末位——`int | str | bool | None | tuple[int, ...]` 读起来是"这几类之一"，末尾放 `None` 反而会被读成"前面的类型可空"，那是误读。

**时机很关键**：契约基线**尚未生成**（清单第 16 项「尚未生成基线」，基线在 S17 冻结），现在改类型串只是文档级成本；一旦冻结，联合成员顺序变化 = 「已有键的哈希变化」= major 变更（`types.py:44-45` 自己写了这条）。**建议随 S4 快照声明一并处理。**

#### M2 · 门禁「推导」并不自足：新增命令漏写三值时，`is_lifecycle_gated` 会安静地返回 False

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/commands.py` |
| start_line / end_line | 436–443（`is_lifecycle_gated`） |
| category | maintainability |
| severity | medium |

**问题**：`is_lifecycle_gated()` 的 docstring 写：

> 这是**推导值**，不是另写一份名单：门禁三值齐全 ⇔ 受门禁约束。推导的好处是两份名单不可能分叉——另写一份的话，加命令的人漏改名单，门禁就会静默失效（而门禁失效的代价是「暂停期间命令照样执行」）。

但推导的**输入**（每条命令的 `reject_reasons` 字面量）本身就是人手写的。**加命令的人漏写三个生命周期原因 → `is_lifecycle_gated` 返回 False → 这条命令不受门禁 → 暂停期间照样执行**——正是 docstring 自陈的那个后果。推导并没有消除"漏改"风险，只是把它从"生产代码里的一份名单"挪到了"每条命令的 reject_reasons 字面量"。

真正拦住它的是**测试层的三张手抄表**：`EXPECTED_COMMAND_ARGS`（`tests/.../test_contracts_declarations.py:481`）、`EXPECTED_COMMAND_REJECT_REASONS`（:533）、`EXPECTED_GATED_COMMANDS`（:660）。加一条命令要改三处——而 docstring 宣称"两份名单不可能分叉"。

**建议**：把「受门禁约束」变成 `CommandSpec` 上的**显式必填维度**，并让拒绝原因由它派生：

- `CommandSpec` 增加 `lifecycle_gated: bool`，**无默认值**（漏表态直接 `TypeError`，而不是安静地取 False）；
- `_cmd()` 在 `lifecycle_gated=True` 时自动并入 `LIFECYCLE_REJECT_REASONS`，于是「标志」与「原因」在构造期就不可能分叉；
- `is_lifecycle_gated()` 退化为读那个字段。

这样"加命令时必须对门禁表态"变成声明层的硬约束，而测试那三张表继续当交叉对照（转录法是本项目有意的设计，保留）。

**为什么值得提**：门禁是需求 §8.2 里唯一一条"错了就出事"的安全约束，且 plan-M0 自己把它的失效后果写成"暂停期间命令照样执行"。声明层给它一个必填字段，成本远低于 S9/S11 实现期真踩一次。

#### M3 · `hold` 缺 `no_active_piece`，与事件侧 `piece:held` 的口径不对齐

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/commands.py` |
| start_line / end_line | 335–352（`hold` 规格）、339–342（doc 备注） |
| category | other |
| severity | medium |

**问题**：需求 §8.2 给 `hold` 列的可拒绝情形只有「本块已使用过暂存」，实现据此**没有**列 `no_active_piece`——这个处理有依据（不替需求加码），doc 里也写了备注：「若实现期发现 running 态确有「无活动方块」的窗口，再按 major 变更流程补」。

**但两侧口径对不上**：

| 侧 | 声明 | 含义 |
| --- | --- | --- |
| 事件侧 | `piece:held` 的 `stored: str` / `taken: str \| None` | v1 已把 `taken` 改成 `str \| None`（S2 报告 M4 的修订）——**事件侧允许"槽空"** |
| 命令侧 | `hold` 的可拒绝原因只有 `hold_already_used` | running 态若无活动方块，hold **既不会被拒、也没有定义的行为** |

**为什么是问题**：事件侧已经承认"槽可以是空的"，命令侧却没说"没有方块可暂存时怎么办"。这不是要现在改需求（实现期的窗口是否存在还没定），而是**这个洞要挂在明处**：否则 S9/S11 实现期遇到 running 态无活动方块，要么静默发出一个语义未定义的 `piece:held`，要么临时给 `hold` 加拒绝原因——而那时候基线可能已经冻结（S17），就是一次 major 变更。

**建议**：把这条写进 `commands.py` 的 `hold` doc（现有备注只覆盖了"实现期发现再补"，没覆盖"事件侧已允许空槽"这个既成事实），并在 `PROGRESS.md` 的待办里挂一条「hold 在 running 态无活动方块时的行为待定」。

---

### 3.2 Low

| # | path | 行 | 问题 | 建议 |
| --- | --- | --- | --- | --- |
| L1 | `src/tetris_kernel/contracts/commands.py` | 443 | `is_lifecycle_gated()` 对未知命令名抛 `KeyError`（`COMMANDS[command_type]` 直接下标）。这是**已从包级导出的公开 API**，错误信息对调用方不友好 | 改成 `COMMANDS.get(...)`，未知名字抛带明确信息的 `ValueError`（或按语义返回 False 并在 doc 里写明） |
| L2 | `src/tetris_kernel/contracts/commands.py` | 171 | `COMMAND_ARGS_TYPE = ARGS` 是**悬空别名**：`CommandSpec` 里没有任何字段引用它（`CommandSpec.args` 是**参数声明列表**，不是 args 容器类型），实际只有测试拿它做禁浮点断言 | 二选一：① 在 `CommandSpec` 上显式声明"本命令的 args 容器类型 = `COMMAND_ARGS_TYPE`"；② 删掉这个别名（它现在只是 `ARGS` 的第二个名字，导出到包级 API 会让人以为有两个概念） |
| L3 | `src/tetris_kernel/contracts/commands.py` | 13–14 | docstring 说「命令名就是 `command:received` / `command:accepted` / `command:rejected` 三个事件负载里 `commandType` 的取值——**两处必须是同一批名字**」，但**没有任何断言或声明**把事件侧 `commandType` 的取值域绑到 `COMMAND_TYPES`；事件侧 `commandType` 的类型只是 `"str"`。`input:intent.commandType` 同理 | 加一条断言：事件侧 `commandType` 的取值域 = `COMMAND_TYPES`（16 个名字）。这是命令集"封闭可枚举"（FR-2.1）在事件侧的落点，目前只写在散文里 |
| L4 | `src/tetris_kernel/contracts/types.py` | 65 | `ARGS_VALUE` 里的 `str` 与 `bool` 在 v1 **没有任何命令使用**（16 条命令的参数类型只有 `int` / `tuple[int, ...]` / `tuple[tuple[int, int, int], ...]`）——取值域是超集 | 要么在 doc 里写明"为变体扩展预留"，要么收窄。留着也行，但要让读者知道这是**有意的余量**而不是抄宽了 |
| L5 | `ai_docs/261003.md` + `tests/test_contracts_declarations.py` | 373 | **决策日志与代码不一致**：S2 修订条目写「测试 docstring 说"每个产出者代号都有中文名"（`host` 的中文名就是 `host`）→ 三处措辞改准」，但 `test_producer_labels_cover_all_producers` 的 docstring **至今仍是原句**（`git log -S'中文名'` 显示该措辞最后一次改动在 `62a8c9a`，S2 修订提交 `bd54e9d` 没碰它）。同一条目还说"14 项发现"，而 S2 报告实为 **12 项**（1 高 + 6 中 + 5 低） | 改掉 docstring（1 行），或把日志里的"三处措辞改准"改成"两处"。**这类不一致会让下一份日志的可信度打折**——日志写"已修"的事，得有人复核 |

---

## 4. 与 S3 验收标准逐条对照

`docs/plan-M0.md` §4「S3 · 命令契约声明（16 条）与拒绝原因（12 值）」：

| # | 任务 / 判据 | 判定 | 证据 |
| --- | --- | --- | --- |
| 1 | 建 `contracts/commands.py`：命令名 → 参数名 / 类型 / 是否可空 / 可拒绝情形 | ✅ | `commands.py:193-427`（16 条）+ `:430`（`COMMANDS`）；`CommandSpec` 四字段齐（`:84-97`） |
| 2 | 拒绝原因枚举（只增不改）12 值 | ✅ | `commands.py:105-123`，与需求 §8.2 分类表逐条相等（`test_reject_reason_enum`）；`_reasons()` 对未知原因抛 `ValueError`（`:163-165`） |
| 3 | `args` 规格约束：只含 JSON 标量与元组、禁浮点、反序列化按命令类型还原 | ✅ | `types.py:65`（`ARGS_VALUE` 无 `float`）；`commands.py:21-24` 写明还原口径；`test_command_args_forbid_float` 把禁浮点写成断言 |
| 4 | 命令集封闭且可枚举（FR-2.1） | ✅ | `COMMANDS` 16 条 = 需求 §8.2 全集；`test_command_registry_is_closed_and_enumerable` 断言 `set(COMMANDS) == set(EXPECTED_COMMAND_ARGS)` 且 `COMMAND_TYPES == tuple(COMMANDS)` |
| 5 | 验证方案 1：命令总数 16；三条有参、其余 13 条无参 | ✅ | `test_command_registry_size` + `test_only_three_commands_take_args` |
| 6 | 验证方案 2：拒绝原因集合 = 12 值，与需求 §8.2 逐条相等 | ✅ | `test_reject_reason_enum` |
| 7 | 验证方案 3：所有命令的 `args` 声明里不出现 `float` | ✅ | `test_command_args_forbid_float`（断言，不靠眼睛） |
| 8 | 验证方案 4（机检）：`writeCells.cells` = `tuple[tuple[int, int, int], ...]`；`injectGarbage.holes` = `tuple[int, ...]` | ✅ | `test_write_cells_cells_type` + `test_inject_garbage_holes_type` |
| 9（计划"落定的判断"） | 统一生命周期门禁 / 配额跨切 / 参数不合规格，三条跨切规则显式化 | ✅ **（一处保留意见）** | 16 条命令的 `reject_reasons` 均含 `quota_exceeded` 与 `invalid_argument`；门禁三值齐全者 11 条。**保留意见见 M2**：门禁归属仍靠人手写三值，推导不自足 |
| 10（契约清单） | 第 5 / 6 项勾选 | ✅ | `00f17d4` 已勾，状态标注"已声明"（≠ 已过机检，S6 才算数） |
| 11（反向验证） | `setLevel.level` 由 `int` 改成 `float` → 4 条断言变红；给 `setLevel` 补三个生命周期原因 → 门禁不变式变红 | ⚠️ **未复跑** | 这两条是**计划记录的历史结论**，本轮未重跑。见 §5「未验证的部分」 |
| 12（架构 §4.4 末条） | 引用处一律用常量、不复制字面量 | ✅ | `5fffbbb` 引入 `types.INT_TUPLE`，`events.py` 与 `types.py` 全部改为引用；`test_command_arg_types_within_args_value_domain` 断言参数类型必落在 `ARGS_VALUE` 成员内 |

---

## 5. 跑过的检查

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 单元测试 | `pytest` | ✅ 113 passed in 0.57s |
| Lint | `ruff check .` | ✅ All checks passed |
| 格式 | `ruff format --check .` | ✅ 30 files already formatted |
| 类型 | `pyright src/tetris_kernel` | ✅ 0 errors, 0 warnings |
| 依赖方向 | `lint-imports` | ✅ 4 kept, 0 broken |
| 文法自洽（临时脚本，已删） | 按 `types.py` 自己的规则 1 + 形态表校验全库类型串 | ⚠️ **11 处冲突**——见 M1 |
| 常量搬家零漂移 | `git show 5fffbbb` 逐条比对类型串值 | ✅ **值一字未改**（`tuple[int, ...]` 的两处字面量 → `INT_TUPLE` 引用），与提交说明与 ai_docs 的说法一致 |

**未验证的部分（说清楚，不含糊）**：

- **反向验证没有复跑**。计划里记着「`setLevel.level` 改成 `float` → 4 条断言变红」「给 `setLevel` 补三个生命周期原因 → 门禁不变式变红」，本轮**没有重跑这两条**——它们是上一轮的历史结论，不是我这轮的实测证据。
- **S6 的基线脚本尚未落地**，所以「这份声明能不能被基线脚本正确哈希」**没有实测**——M1 正是基于此提的隐患（11 处类型串按现行规则属"不合规"）。
- **命令与需求 §8.2 的逐条对齐是人工核对的**（类型名、参数名、参数类型、可拒绝情形四列），核对结果一致；`EXPECTED_COMMAND_*` 三张表是**转录**不是机器解析文档，转录本身的正确性靠人工。
- 运行时行为（命令真的按这些规格被校验、被拒绝）属 S9/S10/S11，不在本次范围内。

---

## 6. S2 审查报告的落实情况复核（独立核实，不采信日志的自陈）

S2 报告共 **12 项**（1 高 · 6 中 · 5 低）。逐条回查代码后：

| S2 项 | 状态 | 核实方式 |
| --- | --- | --- |
| H1 契约版本号 v1 | ✅ 已修 | `events.py:30` `CONTRACT_VERSION = "v1"`，包级导出 + `test_contract_version_declared` |
| M1 类型文法 | ⚠️ **只修了一半** | 形态表 6 → 8 形 ✓，但新增的规则 1 与实际类型串冲突（本次 M1） |
| M2 信封类型断言 | ✅ 已修 | `test_envelope_fields` 改成 `name → type` 映射断言 |
| M3 负载字段无断言 | ✅ 已修 | `EXPECTED_PAYLOAD` 全表 + `test_event_payload_fields_match_expectation` |
| M4 `piece:held.taken` | ✅ 已修 | 改成 `str \| None` |
| M5 同名不同型 | ✅ 已修（改法有别） | `board:changed.cells` → `cellChanges`、`board:lines-cleared.rows` → `rowIndexes`（同层改名）；`level` / `cause` 用 doc 消歧 + 例外表 `SAME_NAME_DIFFERENT_TYPE_ALLOWED` |
| M6 取值域常量归属 | ✅ 已修 | 新建 `contracts/types.py`，并写进架构 §4.4 末条（基线只哈希引用处展开的类型串） |
| L1 `render:frame` 浮点措辞 | ✅ 已修 | `events.py:12-17` 改成"微秒整数 + `int \| float`"的准确说法 |
| L2 `state:changed` 缺 `rule` / `activeType` | ✅ 已修 | 补了 `rule`；差异写进 doc |
| L3 "没有 dataclass 之外的运行时对象" | ✅ 已修 | `events.py:4-5` 改成"只构造声明用的 dataclass 与只读映射" |
| L4 测试 docstring "中文名" | ❌ **未修** | `tests/.../test_contracts_declarations.py:373` 仍是原句；`git log -S'中文名'` 显示该措辞最后一次改动在 `62a8c9a` |
| L5 清单第 14 项容错策略 | ⏸ 按计划推迟 | 随 S11 补 —— 符合 S2 报告的建议 |

**结论**：12 项里 9 项已修、1 项半修（M1）、1 项漏修（L4）、1 项按计划推迟。**S3 的代码本身没有把 S2 的遗留问题带进来**（`types.py` 是新建的，`events.py` 的改动只是引用方式）。

---

## 7. 建议的处置顺序

1. **S6 之前必须解决**：M1（改文法规则 1 + 形态 2 的定义；趁基线未生成，成本是文档级）。
2. **S4 一并处理**：M2（`CommandSpec` 加必填的 `lifecycle_gated`，拒绝原因由它派生）、M3（`hold` 的空槽语义写进 doc + PROGRESS 待办）。
3. **S4/S5 顺带**：L3（事件侧 `commandType` 取值域 = `COMMAND_TYPES` 的断言）、L2（`COMMAND_ARGS_TYPE` 的去留）、L1（未知命令名的错误处理）。
4. **随手改**：L4（`ARGS_VALUE` 余量说明）、L5（测试 docstring + 日志里"14 项"改 12、"三处改准"改"两处"）。

---

*本报告由 `open-code-review-delegate` 委托模式产出：OCR 负责文件选择与规则解析，审查判断由 Mavis 完成。临时校验脚本已删除，工作区无残留。*
