# S4 阶段代码审查报告（委托模式）

> **审查对象**：M0 阶段 B 的 S4 —— 快照契约声明（21 顶层 + `board` 5 子字段 + `Active` 4 子字段）与 `PlacementInfo`（6 字段）。
> **代码范围**：`d294e8b`「新功能：新增快照字段声明与 PlacementInfo 结构」（commit 模式）。其后的 `bbc2596` 是纯文档提交（`docs/` + `PROGRESS.md` + `ai_docs/`），不含 `src/` 与 `tests/` 改动，已核对：`git diff d294e8b..HEAD -- src tests` 为空。
> **审查方式**：`ocr delegate` 委托模式（OCR 只做文件选择与规则解析，审查判断由本 Agent 完成）。
> **审查日期**：2026-10-23
> **审查基线**：需求 `docs/需求与边界.md` §8.3 · 架构 `docs/项目架构.md` §4.4 末条 / §5.2 / §5.4 / §7.3.1 · spec `docs/spec-M0.md` §6.5 / §7.1 · 契约清单 `docs/契约v1清单.md` 第 3 / 4 / 10 / 12 / 15 项 · 计划 `docs/plan-M0.md` S4

---

## 1. 结论

**声明本身对得上需求 §8.3：21 个顶层字段的名、类型、顺序，`board` 的 5 个子字段，`Active` 的 4 个子字段，`PlacementInfo` 的 6 个字段——逐条核对一致，没有一处偏离。这份声明可以进基线。**

新增的**类型文法形态 9（命名结构引用）**是这一轮最关键的一步：需求 §8.3 把 `board` / `active` 写成结构类型，封闭文法原先没有这一形态，不补就得写文法认不出的串——正是"看着在基线里、实际没被哈希"的失效模式。补得对，且把"命名结构不是自由别名、成员表必须在 `contracts/` 内另行声明"写进了附加规则 4。

有 **3 项中等**需要在 S6 写基线脚本之前处理，共同点是**规则与机检没有对齐**：文法表附加规则 4 要求"成员表没声明的结构名一律不合规"，但回验把结构名硬编码在测试里；`KERNEL_STATE_TO_SNAPSHOT` 只有快照侧是机器可读的，内核侧还是散文；`RUNTIME_ONLY_STATE` 的守卫在当前数据下不可能触发。另有 6 项低。

- 四条质量门全绿：`pytest` **130 passed** · `ruff check` 全过 · `ruff format --check` 33 files · `pyright src/tetris_kernel` 0 错 · `lint-imports` 4 kept / 0 broken。
- 需求 §8.3 的 21 个顶层字段**逐条核对一致**（名、类型、顺序三项都对）；`board` 5 子字段、`Active` 4 子字段、`PlacementInfo` 6 字段同样逐条一致。
- 坐标系 5 条约定与需求 §8.3 的注逐条相等；`hiddenRows` 写死为「数量」而非「可见起始行」——这处歧义被显式钉住了。
- **无高危 / 严重问题**。S4 的声明质量明显高于 S2 / S3 两轮。

---

## 2. 覆盖统计

| 项 | 值 |
| --- | --- |
| total_files（preview 检出） | 5 |
| reviewable_files | 4 |
| excluded_files | 1（`tests/test_contracts_declarations.py`） |
| reviewed_files | 5（4 个 reviewable + 被 `default_path` 排除的测试文件） |
| skipped_files | 0 |
| coverage_rate | **100%（代码文件 4/4；含测试文件 5/5）** |

| 文件 | 状态 | 行数 | OCR 判定 |
| --- | --- | --- | --- |
| `src/tetris_kernel/contracts/snapshot.py` | 新增 | +237 | reviewable |
| `src/tetris_kernel/contracts/placement.py` | 新增 | +43 | reviewable |
| `src/tetris_kernel/contracts/types.py` | 修改 | +10 −1 | reviewable |
| `src/tetris_kernel/contracts/__init__.py` | 修改 | +22 | reviewable |
| `tests/test_contracts_declarations.py` | 修改 | +410 −3 | excluded（`default_path`）——**本报告仍逐行审查了它**：S4 的机检担保全在这个文件里 |

**OCR 命令留痕**：`ocr delegate preview --format json -c d294e8b` → `ocr delegate rule --format json <5 个路径>`（规则组 `system`，覆盖 `**/*.{py,pyi,ipynb}`）。

**被排除文件仍计入覆盖率的理由**：OCR 的 `default_path` 规则把 `tests/**` 整体排除，理由是"测试不是产品代码"。但 S4 的正确性担保恰恰全在这个文件里——`SNAPSHOT_FIELDS` / `BOARD_FIELDS` / `ACTIVE_FIELDS` / `KERNEL_STATE_TO_SNAPSHOT` 的机检、类型文法回验、公共叶子纯净度扫描，没有一条在 `src/` 里。漏审它等于没审 S4。

---

## 3. 发现清单

### 3.1 Medium

#### M1 · 形态 9 的成员表绑定没有机检——文法表规则与 S4 回验口径不一致

| 字段 | 值 |
| --- | --- |
| path | `tests/test_contracts_declarations.py` |
| start_line / end_line | 1023–1024（`_NAMED_STRUCTS`）、1069–1073（`parse_atom` 分支） |
| category | test / documentation |
| severity | medium |

**问题**：`types.py` 的附加规则 4（第 51–58 行）把形态 9 的合规条件写得很死：

> 命名结构**不是自由命名的别名**：成员表必须在 `contracts/` 内另行声明（`snapshot.py` 的 `BOARD_FIELDS` / `ACTIVE_FIELDS`），S6 解析器把它当引用处，哈希按成员表展开计算——**成员表没声明的结构名一律不合规**。

但 S4 的文法回验把这份绑定**硬编码在测试里**：

```python
_NAMED_STRUCTS = frozenset({"Board", "Active"})
```

这个集合与 `BOARD_FIELDS` / `ACTIVE_FIELDS` **没有任何联动**——测试里没有任何一处断言"`_NAMED_STRUCTS` 里的每个名字都有一张成员表"或"成员表的键集合就是结构名集合"。

**实测（反向验证 RV1）**：把测试模块里的 `BOARD_FIELDS` 与 `ACTIVE_FIELDS` 置空（模拟成员表被改名或删除），`test_all_type_strings_match_grammar` **仍然全绿**。`snapshot.board` 的 `"Board"` 与 `snapshot.active` 的 `"Active | None"` 照旧解析成形态 9。

**后果**：规则说"成员表没声明的结构名一律不合规"，而唯一守着这条规则的测试看不见成员表。真正执行它的是 S6 的基线脚本——**规则与机检的落差要到 S6 才暴露**，那时改起来比现在贵。

**建议**：让结构名从成员表推导，而不是反过来：

```python
_NAMED_STRUCTS = {"Board": BOARD_FIELDS, "Active": ACTIVE_FIELDS}
```

解析时按名字取成员表，并额外断言"形态 9 出现的名字必须在这张映射里"——成员表一改名，回验立刻变红。

---

#### M2 · `KERNEL_STATE_TO_SNAPSHOT` 只有快照侧是机器可读的，内核侧仍是散文

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/snapshot.py` |
| start_line / end_line | 192–214（映射表）、18–19（docstring） |
| category | maintainability / documentation |
| severity | medium |

**问题**：这张表是契约清单第 15 项（「快照 = 内核状态全集」）的机器可读落盘，docstring 自述"内核状态字段与快照字段**一一对应**"，spec §7.1 第 314 行也自称已落"机器可读版"。但**只有右侧（快照侧）是机器可读的**。

实测数据（探针 PROBE 3）：

| 指标 | 值 |
| --- | --- |
| 总行数 | 21 |
| 不同 target 数 | 21（快照侧一对一 ✓） |
| 不同 source 数 | **19** |
| 重复出现的 source | `extensions`（2 行）、`hold / holdUsed`（2 行） |
| 含「 / 」的复合 source | `hold / holdUsed`、`active（类型 / 朝向 / x / y，可空）` |

也就是说：

- `board.width/height/hiddenRows/cells` **四个**内核字段被压成**一行**；
- `active（类型 / 朝向 / x / y，可空）` 把四个字段 + 可空性塞进一个散文串；
- `hold / holdUsed` 用**斜杠复合**表示两个内核字段，机器无法把它拆成两个源。

**后果**：现有断言 `targets == set(SNAPSHOT_FIELDS)` 守的是"**每个快照字段都有来源**"；但契约清单第 15 项真正要的纪律是反向的——"**内核新增一个状态字段，快照必须同步新增一个字段**"。**内核侧是散文，这条纪律现在守不住**：S8 落地内核状态时新增一个字段，映射表不会红。

**建议**：source 侧改成一行一字段（`board` 的 4 个子字段拆成 4 行、`active` 拆成 4 行、`hold` 与 `holdUsed` 各一行），这样一个 source 对应一个 target，"一一对应"就名副其实；反向断言也就能写成"内核字段清单 ⊆ 映射表的 source 集合"。

---

#### M3 · `RUNTIME_ONLY_STATE` 的"混进快照"守卫在当前数据下不可能触发

| 字段 | 值 |
| --- | --- |
| path | `tests/test_contracts_declarations.py` |
| start_line / end_line | 982–986 |
| category | test |
| severity | medium |

**问题**：

```python
assert len(RUNTIME_ONLY_STATE) == 6
assert not set(RUNTIME_ONLY_STATE) & set(SNAPSHOT_FIELDS), "运行时状态混进了快照"
```

第一条是自证长度（常量写死 6 项，就断言 6 项），恒真。第二条看着像真守卫，实则**在当前数据下不可能触发**：`RUNTIME_ONLY_STATE` 的 6 项是中文散文（"事件序号" / "轮次计数" / "命令数计数" / "同因拒绝合并表" / "订阅注册表" / "随机源实例本体"），而 `SNAPSHOT_FIELDS` 的键是英文标识符——两个集合的交集**结构上恒为空**。

**实测（RV5）**：把 6 项中的一项换成真实的快照键 `"score"`，断言立刻变红（"运行时状态混进了快照"）；放回中文散文则全绿。也就是说这个守卫**只在条目用英文标识符书写时才可能报警**，而实际数据用的全是中文——它守的是一个不会发生的情形。

**后果**：契约清单第 15 项的备注写的是"**有断言守**"，实际这个断言对真实数据是空的。

**建议**：要么把 `RUNTIME_ONLY_STATE` 改成英文标识符（与内核状态字段名同命名体系，守卫才有意义），要么把断言改成对**语义**的检查（例如：断言这 6 项都不对应任何快照字段的中文说明关键词），要么在文档里把"有断言守"降级为"已写定清单，语义检查待 S8 复核"。

---

### 3.2 Low

#### L1 · `PlacementInfo` 的 6 条类型串没有进文法回验

| 字段 | 值 |
| --- | --- |
| path | `tests/test_contracts_declarations.py` |
| start_line / end_line | 1139–1166（`_all_declared_type_strings`） |
| category | test |
| severity | low |

`_all_declared_type_strings()` 收了 types 常量 7 条、信封 5 条、元数据 3 条、44 条事件负载、16 条命令参数、快照三表（21 + 5 + 4）——**唯独没收 `PlacementInfo` 的 6 条 dataclass 注解**（实测：函数产出的标签里没有任何 `placement.*`）。

plan-M0 S4 自称"**全库**类型串按封闭文法回验"，在这 6 条上是漏的。它们目前只有"逐条等于 `EXPECTED_PLACEMENT_FIELDS`"的转录断言，**没有文法断言**——万一有人把 `kickOffset` 的注解写成文法认不出的串，转录表会先红，但红的原因不是"不合文法"，而是"抄错了"，两者混在一起。

**建议**：在 `_all_declared_type_strings()` 里补一段 `dataclasses.fields(PlacementInfo)` 的收集。

---

#### L2 · `board.cells` 的类型串是裸字面量，与本模块自述的做法不一致

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/snapshot.py` |
| start_line / end_line | 101–103 |
| category | maintainability |
| severity | low |

`FieldSpec` 的 docstring（第 53–54 行）自述：

> 本模块引用那里的取值域常量（`SCALAR_MAP` / `INT_TUPLE`），不复制字面量（架构 §4.4 末条）。

而 `extensions` 与 `rngState` 确实引用了常量（`SCALAR_MAP`、`f"{INT_TUPLE} | None"`），`board.cells` 却是裸字面量 `"tuple[tuple[int, ...], ...]"`。

实测：contracts/ 内这个字面量**只此一处**（`snapshot.py:101`），所以**目前没有分叉风险**。且它不能直接复用 `COORD_CELLS`（后者是 `(x, y)` 坐标对，形状不同——`board.cells` 是行优先的格子值数组），所以不是"该用现成常量却没用"的错。

**建议**：二选一——要么把该形状提成 `types.py` 的常量（`BOARD_CELLS`）让引用处用常量，要么把 docstring 的措辞改成"两个形状引用常量，`board.cells` 暂无对应常量"。当前写法读起来像"漏了"。

---

#### L3 · `SNAPSHOT_FIELDS` 留了一个可变的模块级别名，`MappingProxyType` 的只读性是半边的

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/snapshot.py` |
| start_line / end_line | 129、176–179 |
| category | bug / maintainability |
| severity | low |

`BOARD_FIELDS` 与 `ACTIVE_FIELDS` 都是内联 `MappingProxyType({...})`，没有别名；但顶层表多留了一个**可变 dict**：

```python
_SNAPSHOT_FIELDS: dict[str, FieldSpec] = {...}  # 129 行：可变
SNAPSHOT_FIELDS: Mapping[str, FieldSpec] = MappingProxyType(_SNAPSHOT_FIELDS)  # 176 行
SNAPSHOT_FIELD_NAMES: tuple[str, ...] = tuple(_SNAPSHOT_FIELDS)  # 179 行
```

`MappingProxyType` 只挡"通过代理改"，不挡"通过底层 dict 改"。

**实测（RV3）**：往 `snapshot._SNAPSHOT_FIELDS` 里塞一个字段，公开的 `SNAPSHOT_FIELDS` 立刻从 21 变 22、`'injectedByMutant' in SNAPSHOT_FIELDS` 为真。（探针跑完已还原。）

而且**这个可变别名并非必需**：`SNAPSHOT_FIELD_NAMES` 完全可以写成 `tuple(SNAPSHOT_FIELDS)`（实测两者相等），别名就可以删掉，三张表也就统一成"内联 `MappingProxyType`"一种写法。

**为什么值得提**：这张表是"只增不改"基线的哈希源，任何一侧被改动都会撞红基线。留一个可变别名，等于在"声明面"上开了一个不打算走基线的旁门。

---

#### L4 · `EXPECTED_SNAPSHOT_FIELDS` 里 `extensions` 一项已不是需求的字面，"独立转录"的说法在这条上不成立

| 字段 | 值 |
| --- | --- |
| path | `tests/test_contracts_declarations.py` |
| start_line / end_line | 862（表头）、881（`extensions` 一项） |
| category | test / documentation |
| severity | low |

测试文件第 87–90 行声明：

> 下面几张表**独立转录**自 `docs/需求与边界.md` §8.3，与 `contracts/snapshot.py` 的声明互为对照——转录的是「需求写了什么」，不是「实现写了什么」。

但需求 §8.3 第 352 行的原文是：

| `extensions` | `tuple[tuple[str, JsonScalar], ...]` | 变体扩展槽：只放标量，key 唯一，内核不理解内容 |

`JsonScalar` 的定义在**架构 §5.4**（第 232 行）：`int` / `float` / `str` / `bool` / `None`。测试里写的是**展开后的字面量** `"tuple[tuple[str, int | float | str | bool | None], ...]"`。

展开值本身是对的（与 `SCALAR_MAP` 一致，另有一条 `test_snapshot_types_reference_shared_constants` 钉住 `types_map["extensions"] == SCALAR_MAP`）。但**一旦架构 §5.4 改了 `JsonScalar` 的定义，这条转录不会红**——它已经不再独立于实现，而测试文件的头注释仍说它独立。

**建议**：把这行的转录值改成需求的字面 `tuple[tuple[str, JsonScalar], ...]`，再单独加一条断言把它展开到 `SCALAR_MAP`；或者在表头注明"需求原文用 `JsonScalar` 记号，此处按架构 §5.4 展开"。

---

#### L5 · 快照 `phase` / `hold` / `next` / `rule` 的取值域仍无机器绑定，`phase` 在契约内有两套写法

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/snapshot.py` |
| start_line / end_line | 130–132、134–136（`phase` / `hold` / `next` / `rule`） |
| category | documentation |
| severity | low |

`contracts/commands.py` 第 121–123 / 163 行用**英文标识符**表达生命周期门禁三值：`not_started` / `paused` / `game_over`；而快照 `phase` 的说明写的是中文散文「待开始 / 运行 / 暂停 / 结束」，类型只有 `str`。`hold`（方块类型）、`next`（方块类型序列）、`rule`（规则集标识）同样只有 `str`，取值域无处可查。

需求 §8.3 原文就是 `str` + 中文散文，所以**这不算 S4 偏离需求**。但基线只哈希类型声明 `str`——两套写法都能"合法"进基线，消费方无法从声明推出该写 `running` 还是「运行」。

**说明**：这是 S2 报告 M6（取值域常量寄居 / 未绑定）在快照侧的同源问题，**S2 就已存在，不是 S4 引入**。S4 只是让它多了一处显形。列出供 S5 之后统一收口时参考，不阻塞 S4。

---

#### L6 · `contracts/__init__.py` 新增 10 个导出名，但仓库里没有 `__all__` 与实际导出一致性的断言

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/__init__.py` |
| start_line / end_line | 36–46（新增 import）、57–92（`__all__`） |
| category | test |
| severity | low |

本轮**手工核对通过**：57–92 行的 `__all__` 与 14–55 行的 import 完全对应（含 `FieldSpec` / `PlacementInfo` 两个类型名与大小写排序），S4 这次改动**没有漏项**。

但 `tests/test_contracts_declarations.py` 里搜不到 `__all__`——这个不变量没有任何测试守着。S4 一次加了 10 个导出名，下次再加就会静默漏项（漏了不报错，只是 `from tetris_kernel.contracts import X` 失败或名字进不了包级 API）。

**建议**：加一条断言 `set(contracts_pkg.__all__) == {n for n in dir(contracts_pkg) if not n.startswith("_") and n not in module_names}`，或更简单：`__all__` 的每个名字都能 `getattr` 到，且模块级 import 的每个名字都在 `__all__` 里。

---

## 4. 逐条核对结果（需求 §8.3 / spec §6.5 / §7.1）

| 核对项 | 依据 | 结果 |
| --- | --- | --- |
| 21 个顶层快照字段：字段名 | 需求 §8.3 表 | ✓ 逐条一致 |
| 21 个顶层快照字段：类型 | 需求 §8.3 表 | ✓ 逐条一致 |
| 21 个顶层快照字段：**枚举顺序** | 需求 §8.3 表行序 | ✓ 一致 |
| `board` 子结构 5 字段（名 + 类型） | 需求 §8.3 棋盘子表 | ✓ 一致 |
| `Active` 子结构 4 字段（`pieceType` / `rotation` / `x` / `y`） | 需求 §8.3 | ✓ 一致 |
| 坐标系 5 条约定 | 需求 §8.3 的注 | ✓ 逐条相等 |
| `hiddenRows` = 数量（非可见起始行） | 需求 §8.3 的注 | ✓ 写死 |
| `structureFingerprint` / `extensionFingerprint` 命名区分 | 需求 §8.3 的注 | ✓ 声明里点明，且注明结构指纹不含扩展字段 |
| `PlacementInfo` 6 字段（名 + 类型 + 顺序） | spec §6.5（架构 §7.3.1） | ✓ 一致 |
| `PlacementInfo` 放 `contracts/` 不放 `rules/` | spec §6.5 四条纪律 / ADR-061 | ✓ 位置正确，理由写在 docstring |
| `PlacementInfo.cells` 用绝对坐标 `(x, y)` | spec §6.5 | ✓ 注解 == `COORD_CELLS`（有专门断言） |
| `kickIndex` / `kickOffset` 可空语义 | 架构 §7.3.1 | ✓ `int \| None` / `tuple[int, int] \| None` |
| 类型文法形态 9 的必要性与来龙去脉 | types.py 附加规则 4 | ✓ 已写明，并注明与 S3 记录的「字面量枚举形态」不是同一件事 |
| `contracts/` 公共叶子纯净度 | 架构 §2.3 / §4.1 | ✓ `contracts/` 内绝对导入只有标准库四项，无 `tetris_kernel.*` |

---

## 5. 本轮做的反向验证（实跑）

plan-M0 S4 记录的反向验证（改 `ghostDelta` 类型 → 断言变红）本轮**未复跑**，属历史结论。本轮另做 5 组，全部实跑：

| 编号 | 做法 | 结果 |
| --- | --- | --- |
| RV1 | 把测试模块的 `BOARD_FIELDS` / `ACTIVE_FIELDS` 置空，重跑 `test_all_type_strings_match_grammar` | **仍全绿** → M1 的直接证据 |
| RV2 | 用文法解析器试 `tuple[int,...]` / `tuple[ int , int ]` / `tuple[str,...]` | **全部接受** → 空白规范未钉死（当前所有串都被转录表钉住，风险有限，未列为发现） |
| RV3 | 改 `snapshot._SNAPSHOT_FIELDS` 后看公开代理 | 公开表 21 → 22 同步变化 → L3 的直接证据（跑完已还原） |
| RV4 | `tuple(SNAPSHOT_FIELDS) == SNAPSHOT_FIELD_NAMES` | ✓ 成立 → L3 的改法可行 |
| RV5 | 把 `RUNTIME_ONLY_STATE` 一项换成英文快照键 `"score"` | 断言变红；换回中文散文则全绿 → M3 的直接证据 |

探针脚本全部位于 `%TEMP%\ocr_s4\`，**任务结束前已删除**（见 §7）。

---

## 6. 文档侧核对（`bbc2596`，纯文档提交，不在代码审查范围）

| 项 | 核对结果 |
| --- | --- |
| `docs/契约v1清单.md` 第 3 / 4 / 10 / 15 项勾选 | ✓ 勾选依据（`SNAPSHOT_FIELDS` / `BOARD_FIELDS` / `ACTIVE_FIELDS` / `COORDINATE_CONVENTIONS` / `KERNEL_STATE_TO_SNAPSHOT` / `RUNTIME_ONLY_STATE` / `PlacementInfo`）在代码里都真实存在 |
| 第 15 项备注「有断言守」 | **部分不成立** —— 快照侧覆盖有断言守（M2 讨论），内核侧不成立（M2）；"不进快照"那条实际不触发（M3） |
| `docs/spec-M0.md` §7.1 补「机器可读版」指针 | 指针指向 `KERNEL_STATE_TO_SNAPSHOT` / `RUNTIME_ONLY_STATE`，存在 ✓；"机器可读"的成色见 M2 / M3 |
| `docs/plan-M0.md` S4「完成判据 → 达成」 | ✓ 与实跑一致（130 passed） |
| `docs/plan-M0.md` S4 任务第 5 条「内核状态字段 ↔ 快照字段一一对应」 | **措辞偏强** —— 只有快照侧一对一（M2） |
| `ai_docs/261003.md` S4 条目 | 自陈内容与代码一致；本轮未发现"日志说改了、代码没改"的情况（S2 报告 L5 那类问题在 S4 未复现） |

---

## 7. 未验证 / 本报告不负责的部分

1. **S6 基线脚本未落地**：形态 9「按成员表展开计算哈希」这条规则本轮**无法实测**——S6 的解析器还不存在。M1 说的"规则与机检落差要到 S6 才暴露"，指的是那个落差本身，不是说 S6 会做错。
2. **快照字段的语义未实现**：S4 只落声明，`Snapshot` / `Board` / `Active` 三个 frozen dataclass 要到 S8 才有。21 个字段在 M0 多为中性取值（spec R3），本报告只审声明面，不审实现面。
3. **需求 §8.3 的逐条对齐是人工核对**：测试里的 `EXPECTED_SNAPSHOT_FIELDS` 等表是**转录**（且 L4 指出一条已不是字面转录），不是机器解析文档得到的。文档改了、表没改，测试不会红。
4. **未复跑 plan-M0 S4 自己的反向验证**（改 `ghostDelta` 类型 → 变红），本轮反向验证是另做的 5 组（见 §5）。
5. **`ai_docs/` 与 `PROGRESS.md` 未改**：本次是代码审查，不是 M0 阶段任务执行，避免无关改动。报告交付后是否落档由你决定。

---

## 8. 建议的处理顺序

**S6 写基线脚本之前**：M1（结构名与成员表联动）、M2（映射表 source 侧拆成一字段一行）、M3（`RUNTIME_ONLY_STATE` 的守卫改成能触发，或文档降级）——这三条都是"S6 一旦落地就固化进基线"的口径，先改成本最低。

**可以不阻塞 S6**：L1–L6 全部。其中 L3 建议顺手改（一行），L4 建议顺手改（一行 + 一条注释）。

**已核对通过、无需动作**：§4 全部 13 项。
