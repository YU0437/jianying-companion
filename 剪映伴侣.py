#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剪映伴侣 —— 通过窗口父子嵌入（SetParent）真正长在剪映界面里的助手条

点一下 = 按教程自动完成：全选→复合片段→预合成→大退剪映→重开进草稿→
打开并选中 combination 里的预合成文件→**清空原时间线**（拖入由你手动做），
之后把该文件拖进时间线，正常导出即可（不再提示会员）。

★ 纯物理辅助：不解析/不转码/**不碰产物文件**（只备份与写回草稿元数据 .json）。

前置（只需一次）：剪映 → 设置 → 快捷键 → 给「预合成复合片段」绑定快捷键
（伴侣会在首次运行时自动写入，默认 Alt+H）

不修改剪映文件、不注入剪映进程：仅用 Win32 SetParent 把本程序窗口设为
剪映主窗口的子窗口，视觉与行为上如同剪映自带控件。
"""

import ctypes
import ctypes.wintypes as wt
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import jy_core as core
import ui_render as _ur        # 自绘渲染层（PIL 超采样 + 逐像素 alpha 上屏）

if getattr(sys, "frozen", False):
    try:
        _lp = core.config_path().parent / "运行日志.txt"
        # ★ 日志封顶（2026-09-18 第九批 · 分发）：这个文件是**追加**写的，
        #   作者机器上天天盯着看无所谓，但别人装完跑几个月能涨到几十 MB。
        #   超过 2MB 就把它挪成 .1（留一份给排查用），重新开一个干净的。
        try:
            if _lp.exists() and _lp.stat().st_size > 2 * 1024 * 1024:
                _lp.replace(_lp.with_name(_lp.name + ".1"))
        except Exception:
            pass
        _log = open(_lp, "a", encoding="utf-8", buffering=1)
        sys.stdout = _log
        sys.stderr = _log
    except Exception:
        pass

# ---------------------------------------------------------------- DPI
# ★★ 2026-09-20 第二十四批（用户："ui 还是丑，看着廉价"）：**必须声明 DPI awareness**。
#
#   旧注释写的是"不要声明"，理由是"声明后实测出现 0.8 倍缩放错位（186x37 vs 232x46）"。
#   那句话把**症状**当成了**结论**：0.8 = 96/120，是当年把 `dpi / 96` **写反**了导致的，
#   不是"声明 awareness"本身的错。而"不声明"的代价大得多，只是没人往那儿看：
#
#   本机主屏 125%（物理 1920 / 逻辑 1536）。进程不声明 awareness 时，
#   Windows 会把窗口当成"96 DPI 世界里的东西"再**整体拉伸 1.25 倍**（DPI 虚拟化）。
#   我们是 `UpdateLayeredWindow` 逐像素上屏 —— 被拉伸的就是我们自己画的那张图：
#   4 倍超采样做出来的抗锯齿、FreeType 按 13px 排好的中文笔画，全部被系统
#   重新采样糊掉一层。而且 62 逻辑 px → 77.5 物理 px **不是整数**，采样点落在半像素上，
#   糊得更厉害。
#
#   实测证据：`shots/v_dpi_pair.png`（同一颗球，左＝1x 位图被拉伸 1.25、右＝按 1.25x
#   原生渲染）。左边"剪"字的横竖笔画糊成一片灰边，右边笔画是利落的 ——
#   这正是"看着廉价"的主因之一（次要主因见 ui_render 的投影/球面对比度）。
#
#   所以：启动时（**必须在任何 Tk 窗口建立之前**）声明 PER_MONITOR_AWARE_V2，
#   然后所有度量乘 `dpi_ratio()`（见下）。这样球在两块屏上都是 1:1 原生像素：
#   125% 的屏上形状 48 → 60 物理 px（和用户现在看到的一样大），只是**不再被拉伸**。
_DPI_AWARENESS = None
#   DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = (HANDLE)-4
_DPI_PMV2 = -4


def _dpi_awareness_state():
    """当前进程的 DPI 感知级别（0=unaware / 1=system / 2=per-monitor）。"""
    try:
        v = ctypes.c_int()
        if ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(v)) == 0:
            return {0: "unaware", 1: "system", 2: "per-monitor"}.get(v.value, "?")
    except Exception:
        pass
    return "?"


def _set_pmv2():
    """声明 PER_MONITOR_AWARE_V2。返回是否成功。

    ★ 必须**显式**声明 argtypes/restype：不声明时 ctypes 会把 `(HANDLE)-4`
      当 32 位 int 传、返回值也按 c_int 收 —— 在 64 位下调用失败还看不出原因
      （第一版就是这么"静默掉到 shcore 的 v1"的）。
    """
    u = core.user32
    u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    u.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
    return bool(u.SetProcessDpiAwarenessContext(ctypes.c_void_p(_DPI_PMV2)))


def _enable_dpi_awareness():
    """声明进程 DPI 感知（三级回退），返回**实际生效**的级别名（供日志/测试）。

    ★ 为什么不看 setter 的返回值判断成败：awareness 一旦被别处（清单 / Tk 自己）
      定过，再 set 会返回 FALSE（ERROR_ACCESS_DENIED）。那时候进程其实**已经是**
      aware 的，若据此认定"unaware"，下面的缩放就不会乘 dpi，球会小一圈。
      所以一律 set 完再**回读** `_dpi_awareness_state()` 为准。
    """
    global _DPI_AWARENESS
    if _DPI_AWARENESS is not None:
        return _DPI_AWARENESS
    if _dpi_awareness_state() == "unaware":
        for fn in (
            _set_pmv2,                                          # Win10 1607+ 最佳
            lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0,   # Win8.1+
            lambda: core.user32.SetProcessDPIAware(),           # Win7+ 兜底
        ):
            try:
                if fn():
                    break
            except Exception:
                pass
    _DPI_AWARENESS = _dpi_awareness_state()
    return _DPI_AWARENESS


def dpi_ratio(hwnd=None):
    """窗口所在显示器的缩放（dpi/96）。**唯一出口** —— 度量/渲染/摆位都乘它。

    ★ 为什么按"窗口所在显示器"而不是"系统 DPI"：本机是 125%(主屏 1920 物理) +
      100%(副屏 1920 物理) 的混合配置。声明 PER_MONITOR_AWARE_V2 之后，球被拖到
      100% 那块屏上时必须把缩放收回到 1.0，否则会比设计大 25%（也就会比在
      主屏上看着还大），两边不一致。
    """
    u = core.user32
    try:
        if hwnd:
            d = u.GetDpiForWindow(wt.HWND(hwnd))
            if d:
                return d / 96.0
    except Exception:
        pass
    try:
        d = u.GetDpiForSystem()
        if d:
            return d / 96.0
    except Exception:
        pass
    try:
        hdc = u.GetDC(0)
        d = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)      # LOGPIXELSX
        u.ReleaseDC(0, hdc)
        if d:
            return d / 96.0
    except Exception:
        pass
    return 1.0


#   ★★ 这一行必须在 `tk.Tk()` **之前**跑（在窗口建出来之后再声明，只有新窗口生效、
#      已建的那个还是虚拟化的 —— 正是当年"半 aware"导致坐标打架的由来）。
#      放模块级是因为所有入口（main / 测试 / 探针）都从这里 import。
_enable_dpi_awareness()

user32 = core.user32

GWL_STYLE = -16
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_POPUP = 0x80000000
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
HWND_TOP = 0

# ================================================================ 设计系统（iOS 风）
# ★★ 2026-09-20 第二十一批（用户："悬浮窗 ui 设计感差，非常僵硬（而且悬浮球旁边还有
#   丑陋的边框，不好看）… ui 设计可以抄一下 iPhone"）。
#
#   话翻译成工程约束就是三条：
#     ① **不要边框**——旧版窗口是个 `overrideredirect` 的矩形，底色 #17181c，
#        圆球只是里面内接的一个圆 → 屏幕上看是"圆球被一个近黑色方块包着"，
#        再加 `bg_item` 上那道 `outline="#33353d"`，就是用户说的"丑陋的边框"。
#        解法：把窗口底色设成一个**绝不会出现在设计里的颜色**（色键），
#        再 `-transparentcolor` 抠掉它 —— 圆形/胶囊之外的像素**真的透明**
#        （而且顺带能被鼠标穿透，点击不再被那个方块吃掉）。
#     ② **不要硬**——旧版配色是"深灰底 + 灰字 + 硬方角"，间距也挤。
#        解法：整套换 iOS 深色系统色（secondarySystemBackground / label /
#        系统蓝绿红橙）+ 胶囊圆角 + 分层间距 + 顶部一道 1px 高光当"玻璃边"。
#     ③ **要像 iPhone**——状态色、圆角尺度、以及下面那套 banner / alert
#        都按 iOS 的比例来（banner 16 圆角、alert 14 圆角、发丝线分隔）。
#
#   色键为什么选品红：设计里**没有任何粉色**，所以不可能误抠到界面像素。
UI_KEY = "#ff00ff"          # 透明色键：窗口底色 = 它，然后整窗抠掉（见 _apply_layered）
SURFACE = "#1c1c1e"         # 卡片底（iOS secondarySystemBackground · dark）
SURFACE_HI = "#2c2c2e"      # 抬起态 / banner 底（tertiarySystemFill）
SURFACE_SOLID = "#242426"   # 悬浮球实心面（比卡片稍亮一点，压在剪映上也看得清）
HAIRLINE = "#3a3a3c"        # 发丝描边（iOS separator dark）
HAIRLINE_HI = "#48484a"     # 顶部高光用的稍亮描边
LABEL = "#ffffff"           # 主文字
LABEL2 = "#9a9aa0"          # 次文字（secondaryLabel 近似）
LABEL3 = "#6c6c70"          # 三级文字 / 未激活
BLUE = "#0a84ff"            # 系统蓝（iOS dark）
GREEN = "#30d158"           # 系统绿（ok）
RED = "#ff453a"             # 系统红（err）
ORANGE = "#ff9f0a"          # 系统橙（busy）
YELLOW = "#ffd60a"          # 系统黄（ask）
TRACK = "#3a3a3c"           # 进度轨道

# 兼容旧名（源码里到处在用，一次改完不值得）
BG = UI_KEY                 # 只用于"窗口底色"，它会被抠成透明
BG_HOVER = SURFACE_HI
FG = LABEL
FG_DIM = LABEL2
OK = GREEN
ERR = RED
BUSY = ORANGE
BAR_BG = TRACK
MENU_BG = SURFACE_SOLID     # 右键菜单**不能**用色键，否则菜单整块透明

BASE_W, BASE_H, BASE_R = 238, 48, 14      # 缩放 1.0 时的基准尺寸
W, H, R = BASE_W, BASE_H, BASE_R          # 兼容旧引用（真正生效的是 self.W/H/R）
CORNERS = {"右上": "tr", "右下": "br", "左上": "tl", "左下": "bl"}

# ★★ 文案自适应（2026-09-19 第十一批）：按钮**宽度跟着文案走**。
#   为什么必须做：基准宽 232px 只够约 14 个汉字，而流程里最关键的提示
#   「请打开草稿「X」（必须这个），我会自动继续… 87s」实测要 **377px** ——
#   旧版会被窗口**直接裁掉一半**，用户根本看不到该点哪个草稿。
#   实测清单（字号 10、基准宽 232 时可用 188px）里有 7/13 条超宽，包括
#   常驻的副标题「预合成 · 存草稿 · 清空原内容 · 你手动拖入」(203px)
#   和「导出完点我 → 还原草稿（回到预合成前）」(204px)。
#   规则：宽度取 主/副文案里较宽者 + 上限；到上限还放不下就降主文案字号，
#   最后才加省略号 —— **任何文案都不会被裁掉**。
UI_MAX_GROW = 1.9                          # 宽度上限 = 基准宽 × 1.9（够放下 426px 文案）
#   ★★ 上限是按"**文字可用宽**"倒推的，而可用宽 = 胶囊宽 − `ui_metrics()["tx"]`
#      − `pad_r`（当前布局 47 + 12 = 59px）。改布局（图标位尺寸 / 左右留白）时
#      **必须一起复核这里** —— 第二十一批就是漏了这步：`_fit_width` 还按旧布局的
#      46px 预算算宽度，实际要 59px，于是所有长文案都被吃掉一截。
UI_GROW_STEP = 16                          # 宽度量化步长（抑制"每秒重排"引起的抖动）
UI_SHRINK_HYST = 24                        # 窄了至少这么多才真的缩回来（同上）
FONT_FAMILY = "Microsoft YaHei UI"

# ★★ 悬浮球（2026-09-19 第十二批）：默认收成一个小圆球，不挡剪映的时间线。
#   用户原话：「做成悬浮球样式，不然会挡住用户操作，自动依附右下角，加点流畅样式」。
#   矛盾点：横条**必须够宽**才能把「请打开草稿「X」（必须这个）」整句写出来（实测 377px），
#   可横条这么宽就一定会压住时间线。解法 = **形状可变**：
#     · 平时 = 直径 46 的圆球（只有一个数字/一个字 + 一圈进度弧），压不住任何东西；
#     · **鼠标悬停** → 平滑长成横条，完整文案随便读，移开自动收回去；
#     · **需要你动手时**（等你导出 / 出错 / 要你打开草稿）→ 自己长出来，不用你去悬停；
#     · 干活中（busy）只留圆球 + 进度弧，盯着不打扰。
#   形状只由 (宽, 圆角) 决定：宽 == 高 且 圆角 == 高/2 就是正圆，所以"球 ↔ 横条"
#   只是把宽度和圆角平滑插值，底图是同一个多边形，不会突变。
PILL_R = 14                # 展开成横条时的圆角（半径）
BALL_MIN_W = 24            # 窗口最小宽度（兜底，别算出 0 或负数）
ANIM_FRAME_MS = 16         # 动画帧间隔（≈60fps）
ANIM_SHAPE_SECS = 0.18     # 形状（球↔横条）渐变时长
ANIM_COLOR_SECS = 0.12     # 颜色渐变时长
ANIM_PCT_SECS = 0.22       # 进度弧追赶时长
BALL_LEAVE_MS = 260        # 鼠标离开后至少等这么久才收起（挡"展开→鼠标出界→收起"自激）
LAUNCH_WAIT_SECS = 60.0    # 点「一键导出」时剪映没开 → 启动后最多等这么久主窗（第十四批）
                           #   冷启动实测 20~60s；超时不当成功，明确让用户手动开 + 再点一次。
LAUNCH_PROBE_SECS = 2.0    # 启动**之前**先给它这么久"自己冒出来"（防重复拉起）——
                           #   用户可能刚点过桌面图标，此时再 Popen 就是两个剪映抢前台。
BALL_TEXT = {"idle": "剪", "ok": "✓", "err": "×", "ask": "!", "busy": "…"}
BALL_MODES = ("auto", "always_pill", "ball_only")
BALL_MODE_LABEL = {"auto": "自动收纳", "always_pill": "一直展开", "ball_only": "只留小球"}


def status_color(kind):
    """状态 → 强调色（整套 iOS 系统色，只在这里定一次）。

    ★ 抽出来是为了让"球/环/弧/状态点/进度条/banner"永远同色 ——
      旧版这几处各写一遍色值，改一次配色要翻五个地方，必然走岔。
    """
    return {"idle": LABEL3, "busy": ORANGE, "ok": GREEN,
            "err": RED, "ask": BLUE}.get(kind, LABEL3)


def pill_glyph(kind, pct=None):
    """横条最左侧那个"圆形图标"里放的字符（iOS 通知/设置行的图标位）。

    ★★ 2026-09-20 第二十二批（补第二十一批留下的坑）：`busy` 原本返回**空串**，
      于是那个 28px 的圆形图标位里什么都没有 —— 屏幕上就是一个**空的暗黄褐色圆**，
      放大截图看非常像"坏掉的色块"（这不是设计，是漏了内容）。
      现在 busy 直接显示**百分比数字**（和球里那个数字同源），一眼读得出进度；
      真拿不到百分比时才退回 "…"。
    """
    if kind == "busy":
        try:
            if pct is not None:
                return str(int(round(float(pct))))
        except Exception:
            pass
        return "…"
    return {"idle": "剪", "ok": "✓", "err": "!", "ask": "!"}.get(kind, "剪")


def ui_metrics(s=1.0):
    """悬浮窗的**布局度量唯一出口**（第二十二批 · 根因修复）。

    ★★ 为什么必须只有一个出口：第二十一批把胶囊从"一个小圆点 + 文字从 36px 起"
      改成了"28px 圆形图标位 + 文字从图标右侧起"（tx 36→47、右侧留白 10→12，
      合计**多占 13px**），但 `_fit_width()` 里**还留着旧的那套数**。
      后果是"按文案算出来的宽度"永远比"真正要用的宽度"少 13px：文字放不下，
      就被省略号吃掉一截 —— 而吃掉的那一截往往正是关键信息。
      实测（`shots/ui_4_err.png`）：「草稿备份没做成，导出后无法自动**还原**」
      → 只剩「…导出后无法自动…」，用户根本不知道要还原什么。
      两处各写一遍 = 迟早再走岔，所以抽成唯一来源，两边都调它。
    """
    pad = max(9, int(round(10 * s)))
    icon_d = max(20, int(round(28 * s)))
    return {
        "pad": pad,
        "icon_d": icon_d,
        "tx": pad + icon_d + max(8, int(round(9 * s))),
        "pad_r": max(10, int(round(12 * s))),
        "fsz": max(7, int(round(10 * s))),
        "ssz": max(6, int(round(8 * s))),
        "ring_inset": max(4, int(round(7 * s))),
        "bar_h": max(3, int(round(4 * s))),
        # ★★ 自绘层用的字号（**像素**，不是 Tk 的点字号）—— 第二十三批。
        #   为什么单列一组：旧版用 `tkfont.Font(size=10)`，那是 10 **点**，
        #   在 96 DPI 下 = 13.33px；而新渲染层是按像素排版的。两者混用就会出现
        #   "量宽按 13.33px、绘制按 13px"——差 2% 足够让最后一行字被窗口裁掉，
        #   而这正是这个项目反复栽的那个坑（见 ui_layout 的注释）。
        "ball_px": max(12, int(round(20 * s))),
        "pct_px": max(10, int(round(16 * s))),
        "disc_px": max(9, int(round(13 * s))),
        "t_px": max(10, int(round(13 * s))),
        "s_px": max(9, int(round(11 * s))),
    }


LAY_KEYS = ("pad", "icon_d", "tx", "pad_r", "bar_h", "ring_inset")
SIZE_KEYS = {"ball": "ball_px", "pct": "pct_px", "disc": "disc_px",
             "title": "t_px", "sub": "s_px"}


def ui_layout(s=1.0):
    """`ui_render.render()` 要的两坨参数：**胶囊布局 + 字号**，全部来自 `ui_metrics`。

    ★★ 为什么必须合成一个出口：这个项目**已经**栽过两次"同一份数写两遍"——
      ① 第二十一批把胶囊布局从"小圆点、文字从 36px 起"改成"28px 图标位、文字从
         47px 起"，`_fit_width()` 里还留着旧数 → 所有长文案被省略号吃掉关键一截；
      ② 同一批里"量宽用 Tk 字体、绘制用另一套" → 算得下也画不下。
      两次的后果一样：用户读不到该读的字。所以第三批（自绘）把规矩钉死：
      **自绘层要的任何尺寸/字号只能从这里取**，别处再写字面量就是埋雷。
    """
    m = ui_metrics(s)
    return ({k: m[k] for k in LAY_KEYS},
            {k: m[mk] for k, mk in SIZE_KEYS.items()})


def ease_out_cubic(t):
    """缓出（先快后慢）—— 形变动画的手感全靠它，线性看着很"廉价"。"""
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    return 1.0 - (1.0 - t) ** 3


def lerp(a, b, t):
    return a + (b - a) * t


def hex_to_rgb(h):
    """'#rrggbb' → (r, g, b)；认不出来就给黑色（不抛，颜色出错不该把界面带崩）。"""
    try:
        s = str(h).lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return (0, 0, 0)


def rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix_hex(a, b, t):
    """在 a、b 两个颜色之间插值（t=0→a，t=1→b）。tk 没有透明度，淡入淡出只能靠它。"""
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    return rgb_to_hex((lerp(ra, rb, t), lerp(ga, gb, t), lerp(ba, bb, t)))


def corner_xy(corner, rect, w, h, margin=14, top_gap=92):
    """把 (w,h) 的窗口按角落摆进矩形 rect=(l,t,r,b)。

    ★ 右侧角落贴**右**、左侧贴左；`top_gap` 只对上角生效（剪映顶部有工具栏，
      贴死会被压住），下角贴底只用 margin。

    ★ 为什么抽成纯函数（2026-09-19 第十二批）：原来**只有嵌入模式**认 corner，
      `_float_position`（默认的独立模式）把位置写死成"右上"，于是「停靠位置」
      菜单在独立模式下**完全没用**。两边共用这一个函数，才不会又走岔。
    """
    l, t, r, b = rect
    x = r - w - margin if corner in ("tr", "br") else l + margin
    y = t + top_gap if corner in ("tl", "tr") else b - h - margin
    return int(x), int(y)


def should_expand(kind, await_restore, hovering, ball_mode="auto"):
    """悬浮球要不要展开成横条（纯函数，便于离线断言"什么时候必须让用户读到字"）。

    · `always_pill`：用户明确要求一直展开 → 回到旧样子（兼容/逃生口）；
    · 悬停：任何时候悬停都展开（球的存在意义就是"想看详情时能看"）；
    · `ball_only`：用户要"只留小球" → 除了悬停都不自动弹；
    · `auto`（默认）：**需要用户动手**的两种情形必须自己弹出来 ——
      「等你导出」（`await_restore`）和"要你打开草稿 / 出错了"（kind=ask/err）。
      这两种状态是**流程卡在等用户**，文字读不到就等于卡死，不能藏在球里。
    """
    if ball_mode == "always_pill":
        return True
    if hovering:
        return True
    if ball_mode == "ball_only":
        return False
    return bool(await_restore) or kind in ("ask", "err")


def ball_label(kind, pct):
    """圆球正中那个字符：busy 显示百分比数字（进度看外圈弧线），其余按状态给一个字。

    ★ 只给 1~3 个字符：球直径才 46px，塞不下词。**完整文案一律靠悬停展开**。
    """
    if kind == "busy":
        try:
            p = int(pct)
        except (TypeError, ValueError):
            return BALL_TEXT["busy"]
        return str(max(0, min(100, p)))
    return BALL_TEXT.get(kind, BALL_TEXT["idle"])


def morph_shape(expanded, pill_w, ball_d, pill_r):
    """目标形状 → (宽, 圆角)。收起时宽=高、圆角=高/2（正圆）；展开时用横条的宽+小圆角。"""
    if expanded:
        return float(max(ball_d, pill_w)), float(pill_r)
    return float(ball_d), float(ball_d) / 2.0


def clamp_box(x, y, w, h, box):
    """把 (x, y, w, h) 夹进 box=(l, t, r, b)，返回 (x, y)。

    ★ 为什么必须夹（2026-09-19 第十二批 · 真机实测）：剪映**最大化**时它的窗口
      矩形会**越过屏幕边界**（本机实测 `(-9,-9)-(1929,1029)`，右/下各多出 9px），
      按"窗口右下角 − 尺寸 − margin"直接算出来的坐标就落到屏幕/工作区**外面**了 ——
      球会压在任务栏上、甚至被任务栏盖住点不着。
    ★ 抽成纯函数是为了能离线断言"贴边时到底落在哪"（见 test_guard[21]）。
    """
    l, t, r, b = box
    x = max(int(l), min(int(round(x)), int(r) - int(w)))
    y = max(int(t), min(int(round(y)), int(b) - int(h)))
    return x, y


def morph_alphas(v):
    """形变进度 v（0=纯球，1=纯横条）→ (球的不透明度, 横条的不透明度)。

    ★ 必须**先出后进、两者永不同时可见**（2026-09-19 第十二批 · 目视验收）：
      球是贴在窗口**右端**的（bx = W-H），而展开是"球不动、窗口向左长"——
      中途那几帧窗口还不够宽，文字的**右半截正好落在球那一块区域**上。
      两边要是同时淡入淡出，就会看到"半个字叠在球上"的糊图。
      让球在 45% 之前走干净、文字 45% 之后才进来，读起来才是"球展开成横条"。
    """
    v = max(0.0, min(1.0, float(v)))
    ball = 1.0 - min(1.0, v / 0.45)
    pill = max(0.0, min(1.0, (v - 0.45) / 0.5))
    return ball, pill


def ring_style(kind):
    """圆球外圈那道环画成什么：返回 (是否用"轨道色", 透明度)。

    ★ 两个都必须分档（2026-09-19 第十二批 · 目视验收抓出来的）：
      · **busy**：环得当**进度弧的轨道**（暗灰）。第一版直接给状态色 → 整圈全黄，
        40% 的黄色进度弧叠在同一圈黄色上**完全看不出来**（截图里就是一只全黄圈）；
      · **idle**：环只是"这里有个按钮"的轮廓，全亮会抢过球本身，
        看着像加载圈而不是待机小球；
      · **ok / err / ask**：环就是结果色（绿/红），必须全亮 —— 一眼看到成没成。
    """
    if kind == "busy":
        return True, 1.0
    if kind == "idle":
        return False, 0.45
    return False, 1.0


def round_pts(x1, y1, x2, y2, r, seg=24):
    """圆角矩形（r 取到半宽时就是**正圆**）的顶点序列：沿四个圆角密集采样。

    ★★ 为什么必须自己采样、**不能**用 `create_polygon(..., smooth=True)`
      （2026-09-19 第十二批，截图实测踩到）：smooth 画的是"穿过相邻控制点**中点**"
      的样条 —— 控制点稀疏时它只是把四个直角**切掉一小块**。球态 r = 半个宽，
      于是"圆"画出来是个**圆角方块**（`_probe_ball.py` 的截图里就是一个方块），
      根本不是悬浮球该有的样子。改成"把每个圆角按 seg 段采成折线、点足够密、
      不平滑"：r 取半宽时轮廓就是标准正圆。

    ★★ seg 从 12 提到 **24**（2026-09-20 第二十一批 · 目视验收）：
      12 段时每个 90° 圆角只有 12 段，48px 的球上弦长 ≈ 3px ——
      放大截图里球的边缘**看得出是一圈折线**（多边形感）。
      24 段把弦长压到 1.5px 以内，肉眼就是圆了。
      代价：每帧多点 4×(24+1)=100 个坐标 —— 60fps 下完全无感。

    ★ 点数**恒定**为 4*(seg+1)：动效每帧都调它去 `coords()` 更新同一个多边形，
      点数忽多忽少虽然 Tk 也接受，但没必要给自己留隐患（r 太小就夹到 0.5）。
    """
    r = max(0.5, min(float(r), (float(x2) - float(x1)) / 2.0, (float(y2) - float(y1)) / 2.0))
    pts = []
    # 屏幕坐标 y 向下，角度增大 = 顺时针：右上 → 右下 → 左下 → 左上
    for cx, cy, a0 in ((x2 - r, y1 + r, -90.0), (x2 - r, y2 - r, 0.0),
                       (x1 + r, y2 - r, 90.0), (x1 + r, y1 + r, 180.0)):
        for i in range(seg + 1):
            a = math.radians(a0 + 90.0 * i / seg)
            pts.extend([cx + r * math.cos(a), cy + r * math.sin(a)])
    return pts


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """圆角矩形画布元素（**不平滑**：轮廓已经采样成折线，再平滑反而会缩水一圈）。"""
    return cv.create_polygon(round_pts(x1, y1, x2, y2, r), **kw)


def round_pts_corners(x1, y1, x2, y2, rtl=0.0, rtr=0.0, rbr=0.0, rbl=0.0, seg=8):
    """四角半径**各自可给**的圆角矩形顶点序列（确认卡按钮高亮用）。

    ★ 为什么 `round_pts` 不够（第二十二批）：它只支持四角同一个 r，
      而 iOS 警告框的按钮高亮是"**上面两个角是直角、下面两个角跟着卡片走圆角**"。
      用统一的 r 画不出来：r 取大了上边会缺一块，r 取小了下面两个角会戳出卡片圆角
      之外 —— 而卡片是"色键透明"的，戳出去的部分就是**浮在桌面上的一个方块**。
    ★ r=0 的角**只出一个点**（不走采样）：直角就是真正的直角，不会因为采样倒个小角。
    ★ 顶点顺序与 `round_pts` 一致（右上 → 右下 → 左下 → 左上），方便对照调试。
    """
    pts = []

    def _arc(cx, cy, r, a0, corner):
        if r <= 0:
            pts.extend(corner)
            return
        for i in range(seg + 1):
            a = math.radians(a0 + 90.0 * i / seg)
            pts.extend([cx + r * math.cos(a), cy + r * math.sin(a)])

    _arc(x2 - rtr, y1 + rtr, rtr, -90.0, [x2, y1])
    _arc(x2 - rbr, y2 - rbr, rbr, 0.0, [x2, y2])
    _arc(x1 + rbl, y2 - rbl, rbl, 90.0, [x1, y2])
    _arc(x1 + rtl, y1 + rtl, rtl, 180.0, [x1, y1])
    return pts


# ★★ 中文折行的「禁则」（2026-09-20 第二十二批）：标点不许落在行首、左括号不许落在行尾。
#   为什么必须做（实测）：卡片正文里「草稿会先备份，导出完可以一键还原。」按宽度折行时，
#   整句刚好占满一行，**句号被挤到下一行单独一行**；而正文是 `justify="center"`，
#   于是屏幕上正中间浮着一个孤零零的「。」——放大 4 倍看就是个"小圆环"，
#   第一眼像渲染坏了/有残影（我为此追了半天的"屏幕残影"，其实根因就是这个）。
#   典型中文标点禁则：```。”！？，、；：）】》」』``` 不许在行首；
#   ```（【《「『``` 不许在行尾。做法是最省事的"悬挂"：宁可让这一行多挤一两个字符。
NO_LINE_START = "。，、；：？！）】》」』〕｝”’·…—～%‰℃,.;:?!)]}>"
NO_LINE_END = "（【《「『〔｛“‘([{<"
ELL_MAX_LINE_HANG = 1      # 悬挂标点最多几个（防止一整串标点把行撑爆）


def wrap_lines(font, text, maxw):
    """按像素宽度把文案折成多行（**中日韩按字断行**，不按空格）。

    ★ 为什么要自己写（第二十一批）：通知/确认卡片要显示"我会自动做什么"这种
      中文长句，tk 的 Label 没有可靠的自动换行（`wraplength` 对中文断行位置也不可控），
      而卡片尺寸是**我们自己算**的（要按行数定高），所以断行必须能提前算出来。
    ★★ 第二十二批：加了**禁则处理**（见 `NO_LINE_START` / `NO_LINE_END`）——
      否则标点会被挤到行首单独成行，居中排版时就是一个"孤零零的句号"。
    """
    out = []
    for para in str(text or "").split("\n"):
        if not para:
            out.append("")
            continue
        cur, hang = "", 0
        for ch in para:
            if cur and font.measure(cur + ch) > maxw:
                if ch in NO_LINE_START and hang < ELL_MAX_LINE_HANG:
                    cur += ch               # 悬挂：标点跟着上一行，不另起一行
                    hang += 1
                    continue
                if cur[-1] in NO_LINE_END:
                    # 左括号类不许落在行尾 → 连同它一起挪到下一行
                    out.append(cur[:-1])
                    cur, hang = cur[-1] + ch, 0
                    continue
                out.append(cur)
                cur, hang = ch, 0
            else:
                cur += ch
        if cur:
            out.append(cur)
    return out


class Popup:
    """贴在悬浮球旁边的一张无边框小卡片（通知 banner / 确认 alert 共用这个壳）。

    ★ 为什么另开一个顶层窗，而不是把球窗"长大"：球的窗口尺寸直接参与形变动画
      （`_target_shape` / `_redraw` 全靠它插值），为了弹一张卡片去改它的高，
      整套"球 ↔ 胶囊"的补间就乱了。独立窗还有两个白拿的好处：
        · **不抢焦点** —— 弹通知时用户正在剪映里按键，抢焦点会把他的操作吃掉；
        · 卡片可以压在球**头顶**（用户要的"以小对话框形式在头顶浮现"），
          而不是把球本身撑大。
    ★★ 第二十三批：卡片底图不再用 Tk Canvas 画，改成 `ui_render.render_card()`
      渲染好的**整张位图**直接设成窗口外观（逐像素 alpha，同主窗）。
      这样卡片才有真正的投影和抗锯齿圆角 —— Canvas 画出来的卡边是一圈硬阶梯，
      在浅色背景上尤其廉价。
    ★ 代价同样是"**窗口里不能有子控件**"：所以按钮的高亮/命中一律自己算矩形
      （`ui_render.card_buttons()` 给的矩形和真正画的按钮**同源**），
      事件直接绑在顶层窗上。
    """

    def __init__(self, master, w, h, radius=16, bg=SURFACE_HI):
        self.top = tk.Toplevel(master)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg=UI_KEY)
        self.w, self.h, self.r, self.bg = int(w), int(h), int(radius), bg
        self._img = None
        self._card_xy = None      # 卡片矩形左上角（屏幕坐标）
        self._shown = 1.0
        self._hwnd = None
        # ★★ 「当前这张位图是谁画的」—— 内容规格（标题 / 正文行 / 哪个按钮高亮）。
        #   为什么要有：改成整张位图上屏之后，卡片上**没有可读的 Canvas 图元**了
        #   （`itemcget(txt,"text")` 那套断言全失效）。把"生成这张图的输入"记在
        #   卡片上，测试就能断言**语义**（正文有没有被砍、悬停高亮到底切没切），
        #   而不是去截图比对像素 —— 后者又脆又看不出对错。
        self.spec = {}

        # ★ 先摆到屏幕外：窗口建出来到第一次上屏之间会有一瞬间的"裸窗"，
        #   摆在 0,0 就是屏幕左上角闪一下。移到 -32000 之外就绝对看不见。
        P = _ur.pad_for(1.0)
        try:
            self.top.geometry(
                f"{self.w + 2 * P}x{self.h + 2 * P}+-32000+-32000")
            self.top.update_idletasks()
        except Exception:
            pass

    def hwnd(self):
        if self._hwnd is None:
            self._hwnd = _ur.toplevel_hwnd(self.top)
        return self._hwnd

    def show(self, img, x, y, alpha=1.0, spec=None):
        """一次性摆好 + 上屏。`x, y` = **卡片矩形**左上角（窗口再外扩投影留白）。

        `spec` = 这张位图对应的内容规格（见 `self.spec` 的注释）。不传则沿用上一张
        —— 入场动画只是在挪同一张图，规格没变。
        """
        self._img = img
        self._shown = max(0.0, min(1.0, float(alpha)))
        self._card_xy = (int(x), int(y))
        if spec is not None:
            self.spec = dict(spec)
        self._blit_now()

    def _blit_now(self):
        """把当前位图（按当前透明度）推到窗口上。

        ★ 位置和尺寸都由 `UpdateLayeredWindow` 一次设完 —— 不走 `geometry()`：
          两个 API 都设位置的话，移动的瞬间会先按旧尺寸摆一次，肉眼是一下"抖"。
        """
        if self._img is None or self._card_xy is None:
            return
        P = _ur.pad_for(1.0)
        x, y = self._card_xy
        try:
            self.top.update_idletasks()
            _ur.blit(self.hwnd(), _ur.with_alpha(self._img, self._shown),
                     int(x) - P, int(y) - P)
        except Exception:
            pass

    def place(self, x, y, alpha=None):
        """摆位置，可同时改透明度（入场动画一帧只需一次上屏）。"""
        if self._img is None:
            return
        if alpha is not None:
            self._shown = max(0.0, min(1.0, float(alpha)))
        self._card_xy = (int(x), int(y))
        self._blit_now()

    def alpha(self, v):
        """整卡透明度（淡入淡出用）。

        ★★ 不能用 `top.attributes("-alpha", …)`：那个 API 走的是
          `SetLayeredWindowAttributes`，和我们用的 `UpdateLayeredWindow`
          **共用同一份分层窗口数据** —— 设下去会把位图顶掉（整卡变纯色块）。
          所以透明度只能重画一遍位图；好在贵的那一遍有渲染缓存，
          这里只是在 1x 小图上缩一道 alpha（几十微秒）。
        """
        if self._img is None or self._card_xy is None:
            return
        self._shown = max(0.0, min(1.0, float(v)))
        self._blit_now()

    def lift(self):
        try:
            self.top.lift()
        except Exception:
            pass

    def destroy(self):
        """收窗：**先 withdraw 再 destroy**（第二十二批）。

        ★ 为什么这么收：这张卡是无边框置顶窗。`overrideredirect + topmost + 分层`
          这一组合在 Windows 上**有可能**被直接摧毁时触发不干净的桌面重绘，
          留下一点旧像素。`withdraw()` 会让系统走一次正常的隐藏/重绘路径，再
          destroy 就稳。
        ★ 老实交代：我们最初以为 `ui_6_alert.png` 里那个 5x4 像素的小点**就是**
          这种残影，追了一圈才发现它是**被折行挤到单独一行的句号「。」**
          （居中排版 → 屏幕正中间孤零零一个点，放大看像个小圆环），
          真正的修法在 `wrap_lines()` 的禁则处理里。
          也就是说：这一个 withdraw **不是**那个点的解药，只是防御性写法 ——
          别把两件事记混。
        """
        try:
            self.top.withdraw()
            self.top.update_idletasks()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass


# ★★ 缩放防抖（2026-09-19 第十一批）：按钮的「同大共小」不能跟着噪声翻面。
#   事故：用户日志里连着 30+ 次 `1.00 -> 0.95 -> 1.00 -> 0.95 …`，肉眼可见地一大一小抖。
#   真因：基准用的是 `SPI_GETWORKAREA`（工作区高），而它**会随任务栏显/隐整体跳一个
#   任务栏高** —— 本机实测在 864（整屏高）和 816（减任务栏）之间跳，差 48px。
#   剪映最大化时正好压着任务栏，于是这个值来回翻；`jh` 明明没变（830），
#   比例却在 830/864=0.9606→0.95 和 830/816=1.0172→1.00 两个**相邻量化台阶**间横跳。
#   两道闸（缺一不可）：
#     ① `stable_ref()`：参考高度差在死区内就**钉住不认**（48px 必须落在死区内）；
#     ② `RESCALE_HOLD`：同一个目标缩放要**稳定持续**这么久才真的改（挡任意单次抖动）。
SCALE_STEP = 0.05      # 缩放量化步进
REF_DEADBAND = 56      # 参考高度死区（px）：任务栏那次 48px 整体跳必须被吃掉
RESCALE_HOLD = 1.0     # 目标缩放要连续稳定这么多秒才真改（秒）
RESCALE_SLACK = 0.04   # 比例死区余量：偏离当前档位「一个步进 + 这么多」才允许改


def stable_ref(new_ref, locked, deadband=REF_DEADBAND):
    """把「参考高度」（工作区高）钉住，挡掉任务栏显/隐造成的整体跳。

    参考高度只是个"屏幕有多大"的粗量，差 5% 完全不影响观感 —— 但它一动就可能
    跨过量化台阶，让按钮无端改尺寸。所以：
      · 首次读数直接锁住；
      · 与已锁值只差 `deadband` 以内 → **继续用已锁值**（哪怕它一直变）；
      · 差得明显（换屏/改分辨率/改了任务栏）→ 采纳新值。
    纯函数：便于离线断言"那串 864/816 交替喂进来，锁值必须纹丝不动"。
    """
    try:
        nr = float(new_ref or 0)
    except (TypeError, ValueError):
        return locked
    if nr <= 200:                     # 读失败 / 离谱值：沿用已锁的，别把界面带崩
        return locked
    if locked is None:
        return nr
    if abs(nr - locked) > deadband:
        return nr
    return locked


def rescale_ratio(jh, ref):
    """剪映窗口高 / 参考高 —— 「按钮该有多大」的原始比例。"""
    try:
        return float(jh) / float(max(480, float(ref or 0) or 1))
    except (TypeError, ValueError):
        return 1.0


def rescale_target(jh, ref):
    """由比例算出量化后的缩放（夹 [0.85, 1.25]，步进 0.05）。"""
    if not jh or not ref:
        return 1.0
    s = round(max(0.85, min(1.25, rescale_ratio(jh, ref))) / SCALE_STEP) * SCALE_STEP
    return round(s, 2)                # 去掉 0.8500000000000001 这类浮点残渣


def scale_deviates(ratio, scale, slack=RESCALE_SLACK, step=SCALE_STEP):
    """比例偏离**当前档位**够不够？不够就当噪声，按兵不动。

    ★ 为什么死区要相对「当前档位」而不是「上一次那对读数」：
      · 实测噪声幅度：钉住参考高之后，比例在 0.9606 / 1.0172 / 1.0759 三点跳
        （剪映窗口高自己也在同步跳 48px，还会出现"工作区先变、窗口后追"的错配），
        换算成比例幅度约 ±0.058。死区取「一个步进 0.05 + 余量 0.04 = 0.09」就整段吃掉。
      · 而**拖动窗口**是单调的：每帧只挪一点点。若死区相对"上一次生效比例"算，
        每步 0.037 永远跨不出 0.08 → 按钮**卡死在拖动前的尺寸**（真踩过这个坑）。
        相对"当前档位"算就没这个问题 —— 档位会跟着改，一直拖总能拉开一个整步。
    """
    return abs(ratio - scale) > (step + slack)


def rescale_verdict(jh, ref, scale, now, cand_s, cand_t0, guard=True, hold=True):
    """缩放判定的**纯函数**版（不起窗口、不读 Win32），便于把线上日志原样重放。

    返回 (动作, 缩放)：
      ("skip",  None)  —— 当噪声忽略 / 本来就等于当前值，什么都不做
      ("wait",  s)     —— 目标 s 还在观察期：记下候选与时间戳，先不改
      ("apply", s)     —— 现在就把缩放改成 s
    `guard=hold=False` 复现旧行为（算出来就改）—— 测试里用它做反向对照。
    """
    ratio = rescale_ratio(jh, ref)
    s = rescale_target(jh, ref)
    if guard and not scale_deviates(ratio, scale):
        return "skip", None
    if abs(s - scale) < 0.049:
        return "skip", None
    if not hold:
        return "apply", s             # 旧行为：一次判定立刻改（翻面的直接原因）
    if cand_s != s:
        return "wait", s
    if now - cand_t0 < RESCALE_HOLD:
        return "wait", s
    return "apply", s


def ellipsize(font, text, avail):
    """把文本缩到 `avail` 像素内，超出部分用 … 收尾（字体度量由调用方传入）。

    宽度量化 + 字号降级之后仍可能放不下（比如用户自己把字号调大了），
    这是最后一道保险：**宁可显示省略号，也不让窗口把字裁掉**。

    ★★ 2026-09-20 第二十二批：**必须幂等** —— 源码里大量文案本身就以省略号收尾
      （「批量处理中…」「执行中…」「正在启动剪映…」「自动导出中…」…），
      旧实现直接往后缀一个 "…" → 屏幕上是「**… …**」，放大看就是两撮孤立的
      小方块，用户报的是"UI 里有乱码"（`shots/_zoom_pill_right.png` 实测）。
      修法：先把结尾已有的省略号（`…` / `。。。` / `...`）连同其前面的空白剥掉，
      再决定要不要补 —— 于是"已经带省略号"的输入原样返回。
    """
    if avail <= 0:
        return ""
    text = str(text or "")
    # 先把结尾**已有的**省略号剥掉（幂等的关键），并记下"原文本来就带省略号"
    had_ell = False
    for _ in range(16):                      # 只剥尾部，剥几次防呆即可
        t = text.rstrip()
        if t.endswith("…"):
            text, had_ell = t[:-1], True
        elif t.endswith("..."):
            text, had_ell = t[:-3], True
        elif t.endswith("。。。"):
            text, had_ell = t[:-3], True
        else:
            break
    body = text.rstrip()
    if not body:
        return "…"
    tail = "…" if had_ell else ""
    if font.measure(body + tail) <= avail:
        return body + tail                   # 原样返回（带不带省略号都行）
    if font.measure(body) <= avail:
        return body                          # 正文放得下、只差那个省略号 → 宁可不加
    ell = "…"
    lo, hi = 0, len(body)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(body[:mid] + ell) <= avail:
            lo = mid
        else:
            hi = mid - 1
    return (body[:lo] + ell) if lo > 0 else ell


# 弹窗归类规则统一放在 jy_core.classify_popup（core 侧关弹窗也要用同一套）。
# 这里只留一个"净化历史学习表"的小工具：旧版本会把**广告弹窗**、甚至剪映真正的
# 「链接媒体」对话框学成"会员弹窗"，之后每弹一次就误触发"自动开跑"（用户报的 bug）。
def sanitize_learned(learned):
    return [l for l in (learned or []) if core._has_guard_kw(l.get("title"))]


# ---------------------------------------------------------------- 对话框文案
# ★★ 2026-09-19 第十五批（用户"继续优化"）：**markdown 星号漏进非 markdown 渲染器**
#   是一个系统性的坑，不是某一句话写错了。
#   背景：文案里用 `**加粗**` 是为了在日志/说明里好读，但真正显示给用户的三个出口
#   都不认 markdown，会把星号**原样画出来**（用户看到 `点「一键导出」会**杀掉并重启剪映**`，
#   第一反应是"程序坏了"）：
#     · Win32 MessageBox（notify_box）—— 第十三批已剥；
#     · **tk 的 messagebox（本文下面这些 askyesno / showinfo）—— 漏了**；
#     · **体检报告 format_selfcheck 的输出 —— 漏了**。
#   修法不是逐句删星号（下一句新文案又会犯，本批就已经犯了两次），
#   而是给出**唯二出口**：`ask_yes` / `show_info`，并在测试里禁止再直接调 `_tkmb.*`。
def md_plain(s):
    """把要让用户看到的文案剥成纯文本（去掉 `**` 的加粗标记）。"""
    return core.strip_md_emphasis(s)


def ask_yes(title, msg, **kw):
    """确认框的唯一出口（**所有** askyesno 都必须走这里，见 test_guard [25]）。"""
    from tkinter import messagebox as _mb
    return bool(_mb.askyesno(md_plain(title), md_plain(msg), **kw))


def show_info(title, msg, **kw):
    """信息框的唯一出口（体检报告这类长文案都从这儿走）。"""
    from tkinter import messagebox as _mb
    return _mb.showinfo(md_plain(title), md_plain(msg), **kw)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


class Companion:
    def __init__(self):
        self.cfg = core.load_config()
        # 清掉旧版本误学成"会员弹窗"的广告特征（见 sanitize_learned）
        try:
            _old = self.cfg.get("learned_popups", [])
            _clean = sanitize_learned(_old)
            if _clean != _old:
                print(f"[init] 清理误学的弹窗特征 {_old} -> {_clean}", flush=True)
                self.cfg["learned_popups"] = _clean
                core.save_config(self.cfg)
        except Exception:
            pass
        self.q = queue.Queue()
        self.state = ("idle", "一键导出")
        self.jy_hwnd = None
        self.embedded = False
        self.my_hwnd = None
        self._until = 0
        # ★ 第十二批：默认**右下角**（用户明确指定「自动依附右下角」）。
        #   右下离时间线顶端、导出按钮、预览区都最远，46px 的球压不到要点的东西。
        self.corner = self.cfg.get("corner", "br")
        if self.corner not in ("tr", "br", "tl", "bl"):
            self.corner = "br"
        self._last_rect = None
        self.known_windows = None
        self._main_class = None
        self._last_rescue = 0.0
        self._jy_seen = False
        self._jy_gone_since = 0.0
        self._hidden_for_jy = False
        # 已自动关闭过的广告窗口句柄（幂等：一个 hwnd 只关一次，
        # 防止剪映反复弹同一广告导致关窗口忙循环）
        self._ad_closed = set()
        # 最近关广告的时间戳，用于"一分钟最多 6 个"的限流（防抖循环）
        self._ad_times = []
        # ★ 「等用户导出 / 等用户发还原指令」标志（2026-09-18）：
        #   流水线跑完、把文件交给用户手动拖之后置 True，界面上常驻
        #   "等你导出 · 导出完点「还原草稿」"。这期间左键点按钮 = 发还原指令
        #   （会先弹确认，避免误触把正在导出的剪映掐掉）。
        self._await_restore = False
        # ★ 「暂终止」令牌（2026-09-19 第十七批 · 用户："用户可以暂终止，终止时自动还原"）：
        #   跑流程/等剪映启动时挂一个 `threading.Event`，用户点中止就 `set()`；
        #   内核在每个检查点和每个等待循环里看它，立刻收手并**自动把草稿还原回去**。
        #   ★ None = 当前**没有可中止的任务**（比如"取最新文件"这种瞬时动作）。
        #     中止入口据此判断能不能点，绝不给出一个点了没反应的按钮。
        #   ★ 每次任务结束（`_on_done`）都清回 None：一个令牌只属于一轮任务，
        #     留着上一轮已 set 的令牌，下一轮刚启动就会"秒中止"。
        self._cancel = None
        # ★★ 第十八批（2026-09-20 导出守望）：「等你导出」期间后台守望剪映的导出
        #   对话框。`_watch_stop` 是收摊令牌（threading.Event，None = 没在守望）；
        #   见到对话框"出现→消失"就把 ("export_done",) 投进队列，由 _pump 提醒用户。
        #   ★ 只观察不动手 —— 对话框消失也可能是用户取消了导出，还原必须用户点头。
        self._watch_stop = None
        # ★ 进度条（2026-09-18 第八批）：流水线跑起来时在按钮底部画一条，
        #   让**第一次用的人**也能看出"在干活 / 干到哪了 / 还要多久"。
        #   _pct = None 表示不在跑（不画条）；0~100 才画。
        self._pct = None
        self._step = None
        # ★ 文案自适应宽度是否已经"上过弦"：第一次布局必须无条件按文案定宽，
        #   之后就靠滞回抑制抖动（倒计时/百分比每秒都在变）。
        self._booted = False
        # ★ 环境体检是否正在跑（防重入：体检要扫草稿目录，重复点是浪费）
        self._selfchecking = False
        # ★ 首次使用标志（别人拿到安装包第一眼就该看到"要先做什么"）
        self._first_run = not bool(self.cfg.get("_selfcheck_done"))

        # ---- 第十二批：悬浮球形态（球 ↔ 横条形变）的状态 ----
        # ★ 这些**必须**在 `_build_ui()` 之前建好：`_build_ui` 里就要读
        #   `_hover` / `_expanded` 来初始化 `_cur`（动画起点），读不到就是 AttributeError。
        self._hover = False          # 鼠标是否在按钮上（悬停 = 展开）
        self._expanded = False       # 当前形态：False=圆球 True=横条
        self._leave_at = 0.0         # 鼠标离开的时间戳（延迟收起用，见 BALL_LEAVE_MS）
        self._pill_w = int(BASE_W)   # **横条**该多宽（宽度只跟文案走，与窗口实际宽度解耦）
        self._ball_mode = self.cfg.get("ball_mode", "auto")   # auto / always_pill / ball_only
        if self._ball_mode not in BALL_MODES:
            self._ball_mode = "auto"
        self._anim_after = None      # 动画 tick 的 after id（None = 没在跑）
        self._anims = {}             # 分通道补间：{"shape"/"color"/"pct": {...}}
        self._cur = {"w": float(BASE_W), "r": float(BASE_R),
                     "bgv": 0.0, "fgv": 0.0, "pct": 0.0}
        self._ui_ready = False       # 画布建好了没（没建好不许播动画）
        self._redraw_err = False     # 重画异常只报一次，别刷屏
        self._pushed_size = None     # 已经推给窗口的 (W,H)，避免每帧都发 SetWindowPos
        self._last_float = None      # 独立模式上次摆的位置
        self._dot_color = status_color("idle")   # 当前状态色（球/胶囊/进度弧都用它）
        self._float_err_shown = False
        # ---- 第二十一批：卡片 + 自由拖动 ----
        self._card = None            # 当前浮在头顶的那张卡（通知/确认共用，只留一张）
        self._press = None           # 左键按下的 (屏幕x, 屏幕y, 窗口x, 窗口y)
        self._dragging = False       # 本次左键是一次"拖动"还是一次"点击"

        self.root = tk.Tk()
        self.root.title("剪映伴侣")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=UI_KEY)
        # ★★ 第二十三批（用户："ui 还是丑，看着廉价"）：**弃用色键，改逐像素 alpha**。
        #   上一批（第二十一批）用 `-transparentcolor` 把"包着球的近黑方块"抠掉了，
        #   方向对，但色键这条路**天生做不到**下面这几样 —— 而它们恰恰就是
        #   "廉价感"的本体：
        #     · 形状边缘的抗锯齿：色键是二值的（要么全透明要么全不透明），
        #       圆形边缘只能是一圈硬阶梯，放大截图能数出台阶（`_probe_edge.py` 实测）；
        #     · 真正的投影：投影靠半透明像素，色键会把它和桌面混成一片脏粉；
        #     · 半透明的悬停反馈 / 淡入淡出：同上。
        #   现在改成 `UpdateLayeredWindow`（逐像素 alpha，见 `ui_render.blit`）：
        #   整个控件在 4 倍画布上渲染 → 降采样 → 带高斯投影 → 整块上屏。
        #   ★ 代价（必须记住，不然下次一定踩）：**窗口里不能有任何 Tk 子控件**——
        #     Tk 的 Canvas 是**子窗口**，它会在我们的位图**上面**重画
        #     （`_probe_layered.py` / `_probe_layered2.py` 实测被盖）。
        #     所以 Canvas 没了，鼠标事件直接绑在顶层窗自己身上。
        #   ★ 鼠标穿透：Windows 对分层窗口的规则是"**alpha 为 0 的地方放行**"
        #     （实测 + MSDN），所以圆形/胶囊外面**照样点得穿**，和色键时代一样；
        #     代价是投影那圈（alpha>0）会拦住点击 —— 所以留白收小到 7px。
        self._layered = True         # 保留这个名字：外部逻辑仍用它判断"无边框透明"

        # —— 外观缩放 / 跟随（同生共死 · 同大共小）——
        # ★★ 第二十四批：缩放拆成**两个因子**，`_scale` 是它们的积（= 生效缩放）。
        #   · `_dpi`    = 显示器 dpi/96（125% 的主屏 = 1.25）—— 让位图按物理像素
        #                 1:1 上屏（见文件头 DPI 段的推导）；
        #   · `_scale_follow` = "跟随剪映窗口大小"那一档（0.85~1.25，见 `_maybe_rescale`）。
        #   ★ 为什么拆开而不是各乘各的：`_maybe_rescale` 是拿"剪映窗口高 / 工作区高"
        #     反推缩放的 —— 若判定里用含 dpi 的 `_scale`，它会以为"球怎么突然大了
        #     25%"，然后**把自己反向调小**；球被拖到另一块屏（dpi 变了）时更会来回打架。
        #     所以：判定/量化永远只在 `_scale_follow` 域里做，`_scale` 只作度量与渲染口径。
        #   ★ 名字**不能**叫 `_follow` —— 那会和 33ms 跟随循环 `self._follow()` 撞名，
        #     把方法覆盖成 float，`after(400, self._follow)` 直接 TypeError（踩过）。
        self._dpi = dpi_ratio()
        self._dpi_mon = 0            # 上次检查时窗口在哪块显示器（0 = 未知）
        self._dpi_checked_at = time.time()
        self._scale_follow = 1.0
        self._scale = self._scale_follow * self._dpi
        # ★ 缩放防抖状态（第十一批）：钉住的参考高度 + 待确认的目标缩放
        self._ref_locked = None      # 已锁定的"工作区高"（见 stable_ref）
        self._cand_s = None          # 候选缩放（连续稳定 RESCALE_HOLD 秒才生效）
        self._cand_t0 = 0.0
        self._txt = "一键导出"
        self._sub = self._idle_sub()
        self._jy_pid = 0            # 剪映主窗口所属 pid（判"剪映是否在前台"用）
        self.W, self.H, self.R = BASE_W, BASE_H, BASE_R
        # ★ 投影留白：形状外面还要留这么多像素给投影。窗口矩形 = 形状 + 2*pad。
        #   **摆位逻辑仍按"形状矩形"算**，上屏时整体外扩 pad（见 _win_xy）。
        self._pad = _ur.pad_for(self._scale)
        self.root.geometry(
            f"{self.W + 2 * self._pad}x{self.H + 2 * self._pad}+120+120")

        self._build_ui()

        # ★ 第二十一批：左键改成"按下→拖动→松开"三段，**点击动作挪到松手时**才发。
        #   理由：用户要"可以自由拖动"，而拖动和点击共用左键 —— 必须等松手才知道
        #   这次到底是"点了一下"还是"拖着摆位"。阈值 5px（见 _on_drag）。
        # ★ 第二十三批：事件从 Canvas 挪到顶层窗（没有 Canvas 了，见上面的注释）。
        self.root.bind("<ButtonPress-1>", self._on_press)
        self.root.bind("<B1-Motion>", self._on_drag)
        self.root.bind("<ButtonRelease-1>", self._on_release)
        # ★ 第十二批：进出都走**一个方法**，因为悬停不只是改个底色 ——
        #   它还要把圆球长成胶囊（`_on_enter` → `_sync_expanded`）。
        #   离开**不当场收**（展开时窗口变宽，tk 会补一个假的 <Leave>，
        #   当场收起就成了"展开↔收起"自激抖动），只记时间戳交给 _pump 延迟判定。
        #   ★ 第二十一批：tk 事件之外**另加一层光标轮询**（_poll_hover），
        #     因为实测"鼠标碰到球没反应"——窗口被高频 geometry() 挪动时事件会丢。
        self.root.bind("<Enter>", self._on_enter)
        self.root.bind("<Leave>", self._on_leave)

        # ★ 菜单底色**不能**用色键（会整块透明看不见），所以单独给 MENU_BG。
        self.menu = tk.Menu(self.root, tearoff=0, bg=MENU_BG, fg=FG,
                            activebackground=BLUE, activeforeground="#ffffff",
                            bd=0, relief="flat")
        # ★ 记下每个菜单项的下标。**不能**用 menu.index("中文标签") 反查：
        #   label 一旦被改成 "xxx ✓"，Tcl 的 menu index 既不做前缀匹配、也不等于原名
        #   → 直接抛 TclError。原代码就是这么写的，于是所有开关（导出拦截/开机自启/
        #   自动粘回…）从第二次调用起都在静默报错、✓ 永远不再更新。
        self._mi = {}

        def _add(key, label, cmd):
            self.menu.add_command(label=label, command=cmd)
            self._mi[key] = self.menu.index("end")

        _add("extract", "取最新预合成文件（推荐）", self._extract_only)
        # ★ 2026-09-18：这个入口以前**只写了函数、没接进菜单**——流水线提示
        #   "右键本按钮 → 「还原草稿」"，用户右键却找不到它。现在补上。
        _add("restore", "还原草稿（回到预合成前）", self._restore_draft)
        # ★ 2026-09-19 第十七批（用户："用户可以暂终止，终止时自动还原"）：
        #   跑起来之后**原来根本没有"停下"这个动作** —— `_click` 里
        #   `if self.state[0] == "busy": return`，点了没反应，只能眼睁睁等它跑完
        #   （最长的等待是"等渲染"120s + 重启剪映那一段）。这条入口补上"停"，
        #   而且停下来**自动把草稿还原回去**，不留半残的草稿。
        #   ★ 只在"真的能中止"时可点（`_sync_menu` 负责置灰），不做点了没反应的死入口。
        _add("abort", "中止本次流程（自动还原）", self._abort_pipeline)
        self.menu.add_separator()
        # ★★ 第十九批（v1.2.0 批量队列）：多份草稿逐份手工跑太磨人。这里是入口，
        #   子菜单在**点开时**才现扫现填（草稿列表会变，不能建菜单时一次定死）。
        _mbatch = tk.Menu(self.menu, tearoff=0,
                          postcommand=self._fill_batch_menu)
        self._batch_menu = _mbatch
        self.menu.add_cascade(label="批量处理草稿…", menu=_mbatch)
        self._mi["batchmenu"] = self.menu.index("end")
        _add("launch", "启动剪映", self._launch_jy)
        # ★ 2026-09-19 第十四批（用户"继续优化" · 分发）：分发病因第 1 条是
        #   "路径假设"——绿色版 / 自定义安装 / 装在别的盘的人，"问进程 + 常见落点"
        #   全都命中不了，只会得到一句"找不到剪映专业版"，然后**没有然后了**。
        #   这条入口让用户自己指一下 exe，是那条路上唯一的兜底。
        _add("jypath", "设置剪映路径…", self._pick_jy_exe)
        # ★ 2026-09-19 第十五批（用户"继续优化"）：**同一个坑的另一半**。
        #   上轮给"找不到剪映"补了手动指路，但"找不到草稿目录"的失败文案还在
        #   让用户「把路径填进 伴侣配置.json」—— 让人手改 JSON，等于没有出路
        #   （而且和本项目"求助回路闭环"的规矩自相矛盾）。
        _add("draftroot", "设置草稿目录…", self._pick_draft_root)
        self.menu.add_separator()
        _add("embed", "嵌入剪映界面", self._toggle_embed)
        _add("corner", "停靠位置：右下", self._cycle_corner)
        # ★★ 第二十一批：球从此**可以拖着走**（用户："用户可以自由拖动"）。
        #   拖过之后就不再被角落吸附拉回去；想回到吸附状态走这一条。
        _add("resetpos", "重置位置（回到角落）", self._reset_pos)
        # ★ 2026-09-19 第十二批（悬浮球）：形态可配。默认「自动收纳」（平时是小球、
        #   悬停/需要你动手时自己长出来）。有人就是喜欢一直看得见全文案，
        #   也有人嫌横条挡事只要球 —— 一条菜单循环三种模式，别逼用户接受一种。
        _add("ballmode", "悬浮球：自动收纳", self._cycle_ball_mode)
        _add("bindhelp", "绑定说明（预合成快捷键）", self._show_bind_help)
        # ★ 2026-09-18 第九批（发给别人用）：别人拿到安装包最常见的问题是
        #   "我这儿点下去没反应" —— 其实是剪映没装 / 没打开 / 桌面写不进去。
        #   给一条"先体检一遍"的路，把能查的都摊开说清楚。
        _add("selfcheck", "环境体检（第一次用先看）", self._selfcheck)
        # ★ 2026-09-19 第十一批（体验）：**关闭求助路的最后一环**。
        #   失败文案一直在说"把运行日志发给开发者""见安装目录下的使用说明.txt"
        #   "可以手动把备份里的 .json 拷回草稿目录" —— 但界面上没有任何地方能
        #   打开它们，用户被指向一个找不到的文件。这三条把路补齐。
        _add("openmanual", "打开使用说明", self._open_manual)
        _add("openlog", "打开运行日志（发给作者）", self._open_log)
        _add("openbackup", "打开草稿备份文件夹", self._open_backup)
        self.menu.add_separator()
        # ★ 2026-09-18 第八批（发给别人用）：伴侣首次运行会往**对方的剪映配置**里
        #   写一个预合成快捷键。改别人机器上的设置，就必须给一条还原的路。
        _add("restorekeys", "还原剪映快捷键设置", self._restore_shortcut_keys)
        _add("autodraft", "自动打开原草稿", self._toggle_auto_draft)
        _add("autodrag", "自动拖进时间线", self._toggle_auto_drag)
        _add("exportwatch", "导出守望（导完提醒还原）", self._toggle_export_watch)
        _add("fullauto", "全自动导出（实验）", self._toggle_fullauto)
        _add("foldertidy", "文件夹窗口规整", self._toggle_folder_tidy)
        _add("guard", "导出拦截", self._toggle_guard)
        _add("autorun", "弹窗时自动接管", self._toggle_auto_run)
        _add("followfg", "跟随剪映显隐", self._toggle_follow_fg)
        _add("startup", "开机自启", self._toggle_autorun)
        self.menu.add_separator()
        _add("quit", "退出", self._quit)

        self.root.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

        self.root.update_idletasks()
        h = self.root.winfo_id()
        while True:
            p = user32.GetParent(h)
            if not p:
                break
            h = p
        self.my_hwnd = h
        print(f"[init] winfo_id={self.root.winfo_id()} top_hwnd={self.my_hwnd}"
              f" dpi-awareness={_DPI_AWARENESS} dpi={self._dpi:.2f}"
              f" scale={self._scale:.3f}", flush=True)
        # ★★ 补一次上屏（第二十三批 · 真踩到）：`_build_ui()` 排在解析 HWND
        #    **之前**，那一次的 `_redraw → _blit` 因为 `my_hwnd is None` 只能跳过。
        #    Canvas 版无所谓（画完就在那儿了），但"整窗位图"版不补这一枪，
        #    启动后**球是空的** —— 要等用户碰一下或状态变一次才现身。
        self._redraw()

        if self.cfg.get("launch_on_start", True) and not core.find_jianying(exclude=self.my_hwnd):
            self._launch_jy(silent=True)

        self.root.after(400, self._follow)
        # ★ 快速跟拍线程：只做"把按钮挪到剪映窗口旁"这一件小事，间隔 ~28ms。
        #   原来只有 _follow 的 500ms 轮询，拖动剪映时按钮会一跳一跳的（用户反馈"卡卡的"）。
        self.root.after(60, self._follow_fast)
        self.root.after(600, self._watch)
        self.root.after(100, self._pump)
        self._sync_menu()

        # ★ 首次运行：自动体检一次并弹"先读我"（2026-09-18 第九批 · 分发）。
        #   延后 1.6s 等剪映窗口探测/按钮摆位先做完，别一开机就怼脸弹窗。
        #   只在第一次跑（`_selfcheck_done` 记在配置里），之后要看得走菜单。
        if self._first_run:
            # ★ 记下 after id：测试/脚本里能把它取消掉，否则首次体检的
            #   **模态**弹窗会把自动化流程挂死（冒烟测试真踩到过这类坑）。
            self._selfcheck_after = self.root.after(1600, self._first_run_notice)

    # ------------------------------------------------------------ 外观（同大共小）
    # ------------------------------------------------------------ 外观 / 动效
    def _ball_d(self):
        """圆球直径（= 横条高度）。球与横条**同高**，所以形变只在宽度上做文章。"""
        return float(max(32, int(round(BASE_H * self._scale))))

    def _ball_at_right(self):
        """球贴窗口哪一端：右侧角落贴右（横条向左长出去），左侧角落贴左。"""
        return self.corner in ("tr", "br")

    def _target_shape(self):
        """按「展开 / 收起」算出目标形状 → (宽, 圆角)。"""
        d = self._ball_d()
        pw = max(float(BASE_W) * self._scale, float(self._pill_w))
        return morph_shape(self._expanded, pw, d, max(6.0, PILL_R * self._scale))

    def _ball_text(self):
        return ball_label(self.state[0], self._pct)

    def _build_ui(self):
        """按 self._scale 重算整套度量，再重画一帧。

        ★★ 第二十三批：这里**不再建任何画布元素**了 —— 整个控件由
          `ui_render.render()` 每帧现画（带缓存），上屏走逐像素 alpha。
          留下的只有"尺寸 / 字号 / 布局"这一组度量和动画状态 `_cur`
          （`_redraw` 每帧读它们生成本帧画面，见 `_view_state`）。
        """
        s = self._scale
        self._ui_ready = False           # 重建期间不许播动画（尺寸是一次到位的）
        self.H = max(34, int(round(BASE_H * s)))
        self.R = max(6, int(round(BASE_R * s)))
        tw, tr = self._target_shape()
        self.W = max(BALL_MIN_W, int(round(tw)))

        # —— 尺寸/字号/布局：一律来自 `ui_metrics()` 这一个出口 ——
        #   ★ 项目历史上两次事故都是"同一份数写两遍"，见 ui_layout 的注释。
        _m = ui_metrics(s)
        self._pad = _ur.pad_for(s)       # 投影留白（窗口比形状大 2*_pad）
        self._pad_l = _m["pad"]          # 胶囊内左右留白（★ 别和 _pad 混）
        self._icon_d = _m["icon_d"]
        self._tx = _m["tx"]
        self._pad_r = _m["pad_r"]
        self._fsz = _m["fsz"]
        self._ssz = _m["ssz"]
        self._bar_h = _m["bar_h"]
        self._bar_pad = self._pad_l
        # ★★ 文字度量与绘制**同一套字体、同一个字号**（都是 1x 像素）。
        #   旧版这里建的是 `tkfont.Font(size=10)`（10 **点** ≈ 13.33px），而渲染层按
        #   像素排版 —— "量宽 13.33px、画 13px"差 2%，足够让最后一行字被窗口裁掉。
        #   `Metrics1x` 在 4 倍画布上量、再除回 1x，和真正绘制逐像素同源。
        self._lay, self._sz = ui_layout(s)
        self._t_px = self._sz["title"]
        self._s_px = self._sz["sub"]
        self._f = _ur.Metrics1x("reg", self._t_px)
        self._fs = _ur.Metrics1x("reg", self._s_px)
        self._card_m = _ur.card_metrics(s)     # 卡片布局口径（算高度、排版共用）

        # 动画状态：形状(宽/圆角)、悬停亮度、展开度(0=球 1=横条)、显示的百分比
        self._cur = {"w": float(self.W), "r": float(self.R),
                     "bgv": 1.0 if self._hover else 0.0,
                     "fgv": 1.0 if self._expanded else 0.0,
                     "pct": float(self._pct or 0)}
        self._anims = {}
        # ★ 尺寸/圆角变了必须让**定位**重算，否则窗口还停在旧尺寸上（宽底图被裁）。
        #   这几个清空要放在 `_paint` **之前** —— `_paint` 里就会走一次
        #   `_redraw → _apply_window_size`，那一次才是真正把新尺寸推给窗口的。
        self._last_float = None
        self._last_rect = None
        self._pushed_size = None
        self._paint(self.state[0], dot=status_color(self.state[0]))
        self._ui_ready = True

    # ------------------------------------------------------------ 这一帧该长什么样
    def _view_state(self):
        """把"这一帧该画成什么样"算成一个 dict —— **纯函数**（不碰窗口、不渲染）。

        ★★ 为什么必须抽出来（第二十三批）：渲染从 Canvas 挪到 `ui_render` 之后，
          "状态"以前是散在 `_redraw` 的一堆 `cv.itemconfigure` 里的 ——
          测试只能靠"去 Canvas 里捞某个图元的属性"断言（`itemcget(txt_item,"text")`）。
          Canvas 没了，如果状态只活在绘制调用里，测试就只剩"截图比对"一条路
          （脆、且看不出语义）。抽成纯函数之后，"球里该显示什么字 / 进度条该不该在 /
          现在是不是横条 / 文案有没有被省略号吃掉"都能直接断言，和渲染引擎无关。
        """
        cur = self._cur
        s = self._scale
        kind = self.state[0]
        v = max(0.0, min(1.0, float(cur["fgv"])))
        W = max(BALL_MIN_W, int(round(cur["w"])))
        # ★ 圆角必须夹到"不超过半宽/半高"（第十二批踩到）：球态的圆角 = 直径/2，
        #   若比实际的半宽还大，圆角矩形会退化成"两头鼓包"。夹一刀才是正圆。
        R = max(4, min(int(round(cur["r"])), max(2, (W - 2) // 2),
                       max(2, (self.H - 2) // 2)))
        ball_a, pill_a = morph_alphas(v)
        #   ★ 外圈**只**当 busy 时进度弧的暗色轨道，其余状态一律不画
        #     （球自己已经有发丝边，再叠一圈灰环就是"双边框"）。
        ring = (kind == "busy" and self._pct is not None and ball_a > 0.004)
        avail = max(0, W - self._tx - self._pad_r)
        pill = None
        if pill_a > 0.004:
            # ★ 每帧按**当前**宽度重算省略号：动画中间帧窗口还窄，
            #   若直接塞最终文案会被窗口边界切掉（看着就是"字被裁了"）。
            pill = {"glyph": pill_glyph(kind, self._pct),
                    "title": ellipsize(self._f, self._txt, avail),
                    "sub": ellipsize(self._fs, self._sub, avail),
                    "bar": (None if self._pct is None
                            else max(0.0, min(1.0, float(self._pct) / 100.0)))}
        return {
            "kind": kind, "scale": s,
            "shape": (W, self.H, R), "morph": v,
            "ball_a": ball_a, "pill_a": pill_a,
            "hover": max(0.0, min(1.0, float(cur["bgv"]))),
            # 球贴窗口哪一端：展开时窗口向左（或右）长出去，球那一端不许跑掉
            "ball_x": (W - self.H) if self._ball_at_right() else 0,
            "ball_text": (self._ball_text() if ball_a > 0.004 else None),
            "ball_pct": (float(cur["pct"]) if ring else None),
            "ring": ring,
            "pill": pill,
            "avail": avail,
        }

    # ------------------------------------------------------------ 渲染 / 动效
    def _redraw(self):
        """唯一的渲染出口：把这一帧渲染出来 + 上屏，并把尺寸同步给窗口。

        ★ 为什么必须"只有一个出口"（第十二批的教训）：形变动画每一帧都要**同时**
          改底图圆角、进度弧、两行文字、底部进度条、以及**窗口自身的大小**。
          散在几处各改一部分，中间帧必然出现"画布宽了、窗口没跟着宽 → 右边被裁"。
        ★★ 第二十三批：从"逐项 itemconfigure"改成"一次性渲染整帧"，反而更简单 ——
          因为渲染层是个纯函数（输入全在 `_view_state()` 里），没有"改了一半"的中间态。
        """
        try:
            st = self._view_state()
            W, H, R = st["shape"]
            self.W, self.R = W, R
            pill = st["pill"]
            img = _ur.render(
                W, H, R, scale=st["scale"], kind=st["kind"],
                glyph=(pill["glyph"] if pill else (st["ball_text"] or "")),
                title=(pill["title"] if pill else ""),
                sub=(pill["sub"] if pill else ""),
                pct=st["ball_pct"],
                bar=(pill["bar"] if pill else None),
                ball_a=st["ball_a"], pill_a=st["pill_a"],
                hover=st["hover"], ball_x=st["ball_x"],
                layout=self._lay,
                sizes={"title": self._t_px, "sub": self._s_px})
            self._apply_window_size()
            self._blit(img)
        except Exception as e:
            if not getattr(self, "_redraw_err", False):
                self._redraw_err = True
                print(f"[ui] 重画异常 {e}", flush=True)

    def _blit(self, img):
        """把这帧位图推到窗口上（逐像素 alpha）。

        ★ 位置**不由这里管**：`UpdateLayeredWindow` 的 `pptDst` 传 None 就是"位置不动"，
          位置仍然归 `_float_position` / `_reposition`（它们按**形状**矩形算，
          再整体外扩 `_pad`）。两个地方都设位置的话，动画中途就会互相打架。
        ★★ `my_hwnd` 还没解析出来时**直接返回、不报错**（第二十三批踩到）：
          `__init__` 里 `_build_ui()` 排在"解析顶层 HWND"**之前**，那一刻
          `my_hwnd is None` → `UpdateLayeredWindow` 返回 0（err=1400 无效句柄）。
          旧版不会暴露这件事（Canvas 画完就在那儿），换成"整窗位图"之后
          **第一次上屏失败 = 启动后球是空的**（要等第一次悬停/状态变化才现身）。
          所以：这里静默跳过，并在 `__init__` 拿到 HWND 之后立刻补一次 `_redraw()`。
        ★ 真正的失败（拿不到 DC 之类的环境问题）只报一次：60fps 的循环不该刷满屏。
        """
        if not self.my_hwnd:
            return
        if not _ur.blit(self.my_hwnd, img, None, None):
            if not getattr(self, "_blit_err", False):
                self._blit_err = True
                print("[ui] 分层上屏失败（UpdateLayeredWindow 返回 0）", flush=True)

    # ------------------------------------------------------------ 窗口几何
    #   ★★ 第二十三批：窗口矩形 = **形状矩形外扩 `_pad`**（投影留白）。
    #      规矩：所有"摆位/夹取/拖动"的逻辑一律按**形状**矩形算（那是用户眼里
    #      看到的东西的位置），只在**推给窗口的那一刻**外扩 `_pad`。
    #      反过来做（让窗口矩形当形状矩形）就会连带把 corner_margin、hover 判定、
    #      拖拽起点全改一遍，一处漏掉就是"球的位置慢慢漂移"。
    def _win_wh(self):
        """窗口尺寸（含投影留白）。"""
        return self.W + 2 * self._pad, self.H + 2 * self._pad

    def _push_geometry(self, x, y):
        """把窗口摆到屏幕 (x, y) —— 注意传的是**形状左上角**。"""
        P = self._pad
        self.root.geometry(
            f"{self.W + 2 * P}x{self.H + 2 * P}+{int(x) - P}+{int(y) - P}")

    def _apply_window_size(self):
        """尺寸**真的变了**才推给窗口（位置另有 33ms 的跟随循环在管）。

        嵌入模式走 SetWindowPos —— `_reposition` 按角落算 x，右角贴右，
        所以"变宽"是**向左长出去**，球那一端不会跑掉。
        """
        if (self.W, self.H) == getattr(self, "_pushed_size", None):
            return
        self._pushed_size = (self.W, self.H)
        self._last_float = None
        self._last_rect = None
        if self.embedded and self.jy_hwnd:
            self._reposition(force=True)
        else:
            self._float_position(force=True, rescale=False)

    def _anim_begin(self, chan, targets, dur):
        """给某个**通道**开一段补间（不同通道各跑各的，互不打断）。

        ★ 分通道的原因：形状（球↔横条）、悬停亮度、进度弧是三条独立的时间线，
          共用一条的话"进度每 1 秒更新一次"会把"形变动画"反复掐断 → 永远走不完。
        """
        frm = {k: float(self._cur.get(k, 0.0)) for k in targets}
        to = {k: float(v) for k, v in targets.items()}
        if all(abs(frm[k] - to[k]) < 1e-6 for k in to):
            self._cur.update(to)              # 已经在目标上：不用动画
            self._anims.pop(chan, None)
            self._redraw()
            return
        self._anims[chan] = {"frm": frm, "to": to, "t0": time.time(),
                             "dur": max(0.01, float(dur))}
        self._anim_kick()

    def _anim_kick(self):
        if self._anim_after is None:
            try:
                self._anim_after = self.root.after(ANIM_FRAME_MS, self._anim_tick)
            except Exception:
                self._anim_after = None

    def _anim_stop(self):
        if self._anim_after is not None:
            try:
                self.root.after_cancel(self._anim_after)
            except Exception:
                pass
        self._anim_after = None
        self._anims = {}

    def _anim_tick(self):
        self._anim_after = None
        try:
            now = time.time()
            alive = {}
            for chan, a in self._anims.items():
                tf = ease_out_cubic((now - a["t0"]) / a["dur"])
                done = tf >= 1.0
                for k, tv in a["to"].items():
                    self._cur[k] = tv if done else lerp(a["frm"][k], tv, tf)
                if not done:
                    alive[chan] = a
            self._anims = alive
            self._redraw()
            if self._anims:
                self._anim_kick()
        except Exception as e:
            self._anims = {}
            print(f"[ui] 动画异常 {e}", flush=True)

    def _sync_expanded(self, animate=True):
        """决定"现在是圆球还是横条"，并（默认平滑地）切过去。

        判定在纯函数 `should_expand` 里：**需要用户动手**的状态
        （等你导出 / 要你打开草稿 / 出错）必须自己弹出来 ——
        那几种状态流程正卡在等用户，字读不到就等于卡死，不能藏在球里。
        """
        want = should_expand(self.state[0], self._await_restore,
                             self._hover, self._ball_mode)
        if want == self._expanded and self._anims.get("shape"):
            return
        self._expanded = want
        tw, tr = self._target_shape()
        tgt = {"w": tw, "r": tr, "fgv": 1.0 if want else 0.0}
        if animate and getattr(self, "_ui_ready", False):
            self._anim_begin("shape", tgt, ANIM_SHAPE_SECS)
        else:
            self._anims.pop("shape", None)
            self._cur.update(tgt)
            self._redraw()

    def _on_enter(self, _e=None):
        self._hover = True
        self._leave_at = 0.0
        if getattr(self, "_ui_ready", False):
            self._anim_begin("color", {"bgv": 1.0}, ANIM_COLOR_SECS)
        else:
            self._cur["bgv"] = 1.0
        self._sync_expanded()

    def _on_leave(self, _e=None):
        """★ 收起<b>不在这里</b>做：展开时窗口会变宽，tk 可能补一个"假的" <Leave>，
        立刻收起就变成"展开↔收起"自激抖动。这里只记时间戳，由 `_pump` 延迟判定。"""
        self._hover = False
        self._leave_at = time.time()
        if getattr(self, "_ui_ready", False):
            self._anim_begin("color", {"bgv": 0.0}, ANIM_COLOR_SECS)
        else:
            self._cur["bgv"] = 0.0

    def _maybe_rescale(self, jh):
        """按剪映窗口高度决定缩放（量化到 0.05，且**跨台阶 + 持续稳定**才改）。

        基准取**工作区高度**（SPI_GETWORKAREA，与 tk 坐标同一套 DPI 虚拟化单位）：
        剪映最大化时窗口高 ≈ 工作区高 → 缩放 ≈ 1.0，不会莫名其妙变大变小。
        夹在 [0.85, 1.25]；用户可用 ui_follow_size=off 关掉。

        ★ 2026-09-19 第十一批：这个基准 API 会随**任务栏显/隐**整体跳一个任务栏高
          （本机 864 ↔ 816），而剪映最大化时窗口高**同步**跳 48px（830 ↔ 878），
          于是比例在 0.95 / 1.00 / 1.10 三个台阶间来回翻，
          按钮肉眼可见地一大一小地抖（用户日志连着 30+ 次翻面）。
          三道闸：① `stable_ref` 钉住参考高；② `scale_deviates` 档位死区；
                  ③ `RESCALE_HOLD` 按时间确认。判定逻辑全在纯函数 `rescale_verdict`。
        """
        if not self.cfg.get("ui_follow_size", True) or jh <= 0:
            return

        # ---- 闸①：读参考高度并**钉住**（任务栏那次 48px 整体跳要吃掉）----
        raw = None
        try:
            wa = wt.RECT()
            if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0) \
                    and (wa.bottom - wa.top) > 200:
                raw = wa.bottom - wa.top
        except Exception:
            raw = None
        if not raw:
            try:
                raw = (user32.GetSystemMetrics(1) or 1080) - 48
            except Exception:
                raw = 1032
        ref = stable_ref(raw, self._ref_locked)
        if not ref:
            return
        self._ref_locked = ref

        # ---- 闸②③：档位死区 + 按时间确认（纯函数判定）----
        #   ★ 判定只在 `_follow` 域里做（不含 dpi）—— 理由见 __init__ 里那段注释。
        act, s = rescale_verdict(jh, ref, self._scale_follow, time.time(),
                                 self._cand_s, self._cand_t0)
        if act == "skip":
            self._cand_s = None           # 回到"不用改" → 候选作废
            return
        if act == "wait":
            if self._cand_s != s:
                self._cand_t0 = time.time()
            self._cand_s = s
            return
        self._cand_s = None
        print(f"[float] 按钮随剪映缩放 {self._scale_follow:.2f} -> {s:.2f}"
              f"（剪映高 {jh} / 工作区 {ref}）", flush=True)
        self._scale_follow = s
        self._apply_scale()

    # ------------------------------------------------------------ 缩放合成
    def _apply_scale(self):
        """两个缩放因子（跟随 / 显示器 dpi）任一变了 → 合成生效缩放并整窗重建。

        ★ 为什么要有这个唯一出口：`_scale` 是**派生量**（= `_scale_follow * _dpi`），
          `_build_ui()` 只认它。散在各处自己乘一遍，迟早出现"有人乘了 dpi、
          有人没乘"——那就是又一次"同一份数写两遍"（本项目已栽过两次）。
        """
        s = self._scale_follow * self._dpi
        if abs(s - self._scale) < 1e-9:
            return
        self._scale = s
        self._build_ui()

    def _sync_dpi(self):
        """窗口挪到另一块显示器时，把缩放换成那块屏的 dpi 比例（≤2 次/秒）。

        ★ 为什么必须做：本机是 125%（主屏）/ 100%（副屏）。声明 PER_MONITOR_AWARE_V2
          之后 Windows **不再**替我们拉伸位图 —— dpi 变了就得自己把度量乘数换掉，
          否则球被拖到副屏上会比设计**大 25%**（用户看到的就是"球突然变胖了"）。
          用 `MonitorFromWindow` 的变化当触发器（比每帧查 dpi 便宜）。
        """
        if not self.my_hwnd:
            return
        now = time.time()
        if now - self._dpi_checked_at < 0.5:
            return
        self._dpi_checked_at = now
        try:
            user32.MonitorFromWindow.restype = ctypes.c_void_p
            user32.MonitorFromWindow.argtypes = [wt.HWND, ctypes.c_uint]
            mon = int(user32.MonitorFromWindow(wt.HWND(self.my_hwnd), 2) or 0)
        except Exception:
            return
        if not mon or mon == self._dpi_mon:
            return
        self._dpi_mon = mon
        r = dpi_ratio(self.my_hwnd)
        if abs(r - self._dpi) < 1e-6:
            return
        print(f"[ui] 换显示器：dpi 缩放 {self._dpi:.2f} -> {r:.2f}", flush=True)
        self._dpi = r
        #   dpi 一变，形状/窗口尺寸全变 → 位置缓存必须清掉，否则窗口停在旧尺寸上被裁
        self._last_float = None
        self._last_rect = None
        self._apply_scale()

    # ------------------------------------------------------------ 嵌入
    def _try_embed(self):
        if self.embedded or not self.jy_hwnd:
            return
        hwnd = self.my_hwnd
        try:
            self.root.withdraw()
            r = user32.SetParent(hwnd, self.jy_hwnd)
            err = ctypes.get_last_error()
            print(f"[embed] SetParent({hwnd} <- {self.jy_hwnd}) ret={r} err={err}", flush=True)
            if not r:
                self.root.deiconify()
                return
            style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
            style = (style & (0xFFFFFFFF ^ WS_POPUP)) | WS_CHILD | WS_VISIBLE
            user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_int32(style).value)
            self.embedded = True
            self.root.deiconify()
            self._reposition(force=True)
            print("[embed] 嵌入完成", flush=True)
        except Exception as e:
            print(f"[embed] 异常: {e}", flush=True)
            self.embedded = False
            self.root.deiconify()

    def _reposition(self, force=False):
        if not (self.embedded and self.jy_hwnd):
            return
        rc = wt.RECT()
        if not user32.GetClientRect(self.jy_hwnd, ctypes.byref(rc)):
            return
        cw, ch = rc.right, rc.bottom
        # 剪映最小化/刚启动时客户区尺寸为 0，此时定位会把按钮算到屏幕外，必须跳过
        if cw < self.W + 60 or ch < self.H + 60:
            return
        # ★ 缓存键必须**带上自身尺寸**（2026-09-19 第十一批）：只比客户区大小的话，
        #   按钮自己变宽/变窄（文案自适应、字号缩放）时这里会直接 return →
        #   窗口还停在旧尺寸上，新画出来的宽底图被窗口边界裁掉。
        rect = (cw, ch, self.W, self.H)
        if not force and rect == self._last_rect:
            return
        self._last_rect = rect
        x, y = corner_xy(self.corner, (0, 0, cw, ch), self.W, self.H,
                         int(self.cfg.get("corner_margin", 14)),
                         int(self.cfg.get("top_gap", 92)))
        # 形状坐标 → 窗口坐标（外扩投影留白，见 _win_wh）
        P = self._pad
        user32.SetWindowPos(self.my_hwnd, HWND_TOP, int(x) - P, int(y) - P,
                            self.W + 2 * P, self.H + 2 * P, SWP_NOACTIVATE)

    def _detach(self):
        """剪映没了：重启自身，回到独立模式重新找宿主"""
        try:
            user32.SetParent(self.my_hwnd, None)
        except Exception:
            pass
        exe = sys.executable if getattr(sys, "frozen", False) else subprocess.list2cmdline(
            [sys.executable, str(core.config_path().parent / "剪映伴侣.py")])
        try:
            subprocess.Popen([exe] if getattr(sys, "frozen", False)
                             else [sys.executable, str(core.config_path().parent / "剪映伴侣.py")],
                             cwd=str(core.config_path().parent))
        except Exception:
            pass
        self._quit()

    # ------------------------------------------------------------ UI 状态
    def _resize_to(self, w):
        """定**横条**的目标宽度（宽度是唯一"跟随文案"的维度，高度不动）。

        ★ 第十二批语义变更：这里不再直接改窗口宽度，只改 `self._pill_w`
          （= 展开成横条时该多宽）。窗口的实际宽度由 `_redraw` 按"球/横条"
          动画状态决定 —— 收成球的时候窗宽就是球径，横条多宽都得等展开才用得上。
          否则收起状态下文案一变就会把球拉长，球就不成球了。

        ★ 尺寸变了必须让**定位**重算（两个缓存都清），否则窗口还停在旧宽度上，
          新画出来的宽底图会被窗口边界裁掉（嵌进剪映那半边尤其明显）。
        """
        if not w or int(w) == self._pill_w:
            return False
        self._pill_w = int(w)
        if self._expanded:
            tw, tr = self._target_shape()
            if getattr(self, "_ui_ready", False):
                self._anim_begin("shape", {"w": tw, "r": tr}, ANIM_SHAPE_SECS)
            else:
                self._cur.update({"w": tw, "r": tr})
        # ★ 尺寸变了必须让**定位**重算：两个缓存都要清，否则窗口还停在旧宽度上
        self._last_float = None
        self._last_rect = None
        return True

    def _fit_width(self):
        """按当前文案定**横条**宽度 + 拿回"该显示的文字"（★ 保证任何文案都不被裁掉）。

        规则：
          ① 宽度取 **主/副文案里较宽者**（副标题在水面下也常是最长那条），
             上限 `BASE_W * UI_MAX_GROW`；
          ② 到上限还放不下 → 主文案字号逐级降（最低 7pt），再不行才用省略号；
          ③ 抖动抑制：倒计时/百分比每秒都在变，宽度只在
             "**放不下**（必须涨）/ 明显变宽 / 明显变窄"时才真改。

        ★★ 第二十二批（根因修复）：`tx` / `pad_r` / 字号**一律取自 `ui_metrics()`**。
          以前这里自己写死 `tx = 36*s`、`pad_r = 10*s`（旧布局的数），而
          `_build_ui()` 用的是新布局的 47 / 12 —— 于是"算出来的宽度"永远比
          "真正要用的宽度"少 13px：文案放不下 → 被省略号吃掉关键一截。
          实测：「草稿备份没做成，导出后无法自动**还原**」只剩「…无法自动…」。
        """
        s = self._scale
        m = ui_metrics(s)
        base = max(150, int(round(BASE_W * s)))
        hard = int(round(base * UI_MAX_GROW))
        tx, pad_r = m["tx"], m["pad_r"]
        # ★★ 第二十三批：字号从 Tk 的**点**改成自绘的**像素**，量宽用 `Metrics1x`
        #   —— 它和真正绘制走同一条 FreeType 路径（4 倍画布上量、再除回 1x），
        #   所以"这里说放得下"就等于"画出来真的放得下"。
        #   旧版这里是 `tkfont.Font(size=10)`（≈13.33px），而绘制按 13px，
        #   差的那 2% 就是"最后一行字被窗口裁掉"的根因。
        tpx, spx = m["t_px"], m["s_px"]
        f = _ur.Metrics1x("reg", tpx)
        fs = _ur.Metrics1x("reg", spx)
        need = max(f.measure(self._txt), fs.measure(self._sub)) + tx + pad_r
        while need > hard and tpx > 9:            # ② 降主文案字号（1px 一级）
            tpx -= 1
            f = _ur.Metrics1x("reg", tpx)
            need = max(f.measure(self._txt), fs.measure(self._sub)) + tx + pad_r
        want = min(hard, max(base, int(math.ceil(need / float(UI_GROW_STEP))) * UI_GROW_STEP))
        # ★ 比的是"横条当前该多宽"（self._pill_w），不是窗口宽度 ——
        #   收成球时窗口宽只有球径，拿它做滞回比较的话每次都会被判成"必须涨"。
        cur_w = max(base, int(self._pill_w or base))
        avail_now = cur_w - tx - pad_r
        # ③ "放不下"必须涨（否则文字就被省略号吃掉）；变宽/变窄都加了滞回
        if getattr(self, "_booted", False) and not (
                need > avail_now or want >= cur_w + UI_GROW_STEP
                or want <= cur_w - UI_SHRINK_HYST):
            want = cur_w
        self._booted = True
        self._resize_to(want)
        self._f, self._fs = f, fs
        self._t_px, self._s_px = tpx, spx
        cur_w = max(base, int(self._pill_w or base))
        avail = cur_w - tx - pad_r
        return (ellipsize(f, self._txt, avail), ellipsize(fs, self._sub, avail))

    def _paint(self, kind, text=None, sub=None, dot=None):
        """刷新外观：定横条宽度 → 定"球/横条"形态 → 统一重画。

        文案会记进 self._txt/_sub，供 _build_ui 重建后复原。
        ★ 这里不再自己 itemconfigure 文字：一切渲染都交给 `_redraw` 一个出口，
          否则动画中间帧会跟这里的改动互相打架。
        """
        if text is not None:
            self._txt = text
        if sub is not None:
            self._sub = sub
        color = dot or status_color(kind)
        self._dot_color = color
        self._fit_width()               # ① 定横条宽度（可能同时降了字号）
        self._sync_expanded()           # ② 定形态（球 / 横条），需要时平滑切换
        # ③ 进度弧：朝新百分比滑过去（不是瞬跳，才叫"流畅"）
        if self._pct is None:
            self._cur["pct"] = 0.0
        elif getattr(self, "_ui_ready", False):
            self._anim_begin("pct", {"pct": float(self._pct)}, ANIM_PCT_SECS)
        else:
            self._cur["pct"] = float(self._pct)
        self._redraw()

    def set_state(self, kind, text, sub=None, hold=0, pct=None, step=None):
        self.state = (kind, text)
        self._until = time.time() + hold if hold else 0
        # ★ 进度只在 kind=="busy" 时显示；收尾（ok/err）把条藏掉，免得
        #   "已完成"还挂着一条半截的进度条让人以为没跑完。
        if pct is None or kind != "busy":
            self._pct = None
        else:
            self._pct = pct
        self._step = step
        self._paint(kind, text, sub)

    # ------------------------------------------------------------ 球头顶的卡片
    # ★★ 第二十一批（用户："悬浮球的通知应该是以小对话框形式在头顶浮现"）。
    #   通知与确认共用同一套"球旁边的无边框小卡"（见 Popup），这里只负责摆位、
    #   淡入淡出、以及"同一时刻只有一张"。
    def _card_anchor(self, w, h, prefer="above"):
        """算出卡片该摆哪：**球的正上方**居中；上方放不下就翻到正下方，再夹进工作区。"""
        gap = 10
        try:
            r = core.window_rect(self.my_hwnd)
        except Exception:
            r = None
        if not r:
            return 80, 80
        wa = None
        try:
            wa = core.monitor_work_area(self.my_hwnd)
        except Exception:
            wa = None
        if not wa:
            wa = (r[0] - 2400, 0, r[2] + 2400, 1080)
        cx = (r[0] + r[2]) // 2
        x = cx - int(w) // 2
        y = r[1] - int(h) - gap
        if prefer == "above" and y < wa[1]:
            y = r[3] + gap                 # 头顶顶到屏幕边了 → 落到脚下
        elif prefer == "below":
            y = r[3] + gap
            if y + int(h) > wa[3]:
                y = r[1] - int(h) - gap
        x, y = clamp_box(x, y, w, h, wa)
        return int(x), int(y)

    def _drop_card(self):
        """收掉当前卡片。**通知与确认都走这一个出口**（保证同一时刻只有一张）。"""
        c = getattr(self, "_card", None)
        self._card = None
        if c is not None:
            try:
                c.destroy()
            except Exception:
                pass

    CARD_W = 300          # 通知卡宽度（确认卡另算，见 ask_confirm）
    CARD_BODY_MAXLINES = 3   # 正文最多几行（通知是"扫一眼"，不是"读一段"）

    def notify(self, title, msg="", kind="ok", hold=4.0):
        """在球**头顶**浮一张 iOS 风通知卡：自动淡出，点一下立刻收。

        ★ 替代旧版的 `core.notify_box()` —— 那是个**系统 MessageBox**（黑底、带标题栏、
          跟桌面风格割裂），正是用户说"ui 设计可以抄一下 iPhone"要换掉的东西。
        ★ 为什么必须"同一时刻只有一张"：流程里连续几步都可能报消息，
          叠成通知墙比不通知更烦；新的顶掉旧的，用户永远只看最新那条。
        ★★ 第二十二批：正文从**单行 ellipsize** 改成**折行 + 按行数定高**。
          旧实现固定 64 高、单行、放不下就砍 —— 实测「左键点球 → 还原草稿（回到预合…」
          把**要用户做的事**砍掉了半句，等于没通知。
        ★★ 第二十三批：整张卡改由 `ui_render.render_card()` 渲染（真投影 + 抗锯齿圆角），
          高度也改成读 `ui_render.card_metrics()` 的行高 —— **和排版读同一份数**，
          否则"折了 3 行该多高"会有两套算法，差 1px 就让最后一行贴到卡边。
        """
        try:
            self._drop_card()
            # ★ 星号剥在这一个出口里做完（和 ask_yes / show_info 同一条规矩）：
            #   自绘卡片不像 messagebox 会自己消化标记，不剥的话用户会看到 ** 本身。
            title, msg = md_plain(title), md_plain(msg)
            s = self._scale
            cm = _ur.card_metrics(s)
            S = _ur.default_card_sizes(s)
            f_t = _ur.Metrics1x("bold", S["title"])
            f_b = _ur.Metrics1x("reg", S["body"])
            w = self.CARD_W
            avail = w - cm["tx"] - cm["pad"]
            # —— 先量文字、再定高（高度是**结果**，不是写死的常数）——
            title = ellipsize(f_t, title, avail)
            lines = wrap_lines(f_b, msg, avail) if msg else []
            if len(lines) > self.CARD_BODY_MAXLINES:
                keep = self.CARD_BODY_MAXLINES
                lines = lines[:keep - 1] + [
                    ellipsize(f_b, "".join(lines[keep - 1:]), avail)]
            gap = 6
            block = cm["t_h"] + (len(lines) * cm["l_h"] + gap if lines else 0)
            h = max(cm["icon_d"] + 2 * cm["pad"], block + 2 * cm["pad"])
            card = Popup(self.root, w, h, radius=16, bg=SURFACE_HI)
            img = _ur.render_card(
                w, h, 16, scale=s, kind=kind,
                glyph=pill_glyph(kind, self._pct),
                title=title, lines=lines, sizes=S)
            x, y = self._card_anchor(w, h)
            card.show(img, x, y - 6, 0.0,     # 起点略高 = 往下滑一点点（入场方向）
                      spec={"kind": kind, "title": title, "lines": list(lines),
                            "glyph": pill_glyph(kind, self._pct),
                            "buttons": None, "hot": None})
            card.lift()
            self._card = card
            # 入场：120ms 内滑到位置 + 淡入（用 tk 的 after 链，别为它开线程）
            steps = [0.45, 0.8, 1.0]
            for i, a in enumerate(steps):
                self.root.after(30 * (i + 1),
                                lambda a=a, x=x, y=y: self._card_slide(card, x, y, a, i))
            if hold:
                self.root.after(int(hold * 1000), lambda c=card: self._card_out(c))
            card.top.bind("<Button-1>", lambda _e, c=card: self._card_out(c))
        except Exception as e:
            print(f"[ui] 通知卡片失败，退回系统提示: {type(e).__name__}: {e}", flush=True)
            try:
                core.notify_box(f"{title} {msg}".strip())
            except Exception:
                pass

    def _card_slide(self, card, x, y, a, i):
        """入场动画的一帧：只对**当前这张**卡片生效（换卡后旧的回调自动失效）。"""
        if card is not getattr(self, "_card", None):
            return
        try:
            card.place(x, y - int(round(6 * (1.0 - a))), alpha=a)
        except Exception:
            pass

    def _card_out(self, card):
        """出场：淡出后销毁。"""
        if card is not getattr(self, "_card", None):
            return
        self._card = None
        for i, a in enumerate((0.7, 0.35, 0.0)):
            self.root.after(26 * (i + 1), lambda a=a: card.alpha(a))
        self.root.after(110, card.destroy)

    def ask_confirm(self, title, msg, ok_label="开始", no_label="取消", kind="ask"):
        """球头顶弹一张 iOS 风确认卡，返回用户是否点了「确定」（阻塞到作答）。

        ★ 这是**自绘确认框的唯一出口**（和 `ask_yes` 那条"模态框不许散在代码里"
          的规矩同源；只是这一张长得像 iOS，而不是系统 MessageBox）。
        ★ 阻塞用 `wait_window`（嵌套事件循环）—— 语义和 messagebox 一致，
          期间的动画/跟随照常跑，界面不会冻住。
        ★ 卡片建不出来（极端情况）→ **退回 `ask_yes`**：宁可弹个丑的系统框，
          也绝不"问都不问就开跑"。
        ★★ 第二十三批：底图从 Tk Canvas 改成 `ui_render.render_card()` 的整张位图
          （逐像素 alpha，真投影 + 抗锯齿圆角）。
          代价与通知卡相同：**窗口里不能有子控件**，所以
            · 按钮悬停高亮 = **重画一遍位图**（带 hot 标记的那版；贵的那一遍有
              渲染缓存，同一组 hot 组合只算一次，来回划鼠标只命中 3 张缓存图）；
            · 点击命中 = 向 `ui_render.card_buttons()` 要矩形 —— 和真正画出来的
              按钮**同一个函数算的**，不会出现"看到按钮在这儿、点下去没反应"。
        ★ 高度依旧是**先量文字再定**：正文折了几行、卡片就多高，不留空档也不裁字。
        """
        try:
            self._drop_card()
            # ★ 星号剥在这个**唯一出口**里做完（同 ask_yes / show_info 的规矩）。
            title, msg = md_plain(title), md_plain(msg)
            s = self._scale
            cm = _ur.card_metrics(s)
            S = _ur.default_card_sizes(s)
            f_m = _ur.Metrics1x("reg", S["body"])
            w = 284
            avail = w - cm["tx"] - cm["pad"]
            # 标题也走同一套度量：太长就省略，别让一个长标题把卡片撑破
            title = ellipsize(_ur.Metrics1x("bold", S["title"]), title, avail)
            lines = wrap_lines(f_m, msg, avail) if msg else []
            block = cm["t_h"] + (len(lines) * cm["l_h"] + 6 if lines else 0)
            btn_h = int(round(_ur.CARD_BTN_H * s))
            # 文字区在"卡片高 − 按钮行"里居中（`_do_card` 就是这么摆的），
            # 所以高度 = 文字区 + 按钮行，两边读的是同一份度量。
            h = max(cm["icon_d"] + 2 * cm["pad"], block + 2 * cm["pad"]) + btn_h
            radius = 16                                  # 卡片圆角（按钮高亮要对齐它）
            card = Popup(self.root, w, h, radius=radius, bg=SURFACE_HI)
            color = status_color(kind)
            rects = _ur.card_buttons(w, h, radius, s, n=2)   # ← 命中矩形（窗口坐标）
            x, y = self._card_anchor(w, h)
            ans = {"v": False}
            hl = {"now": None}

            def _paint(which):
                """把卡片（含当前哪个按钮被悬停）重画一遍并上屏。

                ★ 只重画**按钮行**的形状变了，但整张卡是一张位图 —— 没法局部更新。
                  靠 `render_card` 的缓存放住成本：hot 组合只有 3 种，
                  划一次鼠标最多算 3 张，之后全命中缓存（1x 上屏几十微秒）。
                """
                btns = [(no_label, which == "no", None),
                        (ok_label, which == "ok", color)]
                img = _ur.render_card(w, h, radius, scale=s, kind=kind,
                                      glyph=pill_glyph(kind, self._pct),
                                      title=title, lines=lines,
                                      buttons=btns, sizes=S)
                card.show(img, x, y,
                          spec={"kind": kind, "title": title, "lines": list(lines),
                                "glyph": pill_glyph(kind, self._pct),
                                "buttons": [no_label, ok_label],
                                "hot": which})

            def pick(v):
                ans["v"] = v
                card.destroy()

            def _which(e):
                """坐标 → 命中的按钮（None = 没在按钮上）。热区就是整块按钮区。"""
                for i, (rx0, ry0, rx1, ry1) in enumerate(rects):
                    if rx0 <= e.x < rx1 and ry0 <= e.y < ry1:
                        return "no" if i == 0 else "ok"
                return None

            def _set_hl(which):
                if which == hl["now"]:
                    return
                hl["now"] = which
                _paint(which)

            def _motion(e):
                _set_hl(_which(e))

            def _click(e):
                got = _which(e)
                if got:
                    pick(got == "ok")

            _paint(None)
            card.lift()
            self._card = card
            card.top.bind("<Motion>", _motion)
            card.top.bind("<Leave>", lambda _e: _set_hl(None))
            card.top.bind("<Button-1>", _click)
            card.top.bind("<Escape>", lambda _e: pick(False))
            card.top.bind("<Return>", lambda _e: pick(True))
            try:
                card.top.focus_force()
            except Exception:
                pass
            self.root.wait_window(card.top)
            if getattr(self, "_card", None) is card:
                self._card = None
            return bool(ans["v"])
        except Exception as e:
            print(f"[ui] 确认卡片失败，退回系统确认框: {type(e).__name__}: {e}", flush=True)
            try:
                self._card = None
            except Exception:
                pass
            return ask_yes(title, msg, parent=self.root)

    # ------------------------------------------------------------ 悬停（轮询兜底）
    def _poll_hover(self):
        """按**光标真实位置**判断悬停，而不是只信 tk 的 <Enter>/<Leave>。

        ★★ 为什么必须兜底（第二十一批）：用户反馈"鼠标碰到悬浮球不会触发展开"。
          离线复现时 `<Enter>` 是通的，说明是**事件在某些情况下没送到**——
          球是无边框置顶窗，还被 `_follow_fast` 每 33ms 用 `geometry()` 摆一次位置，
          窗口移动/改尺寸的瞬间 tk 可能吞掉 Enter/Leave。
          光标的真实位置骗不了人，所以这里用 Win32 直接问一次：
          光标落在球窗内 → 当作悬停；离开 → 记时间戳交给 `_pump` 延迟收起。
        ★ 成本：每帧两次 Win32 调用（GetCursorPos + 矩形比较），30fps 无感。
        """
        if self._dragging:
            return
        try:
            pt = wt.POINT()
            if not user32.GetCursorPos(ctypes.byref(pt)):
                return
            r = core.window_rect(self.my_hwnd)
            if not r:
                return
            inside = (r[0] <= pt.x < r[2]) and (r[1] <= pt.y < r[3])
            if inside and not self._hover:
                self._on_enter()
            elif (not inside) and self._hover:
                self._on_leave()
        except Exception:
            pass

    # ------------------------------------------------------------ 拖动（用户自由摆位）
    def _on_press(self, e):
        """按下：**先只记坐标**，不动窗口 —— 是不是"拖动"要等鼠标真的走起来才判。"""
        self._press = (e.x_root, e.y_root,
                       int(self.root.winfo_x()), int(self.root.winfo_y()))
        self._dragging = False

    def _on_drag(self, e):
        """按住移动：超过阈值才算拖动（否则就是一次普通点击，不能变成挪位置）。"""
        p = getattr(self, "_press", None)
        if not p:
            return
        dx, dy = e.x_root - p[0], e.y_root - p[1]
        # ★ 阈值用**欧氏距离**而不是 |dx|+|dy|：后者在斜向移动时会被放大
        #   （3px 斜移 = 6），于是"手抖一下"就被判成拖动，点击就丢了。
        if not self._dragging and (dx * dx + dy * dy) < 25:   # < 5px
            return
        self._dragging = True
        try:
            # p[2]/p[3] 记的是**窗口**左上角（winfo_x/y），所以这里直接用窗口尺寸
            _w, _h = self._win_wh()
            self.root.geometry(f"{_w}x{_h}+{p[2] + dx}+{p[3] + dy}")
        except Exception:
            pass

    def _on_release(self, e):
        """松开：拖动过 → **记住新位置**（下次启动还在那），并且**不触发点击动作**；
        没拖动过 → 当成一次正常点击。"""
        drag = self._dragging
        self._dragging = False
        self._press = None
        if not drag:
            self._click(e)
            return
        try:
            # ★ 退回**形状坐标**：cfg["float_xy"] 里一直存的是形状位置
            #   （`_float_position` 会再外扩 _pad），存窗口坐标会让球每次启动
            #   往左上跳 _pad。
            P = self._pad
            x = int(self.root.winfo_x()) + P
            y = int(self.root.winfo_y()) + P
            wa = core.monitor_work_area(self.my_hwnd)
            if wa:
                x, y = clamp_box(x, y, self.W, self.H, wa)
                self._push_geometry(x, y)
            # ★ 用户自己摆过了 → 从此**不再**被角落吸附拉回去（那会让人觉得"摆了没用"）
            self.cfg["float_xy"] = [x, y]
            self.cfg["pos_custom"] = True
            core.save_config(self.cfg)
            self._last_float = None
            self.notify("位置已记住", "想回到角落：右键菜单 →「重置位置」",
                        kind="ok", hold=2.5)
        except Exception as ex:
            print(f"[ui] 记住位置失败: {type(ex).__name__}: {ex}", flush=True)

    def _reset_pos(self):
        """把球放回角落吸附。"""
        self.cfg["pos_custom"] = False
        self.cfg.pop("float_xy", None)
        try:
            core.save_config(self.cfg)
        except Exception:
            pass
        self._last_float = None
        self._float_position(force=True, rescale=False)
        self.notify("已回到角落", "", kind="ok", hold=2.0)

    def _idle_sub(self):
        """空闲态按钮下面那行字。

        ★ 2026-09-19 第十一批（体验）：有"跑过一键导出、但还没还原"的备份时，
          优先提示这件事。理由：那个状态是**存在草稿里**的
          （时间线被真清空过、草稿已是预合成后的样子），可界面提示只在
          「等你导出」那一小段里常驻 —— 用户要是那会儿关了伴侣、或者重启了电脑，
          再打开就只剩一句"一键导出"，很容易忘了还没还原，
          甚至在半残的草稿上又点一次。空闲态说出来，这条线才不会断。
        """
        try:
            _bk, _dr, n = core.backup_info(self.cfg)
            if n and not core.backup_consumed(self.cfg):
                return "上次那轮还没还原 · 右键「还原草稿」"
        except Exception:
            pass
        return "预合成 · 存草稿 · 清空原内容 · 你手动拖入"

    def _back_to_idle(self):
        if self._until and time.time() > self._until:
            self._until = 0
            self._pct = None
            self._step = None
            self.state = ("idle", "一键导出")
            self._paint("idle", "一键导出", self._idle_sub())

    # ------------------------------------------------------------ 动作
    def _click(self, _e=None):
        print(f"[click] 收到点击 state={self.state[0]} jy={self.jy_hwnd} embedded={self.embedded} "
              f"await_restore={self._await_restore} cancel={self._cancel is not None}", flush=True)
        if self.state[0] == "busy":
            # ★★ 第十七批：运行中左键点球 = 「暂终止」（用户："用户可以暂终止，
            #   终止时自动还原"）。以前这里直接 `return` —— 流程一旦跑起来
            #   就成了死键，只能干等它跑完，正是"点了没反应"那一类体验。
            #   ★ 只在 `self._cancel` 存在（= 真的能中止）时才接这个动作：
            #     "取最新文件"这类瞬时任务不挂令牌，保持不响应，绝不假装能停。
            if self._cancel is not None:
                self._abort_pipeline()
            return
        # ★ 正在"等你导出"时，左键点一下 = 发还原指令（用户说的"等待用户还原指令"）。
        #   先弹确认：这一步会**关掉剪映**（还原必须杀进程，否则它退出时会把内存里的
        #   旧内容覆盖回来），要是你还在导出就被掐了，所以必须问一句。
        if self._await_restore:
            yes = self.ask_confirm(
                "结束这一轮，还原草稿？",
                "已经导出完成了吗？\n\n"
                "点「还原」会把草稿还原到预合成之前，\n"
                "并重开剪映回到这份草稿（产物文件一个字节不碰）。\n"
                "还想继续导的话点「取消」，我继续等你。",
                ok_label="还原", no_label="取消", kind="ok")
            if not yes:
                return
            self._restore_draft()
            return
        # ★★ 第二十一批（用户："点击后要先问一下用户是否准备开始导出"）：
        #   以前点一下**直接开跑** —— 全选 + 预合成 + **清空原时间线**（真删），
        #   误触一次的代价是草稿被改（虽然能还原，但用户当时并不知道）。
        #   现在先弹一张 iOS 风确认卡，把"我会做什么"和"要花多久"讲清楚。
        if not self.ask_confirm(
                "开始导出？",
                "全选 → 复合片段 → 预合成 → 重启剪映 →\n"
                "清空原时间线 → 打开产物文件夹。\n\n"
                "草稿会先备份，导出完可一键还原；\n"
                "产物文件全程不动。大约 2~5 分钟。",
                ok_label="开始", no_label="取消", kind="ask"):
            return
        if self.jy_hwnd and user32.IsWindow(self.jy_hwnd):
            self._start_pipeline()
            return
        # ★ 2026-09-19 第十四批（用户"继续优化"）：别急着下"剪映没开"的结论 ——
        #   `_watch` 可能只是**还没认出**窗口（它 400ms 一轮，且剪映切页会重建主窗）。
        #   不这么做的话，剪映明明开着、只是伴侣晚了一拍，就会**又启动一个剪映** ——
        #   两个窗口抢前台，后面 find_jianying 还可能认错那个。
        #   ★ 但这里**只做一次瞬时查找，绝不 sleep 等**：主线程一卡，球就冻住，
        #     用户刚点完却半天没反应 —— 又是一出"点了没反应"。等待/启动/重试
        #     整个丢给工作线程（`_launch_then_run`），界面全程不卡。
        win = core.find_jianying(exclude=self.my_hwnd)   # 瞬时，内部不 sleep
        if win:
            self.jy_hwnd = win[0]
            self._start_pipeline()
        else:
            self._launch_jy(then_run=True)

    # ==================== 批量队列（第十九批 v1.2.0） ====================

    def _fill_batch_menu(self):
        """点开「批量处理草稿…」时**现扫现填**候选草稿（最多 8 份，最近在前）。

        扫描是有界的（3s 预算），但仍是磁盘 IO —— 放在 postcommand 里意味着
        只在用户真的点开菜单时才扫一次，平时零开销。"""
        m = getattr(self, "_batch_menu", None)
        if m is None:
            return
        m.delete(0, "end")
        cands = []
        try:
            root = core.resolve_root(self.cfg, None)
            if root:
                cands = core.list_batch_candidates(root, limit=8)
        except Exception as _e:
            print(f"[batch] 列候选失败: {type(_e).__name__}: {_e}", flush=True)
        if not cands:
            m.add_command(label="（草稿目录里没找到带预合成素材的草稿）",
                         state="disabled")
            return
        for d, name, _mt in cands:
            m.add_command(label=f"「{name}」",
                          command=lambda dd=d: self._start_batch([dd]))
        m.add_separator()
        m.add_command(label=f"全部处理（{len(cands)} 份，按最近排序）",
                      command=lambda: self._start_batch([d for d, _n, _mt in cands]))

    def _start_batch(self, drafts):
        """排队跑批量：逐份 batch_prepare_draft，每份之间不用用户动手。"""
        if self.state[0] == "busy" or self._cancel is not None:
            show_info("批量处理", "上一轮任务还在跑。等它结束（或先中止）再批量。",
                      parent=self.root)
            return
        if not drafts:
            return
        names = "、".join(Path(d).name for d in drafts[:5]) + ("…" if len(drafts) > 5 else "")
        if not ask_yes(
                "批量处理草稿",
                f"将要**自动逐份**处理 {len(drafts)} 份草稿：\n{names}\n\n"
                "每一份都会：备份 → 预合成 → 清空原时间线，\n"
                "跑完就处于「打开即可导出」的状态（导出仍由你自己点）。\n\n"
                "随时可以中止：会还原**当前这一份**并停下，后面的不再动。\n"
                "整个过程大约每份 1~2 分钟，开始后请别动鼠标键盘。",
                parent=self.root):
            return
        self._await_restore = False
        self._stop_export_watch()
        self._cancel = threading.Event()
        self._batch_queue = [Path(d) for d in drafts]
        self.set_state("busy", "批量处理中…",
                       sub=f"共 {len(drafts)} 份 · 每份跑完自动接下一份", pct=2)
        self._sync_menu()
        threading.Thread(target=self._batch_worker, daemon=True).start()

    def _batch_worker(self):
        """批量工作线程：逐份调用内核 batch_prepare_draft，汇总结果。

        ★ 中止语义与单份一致：当前这份**已经动过**（有备份）→ 先还原再停；
          还没动 → 直接停。后面的草稿一律不再碰。"""
        queue = list(getattr(self, "_batch_queue", []))
        total = len(queue)
        results = []
        cancelled = False
        for i, d in enumerate(queue, 1):
            if core.is_cancelled(self._cancel):
                cancelled = True
                break
            prefix = f"[{i}/{total}] "

            def st(t, k="busy", pct=None, sub=None, _i=i, _d=d):
                self._on_status(f"{prefix}「{_d.name}」{t}", k,
                                pct if pct is not None else int(2 + 96 * (_i - 1) / total),
                                sub=sub)

            try:
                ok, msg = core.batch_prepare_draft(self.cfg, d, st,
                                                   cancel=self._cancel)
                results.append((d.name, ok, msg))
            except core.PipelineCancelled:
                cancelled = True
                # 当前这份动过吗？批量每份都先备份再动 —— 只要走到备份之后就要还原。
                try:
                    binfo = core.backup_info(self.cfg)
                except Exception:
                    binfo = None
                if binfo:
                    st("已中止 · 正在还原当前草稿…", "busy")
                    cap = {}

                    def _fin(ok, msg, _cap=cap):
                        _cap["r"] = (ok, msg)

                    core.restore_draft(self.cfg, st, _fin)
                    results.append((d.name, False, "已中止并还原"))
                else:
                    results.append((d.name, False, "已中止（还没动这份）"))
                break
            except Exception as _e:
                results.append((d.name, False, f"出错：{type(_e).__name__}: {_e}"))
        ok_n = sum(1 for _n, ok, _m in results if ok)
        if cancelled:
            self.q.put(("done", True,
                        "批量已中止。\n" + "\n".join(f"· {n}: {m}" for n, _ok, m in results),
                        "abort"))
            return
        lines = [f"批量处理完成：{ok_n}/{total} 份就绪（打开即可导出）。", ""]
        for n, ok, m in results:
            lines.append(f"· {'✅' if ok else '⚠️'} {n}：{m}")
        lines += ["", "导出某份时：打开它 → 左键点球「一键导出」→ 走到拖入那步即可"
                      "（预合成都已经做完了）。"]
        self.q.put(("done", True, "\n".join(lines), "batch"))

    def _start_pipeline(self):
        """起一轮「一键导出」，并挂上可中止令牌（第十七批）。

        ★ 令牌只属于**这一轮**：`_on_done`（在 `_pump` 里）会把它清回 None。
          留着上一轮已 set 的令牌，下一轮刚启动就会"秒中止"。
        """
        self._await_restore = False
        self._stop_export_watch()   # 新一轮开始 = 上一轮的守望作废
        self._cancel = threading.Event()
        self.set_state("busy", "执行中…", "全选 · 复合片段 · 预合成")
        self._sync_menu()          # 让「中止本次流程」立刻可点
        threading.Thread(target=core.run_pipeline, daemon=True,
                         args=(self.cfg, self._on_status, self._on_done),
                         kwargs={"cancel": self._cancel}).start()

    def _abort_pipeline(self):
        """「暂终止」：让正在跑的流程停下来，并**自动把草稿还原回去**。

        ★ 用户要求原话（2026-09-19 第十七批）："用户可以暂终止，终止时自动还原"。
        ★ 两个阶段都能用：
          ① 流程正在跑（`self._cancel` 有令牌）→ 置位令牌；内核在各检查点收手，
             然后**自己**走一遍 `restore_draft`（见 `run_pipeline` 的
             `except PipelineCancelled`），并把结果以 `rescue="abort"` 报回来。
          ② 已经跑完、停在「等你导出」（`self._await_restore`）→ 等价于
             "我不导出了，收工"，直接走还原。
        ★ 必须先确认：**中止会关掉剪映**（还原要杀进程），正导到一半会被掐掉。
        """
        running = self._cancel is not None and not self._cancel.is_set()
        if not running:
            if self._await_restore:
                # 等待期没有"在跑的任务"，但"放弃本轮"就是还原 —— 复用现成入口。
                self._restore_draft()
                return True
            return False
        yes = ask_yes(
            "中止本次流程？",
            "要中止这次「一键导出」吗？\n\n"
            "★ 中止后我会**自动把草稿还原到预合成之前**\n"
            "   （只写草稿元数据 .json，预合成产物一个字节不碰），\n"
            "   再重开剪映回到这份草稿 —— 不会给你留一个半残的草稿。\n"
            "★ 如果流程还没走到预合成，草稿本来就没动过，我会直接停下、不折腾它。\n\n"
            "确定要中止吗？",
            parent=self.root)
        if not yes:
            return False
        self._cancel.set()
        self.set_state("busy", "正在中止…", sub="稍等，我把草稿还原回去")
        print("[abort] 用户点了「暂终止」，已发出中止指令", flush=True)
        return True

    def _on_status(self, text, kind, pct=None, step=None, sub=None):
        # ★ 第十三批：`sub` = 内核给的**第二行提示**（"怎么做"与"为什么"分开放）。
        #   内核那边用 `_call_status` 逐级降级调用，老签名也还能打进来。
        self.q.put(("status", kind, text, pct, step, sub))

    def _on_done(self, ok, msg, rescue=False):
        self.q.put(("done", ok, msg, rescue))

    def _launch_jy(self, silent=False, then_run=False):
        """启动剪映。

        ★ 2026-09-19 第十四批（用户"继续优化"）：原来点「一键导出」而剪映没开时，
          这里**只启动、不跟进** —— 启动完 10 秒状态就自己回到「一键导出」，
          用户得**再点一次**才发现能跑了；而且全程没人告诉他"要再点一次"。
          这就是"点了没反应"那一类体验。
          现在 `then_run=True`：启动 → 等主窗出来（带状态提示）→ **自己接着跑流程**，
          等价于用户再点一次，但不用他动手、也不用他猜。
          菜单里的「启动剪映」仍然是**只启动**（那是一个明确的单一动作）。
        """
        if silent:
            # 开机自启那条：安静地把剪映拉起来就行，别占用界面文案。
            core.launch_jianying(self.cfg)
            return
        if then_run:
            # ★ 整条链路（等 → 没有才启动 → 再等 → 跑）都在工作线程里做。
            #   实测冷启动 20~60s，主线程等它 = 悬浮球连"我正在启动"都刷不出来。
            # ★ 第十七批：这段最长要等 60s，**也要能中止** —— 所以令牌在这里就挂上
            #   （和 `_start_pipeline` 同一个规矩），`_launch_then_run` 会把它透传下去。
            self._await_restore = False
            self._stop_export_watch()   # 新一轮开始 = 上一轮的守望作废
            self._cancel = threading.Event()
            self.set_state("busy", "正在启动剪映…",
                           sub="冷启动可能要几十秒，起来后我自己接着跑", pct=4)
            self._sync_menu()
            threading.Thread(target=self._launch_then_run, daemon=True).start()
            return
        exe = core.launch_jianying(self.cfg)
        if not exe:
            self.set_state("err", "找不到剪映",
                           sub="右键「设置剪映路径…」手动指一下", hold=8)
            return
        self.set_state("busy", "正在启动剪映…", sub="起来后点我「一键导出」", hold=10)

    def _launch_then_run(self):
        """工作线程：确保剪映主窗出现，然后**直接把流程跑起来**（不用用户再点一次）。

        顺序（先等、后启动）很关键：用户点导出时剪映可能**正在启动**（点了桌面图标），
        这时候再 `Popen` 一次就是**重复拉起一个剪映** —— 两个窗口抢前台。
        所以先给它 `LAUNCH_PROBE_SECS` 的时间自己冒出来；真没有才动手启动。
        """
        win = core.wait_jianying(LAUNCH_PROBE_SECS, exclude=self.my_hwnd,
                                 cancel=self._cancel)
        if not win:
            # 确实没在启动 → 现在才真去拉起它（找不到 exe 时 launch_jianying 返回 None）。
            if not core.launch_jianying(self.cfg):
                self._on_done(False,
                              "找不到「剪映专业版」的安装位置。\n\n"
                              "请先装好剪映，或用右键菜单里的「设置剪映路径…」指一下。",
                              rescue="launch")
                return
            win = core.wait_jianying(LAUNCH_WAIT_SECS, exclude=self.my_hwnd,
                                     cancel=self._cancel)
        if core.is_cancelled(self._cancel):
            # ★ 第十七批：用户点了中止 → 别报"剪映没起来"（那是假故障）。
            self._on_done(True,
                          "已经停下了（剪映还在启动，我没再往下走）。\n\n"
                          "★ 这一轮**没动过你的草稿**，不需要还原。\n"
                          "★ 想重新开始，点一下悬浮球就行。",
                          rescue="abort")
            return
        if not win:
            print(f"[launch] 等了 {LAUNCH_WAIT_SECS + LAUNCH_PROBE_SECS:.0f}s 没等到剪映窗口",
                  flush=True)
            self._on_done(False,
                          "剪映没能在规定时间里启动起来。\n\n"
                          "请手动打开「剪映专业版」，等它到首页，再点一次「一键导出」。",
                          rescue="launch")
            return
        print(f"[launch] 剪映起来了 hwnd={win[0]}，自动接上流程", flush=True)
        self.jy_hwnd = win[0]
        core.run_pipeline(self.cfg, self._on_status, self._on_done,
                          cancel=self._cancel)

    def _pick_jy_exe(self):
        """手动指定剪映主程序（绿色版 / 自定义安装 / 装在别的盘 的唯一出路）。

        ★ 2026-09-19 第十四批（用户"继续优化" · 分发）：`find_jianying_exe` 的两条路
          （问运行中的进程 + 几个常见落点）对自定义安装目录**永远命中不了**，
          而失败文案只能干说"找不到剪映专业版"。这条入口把最后一步交回用户。
        ★ 取消对话框**不清空**已有设置 —— 但会问一句要不要清，否则设错了没路回头。
        """
        cur = str(self.cfg.get("jianying_exe") or "").strip()
        _cur_dir = os.path.dirname(cur) if cur else ""
        from tkinter import filedialog as _fd
        p = _fd.askopenfilename(
            title="选中剪映的主程序（一般是 JianyingPro.exe）",
            initialdir=_cur_dir if os.path.isdir(_cur_dir) else None,
            filetypes=[("剪映主程序", "JianyingPro.exe"),
                       ("可执行文件", "*.exe"),
                       ("全部文件", "*.*")],
            parent=self.root)
        if not p:
            if cur and ask_yes(
                    "改回自动查找？",
                    "现在手动指定的是：\n{}\n\n要清掉它、改回自动查找吗？".format(cur),
                    parent=self.root):
                self.cfg["jianying_exe"] = ""
                core.save_config(self.cfg)
                self._sync_menu()
                self.set_state("ok", "已改回自动查找", sub="下次导出会重新自动找剪映", hold=6)
            return
        # 选错文件要拦一下（用户可能在对话框里随手点了个别的 exe）——
        # 允许 CapCut（国际版）和 JianyingPro 两个真名，其余的问一句再放行。
        base = os.path.basename(p).lower()
        if base not in ("jianyingpro.exe", "capcut.exe") and not ask_yes(
                "确认这个路径？",
                "你选的是：\n{}\n\n它**不是**剪映的常见主程序名。确定要用它吗？".format(p),
                parent=self.root):
            return
        self.cfg["jianying_exe"] = p
        core.save_config(self.cfg)
        self._sync_menu()
        self.set_state("ok", "剪映路径已设置", sub=os.path.basename(p), hold=8)
        print(f"[cfg] jianying_exe = {p}", flush=True)

    def _pick_draft_root(self):
        """手动指定剪映的**草稿目录**（在剪映里改过草稿位置的人的唯一出路）。

        ★ 2026-09-19 第十五批（用户"继续优化"）：和「设置剪映路径…」是同一个坑的两半。
          失败文案原来写的是「把路径填进 伴侣配置.json 的 draft_root」——
          让用户手改 JSON，实际上等于没有出路。
        ★ 三处兜底：
          ① 选中的是**上一级**（比如 …\\Projects）时自动往下走一层到 com.lveditor.draft；
          ② 里面找不到预合成产物时问一句（很可能指错了）；
          ③ 取消对话框时问一句要不要改回自动查找。
        """
        cur = str(self.cfg.get("draft_root") or "").strip()
        from tkinter import filedialog as _fd
        p = _fd.askdirectory(
            title="选中剪映的草稿目录（一般叫 com.lveditor.draft）",
            initialdir=cur if os.path.isdir(cur) else None,
            mustexist=True, parent=self.root)
        if not p:
            if cur and ask_yes(
                    "改回自动查找？",
                    "现在手动指定的是：\n{}\n\n要清掉它、改回自动查找吗？".format(cur),
                    parent=self.root):
                self.cfg["draft_root"] = ""
                core.save_config(self.cfg)
                self._sync_menu()
                self.set_state("ok", "已改回自动查找", sub="下次导出会重新自动找草稿目录", hold=6)
            return
        # ① 常见误选：选了包含 com.lveditor.draft 的**上一级**
        sub = Path(p) / "com.lveditor.draft"
        if sub.is_dir():
            p = str(sub)
            print(f"[cfg] 目录里还有一層 com.lveditor.draft，已自动往下走：{p}", flush=True)
        # ② 认不出预合成产物就问一句（"存在"≠"是对的"）
        if not core.draft_root_has_combos(p) and not ask_yes(
                "确认这个目录？",
                "在这个目录里**没找到**剪映的预合成产物（combination 文件夹）。\n\n"
                "你选的是：\n{}\n\n"
                "如果剪映确实把草稿放在这儿（只是还没做过预合成），可以继续；\n"
                "否则请回去重选 —— 一般是 …\\Projects\\com.lveditor.draft。\n\n"
                "确定要用这个目录吗？".format(p),
                parent=self.root):
            return
        self.cfg["draft_root"] = p
        core.save_config(self.cfg)
        self._sync_menu()
        self.set_state("ok", "草稿目录已设置", sub=Path(p).name, hold=8)
        print(f"[cfg] draft_root = {p}", flush=True)

    def _extract_only(self):
        if self.state[0] == "busy":
            return
        self.set_state("busy", "取文件中…", "定位最新预合成产物")
        threading.Thread(target=core.extract_latest, daemon=True,
                         args=(self.cfg, self._on_status,
                                lambda ok, msg: self._on_done(ok, msg, rescue=True))).start()

    # ==================== 导出守望（第十八批 v1.1.0） ====================

    def _start_export_watch(self):
        """进入「等你导出」后，后台守望剪映的导出对话框。

        见到"出现 → 消失"（= 导出走完了，或被用户取消）就投一条
        ("export_done",) 进队列，由 `_pump` 把球弹出来提醒"可以一键还原了"。
        ★ 只观察、不动手：绝不因对话框消失就自动杀剪映/写草稿。
        ★ 守望线程是 daemon，用 stop 事件收摊；重复调用先收旧的（幂等）。
        """
        self._stop_export_watch()
        if not self.cfg.get("export_watch", True):
            return
        stop = threading.Event()
        self._watch_stop = stop

        def _loop():
            result = core.watch_export_dialog(stop)
            try:
                # 收摊令牌自己清掉（谁停的都行；新一轮开始也会先 stop 旧的）
                if self._watch_stop is stop:
                    self._watch_stop = None
            except Exception:
                pass
            if result == "closed":
                try:
                    self.q.put(("export_done",))
                except Exception:
                    pass

        threading.Thread(target=_loop, daemon=True,
                         name="export-watch").start()

    def _stop_export_watch(self):
        """收掉守望线程（开始还原 / 新一轮 / 退出时都要）。幂等。"""
        stop = getattr(self, "_watch_stop", None)
        if stop is not None:
            stop.set()
            self._watch_stop = None

    def _toggle_export_watch(self):
        """菜单开关：导出守望（导出完主动提醒还原）。默认开 —— 纯观察零风险。"""
        self.cfg["export_watch"] = not self.cfg.get("export_watch", True)
        try:
            core.save_config(self.cfg)
        except Exception:
            pass
        self._sync_menu()
        if self.cfg["export_watch"]:
            self.set_state("busy", "导出守望已开",
                           sub="导出窗口一关就提醒你还原", hold=5)
        else:
            self._stop_export_watch()
            self.set_state("busy", "导出守望已关",
                           sub="导完自己记得回来点还原", hold=5)

    def _toggle_fullauto(self):
        """菜单开关：全自动导出（实验）。默认关 —— 开了就是授权替你按导出键。"""
        self.cfg["full_auto_export"] = not self.cfg.get("full_auto_export", False)
        try:
            core.save_config(self.cfg)
        except Exception:
            pass
        self._sync_menu()
        if self.cfg["full_auto_export"]:
            self.set_state("busy", "全自动导出已开（实验）",
                           sub="拖入→导出→守望→自动还原，一条龙", hold=6)
        else:
            self.set_state("busy", "全自动导出已关",
                           sub="拖入和导出交回你手动", hold=5)

    def _run_full_auto(self):
        """「等你导出」之后接管最后一程（v1.3.0 实验）。失败/中止一律退回手动。"""
        if not self._await_restore:
            return
        if self._cancel is None:
            self._cancel = threading.Event()
        self._sync_menu()
        self.set_state("busy", "全自动导出中…",
                       sub="拖入 → 导出 → 守望完成 → 自动还原")
        threading.Thread(target=self._fullauto_worker, daemon=True).start()

    def _fullauto_worker(self):
        try:
            ok, msg = core.full_auto_export(self.cfg, self._on_status,
                                            cancel=self._cancel)
        except core.PipelineCancelled:
            self.q.put(("full_auto_fail", "已中止 · 回到手动模式"))
            return
        except Exception as _e:
            self.q.put(("full_auto_fail", f"全自动出错（{type(_e).__name__}）· 回到手动"))
            return
        if ok:
            self.q.put(("full_auto_done", msg))
        else:
            self.q.put(("full_auto_fail", msg))

    def _restore_draft(self):
        """导出完成后：把草稿**还原回还没有预合成的那一版**。

        ★ 做法：把「一键导出」在预合成**之前**自动备份的草稿元数据 json 写回草稿
          目录（伴侣只写 `.json`；预合成产物一个字节都不碰）。
        ★ 顺序由 core.restore_draft 负责：先杀剪映（否则它退出时会覆盖写回）→
          留一份「还原前」快照 → 写回备份 → 重开剪映回到这份草稿。
        """
        if self.state[0] == "busy":
            return
        # ★ 二次还原必须拦一下（2026-09-18 第十批）：backup_info 在还原后**依然报
        #   "有备份"**（备份原件要留着），用户看不出"已经还原过了"；再点一次就会
        #   把同一份旧备份再写一遍 —— 他要是在还原之后又剪了新东西，
        #   那些新改动会被**无声盖掉**。所以这里只对"二次还原"补一次确认，
        #   正常那一次（右键 = 用户说的"旁路"）保持不加确认。
        if core.backup_consumed(self.cfg):
            if not ask_yes(
                    "再次还原草稿",
                    "这份备份**你已经还原过一次了**。\n\n"
                    "再还原一次 = 把**同一份旧备份**（预合成之前的状态）重新写一遍，\n"
                    "会覆盖掉你在还原之后做的**所有新改动**。\n\n"
                    "（写回前会自动留一份「还原前」快照兜底，但那是事后补救。）\n\n"
                    "真的要再还原一次吗？",
                    parent=self.root):
                return
        self._start_restore()

    def _start_restore(self):
        """真正发起还原（_restore_draft 与全自动导出的收尾共用这一条路）。"""
        self._await_restore = False
        self._stop_export_watch()   # ★ 开始还原 = 守望结束（别让它再报"导出完成"）
        self.set_state("busy", "还原草稿中…", "写回预合成前的草稿 · 只动元数据")
        threading.Thread(target=core.restore_draft, daemon=True,
                         args=(self.cfg, self._on_status,
                               lambda ok, msg: self._on_done(ok, msg,
                                                             rescue="restore"))).start()

    def _toggle_embed(self):
        self.cfg["embed_mode"] = not self.cfg.get("embed_mode", False)
        core.save_config(self.cfg)
        if not self.cfg["embed_mode"]:
            self._detach_to_float()
        else:
            self._detach_to_float()
            self.set_state("busy", "将嵌入剪映", sub="下次检测时生效", hold=4)
        self._sync_menu()

    def _detach_to_float(self):
        """退出嵌入，恢复为独立置顶悬浮窗（保证一定可见可点）"""
        try:
            if self.embedded and self.my_hwnd:
                user32.SetParent(self.my_hwnd, None)
                style = user32.GetWindowLongW(self.my_hwnd, GWL_STYLE) & 0xFFFFFFFF
                style = (style & ~WS_CHILD) | WS_POPUP | WS_VISIBLE
                user32.SetWindowLongW(self.my_hwnd, GWL_STYLE, ctypes.c_int32(style).value)
                self.embedded = False
                self._last_rect = None
            self.root.deiconify()
            self._float_position()
        except Exception as e:
            print(f"[float] 异常 {e}", flush=True)

    def _float_position(self, force=False, rescale=True):
        """独立模式：贴着剪映窗口的**停靠角落**（剪映在哪个屏就跟到哪个屏）；
        剪映不可用时退回主屏工作区的同一个角落。顺带按窗口高度做「同大共小」缩放。

        ★ 第十二批修正：这里原来把位置**写死成右上角**，于是「停靠位置」菜单
          在默认（独立）模式下**完全没有效果** —— 只有嵌入模式认 corner。
          现在两边共用 `corner_xy`，右上/右下/左上/左下都真的生效。

        `rescale=False`：只摆位置、不做缩放判定。形变动画每帧都会调这里，
        不能顺带触发 `_build_ui`（那会在动画中途重建画布）。
        """
        try:
            # ★ 正在被用户拖着走的时候，定位循环**别插手** ——
            #   它按 cfg["float_xy"]（还是拖动**前**那个值）算位置，
            #   虽然当前恰好和 `_last_float` 相等而不会真的挪窗口，
            #   但任何一次尺寸变化就会把 `_last_float` 清掉 → 窗口被弹回原处，
            #   手感就是"拖到一半球自己跳回去了"。直接让位最省心。
            if self._dragging:
                return
            x, y = None, None
            margin = int(self.cfg.get("corner_margin", 14))
            top_gap = int(self.cfg.get("top_gap", 92))
            # ★★ 第二十一批：用户**自己拖过**就听用户的（`pos_custom`），
            #   不再按角落吸附 —— 否则他费劲把球挪开，下一秒又被拽回角落，
            #   感受就是"这个球根本拖不动"。菜单「重置位置」清掉这个开关。
            custom = self.cfg.get("pos_custom")
            _xy = self.cfg.get("float_xy")
            if custom and isinstance(_xy, (list, tuple)) and len(_xy) == 2:
                x, y = int(_xy[0]), int(_xy[1])
            if x is None and self.jy_hwnd and user32.IsWindow(self.jy_hwnd) \
                    and not core.is_minimized(self.jy_hwnd):
                rc = wt.RECT()
                if user32.GetWindowRect(self.jy_hwnd, ctypes.byref(rc)):
                    w, h = rc.right - rc.left, rc.bottom - rc.top
                    if w > 200:
                        if rescale:
                            self._maybe_rescale(h)
                        x, y = corner_xy(self.corner,
                                         (rc.left, rc.top, rc.right, rc.bottom),
                                         self.W, self.H, margin, top_gap)
            if x is None:
                # 剪映不在：退到主屏**工作区**的对应角落。
                # ★ 用工作区而不是整屏：任务栏占位时右下角不能压到任务栏上。
                wa = wt.RECT()
                ok = user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
                if ok and (wa.right - wa.left) > 200:
                    box = (wa.left, wa.top, wa.right, wa.bottom)
                else:
                    box = (0, 0, user32.GetSystemMetrics(0) or 1920,
                           user32.GetSystemMetrics(1) or 1080)
                x, y = corner_xy(self.corner, box, self.W, self.H, margin, margin)
            # 夹到虚拟桌面范围内：多显示器可能有负坐标，剪映窗口也可能被拖到屏幕外，
            # 不夹的话按钮会算到看不见的地方（实测出现过 x=-620）。
            try:
                vx = user32.GetSystemMetrics(76)          # SM_XVIRTUALSCREEN
                vw = user32.GetSystemMetrics(78)          # SM_CXVIRTUALSCREEN
                vy = user32.GetSystemMetrics(77)          # SM_YVIRTUALSCREEN
                vh = user32.GetSystemMetrics(79)          # SM_CYVIRTUALSCREEN
                if vw > 0 and vh > 0:
                    x = max(vx, min(int(x), vx + vw - self.W))
                    y = max(vy, min(int(y), vy + vh - self.H))
            except Exception:
                x, y = max(0, int(x)), max(0, int(y))
            # ★★ 再夹一次到**那块屏的工作区**（避开任务栏 / 屏幕外的无边框残留）。
            #   上面那次夹的是"整个虚拟桌面"，对右下角不够：剪映最大化时它的窗口
            #   矩形会越过屏幕边界（本机实测多出 9px），算出来球就压在任务栏上了。
            #   基准取**剪映所在那块显示器**的工作区（拿不到就用伴侣自己的）。
            #   ★★ 第二十一批：用户**自己拖过**（custom）时**跳过这一夹** ——
            #      `monitor_work_area(球自己)` 给的是"球此刻所在那块屏"，
            #      拿它去夹，球就永远出不了这块屏（想拖到另一块屏会被拽回来）。
            #      用户摆的位置只要落在虚拟桌面内就合法（上面那次夹已经保证）。
            if not custom:
                try:
                    _h = self.jy_hwnd if (self.jy_hwnd and user32.IsWindow(self.jy_hwnd)) \
                        else self.my_hwnd
                    _wa = core.monitor_work_area(_h)
                    if _wa:
                        x, y = clamp_box(x, y, self.W, self.H, _wa)
                except Exception:
                    pass
            pos = (int(x), int(y))
            if force or pos != getattr(self, "_last_float", None):
                self._last_float = pos
                self._push_geometry(pos[0], pos[1])
        except Exception as e:
            if not getattr(self, "_float_err_shown", False):
                self._float_err_shown = True
                print(f"[float] 定位异常 {e}", flush=True)

    # ------------------------------------------------------------ 跟随 / 同生共死
    def _jy_pid_cached(self):
        if self.jy_hwnd and user32.IsWindow(self.jy_hwnd):
            try:
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(self.jy_hwnd, ctypes.byref(pid))
                return pid.value
            except Exception:
                return 0
        return 0

    def _should_show(self):
        """「同生共死」判据：只在剪映（含它自己的弹窗）或本程序位于前台时显示。

        例外：正在干活（busy）或刚出结果（hold 未到期）时始终显示，
        这样用户切到别的窗口也能看到进度/结果，不会以为程序没了。
        """
        if not self.cfg.get("follow_foreground", True):
            return True
        if self.state[0] == "busy" or (self._until and time.time() < self._until):
            return True
        try:
            if self.jy_hwnd and user32.IsWindow(self.jy_hwnd) and core.is_minimized(self.jy_hwnd):
                return False
            fg = user32.GetForegroundWindow()
            if not fg:
                return True
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
            if pid.value == os.getpid():
                return True
            jp = self._jy_pid_cached()
            return bool(jp) and pid.value == jp
        except Exception:
            return True

    def _set_shown(self, want):
        """按需显示/隐藏，用 IsWindowVisible 判当前真实状态，避免 30fps 空转 ShowWindow。"""
        try:
            if bool(user32.IsWindowVisible(self.my_hwnd)) == bool(want):
                return
            if want:
                self.root.deiconify()
                self._float_position(force=True)
            else:
                # ★ 球藏起来时，头顶那张卡也得跟着收 —— 否则会出现
                #   "球没了、一张通知孤零零浮在桌面角落"的诡异画面。
                self._drop_card()
                self.root.withdraw()
        except Exception:
            pass

    def _follow_fast(self):
        """高频轻量循环（~30fps）：只做"贴住剪映 + 按前台显隐"。

        原来这两件事都挤在 500ms 的 _follow 里，拖动剪映时按钮就是 2fps 地跳，
        用户评价"卡卡的、不自然"。重量级判断（枚举窗口、找宿主、退出判定）
        仍留在 500ms 的 _follow，这里只做最便宜的 Win32 调用。
        """
        try:
            self._poll_hover()      # ★ 悬停兜底（事件会丢，光标位置不会骗人）
        except Exception:
            pass
        try:
            self._sync_dpi()        # ★ 换显示器 → 换 dpi 缩放（≤2 次/秒，很便宜）
        except Exception:
            pass
        try:
            if not self.embedded:
                if self._should_show():
                    self._set_shown(True)
                    self._float_position()
                else:
                    self._set_shown(False)
        except Exception:
            pass
        self.root.after(33, self._follow_fast)

    def _cycle_corner(self):
        """右上 → 右下 → 左下 → 左上 循环停靠角（默认从**右下**出发）。

        ★ 第十二批修正：两个模式都要重摆。原代码只调 `_reposition`（嵌入模式专属），
          而 `_reposition` 在非嵌入时**第一行就 return** —— 于是默认（独立悬浮）模式下
          点「停靠位置」菜单**完全没反应**，用户以为菜单坏了。
        """
        order = ["tr", "br", "bl", "tl"]
        if self.corner not in order:
            self.corner = "br"
        i = order.index(self.corner)
        self.corner = order[(i + 1) % 4]
        self.cfg["corner"] = self.corner
        core.save_config(self.cfg)
        # 停靠角变了 = 位置**该整体挪**，不能等"位置变了才动"的缓存判定
        self._last_float = None
        self._last_rect = None
        self._reposition(force=True)
        self._float_position(force=True, rescale=False)
        self._sync_menu()

    def _cycle_ball_mode(self):
        """循环切换形态策略：自动收纳 → 一直展开 → 只留小球。

        立即保存并当场重算形态（`_sync_expanded(animate=True)`），让用户点完
        马上看到效果，而不是"改了配置但界面没动、要重启才生效"。
        """
        i = BALL_MODES.index(self._ball_mode)
        self._ball_mode = BALL_MODES[(i + 1) % len(BALL_MODES)]
        self.cfg["ball_mode"] = self._ball_mode
        core.save_config(self.cfg)
        self._sync_expanded(animate=True)
        self._sync_menu()

    def _toggle_auto_draft(self):
        """是否让伴侣自己点开「原草稿」那张卡片。

        ★ 为什么需要点：剪映**没有**任何非鼠标入口能打开指定草稿（快捷键表 91 条
          命令里没有；UIA 控件树是空的；没有 CLI 参数；重启也不会自动回到上次草稿）。
        ★ 怎么点得准：拿**草稿自己的 draft_cover.jpg**（首页卡片缩略图的来源）做
          多尺度匹配，认准了才双击；点之前用 WindowFromPoint 复核坐标上是不是剪映、
          点之后用 `.locked` 验证真的进了目标草稿；**认不准 / 被挡住 → 绝不乱点**，
          退回让你自己点。点完光标还原到原位。
        ★ 2026-09-18 用户定案：**默认关**。自动点实测成功率不稳（同页多张封面
          判别度只有 +0.047），点错草稿必报「媒体格式不支持」，代价太大 ——
          宁可自己点一下卡片换 100% 确定性。关着的时候伴侣**只点名**
          "请打开草稿「X」（必须这个）"，鼠标一下都不动。
        """
        self.cfg["auto_open_draft"] = not self.cfg.get("auto_open_draft", False)
        core.save_config(self.cfg)
        self._sync_menu()
        if self.cfg["auto_open_draft"]:
            self.set_state("busy", "自动打开原草稿已开", sub="认不准就交回你手动点", hold=5)
        else:
            self.set_state("busy", "自动打开原草稿已关", sub="重启后由你自己点卡片", hold=5)

    def _toggle_auto_drag(self):
        """是否让伴侣**自动把预合成文件拖进时间线**。

        ★ 用户实测：剪映**不吃剪贴板**（Ctrl+V 拖不进去），所以只能真鼠标拖。
        ★ 怎么拖得准：资源管理器里按**文件名精确相等**定位条目（防抓到 `.alpha.mp4`
          那个占位）→ 取**图标中心**当起点（条目框中心常落在空白上，会变成框选）→
          分步移到时间线落点 → 用 PrintWindow 抓"时间线那一条带"和拖之前比像素，
          变了才算成功。**认不准 / 被盖住 / 自检没过 → 一律交回你手动拖**，
          绝不假装成功。全程只动光标和鼠标，**一个文件都不碰**。
        ★ 默认开（用户要求"最好能把相应文件自动拖进去"）。
        """
        self.cfg["auto_drag_product"] = not self.cfg.get("auto_drag_product", True)
        core.save_config(self.cfg)
        self._sync_menu()
        if self.cfg["auto_drag_product"]:
            self.set_state("busy", "自动拖进时间线已开", sub="拖不准就交回你手动", hold=5)
        else:
            self.set_state("busy", "自动拖进时间线已关", sub="只打开文件夹选中文件", hold=5)

    def _toggle_folder_tidy(self):
        """`explorer /select` 弹出的文件夹窗口**默认是最大化**，把屏幕盖满很乱。

        开着 = 打开后自动恢复成 1000x620、在该屏居中（用户要"框框规矩点"）；
        关掉 = 回到"打开就行、多大随它"。
        """
        self.cfg["folder_win_tidy"] = not self.cfg.get("folder_win_tidy", True)
        core.save_config(self.cfg)
        self._sync_menu()
        if self.cfg["folder_win_tidy"]:
            self.set_state("busy", "文件夹窗口规整已开", sub="1000x620 居中显示", hold=5)
        else:
            self.set_state("busy", "文件夹窗口规整已关", sub="按系统默认（最大化）", hold=5)

    def _show_bind_help(self):
        hk = core.read_shortcut("precompileCombination", None)
        if hk:
            core.notify_box(
                "无需手动绑定：预合成快捷键已自动配置。\n\n"
                f"当前快捷键：{hk.upper()}\n"
                "（由伴侣自动写入剪映快捷键方案，\n"
                "  如需修改可在剪映设置→快捷键里改，\n"
                "  伴侣会自动跟随）",
                "预合成快捷键")
        else:
            core.notify_box(
                "下次一键导出时，伴侣会自动把预合成快捷键\n"
                "写入剪映的快捷键方案（默认 Alt+H），\n"
                "不需要你手动设置。\n\n"
                "★ 写之前会自动备份你原来的快捷键设置，\n"
                "   想还原就点右键菜单「还原剪映快捷键设置」。",
                "预合成快捷键")

    # ------------------------------------------------------------ 打开文件 / 文件夹
    # ★ 2026-09-19 第十一批（体验）：把"求助路"补全。
    #   失败文案一直在说「把 运行日志.txt 发给开发者」「见安装目录下的使用说明.txt」
    #   「可以手动把备份里的 .json 拷回草稿目录」—— 可是界面上**没有任何地方**
    #   能打开它们。用户被指向一个自己找不到的文件，这条求助路等于断了：
    #   最需要它的时候（出错了）恰好用不上。
    def _reveal(self, path, what, missing_hint=""):
        """用系统默认程序打开文件/文件夹；不存在就如实说，别静默失败。"""
        p = Path(path)
        if not p.exists():
            core.notify_box((f"暂时没有「{what}」：\n{p}\n\n{missing_hint}").strip(),
                            f"打开{what}")
            return False
        try:
            os.startfile(str(p))          # 只打开我们自己的目录（日志/说明/备份）
            return True
        except Exception as e:
            core.notify_box(f"打不开「{what}」：{type(e).__name__}: {e}\n\n{p}",
                            f"打开{what}")
            return False

    def _open_log(self):
        """打开运行日志 —— 出问题时把它整个发给作者就行。"""
        self._reveal(core.config_path().parent / "运行日志.txt", "运行日志",
                     "（还没跑过一键导出，所以还没有日志）")

    def _open_manual(self):
        """打开安装目录下的「使用说明.txt」（绿色版/源码运行可能没有）。"""
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
            else Path(__file__).resolve().parent
        self._reveal(base / "使用说明.txt", "使用说明",
                     "（绿色版或源码运行时可能没带这份文档）")

    def _open_backup(self):
        """打开草稿备份文件夹 —— 自动还原用不上时，用户可以自己把 .json 拷回去。"""
        bk, _dr, n = core.backup_info(self.cfg)
        if bk:
            self._reveal(bk, f"草稿备份（{n} 个元数据文件）")
        else:
            self._reveal(core.backup_root_dir(), "草稿备份文件夹",
                         "（还没跑过一键导出，所以还没有备份）")

    def _first_run_notice(self):
        """第一次运行：弹出「先读我 + 体检结果」。标记写进配置，之后不再自动弹。"""
        self.cfg["_selfcheck_done"] = True
        try:
            core.save_config(self.cfg)
        except Exception:
            pass
        self._first_run = False
        self._selfcheck(first_run=True)

    def _selfcheck(self, first_run=False):
        """★ 环境体检：把"这台机器能不能跑"摊开说清楚（2026-09-18 第九批）。

        为什么要它：作者机器上剪映装好、草稿目录在配置里、快捷键也绑好了，
        点一下就通；**别人拿到的是一台陌生机器**。与其等他一键导出失败看见
        一句看不懂的报错，不如开机就把缺什么列出来。

        ★ 扫草稿目录可能要几秒（限深限时扫描），所以丢后台线程，别卡界面；
          结果走队列回主线程弹窗（tkinter 不能跨线程操作控件）。
        """
        if getattr(self, "_selfchecking", False):
            return
        self._selfchecking = True
        self.set_state("busy", "正在体检…", sub="剪映 / 快捷键 / 草稿 / 备份目录", hold=0)

        def work():
            try:
                # ★★ 第十六批：体检**之前**先替用户把环境认好（用户原话"不要让用户
                #   多余操作"）。安装包装完已经跑过一次 `--autosetup`，这里兜住
                #   两种情况：① 绿色版/直接拷 exe 的（没走安装包）；② 安装包那次
                #   刚好遇到剪映还没装完/没打开过。两处都写了就"幂等无变化"。
                #   ★ 在**工作线程**里跑：它会问进程、读剪映设置、扫几个固定落点，
                #     主线程一卡球就冻住（"点了没反应"的老毛病）。
                try:
                    core.autodetect_env(self.cfg)
                except Exception as e:
                    print(f"[ui] 自动配置失败：{type(e).__name__}: {e}", flush=True)
                rows = core.env_selfcheck(self.cfg)
                text = core.format_selfcheck(rows)
                bad = sum(1 for r in rows if r[0] == "bad")
            except Exception as e:
                text, bad = f"体检没跑起来：{type(e).__name__}: {e}", 0
            if first_run:
                text = ("第一次用，先看这份说明：\n"
                        "① 装好剪映专业版，并打开过一次；\n"
                        "② 打开你要处理的草稿，选中要预合成的内容；\n"
                        "③ 点本按钮的「一键导出」。\n"
                        "   ★ 过程中会**杀掉并重启剪映**、**清空当前时间线**，\n"
                        "     清空前会自动备份草稿，导出完点按钮即可一键还原。\n"
                        "   ★ 详细说明见安装目录下的「使用说明.txt」\n"
                        "     （右键本按钮 →「打开使用说明」也能直接打开）。\n"
                        "\n----------------------------------------\n\n" + text)
            self.q.put(("selfcheck", text, bad > 0))

        threading.Thread(target=work, daemon=True).start()

    def _restore_shortcut_keys(self):
        """把伴侣改过的剪映快捷键设置还原回去（发给别人用时必须有这条路）。"""
        bk = self.cfg.get("_shortcut_backup_dir") or ""
        cur = core.read_shortcut("precompileCombination", "") or "（没有）"
        if not bk:
            core.notify_box(
                "还没备份过剪映快捷键设置，没有可还原的东西。\n\n"
                "（只有伴侣给你自动绑过预合成快捷键时才会备份）\n"
                f"当前预合成快捷键：{cur}",
                "还原剪映快捷键设置")
            return
        if not ask_yes(
                "还原剪映快捷键设置",
                "把剪映的快捷键设置还原成**伴侣改动之前**的样子？\n\n"
                f"备份位置：\n{bk}\n\n"
                "⚠️ 还原后请**完全退出剪映再打开**才会生效。\n"
                "（预合成快捷键会回到「没有」的状态，\n"
                "  下次一键导出时伴侣会重新自动绑定）",
                parent=self.root):
            return
        ok, msg = core.restore_shortcut_config(self.cfg)
        core.notify_box(msg, "还原剪映快捷键设置")
        self.set_state("ok" if ok else "err",
                       "快捷键已还原" if ok else "还原失败",
                       sub="请完全退出剪映再打开" if ok else "详情见弹窗", hold=8)

    def _autorun_enabled(self):
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Run")
            try:
                winreg.QueryValueEx(k, "JianyingCompanion")
                return True
            finally:
                winreg.CloseKey(k)
        except Exception:
            return False

    def _set_autorun(self, enable):
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                               winreg.KEY_SET_VALUE)
            exe = f'"{sys.executable}"'
            if enable:
                winreg.SetValueEx(k, "JianyingCompanion", 0, winreg.REG_SZ, exe)
            else:
                try:
                    winreg.DeleteValue(k, "JianyingCompanion")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(k)
        except Exception:
            pass

    def _toggle_autorun(self):
        self._set_autorun(not self._autorun_enabled())
        self._sync_menu()

    def _sync_menu(self):
        """刷新菜单里的 ✓ 与「停靠位置」。

        ★ 一律走 self._mi 里记下的下标，绝不用 menu.index("中文标签") 反查
        （改名后会抛 TclError，见 __init__ 里的注释）。整个过程包在 try 里：
        菜单刷新失败不该影响主流程。
        """
        names = {"tr": "右上", "br": "右下", "tl": "左上", "bl": "左下"}
        try:
            self.menu.entryconfig(self._mi["startup"], label="开机自启 "
                                  + ("✓" if self._autorun_enabled() else ""))
            self.menu.entryconfig(self._mi["guard"], label="导出拦截 "
                                  + ("✓" if self.cfg.get("export_guard", True) else ""))
            self.menu.entryconfig(self._mi["autorun"], label="弹窗时自动接管 "
                                  + ("✓" if self.cfg.get("guard_auto_run", False) else ""))
            self.menu.entryconfig(self._mi["followfg"], label="跟随剪映显隐 "
                                  + ("✓" if self.cfg.get("follow_foreground", True) else ""))
            self.menu.entryconfig(self._mi["autodraft"], label="自动打开原草稿 "
                                  + ("✓" if self.cfg.get("auto_open_draft", False) else ""))
            self.menu.entryconfig(self._mi["autodrag"], label="自动拖进时间线 "
                                  + ("✓" if self.cfg.get("auto_drag_product", True) else ""))
            self.menu.entryconfig(self._mi["exportwatch"], label="导出守望（导完提醒还原） "
                                  + ("✓" if self.cfg.get("export_watch", True) else ""))
            self.menu.entryconfig(self._mi["fullauto"], label="全自动导出（实验） "
                                  + ("✓" if self.cfg.get("full_auto_export", False) else ""))
            self.menu.entryconfig(self._mi["foldertidy"], label="文件夹窗口规整 "
                                  + ("✓" if self.cfg.get("folder_win_tidy", True) else ""))
            self.menu.entryconfig(self._mi["embed"], label="嵌入剪映界面 "
                                  + ("✓" if self.cfg.get("embed_mode", False) else ""))
            self.menu.entryconfig(self._mi["corner"],
                                  label=f"停靠位置：{names.get(self.corner, '右下')}")
            self.menu.entryconfig(self._mi["ballmode"],
                                  label="悬浮球："
                                  + BALL_MODE_LABEL.get(self._ball_mode, "自动收纳"))
            # ★ 还原入口顺便报一下"有没有备份、是哪份草稿"：没有备份时点了必然失败，
            #   先在菜单上告诉用户，省得他点了才看到报错。
            _bk, _bdr, _bn = core.backup_info(self.cfg)
            if _bn:
                _nm = self.cfg.get("_backup_draft_name") or ""
                # ★ 还原过的备份要标出来：否则用户看不出"这份已经用过了"，
                #   再点一次就会把旧备份再写一遍（盖掉新改动）。
                _done = "　· 已还原过" if core.backup_consumed(self.cfg) else ""
                self.menu.entryconfig(self._mi["restore"],
                                      label=f"还原草稿（回到预合成前）　· {_nm}{_done}")
            else:
                self.menu.entryconfig(self._mi["restore"],
                                      label="还原草稿（回到预合成前）　· 暂无备份")
            # ★ 第十七批：「中止本次流程」只在**真的能中止**时才可点。
            #   能做中止的两种情况：① 有个没被中止过的令牌（流程/启动正在跑）；
            #   ② 正停在「等你导出」（这时"中止"= 放弃本轮并还原）。
            #   ★ 绝不做一个"点了没反应"的死入口 —— 所以不可用时明确置灰。
            _can_abort = (self._cancel is not None and not self._cancel.is_set()) \
                or bool(self._await_restore)
            self.menu.entryconfig(self._mi["abort"],
                                  state=("normal" if _can_abort else "disabled"),
                                  label=("中止本次流程（自动还原）" if _can_abort
                                         else "中止本次流程（当前没在跑）"))
            # ★ 第十四批：菜单上直接报"有没有手动指过路径"——设错了要看得见。
            _jx = str(self.cfg.get("jianying_exe") or "").strip()
            if _jx:
                self.menu.entryconfig(self._mi["jypath"],
                                      label=f"设置剪映路径…　· {os.path.basename(_jx)}")
            else:
                self.menu.entryconfig(self._mi["jypath"], label="设置剪映路径…")
            # ★ 第十五批：草稿目录同理 —— 报出来才看得见"我现在用的是哪一份草稿目录"。
            #   用户抱怨"一键导出说找不到草稿"时，第一眼就该看到这里指到哪儿了。
            #   ★ 不报 `Path(_dr).name`：那个名字人人都是 `com.lveditor.draft`，
            #     报了等于没报。是默认位置就写"默认位置"，否则把**实际路径**摆出来。
            _dr = str(self.cfg.get("draft_root") or "").strip()
            if _dr:
                try:
                    _std = str(core.standard_draft_roots()[0])
                except Exception:
                    _std = ""
                _same = bool(_std) and os.path.normcase(os.path.normpath(_dr)) == \
                    os.path.normcase(os.path.normpath(_std))
                self.menu.entryconfig(self._mi["draftroot"],
                                      label="设置草稿目录…　· "
                                            + ("默认位置" if _same else _dr))
            else:
                self.menu.entryconfig(self._mi["draftroot"], label="设置草稿目录…")
        except Exception as e:
            print(f"[menu] 刷新失败: {e}", flush=True)

    def _quit(self):
        self._stop_export_watch()   # ★ 守望是 daemon，不收也会随进程走；显式停一遍干净
        self._drop_card()           # ★ 第二十一批：头顶的卡片是独立顶层窗，得显式收掉
        try:
            if self.embedded and self.my_hwnd:
                user32.SetParent(self.my_hwnd, None)
        except Exception:
            pass
        self.root.destroy()

    # ------------------------------------------------------------ 导出拦截
    def _toggle_guard(self):
        self.cfg["export_guard"] = not self.cfg.get("export_guard", True)
        core.save_config(self.cfg)
        self._sync_menu()
        if self.cfg["export_guard"]:
            self.set_state("busy", "导出拦截已开", sub="只拦会员弹窗 · 不会自己开跑", hold=4)
        else:
            self.set_state("err", "导出拦截已关", sub="弹窗一律不管", hold=4)

    def _toggle_auto_run(self):
        """弹窗出现时是否自动接管跑流水线。默认关（用户要求：必须自己点）。"""
        self.cfg["guard_auto_run"] = not self.cfg.get("guard_auto_run", False)
        core.save_config(self.cfg)
        self._sync_menu()
        if self.cfg["guard_auto_run"]:
            self.set_state("busy", "自动接管已开", sub="会员弹窗出现时会自动开始", hold=5)
        else:
            self.set_state("busy", "自动接管已关", sub="需要时自己点一键导出", hold=5)

    def _toggle_follow_fg(self):
        """是否只在剪映处于前台时显示按钮（同生共死）。"""
        self.cfg["follow_foreground"] = not self.cfg.get("follow_foreground", True)
        core.save_config(self.cfg)
        self._sync_menu()
        if self.cfg["follow_foreground"]:
            self.set_state("busy", "跟随剪映显隐", sub="切走就隐藏", hold=4)
        else:
            self.set_state("busy", "始终显示", sub="不跟随显隐", hold=4)

    def _watch(self):
        """监控剪映新弹出的顶层窗口：会员提示弹窗 → 关闭（可选自动接管）。

        ★ 2026-09-17 重要修正（bug：用户"没点一键导出，它自己就开始了"）：
        原判据是「有一个 ≥800px 的大窗在场 + 新窗口尺寸落在 250~760 x 150~560」
        就算会员弹窗。而**打开草稿时蹦出的广告弹窗标题也是 "JianyingPro"、
        尺寸正好落在那个区间** → 被判成会员弹窗 → 关闭 + 自动跑完整流水线。
        更糟的是它还会被写进 learned_popups（配置里那条 {"title":"JianyingPro",616x500}
        就是这么来的），于是以后每弹一次广告都命中。

        现在：① 必须标题带会员/付费关键词（GUARD_KEYWORDS）才算；
              ② 广告类新窗口只打印日志，不关闭、不学习；
              ③ 是否"自动接管"由 guard_auto_run 决定，默认**关**。
        流水线运行期间只记录不动手——否则会把预合成流程自己的弹窗掐死。"""
        try:
            tops = core.jianying_toplevels()
            current = {hwnd for hwnd, *_ in tops}
            if self.known_windows is None:
                self.known_windows = current
            else:
                guard_on = self.cfg.get("export_guard", True) and self.state[0] != "busy"
                main = self.jy_hwnd
                if not (main and user32.IsWindow(main)) and tops:
                    main = max(tops, key=lambda t: (t[4][2] - t[4][0]) * (t[4][3] - t[4][1]))[0]
                for hwnd, title, cls, owner, rect in tops:
                    if hwnd in self.known_windows or hwnd == main:
                        continue
                    w, hh = rect[2] - rect[0], rect[3] - rect[1]
                    kind = core.classify_popup(title, w, hh)
                    # ---- 广告：自动跳过（用户要求）----
                    # 只关窗口，**绝不**触发流水线、**绝不**写进学习表；
                    # 忙碌期间也照关（广告挡着会把 Ctrl+N / Ctrl+A 这些按键吃掉）。
                    # 同一个 hwnd 只关一次，避免剪映反复弹导致忙循环。
                    if kind == "ad":
                        # ★ 前台也照关 —— 这里踩过一次坑，别改回去：
                        #   广告抢走前台时，主窗口就再也抢不回前台（日志实测
                        #   `activate 最终结果 => False` → `wait_foreground => False`
                        #   → "清空时间线"整个失败）。而"广告抢焦点"恰恰是**最需要**
                        #   关掉它的场景。所以不能有"前台就不关"这条。
                        #   防误关靠三道护栏：①尺寸白名单（只认实测的三种广告尺寸）
                        #   ②同一个 hwnd 只关一次 ③一分钟内最多关 6 个（防抖循环）。
                        if self.cfg.get("auto_skip_ad", True) \
                                and hwnd not in self._ad_closed:
                            now = time.time()
                            self._ad_times = [t for t in self._ad_times if now - t < 60]
                            if len(self._ad_times) >= 6:
                                print(f"[ad] 一分钟内已关 6 个，疑似抖动 → 本次放过 "
                                      f"{title!r} {w}x{hh}", flush=True)
                                continue
                            self._ad_closed.add(hwnd)
                            self._ad_times.append(now)
                            if len(self._ad_closed) > 64:
                                self._ad_closed.clear()
                                self._ad_times.clear()
                            fg = (user32.GetForegroundWindow() == hwnd)
                            print(f"[ad] 自动跳过广告 {title!r} {w}x{hh} cls={cls} "
                                  f"{'(抢着前台，照关)' if fg else ''}", flush=True)
                            core.close_window(hwnd)
                        continue
                    if not guard_on:
                        # 忙碌/关闭期间：只记录特征，帮助排查，绝不关闭
                        print(f"[watch] 新窗口(不拦截) title={title!r} cls={cls} {w}x{hh}", flush=True)
                        continue
                    if kind == "vip":
                        print(f"[watch] 会员弹窗候选 title={title!r} {w}x{hh}", flush=True)
                        # ★ 记账必须放在 break **之前**（2026-09-18 第十批修的 bug）：
                        #   `break` 会跳过循环后面那句 `self.known_windows = current`，
                        #   于是这一拍发现的新窗口在下一拍（400ms 后）又被当成"新窗口"，
                        #   同一个会员弹窗被**反复入队**。而"忙碌 / 冷却中"时
                        #   `_handle_popup` 是**故意不关窗**的（怕掐死自己流程的弹窗）
                        #   → 重复入队会一直持续下去，白烧 CPU 还把日志刷满。
                        self.known_windows = current
                        self.q.put(("popup", hwnd, title, True))
                        break
                    print(f"[watch] 新窗口(非会员弹窗，只记录不动作) title={title!r} "
                          f"cls={cls} {w}x{hh}", flush=True)
                self.known_windows = current
        except Exception:
            pass
        self.root.after(400, self._watch)

    def _handle_popup(self, hwnd, title, is_vip=False):
        now = time.time()
        cooldown = float(self.cfg.get("guard_cooldown", 90))
        try:
            r = core.window_rect(hwnd)
            w, hh = (r[2] - r[0], r[3] - r[1]) if r else (0, 0)
        except Exception:
            w, hh = 0, 0
        print(f"[guard] 拦截弹窗 hwnd={hwnd} title={title!r} vip={is_vip} {w}x{hh}", flush=True)
        # 忙碌或冷却中：绝不能关——很可能是自己流程的弹窗
        if self.state[0] == "busy" or now - self._last_rescue < cooldown:
            print("[guard] 忙碌/冷却中，跳过关闭", flush=True)
            return
        # 只把「带会员关键词」的弹窗记进学习表。广告弹窗一样会被关掉，
        # 但绝不能进学习表——进去以后每弹一次广告都会命中（这次 bug 的帮凶）。
        if is_vip:
            try:
                entry = {"title": title, "w": w, "h": hh}
                learned = [l for l in self.cfg.get("learned_popups", []) if l != entry]
                learned.append(entry)
                self.cfg["learned_popups"] = learned[-8:]
                core.save_config(self.cfg)
            except Exception:
                pass
        core.close_window(hwnd)
        self._last_rescue = now
        if not (self.jy_hwnd and user32.IsWindow(self.jy_hwnd)):
            win = core.find_jianying(exclude=self.my_hwnd)
            self.jy_hwnd = win[0] if win else None
        if not self.jy_hwnd:
            return
        if not (is_vip and self.cfg.get("guard_auto_run", False)):
            # ★ 默认不自动接管！用户反馈"我没点一键导出，它自己就开始了"。
            #   现在：弹窗关掉 + 提示一下，要不要生成由用户点按钮决定。
            self.set_state("busy", "已拦住会员弹窗", sub="要生成就点一下", hold=6)
            print("[guard] guard_auto_run=关 → 只拦截，不自动开始（等用户点按钮）", flush=True)
            return
        self.set_state("busy", "自动导出中…", "已接管 · 正在渲染成品")
        threading.Thread(target=core.run_pipeline, daemon=True,
                         args=(self.cfg, self._on_status,
                               lambda ok, msg: self._on_done(ok, msg, rescue=True), True)).start()

    def _pump(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "status":
                    _, kind, text, pct, step = item[:5]
                    hint = item[5] if len(item) > 5 else None
                    # ★ 把"第几步 / 共几步 / 百分比"拼进副标题，并画底部进度条。
                    #   别人第一次用是个黑盒（中间要等 20s~2min 渲染），
                    #   有这条才知道"在干活"还是"死了"。
                    # ★ 第十三批：内核可以给一条**自己的副文案**（"怎么做/为什么"）。
                    #   给了就用它的 —— 一句话挤不进"你要做什么 + 为什么"两件事，
                    #   分两层才说得清（进度百分比在底部进度条上照样看得到）。
                    sub = hint or None
                    if step:
                        n, _name, lo, _hi = core.stage_bounds(step)
                        if pct is None:
                            pct = lo          # 没给细进度就取该阶段起点
                        if not sub:
                            sub = f"第 {n}/{core.PIPELINE_TOTAL_STEPS} 步 · {pct}%"
                    self.set_state(kind if kind != "idle" else "busy", text, sub,
                                   hold=0, pct=pct, step=step)
                elif item[0] == "selfcheck":
                    _, text, has_bad = item
                    self._selfchecking = False
                    if has_bad:
                        self.set_state("err", "体检没过",
                                       sub="右键「环境体检」可再看", hold=10)
                    else:
                        self.set_state("ok", "体检通过", sub="可以开始用了", hold=8)
                    show_info("剪映伴侣 · 环境体检", text, parent=self.root)
                elif item[0] == "popup":
                    _, hwnd, title, is_vip = item
                    self._handle_popup(hwnd, title, is_vip)
                elif item[0] == "export_done":
                    # ★★ 第十八批（v1.1.0 导出守望）：剪映的导出对话框"出现→消失"了。
                    #   消失 = 导出流程走完**或被用户取消** —— 分不清，所以只提醒、
                    #   绝不自动动手；点不点还原由用户决定。
                    #   ★ 只在确实还停在「等你导出」时才说话：如果用户已经自己
                    #     还原了（_await_restore=False），这条提醒就是噪音，直接丢。
                    if self._await_restore and self._cancel is None:
                        self.set_state(
                            "ask", "检测到导出完成",
                            sub="导出窗口关了 · 左键一键还原草稿", hold=12)
                        # ★ 第二十一批：这条是**主动提醒**（用户没在等，是我们在看），
                        #   最适合走"头顶小卡片"——球那时往往收成一个小圆，
                        #   光靠球上那两行字容易错过。
                        self.notify("导出完成了", "左键点球 → 还原草稿（回到预合成前）",
                                    kind="ask", hold=6.0)
                elif item[0] == "full_auto_done":
                    # ★★ 第二十批（v1.3.0 实验）：全自动走通了 → 立刻还原草稿，
                    #   一条龙闭环。注意**不能**走 _restore_draft —— 它开头的
                    #   busy 护栏会把"全自动导出中…"这个状态挡回去（本轮踩过）。
                    self._start_restore()
                elif item[0] == "full_auto_fail":
                    # 没走通（或用户中止）→ 退回手动。等待态原样保留：
                    # 球还在「等你导出」，手动导出的路一直是通的。
                    if self._await_restore:
                        self.set_state("ok", "等你导出", sub=item[1], hold=10)
                elif item[0] == "done":
                    _, ok, msg, rescue = item
                    # ★ 第十七批：一轮任务结束（不管成功/失败/中止）就把令牌撤掉。
                    #   留着它会连累下一轮 —— 已 set 的令牌会让新一轮"刚起步就中止"。
                    self._cancel = None
                    if ok:
                        if rescue == "restore":
                            self._await_restore = False
                            self.set_state("ok", "已还原", sub="草稿已回到预合成前", hold=6)
                        elif rescue == "abort":
                            # ★★ 第十七批「暂终止」的收尾。
                            #   必须和"跑完了、等你导出"（下面 else 那支）分开：
                            #   两者都是 ok=True，混在一起就会出现
                            #   **用户明明喊了停、界面却说"接下来交给你拖"** 的误报。
                            self._await_restore = False
                            self.set_state("ok", "已中止", sub="草稿已还原到预合成前",
                                           hold=10)
                        elif rescue == "batch":
                            # ★★ 第十九批：批量收尾 —— 每份的就绪/失败明细在弹窗里，
                            #   球上只放一句结论（别把 8 行明细塞进球的两行文案）。
                            self._await_restore = False
                            self.set_state("ok", "批量完成",
                                           sub="明细见弹窗 · 每份打开即可导出", hold=10)
                        elif rescue == "launch":
                            # ★ 第十四批：启动超时不当"成功"。正常路径下 ok 是 False 走 else；
                            #   这一支只是防御（万一将来有"启动成功但流程没跑"的 ok=True 路径）。
                            self.set_state("ok", "剪映已启动", sub="再点一次「一键导出」", hold=8)
                        elif rescue:
                            self.set_state("ok", "已就位", sub="文件已选中 · 按提示拖入导出", hold=6)
                        else:
                            # ★ 常驻（hold=0 = 不自动回 idle）：把"等你导出 + 导出后点
                            #   还原草稿"一直写在按钮上。用户导完一眼就知道下一步做什么，
                            #   不用去回忆那个一闪而过的弹窗说了什么。
                            #   他在这个状态下左键点按钮 = 发还原指令（见 _click）。
                            self._await_restore = True
                            # ★★ 第十八批：从这一刻起守望剪映的导出对话框 ——
                            #   见到"出现→消失"就主动提醒"可以还原了"，
                            #   把"导完还得记得回来"这段纯记忆的人工步骤消掉。
                            self._start_export_watch()
                            # ★★ 第二十批（v1.3.0 实验）：开了全自动 → 接着把
                            #   "拖入 → 导出 → 守完成 → 自动还原"也走完。
                            #   稍等 2.5s 是给"文件夹弹出+选中"留出稳定时间。
                            if self.cfg.get("full_auto_export"):
                                self.root.after(2500, self._run_full_auto)
                            self.set_state("ok", "等你导出",
                                           sub="导出完点我 → 还原草稿（回到预合成前）",
                                           hold=0)
                        # ★★ 第二十一批：收尾提示从"系统 MessageBox"改成
                        #   球**头顶浮出的小卡片**（用户要的形态）。
                        #   文案里第一行当标题、其余当正文，长句会被折行。
                        _t, _, _m = msg.partition("\n")
                        self.notify(_t or "已完成", _m.strip(), kind="ok", hold=5.0)
                    else:
                        if rescue == "restore":
                            self.set_state("err", "未还原", sub="右键「还原草稿」可重试", hold=8)
                        elif rescue == "abort":
                            # ★ 中止了、但自动还原没做成（多半是这轮没备份）。
                            #   如实报 err，别让用户以为"已经收拾干净了"。
                            self._await_restore = False
                            self.set_state("err", "已中止 · 未还原",
                                           sub="右键「打开草稿备份文件夹」看看", hold=12)
                        elif rescue == "launch":
                            # ★ 第十四批：剪映没能在 LAUNCH_WAIT_SECS 内起来。
                            #   不能报"未完成/右键看绑定说明"（用户已经做对了，只是慢），
                            #   也不能说"已就位"。直说：自己开、开完再点我一次。
                            self.set_state("err", "剪映没起来",
                                           sub="手动打开后，再点一次「一键导出」", hold=12)
                        else:
                            self.set_state("err", "未完成", sub="右键查看绑定说明", hold=8)
                        _t, _, _m = msg.partition("\n")
                        self.notify(_t or "未完成", _m.strip(), kind="err", hold=7.0)
                    # ★ 第十七批：任务结束后重刷一次菜单 —— 「中止本次流程」该由
                    #   `_sync_menu` 依据"现在还能不能中止"置灰/点亮。
                    self._sync_menu()
        except queue.Empty:
            pass
        except Exception as _e:
            # ★★ 2026-09-19 第十四批（用户"继续优化"）：这里原来**只兜 queue.Empty**。
            #   而重注册 `self.root.after(120, self._pump)` 在函数**最后一行** ——
            #   于是队列里任何一条畸形/意外的消息（解包失败、set_state 里抛异常…）
            #   都会让异常穿出 `_pump`，**整条 pump 链从此死掉**：
            #   状态不再更新、悬停不再收起、弹窗不再处理 ——
            #   用户看到的就是"点了没反应"的死球，日志上还什么都看不出来。
            #   （和 `_watch` 那条"break 别改 return"是同一类事故。）
            #   现在：异常照旧不静默（打一行进日志），但**循环必须活下来**。
            print(f"[ui] 处理事件失败: {type(_e).__name__}: {_e}", flush=True)
        finally:
            # ★ 这三件事一律放 `finally`：**保证**每隔 120ms 都会重新排一次自己。
            #   延迟收起（第十二批 · 悬浮球）：鼠标离开后**不立刻**收，等满 BALL_LEAVE_MS。
            #   为什么不能在 <Leave> 里当场收：展开会让窗口变宽，tk 有时会在变宽那一瞬
            #   补一个"假的" <Leave> → 当场收起 → 窗口变窄 → 鼠标又落回球上 → 展开……
            #   肉眼看到的就是小球疯狂抖动。所以只记时间戳，改在这里按"时间 + 真没悬停"
            #   两个条件判定；期间鼠标回来了 `_on_enter` 会把 `_leave_at` 清 0，自动放弃收起。
            try:
                if self._expanded and not self._hover and self._leave_at \
                        and (time.time() - self._leave_at) * 1000.0 >= BALL_LEAVE_MS:
                    self._leave_at = 0.0
                    self._sync_expanded()
            except Exception:
                pass
            try:
                self._back_to_idle()
            except Exception:
                pass
            self.root.after(120, self._pump)

    def _follow(self):
        try:
            if self.embedded:
                if not user32.IsWindow(self.jy_hwnd) or user32.GetParent(self.my_hwnd) != self.jy_hwnd:
                    self._detach()
                    return
                self._reposition()
            else:
                win = core.find_jianying(exclude=self.my_hwnd)
                self.jy_hwnd = win[0] if win else None
                if (self.cfg.get("embed_mode", False) and win
                        and user32.IsWindowVisible(self.jy_hwnd)
                        and not core.is_minimized(self.jy_hwnd)):
                    self._try_embed()
                elif not self.embedded:
                    self._float_position()

            # ---- 同生共死（退出侧）：剪映彻底退出 → 伴侣随之退出 ----
            # 注意：显示/隐藏（最小化、切到别的程序）已交给 33ms 的 _follow_fast，
            # 这里只管"剪映真的没了"这一件事，避免两个循环抢窗口的显隐状态。
            if not self.embedded and self.state[0] != "busy":
                if self.jy_hwnd and user32.IsWindow(self.jy_hwnd):
                    self._jy_seen = True
                    self._jy_gone_since = 0.0
                else:
                    if not getattr(self, "_jy_gone_since", 0):
                        self._jy_gone_since = time.time()
                    if getattr(self, "_jy_seen", False) \
                            and time.time() - self._jy_gone_since > 10:
                        print("[exit] 剪映已完全退出，伴侣随之退出", flush=True)
                        self._quit()
                        return
        except Exception:
            pass
        self.root.after(500, self._follow)

    def run(self):
        self.root.mainloop()


def ensure_single_instance():
    """防多开：已有同名进程在跑就直接退出

    ★ 2026-09-18 第九批：安装包里 exe 叫「剪映伴侣.exe」，但**直接跑绿色版**
      （dist\\JianyingCompanion.exe，或者别人拷出去改回英文名）时进程名不含中文，
      旧判断认不出来 → 能开出两个伴侣，两个 watch 线程互相抢前台。
      所以两种名字都认。
    """
    try:
        me = os.getpid()
        ppid = os.getppid()

        def _is_me(nm):
            low = (nm or "").lower()
            return ("剪映伴侣" in (nm or "")) or ("jianyingcompanion" in low)

        # PyInstaller 单文件模式会有父子两个同名进程（bootloader + 实际程序），都要排除
        others = [pid for pid, nm in core.pid_name_map().items()
                  if _is_me(nm) and pid not in (me, ppid)]
        if others:
            print(f"[exit] 已有实例 {others}", flush=True)
            return False
        return True
    except Exception:
        return True


def autosetup_cli():
    """★ 第十六批：`剪映伴侣.exe --autosetup` —— **安装包在装完之后调它一次**。

    用户原话：「我觉得最好是安装时自动帮用户搞的很好，不让用户多余操作」。
    于是装完那一步就替他把环境认好并写进 伴侣配置.json：
    剪映装在哪、草稿放在哪（**直接读剪映自己的设置**，所以用户改过位置也认得出）。

    ★ 这个入口必须**绝不开窗、绝不弹框、绝不抢前台、绝不碰剪映**：
      安装过程里任何一次前台切换都可能让用户以为"装坏了"。
      它只做三件事：认环境 → 写配置 → 把结果落到 运行日志.txt 和
      `_自动配置结果.txt`（安装包读后者来报喜/报忧），然后退出。
    """
    try:
        rep = core.autodetect_env()
    except Exception as e:
        rep = {"jianying_exe": None, "draft_root": None, "changed": [],
               "jianying_how": "", "draft_how": ""}
        print(f"[autosetup] 自动配置失败：{type(e).__name__}: {e}", flush=True)
    msg = core.format_autosetup(rep)
    try:
        core.write_autosetup_result(rep)
    except Exception:
        pass
    # 冻结时 stdout 已经指向 运行日志.txt（见文件头的重定向），直接 print 就是写日志
    for ln in ("[autosetup] " + msg).splitlines():
        print(ln, flush=True)
    return 0


def selftest():
    print("剪映 exe:", core.find_jianying_exe())
    print("剪映窗口:", core.find_jianying())
    root = core.resolve_root(core.load_config())
    print("草稿目录:", root)
    print("selftest 完成")


if __name__ == "__main__":
    # ★ 顺序有讲究：`--autosetup` 必须排在防多开之前 ——
    #   升级安装时旧版伴侣**可能正开着**，防多开会让它直接退出、什么也不做，
    #   那就等于"什么都没自动配"。它只写配置、不开窗口，和运行中的实例不冲突。
    if "--autosetup" in sys.argv:
        sys.exit(autosetup_cli())
    elif "--selftest" in sys.argv:
        selftest()
    elif not ensure_single_instance():
        print("[exit] 已有实例在运行", flush=True)
    else:
        Companion().run()
