# M0 阶段规格（spec）· Tetris Kernel

| 项目 | 内容 |
| --- | --- |
| 适用范围 | **M0 骨架**：命令进、事件出，完全无头跑通 |
| 上游依据 | `docs/需求与边界.md`（§5 架构约束 N1–N7 · §6 边界铁律 B-1…B-10 · §8 契约 · §12 批次与门判据）· `docs/项目架构.md`（§3 目录 · §4 模块边界 · §5 数据模型 · §6 帧模型 · §7 实现红线）· `docs/决策记录.md`（ADR-001…ADR-064）· `docs/契约v1清单.md`（16 项） |
| 状态 | **本文只定规格，不改任何代码或既有文档**。执行顺序见 §10，验收判定见 §9 |
| 日期 | 2026-10-23 |

> **读法**：§1 说清 M0 交付什么；§3 说清输入输出与对外接口；§4 说清边界（**做什么与坚决不做什么同等重要**）；§6 是"声明面"（M0 必须定死并入基线的东西）；§7 是"行为面"（M0 必须跑起来的最小实现）；§9 是可判定的验收标准。
> **编号纪律**：本文用 `M0-` 前缀的是**本文内部的条目编号**，不是 `docs/需求与边界.md` §12 的正式任务编号（正式任务编号随做随定，落定前不编造；已登记的只有 `M0-15` / `M0-28`）。

---

## 1. M0 目标与完成判定

### 1.1 一句话目标

**命令进、事件出，完全无头跑通。** M0 结束时，仓库里有一副能自转的骨架：外部派发命令 → 内核校验 → 状态变更 → 事件流出 → 订阅者消费；没有渲染器、没有输入设备也能跑完整局。

### 1.2 M0 兑现的四件事

| # | 兑现物 | 说明 |
| --- | --- | --- |
| 1 | **分层空壳** | `kernel / bus / rules / contracts / systems / variants / adapters / host` 八个子包就位，依赖方向由 `lint-imports` 持续校验 |
| 2 | **契约 v1 落定** | 44 事件 / 16 命令 / 快照 21 字段 + `board` 5 子字段 / 拒绝原因 12 值 / 规则接口 8 条签名 / `PlacementInfo` 6 字段，全部写成机器可读声明并进"只增不改"基线（对应正式任务号 **M0-28**） |
| 3 | **内核跑起来** | 快照、可播种随机源、命令校验、phase 门禁、帧推进五步、总线（信封 + 全局序号 + 订阅 + 优先级 + 异常隔离） |
| 4 | **判据可验** | G0-1…G0-5 五道门各有对应测试；开放项 B / C 有实测值 |

### 1.3 完成判定（DoD 摘要，全表见 §9）

1. 五道门 G0-1…G0-5 全过，且每道门有一个可复跑的测试。
2. `docs/契约v1清单.md` 16 项全勾，`contracts/` 基线已生成，生成基线的那次提交 message 写明「契约 v1 冻结」。
3. `kernel/` + `rules/` 冻结哈希基线已生成（**M0 收尾才生成**，不是开工时）。
4. 四条静态检查全绿：`ruff check .` · `pyright src/tetris_kernel` · `lint-imports` · `pytest`。
5. 开放项 B（单帧逻辑开销真实值）、C（每帧真实事件数）已采集并落档。

---

## 2. 术语速查

沿用 `docs/需求与边界.md` §4，本文不重新定义。需要特别记住的四个：

| 术语 | 在 M0 的含义 |
| --- | --- |
| 命令 Command | 外部对内核的**意图**，可被接受也可被拒绝，不直接改状态 |
| 事件 Event | 已发生的**事实**，不可变、可广播；系统对外输出的唯一形式 |
| 快照 Snapshot | 某一时刻内核完整状态的只读导出，**导出时深拷贝** |
| 轮次 Round | 一次事件分发及其引发的命令执行；命令递归被展开为同帧内多个轮次 |

---

## 3. 输入 / 输出与对外接口

### 3.1 输入（M0 接受的东西）

| 输入 | 来源 | 约束 |
| --- | --- | --- |
| 种子 `seed: int` | 构造参数 | 决定内核随机源初值；同种子必须完全可复现（FR-1.4 / NFR-1） |
| 规则集标识 `rule: str` | 构造参数 | 进快照 `rule` 字段与录像头（v1 先只存标识） |
| 配额配置 | 构造参数 | 默认轮次 **8** / 命令数 **100**（实时局）；回放宿主注入大得多的上限 |
| 命令 `Command` | `execute(cmd)` | 命令集封闭且可枚举（16 条），参数禁浮点、只含 JSON 标量与元组（B-7） |
| 帧推进许可 | `tick` 命令被接受 | **一帧 = 一条 tick**，tick 是本帧的推进开关；倍速 = 连续跑 N 帧 |

### 3.2 输出（M0 产出的东西）

| 输出 | 形态 | 约束 |
| --- | --- | --- |
| 事件流 | 信封五字段：`序号 / 帧号 / 类型 / 负载 / 元数据` | 序号全局单调递增，**唯一权威在总线**（架构 §4.2）；负载不可变 |
| 元数据 | 三键：`source` / `producer` / `cause` | `cause` = `(帧号, 帧内序号)`，`None` = 无上游命令；**未知键静默保留但不解释**（NFR-9） |
| 快照 | 只读 dataclass | 导出时深拷贝；**绝不 JSON 化**；`rngState` 在 `snapshot()` 路径恒为 `None` |
| 帧推进步骤序列 | `Iterator[tuple[EventDraft, ...]]` | 五步：重力 → 锁定 → 消行 → 塌落 → 出生；**必须完整消费，不得提前 break**（B-9） |

### 3.3 对外接口签名（M0 定稿，之后只增不改）

```python
# kernel —— 唯一状态所有者，对外只有这两个接口
def execute(self, cmd: Command) -> tuple[EventDraft, ...]: ...
def advance_frame(self) -> Iterator[tuple[EventDraft, ...]]: ...
def snapshot(self) -> Snapshot: ...

# bus —— 序号唯一权威；内核不 import 总线
def subscribe(self, event: str, handler: Handler) -> Subscription: ...          # 单参数，给适配器与渲染器
def subscribe_system(self, event: str, handler: SystemHandler) -> Subscription: # 双参数，给系统层，ctx.view() 惰性取视图
def publish(self, drafts: Sequence[EventDraft]) -> None: ...                    # 封装信封 + 分配序号 + 分发

# contracts —— 公共叶子，不 import 任何东西
EVENT_TYPES: Mapping[str, EventSpec]        # 44 条：负载字段声明 + 产出者
COMMAND_SPECS: Mapping[str, CommandSpec]    # 16 条：参数规格 + 可拒绝情形
SNAPSHOT_FIELDS: Mapping[str, FieldSpec]    # 21 顶层字段 + board 5 子字段
RULE_SIGNATURES: Mapping[str, SignatureSpec] # 8 条规则接口签名
@dataclass(frozen=True) class PlacementInfo: ...   # 6 字段
```

> **三条硬约束**（架构 §7.1）：内核 **不 import 总线**；两个订阅入口**共享同一个注册表与优先级序列，排序在合并之后进行**；**不提供 `bus.view()`**——访问器会把"分发期间内核状态不变"退回成约定（ADR-021）。

### 3.4 装配闭环（M0 的最小 host）

```
宿主派发命令 → kernel.execute → 取回事件草稿 → bus.publish（封装信封、分配序号、分发）
             → 订阅者回调里派发的命令进队列 → 当前分发结束后 FIFO 取队列 → 新一轮分发
             → 队列清空后跑 advance_frame() 的五步 → 帧末产出一条 state:changed
```

- 帧末 = 命令轮次循环结束（队列清空或超限）**且**帧推进步骤序列跑完。快照不在中间某轮之后拉（架构 §6.6）。
- `state:changed` **每帧至多一条**，帧末产出；它不是"每次变更一条事件"，脏标记实际由 `version` 承担。

---

## 4. 范围边界

### 4.1 M0 范围内

- 八个子包的空壳与最小实现；依赖方向零违规。
- `contracts/` 机器可读声明（事件 / 命令 / 快照 / 规则签名 / `PlacementInfo`）。
- 内核：生命周期（待开始 / 运行 / 暂停 / 结束 + 六种迁移）、命令校验、phase 门禁、快照、可播种随机源、帧号与帧内序号、轮次与配额、同因拒绝合并。
- 总线：信封封装、全局序号、精确 / 通配 / 全局三种订阅、优先级、异常隔离、环形历史缓冲（最小）。
- 规则：四项接口 + 形状表 / 踢墙表接口的实现位 + **最小标准实现**。
- 宿主：最小装配（命令进、事件出）+ CLI 骨架（`play / sim / replay / inspect` 四个子命令先占位，M0 只要求 `sim` 能跑无头批量）。
- 测试骨架：`pytest` + `hypothesis`（`derandomize=True`，落点 `tests/conftest.py`）。
- `tools/` 自研脚本：内核纯净度、事件类型登记、冻结基线、`contracts/` 只增不改基线、契约清单校验。
- 两份基线：`contracts/` 只增不改基线（M0 收尾）· `kernel/` + `rules/` 内容哈希基线（M0 收尾）。

### 4.2 M0 范围外（**明确不做，留给后续批次**）

| 不做的事 | 归谁 | 为什么现在做会坏事 |
| --- | --- | --- |
| 标准玩法完整实现（踢墙表逐条验证、T-Spin 三类、连击 / B2B 计分表、锁定延迟重置次数） | M1 | M1 的开门判据就是"踢墙表逐条验证 · T-Spin 三类全对"；M0 只要求接口与最小实现 |
| 录像、回放、时间旅行、事件历史查询 UI | M2 | 依赖 M0 定稿的事件目录与帧号语义 |
| 降级三档（关调试事件 → 降日志级别 → 关历史缓冲） | M2 | NFR-2 的降级是 M2 的事；M0 只**采集**单帧开销真实值 |
| 热插拔与插件生命周期语义（`kernel:system:installed/removed` 的真实触发） | M3 | AC-8 是 M3 判据；M0 只在 `contracts/` 登记事件类型 |
| 运行期切换规则集 / 换轴命令 | **M3**（ADR-061） | 需求 §10 说变体 A「切换经命令通道」，但 16 条命令里没有这条——**排到 M3，不在 M0 抢跑**；M0 只把 `axis` 作为 `RuleContext` 字段预留 |
| 渲染器（tkinter）、输入适配器（DAS/ARR）、存储适配器 | M3–M4 | M0 是无头批次；渲染器与输入都是订阅者/适配器，M0 不需要 |
| 变体实现（重力可旋转 / 生长 / 塌方 / 拓扑） | M4 | G4-1 判的是 M4 落地变体时内核差异为空 |
| 落盘路径策略（`%LOCALAPPDATA%` / `~/Documents`） | M2 | 边界已钉（ADR-064：经宿主注入 sink、路径可注入、文件名不带墙钟），策略留 M2 |
| 发布 / exe / GitHub 仓库与许可证 | M4 之后 | ADR-008：发布时点整体推后 |

### 4.3 三条"做得到也不许做"（M0 期间最容易犯）

| 编号 | 红线 | M0 的具体含义 |
| --- | --- | --- |
| B-1 | 不得引入"关闭事件分发"的快速 / 批量模式 | 批量模拟照常发全部事件（ADR-018） |
| B-2 | 内核文件不得为玩法需求改动 | `kernel/` + `rules/` 自 M0 进冻结区 |
| B-5 | `writeCells` 是外部改变棋盘的唯一合法通道 | M0 就把这条通道建起来，不给第二条路 |
| B-9 | 帧推进必须完整消费 | 装配层用 `try/finally` 耗尽步骤序列 |
| N1 | 单一状态所有者 | 内核之外任何模块不得写棋盘 / 分数 / 活动方块 |

---

## 5. 交付物清单（文件级）

```
src/tetris_kernel/
├─ __init__.py
├─ contracts/        公共叶子：事件注册表 · 命令规格 · 快照字段声明 · 规则签名声明 · PlacementInfo
├─ rules/            冻结区：四项接口 + 形状表 / 踢墙表 + 最小标准实现
├─ kernel/           冻结区：状态 · 命令校验 · 快照 · 随机源 · 帧推进五步
├─ bus/              信封封装 · 全局序号 · 订阅 · 优先级 · 异常隔离 · 环形历史
├─ systems/          M0 只放空壳与契约占位
├─ adapters/         M0 只放空壳
├─ variants/         M0 只放空壳
└─ host/             装配层 · 帧循环 · CLI 骨架

tests/
├─ conftest.py                     hypothesis derandomize=True 落点
├─ test_kernel_lifecycle.py        G0-1 · G0-2
├─ test_bus_isolation.py           G0-5
├─ test_command_rounds.py          G0-4
├─ test_determinism.py             G0-3
├─ test_contracts_declarations.py  契约面与需求文档的一致性
└─ baseline/
   ├─ kernel_hashes.json           M0 收尾生成
   └─ contracts_baseline.json      M0 收尾生成（本文新增落点，见 §6.6）

tools/
├─ check_kernel_purity.py          禁第三方 import · 禁平台名 · 禁墙钟读
├─ check_event_registry.py         扫 contracts/：类型必须已登记
├─ check_frozen_baseline.py        kernel/ + rules/ 内容哈希
├─ contract_baseline.py            contracts/ 只增不改基线（生成前先过清单）
└─ measure_frame_cost.py           采集开放项 B / C
```

---

## 6. 契约面规格（M0 必须定死并入基线）

> **这是 M0 最不可返工的部分**：契约 v1 一旦冻结，改字段 = major 变更（B-4 / ADR-054）。`contracts/` 的基线就是那道牙齿。

### 6.1 事件目录 44 条（M0 全部登记；**登记 ≠ 必须在本批产生**）

| 命名空间 | 数量 | 事件 | 产出者 |
| --- | --- | --- | --- |
| kernel | 5 | `kernel:ready` · `kernel:system:installed` · `kernel:system:removed` · `kernel:error` · `kernel:quota-exceeded` | 内核 2 · 总线 1 · host 2 |
| command | 3 | `command:received` · `command:accepted` · `command:rejected` | 内核 |
| game | 6 | `game:created` · `game:started` · `game:paused` · `game:resumed` · `game:over` · `game:reset` | 内核 |
| clock | 2 | `clock:tick` · `clock:gravity` | 内核 |
| input | 2 | `input:raw` · `input:intent` | 输入适配器 |
| rng | 1 | `rng:next` | 内核 |
| piece | 11 | `piece:spawned` · `piece:moved` · `piece:rotated` · `piece:kicked` · `piece:softdrop` · `piece:harddrop` · `piece:locked` · `piece:held` · `piece:hold-denied` · `piece:lock-delay-start` · `piece:lock-delay-reset` | 内核 |
| board | 4 | `board:changed` · `board:lines-cleared` · `board:collapsed` · `board:garbage-received` | 内核 |
| queue | 1 | `queue:refilled` | 内核 |
| score | 3 | `score:changed` · `score:combo-changed` · `score:b2b-changed` | 内核 |
| progress | 1 | `progress:level-changed` | 内核 |
| state | 1 | `state:changed` | 内核 |
| render | 1 | `render:frame` | 渲染器 |
| log | 1 | `log:entry` | 日志系统 |
| metric | 1 | `metric:sampled` | 指标系统 |
| achievement | 1 | `achievement:unlocked` | 成就系统 |

- **合计 44 条**；产出者分布：内核 **35** · 总线 1 · host 2 · 输入适配器 2 · 渲染器 1 · 日志 / 指标 / 成就各 1（ADR-063）。
- **命名空间 ≠ 产出者**：`kernel:` 前缀说的是"这件事属于内核实例"，不必然是内核干的。
- 每条的**关键负载字段**以 `docs/需求与边界.md` §8.1 为准，逐条写进 `contracts/` 的声明。负载一律不可变（`frozen=True` + tuple）。
- `rng:next` 负载瘦身：随机值 + 序号 + 状态指纹（短哈希），**不含完整 MT 状态**（ADR-060）；**默认开启**，不随调试事件一起关。
- 事件目录**只允许追加**；新增必须登记到 §8.1 并归入既有命名空间（B-4 / ADR-054 / NFR-9）。

### 6.2 命令目录 16 条（参数规格 + M0 行为范围）

| 命令 | `args` | 可被拒绝的情形 | M0 行为范围 |
| --- | --- | --- | --- |
| `start` / `pause` / `resume` / `reset` | 无 | 状态不合法（`illegal_transition`） | **完整实现**（FR-1.1 六种迁移，非法迁移发事件） |
| `tick` | 无 | 未在运行中 | **完整实现**（帧推进开关） |
| `moveLeft` / `moveRight` | 无 | 未开局 / 非运行态 / 无活动方块 | 通道 + 校验 + 最小位移语义 |
| `softDrop` / `hardDrop` | 无 | 同上 | 通道 + 校验 + 最小语义 |
| `rotateCW` / `rotateCCW` / `rotate180` | 无 | 同上；**旋转无合法落点仍被接受**，另发受阻事件（ADR-043） | 通道 + 校验 + 最小语义（标准踢墙表 M1） |
| `hold` | 无 | 本块已用过暂存（`hold_already_used`） | 通道 + 校验 + 最小语义 |
| `setLevel` | `level: int` | 越界（`level_out_of_range`） | **完整实现**（校验 + 状态写入） |
| `injectGarbage` | `rows: int`、`holes: tuple[int, ...]` | 未开局 / 非运行态 | 通道 + 校验 + 最小注入（M1 完善空洞语义） |
| `writeCells` | `cells: tuple[tuple[int, int, int], ...]` | 未开局 / **非运行态** / 越界 / 未获授权 / 条件不满足 / 超配额 | 通道 + 校验 + 最小落笔 + `board:changed`（FR-11.1；授权粒度是 C 级需求，M0 只做"未授权默认拒绝"） |

**统一生命周期门禁**（架构 §7.1）：除 `start/pause/resume/reset` 外，所有改变棋盘内容或活动方块位置的命令在 `phase != running` 时一律 `rejected`，原因取 `not_started` / `paused` / `game_over`。**不设例外**——AI 与其它系统的命令同样适用。

**命令三段生命周期**：`command:received`（出队执行前发，**不在入队时发**）→ `command:accepted` / `command:rejected`。三者**都带不可变 `args`**（架构 §7.2）。

**拒绝原因枚举（12 值，只增不改）**：`not_started` · `paused` · `game_over` · `quota_exceeded` · `invalid_argument` · `level_out_of_range` · `out_of_bounds` · `illegal_transition` · `no_active_piece` · `hold_already_used` · `not_authorized` · `write_condition_failed`。

**参数规格纪律**：`args` 只含 JSON 标量与元组、**禁浮点**（B-7）、可无损往返 JSON、反序列化按命令类型还原。

### 6.3 信封与元数据

- 信封五字段：`序号 / 帧号 / 类型 / 负载 / 元数据`。**不设逻辑时间戳**（逻辑时间 = 帧号 × 步长，派生即可）。
- 元数据三键：`source`（`human/ai/replay/tutorial/network/test`，内核**不得因它分支** · FR-8.5）· `producer`（总线分发时自动填）· `cause`（`(帧号, 帧内序号) | None`）。
- **未知键静默保留但不解释**（NFR-9）。

### 6.4 快照字段（21 顶层 + `board` 5 子字段）

顶层：`version` · `rule` · `phase` · `frame` · `board` · `active` · `ghostDelta` · `hold` · `holdUsed` · `next` · `score` · `lines` · `level` · `combo` · `b2b` · `piecesPlaced` · `grounded` · `lockTimer` · `extensions` · `extensionFingerprint` · `rngState`。
`board` 子字段：`width` · `height` · `hiddenRows` · `cells` · `structureFingerprint`。

- **这张表是"至少包含"，不是"只有这些"**：内核每新增一个状态字段，快照必须同步新增一个字段（契约清单第 15 项）。
- 坐标系全项目唯一：x 右正、y 下正、**第 0 行是顶部**、`cells` 行优先；`hiddenRows` 是**数量**不是可见起始行。
- `structureFingerprint`（棋盘内容）与 `extensionFingerprint`（扩展槽摘要）**必须一眼可辨**，前者**不含**扩展字段。
- `rngState` 是两条构造路径：`snapshot()` 恒为 `None`；存档路径用 `dataclasses.replace(snapshot, rngState=...)` 填。**不在 `frozen=True` 上惰性赋值**。

### 6.5 规则接口签名 8 条（M0 定稿，ADR-061）

`RuleContext`（定义在 `contracts/`）：`frame: int` · `level: int` · `piecesPlaced: int` · `axis: str`（`down/left/right/up`）· `stepMs: int`。

| 能力 | 签名 | 关键点 |
| --- | --- | --- |
| 边界（拓扑） | `wrap_cell(x: int, y: int, w: int, h: int) -> tuple[int, int]` | 坐标映射**函数**，不是参数 |
| 重力 | `gravity_vector(ctx: RuleContext) -> tuple[int, int]` | 方向必须是**向量 (x, y)**，横向重力时 dy = 0 |
| 重力 | `gravity_interval(ctx: RuleContext) -> int` | 多少逻辑帧落一格 |
| 消行 | `scan_clears(board: Sequence[Sequence[int]], ctx: RuleContext) -> tuple[ClearGroup, ...]` | 返回"轴 + 行/列索引 + 格子清单 + 放置分类"，**不能只返回数量** |
| 消行 | `classify_placement(board, placement: PlacementInfo, ctx) -> PlacementClass` | T-Spin 三分 + Mini，**归消行判定模块，不另开第五项** |
| 计分 | `score_clear(groups, ctx, prev: ScoreState) -> ScoreDelta` | **纯函数**：返回增量与新状态，**落笔由内核做** |
| 形状 | `shape_cells(ctx, pieceType: str, rotation: int) -> tuple[tuple[int, int], ...]` | **必须带 `ctx.axis`** |
| 踢墙 | `kick_offsets(ctx, pieceType: str, fromRotation: int, toRotation: int) -> tuple[tuple[int, int], ...]` | 同上，且**含 180°** |

四条纪律：**规则集不可变**，随时间的一切都表达为关于 `ctx` 的纯函数 · 形状表与踢墙表**必须带轴维度** · T-Spin 判定需要 `PlacementInfo`（内核提供放置上下文）· `PlacementInfo` 放 `contracts/` **不放 `rules/`**（后者是冻结区）。

`PlacementInfo` 6 字段：`pieceType: str` · `rotation: int` · `cells: tuple[tuple[int, int], ...]`（绝对坐标）· `lastMoveWasRotation: bool` · `kickIndex: int | None` · `kickOffset: tuple[int, int] | None`。

**其余四个中性交换结构**（定义在 `contracts/`）：`PlacementClass`（`tSpin: str` · `classKey: str`）· `ClearGroup`（`axis: str` · `index: int` · `cells: tuple[tuple[int, int], ...]` · `classification: PlacementClass | None`）· `ScoreState`（`score` · `lines` · `combo` · `b2b` · `level`）· `ScoreDelta`（`scoreDelta: int` · `linesDelta: int` · `state: ScoreState`）。

**T-Spin 分类怎么到达计分**：冻结的 `score_clear(groups, ctx, prev)` 里没有分类的位置，
而计分模块看不见分类就没法算 T-Spin 的分值（FR-4.6）——所以分类**挂在消行组上**
（`ClearGroup.classification`），内核拿到分类后挂上去再交给 `score_clear`。**不给
签名加参数**（那是改冻结签名），**内核只搬运不判断**。

**棋盘参数是只读序列**（`Sequence[Sequence[int]]`，类型文法形态 10），不是 tuple：
内核内部棋盘是可变 `list[list[int]]`，规则只读，写成 tuple 会逼内核在热路径上
反复深拷贝。

**取值域归谁定**：**内核要解读的字段**（`ctx.axis` 重力方向、`group.axis` 消行
轴）取值域由契约层钉死且**构造即校验**；**内核只透传的字段**（`tSpin` /
`classKey`）取值域由规则集定义。

**`stepMs` 的语义待定**：架构只给了字段名与类型，没说它是谁算、给谁用（事件侧
`gravityInterval` 是逻辑帧数，毫秒这条线在契约里没有第二个落点）——**实现期按
major 变更流程定**。

> 上表的**机器可读版**已落 `contracts/rules.py`（S5）：`RULE_SIGNATURES`（8 条，
> 参数名 + 类型 + 返回类型）+ `RuleContext` 与四个中性交换结构（frozen dataclass）；
> 命名结构注册表在 `contracts/structures.py` 的 `NAMED_STRUCTS`（结构名 → 成员表，
> 成员表**从声明推导**）。断言守着的是**逐条相等**：每条签名的参数名与类型与本表
> 逐条相等，`gravity_vector` 必须是向量，形状表 / 踢墙表必须带 `ctx`，
> `scan_clears` 不能只返回数量。

### 6.6 基线规则（两份基线，别混）

| 基线 | 管什么 | 键 → 值 | 生成时点 |
| --- | --- | --- | --- |
| `contracts/` 只增不改基线 | **接口面**：事件负载字段 / 命令参数规格 / 快照字段类型 / 规则签名 | 字段名（或事件类型名 / 命令类型名）→ **该字段类型声明**的哈希 | **M0 收尾**（M0-28） |
| `kernel/` + `rules/` 内容哈希基线 | **实现面** | 文件路径 → 内容哈希（归一化换行符） | **M0 收尾** |

纪律：

1. **粒度按字段级**：允许新增键（新增字段 / 事件 / 命令），**禁止已有键的哈希变化**（改既有字段类型 = major 变更）。
2. **进基线的是声明，不是值**：信封结构、事件负载字段、命令参数规格、快照字段类型、规则签名进；序号、帧号、负载的具体值一律不进。
3. **生成 `contracts/` 基线前必须先过 `docs/契约v1清单.md` 16 项**：有未勾项就**拒绝生成**并打印缺哪几项。清单自身不进基线（过程文档进基线会自锁）。
4. 生成基线的那次提交 message **必须写明「契约 v1 冻结」**，日后靠 `git log` 定位 v1 时点。
5. 指纹用 `blake2b` + TLV 编码，**不用 Python 内置 `hash()`**（受 `PYTHONHASHSEED` 影响，跨进程不稳定）。
6. 清单勾选状态的落地位置（本文新增落点，文档未定）：清单表格的「状态」列由 `☐` 改 `☑`；`tools/contract_baseline.py` 读该列判机检项，人检项以人勾为准，脚本只做"未勾即拒生成 + 打印缺项"。

---

## 7. 行为面规格（M0 的最小实现）

### 7.1 内核状态清单（契约清单第 15 项，M0 必须写定）

| 内核状态字段 | 快照字段 | 说明 |
| --- | --- | --- |
| `version` | `version` | 每次变更递增，脏标记载体 |
| `ruleId` | `rule` | 规则集标识 |
| `phase` | `phase` | 待开始 / 运行 / 暂停 / 结束 |
| `frame` | `frame` | 逻辑帧号，暂停期照走 |
| `board.width/height/hiddenRows/cells` | `board.*` | 内部用 `list[list[int]]`，导出时深拷贝成 tuple |
| `active` | `active` | 活动方块（类型 / 朝向 / x / y），可空 |
| `ghostDelta` | `ghostDelta` | 幽灵落点**位移向量** (dx, dy) |
| `hold` / `holdUsed` | `hold` / `holdUsed` | 暂存与"每块一次"限制 |
| `nextQueue` | `next` | 预览队列 |
| `score` / `lines` / `level` / `combo` / `b2b` | 同名 | 计分与进度 |
| `piecesPlaced` | `piecesPlaced` | 已放置方块数 |
| `grounded` / `lockTimer` | `grounded` / `lockTimer` | 落地与锁定计时 |
| `extensions` | `extensions` / `extensionFingerprint` | 扩展槽只放标量、key 唯一；内核**不理解内容** |
| 随机源实例 | `rngState` | `snapshot()` 路径为 `None`，存档路径另填 |

**不进快照的状态**（只活在运行时）：事件序号、轮次计数、命令数计数、同因拒绝合并表、订阅注册表、随机源实例本体。

> 上表的**机器可读版**已落 `contracts/snapshot.py`（S4，审查修订后）：对应表为 `KERNEL_STATE_TO_SNAPSHOT`——`KernelStateEntry(kernel_field, snapshot_path, derived)` 列表，**一行一字段**，两侧都是点分路径（`board.width` / `active.pieceType`），`derived` 标出"快照侧是算出来的"那三条（结构指纹、扩展摘要、随机源的可序列化状态）；"不进快照的状态"六项为 `RUNTIME_ONLY_STATE`（**英文标识符 → 中文说明**，与快照字段、内核状态字段同一套命名体系，守卫才真能触发）；形态 9 的结构名 ↔ 成员表登记在 `contracts/structures.py` 的 `NAMED_STRUCTS`（**类型文法层面**的清单，快照 / 放置 / 规则三处共用，成员表**从声明推导**）。断言守着的是**两个方向**：快照叶子字段恰好一个来源，且内核侧字段路径不许压行、不许写散文——实现期随 S8 复核。

### 7.2 命令通道

- **phase guard 前置**：命令校验层统一前置一道门，不散落在各命令里各判各的。
- **`received` 在出队执行前发**：入队时本帧帧号尚未确定，在入队时发会让帧号差一帧、回放对齐整体错位。配额丢弃时**先补 `received` 再补 `rejected`**。
- **帧内序号**只对**被接受**的命令编号（每帧归零，被拒的不占号）；**tick 也占号**（正常帧里占 0）。回放宿主自生成的 tick 同样占 0。
- **同因拒绝合并**：键 = `(phase, 命令类型, 拒绝原因)`；`received` 与 `rejected` **成对合并、计数一致**；`mergedCount` 起始值 = 1；phase 变化或 `resume` 后重置计数。**"一条事件承载 N 次拒绝"是"有事件"，不是"没事件"**——否则事件完备性检查会误判。
- **配额**：轮次上限 **8** / 命令数上限 **100**，**做成内核构造参数**（回放宿主注入大得多的上限，否则回放超限会丢命令，违反 B-6）。两个计数器**按帧累计**（每帧开始清零，不是按单轮）；`tick` 自身不计入命令数；超限判定发生在"取队执行"之前，丢弃剩余命令并补发 `command:rejected`（`quota_exceeded`），**逐条计入合并计数**，不跨帧顺延；两类配额超限统一用 `kernel:quota-exceeded`（靠负载里的配额名区分）。
- **写裁决**：`writeCells` 同一帧内多请求按到达顺序串行；命中同一格默认**后者覆盖**（可配置为后者被拒），两种结果都必须发 `board:changed` 或拒绝事件；单帧写入格子总数设上限，超限**整批拒绝**并发配额超限事件。

### 7.3 帧模型

- **帧号推进与推进许可是两件事**：帧号 +1、帧内序号归零在 tick **出队时**发生；推进许可在 tick **被接受**时授予。
- **帧首以 tick 为队首重建队列**：tick 插到队首，上帧遗留命令排其后；tick 恒定是本帧第 1 条命令。
- **五步**：重力 → 锁定 → 消行 → 塌落 → 出生。**必须 step 化**（整批会让所有事件共享"执行后的最终态"）；每个步骤内部**先算完再一次性写入**；**不得提前 break**。
- **推进前复查 phase**：`clock:tick` 分发期间若有人派发 `pause`，它会在下一轮执行——首个步骤前必须复查 phase，不是 running 就直接结束本帧。
- **`reset` 归零帧号、帧内序号与版本号**（"与首次启动完全等价"包含帧号）。帧号局内唯一，**事件序号才是唯一时间轴**。
- **`resume` 生效的那一帧不推进**，这是设计不是缺陷（推进许可在本帧第 1 条命令就裁决完了）。

### 7.4 总线

- **信封封装 + 序号分配**在总线（内核生成草稿时拿不到序号）；草稿不含序号。
- **三种订阅**：精确订阅 / 命名空间通配（`piece:*`）/ 全局（`*`）；命名可枚举且有文档。
- **优先级**：`subscribe` 与 `subscribe_system` **共享注册表**，**排序在合并之后进行**，保证"业务订阅者先于日志订阅者"。
- **异常隔离**：单个订阅者抛错 → 转成 `kernel:error`（**只留给订阅者异常这一种语义**），不得中断内核或影响其它订阅者。
- **自产事件复用轮次机制**：`log:entry` / `metric:sampled` / `achievement:unlocked` 与"订阅者派发的命令"同一种东西——分发期间产生、延后处理，总线维护待发布队列，当前分发结束后 FIFO 发出，**不新造概念**。
- **未知事件容错**：按信封解析后静默丢弃或记 trace，不得中断分发或回放。
- 环形历史缓冲（最近 N 条）M0 做**最小可用版本**（区间查询与时间旅行 M2）。

### 7.5 规则最小标准实现

- **四项接口 + 两张表在 M0 定稿签名**（§6.5），并提供**最小标准实现**：标准 7 种方块的基础形状与 4 朝向、恒等 `wrap_cell`、向下重力向量与最小重力间隔、整行满即消的最小消行判定、基础计分、**恒等偏移的最小踢墙表**（标准踢墙表 M1 实现并逐条验证）。
- **规则表落成 Python 字面量常量**（ADR-032），不落外部数据文件。
- 规则集**不可变、无状态**；一切随时间变化的量通过 `RuleContext` 传入。

### 7.6 随机源与时间源

- 随机源：标准库 `random.Random`（MT），**只用 `random()` 与 `getrandbits()` 两个原语**（ADR-010），7-bag 洗牌自己实现 Fisher-Yates。
- **内核随机源只服务内核**：外部系统（未来的 AI）必须自建实例并显式播种，**不得取用或推进内核随机源**（FR-5.3）。
- 每次取值发 `rng:next`（随机值 + 序号 + 状态指纹），**默认开启**。
- 时间源：**禁读墙钟**——`kernel/` 与 `rules/` 内不得出现 `time.time()` / `time.monotonic()` / `datetime.now()` / 模块级 `random.*` / 未注入的 `random.Random()` / `os.urandom` / `uuid.uuid4`（`id()` 只警告不阻断）。时间以固定步长逻辑帧推进，步长可配置（FR-5.1）。

### 7.7 快照与只读视图

- **视图与快照是同一个 dataclass**（ADR-056），不写两套。
- **视图构造必须纯读**：不得触碰随机源（尤其不能惰性初始化 RNG），不得改任何内核状态——否则"关闭全部可观测系统后内核逐帧一致"这条判据在概率上就废了。
- **视图缓存粒度是"一次发布"**，不是步骤名；`ctx.step` 只是标签（取值集合含 `frame_end`）。
- **渲染器走单参数入口**，只消费帧末宿主推送的那一份快照（ADR-059）。

---

## 8. 非功能要求（M0 可判定版）

| 项 | 要求 | 判定方式 |
| --- | --- | --- |
| 依赖方向 | `host > adapters > systems > bus > kernel > rules > contracts` 零违规；变体不 import kernel/bus/adapters/host；系统之间互不直调；内核/总线/规则不依赖第三方 | `lint-imports`（4 条契约全过） |
| 类型 | `kernel/` 与 `bus/` 走 **strict**，其余 standard | `pyright src/tetris_kernel` 零错 |
| Lint / 格式 | ruff 0.17.1（环境内那一份），line-length 100 | `ruff check .` · `ruff format` |
| 内核纯净度 | 无第三方 import、无平台名、无墙钟读 | `tools/check_kernel_purity.py`（AST + 文本扫描） |
| 事件登记 | 事件类型必须已登记在 `contracts/` | `tools/check_event_registry.py` |
| 确定性 | 同种子 + 同命令序列**逐帧一致**（NFR-1） | G0-3：10 次全等 |
| 性能 | 单帧逻辑开销 ≤ 帧步长 10%（60fps → ≤1.6ms，NFR-2）；**M0 只标定真实值**，降级实现是 M2 | `tools/measure_frame_cost.py` 采 P50 / P99（开放项 B） |
| 事件数 | 每帧真实事件数 | 同上工具采集（开放项 C） |
| 可测试性 | 内核 100% 可无头运行；每个规则函数可脱离事件系统单独测试 | G0-1 + 规则单测 |
| 可观测性 | 不存在无事件伴随的状态变更 | G0-2 |
| 属性测试 | hypothesis **必须开 `derandomize=True`** | `tests/conftest.py` 落点 |

---

## 9. 验收标准

### 9.1 五道门（M0 硬门）

| 门 | 判据 | 判定方法（可复跑） |
| --- | --- | --- |
| **G0-1** | 无头跑通 10000 帧 | 不挂任何渲染 / 输入模块，跑满 10000 帧，输出帧数、事件数、最终棋盘指纹与统计；中途无异常、无未消费步骤 |
| **G0-2** | 状态变更 100% 伴随事件 | 遍历"命令类型 × phase × 有无活动方块"，每次 `execute` 后：状态未变 → 必有 `command:rejected`；状态变了 → 必有对应语义事件（`version` 递增次数 ≤ 语义事件条数） |
| **G0-3** | 同种子 10 次结果全等 | 同 seed + 同命令序列跑 10 次，(棋盘结构指纹, score, lines, piecesPlaced) 四项全等；**事件流序列亦全等** |
| **G0-4** | 命令入队不在本轮生效 | 订阅者回调里派发命令 → 该命令产生的事件出现在**下一轮**，不出现在当前分发内；同帧多轮顺序稳定 |
| **G0-5** | 注入抛错订阅者内核存活（AC-4） | 注入一个必然抛错的订阅者 → 内核继续推进、产生 `kernel:error`、其余订阅者**不受影响**（事件一条不少） |

### 9.2 契约 v1 清单 16 项（`docs/契约v1清单.md`）

M0 收尾时 **16 项全勾**：机检 11 项由 `contracts/` 的机器可读声明担保（声明不存在或与文档不符即判未勾），人检 5 项由文档承诺担保。生成基线的那次提交写明「契约 v1 冻结」。

### 9.3 质量门（每道都必须是红的）

`ruff check .` · `ruff format --check .` · `pyright src/tetris_kernel` · `lint-imports` · `pytest` · 四条 `tools/` 脚本。

### 9.4 开放项 B / C 的采集与落档

| 开放项 | 内容 | M0 的动作 |
| --- | --- | --- |
| **B** | 单帧逻辑开销**真实值** | 用 `tools/measure_frame_cost.py` 采 P50 / P99（60fps 预算 1.6ms），记录实际值 |
| **C** | 每帧**真实事件数** | 同工具采集，记录实际值 |

- 这两个数**要内核跑起来才有值**，是 M0 期间采的，不是开工前的输入。
- 落档位置：`PROGRESS.md`「卡点」栏 + 当日 `ai_docs/` 条目。**不标定就没法验收性能预算与降级阈值**（降级本身 M2 实现）。

### 9.5 需求侧验收在 M0 的对应切片

| 需求 | M0 的切片 |
| --- | --- |
| AC-2 事件完备性 | **全量落在 M0**（G0-2） |
| AC-4 错误隔离 | **全量落在 M0**（G0-5） |
| AC-5 无头运行 | M0 做 10000 帧（G0-1）；M2 再做千局统计 |
| AC-1 确定性回放 | M0 做同种子全等（G0-3）；录像 / 回放在 M2 |
| AC-3 / AC-6 / AC-7 / AC-8 | M3 / M4 判据，M0 只保证不留堵路 |

---

## 10. 任务分解与执行顺序

> 顺序有依赖：**先有壳才能装依赖，先有声明才能生成基线，先有基线才谈冻结**。M0 内部再分三段：**阶段 A 空壳 → 阶段 B 契约面 → 阶段 C 行为面 → 阶段 D 验证与冻结**。

### 阶段 A：空壳与工具链

| # | 任务 | 完成标志 |
| --- | --- | --- |
| A1 | 建 `src/tetris_kernel/` 八个子包空壳，各带 `__init__.py` | 目录就位 |
| A2 | 装依赖（**必须先有最小壳**：uv_build 要 `src/tetris_kernel/__init__.py` 存在才能构建） | `uv pip install --python D:\miniconda3\envs\py312\python.exe -e .` + 同参数 `--group dev` 成功 |
| A3 | 四条静态检查首次真跑起来 | `lint-imports` 的四条契约**第一次真的执行**（此前连包都不存在） |

### 阶段 B：契约面（M0 不可返工的部分）

| # | 任务 | 完成标志 |
| --- | --- | --- |
| B1 | `contracts/` 声明：事件注册表 44 条（负载字段 + 产出者） | 与需求 §8.1 逐条对齐 |
| B2 | `contracts/` 声明：命令规格 16 条 + 拒绝原因 12 值 | 与需求 §8.2 对齐 |
| B3 | `contracts/` 声明：快照字段 21 + `board` 5 + 坐标约定 | 与需求 §8.3 对齐 |
| B4 | `contracts/` 声明：规则签名 8 条 + `RuleContext` + `PlacementInfo` 6 字段 | 与架构 §7.3.1 对齐 |
| B5 | `tools/` 四条自研脚本 + 契约清单校验脚本 | 清单未勾完时**拒绝生成基线**并打印缺项 |

### 阶段 C：行为面

| # | 任务 | 完成标志 |
| --- | --- | --- |
| C1 | 随机源（可播种、只用两个原语、`rng:next`） | 同种子序列可复现 |
| C2 | 快照与只读视图（深拷贝导出、视图构造纯读） | 快照改不动内核内层结构 |
| C3 | 内核命令通道（phase 门禁、三段生命周期、合并防抖、配额） | 拒绝不改状态 |
| C4 | 总线（信封、序号、三种订阅、优先级、异常隔离、最小环形历史） | 抛错订阅者被隔离 |
| C5 | 帧模型（五步、帧号语义、轮次、完整消费） | 命令入队不在本轮生效 |
| C6 | 规则最小标准实现（8 接口 + 最小表） | 每个规则函数可脱离事件系统单测 |
| C7 | 宿主最小装配（命令进、事件出、无头闭环） | 无头 10000 帧跑通 |
| C8 | `pytest` + `hypothesis` 骨架（`derandomize=True` 落 `tests/conftest.py`） | `pytest` 全绿 |

### 阶段 D：验证与冻结

| # | 任务 | 完成标志 |
| --- | --- | --- |
| D1 | G0-1…G0-5 五个测试落地 | 每道门一个可复跑测试 |
| D2 | 采集开放项 B / C | 有实测值，落 `PROGRESS.md` + `ai_docs/` |
| D3 | 契约清单 16 项全勾 → 跑清单校验 → 生成 `contracts/` 基线 | 提交 message 写明「契约 v1 冻结」（**M0-28**） |
| D4 | 生成 `kernel/` + `rules/` 冻结哈希基线 | `tests/baseline/kernel_hashes.json` 就位（**M0 收尾才生成**） |
| D5 | 收尾：更新 `PROGRESS.md` 三段 + 写当日 `ai_docs/` 条目 | 快照是当前状态，不是流水账 |

> **顺序上的两个硬依赖**：① `contracts/` 基线必须在 `rules/` 完整实现（**M1**）之前冻结，否则签名在实现期被反向塑形；② `kernel/` + `rules/` 冻结基线**在 M0 收尾生成**——太早生成会让基线在开发期反复失效，成橡皮图章（ADR-019）。
> **随机序列审计**（正式任务号 **M0-15**）依赖 `rng:next` 事件；M0 已默认开启该事件，审计脚本可在 M0 内顺带验一次（M2 的 `M2-26` 是完整版）。

---

## 11. 风险与未决

| # | 风险 / 未决 | 影响 | M0 的处置 |
| --- | --- | --- | --- |
| R1 | `rules/` 的冻结基线在 M0 收尾生成，而 M1 要补齐完整标准规则（踢墙表、T-Spin、计分表） | 基线会被刷新一次 | 属架构 §4.4 的"按批次刷新"，但**刷新必须写明理由**并单独提交，不得静默刷新 |
| R2 | 契约声明面（44 事件）与 M0 行为面（只产生其中一部分）分离 | 容易被误读成"承诺了但没实现" | 声明与实现**同源**（一份数据生成两份视图），避免手抄漂移；清单第 7 项哈希的是负载字段，不是触发时机 |
| R3 | 快照 21 个字段在 M0 多为中性取值 | 契约完整但语义未全实现 | M0 只声明与承载，M1/M3 逐字段补语义；**内核加字段 → 快照同步加字段**这条纪律从 M0 就立 |
| R4 | 配额上限 8/100 对回放宿主太小 | 回放超限会丢命令，违反 B-6 | 上限**必须是内核构造参数**，回放宿主注入大得多的上限；M0 就要留这个参数口 |
| R5 | 事件序号由总线分配，内核拿不到 | `cause` 不能指向全局序号 | `cause` 用 `(帧号, 帧内序号)`；内核与总线的边界在 M0 就按架构 §4.2 立住 |
| R6 | `rules/` 与 `contracts/` 的边界容易混 | 变体改 T-Spin 定义会撞 G4-1 | `PlacementInfo` 放 `contracts/`；`rules/` 只用中性类型，不 import kernel 的 dataclass |
| R7 | 编号纪律 | 新造 `M0-n` 任务号会造成坏引用 | 正式任务编号随做随定；本文用 `M0-` 前缀的只是本文内部条目编号 |
| R8 | 开放项 B / C 无值 → 性能预算与降级阈值无从验收 | M2 返工 | M0 期间必采（D2），采不到就记为卡点 |

---

## 12. 完成定义（DoD）与提交约定

### 12.1 DoD

1. §9.1 五道门全过，各有可复跑测试。
2. §9.3 质量门全绿；§9.4 开放项 B / C 有实测值并落档。
3. `docs/契约v1清单.md` 16 项全勾；`contracts/` 基线已生成；生成基线的提交 message 写明「契约 v1 冻结」。
4. `kernel/` + `rules/` 冻结基线已生成。
5. M0 内核状态清单（§7.1）已写定并与快照字段一一对应。
6. `PROGRESS.md` 已改写为当前状态快照（**不堆历史**），`ai_docs/` 记了当日条目。

### 12.2 提交约定（沿用 `AGENTS.md`）

- 标题 `类型：主题`（新功能 · 修复 · 文档 · 测试 · 重构 · 性能 · 格式 · 构建 · 持续集成 · 杂务 · 回滚），**不要临时造新类型**。
- **破坏性改动**在类型后加感叹号——判据：会不会让旧录像、旧存档或既有调用方失效。契约基线撞红 = 破坏性 = `新功能!：…`。
- 标题与正文之间空一行，正文写"改了什么、为什么"，用中文。
- **一次提交 = 一个逻辑改动**；格式化与逻辑改动分开提交。
- 两个特殊提交：**「契约 v1 冻结」**（生成 `contracts/` 基线的那次）· 冻结基线刷新（写明理由）。
