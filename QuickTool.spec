# -*- mode: python ; coding: utf-8 -*-

import os as _os

# v1.7 PDF 导入：pypdf（唯一第三方运行时依赖例外，纯 Python 零原生扩展）。
# 打包机需能 import 到 pypdf，两种方式任选：
#   1) py -3.12 -m pip install pypdf（最简单，spec 零改动）
#   2) 设环境变量 PYPDF_PATH 指向隔离 venv 的 site-packages（不污染系统 Python）
_pypdf_path = [p for p in _os.environ.get("PYPDF_PATH", "").split(_os.pathsep) if p]

# v1.7 PDF 实测：pypdf 的可选依赖（numpy/PIL 等）不参与 extract_text（三方
# 提取 SHA1 一致），但 PyInstaller 会把它们全收进来（+10MB）——必须排除。
# ⚠️ 不能排 xml（pypdf.xmp 强依赖）与 sqlite3（RAG 知识库在用）。
_EXCLUDES = ['numpy', 'PIL', 'setuptools', '_distutils_hack', 'pkg_resources',
             'pydoc', 'pydoc_data', 'pip', 'wheel', 'pytest', 'IPython',
             'matplotlib', 'pandas', 'scipy', 'jaraco', 'more_itertools',
             'typing_extensions']

a = Analysis(
    ['main.py'],
    pathex=_pypdf_path,
    binaries=[],
    datas=[('data/mini_dict.json', 'data')],
    # v1.7 RAG：main 里 rag.store/engine 是运行时局部 import（保启动轻量），
    # 静态扫描抓不到，必须显式列出——否则打包版一碰 RAG 就 ImportError。
    hiddenimports=['rag', 'rag.api', 'rag.bm25', 'rag.engine',
                   'rag.fusion', 'rag.splitter', 'rag.store',
                   'rag.vectors', 'rag.pdftext',
                   'pypdf', 'pypdf._codecs', 'pypdf._codecs.adobe_glyphs',
                   'pypdf.xmp', 'pypdf._text_extraction', 'pypdf._cmap',
                   'pypdf.filters', 'pypdf.generic', 'pypdf._reader',
                   'pypdf._utils', 'pypdf._doc_common', 'pypdf.errors',
                   'pypdf.constants'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDES,
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
