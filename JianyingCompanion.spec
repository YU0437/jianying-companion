# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['剪映伴侣.py'],
    pathex=[],
    binaries=[],
    # ★ 球心动图（第二十五批）必须**跟着打进包**：它没有"退化回字"以外的兜底，
    #   漏带就是"球心永远是个剪字"。onefile 下会被解到 `sys._MEIPASS`，
    #   所以取用要走 `ui_render.res_dir()`（**不是** `Path(sys.executable).parent`
    #   —— 那是安装目录，放的是 使用说明.txt / 运行日志.txt 这类**可写**文件）。
    datas=[('ball_avatar.gif', '.')],
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
