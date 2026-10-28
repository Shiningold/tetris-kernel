"""控制台版 exe 的入口（`tetris-cli.exe`）。

**为什么不用 `[project.scripts]` 生成的 `tetris` 作为打包入口**：PyInstaller 打包
的是「一个脚本 + 它的依赖」，而 console script 是安装时生成的 `.exe`/包装脚本，
拿它当入口等于打包一个包装器。这里直接指向模块里的 `main`，少一层壳。

**行为**：原样转交命令行参数给 `tetris_kernel.host.cli:main`——`sim` / `replay` /
`inspect` / `systems` / `variants` / `play` 全部可用。
"""

from __future__ import annotations

import sys

from tetris_kernel.host.cli import main

if __name__ == "__main__":
    sys.exit(main())
