# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（--onefile / --windowed / 零第三方依赖）。

构建：  pyinstaller build.spec --noconfirm --clean
输出：  dist/QuickTrans.exe
"""
from PyInstaller.utils.hooks import collect_data_files

APP_NAME = "QuickTrans"

# 用不到的标准库 / 常见大包，剔除以减小体积。
# 注意：只剔确定没被引用的，乱剔会导致运行期 ModuleNotFoundError。
EXCLUDES = [
    "tkinter.test", "test", "unittest", "doctest", "pydoc", "pdb", "lib2to3",
    "idlelib", "turtledemo", "distutils", "setuptools", "pip", "wheel",
    "numpy", "pandas", "scipy", "matplotlib", "PIL", "IPython", "notebook",
    "PyQt5", "PySide2", "PySide6", "torch", "tensorflow", "cv2", "sklearn",
    "pytest", "sphinx", "requests", "pywin32", "win32com", "pythoncom",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[("data", "data")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # 有 UPX 时改成 True 可再压缩 30%~40%
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,             # windowed：不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version=None,
)
