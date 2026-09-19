# -*- coding: utf-8 -*-
r"""回归测试：弹窗归类规则 + 纯物理辅助流程的配置红线。

覆盖：
  A. 「我没点一键导出，它自己就开始了」
     根因：旧判据 = 大窗在场 + 尺寸落在 250~760 x 150~560 就算会员弹窗。
     剪映的**广告**（标题也叫 "JianyingPro"）、以及它真正的「链接媒体」对话框
     尺寸正好落在那个区间 → 被当成会员弹窗关掉、还写进学习表 → 之后每弹一次广告
     都误触发"自动开跑"。
     现在：会员弹窗只看标题关键词；广告单独归一类（只关不跑）。
  B. 2026-09-18 用户红线：剪映伴侣 = **纯快捷键物理辅助**。
     红线分两层（同日二次定稿）：
       · 绝对禁地：**产物文件**（`Resources\combination\<GUID>_video.mp4`）——
         禁止任何解码 / 转码 / 解密 / 改写 / 删除，全程一个字节不动；
       · 用户明确批准的解禁：**草稿元数据 .json** 的备份与写回
         （「导出后还原到预合成前」功能必须靠它，只写草稿目录内的 .json）。
     凡属于"解析/还原/转码产物"的函数必须**不存在**于 jy_core。
"""
import importlib.util
import inspect as _inspect
import math
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import jy_core as core  # noqa: E402

spec = importlib.util.spec_from_file_location("companion_gui", HERE / "剪映伴侣.py")
gui = importlib.util.module_from_spec(spec)
sys.modules["companion_gui"] = gui
spec.loader.exec_module(gui)

fails = []


def check(name, got, want):
    ok = got == want
    print(("  OK   " if ok else "  FAIL ") + f"{name}  期望={want!r} 实际={got!r}")
    if not ok:
        fails.append(name)


# ---- A1. 本次 bug 的最小复现：广告弹窗被旧版本学成了"会员弹窗" ----
POLLUTED = [{"title": "JianyingPro", "w": 616, "h": 500}]

print("[1] sanitize_learned 只留带会员关键词的记录")
check("广告特征被清掉", gui.sanitize_learned(POLLUTED), [])
check("会员特征保留",
      gui.sanitize_learned([{"title": "开通会员", "w": 400, "h": 300}]),
      [{"title": "开通会员", "w": 400, "h": 300}])
check("None / 空表安全", (gui.sanitize_learned(None), gui.sanitize_learned([])), ([], []))

print("[2] 广告弹窗（标题 JianyingPro + 实测白名单尺寸）→ 'ad'，绝不判成会员弹窗")
for w, hh in ((652, 417), (616, 500), (464, 402)):
    check(f"真广告 JianyingPro {w}x{hh}", core.classify_popup("JianyingPro", w, hh), "ad")
check("容差内（+4px）仍识别", core.classify_popup("JianyingPro", 656, 421), "ad")
check("只给标题不给尺寸 → 不明 ⇒ None（宁可不关也别误判）",
      core.classify_popup("JianyingPro"), None)
check("标题带尾随空格 ≠ 精确 JianyingPro → None",
      core.classify_popup("JianyingPro  ", 652, 417), None)

print("[2b] 同名同窗口类的**正常工具窗**绝不能被当成广告关掉（日志实测尺寸）")
BENIGN = [(480, 125), (804, 420), (820, 420), (1920, 1032), (168, 150),
          (192, 210), (368, 43), (253, 34), (293, 64), (181, 19), (226, 24),
          (52, 19), (316, 43), (2000, 1200)]
for w, hh in BENIGN:
    check(f"工具窗 JianyingPro {w}x{hh} → 不动", core.classify_popup("JianyingPro", w, hh), None)

print("[3] 剪映自己的「链接媒体」对话框 —— 曾被旧判据误关，现在必须不动")
check("链接媒体 556x455", core.classify_popup("链接媒体", 556, 455), None)
check("链接媒体 无尺寸", core.classify_popup("链接媒体"), None)

print("[4] 真会员弹窗必须仍然能认出来（不依赖尺寸）")
check("标题带「会员」", core.classify_popup("开通会员即可导出高清视频"), "vip")
check("标题带 VIP", core.classify_popup("VIP 专享功能"), "vip")
check("标题带 vip（小写）", core.classify_popup("vip 功能"), "vip")
check("标题带 付费", core.classify_popup("付费才能使用"), "vip")
check("标题带 去水印", core.classify_popup("去水印需开通"), "vip")
check("标题带 权益", core.classify_popup("会员权益说明"), "vip")
check("会员关键词优先于广告规则", core.classify_popup("JianyingPro 开通会员", 616, 500), "vip")

print("[5] 无关窗口一律不动")
check("空标题", core.classify_popup(""), None)
check("None 标题", core.classify_popup(None), None)
check("普通窗口", core.classify_popup("剪映专业版"), None)

print("[6] 流程配置：2026-09-18 纯物理辅助定稿（红线：禁止解码/转码/解密）")
_orig = core.load_config()
check("默认开启「大退剪映」（教程必要一步）", _orig.get("prekill_restart"), True)
check("清空原时间线走剪映自己的「全选 + 删除」命令（Ctrl+A → Del，实时读快捷键表）",
      "ctrl+a" in [str(c).lower() for c in core._clear_timeline.__code__.co_consts]
      and "del" in [str(c).lower() for c in core._clear_timeline.__code__.co_consts]
      and "read_shortcut" in _inspect.getsource(core._clear_timeline), True)
check("清空里**没有** Ctrl+V（用户实测：剪映不认剪贴板文件落地）",
      "ctrl+v" not in [str(c).lower() for c in core._clear_timeline.__code__.co_consts],
      True)
check("旧的「停用」入口已废弃（再接线会当场抛错，不会静默用错做法）",
      "已弃用" in (core._disable_original_contents.__doc__ or "")
      and "_clear_timeline" in (core._disable_original_contents.__doc__ or ""), True)
check("旧的「清空+粘贴」实现已删除（新流程是全清 + 真鼠标拖拽）",
      not hasattr(core, "_clear_and_place"), True)
check("默认自动跳过广告", _orig.get("auto_skip_ad"), True)
check("默认不自动开跑流水线", _orig.get("guard_auto_run"), False)
check("默认不点鼠标兜底", _orig.get("allow_mouse_fallback"), False)
# ★★ 红线（2026-09-18 二次定稿）：**产物文件**是绝对禁地，**草稿元数据 json**
#   经用户明确批准解禁（「还原」功能必须靠它）。所以黑名单只留"解码/转码/解密/
#   改写产物"这一路——谁把它加回来，这一组立刻红。
for _fn in ("deobfuscate_product", "probe_artifact", "validate_mp4", "deliver_product",
            "remux", "find_ffmpeg", "detect_xor_key", "_track_sample_keys",
            "_sample_key_of", "_cut_candidates", "archive_product", "out_dir"):
    check(f"红线：core.{_fn} 不存在（禁解码/转码/解密/改写产物）",
          not hasattr(core, _fn), True)
check("红线：run_pipeline 文档自带「禁止任何解码」声明",
      "禁止任何解码" in (core.run_pipeline.__doc__ or ""), True)
# ★★ 解禁的**边界**断言（这是这次改动的核心保险）：
#   备份/还原只能碰 `.json`，产物 mp4（Resources\combination）一个字都不许写。
_bk_src = _inspect.getsource(core.backup_draft_json)
check("★ 解禁边界：备份函数只拷 .json（后缀白名单硬编码在源码里）",
      '.json' in _bk_src and "_backup_rel_files" in _bk_src, True)
check("★ 解禁边界：备份函数**不碰** Resources / combination / *_video.mp4",
      not any(t in _bk_src for t in ("combination", "_video.mp4", "MEDIA_EXT")), True)
_rel_src = _inspect.getsource(core._backup_rel_files)
check("★ 解禁边界：候选文件筛选里只留 .json，且跳过 Resources 等目录",
      '".json"' in _rel_src and "BACKUP_SKIP_DIRS" in _rel_src, True)
check("★ 解禁边界：跳过名单里含 resources（产物目录）",
      "resources" in core.BACKUP_SKIP_DIRS, True)
_rd0_src = _inspect.getsource(core.restore_draft)
check("★ 解禁边界：还原写回时**再查一次**后缀，非 .json 直接跳过",
      '".json"' in _rd0_src and "continue" in _rd0_src, True)
check("★ 解禁边界：还原写回路径基于**草稿目录 + 相对路径**，不会写到别处",
      "shutil.copy2" in _rd0_src and "dr / rel" in _rd0_src, True)
check("红线：还原里没有任何解码/转码/解密字样",
      not any(t in _rd0_src.lower() for t in ("decrypt", "deobfuscate", "remux",
                                              "transcode", "ffmpeg", "xor")), True)

print("[7] 鼠标策略分类：内容点击 vs 窗口激活点击，各自独立")
check("默认允许任务栏激活点击（不是内容操作）", core.set_mouse_policy(_orig), (True, False))
check("关掉任务栏点击后只剩纯 API",
      core.set_mouse_policy({"allow_taskbar_click": False, "allow_mouse_fallback": True}),
      (False, True))
core.set_mouse_policy(_orig)   # 复原

print("[8] ★ 大退剪映开关：默认开（教程对齐），显式关闭也如实反映（排查用）")
# 教程原文："把素材全部建成一个复合片段，然后右键预合成，**之后大退剪映**；
# 找到草稿位置…combination…就能看到预合成的文件；再打开剪映拖进去，
# **把原内容停用**，预合成文件拖进去，这时候导出就不会提示需要会员了。"
check("默认 prekill_restart=True", _orig.get("prekill_restart"), True)
check("显式关掉大退也如实反映",
      {"prekill_restart": False}.get("prekill_restart", True), False)
check("配置缺字段时按默认（大退开）", {}.get("prekill_restart", True), True)
check("旧的 prekill_steps/撤销预合成逻辑已删除",
      not hasattr(core, "prekill_steps"), True)

print("[9] ★ 「媒体格式不支持」根因回归：大退前必须先提交「预合成登记」")
# 根因（2026-09-18 实测定案）：Resources\combination\<GUID>_video.mp4 是剪映自己的
# 加密缓存。只有草稿元数据（subdraft/**.json 的 combination_id）里存在**同一个
# GUID** 的登记时，剪映才当它是"自己的预合成"去读；否则当普通视频解析 →
# 「媒体格式不支持」。而这条登记**只在草稿被保存时**才写盘。
# 旧实现直接 taskkill（不触发保存）→ 登记缺失（实测三个草稿里最新产物的 GUID
# 在元数据里出现次数都是 0）→ 用户每次都撞「媒体格式不支持」。
import tempfile as _tempfile  # noqa: E402

check("product_guid 能从产物名取出 36 位 GUID",
      core.product_guid("5BFBDA7A-8C48-43bb-890B-93FD40FC9F13_video.mp4"),
      "5BFBDA7A-8C48-43bb-890B-93FD40FC9F13")
check("product_guid 对非 GUID 名字返回空串", core.product_guid("abc_video.mp4"), "")

_G = "AAAAAAAA-1111-2222-3333-444444444444"
with _tempfile.TemporaryDirectory() as _td:
    _d = Path(_td)
    (_d / "draft_content.json").write_text('{"a":1}', encoding="utf-8")
    check("元数据里没有该 GUID → 未登记", core.registration_committed(_d, _G), False)
    (_d / "subdraft" / "S1").mkdir(parents=True)
    (_d / "subdraft" / "S1" / "draft_content.json").write_text(
        '{"drafts":[{"combination_id":"%s"}]}' % _G, encoding="utf-8")
    check("subdraft 里出现 combination_id → 已登记",
          core.registration_committed(_d, _G), True)
    check("空 GUID 一律视为未登记", core.registration_committed(_d, ""), False)

_rp_src = _inspect.getsource(core.run_pipeline)
# 注意：run_pipeline 里第一处 kill_jianying() 是"自动绑定快捷键后重启"用的，
# 这里要跟**教程第②步的「大退剪映（杀进程）」**比顺序。
check("run_pipeline 里「提交登记」排在「大退剪映」之前（顺序错了必现不支持）",
      0 <= _rp_src.find("commit_draft_registration") < _rp_src.find("大退剪映（杀进程）"),
      True)
_cc_src = _inspect.getsource(core.commit_draft_registration)
check("保存草稿走剪映自己的键盘命令（returnDraftPage / quit 或返回草稿页 / 退出剪映）",
      ("returnDraftPage" in _cc_src and "quit" in _cc_src), True)
check("登记检查是**只读**的（不写任何文件）",
      not any(t in _cc_src for t in ("write_text", '"w"', "'w'", '"wb"', "'wb'",
                                     "open(", "unlink", "rename", "remove")), True)

print("[10] ★ 草稿身份：必须回到**原本那份**草稿（用户实测：换草稿导入必报不支持）")
# 预合成登记（combination_id）只写在做预合成的那份草稿里，导入也只在那份草稿里认
# 缓存；打开别的草稿/新建草稿 → 必报「媒体格式不支持」。
# 判据：剪映打开草稿会在它目录下写 `<草稿>/.locked`，最新的那个就是当前打开的。
import os as _os  # noqa: E402

with _tempfile.TemporaryDirectory() as _td:
    _r = Path(_td)
    for _nm, _t in (("甲", 100.0), ("乙", 500.0)):
        _d = _r / _nm
        _d.mkdir()
        _lk = _d / ".locked"
        _lk.write_text("", encoding="utf-8")
        _os.utime(_lk, (_t, _t))
    check("锁最新的那个 = 当前打开的草稿", core.open_draft_name(_r), "乙")
    check("since 过滤旧锁后仍是「乙」", core.open_draft_name(_r, since=300), "乙")
    check("since 比所有锁都新 → 认不出（不误报）", core.open_draft_name(_r, since=1000), None)
    (_r / "丙").mkdir()
    (_r / "丙" / ".locked").write_text("", encoding="utf-8")
    _os.utime(_r / "丙" / ".locked", (900, 900))
    check("重启后新打开的草稿会顶替旧的", core.open_draft_name(_r, since=300), "丙")
check("run_pipeline 会把目标草稿名传进「请双击草稿…」提示",
      "draft_hint=draft.name" in _rp_src, True)
check("★ 换草稿会**直接中止**（2026-09-18 晚：清空=真删，认错草稿救不回来，宁可不动手）",
      "已经停下了，没有动你的时间线" in _rp_src and "切回草稿" in _rp_src, True)

print("[11] ★ 导出后还原项目：写回预合成前备份的草稿 json（只写元数据，产物不碰）")
# 用户原话（2026-09-18）："打开原草稿后，先清空里面的内容，然后将新东西丢进去，
# 等待用户导出，导出后再还原（还原回没有预合成的时候）"。
# 因为原内容是被**真删**的（Ctrl+A → Del），没有"开关"可拨回来，所以还原改成
# 写回预合成**之前**自动备份的草稿元数据。用户明确批准了这个"解禁"，
# 但**边界没变**：只写草稿里的 .json，产物 mp4 一个字节都不碰。
_rd_src = _inspect.getsource(core.restore_draft)
check("restore_draft 存在且可调用", callable(core.restore_draft), True)
check("还原 = 从备份**写回**草稿元数据（shutil.copy2 到草稿目录）",
      "shutil.copy2" in _rd_src and "backup_info" in _rd_src, True)
check("★ 必须先杀剪映再写回（它退出时会覆盖写回，顺序反了就白写）",
      0 <= _rd_src.find("_jianying_running") < _rd_src.find("shutil.copy2"), True)
check("★ 写回前先给自己留一份「还原前」快照（后悔了能捞）",
      "还原前" in _rd_src and "remember=False" in _rd_src, True)
check("★ 没有备份时**拒绝动刀**，只提示手动恢复（不凭空改草稿）",
      "没找到可还原的备份" in _rd_src, True)
check("★ 备份在但原草稿目录不存在时也拒绝写（不往别处乱写）",
      "原草稿目录" in _rd_src and "被改名" in _rd_src, True)
check("★ 只写 .json：写回循环里有后缀二次校验（非 json 直接 continue 跳过）",
      'suffix.lower() != ".json"' in _rd_src, True)
check("★ 还原写回的相对路径只拼在草稿目录下（dr / rel），不会写到别处",
      "dr / rel" in _rd_src and "tgt = dr / rel" in _rd_src, True)
check("红线：还原里没有任何解码/转码/解密字样",
      not any(t in _rd_src.lower() for t in ("decrypt", "deobfuscate", "remux",
                                             "transcode", "ffmpeg", "xor")), True)
check("红线：还原**不写产物**（源码里不出现 combination / *_video.mp4）",
      "combination" not in _rd_src and "_video.mp4" not in _rd_src, True)
check("还原写回前会等剪映真的起来（用 wait_jianying 系的重启路径）",
      "_restart_and_enter" in _rd_src, True)

_gui_src = open(Path(__file__).with_name("剪映伴侣.py"), encoding="utf-8").read()
check("右键菜单里有「还原草稿」入口，且真的接到 core.restore_draft",
      "还原草稿" in _gui_src and "target=core.restore_draft" in _gui_src, True)
check("★ 还原入口真的**加进了菜单**（以前只写了函数没接菜单，用户右键找不到）",
      '_add("restore"' in _gui_src and 'self._restore_draft)' in _gui_src, True)
check("GUI 里不再出现「停用原内容」这类过期文案（流程已改成清空）",
      "停用" not in _gui_src, True)
check("流水线成功提示里教用户怎么还原（且说明是回到预合成前）",
      "回到预合成前" in _rp_src and "还原草稿" in _rp_src, True)
check("★ 流水线里备份排在预合成按键**之前**（晚了备份的就是预合成后的草稿）",
      0 <= _rp_src.find("backup_draft_json") < _rp_src.find("_send_keys_sequence"), True)
check("★ 备份失败时如实告知「导出后无法自动还原」（不假装有备份）",
      "无法自动还原" in _rp_src or "没能做出草稿备份" in _rp_src, True)

print("[12] ★ 自动打开原草稿：封面匹配定位卡片 + 三重护栏（2026-09-18 实测通过）")
# 用户要求"伴侣自己打开原草稿"。剪映没有非鼠标入口，所以只能真点一下卡片；
# 但这一下必须**又准又安全**：封面匹配认卡片 → WindowFromPoint 复核 → .locked 验证。
from PIL import Image as _Img, ImageDraw as _Draw  # noqa: E402

_cov = _Img.new("RGB", (240, 135))
_d = _Draw.Draw(_cov)
for _i in range(0, 240, 8):                       # 造一张有辨识度的封面
    _d.rectangle([_i, 0, _i + 4, 135], fill=(_i % 255, (255 - _i) % 255, (_i * 3) % 255))
_d.ellipse([60, 30, 170, 110], fill=(250, 240, 20))
_d.rectangle([20, 90, 90, 130], fill=(10, 200, 120))

check("card_template 按 4:3 中心裁剪再缩放（尺寸对得上）",
      core.card_template(_cov, 120, 90).size, (120, 90))
_orig60 = _cov.convert("L").resize((60, 34))
_flip = _cov.convert("L").transpose(_Img.FLIP_LEFT_RIGHT).resize((60, 34))
check("_pearson_l 同图相关度 = 1（用 multiply 截断会算出 0.913 那种假值）",
      round(core._pearson_l(_orig60, _orig60), 3), 1.0)
check("_pearson_l 不会跑到 ±1 以外",
      -1.0 <= core._pearson_l(_orig60, _flip) <= 1.0, True)

# 造一张"首页"：深灰底 + 干扰块，把封面做的卡片贴到 (200,300) 大小 120x90
_scr = _Img.new("RGB", (800, 600), (26, 26, 30))
_d2 = _Draw.Draw(_scr)
for _i in range(6):
    _d2.rectangle([20 + _i * 130, 40, 120 + _i * 130, 110], fill=(70, 70, 90))
_card = core.card_template(_cov, 120, 90).convert("RGB")
_scr.paste(_card, (200, 300))
_d2.rectangle([245, 330, 275, 360], fill=(255, 170, 30))     # 模拟剪映的水印遮挡
_hits = core.locate_draft_card(_scr, _cov)
check("封面匹配能在首页里找到那张卡片", bool(_hits), True)
if _hits:
    _r, _X, _Y, _W, _H = _hits[0]
    _cx, _cy = _X + _W // 2, _Y + _H // 2
    check("找到的位置就在真卡片中心附近（±20px）",
          abs(_cx - 260) <= 20 and abs(_cy - 345) <= 20, True)
    check("有遮挡也能匹配上（相关度 ≥ 0.55）", _r >= 0.55, True)
    check("第二名要么没有、要么明显更低（排序干净）",
          (len(_hits) < 2) or (_hits[1][0] < _r), True)
check("封面是纯色时明确返回 -2.0（不给出假分数）",
      core._pearson_l(_Img.new("L", (40, 30), 128), _Img.new("L", (40, 30), 128)), -2.0)

with _tempfile.TemporaryDirectory() as _td:
    check("草稿没有封面图时返回 None（不硬猜）",
          core.draft_cover_path(_td), None)
    _cvp = Path(_td) / "draft_cover.jpg"
    _Img.effect_noise((320, 240), 60).convert("RGB").save(_cvp, quality=95)
    check("草稿有封面图时返回它的路径", core.draft_cover_path(_td), _cvp)
    _cvp.write_bytes(b"x")          # 太小的不算（截断/占位图）
    check("封面文件过小视为没有", core.draft_cover_path(_td), None)

_od_src = _inspect.getsource(core.open_draft_by_card)
check("点之前用 WindowFromPoint 复核坐标上是哪个窗口",
      "window_root_at" in _od_src and "!= hwnd" in _od_src, True)
check("点之后用 open_draft_name 验证真的进了目标草稿",
      "open_draft_name" in _od_src and "got == name" in _od_src, True)
check("点完把光标还原（尽量不打扰）",
      "mouse_double_click" in _od_src, True)
check("双击间隔短于系统双击时限（单击打不开草稿）",
      _inspect.getsource(core.mouse_double_click).count("mouse_event") == 2, True)
check("认不准 / 被挡住 → 返回 None，绝不乱点",
      _od_src.count("return None") >= 3, True)
check("自动打开是**只读**的（不写 / 不改 / 不删任何文件）",
      not any(t2 in _od_src for t2 in ("write_text", "unlink", "rename", "remove",
                                       "rmtree", "shutil", "os.replace")), True)
check("红线：自动打开里没有解码/转码/解密字样",
      not any(t2 in _od_src.lower() for t2 in ("decrypt", "deobfuscate", "remux",
                                               "transcode", "ffmpeg", "xor")), True)
check("默认**关闭**「自动打开原草稿」（用户定案：宁可自己点，换 100% 确定性）",
      _orig.get("auto_open_draft"), False)
_eep_src = _inspect.getsource(core.ensure_edit_page)
check("ensure_edit_page 支持 draft_dir / auto_open 并会调用自动打开",
      "auto_open" in _eep_src and "open_draft_by_card" in _eep_src, True)
# ★ 关掉自动打开时，伴侣必须**点名草稿 + 说清怎么打开**（第十三批：用户反馈
#   "提示不够（比如让用户打开草稿等）"——原来只说"请打开草稿"，没说在哪、怎么开；
#   剪映的草稿**只能双击首页卡片**进，不写清楚用户就会干等两分钟）。
check("关掉自动打开时会点名草稿、说清怎么打开、等用户自己点（不碰鼠标）",
      "请双击草稿「{draft_hint}」卡片打开" in _eep_src
      and "卡片" in _eep_src
      and "draft_hint" in _eep_src
      and "auto_open and draft_dir and not auto_tried" in _eep_src, True)
# ★ 结构闸门：自动点卡片**只能**有那一个入口，且必须站在 auto_open 闸门后面。
#   （以后谁在别处再加一处调用，就等于关着开关也会动鼠标 —— 这里立刻红。）
check("open_draft_by_card 全流程只有一处调用（不会绕过开关偷偷点鼠标）",
      _eep_src.count("open_draft_by_card(") == 1
      and _inspect.getsource(core.run_pipeline).count("open_draft_by_card") == 0, True)
# ★ 三处 `cfg.get("auto_open_draft", ...)` 的兜底默认都必须是 False，否则又悄悄变回自动。
_rae_src = _inspect.getsource(core._restart_and_enter)
check("重启后「再进草稿」那条路的兜底默认也是关",
      'auto_open_draft", False' in _rae_src, True)
check("GUI 菜单勾选状态 / 开关翻转的兜底默认也都是关",
      'auto_open_draft", False' in _gui_src, True)
check("run_pipeline 把目标草稿目录传下去", "draft_dir=draft" in _rp_src, True)
check("右键菜单有「自动打开原草稿」开关，且接线到 core.save_config",
      "自动打开原草稿" in _gui_src and "auto_open_draft" in _gui_src, True)

print("[13] ★ 静态闸门：不留「用了却没先赋值」的局部变量（UnboundLocalError 的回归保险）")
# 历史教训：改流水线时把某个变量定义所在的代码块整段删掉，变量却在后面还被引用 →
# run_pipeline 一进那一步就 UnboundLocalError（异常被兜住，只写日志，界面看起来"没动作"）。
# 这类错误 pyflakes 也抓不到，所以这里自己扫：变量"首次赋值行号 > 使用行号"即视为问题。
import ast as _ast


def _use_before_assign(src_text):
    tree = _ast.parse(src_text)
    problems = []

    def scan_fn(fn):
        first = {}
        params = {a.arg for a in list(getattr(fn.args, "posonlyargs", []))
                  + list(fn.args.args) + list(fn.args.kwonlyargs)}
        if fn.args.vararg:
            params.add(fn.args.vararg.arg)
        if fn.args.kwarg:
            params.add(fn.args.kwarg.arg)
        # global / nonlocal 声明的名字按"外部已有"处理
        for sub in _ast.walk(fn):
            if isinstance(sub, (_ast.Global, _ast.Nonlocal)):
                params.update(sub.names)

        def collect(node):
            for st in _ast.iter_child_nodes(node):
                if isinstance(st, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                   _ast.Lambda, _ast.ClassDef)):
                    continue
                if isinstance(st, _ast.Name) and isinstance(st.ctx, _ast.Store):
                    first.setdefault(st.id, st.lineno)
                elif isinstance(st, _ast.ExceptHandler) and st.name:
                    first.setdefault(st.name, st.lineno)
                elif isinstance(st, (_ast.Import, _ast.ImportFrom)):
                    for al in st.names:
                        first.setdefault((al.asname or al.name).split(".")[0], st.lineno)
                collect(st)

        collect(fn)

        def walk(node, stmt=None):
            for st in _ast.iter_child_nodes(node):
                if isinstance(st, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                   _ast.Lambda, _ast.ClassDef)):
                    continue          # 嵌套作用域单独扫
                cur = st if isinstance(st, _ast.stmt) else stmt
                if isinstance(st, _ast.Name) and isinstance(st.ctx, _ast.Load):
                    nm = st.id
                    # 同一条语句内的绑定（如生成器 `for a, b in X` 与它引用的 a/b）不算
                    same_stmt = cur is not None and first.get(nm, 0) <= (cur.end_lineno or cur.lineno)
                    if nm in first and nm not in params and not same_stmt \
                            and first[nm] > st.lineno:
                        problems.append((fn.name, nm, st.lineno, first[nm]))
                walk(st, cur)

        walk(fn)

    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            scan_fn(node)
    return problems


check("检查器本身有效（合成样例能被抓出）",
      len(_use_before_assign("def f():\n    print(a)\n    a = 1\n")) > 0, True)
_src_core = open(core.__file__, encoding="utf-8").read()
_problems = _use_before_assign(_src_core)
check("jy_core.py 无「先用后赋值」的局部变量", _problems, [])

print("[14] ★ 2026-09-18 实测三坑回归：认错窗口 / 模态框锁焦点 / 靠封面分数排序")
import json as _json  # noqa: E402

# ── 坑①：`find_jianying()` 把 52x19 的工具提示当成了剪映主窗口。
#    主窗口切页/重建的那一瞬枚举不到，旧逻辑只剩小配件窗可挑 →
#    activate 返回 False、Ctrl+Alt+Q 送进空气，日志只显示"停在首页/超时"。
check("主窗口最小尺寸闸存在且够大（52x19 的工具提示必须被排除）",
      core.MAIN_MIN_W >= 200 and core.MAIN_MIN_H >= 150, True)
_fj_src = _inspect.getsource(core.find_jianying)
check("find_jianying 用尺寸闸滤掉小配件窗",
      "MAIN_MIN_W" in _fj_src and "MAIN_MIN_H" in _fj_src, True)
check("find_jianying 优先无 owner 的窗口（有 owner 的是模态子对话框）",
      "c[5] == 0" in _fj_src, True)
_ws_src = _inspect.getsource(core._window_size)
check("最小化窗口改用还原尺寸（GetWindowRect 会给 -32000 的假坐标）",
      "IsIconic" in _ws_src and "GetWindowPlacement" in _ws_src
      and "rcNormalPosition" in _ws_src, True)
_wj_src = _inspect.getsource(core.wait_jianying)
check("wait_jianying 会重试（剪映切页时会短暂重建主窗口，单发一次会误判成没开）",
      "while True" in _wj_src and "sleep" in _wj_src and "timeout" in _wj_src, True)
check("单发查找的地方都改成了等待式（一键导出入口 / 预合成登记）",
      all("wait_jianying" in _inspect.getsource(f)
          for f in (core.run_pipeline, core.commit_draft_registration)), True)
check("还原草稿走「重启 + 等草稿编辑页」的等待式路径（不再单发一次就下结论）",
      "_restart_and_enter" in _rd_src
      and "while" in _inspect.getsource(core._restart_and_enter)
      and "find_jianying" in _inspect.getsource(core._restart_and_enter), True)

# ── 坑②：剪映的「链接媒体」是**主窗口拥有的模态子对话框**，一出现就锁死键盘焦点：
#    activate 恒失败、Ctrl+A / Shift+E / Ctrl+Alt+Q 全送不进去，
#    连"自动开草稿"都因为激活不了窗口而放弃。它等价于点取消（素材保持离线继续），
#    关掉它**不碰任何文件**。
check("「链接媒体」被认成「可关掉的挡路模态框」",
      core.is_dismissable_modal("链接媒体", 8398360), True)
check("没有 owner 的同名窗口不算模态框（独立顶层窗走 classify_popup 那条路）",
      core.is_dismissable_modal("链接媒体", 0), False)
check("别的标题一律不关（白名单宁缺毋滥）",
      [core.is_dismissable_modal(t, 8398360)
       for t in ("导出-9月17日", "JianyingPro", "预合成", "保存草稿", "")],
      [False] * 5)
check("「链接媒体」仍不会被 classify_popup 判成会员弹窗/广告（不触发自动开跑）",
      core.classify_popup("链接媒体", 556, 455), None)
check("模态框白名单只收「链接媒体」这一种",
      tuple(core.MODAL_TITLES), ("链接媒体",))
check("总开关可以整个关掉（enabled=False → 一个都不关）",
      core.dismiss_jy_modals(enabled=False), 0)
check("默认开启「清掉挡路模态框」", _orig.get("dismiss_modals"), True)
_dfm_src = _inspect.getsource(core.dismiss_jy_modals)
check("关模态框=等价点取消，不碰任何文件",
      not any(t2 in _dfm_src for t2 in ("write", "unlink", "remove", "rename",
                                        "replace", "rmtree")), True)
check("_wait_foreground（发键前的必经点）每轮都先清模态框",
      "dismiss_jy_modals" in _inspect.getsource(core._wait_foreground), True)
check("ensure_edit_page 每轮也清模态框",
      "dismiss_jy_modals" in _eep_src, True)

# ── 坑③：封面匹配的**排序**不可信。
#    实测两份草稿的封面都是剪映的占位图（「媒体丢失」/「媒体格式不支持」），
#    只有中间一小块文字不同 → "自己 vs 别人"只差 **0.047**（0.694 / 0.647），
#    稍有扰动就翻盘 → 会点到隔壁卡片 → 打开错误草稿 → 正好撞上「媒体格式不支持」。
#    改用**剪映自己的显示顺序**：root_meta_info.json 的 all_draft_store 数组顺序
#    == 首页卡片左右顺序（已用卡片上的 "92.9K / 56.0M" 对过元数据的 materials_size）。
_ddo_src = _inspect.getsource(core.draft_display_order)
check("显示顺序只读剪映元数据（不写任何文件）",
      "root_meta_info.json" in _ddo_src and "all_draft_store" in _ddo_src
      and not any(t2 in _ddo_src for t2 in ("write_text", "unlink", "remove")), True)
with _tempfile.TemporaryDirectory() as _td3:
    check("读不到 root_meta_info.json → 返回空（不硬猜）",
          core.draft_display_order(_td3), [])
    (Path(_td3) / "root_meta_info.json").write_text(
        _json.dumps({"all_draft_store": [{"draft_name": "A"}, {"draft_name": "B"},
                                         {"draft_name": "C"},
                                         {"draft_fold_path": r"X:\d\D"},
                                         "垃圾数据"]}, ensure_ascii=False),
        encoding="utf-8")
    check("按数组顺序返回（= 首页卡片左右顺序），非字典项跳过",
          core.draft_display_order(_td3), ["A", "B", "C", "D"])
_od2 = _inspect.getsource(core.open_draft_by_card)
check("排序改用显示顺序（不再靠封面分数挑卡）",
      "draft_display_order" in _od2 and "显示顺序" in _od2, True)
check("点错草稿立刻退回首页换一张，而不是继续往下做",
      "退回首页换一张" in _od2 and "returnDraftPage" in _od2, True)
check("验证用的 since 取「本次点击之前」（防上一次的旧锁被误认成这一次）",
      "t_click" in _od2, True)
check("点之前先确认那个坐标上真有卡片（_card_at）",
      "_card_at" in _od2, True)
check("同一个草稿被重复打开就放弃（不做无意义的循环）",
      "tried_names" in _od2, True)

# ── 卡片槽位识别本身：三张等距卡片 + 上半页假峰
_scr3 = _Img.new("RGB", (900, 700), (28, 28, 28))
_d3 = _Draw.Draw(_scr3)
for _k in range(3):
    _d3.rounded_rectangle([250 + _k * 112, 560, 250 + _k * 112 + 96, 660],
                          radius=10, fill=(69, 11, 10))
_hits3 = [(0.90 - _k * 0.05, 250 + _k * 112, 560, 96, 100) for _k in range(3)]
check("_card_at：卡片正中就是卡片", core._card_at(_scr3, 298, 610), True)
check("_card_at：页面空白处不是卡片", core._card_at(_scr3, 600, 300), False)
_sl3, _ry3 = core._card_row_slots(_scr3, _hits3)
check("还原出整排卡片（左→右，间距 112）", _sl3, [298, 410, 522])
check("行中心落在卡片真实上下边中点附近（±12px）", abs(_ry3 - 610) <= 12, True)
_sl4, _ry4 = core._card_row_slots(_scr3, [(0.99, 300, 40, 96, 100)] + _hits3)
check("上半页的假峰不会把行锚点带偏（只在 y > 45% 处找草稿行）",
      (_sl4, abs(_ry4 - 610) <= 12), ([298, 410, 522], True))
check("卡片间距兜底值合理（卡片是固定像素尺寸，实测 ≈112）",
      80 <= core.CARD_PITCH_FALLBACK <= 160, True)
check("「本地草稿」行只在页面下半部分找",
      0.3 <= core._CARD_ROW_YMIN <= 0.6, True)
check("自动打开只在下半页做封面匹配（上半页的横幅/推荐块不参与）",
      "_CARD_ROW_YMIN" in _od2 and "crop" in _od2, True)

print("[15] ★ 文件夹窗口规整化（2026-09-18 用户要「大小和框框规矩点」）")
# 起因：`explorer /select` 弹出的资源管理器窗口**默认是最大化**（实测
# GetWindowPlacement.showCmd=3、rect = 整块屏），把主屏整个盖住、很乱。
_ofas_src = _inspect.getsource(core.open_folder_and_select)
_tew_src = _inspect.getsource(core.tidy_explorer_window)
_mwa_src = _inspect.getsource(core.monitor_work_area)
check("explorer /select 的路径带引号（草稿路径含 \"User Data\" 空格）",
      '/select,"' in _ofas_src, True)
check("打开后会调 tidy_explorer_window 规整",
      "tidy_explorer_window" in _ofas_src, True)
check("规整 = 先 SW_RESTORE(9) 再 SetWindowPos（原来就是最大化的）",
      "ShowWindow(hwnd, 9)" in _tew_src and "SetWindowPos" in _tew_src, True)
check("规整尺寸是固定值且看着正常（不是全屏）",
      600 <= core.FOLDER_WIN_W <= 1600 and 400 <= core.FOLDER_WIN_H <= 1080, True)
check("规整只在**同一块屏内**居中（窗口所在屏的工作区，避开任务栏）",
      "MonitorFromWindow" in _mwa_src and "GetMonitorInfoW" in _mwa_src
      and "rcWork" in _mwa_src, True)
check("窗口被复用时也认得出（同一文件夹已开着就不再新建窗口）",
      "key in title" in _ofas_src, True)
check("认不出窗口就跳过、不拖垮流程（返回 None，不抛异常）",
      "跳过规整" in _ofas_src and _ofas_src.count("return None") >= 2, True)
check("规整是**纯窗口操作**：不写/不改/不删任何文件",
      not any(t in _ofas_src + _tew_src for t in
              ("write_text", "unlink", "rename", "rmtree", "shutil",
               "os.replace", "remove(")), True)
check("红线：规整里没有解码/转码字样",
      not any(t in (_ofas_src + _tew_src).lower() for t in
              ("decrypt", "remux", "transcode", "ffmpeg", "xor")), True)
check("默认开启规整（folder_win_tidy）", _orig.get("folder_win_tidy"), True)
check("两个调用点都按配置传 tidy（run_pipeline / extract_latest）",
      _rp_src.count("folder_win_tidy") == 1
      and _inspect.getsource(core.extract_latest).count("folder_win_tidy") == 1, True)
# ★ 2026-09-18 实测追加：`explorer /select` 把窗口开在**主显示器**，而用户盯的是
#   剪映所在那块（本机 -1920,0）；两块屏一人一半 ⇒ 用户看不到文件夹，
#   自动拖拽还变成跨屏长途。所以要把文件夹摆到**剪映那块屏**上。
check("★ 文件夹摆到**剪映所在那块屏**上（near=剪映窗口 → 同屏可见）",
      "near" in _inspect.signature(core.tidy_explorer_window).parameters
      and "monitor_work_area(near or hwnd)" in _tew_src
      and "near" in _inspect.signature(core.open_folder_and_select).parameters
      and "tidy_explorer_window(target, near=near)" in _ofas_src
      and _rp_src.count("near=hwnd") == 1
      and "near=" in _inspect.getsource(core.extract_latest), True)

print("[16] ★ 自动把预合成文件拖进时间线（2026-09-18 用户：最好能把相应文件自动拖进去）")
# 起因：教程第⑥步"把预合成文件拖进时间线"。用户实测 **Ctrl+V 行不通**（剪映不吃
# 剪贴板的文件落地），所以只能**真鼠标拖拽**。难点全在"怎么拖得准 + 怎么证明拖成功了"：
#   ① Win11 资源管理器是 WinUI —— 窗口树里没有 SysListView32，只能走 UIA；
#   ② 条目要**精确匹配**文件名（旁边有 `<GUID>_video.mp4.alpha.mp4` 占位，包含匹配会抓错）；
#   ③ 抓取点必须取**图标中心**（条目框中心常落在空白上 → 变成框选，不是拖拽）；
#   ④ 拖拽必须**分步**移动（一步到位 Explorer 不认）；
#   ⑤ 落点必须先用 window_root_at 确认那一像素上**真的是剪映**；
#   ⑥ 拖完用 PrintWindow 抓"时间线那一条带"和拖之前比像素，变了才算成功。
#   ⑦ 任何不确定 → 返回 False **交回用户手动拖**，绝不假装成功；全程不碰任何文件。
_uli_src = _inspect.getsource(core.uia_list_items)
_fei_src = _inspect.getsource(core.find_explorer_item)
_drag_src = _inspect.getsource(core.drag_file_into_jianying)
_uia_all = _uli_src + _fei_src + _drag_src
_core_all = (HERE / "jy_core.py").read_text(encoding="utf-8")
_gui_all = (HERE / "剪映伴侣.py").read_text(encoding="utf-8")
_xl_src = _inspect.getsource(core.extract_latest)

check("UIA 读条目存在（uia_list_items / find_explorer_item）",
      callable(getattr(core, "uia_list_items", None))
      and callable(getattr(core, "find_explorer_item", None)), True)
check("★ 零依赖：这次的 UIA/拖拽代码不引 pywinauto / comtypes / uiautomation"
      "（单文件 exe 不能被拖胖）",
      not any(m in _uia_all for m in
              ("import comtypes", "comtypes.client", "pywinauto", "uiautomation",
               "import win32com", "from win32com")), True)
check("★ vtable 直调：CreatePropertyCondition 的 2 参必须是 VARIANT（传 int 会崩）",
      "_var_int" in _uli_src and "vt = 3" in _inspect.getsource(core._var_int)
      and "_UIA_CREATE_PROP_COND" in _uli_src, True)
check("★ SysFreeString 在 oleaut32（放 ole32 会 AttributeError）",
      "oleaut32" in _core_all and "oleaut32.SysFreeString" in _uli_src, True)
check("★ 条目按文件名**精确相等**匹配（不能用包含：会抓到 .alpha.mp4 占位）",
      "nm != name" in _fei_src and "name in nm" not in _fei_src
      and "nm in name" not in _fei_src, True)
check("★ 抓取点取**图标区中点**，不是条目框中心（框中心会变框选）",
      "text_rc[1] > t + 8" in _fei_src and "text_rc[1]) // 2" in _fei_src, True)
check("拖拽 = 真鼠标（SetCursorPos + mouse_event 下/上）",
      "SetCursorPos" in _drag_src and "mouse_event" in _drag_src
      and "0x0002" in _drag_src and "0x0004" in _drag_src, True)
check("★ 必须**分步**移动（一步到位不算拖拽）",
      "for i in range(1, steps + 1)" in _drag_src and "// steps" in _drag_src, True)
check("★ 按下前先把资源管理器**抬到最上层**（剪映最大化会盖住图标，否则点到剪映）",
      "_raise_for_drag(explorer_hwnd)" in _drag_src, True)
check("★ 落点护栏检查前先把**剪映**抬到最上层（被别的全屏窗口盖住时护栏会正确拒绝）",
      "_raise_for_drag(jy_hwnd)" in _drag_src, True)
check("★ 落点先用 window_root_at 校验那一像素上真的是剪映",
      "window_root_at" in _drag_src and "== jy_hwnd" in _drag_src, True)
check("★ 拖完自检：抓时间线那一条带比像素，变了才算成功",
      "_timeline_strip" in _drag_src and "_strip_diff" in _drag_src
      and "DRAG_DIFF_MIN" in _drag_src, True)
check("★ 自检基线在「抬资源管理器」**之前**取"
      "（否则光「资源管理器消失」就能骗过自检，假阳性）",
      _drag_src.index("_strip_of(base_img)")
      < _drag_src.index("_raise_for_drag(explorer_hwnd)"), True)
check("★ 自检是**前后各抓一次**（基线 + 拖后），不是只看一次",
      "_strip_of(base_img)" in _drag_src and "_timeline_strip" in _drag_src, True)
check("★ 拖完把剪映**抬回最上层**再抓（和基线镜头对齐，防资源管理器消失造成假阳性）",
      _drag_src.count("_raise_for_drag(jy_hwnd)") == 2, True)
# ★ 2026-09-18 实测：本机 `_alt_trick_foreground`（ALT 技巧）**恒失败**
#   （有别的全屏窗口满屏占着剪映那块屏）。真正管用的是 SwitchToThisWindow 与
#   SetWindowPos(TOPMOST→NOTOPMOST)，两者都**不动鼠标**。
_raise_src = _inspect.getsource(core._raise_for_drag)
check("★ 抬窗有实测可用的兜底（SwitchToThisWindow + TOPMOST→NOTOPMOST）",
      "SwitchToThisWindow" in _raise_src and "HWND_TOPMOST" in _raise_src
      and "HWND_NOTOPMOST" in _raise_src, True)
check("★ 抬窗**不动鼠标**（拖拽前的抬升不该乱点）",
      "SetCursorPos" not in _raise_src and "mouse_event" not in _raise_src, True)
check("★ hwndInsertAfter 用 c_void_p 传（裸 int 会被当 32 位，-1 就不是 HWND_TOPMOST）",
      "c_void_p(HWND_TOPMOST)" in _raise_src, True)
check("★ 不确定就返回 False（找不到条目/被盖住/自检没过），绝不假装成功",
      _drag_src.count("return False") >= 6 and "绝不假装成功" in _drag_src, True)
check("★ 落点写成**窗口比例**（用户改窗口大小也能跟着走）",
      0.03 <= core.DRAG_DROP_X_FRAC <= 0.30 and 0.55 <= core.DRAG_DROP_Y_FRAC <= 0.90, True)
check("★ 纵向落点是**动态找空白带**（empty_lane_y），不是写死比例",
      callable(getattr(core, "empty_lane_y", None))
      and "empty_lane_y(base_img)" in _drag_src
      and "DRAG_LANE_SCAN_X" in _inspect.getsource(core.empty_lane_y), True)
check("★ 空带检测**避开左侧轨道头图标列**（否则永远找不到空带）",
      0.20 <= core.DRAG_LANE_SCAN_X[0] <= 0.35, True)
check("★ 自检纵带覆盖**整条时间线**（新轨道一定落在里面，否则会漏检）",
      core.DRAG_CHECK_Y[0] <= 0.70 and core.DRAG_CHECK_Y[1] >= 0.93, True)
check("★ 有 3 个纵向微调候选 + 比例兜底（动态检测失败也不至于没落点）",
      len(core.DRAG_DROP_Y_TRIES) >= 3
      and "退回比例兜底" in _drag_src, True)
# ★ 2026-09-18 实测：往时间线**空白处**丢文件时剪映是**按播放头**落位的
#   （那次播放头在 17.28s → 新片段落到 17.28s，导出总长变 37s、前 17s 是黑的）。
#   剪映快捷键表里 `locateFirstFrame`（本机 Home）= 播放头回首帧，所以拖之前先按一下。
check("★ 拖前把播放头打回首帧（Home），新片段才和原内容对齐在 0",
      'read_shortcut("locateFirstFrame"' in _drag_src
      and "GetForegroundWindow() == jy_hwnd" in _drag_src, True)
check("★ 拖拽路径是纯物理动作：不写/不改/不删任何文件",
      not any(t in _uia_all for t in
              ("write_text", "unlink", "rename", "rmtree", "shutil",
               "os.replace", "remove(", "open(")), True)
check("红线：拖拽/UIA 代码里没有解码/转码/解密字样",
      not any(t in _uia_all.lower() for t in
              ("decrypt", "remux", "transcode", "ffmpeg", "xor", "deobfuscate")), True)
check("★ 默认**不**自动拖入（auto_drag_product=False，2026-09-18 用户定案：让用户手动拖）",
      core.default_config().get("auto_drag_product"), False)
check("★ 代码保留：勾上开关就能回到全自动（拖拽函数还在、还被流水线调到）",
      callable(core.drag_file_into_jianying)
      and "drag_file_into_jianying" in _rp_src, True)
check("★ 关掉时**不能**再说「已自动拖进时间线」（会说谎）",
      '"已拖进时间线"' in _rp_src and "自动拖入是**关闭**状态" in _rp_src, True)
check("★ 三种收尾分开写：关掉 / 拖成功 / 拖失败，理由不同不能混",
      all(t in _rp_src for t in ("自动拖入是**关闭**状态",
                                "**自动拖进时间线**了",
                                "自动拖入**没成功**")), True)
check("★ 手动拖的提示里带「拖前按 Home 归零」（实测：往空白处丢是按播放头落位）",
      "按一下 **Home**" in _rp_src and "按播放头落位" in _rp_src, True)
check("★ 流水线结尾明说「伴侣什么都不做，会一直等你」+ 导出后怎么还原",
      "伴侣什么都不做" in _rp_src and "一直等你" in _rp_src
      and "等你导出完成" in _rp_src and "还原草稿" in _rp_src, True)
# ★ 2026-09-18 用户实测反馈："打开文件夹之前没有清空掉草稿内容，并且没有等待用户导出，
#   导出后帮用户还原的指示"。三处修复各自钉一条断言（防回退）：
check("★ 顺序修复：**先清空时间线、再打开文件夹**（资源管理器一开就抢前台，反手抢回要卡 5 秒）",
      "open_folder_and_select" in _rp_src
      and 0 <= _rp_src.index("_clear_timeline(cfg") < _rp_src.index("open_folder_and_select(")
      and "open_folder_and_select" not in _rp_src.split("_clear_timeline(cfg")[0], True)
check("★ 清空失败就**不再往下走**（不动时间线、不打开文件夹，只教用户手动）",
      "清空这一步没通过自检，就没往下走" in _rp_src
      and "没有被伴侣改动" in _rp_src, True)

print("[16b] ★★ 清空时间线「假成功」事故（2026-09-18 晚）的三条防回退")
# 用户实测反馈："清空原本内容有问题好像，没成功，然后剪映无响应了"。
# 复盘出三个独立问题，各自钉一条断言。
_clr_src = _inspect.getsource(core._clear_timeline)
check("① ★ 按键要用 **Del**，不能用 Backspace（快捷键表 del=['Backspace','Del']，"
      "read_shortcut 只取第一项 → 老代码一直在发 Backspace）",
      callable(getattr(core, "read_shortcut_all", None))
      and callable(getattr(core, "_pick_clear_delete_key", None))
      # ★ 只断言"真实调用"，别数注释/文档里的字面量（docstring 里就写着 read_shortcut("del")
      #   当反面教材，用 `not in` 会被散文带倒）
      and "del_key = _pick_clear_delete_key()" in _clr_src
      and "read_shortcut_all(" in _clr_src
      and "CLEAR_DEL_PREFER" in _inspect.getsource(core._pick_clear_delete_key), True)
check("①b 真的读得到列表全项（del 同时绑 Backspace 和 Del）",
      len(core.read_shortcut_all("del")) >= 1
      and any(k.strip().lower() in ("del", "delete")
              for k in core.read_shortcut_all("del")), True)
check("①c 挑出来的键必须是 Del 系，不是 Backspace",
      core._pick_clear_delete_key().strip().lower() in ("del", "delete"), True)
check("② ★ 发键前先等剪映空闲（前台 + 主窗口响应），不在加载中劈头发键",
      callable(getattr(core, "_wait_jy_ready_for_keys", None))
      and callable(getattr(core, "_jy_is_responding", None))
      and "_wait_jy_ready_for_keys(hwnd" in _clr_src, True)
check("③ ★★★ 清空**必须自检**，绝不能无条件 return True（旧版就是这里对用户说谎）",
      "_strip_diff" in _clr_src and "DRAG_DIFF_MIN" in _clr_src
      # 末尾的 return True 必须在"自检通过"分支里，不能是无条件兜底
      and _clr_src.rstrip().endswith("return False"), True)
check("③b 不再「往未知状态连发删除键」（旧的防吞双 Del 已去掉）",
      _clr_src.count("send_combo(del_key)") == 1, True)
check("③c 清不掉时最多重试一次，然后如实失败（不假装成功）",
      "for attempt in range(2)" in _clr_src
      and "判定失败，不往下走" in _clr_src, True)
check("③d ★ 自检带比拖拽那条**更宽**（原片段可能在时间线靠上的行，窄带会假失败）",
      core.CLEAR_CHECK_Y[0] < core.DRAG_CHECK_Y[0]
      and core.CLEAR_CHECK_Y[1] >= core.DRAG_CHECK_Y[1]
      and callable(getattr(core, "_clear_strip", None)), True)
check("③e ★ 取基线前先把剪映抬到最上层（编辑页 GPU 渲染只能屏幕截取，"
      "被别的窗口压着会把『真清掉了』误判成『没清』）",
      0 <= _clr_src.find("_raise_for_drag") < _clr_src.find("_clear_strip(hwnd, jr)"), True)
check("③f ★ 抬窗后剪映不在前台就**不发键**（键会打到别的窗口）",
      _clr_src.find("GetForegroundWindow() != hwnd") < _clr_src.find("send_combo("), True)
check("③g ★★ 发键前先单击时间线**取键盘焦点**（否则 Ctrl+A 选中的不是时间线片段）",
      callable(getattr(core, "_focus_timeline_by_click", None))
      and 0 <= _clr_src.find("_focus_timeline_by_click(hwnd)")
      < _clr_src.find("send_combo("), True)
check("③h ★ 取焦点落点避开左侧轨道头图标列（点那儿会整轨选中）",
      core.CLEAR_FOCUS_X_FRAC >= 0.35, True)
check("③i 取焦点这一步**不动文件**（只 SetCursorPos + 单击）",
      not any(t in _inspect.getsource(core._focus_timeline_by_click)
              for t in ("write_text", "unlink", "shutil", "os.replace")), True)
check("★ 结尾明说「左键点本按钮 = 发还原指令」且**会先问一句**（防打断导出）",
      all(t in _rp_src for t in ("左键点本按钮", "它会问你一句",
                                 "不会打断你的导出")), True)
check("★ GUI 常驻「等你导出」状态 + 左键=还原指令（hold=0 不会自动回 idle）",
      "_await_restore" in _gui_all and '"等你导出"' in _gui_all
      and "导出完点我 → 还原草稿" in _gui_all, True)
check("run_pipeline 里只在清空原时间线成功后才拖，且**只有一个调用点**",
      # ★ 只数"带实参的调用"（`...(cfg`），不数注释/文档里提到的函数名 ——
      #   否则往注释里写一句 "走 drag_file_into_jianying 的真拖拽" 就会误红。
      _rp_src.count("drag_file_into_jianying(cfg") == 1
      and _rp_src.index("drag_file_into_jianying(cfg") > _rp_src.index("_clear_timeline"),
      True)
check("★ 用户可见文案里不再有**错误**的 Ctrl+V 提示（用户实测：Ctrl+V 行不通）",
      "也可以 Ctrl+V" not in _rp_src and "直接 Ctrl+V" not in _rp_src
      and "别用 Ctrl+V" in _xl_src, True)
check("GUI：右键菜单有「自动拖进时间线」「文件夹窗口规整」两个开关",
      '"自动拖进时间线"' in _gui_all and '"文件夹窗口规整"' in _gui_all
      and "_toggle_auto_drag" in _gui_all and "_toggle_folder_tidy" in _gui_all, True)
check("GUI：两个开关都在 _sync_menu 里刷 ✓（漏一个就会“点了没反应”）",
      '"autodrag"' in _gui_all and '"foldertidy"' in _gui_all
      and '_mi["autodrag"]' in _gui_all and '_mi["foldertidy"]' in _gui_all, True)

# ================================================================
print("[17] ★ 分发给别人用：不能「我能用、别人不能用」（2026-09-18 第九批）")
# 作者机器上剪映装好、草稿目录在配置里、快捷键也绑好了 —— 点一下就通。
# 但别人拿到的是一台**陌生机器**：可能没装剪映、可能自定义安装路径、
# 可能 Alt+H 已经绑了别的功能、可能桌面写不进去。这一节把这些逐条钉住。

# ---- 17a. 绝不覆盖别人的剪映快捷键 ----
_eh_src = _inspect.getsource(core.ensure_precomp_hotkey)
_ws_src = _inspect.getsource(core.write_shortcut)
_rs_src = _inspect.getsource(core.restore_shortcut_config)
check("① 绑快捷键前先查这个键有没有被**别的动作**占用（旧版是无脑覆盖）",
      "_action_using_key(scheme, want" in _eh_src, True)
check("② 撞了就从候选表里换一个没人用的键",
      len(core.PRECOMP_KEY_CANDIDATES) >= 3
      and "_action_using_key(scheme, cand" in _eh_src, True)
check("③ ★ 改别人的剪映配置**之前**先整目录备份，且备份先于写盘",
      0 <= _ws_src.find("backup_shortcut_config(cfg)")
      < _ws_src.find("f.write_text("), True)
check("④ 备份只做一次，不把『原始状态』那份冲掉",
      'not cfg.get("_shortcut_backup_dir")' in _ws_src, True)
check("⑤ 有还原的路（**只覆盖不回删**），并且菜单里找得到",
      callable(getattr(core, "restore_shortcut_config", None))
      and "shutil.copy2" in _rs_src
      and "unlink" not in _rs_src and "rmtree" not in _rs_src
      and "还原剪映快捷键设置" in _gui_all
      and '_add("restorekeys"' in _gui_all, True)
# ★ 第九批补：找不到剪映快捷键配置文件时**不许假装绑好了**
check("⑥ 绑不上就如实返回三个值（旧版会返回 (键, False)，上层以为绑好了）",
      'return (cfg.get("precomp_hotkey") or "Alt+H"), False, False' in _eh_src, True)
check("⑦ 写盘静默失败也不算绑上（changed=False ⇒ ok=False）",
      "return picked, changed, bool(changed)" in _eh_src, True)
check("⑧ ★ 没绑上就**中止**流程，且中止发生在真正干活之前"
      "（否则硬跑两分钟才报一句看不懂的错）",
      "if not hk_ok:" in _rp_src and "没能给剪映绑上" in _rp_src
      and _rp_src.index("if not hk_ok:") < _rp_src.index("resolve_root(cfg, st)"), True)
_sd_src = _inspect.getsource(core._shortcut_dir)
check("⑨ 快捷键目录有兜底浅找（别人的剪映可能落在非标准位置），但不是全盘扫",
      "glob" in _sd_src and "time.time() - t0 < 3.0" in _sd_src
      and "rglob" not in _sd_src, True)

# ---- 17b. 别人把剪映装在自定义目录，也得找得到 ----
_fj_src = _inspect.getsource(core.find_jianying_exe)
check("① 先问「正在运行的剪映进程」要 exe 路径（自定义安装目录也命中）",
      "_exe_path_of_process" in _fj_src, True)
check("② 静态兜底覆盖三个常见落点（Apps / Program Files / LOCALAPPDATA\\Programs）",
      all(t in _fj_src for t in ('"Apps"', '"Programs"', '"ProgramFiles"')), True)

# ---- 17c. 找不到草稿目录不许全盘乱扫（别人首次运行配置是空的）----
_rr_src = _inspect.getsource(core.resolve_root)
_sh_src = _inspect.getsource(core._shallow_find)
check("① 兜底搜索**不再**用无深度限制的 rglob（会卡几分钟，像死机）",
      "rglob" not in _rr_src, True)
check("② 兜底搜索限深度 + 限时间 + 限目录数，超了就放弃",
      all(t in _sh_src for t in ("max_depth", "budget", "max_dirs",
                                 "time.time() - t0 > budget")), True)
check("③ 跳过 windows / $recycle.bin 这类不可能放草稿的目录",
      "windows" in core.SCAN_SKIP_DIRS
      and "$recycle.bin" in core.SCAN_SKIP_DIRS, True)

# ---- 17d. 环境体检真跑一遍（把备份目录指到临时目录，别在真桌面留垃圾）----
import tempfile as _tf
_tmp = Path(_tf.mkdtemp(prefix="jy_selfcheck_"))
_backup_root_bak = core.backup_root_dir
core.backup_root_dir = lambda: _tmp / "bk"
try:
    _rows = core.env_selfcheck({"draft_root": ""}, quick=True)
finally:
    core.backup_root_dir = _backup_root_bak
check("① 体检返回 [(级别, 标题, 说明)]，级别只有 ok/warn/bad",
      isinstance(_rows, list) and bool(_rows)
      and all(isinstance(r, tuple) and len(r) == 3 and r[0] in ("ok", "warn", "bad")
              for r in _rows), True)
check("② 必须包含「用之前请先知道」（会杀剪映 / 会清空时间线 —— 用户要知情）",
      any("用之前请先知道" in r[1] for r in _rows), True)
check("③ 备份目录能写就报 ok（写不进去就「导出后无法还原」）",
      any(r[0] == "ok" and "备份目录" in r[1] for r in _rows), True)
check("④ quick=True 走的是不扫盘的分支（启动时用，别让开机卡住）",
      "if quick:" in _inspect.getsource(core.env_selfcheck), True)
#   ★ 第十五批：上面那条只是"形状"。这里补**行为级**：把深扫函数换成"一调就炸"，
#     quick=True 必须走不到它（没有草稿目录时直接给"等第一次跑"）。
_pick_bak = core._pick_standard_root
_bd_bak = core.backup_root_dir
core._pick_standard_root = lambda *a, **k: (_ for _ in ()).throw(AssertionError("quick 不该扫盘"))
core.backup_root_dir = lambda: _tmp / "bk"
try:
    _rows_q = core.env_selfcheck({"draft_root": ""}, quick=True)
    _quick_ok = True
except AssertionError:
    _quick_ok = False
finally:
    core._pick_standard_root = _pick_bak
    core.backup_root_dir = _bd_bak
check("④b ★ 行为：quick=True 时**真的**不会去扫候选目录", _quick_ok, True)
check("④c 而且它如实说明\"还没找\"（不是假装通过）",
      any(r[0] == "warn" and "草稿目录" in r[1] for r in _rows_q), True)
check("⑤ 排版成人话且先报结论（有几项必须先解决）",
      "体检结果" in core.format_selfcheck(_rows), True)

# ---- 17e. 首次运行就得让人看明白 ----
check("① GUI 有体检入口，并且**接进了菜单**（挂了函数不接菜单 = 用户找不到）",
      '_add("selfcheck"' in _gui_all and "def _selfcheck" in _gui_all
      and "def _first_run_notice" in _gui_all, True)
check("② 首次运行自动弹一次，之后不再打扰（标记写进配置）",
      "self._first_run" in _gui_all and '"_selfcheck_done"' in _gui_all, True)
check("③ _pump 必须处理 selfcheck 回包（否则体检完永远不弹窗）",
      'item[0] == "selfcheck"' in _gui_all, True)
check("④ 运行日志封顶 2MB，不在别人机器上无限涨",
      "2 * 1024 * 1024" in _gui_all, True)
_si_src = _inspect.getsource(gui.ensure_single_instance)
check("⑤ 防多开两种进程名都认（安装版「剪映伴侣.exe」/ 绿色版 JianyingCompanion.exe）",
      "剪映伴侣" in _si_src and "jianyingcompanion" in _si_src.lower(), True)

# ---- 17f. 安装包：卸载/升级不能把用户配置删掉 ----
_iss_p = HERE / "剪映伴侣安装包.iss"
_iss = _iss_p.read_text(encoding="utf-8") if _iss_p.exists() else ""
# ★ 只看**生效的指令行**，去掉 `;` 注释行 —— 否则注释里写一句
#   「别写成 filesandordirs {app}」就会把这条断言带红（真踩过）。
_iss_live = "\n".join(ln for ln in _iss.splitlines()
                      if not ln.lstrip().startswith(";"))
check("① 卸载脚本**不许** filesandordirs 删整个 {app}"
      "（Inno 升级会先跑旧版卸载 → 用户配置没了，还原功能全废）",
      'filesandordirs; Name: "{app}"' not in _iss_live, True)
check("② 只删自己的垃圾文件，目录空了才回收",
      'Type: files; Name: "{app}\\运行日志.txt"' in _iss
      and "dirifempty" in _iss, True)
check("③ 有稳定的 AppId（升级才认得出是同一个程序，而不是装两个）",
      "AppId=" in _iss, True)
_manual_p = HERE / "使用说明.txt"
_manual = _manual_p.read_text(encoding="utf-8") if _manual_p.exists() else ""
check("④ 安装包带一份「使用说明.txt」并建了快捷方式",
      _manual_p.exists() and 'Source: "使用说明.txt"' in _iss, True)
check("⑤ 使用说明讲清三件最容易出事的事：会杀剪映 / 会清空时间线 / 拖前按 Home",
      all(t in _manual for t in ("杀掉剪映", "清空当前时间线", "Home", "Ctrl+V")), True)

# ================================================================
print("[18] ★ 第十批：隐藏 bug 回归（路径解析 / 进度条倒带 / 大文件指纹 / 状态回调）")
# 这一批不是"加功能"，是**找 bug**：三个都在作者机器上看不出来，
# 要么只在别人的机器上触发，要么只在特定工程规模下触发。

# ---- 18a. 盘根草稿候选：`Path("C:")` 是"驱动器相对路径"，永远匹配不上 ----
# ★ 只看**生效的代码行**（先滤掉整行注释）——注释里正好写着这个反面教材，
#   不滤就会把"正确的修复"当成"还在犯的错"（真踩过，见 [17f]）。
# ★ 2026-09-19 第十五批：候选表从 resolve_root 内联改成了共用的
#   `standard_draft_roots()`（体检那边也要用），所以这两条跟着看新位置。
_src_lines = [ln for ln in _inspect.getsource(core.standard_draft_roots).splitlines()
              if not ln.lstrip().startswith("#")]
_live = "\n".join(_src_lines)
check("① 盘根候选写成 `Path(f\"{drv}:/)\"`（`Path(\"C:\") / \"x\"` = 'C:x' ，"
      "驱动器相对 → 10 条候选全是死代码）",
      'f"{drv}:/"' in _live, True)
check("② 反面写法必须不存在（否则又退回来了）",
      'Path(f"{drv}:")' in _live, False)
check("③ 兜底浅搜那行本来就是对的（`{drv}:` + os.sep），别被一起改坏",
      'Path(f"{drv}:" + os.sep)' in _inspect.getsource(core.resolve_root), True)
check("④ Path 语义自证：驱动器相对路径确实拼不出绝对路径（这条是「知识锚」）",
      str(Path("C:") / "x"), "C:x")
check("⑤ 修复后拼出来的是绝对路径",
      str(Path("C:/") / "JianyingPro Drafts"), "C:\\JianyingPro Drafts")

# ---- 18b. 进度条"倒带"：阶段内只增不减 + 无秒数取阶段末端 ----
check("① 阶段表首尾相接（上一阶段的 hi == 下一阶段的 lo，跨阶段也不会回缩）",
      all(core.PIPELINE_STAGES[i][2] == core.PIPELINE_STAGES[i + 1][1]
          for i in range(len(core.PIPELINE_STAGES) - 1))
      and core.PIPELINE_STAGES[0][1] == 0
      and core.PIPELINE_STAGES[-1][2] == 100, True)
check("② 有秒数：按估算时长线性映射到阶段区间（120s → 阶段起点）",
      core.render_pct(4, "等待渲染 120s", None) == core.PIPELINE_STAGES[3][1], True)
check("③ 有秒数：秒数走完 → 阶段末端",
      core.render_pct(4, "等待渲染 0s", None) == core.PIPELINE_STAGES[3][2], True)
check("④ ★ 无秒数（如「等待落盘 21.6MB」）取阶段**末端**，不取起点",
      core.render_pct(4, "等待落盘 21.6MB", None) == core.PIPELINE_STAGES[3][2], True)
check("⑤ ★★ 第二轮 60s 的换算结果小于上一轮终点时必须夹住（就是「倒带」现场）",
      core.render_pct(4, "等待渲染 60s", core.PIPELINE_STAGES[3][2])
      == core.PIPELINE_STAGES[3][2], True)
_SEQ, _acc = [], None
for _m in ("等待渲染 120s", "等待渲染 90s", "等待落盘 30MB", "等待渲染 60s", "等待渲染 3s"):
    _acc = core.render_pct(4, _m, _acc)
    _SEQ.append(_acc)
check("⑥ 单调性抽查：同一阶段内逐条喂消息，百分比只增不减（含中间插一条无秒数的）",
      (lambda ps: ps == sorted(ps) and len(ps) == 5)(_SEQ), True)
_st_render_src = _inspect.getsource(core.run_pipeline)
check("⑦ run_pipeline 里 st_render 走的就是这个纯函数（别在闭包里又抄一份）",
      "render_pct(_stage_no[0], t, _last_pct[0])" in _st_render_src, True)
check("⑧ 不传 pct 的普通提示沿用本阶段上一次的值（否则会掉回阶段起点）",
      "pct = _last_pct[0]" in _st_render_src, True)

# ---- 18c. 等落盘：大小还在变就别算 md5（大文件每秒整读一遍 = O(n²) 读盘）----
_ws = _inspect.getsource(core.wait_file_settled)
check("① 先 stat 拿大小，且**只**在「大小不再变」的分支里取内容指纹",
      _ws.count("file_fingerprint(path)") == 1
      and _ws.find("stat().st_size") < _ws.find("file_fingerprint(path)"), True)
check("② 还在长的分支里不许出现 md5 调用（那正是要省掉的读盘）",
      "file_fingerprint" not in _ws.split("elif size != last_size:")[1]
      .split("else:")[0], True)

import hashlib as _hashlib  # noqa: E402
import tempfile as _tempfile  # noqa: E402
import threading as _threading  # noqa: E402
import time as _time  # noqa: E402

with _tempfile.TemporaryDirectory() as _td:
    # 18c-③ 分块 md5 与一次性 md5 结果必须一致（改动本身不能算错）
    _big = Path(_td) / "big.bin"
    _payload = bytes(range(256)) * (12 * 1024)          # 3MB，跨 3 个 1MB 块
    _big.write_bytes(_payload)
    _ref = _hashlib.md5(_payload).hexdigest()[:12]
    _got = core.file_fingerprint(_big)
    check("③ 分块算 md5 与整读结果一致（3MB 跨块样本；大小/时间也对）",
          (_got[0], _got[2]), (len(_payload), _ref))

    # 18c-④ 内容变了指纹就得变（否则"稳了"是假的）
    _big.write_bytes(_payload + b"x")
    check("④ 内容变了指纹必须变（防「以为稳了其实还在写」）",
          core.file_fingerprint(_big)[2] != _ref, True)

    # 18c-⑤ 写完的文件要判定为"稳"；一直在长的文件**不许**判稳
    _still = Path(_td) / "still.bin"
    _still.write_bytes(b"done")
    check("⑤ 已经写完的文件 → 稳定（True）",
          core.wait_file_settled(_still, timeout=3, interval=0.1), True)
    _grow = Path(_td) / "grow.bin"
    _grow.write_bytes(b"")
    _stop = _threading.Event()

    def _writer():
        while not _stop.is_set():
            with open(_grow, "ab") as f:
                f.write(b"z" * 4096)
            _time.sleep(0.05)

    _wt = _threading.Thread(target=_writer, daemon=True)
    _wt.start()
    try:
        _r = core.wait_file_settled(_grow, timeout=1.2, interval=0.1)
    finally:
        _stop.set()
    check("⑥ ★ 一直在长的文件不许判稳（旧版会在这里提前 return True → 交付半成品）",
          _r, False)

# ---- 18d. 状态回调：内部 TypeError 不许被当成"签名不符"再叫一次 ----
_double = []


def _bad_cb(text, kind, pct=None, step=None):
    _double.append(text)
    raise TypeError("这是回调自己的 bug，不是签名不符")


core._call_status(_bad_cb, "只该投递一次", "busy", 10, 4)
check("① 回调内部抛 TypeError 时只投递一次（旧写法会吞掉异常再叫一次）",
      len(_double), 1)
_legacy = []


def _old_cb(text, kind):
    _legacy.append((text, kind))


core._call_status(_old_cb, "老签名", "busy", 10, 4)
check("② 只收两个参数的老回调仍能收到（向后兼容不能破）",
      _legacy, [("老签名", "busy")])

# ---- 18e. 二次还原：必须拦住（旧备份再写一遍会无声盖掉还原后的新改动）----
_rd_src = _inspect.getsource(core.restore_draft)
_bd_src = _inspect.getsource(core.backup_draft_json)
_gui_restore = _inspect.getsource(gui.Companion._restore_draft)
check("① 还原写回成功后置位「已用过」，且是在写回**之后**（写失败不算用过）",
      'cfg["_backup_consumed"] = True' in _rd_src
      and 0 <= _rd_src.find("cfg[\"_backup_consumed\"] = True")
      < _rd_src.find("_restart_and_enter"), True)
check("② 做新备份时把标记清回 False（新一轮导出照常能还原）",
      'cfg["_backup_consumed"] = False' in _bd_src
      and "if remember:" in _bd_src
      and _bd_src.find('cfg["_backup_consumed"] = False')
      > _bd_src.find("if remember:"), True)
check("③ ★ 备份原件**不删**（只清状态位，红线：伴侣不删文件）",
      "unlink" not in _rd_src and "rmtree" not in _rd_src, True)
check("④ ★ 二次还原前必须弹确认（别无声无息覆盖新改动）",
      "backup_consumed(self.cfg)" in _gui_restore
      and "ask_yes(" in _gui_restore, True)  # ★ 第十五批：确认框统一走 ask_yes
check("⑤ 菜单里那份备份要标出「已还原过」（用户看不出的话还会再点）",
      "已还原过" in _gui_all and "backup_consumed" in _gui_all, True)

# ================================================================
print("[19] ★ 第十一批（体验）：文案不被裁 + 求助路闭环 + 未还原提示")
# 这一批全是"别人用起来更顺"的改动，但每一条都容易被后续改动悄悄弄坏：
# 自适应宽度一旦被回退成死值，长提示就又被窗口裁掉一半（用户看不到该点哪个草稿）。

# ---- 19a. 文案自适应宽度：任何提示都不许被裁 ----
_resize_src = _inspect.getsource(gui.Companion._resize_to)
_fit_src = _inspect.getsource(gui.Companion._fit_width)
_rep_src = _inspect.getsource(gui.Companion._reposition)
check("① 有自适应宽度常量与省略号工具（不是把宽度写死）",
      isinstance(getattr(gui, "UI_MAX_GROW", None), float) and gui.UI_MAX_GROW > 1.5
      and callable(getattr(gui, "ellipsize", None)), True)
check("② 宽度按「主/副里较宽者」算，并且到上限会降字号",
      "max(f.measure(self._txt), fs.measure(self._sub))" in _fit_src
      and "while need > hard" in _fit_src, True)
check("③ ★ 「放不下」必须立即变宽（否则会被省略号吃掉）",
      "need > avail_now" in _fit_src, True)
check("④ 抖动抑制：宽度量化 + 缩回滞回（倒计时/百分比不带着按钮跳）",
      "UI_GROW_STEP" in _fit_src and "UI_SHRINK_HYST" in _fit_src, True)
check("⑤ ★ 改宽度必须让**定位缓存**失效，否则窗口还停在旧尺寸上（宽底图被裁）",
      "_last_float = None" in _resize_src and "_last_rect = None" in _resize_src, True)
check("⑥ ★ _reposition 的缓存键要带上自身尺寸（原来只比客户区 → 变宽后不重排）",
      "(cw, ch, self.W, self.H)" in _rep_src, True)
check("⑦ 文字一律过 ellipsize 再上屏（最后一道保险）",
      _fit_src.count("ellipsize(") >= 2, True)

# ---- 19b. 求助路闭环：文案让用户"把日志发给作者"，就得能打开那个日志 ----
check("① 三个「打开…」入口都接进了菜单（挂了函数不接菜单 = 用户找不到）",
      all(f'_add("{k}"' in _gui_all for k in ("openlog", "openmanual", "openbackup")), True)
check("② 打开日志 = 配置目录下的 运行日志.txt（和 stdout 重定向同一份）",
      'core.config_path().parent / "运行日志.txt"' in _gui_all, True)
check("③ ★ 文件不存在时如实说，不许静默假装打开了",
      "not p.exists()" in _inspect.getsource(gui.Companion._reveal), True)
check("④ 失败文案指向了那个新入口（不然用户还是找不到）",
      "「打开运行日志」" in _inspect.getsource(core.run_pipeline)
      and "「打开草稿备份文件夹」" in _inspect.getsource(core.restore_draft), True)
check("⑤ 还原类失败文案也指向备份文件夹（「手动拷回去」得先找得到）",
      _inspect.getsource(core.restore_draft).count("「打开草稿备份文件夹」") >= 2, True)

# ---- 19c. 空闲态提示"上次那轮还没还原"（关掉伴侣/重启电脑后提示不能断）----
import types as _types  # noqa: E402
_DEFAULT_SUB = "预合成 · 存草稿 · 清空原内容 · 你手动拖入"
_HINT_SUB = "上次那轮还没还原 · 右键「还原草稿」"
with _tempfile.TemporaryDirectory() as _td:
    _bkdir = Path(_td) / "9月18日_20260918-213739_预合成前"
    _bkdir.mkdir()
    (_bkdir / "draft_content.json").write_text("{}", encoding="utf-8")
    _base_cfg = {"_backup_dir": str(_bkdir), "_backup_draft_dir": _td,
                 "_backup_draft_name": "9月18日"}
    _with = _types.SimpleNamespace(cfg=dict(_base_cfg, _backup_consumed=False))
    _done = _types.SimpleNamespace(cfg=dict(_base_cfg, _backup_consumed=True))
    _none = _types.SimpleNamespace(cfg=dict(_base_cfg, _backup_dir=""))
    check("① 有未还原的备份 → 空闲态直接说出来",
          gui.Companion._idle_sub(_with), _HINT_SUB)
    check("② 已经还原过 → 不再念叨（回到常规文案）",
          gui.Companion._idle_sub(_done), _DEFAULT_SUB)
    check("③ 没有备份 → 常规文案", gui.Companion._idle_sub(_none), _DEFAULT_SUB)
check("④ 空闲文案只有一处默认值（别在两个地方各写一份，改了一处漏一处）",
      # ★ 先滤掉整行注释：上面那段解释里也引用了这句文案（老坑了，见 [18a]）
      "\n".join(ln for ln in _gui_all.splitlines()
                if not ln.lstrip().startswith("#")).count(_DEFAULT_SUB), 1)
check("⑤ _idle_sub 里读备份信息失败也不许把界面搞崩",
      "except Exception" in _inspect.getsource(gui.Companion._idle_sub), True)

print()
print("[20] ★ 第十一批（体验）：按钮缩放不许跟着「任务栏显/隐」翻面")
# 真事：用户日志里连着 30+ 次 `1.00 -> 0.95 -> 1.00 -> 0.95 …`，按钮一大一小地抖。
# 真因：基准 `SPI_GETWORKAREA` 会随任务栏显/隐整体跳一个任务栏高（实测 864 ↔ 816），
#   而 830/864=0.9606→0.95 与 830/816=1.0172→1.00 恰好分属**相邻量化台阶**。
_src_gui = _gui_all
check("① 有「参考高死区」常量，且必须大于任务栏那次 48px 的跳",
      ("REF_DEADBAND" in _src_gui) and gui.REF_DEADBAND > 48, True)
check("② 有「稳定持续」时间常量（按时间而不是按次数 —— "
      "_float_position 被 33ms 和 500ms 两个循环共用）",
      ("RESCALE_HOLD" in _src_gui) and gui.RESCALE_HOLD >= 0.3, True)
check("③ 钉参考高抽成了纯函数（能离线喂那串 864/816）",
      callable(gui.stable_ref), True)
_locked_probe = None
_locked_trace = []
for _r in [816, 864] * 5:
    _locked_probe = gui.stable_ref(_r, _locked_probe)
    _locked_trace.append(_locked_probe)
check("④ 参考高抖动被吃掉：864/816 交替喂 10 次，锁值纹丝不动",
      set(_locked_trace), {816})
check("⑤ 但真实变化（换屏 816→1020）必须采纳，不能死锁",
      gui.stable_ref(1020, 816), 1020)
check("⑥ 量化换算也抽成纯函数，且夹在 [0.85, 1.25]",
      (gui.rescale_target(10, 816), gui.rescale_target(9999, 816)), (0.85, 1.25))
# ★ 反向核验：如果谁把 stable_ref 从 _maybe_rescale 里摘掉，这条会先红。
_mr = _inspect.getsource(gui.Companion._maybe_rescale)
check("⑦ _maybe_rescale 真的用了 stable_ref（不是自己重算一遍）",
      "stable_ref(" in _mr, True)
check("⑧ _maybe_rescale 真的按 RESCALE_HOLD 卡住了时间",
      "RESCALE_HOLD" in _mr and "time.time()" in _mr, True)
check("⑨ 旧写法「一次判定就立刻改」必须已消失（那是翻面的直接原因）",
      "abs(s - self._scale) >= 0.049" not in _mr, True)
check("⑩ 防抖状态在 __init__ 里初始化了（否则第一帧 AttributeError）",
      all(k in _inspect.getsource(gui.Companion.__init__)
          for k in ("_ref_locked", "_cand_s", "_cand_t0")), True)
check("⑪ 回到原位时候选要作废（别拿旧候选配新时间戳）",
      "_cand_s = None" in _mr, True)

# ---- ⑫★★ 把用户日志**原样重放**：旧逻辑翻 30 次，新逻辑一次都不翻 ----
# 序列 = 部署版 `%LOCALAPPDATA%\JianyingCompanion\运行日志.txt` 第 2~33 行逐条转写
_REPLAY_PAIRS = [
    (816, 864), (830, 816), (830, 864), (878, 864), (830, 864), (878, 816),
    (830, 816), (830, 864), (878, 864), (830, 864), (830, 816), (830, 864),
    (878, 864), (830, 864), (830, 816), (830, 864), (878, 864), (830, 864),
    (830, 816), (830, 864), (830, 816), (830, 864), (830, 816), (830, 864),
    (830, 816), (878, 816), (830, 816), (878, 816), (830, 816), (830, 864),
    (830, 816), (830, 864), (878, 864)]


def _replay(guard, hold=True, dt=0.10):
    """按 `_maybe_rescale` 的编排重放：返回 (缩放改动次数, 最终缩放, 轨迹)。

    `guard=hold=False` = 旧逻辑（没有死区、也没有观察期）。
    """
    locked_ref, scale = None, 1.0
    cand, t0, now, changes, seq = None, 0.0, 0.0, 0, []
    for jh, ref in _REPLAY_PAIRS:
        r = gui.stable_ref(ref, locked_ref) if guard else ref
        locked_ref = r
        act, s = gui.rescale_verdict(jh, r, scale, now, cand, t0,
                                     guard=guard, hold=hold)
        if act == "apply":
            scale, changes = s, changes + 1
            cand = None
        elif act == "wait":
            if cand != s:
                t0 = now
            cand = s
        else:
            cand = None
        seq.append(scale)
        now += dt
    traj = [seq[0]]
    for v in seq[1:]:
        if v != traj[-1]:
            traj.append(v)
    return changes, scale, traj


_old_ch, _old_sc, _old_traj = _replay(guard=False, hold=False)
_new_ch, _new_sc, _new_traj = _replay(guard=True, hold=True)
print(f"      旧逻辑：改动 {_old_ch} 次，轨迹 {_old_traj}")
print(f"      新逻辑：改动 {_new_ch} 次，轨迹 {_new_traj}")
check("⑫ 旧逻辑重放这段日志**确实**会反复翻面（≥20 次）—— 证明这段日志真抓到了问题",
      _old_ch >= 20, True)
check("⑬ 新逻辑重放同一段日志：缩放**一次都不改**（那 48px 抖动就是噪声）",
      _new_ch, 0)
check("⑭ 新逻辑最终只停留在一个缩放上，不会一大一小",
      len(_new_traj), 1)
check("⑮ 新逻辑最终值仍在合法档位里",
      _new_sc in (0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.25), True)

# ---- ⑯ 反向对照：真去拖小窗口必须照改（别把护栏做成"永远不动"）----
def _replay_drag(dt=0.10, settle=15):
    """用户把剪映从 830 一路拖到 500，然后松手停住。"""
    locked_ref, scale = None, 1.0
    cand, t0, now, changes = None, 0.0, 0.0, 0
    seq = list(range(830, 500, -30)) + [500] * settle
    for jh in seq:
        r = gui.stable_ref(816, locked_ref)
        locked_ref = r
        act, s = gui.rescale_verdict(jh, r, scale, now, cand, t0)
        if act == "apply":
            scale, changes = s, changes + 1
            cand = None
        elif act == "wait":
            if cand != s:
                t0 = now
            cand = s
        else:
            cand = None
        now += dt
    return changes, scale


_drag_ch, _drag_sc = _replay_drag()
print(f"      拖动对照：改动 {_drag_ch} 次，落到 {_drag_sc}")
check("⑯ 真去拖小剪映（830→500 后松手）：档位死区不会把真实变化吃掉",
      _drag_sc, 0.85)
check("⑰ 而且拖动过程中不许每帧都改（改 1 次就够，别抖）",
      _drag_ch <= 2, True)

# ---- ⑱ 死区本身的两侧行为 ----
check("⑱ 噪声幅度（±0.058）必须落在死区内 → 不动",
      gui.scale_deviates(1.0172, 1.0) or gui.scale_deviates(0.9606, 1.0), False)
check("⑲ 真跨过一个整档（1.0 → 0.90）必须能动",
      gui.scale_deviates(0.90, 1.0), True)
check("⑳ 死区必须**大于**噪声幅度，否则又会翻面",
      gui.SCALE_STEP + gui.RESCALE_SLACK > 0.058, True)

# ========================================================================
# [21] 2026-09-19 第十二批（悬浮球）：形态可从"横条"变形成"圆球"
#   用户原话：「做成悬浮球样式，不然会挡住用户操作，自动依附右下角，加点流畅样式」。
#   这一节守的是**三条容易悄悄退化的契约**：
#     ① 停靠角在两种模式（独立 / 嵌入）下都必须真的生效；
#     ② 形态只在"该展开"时展开 —— 尤其是「流程卡在等用户」的状态必须自己弹出来；
#     ③ 渲染只有一个出口，且"鼠标离开"不许当场收（否则展开↔收起自激抖动）。
# ========================================================================
print("[21] ★ 第十二批（悬浮球）：形变 / 展开判定 / 停靠角 / 流畅动效")

# ---- ① 停靠角（纯函数）：四角 + top_gap 只对上角生效 ----
_RECT = (0, 0, 1920, 1080)
check("①-1 右上：贴右 + 留出顶部工具栏（top_gap=92）",
      gui.corner_xy("tr", _RECT, 46, 46, 14, 92), (1860, 92))
check("①-2 右下：贴右 + 贴底（只留 margin，不吃 top_gap）",
      gui.corner_xy("br", _RECT, 46, 46, 14, 92), (1860, 1020))
check("①-3 左上：贴左 + 让开顶部",
      gui.corner_xy("tl", _RECT, 46, 46, 14, 92), (14, 92))
check("①-4 左下：贴左 + 贴底",
      gui.corner_xy("bl", _RECT, 46, 46, 14, 92), (14, 1020))
check("①-5 展开成横条时是**向左长出去**（球那一端不动）",
      gui.corner_xy("tr", _RECT, 232, 46, 14, 92)[0]
      < gui.corner_xy("tr", _RECT, 46, 46, 14, 92)[0], True)
check("①-6 展开后右边缘仍然对齐（不会把球推出屏幕）",
      gui.corner_xy("tr", _RECT, 232, 46, 14, 92)[0] + 232,
      gui.corner_xy("tr", _RECT, 46, 46, 14, 92)[0] + 46)
check("①-7 左上角同理：展开向右长，左边缘不动",
      gui.corner_xy("tl", _RECT, 232, 46, 14, 92)[0],
      gui.corner_xy("tl", _RECT, 46, 46, 14, 92)[0])
check("①-8 多显示器负坐标照样正确（rect 左上角就是负的）",
      gui.corner_xy("br", (-1920, 0, 0, 1080), 46, 46, 14, 92), (-60, 1020))
_src_fp = _inspect.getsource(gui.Companion._float_position)
check("①-9 ★ 独立模式也必须认停靠角（回归：这一段以前把位置写死成右上，"
      "菜单在默认模式下完全没用）",
      "corner_xy(" in _src_fp, True)
check("①-10 嵌入模式也走同一个 corner_xy（两边不许各算一套）",
      "corner_xy(" in _inspect.getsource(gui.Companion._reposition), True)
_src_cc = _inspect.getsource(gui.Companion._cycle_corner)
check("①-11 ★ 点「停靠位置」菜单必须**两种模式都重摆**"
      "（回归：以前只调嵌入模式专属的定位 → 独立模式下点了没反应）",
      "_reposition(force=True)" in _src_cc and "_float_position(force=True" in _src_cc, True)

# ---- ② 展开判定（纯函数）：该弹的必须弹，不该弹的别乱弹 ----
check("②-1 空闲 + 没悬停 → 收成球", gui.should_expand("idle", False, False, "auto"), False)
check("②-2 悬停 → 展开（球的存在意义就是「想看详情时能看」）",
      gui.should_expand("idle", False, True, "auto"), True)
check("②-3 等你导出（await_restore）→ 必须自己弹出来", gui.should_expand("ok", True, False, "auto"), True)
check("②-4 出错 → 必须自己弹出来", gui.should_expand("err", False, False, "auto"), True)
check("②-5 要用户操作（ask）→ 必须自己弹出来", gui.should_expand("ask", False, False, "auto"), True)
check("②-6 只是「在干活」（busy）→ 收成球，别一直挡着",
      gui.should_expand("busy", False, False, "auto"), False)
check("②-7 always_pill = 逃生口：回到旧观感（恒展开）",
      gui.should_expand("busy", False, False, "always_pill"), True)
check("②-8 ball_only = 用户明确要求只留球（连「等你导出」也收）",
      gui.should_expand("err", True, False, "ball_only"), False)
check("②-9 ball_only 下悬停仍能读到全文案",
      gui.should_expand("err", True, True, "ball_only"), True)
check("②-10 容错：ball_mode 给个乱值不许把它判成「永不展开」",
      gui.should_expand("err", False, False, "乱值"), True)
check("②-11 三种模式常量与标签齐全（菜单要拿它显示）",
      (len(gui.BALL_MODES), [m for m in gui.BALL_MODES if m in gui.BALL_MODE_LABEL]),
      (3, list(gui.BALL_MODES)))

# ---- ③ 球里那个字 ----
check("③-1 idle=剪 ok=✓ err=×",
      (gui.ball_label("idle", None), gui.ball_label("ok", None), gui.ball_label("err", None)),
      ("剪", "✓", "×"))
check("③-2 busy 显示百分比数字（进度不用展开也看得见）", gui.ball_label("busy", 87), "87")
check("③-3 越界百分比被夹到 0~100",
      (gui.ball_label("busy", 250), gui.ball_label("busy", -9)), ("100", "0"))
check("③-4 pct 不是数字 → 退回省略号（不抛异常）",
      gui.ball_label("busy", None), gui.BALL_TEXT["busy"])
check("③-5 球里最多 3 个字符（球直径才 46px，塞不下词）",
      all(len(gui.ball_label(k, 100)) <= 3 for k in gui.BALL_TEXT), True)

# ---- ④ 形变数学：收起必须是**正圆** ----
check("④-1 收起：宽==高、圆角==半高 ⇒ 正圆",
      gui.morph_shape(False, 232.0, 46.0, 14.0), (46.0, 23.0))
check("④-2 展开：横条宽 + 小圆角", gui.morph_shape(True, 232.0, 46.0, 14.0), (232.0, 14.0))
check("④-3 横条比球还窄时取球径兜底（不许出负宽/零宽）",
      gui.morph_shape(True, 10.0, 46.0, 14.0)[0], 46.0)
check("④-4 形变两端都是合法尺寸（不会算出 0 宽）",
      all(gui.morph_shape(e, 232.0, 46.0, 14.0)[0] >= 46.0 for e in (True, False)), True)
_src_rd = _inspect.getsource(gui.Companion._redraw)
check("④-5 ★ 圆角被夹到不超过半宽/半高（回归：球态圆角 23 > 实际半宽 22，"
      "顶点序列里会出现反向控制点，圆球看着是歪的）",
      "(W - 2) // 2" in _src_rd and "(H - 2) // 2" in _src_rd, True)

# ---- ⑤ 缓动 / 颜色插值（"加点流畅样式"的实际实现）----
check("⑤-1 缓动端点：e(0)=0 e(1)=1", (gui.ease_out_cubic(0), gui.ease_out_cubic(1)), (0.0, 1.0))
check("⑤-2 越界输入被夹住（不许返回负数或大于 1）",
      (gui.ease_out_cubic(-5), gui.ease_out_cubic(9)), (0.0, 1.0))
check("⑤-3 缓出：前半程就走完大半（0.5 处已到 87.5%）",
      abs(gui.ease_out_cubic(0.5) - 0.875) < 1e-9, True)
check("⑤-4 单调不减（否则动画会来回抖）",
      all(gui.ease_out_cubic(i / 50.0) <= gui.ease_out_cubic((i + 1) / 50.0) for i in range(50)),
      True)
check("⑤-5 颜色插值端点：t=0 取 a、t=1 取 b",
      (gui.mix_hex("#112233", "#445566", 0.0), gui.mix_hex("#112233", "#445566", 1.0)),
      ("#112233", "#445566"))
check("⑤-6 颜色插值中点在两者之间（淡入淡出靠它，tk 没有透明度）",
      gui.hex_to_rgb(gui.mix_hex("#000000", "#ffffff", 0.5))[0] in (127, 128), True)
check("⑤-7 颜色解析容错：3 位缩写 / 乱值都不抛",
      (gui.hex_to_rgb("#fff"), gui.hex_to_rgb("不是颜色")), ((255, 255, 255), (0, 0, 0)))
check("⑤-8 动画帧间隔 ≤ 20ms（≈50fps 以上才叫流畅）",
      gui.ANIM_FRAME_MS <= 20, True)
check("⑤-9 形变时长在 [0.1, 0.4]s（再快没质感、再慢嫌拖）",
      0.1 <= gui.ANIM_SHAPE_SECS <= 0.4, True)

# ---- ⑥ 渲染出口唯一 + 事件绑定（结构守卫，防悄悄退化）----
_src_paint = _inspect.getsource(gui.Companion._paint)
check("⑥-1 ★ `_paint` 不再自己画字（一切渲染走 `_redraw` 一个出口，"
      "否则动画中间帧会跟它互相打架）",
      "itemconfigure(" not in _src_paint, True)
check("⑥-2 `_paint` 确实把渲染交给了 `_redraw`", "_redraw()" in _src_paint, True)
check("⑥-3 `_paint` 确实先定形态（球/横条）再画", "_sync_expanded(" in _src_paint, True)
check("⑥-4 `_redraw` 是全模块唯一更新窗口尺寸的地方（宽度/圆角/淡入都在它里面收口）",
      sum("_apply_window_size" in _inspect.getsource(f)
          for f in (gui.Companion._redraw, gui.Companion._build_ui, gui.Companion._resize_to,
                    gui.Companion._paint, gui.Companion._paint_bar)) >= 1, True)
check("⑥-5 `_redraw` 里对底图只改坐标、不重建元素（每帧重建会闪 + 打乱 z 序）",
      "coords(self.bg_item" in _src_rd and "create_polygon" not in _src_rd, True)
check("⑥-6 形态补间是**分通道**的（否则每秒一次的进度更新会把形变动画反复掐断）",
      "def _anim_begin(self, chan" in _inspect.getsource(gui.Companion._anim_begin), True)
check("⑥-7 分通道后仍只有一个 tick 循环（不许每个通道各排一个 after）",
      _inspect.getsource(gui.Companion).count("after(ANIM_FRAME_MS"), 1)

# ---- ⑦ 悬停/离开：不许自激抖动 ----
_src_leave = _inspect.getsource(gui.Companion._on_leave)
check("⑦-1 ★ 鼠标离开**不当场收**（展开时窗口变宽，tk 会补一个假 <Leave> → "
      "当场收起就成「展开↔收起」自激）",
      "_sync_expanded" not in _src_leave, True)
check("⑦-2 但必须记下时间戳（交给 _pump 延迟判定）", "_leave_at = time.time()" in _src_leave, True)
_src_enter = _inspect.getsource(gui.Companion._on_enter)
check("⑦-3 鼠标回来必须把计时清零（否则过了保持期会莫名收起）",
      "_leave_at = 0.0" in _src_enter, True)
check("⑦-4 悬停要展开 + 底色变亮一起走", "_sync_expanded(" in _src_enter, True)
_src_pump = _inspect.getsource(gui.Companion._pump)
check("⑦-5 `_pump` 里真的做了延迟收起", "BALL_LEAVE_MS" in _src_pump, True)
check("⑦-6 延迟判定同时看「时间到」和「确实没悬停」两个条件（缺一个就会误收）",
      "_leave_at" in _src_pump and "self._hover" in _src_pump, True)
check("⑦-7 保持期必须 ≥ 一帧的若干倍（太小挡不住抖动，太大人觉得「粘手」）",
      120 <= gui.BALL_LEAVE_MS <= 600, True)

# ---- ⑧ 宽度语义：横条宽度 ≠ 窗口宽度 ----
_src_rt = _inspect.getsource(gui.Companion._resize_to)
check("⑧-1 ★ `_resize_to` 只改「横条该多宽」，不再直接改窗口宽度"
      "（否则收起态的球会被文案拉成椭的）",
      "_pill_w = int(w)" in _src_rt and "self.W = " not in _src_rt.replace("self.W == ", ""), True)
check("⑧-2 `_fit_width` 的滞回要拿 `_pill_w` 比，不能拿窗口宽度比"
      "（收起时窗宽只有球径，每次都会被判成「必须涨」）",
      "_pill_w" in _inspect.getsource(gui.Companion._fit_width), True)

# ---- ⑨ 菜单必须真的挂上去（回归：写了函数没 _add，用户找不到入口）----
check("⑨-1 形态策略有对应菜单项（函数写了没接进菜单 = 用户找不到）",
      'ballmode' in _inspect.getsource(gui.Companion.__init__), True)
check("⑨-2 菜单标签会跟着配置刷新", "_mi[\"ballmode\"]" in _inspect.getsource(gui.Companion._sync_menu), True)
check("⑨-3 形态状态在 __init__ 里就建好了（否则 _build_ui 第一帧 AttributeError）",
      all(f"self.{k} =" in _inspect.getsource(gui.Companion.__init__)
          for k in ("_hover", "_expanded", "_leave_at", "_pill_w", "_ball_mode",
                    "_anim_after", "_ui_ready", "_pushed_size", "_dot_color")), True)
check("⑨-4 进出事件绑的是方法，不是一句 lambda（悬停要触发展开，不只是改底色）",
      "self._on_enter" in _inspect.getsource(gui.Companion.__init__)
      and "self._on_leave" in _inspect.getsource(gui.Companion.__init__), True)
check("⑨-5 重建画布期间禁止播动画（尺寸要一次到位，否则会看到「边建边动」）",
      "_ui_ready = False" in _inspect.getsource(gui.Companion._build_ui), True)

# ---- ⑩ 默认停靠角 = 右下（用户点名），且四角全通 ----
_dc = core.default_config()
check("⑩-1 core.default_config() 默认停靠角 = 右下（用户原话「自动依附右下角」）",
      _dc.get("corner"), "br")
check("⑩-2 默认形态策略 = 自动收纳",
      _dc.get("ball_mode"), "auto")
check("⑩-3 贴边留白也在默认配置里（右上角要让开剪映顶部工具栏）",
      (_dc.get("corner_margin"), _dc.get("top_gap")), (14, 92))
check("⑩-4 GUI 读不到配置时的兜底也是右下（别又回落成旧的右上）",
      '"br"' in _inspect.getsource(gui.Companion.__init__), True)
_src_cyc = _inspect.getsource(gui.Companion._cycle_corner)
check("⑩-5 停靠角乱值（配置被手改坏）会被纠正成合法角",
      'if self.corner not in order' in _src_cyc, True)
check("⑩-6 循环覆盖四个角，一个都不漏",
      all(f'"{c}"' in _src_cyc for c in ("tr", "br", "tl", "bl")), True)
check("⑩-7 循环要真的写回配置（否则重启就变回去）",
      'self.cfg["corner"] = self.corner' in _src_cyc
      and "save_config" in _src_cyc, True)
check("⑩-8 「球贴窗口哪一端」由停靠角决定：右侧角 → 贴右（展开向左长）",
      'in ("tr", "br")' in _inspect.getsource(gui.Companion._ball_at_right), True)
check("⑩-9 形态策略循环要真的写回配置",
      'self.cfg["ball_mode"] = self._ball_mode' in _inspect.getsource(gui.Companion._cycle_ball_mode)
      and "save_config" in _inspect.getsource(gui.Companion._cycle_ball_mode), True)

# ---- ⑪ 形变淡入淡出：球与文字**永不同时可见** ----
check("⑪-1 v=0（纯球）→ 球全亮、横条全暗", gui.morph_alphas(0.0), (1.0, 0.0))
check("⑪-2 v=1（纯横条）→ 球走干净、横条全亮", gui.morph_alphas(1.0), (0.0, 1.0))
check("⑪-3 ★ 任意中间帧都不许「球和文字同时可见」"
      "（球贴窗口右端、展开是向左长，同时可见就是半个字叠在球上）",
      any(b > 0 and p > 0 for b, p in
          (gui.morph_alphas(i / 100.0) for i in range(101))), False)
check("⑪-4 但也不许出现长时间「两个都看不见」的空档（只允许 v=0.45 那一瞬）",
      sum(1 for i in range(101) if gui.morph_alphas(i / 100.0) == (0.0, 0.0)), 1)
_al = [gui.morph_alphas(i / 100.0) for i in range(101)]
check("⑪-5 球的不透明度单调不增、文字的单调不减（淡入淡出不许回闪）",
      all(_al[i][0] >= _al[i + 1][0] and _al[i][1] <= _al[i + 1][1] for i in range(100)), True)
check("⑪-6 球在 45% 处已经走干净（先出）", gui.morph_alphas(0.45)[0], 0.0)
check("⑪-7 文字 45% 之前一次都不露面（后进）", gui.morph_alphas(0.44)[1], 0.0)
check("⑪-8 越界 v 被夹住（不许算出负透明度）",
      (gui.morph_alphas(-1), gui.morph_alphas(9)), ((1.0, 0.0), (0.0, 1.0)))

# ---- ⑫ 圆球外圈那道环的分档（目视验收抓出来的：全黄一圈看不见进度）----
check("⑫-1 busy 时环是**轨道色**（否则整圈黄，进度弧叠在上面根本看不出来）",
      gui.ring_style("busy"), (True, 1.0))
check("⑫-2 idle 时环弱化成轮廓（全亮比球本身还抢眼，像加载圈）",
      gui.ring_style("idle"), (False, 0.45))
check("⑫-3 结果态（ok/err/ask）环用状态色且全亮（一眼看到成没成）",
      [gui.ring_style(k) for k in ("ok", "err", "ask")], [(False, 1.0)] * 3)
check("⑫-4 容错：未知状态也有明确取值（不许返回 None 把渲染带崩）",
      gui.ring_style("乱值")[0] in (True, False), True)

# ---- ⑬ 圆角轮廓：必须是**密集采样的折线**，不能靠 smooth 样条 ----
#   ★ 事故（截图实测）：第一版用 create_polygon(smooth=True) + 8 个控制点，
#     球态 r = 半个宽，画出来是个**圆角方块**（smooth 只把四个直角切掉一小块），
#     根本不是悬浮球该有的样子。
_pts_ball = gui.round_pts(1, 1, 45, 45, 22)
_px, _py = _pts_ball[0::2], _pts_ball[1::2]
check("⑬-1 点数足够密（球态才不会画成圆角方块）", len(_px) >= 40, True)
check("⑬-2 点数**恒定**（动效每帧 coords 更新同一个多边形，点数忽多忽少是隐患）",
      len(_px) == len(gui.round_pts(1, 1, 233, 45, 14)) // 2, True)
check("⑬-3 球态（r = 半宽）轮廓真的落在一个圆上：每点到圆心距离 ≈ 半径",
      max(abs(math.hypot(x - 23.0, y - 23.0) - 22.0) for x, y in zip(_px, _py)) < 0.6, True)
check("⑬-4 球态轮廓上没有任何一点掉在「圆角方块」的直边上（x/y 都远小于半径的点）",
      min(math.hypot(x - 23.0, y - 23.0) for x, y in zip(_px, _py)) > 21.0, True)
check("⑬-5 画底图时**不再**用 smooth 样条（它就是方块的真凶）",
      "smooth" not in _inspect.getsource(gui.round_rect), True)
check("⑬-6 圆角被夹在不超过半宽/半高（超出会让轮廓自交、长角）",
      all(abs(x - 23.0) <= 22.01 and abs(y - 23.0) <= 22.01
          for x, y in zip(*[iter(gui.round_pts(1, 1, 45, 45, 999))] * 2)), True)
check("⑬-7 极小的 r 也不抛异常、点数仍然是正的",
      len(gui.round_pts(1, 1, 45, 45, 0)) > 0, True)
check("⑬-8 展开态轮廓仍然占满整个窗口（没被圆角「吃掉」尺寸）",
      (max(gui.round_pts(1, 1, 233, 45, 14)[0::2])
       - min(gui.round_pts(1, 1, 233, 45, 14)[0::2]),
       max(gui.round_pts(1, 1, 233, 45, 14)[1::2])
       - min(gui.round_pts(1, 1, 233, 45, 14)[1::2])), (232.0, 44.0))

# ---- ⑭ 落到屏幕外/任务栏上：必须夹回工作区 ----
#   ★ 真机实测（第十二批）：剪映最大化时窗口矩形 `(-9,-9)-(1929,1029)` 越过屏幕边界，
#     按"右下角 − 尺寸 − margin"算出来的球会压到任务栏上。
_WA = (0, 0, 1920, 1020)
check("⑭-1 工作区内部的位置原样不动",
      gui.clamp_box(1000, 500, 46, 46, _WA), (1000, 500))
check("⑭-2 ★ 越过右下角 → 推回工作区内（不许压任务栏）",
      gui.clamp_box(1869, 969 + 60, 46, 46, _WA), (1869, 974))
check("⑭-3 越过右上 → 推到右边缘",
      gui.clamp_box(9999, 10, 46, 46, _WA), (1874, 10))
check("⑭-4 越过左下 → 推到左/下边缘",
      gui.clamp_box(-500, 9999, 46, 46, _WA), (0, 974))
check("⑭-5 多显示器负坐标也能用（工作区可以是负的）",
      gui.clamp_box(-1900, 500, 46, 46, (-1920, 0, 0, 1032)), (-1900, 500))
check("⑭-6 窗口比工作区还大时贴左上，不返回负坐标（兜底不许算到屏幕外）",
      gui.clamp_box(500, 500, 3000, 3000, _WA), (0, 0))
check("⑭-7 浮点坐标会被取整（tk geometry 不接受小数）",
      gui.clamp_box(1000.7, 500.2, 46, 46, _WA), (1001, 500))
check("⑭-8 独立模式真的调了它（不能只是写了个没用的纯函数）",
      "clamp_box(" in _inspect.getsource(gui.Companion._float_position), True)
check("⑭-9 基准取的是**剪映所在那块屏**的工作区（不是整个虚拟桌面）",
      "monitor_work_area" in _inspect.getsource(gui.Companion._float_position), True)

# ================================================================ 第十三批
print()
print("[22] ★ 第十三批：文件夹不再重复弹窗 + 提示分两层（用户：弹了两次 / 提示不够）")

# ---- 22a. 「弹了两次」的真凶：explorer /select **每次都新开一个窗口** ----
#   实测（`_probe_folder_once.py`）：同一个文件连开 3 次 → 屏幕上并排 3 个一样的文件夹；
#   上一轮留下的窗口还在、这一轮又开一个 = 用户看到的"弹了两次"。
_sel_src = _inspect.getsource(core.select_in_folder)
check("① 有官方那条路（shell32.SHOpenFolderAndSelectItems，Explorer 自己就用它）",
      "SHOpenFolderAndSelectItems" in _sel_src, True)
check("② ★ 释放不混用：folder 用 shell32.ILFree，pidl 用 ole32.CoTaskMemFree（写错会崩/泄漏）",
      "ole32.CoTaskMemFree(pidl)" in _sel_src and "_shell32.ILFree(folder)" in _sel_src, True)
check("③ ★ 失败必须如实返回 False（好让上层走兜底，别静默什么都不做）",
      "return False" in _sel_src and "return True" in _sel_src, True)
_ofs_src = _inspect.getsource(core.open_folder_and_select)
check("④ ★ 主路径走官方 API、`explorer /select` 只当兜底（顺序不能反）",
      _ofs_src.index("select_in_folder(path)") < _ofs_src.index("subprocess.Popen"), True)
check("⑤ ★ 兜底有闸门：API 成功就不许再起 explorer（否则照样弹两个）",
      "if not select_in_folder(path):" in _ofs_src, True)
check("⑥ ★ 认窗口时先按标题认「该在的那个文件夹」（官方 API 复用窗口，压根没有新 hwnd）",
      "named" in _ofs_src and "key in t" in _ofs_src, True)

# ---- 22b. 顺手收掉"历史遗留的重复窗口"（只动伴侣自己开的那些） ----
_dedup_src = _inspect.getsource(core._close_dup_win_impl)
check("⑦ 去重只碰「标题含该文件夹名」的窗口",
      "key not in title" in _dedup_src, True)
check("⑧ ★ 只关**伴侣自己规整过**的尺寸（1000x620±30）；用户自己拉的/最大化的一律不动",
      "FOLDER_WIN_W" in _dedup_src and "FOLDER_WIN_H" in _dedup_src
      and "> 30" in _dedup_src, True)
check("⑨ ★ 尺寸比较必须在 _dpi_aware() 里做（实测 125% 缩放会读成 800x496，否则永不命中）",
      "_dpi_aware()" in _inspect.getsource(core._close_duplicate_folder_windows), True)
# 真跑一遍逻辑：假窗口表 → 只有"同名 + 同尺寸 + 非 keep"的那个该被关
_orig_ew, _orig_wr, _orig_cw = (core.explorer_windows, core.window_rect, core.close_window)
try:
    _closed = []
    core.explorer_windows = lambda: [(1, "dist"), (2, "dist"), (3, "下载"), (4, "dist")]
    core.window_rect = lambda h: {1: (0, 0, 1000, 620), 2: (0, 0, 1000, 620),
                                  3: (0, 0, 1000, 620), 4: (0, 0, 1600, 900)}[h]
    core.close_window = lambda h: _closed.append(h)
    core._close_duplicate_folder_windows("dist", keep=1)
finally:
    core.explorer_windows, core.window_rect, core.close_window = _orig_ew, _orig_wr, _orig_cw
check("⑩ 去重行为：同名同尺寸的多余窗口关掉；不同名 / 最大化的窗口一个都不许碰",
      _closed, [2])

# ---- 22c. 提示分两层：主文案=怎么做，副文案=为什么 ----
_got = []
core._call_status(lambda t, k, p=None, s=None, sub=None: _got.append((t, k, p, s, sub)),
                  "请双击草稿卡片", "busy", 58, 5, "必须这份")
check("⑪ ★ 新回调收得到第 5 个参数 sub（内核能把第二行提示送到界面上）",
      _got, [("请双击草稿卡片", "busy", 58, 5, "必须这份")])
_got2 = []
core._call_status(lambda t, k: _got2.append((t, k)), "旧回调", "err", 1, 2, "sub")
check("⑫ ★ 只收 2 个参数的老回调仍能收到（逐级降级，不许把这条状态丢掉）",
      _got2, [("旧回调", "err")])
_got3 = []


def _boom(t, k, p=None, s=None, sub=None):
    _got3.append(1)
    raise TypeError("回调自己的 bug")


core._call_status(_boom, "x", "busy")
check("⑬ 回调**自己**抛的 TypeError 不许被当成签名不符而重投（同一条状态会投两遍）",
      len(_got3) == 1, len(_got3))

check("⑭ GUI 的状态回调收得下 sub",
      "sub" in _inspect.getsource(gui.Companion._on_status), True)
_pump_src = _inspect.getsource(gui.Companion._pump)
check("⑮ ★ 副文案优先用内核给的（给了就别盖成「第 N/步 · %」）",
      "sub = hint or None" in _pump_src, True)
check("⑯ ★ 队列里 5 元组 / 6 元组都要能吃（老调用方还在投 5 元组）",
      "item[:5]" in _pump_src and "len(item) > 5" in _pump_src, True)
check("⑰ ★ 第二行文字真有元素承载（sub → _paint 存进 _sub → _redraw 画到 sub_item）",
      "_sub = sub" in _inspect.getsource(gui.Companion._paint)
      and "sub_item" in _inspect.getsource(gui.Companion._redraw), True)

# ---- 22d. 文案内容：要能回答"在哪打开 / 怎么打开 / 为什么是这份" ----
check("⑱ ★ 「打开草稿」提示说清了**怎么开**（双击卡片），不再是光秃秃一句「请打开草稿」",
      "请双击草稿" in _eep_src and "卡片" in _eep_src, True)
check("⑲ ★ 副文案说明了**为什么必须是这份**（换草稿会报「媒体格式不支持」）",
      "媒体格式不支持" in _eep_src, True)
check("⑳ 流程一开始就告诉用户要停在草稿编辑页（别在首页按）",
      "需要你停在草稿编辑页（在首页就双击草稿卡片进去）" in _inspect.getsource(core.run_pipeline), True)
check("㉑ 认错草稿时也给了「回首页双击哪张」（不是只说一句不匹配）",
      "那张卡片再点我" in _inspect.getsource(core.run_pipeline), True)
check("㉒ 内核 st() 有 sub 通道且真的透传给 _call_status",
      "def st(t, k=\"busy\", pct=None, sub=None)" in _inspect.getsource(core.run_pipeline)
      and "sub)" in _inspect.getsource(core.run_pipeline), True)

# ---- 22e. 收尾提示走的是 Win32 MessageBox，**不认 markdown** ----
#   ★ 实测：`**已经停下了…**` 会连着星号画在用户面前（看着像乱码）。
check("㉓ 剥星号是纯函数且真的剥掉了",
      core.strip_md_emphasis("**已经停下了，没有动你的时间线。**"),
      "已经停下了，没有动你的时间线。")
check("㉔ ★ notify_box 弹之前必须过一遍（否则用户看到的是带星号的乱码）",
      "strip_md_emphasis(msg)" in _inspect.getsource(core.notify_box), True)
check("㉕ 空串/None 不炸（有些提示没有正文）",
      (core.strip_md_emphasis(""), core.strip_md_emphasis(None)), ("", None))

print("[23] ★ 第十四批（用户\"继续优化\"）：事件循环存活 + 剪映没开时不用点两次 + 手动路径")
_gui_all = _inspect.getsource(gui)
_pump_src = _inspect.getsource(gui.Companion._pump)
_click_src = _inspect.getsource(gui.Companion._click)

# ---- 23a. 自重复事件循环：**异常不许打穿**，重注册必须在 finally ----
#   ★ 事故形态：重注册写在函数最后一行，队列里一条坏消息 → 异常穿出 → 整条链死掉，
#     用户看到的是"点了没反应"的死球，而日志上什么都看不出来。
check("① _pump 兜住了**泛**异常（不是只兜 queue.Empty）",
      "except Exception as _e" in _pump_src, True)
check("② 重注册放在 finally 里（无论中间怎么炸，120ms 后一定会再排自己）",
      # ★ 用 rindex：`except` 分支的注释里**提到过**那行代码（反面教材），
      #   拿 index 会命中注释 → 假红（这个坑本项目踩过，见 TECH_NOTES）。
      "finally:" in _pump_src
      and _pump_src.rindex("self.root.after(120, self._pump)")
      > _pump_src.rindex("finally:"), True)
check("③ 出事不静默（打一行进日志，别让人瞎猜）",
      "[ui] 处理事件失败" in _pump_src, True)
#   ★ 反向对照：其它几个自重复循环也必须是"先重排、后干活"这个形状
for _fn in ("_watch", "_follow", "_follow_fast"):
    _s = _inspect.getsource(getattr(gui.Companion, _fn))
    check(f"④ {_fn} 也一定会重新排自己（不能有裸 return 早退）",
          "self.root.after(" in _s, True)

# ---- 23b. 剪映没开时点「一键导出」→ 自动接上流程（原来要点两次）----
_lt_src = _inspect.getsource(gui.Companion._launch_then_run)
_lj_src = _inspect.getsource(gui.Companion._launch_jy)
check("⑤ 点导出时剪映没开 → 走 then_run（启动完**自己接着跑**）",
      "self._launch_jy(then_run=True)" in _click_src, True)
check("⑥ ★ 等待/启动/重试全在工作线程（主线程一卡就是\"点了没反应\"）",
      "threading.Thread(target=self._launch_then_run" in _lj_src, True)
check("⑦ ★ _click 里**只做瞬时查找**，绝不在主线程 sleep 等（原来 wait_jianying(1.2)）",
      "core.find_jianying(exclude=self.my_hwnd)" in _click_src
      and "wait_jianying(1.2" not in _click_src, True)
check("⑧ ★ 先探测、没有再启动（用户可能刚点过桌面图标，重复 Popen = 两个剪映抢前台）",
      _lt_src.index("wait_jianying(LAUNCH_PROBE_SECS") < _lt_src.index("launch_jianying("), True)
check("⑨ 启动后等主窗，超时不当成功、明确让用户手动开",
      "wait_jianying(LAUNCH_WAIT_SECS" in _lt_src and "手动打开" in _lt_src, True)
check("⑩ 常量齐全且探测窗口 < 启动等待",
      (gui.LAUNCH_PROBE_SECS, gui.LAUNCH_WAIT_SECS) == (2.0, 60.0)
      and gui.LAUNCH_PROBE_SECS < gui.LAUNCH_WAIT_SECS, True)
check("⑪ ★ rescue='launch' 有**专门分支**（不能落到「已就位」或笼统「未完成」）",
      'rescue == "launch"' in _pump_src and "剪映没起来" in _pump_src, True)
check("⑫ 菜单「启动剪映」仍然是**只启动**（不是单一动作就别偷偷改语义）",
      "起来后点我「一键导出」" in _lj_src, True)

# ---- 23c. 分发痛点第 1 条「路径假设」的兜底：用户自己指路 ----
_fj_src2 = _inspect.getsource(core.find_jianying_exe)
check("⑬ find_jianying_exe 接受 cfg，且手动路径是**最高优先级**（排在问进程之前）",
      "def find_jianying_exe(cfg=None)" in _fj_src2
      and _fj_src2.index('cfg.get("jianying_exe")') < _fj_src2.index("_exe_path_of_process"), True)
check("⑭ 手动路径失效时自动回落（文件不在了也不会卡死在错路径）",
      "is_file()" in _fj_src2.split('cfg.get("jianying_exe")')[1][:160], True)
check("⑮ 启动/体检都吃 cfg（否则手动路径设了也白设）",
      "def launch_jianying(cfg=None)" in _inspect.getsource(core.launch_jianying)
      and "find_jianying_exe(cfg)" in _inspect.getsource(core.env_selfcheck), True)
check("⑯ 配置有 jianying_exe 这一项（默认空 = 自动找）",
      core.default_config().get("jianying_exe"), "")
check("⑰ ★ 菜单真有「设置剪映路径…」入口并接线（文案指向它就必须能找到）",
      '_add("jypath"' in _gui_all and "self._pick_jy_exe)" in _gui_all, True)
check("⑱ 失败文案指向的入口和菜单里的**名字一致**",
      all("设置剪映路径" in s for s in
          (_inspect.getsource(gui.Companion._launch_then_run),
           _inspect.getsource(gui.Companion._launch_jy))), True)
check("⑲ 体检里找不到剪映时也给出这条出路",
      "设置剪映路径" in _inspect.getsource(core.env_selfcheck), True)
check("⑳ 选错文件要拦一句（用户可能随手点了个别的 exe）",
      "剪映的常见主程序名" in _inspect.getsource(gui.Companion._pick_jy_exe), True)
check("㉑ 取消对话框**不清空**已有设置，但要给一条改回去的路",
      "改回自动查找" in _inspect.getsource(gui.Companion._pick_jy_exe), True)
check("㉒ 菜单上会报出当前手动路径（设错了要看得见）",
      '_mi["jypath"]' in _inspect.getsource(gui.Companion._sync_menu), True)

print("[24] ★ 第十五批（用户\"继续优化\"）：草稿目录也能手动指 + 体检不许假绿")
import tempfile  # noqa: E402

# ---- 24a. 「设置草稿目录…」入口（和「设置剪映路径…」是同一个坑的两半）----
check("① ★ 菜单真有「设置草稿目录…」入口并接线（文案指向它就必须能找到）",
      '_add("draftroot"' in _gui_all and "self._pick_draft_root)" in _gui_all, True)
check("② 菜单上会报出当前用的是哪份草稿目录（默认位置就写「默认位置」）",
      '_mi["draftroot"]' in _inspect.getsource(gui.Companion._sync_menu)
      and "默认位置" in _inspect.getsource(gui.Companion._sync_menu), True)
_pdr_src = _inspect.getsource(gui.Companion._pick_draft_root)
check("③ ★ 手滑选了上一级（…\\Projects）要自动往下走一层到 com.lveditor.draft",
      '"com.lveditor.draft"' in _pdr_src and "is_dir()" in _pdr_src, True)
check("④ 认不出预合成产物时问一句（\"存在\"≠\"是对的\"）",
      "draft_root_has_combos" in _pdr_src and "ask_yes(" in _pdr_src, True)
check("⑤ 取消对话框能给一条改回自动查找的路",
      "改回自动查找" in _pdr_src, True)
check("⑥ 设置完立刻落盘（否则下次开机又找不到）",
      "save_config" in _pdr_src, True)

# ---- 24b. ★★ 失败文案不许再让用户"手改配置文件"（求助回路闭环）----
#   旧文案原话：「或把路径填进 伴侣配置.json 的 draft_root」—— 让用户改 JSON = 没有出路。
#   ★ 一律先滤掉**整行注释**再查：这里的新注释本身就写着那句反面教材，
#     不滤就会把自己写的说明当成"还在犯的错"（本项目踩过多次，见 [17f]）。
def _live_src(fn):
    return "\n".join(ln for ln in _inspect.getsource(fn).splitlines()
                     if not ln.lstrip().startswith("#"))


_need_no_json = {
    "run_pipeline": _live_src(core.run_pipeline),
    "extract_latest": _live_src(core.extract_latest),
    "env_selfcheck": _live_src(core.env_selfcheck),
}
check("⑦ ★ 三处「找不到草稿目录」的文案都不再提 伴侣配置.json",
      [k for k, v in _need_no_json.items() if "伴侣配置.json" in v], [])
check("⑧ ★ 而且都指向了菜单里的那个入口（文案↔入口闭环）",
      [k for k, v in _need_no_json.items() if "设置草稿目录" not in v], [])

# ---- 24c. draft_root_has_combos：有界、只认名字、不慢扫 ----
_has_src = _inspect.getsource(core.draft_root_has_combos)
check("⑨ 限时 + 限目录数（别人的草稿根可能很大，不能拖住流程）",
      "budget" in _has_src and "max_dirs" in _has_src
      and "time.time() - t0 > budget" in _has_src and "n > max_dirs" in _has_src, True)
check("⑩ 不用 rglob 全递归（要能随时放弃）", "rglob" not in _has_src, True)

_tmp = Path(tempfile.mkdtemp(prefix="draftroot_"))
_empty = _tmp / "空目录"
_good = _tmp / "真草稿根"
(_empty).mkdir(parents=True)
(_good / "草稿A" / "Resources" / "combination").mkdir(parents=True)
(_good / "草稿A" / "Resources" / "combination" / "X_video.mp4").write_bytes(b"x")
check("⑪ 真装着预合成产物 → True", core.draft_root_has_combos(_good), True)
check("⑫ 空的 / 不存在的 → False（不许把「存在」当「是对的」）",
      (core.draft_root_has_combos(_empty), core.draft_root_has_combos(_tmp / "没有这个")),
      (False, False))

# ---- 24d. draft_root_status：三态，且"指错了"不许报 ok ----
check("⑬ 有内容 → ok",
      core.draft_root_status({"draft_root": str(_good)})[0], "ok")
_st = core.draft_root_status({"draft_root": str(_empty)})
check("⑭ ★ 目录在但没内容 → warn（这是「指错了」最典型的样子，以前会报 ok）",
      _st[0], "warn")
check("⑮ warn 的正文告诉用户去哪儿改（不是只说一句\"不对\"）",
      "设置草稿目录" in _st[2] and "combination" in _st[2], True)
check("⑯ 记的路径已经不在了 → warn（会重新自动找）",
      core.draft_root_status({"draft_root": str(_tmp / "早没了")})[0], "warn")

# ---- 24e. resolve_root：手动优先 / 失效回落 / 别被空目录钉死 ----
_rr_src2 = _inspect.getsource(core.resolve_root)
check("⑰ 缓存的根排在最前（手动设的就在这个键里），先看它再看常见落点",
      _rr_src2.index('cfg.get("draft_root")') < _rr_src2.index("_pick_standard_root"), True)
check("⑱ ★ 缓存根里没内容而别的候选有内容 → 改用它并记日志（旧代码会被永久钉死）",
      "没有预合成产物，改用有内容的" in _rr_src2, True)
check("⑲ ★ 父目录不参与\"优先\"竞争（根指到上一级会让 open_draft_name 认不出草稿）",
      "_is_parent_root" in _rr_src2, True)
#   行为级：把候选表换成临时目录，看它到底选谁
#   ★ 第十六批：这里必须把「问剪映自己」那条**关掉**（stub 成 None）——
#     否则会真读到本机剪映设置里的草稿位置，测的就不是"常见落点怎么挑"了。
_osdr, _osave2 = core.standard_draft_roots, core.save_config
_ojy2 = core.jianying_setting_draft_path
_saved2 = []
core.standard_draft_roots = lambda: [_empty, _good]
core.save_config = lambda cfg: _saved2.append(dict(cfg))
core.jianying_setting_draft_path = lambda: None
try:
    _cfg2 = {"draft_root": str(_empty)}          # 缓存指到"存在但空"的目录
    _got2 = core.resolve_root(_cfg2)
    check("⑳ ★ 行为：被钉死的空目录能自愈（换到真有草稿的那个）", _got2, _good)
    check("㉑ 自愈后写回配置（下次启动直接用对的）", _cfg2.get("draft_root"), str(_good))
    _cfg3 = {"draft_root": str(_good)}           # 缓存本来就是对的
    check("㉒ 反向对照：缓存是对的就不动它", core.resolve_root(_cfg3), _good)
    _cfg4 = {"draft_root": str(_tmp / "早没了")}  # 缓存失效
    check("㉓ 反向对照：缓存失效 → 回落到自动查找，且**优先挑有内容的那个**",
          core.resolve_root(_cfg4), _good)
    #   再对照一次"自动查找里一个都没内容"的极端：这时退回"第一个存在的"
    #   （保持老行为：不能因为都没做过预合成就判它找不到草稿目录）
    _noscan_bak = core.draft_root_has_combos
    core.draft_root_has_combos = lambda *a, **k: False
    try:
        _cfg5 = {"draft_root": ""}
        check("㉓b 反向对照：候选全都没内容 → 仍然是第一个存在的（不是 None）",
              core.resolve_root(_cfg5), _empty)
    finally:
        core.draft_root_has_combos = _noscan_bak
    #   ★★ 第十六批新增：**剪映设置里明说了一个位置** → 它优先于"常见落点"，
    #      哪怕那个位置里还没有预合成产物（用户挪过草稿位置时靠这条自愈）。
    core.jianying_setting_draft_path = lambda: _good
    _cfg6 = {"draft_root": str(_empty)}
    check("㉓c ★ 剪映设置里那个位置说了算（比常见落点优先级高）",
          core.resolve_root(_cfg6), _good)
finally:
    (core.standard_draft_roots, core.save_config,
     core.jianying_setting_draft_path) = _osdr, _osave2, _ojy2
#   ★ 父目录防护要能被单独的纯函数验出来（否则上面那条只是「字符串在源码里」）
check("㉔ ★ _is_parent_root：把 …\\Projects 认成父目录（因为它里面还有 com.lveditor.draft）",
      callable(getattr(core, "_is_parent_root", None))
      and core._is_parent_root(core.standard_draft_roots()[1]), True)
check("㉕ 反向对照：真正的草稿根不是父目录",
      core._is_parent_root(core.standard_draft_roots()[0]), False)

# ---- 24f. 候选表只有一份（两份实现一定会慢慢跑偏）----
check("㉖ ★ 10 条盘根候选只写在 standard_draft_roots 里，resolve_root 不再内联",
      'Path(f"{drv}:/")' in _inspect.getsource(core.standard_draft_roots)
      and 'Path(f"{drv}:/")' not in _rr_src2, True)
check("㉗ 体检也不再自己拼候选（统一走 draft_root_status）",
      "resolve_root" not in _inspect.getsource(core.env_selfcheck), True)

print("[25] ★ 第十五批：markdown 星号不许漏进非 markdown 渲染器（对话框 / 体检报告）")
#   背景：文案用 `**加粗**` 写是为了在日志里好读，但真正显示给用户的出口
#   （tk 的 messagebox、体检报告 showinfo）都不认 markdown，会把星号原样画出来。
#   修法不是逐句删星号（新文案还会再犯），而是给出**唯二出口** + 源码级禁令。
check("① 三个出口函数都在（md_plain / ask_yes / show_info）",
      all(f"def {n}(" in _gui_all for n in ("md_plain", "ask_yes", "show_info")), True)

import tkinter.messagebox as _tmbx  # noqa: E402
_got_dlg = {}


def _fake_dlg(title, msg, **kw):
    _got_dlg["t"], _got_dlg["m"] = title, msg
    return True


_oay, _osi = _tmbx.askyesno, _tmbx.showinfo
_tmbx.askyesno, _tmbx.showinfo = _fake_dlg, _fake_dlg
try:
    gui.ask_yes("标题**甲**", "正文**乙**")
    _seen_ask = (_got_dlg["t"], _got_dlg["m"])
    gui.show_info("标题**丙**", "正文**丁**")
    _seen_info = (_got_dlg["t"], _got_dlg["m"])
finally:
    _tmbx.askyesno, _tmbx.showinfo = _oay, _osi
check("② ★ ask_yes 真的把星号剥掉再交给 tk", _seen_ask, ("标题甲", "正文乙"))
check("③ show_info 同样剥（体检报告走它）", _seen_info, ("标题丙", "正文丁"))
check("④ md_plain 对空/None 安全（老规矩）",
      (gui.md_plain(""), gui.md_plain(None)), ("", None))

#   源码级禁令：不许再有人绕过出口直接调 messagebox（过滤注释行 —— 本批的注释里
#   正好写着 `_tkmb.*` 这个反面教材，不滤就会假红）。
_live_gui = "\n".join(ln for ln in _gui_all.splitlines()
                      if not ln.lstrip().startswith("#"))
check("⑤ ★ GUI 里不再有直接的 messagebox 调用（askyesno/showinfo/... 全走出口）",
      [ln.strip()[:70] for ln in _live_gui.splitlines()
       if "messagebox." in ln or "_tkmb." in ln], [])
_doctor = core.format_selfcheck(core.env_selfcheck({"draft_root": ""}, quick=True))
check("⑥ ★ 体检报告（format_selfcheck 输出）里没有 **",
      [ln.strip()[:70] for ln in _doctor.splitlines() if "**" in ln], [])
check("⑦ notify_box 仍剥星号（第十三批那条不许被带坏）",
      "strip_md_emphasis(msg)" in _inspect.getsource(core.notify_box), True)

print()
print("[26] ★ 第十六批（用户\"安装时自动帮用户搞好，不让用户多余操作\"）")
import json  # noqa: E402

# ---- 26a. 内核：问剪映自己 + 自动配置 ----
for _n in ("autodetect_env", "jianying_setting_draft_path", "format_autosetup",
           "write_autosetup_result", "_save_autosetup_keys", "_jianying_user_data"):
    check(f"① core 有 {_n}()", callable(getattr(core, _n, None)), True)
check("② 草稿位置读的是剪映设置里那个键（currentCustomDraftPath）",
      core.JY_DRAFT_KEY, "currentCustomDraftPath")

# ---- 26b. ★★ 红线：读剪映的设置文件**只读**，绝不写它一个字 ----
_jy_src = _inspect.getsource(core.jianying_setting_draft_path)
check("③ ★ 读剪映设置只用 read_bytes（它是剪映的文件，我们不许写）",
      "read_bytes" in _jy_src and "read_text" not in _jy_src, True)
check("④ ★ 里面没有任何写操作",
      [k for k in ("write_bytes", "write_text", "open(") if k in _jy_src], [])
check("⑤ 剪映的设置文件路径是 User Data/Config/globalSetting（本机实测）",
      "User Data" in _inspect.getsource(core._jianying_user_data)
      and '"Config"' in _jy_src and '"globalSetting"' in _jy_src, True)
check("⑥ 文件里路径是双反斜杠字面量 → 要还原成单反斜杠",
      '.replace("\\\\\\\\", "\\\\")' in _jy_src, True)

# ---- 26c. ★★ 自动配置只写"机器事实"，**绝不把内置默认值抄成文件** ----
#   这条是第九批那条老规矩（[Files] 段"故意不写 伴侣配置.json"）的落地守卫：
#   autodetect_env() 手里的 cfg 是 load_config() 的产物（默认值打底），
#   整份 save_config() 就等于把今天的默认值钉死在用户机器上。
check("⑦ ★ 落盘白名单只有 4 个键（剪映路径 / 草稿目录 / 两个记账位）",
      list(core.AUTOSETUP_KEYS),
      ["jianying_exe", "draft_root", "_autosetup_done", "_autosetup_at"])
check("⑧ ★ 自动配置走的是白名单合并，不是 save_config",
      "_save_autosetup_keys(cfg)" in _inspect.getsource(core.autodetect_env)
      and "save_config(cfg)" not in _inspect.getsource(core.autodetect_env), True)

_t26 = Path(tempfile.mkdtemp(prefix="autosetup26_"))
_cf26 = _t26 / "伴侣配置.json"
_keep = (core.config_path, core.find_jianying_exe, core.jianying_setting_draft_path,
         core._pick_standard_root)
_dr26 = _t26 / "draftroot"
_dr26.mkdir(parents=True)
core.config_path = lambda: _cf26
core.find_jianying_exe = lambda cfg=None: Path("C:/Fake/JianyingPro.exe")
core.jianying_setting_draft_path = lambda: _dr26
core._pick_standard_root = lambda *a, **k: None
try:
    _rep26 = core.autodetect_env()
    _data26 = json.loads(_cf26.read_text(encoding="utf-8"))
finally:
    (core.config_path, core.find_jianying_exe, core.jianying_setting_draft_path,
     core._pick_standard_root) = _keep
check("⑨ ★ 新机器上落盘的键**正好**是白名单那 4 个（没混进任何默认值）",
      sorted(_data26.keys()), sorted(core.AUTOSETUP_KEYS))
check("⑩ 侦测到的剪映路径 / 草稿目录真的写进去了",
      (_data26.get("jianying_exe"), _data26.get("draft_root")),
      ("C:\\Fake\\JianyingPro.exe", str(_dr26)))
check("⑪ 报告里说清了「改了什么」（安装包/GUI 要照着念）",
      _rep26["changed"], ["剪映路径", "草稿目录"])
check("⑫ 草稿位置来路如实标注（读剪映设置 = 最准的那条）",
      _rep26["draft_how"], "剪映设置里记着的位置")

# ---- 26d. 手动指定优先：活着的不动，死了的让自动值接手 ----
_man26 = _t26 / "我自己装的.exe"
_man26.write_text("x", encoding="utf-8")
_cf26.write_text(json.dumps({"jianying_exe": str(_man26)}, ensure_ascii=False),
                 encoding="utf-8")
core.config_path = lambda: _cf26
core.find_jianying_exe = lambda cfg=None: Path("C:/Fake/JianyingPro.exe")
core.jianying_setting_draft_path = lambda: _dr26
core._pick_standard_root = lambda *a, **k: None
try:
    _rep26b = core.autodetect_env()
    _data26b = json.loads(_cf26.read_text(encoding="utf-8"))
finally:
    (core.config_path, core.find_jianying_exe, core.jianying_setting_draft_path,
     core._pick_standard_root) = _keep
check("⑬ ★ 用户手动指过、文件还在 → 一个字都不改（不抢用户的决定）",
      (_data26b.get("jianying_exe"), _rep26b["jianying_how"]),
      (str(_man26), "沿用你手动指定的"))

_cf26.write_text(json.dumps({"jianying_exe": str(_t26 / "早就删了.exe")},
                            ensure_ascii=False), encoding="utf-8")
core.config_path = lambda: _cf26
core.find_jianying_exe = lambda cfg=None: Path("C:/Fake/JianyingPro.exe")
core.jianying_setting_draft_path = lambda: _dr26
core._pick_standard_root = lambda *a, **k: None
try:
    core.autodetect_env()
    _data26c = json.loads(_cf26.read_text(encoding="utf-8"))
finally:
    (core.config_path, core.find_jianying_exe, core.jianying_setting_draft_path,
     core._pick_standard_root) = _keep
check("⑭ ★ 手动指的已经不在 → 清掉并让自动值接手（死路径不许永久挡路）",
      _data26c.get("jianying_exe"), "C:\\Fake\\JianyingPro.exe")

# ---- 26e. 什么都认不到：不许凭空造配置文件、也不许说"没问题" ----
_t26b = Path(tempfile.mkdtemp(prefix="autosetup26b_"))
_cf26b = _t26b / "伴侣配置.json"
core.config_path = lambda: _cf26b
core.find_jianying_exe = lambda cfg=None: None
core.jianying_setting_draft_path = lambda: None
core._pick_standard_root = lambda *a, **k: None
try:
    _rep26d = core.autodetect_env()
    _txt26d = core.format_autosetup(_rep26d)
finally:
    (core.config_path, core.find_jianying_exe, core.jianying_setting_draft_path,
     core._pick_standard_root) = _keep
check("⑮ ★ 一个都没认到时**不创建**配置文件（免得「配置存在」这个判断从此失真）",
      _cf26b.exists(), False)
check("⑯ 也不许说「配置本来就是对的」（那是说谎）",
      "配置本来就是对的" in _txt26d, False)

# ---- 26f. 给安装包读的结果文件：ASCII 标记打头 + 中文用系统 ANSI ----
_res26 = _t26 / core.AUTOSETUP_RESULT
core.config_path = lambda: _cf26
core.find_jianying_exe = lambda cfg=None: Path("C:/Fake/JianyingPro.exe")
core.jianying_setting_draft_path = lambda: _dr26
try:
    _p26 = core.write_autosetup_result({"jianying_exe": "C:/Fake/JianyingPro.exe",
                                        "draft_root": str(_dr26),
                                        "jianying_how": "自动", "draft_how": "自动",
                                        "changed": ["剪映路径"]})
    _raw26 = _p26.read_bytes()
finally:
    (core.config_path, core.find_jianying_exe, core.jianying_setting_draft_path,
     core._pick_standard_root) = _keep
check("⑰ ★ 第一行是纯 ASCII 标记（Inno 用 Pos 匹配，不怕编码）",
      _raw26.split(b"\r\n", 1)[0], b"JIANYING=1")
check("⑱ ★ 用 CRLF，且标记和正文之间有空行（Inno 靠它切开）",
      b"\r\n\r\n" in _raw26, True)
check("⑲ ★★ 正文按**系统 ANSI** 写（Inno 只认 AnsiString，按 ANSI 解字节；"
      "若写成 UTF-8，这里按 mbcs 解就会是乱码）",
      "剪映：已找到" in _raw26.decode("mbcs", "replace"), True)
check("⑳ 正文里没有 markdown 星号（安装包弹的是原生 MsgBox）",
      [ln for ln in core.format_autosetup({"jianying_exe": "x", "draft_root": "y",
                                           "changed": []}).splitlines()
       if "**" in ln], [])

# ---- 26f2. ★ 幂等：第二次跑没有变化 → 不再写盘 ----
#   体检每次点都会走一遍自动配置；如果"没变化也写"，配置文件的修改时间就一直在跳，
#   而且 `_autosetup_at` 会变成"最后一次点击时间"，本来是想让它表示"最后配成功的时间"。
_t26c = Path(tempfile.mkdtemp(prefix="autosetup26c_"))
_cf26c = _t26c / "伴侣配置.json"
_fake_exe = _t26c / "FakeJianyingPro.exe"
_fake_exe.write_text("x", encoding="utf-8")
core.config_path = lambda: _cf26c
core.find_jianying_exe = lambda cfg=None: _fake_exe
core.jianying_setting_draft_path = lambda: _dr26
core._pick_standard_root = lambda *a, **k: None
try:
    _r1_26 = core.autodetect_env()
    _m1_26 = _cf26c.stat().st_mtime_ns
    _r2_26 = core.autodetect_env()
    _same_26 = _cf26c.stat().st_mtime_ns == _m1_26
finally:
    (core.config_path, core.find_jianying_exe, core.jianying_setting_draft_path,
     core._pick_standard_root) = _keep
check("㊴ ★ 幂等：第二次跑没变化 → 不再写盘（反复体检不会一直改配置）",
      (_r1_26["saved"], _r2_26["saved"], _r2_26["changed"], _same_26),
      (True, False, [], True))
check("㊵ ★ 第二次认到的就是「我们自己刚写进去的那个」→ 报「沿用你手动指定的」"
      "（不会和自己较劲、更不会跟用户较劲）",
      _r2_26["jianying_how"], "沿用你手动指定的")

# ---- 26g. GUI：`--autosetup` 入口 + 首跑/体检前先自动配 ----
_auto_cli_src = _inspect.getsource(gui.autosetup_cli)
#   ★ 只在 `__main__` 块里比顺序：`def ensure_single_instance():` 那行本身
#     就含 `ensure_single_instance()` 这个子串，拿整个文件 index 会假红。
_gui_main = _gui_all[_gui_all.index('if __name__ == "__main__":'):]
check("㉑ GUI 有 autosetup_cli（安装包装完调它）", callable(gui.autosetup_cli), True)
check("㉒ ★ 它只认环境 + 写配置 + 落结果文件（不开窗、不弹框）",
      [k for k in ("Tk()", "mainloop", "notify_box", "ask_yes", "Companion(")
       if k in _auto_cli_src], [])
check("㉓ ★ 结果文件与内核同一个名字（防两边改名跑偏）",
      core.AUTOSETUP_RESULT, "_自动配置结果.txt")
check("㉔ ★★ `--autosetup` 必须排在**防多开之前**"
      "（升级安装时旧版可能在跑，防多开会让它直接退出、什么都配不了）",
      _gui_main.index('"--autosetup" in sys.argv')
      < _gui_main.index("ensure_single_instance()"), True)
_work_src = _inspect.getsource(gui.Companion._selfcheck)
check("㉕ ★★ 体检**之前**先自动配一遍（绿色版/没走安装包的机器也能零操作）",
      "core.autodetect_env(self.cfg)" in _work_src
      and _work_src.index("core.autodetect_env(self.cfg)") < _work_src.index("env_selfcheck"),
      True)

# ---- 26h. 体检报告里要有「自动配置：已经替你配好了」这条 ----
_esc_src = _inspect.getsource(core.env_selfcheck)
check("㉖ ★ 体检报告有「自动配置」这一条（让用户看见「他不用做任何事」）",
      "自动配置：已经替你配好了" in _esc_src and "_autosetup_done" in _esc_src, True)
_rows26 = core.env_selfcheck({"draft_root": str(_dr26), "jianying_exe": "C:/x.exe",
                              "_autosetup_done": True}, quick=True)
check("㉗ ★ 配齐了两项 → 报「已经替你配好了」（不再让用户去点菜单）",
      bool([r for r in _rows26 if r[0] == "ok" and "自动配置" in r[1]]), True)
_rows26b = core.env_selfcheck({"draft_root": "", "_autosetup_done": True}, quick=True)
check("㉘ ★ 没配齐 → 如实报 warn（不许假绿）",
      bool([r for r in _rows26b if r[0] == "warn" and "自动配置" in r[1]]), True)

# ---- 26i. 草稿根自愈时先问剪映自己 ----
_rr_src = _inspect.getsource(core.resolve_root)
check("㉙ ★ 缓存里的草稿根不对时，优先采信剪映设置里那个（用户挪过位置能自愈）",
      "jianying_setting_draft_path()" in _rr_src and "authoritative" in _rr_src, True)
check("㉚ 体检的草稿目录文案也如实说了「会去读剪映设置」（文案别和实现打架）",
      "也会去读剪映设置" in _inspect.getsource(core.draft_root_status), True)

# ---- 26j. 安装包：装完自己跑 --autosetup 并把结果念给用户 ----
_iss_all = (HERE / "剪映伴侣安装包.iss").read_text(encoding="utf-8")
check("㉛ ★ 安装包装完调用 --autosetup（这是「不让用户多余操作」的落点）",
      "'--autosetup'" in _iss_all, True)
check("㉜ ★ 静默跑、且等它结束（SW_HIDE + ewWaitUntilTerminated）",
      "SW_HIDE" in _iss_all and "ewWaitUntilTerminated" in _iss_all, True)
check("㉝ ★★ 读结果文件的变量必须是 AnsiString（传 String 会编译期 Type mismatch）",
      "A: AnsiString;" in _iss_all, True)
check("㉞ ★ 结果文件名和内核一致（两边改名就跑偏）",
      ("'" + core.AUTOSETUP_RESULT + "'") in _iss_all, True)
check("㉟ 认到了就先报喜（把结果直接念给用户）",
      "已经替你配好了，装完就能直接用" in _iss_all, True)
check("㊱ 一个都没认到才提示装剪映（**不阻断安装**，没有 Abort/ExitProcess）",
      ("没有在这台电脑上找到「剪映专业版」" in _iss_all
       and "Abort" not in _iss_all), True)
check("㊲ ★ 旧注释「安装包不要去代劳（草稿目录）」必须改掉（文档不能和实现打架）",
      "草稿目录 / 桌面导出目录 → 第一次跑的时候自动找" in _iss_all, False)
check("㊳ ★ 结果文件在卸载时也清掉（不留垃圾）",
      '_自动配置结果.txt"' in _iss_all, True)

print()
print("[27] ★ 第十七批（用户\"用户可以暂终止，终止时自动还原\"）：中止令牌 + 自动还原")

# ---- 27a. 原语：中止令牌的判定必须"失败开放" ----
check("① 有专门的 PipelineCancelled（中止 ≠ 故障，收尾规矩完全不同）",
      issubclass(core.PipelineCancelled, Exception), True)


class _BrokenProbe:
    def is_set(self):
        raise RuntimeError("探针坏了")


_ev_unset = threading.Event()
_ev_set = threading.Event()
_ev_set.set()
check("② cancel=None → 不算中止（老调用点行为一个字节都不变）",
      core.is_cancelled(None), False)
check("③ Event 没置位 → 不算中止", core.is_cancelled(_ev_unset), False)
check("④ Event 置位 → 是中止", core.is_cancelled(_ev_set), True)
check("⑤ 无参 callable 也吃（测试里最省事的写法）",
      core.is_cancelled(lambda: True), True)
check("⑥ ★ 探针自己坏了 → 当作**没**中止（宁可多跑一步，也不误杀用户流程）",
      core.is_cancelled(_BrokenProbe()), False)

# ---- 27b. 形状：凡是"长等待"，都得能吃一个 cancel ----
def _takes_cancel(fn):
    try:
        return "cancel" in _inspect.signature(fn).parameters
    except Exception:
        return False


check("⑦ run_pipeline 收 cancel", _takes_cancel(core.run_pipeline), True)
for _nm17 in ("wait_jianying", "wait_new_combo", "wait_file_settled",
              "_wait_foreground", "_wait_jy_ready_for_keys",
              "commit_draft_registration", "ensure_edit_page",
              "_restart_and_enter", "_clear_timeline"):
    check(f"⑦ {_nm17} 也收 cancel（长等待要能立刻收手，不能让用户干等满 timeout）",
          _takes_cancel(getattr(core, _nm17)), True)

_rp17 = _inspect.getsource(core.run_pipeline)
check("⑧ ★★ 中止分支必须排在 `except Exception` **之前**"
      "（顺序反了就会被当故障吞掉 → 还原根本不做）",
      0 <= _rp17.find("except PipelineCancelled") < _rp17.find("except Exception as e"),
      True)
check("⑨ ★★★ 中止时走 restore_draft —— 这就是用户要的「终止时自动还原」",
      "restore_draft(cfg, status_cb=status_cb" in _rp17, True)
check("⑩ ★★★ 还原与否只看**本轮**台账（touched / bkp_ok）——"
      "绝不查 cfg 里的旧指针（那会拿上一轮的旧备份盖掉用户现在的工程）",
      ('if not _run["touched"]:' in _rp17 and 'if not _run["bkp_ok"]:' in _rp17), True)
check("⑪ ★★ 中止必须以 rescue=\"abort\" 报回界面"
      "（否则界面把「中止」当成「跑完了，等你导出」→ 最糟的误报）",
      '"abort")' in _rp17, True)
check("⑫ ★ 检查点 ck() 只在动作**之间**，不许在动作中途丢下现场",
      "def ck():" in _rp17 and "_cancelled(cancel)" in _rp17, True)

# ---- 27c. GUI：入口、置灰、以及"中止"和"等你导出"必须分开 ----
_gui17 = (HERE / "剪映伴侣.py").read_text(encoding="utf-8")
check("⑬ GUI 有 _start_pipeline（起流程时挂令牌）",
      "def _start_pipeline(self):" in _gui17, True)
check("⑭ GUI 有 _abort_pipeline", "def _abort_pipeline(self):" in _gui17, True)
check("⑮ ★★★ 运行中左键点球 = 中止（原来这里直接 return，跑起来就成了死键）",
      "if self._cancel is not None:" in _gui17, True)
check("⑯ ★ 菜单里有中止入口", '"abort", "中止本次流程（自动还原）"' in _gui17, True)
check("⑰ ★ 中止入口按可用性置灰（不做「点了没反应」的死入口）",
      'state=("normal" if _can_abort else "disabled")' in _gui17, True)
check("⑱ ★★ 界面把 abort 与「等你导出」分开（两者都是 ok=True）",
      'elif rescue == "abort":' in _gui17, True)
check("⑲ ★ 一轮任务结束就把令牌撤掉（留着会让下一轮刚起步就中止）",
      "self._cancel = None" in _gui17, True)
check("⑳ ★ 等剪映启动那 60s 也能中止（令牌在启动前就挂上）",
      "cancel=self._cancel" in _gui17, True)

# ---- 27d. 行为：真跑一遍内核，看它有没有**真的**去还原 ----
def _run_abort_case(bkp_ok=True, preset=False):
    """跑一遍真 `run_pipeline`（环境的副作用全部打桩），返回观察结果。

    三种情形：① 一开局就中止；② 预合成按键**刚发出去**就中止（最危险的时刻）；
    ③ 同②，但这一轮没做出备份。
    """
    _root = Path(tempfile.mkdtemp(prefix="abort17_"))
    (_root / "草稿A").mkdir(parents=True, exist_ok=True)
    _ev = threading.Event()
    if preset:
        _ev.set()
    _seen = {"restore": False, "keys": 0, "killed": 0, "finished": []}
    _keep = {k: getattr(core, k) for k in (
        "wait_jianying", "_wait_foreground", "ensure_edit_page",
        "ensure_precomp_hotkey", "resolve_root", "open_draft_name",
        "backup_draft_json", "_send_keys_sequence", "wait_new_combo",
        "restore_draft", "kill_jianying")}
    try:
        core.wait_jianying = lambda *a, **k: (1234, "剪映专业版")
        core._wait_foreground = lambda *a, **k: True
        core.ensure_edit_page = lambda *a, **k: True
        core.ensure_precomp_hotkey = lambda cfg: ("Alt+H", False, True)
        core.resolve_root = lambda cfg, status_cb=None: _root
        core.open_draft_name = lambda r, since=0: "草稿A"
        core.backup_draft_json = lambda *a, **k: (None if not bkp_ok else Path("X:/b"))
        core.wait_new_combo = lambda *a, **k: None      # 等渲染：立刻返回"没等到"
        core.kill_jianying = lambda: _seen.__setitem__("killed", _seen["killed"] + 1)

        def _send(cfg, hwnd, st):
            _seen["keys"] += 1
            _ev.set()        # ★ 按键**已经发出去了**，用户偏偏在这一刻喊停
            return True

        core._send_keys_sequence = _send

        def _restore(cfg, status_cb=None, finished_cb=None):
            _seen["restore"] = True
            if finished_cb:
                finished_cb(True, "（测试替身）已还原")

        core.restore_draft = _restore
        core.run_pipeline(
            {}, status_cb=lambda *a, **k: None,
            finished_cb=lambda ok, msg, rescue=False: _seen["finished"].append((ok, rescue)),
            cancel=_ev)
    finally:
        for k, v in _keep.items():
            setattr(core, k, v)
    return _seen


_a17 = _run_abort_case(preset=True)
check("㉑ ★★ 连预合成都还没发就中止 → 报中止、**不去碰草稿**（没坏的东西别修）",
      (_a17["finished"] == [(True, "abort")] and _a17["restore"] is False
       and _a17["keys"] == 0), True)

_b17 = _run_abort_case(bkp_ok=True)
check("㉒ ★★★ 已经动过草稿后中止 → **自动还原真的被调用**（这就是用户要的效果）",
      (_b17["finished"] == [(True, "abort")] and _b17["restore"] is True
       and _b17["keys"] == 1), True)

_c17 = _run_abort_case(bkp_ok=False)
check("㉓ ★★ 动过草稿、但本轮没备份 → 如实报「没还原」（绝不假装收拾干净了）",
      (bool(_c17["finished"]) and _c17["finished"][0][0] is False
       and _c17["finished"][0][1] == "abort" and _c17["restore"] is False), True)

print()
print("失败项:", fails if fails else "无")
sys.exit(1 if fails else 0)
