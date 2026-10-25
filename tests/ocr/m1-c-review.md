# 批次 C 代码审查报告 · S11 内核三条改动 · S12 宿主跑到顶出 · S13 参考策略与九道门

> **轮次**：第 1 轮（首审）
> 审查范围：`0c99d90..feature/m1-batch-c`（批次 B 的落笔提交 → 批次 C 落笔提交，含 **13 笔**阶段 / 收尾提交 + **11 笔** `--no-ff` 合并 / 回合并）
> 审查方式：`open-code-review-delegate`（OCR 只做文件选择与规则解析，**判断全部由宿主 Agent 完成**）
> 审查基线：**`bc5323f`**（`feature/m1-batch-c` HEAD）
> 审查日期：2026-10-25
>
> **基线说明**：审查期间**另一个会话在同一工作区并发推进 S14（批次 D）**——中途把工作区分支切到了
> `feature/m1-batch-d`，并刷了两份基线（`d816a73` 冻结区内容哈希 · `e326791` 逐帧事件数）。
> 本报告的**全部结论都按 `feature/m1-batch-c` 的提交态复算**，不受该并发影响；受影响的一次观察偏差记在 §4.2。

---

## 1. 结论

**批次 C 的功能实现没有查出缺陷。** 内核侧三条改动（软降计分归规则集 · 锁定延迟 · 顶出判定）、宿主侧"跑到顶出"、测试侧"参考策略 + 九道门"，逐条与 spec 对齐并**独立复跑核实**通过。查出 **5 项，全部落在文档表述 / 提交规范上**。

| 项 | 结论 |
| --- | --- |
| **发现总数** | **5 项**：高 **0** · 中 **1** · 低 **4** |
| **最要紧的一条** | **M-1** —— `RunReport` 加了**三个必填字段**，而 docstring 明写"**按位置构造的既有调用方不受影响**"：**实测反着**（按位置构造直接 `TypeError`）。仓内零影响，但那是一句**假话**，且该笔提交按 AGENTS.md 的判据**属于破坏性改动**却标成了 `新功能` |
| **内核改动** | K1 软降计分归规则集（替身规则集 5 分/格当场生效，写死 `+1` 的内核会报红）· K2 锁定延迟（落地帧 1 → 锁定帧 31，**相隔 30 帧** ✓；重置计数 **1..15** 后不再重置、照常锁定）· K3 顶出判定 + 出生位置按各自盒子居中（**O = 4**，其余 = 3） |
| **门判据** | 九个入口逐个复跑命中 **6 / 8 / 8 / 8 / 5 / 2 / 3 / 3 / 2**（与 `PROGRESS.md:61` 逐字一致）；G1-2 / G1-3 的失效入口已由 `cde6769` 绑回用例名，**元守卫已收紧到只认 §5.2 表格行**（`a840d5d`） |
| **手算三层** | seed=4 一局 **2546 帧 / 75 块 / 14 行 / 1900 分 / 2 级**；**本报告自己写的**计分表与公式重算 = **1900 / 14 / 2**，与内核结算**逐项相等**；10 条消行序列与用例里的手抄表**逐条相等** |
| **契约面** | `contracts/` **一字未动** ✓；事件仍 **44** 条 · 命令仍 **16** 条 · 快照字段集合不变 ✓；契约基线 **360 条**逐条相等 ✓ · 期望清单与基线逐条相等 ✓ · 契约清单 **16 项**全勾 ✓ |
| **质量门** | ruff check ✓ · ruff format **72 files** ✓ · pyright **0 errors** · lint-imports **4 kept / 0 broken** · 内核纯净度 **0 违规** |
| **批次 C 交付点的红** | **2 条**（冻结区基线 + 逐帧事件数基线），**都是计划内**——按计划留到 S14 随 M1-C3 一起刷，理由一句话说得清："S11 改了 `kernel/` 的玩法与帧行为 → 撞的是这两份基线" |
| **越界** | 批次 C 的 diff **未碰 `contracts/`** ✓；帧推进五步的顺序 / step 化结构 / 命令轮次语义 / 配额与防抖 / phase 门禁**一律未动** ✓；`systems/` · `variants/` · `adapters/` 仍为空壳 ✓ |

**为什么没有「高」**：5 项都不损坏数据、不让已冻结的契约分叉、不改变任何一条门的行为。最坏的情况是**一句关于兼容性的假话**（M-1）与**四处关于"pytest 退出码"的前提写错**（L-1）——都是**读者会照着做决定**的措辞问题，不是运行时缺陷。

---

## 2. 审查范围与覆盖率

### 2.1 OCR 选取结果

`ocr delegate preview --format json --from 0c99d90 --to feature/m1-batch-c`：

| 项 | 值 |
| --- | --- |
| mode / merge_base | `range` / `0c99d90`（`feature/m1-batch-b` 的 tip） |
| total_files | **20** |
| reviewable_count | **7** |
| excluded_count | **13**（12 × `default_path` · 1 × `unsupported_ext`） |
| total_insertions / deletions | **3096 / 158** |

### 2.2 覆盖率（**强制项**）

| 项 | 值 |
| --- | --- |
| `total_files` | **20** |
| `reviewed_files` | **20** |
| `skipped_files` | **0** |
| `coverage_rate` | **100%** |

**OCR 排除的 13 个文件也全部审了**——排除只是 OCR 的文件类型规则，不是审查边界：

- **9 个测试文件**（`default_path` 排除）——**按代码审**：`test_m1_gates.py`（904 行）· `test_m1_kernel.py`（462 行）· `test_host_loop.py`（+225 行）· `test_frame_model.py`（+113 行）· `test_rules_m1.py`（23 处用例改名）· `test_command_channel.py` · `test_determinism.py` · `test_kernel_lifecycle.py` · `test_repo_text_hygiene.py`（新增 119 行）
- **4 份文档**（`unsupported_ext` 排除）——**按一致性审**：`docs/plan-M1.md`（+349 行）· `PROGRESS.md` · `ai_docs/261003.md`（+136 行）· `docs/spec-M1.md`

**7 个 OCR 判为可审的文件**：`src/tetris_kernel/host/cli.py`（+114）· `src/tetris_kernel/host/loop.py`（+25）· `src/tetris_kernel/kernel/__init__.py`（+4）· `src/tetris_kernel/kernel/core.py`（+172）· `tests/support/__init__.py`（新增 24）· `tests/support/driver.py`（新增 200）· `tests/support/policy.py`（新增 292）。

### 2.3 审查的 13 笔非合并提交

```
1171851 新功能：内核软降计分改走规则集并补齐锁定延迟与顶出判定
c6fe21f 文档：同步 S11 的留痕与进度
abda94f 新功能：宿主支持无头跑到顶出并输出结算
99ac83b 文档：同步 S12 的留痕与进度
bcad655 测试：新增 M1 九道门判据与确定性参考策略
7d50677 文档：同步 S13 的留痕与进度
30099a5 文档：同步批次 C 的合并前进与状态
cde6769 修复：把 §5.2 的两个失效门入口绑回用例名
40902d9 文档：补记 S13 的门入口失效发现与处置
a840d5d 修复：让元守卫只认 §5.2 表格行
e1796f4 修复：修回三处被编码错配打散的中文
ffd2c15 测试：新增仓库文本的编码卫生守卫
e071e96 文档：同步 S13 收尾的留痕与状态
```

---

## 3. 发现明细

### 3.1 中

---

#### M-1　`RunReport` 加了三个必填字段，docstring 的兼容性声称与事实相反

- **path**：`src/tetris_kernel/host/loop.py`
- **start_line**：64
- **end_line**：76
- **category**：documentation
- **severity**：medium

**content**：

docstring 写：

> **M1 只加字段，不改已有字段的含义**（plan-M1.md S12 段）：前 7 项是 M0 的口径，一字未动；新加的三项排在**末尾**，**按位置构造的既有调用方不受影响**。

**这句话的后半句是错的。** `RunReport` 是 `@dataclass`，加了 `combo: int` / `b2b: bool` / `level: int` 三个字段且**一个默认值都没有**——按位置构造的老调用方**直接炸**：

```
>>> RunReport(1, 2, 'fp', 0, 0, 0, 'running')
TypeError: RunReport.__init__() missing 3 required positional arguments: 'combo', 'b2b', and 'level'
>>> [f.name for f in dataclasses.fields(RunReport)]
['frames', 'events', 'fingerprint', 'score', 'lines', 'piecesPlaced', 'phase', 'combo', 'b2b', 'level']
>>> [(f.name, f.default) for f in dataclasses.fields(RunReport) if f.default is not dataclasses.MISSING]
[]
```

**影响面**：仓内唯一构造点是 `host/loop.py:166`，**用的是关键字** → **仓内零影响**，现有用例全绿。但：

1. 这是一句**关于兼容性的假话**，且写在**公开数据结构**的 docstring 里——外部调用方按它做决定就会踩。
2. 按 `AGENTS.md` 的判据（"破坏性改动……**会不会让旧录像、旧存档或既有调用方失效**"），**给必填 dataclass 加字段 = 让既有调用方失效** → `abda94f` 这笔**应当标 `新功能!`**，实际标的是 `新功能`。仓库的先例是同一把尺子：`ab6211d 新功能!：兑现重力间隔并给契约增补重力计时字段`（M0 的同类内核行为变更）、`d2e207f 新功能!：规则接口新增软降计分签名`。

**修法（二选一）**：

- **A（真兼容）**：三个新字段给默认值 —— `combo: int = 0` / `b2b: bool = False` / `level: int = 1`。老调用方按位置构造照旧能跑，新字段仍是"从快照取值覆盖"。
- **B（认账）**：保留现状，把 docstring 那句改成"**按关键字构造的既有调用方不受影响**；按位置构造的会因缺 3 个必填参数报 `TypeError`"，并在 `docs/plan-M1.md` §5.12 补一条"提交类型标注的偏差"留痕（与 §5.11–§5.13 同体例）。

---

### 3.2 低

---

#### L-1　"`pytest -k` 选不到用例时退出码是 0" —— 实测是 **5**，六处前提写错

- **path**：`docs/plan-M1.md`
- **start_line**：828
- **end_line**：1212
- **category**：documentation
- **severity**：low

**content**：

六处都写着"pytest 对'一条都没选到'照样退出 **0**（它只在'选中了、但有失败'时才非 0）"：

| 位置 | 上下文 |
| --- | --- |
| `docs/plan-M1.md:828` | §5.2 表下的注（守卫存在的那条理由） |
| `docs/plan-M1.md:1199` | §5.13 第 2 条（发现记录） |
| `docs/plan-M1.md:1212` | §5.13 第 2 条末尾的**「教训」** |
| `tests/test_m1_gates.py:856` | 元守卫用例的 docstring |
| `PROGRESS.md:63` | 「九道门的九个复跑入口现在全部活着」 |
| `ai_docs/261003.md` | 20:20 条目（「发生了什么」+「踩过的坑」各一处） |

**实测（pytest 9.1.1）**：

```
> pytest -q -k zzz_definitely_no_such_test
821 deselected in 0.73s
exit=5
```

退出码是 **5**（pytest 的 `EXIT_NOTESTSCOLLECTED`），**不是 0**。

**要分清两件事**：

- ✅ **结论是真的**："G1-2 / G1-3 的两个命中名从来没绑定过任何用例 → 两道门从入口上消失、没人发现"——这是实测事实，处置（绑回用例名 + 元守卫）也对。
- ❌ **机制解释是错的**：pytest **会**用非 0 退出码把"一条都没选到"报出来，只是那个码是 **5**（"没收集到用例"），不是失败码 1，**也不会告诉你哪个门死了**。

**为什么仍要改**：这句被当成**教训**写进了 `ai_docs` 与 `PROGRESS`——下个里程碑会照着"pytest 不会发现"这个前提做决定（例如把守卫当唯一防线、或者反过来觉得"pytest 反正会报"就把守卫删了）。**守卫本身该留**（它给的是精确点名 + 契约绑定），但**理由要换成实测值**。

**修法**：六处统一改成——"**退出码 5（no tests collected）**：CI 里**能**被发现，但诊断只说'没收集到用例'，不会告诉你**哪个门**死了；所以另配元守卫（读 §5.2 的表 → 逐条点名）"。

---

#### L-2　提交 `1171851` 的类型应是 `新功能!`

- **path**：`src/tetris_kernel/kernel/core.py`
- **start_line**：147
- **end_line**：1795
- **category**：other
- **severity**：low

**content**：

`1171851 新功能：内核软降计分改走规则集并补齐锁定延迟与顶出判定` 这一笔同时改了：

1. **内核每帧的事件构成** —— 锁定延迟把"落即锁"换成"30 帧后锁"，逐帧事件数基线**必然变**（spec §6.6 明写）；新增两条已登记的 `piece:lock-delay-*` 事件；
2. **公开常量 `SPAWN_X` 被删** —— `from tetris_kernel.kernel.core import SPAWN_X` 的调用方直接 ImportError（仓内已无引用，`__all__` 里本来也没有）；
3. **出生列变了** —— O 从 `x=3` 改成 `x=4`；
4. **锁定时机变了** —— 落即锁 → 30 帧。

按 `AGENTS.md` 的判据，这几条都落在"**会不会让旧录像、旧存档或既有调用方失效**"这一侧 → **应带 `!`**。仓库的先例是同一把尺子：`ab6211d 新功能!：兑现重力间隔并给契约增补重力计时字段`（M0 的同类内核行为变更）、`d2e207f 新功能!：规则接口新增软降计分签名`、`51cbdc1 重构!：契约取值域统一收口为字面量枚举`。

**修法**：提交已合并 → 按 `docs/plan-M1.md` §5.11–§5.13 的体例补一条"提交类型标注的偏差"留痕即可（不必改历史）。

---

#### L-3　spec §7.9 的"内核三条改动"与实际的第四处内核改动对不上 —— **spec 自身不自洽**

- **path**：`docs/spec-M1.md`
- **start_line**：430
- **end_line**：454
- **category**：documentation
- **severity**：low

**content**：

§7.9「内核侧配套改动」表只列 **K1 / K2 / K3** 三条，表下明写：

> **三条之外，内核不许动**。特别是**帧推进五步的顺序、step 化结构、命令轮次语义、配额与合并防抖、phase 门禁**——M1 一律**只读不改**。

但 S11 实际改了**四处**内核行为——第四处是**出生位置按各自盒子居中**：`kernel/core.py:1795` 新增 `_spawn_x()`（按占位列的跨度居中），并**删掉了常量 `SPAWN_X`**。

而 §7.7 自己写着：

> **出生位置**：7 种方块的水平居中位置按各自盒子算（I 用 4×4 盒居中，其余 3×3，O 用 2×2）——**随 §7.1 的形状表逐条验证**。

**所以这是 spec 内部的自相矛盾**：§7.7 要求它，§7.9 的表却不许内核动。**处置本身是对的**——`docs/plan-M1.md` §5.11 把它记成了**用户裁定的"一起摆正"**（"M0 的单一常量 `SPAWN_X = 3` 没满足 spec——O 用 2×2 盒，居中后落在 x = 4"），实现也照此做了（实测 O = 4，其余 = 3）。**缺的只是 spec §7.9 的表没同步**。

**修法**：§7.9 的表补一行 **K4（出生位置按各自盒子居中，§7.7）**，或把 K3 的描述改成"顶出判定确认（出生重叠即结束）+ **出生位置按各自盒子居中**（§7.7）"。

---

#### L-4　"30 帧"与"31 帧"两个数并排出现，措辞易被读成时长

- **path**：`docs/plan-M1.md`
- **start_line**：1117
- **end_line**：1117
- **category**：documentation
- **severity**：low

**content**：

**实测**（本报告自写探针）：落地帧 **1** → 锁定帧 **31**，**相隔 30 帧** ✓ 满足 spec §7.5 的"锁定延迟时长 **30 逻辑帧**（0.5 秒 @ 60fps）"。

写得最准的一处是 `tests/test_m1_gates.py:461`：

> **口径**（spec §7.5）：落地那一帧**起算但不消耗**计时 → 锁定帧与落地帧**相差 30**（含落地那一帧算进去是 31 帧）。

但另外三处都写成"**落地到锁定共 31 帧**"：`docs/plan-M1.md:1117` · `PROGRESS.md:16` · `tests/test_m1_kernel.py:253`。"共 31 帧"是**含头含尾的帧数**、**不是时长**——读者容易把 31 当成 0.517 秒，或者反过来以为内核多等了 1 帧。

**修法**：统一成"**落地到锁定相隔 30 帧**（含落地帧共 31 帧）"。

---

## 4. 实跑复核记录

> **纪律**：下面每一项都是本报告**自己复跑**的，**不采信用例的断言**（退出码是被测代码自己算的）。

### 4.1 批次 C 交付点的门禁

| 项 | 实测 | 判定 |
| --- | --- | --- |
| pytest | **772 passed · 44 skipped · 2 failed** | 与 `PROGRESS.md:61` 记录**逐字一致** ✓ |
| 红 ① | **冻结区基线** —— 用批次 C 自己的 `kernel_hashes.json` 校验当前内核：**14 个文件里 8 个变了** → exit 1 | **计划内**（S14 刷）✓ |
| 红 ② | **逐帧事件数基线** —— "基线同口径 6100 帧，当前 7381 帧" → exit 1 | **计划内**（S14 刷）✓ |
| ruff check | All checks passed | ✓ |
| ruff format --check | **72 files** already formatted | ✓ |
| pyright | **0 errors**, 0 warnings, 0 informations | ✓ |
| lint-imports | **4 kept, 0 broken** | ✓ |
| 内核纯净度 | 扫 2 个目录 · 违规 **0** · 警告 **0** | ✓ |
| 契约基线 | **360 条**：新增 0 · 变化 0 · 消失 0 | ✓ |
| 契约清单 | **16 项全勾** | ✓ |
| §5.2 九个入口 | **6 / 8 / 8 / 8 / 5 / 2 / 3 / 3 / 2**（逐个 `--collect-only` 复跑） | 与 `PROGRESS.md:61` 一致 ✓ |

### 4.2 一次观察偏差（并发造成，不影响结论）

审查期间另一个会话在同一工作区做 S14（批次 D）：中途把分支切到 `feature/m1-batch-d` 并刷了两份基线（`d816a73` / `e326791`）。因此**我第一次全量 pytest 只看到 1 条失败**（冻结区基线已被对方刷绿），而批次 C 交付点实际是 **2 条**——上表的两条红是**用批次 C 自己的两份基线复算**坐实的，与并发无关。

另记：审查中我用过一句 `git checkout -- tests/baseline/frame_events.json`，覆盖掉了对方刚刷出的基线；**对方随后自己提交了 `e326791` 重刷**，两份基线均已落库、工作区干净，**无损失**。

### 4.3 内核侧逐条复跑

| 判据 | 实测 | 对照 |
| --- | --- | --- |
| 软降计分归规则集 | 标准规则集 **1 分/格**；替身规则集（每格 5 分）**5 分/格** | spec §7.5 / plan §1.4.2 ✓ |
| 锁定延迟时长 | 落地帧 **1** → 锁定帧 **31**（**差 30**） | spec §7.5「30 逻辑帧」✓ |
| 重置上限 | 重置计数 **1,2,…,15**，第 16 次**不再重置**；**最后一次重置 → 锁定差 30** | spec §7.5「15 次 / 上限到了照常锁定」✓ |
| 移不动不重置 | 撞墙那一步**不发** `piece:lock-delay-reset` | spec §7.5 ✓ |
| 离地就取消 | 抽掉支撑平台 → `lockTimer` 归零 | plan §5.11 ✓ |
| 出生横坐标 | **I=3 J=3 L=3 O=4 S=3 Z=3** | spec §7.7（手算：`(10−盒宽)//2`）✓ |
| 顶出判定 | 出生与已有方块重叠 → `phase=game_over` + `game:over`（带 score / lines / reason） | spec §7.7 ✓ |

### 4.4 整局门判据的独立手算（**不 import 实现**）

```
一局（seed=4 + ReferencePolicy）：
  frames 2546 · piecesPlaced 75 · score 1900 · lines 14 · level 2 · combo 0 · b2b False
  phase game_over · events 14049

消行序列（行数, 类别键, 消行前连击数）：
  (1,'single',0) (1,'single',0) (1,'single',1) (2,'double',0) (2,'double',0)
  (1,'single',0) (1,'single',0) (1,'single',1) (2,'double',0) (2,'double',0)

本报告自己写的计分表与公式重算 → 1900 / 14 / 2   ←→   内核结算 1900 / 14 / 2   ✅ 逐项相等
手算 == 用例里的 EXPECTED_SCORE（1900）                                              ✅
观测序列 == 用例里的 EXPECTED_CLEARS（逐条）                                          ✅
最后 8 手全是放空（combo / b2b 断链）                                                 ✅
```

三层交叉核对（手抄序列 → 本文件自己的公式 → 人工推导的常数）在本报告里**独立重跑了一遍**，结论一致。

### 4.5 CLI 两种输出形态

| 形态 | 实测 |
| --- | --- |
| `sim --until game-over` | `phase=game_over` / `frames` / `events` / `fingerprint` / `score` / `lines` / `piecesPlaced` / `combo` / `b2b` / `level` —— **10 键**，退出码 0 |
| `sim --until game-over --json` | 同款十键，JSON 可直读 ✓ |
| `sim`（**不带** `--until`） | **七行中文标签**（帧数 / 事件数 / 棋盘指纹 / 分数 / 行数 / 已放置方块数 / 阶段）—— 与 M0 **逐字一致** ✓ |

---

## 5. 越界检查与残余上限

### 5.1 越界

| 红线 | 判定 |
| --- | --- |
| 契约面只增不改 | ✓ `contracts/` **一字未动**（事件 44 · 命令 16 · 快照字段集合不变） |
| 帧推进五步的顺序 / step 化结构 / 命令轮次语义 / 配额与防抖 / phase 门禁 | ✓ **一律未动** |
| 规则接口 8+1 条签名 | ✓ 未新增第 10 条（锁定延迟留在内核，符合 spec §7.5 的归属裁决） |
| M2–M4 的需求不提前实现 | ✓ `systems/` · `variants/` · `adapters/` 仍为空壳；`host/` 无渲染器与输入适配器；无落盘与回放 |
| 规则集表只读不写 | ✓ 参考策略只**派发命令**，不碰棋盘（快照是 frozen dataclass + `cells` 是深冻结的 tuple） |
| 冻结区 | ⚠️ `kernel/` 改了（K1–K3 + 出生位置），**基线未在批次 C 刷**——**这是计划的**（§5.1 的三处预期红之一，S14 才刷） |

### 5.1.1 本报告的 5 项发现**不碰冻结区** → S14 刚刷的两份基线**不会二次变红**

S14 已把两份基线刷绿（`d816a73` 冻结区内容哈希 · `e326791` 逐帧事件数）。本报告的 5 项修法逐条对照：

| 发现 | 改动落在哪 | 冻结区（`kernel/` + `rules/`） | 逐帧事件数基线 |
| --- | --- | --- | --- |
| M-1 | `host/loop.py`（**宿主层**）· plan §5.12 | **不碰** | 不影响（`RunReport` 的字段默认值不改内核事件流） |
| L-1 | `docs/plan-M1.md` · `tests/test_m1_gates.py` 的 docstring · `PROGRESS.md` · `ai_docs/` | **不碰** | 不影响（只改注释与散文） |
| L-2 | 只补 plan §5.13 的留痕 | **不碰** | 不影响 |
| L-3 | `docs/spec-M1.md` §7.9 的表 | **不碰** | 不影响 |
| L-4 | `docs/plan-M1.md` · `PROGRESS.md` · `tests/test_m1_kernel.py` 的 docstring | **不碰** | 不影响（只改措辞） |

**结论**：按本报告修订**不需要再刷基线**，也**不构成又一次 major 变更**（`PROGRESS.md` 里那条"批次 C 审查结果落地后两份基线的处置口径"的两条分支都不适用）。**唯一要留神的是 M-1 的修法 A**——若给 `RunReport` 三个字段加默认值，改的是 `host/loop.py`，**不在冻结区**，事件流也不变 ✓。

### 5.2 残余上限（写实记录）

- **本报告的验证是"独立复跑"，不是"形式证明"**：手算那三层只要**同时**改成实现算出来的值，三层就一起失守——这是 `docs/plan-M1.md` §5.13 第三条已经写明的残余上限，本报告不重复假装它被封住了。
- **参考策略的落点枚举直接用了实现表**（`tests/support/policy.py` 从 `rules.tables.SHAPE_TABLE` 取形状）——它**不是判据、只是驱动器**，所以不构成"同源自证"；但**若形状表本身错了，策略会跟着错**，G1-2 那条独立转录表才是形状表的牙齿。
- **`EXPECTED_CLEARS` 是观测事实的抄本**（"同种子同策略跑一局抄下来"）——**消行序列本身不是独立推导出来的**；真正的牙齿是"抄本 → 自己写的公式 → 人工常数"这三层的互锁。
- **批次 C 的两条基线红是"计划内"**——但"计划内"不等于"已解决"：**在 S14 刷掉之前，任何人拿 `pytest` 看这个仓库都会看到 2 条红**，且**不能**顺手 `--update-baseline` 刷掉（那要带 `--reason` 并写明理由）。

---

## 6. 待裁定的口径

| # | 事项 | 本报告的倾向 |
| --- | --- | --- |
| 1 | **M-1** 走 A（给默认值，真兼容）还是 B（认账：订正 docstring + 补提交类型留痕） | 倾向 **B**：仓内唯一构造点用关键字，给默认值反而给"三个结算字段可以是默认值"开了口子；但**那句声称必须改** |
| 2 | **L-1** 的六处一起改，还是只改 plan 两处 | 建议**六处一起改**（含 `test_m1_gates.py` 的 docstring 与 `ai_docs` 的"教训"），否则下个里程碑还会照着错的前提走 |
| 3 | L-2 / L-3 / L-4 是否并成一笔"文档：批次 C 审查修订" | 建议**并成一笔**：三项都是纯留痕与措辞，**不碰逻辑、不触基线** |

---

## 7. 复跑方式

```bash
# 范围（与本报告一致）
ocr delegate preview --format json --from 0c99d90 --to feature/m1-batch-c --repo "D:\PythonProject\Tetris"

# 批次 C 交付点的两道预期红（用批次 C 自己的基线复算）
git show feature/m1-batch-c:tests/baseline/kernel_hashes.json  > "$TEMP/bc_kernel_hashes.json"
git show feature/m1-batch-c:tests/baseline/frame_events.json  > "$TEMP/bc_frame_events.json"
python tools/check_frozen_baseline.py  --baseline "$TEMP/bc_kernel_hashes.json"   # exit 1（预期）
python tools/measure_frame_cost.py   --baseline-check "$TEMP/bc_frame_events.json"  # exit 1（预期）

# 门禁
python -m pytest -q
python -m ruff check . && python -m ruff format --check .
python -m pyright src tools
python -c "import importlinter.cli as c; c.lint_imports()"
python tools/check_kernel_purity.py
python tools/contract_baseline.py
```
