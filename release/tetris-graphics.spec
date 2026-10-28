# -*- mode: python ; coding: utf-8 -*-
"""图形版 exe 的 PyInstaller 规格（`tetris-graphics.exe` · `--windowed`）。

**与另两个 exe 的差别**：**`console=False`**（**不弹控制台窗口**）——图形程序
要的就是这个。**入口脚本**是 `graphics_launcher.py`（菜单 → 开局）。

**tkinter 的坑**：Tcl/Tk 资源（`tcl8.6` / `tk8.6` 那些目录）**必须被打进
包里**——PyInstaller 自带 tkinter hook 会处理，但**必须真跑自检**
（`tetris-graphics.exe --selftest` · **退出码 0 = 通过**）——**不靠推断**。

**为什么与终端版分开打包**：**两个 exe 的入口与控制台模式都不同**
（`--windowed` 的 exe 里 `sys.stdout` 是 `None` · rich 写 stdout 会炸）
——**打成两个 exe 比一个 exe 加参数省心**。
"""

import os

# **spec 里没有 `__file__`**（PyInstaller 用 `exec` 跑它）——用注入的 `SPECPATH`，
# 它就是 spec 文件自己所在的目录。
_HERE = SPECPATH

a = Analysis(
    [os.path.join(_HERE, "graphics_launcher.py")],
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
    name="tetris-graphics",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
