# 批次 A 代码审查报告 · S7 随机源与时间源 · S8 快照与只读视图 · S13 规则最小标准实现

> **轮次**：第 1 轮（首审）
> 审查范围：`77c3bea..feature/batch-a`（master HEAD → 批次 A 落笔提交，含 A1 / A2 / A3 三笔阶段提交与一笔集成收口）
> 审查方式：`open-code-review-delegate`（OCR 只做文件选择与规则解析，**判断全部由宿主 Agent 完成**）
> 审查基线：`f89ce9a`（`feature/batch-a` HEAD）
> 审查日期：2026-10-24

---

## 1. 结论

**批次 A 的骨架是对的，可以进批次 B；但有 3 处在 S9 / S12 消费之前就该定死的语义缺口。**

| 项 | 结论 |
| --- | --- |
| **发现总数** | **15 项**：中 **3** · 低 **12** · 高 **0** · critical **0** |
| **最要紧的一条** | **A1** —— 标准规则集下 `b2b` **永远为 False**，背面连击链无法起链（M 级需求 FR-4.6 静默失效） |
| **契约面** | `contracts/` **一字未动**，基线仍 **355** 条 ✓；快照字段与声明**导入期逐字段相等** ✓；`rng:next` 负载与契约声明**逐字一致** ✓ |
| **五道门** | pytest **245 passed** · ruff check 全过 · ruff format **57 files** · pyright **0 errors** · lint-imports **4 kept** ✓ |
| **机检** | 纯净度绿（冻结区 2 目录 / 违规 0）· 事件登记绿 ✓ |
| **形状表** | 7 方块 × 4 朝向 = **28 组坐标逐条核对，与 SRS 基准一致** ✓ |
| **随机源纪律** | 只用 `random()` / `getrandbits()` ✓ · 扁平化状态 **626** int ✓ · 导入用**导入状态自带的** version（ADR-011 的集成修正做对了）✓ |

**为什么没有「高」**：这 15 项都不损坏数据、不影响回放比对、不让已冻结的契约分叉。最坏的情况是**某个状态字段一直是默认值**（A1 的 `b2b`）或**某条 API 承诺只在调用方记得时才成立**（A3）。但 A1 / A2 / A3 都会被 S9 / S12 直接消费——**在这三个阶段把语义定死，比之后回改便宜得多**，所以它们定级为「中」而不是「低」。

**作者的三处刻意边界不算漏做**：标准踢墙表只做恒等偏移（逐条验证是 M1 开门判据）· 形状表只填 `down` 轴（横向重力属 M4-A5）· T-Spin 只一档（Mini 与三档属 M1）。这三条在 `docs/plan-M0.md` 的 S13 段与 `PROGRESS.md` 里都写明了，**本报告不把它们当缺陷**。

---

## 2. 审查范围与文件处置

### 2.1 OCR 预览

```
ocr delegate preview --format json --from master --to feature/batch-a
→ mode=range  merge_base=77c3beafc8464220fd94b679a9039da0e9053bb7
→ total_files=17  reviewable_count=10  excluded_count=7
→ total_insertions=3517  total_deletions=43
```

规则解析：`ocr delegate rule --format json <10 个路径>` → **1 个规则组**（`system` 组，`**/*.{py,pyi,ipynb}`，通用清单：边界与空输入 · 错误处理 · 死代码 · 可变默认值与共享状态 · 性能 · 并发 · 安全）。

### 2.2 可审文件（10 / 10 全部审查）

| 文件 | 状态 | 行数 |
| --- | --- | --- |
| `src/tetris_kernel/kernel/rng.py` | added | 298 |
| `src/tetris_kernel/kernel/fingerprint.py` | added | 96 |
| `src/tetris_kernel/kernel/snapshot.py` | added | 475 |
| `src/tetris_kernel/rules/interface.py` | added | 260 |
| `src/tetris_kernel/rules/standard.py` | added | 386 |
| `src/tetris_kernel/rules/tables.py` | added | 275 |
| `src/tetris_kernel/kernel/__init__.py` | modified | 56 |
| `src/tetris_kernel/rules/__init__.py` | modified | 86 |
| `src/tetris_kernel/host/__init__.py` | modified | 18 |
| `src/tetris_kernel/host/clock.py` | added | 102 |

### 2.3 排除文件（7，全部**手动审查并计入覆盖率**）

| 文件 | 排除原因 | 处置 |
| --- | --- | --- |
| `tests/test_rng.py`（434 行） | `default_path` | **逐行审查**——本批次的守卫质量大半在它身上 |
| `tests/test_snapshot.py`（435 行） | `default_path` | **逐行审查** |
| `tests/test_rules.py`（462 行） | `default_path` | **逐行审查** |
| `tests/test_package_exports.py`（40 行） | `default_path` | **逐行审查** |
| `PROGRESS.md`（+23 / −16） | `unsupported_ext` | 逐节比对**计数与代码是否相符**（查出 A12） |
| `ai_docs/261003.md`（+34） | `unsupported_ext` | 逐节读（查出 A12 的第三处） |
| `docs/plan-M0.md`（+37 / −27） | `unsupported_ext` | 逐节读 S7 / S8 / S13 段，与实现逐条对 |

> `tests/**` 被 OCR 的 `default_path` 排除是**已知行为**——上一轮（S2–S6）同样如此。**这四个测试文件是本批次唯一的机器担保载体，必须逐行看**，不能因为「被排除」就不算数。

---

## 3. 发现清单

| 编号 | 位置 | 类别 | 级别 | 一句话 |
| --- | --- | --- | --- | --- |
| **A1** | `rules/standard.py:250` | bug | **中** | `b2b` 与 `prev.b2b` 相与 → **B2B 链永远起不了**，M 级需求 FR-4.6 静默失效 |
| **A2** | `rules/standard.py:239-240` | bug | **中** | 无消行时 `combo` 原样保留 → **连击永不中断**，跨「没消行的放置」累加 |
| **A3** | `kernel/snapshot.py:366-418` | bug | **中** | `build_snapshot` 不做扩展槽规范化 → 「同一组扩展 = 同一个快照」只在调用方记得先过 `build_extensions` 时成立 |
| **A4** | `rules/tables.py:63-108` | bug | 低 | 形状表**内层是普通 dict**，运行期可改写；与 `KICK_TABLE` 口径不一致 |
| **A5** | `kernel/snapshot.py:349-363` | bug | 低 | `build_board` 不校验 `width/height` 与 `cells` 一致，且指纹**不覆盖尺寸** |
| **A6** | `kernel/rng.py:48` | maintainability | 低 | 事件类型名 `"rng:next"` 在 kernel 侧复制了一份字面量（架构 §4.4 末条） |
| **A7** | `kernel/snapshot.py:63` | maintainability | 低 | `ExtensionEntry` 复制了 `contracts/types.py:117` 的标量联合，且**不在导入期校验范围内** |
| **A8** | `kernel/rng.py:106` | documentation | 低 | `import_state` 之后 `seed` 属性仍报构造时的种子 → **审计时误导** |
| **A9** | `kernel/rng.py:228` | bug | 低 | 越界状态值抛 `OverflowError`，与本模块其它校验的 `ValueError` 不一致 |
| **A10** | `tests/test_rng.py:224-228` | test | 低 | 「只用两个原语」的 AST 守卫**只认属性调用**，认不出 `from random import shuffle` |
| **A11** | `tests/test_rng.py:210-221` | test | 低 | 纯净度用例只扫两个文件，理由（"还有别的写手在写"）**批次合入后已过期** |
| **A12** | `PROGRESS.md` · `docs/plan-M0.md` · `ai_docs/261003.md` | documentation | 低 | 三处把 S7 记成「25 passed」，该提交实际含 **27** 条用例（总数 245 仍对） |
| **A13** | `rules/standard.py:241` | other | 低 | 横向重力下 `linesDelta` 用**组数**当消行数，口径未对齐 |
| **A14** | `rules/standard.py:99-108` | other | 低 | 行 / 列两条路径对「对不齐的棋盘」口径不一致 |
| **A15** | `tests/test_rng.py:424` | test | 低 | `assert real.now_ms() <= real.now_ms()` —— **恒真断言**，什么也没守 |

---

## 4. 发现明细

### A1（中）`b2b` 永远为 False —— 背面连击链无法起链

**位置**：`src/tetris_kernel/rules/standard.py:250`

```text
b2b=class_key in DIFFICULT_CLASS_KEYS and prev.b2b,
```

**问题**：`prev.b2b` 的**唯一来源**就是上一次 `score_clear` 返回的 `state.b2b`，而那个值同样是与更早的 `prev.b2b` 相与的结果。一串从 `False` 起步的 AND，**结果恒为 `False`**。当前置状态是 `b2b=False`（新局的初始值），此后无论消多少次四消 / T-Spin，`b2b` 都不会变成 `True`。

**实测**（探针 P1，连续 4 次难消，把返回的 state 当下一次 prev 喂回去）：

```
P1 b2b loop 0 b2b= False combo= 1 score= 800
P1 b2b loop 1 b2b= False combo= 2 score= 1650
P1 b2b loop 2 b2b= False combo= 3 score= 2550
P1 b2b loop 3 b2b= False combo= 4 score= 3500
```

**后果**：

- 快照字段 `b2b`（需求 §8.3「当前背面连击状态」）在标准规则集下**永远是 `False`**；
- 事件 `score:b2b-changed`（需求 §8.1）**永远不会有真值**；
- FR-4.6（M 级）「实现消行判定、连击、背面连击与计分表」的 B2B 部分**静默失效**——不报错、不红，只是这个功能从来没生效过。

**根因是语义混用**：一个布尔位同时被要求表达两件事——「当前处于 B2B 链中」（`b2b = 难消 ∧ 上一次也是链`）与「上一手是难消」（链的输入）。只有前者时，链需要**两次**难消才成立，而第一次难消必须把标志置起来；只有后者时，`b2b = 难消`（本次）就够了，链的判定交给内核。**现在的写法把两件事塞进同一位，链就永远起不来。**

**测试为什么没抓到**：`tests/test_rules.py:242-258` 的 `test_score_clear_b2b_flag` 守住了三种转移——`True→True`（难消 + prev 难消）、`True→False`（普通消行）、`False→False`（难消链的第一次）——**唯独没有守 `False→True`**，也就是起链那一跳。这正是「少守一处比多守一处更难发现」的老形状。

**建议（供作者裁决，本报告不替项目定语义）**：

- 路线甲：把 `b2b` 定义为「上一手是难消」，实现写成 `b2b = class_key in DIFFICULT_CLASS_KEYS`，**链的判定交内核**（内核手里有上一手）；
- 路线乙：内核另带一位链状态，规则集只报「本次是否难消」；
- 无论走哪条，都要**补一条 `False→True` 的用例**，并把「`b2b` 这个位到底是什么意思」写进 `contracts/rules.py` 的 `ScoreState` docstring（它现在只有一句「与快照的计分字段同名同义」）。

> 定级说明：这不是高危——它不损坏数据、不影响回放比对（回放比的是棋盘指纹 / 分数 / 行数 / 方块数 / 扩展指纹）。但它让一个 M 级需求**静默不生效**，且会在 S9 / S12 消费 `ScoreState` 时被固化下来。

---

### A2（中）连击永不中断

**位置**：`src/tetris_kernel/rules/standard.py:239-240`（空组早返回）· `:245`（奖励）· `:249`（新状态）

```text
if not groups:
    return ScoreDelta(scoreDelta=0, linesDelta=0, state=prev)   # ← combo 原样保留
...
score_delta = SCORE_TABLE[class_key] + COMBO_BONUS * prev.combo
...
combo=prev.combo + 1,
```

**问题**：一次**没消行**的放置会让 `score_clear` 原样返回 `prev` —— `combo` 不复位。于是连击链跨着「没消行的放置」继续累加，奖励按**已经断了的**连击数发放。

**实测**（探针 P2）：

```
P2 after 1st clear    combo= 1 score= 100
P2 after no-clear     combo= 1 score= 100      ← 没消行，combo 没断
P2 after 2nd clear    delta= 150 combo= 2      ← 100 + 50×1，奖励按断了的连击发
```

**后果**：标准玩法里「没消行」应当中断连击。现在不中断 → 玩家放了一块没消行的方块，下一块消行仍然拿连击奖励。FR-4.6（M 级）含连击。

**口径提醒（这条可能同时是「口径缺口」而不只是实现错）**：`docs/plan-M0.md` 的 S13 段与 `rules/standard.py` 的 docstring **都没写「连击何时中断」**，docstring 只说「本次消行后 `combo + 1`，奖励按消行前的连击数给」。所以「谁来复位」这件事**当前是没有定义的**——本报告把它列为发现，是因为**现状（永不中断）是一个具体行为**，而不是因为它一定错。

**建议**：在 M0 内把这条口径定死并写进 `plan-M0.md` 的 S13 段——要么规则集在空组时复位 `combo`（`score_clear((), ...)` 返回 `combo=0` 的新状态），要么内核负责复位并写进 S12 的落笔逻辑。**别留成「实现碰巧是这样」**。

---

### A3（中）`build_snapshot` 不做扩展槽规范化

**位置**：`src/tetris_kernel/kernel/snapshot.py:366-418`（`build_snapshot`）· `:215-223`（`build_extensions`）

**问题**：`build_extensions` 负责「校验 key 唯一 + 按 key 排序」，其 docstring 承诺：

> 排序让"同一组扩展、不同合并顺序"产出**同一个快照**——回放比对的是内容，不是宿主合并扩展时的偶然顺序。

但 `build_snapshot` **直接把传入的 `extensions` 存进快照**，既不排序也不校验它已规范化。这条承诺**只在调用方记得先过 `build_extensions` 时成立**，而构造入口本身不保证。

**实测**（探针 P4，同一组扩展、两种顺序）：

```
P4 snapshots equal:      False      ← 两个快照不相等
P4 fingerprints equal:   True       ← 但扩展指纹完全相同
P4 stored extensions a:  (('a', 1), ('b', 2))
P4 stored extensions b:  (('b', 2), ('a', 1))
```

**后果**：

- **回放比对不受影响**（架构 §5.4 的第五项比的是 `extensionFingerprint`，指纹与顺序无关）；
- 但「快照相等」这条判据（dataclass `__eq__`）**被顺序影响** → 同一局状态、扩展合并顺序不同 → 两个快照不相等。任何拿快照做 `==` 比较的地方（单测、宿主侧缓存键、录像分段比较）都会踩到；
- 快照里存的 `extensions` 也不是「规范化形态」，与字段名 `extensions` 在架构 §5.4 里的定性（规范化摘要的输入）不一致。

**建议**：`build_snapshot` 内部对 `extensions` 做一次规范化（排序 + 唯一性校验），或改成**只接受 `build_extensions` 的产物**并在类型上区分这两种形态。代价很小，改完 A3 的两条隐患一起消。

---

### A4（低）形状表内层是普通 dict，「只读常量」只做到外层

**位置**：`src/tetris_kernel/rules/tables.py:63-108`（`_SHAPE_DOWN`）· `:115-124`（`SHAPE_TABLE`）· 对照 `:146-172`（`KICK_TABLE`）

**问题**：`SHAPE_TABLE` 与 `_SHAPE_DOWN` 用了 `MappingProxyType`，但 `_SHAPE_DOWN` 的**值**（`"I"` / `"J"` … 那七张「方块 → 朝向 → 格子」表）是**普通 `dict`** → 运行期可改写。而 `KICK_TABLE` 的内层是 `MappingProxyType`，**两张表口径不一致**。

**实测**（探针 P3）：

```
P3 SHAPE_TABLE nested dict MUTABLE: YES      ← SHAPE_TABLE["down"]["I"][0] = ((0,0),) 成功
P3 KICK_TABLE nested mutability:    NO  TypeError
P3 SHAPE_TABLE['down']['I'] type:   dict
```

**后果**：架构 §7.3.1 纪律 1「规则集不可变、无状态」被绕过 —— 任何拿到 `SHAPE_TABLE` 引用的代码都能在运行期改掉形状表，而回放校验用的是**快照指纹**，改表不会让任何门禁变红。

**测试为什么没抓到**：`tests/test_rules.py:383-389` 的 `test_tables_are_read_only` 只断言**外层**是 `MappingProxyType`（并试了一次外层 `setitem`）；`tests/test_rules.py:375-380` 的 `test_standard_rule_set_holds_no_mutable_state` 只扫 `vars(standard)`（`tables` 是另一个模块，扫不到）。两条守卫都守着外层，**内层没人守**。

**建议**：把 `_SHAPE_DOWN` 的内层也用 `MappingProxyType` 包起来（与 `KICK_TABLE` 对齐），并把 `test_tables_are_read_only` 的断言扩到内层。

---

### A5（低）`build_board` 不校验棋盘尺寸与 `cells` 一致

**位置**：`src/tetris_kernel/kernel/snapshot.py:349-363`

**问题**：`build_board(width, height, hidden_rows, cells)` 把四个参数原样交给 `Board`，**不检查 `cells` 的形状是否与 `width/height` 相符**。

**实测**（探针 P5）：

```
P5 mismatched board ACCEPTED width=4 height=3 cells= ((0, 0),)   ← 自称 4×3，实际 1 行 2 格
P5 empty cells ACCEPTED     width=4 height=3 cells= ()
```

**后果**：`structureFingerprint` 只覆盖 `cells`（架构 §5.4 的口径本就如此，**不是缺陷**），**不覆盖 `width` / `height` / `hiddenRows`** → 尺寸与内容对不上时，指纹照样算得出来、快照照样发得出去，存档恢复后可能是一块尺寸不符的棋盘，而回放比对**四项全绿**。

**建议**：`build_board` 校验 `len(cells) == height` 且每行长度 `== width`，不一致当场报错；或退一步，在 docstring 里写明「尺寸由调用方保证，本函数不校验」（但那样 A14 的不一致就没有拦）。

---

### A6（低）事件类型名在 kernel 侧复制了一份字面量

**位置**：`src/tetris_kernel/kernel/rng.py:48`（权威在 `src/tetris_kernel/contracts/events.py:311`）

```python
RNG_NEXT_EVENT = "rng:next"
```

**问题**：`contracts/events.py` 是事件类型名的唯一权威，`kernel/` 侧另写一份字面量。架构 §4.4 末条与 `contracts/types.py:96-98` 都写着「**引用处一律用常量，不复制字面量**」——S2–S6 汇总审查的 N5 正是为此补了 `types.NEXT_QUEUE`。

**缓解（比 N5 当时轻）**：`tests/test_rng.py:289-293` 断言 `RNG_NEXT_EVENT in EVENT_TYPES` 且命名空间为 `rng` → 契约那边改名会**当场变红**，不会静默分叉。

**建议**：kernel 侧改为引用契约里的名字，或让 `contracts/events.py` 导出一条单事件常量供引用。属洁癖级，但成本极低。

---

### A7（低）`ExtensionEntry` 复制了契约里的标量联合

**位置**：`src/tetris_kernel/kernel/snapshot.py:63`（权威在 `src/tetris_kernel/contracts/types.py:117`）

```text
ExtensionEntry = tuple[str, int | float | str | bool | None]        # kernel 侧
SCALAR_MAP = "tuple[tuple[str, int | float | str | bool | None], ...]"   # contracts 侧
```

**问题**：同一份标量联合写了两遍。`_assert_contract_alignment()`（`snapshot.py:452-457`）钉死了**字段注解**与 `SCALAR_MAP` 逐字相等，但 **`ExtensionEntry` 这个别名不在校验范围内** → `SCALAR_MAP` 改了，别名不会跟着改，两处**静默分叉**（N5 的病根）。

**建议（注意代价）**：要让别名也走常量，得在 `contracts/types.py` 加一个标量联合常量、再由它拼出 `SCALAR_MAP` —— 那会**新增一条基线条目**（355 → 356），撞 S17 的基线生成。所以要么在 S17 前一并处理，要么先在 `kernel/snapshot.py` 里加一句显式断言把两者钉住。

---

### A8（低）`import_state` 之后 `seed` 属性变成误导信息

**位置**：`src/tetris_kernel/kernel/rng.py:106`（赋值）· `:115-118`（属性）· `:207-228`（`import_state`）

**问题**：`self._seed` 只在构造时赋值；`import_state` 换掉了整个 MT 状态，但 `rng.seed` **仍报构造时的种子**。存档恢复之后，拿 `rng.seed` 去复现会得到**另一局**。

**为什么值得记**：这个类的卖点是「可播种、可复现、**可审计**」（`rng.py:90-97`）。架构 §5.5 与 ADR-011 正是为了这件事要把 `rngStateVersion` 与 `pythonVersion` 记进录像头。一个会骗人的 `seed` 属性，与「可审计」直接冲突。

**建议**：`import_state` 后把 `seed` 置成 `None`（或另给一个 `stateSeed`），或者至少在 docstring 里写明「`seed` 只表示**建实例时**的种子，状态恢复后不代表当前状态」。

---

### A9（低）越界状态值抛 `OverflowError`，与本模块其它校验不一致

**位置**：`src/tetris_kernel/kernel/rng.py:228`（对照 `:221` 与 `:224`）

**问题**：`import_state` 的两条自带校验（长度不符 / 版本不符）抛 `ValueError`，但把越界状态值交给 `random.setstate` 时，CPython 抛的是 **`OverflowError`**。

**实测**（探针 P7）：

```
P7 tampered state rejected: OverflowError
```

**后果**：调用方若按 `except ValueError` 兜「非法状态输入」，会漏掉这一类。**不会静默出错**（异常照样抛出），所以级别低。

**建议**：在 `setstate` 之前自己校验取值范围，统一抛 `ValueError`。

---

### A10（低）「只用两个原语」的 AST 守卫认不全写法

**位置**：`tests/test_rng.py:224-228`

```python
used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
assert used & FORBIDDEN_PRIMITIVES == set()
```

**问题**：这条守卫**只收集属性节点**。`from random import shuffle` 之后的 `shuffle(items)` 是**名字节点**，属性集是空的 → **守不到**。

**实测**（探针 D，对同一份样例文件用两种方式检查）：

```
purity script findings: [(5, 'random.shuffle()：模块级随机数不可播种、不可复现')]
unit-test style AST check catches it: False
attrs seen by the AST check: []
```

**缓解**：`tools/check_kernel_purity.py` **认得**这种写法（它先还原导入绑定再判调用形状），而 `tests/test_rng.py:210-221` 的纯净度用例确实覆盖了 `rng.py` → **真门禁没漏**。所以这不是「没有守卫」，而是「**重复守卫里较弱的那条**」。

**建议**：让这条 AST 用例复用 purity 脚本的绑定还原逻辑，或干脆断言 purity 脚本对 `rng.py` 的结论——**两条守卫强度不同、却看不出差别**，容易让人以为 AST 那条更严。仓库自己的口号就是「守卫要能触发，漏网的守卫等于没守」。

---

### A11（低）纯净度用例的扫描范围已过期

**位置**：`tests/test_rng.py:210-221`

**问题**：这条用例只查 `rng.py` 与 `fingerprint.py` 两个文件，docstring 给的理由是「冻结区里还有别的写手正在写的文件，全目录扫描会把他们的半成品算到我头上」。**批次 A 已经合入**，冻结区（`kernel/` + `rules/`）现在是完整的。

**实测**：真门禁 `python tools/check_kernel_purity.py` 扫的是**整个冻结区**（2 个目录，违规 0，exit 0）✓ —— 所以覆盖面没问题，**只是这条单元级用例的说明过期了**。

**建议**：把范围扩到冻结区，或在 docstring 里注明「真门禁覆盖全冻结区，本用例只做单元级回归」。

---

### A12（低）三处文档把 S7 记成「25 passed」，该提交实际含 27 条用例

**位置**：`PROGRESS.md`（待办第 1 项）· `docs/plan-M0.md`（S7 完成判据）· `ai_docs/261003.md`（11:02 条目的表格行）

**实测**：

```
tests/test_rng.py 现在跑         → 27 passed
git show a2af287:tests/test_rng.py 里 def test_ 计数 → 27
git diff a2af287..HEAD -- tests/test_rng.py → 无变化（该文件自 a2af287 起没动过）
```

即：**提交 `a2af287` 里就含 27 条用例**，不是 25。

**总数不受影响**：`ai_docs` 的验证行写「157 契约 + 25 rng + 24 snapshot + 34 rules + 3 导出一致性 + **2 本轮新增**」= 245 —— 算术自洽（多出的 2 条是集成时给 `import_state` 版本校验补的用例），而实测 **245 passed** 也确实对上。所以**问题只在提交行 / 完成判据里的那个「25」与提交内容不符**。

**为什么记**：这正是 S2–S6 汇总复核总结的那条教训 ——「**报告自陈计数有误**」「结论对不代表过程省」。三个文件里同一处数字对不上，下一个读者复跑 `pytest tests/test_rng.py` 会拿到 27 而不是 25。

**建议**：三处统一改成 27，或在提交行注明「25 + 集成时补的 2 条」。

---

### A13（低）横向重力下 `linesDelta` 用组数当消行数

**位置**：`src/tetris_kernel/rules/standard.py:241`

**问题**：`cleared = len(groups)`。消行轴是 `row` 时组数 = 消的**行**数 ✓；是 `column` 时组数 = 消的**列**数，而快照的 `lines` 是「累计消行数」。

**实测**（探针 P9，3 行棋盘消 1 列）：

```
P9 column groups: 1 linesDelta: 1 board rows: 3      ← 消了 1 列（3 格），linesDelta 记 1
```

**为什么现在跑不到**：形状表 / 踢墙表只填 `down` 轴，横向重力下 `shape_cells` 直接报错（属 M4-A5）。但 `scan_clears` 四个轴都能跑。

**建议**：在 M4-A5 开工前把「横向重力下 `lines` 怎么算」写进 `plan-M0.md`，别到时候默默沿用这一条。

---

### A14（低）行 / 列两条路径对「对不齐的棋盘」口径不一致

**位置**：`src/tetris_kernel/rules/standard.py:99-102`（行）· `:103-108`（列）

**问题**：行路径按**该行自己的长度**判满（`all(cell != 0 for cell in row)`）→ 2 格的短行只要非零就算满；列路径按**各行长度的最小值**判满（`_board_width`）。

**实测**（探针 P10，`[[1,1,1],[1,1]]`）：

```
P10 ragged row groups: 2 组，第二组 cells 只有 2 格
```

**为什么现在跑不到**：正常棋盘不会对不齐（`build_board` 也不校验，见 A5）。但**两条路径口径不一致本身是隐患**：同一个「不整齐」的行集合，按行判和按列判会得出不同的结论。

**建议**：与 A5 一起处理 —— 要么 `build_board` 挡住对不齐的棋盘，要么两条路径统一用同一个宽度口径。

---

### A15（低）恒真断言

**位置**：`tests/test_rng.py:424`

```python
assert real.now_ms() <= real.now_ms()
```

同一个表达式调用两次比较，**永远为真**。意图显然是「`now_ms` 单调不减」，正确写法应是与**上一次取值**比：

```python
now = real.now_ms()
assert real.now_ms() >= now
```

---

## 5. 复核过、确认没问题的部分

这一节记的是**查过之后确认成立**的部分 —— 免得下一轮重复查，也免得把「没查」误当成「没问题」。

### 5.1 随机源（S7）

| 检查项 | 结论 |
| --- | --- |
| 只用两个原语 | ✓ `rng.py` 内只调 `self._rng.random()` / `self._rng.getrandbits()`；`random.Random(seed)` **全文仅 1 处**（`rng.py:107`）；`shuffle` / `sample` / `choice` / `randrange` 零命中 |
| 扁平化状态 | ✓ `export()` → **626** 个 int（`version=3` + 625 个状态值）；`import_state` 复原成 `(imported_version, tuple(state[1:]), None)` |
| ADR-011 版本检测 | ✓ **用导入状态自带的 version 复原**，版本对不上当场 `ValueError`（`rng.py:222-227`）—— 集成时抓到的那处真缺陷**修对了** |
| `gauss_next` 非 `None` 时导出报错 | ✓ `RuntimeError`（`rng.py:203-204`），有对应用例 |
| 状态指纹跨进程稳定 | ✓ 探针与用例双证：不同 `PYTHONHASHSEED` 的子进程算出的指纹与本进程相等；`blake2b` / 16 字节 / 小写 hex（32 字符） |
| 指纹不用内置 `hash()` | ✓ `kernel/fingerprint.py` 用 `hashlib.blake2b`；与 `tools/_toolkit.py` **同算法同摘要长度**（用例 `test_blake2b_hex_matches_toolkit_algorithm` 钉住） |
| TLV 键边界无歧义 | ✓ 键与值都带 4 字节长度前缀 → `("ab", b"c")` 与 `("a", b"bc")` 不撞；重复键报错；顺序由调用方定（函数不擅自排序） |
| 整数编码无碰撞 | ✓ 探针 P6：13 个边界值（0 / ±1 / 127 / 128 / −128 / −129 / 255 / 256 / 32767 / 32768 / −32768 / 2⁴⁰）编码**两两不同** |
| `rng:next` 负载常数级 | ✓ 三个字段（`value` / `seq` / `stateFingerprint`），**不含 625 元素的状态数组**（ADR-060） |
| `rng:next` 与契约一致 | ✓ 探针 P11：契约声明的负载是 `('value', 'seq', 'stateFingerprint')` / 类型 `('float | int', 'int', 'str')`，与 `RngNextPayload` **逐字一致**；事件已登记、命名空间 `rng` ✓ |
| 指纹取「该次取值之后」的状态 | ✓ 探针 P12：最后一条草稿的指纹 == 当前状态指纹 |
| 7-bag 洗牌 | ✓ 自己实现 Fisher-Yates，下标用 `getrandbits()` + 拒绝采样；探针 P8：7 个方块 → **6 条草稿**（6 次换位），草稿值就是取到的下标 |
| `next_bag` 的拒绝采样口径 | ✓ 被拒候选消耗状态但不产草稿 —— **plan-M0.md S7 段明写了这个口径**，不算缺陷 |
| 时钟 | ✓ 真实 / 虚拟同一接口 `Clock`（结构化子类型）；虚拟时钟只随 `advance()` 变；步长与帧数都拒非法值 |
| **时钟落 `host/` 的正确性** | ✓ 架构 §5.3 只禁 `kernel/` 与 `rules/` 读墙钟；`tools/check_kernel_purity.py` 的默认扫描范围就是**冻结区两目录**（`FROZEN_DIRS`）—— `host/clock.py` 的 `time.monotonic()` **不违规**。**注**：若手工把 `src/` 整棵树喂给 purity 脚本，它会报 2 条 blocking（都在 `host/clock.py`），那是**扫描范围用错**，不是代码违规 |

### 5.2 快照与只读视图（S8）

| 检查项 | 结论 |
| --- | --- |
| 字段与契约**逐字段相等** | ✓ `Snapshot` 21 / `Board` 5 / `Active` 4 —— **导入期**逐字段自检（字段名 / 顺序 / 类型串），外加测试双保险 |
| 引用契约常量的五个字段 | ✓ `BOARD_CELLS` / `VECTOR2` / `NEXT_QUEUE` / `SCALAR_MAP` / `INT_TUPLE` **逐字相等** |
| 深拷贝 | ✓ 逐行重建成 tuple（不只是换外层容器），改内核棋盘不影响快照、改快照内层抛 `TypeError` |
| 两个指纹不混 | ✓ 结构指纹（棋盘内容，不含扩展）与扩展指纹（key 排序的规范化摘要）是**两个哈希**；空扩展槽也带域标签 |
| 布尔与整数区分编码 | ✓ `_encode_scalar` 先判 `bool` 再判 `int`，标签不同 → 摘要不同 |
| 浮点按内存值编码 | ✓ IEEE 754 双精度小端，**不基于 JSON 文本**（架构 §5.4） |
| 域分隔写进「值」 | ✓ 扩展槽 key 叫 `domain` **不撞键**（用例钉住） |
| `rngState` 两条构造路径 | ✓ `build_snapshot` 恒 `None` 且**签名里根本没有 `rngState` 参数**（用例查签名）；存档路径走 `dataclasses.replace` |
| 视图构造纯读 | ✓ 不 import `random`（AST 用例钉住）、不改入参、同入参两次构造结果相等 |
| 快照不可变 | ✓ `frozen=True`（改字段抛 `FrozenInstanceError`） |

### 5.3 规则层（S13）

| 检查项 | 结论 |
| --- | --- |
| 8 条签名与冻结声明一致 | ✓ 探针 P13：`signature_mismatches(STANDARD_RULESET)` 返回**空元组**；守卫本身**能触发**（有对应用例造坏签名集） |
| 依赖方向 | ✓ `rules/` 不 import `kernel`（AST 用例逐文件查）；`lint-imports` **4 kept** |
| 规则集不可变、无状态 | ⚠️ **表的外层不可变，内层可变**（见 A4） |
| **形状表内容正确性** | ✓ **28 组坐标逐条核对，与 SRS 基准一致**（I / J / L / O / S / T / Z × 4 朝向；含 O 块四朝向相同、其余六块四朝向互异）—— **人工逐条核对**，非机器比对 |
| 踢墙表 16 种转向全覆盖 | ✓ 顺时针 4 + 180° 4 + 逆时针 4 + 同向 4，M0 全部为恒等偏移 `(0, 0)`（plan 明写这是 M1 的开门判据） |
| 重力向量是向量 | ✓ 四个轴都有向量；横向重力 `dy = 0` |
| `scan_clears` 给轴 + 索引 + 格子清单 | ✓ 不是只给数量；格子清单是**整条线**（架构 §7.3.1） |
| `classify_placement` 的棋盘口径 | ✓ 与 S12 / S14 的约定一致：棋盘**含本次放置、尚未消行** |
| 分类搬运而非重判 | ✓ `scan_clears` 产出的组 `classification` 恒 `None`，内核挂上后再交 `score_clear` |
| `RuleContext.stepMs` 没被发明含义 | ✓ 标准实现**完全没用** `stepMs` —— 契约层写明「语义待定，实现期按 major 变更流程定，不要在实现期临时发明一个含义」，这条守住了 |
| `rules/` 无第三方 import / 无墙钟 | ✓ purity 脚本对 `rules/` **0 违规** |

### 5.4 导出面与文档

| 检查项 | 结论 |
| --- | --- |
| 三个包级 `__init__.py` | ✓ `__all__` 与实际公开名**一一对应**（`tests/test_package_exports.py` 钉住：漏一个、多一个都红） |
| 导出面归属 | ✓ 三个包级 `__init__.py` 都是**集成者**一笔收口（`a9355c7`），没让写手各写一行 ✓ |
| `contracts/` 一字未动 | ✓ 批次 A 的 diff 里**没有任何 `contracts/` 文件**；基线仍 **355** 条（探针实测 `collect_entries()` = 355） |
| 文档与代码 | ✓ plan 的 S7 / S8 / S13 三段的「完成判据 / 提交 / 实现补记」与代码一致（**除 A12 的那一处计数**） |

---

## 6. 质量门实跑

审查基线 `f89ce9a`，conda 环境 `py312`（3.12.13）。

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **245 passed**（1.42s）= 157（既有）+ 27（`test_rng`）+ 24（`test_snapshot`）+ 34（`test_rules`）+ 3（导出一致性） |
| `python -m ruff check .` | **All checks passed!** |
| `python -m ruff format --check .` | **57 files already formatted**（**批次 A 树、写本报告之前**） |
| `python -m pyright`（不带路径，走 `include = ["src", "tools"]`） | **0 errors, 0 warnings, 0 informations** |
| `lint-imports` | **Contracts: 4 kept, 0 broken**（exit 0） |

机检（批次 A 自己声称的四项）：

| 命令 | 结果 |
| --- | --- |
| `python tools/check_kernel_purity.py` | 冻结区 2 目录 · **违规 0 · 警告 0**，exit 0 |
| `python tools/check_event_registry.py` | **违规 0**，exit 0 |
| `python tools/contract_baseline.py --checklist-only` | exit 1（**预期**：清单第 14 / 16 项未勾，与基线未生成一致） |
| `collect_entries()` | **355** 条（与修订前逐条相同） |

> 五个脚本的退出码（0 / 0 / 0 / 0 / 2）中，最后一个是 `--checklist-only` 的**预期**退出码（清单未完成），不是失败。

> **口径说明（重要）**：以上全部数字测的是**批次 A 的已提交树**（`feature/batch-a` @ `f89ce9a`）。**审查期间工作区被并行推进批次 B 的会话占用**——分支已切到 `feature/batch-b`，并有 `src/tetris_kernel/bus/{bus,envelope,subscriber}.py` 三个**未跟踪的在途文件**。那些文件**不在本轮范围内、未被审查**，它们一度让 `ruff check` / `ruff format` 报错、让 `pyright` 报 3 个错（`bus/` 的 `handler` 属性未声明）——**那些错不是批次 A 的**，作者切分支或提交批次 B 时自会消解。本报告的每条结论都只指向 `f89ce9a` 及更早的批次 A 提交。
>
> `ruff format` 的 57 = 批次 A 树上的 .py 与 .md（**该命令也统计 `.md`**）；本报告入库后 +1。
>
> 另注：报告文件本身**尚未提交**，且当前工作区在 `feature/batch-b` 上——按 `plan-M0.md` §1.4 纪律 6，它应当**随批次 A 的修正提交一起入库**，即需在 `feature/batch-a` 分支上提交，**不要带进批次 B 的分支**。

---

## 7. 覆盖率

| 项 | 数 |
| --- | --- |
| OCR 预览文件总数 | **17** |
| 可审文件（`reviewable_files`） | **10** |
| 已审查 | **10** |
| 跳过 | **0** |
| 额外审查（OCR `default_path` / `unsupported_ext` 排除） | **7**（4 个测试文件 + 3 份 `.md`） |
| **覆盖率** | **17/17 = 100%** |

**排除文件处置**：4 个测试文件**逐行审查并计入覆盖率**（本批次的机器担保大半在它们身上）；3 份 `.md` 作为**审查基准**读取（`plan-M0.md` 的 S7 / S8 / S13 段 · `PROGRESS.md` 的状态与待办 · `ai_docs/261003.md` 的 11:02 条目），本报告的每条结论都指向具体行号。

---

## 8. 残余风险（非缺陷，供作者决定）

三点是**设计上的取舍或已知边界**，代码与文档都写明了，**本报告不要求改**：

1. **形状表 / 踢墙表只填 `down` 轴**（其余三轴留空位、调用即报错）。横向重力的形状 / 踢墙变换属 **M4-A5**，`plan-M0.md` S13 段明写「别当成没做完」✓
2. **标准踢墙表只做恒等偏移**（唯一候选 `(0, 0)`，16 种转向全覆盖）。逐条验证是 **M1 的开门判据**，M0 做掉反而让 M1 的判据失去意义 ✓
3. **T-Spin 只一档**（`none` / `tspin`）。Mini 与三档区分是 **M1**；`classKey` 属规则集定义域，契约层不钉 ✓

另有两点**不是缺陷但值得作者知道**：

- **`b2b` 与 `combo` 的语义当前没有权威定义**（A1 / A2 的根因）。`contracts/rules.py` 的 `ScoreState` docstring 只说「与快照的计分字段同名同义」，没说 `b2b` 这一位**到底表示什么**。**趁 S9 / S12 还没消费它，先把这句写清楚**，成本几乎为零。
- **`tests/` 仍不在 pyright 覆盖内**（`include = ["src", "tools"]`）。批次 A 又新增了 **1371 行测试代码**没有类型检查——上一轮报告里这条缺口是「1825 行」，现在更大了。

---

## 9. 未验证 / 不作评价的部分

- **基线文件本身尚未生成**（按计划 S17 生成）→ 本报告所有「进基线 / 不进基线」的判断都是**口径推导**，没有真实基线文件的哈希可比。本轮只实测了**条目数仍为 355**。
- **形状表的 SRS 正确性是人工逐条核对的**（28 组坐标），不是机器比对——仓内没有 SRS 基准表可比。
- **`ai_docs/` 与 `PROGRESS.md` 的内容质量**只做了「与代码是否相符」的核对（A12），未评价其写作质量。
- **未复跑各阶段提交说明里的反向验证原文**（例如 plan 里写的「跨进程指纹在 `PYTHONHASHSEED=0/12345/999` 三次全等」）——本轮做的是独立复跑，**用例里只固化了 `0` 与 `12345` 两组**，`999` 未固化。
- **批次 B（`feature/batch-b`）不在本轮范围内**，本报告不对其做任何判断。审查期间该分支上有**未提交的在途文件**（`src/tetris_kernel/bus/{bus,envelope,subscriber}.py`）——**未审查**，且它们造成的 lint / 类型报错**与批次 A 无关**（见 §6 口径说明）。
- **本报告的每条结论都锚在 `f89ce9a` 这棵已提交树上**；批次 B 后续的提交**没有**改变批次 A 的任何一行代码（`git diff f89ce9a..09cef8e` 只动了文档）。
- **并行写手之间的接缝**（§1.4.4 先验定的签名）只核对了「指纹函数的签名」这一处；本轮没有逐个复核「写手 A2 消费 A1 的 `fingerprint.py`」是否与先验定完全一致——**实测结果一致**（A2 的 `snapshot.py` 用的正是 `tlv_digest` / `blake2b_hex` 两个函数，签名与 `kernel/fingerprint.py:45/55` 一致）。

---

## 10. 给作者的修正建议（按优先级）

**第一批 · 建议在 S9 / S12 开工前做完**（这三处都会被下游直接消费）

1. **A1** —— 定死 `b2b` 的语义并修实现，补 `False→True` 的用例（A1 的「路线甲 / 路线乙」两条都可行，**本报告不替项目选**）。
2. **A2** —— 定死「连击何时中断」，写进 `plan-M0.md` 的 S13 段与 `ScoreState` 的 docstring。
3. **A3** —— `build_snapshot` 内部规范化 `extensions`（或改成只接受 `build_extensions` 的产物）。

**第二批 · 成本极低、建议同批修掉**

4. **A4** —— 形状表内层改 `MappingProxyType`（与 `KICK_TABLE` 对齐）+ 断言扩到内层。
5. **A5 + A14** —— `build_board` 校验尺寸（一次挡住两个）。
6. **A12** —— 三处文档的「25 passed」改成 27。
7. **A15** —— 恒真断言改写成真正的单调性断言。
8. **A9** —— 越界状态值统一抛 `ValueError`。

**第三批 · 可选或挂账**

9. **A6 / A7** —— 常量引用收口（A7 的修法会动基线条目数，**建议与 S17 的基线生成一并决定**）。
10. **A8** —— `seed` 属性在状态恢复后的语义。
11. **A10 / A11** —— 两条重复守卫的强度对齐与说明更新。
12. **A13** —— 横向重力的 `lines` 口径，写进 M4-A5 之前。

> 按 `plan-M0.md` §1.4 纪律 7：报告交回后**逐条核实**，成立项在 `feature/batch-a` 上修正，**修正提交单独一笔**（`修复：按 batch-a 审查报告修订…`），不与新功能混提。本报告 §3 的 15 条**建议逐条裁定**，不必照单全收——**每条都附了可复现的实测**，不接受时也请写明理由。

---

## 附：本次审查的自我约束

- **只报有把握的真缺陷**：每一条都附了**可复现的探针输出**或**具体行号 + 机制说明**；风格类不阻塞。
- **不硬凑**：15 条里没有一条是「其实这样也行」的 —— 每条都写清了「在什么条件下才会咬人」。
- **区分「缺陷」与「口径缺口」**：A2 与 A13 明确标出「当前没有定义」，没有替项目发明语义。
- **假阳性自查**：`host/clock.py` 的 `time.monotonic()` 曾疑似违反纯净度纪律，**实跑 purity 脚本后确认不是**（默认扫描范围是冻结区两目录），已写进 §5.1 备查。
- **计数与行号全部复跑过**：`def test_` 计数、pytest 逐文件条数、基线条目数、探针输出均来自本次实跑，不是转述提交说明。
