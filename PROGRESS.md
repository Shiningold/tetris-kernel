# 进度 · Tetris Kernel

> 最后更新：2026-10-23 15:15
> **本文件是当前状态快照，不是流水账**：每次工作结束直接改写下面三段，历史记录交给 git，**不往这里堆历史条目**。
> 分批契约、门判据编号、任务编号索引在 `docs/需求与边界.md` §11–§12；**契约 v1 冻结清单在 `docs/契约v1清单.md`**。
> **M0 的规格与分阶段计划在 `docs/spec-M0.md` 与 `docs/plan-M0.md`**——阶段编号 S0–S18、验证方案、提交序列都在那里，本文件只记状态。

## 当前在攻

**M0 阶段 B（契约面）进行中**：S0 规格与计划入档（`2185114`）· S1 分层空壳与打包可用（`facd1d4`）· 顺带修掉一个静默失效的质量门（`cb24f7d`，见卡点）· **S2 事件契约声明（44 条）已完成并经审查修订** · **S3 命令契约声明（16 条 + 12 值拒绝原因）已完成并经审查修订**（`e4b4c63` + `4866bbc`）· **S4 快照契约声明（21 + 5）+ `PlacementInfo` 已完成**（`d294e8b`，见 ai_docs 2026-10-23 条目）。`contracts/` 现有五个模块：`events.py` · `types.py`（取值域 + 类型文法表，**S4 补了形态 9：命名结构引用**）· `commands.py` · `snapshot.py`（21 顶层 + `board` 5 子字段 + `Active` 4 子字段 + 坐标常量 + 内核状态清单机器可读版）· `placement.py`（`PlacementInfo`，frozen 6 字段）。契约清单第 1 / 2 / 3 / 4 / 5 / 6 / 7 / 8 / 10 / 13 / 15 项已勾。**下一步：S5 规则签名声明（8 条）+ `RuleContext`。**

## 待办

**接下来一步（S5，现在就能开工）**

- [ ] `contracts/rules.py`：8 条签名的机器可读声明（四项接口 + 形状表 / 踢墙表，架构 §7.3.1）
- [ ] `RuleContext`（`frozen=True`）：`frame` · `level` · `piecesPlaced` · `axis`（`down/left/right/up`）· `stepMs`
- [ ] 中性类型交换结构：`ClearGroup` · `PlacementClass` · `ScoreState` · `ScoreDelta`（**定义在 `contracts/`，不放 `rules/`**）
- [ ] 签名声明与架构 §7.3.1 表格逐条比对；`gravity_vector` 必须是向量、`shape_cells` / `kick_offsets` 必须带 `ctx`、`scan_clears` 不能只返回数量
- [ ] S5 新增类型串按修订后文法（含形态 9）回验——`test_all_type_strings_match_grammar` 已就位，新域加进 `_all_declared_type_strings()` 即可

**后续阶段（详见 `docs/plan-M0.md` §4）**

- [ ] S5 规则签名声明 → S6 契约校验与基线脚本
- [ ] S7–S14 行为面：随机源 · 快照 · 命令通道 · 合并配额 · 总线 · 帧模型 · 规则 · 宿主装配
- [ ] S15 五道门测试 → S16 采开放项 B / C → S17 冻结（契约基线写明「契约 v1 冻结」+ 内核基线）→ S18 收尾

**挂着的未定项（S3 审查时挑出来的）**

- [ ] **`hold` 在 running 态没有活动方块时的行为**：事件侧 `piece:held.taken` 已是 `str | None`（v1 承认暂存槽可以是空的），命令侧却只有「本块已用过暂存」这一条拒绝原因。需求 §8.2 没写，实现期才能确认那个窗口到底存不存在——**届时按 major 变更流程定，不要在实现期临时加一个拒绝原因**（那时基线可能已冻结）。已写进 `contracts/commands.py` 的 `hold` doc。

**不阻塞 M0（明确挂着）**

- [ ] 建 GitHub 仓库并配 `remote`（当前仓库无远程）· 选许可证并补 `LICENSE`
- [ ] 把 README 里的 `<仓库地址>` 占位换成真实 GitHub 地址
- [x] README：GitHub 安装方式 + `python -m tetris_kernel` 入口
- [x] `git init` 并按提交规范切了历史（历史记录从此进 git；提交标题统一为 `类型：主题`）

## 卡点

- **~~`pyright` 的 strict 从未生效~~ → 已解（2026-10-23，`cb24f7d`）**：pyright 1.1.414 的配置项里**已无 `overrides` 键**，写 `[[tool.pyright.overrides]]` 只会得到一行 `Config contains unrecognized setting "overrides"` 警告，**strict 静默失效而退出码仍是 0**——架构 §1.1 说的"内核与总线走 strict"其实一直没兑现。改用 `strict = ["src/tetris_kernel/kernel", "src/tetris_kernel/bus"]`（已实测：报错如约出现、配置无警告）。**教训：会"静默失效"的质量门比没有质量门更坏。**
- **4 条开放项待闭合**：B 单帧逻辑开销真实值 · C 每帧真实事件数 · E exe 下载点落点 · F 轮次号 / 还原被拒命令。**B、C 要内核跑起来才有值**，按计划在 **S16** 采（已排进 `docs/plan-M0.md`）；不标定就没法验收性能预算与降级阈值。（A「轮次与命令数上限」已闭合：轮次 8 / 命令数 100，且上限做成内核构造参数；D「PyPI 包名」已闭合：不发 PyPI，只发 GitHub。）
- **契约 v1 尚未落定**：三处字段空白已于 2026-10-23 补齐（ADR-062），**剩下的门是"清单逐项勾完 + 清单校验脚本"**——清单在 `docs/契约v1清单.md`，声明落地在 S2–S5，脚本在 S6，冻结在 S17。**进度：S2 落地事件面（第 1 / 2 / 7 / 8 / 13 项），S3 落地命令面（第 5 / 6 项），S4 落地快照面（第 3 / 4 / 10 / 15 项），S5 落地规则签名面（第 9 项）。**注意清单状态栏的「已声明」**不等于「已过机检」**——机检脚本在 S6 才落地。
- **S3 定下的三条跨切规则**（实现期照此执行，别再各写各的）：**统一生命周期门禁**——除 `start` / `pause` / `resume` / `reset` 外，改棋盘内容或活动方块位置的命令在 `phase != running` 时一律被拒，原因取 `not_started` / `paused` / `game_over`；**配额跨切**——命令数 / 轮次数配额超限时，任何命令都以 `quota_exceeded` 被拒（架构 §6.4）；**参数不合规格统一 `invalid_argument`**。例外：`setLevel` 不受门禁约束（需求 §8.2 给它的可拒绝情形只有「越界」），生命周期命令走 `illegal_transition`。**门禁归属已做成必填字段** `CommandSpec.lifecycle_gated`（无默认值，漏表态直接 `TypeError`），三个门禁原因由它派生——别手写，手写会被拒。
- **类型文法表是 S6 解析器的规格书，规则与实例必须同时回验**：S3 审查发现规则 1（"`None` 只在可空形态里且永远末位"）与自家三个取值域冲突，S6 照规则写解析器会把合规声明判成"不合规"。已改规则（形态 2 放宽、`None` 可作普通成员）。**S4 补了形态 9（命名结构引用 `Board` / `Active`）**——需求 §8.3 把 board / active 写成结构类型，成员表在 `contracts/snapshot.py` 另行声明。全库类型串的文法回验已进测试（`test_all_type_strings_match_grammar`，含负例抽查），S5 新增类型串时把新域加进收集函数即可。
- **S2 的事件声明经一次外部审查修订**（`tests/ocr/S2-review.md`，14 项发现：1 高 · 6 中 · 5 低）。**已修**：契约版本号补声明 · 类型文法表扩到能覆盖实际写法 · 信封类型补断言 · 负载字段级断言补齐（反向验证：删字段、改类型都必红）· `piece:held.taken` 改 `str | None` · 同名不同型字段改名（`board:changed.cells`→`cellChanges`、`board:lines-cleared.rows`→`rowIndexes`）· `state:changed` 补 `rule` · 共用取值域挪到 `contracts/types.py` · 基线归属口径写进架构 §4.4。**未修**：第 14 项（未知事件容错）随 S11 一起补。
- **落盘路径的跨平台策略留到 M2**，但边界已钉（ADR-064）：落盘经宿主注入的 sink，路径可注入，文件名不带墙钟。实现时别在 `systems/` 里直接 `open()`。
- **发布落点未定**：渠道已定（只发 GitHub Releases，ADR-008），但**分窗口版 / 控制台版 exe 在国内的下载点**要 M4 前定，发布时点推后到 M4 之后。不阻塞 M0–M3。
- **门判据与任务编号只落地了一小部分**：门判据已有 10 个（`G0-1`–`G0-5` / `G2-1` / `G2-4` / `G4-1` / `G4-2` / `G4-6`），M1 / M3 的还没有；任务编号只有 10 个，M0–M4 其余仍缺。**随做随定，落定前不编造**（索引见 `docs/需求与边界.md` §12；M0 阶段用 `docs/plan-M0.md` 的 S0–S18 内部编号，不占正式编号空间）。

