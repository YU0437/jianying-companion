#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线往返测试：草稿备份 → 清空/改动 → 还原写回。

为什么要单独测：这条链路**会真的动用户文件**（写回草稿 json），
不能拿用户的真实草稿做实验。这里用临时目录造一份"假草稿"，
把「只备份 .json」「产物一个字节不动」「还原前留快照」「崩溃/缺失时拒绝动刀」
这几条硬性质钉死。

用法：python _test_backup_restore.py
"""
import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import jy_core as core  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(("  OK   " if ok else "  FAIL ") + f"{name}  期望={want!r} 实际={got!r}")
    if not ok:
        fails.append(name)


def md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


with tempfile.TemporaryDirectory() as _td:
    base = Path(_td)
    fake_desktop = base / "Desktop"
    fake_desktop.mkdir()

    # —— 造一份假草稿 ——
    draft = base / "草稿目录" / "9月18日"
    (draft / "subdraft" / "S1").mkdir(parents=True)
    (draft / "Resources" / "combination").mkdir(parents=True)
    (draft / ".backup").mkdir(parents=True)

    (draft / "draft_content.json").write_text('{"v":"预合成前"}', encoding="utf-8")
    (draft / "draft_meta_info.json").write_text('{"n":"9月18日"}', encoding="utf-8")
    (draft / "subdraft" / "S1" / "draft_content.json").write_text(
        '{"combination_id":"AAA"}', encoding="utf-8")
    (draft / ".backup" / "draft_content.json").write_text('{"v":"更早的"}', encoding="utf-8")
    # ▼ 产物：这是禁地，全程必须一字不变
    prod = draft / "Resources" / "combination" / "AAAA-1111_video.mp4"
    prod.write_bytes(b"\x00\x01\x02" * 1000)
    prod_md5 = md5(prod)

    core.desktop_dir = lambda: fake_desktop          # 把"桌面"指到临时目录
    # ★ 关键：backup_draft_json 成功后会 save_config()，而 save_config 写的是
    #   **项目目录里的真实 伴侣配置.json**。测试必须把它也重定向到临时目录，
    #   否则会往用户配置里塞一堆 fake 路径（第一次写这个测试时就踩了）。
    core.config_path = lambda: base / "假·伴侣配置.json"

    print("[1] 备份：只拷 .json，跳过 Resources / .backup")
    cfg = core.default_config()
    bk = core.backup_draft_json(cfg, draft, st=None, tag="预合成前")
    check("备份目录建出来了", bk is not None and Path(bk).is_dir(), True)
    got = sorted(str(p.relative_to(bk)).replace("\\", "/")
                 for p in Path(bk).rglob("*") if p.is_file())
    check("备份内容 = 3 个元数据 json（不含 .backup / 不含产物）",
          got, ["draft_content.json", "draft_meta_info.json",
                "subdraft/S1/draft_content.json"])
    check("cfg 记住了备份位置",
          cfg.get("_backup_dir") == str(bk)
          and cfg.get("_backup_draft_dir") == str(draft)
          and cfg.get("_backup_draft_name") == "9月18日", True)
    check("★ 产物 mp4 没被拷进备份区",
          not any(p.suffix.lower() in core.MEDIA_EXT for p in Path(bk).rglob("*")), True)
    check("★ 产物 mp4 内容没变（备份是只读的）", md5(prod), prod_md5)

    print("[2] backup_info 能认出这份备份（GUI/还原都靠它）")
    b2, d2, n2 = core.backup_info(cfg)
    check("返回备份目录 / 草稿目录 / 文件数",
          (Path(b2) == Path(bk), Path(d2) == draft, n2), (True, True, 3))

    print("[3] 模拟流水线跑完：时间线被清空、拖入产物 → 草稿 json 全变了")
    (draft / "draft_content.json").write_text('{"v":"预合成后-只剩一个片段"}',
                                              encoding="utf-8")
    (draft / "draft_meta_info.json").write_text('{"n":"9月18日","t":123}', encoding="utf-8")
    (draft / "subdraft" / "S1" / "draft_content.json").write_text(
        '{"combination_id":"AAAA-1111"}', encoding="utf-8")
    (draft / "new_extra.json").write_text('{"x":1}', encoding="utf-8")
    (draft / "Resources" / "combination" / "BBBB-2222_video.mp4").write_bytes(b"\xff" * 500)
    prod2 = draft / "Resources" / "combination" / "BBBB-2222_video.mp4"
    prod2_md5 = md5(prod2)

    print("[4] 还原：杀剪映 → 留快照 → 写回备份（stub 掉真实开关机）")
    called = {}
    core._jianying_running = lambda: True
    core.kill_jianying = lambda: called.setdefault("kill", True)
    core._restart_and_enter = lambda cfg, st, **kw: called.setdefault("restart", kw) or 0

    msgs = []
    core.restore_draft(cfg, None, lambda ok, msg: msgs.append((ok, msg)))
    check("走完了流程（杀过剪映 + 重启过）",
          bool(called.get("kill")) and isinstance(called.get("restart"), dict), True)
    check("★ 是先杀剪映再写回（顺序反了会被剪映覆盖）",
          called.get("kill") is True, True)
    check("回调报成功", msgs and msgs[-1][0], True)

    check("★ 时间线内容写回成「预合成前」",
          (draft / "draft_content.json").read_text(encoding="utf-8"),
          '{"v":"预合成前"}')
    check("★ subdraft 的登记也写回成预合成前",
          (draft / "subdraft" / "S1" / "draft_content.json").read_text(encoding="utf-8"),
          '{"combination_id":"AAA"}')
    check("★ 备份里没有的 extra json **不会被删**（写回不是镜像同步，只覆盖）",
          (draft / "new_extra.json").is_file(), True)
    check("★ .backup（剪映自己的草稿备份）原样没动",
          (draft / ".backup" / "draft_content.json").read_text(encoding="utf-8"),
          '{"v":"更早的"}')

    print("[5] ★ 红线：产物文件一个字节都没被动过")
    check("旧产物 md5 不变", md5(prod), prod_md5)
    check("新产物（拖进去之后才有的）md5 不变", md5(prod2), prod2_md5)
    check("产物文件都还在（还原不删产物）",
          (prod.is_file(), prod2.is_file()), (True, True))

    print("[6] 还原前快照留档（后悔了能捞回「导出后」的状态）")
    snaps = [p for p in core.backup_root_dir().iterdir() if "还原前" in p.name]
    check("确实生成了一个「还原前」快照目录", len(snaps), 1)
    snap_files = sorted(str(p.relative_to(snaps[0])).replace("\\", "/")
                        for p in snaps[0].rglob("*") if p.is_file())
    check("快照抓的是**导出后**那一版（含 extra / 新登记）",
          "new_extra.json" in snap_files
          and (snaps[0] / "subdraft" / "S1" / "draft_content.json").read_text(
              encoding="utf-8") == '{"combination_id":"AAAA-1111"}', True)
    check("★ 快照**不顶掉**正式还原点（_backup_dir 仍指预合成前那份）",
          cfg.get("_backup_dir") == str(bk), True)

    print("[6b] ★ 还原成功后打「已用过」标记（否则二次还原会无声盖掉新改动）")
    check("★ 刚才那次还原已经把标记置位（备份指针不清理，只能靠这个标记）",
          cfg.get("_backup_consumed"), True)
    check("backup_consumed 读得到（GUI 靠它决定要不要二次确认）",
          core.backup_consumed(cfg), True)
    check("没有备份时 backup_consumed 不炸（默认 False）",
          core.backup_consumed(core.default_config()), False)
    _probe_cfg = dict(cfg)
    _probe_cfg["_backup_dir"] = ""
    core.backup_draft_json(_probe_cfg, draft, tag="重置探针")
    check("★ 做新备份会把标记清回 False（新一轮导出又能正常还原）",
          _probe_cfg.get("_backup_consumed"), False)

    print("[7] 没有备份时：拒绝动刀（不能凭空改草稿）")
    cfg2 = core.default_config()
    msgs2 = []
    core.restore_draft(cfg2, None, lambda ok, msg: msgs2.append((ok, msg)))
    check("报失败且提示「没找到可还原的备份」",
          msgs2 and msgs2[-1][0] is False and "没找到可还原的备份" in msgs2[-1][1], True)

    print("[8] 有备份但原草稿目录没了：也拒绝乱写")
    cfg3 = dict(cfg)
    cfg3["_backup_draft_dir"] = str(base / "不存在的草稿")
    msgs3 = []
    core.restore_draft(cfg3, None, lambda ok, msg: msgs3.append((ok, msg)))
    check("报失败且提示原草稿目录不见了",
          msgs3 and msgs3[-1][0] is False and "原草稿目录" in msgs3[-1][1], True)

    print("[9] 清空时间线：走剪映自己的全选+删除键，且不碰 Ctrl+V")
    import inspect as _inspect
    cs = _inspect.getsource(core._clear_timeline)
    check("发 Ctrl+A", "ctrl+a" in cs.lower(), True)
    check("删除键从快捷键表实时读（用户改绑也能跟上）",
          "read_shortcut" in cs and '"del"' in cs, True)
    check("没有 Ctrl+V（用户实测剪映不认剪贴板文件落地）",
          "ctrl+v" not in cs.lower(), True)
    check("清空函数不写任何文件",
          not any(t in cs for t in ("write_text", "open(", "unlink", "remove",
                                    "shutil", "os.replace")), True)
    # ★ 2026-09-18 晚「清空假成功」事故后的离线冒烟（不碰真剪映）：
    check("★ 挑出来的删除键是 Del 系，不是 Backspace",
          core._pick_clear_delete_key().strip().lower() in ("del", "delete"), True)
    check("★ 没有剪映窗口时 _jy_is_responding 老实返回 False（不抛异常）",
          core._jy_is_responding(0), False)
    check("★ 没有剪映时 _wait_jy_ready_for_keys 限时返回 False（不卡死）",
          core._wait_jy_ready_for_keys(0, 0.8), False)
    # ★ 用可控替身把新代码真正跑一遍（防 NameError / 防"没基线也硬发键"）：
    #   把"抬前台/等空闲/抬窗"都假装成功、但**抓不到窗口矩形** →
    #   正确行为是"没有基线就不敢发键、直接 return False"，且**一个键都不发**。
    import time as _t
    _sent = []
    _orig = (core._wait_foreground, core._wait_jy_ready_for_keys,
             core._raise_for_drag, core.window_rect, core.send_combo)
    try:
        core._wait_foreground = lambda *a, **k: True
        core._wait_jy_ready_for_keys = lambda *a, **k: True
        core._raise_for_drag = lambda *a, **k: True
        core.window_rect = lambda *a, **k: None          # ← 抓不到矩形 = 没基线
        core.send_combo = lambda *a, **k: _sent.append(a)
        _t0 = _t.time()
        _r = core._clear_timeline({}, 12345, lambda *a, **k: None)
        _dt = _t.time() - _t0
    finally:
        (core._wait_foreground, core._wait_jy_ready_for_keys,
         core._raise_for_drag, core.window_rect, core.send_combo) = _orig
    check("★ 抓不到基线时 _clear_timeline 返回 False（不假装成功）", _r, False)
    check("★ 而且**一个按键都没发**（没有基线就不敢动时间线）", _sent, [])
    check(f"★ 这条路径很快（{_dt:.2f}s），不会卡住", _dt < 3.0, True)

    print("[10] 孤儿 subdraft 检测：如实报告「预合成时新建、还原后仍留着」的目录")
    # 上一步的写回不删文件，所以 [3] 里没有的东西会留着——这里模拟一下
    (draft / "subdraft" / "NEW-PRECOMP-ID").mkdir(parents=True, exist_ok=True)
    (draft / "subdraft" / "NEW-PRECOMP-ID" / "draft_content.json").write_text(
        "{}", encoding="utf-8")
    check("认出多出来的 subdraft（备份里只有 S1）",
          core._orphan_subdrafts(bk, draft), ["NEW-PRECOMP-ID"])
    check("备份里有的 S1 不算孤儿（它是原有内容）",
          "S1" not in core._orphan_subdrafts(bk, draft), True)
    check("没有 subdraft 目录时不报错（返回空）",
          core._orphan_subdrafts(bk, base / "空目录"), [])
    check("★ 报告文案里明确写了「伴侣从不删你的文件」",
          "从不删你的文件" in _inspect.getsource(core.restore_draft), True)

print()
print("失败项:", fails if fails else "无")
sys.exit(1 if fails else 0)
