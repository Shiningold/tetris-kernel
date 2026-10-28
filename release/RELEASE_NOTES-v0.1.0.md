# 发布说明 · v0.1.0

**发布日期**：2026-10-28　**tag**：`v0.1.0`　**渠道**：GitHub Releases（不发 PyPI · ADR-008）

## 产物

| 文件 | 说明 |
| --- | --- |
| `tetris_kernel-0.1.0.tar.gz` | 源码分发包（sdist） |
| `tetris_kernel-0.1.0-py3-none-any.whl` | Python 包（wheel） |
| `tetris-cli.exe` | 控制台版：跑 `sim` / `replay` / `inspect` / `systems` / `variants` / `play` |
| `tetris-play.exe` | 双击即玩版：双击开一个控制台窗口直接进游戏 |

**运行环境**：Windows x64；exe 免安装（含 Python 运行时），wheel 需要 Python 3.12+。

## 用法

```
# 双击即玩：双击 tetris-play.exe
# 控制台版：
tetris-cli.exe sim --frames 100 --seed 20261120
tetris-cli.exe play --seed 20261120
tetris-cli.exe inspect --frames 20
tetris-cli.exe replay <录像文件>
```

## 两个 exe 的关系

`tetris-play.exe` = `tetris-cli.exe` + 「无参数即进 `play`」+ 退出前等一次回车。
带参数时两者完全等价。

**它不是图形版**：渲染层目前只有终端渲染器（`adapters/render/terminal.py`），
仓库里没有 tkinter 代码——所以「双击即玩」是「开一个控制台窗口玩终端版」，
不是 `--windowed` 的空窗口。原因与取舍见 `docs/决策记录.md` ADR-099。

## 这个版本是什么

M1–M5 的内核底座：事件流是唯一信息载体，内核与规则集零改动即可新增玩法
（变体只表现为「多一个订阅者」）。带 AI 玩家、确定性录像回放、日志与指标落盘。
能力与边界见 `README.md` 与 `docs/需求与边界.md`。

## 已知边界

- **性能**：`G5-2` 未达标——多局合并样本下 AI 决策 P99 为 1481 μs，门槛是 400 μs。
  AI 已不再是逻辑帧的越界者，剩余开销在变体与实时宿主档自身。见 `PROGRESS.md`。
- **Windows only**：平台范围只覆盖 Windows x64（`docs/项目架构.md` §1）。
- **国内下载点**：只走 GitHub Release 直链，未做镜像。

## 校验

每个附件的 SHA-256 见 `SHA256SUMS.txt`。
