# -*- mode: python ; coding: utf-8 -*-
"""双击即玩版 exe 的 PyInstaller 规格。

**与控制台版的唯一差别**：入口脚本不同（`play_launcher.py` → 无参数直接进 `play`），
其余打包选项一致。

**为什么 `console=True` 而不是 `--windowed`**：渲染层目前只有终端渲染器，
仓库里没有 tkinter 代码；`--windowed` 会把 `sys.stdout` / `sys.stderr` 置为 `None`
（而 rich 默认写 stdout），双击后只有一个空窗口。**双击即玩 = 开一个控制台窗口
跑终端版**，详见 `docs/决策记录.md` ADR-099。
"""

import os

# **spec 里没有 `__file__`**（PyInstaller 用 exec 跑它）——用注入的 `SPECPATH`，
# 它就是 spec 文件自己所在的目录。
_HERE = SPECPATH

a = Analysis(
    [os.path.join(_HERE, "play_launcher.py")],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="tetris-play",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
