# S5 规则签名声明 —— 代码审查报告

- **审查对象**：`5a55302`「新功能：新增规则接口签名与 RuleContext 声明」
- **审查方式**：OCR **delegate（委托）模式** —— OCR 只做文件选择与规则解析，审查判断由宿主 Agent 完成
- **审查日期**：2026-10-23
- **基准 HEAD**：`0442dad`（纯文档提交，代码与 `5a55302` 一致，`git diff 5a55302..HEAD -- src tests` 为空）
- **规则组**：`system`（`**/*.{py,pyi,ipynb}`）

---

## 1. 结论摘要

**没有高危 / 严重问题。** 本阶段实现忠实落地了架构 §7.3.1 的 8 条签名与四个中性交换结构，签名参数名与类型逐条对得上，独立转录表的机检也确实立住了（我独立复跑了 5 组反向验证，全部变红）。

发现 **2 中 · 4 低**，集中在**声明面自身的"自洽性"**上，不在签名的业务正确性上：

| # | 等级 | 类别 | 一句话 | 位置 |
| --- | --- | --- | --- | --- |
| M1 | 中 | documentation / bug | 类型文法表形态 10 的规则与自家实例**互相矛盾**（两处），S6 照表实现会把自家声明判成不合规 | `contracts/types.py:35` · `contracts/types.py:66` |
| M2 | 中 | bug / maintainability | S5 新增的两个词汇表（`AXIS_VALUES` / `CLEAR_AXIS_VALUES`）与字段之间**没有任何机器绑定**，取值域只活在散文里 | `contracts/rules.py:77` · `contracts/rules.py:83` · `contracts/rules.py:112` · `contracts/rules.py:159` |
| L1 | 低 | bug | `structures._member_types` 依赖「所在模块有 `from __future__ import annotations`」这一**未强制**的假设 | `contracts/structures.py:48` |
| L2 | 低 | other | `NAMED_STRUCTS` 迁出 `contracts.snapshot` 是**模块级破坏性变更**（仓内已同步，项目未发布，影响有限） | `contracts/snapshot.py` · `contracts/structures.py:66` |
| L3 | 低 | test | 快照叶子展开按「注册表命中」判定结构字段，注册表从 2 个结构涨到 8 个后**耦合变宽** | `tests/test_contracts_declarations.py:963` |
| L4 | 低 | documentation | 形态 9 的规则标题仍写「只用于需求 §8.3 定名的子结构」，与同文件下文的 S5 扩展**自相矛盾** | `contracts/types.py:52` |

---

## 2. 中等问题

### M1　类型文法表形态 10 的规则与自家实例互相矛盾（两处）

**位置**：`src/tetris_kernel/contracts/types.py:35`（形态表第 10 行）、`src/tetris_kernel/contracts/types.py:66`（附加规则 5）

**问题**：S5 新增的形态 10（只读序列 `Sequence[T]`）在文法表里写了两条约束，两条都与仓内已有的形态 10 实例冲突。

**(a) 形态表第 10 行的 T 范围写窄了**

```
| 10 | 只读序列 | `Sequence[T]`（T 为形态 1–9 的任意类型表达式） | `Sequence[Sequence[int]]` |
```

同一行给的示例 `Sequence[Sequence[int]]`，其 T 是 `Sequence[int]` —— **形态 10 自己**，不在 1–9 内。规则字面排除的正是它自己的示例。

**(b) 附加规则 5 的适用范围写窄了**

```
5. **形态 10 只用于规则接口的棋盘参数**：内核内部棋盘是可变结构
   `list[list[int]]`（架构 §7.1），规则只读。……
```

但仓内形态 10 共有三处用法，其中 `score_clear.groups: Sequence[ClearGroup]` 是**消行组序列**，不是棋盘参数。

**证据（探针实测）**：

```
=== PROBE 1: every form-10 type string actually in use ===
   param scan_clears.board = Sequence[Sequence[int]]
   param classify_placement.board = Sequence[Sequence[int]]
   param score_clear.groups = Sequence[ClearGroup]
   BOARD_VIEW = Sequence[Sequence[int]]
=== PROBE 1b: literal reading of grammar rule 5 / form-table row 10 ===
   BOARD_VIEW inner T='Sequence[int]' is-form10=True -> 'T is form 1-9' literal accepts=False
   score_clear.groups: is-board-param=False -> rule5 literal accepts=False
```

**为什么重要**：`PROGRESS.md` 卡点第 41 行明写——「**S6 的解析器以文法表为准重新实现，别照抄测试里那份**」。按字面实现这条表，S6 会把 `types.BOARD_VIEW` 和 `score_clear.groups` 判成"表里没有的形态 = 声明不合规"，而这两个串正是 S5 自己落的声明。

**佐证这是 types.py 落下了、不是清单落下了**：`docs/契约v1清单.md` 第 12 项的口径是对的——「形态 10（只读序列 `Sequence[T]`，**只用于规则接口的棋盘参数与消行组序列**）」。两处口径不一致，以清单为准。

**建议**：

1. `types.py:35` 的 T 范围改为「T 为形态 1–10 的任意类型表达式」（或明确写"T 可再嵌套，不限深"）；
2. `types.py:66` 补上"与消行组序列"：形态 10 用于**规则接口的棋盘参数与消行组序列**；
3. 改完与 `docs/契约v1清单.md` 第 12 项逐字对齐。

---

### M2　S5 新增的两个词汇表与字段之间没有任何机器绑定

**位置**：`src/tetris_kernel/contracts/rules.py:77`（`AXIS_VALUES`）、`rules.py:83`（`CLEAR_AXIS_VALUES`）、`rules.py:112`（`RuleContext.axis: str`）、`rules.py:159`（`ClearGroup.axis: str`）

**问题**：S5 声明了两个取值域常量，并在 docstring 里反复强调「重力方向是变体维度，**契约层钉死**」「`ClearGroup.axis` 取值域 `CLEAR_AXIS_VALUES`」。但两个字段的类型都是**裸 `str`**，取值域与字段之间没有任何代码关联。

**证据（探针实测）**：

```
=== PROBE 2: is any value domain bound to a struct field? ===
   RuleContext.axis type   = str
   ClearGroup.axis type    = str
   AXIS_VALUES             = ('down', 'left', 'right', 'up')
   CLEAR_AXIS_VALUES       = ('row', 'column')
   RuleContext(axis='diagonal') accepted  -> RuleContext(frame=0, ..., axis='diagonal', stepMs=1000)
   ClearGroup(axis='diagonal') accepted   -> ClearGroup(axis='diagonal', index=0, cells=(), classification=None)
   AXIS_VALUES occurrences inside contracts/:
      __init__.py:40/41/81/86（导入与导出）
      rules.py:56/57/103/144（docstring）
      rules.py:77/83（定义本身）
      rules.py:356/357（__all__）
```

`AXIS_VALUES` 在 `contracts/` 里的全部出现位置**只有定义、导出与 docstring**——没有任何一处参与校验。

唯一引用这两个常量的测试 `tests/test_contracts_declarations.py:1382` `test_axis_vocabularies_are_not_mixed`（断言在 1388–1390 行）**只把字面量再抄一份比对常量自身**：

```python
assert AXIS_VALUES == ("down", "left", "right", "up")
assert CLEAR_AXIS_VALUES == ("row", "column")
assert not set(AXIS_VALUES) & set(CLEAR_AXIS_VALUES)
```

它证明的是"这两个常量没有互相混用"，**没有证明任何字段受这两个常量约束**。字段类型改成别的、或者传了域外的值（如上面的 `axis="diagonal"`），都不会红。

**为什么重要**：契约层的价值就是"声明可被机检"。轴是 M4 变体 A 的核心维度（架构 §7.3.1 纪律 2 的立论基础），而基线只哈希类型串 `str`（架构 §4.4 末条：哈希引用处的类型串，**常量定义本身不进基线**）——消费方无法从声明推出该写哪一套值，写错了也没有任何机检拦。

**与既有待办的关系**：`PROGRESS.md` 待办第 27 行已记「取值域统一收口（S4 审查 L5，**S2 起就有的同源问题**）……**S5 之后统一收口**」。也就是说全局收口本来就不在 S5 的范围内。**但 S5 新增的这两个词汇表沿用了同样的无绑定写法**——新声明面在本阶段内就已经带着这个缺口，而不是等到收口。

**建议**（二选一，不必在本阶段做全局收口）：

- 轻量：在 `RuleContext` / `ClearGroup` 上补一条「字段取值 ⊆ 对应词汇表」的断言（构造实例逐字段查表即可，不必进类型串）；
- 彻底：给类型文法补「形态 11 = 字面量枚举 `Literal["a", "b"]`」，把轴的取值域直接写进类型串——那样基线能看见，契约清单第 12 项的"禁止已有字段的类型变化"也才真的管得住轴。

---

## 3. 低优先级

### L1　`structures._member_types` 依赖「模块有 `from __future__ import annotations`」这一未强制的假设

**位置**：`src/tetris_kernel/contracts/structures.py:48`

`_member_types` 用 `str(field.type)` 取类型表达式。`dataclasses.fields()` 返回的 `field.type` **只有在模块启用了 future import 时才是源码字符串**；否则是运行时类型对象，`str()` 出来是 `"<class 'xxx.Yyy'>"` 这种带模块前缀的表示——既不是形态 9 的标识符，也不是合法类型串。

**证据（探针实测）**：

```
=== PROBE 3: structures._member_types depends on future-annotations ===
   Holder: {'value': "<class 'probe_nofuture.SomeClass'>"}
   WithType: {'value': "<class 'int'>"}
```

现状安全：`placement.py:17` 与 `rules.py:61` 都有 future import，所以 8 个结构的成员表当前全部是干净的类型串。但**没有任何断言守住这一点**：将来新增一个结构、所在模块漏了 future import，就会静默产出非法类型串，而且这个串会被基线哈希进去。

**建议**：`_member_types` 里加一句 `if not isinstance(field.type, str): raise TypeError(...)`，让 docstring 承诺的「跨进程稳定」成为受检事实而不是口头约定。

### L2　`NAMED_STRUCTS` 迁出 `contracts.snapshot` 是模块级破坏性变更

**位置**：`src/tetris_kernel/contracts/snapshot.py`（旧位置）、`src/tetris_kernel/contracts/structures.py:66`（新位置）

**证据（探针实测）**：

```
=== PROBE 4 ===
   import from contracts.snapshot: ImportError -> cannot import name 'NAMED_STRUCTS' from 'tetris_kernel.contracts.snapshot'
   contracts.structures.NAMED_STRUCTS keys = ['Board', 'Active', 'PlacementInfo', 'RuleContext', 'PlacementClass', 'ClearGroup', 'ScoreState', 'ScoreDelta']
```

仓内引用**已同步**（`contracts/__init__.py:63` 改为 `from .structures import NAMED_STRUCTS`，包级 `tetris_kernel.contracts.NAMED_STRUCTS` 仍可用），项目未发布，影响有限。**这是一次有意的迁移，不是缺陷**，此处只做留档：按"注册表在快照模块"的旧记忆写代码会直接断。

### L3　快照叶子展开按「注册表命中」判定结构字段，注册表扩容后耦合变宽

**位置**：`tests/test_contracts_declarations.py:963`（`_snapshot_leaf_paths`）

```python
members = NAMED_STRUCTS.get(field.type.removesuffix(" | None"))
if members is None:
    leaves.add(name)
else:
    leaves.update(f"{name}.{member}" for member in members)
```

只要快照字段的类型串**命中注册表里任何一个结构名**，就被当作嵌套结构展开。S5 之前注册表只有 `Board` / `Active`，这个判定等价于"是不是快照子结构"；S5 之后注册表有 8 个结构，判定含义变宽了。

**证据（探针实测）**：

```
=== PROBE 5 ===
   type 'Board'            -> expanded as nested struct? True
   type 'Active | None'    -> expanded as nested struct? True
   type 'ScoreState'       -> expanded as nested struct? True
   type 'RuleContext'      -> expanded as nested struct? True
   type 'int'              -> expanded as nested struct? False
```

当前快照字段只用到 `Board` / `Active`，**没有活跃 bug**。但将来若加一个类型为 `ScoreState` 的快照字段，它会被静默展开成"子结构"，叶子集合断言会以极难懂的方式变红。

**建议**：把判定改成显式的（`field.name in {"board", "active"}`，或 `field.type in {"Board", "Active"}`），别让"注册表里有什么"决定"快照里什么是结构"。

### L4　形态 9 的规则标题仍写「只用于需求 §8.3 定名的子结构」

**位置**：`src/tetris_kernel/contracts/types.py:52`

```
4. **形态 9 只用于需求 §8.3 定名的子结构**——快照的 `Board` / `Active`，……
```

同一条规则的 62–65 行已补上 S5 的扩展说明（`RuleContext` 等五个结构与 `PlacementInfo` 同样登记），但**标题那句仍是旧的**。标题与正文自相矛盾，读者只读到标题就会以为注册表里只该有 `Board` / `Active`——而这正是 S4 审查 M1 修掉的老问题（当时是测试里硬编码名单与成员表脱钩）。

**建议**：把标题改成「形态 9 只用于 `contracts/` 内登记过的命名结构」。

---

## 4. 质量门（HEAD `0442dad` 实跑）

| 门 | 命令 | 结果 |
| --- | --- | --- |
| 测试 | `python -m pytest -q` | **151 passed** |
| 静态检查 | `python -m ruff check .` | All checks passed |
| 格式 | `python -m ruff format --check .` | 36 files already formatted |
| 类型 | `python -m pyright src/tetris_kernel` | 0 errors, 0 warnings, 0 informations |
| 分层 | `lint-imports` | Contracts: **4 kept, 0 broken** |

环境：`D:\miniconda3\envs\py312\python.exe`（3.12.13），未使用 `pip install` / `uv sync` / `uv venv`。

---

## 5. 反向验证（独立复核，不采信提交说明）

提交说明自陈"反向验证 11 组全部变红"。我**独立复跑 5 组**（在测试模块上打值绑定补丁，见下），全部变红：

| 组 | 破坏方式 | 期望 | 实测 |
| --- | --- | --- | --- |
| RV1 | `gravity_vector` 返回类型改成 `int` | 逐条比对红 | ✅ RED（`AssertionError`） |
| RV2 | `shape_cells` 去掉 `ctx` 参数 | 逐条比对 + 轴维度双红 | ✅ RED ×2 |
| RV3 | 注册表里删掉 `ScoreState` | 未登记的结构名文法红 | ✅ RED（`_GrammarError`） |
| RV4 | `AXIS_VALUES` 加一个值 | 词汇表断言红 | ✅ RED |
| RV5 | `Board.width` 的 `FieldSpec.name` 改名 | 键与声明分家断言红 | ✅ RED |

> 补丁打在**测试模块**（`T.RULE_SIGNATURES` / `T.NAMED_STRUCTS` / `T._NAMED_STRUCTS` / `T.AXIS_VALUES` / `T.SNAPSHOT_FIELDS`）而非包属性——测试是按值绑定导入名的，打在包上无效。

**结论**：S5 自陈的反向验证结论可信，机检确实立得住。

---

## 6. 逐条核对：8 条签名与架构 §7.3.1 是否一致

架构 §7.3.1（`docs/项目架构.md:355–364`）与 spec §6.5（`docs/spec-M0.md:258–267`）的签名表，与 `contracts/rules.py:239–345` 的 `RULE_SIGNATURES` **逐条一致**（参数名、参数顺序、类型、返回类型），包括：

- `wrap_cell(x, y, w, h) -> tuple[int, int]`（拓扑是函数不是参数，ADR-037）✅
- `gravity_vector(ctx) -> tuple[int, int]`（必须是向量，不能只有 dy）✅
- `gravity_interval(ctx) -> int` ✅
- `scan_clears(board, ctx) -> tuple[ClearGroup, ...]`（不能只返回数量）✅
- `classify_placement(board, placement, ctx) -> PlacementClass`（不另开第五项，FR-4.9）✅
- `score_clear(groups, ctx, prev) -> ScoreDelta`（纯函数，落笔由内核做）✅
- `shape_cells(ctx, pieceType, rotation) -> tuple[tuple[int, int], ...]`（必须带 `ctx`）✅
- `kick_offsets(ctx, pieceType, fromRotation, toRotation) -> tuple[tuple[int, int], ...]`（含 180°）✅

`RuleContext` 5 字段（`frame` · `level` · `piecesPlaced` · `axis` · `stepMs`）与架构 §7.3.1 第 353 行逐条相等，顺序一致 ✅
四个中性交换结构的字段与顺序与 spec §6.5 第 273 行逐条相等 ✅
`PlacementInfo` 6 字段与架构 §7.3.1 第 391 行、spec §6.5 一致（`EXPECTED_PLACEMENT_FIELDS` 转录在 `tests:932–939`，断言在 `tests:1137–1138`，**顺序也锁死**）✅

**T-Spin 分类的搬运机制**（架构 §7.3.1 第 374–379 行 / spec §6.5 第 275–278 行）：分类挂在 `ClearGroup.classification`（`rules.py:162`），冻结签名一行未动，内核只搬运不判断——**与冻结口径一致** ✅

---

## 7. 覆盖率

| 项 | 数 |
| --- | --- |
| `total_files` | 6 |
| `reviewable_files`（OCR 判定可审） | 5 |
| `excluded_files` | 1 |
| `reviewed_files` | **6** |
| `skipped_files` | 0 |
| `coverage_rate` | **100%**（代码 5/5；含被排除文件 6/6） |

**被排除文件的说明**：`tests/test_contracts_declarations.py`（`+366 / -13`）被 OCR 以 `default_path` 排除——这是 OCR 对 `tests/**` 的默认策略，**不代表它不重要**。本阶段的机检担保几乎全在该文件里（签名转录表、形态 10 的文法解析、词汇表断言、注册表断言、纯净度扫描），因此**逐行审查了该文件并计入覆盖率**。

**逐文件审查记录**：

| 文件 | 状态 | 增量 | 结论 |
| --- | --- | --- | --- |
| `src/tetris_kernel/contracts/rules.py` | reviewed | +367 | 签名与交换结构忠实落地；M2 涉及 |
| `src/tetris_kernel/contracts/structures.py` | reviewed | +79 | 注册表迁移干净，无循环导入；L1 涉及 |
| `src/tetris_kernel/contracts/types.py` | reviewed | +29 / -2 | **M1 / L4 在此** |
| `src/tetris_kernel/contracts/snapshot.py` | reviewed | +6 / -17 | 只移出 `NAMED_STRUCTS` + `ghostDelta` 改引用常量；L2 涉及 |
| `src/tetris_kernel/contracts/__init__.py` | reviewed | +31 / -1 | 导出与 `__all__` 一致（`test_package_all_matches_actual_exports` 守住） |
| `tests/test_contracts_declarations.py` | reviewed | +366 / -13 | 机检有效（RV1–RV5 复跑全红）；L3 涉及 |

---

## 8. S4 报告 9 项落实复核（顺带核对）

S4 的 9 项发现已在 S5 之前的 `0a2117e`「修复：按 S4 审查报告修订快照契约声明」中修掉 8 项，剩 1 项推迟。S5 的改动与它们的交互：

| S4 发现 | S5 之后的状态 |
| --- | --- |
| M1 形态 9 成员表绑定无机检 | ✅ **已彻底解决**——`structures.py` 的 `NAMED_STRUCTS` 落地，成员表从声明推导，回验从它推导结构名集合 |
| M2 `KERNEL_STATE_TO_SNAPSHOT` source 侧是散文 | ✅ 已修（S4 修订），S5 未触及 |
| M3 `RUNTIME_ONLY_STATE` 守卫不可触发 | ✅ 已修（S4 修订），S5 未触及 |
| L1 `PlacementInfo` 未进文法回验 | ✅ 已修（S4 修订），S5 未触及 |
| L2 `board.cells` 裸字面量 | ✅ 已修（S4 修订）；S5 延续同一做法（`VECTOR2` / `BOARD_VIEW`） |
| L3 `_SNAPSHOT_FIELDS` 可变别名 | ✅ 已修（S4 修订），S5 未触及 |
| L4 `extensions` 转录已非需求字面 | ✅ 已修（S4 修订），S5 未触及 |
| L5 `phase` 取值域无绑定 | ⏳ **仍未修**——已记在 `PROGRESS.md` 待办第 27 行「S5 之后统一收口」；**S5 新增的 `AXIS_VALUES` / `CLEAR_AXIS_VALUES` 沿用了同样的无绑定写法**（本报告 M2） |
| L6 `__all__` 无一致性断言 | ✅ 已修（S4 修订）；S5 新增两个模块后该断言已自动覆盖 `rules` / `structures` |

---

## 9. 对下一阶段（S6）的提醒（非缺陷，不计入发现数）

1. **改名 vs 新增**：契约清单第 12 项的口径是「键 = 字段名、值 = 类型声明哈希；允许新增字段，禁止已有字段的类型变化」。但形态 9 的成员表**从声明推导**——成员**改名**会让旧键消失、新键出现，按"允许新增键"的宽松口径可能被当成纯新增而放过。S6 定基线键时需要明确：**改名 = 删旧键 + 加新键，按 major 报**。
2. **取值域常量不进基线**：架构 §4.4 末条已定「哈希引用处的类型串，**常量定义本身不进基线**」。因此 `AXIS_VALUES` 这类词汇表的变更**不会让基线变红**——这正是 M2 的危害所在：在收口之前，轴写错没有任何机检会拦。
3. **别照抄测试里的解析器**：`PROGRESS.md` 卡点第 41 行已明写「S6 的解析器以文法表为准重新实现」。结合 M1，**先把 `types.py` 的形态 10 规则改对，再照它写解析器**，否则 S6 会把 S5 自己的声明判成不合规。

---

## 10. 未验证 / 明确说明的部分

- **基线尚未生成**（契约清单第 11 / 12 / 16 项未勾，基线要等 S17 冻结）——本报告所有"进基线"的判断都是基于架构 §4.4 / §7.6 的口径推导，**没有实测哈希**。
- **S6 解析器未落地**——M1 的实际后果（S6 会把自家声明判成不合规）是按 `PROGRESS.md` 的口径推导的**前瞻性判断**，尚未发生。
- **需求 §8.3 / 架构 §7.3.1 的逐条对齐是人工核对**，不是机器解析文档——`tests/ocr/` 里的 `EXPECTED_*` 表是**转录**而非从文档解析出来的，转录本身可能与文档有偏差（S4 报告 L4 就在 `extensions` 上抓到过一处已非字面的转录）。
- **本轮没有复跑 S5 自陈的另外 6 组反向验证**（只独立复跑了 5 组），未复跑部分不作评价。
- **快照字段语义、规则接口实现均不在本阶段**（S8 / M1 之后）——本报告只审声明面，不评价行为面。
- **`ai_docs/` 与 `PROGRESS.md` 未改动**：本次是代码审查而非阶段任务执行；是否把本报告结论落档，待决定。
- **临时产物已清理**：本轮共创建 2 个探针文件（`.tmp_ocr_s5/probe_s5.py` 与 `.tmp_ocr_s5/probe_nofuture.py`；diff 一律走 `git show` 直接读，**未导出临时 diff 文件**），全部删除（先移入工作区，再 `rm --` 走回收站），工作区当前只有本报告一个未跟踪文件。
