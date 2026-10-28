---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: d56fea5ff969d8adfce12b1d40e265c4_07445d37d2b411f1b84e52540064ee0f
    ReservedCode1: ULZRVPo8j0OMTm16oGmLP2WqlsFyH2VhmTnYREUp9qiwZUQQcavFfh1Nao9OrkL9u9A3zzQbbh2gkvS2LTtUXy2KTE9jrcfejkEzYpoYzNEmsj8LGmI2nx40dxBoBcQ5STL+jS5g9xkoG9Me6V+JURRaBBBVt4PE8qhAIEZo2PArjb/0yAwaBxD+wmo=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: d56fea5ff969d8adfce12b1d40e265c4_07445d37d2b411f1b84e52540064ee0f
    ReservedCode2: ULZRVPo8j0OMTm16oGmLP2WqlsFyH2VhmTnYREUp9qiwZUQQcavFfh1Nao9OrkL9u9A3zzQbbh2gkvS2LTtUXy2KTE9jrcfejkEzYpoYzNEmsj8LGmI2nx40dxBoBcQ5STL+jS5g9xkoG9Me6V+JURRaBBBVt4PE8qhAIEZo2PArjb/0yAwaBxD+wmo=
---

# M4 代码审查报告（渲染与输入适配器 · 变体层与扩展槽 · AI 系统 · 录像 v2 与回放 · 宿主接线）

> 审查方式：`open-code-review-delegate` 委托模式——OCR 只负责**文件选择与规则解析**，审查判断由宿主 Agent 完成。
> 基线：`67881a7`（M3 收官）→ `cfec469`（M4 收官，含批次 A-E）。
> 审查日期：2026-10-28。

---

## §1 概要

M4 兑现五件事：渲染与输入适配器（`adapters/render/terminal.py` 终端渲染器 · `adapters/input/keyboard.py` 键盘输入 · `reader.py` 按键读取）· 变体层（`variants/gravity` 重力变体 A · `variants/growth` 生长变体 B · 变体注册表与内置变体装配 · 扩展槽帧末合并）· AI 系统（`systems/ai/`：search 落点搜索 / system 决策订阅 / manifest 声明）· 录像升版 v2 与回放五项比对（`systems/replay/` + `host/replay.py`）· 宿主接线（`host/play.py` 实时宿主 · `host/variants.py` · `host/performance.py` 单帧成本记账 · `host/extensions.py`）。

**结论先说**：M4 的**架构纪律保持得不错**——新增能力全部长在外围（`adapters/` · `systems/` · `variants/` · `host/`），依赖方向恒定（变体层禁 import `kernel`/`bus`/`adapters`/`host`，靠结构化类型 `Protocol` 声明事件与命令通道的最小形状）；内核仅动 `kernel/core.py` 且冻结区哈希基线同步刷新；契约基线 383 条只增不改。AI 与 growth 变体**端到端实跑均通**（AI 200 帧 piecesPlaced=6 / score=100 / lines=1；growth softDrop 局晶体 22 格、扩展槽 `growth.crystalCount` 受控写入路径可用）；录像 v1 五项兼容（无第五项按 `EMPTY_EXTENSION_FINGERPRINT` 处理）实测正确。

**问题出在两处低危**，均非功能性缺陷：

- **AI 落点搜索的「固定预算」声明是摆设**：`MAX_CANDIDATES=40` 被文档称为候选数上限，但 `plan_placement` 从不引用它——实际枚举 4 朝向 × 16 列 = **64 个候选**（10 宽棋盘），docstring 与实现不一致（L1 · 低）。
- **growth 顶部锁定场景晶体生长被系统性抑制且无测试覆盖**：hardDrop 快速堆叠把锁定格顶到 spawn 区附近时，晶体目标格恒被新出生方块占据，`MAX_ATTEMPTS_PER_CELL=3` 重试全失败后静默放弃，变体 B 功能零可见（实测 300 帧 9 块 crystal=0）。行为本身被设计文档「宁可少长，不写错」覆盖，但边界未被显式声明、测试未覆盖（L2 · 低）。

**发现计数：低 2，合计 2 项。** 未发现中/高危问题。

---

## §2 审查范围与基线

### 2.1 OCR 委托输出

```
ocr delegate preview --format json --from 67881a7 --to HEAD
```

| 项 | 值 |
| --- | --- |
| mode | `range` |
| merge_base | `67881a70806b9fb0d41e93a52ac456b111a51af5`（M3 收官） |
| total_files | 84 |
| reviewable_count | **47** |
| excluded_count | 37 |
| insertions / deletions | 13824 / 263 |

**可审文件（47）**：`docs/html源文件/路线图_源文件.html` · `pyproject.toml` · `src/tetris_kernel/` 下 43 个（`adapters/` 6 · `contracts/` 4 · `host/` 11 · `kernel/` 1 · `systems/` 9 · `variants/` 12）+ `tools/measure_frame_cost.py` · `tools/measure_m4_cost.py`。

**排除文件（37）**：`docs/` 与 `ai_docs/` 及 `PROGRESS.md`（`unsupported_ext`/默认排除）+ `tests/test_*.py` 26 个（`default_path`：`tests/` 默认排除；`tests/baseline/*.json` 不排除，属于可审）+ `tests/ocr/m3-review.md`。

**规则组**：`ocr delegate rule --format json` → 3 组。

- 组 1（`pyproject.toml`）：仅查 toml 语法类问题。
- 组 2（`**/*.{py,pyi,ipynb}`）：类型错配 / 死代码 / 可变默认值 / 边界与空输入 / 异常处理 / 资源管理 / 性能 / 并发 / 安全。
- 组 3（`tests/baseline/*.json`）：仅查 json 键拼写（与契约键对照）。

### 2.2 逐文件处置（47 / 47 全部读过）

| 分组 | 文件 | 处置 |
| --- | --- | --- |
| 适配器 | `adapters/__init__.py` · `adapters/input/__init__.py` · `input/keyboard.py`（新增）· `input/reader.py`（新增）· `adapters/render/__init__.py` · `render/terminal.py`（新增） | 读 diff + 全文（键码映射、终端渲染输出、适配器出口） |
| 契约面 | `contracts/__init__.py` · `contracts/commands.py` · `contracts/events.py` · `contracts/systems.py` | 读 diff（`piece:locked` 等事件负载形状、扩展槽键、录像 v2 契约） |
| 宿主 | `host/__init__.py` · `assembly.py` · `builtin_variants.py` · `cli.py` · `discovery.py` · `extensions.py`（新增）· `loop.py` · `performance.py`（新增）· `play.py`（新增）· `replay.py` · `variants.py`（新增） | 读 diff + 关键段全文（`_merge_extensions` 帧末合并、`_extension_providers` 每帧现查、`apply_extensions`、`play` 实时宿主、`replay` 五项比对） |
| 内核 | `kernel/core.py`（2147 行） | 读 diff + 关键段全文（`_cmd_write_cells` 边界校验 · `_write_condition_failed` 禁止改活动格 · 锁定事件负载 · 配额语义） |
| AI 系统 | `systems/ai/__init__.py` · `manifest.py` · `search.py`（363 行，新增）· `system.py`（新增）· `systems/context.py` | 读 diff + 全文（`plan_placement` 枚举 · `MAX_CANDIDATES` 引用 · 固定预算声明 · `_view_of` 容错） |
| 回放 | `systems/replay/__init__.py` · `format.py` · `playback.py` · `recorder.py` | 读 diff + 全文（v2 格式 · 第五项 extension_fingerprint 比对 · v1 兼容） |
| 变体 | `variants/extensions.py`（新增）· `gravity/`（4，新增）· `growth/`（4，新增）· `variants/manifests.py` | 读 diff + 逐文件全文（gravity 规则集 · growth `_on_piece_locked` / `_flush` 重试 / `_as_cells` 解析 · 变体注册） |
| 工具 | `tools/measure_frame_cost.py` · `tools/measure_m4_cost.py` | 读 diff + 全文（单帧成本分档记账） |
| 配置 | `pyproject.toml` | 读 diff（6 行变更） |
| 文档 | `docs/html源文件/路线图_源文件.html` | 读 diff（1 行） |

**覆盖率：total_files 47 · reviewed_files 47 · skipped_files 0 · coverage_rate 100%。**

### 2.3 配套上下文（读，不在改动范围）

`docs/spec-M4.md`（渲染/输入适配器 · 变体层 · AI · 录像 v2 · M4-S1…S5 纪律）· `docs/plan-M4.md` · `tests/test_m4_*.py` 13 个（含 `test_m4_growth_variant.py` / `test_m4_ai.py` / `test_m4_terminal_renderer.py` / `test_m4_recording_v2.py` / `test_m4_extension_merge.py`）· `tests/test_determinism.py` · `tests/test_contracts_declarations.py` · `tests/test_baselines.py` · `tests/ocr/m3-review.md`（格式对齐参照）。

### 2.4 未覆盖的部分（明确声明）

- **OCR 排除的 37 个文件没有逐字审**（`docs/` 与 `ai_docs/` + `PROGRESS.md` + `tests/` 26 个）。`docs/spec-M4.md` / `plan-M4.md` 只按需读了相关段落；**测试文件只在追调用点/查断言时读过片段**，断言强度未被独立审查——本报告 L1/L2 的判断依赖「代码 + 实跑探针」这一事实，不依赖测试对错。
- **未做变异测试**：每条结论给了静态或实跑证据，但没有把守卫类逻辑逐个反证。
- **M3 遗留未修项不在本次范围**：M3 报告的 H1（`_drop_commands` 丢弃合并器冲刷旧组 → 拒绝事件静默丢失）在 M4 未修、未复现（M4 未新增命令类型，配额丢弃路径不变）；M2 遗留三条真偏离（0 行 T-Spin 断 B2B · `tspin-mini` 送不到计分 · 事件数基线）亦不在本次范围。

---

## §3 发现清单

| 编号 | 严重度 | 一句话结论 | 位置 |
| --- | --- | --- | --- |
| **L1** | 低 | AI 落点搜索的「固定预算」`MAX_CANDIDATES=40` **从未参与剪枝**——`plan_placement` 实际枚举 4 朝向 × `range(-3, width+3)` = 64 候选（10 宽棋盘），docstring 与实现不一致 | `systems/ai/search.py:17` · `systems/ai/search.py:53` · `systems/ai/search.py:165-172` |
| **L2** | 低 | growth 顶部锁定场景（锁定格贴近 spawn 区）晶体生长被**系统性抑制**：目标格恒被新出生方块占据 → `MAX_ATTEMPTS_PER_CELL=3` 重试全失败后静默放弃，变体 B 零可见；行为被「宁可少长」覆盖但**边界未显式声明且无测试覆盖** | `variants/growth/system.py:31-34` · `variants/growth/system.py:296-342` · `tests/test_m4_growth_variant.py` |

---

## §4 逐条详情

### L1 · AI 落点搜索的「固定预算」声明是摆设 · low

**位置**

- `systems/ai/search.py:17`（模块 docstring：「候选数 | `MAX_CANDIDATES`（4 朝向 × 棋盘宽度） | 按耗时自适应 → 同一录像在不同机器上重放不同」——**把它当作候选数上限/固定预算**）；
- `systems/ai/search.py:53`（`MAX_CANDIDATES = 40`）；
- `systems/ai/search.py:165-172`（`_candidate_columns`：`return range(-3, width + 3)` → 10 宽棋盘共 **16 列**）；
- `systems/ai/search.py:117-140`（`plan_placement`：`for target_rotation in (0, 1, 2, 3): for pre_x in _candidate_columns(width):`——**从不引用 `MAX_CANDIDATES`**）。

**机制**

`plan_placement` 的枚举规模 = 4 朝向 × `(width + 6)` 列。10 宽棋盘：`range(-3, 13)` = 16 列 → **64 个候选**。而 `MAX_CANDIDATES=40` 被文档表述为候选预算（并配了「按耗时自适应 → 不同机器重放不同」的理由），但**代码里没有一处剪枝/上限逻辑引用它**——常量只被 `__all__` 导出，纯摆设。实测：

```
candidate columns (width=10): count=16 range=[-3..12]
enumerated = 4 rotations x 16 columns = 64 ; MAX_CANDIDATES=40
```

**影响**

- 文档承诺的「固定预算 40」与实际枚举 64 不一致——读者按文档理解会误以为搜索被预算约束；
- 「不同机器重放不同」的顾虑实际不成立：枚举顺序固定（朝向在外 · 列在内）、同分先枚举到的赢，结果与机器无关（docstring 自己也写了「结果与机器无关」，与上一句自相矛盾）；
- 属可观测性/可维护性问题，不影响功能正确性（64 候选对单帧开销影响可忽略，`measure_m4_cost` 已记账）。

**修复建议**

二选一：① 若要真正限制候选数，在 `plan_placement` 枚举后按预算截断或提前退出，让 `MAX_CANDIDATES` 名副其实；② 若 64 候选是设计意图，删除 `MAX_CANDIDATES`（或改名如 `CANDIDATES_PER_COLUMN`）并同步修正模块 docstring 的「候选数」行——**推荐②**，删除死常量最小改动、消除两处不一致。

---

### L2 · growth 顶部锁定场景晶体生长被系统性抑制且无测试覆盖 · low

**位置**

- `variants/growth/system.py:31-34`（生长规则 docstring：「长在哪：锁定方块占据的每一列，该列最高的锁定格正上方，往上取连续空格；只写空格：不够就少长，一格都不够就不写——**不报错、不硬塞**」）；
- `variants/growth/system.py:296-342`（`_on_piece_locked`：算 `top_per_column` → 目标格 `range(top-1, top-1-height, -1)`；**break 条件 `(x, target_y) in self._board or (x, target_y) in self._active`**）；
- `variants/growth/system.py:360-393`（`_flush`：每帧 `clock:tick` 重试，`MAX_ATTEMPTS_PER_CELL=3` 次失败即放弃）；
- `tests/test_m4_growth_variant.py`（测试只覆盖底部锁定正常生长，**无顶部锁定场景**）。

**机制（实跑证据）**

hardDrop 每帧快速堆叠时，方块在 spawn 区附近（棋盘顶部）原地锁定，锁定格自身贴近新出生方块占据区。`_on_piece_locked` 算出的晶体目标格 = 锁定列最高格正上方 → **几乎总被同帧 spawn 的新方块占据**（`in self._active`）→ 不入待写清单或入清后 `_flush` 每帧被 `write_condition_failed` 拒（撞活动格）→ 3 帧重试全失败 → 放弃。实测 300 帧 hardDrop：

```
growth: pieces=9 locked=9 pending=0 crystal=0 attempts=42
```

9 次锁定全部触发生长逻辑，**42 格待写全部 3 次重试失败被放弃，晶体零生长**。对照组（softDrop 正常游玩至 game over）：`crystal=22`、`extensions=('growth.crystalCount', 22)`——底部锁定场景端到端正常。

**说明**

该行为本身被设计文档「一格都不够就不写——不报错、不硬塞」「宁可少长，不写错」覆盖，**不是功能性 bug**：锁定格贴近顶部时「没有空格可长」确实是语义允许的少长。但存在两点缺口：

1. **边界未显式声明**：docstring 只描述了「锁定格正上方取连续空格」，没有说明「棋盘堆高（锁定格贴近 spawn 区）时晶体生长会被新方块持续占用而系统性抑制」——变体 B 的消费方（渲染器/录像/指标）看到 0 晶体时会以为是功能故障；
2. **无测试覆盖**：`test_m4_growth_variant.py` 未覆盖顶部锁定场景，该「近顶抑制」路径（`_active` 冲突分支 + 重试放弃分支）无断言兜底。

**影响**

- 可观测性误导：顶部局（AI 高速 hardDrop 或玩家堆叠到顶）变体 B 功能零可见，无日志无事件提示（系统按「宁可少长」静默放弃）；
- 维护风险：`_active` 冲突分支与放弃分支是「少长」的两条关键路径，无测试保护，未来改动可能把「顶部抑制」变成「底部也抑制」。

**修复建议**

① 在 `variants/growth/system.py` docstring 的「生长规则」补一句边界声明：「锁定格贴近 spawn 区（棋盘堆高）时，晶体目标格可能持续被活动方块占用，重试上限内未成功即放弃（实测可完全抑制生长）」；② 在 `tests/test_m4_growth_variant.py` 补顶部场景测试：构造锁定在顶部行的局，断言 `pending_count` 归零、`crystal_count=0`、且 `kernel:error` 不出现（静默放弃是预期，但要钉住「不报错」）。
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
