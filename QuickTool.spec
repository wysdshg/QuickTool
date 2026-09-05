# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('data/mini_dict.json', 'data')],
    # v1.7 RAG：main 里 rag.store/engine 是运行时局部 import（保启动轻量），
    # 静态扫描抓不到，必须显式列出——否则打包版一碰 RAG 就 ImportError。
    hiddenimports=['rag', 'rag.api', 'rag.bm25', 'rag.engine',
                   'rag.fusion', 'rag.splitter', 'rag.store',
                   'rag.vectors'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='QuickTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
