# -*- mode: python ; coding: utf-8 -*-

# ============================================================================
# 第二十八批（2026-09-20 · 启动性能）：**裁剪打包体积**
#
# 为什么要动这里 —— 实测出来的账（不是猜的）：
#     `--selftest` 打包后 ~1750ms ｜ 源码直跑 ~385ms ｜ 裸解释器 ~230ms
#     ⇒ 打包多出来的 ~1.37s **全在 onefile 把包解到 %TEMP%**（每次启动都解一遍）。
#     而包里"解包后"合计 46.42MB，其中一大块是本程序**从来不碰**的东西。
#     `_probe_bundle.py` 按解包后字节排出来，前几名里有四个是纯死重。
#
# 裁剪的证据（每条都在源码里 grep 过，数字留在 `_probe_bundle.py` 的输出里）：
#
#   ① `PIL._avif`  7706K(解包) / 4224K(压缩) —— **从不调用**。
#      本程序只 `Image.open` 一个东西：草稿目录里写死的 `draft_cover.jpg`
#      （见 `jy_core.draft_cover_path()`）；其余图像全是自己用 ImageDraw 画的、
#      或者 `ImageGrab` 抓的屏。没有任何一条路会解 AVIF。
#
#   ② `PIL._webp`  406K —— 同上，只读 jpg。
#
#   ③ pywin32 全家（`win32\*.pyd` + `win32ui.pyd` + `pywin32_system32\*.dll`）
#      约 2.37MB —— **源码里一个 `win32` 字样都没有**
#      （`grep -n win32 jy_core.py 剪映伴侣.py ui_render.py` 零命中）。
#      所有 Win32 调用一律走 `ctypes`（见 `ui_render.py` 的 `_u32` / `_gdi`）。
#      它纯粹是因为这台机器的 env 里装着 pywin32，被 PyInstaller 顺带收进来的。
#
#   ★ **留着的（别顺手删，删了会炸、而且不一定当场报错）**：
#     · `libcrypto` + `_hashlib`：`jy_core` 用 `hashlib.md5()` 算产物指纹
#       （`file_fingerprint` 判"落盘稳定了没有"）—— 这是**功能依赖**，不是装饰；
#     · `unicodedata` / `_decimal`：看着无关，但 PIL 与标准库在文本路径上会用到；
#     · `_tcl_data\encoding\*`：tcl 的文本编码表，省那几 KB 的风险远大于收益。
# ============================================================================

# 死重模块（按**模块名**排除；`excludes` 对扩展模块 .pyd 同样生效）
DEAD = [
    # ①② 从不调用的 PIL 解码器插件
    'PIL._avif',
    'PIL._webp',
    # ③ pywin32（含 pythonwin / MFC UI）—— 本程序全程用 ctypes
    'win32', 'win32api', 'win32gui', 'win32file', 'win32event',
    'win32process', 'win32trace', 'win32ui', 'win32com', 'pythonwin',
    'pythoncom', 'pywintypes',
]

# 兜底：万一将来 PyInstaller 的 hook 又把它们塞进 binaries（有些 hook 是
# "env 里装了就往里加"），这里再滤一遍。**两条都要有**：`excludes` 管"别收"，
# 这一条管"收进来了也扔掉"。产物侧的断言见 `_verify_exe.py` 的红名单。
BANNED_IN_BUNDLE = ('_avif', '_webp', 'win32/', 'win32\\', 'pythonwin', 'pythoncom')

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
    excludes=DEAD,
    noarchive=False,
    optimize=0,
)

# 兜底过滤（见上面 BANNED_IN_BUNDLE 的说明）
a.binaries = [b for b in a.binaries
              if not any(p in b[0].replace('\\', '/') for p in BANNED_IN_BUNDLE)]

pyz = PYZ(a.pure)

# ============================================================================
# 第二十八批（2026-09-20 · 启动性能）：**onefile → onedir**
#
# 实测（同一台机器、同一时段，`--selftest` 取 6 次最好成绩）：
#     onefile（本批裁剪前）  ~1691 ms
#     onefile（裁剪体积后）  ~1517 ms
#     onedir（同样裁过）     ~ 256 ms      ← **再快 ~1.2 秒**
#
# 为什么差这么多：onefile 每次启动都要把自己**整个解到 %TEMP%\_MEIxxxx** 再跑，
# 实测"打包开销 ≈ 1.37s"里 ~1.15s 就是解包（裸解释器 230ms / 源码直跑 385ms）。
# onedir 没有这一步 —— 文件就摊在安装目录里，直接映射加载。
#
# ★★ 对用户**不可见**，这是选它的关键：
#     · 用户下载到的仍然**只有一个 Setup exe**（`_release.py` 发的也是它）；
#     · 装完是 `{app}\剪映伴侣.exe` + `{app}\_internal\` 一堆依赖 —— 用户只点快捷方式；
#     · 配置/日志仍然写在 `{app}`（`Path(sys.executable).parent` 语义没变）。
#   代价：安装时要多写 ~1000 个小文件（安装时间长几秒，一次性）；
#         绿色版"单文件拷走就能跑"这个性质没了（但本项目本来就走安装包）。
#
# ⚠️ 三处跟着改（**漏一处就是"装完打不开"**）：
#   ① `剪映伴侣安装包.iss` 的 [Files]：`_internal` 递归拷 + 主 exe 重命名两条；
#      它的 [UninstallDelete] 也要补 `{app}\_internal`（不然卸载留垃圾）；
#   ② `重启伴侣.py`：拷的是**目录**，不是单个文件；
#   ③ `_verify_exe.py`：EXE 路径变成 `dist\JianyingCompanion\JianyingCompanion.exe`。
# ============================================================================

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # ★ onedir：依赖交给下面的 COLLECT
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='JianyingCompanion',
)
