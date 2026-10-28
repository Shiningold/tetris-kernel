"""双击即玩版 exe 的入口（`tetris-play.exe`）。

**这个 exe 是什么**：双击它会开一个控制台窗口并直接进入 `play`（终端画面 + 键盘
输入 + 真实时钟）。**它不是 tkinter 图形版**——渲染层目前只有终端渲染器
（`adapters/render/terminal.py`），仓库里根本没有 tkinter 代码，
`--windowed` 出来的 exe 双击只会闪一下就没了（详见 `docs/决策记录.md` ADR-099）。

**带参数时**：参数原样转交给 CLI（`tetris-play.exe sim --frames 100` 等价于
`tetris-cli.exe sim --frames 100`），方便一个 exe 顶两个用。

**退出前停一下**：双击运行时窗口会随进程退出一起关掉，玩家看不到「本局结束」的
统计；这里在退出前等一次回车。带参数转交时不加停顿（那是给脚本 / 管道用的）。
"""

from __future__ import annotations

import sys

from tetris_kernel.host.cli import main


def _pause_before_close() -> None:
    """等一次回车，让双击打开的窗口别立刻消失。

    **失败就跳过**：不是交互式终端（重定向到文件 / 管道）时读输入会直接抛错，
    这里一律吞掉——停顿只是体验，不是功能。
    """
    try:
        input("按回车关闭窗口…")
    except (EOFError, KeyboardInterrupt, OSError):
        pass


if __name__ == "__main__":
    _argv = sys.argv[1:]
    if not _argv:
        # 无参数 = 双击即玩
        sys.exit(main(["play"]))
    sys.exit(main(_argv))
