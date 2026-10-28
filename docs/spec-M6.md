# M6 阶段规格（spec）· Tetris Kernel

> **M6 = 图形版**：把「内核底座」变成**双击就能玩的图形程序**。
> **红线**：**内核 / 规则 / 契约 / 事件基线一律零改动**——M6 只在**适配层与宿主层**长出新东西（M4 已经把这条路走通过一次：渲染器只是又一个订阅者）。

## 1. M6 目标与完成判定

### 1.1 一句话目标

**给这套底座补上一个真正的图形外壳**：tkinter 窗口（完整面板）+ 主菜单（三个变体 + 难度）+ `tetris-graphics.exe`（`--windowed`，双击即玩），**而内核、规则集、契约与事件基线一个字节都不动**。

### 1.2 M6 兑现的三件事

1. **图形渲染器**（`adapters/render/graphics.py`）——**完整面板**：棋盘 · 幽灵落点 · HOLD · NEXT · 分数 / 行数 / 等级 / 连击 · 锁定延迟进度条 · 结算面板。
2. **主菜单**（`host/menu.py`）——**选玩法**（标准玩法 / 重力旋转 / 落地生长）+ **选难度**，选完就开局。
3. **可分发的图形程序**——`tetris-graphics.exe`（`--windowed`）双击即玩；`tetris-cli.exe` 保持原样（终端版仍然在）。

### 1.3 完成判定（DoD 摘要，全表见 §9）

- **六道门 `G6-1`…`G6-6` 全绿**（§9.1）。
- **质量门**（§9.2）全绿：pytest 主套件 + 基线套件 + ruff + ruff format + pyright + lint-imports + 事件登记 + 契约基线 + 冻结区基线 + 事件数基线。
- **桌面级冒烟**：`tetris-graphics.exe` 双击 → 主菜单 → 选玩法与难度 → 开局 → 能玩到局结束（§7.6）。

## 2. 术语速查

| 词 | 是什么 |
| --- | --- |
| **图形渲染器** | `adapters/render/graphics.py` 里的 `GraphicsRenderer`——**快照驱动**的 tkinter 画板 |
| **输入桥** | `adapters/input/graphics_input.py` 里的 `GraphicsInput`——把 tkinter 按键**翻译成归一键名**再交给既有 `KeyboardInput` |
| **主菜单** | `host/menu.py` 里的 `MenuSession`——tkinter 窗口，选玩法与难度 |
| **归一键名** | `left` / `right` / `up` / `down` / `space` / `z` / `x` / `c` / `p` / `escape` / `q`——`KeyboardInput.KEY_BINDINGS` 的键名 |
| **难度** | **起始等级**（1 / 5 / 10）——见 §7.5 |
| **windowed** | PyInstaller 的 `--windowed`：**不弹控制台窗口**（图形程序要的就是这个） |

## 3. 现状（**全部实测 · 不许猜**）

| 事实 | 证据 |
| --- | --- |
| **仓库里没有一行 tkinter** | `grep -r tkinter src/` → 零命中；`adapters/render/` 下只有 `terminal.py` |
| **M1 时代"渲染是 tkinter"只是设想** | `docs/项目架构.md` §1 的平台范围一栏；实际落地的是终端渲染器（M4-S6）+ 键盘适配器（M4-S7） |
| **既有输入适配器**只吃"键名 + 按下/抬起"，**不读键** | `adapters/input/keyboard.py` 模块头与 `handle_key()`——**读键在 `reader.py`** |
| **实时宿主的三件外围全可注入** | `host/play.py`：`renderer` / `input_adapter` / `clock` / `sleeper` 都是构造参数 |
| **快照带齐了画完整画面所需的字段** | 契约 v1 的 22 个顶层字段：`board` · `active` · `ghostDelta` · `hold` · `holdUsed` · `next` · `score` · `lines` · `level` · `combo` · `b2b` · `lockTimer` · `gravityTimer` · `phase` |
| **渲染器可以只消费"事件与快照"** | `adapters/render/__init__.py` 模块头原文 |
| **三个内建变体已在册** | `host/builtin_variants.py`：标准玩法 · 重力旋转（gravity-right / gravity-left）· 落地生长 |

**结论**：M6 **不需要碰内核**，也不需要重写输入翻译——**输入逻辑整套复用 `KeyboardInput`**，图形层只做「按键 → 归一键名」这一层翻译。

## 4. 范围边界

### 4.1 M6 范围内

- `src/tetris_kernel/adapters/render/graphics.py`（新）——**快照驱动**的 tkinter 渲染器。
- `src/tetris_kernel/adapters/input/graphics_input.py`（新）——tkinter → 归一键名的薄桥。
- `src/tetris_kernel/host/menu.py`（新）——主菜单窗口。
- `src/tetris_kernel/host/play.py`（改）——**注入图形渲染器 + 注入事件泵**（`sleeper`）。
- `src/tetris_kernel/host/assembly.py`（改）——**图形装配**（变体 + 难度 → 规则集 / 系统 / 起始等级）。
- `src/tetris_kernel/host/cli.py`（改）——**新增 `graphics` 子命令**（与 `play` 并列，终端版不动）。
- `release/graphics_launcher.py` + `release/tetris-graphics.spec`（新）——`--windowed` 的双击即玩 exe。
- `tests/test_m6_graphics.py`（新）——`G6-2`…`G6-5` 的机检。

### 4.2 M6 范围外（**明确不做**）

- **不改内核与规则**（`kernel/` + `rules/` 是冻结区 · 改一行也该红）。
- **不改契约面**（事件目录 / 命令集 / 快照字段 / 契约基线一律零变化）。
- **不改事件数基线**（图形层不新增事件）。
- **不改 `TerminalRenderer` 的既有行为**（终端版继续在，行为与画面不许变）。
- **不做** pygame / 音效 / 联网 / 触屏 / 存档 UI / 设置界面 / 多语言 / 主题皮肤。
- **不做**图形版的录像回放 UI（`replay` 仍是终端命令）。

### 4.3 M6 期间最容易犯的四条

1. **别在图形层里长出第二套规则**（形状表、计分、落点判定）——**画面的一切来自快照**，规则一切在内核。
2. **别把 tkinter 的事件循环与实时宿主的节拍打架**——**节拍归宿主**（`PlaySession.run`），tkinter 只做「被叫醒时画一帧」（§7.3）。
3. **别在输入桥里重写 DAS / ARR / 命令翻译**——**整套复用 `KeyboardInput`**（§7.2），否则两套手感必然漂。
4. **别把"能出窗口"当成"能玩"**——**门判据要判到"同一局同一指纹"**（`G6-5`），不能只判"窗口打开了"。

## 5. 交付物清单（文件级）

| 路径 | 新/改 | 归属层 | 干什么 |
| --- | --- | --- | --- |
| `src/tetris_kernel/adapters/render/graphics.py` | 新 | 适配层 | 图形渲染器（快照驱动） |
| `src/tetris_kernel/adapters/input/graphics_input.py` | 新 | 适配层 | tkinter 按键 → 归一键名 |
| `src/tetris_kernel/host/menu.py` | 新 | 宿主层 | 主菜单（玩法 + 难度） |
| `src/tetris_kernel/host/play.py` | 改 | 宿主层 | 图形渲染器与事件泵的接线 |
| `src/tetris_kernel/host/assembly.py` | 改 | 宿主层 | 图形装配（变体 + 难度） |
| `src/tetris_kernel/host/cli.py` | 改 | 宿主层 | `graphics` 子命令 |
| `src/tetris_kernel/adapters/render/__init__.py` | 改 | 适配层 | 导出图形渲染器 |
| `src/tetris_kernel/adapters/input/__init__.py` | 改 | 适配层 | 导出输入桥 |
| `release/graphics_launcher.py` | 新 | 发布 | 双击即玩入口 |
| `release/tetris-graphics.spec` | 新 | 发布 | `--windowed` 打包规格 |
| `tests/test_m6_graphics.py` | 新 | 测试 | `G6-2`…`G6-5` 的机检 |
| `docs/spec-M6.md` · `docs/plan-M6.md` | 新 | 文档 | 本规格与执行计划 |
| `docs/决策记录.md` | 改 | 文档 | ADR-100 |

## 6. 契约面规格（**M6 零变更**）

- **命令集**：不变（17 条 · 契约 v1 冻结）。
- **事件目录**：不变（**图形层不新增事件**——它只读快照）。
- **快照字段**：不变（**画面需要的字段早就齐了**，见 §3）。
- **契约基线**：**410 条零变化**。
- **冻结区**：`kernel/` + `rules/` **14 文件零变化**。
- **事件数基线**：**零变化**（图形层不进默认装配 · 不订阅事件）。

## 7. 行为面规格

### 7.1 图形渲染器：**快照驱动**，不订阅事件

- **每一帧**：宿主把**当帧快照**推给渲染器（`update_snapshot(snapshot)`），渲染器据此重画。
- **为什么不用事件镜像**：`adapters/render/__init__.py` 写明渲染器可以消费"**事件与快照**"；而快照**已经带齐了画完整画面所需的全部字段**。**事件镜像那套逻辑（`TerminalRenderer` 里的 `_apply_cells` / `_on_*`）是终端版为了画字符画准备的**——图形版要的是整幅画面，重画一整块比增量改字符更简单也更可靠。
- **代价**：每帧重画 10×20 = 200 格 + 面板 → tkinter Canvas 上是几百个 item 的更新，**量级完全够**（§8 有界）。
- **不许在渲染器里改快照**（只读）。

### 7.2 输入桥：**翻译归一键名，其余全复用**

- tkinter 的 `<KeyPress>` / `<KeyRelease>` → **归一键名**（`Left` → `left` · `space` → `space` · `Escape` → `escape` · 字母键取小写）→ 交给**既有的** `KeyboardInput.handle_key(key, kind)`。
- **DAS / ARR / 命令翻译 / `input:raw` / `input:intent` 两条事件**：**全部由 `KeyboardInput` 负责**——M6 **不写第二套**。
- **归一表要单测**：每个键一行，**键名 → 动作 → 命令类型**逐条断言（`G6-3`）。

### 7.3 实时接线：**节拍归宿主，tkinter 只负责被叫醒**

- `PlaySession` 的 `sleeper`（等待函数）**由图形侧注入**：图形版注入一个"**等一小段时间 + 泵一次 tkinter 事件循环**"的实现（`root.update()` 家族）。
- **顺序不变**（`host/play.py` 的 `_run_frame`：先读输入 → 再推进帧 → 再喂开销 → 最后渲染）。
- **输入的读法**：输入桥在 tkinter 事件回调里把按键**入队**，`poll_keys(now_ms)` 时**取队**（与终端版的"轮询间隙读键"同构）。

### 7.4 主菜单：**选玩法 + 选难度**

- **玩法三选一**：标准玩法（`standard`）· 重力旋转（`gravity-right` / `gravity-left`）· 落地生长（`growth`）。
- **难度三档**：**起始等级** 1 / 5 / 10（§7.5）。
- **菜单的输出是一个"选择"**：`(变体名, 起始等级)`——**菜单自己不装配游戏**（装配在宿主层，§7.5）。
- 菜单**不新增事件**（它是宿主层的 UI，不是系统）。

### 7.5 难度口径：**起始等级**

- 难度 = **开局时的等级**（1 / 5 / 10）——等级决定**重力间隔**（标准 1 级 = 60 帧落一格，随等级变快）。
- **实现**：开局后发一条**既有命令**把等级设到目标值（契约里 `setLevel` 是既有命令之一）——**不新增命令、不改契约**。
- **变体与难度正交**：难度对三个玩法一视同仁。

### 7.6 打包与交付

- `release/graphics_launcher.py`：双击 → **弹主菜单** → 选完 → 开局 → 局结束弹结算 → 关窗。
- `release/tetris-graphics.spec`：**`console=False`**（`--windowed`）——**不弹控制台窗口**。
- **tkinter 是标准库**（`tkinter` + `tkinter.ttk`）→ **零新依赖**，`pyproject.toml` 的运行时依赖**不变**。
- **PyInstaller 注意事项**：tkinter 需要 `TCL_LIBRARY` / `TK_LIBRARY` 隐式导入——**PyInstaller 自带 tkinter hook**，但**必须实测**（`G6-6`）。
- **与 v0.1.0 并存**：三个 exe（`tetris-play.exe` 终端双击即玩 · `tetris-cli.exe` 控制台 · `tetris-graphics.exe` 图形）——**不删旧的**。

## 8. 非功能要求（M6 可判定版）

| 编号 | 要求 | 判据 |
| --- | --- | --- |
| NFR-6-1 | **图形版不拖慢逻辑帧** | 渲染与输入**不计入逻辑帧预算**（NFR-2 明写）——**图形版每帧的推进耗时与终端版同量级**（同机配对 A/B） |
| NFR-6-2 | **窗口响应** | 单次 `update` 泵的事件处理 **< 16 ms**（60 fps 的一半预算）——超了就是事件泵在主线程干太多事 |
| NFR-6-3 | **启动可用** | 从双击到主菜单出现 **< 3 s**（含 Python 与 tkinter 冷启动） |
| NFR-6-4 | **无第三方依赖** | 图形层的运行时依赖**只有标准库**——`pyproject.toml` 的 `dependencies` **一字不改** |
| NFR-6-5 | **可关窗** | 关窗 / `q` / 局结束 → **进程正常退出**（不残留窗口、不挂起） |

## 9. 验收标准

### 9.1 M6 门判据（**硬门**）

| 编号 | 判据 | 覆盖 |
| --- | --- | --- |
| **G6-1** | **零变更**：契约基线 **410 条零变化** · 冻结区 **14 文件零变化** · 事件数基线 **零变化** · 事件登记 **50 / 0 警告** | M6 |
| **G6-2** | **画面等价**：同一局的同一份快照，**图形渲染器画出的棋盘内容与终端渲染器一致**（逐格比对 · 含活动方块占格与幽灵落点） | M6 |
| **G6-3** | **输入等价**：tkinter 按键经输入桥产生的**动作与命令序列**，与 `KeyboardInput` 同映射表产出的**逐条相同**（含 DAS / ARR 口径） | M6 |
| **G6-4** | **菜单装配正确**：菜单选出的（玩法 · 难度）**装配出的规则集标识与附带系统正确**（三个变体逐个断言 · 难度 = 起始等级） | M6 |
| **G6-5** | **可玩且确定**：图形版跑完整局到 `game-over`，**同种子同输入的棋盘指纹与终端版相同** | M6 |
| **G6-6** | **可分发**：`tetris-graphics.exe`（`--windowed`）**双击即出主菜单** → 选玩法与难度 → **开局能玩到局结束**（桌面级冒烟 · 真跑） | M6 |

### 9.2 质量门（每道都必须是红的）

pytest 主套件 · pytest 基线套件 · `ruff check` · `ruff format --check` · `pyright` · `lint-imports` · 事件登记 · 契约基线 · 冻结区基线 · 事件数基线。

### 9.3 M6 的反向验证清单（**必须真跑 · 改坏 → 必红 → 精确还原**）

1. **破坏输入桥的归一表**（把 `Left` 映射成 `right`）→ `G6-3` 的用例**必红**。
2. **破坏图形渲染器的占格计算**（活动方块少画一格）→ `G6-2` 的用例**必红**。
3. **破坏菜单装配**（难度不落到起始等级）→ `G6-4` 的用例**必红**。
4. **把难度实现改成"不发命令"** → `G6-4` / `G6-5` **必红**。
5. **让图形层订阅一个事件**（多发一条）→ 事件数基线**必红**（`G6-1`）。

## 10. 任务分解与执行顺序

| 阶段 | 干什么 | 门 |
| --- | --- | --- |
| **S0** | 规格与计划入档 · `G6-n` 登记 · ADR-100 | — |
| **S1** | 图形渲染器（快照驱动）+ `G6-2` 的机检 | G6-2 |
| **S2** | 输入桥 + 实时宿主接线（事件泵）+ `G6-3` 的机检 | G6-3 |
| **S3** | 主菜单 + 图形装配 + 难度 + `G6-4` 的机检 | G6-4 |
| **S4** | 端到端：`G6-5`（可玩且确定）+ 性能对照（NFR-6-1） | G6-5 |
| **S5** | 打包：`graphics_launcher` + `--windowed` spec + 桌面级冒烟 + `G6-6` | G6-6 |
| **S6** | 收尾与状态同步（文档回填 · PROGRESS 快照 · ai_docs） | 全门 |

## 11. 风险与未决

### 11.1 三条会让 M6 返工的风险

1. **tkinter 事件循环与节拍打架**——`PlaySession` 是"睡 → 推进"的循环，tkinter 是"事件驱动"的循环。**解法已定**：`sleeper` 注入 + 输入走队列（§7.3）。**风险点**：泵得太勤 → CPU 100%；泵得太懒 → 手感迟钝。**判据**：NFR-6-2。
2. **图形版与终端版手感漂**——DAS / ARR 两套参数一旦分叉，**同一个种子会走出不同的局**。**解法**：输入桥只翻译键名，手感参数仍是 `KeyboardInput` 的（§7.2）。
3. **`--windowed` 打包踩坑**——tkinter 需要 Tcl/Tk 资源，onefile 下偶发找不到（与 ADR-009 的 editable 坑同类）。**解法**：**S5 必须真机冒烟**（`G6-6`），不靠推断。

### 11.2 已知缺口

- **图形版没有录像回放界面**（`replay` 仍是终端命令）——**明确不在 M6 范围**。
- **难度只改起始等级**，不改计分表与手感参数（DAS / ARR 仍是默认档）——**明确不在 M6 范围**。

### 11.3 M6 的 ADR

- **ADR-100**：图形版的技术选型与形态（tkinter · 快照驱动渲染器 · 复用 `KeyboardInput` · 主菜单 · `--windowed` 打包）。

## 12. 完成定义（DoD）与提交约定

### 12.1 DoD

- `G6-1`…`G6-6` **全绿**；质量门全绿；**反向验证清单五条真跑过**。
- **`release/graphics_launcher.py` + `release/tetris-graphics.spec` 入库**（可复现重建）。
- **文档同步**：架构 §9 补图形版交付形态 · 需求 §12 登记 `G6-n` · 决策记录 ADR-100 · PROGRESS 改写快照 · ai_docs 当天条目。
- **版本号**：M6 交付后**抬到 0.2.0**（**加了功能** → 0.2.0 · 0.1.1 只留给纯口径修补）。

### 12.2 提交约定（沿用 `AGENTS.md`）

- 标题 `类型：主题`；**一次提交 = 一个逻辑改动**；**格式化与逻辑改动分开提交**。
- **纯文档与代码不混提**。
