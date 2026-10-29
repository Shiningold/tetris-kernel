# M7 阶段规格（spec）· Tetris Kernel

> **M7 = 现代外壳**：把 M6 那块"能玩但不现代"的 tkinter 面板，换成一套**真正现代的 Qt 外壳**——**游玩模式**与**开发者模式**同一界面切换，开发者模式把内核**已有**的能力（事件流 · 日志 · 指标 · 系统清单 · 规则与拓扑）**全部铺开**，另加一个**用户可改的配置文件**。
> **红线**：**内核 / 规则 / 契约 / 事件基线一律零改动**——M7 只在**宿主层与一个新的外置包**里长出新东西（M4 与 M6 已经把这条路走通过两次）。

## 1. M7 目标与完成判定

### 1.1 一句话目标

**给这套底座换一身现代的衣服，并把内核的能力全部显形**：PySide6/Qt 外壳（游玩 ↔ 开发者双模式）+ 公开门面 `tetris_kernel.api` + `config.toml` 用户配置，**而内核、规则集、契约与事件基线一个字节都不动**。

### 1.2 M7 兑现的四件事

1. **Qt 外壳**（`src/tetris_kernel/qt/`）——**游玩模式**（棋盘 · HOLD / NEXT · 统计）与**开发者模式**（事件流 · 系统清单 · 指标 · 日志 · 规则与拓扑）**同窗切换、同一份数据**。
2. **公开门面**（`src/tetris_kernel/api.py`）——**任何外部前端只需依赖它**：开一局 · 拿快照 · 订阅事件 · 提命令 · 看系统清单 · 读配置。**门面是"外置"这条纪律的载体**（§7.1）。
3. **用户配置文件**（`config.toml` + `host/config.py`）——玩法 · 难度 · 种子 · 手感（DAS / ARR）· 主题 · 面板刷新频率，**读得到也写得住**。
4. **可分发的 Qt 程序**——`tetris-qt.exe`（`--windowed`），**与内核 / 总线 / 规则三层的零第三方依赖并存**（PySide6 走**可选 extra**）。

### 1.3 完成判定（DoD 摘要，全表见 §9）

- **六道门 `G7-1`…`G7-6` 全绿**（§9.1）。
- **质量门**（§9.2）全绿：pytest 主套件 + 基线套件 + ruff + ruff format + pyright + lint-imports + 事件登记 + 契约基线 + 冻结区基线 + 事件数基线。
- **桌面级冒烟**：`tetris-qt.exe` 双击 → 主菜单 → 开局 → **两种模式都能切** → 能玩到局结束（§7.9）。

## 2. 术语速查

| 词 | 是什么 |
| --- | --- |
| **Qt 外壳** | `src/tetris_kernel/qt/` —— 一个**在分层栈之外**的包：它只 import `tetris_kernel.api` + 标准库 + PySide6 |
| **公开门面** | `src/tetris_kernel/api.py` —— **外部前端的唯一入口**（§7.1） |
| **外置包** | `tetris_kernel/qt/`：**只依赖门面**（`G7-5` 机检守着）→ **拆成独立仓是机械操作** |
| **游玩模式** | 棋盘 · HOLD / NEXT · 统计条 —— **面向玩家** |
| **开发者模式** | 事件流 · 系统清单 · 指标 · 日志 · 规则与拓扑 —— **面向内核**（**只读**） |
| **归一键名** | `left` / `right` / `up` / `down` / `space` / `z` / `x` / `c` / `p` / `escape` / `q` —— `KeyboardInput.KEY_BINDINGS` 的键名 |
| **`config.toml`** | 项目根的**用户配置**（`[game]` / `[input]` / `[ui]` / `[dev]` 四段 · §7.7） |
| **extra** | `pip install "tetris-kernel[qt]"` —— **PySide6 走这条**（§4.2） |

## 3. 现状（**全部实测 · 不许猜**）

| 事实 | 证据 |
| --- | --- |
| **M6 的图形层只有一块 Canvas** | `adapters/render/graphics.py`：`TkPainter` 建 200 个 `create_rectangle` + 7 条 `create_text`；**没有字体层级 · 没有色彩语义 · 没有事件流面板** |
| **M6 的菜单是 tkinter 原生控件** | `host/menu.py`：`tk.Radiobutton` + `tk.Button` —— **系统默认外观** |
| **渲染器的纯逻辑层可以原样复用** | `GraphicsRenderer.update_snapshot(snapshot)` → `FrameModel`（**不含 tkinter**）——`G6-2` 已证明它逐格正确 |
| **输入适配器只吃"键名 + 按下/抬起"** | `adapters/input/keyboard.py`：`handle_key(key, kind)` —— **读键在 `reader.py`**；**DAS 150 ms / ARR 33 ms**（`DEFAULT_DAS_MS` / `DEFAULT_ARR_MS`） |
| **快照带齐了画完整画面所需的字段** | 契约 v1 的 22 个顶层字段（`board` · `active` · `ghostDelta` · `hold` · `next` · `score` · `lockTimer` · `gravityTimer` · `phase` …） |
| **事件流面板已有现成的** | `systems/metrics/trace.py`：`EventPanel`（环形缓冲 4096 条 · `by_namespaces()` 过滤 · `export_bytes()`） |
| **指标系统每帧采样** | `systems/metrics/system.py`：遇 `clock:tick` 采样一次 · 环形留 256 条 · 指标名清单 `METRIC_NAMES` 固定顺序 |
| **系统目录里现成 6 个可插拔系统** | `log` · `metrics` · `replay` · `achievements` · `rotations` · `ai`（+ 变体 B 的 `growth` 住在 `variants/`） |
| **仓库里没有公开门面** | `src/tetris_kernel/__init__.py` 只有模块 docstring —— **外部前端现在只能 import 内部模块**（这正是"外置"要解决的事） |
| **仓库里没有用户配置** | `pyproject.toml` 是构建配置；`~/.tetris-data` 存的是产物 jsonl —— **用户可改的运行时配置从零开始** |

**结论**：M7 **不需要碰内核**，也**不需要重写渲染逻辑**——**画面模型复用 `GraphicsRenderer`，手感复用 `KeyboardInput`，观测能力全部是现成的订阅者**。M7 要做的只有三件新事：**门面 · 外壳 · 配置**。

## 4. 范围边界

### 4.1 M7 范围内

- `src/tetris_kernel/api.py`（新）——**公开门面**（§7.1）。
- `src/tetris_kernel/qt/`（新）——**Qt 外壳包**：`app.py`（外壳与双模式）· `board.py`（棋盘绘制）· `panels.py`（开发者面板）· `input.py`（键名归一）· `theme.py`（视觉 token）。
- `src/tetris_kernel/host/config.py`（新）——`config.toml` 读写（**纯标准库**：`tomllib` 读 + 受限 writer 写）。
- `config.toml`（新）——项目根的**默认配置**（入库）。
- `src/tetris_kernel/host/cli.py`（改）——**`graphics` 子命令删掉**（**破坏性**）→ 新增 `qt` 子命令。
- `src/tetris_kernel/adapters/render/graphics.py`（改）——**保留 `GraphicsRenderer`**（纯逻辑）· **删掉 `TkPainter`**（画笔由 Qt 接管）。
- `src/tetris_kernel/host/graphics.py` · `src/tetris_kernel/host/menu.py` · `src/tetris_kernel/adapters/input/graphics_input.py`（**删**）——tkinter 版整体退场。
- `pyproject.toml`（改）——**新增可选 extra `qt`**（**`dependencies` 一字不改**）。
- `release/qt_launcher.py` + `release/tetris-qt.spec`（新）——`--windowed` 的双击即玩 exe。
- `tests/test_m7_*.py`（新）——`G7-2`…`G7-5` 的机检。

### 4.2 M7 范围外（**明确不做**）

- **不改内核与规则**（`kernel/` + `rules/` 是冻结区 · 改一行也该红）。
- **不改契约面**（事件目录 / 命令集 / 快照字段 / 契约基线一律零变化）。
- **不改事件数基线**（Qt 层不进默认装配 · 不新增事件）。
- **不改 `TerminalRenderer` 的既有行为**（终端版继续在）。
- **不做**开发者模式的**系统装卸**（**只读展示系统清单** · §7.5）——**热插拔能力仍在注册表里**（M3 的 `G3-2` 守着），**M7 不给它做交互入口**。
- **不做**音效 · 联网 · 触屏 · 多人 · 多语言 · 皮肤商店 · 录像回放界面（`replay` 仍是终端命令）。
- **不做** Arcade / pygame / Web 前端（**技术选型见 ADR-101**）。
- **不做**把 `rich` 从终端版里拿掉（终端版继续用它）。

### 4.3 M7 期间最容易犯的五条

1. **别在 Qt 层里长出第二套规则**——画面的一切来自 `FrameModel`，规则一切在内核。
2. **别在 Qt 层里 import 内部模块**——**只 import `tetris_kernel.api`**（`G7-5` 机检守着）。**"外置"不是口号**。
3. **别让 Qt 的事件循环接管游戏节拍**——**节拍仍归宿主**（`PlaySession`），Qt 侧只注入"泵事件的 `sleeper`"（§7.6）。
4. **别让开发者面板的高频事件拖死主线程**——事件流**按时间节流刷新**（§7.6 · NFR-7-2）。
5. **别把"能出窗口"当成"能玩"**——门判据要判到"**同一局同一指纹**"（`G7-6`），不能只判"窗口打开了"。

## 5. 交付物清单（文件级）

| 路径 | 新/改 | 归属 | 干什么 |
| --- | --- | --- | --- |
| `src/tetris_kernel/api.py` | 新 | 门面 | **公开门面**：会话 · 快照 · 事件 · 命令 · 系统清单 · 配置 |
| `src/tetris_kernel/qt/__init__.py` | 新 | 外置包 | 外壳包入口（**只导出 `run()`**） |
| `src/tetris_kernel/qt/app.py` | 新 | 外置包 | `QApplication` 外壳 · 主菜单 · **双模式切换** |
| `src/tetris_kernel/qt/board.py` | 新 | 外置包 | 棋盘绘制（吃 `FrameModel`） |
| `src/tetris_kernel/qt/panels.py` | 新 | 外置包 | 开发者模式面板（事件流 · 系统 · 指标 · 日志 · 规则） |
| `src/tetris_kernel/qt/input.py` | 新 | 外置包 | Qt 键 → **归一键名** |
| `src/tetris_kernel/qt/theme.py` | 新 | 外置包 | 视觉 token（配色 · 字号 · 间距） |
| `src/tetris_kernel/host/config.py` | 新 | 宿主层 | `config.toml` 读 / 写 / 校验 |
| `config.toml` | 新 | 仓库根 | 默认配置（入库） |
| `src/tetris_kernel/host/cli.py` | 改 | 宿主层 | `graphics` **删** → `qt` **增** |
| `src/tetris_kernel/adapters/render/graphics.py` | 改 | 适配层 | 留 `GraphicsRenderer` · 删 `TkPainter` |
| `src/tetris_kernel/host/graphics.py` · `host/menu.py` | 删 | 宿主层 | tkinter 版退场 |
| `src/tetris_kernel/adapters/input/graphics_input.py` | 删 | 适配层 | tkinter 输入桥退场 |
| `pyproject.toml` | 改 | 构建 | `[project.optional-dependencies] qt` |
| `release/qt_launcher.py` · `release/tetris-qt.spec` | 新 | 发布 | `--windowed` 双击即玩 |
| `tests/test_m7_api.py` · `test_m7_config.py` · `test_m7_qt.py` | 新 | 测试 | `G7-2`…`G7-5` 的机检 |
| `docs/spec-M7.md` · `docs/plan-M7.md` | 新 | 文档 | 本规格与执行计划 |
| `docs/决策记录.md` | 改 | 文档 | ADR-101 |

## 6. 契约面规格（**M7 零变更**）

- **命令集**：不变（17 条 · 契约 v1 冻结）。
- **事件目录**：不变（**Qt 层不新增事件**——它只读快照与事件）。
- **快照字段**：不变（**画面需要的字段早就齐了**，见 §3）。
- **契约基线**：**410 条零变化**。
- **冻结区**：`kernel/` + `rules/` **14 文件零变化**。
- **事件数基线**：**零变化**（Qt 层不进默认装配 · 不订阅事件）。

## 7. 行为面规格

### 7.1 公开门面：**任何外部前端的唯一入口**

**为什么要有它**：现在 `tetris_kernel/__init__.py` 是空的——**外部前端想接内核，只能 import 内部模块**（`host.assembly` · `kernel.snapshot` …），那就不是"外置"，那是"扒着内核的内部实现"。**门面把"外部前端需要什么"显式写出来**，并且**只给最窄的那一份**。

- **门面给什么**（**建议的形状 · 实现时以本节为准**）：

| 能力 | 说明 |
| --- | --- |
| `open_session(...)` | 开一局：玩法 · 难度 · 种子 · 手感参数 → 返回一个会话句柄 |
| `Session.snapshot()` | **当帧快照**（**只读**） |
| `Session.events(...)` | 订阅 / 查询事件流（**带命名空间过滤**） |
| `Session.submit(...)` | 提命令（**与玩家同一通道**） |
| `Session.systems()` | **系统清单**（名字 · 版本 · 订阅 · 命令 · 是否已装）——**只读** |
| `Session.metrics()` / `Session.logs()` | 指标与日志（**只读**） |
| `load_config()` / `save_config()` | 配置读与写（§7.7） |

- **门面不给什么**（**最窄能力原则**）：**不给 `Kernel` 对象本身** · 不给内部总线 · 不给规则集实例 · 不给装配层的私有入口。
- **门面不许 import PySide6**——**装 core（不装 extra）时门面必须能用**。
- **门禁**：`G7-5` 的 AST 机检——**`tetris_kernel/qt/**` 只许 import `tetris_kernel.api` + 标准库 + PySide6**。

### 7.2 视觉：**一套 token，两个模式共用**

- **`theme.py` 是一处 token 表**（背景 · 面板 · 描边 · 文本 · 强调色 · 字号 · 间距）——**两套模式读同一张表**，**不许各写一套配色**。
- **视觉基准**：仓库里已有的 `docs/html源文件/产品预览_源文件.html`——**那是本项目的"现代"基准**（暗色底 · 语义色 · 统计条 · 事件流 · 系统清单）。M7 的目标就是**把它从静态假数据变成真的**。
- **棋盘绘制**：**吃 `FrameModel`**（`GraphicsRenderer` 的产出）——**画面模型是现成的，Qt 只当画笔**（与 `G6-2` 同一套纪律）。

### 7.3 游玩模式：**面向玩家**

- **棋盘**（10 × 20 + 隐藏行不画）· **幽灵落点** · **HOLD** · **NEXT（前 5）** · **统计条**（分数 · 行数 · 等级 · 连击 · B2B · 方块数）· **锁定延迟进度条** · **结算面板**。
- **与 M6 的关系**：**画面内容逐格相同**（同一个 `GraphicsRenderer`）——**变的只是皮肤与排版**（`G7-2`）。
- **不许**在 Qt 层里"顺手多画点什么"——**多画的东西要有快照字段支撑**。

### 7.4 开发者模式：**面向内核（只读）**

**这一模式要做的事只有一件：把内核已有的能力**显形**——不新增能力，只新增"看得见"。

| 面板 | 内容 | 数据来源（**全部现成**） |
| --- | --- | --- |
| **状态条** | phase · seed · frame · 快照版本 · 已装系统数 · 事件总数 · 逻辑帧耗时 | 快照 + 指标 |
| **统计条** | 分数 · 行数 · 等级 · 连击 · B2B · 方块数 · APM · 行/块 | 快照（APM 由指标推） |
| **事件流** | 逐条：序号 · 帧号 · 类型 · 负载摘要；**按命名空间过滤** · 按级别着色 · 自动滚动 | `EventPanel`（环形 4096 · `by_namespaces()`） |
| **系统清单** | 名字 · 版本 · 订阅了谁 · 派发什么 · **是否已装**（**只读**） | `SystemRegistry` + 自动发现的目录 |
| **指标** | 指标名清单（`METRIC_NAMES` 固定顺序）+ 最近采样 | `MetricsSystem.samples` |
| **日志** | 最近条目（级别 · 频道 · 消息） | `LogSystem` 的内存目标 |
| **规则与拓扑** | 规则集标识 · 重力轴 · 锁定延迟上限 · 结构指纹 · 扩展指纹 · 幽灵位移 | 快照 + 规则集 |

- **只读，不装卸**（用户 2026-10-29 拍板）：**系统清单只展示状态，不提供装卸按钮**。**理由**：装 `ai` 会当场接管这一局、装 `growth` 会改棋盘、卸 `log` / `metrics` 会让降级控制器失去输入——**在"看"的模式里放"改"的开关是危险的**。**热插拔能力仍在注册表里**（M3 的 `G3-2` 用例守着），**M7 不给它做交互入口**。
- **两种模式共用同一局**：**顶部切换**（`QTabWidget` 或等价）——**不重开一局、不暂停、不改任何状态**。

### 7.5 输入：**翻译归一键名，其余全复用**

- Qt 的 `QKeyEvent` → **归一键名**（`Qt.Key_Left` → `left` · `Qt.Key_Space` → `space` · `Qt.Key_Escape` → `escape` · 字母取小写）→ 交给**既有的** `KeyboardInput.handle_key(key, kind)`。
- **DAS / ARR / 命令翻译 / `input:raw` / `input:intent` 两条事件**：**全部由 `KeyboardInput` 负责**——**M7 不写第二套**（与 ADR-100 同款纪律）。
- **归一表要单测**：每个键一行，**键名 → 动作 → 命令类型**逐条断言（`G7-3`）。

### 7.6 实时接线：**节拍归宿主，Qt 只负责被叫醒**

- `PlaySession` 的 `sleeper`（等待函数）**由 Qt 侧注入**：注入一个"**等一小段时间 + 泵一次 Qt 事件循环**"的实现（`QApplication.processEvents()`）。
- **顺序不变**（`host/play.py` 的 `_run_frame`：先读输入 → 再推进帧 → 再喂开销 → 最后渲染）。
- **不许用 `QTimer` 接管游戏循环**——**那是 M6 明确否决过的做法**（ADR-100 ④）：节拍只有一个主人。
- **面板刷新按时间节流**：事件流 / 指标 / 日志面板**不在每帧重排**——**距上次刷新不足 `refresh_ms`（默认 100 ms）就跳过**（NFR-7-2）。**节流在 `on_frame` 回调里做**（**用时间戳比，不用定时器**）→ **零定时器**。

### 7.7 配置：`config.toml`

- **位置**：**项目根** `config.toml`（入库 · 提交默认值）。
- **格式**：**TOML**（`tomllib` 是 Python 3.11+ 标准库 → **零新依赖**）。
- **四段 schema**：

```toml
[game]
variant = "standard"      # standard | gravity-right | growth
start_level = 5           # 难度 = 起始等级（沿用 M6 的口径）
seed = 0

[input]
das_ms = 150             # 默认 = KeyboardInput 的 DEFAULT_DAS_MS
arr_ms = 33              # 默认 = KeyboardInput 的 DEFAULT_ARR_MS

[ui]
theme = "dark"            # dark | light
scale = 1.0              # 界面缩放（高 DPI 兜底）
show_dev_mode = false     # 启动后是否直接停在开发者模式

[dev]
log_level = "info"        # 面板显示的日志阈值
event_namespaces = ["command", "piece", "board", "score", "clock"]
refresh_ms = 100          # 面板刷新节流
```

- **读取**：**文件不存在 → 用默认值**（**不自动写盘**——**没写过的配置不该凭空多出一个文件**）。
- **写入**：**受限 writer**（只支持本 schema 的字符串 / 整数 / 浮点 / 布尔 / 字符串数组）——**不是通用 TOML 序列化器**。
- **非法值**：**明确报错**（**不静默夹取**）——沿用 `host/menu.py` 的既有口径（越界就报错）。
- **改完即生效的项**：`ui.*` 与 `dev.*`（**下一次刷新即生效**）；`game.*` 与 `input.*` **在下一局生效**（**开局后改难度要重开一局**——**难度 = 起始等级**这条口径决定的）。

### 7.8 子命令与打包

- **`tetris graphics` 子命令删掉**（**破坏性改动**）→ 新增 **`tetris qt`**。
- **`tetris play` / `tetris sim` / `tetris replay` / `tetris inspect` 的行为一个字都不许变**。
- **`tetris qt` 在没装 PySide6 时**：**明确报错并提示** `pip install "tetris-kernel[qt]"`——**不静默退回 tkinter 版**（**tkinter 版已被删除**，没有"退回"这个选项）。
- **打包**：`release/qt_launcher.py` + `release/tetris-qt.spec`（**`console=False`**）→ `tetris-qt.exe`。
- **PyInstaller 与 PySide6**：PySide6 自带 hook，但**必须在真机上冒烟**（`G7-6`）——**不靠推断**（与 M6 的 tkinter 坑同类）。
- **发布分开**：Qt exe **单独发**（体积大），**核心 sdist / wheel 不含 PySide6**。

## 8. 非功能要求（M7 可判定版）

| 编号 | 要求 | 判据 |
| --- | --- | --- |
| NFR-7-1 | **图形层不拖慢逻辑帧** | 渲染与输入**不计入逻辑帧预算**（NFR-2）——**图形版每帧的推进耗时与终端版同量级**（同机配对 A/B） |
| NFR-7-2 | **面板不拖死主线程** | 事件流 / 指标 / 日志面板**刷新间隔 ≥ `refresh_ms`（默认 100 ms）**——**不许每帧重排** |
| NFR-7-3 | **启动可用** | 从双击到主菜单出现 **< 5 s**（**PySide6 的导入比 tkinter 重** → 门槛比 M6 宽，但仍要实测） |
| NFR-7-4 | **核心依赖零变化** | `pyproject.toml` 的 **`dependencies` 一字不改**（仍只有 `rich`）——PySide6 **只在可选 extra 里** |
| NFR-7-5 | **可关窗** | 关窗 / `q` / 局结束 → **进程正常退出**（不残留窗口、不挂起） |
| NFR-7-6 | **可无头验证** | **门判据的机检不许起真窗口**（纯逻辑层与门面必须**无头可测**——CI 没有显示器） |

## 9. 验收标准

### 9.1 M7 门判据（**硬门**）

| 编号 | 判据 | 覆盖 |
| --- | --- | --- |
| **G7-1** | **零变更**：契约基线 **410 条零变化** · 冻结区 **14 文件零变化** · 事件数基线 **零变化** · 事件登记 **50 / 0 警告** | M7 |
| **G7-2** | **画面等价**：Qt 画笔消费的 `FrameModel` 与 M6 的图形渲染器**逐格相同**（含活动方块占格与幽灵落点）——**同一份快照 → 同一份画面模型** | M7 |
| **G7-3** | **输入等价**：Qt 按键经输入桥产生的**动作与命令序列**，与 `KeyboardInput` 同映射表产出的**逐条相同**（含 DAS / ARR 口径） | M7 |
| **G7-4** | **配置往返**：`config.toml` **读 → 改 → 写回 → 再读**，**逐字段相等**；**非法值明确报错**；**文件不存在时用默认值且不写盘** | M7 |
| **G7-5** | **外置纪律**：`tetris_kernel/qt/**` **只 import `tetris_kernel.api` + 标准库 + PySide6**——**import 任何内部模块即红** | M7 |
| **G7-6** | **可玩且确定**：Qt 版跑完整局到 `game-over`，**同种子同输入的棋盘指纹与终端版相同**；**两种模式都能切换**；`tetris-qt.exe` **双击即出主菜单并能玩到局结束**（桌面级冒烟 · 真跑） | M7 |

### 9.2 质量门（每道都必须是红的）

pytest 主套件 · pytest 基线套件 · `ruff check` · `ruff format --check` · `pyright` · `lint-imports` · 事件登记 · 契约基线 · 冻结区基线 · 事件数基线。

### 9.3 M7 的反向验证清单（**必须真跑 · 改坏 → 必红 → 精确还原**）

1. **让 Qt 层 import 一个内部模块**（如 `from ..kernel.snapshot import Snapshot`）→ `G7-5` 的用例**必红**。
2. **破坏 Qt 输入桥的归一表**（把 `Qt.Key_Left` 映射成 `right`）→ `G7-3` 的用例**必红**。
3. **让 Qt 画笔少画一格**（幽灵落点少一格）→ `G7-2` 的用例**必红**。
4. **让配置写回丢掉一个字段**（`das_ms` 不落盘）→ `G7-4` 的用例**必红**。
5. **让开发者模式订阅一个新事件**（多发一条）→ 事件数基线**必红**（`G7-1`）。

## 10. 任务分解与执行顺序

| 阶段 | 干什么 | 门 |
| --- | --- | --- |
| **S0** | 规格与计划入档 · `G7-n` 登记 · ADR-101 | — |
| **S1** | **公开门面** `api.py`（不含 Qt） | G7-5 |
| **S2** | **配置** `host/config.py` + `config.toml` | G7-4 |
| **S3** | **Qt 画笔 + 主题**（吃 `FrameModel`） | G7-2 |
| **S4** | **Qt 输入桥**（复用 `KeyboardInput`） | G7-3 |
| **S5** | **外壳与双模式**（主菜单 · 游玩 · 开发者面板 · 模式切换） | G7-6 |
| **S6** | **子命令切换 + 打包**（`graphics` 删 · `qt` 增 · exe 冒烟） | G7-6 |
| **S7** | **端到端与收尾**（指纹一致 · 质量门 · 文档回填 · PROGRESS · ai_docs） | 全门 |

## 11. 风险与未决

### 11.1 四条会让 M7 返工的风险

1. **PySide6 装不上 / 没有显示器的机器** → 图形前端不可用。**解法**：PySide6 走**可选 extra**（**核心与 CLI 终端版不受影响**）· `tetris qt` **明确报错并给出安装提示**（**不静默降级**）。
2. **exe 体积暴涨** → PySide6 打包后约 **60–120 MB**（M6 的 tkinter 版约 15 MB）。**解法**：**Qt exe 单独发**（§7.8）· **核心 wheel / sdist 不含它**。
3. **事件流把主线程拖死** → 每帧十几条事件 × 每帧重排 = 卡死。**解法**：**按时间节流刷新**（NFR-7-2 · §7.6）。
4. **高 DPI 下棋盘错位** → 缩放后格子不是整数像素 → 边缘渗色。**解法**：**格子尺寸取整**并用 `devicePixelRatio` 校正（S3 落地时实测）。

### 11.2 已知缺口（**明确不在 M7 范围**）

- **开发者模式不能装卸系统**（只读展示）——**用户 2026-10-29 拍板**（§7.4）。
- **没有录像回放界面**（`replay` 仍是终端命令）。
- **难度只改起始等级**，不改计分表（沿用 M6 的口径）。
- **`config.toml` 不在数据目录**（在项目根）——**发布出去的 exe 旁边要有它**（随 exe 发一份默认的）。

### 11.3 M7 的 ADR

- **ADR-101**：现代外壳的技术选型与形态（**PySide6/Qt** · **外置包 + 公开门面** · **可选 extra** · **双模式同窗** · **`config.toml`** · **替换 tkinter 版**）。

## 12. 完成定义（DoD）与提交约定

### 12.1 DoD

- `G7-1`…`G7-6` **全绿**；质量门全绿；**反向验证清单五条真跑过**。
- **`release/qt_launcher.py` + `release/tetris-qt.spec` 入库**（可复现重建）。
- **文档同步**：架构 §9 补 Qt 交付形态 · 需求 §12 登记 `G7-n` · 决策记录 ADR-101 · PROGRESS 改写快照 · ai_docs 当天条目。
- **版本号**：M7 交付后**抬到 0.3.0**（**替换了入口 + 改了依赖面** → **破坏性改动** · 提交标题带 `!`）。

### 12.2 提交约定（沿用 `AGENTS.md`）

- 标题 `类型：主题`；**一次提交 = 一个逻辑改动**；**格式化与逻辑改动分开提交**。
- **纯文档与代码不混提**。
- **破坏性改动**（删 `tetris graphics` · 换渲染栈 · 依赖面变化）在类型后加 `!`。
