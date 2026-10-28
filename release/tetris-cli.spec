# -*- mode: python ; coding: utf-8 -*-
"""控制台版 exe 的 PyInstaller 规格。

**为什么用 spec 而不是命令行参数**：命令行每次都要重复一长串选项，容易漏；
spec 把选项固化进仓库，版本升级时改一处即可。**`upx=False`**：本机没装 upx，
开着只会每次打包都报一遍警告。

**依赖来源**：按 ADR-009，打包前先做**非 editable 安装**
（`uv pip install --python <py312> .`），所以这里 `pathex` 留空——
包从 site-packages 里找，editable 的 `.pth` 在 `--onefile` 下偶发解析失败。
"""

import os

# **spec 里没有 `__file__`**（PyInstaller 用 exec 跑它）——用注入的 `SPECPATH`，
# 它就是 spec 文件自己所在的目录。
_HERE = SPECPATH

a = Analysis(
    [os.path.join(_HERE, "cli_launcher.py")],
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
    name="tetris-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
