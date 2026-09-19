# -*- coding: utf-8 -*-
"""回归测试：「产物必须先写完再交付」这道闸门。

背景（用户提的疑问：预合成结束太快 / 杀进程太快）：
  旧版 `wait_new_combo` 只比 `mtime`，文件**一出现就返回**。
  大文件在刚出现的瞬间可能还在写 → 交付到半成品 → 拖进剪映报
  「导入文件损坏 / 媒体格式不支持」。
  现在把它焊死：未稳定的文件**不许**被返回。
"""
import importlib.util
import sys
import tempfile
import threading
import time
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


def make_root(tmp):
    """造一个假的草稿树：<tmp>/某草稿/Resources/combination/"""
    combo = Path(tmp) / "某草稿" / "Resources" / "combination"
    combo.mkdir(parents=True, exist_ok=True)
    return combo


def grow_file(path, seconds, delay=0.15):
    """后台线程持续追加写入，模拟"剪映还在写"。"""
    stop = threading.Event()

    def run():
        with open(path, "ab") as fh:
            t0 = time.time()
            while time.time() - t0 < seconds:
                fh.write(b"\x00" * (64 * 1024))
                fh.flush()
                time.sleep(delay)
        stop.set()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return stop, th


print("[1] wait_file_settled：还在写的文件必须判为「不稳」")
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "growing_video.mp4"
    p.write_bytes(b"\x00" * 1024)
    stop, th = grow_file(p, seconds=3)
    t0 = time.time()
    ok = core.wait_file_settled(p, timeout=1.6, interval=0.4)
    check("写入中 → False", ok, False)
    print(f"       （耗时 {time.time()-t0:.1f}s，size={p.stat().st_size}）")
    th.join()

    print("[2] 写完之后应当判为「稳」")
    t0 = time.time()
    ok2 = core.wait_file_settled(p, timeout=5, interval=0.4)
    check("已写完 → True", ok2, True)
    print(f"       （耗时 {time.time()-t0:.1f}s）")

print("[3] pick_combo_file 必须排除剪映的临时文件与 alpha 兄弟")
with tempfile.TemporaryDirectory() as tmp:
    combo = make_root(tmp)
    good = combo / "AAAA_video.mp4"
    good.write_bytes(b"done" * 1000)
    (combo / "BBBB_video.mp4_temp.mp4").write_bytes(b"\x00" * 48)   # 写到一半的临时件
    (combo / "AAAA_video.mp4.alpha.mp4").write_bytes(b"alpha" * 100)
    picked = core.pick_combo_file(combo)
    check("选中的是正式文件", picked.name if picked else None, "AAAA_video.mp4")

print("[4] wait_new_combo：候选还在写的时候**不许**返回（防抢跑）")
with tempfile.TemporaryDirectory() as tmp:
    combo = make_root(tmp)
    late = combo / "CCCC_video.mp4"
    stop4 = threading.Event()

    def delayed():
        time.sleep(0.8)
        late.write_bytes(b"\x00" * 1024)
        with open(late, "ab") as fh:          # 出现后一直写，直到测试喊停
            while not stop4.is_set():
                fh.write(b"\x00" * (64 * 1024))
                fh.flush()
                time.sleep(0.15)

    th4 = threading.Thread(target=delayed, daemon=True)
    th4.start()
    t0 = time.time()
    got = core.wait_new_combo(Path(tmp), {}, timeout=6)
    spent = time.time() - t0
    check("持续在写 → 不返回（None）", got, None)
    check("外层 timeout 没被内层等待吃掉（≤10s）", spent <= 10, True)
    print(f"       （等待窗口 6s，实际耗时 {spent:.1f}s，"
          f"文件已长到 {late.stat().st_size} 字节）")
    stop4.set()
    th4.join(timeout=5)                       # 收尾，否则临时目录删不掉

print("[5] wait_new_combo：文件写完且不再变 → 正常返回")
with tempfile.TemporaryDirectory() as tmp:
    combo = make_root(tmp)
    f = combo / "DDDD_video.mp4"
    f.write_bytes(b"x" * 4096)                 # 一次性写好，之后不动
    got = core.wait_new_combo(Path(tmp), {}, timeout=8)
    check("稳定 → 返回该文件", (got is not None and str(got[2]) == str(f)),
          True)

print("[6] settle=False 时保持旧行为（只用来对照，不用于生产）")
with tempfile.TemporaryDirectory() as tmp:
    combo = make_root(tmp)
    f = combo / "EEEE_video.mp4"
    f.write_bytes(b"y" * 512)
    t0 = time.time()
    got = core.wait_new_combo(Path(tmp), {}, timeout=5, settle=False)
    check("不等稳定也能返回", (got is not None and str(got[2]) == str(f)), True)
    print(f"       （耗时 {time.time()-t0:.2f}s，可见确实没等）")

print()
print("失败项:", fails if fails else "无")
sys.exit(1 if fails else 0)
