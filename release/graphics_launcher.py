"""图形版 exe 的入口（`tetris-graphics.exe` · `--windowed` · M6-S5）。

**双击它会做什么**：弹**主菜单**（选玩法与难度）→ 开一个 **tkinter 窗口**
打一局 → 局结束**窗口留着看结算** → 关窗。

**为什么有 `--selftest`**：**发布要能自证**——`--selftest` 会**开真窗口跑
30 帧然后退出**（**退出码 0 = 通过**），打包后**一条命令就能验**这个 exe
**在打包环境里能跑**（tkinter 的 Tcl/Tk 资源打包后还在不在，**只能真跑**）。

**注意**：这个 exe 是 **`--windowed`（不弹控制台）**——所以自检的结果看
**退出码**，**不看打印**（`sys.stdout` 在 windowed 模式下是 `None`）。
"""

from __future__ import annotations

import argparse
import sys

#: 自检要跑多少帧（**够看到画面出来 · 又不至于久**）。
SELFTEST_FRAMES = 30


def main(argv: list[str] | None = None) -> int:
    """**图形版入口**（**菜单 → 开局**）。"""
    parser = argparse.ArgumentParser(
        prog="tetris-graphics",
        description="俄罗斯方块内核 · 图形版（主菜单选玩法与难度，然后开打）",
    )
    parser.add_argument("--seed", type=int, default=0, help="内核随机源种子（默认 0）")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="自检：开真窗口跑 30 帧就退出（**退出码 0 = 通过**）",
    )
    args = parser.parse_args(argv)

    from tetris_kernel.host.graphics import GraphicsSession
    from tetris_kernel.host.menu import MenuChoice, MenuSession

    if args.selftest:
        session = GraphicsSession(
            choice=MenuChoice(variant_id=None, start_level=1),
            seed=args.seed,
        )
        try:
            session.advance(SELFTEST_FRAMES)
        finally:
            session.close()
        return 0

    choice = MenuSession().choose()
    session = GraphicsSession(choice=choice, seed=args.seed)
    try:
        session.run()
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
