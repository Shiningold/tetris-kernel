# S2 阶段代码审查报告（委托模式）

> **审查对象**：提交 `62a8c9a`「新功能：新增 44 条事件的机器可读声明」——M0 阶段 B 的 S2。
> **审查方式**：`ocr delegate` 委托模式（OCR 只做文件选择与规则解析，审查由本 Agent 完成）。
> **审查日期**：2026-10-23
> **审查基线**：需求 `docs/需求与边界.md` §8 · 架构 `docs/项目架构.md` §4.4 / §5.1 / §6 / §7.6 · 契约清单 `docs/契约v1清单.md` · 计划 `docs/plan-M0.md` S2 / §5.3 / §5.4

---

## 1. 结论

**这份声明可以进基线，但有两处"现在不定、以后就是 major 变更"的空洞，建议在 S3 开工前补掉。**

- 44 条事件的名字、命名空间、产出者与需求 §8.1 **逐条对得上**，产出者分布（内核 35 · 总线 1 · host 2 · 输入适配器 2 · 渲染器 1 · 日志/指标/成就各 1）**实测一致**；信封五字段、元数据三键与需求 §8 / ADR-062 一致。
- 四条质量门全绿：`pytest` 53 passed · `ruff check` 全过 · `pyright src/tetris_kernel` 0 错 · `lint-imports` 4 条契约全 kept（`contracts/` 确实不引任何包外模块）。
- 值得处理的是 **1 项高**（契约版本号 v1 没进机器可读声明，而计划把它排在 S2）、**5 项中**（类型文法自相矛盾、两处测试覆盖空洞、`piece:held` 取值域表达不了"槽空"、同名字段跨事件不同义）、**5 项低**（文档措辞与口径小问题）。

**没有发现**的问题（已逐项查过，不重复列出）：事件重名、命名空间与类型名前缀不一致、产出者代号无中文名、`args` 漏禁浮点、模块级可变状态、`contracts/` 越层依赖、公开 API 泄漏未导出符号。

---

## 2. 覆盖统计

| 项 | 值 |
| --- | --- |
| total_files（preview 检出） | 3 |
| reviewed_files | 3 |
| skipped_files | 0 |
| coverage_rate | 100% |

| 文件 | 状态 | 行数 | OCR 判定 |
| --- | --- | --- | --- |
| `src/tetris_kernel/contracts/events.py` | 新增 | +663 | reviewable |
| `src/tetris_kernel/contracts/__init__.py` | 修改 | +35 | reviewable |
| `tests/test_contracts_declarations.py` | 新增 | +180 | **excluded（`default_path`）——本报告仍逐行审查了它**，理由：它是 S2 的一部分，也是清单第 1/2/7/8 项唯一的机检担保 |

**OCR 命令留痕**：`ocr delegate preview --format json -c 62a8c9a` → `ocr delegate rule --format json <3 个路径>`（规则组 `system`，覆盖 `**/*.{py,pyi,ipynb}`）。

---

## 3. 发现清单

### 3.1 High

#### H1 · 契约版本号 v1 没有进入机器可读声明 —— 清单第 13 项漏在 S2

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/events.py`（模块级常量缺失） |
| category | bug / other |
| severity | high |

**问题**：契约清单第 13 项「契约版本号 `v1`，版本升级 = major 变更」判定为**机检**，而 `docs/plan-M0.md` §5.3 明确把它的验证阶段写成「**S2（声明里带版本）**」。但 `contracts/events.py` 里**没有任何版本常量**——`src/` 全目录 grep `VERSION` / `CONTRACT` 零命中，版本号只以散文形式出现在模块 docstring 的「契约 v1」四个字里。清单第 13 项状态仍是「☐ 文档已定稿，待声明」。

**为什么是问题**：S6 的基线脚本要判「这次改动是不是 major 变更」，版本号必须能从声明里读到，而不是从文档散文里读。架构 §5.5 也要求录像头写版本、存档携带版本（FR-6.1 / FR-10.1）——这些版本值最终都要与声明里的版本号对齐，今天没有机器可读的来源，S17 冻结时就得回头补，而那时候补会撞基线。

**建议**：在 `contracts/` 加一个包级常量（如 `CONTRACT_VERSION = "v1"`，放 `contracts/__init__.py` 或独立 `contracts/version.py`），并在 S6 的基线里把它一并纳入；同步把清单第 13 项的状态从「待声明」改成「已声明」。

---

### 3.2 Medium

#### M1 · 声明文件自称的类型文法，认不出它自己用的类型串

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/events.py` |
| start_line / end_line | 17–26（文法表） |
| category | documentation / maintainability |
| severity | medium |

**问题**：模块 docstring 用一张**封闭表**定义了类型表达式的文法（标量 · `T | None` · 定长元组 · 变长元组 · 嵌套元组 · 递归元组），并明说「**只有这几种形态，禁止自由发挥**」。但实现里出现了表外的形态：

| 实际写法 | 出现位置 | 表里有没有 |
| --- | --- | --- |
| `float \| int` | `events.py:350`（`rng:next.value`） | ❌ 表里只有 `T \| None` |
| `tuple[tuple[str, int \| float], ...]` | `events.py:617`（`metric:sampled.metrics`） | ❌ 元组元素位上的联合 |
| `tuple[tuple[str, int \| str \| bool \| None \| tuple[int, ...] \| tuple[tuple[int, int, int], ...]], ...]` | `events.py:51/54`（`ARGS_VALUE` / `ARGS`） | ❌ 六元联合嵌在元组元素位 |
| `tuple[...]`（递归形态） | `events.py:46`（`JSON_VALUE`），用于信封 payload / metadata | ⚠️ 表里说递归形态「**仅用于开放取值域**」 |

**为什么是问题**：提交信息写明「基线校验脚本要能解析它（S6）：字符串可直接正则解析」——**docstring 里那张文法表就是 S6 正则的规格书**。规格书认不出现有的类型串，只有两种结局：S6 的正则漏匹配（声明看着在基线里、实际没被哈希，等于没进基线），或者 S6 回头改声明（撞「只增不改」基线）。两种都应该在 S3 开工前解决，而不是留到 S6。

**建议**：二选一——① 把文法表扩成能覆盖实际写法（增加「标量联合 `T1 | T2`」与「元组元素位可为联合」两行，并说明递归形态的适用范围）；② 把类型串规范化到现有文法内（例如 `float | int` 写成 `int | float` 之外的写法并不存在更好的选项时，倾向于 ①）。改文法表是文档改动，不动基线，成本最低。

#### M2 · 清单第 1 项要求「五字段**及其类型**」，测试只断言了字段名

| 字段 | 值 |
| --- | --- |
| path | `tests/test_contracts_declarations.py` |
| start_line / end_line | 162–170 |
| category | test |
| severity | medium |

**问题**：`test_envelope_fields` 只断言 `ENVELOPE_FIELDS` 的**名字**是 `seq / frame / type / payload / metadata`，**类型一个字都没断言**。而契约清单第 1 项的原文是「信封结构：五字段 `序号 / 帧号 / 类型 / 负载 / 元数据` **及其类型**」，判定为**机检**。对照同文件的 `test_metadata_keys`（第 173–180 行）——元数据三键是**连类型一起断言**的。信封漏了。

**为什么是问题**：信封的 `payload` / `metadata` 两字段类型恰好是全文件最复杂的两个串（展开了 `JSON_VALUE`），也正是 S6 基线要哈希的东西。把 `metadata` 的类型从 `tuple[tuple[str, JSON_VALUE], ...]` 改成别的，今天的测试**全绿**，而基线会在 S17 冻结那天第一次变红——那时候已经晚了。

**建议**：把 `test_envelope_fields` 改成同时断言 `name → type` 映射（与 `test_metadata_keys` 写法对齐）。

#### M3 · S2 的验收点「关键负载字段与类型」没有任何断言守护

| 字段 | 值 |
| --- | --- |
| path | `tests/test_contracts_declarations.py` |
| start_line / end_line | 136–145 |
| category | test |
| severity | medium |

**问题**：`plan-M0.md` S2 任务第 2 条写的是「逐条对齐 `docs/需求与边界.md` §8.1 的 44 行（**类型名、产出者、关键负载字段与类型**）」。但测试只对齐了类型名、命名空间、产出者三项；**负载字段**只查了三件事——非空、字段名不重复、`type` 字符串非空（`assert field.type`）。字段**名**和**类型**对不对需求 §8.1 的「关键负载」列，全靠人眼。

**为什么是问题**：这份声明的主体内容就是负载字段，需求 §8.1 的「关键负载」列是它的对照源。现在删掉 `board:lines-cleared.classKey`、把 `piece:locked.cells` 的类型从 `COORD_CELLS` 改成别的、去掉 `state:changed.piecesPlaced`，测试**全部照过**。`plan-M0.md` §5.4 给 S2 定的反向验证只有一条「删掉一条事件声明 → 44 断言红」，字段级的漂移没人看守。

**建议**：在测试里补一张 `EXPECTED_PAYLOAD: dict[str, dict[str, str]]`（事件 → 字段名 → 类型），逐条断言字段名集合与类型；这样字段级改动会立刻变红。

#### M4 · `piece:held` 的 `taken` 表达不了「暂存槽原本是空的」

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/events.py` |
| start_line / end_line | 453–462（`taken` 字段在 461 行） |
| category | bug |
| severity | medium |

**问题**：`piece:held` 声明为 `PayloadField("taken", "str", "取出暂存槽的方块类型")`。**首次暂存时暂存槽是空的**，这时「取出暂存槽的方块类型」没有任何值可写，但类型只允许 `str`——既不允许 `None`，doc 也没写「槽空时取空串」这类约定。

**为什么是问题**：清单第 7 项说负载字段声明是机检项，这份声明会被哈希进「只增不改」基线。M1 落地时如果用 `None` 表示槽空，类型串一变就是 **major 变更**，要重新走显式更新流程 + 写明理由；如果用空串，那也是一条**没写进契约**的约定——契约基线哈希的是声明，约定不写下来就等于没有。

**建议**：现在定死其中一种——① `taken` 改成 `str | None`；② 保留 `str` 但在 doc 里写明「槽为空时取空串」。哪种都行，但必须现在定，定在 S3 之前。

#### M5 · 同名负载字段跨事件类型不同义、不同型

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/events.py` |
| category | maintainability |
| severity | medium |

**问题**：实测有几组字段名在**不同事件里含义/类型不同**：

| 字段名 | 出现处 A | 出现处 B |
| --- | --- | --- |
| `level` | `game:started` = `int`（开局等级） | `log:entry` = `str`（日志级别） |
| `cause` | `piece:moved` / `piece:locked` / `board:changed` / `score:changed` = `str`（成因：command / gravity / lock…） | **元数据** `cause` = `tuple[int, int] | None`（命令追溯标识） |
| `rows` | `board:lines-cleared` = `tuple[int, ...]`（被消行的行号） | `board:garbage-received` = `int`（注入行数） |

**为什么是问题**：`PayloadField` 的 docstring 自己写着「**字段名即契约**，改名 = major 变更」，需求 §8.1 也立了「新增事件必须归入既有命名空间」的纪律。消费者若按字段名统一处理（回放校验、事件完备性检查、指标的通用提取），`level` 与 `cause` 会直接踩坑。尤其 `cause`：**负载里的 `cause` 是成因字符串，元数据里的 `cause` 是 (帧号, 帧内序号)**，同名不同义，差一个数量级的误解风险。

**建议**：不必全改（改名 = major，越晚越贵）。最低限度是在**声明文本里消歧**：把载荷成因的 doc 写成「成因字符串（枚举：command / gravity / softDrop…），**与元数据 `cause`（命令追溯标识）无关**」；`level` 在 `log:entry` 的 doc 里写明「日志级别（info / warn / error），非游戏等级」。若要彻底解，靠改名——那要趁基线还没冻结（S17）做。

#### M6 · 跨阶段共用的取值域常量寄居在 `events.py`，与 S3/S4 的来源归属没定死

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/events.py` |
| start_line / end_line | 45–63（`JSON_VALUE` / `ARGS_VALUE` / `ARGS` / `SCALAR_MAP` / `CELLS` / `COORD_CELLS`） |
| category | maintainability |
| severity | medium |

**问题**：`ARGS_VALUE` / `ARGS` / `CELLS` / `SCALAR_MAP` / `COORD_CELLS` 正是 **S3 命令参数规格**与 **S4 快照字段**要用的类型域，现在定义在 `events.py` 里，并从 `contracts/__init__.py` 再导出。`PROGRESS.md` 已写「`ARGS_VALUE` 已在 S2 定好，S3 只需保证命令规格里的参数类型都落在它的取值域内」——**单点来源是有意为之，这点做得好**。问题在于**基线的归属**没定：清单第 12 项要求「基线粒度按字段级哈希，键 = 字段名、值 = 类型声明哈希」，第 5 项要求「16 个命令的参数规格」进基线。同一个类型串既出现在常量里、又会被 S3 的命令字段声明引用，基线该哈希哪一个、要不要哈希两次，S6 之前必须写死。

**建议**：在 S6 之前把这条写进架构 §4.4 / 清单第 12 项：**哈希的是"声明处引用的类型串"，常量定义本身不进基线**（否则改一个常量的排版就撞基线）。S3/S4 直接复用这些常量，不要复制字面量。

---

### 3.3 Low

| # | path | 行 | 问题 | 建议 |
| --- | --- | --- | --- | --- |
| L1 | `src/tetris_kernel/contracts/events.py` | 28–32 | **docstring 与实现自相矛盾**：docstring 说「事件负载不受禁浮点约束——`render:frame` 的耗时、`metric:sampled` 的聚合值本来就带小数，按需求原文用 `float`」，但 `render:frame` 实际声明的是 `durationUs: int`（「微秒整数，避免浮点」），`metric:sampled` 实际是 `tuple[tuple[str, int | float], ...]`。**被点名的两个例子都不是 float。** | 改 docstring：说明 `render:frame` 改用微秒整数表达耗时、`metric:sampled` 用 `int | float` 逐项混合；禁浮点只约束命令 `args` 这条**结论不变** |
| L2 | `src/tetris_kernel/contracts/events.py` | 561–581 | `state:changed` 的字段名与快照（需求 §8.3）**对不上**：快照叫 `active`（`Active | None`，含 pieceType/rotation/x/y），事件里叫 `activeType: str | None`（只有类型，丢了朝向与坐标）；快照有的 `rule`（规则集标识，FR-6.1/FR-10.1 要求录像与存档携带）在事件里也没有。需求 §8.1 对 `state:changed` 只写「轻量摘要（不含整张棋盘）」，**不算违规**，但按字段名对齐快照的机器会踩空。 | 与需求确认：`state:changed` 是否要带 `rule`、活动方块要不要给结构化字段。要么补齐，要么在 `state:changed` 的 doc 里写明「本事件是快照的子集投影，字段名自成一套」 |
| L3 | `src/tetris_kernel/contracts/events.py` | 4–5 | 「没有任何 dataclass 之外的运行时对象被构造出来」与实际不符——`EVENT_TYPES` 就是 `MappingProxyType(dict(...))`，构造了一个 dict 运行时对象。本意应是「不构造行为对象，只做声明」。 | 改成「只构造声明用的 dataclass 与只读映射，不构造任何行为对象」 |
| L4 | `tests/test_contracts_declarations.py` | 124–127 | `test_producer_labels_cover_all_producers` 的 docstring 说「每个产出者代号都有中文名」，但 `"host"` 的中文名就是 `"host"`（与需求 §8.1「产出者」列字面一致，**值是对的**）。 | docstring 改成「每个产出者代号都有对应名称（host 在需求里就是 host）」 |
| L5 | `docs/契约v1清单.md` 第 14 项 | — | 清单第 14 项「未知事件 / 未知元数据键的容错策略」验证阶段写的是「S2 + S11」，但 S2 这半边**只有散文**（`ENVELOPE_FIELDS` 的 docstring 里一句「未知键静默保留但不解释」），没有 S6 脚本可读的声明（例如「已知事件类型集合 = `EVENT_TYPES` 的键，未知者按标准信封解析后丢弃」）。该项是人检项，不阻塞。 | S11 实现容错时，把「已知类型集合」与「未知键保留策略」补成可被 S6 读取的声明，一并勾掉第 14 项 |

---

## 4. 与 S2 验收标准逐条对照

`docs/plan-M0.md` §4「S2 · 事件契约声明（44 条）」任务清单：

| # | 任务 | 判定 | 证据 |
| --- | --- | --- | --- |
| 1 | 建 `contracts/events.py`：注册表（键 = 事件类型名，值 = 负载字段声明 + 产出者 + 命名空间） | ✅ | `events.py:143`（`_EVENT_SPECS`）+ `:639`（`EVENT_TYPES`），`EventSpec` 三字段齐（`events.py:84-93`） |
| 2 | 逐条对齐需求 §8.1 的 44 行（类型名、产出者、关键负载字段与类型） | ⚠️ **部分** | 类型名 / 产出者 / 命名空间 ✅ 实测 44 条逐条相等；**关键负载字段与类型无断言守护**（见 M3） |
| 3 | 产出者分布核对（内核 35 · 总线 1 · host 2 · 输入适配器 2 · 渲染器 1 · 日志/指标/成就各 1） | ✅ | 实测 `Counter({'kernel': 35, 'bus': 1, 'host': 2, 'input-adapter': 2, 'renderer': 1, 'log': 1, 'metrics': 1, 'achievements': 1})`，与 `tests/.../test_producer_distribution` 一致 |
| 4 | 负载字段声明用中性类型；禁浮点只对命令 `args` 生效 | ✅ | `ARGS_VALUE`（`events.py:51`）无 `float`；事件负载确有浮点（`events.py:350`、`:617`）。**但文档表述与实现不符**（见 L1） |
| 5 | 在 `contracts/__init__.py` 导出注册表（公共叶子，不 import 任何东西） | ✅ | `__init__.py` 只 `from .events import ...`；`lint-imports` 4 条契约全 kept |
| 6（清单） | 契约清单第 1 / 2 / 7 / 8 项 | ⚠️ | 第 1 项缺类型断言（M2）；第 13 项版本号没声明（H1）——后者按 `plan-M0.md` §5.3 归属 S2 |
| 7（反向验证） | 删掉一条事件声明 → 事件数断言（44）变红 | ✅ | `test_event_registry_size` + 集合相等断言可捕获；但**字段级破坏不会变红**（M3） |

---

## 5. 跑过的检查

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 单元测试 | `pytest` | ✅ 53 passed in 0.60s |
| Lint | `ruff check .` | ✅ All checks passed |
| 类型 | `pyright src/tetris_kernel` | ✅ 0 errors, 0 warnings |
| 依赖方向 | `lint-imports` | ✅ 4 kept, 0 broken |
| 声明一致性 | 自查脚本（临时，已删） | 44 事件 / 16 命名空间 / 8 产出者 / 14 种类型串，全部符合需求 §8.1 |

**未验证的部分（说清楚，不含糊）**：

- S6 的基线脚本**尚未落地**，因此「这份声明能不能被基线脚本正确哈希」**没有实测**——M1（文法表认不出实际类型串）正是基于此提的隐患。
- 事件负载字段与需求 §8.1「关键负载」列的**逐字段语义对齐**是人工核对的（类型名/命名空间/产出者三项是脚本核对的）；核对结果：44 条的**关键负载项全部覆盖**，另有若干扩展字段（如 `kernel:ready.rule`、`kernel:system:removed.clean`、`kernel:error.eventSeq`、`piece:moved.blocked`）属自足性补充，符合提交说明的口径。
- 运行时行为（事件真的按这些字段发出）属 S7–S14，不在本次范围内。

---

## 6. 建议的处置顺序

1. **S3 开工前**：H1（加 `CONTRACT_VERSION`）、M1（补文法表）、M4（定 `piece:held.taken`）、M5（消歧 doc）——这四条都属于「现在改是文档级成本，以后改是 major 变更」。
2. **S6 之前**：M2、M3（补测试断言）、M6（基线归属写进架构 §4.4 / 清单第 12 项）。
3. **随手改**：L1–L4（纯措辞与文档一致性）。L5 随 S11 一起补。

---

*本报告由 `open-code-review-delegate` 委托模式产出：OCR 负责文件选择与规则解析，审查判断由 Mavis 完成。临时脚本已删除。*
