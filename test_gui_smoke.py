# -*- coding: utf-8 -*-
"""GUI 冒烟测试：真起一次 tk 窗口，验证这轮改动（同大共小缩放 / 同生共死显隐 / 高频跟随）
不会在构造或运行中炸掉。窗口只存在几秒，跑完立刻销毁。

注意：这是**前台**运行（不是后台启动），所以不会踩"沙盒清理子进程"的坑。
"""
import importlib.util
import inspect
import sys
import time
from ctypes import byref
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))          # 进度条断言要直接读 core 的常量
import jy_core as core
spec = importlib.util.spec_from_file_location("companion_gui", HERE / "剪映伴侣.py")
gui = importlib.util.module_from_spec(spec)
sys.modules["companion_gui"] = gui
spec.loader.exec_module(gui)

fails = []


def check(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


c = None
try:
    print("[1] 构造 Companion()（会真建一个悬浮窗）")
    c = gui.Companion()
    check("构造成功", True)


    def settle():
        """把"球↔横条"补间**一次落到终态**。

        ★ 为什么测试需要它（第十二批）：形态是**动画**的，`set_state` 之后
          `_cur` 还在 0.18s 的补间中间，此刻 `_redraw` 画的是"半展开"的中间帧
          （文字刚淡入一点、宽度还没到位），拿它量尺寸/文案必然是飘的。
          生产代码里由 16ms 的 time 驱动收敛，测试里没有事件循环等它，
          所以显式 `_anim_stop()` + `animate=False` 直接落到终态。
        """
        c._anim_stop()
        c._expanded = gui.should_expand(c.state[0], c._await_restore,
                                        c._hover, c._ball_mode)
        tw, tr = c._target_shape()
        c._cur.update({"w": tw, "r": tr, "fgv": 1.0 if c._expanded else 0.0,
                       "pct": float(c._pct or 0)})
        c._redraw()


    def run_anim(step=0.05, limit=80):
        """把补间"快进"到跑完。

        ★ 测试里没有 mainloop，真时间几乎不流动 → `_anim_tick` 算出来的进度
          永远贴着 0，补间永远跑不完。所以每帧**手动把起点往回拨** `step` 秒，
          等价于"模拟过去了这么多时间"。这样测的是真补间（含缓动函数），
          而不是绕过动画直接赋值。
        """
        for _ in range(limit):
            if not c._anims:
                break
            for a in c._anims.values():
                a["t0"] -= step
            c._anim_tick()
        return not c._anims

    # ★ 首次运行会排一个"环境体检"模态弹窗（1600ms 后）。测试里必须取消掉，
    #   否则一旦有 update()/mainloop 把定时器跑起来，测试就卡在弹窗上。
    _sc = getattr(c, "_selfcheck_after", None)
    if _sc:
        c.root.after_cancel(_sc)
    check("首次体检的定时弹窗可被取消（自动化里不许挂住）", True)

    # ★★ 2026-09-19 第十二批：默认形态从"横条"改成**悬浮球**。
    #   用户原话「做成悬浮球样式，不然会挡住用户操作」—— 所以"初始就是小球"
    #   成了新的硬契约，而横条宽度降级为"展开时才用得上"的目标值 `c._pill_w`。
    _base_w = max(150, int(round(gui.BASE_W * 1.0)))
    _ball_d = c._ball_d()
    check("★ 默认形态是**圆球**（用户要求：平时别挡着时间线）",
          c.W == int(round(_ball_d)), (c.W, _ball_d))
    check("圆球是正圆（宽==高，圆角==半高）", c.R == (c.W - 2) // 2 or c.R == c.W // 2, c.R)
    check("圆球态默认不悬停不展开", (c._hover, c._expanded) == (False, False),
          (c._hover, c._expanded))
    check("默认形态策略 = 自动收纳", c._ball_mode == "auto", c._ball_mode)
    check("横条目标宽度不小于基准（宽度只跟文案走）", c._pill_w >= _base_w, (c._pill_w, _base_w))
    check("横条目标宽度不超过上限",
          c._pill_w <= int(round(_base_w * gui.UI_MAX_GROW)) + 16, (c._pill_w, _base_w))
    check("初始高度 = 基准（加宽只动宽度，不动高度）", c.H == gui.BASE_H, c.H)
    c._hover = True                      # 悬停 = 展开，才看得到完整文案
    settle()
    check("★ 悬停展开后，常驻副标题「预合成 · 存草稿 · 清空原内容 · 你手动拖入」完整可见（不被裁）",
          c._fs.measure(c._sub) <= c.W - max(20, int(round(36 * c._scale)))
          - max(8, int(round(10 * c._scale))), c._fs.measure(c._sub))
    check("画布元素齐备（球套件 + 横条套件两套都要在）",
          all(getattr(c, k, None) for k in ("bg_item", "dot_item", "txt_item", "sub_item",
                                            "ring_item", "arc_item", "ball_item",
                                            "bar_track", "bar_fill")))
    c._hover = False
    settle()

    print("[2] _build_ui 在不同缩放下重画")
    for s in (0.85, 1.0, 1.25):
        c._scale = s
        c._build_ui()
        want_h = max(32, int(round(gui.BASE_H * s)))
        want_w = max(150, int(round(gui.BASE_W * s)))
        check(f"scale={s} 高度正确（缩放只影响高度基准）", c.H == want_h, (c.W, c.H))
        check(f"scale={s} 收起态窗口宽 = 球径（缩放后仍然是个正圆）",
              c.W == max(32, int(round(gui.BASE_H * s))), (c.W, want_h))
        check(f"scale={s} 横条目标宽度在 [基准, 基准*上限] 内（不再是死值）",
              want_w <= c._pill_w <= int(round(want_w * gui.UI_MAX_GROW)) + 16,
              (c._pill_w, want_w))
        check(f"scale={s} 文案保留", c._txt == c.state[1], c._txt)

    print("[2b] ★★ 文案自适应：任何提示都不许被窗口裁掉（第十一批核心修复）")
    # 这几条是实测最宽的（旧版 232px 窗口只够 188px，最长那条要 377px
    # → 用户根本看不到"该点哪个草稿"）。
    # ★ 第十三批补进来三条**新的两行提示**（主文案=怎么做 / 副文案=为什么）：
    #   用户反馈"提示不够（比如让用户打开草稿等）"，现在同一屏能说两件事，
    #   但两行都得放得下 —— 这里就是"放不下"的红线断言。
    LONG_CASES = [
        ("busy", "请双击草稿「9月18日 (2)」卡片打开… 87s",
         "必须这份 · 换草稿会报「媒体格式不支持」"),
        ("busy", "先关闭剪映（免得它把旧内容覆盖回来）…", "备份当前状态…"),
        ("err", "⚠ 你打开的是「9月18日 (2)」，本流程要的是「9月18日 (2)」",
         "请回首页双击「9月18日 (2)」那张卡片再点我"),
        ("err", "⚠ 草稿备份没做成，导出后无法自动还原", "导出后无法自动还原"),
        ("busy", "等待渲染 87s", "第 4/7 步 · 58%"),
        ("ok", "等你导出", "导出完点我 → 还原草稿（回到预合成前）"),
        ("idle", "一键导出", "预合成 · 存草稿 · 清空原内容 · 你手动拖入"),
        # ★ 第十四批补进来三条：点导出时剪映没开 → 自动等/启动/接着跑，以及
        #   "找不到剪映"时指向新的「设置剪映路径…」入口。副文案都很长，
        #   必须证明在最小缩放下也放得下（放不下就会被裁成省略号）。
        ("busy", "正在启动剪映…", "冷启动可能要几十秒，起来后我自己接着跑"),
        ("err", "剪映没起来", "手动打开后，再点一次「一键导出」"),
        ("err", "找不到剪映", "右键「设置剪映路径…」手动指一下"),
    ]
    for s in (0.85, 1.0, 1.25):
        c._scale = s
        c._build_ui()
        c._hover = True                  # ★ 第十二批：文案只在**展开态**才画出来
        bad = []
        slack_min = None
        for kind, txt, sub in LONG_CASES:
            c.set_state(kind, txt, sub, hold=0)
            settle()
            tx = max(20, int(round(36 * s)))
            pad_r = max(8, int(round(10 * s)))
            avail = c.W - tx - pad_r
            # 界面上**真实显示**的文字（省略号会出现在这里）== 原文 → 说明没被裁
            shown_t = c.cv.itemcget(c.txt_item, "text")
            shown_s = c.cv.itemcget(c.sub_item, "text")
            slack = avail - max(c._f.measure(shown_t), c._fs.measure(shown_s))
            slack_min = slack if slack_min is None else min(slack_min, slack)
            if shown_t != txt or shown_s != sub or slack < 0:
                bad.append((txt[:16], shown_t[:16], slack))
        check(f"scale={s} {len(LONG_CASES)} 条长文案全部完整显示（无省略号、无溢出）",
              not bad, bad or f"最紧余量 {slack_min:+d}px")
        check(f"scale={s} 展开宽度仍在上限内",
              c.W <= int(round(max(150, int(round(gui.BASE_W * s))) * gui.UI_MAX_GROW)) + 16,
              c.W)
        c._hover = False
        settle()

    print("[2c] ★ 抖动抑制：倒计时/百分比来回变时，宽度不该跟着跳")
    c._scale = 1.0
    c._build_ui()
    c._hover = True
    c.set_state("busy", "等待渲染 120s", "第 4/7 步 · 26%", hold=0)
    settle()
    # ★ 量的是**横条目标宽**（`_pill_w`）：收起态的窗口宽恒等于球径，
    #   拿它做"宽度变了几次"的判据会永远只有 1 个值 —— 测试会变成假绿。
    ws = {c._pill_w}
    for left in range(120, 100, -1):
        c.set_state("busy", f"等待渲染 {left}s", "第 4/7 步 · 40%", hold=0)
        settle()
        ws.add(c._pill_w)
    check("倒计时 20 秒内宽度变化次数很少（≤2 次）", len(ws) <= 2, sorted(ws))
    check("抖动期间宽度始终放得下文案（没有为了「稳定」而裁字）",
          c._f.measure(c.cv.itemcget(c.txt_item, "text")) <= c.W - 46, (c.W,))
    c._hover = False
    settle()
    check("抖动期间窗口实际宽度 = 球径（收起态不该被文案拉长）",
          c.W == int(round(c._ball_d())), (c.W, c._ball_d()))

    print("[2d] ★ 第十二批：形态判定（纯函数）+ 球里的字")
    check("idle 且没悬停 → 收成球", not gui.should_expand("idle", False, False, "auto"))
    check("悬停 → 展开", gui.should_expand("idle", False, True, "auto"))
    check("「等你导出」必须自己弹出来（否则用户读不到下一步该干嘛）",
          gui.should_expand("ok", True, False, "auto"))
    check("出错必须自己弹出来", gui.should_expand("err", False, False, "auto"))
    check("要用户操作（ask）必须自己弹出来", gui.should_expand("ask", False, False, "auto"))
    check("busy 且没事 → 收成球（干活时不打扰）",
          not gui.should_expand("busy", False, False, "auto"))
    check("always_pill 模式恒展开（逃生口：兼容旧观感）",
          gui.should_expand("busy", False, False, "always_pill"))
    check("ball_only 模式不自动弹（用户明确要求只留球）",
          not gui.should_expand("err", True, False, "ball_only"))
    check("ball_only 悬停仍可读全文案",
          gui.should_expand("err", True, True, "ball_only"))
    check("球里的字：idle=剪 / ok=✓ / err=× / busy=百分比数字",
          (gui.ball_label("idle", None), gui.ball_label("ok", None),
           gui.ball_label("err", None), gui.ball_label("busy", 87)) == ("剪", "✓", "×", "87"),
          (gui.ball_label("idle", None), gui.ball_label("busy", 87)))
    check("球里的字：百分比越界会被夹到 0~100",
          (gui.ball_label("busy", 250), gui.ball_label("busy", -9)) == ("100", "0"),
          (gui.ball_label("busy", 250), gui.ball_label("busy", -9)))
    check("球里的字：pct 不是数字时退回省略号（不抛异常）",
          gui.ball_label("busy", None) == gui.BALL_TEXT["busy"])
    check("形变：收起 = 宽==高 且 圆角==半高（正圆）",
          gui.morph_shape(False, 232.0, 46.0, 14.0) == (46.0, 23.0),
          gui.morph_shape(False, 232.0, 46.0, 14.0))
    check("形变：展开 = 横条宽 + 小圆角",
          gui.morph_shape(True, 232.0, 46.0, 14.0) == (232.0, 14.0),
          gui.morph_shape(True, 232.0, 46.0, 14.0))
    check("形变：横条比球还窄时也不会缩成负宽（取球径兜底）",
          gui.morph_shape(True, 10.0, 46.0, 14.0)[0] == 46.0)

    print("[2e] ★ 第十二批：真跑一次「悬停展开 → 移开收起」的完整来回")
    c._scale = 1.0
    c._ball_mode = "auto"
    c._await_restore = False
    c._hover = False
    c._build_ui()
    c.set_state("idle", "一键导出", "副标题", hold=0)
    settle()
    w_ball = c.W
    check("① 起点是球", c.W == int(round(c._ball_d())), c.W)
    c._on_enter()                       # 悬停
    check("② 悬停立刻切到「要展开」（形状补间已开）",
          c._expanded and bool(c._anims.get("shape")), (c._expanded, list(c._anims)))
    # 把补间跑完（模拟 16ms 帧循环）
    check("③ 补间真的跑得完（不是卡在 0 帧）", run_anim(), list(c._anims))
    check("④ 补间跑完 → 窗口真的是横条宽", c.W > w_ball and c.W == c._pill_w, (c.W, c._pill_w))
    check("⑤ 横条态圆角是小圆角（不是球）", c.R < c.H // 2, (c.R, c.H))
    c._on_leave()                       # 移开
    check("⑥ 移开的**当场**不许收（防「展开↔收起」自激抖动）", c._expanded, c._expanded)
    check("⑦ 但记下了离开时间戳，交给 _pump 延迟判定", c._leave_at > 0, c._leave_at)
    c._leave_at = time.time() - (gui.BALL_LEAVE_MS / 1000.0 + 0.05)   # 假装已经过了保持期
    c._pump()                           # 走一次延迟判定
    check("⑧ 过了保持期 → 形状补间已开（要收回球）", bool(c._anims.get("shape")), list(c._anims))
    run_anim()
    check("⑨ 收回到位 → 窗口又是球径", c.W == int(round(c._ball_d())), c.W)
    check("⑩ 收回到位后圆角又是半高（正圆）", c.R == (c.W - 2) // 2 or c.R == c.W // 2, c.R)
    c._on_enter()                       # 中途又回来 → 放弃收起
    run_anim()
    c._on_enter()                       # 重新进入 → _leave_at 归零
    check("⑪ 鼠标回来了 → 离开计时清零（不再收起）", c._leave_at == 0.0, c._leave_at)
    c._hover = False
    c._pump()
    check("⑫ 计时清零后 _pump 也不会误收", c._expanded, c._expanded)
    c._hover = False
    c._ball_mode = "auto"
    # ★ 生产里「等你导出」是这么来的（见 `_pump` 的 done 分支）：
    #   `_await_restore = True` + kind="ok"。**不是**靠 kind="ok" 本身触发展开 ——
    #   我第一版测试只写了 kind="ok"，于是它按预期收成球，断言反而红了。
    c._await_restore = True
    c.set_state("ok", "等你导出", "导出完点我 → 还原草稿（回到预合成前）", hold=0)
    settle()
    check("⑬「等你导出」（await_restore）不用悬停也自己展开（流程卡在等用户，字必须读得到）",
          c._expanded and c.W == c._pill_w, (c._expanded, c.W, c._pill_w))
    c._await_restore = False
    c.set_state("idle", "一键导出", "副标题", hold=0)
    settle()
    check("⑭ 回到 idle → 自己收回成球", c.W == int(round(c._ball_d())), c.W)

    print("[2f] ★ 第十二批：三种形态策略点一下就要立刻生效（不是「改了配置要重启」）")
    c._ball_mode = "auto"
    c.set_state("idle", "一键导出", "副标题", hold=0)
    settle()
    _seen = []
    for _ in range(3):
        c._cycle_ball_mode()
        settle()
        _seen.append((c._ball_mode, c.W))
    check("循环顺序 = auto → always_pill → ball_only → auto",
          [m for m, _w in _seen] == ["always_pill", "ball_only", "auto"],
          [m for m, _w in _seen])
    _d = int(round(c._ball_d()))
    check("always_pill 那一档真的当场展开", _seen[0][1] == c._pill_w, _seen[0])
    check("ball_only 那一档真的当场收成球", _seen[1][1] == _d, _seen[1])
    check("回到 auto 且空闲 → 又是球", _seen[2][1] == _d, _seen[2])

    c._scale = 1.0
    c._ball_mode = "auto"
    c._build_ui()

    print("[3] _maybe_rescale（同大共小）：按剪映窗口高 / 工作区高 量化缩放")
    wa = gui.wt.RECT()
    okwa = gui.user32.SystemParametersInfoW(0x0030, 0, byref(wa), 0)
    work_h = (wa.bottom - wa.top) if okwa else 1032
    print(f"      当前工作区高度 = {work_h}")

    # ① 夹取语义（纯函数）：直接喂 jh/ref，不受防抖影响
    for ref, jh, low, high, desc in [
            (work_h, int(work_h * 0.9), 0.85, 1.0, "最大化 → 缩放到 ~1.0"),
            (work_h, int(work_h * 0.7), 0.85, 1.0, "窗口变小 → 跟着变小"),
            (work_h, int(work_h * 0.4), 0.85, 0.85, "很小的窗口 → 夹在下限"),
            (work_h, int(work_h * 2), 1.25, 1.25, "超大 → 夹在上限")]:
        s = gui.rescale_target(jh, ref)
        check(f"rescale_target({jh},{ref})（{desc}）→ {s}", low <= s <= high, s)

    # ② ★ 复现用户日志里那段翻面：jh 恒定 830，参考高在 816/864 之间跳
    #    （864 = 整屏高，任务栏那一刻没占位）。未加护栏时，比例正好横跨
    #    830/864=0.9606→0.95 和 830/816=1.0172→1.00 两个**相邻台阶**。
    raw_pair = {gui.rescale_target(830, 816), gui.rescale_target(830, 864)}
    check("前提：不钉住参考高时，这对读数**确实**会算出两个不同缩放（否则本测试没意义）",
          len(raw_pair) == 2, sorted(raw_pair))
    locked = None
    seq = [816, 864, 816, 864, 816, 864, 816, 864, 816, 864]
    for r in seq:
        locked = gui.stable_ref(r, locked)
    check("stable_ref 把参考高钉死在首个读数上（48px 抖动吃不掉）",
          locked == 816, locked)
    check("钉住之后，同一段 jh 只算得出一个缩放 → 按钮绝不翻面",
          len({gui.rescale_target(830, locked)}) == 1, gui.rescale_target(830, locked))
    check("换屏/改分辨率这种**真实**变化仍要采纳（不能死锁）",
          gui.stable_ref(1020, 816) == 1020 and gui.stable_ref(1440, 816) == 1440)
    check("读失败/离谱值沿用已锁值（不把界面带崩）",
          gui.stable_ref(0, 816) == 816 and gui.stable_ref(None, 816) == 816
          and gui.stable_ref("", 816) == 816)
    check("首次读数直接锁住（locked=None）", gui.stable_ref(816, None) == 816)

    # ③ 端到端：真的调 _maybe_rescale，验证"稳定持续才生效"这道闸
    c._ref_locked = None
    c._cand_s = None
    c._scale = 1.0
    # 先喂两次建立参考高
    c._maybe_rescale(work_h)
    c._maybe_rescale(work_h)
    c._scale = 1.0
    c._cand_s = None
    tiny = int((c._ref_locked or work_h) * 0.4)
    c._maybe_rescale(tiny)
    check("第一次算出新缩放**不立刻生效**（先观察稳不稳）",
          abs(c._scale - 1.0) < 1e-9, c._scale)
    check("已经记下候选缩放", c._cand_s is not None, c._cand_s)
    # 模拟"稳定持续了 RESCALE_HOLD 秒"
    c._cand_t0 = time.time() - (gui.RESCALE_HOLD + 0.5)
    c._maybe_rescale(tiny)
    check("稳定持续够了才真的改（且夹在下限 0.85）",
          abs(c._scale - 0.85) < 1e-9, c._scale)

    c._scale = 1.0
    c._build_ui()
    c._maybe_rescale(0)     # 不该抛
    check("剪映高 0 时不炸", True)

    print("[4] _should_show / _set_shown 不炸")
    c.jy_hwnd = None
    check("无剪映窗口时返回布尔", isinstance(c._should_show(), bool))
    c._set_shown(True)
    check("显示后窗口可见", bool(gui.user32.IsWindowVisible(c.my_hwnd)))
    c._set_shown(False)
    check("隐藏后窗口不可见", not bool(gui.user32.IsWindowVisible(c.my_hwnd)))
    c._set_shown(True)

    print("[5] _float_position 不炸（剪映窗口在就贴过去）")
    win = gui.core.find_jianying(exclude=c.my_hwnd)
    c.jy_hwnd = win[0] if win else None
    print("      剪映窗口 =", win)
    c._float_position(force=True)
    check("定位完成", c._last_float is not None, c._last_float)

    print("[6] _paint 各状态 + _back_to_idle")
    for kind in ("idle", "busy", "ok", "err"):
        c.set_state(kind, f"测试-{kind}", "副标题")
    c._until = time.time() - 1
    c._back_to_idle()
    check("回到 idle 文案", c._txt == "一键导出", c._txt)

    print("[7] 放大后重画，文案/颜色都要保留（回归：重画丢状态）")
    c.set_state("err", "出错了", "看看日志")
    c._scale = 1.25
    c._build_ui()
    check("重画后文案保留", c._txt == "出错了", c._txt)
    check("重画后副标题保留", c._sub == "看看日志", c._sub)
    check("重画后状态点颜色保留", c._dot_color == gui.ERR, c._dot_color)

    print("[8] 菜单：下标登记齐全，且 _sync_menu 必须幂等（回归：改名后 index 抛 TclError）")
    for key in ("extract", "restore", "launch", "embed", "corner",
                "bindhelp", "guard", "autorun", "followfg", "startup", "quit",
                # 2026-09-18 第九批（发给别人用）：这几个入口漏登记 = 用户找不到
                "restorekeys", "selfcheck", "autodraft", "autodrag", "foldertidy",
                # 2026-09-19 第十二批（悬浮球）：形态策略必须能改（有人要一直展开）
                "ballmode"):
        check(f"菜单键 {key} 已登记", key in c._mi, c._mi.get(key))
    # 2026-09-18 晚更新：`restore`（还原草稿）**重新启用**——用户明确要
    # 「导出后再还原（回到没预合成的时候）」，所以它必须挂在菜单上；
    # 但另外两项仍随解密方向一并删除，不许回来。
    for key in ("openout", "autopaste"):
        check(f"已删除的菜单项 {key} 不该再登记", key not in c._mi, c._mi.get(key))
    for i in range(3):                      # 连刷三次，第二次起才是原 bug 的触发点
        try:
            c._sync_menu()
            ok = True
            err = ""
        except Exception as e:
            ok, err = False, e
        check(f"第 {i + 1} 次 _sync_menu() 不抛异常", ok, err)
    check("✓ 会跟着配置走（导出拦截）",
          "✓" in c.menu.entrycget(c._mi["guard"], "label")
          if c.cfg.get("export_guard", True) else "✓" not in c.menu.entrycget(c._mi["guard"], "label"),
          c.menu.entrycget(c._mi["guard"], "label"))
    c.cfg["export_guard"] = False
    c._sync_menu()
    check("关掉后 ✓ 消失", "✓" not in c.menu.entrycget(c._mi["guard"], "label"),
          c.menu.entrycget(c._mi["guard"], "label"))
    c.cfg["export_guard"] = True
    c._sync_menu()
    check("再打开 ✓ 回来", "✓" in c.menu.entrycget(c._mi["guard"], "label"),
          c.menu.entrycget(c._mi["guard"], "label"))
    _cname = {"tr": "右上", "br": "右下", "tl": "左上", "bl": "左下"}
    check("停靠位置标签正确（第十二批：默认改成**右下**）",
          c.menu.entrycget(c._mi["corner"], "label")
          == "停靠位置：" + _cname[c.corner],
          (c.menu.entrycget(c._mi["corner"], "label"), c.corner))
    check("★ 默认停靠角 = 右下（用户原话「自动依附右下角」）", c.corner, "br")
    check("悬浮球模式标签跟着配置走",
          c.menu.entrycget(c._mi["ballmode"], "label")
          == "悬浮球：" + gui.BALL_MODE_LABEL.get(c._ball_mode, "自动收纳"),
          c.menu.entrycget(c._mi["ballmode"], "label"))

    # ---- ★ 进度条（2026-09-18 第八批：给别人用要能看明白在干什么）----
    # ★ 第十二批：进度条是**横条套件**的一部分，收起成球时它跟文案一起隐藏
    #   （球态的进度靠外圈那道弧线表示）。所以这一段必须在**展开态**下量。
    c._ball_mode = "auto"
    c._await_restore = False
    c._hover = True
    settle()

    def _bar_state():
        return (c.cv.itemcget(c.bar_track, "state"), c.cv.itemcget(c.bar_fill, "state"))

    def _fill_w():
        x0, _y0, x1, _y1 = c.cv.coords(c.bar_fill)
        return int(x1 - x0)

    print("[9] 进度条（展开态）")
    c._pct = None
    c._paint("idle")                                   # 不在跑 → 条要藏起来
    check("进度条：不在跑时不显示（平时外观不受影响）",
          _bar_state() == ("hidden", "hidden"), _bar_state())
    c.set_state("busy", "等剪映渲染出产物", "第 4/7 步 · 26%", hold=0, pct=26, step=4)
    settle()
    check("进度条：跑起来时两条都显示", _bar_state() == ("normal", "normal"), _bar_state())
    check("进度条：填充色跟着状态走（busy=黄）",
          c.cv.itemcget(c.bar_fill, "fill") == gui.BUSY, c.cv.itemcget(c.bar_fill, "fill"))
    w26 = _fill_w()
    check("进度条：26% 时填充 ≈ 内宽的 1/4",
          abs(w26 - (c.W - 2 * c._bar_pad) * 0.26) <= 2, w26)
    c.set_state("busy", "等剪映渲染出产物", "第 4/7 步 · 80%", hold=0, pct=80, step=4)
    settle()
    w80 = _fill_w()
    check("进度条：百分比越大填充越长", w80 > w26, (w26, w80))
    c.set_state("busy", "等剪映渲染出产物", "第 4/7 步 · 200%", hold=0, pct=200, step=4)
    settle()
    check("进度条：超过 100% 不会画到按钮外面去",
          _fill_w() <= c.W - 2 * c._bar_pad, _fill_w())
    c.set_state("busy", "等剪映渲染出产物", "第 4/7 步 · -5%", hold=0, pct=-5, step=4)
    settle()
    check("进度条：负数不会反向画", _fill_w() >= 0, _fill_w())
    c.set_state("err", "没成功", "第 6/7 步 · 80%", hold=0, pct=80, step=6)
    settle()
    check("进度条：失败时也藏掉（别显示个 80% 让人以为还在跑）",
          _bar_state() == ("hidden", "hidden"), _bar_state())
    c.set_state("ok", "等你导出", "导出完点我", hold=0)
    settle()
    check("进度条：收尾（ok/err）时自动藏掉，不留半截条误导人",
          _bar_state() == ("hidden", "hidden"), _bar_state())
    c.set_state("busy", "x", hold=0, pct=50, step=2)
    c._until = time.time() - 1          # 假装"停留时间已过"
    c._back_to_idle()
    settle()
    check("进度条：回 idle 时也会清掉百分比（不留残影）", c._pct is None, c._pct)
    check("进度条：回 idle 后条也藏起来了", _bar_state() == ("hidden", "hidden"), _bar_state())

    print("[9b] ★ 第十二批：干活中只留球 + 进度弧（不打扰、也不挡时间线）")
    c._await_restore = False
    c._hover = False
    c.set_state("busy", "等剪映渲染出产物", "第 4/7 步 · 40%", hold=0, pct=40, step=4)
    settle()
    check("① busy 且没悬停 → 收成球", c.W == int(round(c._ball_d())), c.W)
    check("② 球里显示的就是百分比数字（进度不用展开也看得见）",
          c.cv.itemcget(c.ball_item, "text") == "40", c.cv.itemcget(c.ball_item, "text"))
    check("③ 进度弧画出来了（弧形，不是整圈）",
          c.cv.itemcget(c.arc_item, "state") == "normal", c.cv.itemcget(c.arc_item, "state"))
    check("④ 进度弧长度 = 40%（extent = -360*0.4）",
          abs(float(c.cv.itemcget(c.arc_item, "extent")) + 144.0) < 1.0,
          c.cv.itemcget(c.arc_item, "extent"))
    check("⑤ 球态下横条套件（文字/进度条）全隐藏 —— 这才叫不挡事",
          all(c.cv.itemcget(i, "state") == "hidden"
              for i in (c.txt_item, c.sub_item, c.dot_item,
                        c.bar_track, c.bar_fill)))
    c.set_state("busy", "等剪映渲染出产物", "第 4/7 步 · 90%", hold=0, pct=90, step=4)
    settle()
    check("⑥ 进度变了球里的数字跟着变", c.cv.itemcget(c.ball_item, "text") == "90",
          c.cv.itemcget(c.ball_item, "text"))
    check("⑦ 进度弧也跟着变长",
          abs(float(c.cv.itemcget(c.arc_item, "extent")) + 324.0) < 1.0,
          c.cv.itemcget(c.arc_item, "extent"))
    c._hover = True
    c.set_state("busy", "等剪映渲染出产物", "第 4/7 步 · 90%", hold=0, pct=90, step=4)
    settle()
    check("⑧ 悬停 → 球让位、横条全套出来（含底部进度条）",
          c.cv.itemcget(c.txt_item, "state") == "normal"
          and c.cv.itemcget(c.bar_track, "state") == "normal",
          (c.cv.itemcget(c.txt_item, "state"), c.cv.itemcget(c.bar_track, "state")))
    check("⑨ 悬停展开时球套件淡出干净（不叠在文字上）",
          c.cv.itemcget(c.ball_item, "state") == "hidden"
          and c.cv.itemcget(c.ring_item, "state") == "hidden",
          (c.cv.itemcget(c.ball_item, "state"), c.cv.itemcget(c.ring_item, "state")))
    c._hover = False
    c._await_restore = True
    c.set_state("ok", "等你导出", "导出完点我 → 还原草稿（回到预合成前）", hold=0)
    settle()
    check("⑩「等你导出」不悬停也展开（球里塞不下这条指令）",
          c.cv.itemcget(c.txt_item, "state") == "normal", c.cv.itemcget(c.txt_item, "state"))
    c._await_restore = False
    c.set_state("idle", "一键导出", c._idle_sub(), hold=0)
    settle()
    check("⑪ 干完回 idle → 收回球（窗口让开）", c.W == int(round(c._ball_d())), c.W)

    # 阶段表本身：起点必须递增、终点 100、步数与流水线对得上
    _st = core.PIPELINE_STAGES
    check("阶段表：起点递增不回头", all(_st[i][2] <= _st[i + 1][1] for i in range(len(_st) - 1)), True)
    check("阶段表：最后一步到 100%", _st[-1][2] == 100, _st[-1][2])
    check("阶段表：每步区间合法（起<止）", all(lo < hi for _n, lo, hi in _st), True)
    check("阶段表：至少有 5 步（少了看不出进展）", core.PIPELINE_TOTAL_STEPS >= 5, core.PIPELINE_TOTAL_STEPS)
    _rp = inspect.getsource(core.run_pipeline)
    check("run_pipeline 真的在切阶段（stage(N) 调用数 = 步数-1）",
          sum(f"stage({i}" in _rp for i in range(2, core.PIPELINE_TOTAL_STEPS + 1))
          == core.PIPELINE_TOTAL_STEPS - 1, True)
    check("渲染等待走的是会算进度的 st_render（否则进度条干等两分钟）",
          "status_cb=st_render" in _rp, True)
    check("GUI 会把「第 N/共几步 · %」拼进副标题",
          "PIPELINE_TOTAL_STEPS" in inspect.getsource(c._pump), True)

    # ============================================================ 第十四批
    print()
    print("[3] ★ 第十四批：一条坏消息不许杀死 _pump（否则球从此「点了没反应」）")
    _orig_after = c.root.after
    _after_ms = []

    def _rec_after(ms, fn=None, *a):
        _after_ms.append(ms)
        return _orig_after(ms, fn, *a)

    c.root.after = _rec_after
    try:
        c.q.put(("status",))                 # 缺字段 → 处理时必然抛（ValueError）
        c._pump()                            # ★ 不许把异常抛出来
        check("① 坏消息没把 _pump 打穿", True)
        check("② ★ 坏消息之后照样重新排了下一轮（after 120ms）", 120 in _after_ms, _after_ms)
        _after_ms.clear()
        c.q.put(("status", "ok", "循环还活着", None, None, None))
        c._pump()
        check("③ ★ 循环**真的还活着**：下一条正常消息照样被处理",
              c.state == ("ok", "循环还活着"), c.state)
        check("④ 正常消息也重新排了下一轮", 120 in _after_ms, _after_ms)
    finally:
        c.root.after = _orig_after

    print()
    print("[4] ★ 第十四批：剪映没开时点「一键导出」= 自动等/启动/接着跑（不用再点一次）")
    _ow, _ol, _orun = core.wait_jianying, core.launch_jianying, core.run_pipeline
    _odone = c._on_done
    _jy = Path("X:/Fake/JianyingPro.exe")

    def _wire(wait_results, launch_ok=True):
        calls = []

        def _fwait(timeout, exclude=0, interval=0.6, cancel=None):
            calls.append(("wait", timeout))
            return wait_results.pop(0) if wait_results else None

        def _flaunch(cfg=None):
            calls.append(("launch",))
            return _jy if launch_ok else None

        def _frun(cfg, on_status, on_done, cancel=None):
            calls.append(("run",))

        core.wait_jianying, core.launch_jianying, core.run_pipeline = _fwait, _flaunch, _frun
        return calls

    try:
        # ① 剪映确实没开：探测 → 启动 → 等到 → 接上流程
        calls = _wire([None, (777, "剪映")])
        c.jy_hwnd = None
        c._launch_then_run()
        check("① 顺序 = 探测 → 启动 → 再等 → 接上流程",
              [k for k, *_ in calls] == ["wait", "launch", "wait", "run"], calls)
        check("① 探测窗口短、启动后等待长",
              (calls[0][1], calls[2][1]) == (gui.LAUNCH_PROBE_SECS, gui.LAUNCH_WAIT_SECS), calls)
        check("① 接上流程前记下剪映 hwnd", c.jy_hwnd == 777, c.jy_hwnd)

        # ② 剪映**正在启动**（用户刚点过桌面图标）→ 绝不再 Popen 一个
        calls = _wire([(888, "剪映")])
        c._launch_then_run()
        check("② 已经在启动 → 不重复拉起（没有 launch）",
              [k for k, *_ in calls] == ["wait", "run"], calls)
        check("② 这种时候也记下 hwnd", c.jy_hwnd == 888, c.jy_hwnd)

        # ③ 起不来：不报笼统「未完成」，直说手动打开 + 再点一次
        _done = []
        c._on_done = lambda ok, msg, rescue=False: _done.append((ok, msg, rescue))
        calls = _wire([None, None])
        c._launch_then_run()
        check("③ 超时走 rescue='launch'", _done and _done[-1][:1] == (False,), _done)
        check("③ 文案说清「手动打开 + 再点一次」",
              "手动打开" in _done[-1][1] and "再点一次" in _done[-1][1], _done[-1][1])

        # ④ 连 exe 都找不到：也给 rescue='launch'，并指向手动路径入口
        _done.clear()
        calls = _wire([None], launch_ok=False)
        c._launch_then_run()
        check("④ 找不到 exe 也走 rescue='launch'", _done[-1][2] == "launch", _done)
        check("④ 指向「设置剪映路径…」（文案提到的入口菜单里必须有）",
              "设置剪映路径" in _done[-1][1], _done[-1][1])
    finally:
        core.wait_jianying, core.launch_jianying, core.run_pipeline = _ow, _ol, _orun
        c._on_done = _odone

    print()
    print("[5] ★ 第十四批：rescue='launch' 不许落进「已就位 / 未完成」")
    _onb = core.notify_box
    core.notify_box = lambda *a, **k: None      # 否则 Win32 模态框把测试挂死
    try:
        for _ok, _want in ((False, ("err", "剪映没起来")), (True, ("ok", "剪映已启动"))):
            c.q.put(("done", _ok, "测试用文案", "launch"))
            c._pump()
            check(f"① rescue=launch ok={_ok} → {_want[1]}", c.state == _want, c.state)
        # 反向对照：老路径不许被带坏
        c.q.put(("done", False, "x", "restore"))
        c._pump()
        check("② 反向对照：rescue='restore' 仍然报「未还原」",
              c.state == ("err", "未还原"), c.state)
        c.q.put(("done", True, "x", False))
        c._pump()
        check("③ 反向对照：正常完成仍进「等你导出」并置 await_restore",
              c.state == ("ok", "等你导出") and c._await_restore,
              (c.state, c._await_restore))
    finally:
        core.notify_box = _onb
        c._await_restore = False

    print()
    print("[6] ★ 第十四批：手动指定剪映路径（分发病因第 1 条「路径假设」的兜底）")
    import tkinter.filedialog as _tfd
    import tkinter.messagebox as _tmb
    _oaof, _omb, _osave = _tfd.askopenfilename, _tmb.askyesno, core.save_config
    _saved, _old_jx = [], c.cfg.get("jianying_exe", "")
    try:
        core.save_config = lambda cfg: _saved.append(dict(cfg))
        _tfd.askopenfilename = lambda **k: r"D:\我的工具\剪映\JianyingPro.exe"
        c._pick_jy_exe()
        check("① 选中的路径写进 cfg['jianying_exe']",
              c.cfg["jianying_exe"] == r"D:\我的工具\剪映\JianyingPro.exe",
              c.cfg.get("jianying_exe"))
        check("① 立刻落盘（不然下次开机又找不到）", bool(_saved), len(_saved))
        check("① 菜单上会报出文件名（设错了要看得见）",
              "JianyingPro.exe" in c.menu.entrycget(c._mi["jypath"], "label"),
              c.menu.entrycget(c._mi["jypath"], "label"))
        check("① 自动查找会优先认它（文件不存在的假路径则自动回落）",
              core.find_jianying_exe({"jianying_exe": r"Z:\不存在\JianyingPro.exe"})
              != Path(r"Z:\不存在\JianyingPro.exe"), True)

        # ② 选了个明显不是剪映的 exe → 问一句；用户说"不"就不改
        c.cfg["jianying_exe"] = r"D:\old\JianyingPro.exe"
        _tmb.askyesno = lambda *a, **k: False
        _tfd.askopenfilename = lambda **k: r"C:\Windows\notepad.exe"
        c._pick_jy_exe()
        check("② 选错文件、用户否认 → 原设置不动",
              c.cfg["jianying_exe"] == r"D:\old\JianyingPro.exe", c.cfg.get("jianying_exe"))

        # ③ 取消对话框 → 问要不要清空；同意才清
        _tfd.askopenfilename = lambda **k: ""
        _tmb.askyesno = lambda *a, **k: True
        c._pick_jy_exe()
        check("③ 取消 + 同意 → 清回自动查找", c.cfg["jianying_exe"] == "", c.cfg.get("jianying_exe"))

        # ④ 取消 + 不同意 → 保持不动（别把用户设置误清）
        c.cfg["jianying_exe"] = r"D:\keep\JianyingPro.exe"
        _tmb.askyesno = lambda *a, **k: False
        c._pick_jy_exe()
        check("④ 取消 + 不同意 → 保持原样",
              c.cfg["jianying_exe"] == r"D:\keep\JianyingPro.exe", c.cfg.get("jianying_exe"))
    finally:
        core.save_config = _osave
        _tfd.askopenfilename, _tmb.askyesno = _oaof, _omb
        c.cfg["jianying_exe"] = _old_jx
        try:
            c._sync_menu()
        except Exception:
            pass

    print()
    print("[7] ★ 第十五批：手动指定草稿目录（同一个坑的另一半）+ 体检不许假绿")
    import shutil
    import tempfile
    _oad, _omb2 = _tfd.askdirectory, _tmb.askyesno
    _osave2 = core.save_config
    _saved2 = []
    _old_dr = c.cfg.get("draft_root", "")
    _dtmp = Path(tempfile.mkdtemp(prefix="gui_draftroot_"))
    _real = _dtmp / "真草稿根"
    (_real / "草稿A" / "Resources" / "combination").mkdir(parents=True)
    (_real / "草稿A" / "Resources" / "combination" / "A_video.mp4").write_bytes(b"x")
    _parent = _dtmp / "上一级"
    (_parent / "com.lveditor.draft" / "草稿B" / "Resources" / "combination").mkdir(parents=True)
    (_parent / "com.lveditor.draft" / "草稿B" / "Resources" / "combination"
     / "B_video.mp4").write_bytes(b"x")
    _empty = _dtmp / "空目录"
    _empty.mkdir()
    try:
        core.save_config = lambda cfg: _saved2.append(dict(cfg))
        # ★★ 先给两个对话框都打上桩**再**开测（第十五批真踩到）：
        #   `_pick_draft_root` 在"认不出预合成产物"时会调 `askyesno` —— 那是**真模态框**，
        #   没有 mainloop 的脚本里会**永久挂死**（实测卡满 2 分钟超时，日志停在上一行）。
        #   这条和"首次体检的模态弹窗要把 after id 存下来"是同一类坑：
        #   **自动化里任何一个没打桩的模态框 = 挂死**。
        _tmb.askyesno = lambda *a, **k: False

        # ① 选对了一个"里面真有预合成产物"的目录
        _tfd.askdirectory = lambda **k: str(_real)
        c._pick_draft_root()
        check("① 选中的目录写进 cfg['draft_root']", c.cfg["draft_root"] == str(_real),
              c.cfg.get("draft_root"))
        check("① 立刻落盘", bool(_saved2), len(_saved2))
        check("① 菜单上会报出**实际路径**（不是无用的 com.lveditor.draft）",
              str(_real) in c.menu.entrycget(c._mi["draftroot"], "label"),
              c.menu.entrycget(c._mi["draftroot"], "label"))

        # ② 默认位置要显示成「默认位置」（人人同名，报名字等于没报）
        try:
            _std0 = str(core.standard_draft_roots()[0])
        except Exception:
            _std0 = ""
        c.cfg["draft_root"] = _std0
        c._sync_menu()
        check("② 是默认位置时标签写「默认位置」",
              "默认位置" in c.menu.entrycget(c._mi["draftroot"], "label"),
              c.menu.entrycget(c._mi["draftroot"], "label"))
        c.cfg["draft_root"] = str(_real)
        c._sync_menu()

        # ③ 手滑选了上一级 → 自动往下走一层
        _tfd.askdirectory = lambda **k: str(_parent)
        c._pick_draft_root()
        check("③ 选到 …\\上一级 → 自动下钻到里面的 com.lveditor.draft",
              c.cfg["draft_root"] == str(_parent / "com.lveditor.draft"),
              c.cfg.get("draft_root"))

        # ④ 认不出预合成产物 → 问一句；用户否认就不改
        c.cfg["draft_root"] = str(_real)
        _tmb.askyesno = lambda *a, **k: False
        _tfd.askdirectory = lambda **k: str(_empty)
        c._pick_draft_root()
        check("④ 认不出内容、用户否认 → 原设置不动", c.cfg["draft_root"] == str(_real),
              c.cfg.get("draft_root"))
        #   反过来：用户确认要用，就照办（有人确实只是还没做过预合成）
        _tmb.askyesno = lambda *a, **k: True
        c._pick_draft_root()
        check("④b 用户确认要用 → 就照办（不能拦死没做过预合成的人）",
              c.cfg["draft_root"] == str(_empty), c.cfg.get("draft_root"))

        # ⑤ 取消 + 同意 → 清回自动查找；取消 + 不同意 → 保持
        _tfd.askdirectory = lambda **k: ""
        _tmb.askyesno = lambda *a, **k: True
        c._pick_draft_root()
        check("⑤ 取消 + 同意 → 清回自动查找", c.cfg["draft_root"] == "", c.cfg.get("draft_root"))
        c.cfg["draft_root"] = str(_real)
        _tmb.askyesno = lambda *a, **k: False
        c._pick_draft_root()
        check("⑤b 取消 + 不同意 → 保持原样", c.cfg["draft_root"] == str(_real),
              c.cfg.get("draft_root"))

        # ⑥ 体检：指到一个"存在但空"的目录时**不许报 ok**
        #   ★ 备份目录要指到临时目录，否则这条会在**真桌面**上建文件夹写探针文件。
        _bd_bak2 = core.backup_root_dir
        core.backup_root_dir = lambda: _dtmp / "bk"
        try:
            _rows = core.env_selfcheck({"draft_root": str(_empty)}, quick=False)
        finally:
            core.backup_root_dir = _bd_bak2
        _r = [r for r in _rows if r[1].startswith("草稿目录")]
        check("⑥ ★ 体检对\"存在但没内容\"的目录报 warn（以前会报 ok）",
              bool(_r) and _r[0][0] == "warn", _r[:1])
        check("⑥ 而且正文指路到菜单入口", bool(_r) and "设置草稿目录" in _r[0][2], _r[:1])
    finally:
        core.save_config = _osave2
        _tfd.askdirectory, _tmb.askyesno = _oad, _omb2
        c.cfg["draft_root"] = _old_dr
        try:
            c._sync_menu()
        except Exception:
            pass
        try:
            shutil.rmtree(_dtmp, ignore_errors=True)
        except Exception:
            pass

    print()
    print("[10] ★ 第十七批（用户\"用户可以暂终止，终止时自动还原\"）：运行中能停 + 收尾别误报")
    _oask10 = gui.ask_yes
    _orb10, _onb10 = core.run_pipeline, core.notify_box
    core.notify_box = lambda *a, **k: None      # 模态框会把无人值守的测试挂死
    _run10 = []
    try:
        core.run_pipeline = lambda *a, **k: _run10.append(k.get("cancel"))

        # ① 空闲：没有令牌 → 入口置灰，点了也不乱动（不做"点了没反应"的死入口）
        c._cancel = None
        c._await_restore = False
        c.state = ("idle", "一键导出")
        c._sync_menu()
        check("① 空闲时「中止」置灰", c.menu.entrycget(c._mi["abort"], "state") == "disabled",
              c.menu.entrycget(c._mi["abort"], "state"))
        check("① 空闲时点它什么也不做", c._abort_pipeline() is False)

        # ② 起一轮流程 → 令牌挂上、菜单亮起、令牌透传给内核
        c._start_pipeline()
        _tok10 = c._cancel
        check("② 起流程就挂上令牌", _tok10 is not None, _tok10)
        check("② 令牌透传给了内核（内核靠它收手）",
              bool(_run10) and _run10[-1] is _tok10, _run10)
        check("② 菜单里「中止」亮起", c.menu.entrycget(c._mi["abort"], "state") == "normal",
              c.menu.entrycget(c._mi["abort"], "state"))
        check("② 按钮文案 = 执行中", c.state == ("busy", "执行中…"), c.state)

        # ③ 运行中左键 → 先弹确认；用户点「否」→ 不许中止（防误触）
        gui.ask_yes = lambda *a, **k: False
        c._click()
        check("③ 运行中左键会弹确认（用户否认 → 不中止）", _tok10.is_set() is False)
        check("③ 否认后仍是执行中", c.state == ("busy", "执行中…"), c.state)

        # ④ 用户点「是」→ 令牌置位
        gui.ask_yes = lambda *a, **k: True
        c._click()
        check("④ ★★★ 运行中左键 = 中止（原来这里直接 return，跑起来就是死键）",
              _tok10.is_set(), True)

        # ⑤ 中止的收尾：必须是「已中止」，**不许**落进「等你导出」
        c.q.put(("done", True, "已中止，并已还原", "abort"))
        c._pump()
        check("⑤ ★★★ abort 的收尾不是「等你导出」（都是 ok=True，混了就是最糟的误报）",
              c.state != ("ok", "等你导出"), c.state)
        check("⑤ 报的是「已中止」", c.state == ("ok", "已中止"), c.state)
        check("⑤ 令牌清掉了（不然下一轮刚起步就被中止）", c._cancel is None, c._cancel)
        check("⑤ 没置 await_restore（中止 ≠ 等你导出）", not c._await_restore)

        # ⑥ 反向对照：正常跑完还是「等你导出」
        c.q.put(("done", True, "正常", False))
        c._pump()
        check("⑥ 反向对照：正常完成仍是「等你导出」并置 await_restore",
              c.state == ("ok", "等你导出") and c._await_restore,
              (c.state, c._await_restore))

        # ⑦ 等待期（等你导出）也能中止 = 放弃本轮 → 走还原
        _hit10 = []
        c._restore_draft = lambda: _hit10.append(1)
        try:
            check("⑦ 等待期「中止」= 放弃本轮并还原",
                  c._abort_pipeline() and bool(_hit10), _hit10)
        finally:
            c._restore_draft = lambda: None
            c._await_restore = False

        # ⑧ 等剪映启动那一段也挂令牌（冷启动 60s 也要能停）
        class _NoThread10:
            def __init__(self, *a, **k):
                pass

            def start(self):
                pass

        _oth10 = gui.threading.Thread
        gui.threading.Thread = _NoThread10
        try:
            c._cancel = None
            c._launch_jy(then_run=True)
            check("⑧ 等剪映启动那 60s 也挂令牌（能中止）", c._cancel is not None, c._cancel)
        finally:
            gui.threading.Thread = _oth10
            c._cancel = None
    finally:
        gui.ask_yes = _oask10
        core.run_pipeline = _orb10
        core.notify_box = _onb10
        c._await_restore = False
        c._cancel = None
finally:
    try:
        if c:
            c.root.destroy()
            print("（已销毁测试窗口）")
    except Exception as e:
        print("销毁异常", e)

print()
print("[8] ★ 第十六批：安装时那次「自动配置」真跑一遍（`--autosetup` 的落点）")
import contextlib  # noqa: E402
import io  # noqa: E402
import json as _json  # noqa: E402
import tempfile as _tf8  # noqa: E402

#   这段是**行为级**：安装包装完会调 `剪映伴侣.exe --autosetup`，
#   这里就照原样调一次 gui.autosetup_cli()，看它到底有没有把配置写好、结果落下来。
#   ★ 必须先把 config_path 指到临时目录 —— 否则会覆盖作者本机**真在用的**配置。
_keep8 = (core.config_path, core.find_jianying_exe, core.jianying_setting_draft_path,
          core._pick_standard_root)
_t8 = Path(_tf8.mkdtemp(prefix="autosetup8_"))
_cf8 = _t8 / "伴侣配置.json"
_dr8 = _t8 / "drafts"
_dr8.mkdir(parents=True)
core.config_path = lambda: _cf8
core.find_jianying_exe = lambda cfg=None: Path("C:/Fake/JianyingPro.exe")
core.jianying_setting_draft_path = lambda: _dr8
core._pick_standard_root = lambda *a, **k: None
_buf8 = io.StringIO()
try:
    with contextlib.redirect_stdout(_buf8):
        _rc8 = gui.autosetup_cli()
    _raw8 = (_t8 / core.AUTOSETUP_RESULT).read_bytes()
    _cfg8 = _json.loads(_cf8.read_text(encoding="utf-8"))
finally:
    (core.config_path, core.find_jianying_exe, core.jianying_setting_draft_path,
     core._pick_standard_root) = _keep8
check("① 返回 0（安装流程不因它失败）", _rc8 == 0, _rc8)
check("② ★ 结果文件落下了，首行是 ASCII 标记（Inno 靠它分支）",
      _raw8.split(b"\r\n", 1)[0], b"JIANYING=1")
check("③ ★★ 配置真的写好了 —— 用户装完直接能用，不用点任何菜单",
      (_cfg8.get("jianying_exe"), _cfg8.get("draft_root")),
      ("C:\\Fake\\JianyingPro.exe", str(_dr8)))
check("④ ★ 落盘只有白名单那几个键（不许把内置默认值抄成文件）",
      sorted(_cfg8.keys()), sorted(core.AUTOSETUP_KEYS))
check("⑤ 输出里有给日志/安装包看的中文结论", "剪映：已找到" in _buf8.getvalue(), True)
check("⑥ 记账位写上了（体检据此说「你不用做任何事」）",
      _cfg8.get("_autosetup_done"), True)
try:
    shutil.rmtree(_t8, ignore_errors=True)
except Exception:
    pass

print()
print("失败项:", fails if fails else "无")
sys.exit(1 if fails else 0)
