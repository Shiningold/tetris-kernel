---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: d56fea5ff969d8adfce12b1d40e265c4_058cdfc9d20411f1992e525400638852
    ReservedCode1: ZnVVAYoB+iAqGEOxatTdLcAbp46z7achuNYcEAnQUDBWydOO6dDhzbnPeyhrUf993GO3iJbAygb+QPz7LTtL9bVgxPWI0CBSX5gGTDrCfr6s47spmbutyl+y/lifRNhi24xsxqi4LpOE2LPEovNnPfuphXtrdPYbOcQltbOIBE5OmyrXjrr7mrV3pqE=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: d56fea5ff969d8adfce12b1d40e265c4_058cdfc9d20411f1992e525400638852
    ReservedCode2: ZnVVAYoB+iAqGEOxatTdLcAbp46z7achuNYcEAnQUDBWydOO6dDhzbnPeyhrUf993GO3iJbAygb+QPz7LTtL9bVgxPWI0CBSX5gGTDrCfr6s47spmbutyl+y/lifRNhi24xsxqi4LpOE2LPEovNnPfuphXtrdPYbOcQltbOIBE5OmyrXjrr7mrV3pqE=
---

# M3 代码审查报告（可扩展：系统契约 · 注册表与热插拔 · 自动发现 · 示范系统 · 运行期换规则集）

> 审查方式：`open-code-review-delegate` 委托模式——OCR 只负责**文件选择与规则解析**，审查判断由宿主 Agent 完成。
> 基线：`7244fff`（M2 收官 + M3 开工入档）→ `67881a7`（`feature/m3-batch-d`，含批次 A/B/C/D）。
> 审查日期：2026-10-27。

---

## §1 概要

M3 兑现五件事：系统契约（`SystemManifest` / `InstallContext` / `CommandChannel` / `check_manifest_types` 跨文件自证）· 宿主层系统注册表（`registry.py`：装上/摘下/运行期热插拔/通道撤销先于退订/按声明订阅/落盘出口按名字问宿主）· 自动发现（`discovery.py`：真扫描 + `ImportPurityGuard` 导入无副作用守卫 + `DependencyGraph`）· 两个示范系统（成就 / 旋转统计）· 运行期换规则集（`switchRuleSet` + `RuleSetResolver` + `kernel:rule-set-changed`），另加 CLI `systems` 落盘接线与契约基线 `reject/*`、`system/*` 两类新键。

**结论先说**：M3 的**架构纪律保持得不错**——新能力全部长在外围（`contracts/` · `host/` · `systems/`），依赖方向恒定（systems 不 import host），契约基线 **369 → 383 条只增不改**，冻结区基线实测绿；`ImportPurityGuard`、`DependencyGraph`、注册表错误语义（不在册/已装/未装）都有独立测试兜底。

**问题出在"配额丢弃路径"这条老线上**，被 M3 的新命令（`switchRuleSet`）撞出真缺陷：

- **`_drop_commands` 忽略合并器冲刷出的旧组** → 被配额丢弃的一批命令里，**只有最后一种命令类型**会发出 `command:rejected`，其余**静默消失**（H1 · 高 · 已实测复现：3 条不同类型全部被丢，只出 1 条 rejected）。这直接违背 `run_rounds` 与契约文档「被丢弃的任何命令都以 `quota_exceeded` 被拒」的承诺。
- **`switchRuleSet` 契约声明缺 `quota_exceeded`** → 与文档「17 条命令都带这个原因」、与内核实际丢弃行为三方不一致（M1 · 中）。

外加 2 条低危（确定性序列未覆盖新命令 · 系统声明 `producer` 拼错无校验）与 1 条命名误导（「零内核改动」断言在 M3 破例语境下易误读）。

**发现计数：高 1 · 中 2 · 低 2，合计 5 项。**

---

## §2 审查范围与基线

### 2.1 OCR 委托输出

```
ocr delegate preview --format json --from 7244fff --to HEAD
```

| 项 | 值 |
| --- | --- |
| mode | `range` |
| merge_base | `7244fff526caf0567c567a6fc380ef2f53c17f16`（M2 收官） |
| total_files | 48 |
| reviewable_count | **30** |
| excluded_count | 18 |
| insertions / deletions | 4812 / 182 |

**可审文件（30）**：`pyproject.toml` · `src/tetris_kernel/` 下 26 个（`contracts/` 5 · `host/` 4 · `kernel/` 2 · `systems/` 12 · `systems/manifests.py`）+ `tests/baseline/` 3 个 json + `tools/contract_baseline.py`。

**排除文件（18）**：`docs/` 与 `ai_docs/` 6 个 + `PROGRESS.md`（`unsupported_ext`）+ `tests/test_*.py` 12 个（`default_path`：`tests/` 默认排除；`tests/baseline/*.json` 不排除，属于可审）。

**规则组**：`ocr delegate rule --format json` → 3 组。

- 组 1（`pyproject.toml`）：仅查 toml 语法类问题。
- 组 2（`**/*.{py,pyi,ipynb}`）：类型错配 / 死代码 / 可变默认值 / 边界与空输入 / 异常处理 / 资源管理 / 性能 / 并发 / 安全。
- 组 3（`tests/baseline/*.json`）：仅查 json 键拼写（与契约键对照）。

### 2.2 逐文件处置（30 / 30 全部读过）

| 分组 | 文件 | 处置 |
| --- | --- | --- |
| 契约面 | `contracts/__init__.py` · `contracts/commands.py`（513 行）· `contracts/events.py` · `contracts/systems.py`（219 行，新增）· `contracts/types.py` | 全文读（命令/事件注册表、`switchRuleSet` 声明、`SystemManifest` 与自证） |
| 宿主 | `host/assembly.py` · `host/cli.py` · `host/discovery.py`（225 行，新增）· `host/registry.py`（323 行，新增） | 全文读（注册表安装/摘除/热插拔/通道撤销顺序；`ImportPurityGuard`；`systems` CLI） |
| 内核 | `kernel/commands.py` · `kernel/core.py`（2132 行） | 读 diff + 关键段全文（`_cmd_switch_rule_set` · `RuleSetResolver` · `_drop_commands` · `_take_batch` · `_dispatch`） |
| 系统 | `systems/context.py`（新增）· `systems/manifests.py`（新增）· `achievements/`（3）· `log/`（2）· `metrics/`（2）· `replay/`（2）· `rotations/`（3，新增） | 读 diff + 逐系统全文核对声明与工厂 |
| 基线 | `tests/baseline/contracts_baseline.json` · `contracts_expected.json` · `kernel_hashes.json` | 读 diff + 结构化解析（383 条契约 · `reject/*` 逐值键 · `system/*` 键 · 冻结区哈希） |
| 工具 | `tools/contract_baseline.py` | 读 diff + `check_manifest_types` 调用点全文 |
| 配置 | `pyproject.toml` | 读 diff（1 行变更） |

**覆盖率：total_files 30 · reviewed_files 30 · skipped_files 0 · coverage_rate 100%。**

### 2.3 配套上下文（读，不在改动范围）

`docs/spec-M3.md`（系统契约 · M3-D1…D5 局部纪律）· `docs/plan-M3.md` · `tests/test_m3_*.py` 12 个（含 `test_system_registry.py` 519 行 / `test_system_discovery.py` 337 行 / `test_m3_rule_set.py` 398 行）· `tests/test_determinism.py` · `tests/test_contracts_declarations.py`（`EXPECTED_COMMAND_REJECT_REASONS` 806 行）· `tests/test_replay_playback.py` · `tests/test_kernel_lifecycle.py` · `tests/test_baselines.py`。

### 2.4 未覆盖的部分（明确声明）

- **OCR 排除的 18 个文件没有逐字审**（`docs/` 6 个 + `tests/` 12 个）。其中 `docs/spec-M3.md` 只按 grep 读了系统契约与局部纪律；**12 个测试文件只在追调用点/查断言时读过片段**，它们的断言强度未被独立审查——本报告对 H1 的判断依赖"丢弃路径代码 + 实跑复现"这一事实，不依赖测试对错。
- **未做变异测试**：本报告的每条结论都给了实跑或静态证据，但没有像批次 D 那样把"守卫被改坏"逐个反证。
- **M2 遗留的三条真偏离**（0 行 T-Spin 断 B2B 链 · `tspin-mini` 送不到计分 · 事件数基线够不着 `piece:lock-delay-reset`）**不在本次范围**，M3 未修，本报告不重复计。M2 报告的 H1（三系统共用覆盖语义 sink）在 M3 的 `system_sink` 出口上**已吸取**——`test_m3_system_sink` 断言每系统一文件、追加语义，本次未再复现该问题。

---

## §3 发现清单

| 编号 | 严重度 | 一句话结论 | 位置 |
| --- | --- | --- | --- |
| **H1** | **高** | `_drop_commands` **忽略 `RejectCoalescer.record` 冲刷出的旧组** → 配额丢弃一批命令时，**只有最后一种命令类型**发 `command:rejected`，其余静默消失；违背「被丢弃的任何命令都以 `quota_exceeded` 被拒」 | `kernel/core.py:742-756` · `kernel/quota.py:190-212` |
| **M1** | 中 | `switchRuleSet` 契约声明**缺 `quota_exceeded`**，与文档「17 条命令都带这个原因」及内核实际丢弃行为三方不一致 → 消费方按声明枚举拒绝面会漏 | `contracts/commands.py:445-453` · `contracts/commands.py:39` · `tests/test_contracts_declarations.py:806` |
| **M2** | 中 | 确定性命令序列**未覆盖 M3 新增命令** → 确定性指纹与回放不含运行期换规则集路径 | `tests/test_determinism.py:59-81` |
| **L1** | 低 | 系统声明自证 `check_manifest_types` **不校验 `producer`** → 拼错时基线照收，消费方按 producer 过滤静默漏事件 | `contracts/systems.py:179-207` |
| **L2** | 低 | 「零内核改动」断言在 M3 破例语境下**命名误导**：断的是「冻结区哈希与基线全等」，而 M3 本身改过内核并已同步刷新基线 | `tests/test_m3_rotations.py:245-250` · `tests/test_m3_achievements.py:13` |

---

## §4 逐条详情

### H1 · `_drop_commands` 忽略合并器冲刷出的旧组 → 配额丢弃路径的拒绝事件静默丢失 · high

**位置**

- `kernel/core.py:742-756`（`_drop_commands`：`for command in commands: self._coalescer.record(...)`——**循环里丢弃了 `record` 的返回值**）
- `kernel/quota.py:190-212`（`RejectCoalescer.record`：**键一变就冲刷旧组并返回它**）
- `kernel/core.py:805-810`（对照：`_reject` 路径**正确处理**了 `record` 返回值——只有 `_drop_commands` 漏）

**机制**

`RejectCoalescer` 的键是 `(phase, 命令类型, 拒绝原因)`，`record` 在键变化时会把上一组冲刷出来并返回。`_drop_commands` 对**同一批被丢弃的不同类型命令**逐条 `record`：

- 记 `switchRuleSet` 组 → 存入 `_pending`；
- 记 `moveLeft` 组 → 键变 → `flush()` 返回 `[switchRuleSet 组]`——**返回值被 `_drop_commands` 丢弃**；
- 记 `moveRight` 组 → 同理冲刷掉 `moveLeft` 组——**又被丢弃**；
- 最终 `run_rounds` 的 `flush_rejects()` 只冲刷出**最后一种类型**（`moveRight`）。

结果：批量丢弃的不同类型命令里，除末位类型外，**rejected 事件全部静默丢失**（`kernel:quota-exceeded` 照发，但逐命令的拒绝明细没了）。

**证据（实跑）**

探针：`QuotaLimits(max_commands=0)`，入队 `switchRuleSet` / `moveLeft` / `moveRight` 三条不同类型命令，`run_rounds` 全部丢弃：

```
kernel:quota-exceeded {'quota': 'commandCount', 'limit': 0, 'actual': 3}
command:received {'commandType': 'moveRight', ...}
command:rejected {'commandType': 'moveRight', 'reason': 'quota_exceeded', 'mergedCount': 1}
== rejected 条数: 1 | 丢弃命令数: 3 ==
```

三条命令全部被丢，只发出一条 rejected（末位的 `moveRight`）；`switchRuleSet` 与 `moveLeft` 的拒绝事件**彻底消失**。

**违背的承诺**

- `run_rounds` docstring（`kernel/core.py:690-692`）：「超限的剩余命令被丢弃并补发 `command:rejected`（`quota_exceeded`），**逐条计入合并计数**（不刷屏）」；
- `_drop_commands` docstring（`kernel/core.py:738-740`）：「丢弃一批命令：**逐条记入合并计数** + 发一条配额超限事件」；
- 契约文档（`contracts/commands.py:38-39`）：「被丢弃的**任何**命令都以 `quota_exceeded` 被拒」。

**影响**

- 事件轨迹 / 结构化日志 / 指标系统**漏报被配额丢弃的命令**——可观测性失真；
- 录像回放侧虽因回放走同路径而"一致"，但"被丢弃命令逐条可见"的契约语义被破坏；
- M3 之前该缺陷就存在（M2 已有 16 条命令），本次因 `switchRuleSet` 加入而被放大并暴露。

**修复建议**

`_drop_commands` 收集 `record` 的返回值（冲刷出的旧组），连同最终 `flush_rejects()` 一起冲刷；参照 `_reject` 路径（`kernel/core.py:908-918`）的既有正确写法。

---

### M1 · `switchRuleSet` 契约声明缺 `quota_exceeded`，与文档及内核行为不一致 · medium

**位置**

- `contracts/commands.py:445-453`（`switchRuleSet` 声明：`reject_reasons=("invalid_argument", "illegal_transition", "unknown_rule_set")`——**没有 `quota_exceeded`**）；
- `contracts/commands.py:38-39`（契约文档：「配额是跨切的……被丢弃的**任何**命令都以 `quota_exceeded` 被拒——**所以 17 条命令都带这个原因**」）；
- `tests/test_contracts_declarations.py:806`（测试钉住：`"switchRuleSet": frozenset({"invalid_argument", "illegal_transition", "unknown_rule_set"})`）。

**行为核对**

内核 `_drop_commands` 对**任何**命令（含 `switchRuleSet`）都以 `quota_exceeded` 记录拒绝（H1 的丢弃路径）；实测 `max_commands=0` 时 `switchRuleSet` 确实以 `quota_exceeded` 被丢弃。因此：**声明不能预见该拒绝**，文档「17 条都带」与声明自相矛盾。

**影响**

- 消费方按 `reject_reasons` 枚举拒绝面会漏掉 `quota_exceeded`；
- 若"配额跨切不算命令声明"是设计意图，则文档「17 条命令都带」是错的，且与其余 16 条**显式声明** `quota_exceeded` 的方式不一致（`moveLeft` / `hold` / `tick` / `setLevel` 等都显式列了）。

**修复建议**

二选一：① 给 `switchRuleSet` 声明补上 `quota_exceeded`（与其余 16 条一致，测试 `806` 行同步更新）；② 若确系设计排除，在契约文档与命令注释中显式说明「配额跨切为全局拒绝，不进入单命令 `reject_reasons`」，并把「17 条都带」改写成「16 条显式声明 + 配额跨切除外」。

---

### M2 · 确定性命令序列未覆盖 M3 新增命令 · medium

**位置**

- `tests/test_determinism.py:59-81`（`COMMAND_SEQUENCE`：12 种命令——`start` / `moveLeft` / `rotateCW` / `softDrop` / `hardDrop` / `tick`×8 / `moveRight` / `rotateCCW` / `hold` / `setLevel` / `injectGarbage` / `writeCells`）。

**缺口**

17 条命令中 5 种未进确定性序列：`pause` / `resume` / `reset` / `rotate180` / `switchRuleSet`。其中 `switchRuleSet` 是 M3 新增：确定性指纹、回放一致性与生命周期组合计数**完全没有覆盖运行期换规则集路径**。

**说明**

- `rotate180` / `pause` / `resume` / `reset` 是 M2 既有缺口，本次不重复计，只计 M3 新增的 `switchRuleSet`；
- `switchRuleSet` 局中必拒（`illegal_transition`），序列里可放在尾部（局末 / 已结束态）覆盖「非 running 态接受切换」分支，同时把 `kernel:rule-set-changed` + `game:reset` 纳入指纹。

**影响**

确定性测试是 G-门禁类测试，新增命令无指纹覆盖意味着「换规则集路径的确定性退化」不会被 CI 抓住。

---

### L1 · 系统声明自证不校验 `producer` · low

**位置**

- `contracts/systems.py:179-207`（`check_manifest_types`：只校验 `subscribes` 事件名与 `commands` 命令名已登记，**不校验 `producer`**）。

**影响**

系统声明 `producer` 拼错（如 `"achivements"`）时基线照收、安装照过；事件负载里的 producer 与声明不一致，消费方按 producer 过滤会**静默漏事件**。`PRODUCER_LABELS` 已含系统代号（`achievements` / `rotations`），把 producer 纳入自证即可闭环。

**修复建议**

`check_manifest_types` 增加 producer 校验：合法值 = `PRODUCER_LABELS` ∪ 本系统名。

---

### L2 · 「零内核改动」断言在 M3 破例语境下命名误导 · low

**位置**

- `tests/test_m3_rotations.py:245-250`（注释与测试名「**零内核改动**（`G3-1`）：冻结区逐文件哈希全等」）；
- `tests/test_m3_achievements.py:13`（同样表述）。

**说明**

该断言测的是「当前工作区内核文件与 `kernel_hashes.json` 基线**哈希全等**」——而 M3 批次 D 本身**改过** `kernel/core.py`（+80/-1：`RuleSetResolver` / `_cmd_switch_rule_set` / `_rule_set_resolver`）与 `kernel/commands.py`（+19/-2），并**同步刷新了 `kernel_hashes.json`**。测试绿 ≠「M3 没改内核」，只等于「改完基线同步了」。M3 的冻结区破例是必要的（`switchRuleSet` 必须进内核，内核是唯一状态所有者），但测试注释/命名对读者有误导。

**修复建议**

注释补充「M3 唯一破例（运行期换规则集）+ 基线已同步刷新」，或改名「冻结区基线同步（G3-1）」。
*（内容由AI生成，仅供参考）*
