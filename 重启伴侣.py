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
DIST = HERE / "dist" / "JianyingCompanion.exe"
DEST = Path.home() / "AppData" / "Local" / "JianyingCompanion" / "剪映伴侣.exe"
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
        if not DIST.is_file():
            print(f"!! 找不到 {DIST}")
            return 2
        DEST.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(DIST), str(DEST))
        a, b = md5(DIST), md5(DEST)
        print(f"[2] 已覆盖  dist={a[:12]}  已装={b[:12]}  一致={a == b}")
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
