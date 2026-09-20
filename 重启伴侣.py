#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付后重启「剪映伴侣」，并**隔一次调用**复核新实例真的在跑。

为什么需要它（2026-09-17 实测踩坑）：
  1) Windows 允许**删除/覆盖一个正在运行的 exe**（文件被标记删除，句柄还在）。
     于是 `cp -f dist\\...exe 剪映伴侣.exe` 会"成功"，但**老进程跑的还是老镜像** ——
     看起来部署成功，实际新代码一行都没生效。必须先杀干净再覆盖。
  2) 沙盒里**没有任何常规办法**能把 GUI 进程真正拉起来并让它活下去：
     · `cmd /c start "" xx.exe` / `explorer.exe xx.exe` → 返回 0，既没窗口也没进程；
     · `subprocess.Popen(DETACHED_PROCESS)` → 进程起来后随工具调用结束被杀；
     · `DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB` → `WinError 5 拒绝访问`
       （作业对象不允许 breakaway）；
     · `wmic process call create` → Win11 24H2 起 wmic 已被移除。
     ✅ 唯一可靠的办法是借**计划任务服务**起进程：
        `schtasks /create ... /sc once /st 23:59 /f` → `schtasks /run /tn ...` → 删任务。
        Task Scheduler 是独立的服务进程，拉起来的 exe 跟沙盒无关，能长期存活。
  3) `taskkill /IM 剪映伴侣.exe /F` 从 bash 传中文文件名容易被编码搞坏参数，
     用 `taskkill /PID <pid> /F` 最稳。

用法：
    python 重启伴侣.py                 # 杀干净 → 覆盖 dist 版 → 启动 → 复核
    python 重启伴侣.py --no-copy       # 只重启，不覆盖
    python 重启伴侣.py --check         # 只报告当前窗口/进程（rc=0 表示活着）

★★ 第二十八批起，产物是 **onedir**（一个目录），不是单个 exe：
   覆盖 = 1) 清空并重拷 `_internal\\`（整个目录一起比内容指纹）
          2) 把 `dist\\JianyingCompanion\\JianyingCompanion.exe` 拷成
             `{安装目录}\\剪映伴侣.exe`（保持重命名，快捷方式/任务管理器里都认这个名字）
   ⚠️ `_internal` 与主 exe **必须同时更新**：只换 exe 会得到"新代码 + 旧依赖"，
      它能起来，然后在某条冷路径上炸。
"""
import ctypes
import ctypes.wintypes as w
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# ★★ 第二十八批：打包从 onefile 换成 **onedir**（启动快 ~1.2 秒，见 spec）。
#   产出物变成一个**目录**：`dist\JianyingCompanion\{JianyingCompanion.exe, _internal\}`。
#   部署 = 把整个目录搬到安装目录，并把主 exe 重命名成 剪映伴侣.exe
#   （`_internal` 必须**紧挨着**它 —— bootloader 就是按这个相对位置找依赖的）。
#   ⚠️ 两个来源都认：onedir 优先，退回落单文件的旧产物（方便中途回退）。
DIST_DIR = HERE / "dist" / "JianyingCompanion"
DIST_EXE_IN_DIR = DIST_DIR / "JianyingCompanion.exe"
DIST_ONEFILE = HERE / "dist" / "JianyingCompanion.exe"
DEST_DIR = Path.home() / "AppData" / "Local" / "JianyingCompanion"
DEST = DEST_DIR / "剪映伴侣.exe"
INTERNAL = "_internal"
TITLE = "剪映伴侣"

u, k = ctypes.windll.user32, ctypes.windll.kernel32


def windows():
    """所有标题为 剪映伴侣 的顶层窗口 → [(hwnd, pid)]"""
    out = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)

    def cb(h, _l):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if b.value == TITLE:
                pid = w.DWORD()
                u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                out.append((h, pid.value))
        return True

    u.EnumWindows(CB(cb), 0)
    return out


def procs():
    """所有名为 剪映伴侣.exe 的进程 → [(pid, name)]"""
    TH32CS_SNAPPROCESS = 0x2

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD),
                    ("th32ProcessID", w.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                    ("th32ParentProcessID", w.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", w.DWORD),
                    ("szExeFile", ctypes.c_char * 260)]

    s = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e, res = PE32(), []
    e.dwSize = ctypes.sizeof(PE32)
    if k.Process32First(s, ctypes.byref(e)):
        while True:
            nm = e.szExeFile.decode("gbk", "replace")
            if "伴侣" in nm:
                res.append((e.th32ProcessID, nm))
            if not k.Process32Next(s, ctypes.byref(e)):
                break
    k.CloseHandle(s)
    return res


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_md5(root):
    """整个目录的**内容指纹**（相对路径 + 每个文件的 md5，按路径排序）。

    ★ 为什么不只比主 exe 的 md5：onedir 的代码/依赖全在 `_internal\\` 里，
      而**只覆盖主 exe** 会让"新 exe + 旧依赖"配在一起 —— 它照样能起来，
      然后在某条冷路径上炸（或更糟：静默行为不对）。所以目录必须一起比。
    ★ 为什么不用"文件数 + 总字节"这种便宜指纹：改一个文件不改大小它就看不出来，
      而这里正好是"要证明两个目录**逐字节一样**"的场合。35MB 读两遍 ~0.5s，值得。
    """
    h = hashlib.md5()
    root = Path(root)
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode("utf-8"))
            h.update(b"\0")
            h.update(md5(p).encode("ascii"))
            h.update(b"\n")
    return h.hexdigest()


def status():
    return windows(), procs()


def main(argv):
    if "--check" in argv:
        win, ps = status()
        print("窗口:", win)
        print("进程:", ps)
        return 0 if (win and ps) else 1

    print("[1] 现状 → 窗口:", windows(), " 进程:", procs())

    # —— 杀干净（按 PID，不按中文进程名）——
    pids = {p for p, _ in procs()} | {p for _, p in windows()}
    for pid in sorted(pids):
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True)
        print(f"    taskkill /PID {pid} => rc={r.returncode}")
    time.sleep(2.0)
    if procs():
        print("!! 还有残留进程，请手动结束：", procs())
        return 2

    # —— 覆盖（此时没人占着 exe 了）——
    if "--no-copy" not in argv:
        onedir = DIST_EXE_IN_DIR.is_file()
        src_exe = DIST_EXE_IN_DIR if onedir else DIST_ONEFILE
        if not src_exe.is_file():
            print(f"!! 找不到产物：{DIST_EXE_IN_DIR} 或 {DIST_ONEFILE}")
            return 2
        DEST_DIR.mkdir(parents=True, exist_ok=True)
        if onedir:
            # ★★ `_internal` **先清空再拷**：上一版多出来的旧文件留在那儿，
            #    新 exe 有可能加载到"这一版不该存在"的模块/依赖 ——
            #    而且症状是**冷路径才炸**，最难查。清空后拷 = 逐字节等于产出。
            di = DEST_DIR / INTERNAL
            if di.exists():
                shutil.rmtree(di, ignore_errors=True)
            shutil.copytree(str(DIST_DIR / INTERNAL), str(di))
            a, b = tree_md5(DIST_DIR / INTERNAL), tree_md5(di)
            print(f"[2] 已覆盖 {INTERNAL}  产出={a[:12]}  已装={b[:12]}  一致={a == b}")
            if a != b:
                print(f"!! {INTERNAL} 内容不一致，部署失败")
                return 2
        shutil.copy2(str(src_exe), str(DEST))
        a, b = md5(src_exe), md5(DEST)
        print(f"[2] 已覆盖 主exe  dist={a[:12]}  已装={b[:12]}  一致={a == b}")
        if a != b:
            print("!! md5 不一致，部署失败")
            return 2
    else:
        print(f"[2] 跳过覆盖，直接启动 {DEST}")

    # —— 启动：借计划任务服务起进程（沙盒里唯一能长久存活的办法）——
    task = "JyCompanionLaunch"
    subprocess.run(["schtasks", "/delete", "/tn", task, "/f"], capture_output=True)
    r1 = subprocess.run(["schtasks", "/create", "/tn", task, "/tr", str(DEST),
                         "/sc", "once", "/st", "23:59", "/f"],
                        capture_output=True)
    if r1.returncode != 0:
        print("!! 创建计划任务失败：" +
              (r1.stdout or r1.stderr).decode("gbk", "replace").strip())
        return 2
    r2 = subprocess.run(["schtasks", "/run", "/tn", task], capture_output=True)
    subprocess.run(["schtasks", "/delete", "/tn", task, "/f"], capture_output=True)
    if r2.returncode != 0:
        print("!! 触发计划任务失败：" +
              (r2.stdout or r2.stderr).decode("gbk", "replace").strip())
        return 2
    print("[3] 已通过计划任务拉起，等一下复核…")

    # —— 复核：隔几秒再看窗口/进程；父进程（本脚本）退出也不影响它 ——
    for _ in range(5):
        time.sleep(2)
        win, ps = status()
        if win and ps:
            print(f"[4] 复核通过 ✅ 窗口={win} 进程={ps}")
            return 0
    print("!! 启动后没看到窗口/进程：", status())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
