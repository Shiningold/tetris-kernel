"""`python -m tetris_kernel` 入口（S14 · plan-M0.md §1.4.4.1 ④）。

只做一件事：**转发到 `host.cli:main`**——命令行逻辑全在宿主层的 CLI 骨架里，
入口文件不另写一套。
"""

from __future__ import annotations

import sys

from .host.cli import main

if __name__ == "__main__":
    sys.exit(main())
