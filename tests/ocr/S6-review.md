# S6 阶段代码审查报告 · 契约校验与基线脚本

> 审查对象：提交 `03053a9`「新功能：新增内核纯净度、事件登记与契约基线校验脚本」
> 审查方式：`open-code-review-delegate`（OCR 负责文件选择与规则解析，判断由宿主 Agent 完成）
> 审查基线：`32eb23b`（HEAD，文档提交，与 `03053a9` 的代码差异为空 → 报告行号可直接引用）
> 审查日期：2026-10-23

---

## 1. 结论摘要

**S6 的交付整体可用，核心机制经实测逐条有效；未发现高危（critical/high）问题。**

五个脚本 + 两个共享模块共 1646 行新代码，覆盖了 plan-M0 S6 要求的全部任务：契约基线的字段级哈希、形态 9 按成员表展开、清单门禁、冻结区内容哈希、事件类型登记、内核纯净度、帧成本采集口径。**关键机制我逐条实跑验证过，全部按预期生效**（详见第 6 节，30 组探针）。

**3 个中等问题全部落在同一类：守卫没能按承诺触发，或口径没落到实现上。**

| 编号 | 问题 | 后果 |
| --- | --- | --- |
| **M1** | 第三方 import 检查只看 `names[0]`，`import os, numpy` 整条漏过 | 纯净度检查**假绿**（且 import-linter 的 forbidden 契约是固定黑名单，`requests` 这类不在名单里的第三方照样漏） |
| **M2** | `--path` 指向不存在的目录时，脚本只往 stderr 打一行就当没事发生，退出码 **0** 并打印「检查通过」 | 路径打错 = 假绿 |
| **M3** | 「签名是冻结面」只落到参数级：**新增一整条第 9 条签名全是新键 → 全绿** | 冻结的 8 条签名（架构 §7.3.1）可以被整条追加而不报警 |

**7 个低优先级问题**：2 处误报风险（`"nt"` 被当平台名、`"note:todo"` 被当未登记事件类型）、1 处文档与实现不符（`--checklist`）、1 处潜在键冲突（当前 353 条实测无碰撞）、2 处文档口径未同步（架构 §8 / `types.py` 的「正则解析器」）、1 处类型检查覆盖缺口。

**总体判断：S6 可以进入 S7。** 三个中等问题都不阻塞 S7（随机源与时间源），但**建议在 S17 生成基线之前**修掉 M1 / M2（它们守的是 S7–S14 期间的内核代码），M3 可在生成基线时一并钉住。

---

## 2. 审查范围与方法

### 2.1 OCR delegate 选出的范围

```
ocr delegate preview --format json -c 03053a9
→ mode=commit · total_files=8 · reviewable_count=8 · excluded_count=0
   total_insertions=1646 · total_deletions=0
```

| # | 路径 | 状态 | +/− | 规则组 |
| --- | --- | --- | --- | --- |
| 1 | `tests/baseline/.gitkeep` | added | 0/0 | `default` |
| 2 | `tools/_toolkit.py` | added | +105 | `system`（`**/*.{py,pyi,ipynb}`） |
| 3 | `tools/check_event_registry.py` | added | +139 | `system` |
| 4 | `tools/check_frozen_baseline.py` | added | +167 | `system` |
| 5 | `tools/check_kernel_purity.py` | added | +309 | `system` |
| 6 | `tools/contract_baseline.py` | added | +397 | `system` |
| 7 | `tools/contract_grammar.py` | added | +293 | `system` |
| 8 | `tools/measure_frame_cost.py` | added | +236 | `system` |

**本次没有 `default_path` 排除的文件**（与 S5 轮不同——S5 的 `tests/test_contracts_declarations.py` 被排除，本轮 S6 未动测试文件，故无排除项）。

### 2.2 上下文基准

逐条核对了以下规格来源（人工核对，非机器解析）：

- `docs/项目架构.md` §4.4（冻结区与基线四条）、§5.3（随机源与时间源）、§7.3.1（规则签名表）、§7.6（冻结与基线）、§8（静态检查与质量命令）
- `docs/契约v1清单.md`（16 项表格 + 状态栏）
- `docs/plan-M0.md` S6 段（任务、验证方案 6 条、落定的判断、常见坑）
- `docs/spec-M0.md` §6.5 / §7.5
- `PROGRESS.md`「当前在攻 / 待办 / 卡点」

### 2.3 审查方式

- diff 全部走 `git --no-pager show` 直读，**未导出临时 diff 文件**（8 个文件全是新增，直接读文件全文比读 diff 更完整）。
- 每条发现都用**实跑探针**确认，不采信提交说明自陈。
- 探针脚本一律 ASCII/UTF-8 输出，临时目录 `.tmp_ocr_s6/` 已 `rm --` 走回收站删除（`mavis-trash: moved to trash`）。

---

## 3. 中等问题

### M1 · 第三方 import 检查漏掉多名称 import 语句

| 字段 | 值 |
| --- | --- |
| path | `tools/check_kernel_purity.py` |
| 行号 | 119–125（`_module_of_import`）、128–145（`_check_imports`） |
| category | bug |
| severity | medium |

**现象**：`_module_of_import` 对 `ast.Import` 只取 `node.names[0]`：

```python
def _module_of_import(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.ImportFrom):
        ...
    return node.names[0].name.split(".")[0]  # ← 只看第一个名字
```

一条 `import os, numpy` 语句里，`numpy` 完全没被检查。`_check_imports` 同样只报 `names[0]`。

**实测**（临时往 `kernel/__init__.py` 追加一行，跑 `tools/check_kernel_purity.py`）：

| 注入代码 | 退出码 | 结果 |
| --- | --- | --- |
| `import numpy` | **1** | 红：`[第三方 import·红] 内核只能依赖标准库，发现 numpy` ✅ |
| `import numpy, os` | **1** | 红（报 `numpy`，`os` 不必报） ✅ |
| `import os, numpy` | **0** | **绿：0 条违规，输出「内核纯净度检查通过」** ❌ |

**影响**：这不是孤例——`import-linter` 的内核 forbidden 契约（`pyproject.toml:84-92`）是**固定黑名单**（`rich` / `tkinter` / `numpy` / `pydantic` / `pytest` / `hypothesis`），不在名单里的第三方（`requests` / `pandas` / `scipy` …）两个门都看不见。本脚本 docstring 声明的职责正是「禁第三方 import」，这个漏法让职责打折。

**建议**：遍历 `node.names` 全部名字逐个判；报错时把**所有**违规名字一次列全（当前 `import numpy, scipy` 只报 `numpy`，`scipy` 漏在后面——虽然整体是红的，但报告会误导）。

---

### M2 · 扫描目录不存在时退出码是 0，脚本自称「检查通过」

| 字段 | 值 |
| --- | --- |
| path | `tools/check_kernel_purity.py`、`tools/check_event_registry.py` |
| 行号 | `check_kernel_purity.py:261-271`（`check_paths`）、`check_event_registry.py:95-104`（`check_paths`） |
| category | bug |
| severity | medium |

**现象**：两个脚本的 `check_paths` 都是这个写法：

```python
for root in roots:
    if not root.is_dir():
        print(f"目录不存在：{relative_to_repo(root)}", file=sys.stderr)
        continue  # ← 目录不存在 → 当作"这里没有违规"
```

**实测**：

| 命令 | 退出码 | 输出 |
| --- | --- | --- |
| `check_kernel_purity.py --path src/typo_dir` | **0** | stderr：`目录不存在：src/typo_dir`；stdout：`内核纯净度检查通过。` |
| `check_event_registry.py --path src/typo_dir` | **0** | stderr：`目录不存在：src/typo_dir`；stdout：`事件类型登记检查通过。` |

**影响**：路径打错、目录被移动、CI 上工作目录不对 → **全绿**。脚本自己的 docstring 写明「退出码：0 = 干净；1 = 有违规；**2 = 用法或 IO 错误**」，目录不存在正是 IO 错误，应当是 2 而不是 0。守护脚本的默认取值是「绿」，把「我没扫到」和「我扫了没问题」混成同一个结果，是最坏的失败模式。

**建议**：`check_paths` 统计缺失目录数，`main` 在缺失时返回 2 并在 stdout 也打一行（stdout 常被 CI 日志吞掉，只写 stderr 容易看不见）。

---

### M3 · 「签名是冻结面」只落到参数级：新增一整条签名不报警

| 字段 | 值 |
| --- | --- |
| path | `tools/contract_baseline.py` |
| 行号 | 189–198（签条目收集）、264–279（`compare`） |
| category | bug |
| severity | medium |

**口径来源**：`plan-M0.md` S6「落定的判断」与 `PROGRESS.md` 卡点段都写着——

> **签名是冻结面**（参数增删 / 重排要红），**快照是"至少包含"**（顶层加字段必须绿）——两者的增删口径不同，是有意的。

而 `compare()` 对所有类别**一视同仁**：新键一律放行。

```python
added = [entry for key, entry in current.items() if key not in baseline]  # 一律放行
```

签名的键是 `rule/<签名名>/<参数名>`、`rule/<签名名>/returns`、`rule/<签名名>/paramOrder`——**签名名本身不在键里**，所以整条新签名产生的全是新键。

**实测**：

| 注入（源级，临时改 `contracts/rules.py`） | 退出码 | 结果 |
| --- | --- | --- |
| 给 `wrap_cell` 加一个参数 | 1 | 红：`rule/wrap_cell/paramOrder` 哈希变化 ✅（参数级口径生效） |
| 交换 `wrap_cell` 的参数顺序 | 1 | 红：`rule/wrap_cell/paramOrder` ✅ |
| **新增第 9 条签名 `probe_new_signature`** | **0** | **绿：新增 3 · 变化 0 · 消失 0，「基线校验通过」** ❌ |

**影响**：架构 §7.3.1 冻结的是**八条**签名（契约清单第 9 项同样写死「架构 §7.3.1 的 8 条」），ADR-061 要防的正是「签名晚于实现 / 实现期反向塑形签名」。整条追加一条签名是比改参数更大的接口变更，却比改参数更难被发现。同样的洞对**命名结构注册表**也存在（`NAMED_STRUCTS` 加第 9 个结构 → 全是 `struct/<名>/<成员>` 新键 → 绿）。

**建议**：在基线里补两条**结构性计数键**（例如 `meta/ruleSignatureCount`、`meta/namedStructCount`），或给签名/结构各加一条 `rule/<名>/exists` / `struct/<名>/exists` 形式的锚点键——键的增删就能表达「整条增删要红」。注意这属于基线**键口径**的变更，S17 生成基线前定下来正好。

---

## 4. 低优先级问题

### L1 · 平台名字面量表含 `"nt"` / `"posix"`，普通字符串会被误判

| 字段 | 值 |
| --- | --- |
| path | `tools/check_kernel_purity.py` |
| 行号 | 83–85（`PLATFORM_NAMES`）、213–223（`_check_platform_literals`） |
| category | bug |
| severity | low |

**实测**：往 `kernel/__init__.py` 加 `PROBE_MODE = 'nt'` → 退出码 **1**，报「平台名字面量 'nt'」。

`"nt"` / `"posix"` / `"aix"` 是极短的常见词，内核里出现一个值为 `"nt"` 的普通字符串（模式名、状态缩写）就会被**阻断**。当前仓内没有这样的字面量，所以今天不发作——但这是「误报会让整个机制失效」那一类。

**建议**：`PLATFORM_NAMES` 拆成两档——`sys.platform` / `os.name` 的**取值集合**（`win32` / `linux` / `darwin` / `cygwin` / `aix` / `freebsd`）保持阻断，`"nt"` / `"posix"` 这类歧义词降为警告，或要求字面量出现在比较表达式的右操作数时才判。

### L2 · 事件类型正则把任何 `词:词` 形状的字符串当成未登记事件类型

| 字段 | 值 |
| --- | --- |
| path | `tools/check_event_registry.py` |
| 行号 | 51（`EVENT_TYPE_PATTERN`）、88–91（判定处） |
| category | bug |
| severity | low |

**实测**：往 `contracts/events.py` 加 `PROBE_NOTE = "note:todo"` → 退出码 **1**，报「未登记的事件类型 'note:todo'」。

正则 `^[a-z][a-z0-9]*(?::[a-z0-9]+(?:-[a-z0-9]+)*)+$` 只看形状，不看是否像事件类型。契约层出现任何 `a:b` 形状的普通字符串（说明文案、映射键、诊断标签）都会被拦。

**正面结论**：注册表里 44 条事件类型**全部**能被这条正则匹配（实测 E1：无一遗漏），所以「已登记但扫不到」这个更危险的洞不存在。

**建议**：命中未登记字符串时，先看它是否落在「事件类型命名空间」附近（例如同文件里有 `EVENT_TYPES` / `emit(` / `publish(` 上下文），否则降为警告；或维护一张「已知非事件形状」的小豁免表。

### L3 · 文档里的 `--checklist` 用法跑不通

| 字段 | 值 |
| --- | --- |
| path | `tools/contract_baseline.py` |
| 行号 | 39（模块 docstring 用法块） |
| category | documentation |
| severity | low |

模块 docstring 写：

```
python tools/contract_baseline.py --checklist         # 只看清单状态
```

但 `--checklist` 在 argparse 里是 **路径参数**（`tools/contract_baseline.py:318-323`），真正的「只看清单状态」开关叫 `--checklist-only`（329–333）。**实测**照文档跑：

```
usage: contract_baseline.py [-h] [--baseline BASELINE] [--checklist CHECKLIST]
                            [--update-baseline] [--checklist-only] [--print-entries]
contract_baseline.py: error: argument --checklist: expected one argument
（退出码 2）
```

**建议**：把 docstring 第 39 行改成 `--checklist-only`。

### L4 · 基线键把「结构性键」与字段名放在同一命名空间，构造时不校验唯一性

| 字段 | 值 |
| --- | --- |
| path | `tools/contract_baseline.py` |
| 行号 | 159–203（`add` / `add_type` 与各结构性 `add`） |
| category | maintainability |
| severity | low |

结构性键 `event/<类型>/producer`、`event/<类型>/namespace`、`command/<名>/lifecycleGated`、`command/<名>/rejectReasons`、`rule/<名>/returns`、`rule/<名>/paramOrder` 与「字段名」共用一套命名空间，而 `entries` 是一个 **list**，`baseline_document` 才转成 dict——**撞键时后写的静默顶掉前写的**。

当前 353 条实测**无碰撞**（事件负载字段名 / 命令参数名 / 签名参数名 / 结构成员名全部核对过，没有 `producer` / `namespace` / `lifecycleGated` / `rejectReasons` / `returns` / `paramOrder` 这些名字），所以今天不是缺陷。

**建议**：`add()` 里断言键不重复（撞了就抛），或给结构性键换个不可能与字段名重合的前缀（如 `meta/`）。这类洞一旦发作，表现是「某条声明没进基线却看不出来」——正是 plan-M0 自己点名的失效模式。

### L5 · 架构 §8 仍写「事件类型登记：扫 `contracts/`，不是 `kernel/`」，与实现不符

| 字段 | 值 |
| --- | --- |
| path | `docs/项目架构.md` |
| 行号 | 448 |
| category | documentation |
| severity | low |

架构 §8 的表格行写着：

> | **事件类型登记** | `tools/` 自研脚本 | **扫 `contracts/`，不是 `kernel/`**；类型必须已登记 |

而实现（`tools/check_event_registry.py:47,120`）扫的是**整个包** `src/tetris_kernel/`。这不是实现跑偏——`plan-M0.md` S6 明确记了这是**有意收严**（「扫的是"哪个事件类型"，不是"哪个目录"」），但**架构文档没跟着改**，两处现在互相矛盾。下一个人照架构 §8 理解会以为 `kernel/` 里出现未登记事件类型是允许的。

**建议**：把架构 §8 那一行改成「扫整个包（`src/tetris_kernel/`），注册表是唯一权威」。

### L6 · `types.py` 说 S6 写的是「正则解析器」，实际是递归下降解析器

| 字段 | 值 |
| --- | --- |
| path | `src/tetris_kernel/contracts/types.py` |
| 行号 | 21–22 |
| category | documentation |
| severity | low |

原文：「S6 的基线脚本按这张表写**正则解析器**，表里没有的形态一律视为声明不合规」。

实际实现（`tools/contract_grammar.py:123-225`）是**递归下降解析器**，docstring 里也写明「这里的实现按那张表重写，不是抄测试里的回验解析器」。文法表本身没写错，但「正则解析器」这个措辞是 S2/S3 时期的旧设想，会误导后来人以为解析规则可以用正则近似。

**建议**：改成「解析器」。

### L7 · `tools/` 不在 pyright 覆盖范围内

| 字段 | 值 |
| --- | --- |
| path | `pyproject.toml`、`tools/measure_frame_cost.py` |
| 行号 | `pyproject.toml:53-60`、`tools/measure_frame_cost.py:164` |
| category | maintainability |
| severity | low |

`pyright` 的 `include = ["src"]`，质量门跑的是 `pyright src/tetris_kernel`——**S6 新增的 1646 行没有任何类型检查**。`measure_frame_cost.py:164` 的 `runner(seed=seed, frames=frames)` 只能靠 `# type: ignore[operator]` 撑着，而 `_load_runner()` 的返回类型写的是 `object`（`tools/measure_frame_cost.py:130`）。

不是缺陷，但 S17 之后 `tools/` 会成为常驻质量门的一部分，建议届时把 `tools/` 纳入 pyright 覆盖（或至少把 `_load_runner` 的返回类型改成带 `Callable` 的协议）。

---

## 5. 质量门

在 HEAD（`32eb23b`）实跑，全部通过：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **156 passed**（0.53s） |
| `python -m ruff check .` | **All checks passed!** |
| `python -m ruff format --check .` | **44 files already formatted** |
| `python -m pyright src/tetris_kernel` | **0 errors, 0 warnings, 0 informations** |
| `lint-imports` | **Contracts: 4 kept, 0 broken**（exit 0） |

五个脚本的默认行为（全部符合各自 docstring）：

| 脚本 | 退出码 | 输出 |
| --- | --- | --- |
| `check_kernel_purity.py` | 0 | 扫了 2 个目录：违规 0 条 · 警告 0 条 |
| `check_event_registry.py` | 0 | 注册表里有 44 条事件类型 · 检查通过 |
| `check_frozen_baseline.py` | 0 | 冻结区基线尚未生成（按计划在 M0 收尾 / S17 生成） |
| `contract_baseline.py` | 0 | 契约基线尚未生成（按计划在 S17 冻结契约 v1 时生成） |
| `measure_frame_cost.py` | **2** | 内核闭环尚未就绪（`tetris_kernel.host.headless` 不存在） |

> 注：`ruff check .` / `ruff format --check .` 覆盖 `tools/`（`pyproject.toml:45` 的 `src = ["src", "tests", "tools"]`），但**探针期间临时目录在仓内会污染 ruff 结果**（一度报 28 errors）——删掉临时目录后复跑即全绿。临时产物已删除，当前工作区只有本报告一个未跟踪文件。

---

## 6. 反向验证（自跑 30 组）

**全部自己跑，不采信提交说明自陈。** 为排除 Python 字节码缓存的干扰（见 §6.3），每组运行前清空 `__pycache__` 并设 `PYTHONDONTWRITEBYTECODE=1`。

### 6.1 契约基线（`tools/contract_baseline.py`）

| 编号 | 破坏 | 期望 | 实测 | 结论 |
| --- | --- | --- | --- | --- |
| C0 | 用「全勾」清单生成基线 | 生成 353 条 | 353 条，exit 0 | ✅ |
| C1 | 原样校验 | 绿 | 新增 0 · 变化 0 · 消失 0，exit 0 | ✅ |
| C2 | `ghostDelta` 类型 `VECTOR2` → `tuple[int,int,int]` | 红 | 红：`snapshot/ghostDelta` 哈希变化 | ✅ |
| C3 | `BOARD_FIELDS.width` 类型 `int` → `str` | 红（**且引用它的快照字段也要红**） | 红 2 条：`snapshot/board` + `struct/Board/width` | ✅ **形态 9 展开生效** |
| C4 | `wrap_cell` 参数顺序交换 | 红 | 红：`rule/wrap_cell/paramOrder` | ✅ |
| C5 | 命令 `setLevel.level` 类型 `int` → `str` | 红 | 红：`command/setLevel/level` | ✅ |
| C6 | 信封字段 `seq` 类型 `int` → `str` | 红 | 红：`envelope/seq` | ✅ |
| D1 | 事件负载 `seed` 类型 `int` → `str` | 红 | 红：`event/kernel:ready/seed` | ✅ |
| C7 | `ghostDelta` 改名 `ghostOffset` | 红（**「已有键消失」而不是当成纯新增**） | 红：`已有键消失（红）：snapshot/ghostDelta` + 新增 `ghostOffset` | ✅ |
| C8 | 新增一个快照字段 | **绿**（快照是「至少包含」） | 新增 1 · exit 0 | ✅ |
| C9 | `VECTOR2` → 同值字面量 `"tuple[int, int]"` | **绿**（声明未变，常量是定义） | exit 0 | ✅ |
| C10 | 类型串改成非法 `tuple[int]` | **拒绝**（exit 2，基线不生成） | `契约声明不符合类型文法，拒绝进基线` | ✅ |
| E3 | 给 `wrap_cell` 加一个参数 | 红 | 红：`rule/wrap_cell/paramOrder` | ✅ |
| **E2** | **新增第 9 条签名** | **红（签名是冻结面）** | **绿：新增 3 · 变化 0，「基线校验通过」** | ❌ **→ M3** |
| — | 真实清单（缺第 14 / 16 项）跑 `--update-baseline` | 拒绝生成 + 打印缺项 + 文件不写出 | 缺 2 项、exit 1、`never.json exists: False` | ✅ |
| — | 基线文件里篡改一个哈希 | 红 | 红：`已有键哈希变化` | ✅ |

### 6.2 其余三个脚本

| 编号 | 破坏 | 期望 | 实测 | 结论 |
| --- | --- | --- | --- | --- |
| — | 改 `kernel/__init__.py` 的一句注释 | 红（归一化只处理换行符，注释改动要被抓到） | 红：`冻结区内容变化（红）：kernel/__init__.py` | ✅ |
| D2 | 在 `contracts/events.py` 加一个未登记事件类型 | 红 + 点名位置 | 红：`events.py:674: 未登记的事件类型 'kernel:probe-unregistered'` | ✅ |
| D4 | `kernel/` 里 `import numpy` | 红 | 红：`[第三方 import·红] … 发现 numpy` | ✅ |
| **D3** | **`kernel/` 里 `import os, numpy`** | **红** | **绿：0 条违规** | ❌ **→ M1** |
| **D7** | **`from time import time` + `time()`** | **红（墙钟读）** | **绿：0 条违规** | ❌ 见下 |
| **D5 / D6** | **`--path` 指向不存在的目录** | **红（IO 错误 → exit 2）** | **绿：exit 0 + 「检查通过」** | ❌ **→ M2** |
| **D8** | **`kernel/` 里写 `'nt'`** | 绿（不是平台名） | **红：平台名字面量 'nt'** | ❌ **→ L1** |
| **D9** | **`contracts/events.py` 里写 `"note:todo"`** | 绿（不是事件类型） | **红：未登记的事件类型 'note:todo'** | ❌ **→ L2** |
| — | `kernel/` 里 `id(...)` | 只警告不阻断 | `blocking=False`，不进阻断列表 | ✅ |
| E1 | 44 条已登记事件类型 vs 扫描正则 | 全部可被扫描 | 44/44 匹配，无遗漏 | ✅ |
| — | `measure_frame_cost.py --self-test --frames 1000` | 可跑 | exit 0，P50 213µs / P99 398µs（合成样本） | ✅ |
| — | `percentile` 口径抽查 | 最近秩 | `[10..100]`：p50=50 · p99=100 · p100=100 | ✅ |

**D7 说明**：`from time import time` 之后的 `time()` 在 AST 里是 `Call(func=Name('time'))`，不是 `Call(func=Attribute)`，`_check_calls` 只认属性调用形态 → 漏。同理 `import time as t` 之后的 `t.time()` 也漏（`module == "t"` 不在 `WALL_CLOCK_FUNCTIONS` 里）。这是**AST 静态检查的固有边界**（别名不可静态判定），plan-M0 自己记了「守卫要能触发，漏网的守卫等于没守」这条纪律，故在此列出；影响面小于 M1（需要刻意改写法），定级为提示而非独立条目。

### 6.3 一处方法论坑（已排除，非项目缺陷）

中途发现：同秒内把文件**改回**且**长度不变**时，Python 的时间戳 `.pyc` 缓存会命中陈旧字节码，导致「明明已还原却仍报红」。第一轮探针因此出现了自相矛盾的结果（工作区 `git status` 干净、脚本却报 3 条变化）。**所有最终结论均已在「清空 `__pycache__` + `PYTHONDONTWRITEBYTECODE=1`」条件下重跑**，上表是干净条件下的结果。

顺带说明：这不是项目代码的缺陷，是 Python 字节码缓存的固有行为；但它对**S17 生成基线**有实际影响——生成基线与校验基线若跨秒、跨机器，务必在干净工作区跑（本项目已用「基线文件不写时间戳、逐字节可复现」规避了另一半风险）。

---

## 7. 与规格逐条核对

### 7.1 plan-M0 S6 任务项 → 实现

| plan-M0 S6 任务 | 实现 | 核对 |
| --- | --- | --- |
| `check_kernel_purity.py`：AST + 文本扫描，禁第三方 import / 平台名 / 墙钟读 | `tools/check_kernel_purity.py` | ✅ 主体在，**多名称 import 漏（M1）**、墙钟别名漏（D7） |
| `check_event_registry.py`：事件类型必须已登记 | `tools/check_event_registry.py` | ✅ 实测红绿有效；**扫描范围比架构 §8 宽（L5）** |
| `check_frozen_baseline.py`：`kernel/` + `rules/` 内容哈希（归一化换行符） | `tools/check_frozen_baseline.py` | ✅ 实测注释改动 → 红 |
| `contract_baseline.py`：先跑 16 项清单，未勾项拒绝生成；字段级哈希 | `tools/contract_baseline.py` | ✅ 清单门禁与字段级口径全部实测有效；**整条签名新增不红（M3）** |
| `measure_frame_cost.py`：P50 / P99 + 每帧事件数 | `tools/measure_frame_cost.py` | ✅ 自检跑通，百分位口径可复现 |
| 基线文件落点 `tests/baseline/contracts_baseline.json` · `tests/baseline/kernel_hashes.json`，**本次只落目录与 `.gitkeep`** | `tests/baseline/.gitkeep` + `_toolkit.py:31-37` | ✅ 目录已建，两个基线文件均未生成 |
| 类型文法解析器按 `contracts/types.py` 的文法表**重新实现**（形态 1–10），命名结构引用按成员表展开 | `tools/contract_grammar.py` | ✅ 全库 353 条声明逐条解析通过；展开生效（C3） |

### 7.2 plan-M0 S6「落定的判断」→ 实现

| 判断 | 核对 |
| --- | --- |
| 键 = 字段名 / 事件类型名 / 命令类型名 / 签名参数名；值 = 规范化后的哈希 | ✅ 实测 353 条键结构一致 |
| 允许新增键、禁止已有键哈希变化、已有键消失同样报红 | ✅ C7 / C2 实测 |
| 形态 9 按成员表展开后再哈希 | ✅ C3：改 `Board.width` → `snapshot/board` 与 `struct/Board/width` 同时红 |
| 规范化只做两件事（展开 + 归一空白）；联合成员顺序照原样保留 | ✅ C9（常量 → 同值字面量仍绿） |
| 结构性声明单独钉住（参数顺序 / 门禁归属 / 拒绝原因 / 生产者 / 命名空间） | ✅ 五类结构性键都在基线里（C4 / E3 / producer / namespace / lifecycleGated / rejectReasons） |
| 签名是冻结面（参数增删 / 重排要红），快照是「至少包含」（加字段必须绿） | ⚠️ **参数级 ✅（C4 / E3），整条签名增删 ❌（M3）** |
| 清单门禁的读法：解析不出 16 项就报错，不当作「全勾了」 | ✅ `parse_checklist` 强制 1–16 各一项，否则抛错 |
| 共用取值域常量不进基线、引用处进 | ✅ C9 实测 |
| 事件登记扫的是「哪个事件类型」，不是「哪个目录」 | ✅ 实现扫全包；**架构 §8 未同步（L5）** |

### 7.3 类型文法：解析器 vs 文法表

`tools/contract_grammar.py` 的实现与 `contracts/types.py` 的形态表逐条比对：

| 形态 | 文法表 | 解析器 | 核对 |
| --- | --- | --- | --- |
| 1 标量 | `int`/`str`/`bool`/`float` | `SCALAR_TYPES`（`contract_grammar.py:46`） | ✅ 一致 |
| 2 联合 | ≥ 2 成员，成员可为形态 1–9 | `_parse_union` | ✅ |
| 3 可空 | `T | None` | 与形态 2 合并处理（`None` 可作普通成员） | ✅ 符合附加规则 1 |
| 4 定长元组 | `tuple[T1, T2]` | 单元素元组报错（`:217`） | ✅ |
| 5 变长元组 | `tuple[T, ...]` | `:205-210` | ✅ |
| 6 嵌套元组 | 逐层展开 | 递归 | ✅ |
| 7 元素位含联合 | 任意深度 | `_parse_union` 嵌在 `_parse_tuple` 内 | ✅ 实测 `tuple[tuple[str, int | str | bool | None | tuple[int, ...]], ...]` 解析通过 |
| 8 递归元组 | `tuple[...]` | `:197-200` | ✅ |
| 9 命名结构 | `contracts/` 内登记过的结构名 | 命中 `NAMED_STRUCTS` 才认，否则 `GrammarError` | ✅ 未登记名一律拒 |
| 10 只读序列 | `Sequence[T]`（T 为形态 1–10，可嵌套） | `_parse_sequence` | ✅ `Sequence[Sequence[int]]` 解析通过 |

**负例抽查**（全部按预期被拒）：`tuple[int]`（单元素元组）· `list[int]` · `Unknown` · `int |` · `int int` · `tuple[int, int`（缺右括号）· 空串 · `Sequence[]` · `int, str`。

**形态 9 自引用防护**：`MAX_EXPANSION_DEPTH = 32`（`contract_grammar.py:53`），成员表自引用会抛 `GrammarError` 而不是挂死 ✅。

---

## 8. 覆盖率

| 项 | 数 |
| --- | --- |
| OCR 预览文件总数 | 8 |
| 已审查（reviewed） | **8** |
| 跳过（skipped） | **0** |
| **覆盖率** | **100%（8/8）** |

**排除文件**：无（`excluded_count = 0`）。本轮 S6 未新增或修改测试文件，`tests/test_contracts_declarations.py` 不在本次提交范围内。

**代码行覆盖**：8 个文件 1646 行全部逐行读过（7 个 `.py` 全文 + 1 个空 `.gitkeep`）。

---

## 9. S5 报告 6 项落实复核

S5 审查（`tests/ocr/S5-review.md`）提出的 6 项，已由提交 `8f98ed3` 修订。逐条核对现状：

| S5 发现 | 现状 | 核对 |
| --- | --- | --- |
| M1 · 文法表形态 10 规则与自家实例矛盾（两处） | `types.py:67-73` 形态 10 规则已改为「只用于规则接口的棋盘参数与消行组序列」；`BOARD_VIEW` 与 `Sequence[ClearGroup]` 两处实例与规则一致 | ✅ 已落实 |
| M2 · `AXIS_VALUES` / `CLEAR_AXIS_VALUES` 与字段无机器绑定 | 契约层钉死的取值域已改为**构造即校验**，规则集定义的取值登记在 `RULE_SET_DEFINED_FIELDS` 点名豁免 | ✅ 已落实 |
| L1 · `_member_types` 依赖未强制的 future-import 假设 | `structures.py:55-60` 已加显式检查：注解不是字符串就抛 `TypeError` 并说明原因 | ✅ 已落实 |
| L2 · `NAMED_STRUCTS` 寄居 `contracts.snapshot` | 已迁到 `contracts/structures.py`，`snapshot.py` 只留指针注释 | ✅ 已落实 |
| L3 · 快照叶子展开按「注册表命中」判定结构字段，耦合变宽 | `tests/test_contracts_declarations.py:956-967` 已改为**用快照模块自己的 `BOARD_FIELDS` / `ACTIVE_FIELDS`** 展开，docstring 写明理由 | ✅ 已落实 |
| L4 · 形态 9 规则标题仍写「只用于需求 §8.3 定名的子结构」 | `types.py:52-59` 已改为「只用于 `contracts/` 内登记过的命名结构」并列出规则侧五个结构 | ✅ 已落实 |

**6/6 已落实**，本轮未发现回归。

---

## 10. 对下一阶段的提醒与未验证说明

### 10.1 给 S7（随机源与时间源）的提醒

- S7 要建的 `kernel/rng.py` **必须是 `kernel/` 下唯一允许出现 `random.Random(...)` 的文件**（`RANDOM_SOURCE_MODULE = "kernel/rng.py"`，`check_kernel_purity.py:58`）。豁免是靠**路径后缀匹配**实现的（`:269`）——文件还没建之前豁免是空转的，建完之后它才生效；改随机源模块名要同步改这个常量。
- S7 往 `kernel/` 里写 `getstate()` / `setstate()` 时注意：这两个不是墙钟函数，不在 `WALL_CLOCK_FUNCTIONS` 里，不受拦截 ✅。
- **M1 的实际影响在 S7 就可能发作**：S7 要往 `kernel/` 里加 `import random`——单名称 import 会被正确判定为标准库放行 ✅，但若顺手写成 `import random, os` 之类，同样漏（当前无影响，因为 stdlib 放行）；真正的风险是 S7 之后往内核里引入**任何**第三方库时用了多名称 import。

### 10.2 给 S17（冻结基线）的提醒

- **M3 建议在生成基线之前解决**：现在往基线里加两条结构性计数键（签名条数 / 命名结构条数）成本很低；基线一旦冻结，「整条签名增删不报警」就固化成机制的一部分。
- **L4 建议在生成基线之前解决**：撞键是**静默丢声明**，基线一旦生成，被丢掉的声明就永远补不回来了（除非再改基线）。构造时加一条唯一性断言即可。
- 生成基线的提交 message 记得写明「契约 v1 冻结」（`contract_baseline.py:367` 已打印提醒，清单第 16 项在那一刻勾）。

### 10.3 未验证 / 不作评价的部分

- **基线文件本身尚未生成**（按计划 S17 才生成）→ 本报告所有关于「进基线 / 不进基线」的判断都是**口径推导 + 临时基线实测**，没有真实基线文件的哈希可比。
- **需求 / 架构 / spec 的逐条对齐是人工核对**，不是机器解析文档——S4 轮曾抓到过一处转录已非字面的情况，这里同样可能漏。
- **未复跑 S6 提交说明自陈的反向验证原文**（plan-M0 S6 段列的 7 组）：我的第 6 节是**独立重跑**，其中「清单门禁缺 11/14/16 三项」与现在的**缺 14/16 两项**不同——因为 `32eb23b` 已把清单第 11 / 12 项勾上，属文档变更，非脚本问题。
- **未审查 `tests/test_contracts_declarations.py`**（不在 S6 提交范围内，S5 轮已审）。
- **`tools/` 的类型检查缺口**（L7）未展开验证，只指出了覆盖范围事实。
- **`contract_grammar.py` 的规范化在极端形态下的表现**（如联合里嵌套形态 9 的成员、形态 9 的成员又是联合）只做了抽样，未做穷举。

---

## 附：本次审查的自我约束

- 只报**有把握的真缺陷**；风格类不单列（L5 / L6 / L7 是文档与覆盖范围的事实陈述，不是风格意见）。
- 每条发现都带 path、行号、category、severity，并附**可复现的实测**。
- 结论不夸大：三个中等问题都是**守卫漏报 / 口径未落地**，不是脚本崩溃或数据损坏；S6 的核心机制（字段级哈希、形态 9 展开、清单门禁、冻结区哈希）经实测**逐条有效**。
