# Tetris Kernel

一个以**事件流**为唯一信息载体的俄罗斯方块内核。

目标不是"做一个俄罗斯方块"，而是做一个**能让俄罗斯方块被反复重构的底座**——任何一次玩法创新（重力翻转、落地后生长、塌方物理），都应当只表现为"新增一个订阅者"，而不需要修改内核。

## 当前状态

**项目还在 M0（骨架）阶段，尚未可玩。** 目录里目前只有设计与规范文档，代码从 M0 开始写。

| 文档 | 管什么 |
| --- | --- |
| [`docs/需求与边界.md`](docs/需求与边界.md) | 做什么 / 坚决不做什么（功能需求、契约、验收标准） |
| [`docs/项目架构.md`](docs/项目架构.md) | 技术栈、目录、数据模型、帧模型、实现红线 |
| [`docs/决策记录.md`](docs/决策记录.md) | 为什么选 A 不选 B |
| [`PROGRESS.md`](PROGRESS.md) | 当前在攻 / 待办 / 卡点 |

## 安装

PyPI 是主渠道，国内三个来源本机实测均可达：

```bash
# 直连
pip install tetris

# 清华镜像
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple tetris

# 阿里镜像
pip install -i https://mirrors.aliyun.com/pypi/simple/ tetris
```

免安装 exe（分窗口版 / 控制台版）的发布落点待定，发布时点在 M4 之后。

## 使用

```bash
tetris              # 启动 tkinter 窗口
tetris play         # 同上
tetris sim          # 批量模拟，输出统计
tetris replay       # 回放录像
tetris inspect      # 检视事件流
```

也可以用模块入口：

```bash
python -m tetris_kernel
```

## 开发

**必须用 conda 的 `py312` 环境**（Python 3.12.13）。`base` 是 3.13.9，不要用。

```bash
conda activate py312

# 装依赖（不要用 uv sync / uv venv——它们会自建 .venv 绕开 py312）
uv pip install --python D:\miniconda3\envs\py312\python.exe -e .
uv pip install --python D:\miniconda3\envs\py312\python.exe --group dev
```

常用命令：

| 做什么 | 命令 |
| --- | --- |
| 跑测试 | `pytest` |
| Lint / 格式化 | `ruff check .` · `ruff format` |
| 类型检查 | `pyright src/tetris_kernel` |
| 依赖方向 | `lint-imports` |
| 构建 | `uv build`（产出 sdist + wheel） |

写测试时 **hypothesis 必须开 `derandomize=True`**——只固定内核种子不够，hypothesis 自己的生成随机性也是变量，不关掉它 CI 上的偶发失败无法复现。

## 架构一句话

依赖方向恒为 `host > adapters > systems > bus > kernel > rules > contracts`，由 `lint-imports` 持续校验。`kernel/` 与 `rules/` 是冻结区——内核文件差异必须为空，这是项目唯一不可妥协的核心判据。
