#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剪映伴侣核心逻辑（无 GUI），供界面与命令行共同使用。"""

import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab, ImageStat

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# 64 位句柄必须显式声明类型，否则被截断
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = ctypes.c_int
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.OpenClipboard.argtypes = [wt.HWND]
user32.CreateWindowExW.restype = wt.HWND
user32.SendInput.restype = ctypes.c_uint
user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
user32.VkKeyScanW.restype = ctypes.c_short

MEDIA_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".ts"}

# ★★ 备份/还原（2026-09-18 用户批准的「解禁」范围）：
#   只备份**草稿的元数据 .json**（时间线怎么排的、有哪些片段），写回时也只写这些
#   `.json`。产物文件（`Resources\combination\<GUID>_video.mp4`）**全程一个字节都不碰**——
#   这条红线没变，只是把「草稿元数据」从禁令里放出来。
BACKUP_ROOT_NAME = "剪映预合成导出"
BACKUP_SUBDIR = "_备份"
# 备份时**必须跳过**的目录名（小写比较）：Resources 里放的是媒体产物，不备份也不写回。
BACKUP_SKIP_DIRS = {"resources", ".recycle_bin", ".backup", "cache", "temp"}

# GUI 打包（console=False）时父进程无控制台，spawn 控制台程序会闪黑窗，统一抑制
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ================================================================ 日志
_LOG_PATH = None


def _log_path():
    global _LOG_PATH
    if _LOG_PATH is None:
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path(__file__).resolve().parent
        _LOG_PATH = base / "运行日志.txt"
    return _LOG_PATH


def _plog(msg):
    line = f"[pipeline {datetime.now():%H:%M:%S}] {msg}"
    # 只走 print：GUI 冻结时 stdout 已重定向到 运行日志.txt，
    # 再直接写文件会导致每行重复两遍。
    try:
        print(line, flush=True)
    except Exception:
        pass


# ================================================================ 中止（第十七批）
class PipelineCancelled(Exception):
    """用户主动「暂终止」流程 —— **不是故障**。

    ★ 为什么要单独一个异常（2026-09-19 第十七批）：
      流程里到处是 `return`（失败）和 `except Exception`（故障）。用户要求
      「可以暂终止，终止时自动还原」——中止要走的收尾（**自动还原草稿**）
      和"失败"完全不同，混在 return 里必然漏掉某条分支。用一个专门的异常
      在检查点抛出，外层 `except PipelineCancelled` 一处收口，就不会有
      "某条 return 把中止当失败吞了、还原没做"这种事。
    """


def _cancelled(cancel):
    """把 `cancel` 统一判成 bool。三种写法都吃：

      · None                —— 不支持中止（老调用点，行为完全不变）
      · threading.Event     —— 主用（`is_set()`）
      · 任意无参可调用对象   —— 测试里写 `lambda: True` 最省事

    ★ 判错一律当"**没**中止"：宁可多跑一步，也不能因为探针自己坏掉就把
      用户的流程误杀在半路。
    """
    if cancel is None:
        return False
    try:
        if hasattr(cancel, "is_set"):
            return bool(cancel.is_set())
        return bool(cancel())
    except Exception:
        return False


def is_cancelled(cancel):
    """`_cancelled` 的**公开别名**（第十七批）。

    GUI 判"用户是不是按了暂终止"要走这个 —— 界面代码不该去碰 `_` 开头的名字。
    """
    return _cancelled(cancel)


# ================================================================ 配置
def default_config():
    return {
        "precomp_hotkey": "alt+h",
        # ★★ 流程（2026-09-18 用户定稿，**只此一套，禁止任何额外动作**）：
        #   ⓪ ★ 预合成**之前**先把草稿的**元数据 json** 备份到桌面（还原要用）；
        #   ① Ctrl+A 全选 → Alt+G 建复合片段 → Alt+H 预合成；
        #   ② ★ 让剪映「返回草稿页」保存一次草稿（**不能跳过**）。
        #      剪映只有在草稿被保存后，才会把「这个 GUID 是预合成产物」写进
        #      草稿元数据里的 `combination_id`。不保存就杀进程 → 登记缺失 →
        #      重开后剪映把加密缓存**当普通视频**解析 → 「媒体格式不支持」。
        #      （实测：三个草稿里最新产物的 GUID 在元数据里出现 0 次，
        #        因为它们最后一次落盘时间都**早于**产物生成时间。）
        #   ③ 大退剪映（结束进程）；
        #   ④ 重开剪映、回到草稿编辑页；
        #   ⑤ 打开并选中**草稿自己的** `Resources\combination\<GUID>_video.mp4`
        #     （原位：不复制、不改名、不改写文件内容）；
        #   ⑥ 把原时间线内容**整条清空**（Ctrl+A → Del，真删）→ 把该文件拖进时间线
        #     → 用户正常导出（不再提示会员）。
        #   ⑦ 导出后用户点「还原草稿」：把 ⓪ 的备份**写回**草稿目录 → 回到预合成前。
        #   ★ 红线（两层）：
        #     · **产物文件**（`Resources\combination\<GUID>_video.mp4`）绝对禁地——
        #       禁止任何解码 / 转码 / 解密 / 改写 / 删除，全程一个字节不动；
        #     · **草稿元数据 .json** 的备份与写回经用户明确批准（还原功能需要），
        #       写入严格限定在草稿目录内的 `.json`。
        "select_all_hotkey": "ctrl+a",
        "do_select_all": True,
        "do_combination": True,
        "do_precomp": True,
        "draft_root": "",
        # ★ 2026-09-19 第十四批：用户手动指定的剪映 exe（空 = 自动找）。
        #   绿色版 / 自定义安装 / 装在别的盘时，"问进程 + 常见落点"都命中不了，
        #   必须有一条让用户自己指的路（菜单「设置剪映路径…」）。
        "jianying_exe": "",
        "launch_on_start": True,
        "export_guard": True,
        # ★ 弹窗出现时**是否自动接管跑流水线**。默认关！必须用户自己点。
        #   之前是"只要有新弹窗就自动开跑"，用户反馈「我没点一键导出，它自己就开始了」。
        "guard_auto_run": False,
        # ★ 遇到剪映广告弹窗（标题 "JianyingPro"、尺寸白名单内）自动关闭。
        #   用户明确要求"遇见广告最好能自动跳过"。广告只关不跑流水线、不写学习表。
        "auto_skip_ad": True,
        # ★ 快捷键路线失败时，是否允许退化成"用鼠标点坐标"兜底。
        #   默认关：用户明确要求尽量别动鼠标；宁可报错让用户手动做一步，
        #   也不要在用户屏幕上乱点（点错草稿卡片的代价很大）。
        "allow_mouse_fallback": False,
        # ★ 窗口激活类鼠标：点任务栏上的剪映按钮把窗口带到前台。
        #   不是对内容的操作（点错只是切错窗口），所以默认开；关掉后 activate()
        #   只剩 ALT 技巧 + SwitchToThisWindow，都不行就如实失败、交回用户手动点一下。
        "allow_taskbar_click": True,
        # ★ 教程第②③步：预合成后「大退剪映（杀进程）→ 重开并回到草稿编辑页」，默认开。
        "prekill_restart": True,
        # ★★ 「自动打开原草稿」——**默认关**（2026-09-18 用户定案："那我让用户自己点"）。
        #   为什么曾经想做：剪映没有任何非鼠标入口能打开指定草稿（快捷键表 91 条命令
        #   里没有；UIA 控件树是空的；没有 CLI 参数；重启后也不会自动回到上次草稿）。
        #   实现过一版（封面匹配 + 显示顺序槽位 + `.locked` 验证，认不准就交回手动），
        #   实测能用但**成功率不稳**（同页多张封面判别度只有 +0.047），用户权衡后决定
        #   **宁可自己点一下**，换 100% 确定性 —— 点错草稿的代价是必报「媒体格式不支持」，
        #   比多动一下鼠标贵得多。
        #   ★ 默认关 = 伴侣只**点名**"请打开草稿「X」（必须这个）"然后等你双击卡片，
        #     一下鼠标都不动。代码全部保留：菜单打开这个开关即回到自动模式
        #     （"回退"=行为回退，不是删代码）。
        "auto_open_draft": False,
        # ★★ 「清掉挡路的模态框」（2026-09-18）。剪映的「链接媒体」这类**主窗口
        #   拥有的模态子对话框**会锁死键盘焦点：它挂着的时候 activate() 恒失败，
        #   Ctrl+Alt+Q / Ctrl+A / Shift+E 全都送不进去，连"认卡片自动开草稿"都
        #   因为激活不了窗口而放弃。所以"要发按键之前"先把它关掉（等价点取消，
        #   保持素材离线，**不碰任何文件**）。关掉这个开关 = 完全不动它。
        "dismiss_modals": True,
        # ★★ 「文件夹窗口规整化」（2026-09-18 用户反馈"文件夹的大小和框框规矩点"）。
        #   `explorer /select` 弹出的资源管理器窗口**默认是最大化**（实测
        #   GetWindowPlacement.showCmd=3、rect = 整块屏），把主屏整个盖住、很乱。
        #   打开后主动 SW_RESTORE → 定成 1000x620 → 在该屏居中 → 提到前台。
        #   关掉它 = 回到"打开就行、多大随它"的老行为。
        "folder_win_tidy": True,
        # ★★ 「自动把预合成文件拖进时间线」——**默认关**（2026-09-18 用户定案：
        #   "不要自动拖动了，让用户手动拖，然后等待用户还原指令"）。
        #   从"默认开"回退成"默认关"：拖拽虽然实测能成（2 次真机验收都过了），
        #   但它要动用户的**真鼠标 + 真窗口层级**，还得先清空时间线才有意义；
        #   用户权衡后宁可自己拖这一下，换"屏幕上不会被程序抢鼠标"的确定性。
        #   ★ 代码**全部保留**（"回退"=行为回退，不是删代码）：菜单「自动拖进时间线」
        #     勾上就回到全自动模式 —— 实现见 `drag_file_into_jianying`：
        #     资源管理器里按文件名精确定位 → 图标中心按下 → 分步移到时间线落点
        #     → 松开 → PrintWindow 比时间线像素自检（≥1.5 才算成功）。
        #     注意 **Ctrl+V 在剪映里行不通**（用户实测），只能真拖。
        #   ★ 关掉时的行为：仍然 `open_folder_and_select` 打开并选中那个文件、
        #     把文件夹摆到剪映那块屏，然后**只提示**"请手动拖入 + 拖前按 Home 归零"，
        #     之后**什么都不做，等用户导出完自己点「还原草稿」**。
        #   ★ 为什么拖前要按 Home（真机实测）：往时间线空白处丢文件是按**播放头**
        #     落位的，不是按鼠标点；播放头在 17.28s 时新片段就落在 17.28s，
        #     导出总长直接多出一截、前面全黑。
        "auto_drag_product": False,
        "guard_cooldown": 90,
        "embed_mode": False,
        # ★★ 第十二批（2026-09-19 悬浮球）：用户原话「做成悬浮球样式，不然会挡住
        #   用户操作，**自动依附右下角**，加点流畅样式」。
        #   · ball_mode：形态策略。auto = 平时是球、悬停/需要你动手时自己长成横条；
        #     always_pill = 回到旧观感（恒展开）；ball_only = 永远只留球。
        #   · corner：停靠角。**默认右下**（用户指定）。右下角离剪映的时间线顶端、
        #     导出按钮、预览区都最远，46px 的球压不到任何要点的东西。
        #   · corner_margin / top_gap：贴边留白。top_gap 只对**上角**生效
        #     （剪映顶部有工具栏 + 导出按钮，贴死会被压住）。
        "ball_mode": "auto",
        "corner": "br",
        "corner_margin": 14,
        "top_gap": 92,
        # ★★ 第十八批（2026-09-20 导出守望）：流程跑到「等你导出」之后，用户要自己在
        #   剪映里导出，**导完还得记得回来点还原** —— 这是最后一段纯靠记忆的人工步骤。
        #   export_watch 开着 = 伴侣在后台**只观察**剪映的导出对话框（出现 → 消失），
        #   消失即"导出流程走完了（或被取消）"，主动把球弹出来提醒"可以一键还原了"。
        #   ★ 只观察、不动手：绝不因为"对话框消失了"就自动去杀剪映/写草稿 ——
        #     对话框消失也可能是用户取消了导出，自动还原等于猜。
        #   ★ 失败开放：枚举窗口出任何异常都当"没看见"，宁可漏提醒一次。
        "export_watch": True,
        # ★★ 第二十批（v1.3.0 实验 · 全自动导出）：默认**关**。开了 = 授权伴侣在
        #   「等你导出」之后自动拖入 → 发导出键（Ctrl+E）→ 回车确认 → 守望完成 →
        #   自动还原。导出对话框不是我们画的，所以每一步都有"交回手动"的退路。
        "full_auto_export": False,
    }


def config_path():
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent
    return base / "伴侣配置.json"


def load_config():
    cfg = default_config()
    try:
        if config_path().exists():
            cfg.update(json.loads(config_path().read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- 弹窗归类
# 会员/付费类弹窗的关键词。剪映的**广告**弹窗标题也叫 "JianyingPro"，
# 光靠标题分不出会员弹窗还是广告，所以关键词这条要单独判。
GUARD_KEYWORDS = ("会员", "VIP", "vip", "开通", "付费", "权益", "试看", "去水印", "升级")
AD_TITLE = "JianyingPro"
# ★ 广告用**实测白名单 + 容差**判，不用"尺寸区间"。
#   理由（来自运行日志 2026-09-17）：标题同为 "JianyingPro"、窗口类同为
#   Qt622QWindowToolSaveBits 的窗口里，除了真广告还有一批**正常工具窗**——
#   480x125 / 804x420 / 820x420 / 1920x1032 / 168x150 / 192x210 等等。
#   真广告实测只有三种尺寸：652x417、616x500、464x402。
#   区间判据（比如 400~900 x 250~700）会把 804x420 / 820x420 一起关掉，
#   而那正是"新建草稿"时会出现的窗口 —— 有干扰流程的风险。
#   白名单宁缺毋滥：漏掉一个新尺寸的广告只是没自动关（用户报上来再加），
#   误关一个正常工具窗却可能打断用户正在做的编辑。
AD_SIZES = ((652, 417), (616, 500), (464, 402))
AD_SIZE_TOL = 40          # DPI / 渲染取整可能差几像素

# ★★ 「挡路的模态对话框」白名单（2026-09-18 实测）。
#
#   为什么需要它：剪映的模态框是**主窗口拥有的子窗口**（GetWindow(hwnd, GW_OWNER)
#   == 主窗口 hwnd）。它一出现就**抢走键盘焦点**，后果是——
#     · `activate()` 反复失败（日志里的 "activate 最终结果 => False"）；
#     · `_wait_foreground()` 超时 → Ctrl+Alt+Q / Ctrl+A / Shift+E 全部送不进去；
#     · 自动打开草稿因为"激活不了窗口"而放弃（认卡片那步根本没机会跑）。
#   实测（运行日志 14:13）：`链接媒体 cls=Qt622QWindowIcon 556x455 owner=8398360`
#   挂着的时候，首页根本回不去，`open_draft_by_card()` 只能返回 None。
#
#   「链接媒体」= "有素材找不到，要不要重新链接？" 的提示框。按 X / 取消 = 保持素材
#   离线继续打开草稿，**不写、不删、不改任何文件**，是个纯开关式提示。
#
#   ★ 白名单宁缺毋滥（沿用 AD_SIZES 的思路）：只关这一种。导出设置、预合成进度、
#     保存对话框…… 一律不动 —— 那些用户可能正要用。
MODAL_TITLES = ("链接媒体",)


def _is_ad_size(w, h):
    return any(abs(w - a) <= AD_SIZE_TOL and abs(h - b) <= AD_SIZE_TOL
               for a, b in AD_SIZES)


def is_dismissable_modal(title, owner=0):
    """是不是「该主动关掉的剪映模态对话框」。

    两个条件同时满足才算（**纯函数，好测**）：
      ① 有 owner —— 是主窗口的子对话框（真正的模态框）；
         没有 owner 的独立顶层窗是广告/工具窗那一类，走 `classify_popup` 的路子。
      ② 标题在白名单 `MODAL_TITLES` 里。
    """
    return bool(owner) and (title or "") in MODAL_TITLES


def _has_guard_kw(title):
    t = (title or "").lower()
    return any(k.lower() in t for k in GUARD_KEYWORDS)


def classify_popup(title, w=0, h=0):
    """给剪映新弹出的顶层窗口分类：'vip' / 'ad' / None。

    - 'vip'：标题带会员/付费关键词 → 可以拦截，是否自动接管由 guard_auto_run 决定。
    - 'ad' ：剪映的启动/插播广告（标题就是 "JianyingPro"，尺寸命中 AD_SIZES 白名单）
             → 只关掉（用户明确要求"遇见广告自动跳过"），**永不**触发流水线、**永不**学习。
    - None ：无关窗口，一律不动。

    ★ 绝不按"大窗在场 + 尺寸落区间"来判断会员弹窗。旧的判据就是这样，结果打开草稿时
      蹦出的广告、以及剪映真正的「链接媒体」对话框都被当成会员弹窗关掉，并且会被写进
      学习表 → 之后每弹一次广告都误触发"自动开跑"（用户报的 bug）。
      尺寸现在只用于识别**广告**这一类，而且必须是白名单里的三种尺寸，且标题精确等于
      "JianyingPro"。
    """
    if _has_guard_kw(title):
        return "vip"
    if (title or "") == AD_TITLE and _is_ad_size(w, h):
        return "ad"
    return None


def close_jy_popups(close_vip=False):
    """关掉剪映的弹窗（广告一律关；vip 类只在 close_vip=True 时关）。

    返回关闭个数。调用方一般在"要发按键之前"调一次，免得弹窗抢焦点把按键吃掉。
    """
    closed = 0
    for hwnd, title, cls, owner, r in jianying_toplevels():
        w, h = r[2] - r[0], r[3] - r[1]
        kind = classify_popup(title, w, h)
        if kind == "ad" or (kind == "vip" and close_vip):
            close_window(hwnd)
            closed += 1
            _plog(f"关闭弹窗[{kind}] {title!r} {w}x{h} cls={cls}")
    if closed:
        time.sleep(1.0)
    return closed


def dismiss_jy_modals(enabled=None):
    """关掉**会抢键盘焦点**的剪映模态对话框（白名单，见 `MODAL_TITLES`）。

    ★ 和 `close_jy_popups` 是两回事，**故意不合并**：
      · `close_jy_popups` 处理的是**抢前台的独立顶层窗**（广告），常驻 watch
        线程也在用它，判据是标题 + 尺寸白名单；
      · 这里处理的是**主窗口的模态子对话框**，判据是 owner + 标题白名单。
      混在一起就会重蹈旧覆辙（把「链接媒体」学成会员弹窗 → 误触发自动开跑）。

    ★ **只在这些地方调用**：`_wait_foreground` / `ensure_edit_page` /
      `open_draft_by_card` —— 也就是"我们马上要发按键或点鼠标之前"。
      **不放常驻 watch 线程**：用户手动编辑时可能正想点「链接」去重新链接素材，
      这时候替他把框关掉很烦人。

    关掉 = 等价于用户点 X/取消（保持素材离线继续），不碰任何文件。
    返回关闭个数。
    """
    if enabled is None:
        enabled = _DISMISS_MODALS
    if not enabled:
        return 0
    closed = 0
    for hwnd, title, cls, owner, r in jianying_toplevels():
        if not is_dismissable_modal(title, owner):
            continue
        w, h = r[2] - r[0], r[3] - r[1]
        close_window(hwnd)
        closed += 1
        _plog(f"关闭挡路模态框 {title!r} {w}x{h} cls={cls} owner={owner}"
              "（等价点取消，不碰文件）")
    if closed:
        time.sleep(0.8)
    return closed


# ================================================================ 剪映 exe / 窗口
def _exe_path_of_process(pid):
    """取某进程的可执行文件完整路径。拿不到返回 None。

    ★ 用途（2026-09-18 第八批 · 分发）：剪映可以装在**自定义目录**，
      静态候选路径（LOCALAPPDATA / ProgramFiles）就找不到了。
      这时只要剪映在跑，就能从**它的进程**里问到真实路径 —— 最靠谱的一条。
    """
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = None
    try:
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return None
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            p = Path(buf.value)
            return p if p.exists() else None
    except Exception:
        return None
    finally:
        if h:
            kernel32.CloseHandle(h)
    return None


def _jianying_user_data():
    """剪映的「用户数据」目录（只读，用来**问剪映自己**）。不存在返回 None。"""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    p = Path(local) / "JianyingPro" / "User Data"
    try:
        return p if p.is_dir() else None
    except Exception:
        return None


# 剪映设置文件里记「当前草稿位置」的那个键名。
JY_DRAFT_KEY = "currentCustomDraftPath"

# 自动配置的结果文件名（给安装包读；装完提示里会念给用户听）。
AUTOSETUP_RESULT = "_自动配置结果.txt"


def jianying_setting_draft_path():
    """★ 问剪映自己："你现在的草稿位置是哪儿？"——**只读它的设置文件，一个字都不写**。

    ★ 2026-09-19 第十六批（用户："最好是安装时自动帮用户搞的很好，不让用户多余操作"）。

    来源（本机实测）：
      %LOCALAPPDATA%\\JianyingPro\\User Data\\Config\\globalSetting
      里面有一行 `currentCustomDraftPath=C:\\...\\Projects\\com.lveditor.draft`。
      这就是用户在「剪映 → 设置 → 草稿位置」里改过的那个值 —— 比我们"猜常见落点"
      **权威得多**：用户把草稿挪到 D 盘、改成带空格/中文的目录，这里照样读得到。
      有了它，"装完什么都不用指"才真的成立（光靠猜落点做不到）。

    ★ 红线：**只读**。它是个没有扩展名的纯文本（`键=值` 一行一条）。
      路径在文件里是**双反斜杠字面量**（`C:\\\\Users\\\\...`），要还原成单反斜杠。
    """
    base = _jianying_user_data()
    if not base:
        return None
    for name in ("globalSetting", "GlobalSetting"):
        f = base / "Config" / name
        try:
            if not f.is_file():
                continue
            txt = f.read_bytes().decode("utf-8", "ignore")
        except Exception:
            continue
        for line in txt.splitlines():
            line = line.strip()
            if not line.startswith(JY_DRAFT_KEY + "="):
                continue
            val = line.split("=", 1)[1].strip().strip('"')
            val = val.replace("\\\\", "\\")
            if not val:
                continue
            try:
                p = Path(val)
                if p.is_dir():
                    return p
            except Exception:
                pass
    return None


def find_jianying_exe(cfg=None):
    """找剪映本体的 exe。

    ★ 2026-09-19 第十四批（用户"继续优化" · 分发病因第 1 条"路径假设"）：
      加一条**用户手动指定**的最高优先级。之前只能靠"问运行中的进程 + 几个常见落点"，
      绿色版 / 自定义安装 / 装在别的盘的人**永远找不到**，而且界面上没有任何地方能纠正 ——
      他只能看到"找不到剪映专业版"，然后没有然后了。
      现在菜单「设置剪映路径…」写进 `cfg["jianying_exe"]`，这里最先认它。
    """
    if cfg:
        p = str(cfg.get("jianying_exe") or "").strip()
        if p and Path(p).is_file():
            return Path(p)
    cands = []
    # ① 最靠谱：剪映正在跑，直接问它的进程要路径（自定义安装目录也能命中）
    try:
        for pid, name in pid_name_map().items():
            if name in ("jianyingpro.exe", "capcut.exe"):
                p = _exe_path_of_process(pid)
                if p:
                    cands.append(p)
                    break
    except Exception:
        pass
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        apps = Path(local) / "JianyingPro" / "Apps"
        if apps.is_dir():
            main = apps / "JianyingPro.exe"
            if main.exists():
                cands.append(main)
            vers = sorted([d for d in apps.iterdir() if d.is_dir() and d.name[0].isdigit()],
                          key=lambda d: d.name, reverse=True)
            for d in vers:
                exe = d / "JianyingPro.exe"
                if exe.exists():
                    cands.append(exe)
    pf = Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "JianyingPro" / "JianyingPro.exe"
    if pf.exists():
        cands.append(pf)
    # ② 有些安装器落在 LOCALAPPDATA\Programs 下
    if local:
        lp = Path(local) / "Programs" / "JianyingPro" / "JianyingPro.exe"
        if lp.exists():
            cands.append(lp)
    # 去重（保序）
    seen, out = set(), []
    for c in cands:
        k = str(c).lower()
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out[0] if out else None


def kill_jianying():
    """结束剪映进程（用户的经验：大退后预合成文件才落盘）"""
    try:
        subprocess.run(["taskkill", "/F", "/IM", "JianyingPro.exe"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                       creationflags=_NO_WINDOW)
        subprocess.run(["taskkill", "/F", "/IM", "JianyingProTray.exe"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                       creationflags=_NO_WINDOW)
        return True
    except Exception:
        return False


def wait_new_combo(root, before, timeout=90, status_cb=None, settle=True, cancel=None):
    """等待 combination 目录出现新的媒体文件。

    ★ 2026-09-17 加固（响应"预合成结束太快/杀进程太快"这个疑问）：
      发现候选后**必须先等内容稳定再返回**，绝不把半成品交出去。

      为什么原来的判据不够：原来只比 `mtime > before + 0.5`，也就是
      「文件一出现就返回」。好的一面是剪映渲染时写的是 `..._temp.mp4`、
      正式文件是**改名**来的（见 pick_combo_file 注释），所以出现即完整；
      但"出现"只证明 mtime 变了，**不证明写盘已经结束**——大文件尤其危险
      （实测 31.7MB 的那种缓存）。抢在写完之前归档/交付，剪映就会报
      「媒体格式不支持 / 导入文件损坏」。

      现在：候选文件先过 `wait_file_settled`（比 md5，连续两次一致才算稳）。
      不稳就**不返回**，在外层 deadline 内继续等——宁可多等两三秒，
      也不交付半成品。settle=False 可关掉（只在调试/秒表场景用）。

    ★ 第十七批：`cancel` 一置位立刻返回 None。这是全流程**最长**的一等（最长 120s），
      用户点「暂终止」时最可能正好卡在这里 —— 不能让他再等两分钟才停。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _cancelled(cancel):
            _plog("等待渲染中被中止")
            return None
        for draft, combo, mt in combo_dirs(root):
            key = str(combo)
            f = pick_combo_file(combo)
            if not f:
                continue
            fmt = f.stat().st_mtime
            if key not in before or fmt > before[key] + 0.5:
                if settle:
                    # ★ 内层等待必须**受外层 deadline 约束**：否则"等稳定"会把
                    #   wait_new_combo 的 timeout 整个吃掉（实测要 6s 却等了 43s）。
                    budget = max(2.0, min(45.0, deadline - time.time()))
                    ok = wait_file_settled(f, timeout=budget, status_cb=status_cb,
                                           cancel=cancel)
                    fp = file_fingerprint(f)
                    if not ok:
                        _plog(f"!! 候选 {f.name} 在 {budget:.0f}s 内 content 仍在变，暂不返回"
                              f"（size={fp[0]} md5={fp[2]}）")
                        continue
                    _plog(f"★ 产物已稳定再交付：{f.name} size={fp[0]} md5={fp[2]}")
                return (draft, combo, f)
        if status_cb:
            left = int(deadline - time.time())
            status_cb(f"等待渲染 {left}s", "busy")
        time.sleep(1.0)
    return None


def launch_jianying(cfg=None):
    exe = find_jianying_exe(cfg)
    if not exe:
        return None
    subprocess.Popen([str(exe)], cwd=str(exe.parent))
    return exe


def pid_name_map():
    TH32CS_SNAPPROCESS = 0x2
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap:
        return {}

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    e = PE()
    e.dwSize = ctypes.sizeof(e)
    m = {}
    ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        m[e.th32ProcessID] = e.szExeFile.lower()
        ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    kernel32.CloseHandle(snap)
    return m


def enum_windows():
    out = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(h, _):
        if user32.IsWindowVisible(h):
            n = user32.GetWindowTextLengthW(h)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(h, buf, n + 1)
                out.append((h, buf.value))
        return True

    user32.EnumWindows(proto(cb), 0)
    return out


# 剪映**主窗口**的最小可信尺寸。小于它的都是工具提示 / 拖拽影 / 悬浮工具条
# 这类"小配件窗"，绝不能当成主窗口（详见 find_jianying 的说明）。
# 实测踩到的坑：把 52x19 的 `Qt622QWindowToolTipSaveBits` 当成了剪映。
MAIN_MIN_W, MAIN_MIN_H = 320, 240


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wt.UINT), ("flags", wt.UINT), ("showCmd", wt.UINT),
                ("ptMinPosition", wt.POINT), ("ptMaxPosition", wt.POINT),
                ("rcNormalPosition", wt.RECT)]


def _window_size(hwnd):
    """窗口的**还原尺寸** (w, h)。

    ★ 为什么不能直接用 GetWindowRect：窗口**最小化**时 Windows 会把它的坐标写成
      `-32000,-32000`、尺寸写成一个假的 199x34 —— 实测剪映主窗口最小化后就是
      这个值，于是 `find_jianying` 的尺寸闸把**真正的主窗口**给滤掉了，
      返回 None，整个流程直接"找不到剪映窗口"。
      最小化时改用 `GetWindowPlacement` 的 `rcNormalPosition`（还原后的位置/尺寸）。
    """
    try:
        if user32.IsIconic(hwnd):
            wp = WINDOWPLACEMENT()
            wp.length = ctypes.sizeof(WINDOWPLACEMENT)
            if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
                r = wp.rcNormalPosition
                w, h = r.right - r.left, r.bottom - r.top
                if w > 0 and h > 0:
                    return (w, h)
    except Exception:
        pass
    r = window_rect(hwnd)
    if not r:
        return (0, 0)
    return (r[2] - r[0], r[3] - r[1])


def find_jianying(exclude=0):
    """剪映**主窗口** (hwnd, title)；找不到返回 None。

    ★★ 2026-09-18 修一个实测踩到的坑：剪映除了主窗口还会开出一堆**小配件窗**——
      `Qt622QWindowToolTipSaveBits` 的 52x19 工具提示、`SysDragImage` 的 488x162
      拖拽影、`Qt622QWindowToolSaveBits` 的 480x125 / 502x249 悬浮工具条……
      主窗口在**重建的那一瞬**（切页/换草稿时 Qt 会销毁重建顶层窗）会短暂枚举
      不到，此时旧逻辑只剩那个小窗可挑，就把 **52x19 的工具提示**当成了剪映：
        `find_jianying() -> (4852514, 'JianyingPro')`  ← 实测
      后果特别隐蔽：`activate()` 返回 False、`Ctrl+Alt+Q` 送进空气、
      日志里只看到"停在首页/超时"，根本看不出是**认错窗口**。
      这正是「自动打开草稿」偶发失败的一个真因。

    现在两道闸：
      ① 尺寸闸：小于 `MAIN_MIN_W x MAIN_MIN_H` 的窗口一律不算主窗口
         （剪映主窗口最小也远大于此；工具提示/拖拽影全部被排除）。
      ② 排序闸：可见 > 无 owner（有 owner 的是模态子对话框，选它没意义）
         > QWindowIcon 类 > 面积大。
    """
    procs = pid_name_map()
    pids = {p for p, nm in procs.items() if nm in ("jianyingpro.exe", "capcut.exe")}
    pid_of = wt.DWORD()
    cands = []
    for hwnd, title in enum_windows():
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_of))
        if pid_of.value not in pids:
            continue
        if hwnd == exclude:
            continue
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        cls = buf.value
        w, h = _window_size(hwnd)                 # 最小化时用还原尺寸
        if w < MAIN_MIN_W or h < MAIN_MIN_H:      # ← 闸①：小配件窗不是主窗口
            continue
        owner = user32.GetWindow(hwnd, GW_OWNER)
        cands.append((hwnd, title, w * h, bool(user32.IsWindowVisible(hwnd)), cls, owner))
    # 兜底：标题含剪映的窗口（务必排除自身，否则会把自己当成剪映）；同样过尺寸闸
    if not cands:
        for hwnd, title in enum_windows():
            if hwnd == exclude or ("剪映专业版" not in title and "CapCut" not in title):
                continue
            w, h = _window_size(hwnd)
            if w >= MAIN_MIN_W and h >= MAIN_MIN_H:
                return (hwnd, title)
        return None
    # 闸②：可见 / 无 owner / QWindowIcon / 面积，依次比较
    best = max(cands, key=lambda c: (c[3], c[5] == 0, "QWindowIcon" in c[4], c[2]))
    return (best[0], best[1])


def window_rect(hwnd):
    r = wt.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return (r.left, r.top, r.right, r.bottom)
    return None


def wait_jianying(timeout=8.0, exclude=0, interval=0.6, cancel=None):
    """在 timeout 内反复找剪映主窗口；找到返回 (hwnd, title)，始终没有返回 None。

    ★ 为什么要它（2026-09-18 实测）：剪映**切换页面时会重建主窗口**——
      发完「返回草稿页」(Ctrl+Alt+Q) 的那一瞬，`find_jianying()` 会枚举不到任何
      合格窗口（实测直接返回 None）。这时候如果就下结论"剪映没开"，
      整个流程会误判成失败。所以凡是"单发一次就下判断"的地方都该走这个函数。
      （`find_jianying` 本身保持无副作用、不 sleep，循环由调用方决定时长。）
    ★ 第十七批：`cancel` 一置位就立刻返回 None（中止时别让用户干等满 timeout）。
    """
    t0 = time.time()
    while True:
        w = find_jianying(exclude=exclude)
        if w:
            return w
        if _cancelled(cancel) or time.time() - t0 >= timeout:
            return None
        time.sleep(interval)


GW_OWNER = 4
WM_CLOSE = 0x0010
user32.GetWindow.restype = wt.HWND
user32.GetWindow.argtypes = [wt.HWND, ctypes.c_uint]


def jianying_toplevels():
    """剪映进程的全部可见顶层窗口 [(hwnd, title, classname, owner, (l,t,r,b))]"""
    procs = pid_name_map()
    pids = {p for p, nm in procs.items() if nm in ("jianyingpro.exe", "capcut.exe")}
    pid_of = wt.DWORD()
    out = []
    for hwnd, title in enum_windows():
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_of))
        if pid_of.value not in pids:
            continue
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        owner = user32.GetWindow(hwnd, GW_OWNER)
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        out.append((hwnd, title, buf.value, owner, (r.left, r.top, r.right, r.bottom)))
    return out


def close_window(hwnd):
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


# ================================================================ 导出完成检测
EXPLORER_CLASSES = ("CabinetWClass", "ExploreWClass")
# 规整小窗的目标尺寸（**物理像素**）。1000x620 在 1920x1080 上是个"看着正常"的
# 窗口：一眼能看到整排文件，又不会把整块屏糊住。
# ★ 2026-09-18 用户反馈"文件夹的大小和框框规矩点"：原来 `explorer /select` 出来的
#   窗口**默认是最大化**（实测 GetWindowPlacement.showCmd=3、rect = 整块屏），
#   把主屏整个盖住、看着很乱。这里主动恢复 + 定尺寸 + 居中。
FOLDER_WIN_W, FOLDER_WIN_H = 1000, 620


def explorer_windows():
    """所有可见的资源管理器窗口 [(hwnd, title)]。"""
    out = []
    for hwnd, title in enum_windows():
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, 64)
        if buf.value in EXPLORER_CLASSES and title:
            out.append((hwnd, title))
    return out


def monitor_work_area(hwnd):
    """窗口所在那块显示器的可用区域 (l, t, r, b)，**物理像素**、已避开任务栏。"""
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                    ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]
    try:
        hmon = user32.MonitorFromWindow(hwnd, 2)        # MONITOR_DEFAULTTONEAREST
        if not hmon:
            return None
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return None
        r = mi.rcWork
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


def tidy_explorer_window(hwnd, w=FOLDER_WIN_W, h=FOLDER_WIN_H, near=None):
    """把一个（通常是最大化的）资源管理器窗口恢复成规整小窗，摆到该屏正中。

    ★ `near` = **要"陪着"的那个窗口**（通常传剪映主窗）。传了就把文件夹摆到
      **剪映所在的那块屏**上；不传就留在它自己现在那块屏。
      —— 2026-09-18 实测：`explorer /select` 会把窗口开在**主显示器**，
      而用户实际盯着的是剪映所在的那块（本机 -1920,0），两块屏一人一半 ⇒
      用户根本看不到文件夹，自动拖拽也变成跨屏长距离甩鼠标。
      摆到剪映这块屏上，文件夹和时间线同屏可见，拖拽就是短短一段。
    ★ 尺寸会按该屏可用区域夹一下（小屏 / 缩放下不至于超出屏幕）。
    """
    try:
        ctx = _dpi_aware()
    except Exception:
        ctx = None
    try:
        user32.ShowWindow(hwnd, 9)                      # SW_RESTORE：从最大化恢复
        time.sleep(0.25)
        wa = monitor_work_area(near or hwnd)
        if not wa:
            return False
        l, t, r, b = wa
        ww = max(560, min(w, (r - l) - 80))
        wh = max(400, min(h, (b - t) - 80))
        x = l + ((r - l) - ww) // 2
        y = t + ((b - t) - wh) // 2
        SWP_SHOWWINDOW, SWP_NOOWNERZORDER = 0x0040, 0x0200
        user32.SetWindowPos(hwnd, 0, x, y, ww, wh,
                            SWP_SHOWWINDOW | SWP_NOOWNERZORDER)
        time.sleep(0.2)
        _plog(f"资源管理器窗口已规整 hwnd={hwnd} 目标={ww}x{wh}@{x},{y} "
              f"near={near} 实际={window_rect(hwnd)}")
        return True
    except Exception as e:
        _plog(f"!! 规整文件夹窗口失败：{e}")
        return False
    finally:
        try:
            _dpi_restore(ctx)
        except Exception:
            pass


# ---------------------------------------------------------------- 官方"在文件夹中显示"
# ★ 2026-09-19（用户反馈"弹出文件夹时弹了两次"，实测复现）：
#   真凶是 `explorer /select,"path"` —— **它每次调用都会新开一个窗口**，不做"复用已有
#   窗口"的合并。实测（`_probe_shell_select.py`）：同一个文件连开 3 次 → 屏幕上并排
#   3 个一模一样的文件夹；用户上一轮留下的窗口还在，这一轮又开一个 ⇒ "弹了两次"。
#   ★ 正解换 `shell32.SHOpenFolderAndSelectItems` —— 资源管理器自己右键"在文件夹中
#   显示"走的就是它。实测连调 3 次**始终复用同一个 hwnd**，不会再堆窗口。
#   ⚠️ 两个 API 陷阱（别混用，会崩或泄漏）：
#     · `ILFree` 在 **shell32**（不在 ole32）；
#     · `SHParseDisplayName` 分配的 pidl 要用 **ole32.CoTaskMemFree** 释放。
ole32 = ctypes.WinDLL("ole32")          # ★ 必须在 UIA 那节之前：这里就先要用它
_shell32 = ctypes.WinDLL("shell32")
_shell32.SHParseDisplayName.argtypes = [wt.LPCWSTR, ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_void_p),
                                        wt.DWORD, ctypes.POINTER(wt.DWORD)]
_shell32.SHParseDisplayName.restype = ctypes.c_long
_shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, wt.UINT,
                                                ctypes.POINTER(ctypes.c_void_p),
                                                wt.DWORD]
_shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
_shell32.ILClone.argtypes = [ctypes.c_void_p]
_shell32.ILClone.restype = ctypes.c_void_p
_shell32.ILFindLastID.argtypes = [ctypes.c_void_p]
_shell32.ILFindLastID.restype = ctypes.c_void_p
_shell32.ILRemoveLastID.argtypes = [ctypes.c_void_p]
_shell32.ILRemoveLastID.restype = wt.BOOL
_shell32.ILFree.argtypes = [ctypes.c_void_p]
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]


def select_in_folder(path):
    """用官方 API 打开所在文件夹并选中该文件（**复用已有窗口 → 不会重复弹窗**）。

    成功返回 True；任何一步失败都返回 False，由调用方回退到 `explorer /select`。
    """
    try:
        ole32.CoInitializeEx(None, 2)       # APARTMENTTHREADED；已初始化会返回 S_FALSE
    except Exception:
        pass
    pidl = ctypes.c_void_p()
    try:
        hr = _shell32.SHParseDisplayName(str(path), None, ctypes.byref(pidl), 0, None)
        if hr or not pidl.value:
            _plog(f"!! SHParseDisplayName 失败 hr=0x{hr & 0xFFFFFFFF:08X}：{path}")
            return False
        folder = _shell32.ILClone(pidl)
        if not folder:
            _plog("!! ILClone 失败")
            return False
        try:
            _shell32.ILRemoveLastID(folder)          # 去掉末级 → 得到"所在文件夹"
            child = _shell32.ILFindLastID(pidl)      # 末级 = 要选中的那个文件
            arr = (ctypes.c_void_p * 1)(child)
            hr2 = _shell32.SHOpenFolderAndSelectItems(folder, 1, arr, 0)
        finally:
            _shell32.ILFree(folder)
        if hr2:
            _plog(f"!! SHOpenFolderAndSelectItems 失败 hr=0x{hr2 & 0xFFFFFFFF:08X}")
            return False
        return True
    except Exception as e:
        _plog(f"!! select_in_folder 异常：{e}")
        return False
    finally:
        if pidl.value:
            try:
                ole32.CoTaskMemFree(pidl)
            except Exception:
                pass


def open_folder_and_select(path, tidy=True, folder_name=None, near=None):
    """打开资源管理器并选中该文件；顺手把窗口恢复成规整小窗（默认开）。

    ★ 2026-09-19：改用官方 `SHOpenFolderAndSelectItems`（见 `select_in_folder`）。
      原来用 `explorer /select,"path"`，**每调一次就多开一个窗口**（实测连开 3 次
      → 3 个窗口堆在屏幕上）→ 用户反馈"弹出文件夹时弹了两次"。官方 API 复用窗口。
      命令行那条只留作兜底（API 不可用时），并在日志里注明。

    ★ 窗口**复用**是常态（同一个文件夹已经开着时更是必然）：所以先看"有没有新
      hwnd"，没有就按标题里的文件夹名找回那个已有窗口 → 照样规整 + 置前。

    ⚠️ 剪映的草稿路径里有 "User Data" 这个**带空格**的目录，兜底那条命令必须
      显式带引号 + 走字符串形式传给 Popen（让命令行走原样），否则窗口开不出来。
    """
    try:
        key = folder_name or os.path.basename(os.path.dirname(str(path)))
        before = {h for h, _ in explorer_windows()}
        if not select_in_folder(path):
            _plog("退回 explorer /select 兜底（这条可能多开一个窗口，但至少把文件夹打开）")
            subprocess.Popen(f'explorer /select,"{path}"')
        if not tidy:
            return None
        t0 = time.time()
        target = None
        while time.time() - t0 < 8 and target is None:
            time.sleep(0.4)
            wins = explorer_windows()
            news = [h for h, _ in wins if h not in before]
            named = [h for h, t in wins if h in news and key and key in t]
            if named:
                target = named[0]           # 新窗口 + 标题对得上（最可信）
                break
            if news:
                target = news[0]            # 没标题可认就认新 hwnd（兜底命令的情况）
                break
            for h, title in wins:           # 复用已有窗口：官方 API 不会产生新 hwnd
                if key and key in title:
                    target = h
                    break
        if not target:
            _plog("!! 没认出新开/复用的资源管理器窗口，跳过规整（不影响其它步骤）")
            return None
        tidy_explorer_window(target, near=near)
        _close_duplicate_folder_windows(key, keep=target)
        user32.SetForegroundWindow(target)
        return target
    except Exception as e:
        _plog(f"!! 打开文件夹失败：{e}")
        return None


def _close_duplicate_folder_windows(key, keep, limit=4):
    """关掉"同一个文件夹多开"的多余窗口（用户抱怨"弹了两次"的残留）。

    ★ 只关**伴侣自己规整过的那种窗口**：标题含 `key` **且**尺寸正好是我们设的
      1000x620（±30px）。用户自己拉的窗口、最大化窗口一律不动 —— 宁可少关，
      也不能关掉用户正在用的东西。
    ★ 尺寸比较必须在 `_dpi_aware()` 里做（`GetWindowRect` 随线程 DPI 感知级别变，
      本机 125% 缩放下会读成 800x496）—— 否则这条去重永远命不中。
    """
    if not key:
        return 0
    ctx = _dpi_aware()
    try:
        return _close_dup_win_impl(key, keep, limit)
    finally:
        _dpi_restore(ctx)


def _close_dup_win_impl(key, keep, limit):
    closed = 0
    for h, title in explorer_windows():
        if h == keep or closed >= limit:
            continue
        if key not in title:
            continue
        r = window_rect(h)
        if not r:
            continue
        w, hh = r[2] - r[0], r[3] - r[1]
        if abs(w - FOLDER_WIN_W) > 30 or abs(hh - FOLDER_WIN_H) > 30:
            continue                                  # 不是我们开的窗口，别动
        _plog(f"关掉重复的文件夹窗口 hwnd={h} title={title} size={w}x{hh}")
        close_window(h)
        closed += 1
    return closed


# ================================================================ 导出守望（第十八批 v1.1.0）
# ★ 管的是流程的最后一段人工：「等你导出」之后，用户去剪映里导出，导完还得
#   **记得回来点还原**。守望 = 后台只观察导出对话框"出现 → 消失"，消失了就
#   主动提醒"可以一键还原了"。**只观察、不动手** —— 对话框消失也可能是用户
#   取消了导出，自动替用户还原等于猜，猜错就是把"没导成"的草稿写回去。
EXPORT_TITLE_KW = ("导出", "Export")

# 导出对话框的**可信尺寸带**（物理像素）：比 tooltip/通知气泡大，比主窗小。
# ★ 白名单宁缺毋滥（沿用 AD_SIZES 的思路）：宁可漏检一次，也不要把主窗口 /
#   全屏遮罩误当成导出框（误报 = 用户没导完就被喊"可以还原了"，会出真事）。
EXPORT_DLG_MIN = (320, 200)
EXPORT_DLG_MAX = (1400, 1100)


def find_export_dialog():
    """在剪映的顶层窗口里找"导出对话框"。找到返回 (hwnd, title, (l,t,r,b))，没有返回 None。

    判据（三条同时满足才算）：
      ① 顶层窗口属于剪映进程（jianyingpro.exe / capcut.exe）；
      ② 标题含「导出」/「Export」（剪映的导出对话框标题就是「导出」）；
      ③ 尺寸落在可信带内 —— 排掉 tooltip / 通知气泡 / 主窗 / 全屏遮罩。
    ★ 失败开放：枚举过程出**任何异常**一律返回 None（当轮没看见）。
      守望是"提醒"性质的功能，漏提醒一次的代价 ≈ 0，
      误报一次（没导完就喊还原）的代价是把用户节奏全打乱 —— 所以宁可少报。
    """
    try:
        for hwnd, title, cls, owner, rect in jianying_toplevels():
            if not title:
                continue
            if not any(k in title for k in EXPORT_TITLE_KW):
                continue
            l, t, r, b = rect
            w, h = r - l, b - t
            if w <= 0 or h <= 0:
                continue
            if w < EXPORT_DLG_MIN[0] or h < EXPORT_DLG_MIN[1]:
                continue
            if w > EXPORT_DLG_MAX[0] or h > EXPORT_DLG_MAX[1]:
                continue
            return (hwnd, title, rect)
    except Exception as _e:
        _plog(f"导出守望：枚举窗口失败（按没看见处理）: {type(_e).__name__}: {_e}")
        return None
    return None


def watch_export_dialog(stop, poll=1.0, timeout=6 * 3600):
    """守望循环：给工作线程跑。返回
      "closed"   —— 见到过导出对话框，然后它消失了（导出完成 / 被取消）
      "stopped"  —— stop 事件被置位（正常收摊：开始还原 / 新一轮 / 退出）
      "timeout"  —— 守了 timeout 秒啥也没等到
      "no_jy"    —— 剪映整个退出了（对话框不可能再消失，静默收摊，不算事件）
    ★ stop 必须是 threading.Event（或带 is_set() 的东西）。轮询间隔 poll 秒，
      每一圈都先看 stop —— 用户一动手就能停，绝不拖泥带水。
    ★ 失败开放：find_export_dialog 自己吃异常；这里再兜一层，
      **守望线程绝不能带着异常炸出来**（它是 daemon，炸了也只是日志，但会漏提醒）。
    """
    try:
        seen = False
        deadline = time.time() + timeout
        while not stop.is_set():
            hit = find_export_dialog()
            if hit:
                seen = True
            elif seen:
                return "closed"
            if not _jianying_running():
                return "no_jy"
            if time.time() > deadline:
                return "timeout"
            stop.wait(poll)
        return "stopped"
    except Exception as _e:
        _plog(f"导出守望：循环异常退出（按没看见处理）: {type(_e).__name__}: {_e}")
        return "stopped"


# ================================================================ 批量队列（第十九批 v1.2.0）
# ★ 用户的真痛点：草稿不止一份，每份都要「打开 → 点一键导出 → 等重启 → 等你打开 →
#   等 → 清空 → 导出」来一整遍。批量 = 把**重活**（备份/预合成/登记/清空）逐份自动做完，
#   每份跑完都停在"打开即导出"的就绪态；导出本身仍由用户做（剪映的导出对话框不是
#   我们该替用户按的东西，见 v1.3.0 之前的设计边界）。
def list_batch_candidates(root, limit=8, budget=3.0, max_dirs=4000):
    """有界扫描草稿根目录，列出「带预合成素材」的草稿，按最近使用排序。

    返回 [(draft_dir, name, mtime)]，最多 limit 份。
    ★ 有界：秒数预算 + 目录数双封顶（沿用 draft_root_has_combos 的思路），
      绝不 rglob 全盘慢扫 —— 草稿结构很浅（根/<草稿名>/Resources/combination）。
    ★ 谁有资格进批量：里面**有 combination 素材**的草稿（没有素材=没得预合成）。
    """
    base = Path(root)
    if not base.is_dir():
        return []
    t0 = time.time()
    found = {}
    stack = [(base, 0)]
    n = 0
    try:
        while stack and n < max_dirs and time.time() - t0 < budget and len(found) < limit * 4:
            d, depth = stack.pop()
            n += 1
            try:
                if d.name == "combination" and d.is_dir():
                    f = pick_combo_file(d)
                    if f:
                        draft_dir = d.parent.parent
                        if draft_dir.is_dir() and str(draft_dir) not in found:
                            found[str(draft_dir)] = (draft_dir, draft_dir.name,
                                                     f.stat().st_mtime)
                    continue
                if depth >= 3:
                    continue
                for sub in d.iterdir():
                    if sub.is_dir():
                        stack.append((sub, depth + 1))
            except Exception:
                continue
    except Exception as _e:
        _plog(f"[批量] 扫描候选出错（按已找到的处理）: {type(_e).__name__}: {_e}")
    out = sorted(found.values(), key=lambda x: x[2], reverse=True)
    return out[:limit]


def _save_draft_via_return(cfg, st=None):
    """让剪映**落盘**：发「返回草稿页」（快捷键表里没有"保存"，这是唯一手段）。

    读用户的实时绑法（改过也能跟上）；发完等 2 秒让剪映写完。
    返回 True/False（键有没有发出去——发没发成和保存成功是两回事，但这是
    整个流程都在依赖的持久化通道，不再重复验证）。"""
    try:
        keys = read_shortcut_all("returnDraftPage")
        if not keys:
            keys = ["Ctrl+Alt+Q"]
        for k in keys:
            send_combo(k)
            time.sleep(0.4)
        time.sleep(2)
        return True
    except Exception as _e:
        _plog(f"[批量] 发「返回草稿页」失败: {type(_e).__name__}: {_e}")
        if st:
            try:
                st("落盘键没发出去", "err")
            except Exception:
                pass
        return False


def batch_prepare_draft(cfg, draft_dir, st=None, cancel=None):
    """对**一份**草稿自动做完「打开 → 备份 → 预合成 → 登记 → 重启进入 → 清空 → 落盘」。

    跑完这份草稿就处于"打开即导出"的就绪态（时间线上只剩预合成产物）。
    返回 (ok: bool, msg: str)；**用户中止时抛 PipelineCancelled**（调用方负责
    还原当前草稿并停掉队列 —— 与单份流程的"中止=还原"同一个语义）。
    ★ 备份在本草稿预合成之前做；每一份的备份都独立记在桌面上。
    ★ 清空之前的草稿名核对与单份流程同样铁面：打开的不是目标草稿就**绝不碰时间线**。
    """
    def say(t, k="busy"):
        _plog(f"[批量] {Path(draft_dir).name} · {t}")
        if st:
            try:
                st(t, k)
            except Exception:
                pass

    def _ck():
        if _cancelled(cancel):
            raise PipelineCancelled()

    draft_dir = Path(draft_dir)
    name = draft_dir.name
    root = draft_dir.parent
    # 批量模式**强制**允许自动打开草稿（每份都靠用户手点就不叫批量了）；
    # 用副本传下去，不改用户配置。
    cfg2 = dict(cfg)
    cfg2["auto_open_draft"] = True

    # ① 重启剪映并自动打开这份草稿
    say(f"打开草稿「{name}」…")
    t_kill1 = time.time()
    hwnd = _restart_and_enter(cfg2, say, enter=True, draft_hint=name,
                              draft_dir=draft_dir, cancel=cancel)
    _ck()
    if not hwnd:
        return False, "没能自动打开草稿（首页没找到/没点中卡片）"
    opened = open_draft_name(root, since=t_kill1)
    if opened and opened != name:
        return False, f"打开的是「{opened}」不是「{name}」——为安全起见跳过这份"

    # ② 备份（预合成前）—— 每份独立一份，还原互不影响
    say(f"备份「{name}」…")
    if backup_draft_json(cfg, draft_dir, say, tag="预合成前") is None:
        return False, "备份没做成 —— 不敢动没备份的草稿，跳过"

    # ③ 预合成三连（Ctrl+A → 复合 → 预合成）
    before = {}
    for _d, combo, mt in combo_dirs(root):
        before[str(combo)] = mt
    _ck()
    if not _send_keys_sequence(cfg, hwnd, say):
        return False, "快捷键没发成功"
    src = wait_new_combo(root, before, timeout=120, status_cb=say, cancel=cancel)
    _ck()
    if not src:
        return False, "预合成没出产物（这轮渲染失败了？）"
    say("等待产物落盘…")
    wait_file_settled(src, timeout=60, status_cb=say, cancel=cancel)
    _ck()
    guid = product_guid(src)
    if not commit_draft_registration(draft_dir, guid, say, timeout=30, cancel=cancel):
        return False, "预合成登记没写进去（导出会报「媒体格式不支持」），已跳过清空"

    # ④ 重启并回到这份草稿，把原时间线清空
    say("重启剪映刷新…")
    t_kill2 = time.time()
    hwnd2 = _restart_and_enter(cfg2, say, enter=True, draft_hint=name,
                               draft_dir=draft_dir, cancel=cancel)
    _ck()
    if not hwnd2:
        return False, "重启后没能回到草稿"
    wait_file_settled(src, timeout=60, status_cb=say, cancel=cancel)
    _ck()
    opened2 = open_draft_name(root, since=t_kill2)
    if opened2 and opened2 != name:
        return False, f"重启后打开的是「{opened2}」—— 绝不清错草稿，跳过"
    say("清空原时间线…")
    if not _clear_timeline(cfg, hwnd2, say, cancel=cancel):
        return False, "清空时间线没成功（这份草稿保持原样，可单独重试）"

    # ⑤ 落盘（发「返回草稿页」持久化"只剩产物"的状态），然后回首页等下一份
    _save_draft_via_return(cfg, say)
    say("✅ 这份就绪（打开即可导出）")
    return True, "就绪"


# ================================================================ 全自动导出（第二十批 v1.3.0 · 实验）
# ★ 流程的最后一段人工（拖入 + 按导出 + 守完成）到这里也自动化。**实验性、默认关**：
#   导出对话框不是我们画的，里面每一版都可能变 —— 所以每一步都自带"不成就交回手动"
#   的退路，绝不硬闯。开了这个开关 = 用户明确授权伴侣替他按导出键。
FULLAUTO_EXPORT_KEY_DEFAULT = "ctrl+e"


def full_auto_export(cfg, st=None, cancel=None):
    """从「等你导出」状态接着走完最后一程：拖入 → 发导出键 → 确认 → 守望完成。

    返回 (ok, msg)。**任何一步不成都如实交回手动** —— 手动导出的路随时都是通的，
    这个函数只做"锦上添花"，绝不把用户逼进死角。中止抛 PipelineCancelled。
    前置：`cfg["_final_path"]`（run_pipeline / batch_prepare_draft 会写）指向本轮产物。
    """
    def say(t, k="busy"):
        _plog(f"[全自动] {t}")
        if st:
            try:
                st(t, k)
            except Exception:
                pass

    def _ck():
        if _cancelled(cancel):
            raise PipelineCancelled()

    def _dlg_gone(timeout):
        """导出对话框消失了没有（消失 = 导出流程走完/被取消）。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            _ck()
            if not find_export_dialog():
                return True
            time.sleep(1)
        return False

    src = cfg.get("_final_path")
    if not src or not Path(src).exists():
        return False, "找不到本轮的预合成产物"
    jy = find_jianying()
    if not jy:
        return False, "剪映窗口不在了"

    # ① 自动拖入（真机验证过的那条路；内部每步自检，不成会把原因带回来）
    exp = None
    try:
        for h, _t in explorer_windows():
            r = window_rect(h)
            if r and abs((r[2] - r[0]) - FOLDER_WIN_W) <= 40 \
                    and abs((r[3] - r[1]) - FOLDER_WIN_H) <= 40:
                exp = h          # 我们规整过的 1000x620 那扇，就是装产物的那扇
                break
    except Exception:
        exp = None
    say("自动拖入时间线…")
    _ck()
    if not drag_file_into_jianying(cfg, jy[0], Path(src), say, explorer_hwnd=exp):
        return False, "自动拖入没走通（交回你手动拖）"

    # ② 发导出快捷键（读用户实时绑法；剪映默认 Ctrl+E）
    _ck()
    keys = read_shortcut_all("exportVideo") or [FULLAUTO_EXPORT_KEY_DEFAULT]
    _wait_foreground(jy[0], 10, say, cancel=cancel)
    for k in keys:
        send_combo(k)
        time.sleep(0.4)
    say("等导出窗口出现…")
    dlg = None
    t0 = time.time()
    while time.time() - t0 < 20:
        _ck()
        dlg = find_export_dialog()
        if dlg:
            break
        time.sleep(1)
    if not dlg:
        return False, "导出窗口没出现（快捷键可能被改绑了），交回你手动导出"

    # ③ 回车确认（导出窗口的默认按钮就是「导出」）。15 秒没关就补一次；
    #    还不关 = 大概率焦点/布局和预期不一样 —— **不硬闯**，请用户点一下，
    #    我们继续守望"对话框消失"（他什么时候导完我们都接得住）。
    for _attempt in range(2):
        _ck()
        send_combo("enter")
        if _dlg_gone(15):
            break
    else:
        say("请在导出窗口点一下「导出」—— 我继续等你导完")
    say("守望导出完成…")
    if not _dlg_gone(30 * 60):
        return False, "等了 30 分钟还没等到导出完成（先交回手动，还原随时可用）"
    say("✅ 导出完成")
    return True, "导出完成"


# ================================================================ 极简 UIA（零依赖）
# ★ 为什么自己写：伴侣要打成**单文件 exe**（PyInstaller onefile），多引一个 COM 封装库
#   （pywinauto / uiautomation）就要多几 MB，而且在冻结环境里很不稳（comtypes 的
#   generated 模块要落盘缓存）。UIA 本身就是 COM —— ctypes 直接走 vtable 就够用，
#   实测能读出资源管理器里的文件名和精确坐标。
# ★ 为什么必须走 UIA：Windows 11 的资源管理器是 **WinUI/DirectUI** —— 实测窗口树里
#   **没有 SysListView32**，`LVM_GETITEMRECT` 这条老路彻底走不通；而
#   MSAA（`oleacc.AccessibleObjectFromWindow`）在这套窗口上**直接崩**
#   （access violation）。两条老路都堵死，只剩 UIA。
# ★ 踩坑备忘（都实测过，别改回去）：
#   ① `CreatePropertyCondition` 第二个参数是 **VARIANT 结构体**，传 int 会在
#      vtable 里 access violation；
#   ② `SysFreeString` 在 **oleaut32** 里（不在 ole32）；
#   ③ UIA 的 `BoundingRectangle` 是**物理像素**，和 `SetCursorPos` 同一套坐标。
#   （`ole32` 已在上面"在文件夹中显示"那节先定义好了，这里不再重复。）
oleaut32 = ctypes.WinDLL("oleaut32")

_CLSID_CUIAUTOMATION = "ff48dba4-60ef-4201-aa87-54103eef594e"
_IID_IUIAUTOMATION = "30cbe57d-d9d0-452a-ab13-7ac5ac4825ee"
# IUIAutomation 的 vtable 下标（IUnknown 占 0-2）
_UIA_EL_FROM_HANDLE = 6         # ElementFromHandle(HWND, elem**)
_UIA_CREATE_TRUE_COND = 21      # CreateTrueCondition(cond**)
_UIA_CREATE_PROP_COND = 23      # CreatePropertyCondition(PROPERTYID, VARIANT, cond**)
# IUIAutomationElement 的 vtable 下标
_EL_FIND_ALL = 6                # FindAll(TreeScope, cond, arr**)
_EL_GET_CONTROLTYPE = 21        # get_CurrentControlType(CONTROLTYPEID*)
_EL_GET_NAME = 23               # get_CurrentName(BSTR*)
_EL_GET_RECT = 43               # get_CurrentBoundingRectangle(RECT*)
# IUIAutomationElementArray 的 vtable 下标
_ARR_LEN = 3                    # get_Length(int*)
_ARR_GET = 4                    # GetElement(int, elem**)
PROP_CONTROLTYPE = 30003
CT_LISTITEM, CT_TEXT = 50007, 50004
SCOPE_CHILDREN, SCOPE_DESCENDANTS = 2, 4


class _GUID(ctypes.Structure):
    _fields_ = [("D1", ctypes.c_ulong), ("D2", ctypes.c_ushort),
                ("D3", ctypes.c_ushort), ("D4", ctypes.c_ubyte * 8)]


def _guid(text):
    import uuid
    g = uuid.UUID(text)
    return _GUID(g.time_low, g.time_mid, g.time_hi_version,
                 (ctypes.c_ubyte * 8)(*g.bytes[8:]))


class _VAR(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("llVal", ctypes.c_longlong), ("lVal", ctypes.c_long),
                    ("dblVal", ctypes.c_double), ("bstrVal", ctypes.c_void_p)]
    _anonymous_ = ("u",)
    _fields_ = [("vt", ctypes.c_ushort), ("w1", ctypes.c_ushort),
                ("w2", ctypes.c_ushort), ("w3", ctypes.c_ushort), ("u", _U)]


class _URECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _vtbl(obj):
    return ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents


def _vcall(obj, idx, *argtypes):
    """按 vtable 下标取一个 COM 方法（第一个参数固定是 this）。"""
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(_vtbl(obj)[idx])


def _var_int(i):
    v = _VAR()
    v.vt = 3            # VT_I4
    v.lVal = i
    return v


def uia_list_items(hwnd):
    """列出某个资源管理器窗口里的文件条目。

    返回 [(文件名, (l,t,r,b), 文件名文字块(l,t,r,b) 或 None)]；失败返回 []。
    """
    out = []
    try:
        ole32.CoInitializeEx(None, 2)       # APARTMENTTHREADED；已初始化会返回 S_FALSE
    except Exception:
        pass
    try:
        uia = ctypes.c_void_p()
        if ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_CUIAUTOMATION)), None, 1,
                                  ctypes.byref(_guid(_IID_IUIAUTOMATION)),
                                  ctypes.byref(uia)) != 0 or not uia.value:
            return out
        el = ctypes.c_void_p()
        if _vcall(uia, _UIA_EL_FROM_HANDLE, ctypes.c_void_p,
                  ctypes.POINTER(ctypes.c_void_p))(
                uia, ctypes.c_void_p(hwnd), ctypes.byref(el)) != 0 or not el.value:
            return out
        cond = ctypes.c_void_p()
        if _vcall(uia, _UIA_CREATE_PROP_COND, ctypes.c_int, _VAR,
                  ctypes.POINTER(ctypes.c_void_p))(
                uia, PROP_CONTROLTYPE, _var_int(CT_LISTITEM),
                ctypes.byref(cond)) != 0 or not cond.value:
            return out
        arr = ctypes.c_void_p()
        if _vcall(el, _EL_FIND_ALL, ctypes.c_int, ctypes.c_void_p,
                  ctypes.POINTER(ctypes.c_void_p))(
                el, SCOPE_DESCENDANTS, cond, ctypes.byref(arr)) != 0 or not arr.value:
            return out
        n = ctypes.c_int()
        _vcall(arr, _ARR_LEN, ctypes.POINTER(ctypes.c_int))(arr, ctypes.byref(n))
        for i in range(max(0, min(n.value, 200))):
            e = ctypes.c_void_p()
            if _vcall(arr, _ARR_GET, ctypes.c_int,
                      ctypes.POINTER(ctypes.c_void_p))(arr, i, ctypes.byref(e)) != 0 \
                    or not e.value:
                continue
            b = ctypes.c_void_p()
            _vcall(e, _EL_GET_NAME, ctypes.POINTER(ctypes.c_void_p))(e, ctypes.byref(b))
            nm = ctypes.wstring_at(b.value) if b.value else ""
            if b.value:
                oleaut32.SysFreeString(b)
            rc = _URECT()
            if _vcall(e, _EL_GET_RECT, ctypes.POINTER(_URECT))(e, ctypes.byref(rc)) != 0:
                continue
            # 子元素里的 Text 块 = 文件名文字块（用来反推图标区，见 find_explorer_item）
            text_rc = None
            ca = ctypes.c_void_p()
            tc = ctypes.c_void_p()
            if _vcall(uia, _UIA_CREATE_TRUE_COND,
                      ctypes.POINTER(ctypes.c_void_p))(uia, ctypes.byref(tc)) == 0 \
                    and tc.value:
                if _vcall(e, _EL_FIND_ALL, ctypes.c_int, ctypes.c_void_p,
                          ctypes.POINTER(ctypes.c_void_p))(
                        e, SCOPE_CHILDREN, tc, ctypes.byref(ca)) == 0 and ca.value:
                    m = ctypes.c_int()
                    _vcall(ca, _ARR_LEN, ctypes.POINTER(ctypes.c_int))(ca, ctypes.byref(m))
                    for k in range(max(0, min(m.value, 8))):
                        ch = ctypes.c_void_p()
                        if _vcall(ca, _ARR_GET, ctypes.c_int,
                                  ctypes.POINTER(ctypes.c_void_p))(
                                ca, k, ctypes.byref(ch)) != 0 or not ch.value:
                            continue
                        ct = ctypes.c_int()
                        _vcall(ch, _EL_GET_CONTROLTYPE,
                               ctypes.POINTER(ctypes.c_int))(ch, ctypes.byref(ct))
                        if ct.value == CT_TEXT:
                            rr = _URECT()
                            if _vcall(ch, _EL_GET_RECT,
                                      ctypes.POINTER(_URECT))(ch, ctypes.byref(rr)) == 0:
                                text_rc = (rr.left, rr.top, rr.right, rr.bottom)
                            break
            out.append((nm, (rc.left, rc.top, rc.right, rc.bottom), text_rc))
    except Exception as e:
        _plog(f"!! UIA 读资源管理器失败：{type(e).__name__}: {e}")
    return out


def find_explorer_item(win_hwnd, name):
    """在资源管理器窗口里按**文件名精确相等**找条目，返回 (抓取x, 抓取y, 条目框)。

    ★ 必须**精确相等**，不能用包含匹配：combination 目录里同时有
      `<GUID>_video.mp4` 和 `<GUID>_video.mp4.alpha.mp4`，用 `in` 会抓到 alpha
      那个（它是明文占位，拖进去没用）。
    ★ 抓取点取**图标中心**，不是条目框中心：条目框 = 图标 + 若干行文件名 + 留白，
      框中心常常落在**图标下方的空白**上 —— 在那儿按下鼠标是**框选（橡皮筋）**
      而不是拖文件。图标区 = [条目框顶, 文件名文字块顶]，取其中点（实测
      y≈440，正好压在图标上）。
    """
    for nm, rc, text_rc in uia_list_items(win_hwnd):
        if nm != name:
            continue
        l, t, r, b = rc
        if text_rc and text_rc[1] > t + 8:
            cx = (text_rc[0] + text_rc[2]) // 2
            cy = (t + text_rc[1]) // 2
        else:                                   # 拿不到文字块就退化成框的上半部分
            cx = (l + r) // 2
            cy = t + int((b - t) * 0.25)
        return (cx, cy, rc)
    return None


# ================================================================ 自动把产物拖进时间线
# ★★ 2026-09-18 **现场校准**（剪映最大化 1919x1031；用 PrintWindow 抓图 + 逐行剖面
#    量出来的，不是估的）：
#      · 主视频轨（胶片条）在 y 735..790（实测方差峰 y=749）
#      · 轨道头图标占 x 0..185 ⇒ **时间轴 t=0 ≈ x=185**
#      · 横向落点取 x=230（刚过 t=0，剪映有「吸附」会吸到 0）
#    写成**窗口比例**（相对剪映窗口宽高），这样用户改窗口大小也能跟着走。
DRAG_DROP_X_FRAC = 0.120        # 230 / 1919
# ★★ 纵向落点：**不写死**，而是从画面上找"所有片段下面的空白带"（见 empty_lane_y）。
#   为什么必须动态找（2026-09-18 用户定案"拖到最下面空白处，新建轨道"）：
#     ① 轨道条数是用户自己拖出来的，**写死比例**在"轨道更多/更少"的草稿上会落到
#        **有内容的轨道**上 —— 剪映主轨是**覆盖式**，那会**切掉原片段**（破坏性的！）；
#     ② 落在所有片段**下面**的空白处 → 不覆盖任何东西，最安全（原时间线已被
#        清空，产物落在新轨道上，导出以产物为准）。
#   这个 frac 只在"动态检测失败"时兜底当默认值。
DRAG_DROP_Y_FRAC = 0.860        # 兜底：约 y=887，实测在末轨下方空白带里
DRAG_DROP_Y_TRIES = (0, 34, -34)    # 相对"空带落点"的纵向微调（像素，自检没过再试）
DRAG_LANE_SCAN_Y = (0.62, 0.97)     # 找空带的纵向扫描范围（窗口高度比例）
DRAG_LANE_SCAN_X = (0.25, 0.95)     # ★ 避开左侧 0..0.25 的**轨道头图标列**
DRAG_LANE_MAD = 4.0                 # 行内 MAD > 它 = "这一行有片段"
#    ★ 为什么用 **MAD**（中位绝对偏差）而不是**标准差**：时间线上永远有一条
#      **竖线**（播放头 / 网格线，实测 x≈1571），它给**每一行**都灌进 std≈6.3 ——
#      用标准差当判据会得到"整片都有内容"（实测踩过：落点被顶到最底下 994）。
#      MAD 只吃"大多数像素"，一根竖线的贡献被中位数吃掉 → 空行 MAD=0.0，
#      有胶片条的行 MAD=13.5~65（实测），判据干净利落。
DRAG_HDR_SCAN_X = (0.02, 0.16)      # 左侧**轨道头图标列**（眼睛/喇叭/锁/▶）
DRAG_HDR_MIN = 110                  # 该列出现了这么亮的像素 = "这条轨道的头在这"
DRAG_LANE_GAP = 30                  # 从"最后一个有内容的行"往下退这么多像素
DRAG_CHECK_X = (0.05, 0.95)     # 自检比对的横条（含轨道头列：新建轨道会多一列图标）
DRAG_CHECK_Y = (0.660, 0.970)   # 自检比对的纵带（覆盖整条时间线：新轨道一定在里面）
DRAG_DIFF_MIN = 1.5             # 平均像素差超过它才算"时间线变了"（实测噪声地板 0.000）


def _window_image(hwnd, rect):
    """抓剪映窗口整图：优先 PrintWindow（能抓被遮挡的内容），全黑则退屏幕截取。"""
    img = None
    try:
        img = grab_window_image(hwnd)
    except Exception:
        img = None
    if img is None or ImageStat.Stat(img.convert("L")).mean[0] <= 8:
        # PrintWindow 全黑（GPU 渲染 / 长时间遮挡）→ 退回屏幕截取（要求窗口可见）
        try:
            img = ImageGrab.grab(bbox=rect, all_screens=True)
        except Exception:
            return img
    return img


def _strip_of(img):
    """把整图裁成"自检用的那条带"（灰度）。"""
    if img is None:
        return None
    w, h = img.size
    return img.crop((int(w * DRAG_CHECK_X[0]), int(h * DRAG_CHECK_Y[0]),
                     int(w * DRAG_CHECK_X[1]), int(h * DRAG_CHECK_Y[1]))).convert("L")


def _med(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _last_row_mad(g, x0, x1, y0, y1):
    """最后一行"有片段"（行内 MAD > 阈值）。见 DRAG_LANE_MAD 的注释。"""
    band = g.crop((x0, y0, x1, y1))
    bw, bh = band.size
    if bw < 8 or bh < 8:
        return None
    px = band.load()
    step = max(1, bw // 220)
    last = None
    for i in range(bh):
        vals = [px[x, i] for x in range(0, bw, step)]
        m = _med(vals)
        if _med([abs(v - m) for v in vals]) > DRAG_LANE_MAD:
            last = i
    return None if last is None else y0 + last


def _last_row_bright(g, x0, x1, y0, y1):
    """最后一行"有轨道头图标"（左列出现足够亮的像素）。"""
    band = g.crop((x0, y0, x1, y1))
    bw, bh = band.size
    if bw < 4 or bh < 4:
        return None
    px = band.load()
    step = max(1, bw // 24)
    last = None
    for i in range(bh):
        if max(px[x, i] for x in range(0, bw, step)) > DRAG_HDR_MIN:
            last = i
    return None if last is None else y0 + last


def empty_lane_y(img):
    """从时间线画面里找"所有轨道**下面**那条空白带"的 y（窗口相对像素）。

    ★ 为什么要动态找：轨道条数是用户自己拖出来的，写死比例在有更多轨道的草稿上
      会落到**有内容的轨道**上 → 剪映主轨是**覆盖式**，会切掉原片段（破坏性）。
      （2026-09-18 用户定案："拖到最下面空白处，新建轨道"。）
    ★ 两个互补信号取 **max**：
      ① **片段行**：内容列（x 0.25..0.95）行内 MAD 大 → 胶片/缩略图（实测 ρ 到 13.5~65）；
      ② **轨道头行**：左列（x 0.02..0.16）有亮图标 → 这条轨道确实存在。
         ★ 少了②会踩坑：**空轨道**在内容列里 MAD=0（跟"轨道下方空白"长得一样），
           只靠①会把落点停在**末轨的片段**下面 —— 而实测"最后一轨是空轨"很常见
           （本次实测：轨道头到 y=834，片段只到 y=764）。
      取 max → 落到**所有轨道之下**（实测 y=864，落在 med=38 的纯空白带里）。
    返回 y（像素）或 None（画面读不出来 → 调用方退回 DRAG_DROP_Y_FRAC）。
    """
    if img is None:
        return None
    try:
        w, h = img.size
        g = img.convert("L")
        y0, y1 = int(h * DRAG_LANE_SCAN_Y[0]), int(h * DRAG_LANE_SCAN_Y[1])
        if y1 - y0 < 8:
            return None
        lane = _last_row_mad(g, int(w * DRAG_LANE_SCAN_X[0]),
                             int(w * DRAG_LANE_SCAN_X[1]), y0, y1)
        hdr = _last_row_bright(g, int(w * DRAG_HDR_SCAN_X[0]),
                               int(w * DRAG_HDR_SCAN_X[1]), y0, y1)
        cands = [v for v in (lane, hdr) if v is not None]
        _plog(f"自动拖入：空带检测 片段行→{lane} 轨道头行→{hdr}")
        if not cands:                          # 整片都空（没有任何轨道）→ 取扫描区中部
            return y0 + (y1 - y0) // 3
        y = max(cands) + DRAG_LANE_GAP
        ylim = int(h * 0.965)                  # 别落到横向滚动条上
        return max(y0 + 8, min(y, ylim))
    except Exception as e:
        _plog(f"!! 找空轨落点失败：{e}")
        return None


def _timeline_strip(hwnd, rect):
    """抓剪映窗口里"时间线那一条带"的灰度图（拖拽自检用）。"""
    return _strip_of(_window_image(hwnd, rect))


def _strip_diff(a, b):
    if a is None or b is None or a.size != b.size:
        return -1.0
    try:
        return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]
    except Exception:
        return -1.0


def drag_file_into_jianying(cfg, jy_hwnd, src, st=None, explorer_hwnd=None):
    """把资源管理器里选中的 src **真鼠标拖**进剪映时间线。

    ★ 落点 = 画面上"**所有片段下面的空白带**"（`empty_lane_y` 动态找）+ 横向 t≈0。
      2026-09-18 用户定案："拖到最下面空白处，新建轨道" —— 因为剪映**主轨是覆盖式**，
      直接拖到主轨 t=0 会**切掉原片段**（破坏性）。落在片尾下方的空白里，不覆盖任何东西。
    ★ 只有**自检确认时间线真的变了**才返回 True。任何不确定（找不到条目 / 落点上
      不是剪映 / 自检没通过）都返回 False —— 用户还能自己拖一下，绝不假装成功。

    流程：① 资源管理器里按文件名精确定位条目 → 图标中心当起点
          ② 先取**自检基线**（此时剪映还在前台，画面干净）
          ③ 把资源管理器提到前台 → 算落点并确认那一像素上**真的是剪映**（护栏）
          ④ 起点按下 → **分步**移到落点（一步到位 Explorer 认不出是拖拽）
             → 在剪映上停一下让 drop target 高亮 → 松开
          ⑤ 把剪映按回前台 → 抓"时间线那一条带"和基线比像素，变了才算成功
          ⑥ 光标还原到原处（尽量不打扰）
    """
    def say(t):
        if st:
            st(t)

    if not explorer_hwnd or not window_rect(explorer_hwnd) or not user32.IsWindow(explorer_hwnd):
        _plog("自动拖入：资源管理器窗口不在了，交回手动")
        return False
    item = find_explorer_item(explorer_hwnd, Path(src).name)
    if not item:
        _plog("自动拖入：资源管理器里没定位到目标条目，交回手动")
        return False
    sx, sy, irc = item
    _plog(f"自动拖入：起点=({sx},{sy}) 条目框={irc}")

    jr = window_rect(jy_hwnd)
    if not jr:
        return False
    jl, jt, jr2, jb = jr
    jw, jh = jr2 - jl, jb - jt

    ctx = _dpi_aware()
    old = wt.POINT()
    try:
        user32.GetCursorPos(ctypes.byref(old))

        # ★ 先把**剪映**抬到最上层：落点护栏要求"那一像素上真的是剪映"，
        #   若剪映被别的窗口（实测：另一个全屏窗口占着同一块屏）盖住，
        #   护栏会正确拒绝 → 整个自动拖入失效。抬升用 _raise_for_drag（不动鼠标）。
        if _raise_for_drag(jy_hwnd):
            time.sleep(0.3)

        # ★★ 把播放头**打回首帧**（`locateFirstFrame`，本机 Home）。
        #   实测（2026-09-18）：往时间线**空白处**丢文件时，剪映是**按播放头**落位的
        #   （那次播放头在 17.28s → 新片段落到 17.28s，导出总长直接变 37s、
        #   前 17s 还是黑的）。先按 Home 把播放头归零，新片段就和原内容**对齐在 0**。
        #   ★ 只在剪映确实拿到**键盘焦点**时才发 —— 免得把 Home 送到别的窗口
        #     （`_raise_for_drag` 的 SwitchToThisWindow 分支会设前台；TOPMOST 分支不会）。
        if user32.GetForegroundWindow() == jy_hwnd:
            try:
                send_combo(read_shortcut("locateFirstFrame", "Home"))
                time.sleep(0.4)
                _plog("自动拖入：播放头已打回首帧（Home）")
            except Exception as e:
                _plog(f"自动拖入：播放头归零失败（{e}），片段会落在当前播放头位置")
        else:
            _plog("自动拖入：剪映未拿到键盘焦点，跳过 Home（片段会落在播放头位置）")

        # ★★ 基线必须在**"把资源管理器提到前台"之前**取。
        #   此刻剪映在最上层，时间线是"干净的剪映画面"。拖完松开时剪映会被系统
        #   激活回前台 → 前后两次抓到的都是**纯粹的剪映内容**，可直接比。
        #   若反过来（先提前台再取基线），基线里会混进**压在时间线带上的资源管理器
        #   窗口**，而拖完它又退回剪映 → 光"资源管理器消失"这一项就足以让像素大变，
        #   自检会**误判成功**（尤其在 PrintWindow 拿不到内容、退回屏幕截取的兜底路径上）。
        user32.SetCursorPos(old.x, old.y)
        time.sleep(0.4)
        base_img = _window_image(jy_hwnd, jr)
        base = _strip_of(base_img)
        if base is None:
            _plog("自动拖入：抓不到时间线基线，无法自检 → 交回手动")
            say("自动拖入：无法自检，交回手动")
            return False

        # ★ 纵向落点＝画面上"所有轨道**下面**那条空白带"（动态找，别写死比例）。
        #   写死比例在有更多轨道的草稿上会落到**有内容的轨道**上 → 主轨覆盖式会切原片段。
        lane = empty_lane_y(base_img)
        if lane is None:
            lane = int(jh * DRAG_DROP_Y_FRAC)
            _plog(f"自动拖入：空带检测失败，退回比例兜底 y={lane}")
        else:
            _plog(f"自动拖入：空带落点 y={lane}（窗口相对，落在末轨下方空白）")

        # ★ 再把资源管理器**抬到最上层**才能按到图标：剪映是**最大化**的，不抬起来
        #   它整块盖住资源管理器 —— 直接在图标上按下鼠标，实际点到的是剪映本身，
        #   压根不是拖拽（真机上必踩）。
        if _raise_for_drag(explorer_hwnd):
            time.sleep(0.45)
        if window_root_at(sx, sy) != explorer_hwnd:
            _plog("自动拖入：起点上不是那个资源管理器窗口（被别的窗口盖住），交回手动")
            return False

        drop = None
        for off in DRAG_DROP_Y_TRIES:
            dx = jl + int(jw * DRAG_DROP_X_FRAC)
            dy = jt + max(8, min(int(jh * 0.965), lane + off))
            owner = window_root_at(dx, dy)
            if owner == jy_hwnd:
                drop = (dx, dy, off)
                break
            _plog(f"自动拖入：落点({dx},{dy}) 上是窗口 {owner}，不是剪映主窗 → 换 y")
        if not drop:
            say("自动拖入：找不到没被挡住的落点")
            _plog("自动拖入：所有候选落点都不是剪映主窗（被别的窗口盖住了）")
            return False

        dx, dy, off = drop
        _plog(f"自动拖入：落点=({dx},{dy}) 纵向微调={off}")
        ok = False
        for attempt in range(2):                     # 第一次不成就重按一次
            user32.SetCursorPos(sx, sy)
            time.sleep(0.35)
            user32.mouse_event(0x0002, 0, 0, 0, 0)   # 左键按下
            time.sleep(0.25)
            steps = 18
            for i in range(1, steps + 1):            # ★ 必须分步：一步到位不算拖拽
                x = sx + (dx - sx) * i // steps
                y = sy + (dy - sy) * i // steps
                user32.SetCursorPos(int(x), int(y))
                time.sleep(0.045)
            time.sleep(0.55)                         # 停在剪映上，等 drop target 亮起来
            user32.mouse_event(0x0004, 0, 0, 0, 0)   # 左键松开
            time.sleep(0.9)
            # ★ 拖完把剪映**抬回最上层**再抓，和基线镜头对齐（两边都是"剪映在最上层"）。
            #   本地拖放的落地窗口通常会被系统自动激活，但不保证 —— 万一没激活，
            #   压在时间线带上的资源管理器就成了"变化本身"，会把失败误判成成功。
            _raise_for_drag(jy_hwnd)
            user32.SetCursorPos(old.x, old.y)
            time.sleep(0.6)
            now = _timeline_strip(jy_hwnd, jr)
            d = _strip_diff(base, now)
            _plog(f"自动拖入：第 {attempt + 1} 次自检像素差={d:.2f}")
            if d >= DRAG_DIFF_MIN:
                ok = True
                break
            if d < 0:
                break
        if ok:
            _plog("自动拖入：成功（时间线自检通过）")
        else:
            _plog("自动拖入：自检没过 → 按失败处理，交回用户手动拖")
        return ok
    except Exception as e:
        import traceback
        _plog("自动拖入异常：" + traceback.format_exc())
        return False
    finally:
        _dpi_restore(ctx)


def is_minimized(hwnd):
    return bool(user32.IsIconic(hwnd))


def mouse_click(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    user32.mouse_event(0x0002, 0, 0, 0, 0)   # left down
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)   # left up
    time.sleep(0.2)


def bg_click(hwnd, x, y, hold=0.06):
    """后台点击（投递 WM_LBUTTONDOWN/UP，不移动物理光标）。

    ⚠️ **实测无效，别再走这条路**（2026-09-17）：在剪映编辑页对 ☰ 菜单区
    `PostMessage` 鼠标消息，前后**像素平均变化 = (0,0,0)**、菜单不弹；
    同一坐标改用真实 `mouse_click` 则像素明显变化。原因：剪映界面是
    **CEF/Chromium** 内核，输入走它自己的管线，不理会投递的窗口消息。

    保留函数只为以后在别的窗口类型上试探；**流程里不要依赖它**。
    """
    try:
        pt = wt.POINT(int(x), int(y))
        if not user32.ScreenToClient(hwnd, ctypes.byref(pt)):
            return False
        lp = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
        user32.PostMessageW(hwnd, 0x0200, 0, lp)           # WM_MOUSEMOVE
        time.sleep(0.03)
        user32.PostMessageW(hwnd, 0x0201, 0x0001, lp)      # WM_LBUTTONDOWN
        time.sleep(hold)
        user32.PostMessageW(hwnd, 0x0202, 0, lp)           # WM_LBUTTONUP
        time.sleep(0.1)
        return True
    except Exception as e:
        _plog(f"后台点击异常：{e}")
        return False


def _dpi_aware():
    """工作线程内切物理坐标（不影响 tkinter 主线程）。"""
    try:
        return user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        return None


def _dpi_restore(ctx):
    try:
        if ctx is not None:
            user32.SetThreadDpiAwarenessContext(ctx)
    except Exception:
        pass


def _alt_trick_foreground(hwnd):
    fg = user32.GetForegroundWindow()
    our_tid = user32.GetWindowThreadProcessId(fg, None)
    tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
    attach = bool(our_tid and our_tid != tgt_tid)
    if attach:
        user32.AttachThreadInput(our_tid, tgt_tid, True)
    user32.keybd_event(0x12, 0, 0, 0)          # ALT 技巧解锁前台限制
    ok_fg = user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)
    if attach:
        user32.AttachThreadInput(our_tid, tgt_tid, False)
    time.sleep(0.35)
    return ok_fg and user32.GetForegroundWindow() == hwnd


def _raise_for_drag(hwnd):
    """把窗口**抬到最上层**（拖拽要"起手能按到、落地镜头对齐"时用）。

    ★★ 2026-09-18 实测（别改回去）：本机 `_alt_trick_foreground`（ALT 技巧）
      **恒返回 False** —— 有别的全屏窗口占着剪映那块屏时，前台锁不给放行。
      真正管用的是下面两条，而且**都不动鼠标**：
        ① `SwitchToThisWindow`（任务栏自己用的那套，不受前台锁限制）；
        ② `SetWindowPos(HWND_TOPMOST)` → 立刻 `HWND_NOTOPMOST`：纯 z 序抬升，
           不改焦点、不抢键盘，抬完窗口稳稳停在最上层（实测命中判据 OK）。
      ⇒ 拖拽前的"提前台"一律走这里，不要只用 _alt_trick_foreground。
    """
    try:
        if _alt_trick_foreground(hwnd):
            return True
    except Exception:
        pass
    try:
        sw = getattr(user32, "SwitchToThisWindow", None)
        if sw:
            sw(ctypes.c_void_p(hwnd), True)
            time.sleep(0.5)
            if user32.GetForegroundWindow() == hwnd:
                return True
    except Exception:
        pass
    try:
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOMOVE_NOSIZE = 0x0002 | 0x0001
        # hwndInsertAfter 是指针参数，必须用 c_void_p 传 -1/-2
        # （不设 argtypes 时裸 int 会被当成 32 位 —— -1 变成 0xFFFFFFFF，不是 HWND_TOPMOST）
        user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_TOPMOST),
                            0, 0, 0, 0, SWP_NOMOVE_NOSIZE)
        time.sleep(0.2)
        user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_NOTOPMOST),
                            0, 0, 0, 0, SWP_NOMOVE_NOSIZE)
        time.sleep(0.4)
        return True
    except Exception as e:
        _plog(f"!! _raise_for_drag 失败：{e}")
        return False


# ★ 鼠标策略分两类，别混：#   ① 内容点击（点草稿卡片、"开始创作"、编辑区任意坐标）—— 风险高，由
#      cfg["allow_mouse_fallback"] 控制，默认**关**（用户明确要求别动鼠标）。
#   ② 窗口激活点击（点任务栏上的剪映按钮把窗口带到前台）—— 不是对内容的操作，
#      点错了也只是切错窗口，由 cfg["allow_taskbar_click"] 控制，默认**开**。
#      这条路被关掉后，activate() 只剩 ALT 技巧 + SwitchToThisWindow，
#      两者都失败就如实失败交回用户手动点一下（不硬来）。
_TASKBAR_CLICK_ALLOWED = True

# ★ 清剪映「挡路模态框」（见 dismiss_jy_modals / MODAL_TITLES）的总开关。
#   默认开：不关它，「链接媒体」一挂上就抢走键盘焦点，后面所有按键都送不进去。
#   它只做"等价于点取消"这件事，不碰任何文件；用户想完全禁用就
#   cfg["dismiss_modals"] = false（在 伴侣配置.json 里改）。
_DISMISS_MODALS = True


def set_mouse_policy(cfg):
    """把配置里的鼠标策略应用到激活路径。返回 (taskbar_click, content_click)。"""
    global _TASKBAR_CLICK_ALLOWED, _DISMISS_MODALS
    _TASKBAR_CLICK_ALLOWED = bool(cfg.get("allow_taskbar_click", True))
    _DISMISS_MODALS = bool(cfg.get("dismiss_modals", True))
    return _TASKBAR_CLICK_ALLOWED, bool(cfg.get("allow_mouse_fallback", False))


def _taskbar_activate(title_kw="剪映专业版"):
    """点任务栏上的应用按钮：被其他窗口（如微信）全遮住时唯一可靠通道。

    属于"窗口激活"类鼠标操作（见 _TASKBAR_CLICK_ALLOWED 说明），默认允许；
    用户把 allow_taskbar_click 关掉后这里直接放弃，交给纯 API 手段。"""
    if not _TASKBAR_CLICK_ALLOWED:
        _plog("任务栏点击已按配置禁用，跳过（改用纯 API 激活）")
        return False
    try:
        from pywinauto import Desktop
        ctx = _dpi_aware()
        try:
            tray = Desktop(backend="uia").window(class_name="Shell_TrayWnd")
            target = None
            for b in tray.descendants(control_type="Button"):
                try:
                    nm = b.window_text()
                except Exception:
                    continue
                if title_kw in nm or (title_kw.rstrip("专业版") in nm and "伴侣" not in nm):
                    target = b
                    break
            if not target:
                _plog("任务栏未找到剪映按钮")
                return False
            rect = target.rectangle()
            cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
            _plog(f"点击任务栏按钮 ({cx},{cy})")
            mouse_click(cx, cy)
            time.sleep(1.2)
            return True
        finally:
            _dpi_restore(ctx)
    except Exception as e:
        _plog(f"任务栏激活异常: {e}")
        return False


def activate(hwnd):
    """把剪映强制带到前台并确认。
    顺序：ALT+SetForegroundWindow（用户刚点过悬浮球时放行）×3 →
    任务栏按钮点击兜底（窗口被微信等盖住时仍有效）。"""
    if is_minimized(hwnd):
        user32.ShowWindow(hwnd, 9)
        time.sleep(0.6)
    for _ in range(3):
        if _alt_trick_foreground(hwnd):
            return True
        time.sleep(0.5)
    # SwitchToThisWindow：任务栏自己用的那套前台切换，不受前台锁限制，
    # 比 SetForegroundWindow 靠谱，而且**不需要动鼠标**。
    try:
        sw = getattr(user32, "SwitchToThisWindow", None)
        if sw:
            sw(ctypes.c_void_p(hwnd), True)
            time.sleep(0.8)
            if user32.GetForegroundWindow() == hwnd:
                _plog("SwitchToThisWindow 激活成功（未动鼠标）")
                return True
    except Exception as e:
        _plog(f"SwitchToThisWindow 异常：{e}")
    if _taskbar_activate():
        if user32.GetForegroundWindow() == hwnd:
            return True
    for _ in range(2):
        if _alt_trick_foreground(hwnd):
            return True
        time.sleep(0.5)
    ok = user32.GetForegroundWindow() == hwnd
    _plog(f"activate 最终结果 => {ok}")
    return ok


# ================================================================ 页面识别 / 自动打开草稿
class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPPM", ctypes.c_long),
                ("biYPPM", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


def grab_window_image(hwnd):
    """PrintWindow 抓窗口内容。编辑页是 GPU 渲染会得到全黑图，调用方需做黑图兜底。"""
    r = window_rect(hwnd)
    if not r:
        return None
    w, h = r[2] - r[0], r[3] - r[1]
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetWindowDC(hwnd)
    mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
    bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
    ctypes.windll.gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)
    bmi = _BMIH()
    bmi.biSize = ctypes.sizeof(_BMIH)
    bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = w, -h, 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    ctypes.windll.gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
    user32.ReleaseDC(hwnd, hdc)
    ctypes.windll.gdi32.DeleteDC(mem)
    ctypes.windll.gdi32.DeleteObject(bmp)
    return img


def _page_image(hwnd):
    """优先 PrintWindow，全黑则退回屏幕截取（要求窗口在前台）。"""
    img = grab_window_image(hwnd)
    if img is not None and ImageStat.Stat(img.convert("L")).mean[0] > 8:
        return img
    r = window_rect(hwnd)
    if not r:
        return None
    ctx = _dpi_aware()
    try:
        return ImageGrab.grab(bbox=r, all_screens=True)
    except Exception:
        return img
    finally:
        _dpi_restore(ctx)


def is_home_page(img):
    """首页顶部有彩色渐变 banner（绿明显多于红）；编辑页该区域是暗色中性色。
    实测：首页 G-R≈28，编辑页 G-R≈0。"""
    if img is None:
        return False
    w, h = img.size
    if w < 300 or h < 200:
        return False
    crop = img.crop((int(0.25 * w), int(0.09 * h), int(0.85 * w), int(0.24 * h)))
    r, g, b = ImageStat.Stat(crop).mean[:3]
    _plog(f"页面特征 G-R={g - r:.0f} 亮度={(r + g + b) / 3:.0f}")
    return (g - r) >= 12


def find_card_center(img):
    """首页「本地草稿」第一张卡片的亮斑中心（相对物理坐标）。
    区域：x∈[0.20w,0.5w] y∈[0.64h,0.92h]——排除左下角随机推广横幅
    和上方的"专业升级"推广卡片行。"""
    w, h = img.size
    x0, y0, x1, y1 = int(0.20 * w), int(0.64 * h), int(0.50 * w), int(0.92 * h)
    region = img.crop((x0, y0, x1, y1)).convert("L")
    px = region.load()
    rw, rh = region.size
    minx, maxx, miny, maxy, n = rw, 0, rh, 0, 0
    for yy in range(0, rh, 3):
        for xx in range(0, rw, 3):
            if px[xx, yy] > 90:
                n += 1
                minx = min(minx, xx); maxx = max(maxx, xx)
                miny = min(miny, yy); maxy = max(maxy, yy)
    if n < 100:
        return None
    return (x0 + (minx + maxx) // 2, y0 + (miny + maxy) // 2)


# ------------------------------------------------ ★ 用草稿封面精确定位首页卡片
# 2026-09-18 用户要求"伴侣自己打开原草稿，不动鼠标"。
# 实测结论：剪映**没有**任何非鼠标入口（快捷键表 91 条命令里没有"打开草稿"；
# UIA 控件树是空的、剪映没开无障碍桥；主程序没有 CLI 参数；跳转列表没有草稿项；
# 优雅退出后重启也**不会**自动回到上次草稿 —— 一定停在首页）。
# 所以只能真点一下卡片。但可以把这一下做到**又准又安全**：
#   ① 用草稿自己的 draft_cover.jpg（首页卡片缩略图的来源）做多尺度相关匹配
#      **认卡片**，而不是靠"第一张大概是吧"去猜；
#   ② 点之前用 WindowFromPoint 复核那个坐标上到底是谁，被挡住就放弃；
#   ③ 点之后用 open_draft_name()（只读 `.locked`）验证真的进了目标草稿；
#   ④ 点完把光标还原到原位。
def _pearson_l(a, b):
    """两张同尺寸 L 图的皮尔逊相关系数（-1..1）。

    ★ 为什么用「差的平方」而不是 `ImageChops.multiply` 来求 Σab（踩过）：
      multiply 内部是**整数截断** `a*b/255`，乘回 255 后会系统性偏低约 2%
      —— 实测**同一张图**的相关度只算出 **0.913**，不是 1.0。改用恒等式
        `Σab = (Σa² + Σb² − Σ(a−b)²) / 2`
      就精确了（`difference` 没有截断），同图相关度精确 = 1。
    """
    n = a.width * a.height
    sa, sb = ImageStat.Stat(a), ImageStat.Stat(b)
    sA, sA2 = sa.sum[0], sa.sum2[0]
    sB, sB2 = sb.sum[0], sb.sum2[0]
    sAB = (sA2 + sB2 - ImageStat.Stat(ImageChops.difference(a, b)).sum2[0]) / 2.0
    d1, d2 = n * sA2 - sA * sA, n * sB2 - sB * sB
    if d1 <= 0 or d2 <= 0:
        return -2.0                       # 有一边是纯色 → 无法判断，明确标出来
    return (n * sAB - sA * sB) / math.sqrt(d1 * d2)


def card_template(cover, w, h):
    """封面 → 卡片缩略图：按目标宽高比**中心裁剪**再缩放。

    首页卡片是"裁切填充"的：封面 1920x1080（16:9）会被裁成卡片的 4:3。
    """
    cw, ch = cover.size
    want = w / float(h)
    if cw / float(ch) > want:             # 封面太宽 → 裁左右
        nw = max(1, int(round(ch * want)))
        x0 = (cw - nw) // 2
        box = (x0, 0, x0 + nw, ch)
    else:                                 # 太高 → 裁上下
        nh = max(1, int(round(cw / want)))
        y0 = (ch - nh) // 2
        box = (0, y0, cw, y0 + nh)
    return cover.crop(box).resize((w, h), Image.BILINEAR).convert("L")


def locate_draft_card(img, cover, aspect=4 / 3.0, topk=3):
    """在首页截图 `img` 里找出"封面 = cover"的那张草稿卡片。

    纯 PIL（本机没有 numpy/cv2）：粗搜（降 8 倍、步长 2）+ 精修（原尺度 ±12px）
    + 非极大抑制。返回 [(score, x, y, w, h), ...] 按得分降序，坐标是**图内坐标**。

    ★ 实测（1168x780 的首页）：目标草稿得分 **+0.81**，第二名误匹配只有 **+0.44**
      —— 排序非常干净，卡个阈值就很稳。
    """
    if img is None or cover is None:
        return []
    f = 8
    # ★ 粗搜步长/尺度档数（2026-09-18 提速）：
    #   原来 scale=2、9 个尺度档 ⇒ 24399 次 `_pearson_l`、耗时 **8.4s**（实测）。
    #   现在 scale=3、6 个尺度档 ⇒ 约 7200 次、~2.5s。
    #   精度不会掉：粗搜的位置误差上限 = scale/2 个小像素 = **±12 全像素**，
    #   而精修阶段在 ±12 像素、宽度 ±16 像素范围里找，完全覆盖得住；
    #   尺度档虽然稀了，但精修的 `bw ±16` 区间是互相重叠的（64±16, 104±16, …）。
    _STEP = 3
    small = img.convert("L").resize(
        (max(1, img.width // f), max(1, img.height // f)), Image.BILINEAR)
    SW, SH = small.size
    coarse = []
    for th in range(6, 23, 3):
        tw = max(4, int(round(th * aspect)))
        if tw >= SW or th >= SH:
            continue
        tmpl = card_template(cover, tw, th)
        for y in range(0, SH - th + 1, _STEP):
            for x in range(0, SW - tw + 1, _STEP):
                coarse.append((_pearson_l(small.crop((x, y, x + tw, y + th)), tmpl),
                               x, y, tw, th))
    if not coarse:
        return []
    coarse.sort(key=lambda t: -t[0])

    full = img.convert("L")
    cand = []
    for _r, x, y, tw, th in coarse[:10]:
        bx, by, bw = x * f, y * f, tw * f
        for dw in (-16, -8, 0, 8, 16):
            w2 = max(24, bw + dw)
            h2 = max(18, int(round(w2 / aspect)))
            tmpl = card_template(cover, w2, h2)
            for dy in (-12, -6, 0, 6, 12):
                for dx in (-12, -6, 0, 6, 12):
                    X, Y = bx + dx, by + dy
                    if X < 0 or Y < 0 or X + w2 > full.width or Y + h2 > full.height:
                        continue
                    cand.append((_pearson_l(full.crop((X, Y, X + w2, Y + h2)), tmpl),
                                 X, Y, w2, h2))
    cand.sort(key=lambda t: -t[0])

    keep = []
    for r, X, Y, w, h in cand:            # 非极大抑制：位置太近的只留最高分
        if any(abs(X - k[1]) < w * 0.6 and abs(Y - k[2]) < h * 0.6 for k in keep):
            continue
        keep.append((r, X, Y, w, h))
        if len(keep) >= topk:
            break
    return keep


def draft_cover_path(draft_dir):
    """草稿自己的封面图（= 首页卡片缩略图的来源）。没有就返回 None。"""
    try:
        p = Path(draft_dir) / "draft_cover.jpg"
        return p if p.is_file() and p.stat().st_size > 1024 else None
    except Exception:
        return None


def draft_display_order(root):
    """首页卡片的**显示顺序**（只读剪映自己的 `root_meta_info.json`，一个字节都不写）。

    ★ 2026-09-18 实测：`all_draft_store` 的数组顺序**就是首页卡片的左右顺序**
      （左起第一张 = 数组第 0 个）。怎么验证的：第 0 个草稿
      `draft_timeline_materials_size=95184` → 卡片上写的就是 "92.9K"，
      第 1 个 = 58764314 → 卡片上 "56.0M"，跟截图一一对上。

    ★ 这条对流程极其关键：**我们要打开的那份草稿刚刚做过预合成 + 保存**，
      所以它一定是最新改动的那个 → 一定是第 0 张卡片。
      于是"开哪张卡"这件事，可以完全不靠画面识别，只靠这个顺序。

    返回 [草稿名, ...]（= 屏上从左到右）；读不到返回 []。
    """
    try:
        p = Path(root) / "root_meta_info.json"
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for e in (data.get("all_draft_store") or []):
        if not isinstance(e, dict):
            continue
        nm = e.get("draft_name")
        if not nm and e.get("draft_fold_path"):
            nm = Path(str(e["draft_fold_path"])).name
        if nm:
            out.append(str(nm))
    return out


# 首页卡片是**固定像素尺寸**（不随窗口缩放），横向间距实测 ≈112px。
# 只在"封面匹配只找到一张卡、量不出间距"时当兜底用，而且每一步都要过
# `_card_at` 确认，所以就算偏了一点也不会点到空白处。
CARD_PITCH_FALLBACK = 112

# "本地草稿"那一行**一定在页面下半部分**（上半部分是横幅 + 模板推荐）。
# 只在下半部分找卡片，能把封面匹配在上半部分产生的零星假峰直接滤掉。
_CARD_ROW_YMIN = 0.45


def _card_at(img, cx, cy, bg=None, half=10, tol=20, ratio=1 / 3.0):
    """(cx, cy) 处**是不是有一张卡片**（用来防止点到页面空白 / 横幅边角）。

    判据故意宽松：卡片是"裁切填充"的缩略图，内容千差万别，但"和页面底色不一样"
    这一条永远成立。取 2*half 见方的一小块，只要里面超过 `ratio` 的像素明显不同于
    页面底色（左上角像素），就算这里有卡片。
    """
    try:
        if bg is None:
            bg = img.getpixel((4, 4))
        x0, y0 = max(0, cx - half), max(0, cy - half)
        patch = img.crop((x0, y0, min(img.width, cx + half), min(img.height, cy + half)))
        if patch.width < 4 or patch.height < 4:
            return False
        px = patch.load()
        hit = 0
        for yy in range(patch.height):
            for xx in range(patch.width):
                p = px[xx, yy]
                if max(abs(p[0] - bg[0]), abs(p[1] - bg[1]), abs(p[2] - bg[2])) > tol:
                    hit += 1
        return hit > (patch.width * patch.height) * ratio
    except Exception:
        return False


def _card_row_slots(img, hits):
    """把封面匹配给出的框整理成"一整排卡片槽位"。返回 (槽位中心 x 列表, 行中心 y)。

    ★ 为什么要"整理"而不是直接用分数最高的那个框：
      封面匹配的**分数**在封面长得像的时候完全不可靠（实测两份占位封面
      「媒体丢失」vs「媒体格式不支持」只差 0.047，随便一点噪声就翻盘）；
      但它给出的**卡片中心 x 非常准**（实测 294/406，真值 294/407）——
      因为决定 x 的是卡片那一整块深色底，不是上面那行小字。
      所以这里**只借它的位置，不借它的排序**。

    做法：
      ① 只在**页面下半部分**找（`_CARD_ROW_YMIN`）：首页上半部分是横幅和模板推荐，
         封面匹配在那里会有零星假峰 —— 实测在 y=75 出过 0.43 的假命中，
         会让整排槽位全部错到顶栏上去。
      ② 按 y 聚簇（±45px 算同一行），取**命中最多、总分最高**的那一行。
      ③ 用横向间距的中位数当卡片 pitch → 以最左一张为锚往左/往右按 pitch 外推，
         每格都用 `_card_at` 确认真有卡片，直到外推出去不是卡片为止 ⇒ 整排卡片。
      ④ 行中心 y 再用 `_card_at` 纵向扫一遍取**卡片真实上下边中点**：
         匹配框的 y 是有偏的（实测偏下 ~23px、框也偏大），照框中心点会落到
         卡片下沿附近，纵向修一次才稳。
    """
    if not hits:
        return [], 0
    ymin = img.height * _CARD_ROW_YMIN
    pool = [(h[1] + h[3] // 2, h[2] + h[4] // 2, h[0]) for h in hits
            if (h[2] + h[4] // 2) > ymin]
    if not pool:
        return [], 0
    pool.sort(key=lambda c: c[1])
    rows = []
    for cx, cy, sc in pool:
        if rows and abs(cy - rows[-1][0]) <= 45:
            rows[-1][1].append((cx, cy, sc))
            rows[-1][0] = sum(c[1] for c in rows[-1][1]) / float(len(rows[-1][1]))
        else:
            rows.append([cy, [(cx, cy, sc)]])
    rows.sort(key=lambda r: (len(r[1]), sum(c[2] for c in r[1])), reverse=True)
    best = rows[0][1]
    xs = sorted(c[0] for c in best)
    top_sc = max(best, key=lambda c: c[2])          # 得分最高的那张当纵向探测锚
    if len(xs) >= 2:
        steps = sorted(xs[i + 1] - xs[i] for i in range(len(xs) - 1))
        pitch = int(round(steps[len(steps) // 2]))
    else:
        pitch = CARD_PITCH_FALLBACK
    if pitch < 40:                     # 量出来的间距太离谱 → 用兜底值
        pitch = CARD_PITCH_FALLBACK

    # 纵向修中心：从匹配框中心往上/下扫，找卡片真实的上下边
    row_y = int(top_sc[1])
    up = down = row_y
    y = row_y
    for _ in range(20):
        y -= 4
        if y < 0 or not _card_at(img, top_sc[0], y):
            break
        up = y
    y = row_y
    for _ in range(20):
        y += 4
        if y > img.height - 1 or not _card_at(img, top_sc[0], y):
            break
        down = y
    row_y = int((up + down) / 2)

    left, right = xs[0], xs[-1]
    for _ in range(12):                # 往左外推
        nx = left - pitch
        if nx < 8 or not _card_at(img, nx, row_y):
            break
        left = nx
    for _ in range(12):                # 往右外推
        nx = right + pitch
        if nx > img.width - 8 or not _card_at(img, nx, row_y):
            break
        right = nx
    # 只保留确实有卡片的位置（外推区间内按 pitch 取整）
    slots = []
    x = left
    while x <= right + pitch // 2:
        if _card_at(img, x, row_y):
            slots.append(int(x))
        x += pitch
    return slots, row_y


def window_root_at(x, y):
    """屏幕坐标 (x,y) 上**实际会收到点击**的顶层窗口句柄（护栏用）。

    剪映窗口被微信之类盖住时，这里返回的是**别的窗口** —— 那就绝不能点。"""
    try:
        h = user32.WindowFromPoint(wt.POINT(int(x), int(y)))
        return user32.GetAncestor(h, 2) if h else 0     # GA_ROOT = 2
    except Exception:
        return 0


def mouse_double_click(x, y, restore=True):
    """双击 (x,y)，**点完把光标还原到原位**（尽量少打扰）。

    ★ 两次单击的间隔必须小于系统双击时限（默认 500ms）。所以这里 sleep 比
      `mouse_click` 短得多 —— 用 mouse_click 连调两次会间隔 ~0.4s，时好时坏。
      实测：首页**单击不会打开草稿，必须双击**。"""
    try:
        old = wt.POINT()
        user32.GetCursorPos(ctypes.byref(old))
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.2)
        for _ in range(2):
            user32.mouse_event(0x0002, 0, 0, 0, 0)      # down
            time.sleep(0.05)
            user32.mouse_event(0x0004, 0, 0, 0, 0)      # up
            time.sleep(0.09)
        time.sleep(0.3)
        if restore:
            user32.SetCursorPos(int(old.x), int(old.y))
        return True
    except Exception as e:
        _plog(f"双击异常：{e}")
        return False


def open_draft_by_card(hwnd, draft_dir, st=None, timeout=20, min_score=0.55,
                       max_tries=3):
    """★ 自动打开指定草稿：认准卡片槽位 → 双击 → **用 `.locked` 验证真的进去了**。

    返回实际打开的草稿名；没点、点错、或没进去都返回 None（**绝不假装成功**）。

    ★★★ 2026-09-18 重要修正 —— **不要相信封面匹配的排序**。
      实测两份草稿的封面都是剪映的**占位图**（「媒体丢失」vs「媒体格式不支持」），
      两张卡片上只有中间一小块文字不同（1920x1080 里那二十来个像素高的小字），
      于是"自己 vs 别人"的相关系数只差 **0.047**（0.694 / 0.647），
      随便一点亮度/噪声扰动就能翻盘 → **会点到隔壁那张卡 → 打开错误的草稿 →
      正好撞上用户最烦的「媒体格式不支持」**。
      所以排序不再用分数，改用**剪映自己的显示顺序**：
        · `root_meta_info.json` 的 `all_draft_store` 数组顺序 = 首页卡片左右顺序
          （已用卡片上的 "92.9K / 56.0M" 和元数据里的 materials_size 一一对上）。
        · 流程里目标草稿刚被预合成 + 保存过 ⇒ 它一定是最新改动的 ⇒ **第 0 张**。
      封面匹配只借它**很准的横向位置**（实测中心 x 294/406，真值 294/407），
      不借它的分数排序。

    ★★★ 三重护栏（任何一道不过就交回手动，绝不乱点）：
      ① 点之前：`window_root_at` 确认那个坐标上真的是剪映（没被别的窗口盖住），
         并且 `_card_at` 确认那里确实有一张卡片（不是空白）；
      ② 点之后：用 `open_draft_name(root, since=点击前一刻)` **验证**。
         点错了 → 立刻退回首页、换一张卡再试（最多 `max_tries` 次）；
      ③ 同一个草稿被重复打开、或候选槽位用尽 → 直接放弃，把控制权交回用户。
    """
    def say(t):
        if st:
            try:
                st(t)
            except Exception:
                pass

    name = Path(draft_dir).name
    cover_p = draft_cover_path(draft_dir)
    if not cover_p:
        _plog(f"自动打开草稿：{name} 没有 draft_cover.jpg，无法定位卡片 → 交回手动")
        return None
    try:
        cover = Image.open(cover_p).convert("RGB")
    except Exception as e:
        _plog(f"自动打开草稿：封面读不出（{e}）→ 交回手动")
        return None

    root = Path(draft_dir).parent
    order = draft_display_order(root)          # 屏上从左到右的草稿名
    try:
        idx = order.index(name)
    except ValueError:
        idx = 0
        _plog(f"自动打开草稿：{name} 不在显示顺序里 → 按第 0 张处理")
    t0 = time.time()
    ctx = _dpi_aware()
    tried = []
    tried_names = []
    try:
        for attempt in range(1 + max_tries):
            # 先清模态框（「链接媒体」会锁住焦点 → activate 必失败 → 首页判断也失真）
            dismiss_jy_modals()
            activate(hwnd)
            close_jy_popups()
            img = grab_window_image(hwnd)
            if img is None:
                time.sleep(1.5)
                continue
            if not is_home_page(img):
                if attempt == 0:
                    # 不在首页 → 可能用户自己已经打开了草稿。**但只有打开的是目标
                    # 才算数**：实测踩过——当前打开的是「9月17日」，而目标是「9月18日」，
                    # 这里原样返回「9月17日」，调用方 `if got:` 就当成成功了，
                    # 于是后面在**错误的草稿**里拖文件 → 正好撞上「媒体格式不支持」。
                    cur = open_draft_name(root, since=t0)
                    if cur == name:
                        _plog(f"自动打开草稿：调用时已经在「{cur}」里了")
                        return cur
                    if cur:
                        _plog(f"自动打开草稿：当前在「{cur}」里，不是目标「{name}」→ 交回手动")
                    return None
                time.sleep(2.0)          # 刚点错、正在退回首页
                continue
            # ★ 只在下半页找卡片：首页上半部分是横幅 + 模板推荐，封面匹配在那里
            #   只有零星假峰，既浪费时间又可能把行锚点带偏。裁掉上半页再搜，
            #   实测把 `locate_draft_card` 从 4.5s 压到 ~1.5s，位置一点没偏。
            ymin = int(img.height * _CARD_ROW_YMIN)
            sub = img.crop((0, ymin, img.width, img.height))
            hits = [(r, x, y + ymin, w, h) for r, x, y, w, h in
                    locate_draft_card(sub, cover)]
            if not hits or hits[0][0] < min_score:
                top = hits[0][0] if hits else -9
                _plog(f"自动打开草稿：封面匹配置信度不足（{top:+.3f} < {min_score}）"
                      " → 不用鼠标，交回手动")
                say("认不准是哪张卡片，请自己在首页双击草稿",
                    sub="剪映首页那一排卡片，双击你要的那张")
                return None
            slots, row_y = _card_row_slots(img, hits)
            r = window_rect(hwnd)
            if not r:
                time.sleep(1.5)
                continue
            # ★ 目标槽位：**显示顺序里的第 idx 张**。
            #   流程里目标刚被预合成 + 保存过 → 它一定是最新改动的 → idx=0 → 最左那张。
            #   所以第一枪就打"最左"，而不是打"封面匹配分最高的那张"——
            #   后者在两份封面长得像的时候会认错（实测分差只有 0.047）。
            plan = []
            if slots:
                if 0 <= idx < len(slots):
                    plan.append(slots[idx])
                for x in slots:
                    if x not in plan:
                        plan.append(x)
            if not plan:
                plan = [h[1] + h[3] // 2 for h in hits]
            nx = next((x for x in plan if x not in tried), None)
            if nx is None:
                _plog("自动打开草稿：候选卡片槽位都试过了 → 交回手动")
                say("没找到那份草稿，请自己在首页双击打开",
                    sub="卡片可能在列表后面，往下翻翻再双击")
                return None
            tried.append(nx)
            if not _card_at(img, nx, row_y):
                _plog(f"自动打开草稿：槽位 x={nx} 处不像卡片，跳过")
                continue
            sx, sy = r[0] + nx, r[1] + row_y
            under = window_root_at(sx, sy)
            if under != hwnd:
                _plog(f"自动打开草稿：({sx},{sy}) 上不是剪映（hwnd={under}）"
                      " → 被别的窗口盖住，不点")
                say("剪映被别的窗口盖住了，请自己点开草稿",
                    sub="先把剪映露出来，再双击首页的草稿卡片")
                return None
            _plog(f"自动打开草稿：显示顺序 #{idx}「{name}」→ 槽位 x={nx} 行 y={row_y}"
                  f" → 屏幕({sx},{sy})，双击（第 {attempt + 1} 次）")
            say(f"自动打开草稿「{name}」（显示顺序第 {idx + 1} 张）")
            t_click = time.time()
            mouse_double_click(sx, sy)
            got = None
            for _ in range(max(4, int(timeout))):
                time.sleep(1.0)
                # ★ since 用**本次点击之前**的时刻：上一次点错留下的旧锁不会被误认成
                #    "这次点开了"。这是"验证"能成立的关键。
                got = open_draft_name(root, since=t_click)
                if got:
                    break
            if got == name:
                _plog(f"自动打开草稿：✅ 已进入「{got}」")
                return got
            if not got:
                _plog(f"自动打开草稿：第 {attempt + 1} 次没进去，换下一个槽位")
                continue
            # 点开了**别的**草稿 → 立刻退回首页，换一张再试（绝不在这里继续做事）
            _plog(f"自动打开草稿：❌ 点到的是「{got}」（目标「{name}」）→ 退回首页换一张")
            say(f"点到的是「{got}」，退回首页换一张…")
            send_combo(read_shortcut("returnDraftPage", "Ctrl+Alt+Q"))
            time.sleep(3.0)
            if got in tried_names:
                _plog("自动打开草稿：同一个草稿又开了一次，再试没意义 → 交回手动")
                return None
            tried_names.append(got)
        return None
    finally:
        _dpi_restore(ctx)


def ensure_edit_page(hwnd, st=None, timeout=50, allow_click=False, draft_hint="",
                     draft_dir=None, auto_open=False, cancel=None):
    """确保剪映停在草稿编辑页。

    ★ 2026-09-17 改动：默认**不再用鼠标点草稿卡片**。原来停在首页时会自动点
    「第一张草稿卡片」进草稿——这既是用户抱怨的"控制鼠标进草稿体验很差"，
    更有打开**错误草稿**的风险。现在默认只检测、不上手：不在编辑页就返回 False，
    由调用方提示用户手动进去（反正用户本来就得在草稿里选中片段才能按快捷键）。
    真要恢复旧行为，把 cfg["allow_mouse_fallback"] 打开。

    ★ 2026-09-18 补 `draft_hint`：**必须回到做预合成的那个草稿**。
      预合成登记（`combination_id`）只写在做预合成的那份草稿里，导入也只在那份
      草稿里认缓存；打开别的草稿/新建草稿 → 必报「媒体格式不支持」。
      所以提示里直接点名草稿，免得用户点错卡片。

    判定主信号：编辑页是 GPU 渲染，PrintWindow 得到全黑图；
    能渲染出内容且顶部区域偏彩色（G-R≥12）才算首页。"""
    def say(t, sub=None):
        # ★ 2026-09-19 第十三批：`sub` = 第二行提示（"怎么做"和"为什么"分开放）。
        #   走 `_call_status` 而不是直接 `st(t)` —— 老回调只收 2 个参数，
        #   这里必须能优雅退回（GUI 主路径收 5 个）。
        _call_status(st, t, "busy", None, None, sub)

    ctx = _dpi_aware()
    try:
        t0 = time.time()
        auto_tried = False
        while time.time() - t0 < timeout:
            if _cancelled(cancel):
                _plog("等编辑页被中止")
                return False
            dismiss_jy_modals()
            activate(hwnd)
            close_jy_popups()
            img = grab_window_image(hwnd)
            if img is None:
                time.sleep(2)
                continue
            bright = ImageStat.Stat(img.convert("L")).mean[0]
            if bright < 8:
                # 黑屏可能是编辑页（GPU 渲染），也可能是弹窗关闭瞬间的假象。
                # 必须 1.5s 后再抓一次仍是黑屏才采信。
                time.sleep(1.5)
                img_chk = grab_window_image(hwnd)
                if img_chk is not None and ImageStat.Stat(img_chk.convert("L")).mean[0] < 8:
                    say("已在编辑页")
                    return True
                continue
            if not is_home_page(img):
                # 有内容但不是首页特征 → 一般是编辑页。**但要先排除一种误判**：
                # 「链接媒体」这类**模态框盖在首页上方**时，顶部那一块会变暗，
                # `is_home_page` 就不认了 → 会误判成"已在编辑页"从而**跳过自动开草稿**。
                # 所以先清一次模态框再看一眼，如果露出首页就重新判定。
                if dismiss_jy_modals():
                    time.sleep(1.5)
                    img2 = grab_window_image(hwnd)
                    if img2 is not None and is_home_page(img2):
                        continue
                say("已在编辑页")
                return True
            if auto_open and draft_dir and not auto_tried:
                # ★ 2026-09-18（用户要求"不动鼠标也能自己打开原草稿"）：
                #   用**剪映自己的显示顺序**挑槽位 → 双击 → 用 `.locked` 验证。
                #   点错会退回首页换一张；实在不行返回 None，落到下面"等用户自己点"。
                auto_tried = True
                got = open_draft_by_card(hwnd, draft_dir, st)
                if got and got == Path(draft_dir).name:
                    say("已在编辑页")
                    return True
                if got:
                    # 理论上到不了这里（open_draft_by_card 只返回目标名或 None），
                    # 但真出现就绝不能当成成功 —— 在错误草稿里继续做等于必报
                    # 「媒体格式不支持」。
                    _plog(f"!! 自动打开返回了非目标草稿「{got}」，按失败处理")
                    say(f"打开的草稿不对（{got}），请自己点开「{draft_hint or '目标草稿'}」",
                        sub="必须回到做预合成的那份，换草稿会报「媒体格式不支持」")
            if not allow_click:
                # ★ 2026-09-17：原来这里直接 return False，把"不用鼠标"做成了硬失败——
                #   实测后果：同草稿流程在剪映重启后停在首页，直接失败，
                #   后面"清空时间线"那一步就白跑一趟（日志里那条
                #   「停在首页且未开启鼠标兜底 → 请用户手动打开草稿后重试」就是它）。
                #   改成**继续等用户自己打开草稿**：不碰鼠标、不点错草稿，
                #   但把控制权还给用户之后流程能自动接上。
                left = int(timeout - (time.time() - t0))
                if draft_hint:
                    # ★ 2026-09-19 第十三批（用户："提示不够"）：原来只有一句
                    #   "请打开草稿「X」（必须这个）"—— 没说**在哪打开、怎么打开**。
                    #   剪映的草稿只能靠**双击首页那张卡片**进（没有任何非鼠标入口，
                    #   见"三个定案"），用户不知道就会干等。
                    #   主文案 = 怎么做（双击卡片）；副文案 = 为什么必须这份。
                    say(f"请双击草稿「{draft_hint}」卡片打开… {left}s",
                        sub="必须这份 · 换草稿会报「媒体格式不支持」")
                    _plog(f"停在首页：要求用户打开**原本那份**草稿「{draft_hint}」"
                          "（预合成登记只写在它里面，换草稿必报「媒体格式不支持」）")
                else:
                    say("请双击首页上的草稿卡片打开", sub="打开后我会自动继续（进度条还在跑）")
                    _plog("停在首页：不点草稿卡片（怕点错），等用户自己打开草稿…")
                time.sleep(2)
                continue
            card = find_card_center(img)
            if not card:
                say("等待草稿列表…")
                time.sleep(2)
                continue
            r = window_rect(hwnd)
            if not r:
                time.sleep(2)
                continue
            say("正在打开草稿…")
            _plog(f"点击草稿卡片 相对={card}")
            mouse_click(r[0] + card[0], r[1] + card[1])
            # 等页面切换（最长 20s）
            for _ in range(10):
                if _cancelled(cancel):
                    _plog("等页面切换被中止")
                    return False
                time.sleep(2)
                img2 = grab_window_image(hwnd)
                if img2 is None:
                    continue
                b2 = ImageStat.Stat(img2.convert("L")).mean[0]
                if b2 < 8:
                    time.sleep(1.5)
                    img3 = grab_window_image(hwnd)
                    if img3 is not None and ImageStat.Stat(img3.convert("L")).mean[0] < 8:
                        _plog("已进入编辑页（PrintWindow 黑屏特征）")
                        time.sleep(1.5)
                        return True
        _plog("确保编辑页超时")
        return False
    finally:
        _dpi_restore(ctx)


# ================================================================ 按键
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
MOD_KEYS = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
NAMED = {"return": 0x0D, "enter": 0x0D, "space": 0x20, "tab": 0x09, "esc": 0x1B,
         "delete": 0x2E, "del": 0x2E, "backspace": 0x08, "back": 0x08,
         # ★ 方向键 / 翻页键（2026-09-18 加）：用来在首页卡片网格里用**纯键盘**
         #   移动选中卡片（实验用；实测 CEF 首页并不吃方向键，见 open_draft_by_card 注释）。
         "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
         "home": 0x24, "end": 0x23, "pgup": 0x21, "pgdn": 0x22,
         "insert": 0x2D}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _anonymous_ = ("i",)
    _fields_ = [("type", wt.DWORD), ("i", _I)]


def _send(vk, up=False):
    # 注意：本机环境下 SendInput 的键盘注入会被吞掉（鼠标 mouse_event 正常），
    # 必须用老式 keybd_event 才能真正送达剪映。实测 2026-09-09。
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if up else 0, 0)


def vk_of(token):
    t = token.lower()
    if t in MOD_KEYS:
        return MOD_KEYS[t]
    if t in NAMED:
        return NAMED[t]
    if len(t) == 2 and t[0] == "f" and t[1:].isdigit():
        return 0x6F + int(t[1:])
    if len(t) == 1:
        r = user32.VkKeyScanW(ctypes.c_wchar(t))
        if r == -1:
            raise ValueError(f"按键无法识别：{token}")
        return r & 0xFF
    raise ValueError(f"按键无法识别：{token}")


def send_combo(combo, hold=0.06):
    parts = [p for p in combo.replace("+", " ").split() if p]
    if not parts:
        return
    *mods, main = parts
    vks = [vk_of(m) for m in mods]
    for v in vks:
        _send(v, False)
    time.sleep(hold)
    mv = vk_of(main)
    _send(mv, False)
    time.sleep(0.05)
    _send(mv, True)
    time.sleep(hold)
    for v in reversed(vks):
        _send(v, True)
    time.sleep(0.12)


# ================================================================ 剪贴板
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002


class DROPFILES(ctypes.Structure):
    _fields_ = [("pFiles", wt.DWORD), ("pt", wt.POINT), ("fNC", wt.BOOL), ("fWide", wt.BOOL)]


def copy_file_to_clipboard(path):
    """写入剪贴板两种格式：
    - CF_HDROP：用于文件拖放与 Ctrl+V 粘贴到支持 HDROP 的控件
    - CF_UNICODETEXT：用于往文件名输入框等普通文本框 Ctrl+V
    """
    for attempt in range(3):
        try:
            data = (str(path) + "\0\0").encode("utf-16-le")
            size = ctypes.sizeof(DROPFILES) + len(data)
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not h:
                return False
            p = kernel32.GlobalLock(h)
            if not p:
                kernel32.GlobalFree(h)
                return False
            df = DROPFILES()
            df.pFiles = ctypes.sizeof(DROPFILES)
            df.pt = wt.POINT(0, 0)
            df.fNC = False
            df.fWide = True
            ctypes.memmove(p, ctypes.byref(df), ctypes.sizeof(DROPFILES))
            ctypes.memmove(p + ctypes.sizeof(DROPFILES), data, len(data))
            kernel32.GlobalUnlock(h)

            text = str(path)
            text_bytes = (text + "\0").encode("utf-16-le")
            h_text = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(text_bytes))
            if h_text:
                p_text = kernel32.GlobalLock(h_text)
                if p_text:
                    ctypes.memmove(p_text, text_bytes, len(text_bytes))
                    kernel32.GlobalUnlock(h_text)
                else:
                    h_text = None

            if not user32.OpenClipboard(None):
                kernel32.GlobalFree(h)
                if h_text:
                    kernel32.GlobalFree(h_text)
                time.sleep(0.8)
                continue
            user32.EmptyClipboard()
            ok1 = user32.SetClipboardData(15, h)   # CF_HDROP
            ok2 = True
            if h_text:
                ok2 = user32.SetClipboardData(13, h_text)  # CF_UNICODETEXT
            user32.CloseClipboard()
            if not ok1 or not ok2:
                if h_text:
                    kernel32.GlobalFree(h_text)
                time.sleep(0.8)
                continue
            return True
        except Exception:
            time.sleep(0.8)
    return False


# ================================================================ 草稿扫描
# 兜底浅搜时**不要进去**的目录名（小写）。进了只会白耗时间。
SCAN_SKIP_DIRS = {
    "windows", "$recycle.bin", "system volume information", "temp", "tmp",
    "node_modules", "cache", "caches", ".git", ".svn", "program files",
    "program files (x86)", "recovery", "perflogs", "msocache",
}


def _shallow_find(base, name, max_depth=4, budget=20.0, max_dirs=20000):
    """在 base 里**浅层**找目录名等于 name 的目录（限深度 + 限时间 + 限目录数）。

    ★★ 为什么不用 `Path.rglob`（2026-09-18 第八批 · 分发）：
      `rglob` 会**全盘无深度限制递归**。作者机器上草稿目录已在配置里、根本走不到
      这条兜底；但**别人第一次运行**时配置是空的，一旦剪映用了自定义草稿路径，
      就会在这里卡几分钟 —— 界面看着像死机，第一印象直接崩掉。
      改成"最多 4 层 / 最多 20 秒 / 最多 2 万目录"，超了就当找不到（宁可让他手动填）。
    """
    t0 = time.time()
    seen = 0
    stack = [(Path(base), 0)]
    while stack:
        d, depth = stack.pop()
        if depth >= max_depth:
            continue
        try:
            for child in d.iterdir():
                seen += 1
                if seen > max_dirs or time.time() - t0 > budget:
                    return None
                try:
                    if not child.is_dir():
                        continue
                except OSError:
                    continue
                if child.name == name:
                    return child
                if child.name.lower() in SCAN_SKIP_DIRS:
                    continue
                stack.append((child, depth + 1))
        except Exception:
            continue
    return None


def resolve_root(cfg, status_cb=None):
    """草稿根目录 = 谁真正装着剪映的草稿。

    优先级：**用户手动指定的**（菜单「设置草稿目录…」）> 缓存值 > 常见落点 > 浅搜兜底。

    ★★ 2026-09-19 第十五批（用户"继续优化"）：加了一条"别被空目录钉死"的规则。
      旧逻辑是"第一个**存在**的候选就写进配置并永久返回"。问题在于
      **存在 ≠ 是对的**：一个残留空目录（比如以前建的 `C:\\剪映草稿`）只要恰好排在
      真目录前面，就会被**永久钉进 伴侣配置.json**，之后每次启动都直接返回它 ——
      用户的表现是"体检全通过，但一键导出说找不到草稿"，而且**没有任何办法纠正**。
      现在：缓存的根里**没有**预合成内容、而别的候选**有**时，改用它（记日志）。
      ★ 有界：只在"缓存根里没内容"时才去看别的候选，且最多看 3 个、总预算 3 秒；
        正常机器上第一个候选就是对的，行为和以前完全一样。
    """
    cur = str(cfg.get("draft_root") or "").strip()
    if cur and Path(cur).is_dir():
        if draft_root_has_combos(cur, budget=3.0, max_dirs=1200):
            return Path(cur)
        # 缓存根里没东西 —— 换哪个？
        # ★★ 第十六批：**先问剪映自己**（`currentCustomDraftPath` 是权威的
        #   "用户当前真正在用哪个位置"），它说的话直接采信；问不到再退到
        #   "常见落点里挑一个真有内容的"。用户把草稿挪走之后，这一步自己跟上。
        better, authoritative = None, False
        try:
            _j = jianying_setting_draft_path()
            if _j and str(_j).lower() != cur.lower():
                better, authoritative = _j, True
        except Exception:
            better = None
        if better is None:
            better = _pick_standard_root()
        try:
            same = bool(better) and str(better).lower() == cur.lower()
        except Exception:
            same = True
        if better and not same and not _is_parent_root(better) \
                and (authoritative or draft_root_has_combos(better)):
            _plog(f"草稿根：缓存里的 {cur} 没有预合成产物，改用有内容的 {better}")
            cfg["draft_root"] = str(better)
            save_config(cfg)
            return better
        _plog(f"草稿根：缓存里的 {cur} 没有预合成产物，但也没找到更好的，继续用它")
        return Path(cur)
    r = _pick_standard_root()
    if r:
        cfg["draft_root"] = str(r)
        save_config(cfg)
        return r
    # 兜底：浅搜（限深度/限时，见 _shallow_find 注释）
    if status_cb:
        try:
            status_cb("找不到草稿目录，正在浅层搜索…", "busy")
        except Exception:
            pass
    for drv in "CDEF":
        base = Path(f"{drv}:" + os.sep)
        if not base.exists():
            continue
        p = _shallow_find(base, "com.lveditor.draft")
        if p:
            cfg["draft_root"] = str(p)
            save_config(cfg)
            return p
    return None


def pick_combo_file(combo):
    # 注意1：alpha 文件命名形如 xxx.mp4.alpha.mp4，后缀仍是 .mp4，必须按"含 .alpha"排除。
    # 注意2：渲染进行中剪映会写 xxx_video.mp4_temp.mp4 临时文件（48 字节起步），
    #        也以 .mp4 结尾，必须按"含 _temp"排除——只认渲染完成的正式文件。
    files = [f for f in combo.iterdir()
             if f.is_file() and f.suffix.lower() in MEDIA_EXT
             and ".alpha" not in f.name.lower()
             and "_temp" not in f.name.lower()]
    if not files:
        return None
    return max(files, key=lambda f: (f.stat().st_mtime, f.stat().st_size))


def combo_dirs(root):
    out = []
    seen = set()
    for combo in root.rglob("combination"):
        if not combo.is_dir() or str(combo).lower() in seen:
            continue
        seen.add(str(combo).lower())
        f = pick_combo_file(combo)
        if f:
            out.append((combo.parent.parent, combo, f.stat().st_mtime))
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def draft_root_has_combos(root, budget=1.5, max_dirs=400):
    """这个草稿根底下**有没有预合成产物**（限时 / 限目录数，绝不全盘慢扫）。

    ★ 2026-09-19 第十五批（用户"继续优化"）：为什么需要它 ——
      「目录存在」和「目录是对的」**不是一回事**。
      旧的环境体检只判 `Path(dr).is_dir()`，于是把 伴侣配置.json 里一个
      **早就失效/指错的**目录当成 OK 报给用户（假绿）；而 resolve_root 又会
      把这个目录永久钉在配置里。用户看到的是"体检全通过，但一键导出说找不到草稿"。
      这里用一个**有界**的检查来回答"里面到底有没有东西"：
      剪映的草稿结构很浅（根/<草稿名>/Resources/combination），所以
      限深 4 层 + 限 400 个目录 + 1.5s 预算就够了，正常几十毫秒返回。
    """
    base = Path(root)
    if not base.is_dir():
        return False
    t0 = time.time()
    stack = [(base, 0)]
    n = 0
    while stack:
        d, depth = stack.pop()
        n += 1
        if n > max_dirs or time.time() - t0 > budget:
            return False
        try:
            for child in d.iterdir():
                if not child.is_dir():
                    continue
                if child.name.lower() == "combination":
                    return True
                if depth < 3 and not child.name.startswith("."):
                    stack.append((child, depth + 1))
        except Exception:
            continue
    return False


def standard_draft_roots():
    """剪映草稿目录的常见落点（**保序**），resolve_root / 体检共用一份。

    ★ 2026-09-19 第十五批：原来这段候选列表**只写在 resolve_root 里面**，
      体检那边只能退而求其次地读 `cfg["draft_root"]`（于是就有了"目录在=OK"的假绿）。
      抽出来共用，两个地方才不会再各说各的。
    """
    roots = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        roots.append(Path(local) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft")
        roots.append(Path(local) / "JianyingPro" / "User Data" / "Projects")
    roots.append(Path.home() / "Documents" / "JianyingPro Drafts")
    # ★★ 盘根候选必须写成 `Path("C:/")`，**不能**写 `Path("C:")`（2026-09-18 第十批）。
    #   `Path("C:") / "x"` 得到的是**驱动器相对路径** `'C:x'`，意思是"当前在 C 盘上的
    #   工作目录下的 x"，不是 `C:\x` —— 于是这 10 条候选（5 个盘 × 2 个名字）
    #   **永远匹配不上**，自定义放在盘根的草稿只能靠后面那条慢速浅搜兜底。
    #   （实测：`Path("C:") / "JianyingPro Drafts"` → `'C:JianyingPro Drafts'`，
    #     而且 `Path("C:").is_dir()` 竟然返回 True，更容易让人误判。）
    for drv in "CDEFG":
        roots.append(Path(f"{drv}:/") / "JianyingPro Drafts")
        roots.append(Path(f"{drv}:/") / "剪映草稿")
    return roots


def _is_parent_root(cand):
    """这个候选是不是"**别的候选的父目录**"？

    ★ 为什么要单独判（2026-09-19 第十五批）：候选表里第 2 条是
      `…\\Projects`，而第 1 条 `…\\Projects\\com.lveditor.draft` 就在它**里面**。
      父目录当草稿根有两个恶果：
        · `open_draft_name(root)` 只看 root 的**一级子目录**里的 `.locked` ——
          根指到上一级就再也认不出"用户当前打开的是哪份草稿"，
          而那条是"必须回到原来那份草稿"红线的地基；
        · 草稿名/还原目录跟着一起错。
      所以"优先挑有内容的候选"这条规则里要排除掉父目录：一个父目录
      **因为包含子候选而有内容**，不等于它自己就是合格的草稿根。
    """
    cs = str(cand).lower().rstrip("\\/")
    for other in standard_draft_roots():
        os_ = str(other).lower().rstrip("\\/")
        _inside = os_.startswith(cs + "\\") or os_.startswith(cs + "/")
        if os_ == cs or not _inside:
            continue
        try:
            if other.is_dir():
                return True
        except Exception:
            pass
    return False


def _pick_standard_root(budget=3.0, max_checked=3):
    """常见落点里挑一个：优先**真有预合成产物**的，退而求其次要第一个存在的。

    ★ 为什么不是"第一个存在的就用"（2026-09-19 第十五批）：一个残留空目录
      （以前建的 `C:\\剪映草稿` 之类）只要恰好排在真目录前面，就会被选中、
      被写进配置、然后**永久**生效。有界（最多细看 3 个、总预算 3 秒）地
      优先挑"里面真有东西"的那个，代价可忽略，收益是"指错了能自愈"。
    ★ 父目录（`…\\Projects`）不参与"优先"竞争，见 `_is_parent_root`。
    """
    first = None
    t0 = time.time()
    checked = 0
    for r in standard_draft_roots():
        try:
            ok = r.is_dir()
        except Exception:
            ok = False
        if not ok:
            continue
        if first is None:
            first = r
        if checked >= max_checked or time.time() - t0 > budget:
            continue                     # 不再细看，但仍继续找 first
        checked += 1
        if draft_root_has_combos(r) and not _is_parent_root(r):
            return r
    return first


def draft_root_status(cfg):
    """体检用：草稿目录到底是"好了" / "有疑问" / "没有"。返回 (级别, 标题, 详情)。

    ★ 三态而不是两态（2026-09-19 第十五批）：把"目录在、里面却是空的"单列成 warn ——
      那是**指错了**最典型的样子，必须让用户看见，而不是报 OK 骗他。
    """
    dr = str(cfg.get("draft_root") or "").strip()
    if dr and Path(dr).is_dir():
        if draft_root_has_combos(dr):
            return ("ok", "草稿目录：找到", f"{dr}\n（里面有预合成产物）")
        return ("warn", "草稿目录：目录在，但没找到预合成产物",
                f"{dr}\n\n"
                "这个路径**可能不是**剪映真正在用的草稿目录 ——\n"
                "剪映的草稿根一般叫 com.lveditor.draft，里面是「草稿名/Resources/combination」。\n"
                "如果点「一键导出」报找不到草稿，请右键伴侣 →「设置草稿目录…」重新指一下。")
    if dr:
        return ("warn", "草稿目录：上次记的路径已经不在了",
                f"{dr}\n\n"
                "伴侣会重新自动找一遍（**也会去读剪映设置里记的草稿位置**）；\n"
                "两边都找不到，才需要右键 →「设置草稿目录…」手动指一下。")
    r = _pick_standard_root()
    if r:
        return ("ok", "草稿目录：找到", str(r))
    return ("bad", "草稿目录：找不到",
            "剪映默认放在 %LOCALAPPDATA%/JianyingPro/User Data/Projects/com.lveditor.draft。\n"
            "伴侣也会去读剪映设置（设置 → 草稿位置）里记的那个位置；\n"
            "两边都没有，才需要右键伴侣 →「设置草稿目录…」手动指一下。")


# 自动配置**允许写进配置文件**的键（其余键一律不落盘，见 _save_autosetup_keys）。
AUTOSETUP_KEYS = ("jianying_exe", "draft_root", "_autosetup_done", "_autosetup_at")


def _save_autosetup_keys(cfg):
    """把"自动配置该写的键"**合并**进磁盘上的配置文件；其余内容原样保留。

    ★★ 为什么不能用 `save_config(cfg)`（2026-09-19 第十六批 · 差点踩进去）：
      `autodetect_env()` 手里的 cfg 是 `load_config()` 的产物 = **内置默认值打底
      + 文件覆盖**。整份写回去 = 把"今天的内置默认值"钉死在用户机器上 ——
      以后改了默认值，老用户**永远收不到**（第九批定的规矩，`[Files]` 段还专门
      写了"故意不写 伴侣配置.json"）。所以这里只挑这几个**机器事实**键合并，
      别的一律不动。测试 `test_guard [26]` 直接断言"落盘键 ⊆ 这四个"。
    """
    path = config_path()
    data = {}
    try:
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                data = old
    except Exception:
        data = {}
    for k in AUTOSETUP_KEYS:
        if k in cfg:
            data[k] = cfg[k]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def autodetect_env(cfg=None, save=True):
    """★ 把"这台机器的环境"问清楚并**写进 伴侣配置.json**（安装时 / 第一次启动时调一次）。

    ★★ 2026-09-19 第十六批。用户原话：
      「我觉得最好是安装时自动帮用户搞的很好，不让用户多余操作」。

    第十四/十五批加的两个菜单入口（「设置剪映路径…」「设置草稿目录…」）本质是
    **兜底路** —— 它们存在，就说明默认情况没能自动认出来。这一版把默认情况做成
    **真自动**，用户一个菜单都不用点：

      · 剪映在哪 → 问正在跑的进程 → `%LOCALAPPDATA%\\JianyingPro\\Apps\\<版本>\\JianyingPro.exe`
        → 其它常见落点（`find_jianying_exe`，全程不递归扫盘）；
      · 草稿在哪 → **问剪映自己的设置**（`currentCustomDraftPath`，最权威）
        → 常见落点里挑一个"真有预合成产物"的（`_pick_standard_root`）。

    ★ 和第九批那条老规矩（"安装时**不要**写 伴侣配置.json"）怎么共存：
      老规矩禁止的是把**内置默认值**抄成文件（那样以后改默认值永远到不了老用户）。
      这里只写**这台机器上测出来的事实**：剪映路径、草稿位置 —— 代码永远猜不到，
      不写才会逼用户去点菜单。默认值仍然只在代码里，文件里就这几项 + 两个记账位。
    ★ 手动值优先：用户明确指过且那个文件还在 → 一个字都不改（不抢用户的决定）；
      指过但文件已经不在了 → 清掉，让自动发现接手（别让一个死路径永远挡住）。

    返回一份报告 dict（GUI 体检 / 安装包都要照着念给用户听）。
    """
    if cfg is None:
        cfg = load_config()
    rep = {"jianying_exe": None, "jianying_how": "", "draft_root": None,
           "draft_how": "", "changed": [], "saved": False}

    # ① 剪映本体 —— 先清掉"已失效的手动指定"，别让它挡住自动发现
    manual = str(cfg.get("jianying_exe") or "").strip()
    if manual:
        try:
            alive = Path(manual).is_file()
        except Exception:
            alive = False
        if not alive:
            cfg["jianying_exe"] = ""
            manual = ""
            rep["changed"].append("剪映路径（清掉已经不在的手动指定）")
    try:
        exe = find_jianying_exe(cfg)
    except Exception:
        exe = None
    if exe:
        rep["jianying_exe"] = str(exe)
        if manual:
            rep["jianying_how"] = "沿用你手动指定的"
        else:
            rep["jianying_how"] = "伴侣自动找到的"
            cfg["jianying_exe"] = str(exe)
            rep["changed"].append("剪映路径")

    # ② 草稿目录 —— **先问剪映自己**（最准），再退到"常见落点里有内容的那个"
    cur = str(cfg.get("draft_root") or "").strip()
    dr, how = None, ""
    try:
        dr = jianying_setting_draft_path()
        if dr:
            how = "剪映设置里记着的位置"
    except Exception:
        dr = None
    if not dr:
        try:
            dr = _pick_standard_root()
        except Exception:
            dr = None
        if dr:
            how = "常见落点里找到的"
    if dr:
        rep["draft_root"] = str(dr)
        rep["draft_how"] = how
        if cur.lower() != str(dr).lower():
            # 只有当"现存的这个不好使"时才改（仍然有效的绝不覆盖）
            ok_cur = False
            if cur:
                try:
                    ok_cur = bool(draft_root_has_combos(cur, budget=1.5, max_dirs=400))
                except Exception:
                    ok_cur = False
            if not ok_cur:
                cfg["draft_root"] = str(dr)
                rep["changed"].append("草稿目录")

    # ③ 记账（"自动配置跑过了" —— 体检要据此告诉用户"你不用做任何事"）
    cfg["_autosetup_done"] = True
    cfg["_autosetup_at"] = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    if save:
        # ★ 什么时候才写盘（2026-09-19 第十六批，三次收窄后的规则）：
        #   ① 真有改动 → 写；
        #   ② 配置文件本来就在、但还没记过账（老用户升级上来）→ 补记一次；
        #   ③ 其它情况**一个字都不写** —— 免得每次体检/每次启动都去改
        #      `_autosetup_at`（那种"写了但等于没写"的写盘只会让文件时间戳失真）。
        #   ★ 一个都没认到、而且本来就没有配置文件 → **不许凭空造一个**
        #     （凭空造一个只有记账位的文件，会让"配置是否存在"这个判断从此失真）。
        existed = False
        old = None
        try:
            existed = config_path().exists()
            if existed:
                old = json.loads(config_path().read_text(encoding="utf-8"))
        except Exception:
            old = None
        done_before = bool(isinstance(old, dict) and old.get("_autosetup_done"))
        if rep["changed"] or (existed and not done_before):
            rep["saved"] = _save_autosetup_keys(cfg)
    return rep


def format_autosetup(rep):
    """把 `autodetect_env` 的结果排成几行人话（安装完成提示 / 日志 / 体检都用它）。

    ★ 不写 `**` 星号：这段文字会进 Inno 的原生 MsgBox（不认 markdown）。
    """
    out = []
    if rep.get("jianying_exe"):
        out.append(f"剪映：已找到（{rep.get('jianying_how') or '自动'}）")
        out.append(f"      {rep['jianying_exe']}")
    else:
        out.append("剪映：这台电脑上没找到。")
        out.append("      伴侣本身不含剪映，请先安装「剪映专业版」并打开过一次；")
        out.append("      装好之后伴侣下次启动会自己认，不用你指路。")
    if rep.get("draft_root"):
        out.append(f"草稿目录：已认好（{rep.get('draft_how') or '自动'}）")
        out.append(f"      {rep['draft_root']}")
    else:
        out.append("草稿目录：暂时没找到。")
        out.append("      第一次点「一键导出」时伴侣会再找一遍；")
        out.append("      实在找不到：右键伴侣 →「设置草稿目录…」手动指一次。")
    ch = rep.get("changed") or []
    if ch:
        out.append("已经自动写进配置：" + "、".join(ch) + "（你不需要做任何设置）")
    elif rep.get("jianying_exe") or rep.get("draft_root"):
        out.append("配置本来就是对的，没有需要改的。")
    else:
        out.append("这台机器上还没认出剪映/草稿位置 —— 装好剪映、打开过一次之后，")
        out.append("伴侣下次启动会自动再认一遍。")
    return "\n".join(out)


def write_autosetup_result(rep):
    """把自动配置结果写一份给**安装包**读（装完要照着念给用户听）。

    ★ 为什么落文件而不是靠 stdout：exe 是 GUI 子系统、没有控制台，
      Inno 的 `Exec` 拿不到输出。写个约定名的小文件最省事、也最好排查。
    ★★ 为什么是"系统 ANSI"编码（2026-09-19 第十六批 · 实测踩出来的坑）：
      Inno 的 `LoadStringFromFile` **只接受 `AnsiString` 变量** —— 传 `String`
      直接在编译期报 `Type mismatch`（踩了半小时才发现），而且它按 **ANSI**
      解释字节。所以这里必须写系统 ANSI（中文 Windows = GBK），
      安装包读出来才是正常中文。
    ★ 兜底：万一在**英文 Windows** 上（`mbcs` 编不出中文）→ 正文退回纯 ASCII，
      标记行照旧（安装包靠它分支），中文说明由安装包自己用 Pascal 讲。
      完整中文在 运行日志.txt（UTF-8）和 GUI 体检里都还在，信息不会丢。
    """
    path = config_path().parent / AUTOSETUP_RESULT
    # 标记行永远是纯 ASCII，安装包用 Pos 匹配（不怕任何编码问题）
    head = ("JIANYING=" + ("1" if rep.get("jianying_exe") else "0") + "\r\n"
            + "DRAFT=" + ("1" if rep.get("draft_root") else "0") + "\r\n\r\n")
    data = head.encode("ascii")
    try:
        data += (format_autosetup(rep) + "\r\n").encode("mbcs")
    except Exception:
        data += b"(details in the log file)\r\n"
    try:
        path.write_bytes(data)
        return path
    except Exception:
        return None


def open_draft_name(root, since=0):
    """当前**打开着**的草稿名（只读目录里的锁文件，不碰任何内容）。

    剪映打开一份草稿时会在它目录下写一个 `.locked`；草稿被删会连 `.locked`
    一起进 `.recycle_bin`，所以只看草稿根目录的一级子目录就够。
    `since`（时间戳）用来过滤：只认「本次重启之后」才更新的锁，
    免得把上一份刚关掉的草稿误报成"你打开了别的草稿"。
    """
    best, name = None, None
    try:
        for d in Path(root).iterdir():
            lk = d / ".locked"
            try:
                mt = lk.stat().st_mtime
            except Exception:
                continue
            if mt < since:
                continue
            if best is None or mt > best:
                best, name = mt, d.name
    except Exception:
        pass
    return name


def desktop_dir():
    """桌面真实路径（被重定向到 OneDrive/别处也能拿到真值）。

    ★ 不能用 `Path.home()/"Desktop"` 猜：中文系统里它叫「桌面」，
      而且用户可能把桌面重定向到了 OneDrive。走 shell 自己的 API 最稳，
      失败再退回 `USERPROFILE\\Desktop`。
    """
    try:
        CSIDL_DESKTOPDIRECTORY = 0x0010
        SHGFP_TYPE_CURRENT = 0
        shell32.SHGetFolderPathW.argtypes = [wt.HWND, ctypes.c_int, wt.HANDLE,
                                             wt.DWORD, ctypes.c_wchar_p]
        shell32.SHGetFolderPathW.restype = ctypes.c_int
        buf = ctypes.create_unicode_buffer(260)
        # shell32.SHGetFolderPathW 在 Win8+ 已被 SHGetKnownFolderPath 取代，
        # 但仍可用；失败就退回环境变量拼路径。
        r = shell32.SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None,
                                     SHGFP_TYPE_CURRENT, buf)
        if r == 0 and buf.value:
            p = Path(buf.value)
            if p.is_dir():
                return p
    except Exception:
        pass
    up = os.environ.get("USERPROFILE", "")
    if up:
        for sub in ("Desktop", "OneDrive/Desktop", "OneDrive/桌面", "桌面"):
            p = Path(up) / sub
            if p.is_dir():
                return p
    return Path(os.environ.get("TEMP", "."))    # 最后兜底：绝不因找不到桌面而中断


def backup_root_dir():
    return desktop_dir() / BACKUP_ROOT_NAME / BACKUP_SUBDIR


def _backup_rel_files(src_dir):
    """列出草稿目录里**该备份的元数据文件**（只有 `.json`，且跳过 Resources 等）。

    ★ 明确排除 `Resources\\combination\\*` 下的产物媒体文件 —— 那些是剪映的
      加密缓存，既不备份也不写回，始终一个字节不动（红线）。
    """
    out = []
    try:
        for p in Path(src_dir).rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() != ".json":
                continue
            try:
                rel = p.relative_to(src_dir)
            except Exception:
                continue
            if any(part.lower() in BACKUP_SKIP_DIRS for part in rel.parts[:-1]):
                continue
            if rel.name.startswith("."):
                continue
            out.append(rel)
    except Exception:
        pass
    return sorted(out)


def backup_draft_json(cfg, draft_dir, st=None, tag="预合成前", remember=True):
    """把草稿的**元数据 json** 复制一份到桌面备份区（还原时写回用）。

    ★ 只拷 `.json`：时间线排布、素材引用、subdraft 里的预合成登记……
      还原全靠它们。草稿资源目录里的媒体（含预合成产物）**一律不拷不写**。
    ★ `remember=False` 时**不写 cfg**：还原前的那份"快照"不能顶掉正式的还原点
      （否则第二次点「还原草稿」就会把"导出后的状态"当成还原目标）。
    ★ 返回备份目录 Path；失败返回 None（调用方据此提示"这次没法自动还原"）。
    """
    draft_dir = Path(draft_dir)
    if not draft_dir.is_dir():
        _plog(f"备份：草稿目录不存在 {draft_dir}")
        return None
    rels = _backup_rel_files(draft_dir)
    if not rels:
        _plog(f"备份：{draft_dir.name} 里没找到可备份的 .json")
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{draft_dir.name}_{ts}" + (f"_{tag}" if tag else "")
    dst = backup_root_dir() / name
    n = 0
    try:
        for rel in rels:
            tgt = dst / rel
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(draft_dir / rel, tgt)      # 只读源、只写备份区
            n += 1
    except Exception as e:
        _plog(f"备份失败：{type(e).__name__}: {e}")
        return None
    if remember:
        cfg["_backup_dir"] = str(dst)
        cfg["_backup_draft_dir"] = str(draft_dir)
        cfg["_backup_draft_name"] = draft_dir.name
        cfg["_backup_time"] = ts
        # ★ 有了新备份，就把"已经还原过"的标记清掉（2026-09-18 第十批）。
        #   这个标记只在 remember=True 时动 —— `remember=False` 那份"还原前快照"
        #   不能改状态（它就是"还原动作"自己写的）。
        cfg["_backup_consumed"] = False
        try:
            save_config(cfg)
        except Exception:
            pass
    _plog(f"备份完成：{n} 个 json → {dst}")
    if st:
        st(f"已备份草稿「{draft_dir.name}」（{n} 个元数据文件）")
    return dst


def backup_info(cfg):
    """(备份目录, 原草稿目录, 备份文件数) —— 给还原和 GUI 显示用。空则 (None,None,0)。"""
    bk = cfg.get("_backup_dir") or ""
    dr = cfg.get("_backup_draft_dir") or ""
    if not bk:
        return (None, None, 0)
    p = Path(bk)
    n = len(_backup_rel_files(p)) if p.is_dir() else 0
    return (p if p.is_dir() else None, (Path(dr) if dr and Path(dr).is_dir() else None), n)


def backup_consumed(cfg):
    """这份备份**已经还原过一次**了吗？

    ★ 为什么要它（2026-09-18 第十批）：还原**不删也不清**备份指针（备份原件要留着），
      所以还原完之后 `backup_info` 依然报"有备份"。用户看不出"已经还原过了"，
      再点一次「还原草稿」就会：杀剪映 → 把同一份旧备份**再写一遍**——如果他在
      还原之后又剪了新东西，那些新改动会被**无声覆盖**（虽然写了"还原前"快照兜底，
      但那是事后补救，用户当场是懵的）。所以还原成功后打这个标记，
      界面在二次还原前会明确警告一次。
    """
    return bool(cfg.get("_backup_consumed"))


def file_fingerprint(path):
    """(大小, 修改时间, md5 前 12 位)。用来判断文件**内容**有没有变。

    ★ 分块算 md5（2026-09-18 第十批）：原来是 `Path.read_bytes()` 把整个文件读进
      内存再算。这个函数在"等产物落盘"的循环里**每秒**调一次，而预合成产物动辄
      几十 MB（实测 21.6MB / 31.7MB）；真碰上几百 MB 的大工程，既白吃内存
      （本机长期就紧张）又拖慢每一轮等待。改成 1MB 分块，内存恒定。
    """
    try:
        p = Path(path)
        stt = p.stat()
        h = hashlib.md5()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return (stt.st_size, int(stt.st_mtime), h.hexdigest()[:12])
    except Exception as e:
        return (None, None, f"err:{e}")


def wait_file_settled(path, timeout=120, interval=1.0, status_cb=None, cancel=None):
    """等文件**内容**不再变化（连续两次指纹一致才算稳）。

    用途：防**抢跑**。文件刚出现时可能还在写（大文件窗口更长），
    只看大小会以为"早就稳定了"，于是拿到半成品 → 拖进剪映报
    「导入文件损坏 / 媒体格式不支持」。所以这里比 md5，不比大小。

    ★ 大小优先短路（2026-09-18 第十批）：**大小还在变就一定没写稳**，
      这时根本不必取 md5。旧版每一轮都整读一遍文件算 md5，而预合成产物
      动辄几十 MB、大工程几百 MB，一个 20s 的等待窗口累计要读掉十几 GB
      （O(n²) 的读盘），既拖慢等待、又和剪映抢磁盘。现在"大小稳住之后"
      才比内容指纹，等待更快也更省。
      代价：文件**在我们第一次看之前就已经写完**时，会多花一轮（约 1s）
      才确认稳定 —— 相对于它防住的"半成品"事故，这点开销是划算的。
    """
    last, last_size, stable, t0 = None, None, 0, time.time()
    while time.time() - t0 < timeout:
        if _cancelled(cancel):
            return False
        try:
            size = Path(path).stat().st_size        # 只 stat，不读内容
        except Exception:
            size = None
        if size is None:              # 文件暂时不在（剪映可能先删后建）
            last, last_size, stable = None, None, 0
        elif size != last_size:
            # 大小在变（含"第一次看到"）⇒ 一定没稳，只更新大小 + 报进度，别取 md5
            last, last_size, stable = None, size, 0
            if status_cb:
                status_cb(f"等待落盘 {size/1048576:.1f}MB", "busy")
        else:
            fp = file_fingerprint(path)             # 大小稳住 → 再比内容
            if fp == last:
                stable += 1
                if stable >= 2:
                    return True
            else:
                stable, last = 0, fp
        time.sleep(interval)
    return False


# ================================================================ 预合成登记
def product_guid(path):
    """取产物文件名里那个 36 位 GUID（剪映的组合 ID）。"""
    head = Path(path).name.split("_")[0]
    return head if len(head) == 36 else ""


def registration_committed(draft_dir, guid):
    """剪映有没有把「这个 GUID = 预合成产物」登记进草稿元数据。

    ★ 为什么这是「媒体格式不支持」的判据（2026-09-18 实测定案）：

      `Resources\\combination\\<GUID>_video.mp4` 是**剪映自己的加密缓存**
      （XOR + 68 字节明文尾块），只有草稿元数据里存在**同一个 GUID** 的
      登记（`subdraft/**.json` 里的 `combination_id`），剪映才当它是
      "自己的预合成"去读；登记缺失（或登记的还是预合成时的临时 id）时，
      它就把这个文件**当普通视频**解析 → 报「媒体格式不支持」。

      而这条登记是**草稿被保存时**才写盘的。实测三个草稿：
        9月18日 (2)  产物 11:56:44  草稿最后保存 11:56:32（早 12s）→ 登记 0 处
        9月17日 (4)  产物 18:15:18  草稿最后保存 18:14:52（早 26s）→ 登记 0 处
        9月17日      产物 10:55:23  草稿最后保存 10:55:09（早 14s）→ 登记 0 处
      反之，被后续某次保存覆盖到的老产物（B7C969C8 等）登记齐全。

      注：这里只是**读**草稿的 json 元数据做状态判断，不碰产物文件一个字节
      （红线内的动作）。
    """
    if not guid:
        return False
    d = Path(draft_dir)
    if not d.is_dir():
        return False
    for p in d.rglob("*.json"):
        try:
            if guid in p.read_text("utf-8", "ignore"):
                return True
        except Exception:
            continue
    return False


def commit_draft_registration(draft_dir, guid, st=None, timeout=30, cancel=None):
    """让剪映**保存一次草稿**，把预合成登记写进元数据（纯快捷键）。

    为什么不用 Ctrl+S：剪映快捷键表里**根本没有"保存"命令**（`sequence` 里
    只有 exportVideo / importMedia / quit / returnDraftPage 之类）。
    唯一会落盘的键盘动作是：
      ① `returnDraftPage`（返回草稿页，本机 Ctrl+Alt+Q）——正常"返回"会持久化；
      ② `quit`（正常退出剪映，本机 Ctrl+Q）——退出流程一定保存。

    两个动作都从快捷键表**实时读取**，用户改绑也能跟上；每个动作后都
    **轮询验证登记真的写进去了**才返回 True，绝不自欺欺人。

    ★ 第十七批：`cancel` 一置位立刻停手。注意这里**不能**在"发完键、还没验证"
      的中间态被优雅打断 —— 所以检查点放在"两轮动作之间"和"等待验证的循环里"：
      已发出的键不会撤回，但不会再发第二个键，也不会再等满 timeout。
    """
    def say(t):
        _plog(f"[登记] {t}")
        if st:
            try:
                st(t, "busy")
            except Exception:
                pass

    if registration_committed(draft_dir, guid):
        say("草稿元数据里已有该产物的预合成登记")
        return True

    for cmd, key_default, label in (("returnDraftPage", "Ctrl+Alt+Q", "返回草稿页"),
                                    ("quit", "Ctrl+Q", "正常退出剪映")):
        if _cancelled(cancel):
            say("已被中止，不再尝试让剪映落盘")
            return False
        win = wait_jianying(5.0, cancel=cancel)   # 发 Ctrl+Alt+Q 之后主窗口可能正在重建
        if not win:
            _plog("[登记] 找不到剪映窗口，放弃")
            break
        if not _wait_foreground(win[0], 12, cancel=cancel):
            _plog(f"[登记] 抢不到剪映前台，跳过「{label}」")
            continue
        key = read_shortcut(cmd, key_default)
        say(f"发「{label}」({key}) 让剪映落盘…")
        send_combo(key)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _cancelled(cancel):
                say("已被中止，停止等待登记写入")
                return False
            time.sleep(1.5)
            if registration_committed(draft_dir, guid):
                say("✅ 预合成登记已写入草稿元数据")
                return True
    say("!! 没能确认预合成登记（导入可能仍报「媒体格式不支持」）")
    return False


# ================================================================ 主流程
def _wait_foreground(hwnd, timeout=25, st=None, cancel=None):
    """反复尝试把剪映带到前台，直到确认成功；绝不盲目发键。

    ★ 2026-09-17 补：抢不到前台时，多半是**广告/弹窗占着前台**（实测日志：
      广告在前台 → `activate 最终结果 => False` → `wait_foreground => False`
      → 后面"清空时间线"整个失败）。所以中途补一次"关广告"再重试。
    ★ 第十七批：`cancel` 一置位立刻放弃抢前台（返回 False）。
    """
    t0 = time.time()
    cleared = False
    while time.time() - t0 < timeout:
        if _cancelled(cancel):
            _plog("抢前台被中止")
            return False
        if user32.GetForegroundWindow() == hwnd:
            return True
        if st:
            st("等待剪映窗口就绪…")
        # ★ 2026-09-18：**每轮都先扫一遍模态框**。剪映的模态子对话框（如「链接媒体」）
        #   会把键盘焦点锁在自己身上，SetForegroundWindow / SwitchToThisWindow 全都
        #   没用（实测 activate 最终结果 => False）。它是关掉就能继续的非破坏性提示，
        #   所以这里无条件清一次——这是"发按键之前"的必经点，最该保证干净。
        try:
            dismiss_jy_modals()
        except Exception:
            pass
        activate(hwnd)
        if not cleared and time.time() - t0 > 6:
            cleared = True
            try:
                n = close_jy_popups()
                if n:
                    _plog(f"抢不到前台 → 先关掉 {n} 个占前台的弹窗，再试一次")
                    continue
            except Exception:
                pass
        time.sleep(1.0)
    ok = user32.GetForegroundWindow() == hwnd
    _plog(f"wait_foreground => {ok}")
    return ok


def _send_keys_sequence(cfg, hwnd, st):
    """确认前台后发 Ctrl+A / Alt+G / 预合成热键。"""
    if not _wait_foreground(hwnd, 15, st):
        return False
    if cfg.get("do_select_all", True):
        st("全选时间线…")
        _plog(f"发送 {cfg.get('select_all_hotkey', 'ctrl+a')}（全选）")
        send_combo(cfg.get("select_all_hotkey", "ctrl+a"))
    if cfg.get("do_combination", True):
        st("创建复合片段…")
        _plog("发送 alt+g（新建复合片段）")
        send_combo("alt+g")
        time.sleep(1.5)
    if cfg.get("do_precomp", True):
        st("触发预合成…")
        _plog(f"发送 {cfg.get('precomp_hotkey', 'alt+h')}（预合成）")
        send_combo(cfg.get("precomp_hotkey", "alt+h"))
    return True


def _restart_and_enter(cfg, st, enter=True, draft_hint="", draft_dir=None, cancel=None):
    """结束并重启剪映（教程第②③步的"大退 → 重开"）。
    enter=True 时自动回到草稿编辑页。
    draft_hint：要点名让用户打开**原本那份**草稿（登记只在它里面）。
    draft_dir：目标草稿目录。给了它就先用**封面匹配**自动打开（`open_draft_by_card`），
               认不准才退回"等用户自己点"。
    ★ 第十七批：`cancel` 一路透传给等待循环。注意**已经杀掉的剪映不会因为中止而复活** ——
      中止发生在重启途中时，剪映会停在"刚启动"的状态，还原流程会再关再开一次。"""
    st("重启剪映刷新文件…")
    set_mouse_policy(cfg)
    _plog("结束剪映进程")
    kill_jianying()
    time.sleep(3)
    _plog("重新启动剪映")
    launch_jianying()
    t0 = time.time()
    newwin = None
    while time.time() - t0 < 60:
        if _cancelled(cancel):
            _plog("重启剪映等待被中止")
            return None
        time.sleep(2)
        newwin = find_jianying()
        if newwin:
            break
    _plog(f"重启后窗口 => {newwin}")
    if not newwin:
        return None
    if not enter:
        st("剪映已重启（停在首页）")
        _wait_foreground(newwin[0], 25, st, cancel=cancel)
        return newwin[0]
    st("重新进入草稿…")
    activate(newwin[0])
    # ★ 超时给足 120s：自动打开失败时会退回"等用户自己点进去"，
    #   用户得先看到提示再去点，60s 太紧（实测 14:55 那次就是这么失败的）。
    # ★ 2026-09-18：draft_dir + auto_open_draft 打开时，先用封面匹配自己点开。
    if not ensure_edit_page(newwin[0], st, timeout=120,
                            allow_click=cfg.get("allow_mouse_fallback", False),
                            draft_hint=draft_hint,
                            draft_dir=draft_dir,
                            auto_open=bool(cfg.get("auto_open_draft", False))
                            and draft_dir is not None,
                            cancel=cancel):
        _plog("重启后等了 120s 用户仍未打开草稿 → 本次放弃自动进入")
        return None
    return newwin[0]


# ================================================================ 剪映快捷键配置
def _shortcut_dir():
    """剪映「快捷键方案」所在目录。

    主落点：`%LOCALAPPDATA%\\JianyingPro\\User Data\\Config\\Shortcut`

    ★ 2026-09-18 第九批（分发）：作者机器上主落点必定命中，但**别人的机器**
      可能因为剪映版本 / 安装方式不同而落在别处。主路径找不到时，在
      `%LOCALAPPDATA%\\JianyingPro` 和 `%APPDATA%\\JianyingPro` 下**浅找**一层
      `.../Config/Shortcut`（两条 glob，限时 3 秒）—— 绝不是全盘扫。
      找不到就老实返回 None，让调用方如实说"绑不了"（见 ensure_precomp_hotkey）。
    """
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    bases = [b for b in (local, appdata) if b]
    for base in bases:
        d = Path(base) / "JianyingPro" / "User Data" / "Config" / "Shortcut"
        if d.is_dir():
            return d
    # 兜底：浅找（限时，见 docstring）
    t0 = time.time()
    for base in bases:
        root = Path(base) / "JianyingPro"
        if not root.is_dir():
            continue
        for pat in ("*/Config/Shortcut", "*/User Data/Config/Shortcut", "*/Shortcut"):
            try:
                for p in root.glob(pat):
                    if p.is_dir() and time.time() - t0 < 3.0:
                        _plog(f"快捷键目录落在非标准位置，已兜底命中：{p}")
                        return p
            except Exception:
                continue
    return None


def _active_scheme_file():
    """当前生效的快捷键方案文件（keymapSettings 的 currentKeymapIndex）。"""
    d = _shortcut_dir()
    if not d:
        return None, None
    idx = 0
    ks = d.parent / "keymapSettings"
    try:
        for line in ks.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("currentKeymapIndex="):
                idx = int(line.split("=", 1)[1])
    except Exception:
        pass
    schemes = [p for p in sorted(d.glob("*.json")) if p.stem != "combined"]
    if not schemes:
        return None, None
    return schemes[min(idx, len(schemes) - 1)], d / "combined.json"


def read_shortcut_all(action):
    """读取某动作**绑定的全部按键**（剪映一个动作可以绑多个键，实测
    `del = ["Backspace", "Del"]`）。返回 list[str]，读不到返回 []。

    ★★ 为什么必须有这个（2026-09-18 晚事故根因）：
       `read_shortcut()` 只返回列表**第一项**，所以 `read_shortcut("del","delete")`
       拿到的其实是 **"Backspace"**，不是 Del 键 —— 伴侣一直在往时间线发 Backspace。
       Backspace 在剪映别的地方语义是"返回上一级"，焦点一旦不在时间线上就跑偏；
       清空时间线这种"真删"动作必须用语义单一的 Del。这里把整张表拿到手自己挑。
    """
    out = []
    f, _ = _active_scheme_file()
    if f:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            v = d.get("sequence", {}).get(action)
            if isinstance(v, list):
                out = [str(x) for x in v if x]
        except Exception:
            pass
    if not out:
        d2 = _shortcut_dir()
        if d2:
            c = d2 / "combined.json"
            try:
                d = json.loads(c.read_text(encoding="utf-8"))
                for scheme in d.values():
                    v = (scheme.get("sequence", {}).get(action)
                         if isinstance(scheme, dict) else None)
                    if isinstance(v, list) and v:
                        out = [str(x) for x in v if x]
                        break
            except Exception:
                pass
    return out


def read_shortcut(action, default=""):
    """读取当前方案里某动作的快捷键，如 read_shortcut('precompileCombination')。"""
    f, _ = _active_scheme_file()
    if f:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            v = d.get("sequence", {}).get(action)
            if isinstance(v, list) and v and v[0]:
                return v[0]
        except Exception:
            pass
    d2 = _shortcut_dir()
    if d2:
        c = d2 / "combined.json"
        try:
            d = json.loads(c.read_text(encoding="utf-8"))
            for scheme in d.values():
                v = scheme.get("sequence", {}).get(action) if isinstance(scheme, dict) else None
                if isinstance(v, list) and v and v[0]:
                    return v[0]
        except Exception:
            pass
    return default


def _norm_key(k):
    """把 "Alt+H" / "alt+h" / ["Alt","H"] 归一成 ('alt','h') 好比较。"""
    if isinstance(k, (list, tuple)):
        parts = [str(p) for p in k]
    else:
        parts = [p for p in str(k).replace("+", " ").split() if p]
    return tuple(p.strip().lower() for p in parts if p.strip())


def _action_using_key(scheme, key, except_action):
    """当前方案里**已经占用了 key** 的其它动作名（没有就 None）。

    ★ 为什么要有（2026-09-18 第八批 · 分发安全）：
      给别人用的时候，他剪映里 Alt+H 很可能已经绑了别的功能。
      旧代码会**直接覆盖** `precompileCombination`，于是两个动作抢同一个键，
      按下去到底触发哪个由剪映内部决定 —— 用户会看到"乱套了"。
      先查一下，撞了就换一个没人用的键。
    """
    if not isinstance(scheme, dict):
        return None
    want = _norm_key(key)
    if not want:
        return None
    for act, v in (scheme.get("sequence") or {}).items():
        if act == except_action or not isinstance(v, list):
            continue
        for kk in v:
            if _norm_key(kk) == want:
                return act
    return None


def backup_shortcut_config(cfg):
    """★ 改剪映快捷键配置**之前**先留一份原样（别人的机器上出事能一键还原）。

    备份到 `桌面\\剪映预合成导出\\_备份\\剪映快捷键_<时间戳>`，返回该目录（失败给 None）。
    只拷贝剪映 `Config\\Shortcut` 目录里的 json —— 不碰任何别的文件。
    """
    d = _shortcut_dir()
    if not d:
        return None
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = backup_root_dir() / f"剪映快捷键_{stamp}"
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in d.glob("*.json"):
            shutil.copy2(f, dst / f.name)
            n += 1
        if not n:
            return None
        cfg["_shortcut_backup_dir"] = str(dst)
        cfg["_shortcut_backup_time"] = stamp
        save_config(cfg)
        _plog(f"剪映快捷键备份：{n} 个文件 → {dst}")
        return dst
    except Exception as e:
        _plog(f"剪映快捷键备份失败：{e}")
        return None


def restore_shortcut_config(cfg):
    """把上次备份的剪映快捷键配置写回去。返回 (成功?, 说明文案)。"""
    src = cfg.get("_shortcut_backup_dir") or ""
    if not src or not Path(src).is_dir():
        return False, "还没备份过剪映快捷键设置，没有可还原的东西。"
    d = _shortcut_dir()
    if not d:
        return False, "找不到剪映的快捷键配置目录（剪映装了吗？运行过一次吗？）。"
    try:
        n = 0
        for f in Path(src).glob("*.json"):
            shutil.copy2(f, d / f.name)
            n += 1
        return (n > 0), (f"已把 {n} 个快捷键配置还原回去（备份来自 {src}）。\n"
                         "**请重启剪映**（完全退出再打开）才会生效。")
    except Exception as e:
        return False, f"还原失败：{type(e).__name__}: {e}"


def write_shortcut(action, keys, cfg=None):
    """把动作快捷键写进当前方案 + combined.json。返回是否改动。

    ★★ `cfg` 传了就**在动别人的配置之前自动备份一次**（2026-09-18 第九批）。
      旧版把备份写在这个函数的 docstring 里、代码却放在调用方 `ensure_precomp_hotkey`
      —— 文档和行为对不上，将来多一个调用方就漏备份。现在把保险放进函数本身：
      **凡是要写剪映快捷键的动作，都得先经过这里，就都跑不掉备份。**
      只备一回（已经备过就不重复覆盖，免得把"原始状态"那份备份冲掉）。
    """
    if cfg is not None and not cfg.get("_shortcut_backup_dir"):
        backup_shortcut_config(cfg)

    changed = False
    f, comb = _active_scheme_file()
    if f:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            seq = d.setdefault("sequence", {})
            if seq.get(action) != [keys]:
                seq[action] = [keys]
                f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
                changed = True
        except Exception:
            pass
    if comb and comb.exists():
        try:
            d = json.loads(comb.read_text(encoding="utf-8"))
            for scheme in d.values():
                if isinstance(scheme, dict) and "sequence" in scheme:
                    scheme["sequence"][action] = [keys]
            comb.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
    return changed


# 预合成键的候选（按顺序挑第一个**没被别人占用**的）
PRECOMP_KEY_CANDIDATES = ("Alt+H", "Alt+Shift+H", "Alt+J", "Ctrl+Alt+H", "Alt+F9")


def ensure_precomp_hotkey(cfg):
    """确保「预合成复合片段」有快捷键；没有就自动写入配置（需重启剪映生效）。

    返回 **(hotkey, 是否改了配置, 是否真的绑上了)**。

    ★★ 2026-09-18 第八批（发给别人前必须处理）：
      旧版会**无条件**把 Alt+H 写进对方剪映的配置。两个问题：
        ① 对方机器上 Alt+H 可能已经绑了别的功能 → 两个动作抢一个键，按下去乱套；
        ② **没有备份**，改了别人的设置却没法还原。
      现在：先查冲突 → 从候选里挑一个没占用的 → **写之前备份整个 Shortcut 目录**
      → 备份路径记进配置，菜单里可以一键还原。

    ★★ 第三个返回值（第九批补）：找不到剪映的快捷键方案文件时，**写不进去**。
      旧版这种情况照样返回 ("Alt+H", False)，上层以为绑好了，实际按下去毫无反应
      —— 用户只看到"点了没动静"，无从排查。现在如实报"没绑上"，收尾文案直接
      教他自己去剪映里手动绑一个。
    """
    hk = read_shortcut("precompileCombination", "")
    if hk:
        cfg["precomp_hotkey"] = hk
        return hk, False, True

    f, _ = _active_scheme_file()
    if not f:
        _plog("!! 找不到剪映快捷键方案文件，无法自动绑定预合成快捷键")
        cfg["_hotkey_autobind_failed"] = True
        return (cfg.get("precomp_hotkey") or "Alt+H"), False, False

    scheme = {}
    if f:
        try:
            scheme = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            scheme = {}

    want = cfg.get("precomp_hotkey") or "Alt+H"
    if "+" not in want:
        want = "Alt+" + want
    parts = want.split("+")
    parts = [parts[0].capitalize()] + [p.upper() if len(p) == 1 else p for p in parts[1:]]
    want = "+".join(parts)

    # ① 撞键就换一个没人用的（先试用户填的，再试候选表）
    picked = want
    if _action_using_key(scheme, want, "precompileCombination"):
        others = []
        for cand in PRECOMP_KEY_CANDIDATES:
            if not _action_using_key(scheme, cand, "precompileCombination"):
                others.append(cand)
        others = [c for c in others if c != want]
        if others:
            picked = others[0]
            _plog(f"预合成键 {want} 已被别的动作占用 → 改用 {picked}")
        else:
            _plog(f"预合成键 {want} 已被占用，候选也都有主 → 仍用 {want}（可能需要用户手动改）")

    # ② 写之前先备份（只在 write_shortcut 里做一次：见那边的注释）
    changed = write_shortcut("precompileCombination", picked, cfg)
    cfg["precomp_hotkey"] = picked
    _plog(f"快捷键缺失，已自动写入 {picked}（改动={changed}）")
    # ★ 写了但**一个字节都没落盘**（写盘函数静默失败）也不能算绑上
    return picked, changed, bool(changed)


def _click_checked(x, y, expect=None):
    """校验该点下是不是剪映的窗口，然后点击。

    校验用 `WindowFromPoint` 直接吃坐标，**不需要先把光标挪过去再读回来**
    （老写法为此白白动了一次鼠标）。注意：鼠标消息只能真点——投递 WM_ 消息
    在剪映的 CEF 界面上无效，见 `bg_click` 的注释。
    """
    got = wt.POINT(int(x), int(y))
    under = user32.WindowFromPoint(got)
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(under, ctypes.byref(pid))
    jy = {p for p, n in pid_name_map().items() if n in ("jianyingpro.exe", "capcut.exe")}
    ok = (under == expect) if expect is not None else (pid.value in jy)
    _plog(f"click({got.x},{got.y}) under={under} ok={ok}")
    if ok:
        mouse_click(got.x, got.y)
    return ok


def _page_state(hwnd):
    """返回 'edit' / 'home' / 'other' / 'none'。"""
    img = grab_window_image(hwnd)
    if img is None:
        return "none"
    if ImageStat.Stat(img.convert("L")).mean[0] < 8:
        time.sleep(1.5)
        img2 = grab_window_image(hwnd)
        if img2 is not None and ImageStat.Stat(img2.convert("L")).mean[0] < 8:
            return "edit"
    w, h = img.size
    crop = img.crop((int(0.25 * w), int(0.09 * h), int(0.85 * w), int(0.24 * h)))
    r, g, b = ImageStat.Stat(crop).mean[:3]
    return "home" if (g - r) >= 12 else "other"


def _goto_home_via_menu(hwnd, st, cfg=None):
    """编辑页 → 菜单 → 返回首页（**坐标点击**，仅在快捷键路线失败时使用）。

    ★ 这是全流程里最"脏"的一步（三次点坐标）。默认**不启用**：只有用户把
    `allow_mouse_fallback` 打开才会走到这里；否则直接返回 False，由调用方
    提示用户手动返回首页/新建草稿。"""
    if cfg is not None and not cfg.get("allow_mouse_fallback", False):
        _plog("快捷键回首页失败，且未开启鼠标兜底 → 不点菜单，返回 False")
        return False
    for attempt in range(3):
        if not _wait_foreground(hwnd, 8, st):
            continue
        r = window_rect(hwnd)
        _click_checked(r[0] + 110, r[1] + 24)   # 菜单
        time.sleep(1.0)
        popup = _find_menu_popup(hwnd)
        if not popup:
            _click_checked(r[0] + 110, r[1] + 24)   # 首击可能被激活吞掉，补一击
            time.sleep(1.0)
            popup = _find_menu_popup(hwnd)
        if not popup:
            _plog("菜单弹窗未出现")
            continue
        h, rr = popup
        # 「返回首页」在下拉第 8 项，约 73% 高度处
        if not _click_checked(rr[0] + (rr[2] - rr[0]) * 0.55,
                              rr[1] + (rr[3] - rr[1]) * 0.73, expect=h):
            continue
        time.sleep(3)
        if _page_state(hwnd) == "home":
            _plog("已返回首页")
            return True
    return False


def _find_menu_popup(hwnd):
    """找剪映菜单下拉弹窗（剪映 pid 的小号可见顶层窗口）。"""
    pid = wt.DWORD()
    for h, t, cls, owner, rr in jianying_toplevels():
        if h == hwnd or not user32.IsWindowVisible(h):
            continue
        w, hh = rr[2] - rr[0], rr[3] - rr[1]
        if 60 < w < 420 and 120 < hh < 620:
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value in {p for p, n in pid_name_map().items()
                             if n in ("jianyingpro.exe", "capcut.exe")}:
                return (h, rr)
    return None


# 清空时间线自检用的比对带。★ 比拖拽那条(DRAG_CHECK_Y=0.660~0.970)更宽：
#   拖拽自检是给"**新建**轨道"用的（新轨道一定出现在下方），而清空要删的是
#   **原来的片段**——它们可能就在时间线靠上的几行里，落不进那条窄带
#   → 会出现"真清掉了却判成没清"的假失败。
CLEAR_CHECK_X = (0.02, 0.98)
CLEAR_CHECK_Y = (0.55, 0.985)


def _clear_strip(hwnd, rect):
    """抓"清空自检"要比的那条带（比拖拽自检更宽，见 CLEAR_CHECK_* 注释）。"""
    img = _window_image(hwnd, rect)
    if img is None:
        return None
    w, h = img.size
    return img.crop((int(w * CLEAR_CHECK_X[0]), int(h * CLEAR_CHECK_Y[0]),
                     int(w * CLEAR_CHECK_X[1]), int(h * CLEAR_CHECK_Y[1]))).convert("L")


# 清空前"点一下时间线取键盘焦点"的落点。
CLEAR_FOCUS_X_FRAC = 0.50   # 轨道区中点（左侧 x0.02~0.16 是**轨道头图标列**，点它会整轨选中，必须避开）
CLEAR_FOCUS_Y_FRAC = 0.75   # 时间线面板内部（面板大约在窗口 0.60~0.98 那段）


def _focus_timeline_by_click(hwnd):
    """在时间线**轨道区单击一下**，把键盘焦点交给时间线。返回是否点了。

    ★★ 为什么必须有（2026-09-18 晚事故）：剪映重开草稿后，键盘焦点通常停在
      **素材库 / 预览区**，不在时间线上。这时 `Ctrl+A` 全选的是别的东西、`Del`
      也就删不到片段 —— 用户看到的就是「清空没成功」。
      同一条还能解释「这次的产物比上一份小了 70 倍（21.6MB → 0.3MB）」：
      20:33 那次 Ctrl+A 大概也没选中整条时间线。
    ★ 落点为什么是 x0.50 / y0.75：避开左侧轨道头图标列（点那儿会**整轨选中**），
      又落在时间线面板内部。就算点中某个片段，最坏也只是"选中那个片段"——
      紧随其后的 `Ctrl+A` 本来就要全选，没有任何副作用。
    ★ 只**单击**一下（不拖、不双击）；点完把鼠标放回原处，尽量不在用户屏幕上留痕。
    """
    jr = window_rect(hwnd)
    if not jr:
        return False
    jl, jt, jr2, jb = jr
    x = jl + int((jr2 - jl) * CLEAR_FOCUS_X_FRAC)
    y = jt + int((jb - jt) * CLEAR_FOCUS_Y_FRAC)
    ctx = _dpi_aware()
    old = wt.POINT()
    try:
        user32.GetCursorPos(ctypes.byref(old))
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.25)
        user32.mouse_event(0x0002, 0, 0, 0, 0)   # 左键按下
        time.sleep(0.06)
        user32.mouse_event(0x0004, 0, 0, 0, 0)   # 左键松开
        time.sleep(0.5)
        user32.SetCursorPos(old.x, old.y)
    except Exception as e:
        _plog(f"清空：点时间线取焦点失败（{e}）")
        return False
    finally:
        _dpi_restore(ctx)
    _plog(f"清空：已单击时间线轨道区取焦点 ({x},{y})")
    return True


# 清空时间线时**优先**用哪个删除键。★ 不用 Backspace：剪映快捷键表里
# `del = ["Backspace","Del"]`，Backspace 在别的上下文里是"返回上一级"，语义不唯一；
# 清空是"真删"，必须用语义单一的 Del（2026-09-18 晚事故根因之一）。
CLEAR_DEL_PREFER = ("del", "delete")


def _pick_clear_delete_key():
    """挑清空要发的那个"删除键"：快捷键表里显式优先 Del/Delete。

    读不到表就退回 "Del"（而不是 read_shortcut 的默认值 —— 那个会拿到 Backspace）。
    """
    keys = read_shortcut_all("del")
    for k in keys:
        if k.strip().lower() in CLEAR_DEL_PREFER:
            return k
    if keys:
        return keys[0]
    return "Del"


def _jy_is_responding(hwnd, timeout_ms=1200):
    """主窗口是否"活着"：SendMessageTimeout(WM_NULL) 有回话才算。0/超时 = 卡住。"""
    SMTO_ABORTIFHUNG = 0x0002
    r = ctypes.c_ulong()
    try:
        ok = user32.SendMessageTimeoutW(wt.HWND(hwnd), 0, 0, 0,
                                        SMTO_ABORTIFHUNG, int(timeout_ms),
                                        ctypes.byref(r))
        return bool(ok)
    except Exception:
        return False


def _wait_jy_ready_for_keys(hwnd, timeout=15.0, st=None, cancel=None):
    """等剪映进入"可以安全收按键"的状态：**它在前台** + **主窗口有响应**。

    ★★ 为什么必须等（2026-09-18 晚事故）：重开草稿后剪映要先**加载时间线**，
      期间会挂一个 `Qt622QWindowToolSaveBits` 进度小窗。旧版从"已在编辑页"
      到劈头发 Ctrl+A / Del 只隔了 5 秒 —— 键全打在那个小窗/半加载的界面上，
      时间线纹丝没动，代码却照样 return True 往下走。
    ★ 这里只做**粗筛**（前台 + 响应），真正的判据是发键后的像素自检（见下）。
    ★ 第十七批：`cancel` 一置位立刻返回 False（还没发任何键，停下最干净）。
    """
    t0 = time.time()
    last = -1
    while time.time() - t0 < timeout:
        if _cancelled(cancel):
            return False
        left = int(timeout - (time.time() - t0))
        if st and left != last and left % 3 == 0:
            st(f"等剪映空闲… {left}s")
            last = left
        if user32.GetForegroundWindow() == hwnd and _jy_is_responding(hwnd):
            time.sleep(0.9)        # 让"刚响应完"那一帧画完，基线才干净
            return True
        time.sleep(0.4)
    return False


def _clear_timeline(cfg, hwnd, st, cancel=None):
    """把时间线上**原来的内容整条清空**（教程第⑤步 · 2026-09-18 用户改定）。

    ★ 用户原话："打开原草稿后，先清空里面的内容，然后将新东西丢进去"。
      ⇒ 从旧的「停用」（`toggleSegmentVisibe`）改成**真实删除**：
        Ctrl+A 全选 → Del（删除键，从剪映快捷键表实时读）。
    ★★ 2026-09-18 晚事故后的三条硬规矩（旧版全踩了）：
      ① **按键要用 Del，不能用 Backspace**：`read_shortcut("del")` 只取列表第一项，
         实际拿到 "Backspace"。Backspace 在剪映别处是"返回上一级"，焦点跑偏会误伤。
         现在走 `_pick_clear_delete_key()` 显式挑 Del。
      ② **发键前先等剪映空闲**（前台 + 主窗口响应），别在它加载时间线/挂进度小窗时
         劈头发键 —— 键会打到小窗上，白按。
      ②.5 ★ **发键前先把键盘焦点点进时间线**（`_focus_timeline_by_click`）：
         重开草稿后焦点通常在素材库/预览区，那样 `Ctrl+A` 选中的不是时间线片段，
         `Del` 自然删不到 —— 这正是"清空没成功"最可能的样子。
      ③ ★★★ **必须自检，绝不能无条件 return True**：旧版发完键就 `return True`，
         于是"根本没清掉"也被当成成功，还照着念"已把原来的内容清空"——**对用户说谎**。
         现在用 `PrintWindow`/屏幕抓取比时间线那一条带的像素（和自动拖入同一套判据）：
         没变化 = 没清掉 → 最多再试一次 → 还是没变化就 return False，**不往下走**。
    ★ 删两次 Del 的"防吞"写法已去掉：与其往未知状态连发按键，不如"发一次 → 看结果
      → 没变再干净地重来一次"。往状态不明的时间线连发删除键，正是这次卡死的风险源。
    ★ 这是**剪映自己的删除**（等价用户手动选中按 Del），伴侣只是替他按键：
      删的是**当前编辑中的时间线**，不改写任何文件——草稿落盘由剪映自己完成。
    ★ 可逆性：真删了原内容，所以"还原"不再是把开关拨回来，而是**把预合成前
      备份的草稿 json 写回**（见 `restore_draft`），由备份兜底。
    """
    if _cancelled(cancel):
        # ★ 第十七批：**发键之前**是最后的机会。真删一旦发出就无法撤回，
        #   所以中止检查只放在这里和"第一次失败之后的第二次尝试之前"。
        _plog("清空：已被中止，未发出任何按键")
        return False
    if not _wait_foreground(hwnd, 15, st, cancel=cancel):
        return False
    if not _wait_jy_ready_for_keys(hwnd, 15.0, st, cancel=cancel):
        _plog("清空：剪映没能在 15s 内进入可发键状态（前台+响应），放弃，不发任何键")
        return False
    # ★ 取基线前把剪映抬到**最上层**：编辑页是 GPU 渲染，`PrintWindow` 会得到全黑图，
    #   实际靠"屏幕截取"兜底 —— 被别的窗口压住时基线里会混进那个窗口，之后它没变化
    #   → 会把"真清掉了"误判成"没清"。抬窗**不动鼠标**（见 `_raise_for_drag`）。
    _raise_for_drag(hwnd)
    time.sleep(0.4)
    if user32.GetForegroundWindow() != hwnd:
        _plog("清空：抬窗后剪映仍不在前台 → 不敢发键（键会打到别的窗口）")
        return False
    # ★ 先把键盘焦点**点进时间线**再发键：重开草稿后焦点多半在素材库/预览区，
    #   那样 Ctrl+A 选的是别的东西、Del 也删不到片段（就是"清空没成功"的样子）。
    _focus_timeline_by_click(hwnd)
    time.sleep(0.4)
    del_key = _pick_clear_delete_key()
    _plog(f"清空：删除键={del_key!r}（快捷键表 del={read_shortcut_all('del')}）")
    st("清空原时间线内容…")

    jr = window_rect(hwnd)
    base = _clear_strip(hwnd, jr) if jr else None
    if base is None:
        _plog("清空：抓不到时间线基线，没法自检 → 不敢发键")
        st("抓不到时间线画面，无法确认清空", "err")
        return False

    for attempt in range(2):
        send_combo("ctrl+a")
        time.sleep(0.9)
        send_combo(del_key)
        time.sleep(1.4)
        now = _clear_strip(hwnd, jr)
        d = _strip_diff(base, now)
        _plog(f"清空：第 {attempt + 1} 次自检像素差={d:.2f}（阈值 {DRAG_DIFF_MIN}）")
        if d < 0:
            _plog("清空：自检图拿不到（d<0）→ 按没清掉处理")
            return False
        if d >= DRAG_DIFF_MIN:
            _plog(f"清空：✅ 确认时间线真的变了（像素差 {d:.2f}）")
            return True
        if attempt == 0:
            if _cancelled(cancel):
                _plog("清空：第一次没清掉，且已被中止 → 不再重试")
                return False
            st("第一次没清掉，再试一次…")
            _plog("清空：第一次没变化，重试一次")
    _plog("清空：两次自检都没有像素变化 → 判定失败，不往下走")
    return False


def _disable_original_contents(cfg, hwnd, st):
    """【已弃用 · 保留仅为可读性对照】旧的「停用」实现。

    2026-09-18 用户改流程为「清空」后，全流程改用 `_clear_timeline`
    （Ctrl+A → Del 真删）。这里不再被任何代码调用，**别重新接回流水线**：
    停用和清空是互斥的两套做法，混用会让"还原"对不上账。
    """
    raise RuntimeError("已弃用：请用 _clear_timeline（清空）")


def _jianying_running():
    """剪映进程还在不在（不看窗口，只看进程——隐藏/最小化也算在跑）。"""
    try:
        return any(n in ("jianyingpro.exe", "capcut.exe")
                   for n in pid_name_map().values())
    except Exception:
        return False


def _orphan_subdrafts(backup_dir, draft_dir):
    """还原后会"多出来"的 subdraft 目录（备份里没有它们）。

    预合成时会新建 `subdraft\\<GUID>\\`（复合片段/预合成的嵌套时间线）。
    写回是按"备份有什么就覆盖什么"做的，**不会删文件**，所以这些新目录会留在
    草稿里变成孤儿。它们不在写回范围内（不删、不动），但**要如实告诉用户**，
    免得他打开草稿看到多余的目录一头雾水。
    返回目录名列表（只读扫描）。
    """
    out = []
    try:
        bd = Path(backup_dir) / "subdraft"
        have = {p.name for p in bd.iterdir() if p.is_dir()} if bd.is_dir() else set()
        sd = Path(draft_dir) / "subdraft"
        if sd.is_dir():
            out = sorted(p.name for p in sd.iterdir()
                         if p.is_dir() and p.name not in have)
    except Exception:
        pass
    return out


def restore_draft(cfg, status_cb=None, finished_cb=None):
    """导出完成后，把草稿**还原回"还没预合成"的那一版**（写回备份的草稿元数据）。

    ★ 为什么现在要动文件（2026-09-18 用户批准的**解禁**）：
      流程改成"先清空原时间线内容 → 把预合成产物拖进去导出"之后，原内容是
      **真删**过的，没有"开关"可以拨回来。但伴侣在预合成**之前**已经把草稿的
      元数据 json 备份到了桌面（`backup_draft_json`），所以
      **还原 = 把那份备份写回草稿目录** → 剪映重开后就是预合成之前的样子
      （原时间线还在，预合成片段不存在）。
    ★ 解禁的**边界**（红线没变）：只写**草稿元数据 .json**
      （draft_content.json / draft_meta_info.json / subdraft 下的 json …）。
      剪映资源目录里的**预合成产物媒体**（形如 `<GUID>_video` 的缓存）
      **一个字节都不碰**——不解码、不转码、不解密、不改写、不删除。
    ★ 顺序：**必须先杀剪映**。剪映开着草稿时手里握着内存版本，退出时会**覆盖写回**，
      我们先写就白写。所以 杀进程 → 快照当前 → 写回备份 → 重启 → 回编辑页。
    ★ 写回前把**当前状态再备份一份**（后缀 `_还原前`）：万一用户后悔，还能捞回来。
    """
    def st(t, k="busy"):
        _plog(f"status[{k}] {t}")
        if status_cb:
            try:
                status_cb(t, k)
            except Exception:
                pass

    finish = finished_cb or (lambda ok, msg: None)
    _plog("还原草稿：开始")

    bk, dr, n = backup_info(cfg)
    if not bk or not n:
        _plog(f"还原草稿：没有可用备份（_backup_dir={cfg.get('_backup_dir')!r}）")
        st("没有可用备份", "err")
        finish(False,
               "没找到可还原的备份。\n\n"
               "「还原草稿」靠的是跑「一键导出」时**自动做的那份草稿备份**"
               "（存在桌面「剪映预合成导出\\_备份」里）。\n"
               "如果这次还没跑过一键导出（或换过电脑、清过桌面），就没有这份备份——\n"
               "伴侣不会凭空去改你的草稿。\n\n"
               "手动恢复也行：在剪映里连按几次 **Ctrl+Z** 撤销，\n"
               "或从剪映自己的草稿备份里恢复。\n\n"
               "★ 想看备份到底在哪：右键本按钮 →「打开草稿备份文件夹」。")
        return
    if not dr:
        _plog(f"还原草稿：有备份 {bk}，但原草稿目录不见了"
              f"（_backup_draft_dir={cfg.get('_backup_draft_dir')!r}）")
        st("原草稿目录不见了", "err")
        finish(False,
               f"备份还在：\n   {bk}\n\n"
               "但**原草稿目录**已经找不到了（被改名/移动/删除了？）。\n"
               "伴侣不会往别处乱写。你可以手动把备份里的 .json 拷回草稿目录，\n"
               "或把这整个备份文件夹发给开发者。\n"
               "★ 右键本按钮 →「打开草稿备份文件夹」就能看到它。")
        return

    name = cfg.get("_backup_draft_name") or dr.name
    _plog(f"还原草稿：备份 {bk}（{n} 个 json）→ 草稿目录 {dr}")

    # ① 先关掉剪映：它开着草稿时退出会覆盖写回，我们先写就白写
    if _jianying_running():
        st("先关闭剪映（免得它把旧内容覆盖回来）…")
        kill_jianying()
        time.sleep(3)

    # ② 写回前把"当前状态"再存一份（万一后悔还能捞）
    st("备份当前状态（以防万一）…")
    safety = backup_draft_json(cfg, dr, st=None, tag="还原前", remember=False)
    _plog(f"还原前快照：{safety}")

    # ③ 写回备份的草稿元数据（**只写 .json**，产物 mp4 一律不碰）
    st("写回预合成前的草稿…")
    wrote = 0
    try:
        for rel in _backup_rel_files(bk):
            srcf = bk / rel
            if not srcf.is_file():
                continue
            if srcf.suffix.lower() != ".json":      # 双保险：非 json 一律跳过
                continue
            tgt = dr / rel
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(srcf, tgt)
            wrote += 1
    except Exception as e:
        _plog(f"还原写回失败：{type(e).__name__}: {e}")
    _plog(f"还原写回 {wrote} 个 json → {dr}")
    if wrote <= 0:
        st("还原失败", "err")
        finish(False,
               f"写回草稿失败（备份在 {bk}）。\n"
               "你的草稿**没有被改动**。可以手动把备份里的 .json 拷回草稿目录。\n"
               "★ 右键本按钮 →「打开草稿备份文件夹」就能看到它。")
        return

    # ★ 写回成功 → 打上"这份备份已经用过了"的标记（2026-09-18 第十批）。
    #   备份原件**不删**（红线：伴侣不删文件），只改配置里的状态位；
    #   以后再点「还原草稿」界面会先警告一次（见 backup_consumed 的注释）。
    try:
        cfg["_backup_consumed"] = True
        save_config(cfg)
    except Exception:
        pass

    # ④ 重开剪映 + 回到这份草稿的编辑页（点名草稿，用户自己双击）
    st("重开剪映并回到草稿…")
    hwnd = _restart_and_enter(cfg, st, enter=True, draft_hint=name, draft_dir=dr)
    reenter_note = "" if hwnd else (
        f"\n\n★ 剪映已经重开，但**没能自动回到草稿编辑页**（这一步伴侣不会替你点草稿卡片，\n"
        f"   避免点错草稿）。请自己双击打开草稿「{name}」看一眼。\n")

    # ★ 如实报告"多出来的东西"：预合成时新建的 subdraft 目录会留在草稿里。
    #   写回只覆盖、不删除（红线：伴侣不删文件），所以这里只报告、把决定权交回用户。
    orphans = _orphan_subdrafts(bk, dr)
    orphan_note = ""
    if orphans:
        _plog(f"还原草稿：草稿里多出 {len(orphans)} 个 subdraft 目录（预合成时新建的）：{orphans}")
        shown = "、".join(orphans[:3]) + ("…" if len(orphans) > 3 else "")
        orphan_note = (f"\n\n★ 有一件事要如实告诉你：这次预合成时剪映新建过嵌套时间线目录\n"
                       f"   `subdraft\\{shown}`，它**不在**备份里，所以还原后仍留在草稿中。\n"
                       f"   伴侣**从不删你的文件**，所以没动它。正常情况下剪映会忽略它；\n"
                       f"   如果打开草稿发现异常，可以手动把这个目录删掉（可先整份拷出来留底）。\n")

    draft_txt = f"「{name}」"
    st("已还原", "ok")
    _plog(f"还原草稿：完成（写回 {wrote} 个 json，重开窗口={hwnd}）")
    finish(True,
           f"已把草稿{draft_txt}**还原到预合成之前**：\n"
           f"  写回 {wrote} 个草稿元数据文件 → {dr}\n\n"
           "现在打开它应该就是**做过预合成之前**的样子（原时间线在）。\n"
           "预合成产物仍在草稿的 Resources 里，随时能再跑一次一键导出。\n\n"
           f"★ 这次只动了草稿的**元数据 .json**。\n"
           f"  预合成产物（资源目录里的视频缓存）**一个字节没碰**——\n"
           f"  不解码、不转码、不解密、不改写、不删除。\n"
           f"★ 写回前的旧状态也留了一份快照：\n   {safety}\n"
           f"  （后悔了可以把里面的 .json 拷回草稿目录捞回来。）\n"
           f"★ 备份原件保留在：{bk}\n"
           "★ 还原点只有**预合成之前**这一个：还原之后你要是又剪了新内容，\n"
           "   请**不要再点**「还原草稿」——那会把这份旧备份再写一遍、盖掉新改动。"
           + reenter_note + orphan_note)



# ================================================================ 进度（给 GUI 画进度条）
# ★ 流水线的阶段表：(给别人看的显示名, 起始%, 结束%)。
#   为什么要有这个：伴侣对作者本人是"点一下就好"，但对**别人**是个黑盒 ——
#   中间有 20 秒到 2 分钟的等待（剪映在渲染），没有进度就分不清"在干活"还是"卡死了"。
#   ★ 顺序 / 数量必须和 `run_pipeline` 里的 `stage(N)` 一一对应（test_guard 有断言）。
PIPELINE_STAGES = (
    ("检查剪映和草稿位置",                 0,   8),
    ("备份草稿（预合成前）",               8,  16),
    ("全选 · 建复合片段 · 预合成",        16,  26),
    ("等剪映渲染出产物",                  26,  58),
    ("保存草稿 · 重启剪映 · 等你打开草稿", 58,  74),
    ("清空原时间线",                      74,  90),
    ("打开文件夹 · 交给你拖",             90, 100),
)
PIPELINE_TOTAL_STEPS = len(PIPELINE_STAGES)

# 渲染阶段的估算时长（秒），用来把"等待渲染 87s"换成进度百分比。
PIPELINE_RENDER_SECS = 120


def stage_bounds(step):
    """第 step 步（1 起）的名称与 (起始%, 结束%)。step 越界会夹到合法范围。"""
    n = max(1, min(PIPELINE_TOTAL_STEPS, int(step or 1)))
    name, lo, hi = PIPELINE_STAGES[n - 1]
    return n, name, lo, hi


def render_pct(step, text, prev=None):
    """把「等待渲染 87s」「等待落盘 21.6MB」这类消息换算成**该阶段**的百分比。

    纯函数（不碰界面、不碰全局），为的是能离线断言它的两条性质 ——
    进度条"倒着走"是用户第一眼就会注意到的问题，必须钉住：

      ① 消息里**没有秒数**时取该阶段**末端**：`等待落盘 21.6MB` 发生在
         "已经找到产物、只等写盘"之后，取阶段起点会让进度条从 58% 掉回 26%。
      ② 同一阶段内**只增不减**（`prev` = 本阶段上一次报过的值）：第二轮等待的
         timeout 是 60s 而不是 120s，同一句「等待渲染 60s」换算出来会**小于**
         上一轮结束时的百分比 —— 不夹住就会看见进度条往回缩。

    `text` 里的秒数按 `PIPELINE_RENDER_SECS`（该阶段估算总时长）线性映射到
    [lo, hi]；秒数已经超过估算总时长时夹到 hi。
    """
    _, _, lo, hi = stage_bounds(step)
    pct = hi
    m = re.search(r"(\d+)\s*s", text or "")
    if m:
        used = PIPELINE_RENDER_SECS - int(m.group(1))
        frac = max(0.0, min(1.0, used / float(PIPELINE_RENDER_SECS)))
        pct = lo + int((hi - lo) * frac)
    if prev is not None:
        pct = max(pct, prev)
    return pct


def _call_status(cb, text, kind="busy", pct=None, step=None, sub=None):
    """调用 status_cb，**兼容只收 2 个参数的老回调**（测试里的 lambda 等）。

    新回调签名：`cb(text, kind, pct=None, step=None, sub=None)`。
    先按新签名调，`TypeError` 再逐级退回老签名 —— 这样不必改遍所有调用点。

    ★ 只有"**参数个数对不上**"才回退（2026-09-18 第十批）：回调**内部**抛的
      TypeError（回调自己的 bug）也会是 TypeError，旧写法会把它误当成签名不符、
      再用 2 个参数叫一次 —— 于是这条状态被"悄悄地"投递两次/被吞掉，日志上
      看不出任何异常。现在检查错误消息里有没有 "positional argument"。
    ★ `sub`（2026-09-19 第十三批）：可选副文案，见 `run_pipeline.st`。
    """
    if cb is None:
        return
    for args in ((text, kind, pct, step, sub), (text, kind, pct, step),
                 (text, kind)):
        try:
            cb(*args)
            return
        except TypeError as e:
            if "positional argument" not in str(e):
                return                  # 回调自己的 TypeError：如实吞掉，不重投
        except Exception:
            return


def run_pipeline(cfg, status_cb=None, finished_cb=None, rescue=False, cancel=None):
    """一键跑完用户口述教程的全流程（**纯快捷键物理辅助**）：

      ⓪ ★ 备份：把**目标草稿的元数据 json** 复制到桌面「剪映预合成导出\\_备份」
        （还原要用；**只备份 .json，产物 mp4 不碰**）。必须在预合成**之前**做，
        备份才是"还没有预合成"的那一版。
      ① Ctrl+A 全选 → Alt+G 新建复合片段 → Alt+H 预合成；
      ①.5 ★ 让剪映「返回草稿页」保存一次草稿，**验证预合成登记写进元数据**。
        没有这一步，剪映重开后会把加密缓存当普通视频 → 「媒体格式不支持」。
      ② 大退剪映（结束进程）；
      ③ 重新打开剪映并回到草稿编辑页；
      ④ ★ 确定产物就绪（**先不打开文件夹**）——原位交付，文件一个字节不动；
      ⑤ ★ 把时间线上原来的内容**整条清空**（Ctrl+A → Del，真删；`_clear_timeline`）。
        ★ 顺序很关键（2026-09-18 用户实测）：**清空在前、开文件夹在后**。
          剪映这会儿还在前台，发按键最稳；资源管理器一开就抢前台，反手再抢回来
          实测要卡 5 秒。而且用户看到的顺序应该 = 实际发生的顺序。
      ⑥ ★ **清空成功之后**才打开 `Resources\\combination` 并选中产物，把预合成文件
        **交给用户手动拖进时间线**（默认**不自动拖**，2026-09-18 用户定案：
        勾菜单「自动拖进时间线」才走 `drag_file_into_jianying` 的真鼠标拖拽 + 自检；
        注意 **Ctrl+V 剪映不认**，只能拖）。伴侣在这里**停下**，之后什么都不做。
      ⑦ 等用户导出完、**自己点按钮（左键，带确认）/ 右键「还原草稿」**才动作：
        把 ⓪ 备份的草稿 json **写回**草稿目录，剪映重开后就是**预合成之前**的样子
        （见 `restore_draft`）。

    ★ 红线（2026-09-18 用户明确要求，同日按批准**部分解禁**）：
      - **仍然**：禁止任何解码 / 转码 / 解密 / 改写**产物文件**的行为——
        剪映资源目录里的预合成媒体缓存全程一个字节都不动；
      - 只有**草稿元数据 .json** 的备份与写回被用户明确批准（还原功能需要），
        写入范围严格限定在草稿目录内的 `.json`，绝不下探资源目录。
    rescue 参数仅为调用方兼容保留，行为与普通模式一致。

    ★★ 第十七批（用户："用户可以暂终止，终止时自动还原"）—— `cancel` 参数：
      `cancel` 可以是 `threading.Event`（主用）或任意无参可调用对象（测试用），
      置位 = 用户按了「暂终止」。效果：

        · 漫长的等待（等渲染 / 等落盘 / 等编辑页 / 重启剪映 / 让剪映落盘…）
          全部**立刻**收手，不再傻等满 timeout（最长的那个是 120s）；
        · 流程停止，并且**自动走一遍 `restore_draft`**，把草稿还原回
          「预合成之前」—— 这样用户中途喊停不会留下一个半残的草稿。
        · `cancel=None`（默认）= 老行为，完全不变（老调用点/老测试不受影响）。

      ★ 自动还原只动用**本轮自己**做的备份：`_backup_dir` 这类指针是跨轮留存的，
        中止在"还没备份"的早期阶段时，若误用上一轮的旧备份写回，
        反而会拿旧状态盖掉用户现在的工程。所以这里用本轮自己的 `_run["bkp_ok"]`
        判断；没备份就只停下、如实说明，绝不猜着写。
    """

    _stage_no = [1]
    # ★ 本阶段"已经报过的最大百分比"（2026-09-18 第十批 · 进度条防倒带）
    _last_pct = [None]
    # ★ 第十七批：本轮的"动过没有 / 备份成没成"——中止时靠它决定要不要还原。
    #   `touched` 在**即将给剪映发预合成按键**那一刻置位：那之后草稿就不再是原样了。
    _run = {"touched": False, "bkp_ok": False}

    def ck():
        """到达一个**可以安全停下**的检查点：用户中止了就抛出去。

        ★ 只在"动作之间"放检查点，绝不在一个动作的中间 —— 半途丢下会比不中止更糟
          （比如"按键已发出、还没做像素自检"时跑掉，就没人知道到底清空成功没有）。
        """
        if _cancelled(cancel):
            raise PipelineCancelled()

    def st(t, k="busy", pct=None, sub=None):
        # ★ 不传 pct 时**沿用本阶段上一次的值**，而不是让上层退回阶段起点：
        #   阶段 4（等渲染）里会穿插若干普通提示（重启剪映、等落盘…），
        #   要是它们一律按阶段起点算，进度条就会从 58% 掉回 26%，像倒带。
        # ★ `sub`（2026-09-19 第十三批）：**多给一行字**的通道。用户反馈"提示不够
        #   （比如让用户打开草稿等）"—— 横条只有一行主文案，把"怎么做"和"为什么"
        #   挤在一起必然被裁。有了副文案就能分两层：
        #     主文案 = 你现在要做什么（长不了，必须短）
        #     副文案 = 为什么 / 注意什么（换行之后才放得下）
        #   ★ 传了 sub 就**盖掉**默认的"第 N/步 · %"（进度条上仍然看得到百分比）。
        if pct is None:
            pct = _last_pct[0]
        else:
            _last_pct[0] = pct
        _plog(f"status[{k}] {t}" + (f" [step={_stage_no[0]} pct={pct}]" if pct is not None else ""))
        _call_status(status_cb, t, k, pct, _stage_no[0], sub)

    def stage(n, t=None, k="busy"):
        """切到第 n 个阶段（进度条随之跳到该阶段起点）。"""
        _stage_no[0] = max(1, min(PIPELINE_TOTAL_STEPS, int(n)))
        _last_pct[0] = None          # 换阶段 → 允许从该阶段自己的起点重新算
        if t:
            st(t, k)

    def st_render(t, k="busy"):
        """等待类消息 → 进度百分比（换算规则见 `render_pct`，那里有可测的断言）。"""
        pct = render_pct(_stage_no[0], t, _last_pct[0])
        _last_pct[0] = pct
        st(t, k, pct)

    _plog(f"流水线启动 rescue={rescue}")

    try:
        ck()
        win = wait_jianying(8.0, cancel=cancel)   # 剪映切页时会短暂重建主窗口，别单发一次就下结论
        if not win:
            st("未检测到剪映", "err", sub="先打开剪映专业版，再点我")
            _plog("失败：未检测到剪映窗口")
            if finished_cb:
                finished_cb(False, "没找到剪映窗口，请先打开剪映。")
            return
        hwnd, title = win
        st("连接剪映…")
        ok_act = _wait_foreground(hwnd, 25, st, cancel=cancel)
        _plog(f"activate({hwnd},{title!r}) => {ok_act}")
        if not ok_act:
            if finished_cb:
                finished_cb(False, "无法把剪映带到前台。\n请确保剪映没有最小化，然后重试。")
            return

        # 关键前置：确保停在草稿编辑页（首页发按键等于空枪）
        st("确认编辑页…", sub="需要你停在草稿编辑页（在首页就双击草稿卡片进去）")
        _edit_ok = ensure_edit_page(hwnd, st,
                                    allow_click=cfg.get("allow_mouse_fallback", False),
                                    cancel=cancel)
        ck()          # ★ 被中止时走"中止收尾"，**不要**误报成"你不在编辑页"
        if not _edit_ok:
            _plog("失败：不在草稿编辑页（未开启鼠标兜底，不点草稿卡片）")
            if finished_cb:
                finished_cb(False, "你当前不在草稿编辑页。\n"
                                   "请先在剪映里打开要处理的草稿、选中片段，然后重试。\n"
                                   "（伴侣不会去点你的草稿列表，避免点错草稿。）")
            return

        # 自动绑定预合成快捷键（写入剪映配置；需重启剪映生效）
        try:
            _, hk_changed, hk_ok = ensure_precomp_hotkey(cfg)
        except Exception:
            hk_changed, hk_ok = False, False
        # ★ 没绑上快捷键就**别往下跑**（2026-09-18 第九批）：
        #   预合成那一步按下去不会生效，硬跑两分钟只会等到一句"没等到产物"，
        #   用户白等还不知道为什么。这里直接中止，并把怎么修写清楚。
        if not hk_ok:
            _plog("失败：预合成快捷键没绑上（找不到剪映快捷键配置），中止")
            if finished_cb:
                finished_cb(False,
                            "没能给剪映绑上「预合成」快捷键，流程没法继续。\n\n"
                            "原因：找不到剪映的快捷键配置文件 —— 通常意味着剪映\n"
                            "还没被打开过一次，或者你的剪映版本把配置放在了别处。\n\n"
                            "请手动设置一次就一劳永逸：\n"
                            "  剪映 →「设置 → 快捷键」→ 找到 **预合成复合片段** →\n"
                            "  绑一个键（比如 Alt+H）→ 保存 → 完全退出剪映再打开，\n"
                            "  然后回来重新点「一键导出」。\n\n"
                            "★ 如果你确认剪映里**已经**绑好了这个快捷键，那多半是\n"
                            "  剪映版本差异导致伴侣没读出来，请把这句话告诉作者。")
            return

        if hk_changed:
            _hk = cfg.get("precomp_hotkey", "")
            _bk = cfg.get("_shortcut_backup_dir") or ""
            stage(1, f"已自动绑定预合成快捷键 {_hk}…")
            _plog(f"快捷键配置已写入 {_hk}，重启剪映以加载；原配置备份={_bk}")
            kill_jianying()
            time.sleep(3)
            ck()
            launch_jianying()
            t0 = time.time()
            win2 = None
            while time.time() - t0 < 60:
                if _cancelled(cancel):
                    break
                time.sleep(2)
                win2 = find_jianying()
                if win2:
                    break
            ck()
            if not win2:
                if finished_cb:
                    finished_cb(False, "快捷键已自动配置，但剪映重启失败，请手动打开剪映后重试。")
                return
            hwnd = win2[0]
            st("等待剪映就绪…")
            time.sleep(8)
            _edit2 = ensure_edit_page(hwnd, st, timeout=60,
                                      allow_click=cfg.get("allow_mouse_fallback", False),
                                      cancel=cancel)
            ck()
            if not _edit2:
                if finished_cb:
                    finished_cb(False, "快捷键已自动配置并重启剪映，请重新点一次开始。")
                return

        root = resolve_root(cfg, st)
        _plog(f"草稿根目录 root={root}")
        ck()
        if not root:
            if finished_cb:
                # ★ 第十五批：别再让用户去手改 伴侣配置.json —— 菜单里有入口了。
                finished_cb(False,
                            "找不到剪映的草稿目录。\n\n"
                            "剪映里改过草稿位置的话（设置 → 草稿位置），\n"
                            "请右键伴侣 →「设置草稿目录…」手动指一下。")
            return

        before = {}
        for draft, combo, mt in combo_dirs(root):
            before[str(combo)] = mt
        _plog(f"before 快照 {len(before)} 个 combination")

        # —— ⓪ ★ 备份：必须在预合成**之前**做 ——
        #   理由：备份要的是"还没有预合成"的那一版（原时间线 + 没有 combination
        #   登记）。等第①步跑完再备份，草稿里已经换成复合片段了，写回去就还原成
        #   预合成之后的样子，等于没还原。所以这里先靠 `.locked` 认出**当前打开
        #   的草稿**，把它的元数据 json 拷到桌面。
        #   ★ 只拷 .json；Resources 里的产物一律不碰（红线）。
        bkp_ok = False
        cur_name = open_draft_name(root)
        draft_dir_pre = (Path(root) / cur_name) if cur_name else None
        if draft_dir_pre and draft_dir_pre.is_dir():
            stage(2, f"备份草稿「{cur_name}」…")
            bkp_ok = backup_draft_json(cfg, draft_dir_pre, st, tag="预合成前") is not None
        _run["bkp_ok"] = bkp_ok          # ★ 中止时**只认本轮这份**（见 run_pipeline 文档）
        if not bkp_ok:
            _plog("!! 预合成前备份没做成（认不出当前草稿？）——还原功能这次不可用")
            st("⚠ 草稿备份没做成，导出后无法自动还原", "err")

        # ★★ 过了这里，草稿就不再是原样了 —— 中止时"必须还原"的分界线。
        #   备份已经做完、按键还没发，是最干净的中止点：抓紧在这里也查一次。
        ck()

        # —— 教程①：Ctrl+A → Alt+G → Alt+H，触发预合成 ——
        stage(3)
        _run["touched"] = True
        if not _send_keys_sequence(cfg, hwnd, st):
            if finished_cb:
                finished_cb(False, "无法把剪映带到前台，按键没有发送。\n请确保剪映没有最小化，然后重试。")
            return
        stage(4, "等待渲染…")
        target = wait_new_combo(root, before, timeout=120, status_cb=st_render,
                                cancel=cancel)
        _plog(f"第一轮等待(120s)结果 => {target}")
        ck()

        # —— 第二轮：没出现就重启刷新 ——
        if not target:
            newhwnd = _restart_and_enter(cfg, st, cancel=cancel)
            if newhwnd:
                hwnd = newhwnd
                target = wait_new_combo(root, before, timeout=60, status_cb=st_render,
                                        cancel=cancel)
                _plog(f"重启刷新等待(60s)结果 => {target}")
                ck()

            # —— 第三轮：重启后再发一遍按键（预合成可能根本没触发） ——
            if not target and newhwnd:
                st("再次尝试预合成…")
                _send_keys_sequence(cfg, newhwnd, st)
                target = wait_new_combo(root, before, timeout=120, status_cb=st_render,
                                        cancel=cancel)
                _plog(f"第三轮等待(120s)结果 => {target}")
                ck()

        if not target:
            st("没有新产物", "err")
            if finished_cb:
                finished_cb(False, "没等到新的预合成文件。\n请确认：已在剪映快捷键里给「预合成复合片段」绑定了\n"
                                   + cfg.get("precomp_hotkey", "alt+h") + " 并保存。")
            return

        draft, combo, src = target
        _plog(f"产物：draft={draft.name} src={src.name} size={src.stat().st_size}")
        st("等待落盘…")
        wait_file_settled(src, timeout=60, status_cb=st, cancel=cancel)
        ck()

        # —— ★ 关键一步：先让剪映**保存草稿**，提交「预合成登记」 ——
        #   2026-09-18 定案：这一步漏了，「媒体格式不支持」必现。硬杀进程不会触发
        #   保存，草稿里 `combination_id` 还是预合成时的**临时 id**（与产物文件名
        #   的 GUID 不一致），重开后剪映就把加密缓存当普通视频解析。
        guid = product_guid(src)
        reg_ok = commit_draft_registration(draft, guid, st, timeout=30, cancel=cancel)
        _plog(f"预合成登记 guid={guid} => {reg_ok}")
        ck()

        # —— 教程②③：大退剪映 → 重开并回到草稿编辑页 ——
        t_kill = 0.0
        if cfg.get("prekill_restart", True):
            stage(5, "大退剪映（杀进程）…")
            t_kill = time.time()
            kill_jianying()
            time.sleep(3)
            st("重启剪映…")
            # ★ 必须回到**原本那份**草稿：预合成登记只写在它里面，
            #   打开别的草稿/新建草稿 → 导入必报「媒体格式不支持」。
            newhwnd = _restart_and_enter(cfg, st, enter=True, draft_hint=draft.name,
                                        draft_dir=draft, cancel=cancel)
            if newhwnd:
                hwnd = newhwnd
            else:
                _plog("!! 剪映重启/回草稿失败，后续自动操作可能不可用")
            wait_file_settled(src, timeout=60, status_cb=st, cancel=cancel)
            ck()

        # —— 教程④：确认产物就绪（**先不打开文件夹**，顺序见下） ——
        #   原位交付：不复制、不改名、不改写，文件本身一个字节都不动。
        if not src.is_file() or src.stat().st_size <= 1024:
            st("产物未就绪，保持原样", "err")
            if finished_cb:
                finished_cb(False, "预合成文件没拿全，原时间线未做改动。\n请重试一次。")
            return
        cfg["_final_path"] = str(src)
        copy_file_to_clipboard(src)
        mb = src.stat().st_size / 1048576

        # —— 教程⑤：把原时间线**整条清空** ——
        stage(6)
        # ★ 防呆（2026-09-18 用户实测结论）：**必须回到原本那份草稿**。
        #   预合成登记只在目标草稿里，打开别的草稿/新建草稿 → 导入必报
        #   「媒体格式不支持」。这里只读一下锁文件，认出"这一轮你打开的是哪份"。
        opened = open_draft_name(root, since=t_kill)
        if opened and opened != draft.name:
            # ★★ 这一步从"只告警、照样清空"改成**直接中止**（2026-09-18 晚）：
            #   流程从「停用」改成「真删」之后，清错草稿 = 真的删掉别人草稿的时间线，
            #   而且备份只做了目标草稿那份，**救不回来**。宁可这一步什么都不做，
            #   让用户切回正确的草稿重试 —— 反正换草稿继续下去也必报
            #   「媒体格式不支持」，本来就跑不通。
            st(f"⚠ 你打开的是「{opened}」，本流程要的是「{draft.name}」", "err",
               sub=f"请回首页双击「{draft.name}」那张卡片再点我")
            _plog(f"!! 中止：打开的草稿「{opened}」≠ 目标草稿「{draft.name}」"
                  "——为避免误清别的草稿，本次不碰任何时间线")
            if finished_cb:
                finished_cb(False,
                            f"**已经停下了，没有动你的时间线。**\n\n"
                            f"检测到你打开的是草稿「{opened}」，\n"
                            f"但这次预合成的登记写在草稿「{draft.name}」里。\n\n"
                            f"清空会**真删**内容，所以伴侣不敢在认错的草稿上动手。\n"
                            f"请先**切回草稿「{draft.name}」**，然后再点一次「一键导出」。")
            return
        reg_note = ("" if reg_ok else
                    "\n\n⚠️ 注意：这次没能确认「剪映已保存草稿 / 预合成已登记」。\n"
                    "   如果拖进去仍报「媒体格式不支持」，请在剪映里点左上角**返回草稿页**\n"
                    "   （让它保存一次），再回到这个草稿重新拖入即可。\n")
        # ★ 备份必须落在**同一份**草稿上，否则还原会写错草稿（宁可只提示、不误导）
        bk_note = ""
        # ★ 首次运行会往剪映配置里写预合成快捷键（别人机器上是"改了他的设置"）——
        #   必须**如实告诉他改了什么、备份在哪、怎么还原**，别悄悄改。
        hk_note = ""
        if not hk_ok:
            # ★ 没绑上就必须说，否则用户只会看到"预合成那一步没反应"却无从下手。
            hk_note = ("\n\n⚠️ **没能自动给剪映绑上预合成快捷键**"
                       "（找不到剪映的快捷键配置文件）。\n"
                       "   这一步不解决的话，流程会在「预合成」卡住 —— 请手动去剪映的\n"
                       "   「设置 → 快捷键」里给**预合成复合片段**绑一个键（比如 Alt+H），\n"
                       "   然后重新点一次「一键导出」。\n")
        elif hk_changed:
            _hk2 = cfg.get("precomp_hotkey", "")
            _bk2 = cfg.get("_shortcut_backup_dir") or ""
            hk_note = (f"\n\nℹ️ 这是第一次用，我给你的剪映加了预合成快捷键 **{_hk2}**"
                       "（菜单「绑定说明」里有详情）。\n"
                       + (f"   你原来的快捷键设置已备份到：\n   {_bk2}\n"
                          "   想还原：右键菜单 →「还原剪映快捷键设置」。\n" if _bk2 else
                          "   ⚠️ 这次没能备份你原来的设置，如果你之前改过快捷键，\n"
                          "      建议先自己记一下原样。\n"))
        bk_name = cfg.get("_backup_draft_name") or ""
        if not bkp_ok:
            bk_note = ("\n\n⚠️ **这次没能做出草稿备份**（认不出你打开的是哪份草稿），\n"
                       "   所以**导出后无法自动还原**——还原功能依赖这份备份。\n"
                       "   原时间线这次是被真清空的，请自己留个心（或先 Ctrl+Z 撤销）。\n")
        elif bk_name and bk_name != draft.name:
            _plog(f"!! 备份的草稿「{bk_name}」≠ 产物所在草稿「{draft.name}」")
            bk_note = (f"\n\n⚠️ 备份做在了草稿「{bk_name}」上，但这次的产物属于"
                       f"「{draft.name}」。\n   自动还原可能对不上，请手动核对。\n")
        # ★ 最后一道检查点：清空是**真删**，能在这里停下来就停下来。
        #   过了这里，中止只能靠"还原"兜回来（也就是会让剪映重启一次）。
        ck()
        ok2 = _clear_timeline(cfg, hwnd, st, cancel=cancel)
        ck()      # ★ 中途被中止 → 走还原，而不是误报"没能确认清空"
        if ok2:
            # —— 教程⑥：**清空之后**才打开文件夹并选中产物 ——
            # ★★ 顺序是这次的修复重点（2026-09-18 用户实测反馈"打开文件夹之前没有
            #   清空掉草稿内容"）。原来这里先开文件夹、再清空，两个毛病：
            #     ① 资源管理器一打开就**抢走前台焦点**，紧接着要发 Ctrl+A / Del 就得
            #        先把剪映抢回来 —— 实测卡了 5 秒（19:49:53 开始抢、19:49:57 才
            #        成功，`SwitchToThisWindow` 兜底才救回）。清空这一步本来完全不
            #        需要冒这个险：剪映这会儿还在前台。
            #     ② 用户看到的顺序应该是"先清空 → 再给我文件"，而不是"文件先弹出来、
            #        背后偷偷清空"。他自己看得见的东西要和实际发生的顺序一致。
            #   ★ 现在把"抢前台"这件事挪到**已经不需要发按键之后**，顺手还让资源
            #     管理器自然落在前台 —— 用户下一步就是要从它里面拖文件。
            #   ★ near 传剪映主窗：把文件夹摆到**剪映所在那块屏**上（否则会开在主屏，
            #     和剪映一人一半屏，用户看不到、自动拖拽也变成跨屏长途）。
            stage(7, "打开 combination 文件夹…")
            folder_hwnd = open_folder_and_select(src,
                                                 tidy=cfg.get("folder_win_tidy", True),
                                                 near=hwnd)

            # —— 教程⑦：把预合成文件交给用户手动拖（默认）/ 自动拖（勾了开关） ——
            #   ★ 默认**不自动拖**（2026-09-18 用户定案："不要自动拖动了，让用户手动拖"）。
            #   ★ 勾了菜单「自动拖进时间线」才走 `drag_file_into_jianying`
            #     （真鼠标拖拽 + 像素自检）；拖不成也**如实**交回手动，不假装成功。
            #   ★ 三种收尾必须分开写，别混用一句话：
            #       A. 开关关掉（默认）→ "自动拖入是关闭的，请你手动拖"
            #       B. 开关开着、拖成功 → "已自动拖进去了"
            #       C. 开关开着、拖失败 → "自动拖入没成功，请你手动拖"
            #     A 和 C 都落在"请手动拖"，但**原因完全不同**；混着说会让用户
            #     以为是自己（或程序）出了问题，其实是主动关掉的。
            auto = bool(cfg.get("auto_drag_product", False))
            drag_ok = False
            if auto:
                st("把预合成文件拖进时间线…")
                drag_ok = drag_file_into_jianying(cfg, hwnd, src, st=st,
                                                  explorer_hwnd=folder_hwnd)
            # 「拖之前先按 Home」是实测硬知识，手动拖的人不知道就会踩
            home_hint = ("      ⚠️ 拖之前先在时间线上按一下 **Home**（把播放头打回 0）——\n"
                         "         往时间线空白处丢文件是**按播放头落位**的，不是按鼠标点；\n"
                         "         播放头停在中间的话，新片段就会塞到中间去。\n")
            if drag_ok:
                st("已拖进时间线", "ok", 100)
                head = ("★ 已把时间线上**原来的内容清空**，并把预合成文件\n"
                        "   **自动拖进时间线**了（请扫一眼时间线确认）。\n\n")
                todo = "   在剪映里正常**导出**——不会再提示会员。\n"
            elif auto:
                st("请把预合成文件拖进时间线", "ok", 100)
                head = ("★ 已把时间线上**原来的内容清空**。\n"
                        "★ 自动拖入**没成功**（不够确定，就不替你乱动了）。\n\n")
                todo = ("   ① 把上面那个文件**手动拖进时间线**。\n"
                        + home_hint
                        + "   ② 在剪映里正常**导出**——不会再提示会员。\n")
            else:
                st("请手动把预合成文件拖进时间线", "ok", 100)
                head = ("★ 已把时间线上**原来的内容清空**。\n"
                        "★ 自动拖入是**关闭**状态（你自己拖更稳）。\n\n")
                todo = ("   ① 把上面那个文件**手动拖进时间线**。\n"
                        + home_hint
                        + "   ② 在剪映里正常**导出**——不会再提示会员。\n")
            if finished_cb:
                finished_cb(True,
                            f"已打开并选中草稿里的预合成文件（{mb:.0f} MB）：\n"
                            f"   {src}\n\n"
                            + head
                            + "★ **接下来全是你的事 —— 伴侣什么都不做，会一直等你**：\n"
                            + todo
                            + "\n★ **等你导出完成**，回来「还原草稿」把项目还原回去：\n"
                            "   **左键点本按钮** → 它会问你一句「导出完成了吗」\n"
                            "       （还在导出就点「否」，我等你；不会打断你的导出）\n"
                            "   确认后伴侣会：关掉剪映 → 把预合成**之前**备份的草稿写回去\n"
                            "       → 重开剪映回到这份草稿。\n"
                            "   （右键菜单里也有「还原草稿（回到预合成前）」，\n"
                            "     还能看到备份的是哪份草稿、有没有备份）\n"
                            "   ★ 只写草稿元数据 .json —— 预合成产物一个字节不碰。"
                            + reg_note + bk_note + hk_note)
        else:
            if finished_cb:
                finished_cb(False,
                            "**没能确认原时间线被清空，所以我停在这里了。**\n\n"
                            "★ 你的时间线**没有被伴侣改动**"
                            "（清空这一步没通过自检，就没往下走、也没打开文件夹）。\n\n"
                            "可能的原因：剪映还在加载/忙碌、有弹窗挡着键盘，\n"
                            "或者焦点不在时间线上，按键没被时间线吃到。\n\n"
                            "请自己看一眼时间线再决定：\n"
                            "   · 如果**还有内容** → 在时间线上点一下，"
                            "按 **Ctrl+A 全选 → Del 删除**\n"
                            "   · 如果**本来就是空的** → 不用清，直接下一步\n"
                            "然后把预合成文件**手动拖进时间线**（拖前先按一下 **Home**）：\n"
                            f"      {src}\n\n"
                            "★ 导出完成后，**右键本按钮 → 「还原草稿（回到预合成前）」**\n"
                            "   就能把草稿还原回去（只写元数据 .json，产物一个字节不碰）。"
                            + bk_note)


    except PipelineCancelled:
        # ★★ 第十七批：用户按了「暂终止」。
        #   收尾规矩和"失败"**完全不同** —— 失败是"没做成"，中止是"我不干了，
        #   但请你把现场收拾干净"。所以这条分支只做一件事：**尽量把草稿还原回去**。
        #   ★ 传 `rescue="abort"`：GUI 靠它区分"中止"和"跑完了，等你导出"——
        #     两者都是 ok=True，不区分的话中止会被界面当成"已就位，等你导出"，
        #     那是最糟的误报（用户明明喊了停，界面却说"接下来交给你拖"）。
        import traceback
        _plog("流程被用户中止（暂终止）")
        st("已中止", "ok")

        if not _run["touched"]:
            # 连预合成按键都还没发出去 ⇒ 草稿一个字节都没动。
            # **不许**顺手去杀剪映/写文件：没坏的东西别去修。
            _plog("中止：本轮还没动过草稿，无需还原")
            if finished_cb:
                finished_cb(True,
                            "已经停下了。\n\n"
                            "★ 这一轮**还没动过你的草稿**（连预合成都还没发出去），\n"
                            "   所以不需要还原 —— 剪映还是你原来那个样子。\n\n"
                            "想重新开始，点一下悬浮球就行。",
                            "abort")
            return

        if not _run["bkp_ok"]:
            # 动过草稿、却没有本轮备份 ⇒ 想还原也没有料。如实说，别假装。
            st("已中止（没能自动还原）", "err")
            _plog("中止：本轮没有可用备份 → 无法自动还原")
            if finished_cb:
                finished_cb(False,
                            "已经停下了，但**这次没能自动还原**。\n\n"
                            "原因：这一轮跑的时候没做出草稿备份"
                            "（多半是那会儿认不出你打开的是哪份草稿）。\n\n"
                            "★ 你的时间线可能已经被改动过（预合成 / 清空）。\n"
                            "   可以先在剪映里连按几次 **Ctrl+Z** 撤销；\n"
                            "   或右键本按钮 →「打开草稿备份文件夹」看看有没有更早的备份。",
                            "abort")
            return

        # —— 有本轮备份 ⇒ 自动还原（这就是用户要的"终止时自动还原"）——
        st("已中止 · 正在还原草稿…", "busy", sub="把草稿回到预合成之前")
        _plog("中止：开始自动还原")
        cap = {}
        try:
            restore_draft(cfg, status_cb=status_cb,
                          finished_cb=lambda ok, msg: cap.update(ok=bool(ok), msg=msg))
        except Exception:
            _plog("中止后自动还原抛异常：\n" + traceback.format_exc())
        _bk, _dr, _n = backup_info(cfg)
        _nm = cfg.get("_backup_draft_name") or ""
        _dtxt = f"「{_nm}」" if _nm else "这份草稿"
        if cap.get("ok"):
            st("已中止 · 已还原", "ok")
            _plog(f"中止：自动还原完成（备份 {_bk}）")
            if finished_cb:
                finished_cb(True,
                            f"已经中止，并且**把草稿{_dtxt}还原回预合成之前**了。\n\n"
                            "★ 中止时伴侣会自动还原，不会给你留一个半残的草稿。\n"
                            f"  写回的草稿目录：{_dr}\n"
                            "  （只动草稿元数据 .json；预合成产物一个字节没碰。）\n"
                            + (f"★ 备份原件保留在：{_bk}\n" if _bk else "")
                            + "★ 想重新跑一遍，点一下悬浮球就行。",
                            "abort")
        else:
            st("已中止（还原没做成）", "err")
            _plog("中止：自动还原未成功")
            if finished_cb:
                finished_cb(False,
                            "已经中止，但**自动还原没能完成**。\n\n"
                            + (cap.get("msg") or "（还原过程没有给出原因，详见运行日志。）")
                            + "\n\n★ 请自己看一眼草稿；不对就先在剪映里 **Ctrl+Z** 撤销，\n"
                            "   或右键本按钮 →「打开草稿备份文件夹」手动把 .json 拷回去。",
                            "abort")
        return

    except Exception as e:
        import traceback
        _plog("异常：" + traceback.format_exc())
        st("出错", "err")
        if finished_cb:
            finished_cb(False,
                        f"执行出错：{type(e).__name__}: {e}\n\n"
                        "原时间线未做改动。详细堆栈已写入 `运行日志.txt`，可直接发给开发者。\n"
                        "★ 右键本按钮 →「打开运行日志」就能打开它。")


def extract_latest(cfg, status_cb=None, finished_cb=None):
    """兜底模式：不发任何按键。用户在剪映里**手动**做完
    「复合片段 → 右键预合成」之后点这个——伴侣只做物理动作：
    找到最新的预合成文件 → 打开并选中它 + 写进剪贴板，剩下的按教程手动来。"""

    def st(t, k="busy"):
        if status_cb:
            try:
                status_cb(t, k)
            except Exception:
                pass

    try:
        root = resolve_root(cfg)
        if not root:
            if finished_cb:
                # ★ 第十五批：同上，指向菜单入口。
                finished_cb(False,
                            "找不到剪映的草稿目录。\n\n"
                            "如果剪映里改过草稿位置，请右键伴侣 →「设置草稿目录…」手动指一下。")
            return
        items = combo_dirs(root)
        if not items:
            if finished_cb:
                finished_cb(False,
                            f"草稿目录下没有任何预合成文件：\n{root}\n\n"
                            "请先在剪映里手动做一次：\n"
                            "1) 时间线 Ctrl+A 全选\n"
                            "2) Alt+G 新建复合片段\n"
                            "3) 在复合片段上右键 → 预合成复合片段\n"
                            "4) 等播放器左上角进度提示消失\n\n然后再点这里取文件。")
            return
        draft, combo, _ = items[0]
        src = pick_combo_file(combo)
        if not src:
            if finished_cb:
                finished_cb(False, "预合成目录是空的。")
            return
        st("等待落盘…")
        wait_file_settled(src, timeout=60, status_cb=st)

        copy_file_to_clipboard(src)
        _jw = find_jianying()
        open_folder_and_select(src, tidy=cfg.get("folder_win_tidy", True),
                               near=_jw[0] if _jw else None)
        mb = src.stat().st_size / 1048576
        st(f"完成 {mb:.0f}MB", "ok")
        if finished_cb:
            reg = registration_committed(draft, product_guid(src))
            finished_cb(True,
                        f"已打开并选中最新的预合成文件（{mb:.0f} MB）：\n{src}\n\n"
                        "接下来按教程手动做：\n"
                        "0) ★ 先让剪映**保存一次草稿**：点左上角**返回草稿页**，"
                        "等它保存完再回到这个草稿——\n"
                        "   不保存的话剪映没把预合成登记写进草稿，导入会报「媒体格式不支持」。\n"
                        + ("   （检查：这个产物的登记**已经在**草稿元数据里了，可直接下一步）\n"
                           if reg else
                           "   （检查：目前元数据里**还没有**这个产物的登记，务必先保存）\n")
                        + "1) 大退剪映（完全退出）再重新打开，回到这个草稿；\n"
                        "2) 时间线上 Ctrl+A 全选 → Del 把原内容清空；\n"
                        "3) 把刚选中的这个文件**拖进时间线**（★ 别用 Ctrl+V，剪映不认，实测）；\n"
                        "4) 正常导出——不会再提示会员。")
    except Exception as e:
        if finished_cb:
            finished_cb(False, f"提取失败：{e}")


def strip_md_emphasis(s):
    """去掉 `**加粗**` 的星号。

    ★ 2026-09-19 第十三批（用户："提示不够"）：收尾/失败提示最后是走 `notify_box`
      → Win32 `MessageBoxTimeoutW` 显示的，**它不认 markdown** ——
      `**已经停下了，没有动你的时间线。**` 会连着星号一起画在用户面前，
      看着就像程序出错了。在内核里统一剥掉，一处生效全部提示。
      纯函数（可离线断言）。
    """
    if not s:
        return s
    return s.replace("**", "")


def notify_box(msg, title="剪映伴侣"):
    msg = strip_md_emphasis(msg)
    try:
        mb = getattr(shell32, "MessageBoxTimeoutW", None)
        if mb:
            mb(None, msg, title, 0x40 | 0x1000 | 0x40000, 0, 3000)
            return
    except Exception:
        pass
    try:
        import winsound
        winsound.MessageBeep(0x40)
    except Exception:
        pass


# ================================================================ 环境自检（给别人用）
def env_selfcheck(cfg, quick=False):
    """给"第一次用的人"体检一次。返回 [(级别, 标题, 说明)]，级别 'ok' / 'warn' / 'bad'。

    ★ 为什么要有（2026-09-18 第八批 · 分发）：
      作者机器上剪映装了、草稿目录在配置里、快捷键也绑好了 —— 点一下就通。
      但**别人拿到的是一台陌生机器**：可能没装剪映、可能没打开过、可能草稿
      放在自定义路径、可能桌面备份目录写不进去。与其等他点了「一键导出」再看到
      一句看不懂的失败，不如开机就把话说明白。
    quick=True 跳过会扫盘的草稿目录查找（启动时用，别让开机卡住）。
    """
    out = []

    # ① 剪映本体在不在
    exe = find_jianying_exe(cfg)
    if exe:
        out.append(("ok", "剪映：已安装", str(exe)))
    else:
        out.append(("bad", "剪映：没找到",
                    "伴侣本身不含剪映，它只是替你按键。\n"
                    "请先安装「剪映专业版」，并至少打开过一次。\n"
                    "装在自定义位置的话：右键伴侣 →「设置剪映路径…」手动指一下。"))

    # ①b ★ 第十六批：「自动配置」——要让用户明确知道**他不需要做任何设置**。
    #   这一条是"安装时自动帮用户搞好"的**可见证据**：装完/首跑时就写好了，
    #   体检报告第一屏就告诉他"已经替你配好了，你不用点任何菜单"。
    _jx = str(cfg.get("jianying_exe") or "").strip()
    _dr = str(cfg.get("draft_root") or "").strip()
    if cfg.get("_autosetup_done"):
        if _jx and _dr:
            out.append(("ok", "自动配置：已经替你配好了",
                        f"剪映：{_jx}\n草稿目录：{_dr}\n\n"
                        "这两个位置是**装的时候**（或第一次启动时）伴侣自己认出来的，\n"
                        "你不需要做任何设置。\n"
                        "以后换了位置：右键 →「设置剪映路径…」/「设置草稿目录…」。"))
        else:
            out.append(("warn", "自动配置：还有没认出来的",
                        "剪映：" + (_jx or "没找到") + "\n"
                        "草稿目录：" + (_dr or "没找到") + "\n\n"
                        "按下面几行的提示处理；实在认不出来，\n"
                        "右键 →「设置剪映路径…」/「设置草稿目录…」手动指一次就好。"))
    else:
        out.append(("warn", "自动配置：还没跑过",
                    "第一次启动 / 点「一键导出」时伴侣会自动认一遍，认到就写进配置，\n"
                    "你不需要手动填任何路径。"))

    # ② 剪映这会儿在不在跑
    running = False
    try:
        running = any(n in ("jianyingpro.exe", "capcut.exe")
                      for n in pid_name_map().values())
    except Exception:
        pass
    if running:
        out.append(("ok", "剪映：正在运行", "很好。"))
    else:
        out.append(("warn", "剪映：没在运行",
                    "点「一键导出」之前，请先把剪映打开到**草稿编辑页**。"))

    # ③ 预合成快捷键（伴侣会自动绑，但要让用户知道"我会动你的设置"）
    sd = _shortcut_dir()
    hk = read_shortcut("precompileCombination", "") if sd else ""
    if hk:
        out.append(("ok", f"预合成快捷键：{hk}", "已经绑好了，不需要你手动设置。"))
    elif sd:
        out.append(("warn", "预合成快捷键：还没绑",
                    "第一次点「一键导出」时伴侣会自动绑一个（默认 Alt+H）。\n"
                    "★ 绑之前会**先备份**你原来的快捷键设置，\n"
                    "   想还原：右键菜单「还原剪映快捷键设置」。"))
    else:
        out.append(("warn", "剪映快捷键配置：还没生成",
                    "第一次打开剪映之后它才会建这份配置。\n"
                    "伴侣会在第一次点「一键导出」时自动绑好预合成快捷键。"))

    # ④ 草稿目录
    #   ★★ 2026-09-19 第十五批：这里原来是 `if cfg["draft_root"] and is_dir(): ok` ——
    #   只要**目录存在**就报 ok。可"存在"和"是对的"不是一回事：一个指错的/残留空目录
    #   照样报"草稿目录：找到"，用户拿着"体检全通过"来找我们，我们只能让他去手改 JSON。
    #   现在非 quick 时走 draft_root_status() 三态（有内容 / 目录在但没内容 / 没有）；
    #   quick 时保持"不扫盘"的约定（启动路上别为了体检多花时间）。
    if quick:
        _drq = str(cfg.get("draft_root") or "").strip()
        if _drq and Path(_drq).is_dir():
            out.append(("ok", "草稿目录：找到", _drq))
        else:
            # ★ 别在这个字面量里写单反斜杠：`\User` 里的 `\U` 会被 Python 当成
            #   `\UXXXXXXXX` 转义 → SyntaxError（2026-09-18 晚踩过）。用正斜杠展示。
            out.append(("warn", "草稿目录：等第一次跑的时候再找",
                        "默认在 %LOCALAPPDATA%/JianyingPro/User Data/Projects/com.lveditor.draft\n"
                        "（第一次点「一键导出」时伴侣会自动找一遍；\n"
                        "  也可以右键 →「设置草稿目录…」直接手动指）"))
    else:
        out.append(draft_root_status(cfg))

    # ⑤ 备份目录能不能写（写不进去就"导出后无法还原"）
    try:
        bd = backup_root_dir()
        bd.mkdir(parents=True, exist_ok=True)
        probe = bd / "_write_test.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        out.append(("ok", "备份目录：可写", str(bd)))
    except Exception as e:
        out.append(("bad", "备份目录：写不进去",
                    f"{backup_root_dir()}\n{type(e).__name__}: {e}\n"
                    "没有它，「导出后还原草稿」就用不了。"))

    # ⑥ 使用前必须知道的事（清空 = 真删）
    out.append(("warn", "用之前请先知道",
                "点「一键导出」会**杀掉并重启剪映**，并且**清空当前时间线**\n"
                "（清空前会自动备份草稿元数据，导出后可以一键还原）。\n"
                "所以请先在剪映里打开**你要处理的那份草稿**，选中要预合成的内容。"))
    return out


def format_selfcheck(rows):
    """把 env_selfcheck 的结果排版成一段人话（GUI 直接弹出来给用户看）。

    ★★ 2026-09-19 第十五批（用户"继续优化"）：**这里是体检正文的唯一渲染出口**，
      所以星号必须在这里统一剥掉 —— 报告最后走的是 tk 的 `showinfo`，
      它不认 markdown，`**杀掉并重启剪映**` 会连着星号一起画在用户面前
      （"用之前请先知道"那条一直是这个毛病，只是一直没人注意）。
      和 `notify_box` 剥星号是同一个道理：**一个出口剥一次，全部文案都干净**。
    """
    icon = {"ok": "[通过]", "warn": "[注意]", "bad": "[问题]"}
    bad = [r for r in rows if r[0] == "bad"]
    lines = []
    for lv, title, detail in rows:
        lines.append(f"{icon.get(lv, '')} {strip_md_emphasis(title)}")
        if detail:
            lines.append("      " + strip_md_emphasis(str(detail)).replace("\n", "\n      "))
        lines.append("")
    if bad:
        head = f"体检结果：有 {len(bad)} 项必须先解决，否则跑不起来。\n\n"
    else:
        head = "体检结果：可以用了。\n\n"
    return head + "\n".join(lines).rstrip()
