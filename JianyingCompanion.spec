# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['剪映伴侣.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['jy_core'],
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
    name='JianyingCompanion',
    # ★ 带版本信息（2026-09-18 第九批）：文件属性里能看到产品名/说明，
    #   比一个"空白属性 + 未知发布者"的 exe 可信得多。
    version='version_info.txt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # ★ 关掉 UPX（2026-09-18 第九批 · 分发）：UPX 压过的 exe 更容易被
    #   Windows Defender / 国产杀软报"可疑"，别人下载完直接被拦。
    #   体积大一点无所谓，能顺利装上更重要。
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app.ico'],
)
