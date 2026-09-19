# -*- coding: utf-8 -*-
"""反向核验：新代码真的进了 exe（走 CArchive -> PYZ.pyz -> ZlibArchive）。

★ 两个 API 坑（踩过，别改回去）：
  ① ZlibArchiveReader 只吃**真实文件路径** —— 先把 arc.extract("PYZ.pyz") 的 bytes
     落到临时文件再用。
  ② zarc.extract("jy_core") 直接返回 **code object**（不是 bytes），
     按 "bytes 再 marshal.loads" 写会拿到空集合 => 假阴性。
"""
import os
import sys
import tempfile

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "dist", "JianyingCompanion.exe")


def all_names(co):
    """递归收集这个 code object（含所有嵌套函数/类）里出现的所有名字。"""
    names = set(co.co_names)
    for c in co.co_consts:
        if hasattr(c, "co_names"):
            names |= all_names(c)
    return names


def load_module(arc, mod):
    """返回 (names, code object)。ZlibArchiveReader 只吃真实文件路径。"""
    data = arc.extract("PYZ.pyz")
    td = tempfile.mkdtemp()
    p = os.path.join(td, "PYZ.pyz")
    with open(p, "wb") as f:
        f.write(data)
    co = ZlibArchiveReader(p).extract(mod)          # ← 直接是 code object
    if co is None:
        return None, None
    return all_names(co), co


def all_consts(co):
    """递归收集 code object 里所有**字符串常量**（含嵌套函数 + 嵌套容器）。

    ★ 第十三批补：文案核验要用**精确常量**，不能用子串 —— 文档字符串/注释里
      提到同一句话就会假绿（这是第十二批踩过的坑，见 TECH_NOTES 二十一）。
    ★★ 第十四批修：原来只递归 **code object**，漏了**容器常量** ——
      `out.append(("bad", "剪映：没找到", "……"))` 这种写法里，
      整个元组会被编译器折成一个**常量元组**，句子就藏在元组元素里，
      于是"文案明明在 exe 里"却报缺失（假红，浪费一轮排查）。
      现在元组 / frozenset / list 里的字符串也一并收进来。
    """
    out = set()

    def _walk(obj):
        if isinstance(obj, str):
            out.add(obj)
        elif hasattr(obj, "co_consts"):
            for x in obj.co_consts:
                _walk(x)
        elif isinstance(obj, (tuple, frozenset, list, set)):
            for x in obj:
                _walk(x)

    _walk(co)
    return out


def find_code(co, name):
    """在嵌套 code object 里按 co_name 找函数。"""
    for c in co.co_consts:
        if hasattr(c, "co_consts"):
            if c.co_name == name:
                return c
            r = find_code(c, name)
            if r is not None:
                return r
    return None


def frozen_default_flag(co, key):
    """★ 真值核验：把 exe 里 `default_config` 的 code object 直接执行，
    取出真实默认值 —— 不看名字，看**值**（名字在，值也可能是旧的 True）。

    可行性：default_config 是纯字面量构 dict（co_names 为空），
    所以用空 globals 造 FunctionType 直接调用即可，不会碰文件系统。
    """
    import types
    dc = find_code(co, "default_config")
    if dc is None:
        return "<找不到 default_config>"
    if dc.co_names or dc.co_freevars:
        # 有外部引用就退化成"名字存在性"检查，避免误执行副作用
        return "<含外部引用，跳过执行>"
    return types.FunctionType(dc, {})().get(key)


def frozen_skip_dirs(co):
    """★ 值核验：把 exe 里 `BACKUP_SKIP_DIRS` 的真实取值捞出来。

    这是「解禁边界」的核心常量——备份/写回必须跳过 resources（产物目录）。
    集合字面量会被编译成 frozenset 常量，所以直接在模块 co_consts 里找。
    """
    for c in co.co_consts:
        if isinstance(c, (frozenset, set)) and "resources" in c:
            return sorted(c)
    return None


def frozen_pipeline_stages(co):
    """★ 值核验：把 exe 里 `PIPELINE_STAGES` 的真值捞出来（进度条的阶段表）。

    它会被编译成模块级常量元组（元素是 3 元组：(名, 起%, 止%)）。
    只看名字在不在没用 —— 阶段表被改坏（漏项/不覆盖到 100%）时，
    进度条会永远停在半路，而"名字存在性"检查照样全绿。
    """
    for c in co.co_consts:
        if (isinstance(c, tuple) and len(c) >= 3
                and all(isinstance(x, tuple) and len(x) == 3 for x in c)):
            return c
    return None


DEPLOYED_CFG = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                            "JianyingCompanion", "伴侣配置.json")


def deployed_overrides():
    """已部署程序真正读的那份配置（可能把默认值**顶掉**）。

    ★★ 这个函数是为了一个真实踩过的坑而存在：
      「改 default_config() 的默认值」**只在配置文件还没有那个键时才生效**。
      把 auto_drag_product 默认从 True 改成 False 后，用户机器上的
      伴侣配置.json 里已经写死了 `"auto_drag_product": true`，
      load_config() 用 update 覆盖默认 → 改动**零生效**，
      而 exe 值核验还全绿（因为 exe 里的默认值确实是对的）。
      ⇒ 只看 exe 里的默认值不够，必须看"最终生效值 = 配置优先，默认兜底"。
    """
    if not os.path.isfile(DEPLOYED_CFG):
        return None
    import json
    try:
        with open(DEPLOYED_CFG, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("!! 读不动已部署配置:", e)
        return None


def main():
    arc = CArchiveReader(EXE)
    print("EXE:", EXE)
    print("CArchive 条目数:", len(arc.toc))

    core_names, core_co = load_module(arc, "jy_core")
    if core_names is None:
        print("!! 取不到 jy_core")
        return 1

    # ★ 值核验：exe 里 auto_open_draft 的默认值必须是 False（用户定案：自己点卡片）
    flag = frozen_default_flag(core_co, "auto_open_draft")
    print("exe 内 default_config()['auto_open_draft'] =", repr(flag), "(期望 False)")
    flag_ok = (flag is False)
    # ★ 值核验：两个开关默认值（2026-09-18 晚）
    #   auto_drag_product 已按用户要求**回退为 False**（"不要自动拖动了，让用户手动拖"）——
    #   名字在 ≠ 值对，这里就是靠"真值"抓住它有没有被打回旧 True。
    drag = frozen_default_flag(core_co, "auto_drag_product")
    tidy = frozen_default_flag(core_co, "folder_win_tidy")
    print("exe 内 default_config()['auto_drag_product'] =", repr(drag), "(期望 False)")
    print("exe 内 default_config()['folder_win_tidy'] =", repr(tidy), "(期望 True)")
    flag_ok = flag_ok and (drag is False) and (tidy is True)

    # ★ 值核验（第十二批 · 悬浮球）：默认停靠角必须是**右下**、形态策略必须是**自动收纳**。
    #   名字在 ≠ 值对 —— 这两个都是"用户明确点名"的默认值，被打回旧值就白做了。
    corner = frozen_default_flag(core_co, "corner")
    bmode = frozen_default_flag(core_co, "ball_mode")
    print("exe 内 default_config()['corner'] =", repr(corner), "(期望 'br' 右下)")
    print("exe 内 default_config()['ball_mode'] =", repr(bmode), "(期望 'auto' 自动收纳)")
    flag_ok = flag_ok and (corner == "br") and (bmode == "auto")

    # ★ 值核验：手动剪映路径默认必须是**空**（空 = 自动找）。
    #   若被写成某个硬编码路径，别人拿到后就会去启动一台不存在的机器上的剪映。
    _jx = frozen_default_flag(core_co, "jianying_exe")
    print("exe 内 default_config()['jianying_exe'] =", repr(_jx), "(期望 '' 即自动找)")
    flag_ok = flag_ok and (_jx == "")

    # ★★ 最终生效值核验：exe 默认值 与 已部署配置 一起看（配置优先）
    cfg = deployed_overrides()
    if cfg is None:
        print("已部署配置: 不存在（该机器上还没装过 / 没跑过）→ 跳过生效值核验")
    else:
        eff_drag = cfg.get("auto_drag_product", drag)
        print(f"已部署配置里的 auto_drag_product = {cfg.get('auto_drag_product', '<无此键>')!r}"
              f"  → 最终生效值 = {eff_drag!r} (期望 False)")
        if eff_drag is not False:
            print("   !! 配置里的旧值把新默认顶掉了 —— 改默认值**没生效**。")
            print(f"      修法：把 {DEPLOYED_CFG} 里的 auto_drag_product 改成 false")
            print("      （或把这个键删掉让它走新默认）。")
        flag_ok = flag_ok and (eff_drag is False)
        # ★ 第十二批：形态/停靠角也要核**最终生效值**（配置文件里写了旧值就会顶掉默认）
        for key, want, hint in (("corner", "br", "右下"),
                                ("ball_mode", "auto", "自动收纳")):
            eff = cfg.get(key, {"corner": corner, "ball_mode": bmode}[key])
            print(f"已部署配置里的 {key} = {cfg.get(key, '<无此键>')!r}"
                  f"  → 最终生效值 = {eff!r} (期望 {want!r} {hint})")
            if eff != want:
                print(f"   !! 配置里的旧值把新默认顶掉了 —— 修法：把 {DEPLOYED_CFG} 里的 "
                      f"{key} 改成 {want!r}（或删掉这个键走新默认）。")
            flag_ok = flag_ok and (eff == want)

    # ★ 值核验：解禁边界的关键常量（必须含 resources，否则可能备份到产物目录）
    skips = frozen_skip_dirs(core_co)
    print("exe 内 BACKUP_SKIP_DIRS =", skips, "(必须含 'resources')")
    flag_ok = flag_ok and bool(skips) and ("resources" in skips)

    # ★ 值核验：进度条阶段表（名字在 ≠ 表是对的）
    _stages = frozen_pipeline_stages(core_co)
    _n = len(_stages) if _stages else None
    print("exe 内 PIPELINE_STAGES 阶段数 =", _n, "(期望 7)")
    if _stages:
        print("   阶段区间:", [(s[0], s[1], s[2]) for s in _stages])
    flag_ok = flag_ok and bool(_stages) and _n == 7 \
        and _stages[0][1] == 0 and _stages[-1][2] == 100
    # ★ 第十批补：阶段表必须**首尾相接**（上一段止% == 下一段起%）。
    #   否则跨阶段时进度条会回缩 —— 光看"首 0 尾 100"抓不住中间断裂。
    if _stages:
        _contig = all(_stages[i][2] == _stages[i + 1][1] for i in range(_n - 1))
        print("   阶段表首尾相接 =", _contig)
        flag_ok = flag_ok and _contig

    must = ["restore_draft", "open_draft_name", "product_guid",
            "registration_committed", "commit_draft_registration",
            "run_pipeline", "_clear_timeline", "desktop_dir", "backup_draft_json",
            "_backup_rel_files", "backup_info", "backup_root_dir",
            "BACKUP_SKIP_DIRS", "_orphan_subdrafts", "_jianying_running",
            # ★ 2026-09-18 晚「清空假成功」事故后新增（挑对按键 + 等空闲 + 像素自检）
            "read_shortcut_all", "_pick_clear_delete_key", "CLEAR_DEL_PREFER",
            "_wait_jy_ready_for_keys", "_jy_is_responding",
            "_clear_strip", "CLEAR_CHECK_Y", "_focus_timeline_by_click",
            # ★ 第十八批（v1.1.0 导出守望）：检测 + 守望 + 尺寸带
            "find_export_dialog", "watch_export_dialog",
            "EXPORT_TITLE_KW", "EXPORT_DLG_MIN", "EXPORT_DLG_MAX",
            # ★ 第十九批（v1.2.0 批量队列）
            "list_batch_candidates", "batch_prepare_draft",
            "_save_draft_via_return",
            # ★ 第二十批（v1.3.0 全自动导出 · 实验）
            "full_auto_export", "FULLAUTO_EXPORT_KEY_DEFAULT",
            # 2026-09-18 第二批：自动打开原始草稿 + 三个实测坑的修复
            "open_draft_by_card", "draft_display_order", "_card_row_slots",
            "_card_at", "dismiss_jy_modals", "is_dismissable_modal",
            "MODAL_TITLES", "_window_size", "wait_jianying", "MAIN_MIN_W",
            "CARD_PITCH_FALLBACK", "_CARD_ROW_YMIN",
            # 2026-09-18 第三批：文件夹窗口规整 + 零依赖 UIA + 自动拖入时间线
            "open_folder_and_select", "tidy_explorer_window", "monitor_work_area",
            "explorer_windows", "FOLDER_WIN_W", "uia_list_items",
            "find_explorer_item", "drag_file_into_jianying", "_timeline_strip",
            "_strip_diff", "DRAG_DROP_X_FRAC", "DRAG_DIFF_MIN",
            # 2026-09-18 第四批（真机验收后）：空带动态检测 + 抬窗 + 播放头归零
            "_window_image", "_strip_of", "empty_lane_y", "_raise_for_drag",
            "_last_row_mad", "_last_row_bright", "_med",
            "DRAG_LANE_SCAN_X", "DRAG_LANE_MAD", "DRAG_HDR_MIN",
            # ★ 2026-09-18 第八/九批（发给别人用 + 进度条）
            #   进度条：把"跑到哪一步了"外化，别人第一次用不是黑盒
            "PIPELINE_STAGES", "PIPELINE_TOTAL_STEPS", "PIPELINE_RENDER_SECS",
            "stage_bounds", "_call_status",
            #   分发安全：改别人的剪映快捷键之前先备份 + 能还原
            "ensure_precomp_hotkey", "write_shortcut", "PRECOMP_KEY_CANDIDATES",
            "_action_using_key", "_norm_key",
            "backup_shortcut_config", "restore_shortcut_config",
            #   分发安全：自定义安装路径也能找到剪映 / 兜底扫盘有界
            "_exe_path_of_process", "_shallow_find", "SCAN_SKIP_DIRS",
            #   分发安全：开机自体检查（别人第一次用先知道自己缺什么）
            "env_selfcheck", "format_selfcheck",
            # ★ 2026-09-19 第十批（潜伏 bug 排查）
            #   进度条换算抽成纯函数（能离线断言"只增不减"）
            "render_pct",
            #   等落盘：分块 md5 + 大小优先短路
            "file_fingerprint", "wait_file_settled",
            #   二次还原护栏（备份用过就打标记，GUI 才会警告）
            "backup_consumed",
            # ★ 2026-09-19 第十三批（用户："弹出文件夹时弹了两次"）
            #   换成官方 API 复用窗口；顺带收掉历史遗留的重复窗口
            "select_in_folder", "_close_duplicate_folder_windows",
            "SHOpenFolderAndSelectItems", "ILFindLastID", "CoTaskMemFree",
            "FOLDER_WIN_H",
            #   收尾提示走 Win32 MessageBox，不认 markdown → 弹之前剥掉星号
            "strip_md_emphasis",
            # ★ 2026-09-19 第十四批（用户"继续优化" · 分发痛点第 1 条"路径假设"）
            #   手动指定剪映 exe（绿色版 / 自定义安装的唯一出路）
            "find_jianying_exe", "launch_jianying",
            # ★ 2026-09-19 第十五批：草稿目录同款（手动指路 + 体检说真话）
            "standard_draft_roots", "_pick_standard_root", "_is_parent_root",
            "draft_root_has_combos", "draft_root_status",
            # ★★ 2026-09-19 第十六批（用户"安装时自动帮用户搞好，不让用户多余操作"）
            #   问剪映自己（读它的设置拿草稿位置）+ 自动配置并落盘白名单
            "_jianying_user_data", "jianying_setting_draft_path",
            "autodetect_env", "_save_autosetup_keys",
            "format_autosetup", "write_autosetup_result",
            # ★★ 2026-09-19 第十七批（用户"用户可以暂终止，终止时自动还原"）
            #   中止原语 + 判定（判定必须"失败开放"：探针坏了当没中止）
            "PipelineCancelled", "is_cancelled"]
    # ★ 红线（2026-09-18 二次定稿）：**只禁"解码/转码/解密/改写产物"这一路**。
    #   草稿元数据 .json 的备份+写回是**用户明确批准的解禁**，不再混进黑名单
    #   （否则 backup_draft_json 这类合法新代码会被误杀）。
    banned = ["deobfuscate_product", "probe_artifact", "validate_mp4", "deliver_product",
              "remux", "find_ffmpeg", "detect_xor_key", "_track_sample_keys",
              "_sample_key_of", "_cut_candidates", "archive_product", "out_dir",
              "_clear_and_place", "restore_original", "undo_restore",
              "restore_from_backup"]

    miss = [n for n in must if n not in core_names]
    hit = [n for n in banned if n in core_names]
    print("必需函数缺失:", miss if miss else "无")
    print("红线函数混入:", hit if hit else "无")

    # ★ 第十三批：内核侧的**文案常量**核验（精确常量，不许子串误命中）。
    #   用户这一轮的原话就是"提示不够"，所以"提示文案真的进了 exe"要当守卫钉住。
    _cconsts = all_consts(core_co)
    _need_core_txt = [
        # ★★ 第十六批：自动配置的**记账/结论文案**（安装包和体检都照着它说）
        #   —— 精确常量，少一条就 FAIL（改文案必须同步这里）。
        "currentCustomDraftPath",              # 问剪映自己的那个键
        "_自动配置结果.txt",                    # 给安装包读的结果文件
        "JIANYING=", "DRAFT=",                 # 安装包按 Pos 匹配的 ASCII 标记
        "剪映设置里记着的位置",                 # 草稿位置的来路（最准的那条）
        "伴侣自动找到的", "沿用你手动指定的",
        "已经自动写进配置：", "（你不需要做任何设置）",
        "自动配置：已经替你配好了",             # 体检里那条「你不用做任何事」
        # ★ 第十五批：找不到草稿目录时的指路文案（跨行隐式拼接后是一整条常量）
        "找不到剪映的草稿目录。\n\n"
        "剪映里改过草稿位置的话（设置 → 草稿位置），\n"
        "请右键伴侣 →「设置草稿目录…」手动指一下。",
        "必须这份 · 换草稿会报「媒体格式不支持」",     # 副文案：为什么必须是这份
        "需要你停在草稿编辑页（在首页就双击草稿卡片进去）",
        "请双击首页上的草稿卡片打开",
        "先把剪映露出来，再双击首页的草稿卡片",
        # ★★ 第十七批：中止/自动还原的**结论文案**（用户据此判断现场有没有收拾干净）
        "已经停下了。\n\n★ 这一轮**还没动过你的草稿**（连预合成都还没发出去），\n"
        "   所以不需要还原 —— 剪映还是你原来那个样子。\n\n"
        "想重新开始，点一下悬浮球就行。",
        "已中止 · 正在还原草稿…",
    ]
    _ctxt_miss = [t for t in _need_core_txt if t not in _cconsts]
    # ★ 第十四批：**跨行隐式拼接**的文案在字节码里被折成**一整条**常量，
    #   所以只能按子串查。（这些片段不出现在 jy_core 的注释/docstring 里，
    #   不会像第十二批那样被同名文档字符串假绿。）
    _need_core_sub = ["右键伴侣 →「设置剪映路径…」手动指一下。",
                      # ★ 第十六批：两句都改成"会读剪映设置"，这里跟着更新
                      #   （旧句子「如果剪映里改过草稿位置…」已经不再出现）。
                      "伴侣会重新自动找一遍（**也会去读剪映设置里记的草稿位置**）；",
                      "两边都没有，才需要右键伴侣 →「设置草稿目录…」手动指一下。",
                      # ★ 第十六批：安装包念给用户的那段（format_autosetup 拼出来）
                      "装好之后伴侣下次启动会自己认，不用你指路。",
                      # ★ 第十七批：这句夹在 f-string 的跨行隐式拼接里，
                      #   字节码里是**和前后文合并后的一整条**常量 → 只能按子串查。
                      #   （全源码只出现这一次，不在任何注释/docstring 里，不会假绿。）
                      "中止时伴侣会自动还原，不会给你留一个半残的草稿。"]
    _ctxt_miss += [f"{t}(子串)" for t in _need_core_sub if not any(t in s for s in _cconsts)]
    print("内核必需文案缺失:", _ctxt_miss if _ctxt_miss else "无")
    flag_ok = flag_ok and (not _ctxt_miss)

    gui = None
    for name in arc.toc:
        base = os.path.basename(str(name)).lower()
        # ★ 第十批修：PyInstaller 6.x 把主脚本存成**没有扩展名**的条目 `剪映伴侣`，
        #   旧匹配（endswith "剪映伴侣.py"/"companion"）**一条都命中不了** →
        #   返回 None → GUI 那半边代码从来没被核验过（等于白核）。这里补上精确名。
        if base in ("剪映伴侣", "剪映伴侣.py", "剪映伴侣.pyc") or "companion" in base:
            gui = name
            break
    print("主脚本条目:", gui)
    gui_ok = True
    if gui is None:
        print("!! 找不到主脚本条目 → GUI 侧的改动无法核验")
        gui_ok = False
    else:
        import marshal
        gco = marshal.loads(arc.extract(gui))
        gnames = all_names(gco)
        # GUI 侧本批要钉住的名字（二次还原护栏 + 第十一批 UX）
        gmust = ["backup_consumed", "askyesno", "_restore_draft", "_watch",
                 # ★ 第十八批（v1.1.0 导出守望）：挂/收/开关
                 "_start_export_watch", "_stop_export_watch", "_toggle_export_watch",
                 "_watch_stop",
                 # ★ 第十九批（v1.2.0 批量队列）：入口/开跑/工作线程
                 "_fill_batch_menu", "_start_batch", "_batch_worker", "_batch_menu",
                 # ★ 第二十批（v1.3.0 全自动导出 · 实验）：开关/接管/收尾共用还原
                 "_toggle_fullauto", "_run_full_auto", "_fullauto_worker",
                 "_start_restore",
                 # ★ 第十一批：按钮自适应宽度（长文案不再被窗口裁掉）
                 "_fit_width", "_resize_to", "ellipsize", "UI_MAX_GROW",
                 # ★ 第十一批：缩放防抖（按钮不再跟着「任务栏显/隐」翻面）
                 "stable_ref", "rescale_target", "REF_DEADBAND", "RESCALE_HOLD",
                 "scale_deviates", "rescale_ratio", "rescale_verdict",
                 "SCALE_STEP", "RESCALE_SLACK",
                 # ★ 第十一批：求助回路闭环（日志 / 说明书 / 备份夹能自己打开）
                 "_reveal", "_open_log", "_open_manual", "_open_backup",
                 # ★ 第十一批：空闲态"还没还原"提醒（关掉重开也还记得）
                 "_idle_sub",
                 # ★ 第十二批：悬浮球（球 ↔ 横条形变 + 悬停展开 + 流畅动效）
                 "corner_xy", "should_expand", "ball_label", "morph_shape",
                 "morph_alphas", "ring_style", "ease_out_cubic", "mix_hex",
                 "round_pts", "clamp_box", "BALL_TEXT",
                 "BALL_MODES", "BALL_MODE_LABEL", "BALL_LEAVE_MS",
                 "ANIM_FRAME_MS", "ANIM_SHAPE_SECS",
                 "_redraw", "_sync_expanded", "_apply_window_size",
                 "_anim_begin", "_anim_tick", "_on_enter", "_on_leave",
                 "_cycle_ball_mode",
                 # ★ 第十二批：形态相关的**实例属性名**。
                 #   注意：属性名进的是 `co_names`（`STORE_ATTR`/`LOAD_ATTR` 的操作数），
                 #   不是常量字符串 —— 我第一版把它们塞进文案常量表去查，
                 #   结果被**文档字符串里的同名子串**误命中（假绿），
                 #   而 `_leave_at` 恰好没人提过就报缺失，才暴露了这个错。
                 "_pill_w", "_expanded", "_leave_at", "_ball_mode",
                 "_pushed_size", "_anims", "_cur",
                 # ★ 第十四批（用户"继续优化"）：
                 #   ① 事件循环存活（一条坏消息不许杀死 _pump）
                 #   ② 剪映没开时点导出 → 自动等/启动/接着跑（不用点两次）
                 #   ③ 手动指定剪映路径（分发"路径假设"的兜底）
                 "_launch_then_run", "_pick_jy_exe",
                 "LAUNCH_WAIT_SECS", "LAUNCH_PROBE_SECS", "find_jianying",
                 # ★ 第十五批：草稿目录手动指路 + markdown 星号的唯二对话框出口
                 "_pick_draft_root", "md_plain", "ask_yes", "show_info",
                 # ★★ 第十六批：安装时那次自动配置（`--autosetup` 的落点）
                 "autosetup_cli",
                 # ★★ 第十七批（用户"用户可以暂终止，终止时自动还原"）：
                 #   起流程时挂令牌 / 中止入口 / 中止的收尾分支
                 "_start_pipeline", "_abort_pipeline"]
        gmiss = [n for n in gmust if n not in gnames]
        # 文案常量核验：菜单标签 / 空闲提醒必须真在 exe 里。
        # ★ 第十四批：原来这里**另写了一份**只递归 code object 的收集器（和
        #   `all_consts` 重复且更弱）→ 统一走 all_consts，免得两份实现慢慢跑偏。
        gconsts = all_consts(gco)
        # 文案常量核验：菜单标签 / 空闲提醒必须真在 exe 里
        need_txt = ["已还原过",                      # 第十批：二次还原护栏
                    "打开运行日志（发给作者）",      # 第十一批：日志能自己打开
                    "打开草稿备份文件夹",            # 第十一批：备份夹能自己打开
                    "打开使用说明",                  # 第十一批：说明书能自己打开
                    "上次那轮还没还原",              # 第十一批：空闲态提醒
                    "悬浮球：自动收纳",              # 第十二批：形态菜单默认标签
                    # 第十四批：启动超时/找不到剪映时的话（别退回笼统"未完成"）
                    "剪映没起来", "剪映已启动", "正在启动剪映…",
                    "冷启动可能要几十秒，起来后我自己接着跑",
                    "手动打开后，再点一次「一键导出」",
                    "右键「设置剪映路径…」手动指一下",
                    # ★★ 第十六批：安装时自动配置（命令行入口 + 结论前缀）
                    "--autosetup", "[autosetup] ", "自动配置失败",
                    # ★★ 第十七批：中止入口的菜单文案与按钮文案
                    "中止本次流程（自动还原）", "正在中止…", "已中止"]
        txt_miss = [t for t in need_txt if not any(t in s for s in gconsts)]
        # ★ 第十二批补：上面那个是**子串**匹配，文档字符串里提到同名也会命中
        #   （弱守卫）。新批次的这几条要求**精确常量**，少一条就 FAIL。
        need_txt_exact = ["悬浮球：自动收纳", "自动收纳", "一直展开", "只留小球",
                          "ball_mode", "corner", "corner_margin", "top_gap",
                          # 第十四批：菜单键 + 配置键（改了名字＝入口/设置失效）
                          "jypath", "jianying_exe",
                          "设置剪映路径…　· ",
                          # ★ 第十五批：草稿目录菜单键 / 标签 / 「默认位置」
                          "draftroot", "设置草稿目录…", "设置草稿目录…　· ", "默认位置",
                          # ★★ 第十六批：命令行入口名（改了名字安装包就调不动了）
                          "--autosetup",
                          # ★★ 第十七批：中止菜单键 +「没在跑」时的置灰标签
                          "abort", "中止本次流程（当前没在跑）",
                          # ★★ 第十八批（v1.1.0 导出守望）：菜单键 / 配置键 / 消息名
                          "exportwatch", "export_watch", "export_done",
                          "导出守望（导完提醒还原）",
                          # ★★ 第十九批（v1.2.0 批量队列）：菜单键 / 收尾 rescue 名
                          "batchmenu", "batch", "批量处理草稿…",
                          # ★★ 第二十批（v1.3.0 全自动导出 · 实验）
                          "fullauto", "full_auto_export", "full_auto_done",
                          "full_auto_fail", "全自动导出（实验）"]
        txt_miss += [f"{t}(需精确常量)" for t in need_txt_exact if t not in gconsts]
        print("GUI 必需名字缺失:", gmiss if gmiss else "无")
        print("GUI 必需文案缺失:", txt_miss if txt_miss else "无")
        gui_ok = (not gmiss) and (not txt_miss)

    ok = (not miss) and (not hit) and flag_ok and gui_ok
    print("核验结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
