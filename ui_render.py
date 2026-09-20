# -*- coding: utf-8 -*-
"""悬浮窗的**渲染层**（第二十三批）：用 PIL 画、用 `UpdateLayeredWindow` 上屏。

════════════════════════════════════════════════════════════════════════
为什么必须换掉旧的渲染方式（用户："ui 还是丑，看着廉价"）
════════════════════════════════════════════════════════════════════════
旧版 = Tk `Canvas` 上摆一堆图元（多边形/弧/文字） + 窗口用 `-transparentcolor` 抠色键。
两条天花板都是**渲染机制**决定的，不是调参能救的：

  ① **没法抗锯齿**。Tk 的 canvas 画多边形和弧线**完全不做 AA**，边缘是硬台阶 ——
     48px 的圆放大 6 倍看就是一圈楼梯（`shots/_edge_zoom.png` 实测）。
     "自己渲染一张带 alpha 的图贴上去"也不行：色键窗口下半透明像素会跟品红
     **混成紫边**（`docs/ui-preview.png` 里那圈紫边就是这么来的 —— 我一开始
     以为是真机漏色，量了像素才发现是预览脚本自己造的）。
  ② **没法做半透明**。色键是"颜色精确匹配"，非键色像素一律**实心不透明** ——
     做不出"把桌面压暗 40%"的投影，也做不出毛玻璃。

`UpdateLayeredWindow` 换了一条路：整窗外观由我们提供的 32 位 BGRA 位图决定，
**alpha 是真的**（`_probe_layered2.py` 实测：50% 绿块上屏后是"绿混桌面"、
透明处透出桌面、投影真的把桌面压暗了）。并且实测：
  · 用 ULW 之后 **Tk 自己画的东西不再参与合成**（不会盖掉我们的位图）；
  · 鼠标命中**按 alpha 走**（全透明处 `WindowFromPoint` 返回底下的窗口 = 自动穿透）。
唯一约束：**顶层窗里不能放子窗口**（Tk 的 Canvas 就是子窗口，会直接画在位图上面）。
所以本层的契约是——**一张 PIL 图 = 整个窗口**，事件绑在顶层窗自己身上。

════════════════════════════════════════════════════════════════════════
设计语言：从"平涂 + 亮描边"改成"柔和玻璃"
════════════════════════════════════════════════════════════════════════
旧版廉价感的三个来源，逐个对治：
  · **一整圈均匀的亮描边**（`outline=mix(SURFACE,#fff,0.13)`）配上硬台阶边缘
    = 剪贴画感。现在改成 **上亮下无的内壁高光**（只沿顶部 1px、向下渐隐）
    + **外圈 1px 极淡暗边**（浅底上定形）。不描轮廓，只交代受光方向。
  · **平涂**：整个面一个色，像默认控件。现在改成**极轻的竖向渐变**
    （#313138 → #212126），肉眼看不出"渐变"，但会读成"有厚度"。
  · **没有投影**：悬浮物没影子就"贴"在屏幕上。现在是**真·高斯投影** ——
    深底上几乎看不见（不脏），浅底上把桌面自然压暗一圈（浮起来）。
强调色的用法也收敛了：不再用饱和色铺满圆盘（那在暗底上会**发浑**、
浅底上像一块污渍），改成 **中性白 10% 的圆盘 + 强调色只出现在字符/细弧/细条上**。

════════════════════════════════════════════════════════════════════════
三个必须记住的坑（都踩过，改之前先读）
════════════════════════════════════════════════════════════════════════
  1. **`ImageDraw` 的"混合"只对 RGB 底生效**。对 RGBA 图调 `Draw(im, "RGBA")`
     画一个 `(255,255,255,24)` 的圆盘，结果是**替换**（像素被直接抹成半透明 =
     挖了个洞），圆盘会显示成一块发亮的脏色 —— 这个坑我踩了两次：
     第一次没传 mode，第二次传了 mode 仍然没用（因为底图还是 RGBA）。
     所以本文件的规矩是：**内容一律画在 RGB 底上**（混合生效），
     最后再用 `paste(..., mask)` 把形状遮罩贴成 RGBA。
  2. **降采样必须在"预乘"状态做**。形状外的像素是 `(0,0,0,0)`，
     面积平均出来的边缘像素 RGB = 覆盖率 × 本体色、A = 覆盖率 × 255，
     正好是数学上正确的预乘表示（先按遮罩 `paste` 再 `resize`）。
     反过来先把形状外的 RGB 填成实色再缩，AA 边缘就会带上"本不该存在"的颜色。
  3. **不能每帧重建整条流水线**：超采样让像素量变成 SS² 倍
     （238x48 的胶囊 = 1024x264 = 27 万像素），一次全画布 `alpha_composite`
     就是 2.6ms。所以：剪影只在 SS 上合成一遍；投影**降到 1x 才做**
     （它本来就是模糊的，在 SS 上算纯属浪费：2.4ms → 0.2ms）；
     降采样用 `BOX`（面积平均 = 精确覆盖率，还比 LANCZOS 快一倍）。
     实测冷渲染：球 ~4ms · 胶囊 ~13ms · 卡片 ~38ms（卡片是静态的，只弹一次；
     淡入的三帧共用同一份缓存，只在小图上缩 alpha）。
"""
import ctypes
import math
import sys
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageSequence

# ---------------------------------------------------------------- 设计标尺
SHADOW_PAD = 10             # 投影留白：窗口比"形状"四周各大这么多（1x 尺寸）
#   ★★ 留白 7 → 10（2026-09-20 第二十四批，用户："ui 还是丑，看着廉价"）。
#      历史：最初 9，为了少"吃点击"收到 7。**这个取舍算错了一半** —— 收小留白
#      并没有真的少吃点击，只是把投影**切掉**了：
#        有效模糊 σ = SHADOW_BLUR × 0.5 = 3px，而留白只有 7px。
#        在 7px 处 140 × exp(-49/18) ≈ **9**（不是 0）→ 窗口边界上投影还是 9/255 的
#        灰，被窗口矩形**硬切一刀**。屏幕上就是"控件周围一圈脏晕、外面还有一条直边"，
#        正是"廉价感"的来源之一。
#      现在两条一起改：留白放大到 10（够投影自然衰减到 0），并且在贴遮罩前把
#      alpha < 2 的一律归零（`_render` 里）—— 于是**真的 0**，边界不再硬切。
#      吃点击的环宽 = "投影 alpha > 0 的那一圈"，现在它比 7px 时代**更窄**：
#      7px 时代圈内到最外沿 alpha 一直是 9~70；现在约 9px 处就已经是 0 了。
SHADOW_BLUR = 7.2           # 投影模糊半径（1x 有效 σ = 本值 × 0.5 ≈ 3.6px）
SHADOW_ALPHA = 112          # 投影峰值 alpha（原来 140 —— 在暗底上就是一圈黑脏印）
SHADOW_DY = 3.0             # 投影下移（光源在上）。原 2.0 太"贴"，看着像描边不像浮起来
SS = 4                      # 超采样倍率（4x 渲染再缩回 = 抗锯齿）

SURF_TOP = (68, 68, 79)     # 形状渐变顶色
SURF_BOT = (36, 36, 43)     # 形状渐变底色
#   ★★ 形状配色整体提亮（2026-09-20 第二十四批）。旧值 (46,46,54)→(30,30,35)
#      和剪映暗色 UI 的底色 (26,26,28) **几乎同色** —— 把球贴到剪映上，
#      下半圈直接"消失"（证据：`shots/v_bg_dark.png` 里四颗球只剩一团模糊暗影，
#      只有橙色的 40 环还看得见）。提亮之后顶色比暗底亮 42 级、比亮壁纸暗得多，
#      在**深底和浅底上都能站住**。渐变跨度也从 16 拉到 32（更像"球面"而不是"贴纸"）。
RIM_A = 74                  # 内壁高光峰值 alpha（只出现在顶部一两像素上）
EDGE_A = 104                # 外圈 1px 暗边 alpha（上半部分会再压一层亮线）
HAIR_A = 96                 # ★ 外圈顶部亮发丝线峰值 alpha（"在深底上分离"靠它）

#   卡片底：比悬浮球**再亮一档**（球是"按钮"、卡是"纸"）—— 靠**明度分层**，
#   不靠描边。抽成常量是为了让测试能直接对着数断言（别去断言源码里的字面量）。
CARD_TOP = (76, 76, 86)
CARD_BOT = (44, 44, 52)

LABEL = (245, 245, 247)
LABEL2 = (156, 156, 163)
TRACK = (255, 255, 255, 46)     # 进度**条**轨道：旧值 30 在暗底上看不见 → 抬到 46
GROOVE = (0, 0, 0, 88)          # 进度**环**的凹槽：**暗色**而不是亮色 ——
                                #   球面提亮之后，亮色轨道变成"第二个外圈"，和球边
                                #   那条发丝线叠在一起像"双边框"。改成暗槽才读得出
                                #   "这是一个凹进去的进度环"（表盘感），也和亮橙弧拉开
DISC = (255, 255, 255, 34)      # 图标圆盘：**中性白**（不是强调色，见上）
BTN_HOT = 22                    # 确认卡按钮悬停底色：白色 alpha

# 状态强调色（比 iOS 系统色**降饱和**一档：饱和色铺在大面积上会显廉价）
ACCENT = {
    "idle": (152, 152, 160),
    "busy": (255, 170, 68),
    "ok": (54, 202, 104),
    "err": (255, 98, 88),
    "ask": (72, 156, 255),
}

SZ_BALL = 20        # 球里的字（idle/ok/err/ask）
SZ_PCT = 16         # 球里的百分比数字
SZ_TITLE = 13       # 胶囊主文案
SZ_SUB = 11         # 胶囊副文案
SZ_DISC = 13        # 胶囊图标字符
SZ_CARD_T = 14      # 卡片标题
SZ_CARD_B = 12      # 卡片正文
SZ_CARD_I = 13      # 卡片图标字符
CARD_BTN_H = 40     # 卡片按钮行高（1x）。见 card_buttons() 的注释：命中矩形与
                    #   `_do_card` 里画的那条分隔线必须同源，所以抽成常量。

F_REG = r"C:\Windows\Fonts\msyh.ttc"
F_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
# ★★ 符号回退字体（Segoe UI Symbol）：**雅黑里没有 ✓ ⚠ ℹ ⇒ ↔ ▶ ☰ 这些符号**。
#   为什么以前不出问题：旧的 Tk/GDI 路径会自己做"字体链接"（缺字自动换字体），
#   而 PIL/FreeType **不做回退** —— 缺字直接画 .notdef（一个方框）。
#   实测：把 `BALL_TEXT["ok"]="✓"` 交给 PIL，屏幕上就是一条**绿色方框**。
#   seguisym 覆盖了全部需要的符号，而且是**单色**的 —— 跟暗色扁平设计一致
#   （不能用 seguiemj 的彩色 emoji：卡片上蹦出一个彩色 ✅ 立刻显廉价）。
F_SYM = r"C:\Windows\Fonts\seguisym.ttf"
_VS = "\ufe0e\ufe0f"      # 变体选择符：零宽修饰符，留着只会多画一个豆腐块


# ================================================================ 字体
class PFont:
    """PIL 字体 + `.measure()` 适配器：**量出来的宽度就是画出来的宽度**。

    ★ 为什么要包一层：全项目的宽度计算链（`ui_metrics` → `_fit_width` →
      `ellipsize` / `wrap_lines`）都调 `font.measure(text)`，而 Tk 的
      `tkfont.Font` 恰好有这个方法 —— 包一层之后调用点和测试一行不用改，
      但度量换成了和渲染**同一个 FreeType 字体、同一个字号**，不会再出现
      "量着放得下、画出来被切"的偏差。
    ★ 顺带解决一个老坑：Tk 的 point 字号会跟着系统 DPI 自己缩放，而本程序
      是**自己算缩放**的（`self._scale`），两边各缩一次就会打架。px 是死的。
    """

    def __init__(self, path, px):
        self.px = int(px)
        self.path = path
        self._f = ImageFont.truetype(path, self.px)
        self._a, self._d = self._f.getmetrics()
        self._sym = _sym_face(self.px)

    def face(self, ch):
        """这个字符该用哪个字体画：主字体缺字 → 回退到符号字体。"""
        if self._sym is not None and _missing(self._f, ch):
            return self._sym
        return self._f

    def runs(self, text):
        """按"用哪个字体"把文本切成若干段（相邻同字体的合并），变体选择符丢弃。"""
        out = []
        for ch in str(text):
            if ch in _VS:
                continue
            f = self.face(ch)
            if out and out[-1][1] is f:
                out[-1][0] += ch
            else:
                out.append([ch, f])
        return out

    def measure(self, text):
        """★ 逐段累加：回退出去的那些符号宽度**必须算进去** ——
        否则 `_fit_width` 算出来的宽度和实际画出来的宽度会不一致（又会裁字）。"""
        try:
            return int(math.ceil(sum(f.getlength(seg) for seg, f in self.runs(text))))
        except Exception:
            return int(math.ceil(self._f.getlength(str(text))))

    def metrics(self, key):
        return {"linespace": self._a + self._d, "ascent": self._a,
                "descent": self._d}.get(key, self._a + self._d)

    @property
    def font(self):
        return self._f


_FONTS = {}
_SYM_FACES = {}
_NOTDEF = {}
_MISSING = {}


def _sym_face(px):
    """符号回退字体（打不开就返回 None，退回"只有主字体"的老行为）。"""
    px = max(6, int(round(px)))
    if px not in _SYM_FACES:
        try:
            _SYM_FACES[px] = ImageFont.truetype(F_SYM, px)
        except Exception:
            _SYM_FACES[px] = None
    return _SYM_FACES[px]


def _notdef(f):
    """这个字体的 .notdef（缺字）**像素长什么样** —— 拿它当缺字指纹。

    ★ 指纹必须取"位图字节"（`bytes(f.getmask(...))`），**不能取 `getbbox`**。
      `getbbox` 版踩过一个很难看的坑：雅黑 px=20 时，缺字方框的
      `(轮廓, 步进) = ((0,7,14,22), 14.0)`，而**字母 `A` 一模一样** ——
      于是一屏正常英文里所有 `A` 都被判成缺字、整段被换字体画。
      位图字节不会撞：方框是个空心矩形，`A` 是字母，像素差得远。
      实测跨 11/14/20/32/48 五个字号：只会命中 `✓ ⚠ ⇒ ▶ ☰ ⓪`，
      `A B a 0 中 空格 全角空格 . À ア ※ ≡` 全部正常（零误报）。
    """
    k = id(f)
    v = _NOTDEF.get(k)
    if v is None:
        try:
            m = f.getmask("\ue000")
            v = (tuple(m.size), bytes(m))
        except Exception:
            v = None
        _NOTDEF[k] = v
    return v


def _missing(f, ch):
    """`ch` 在 `f` 里是不是缺字。见 `_notdef` 里为什么用位图字节而不用 bbox。"""
    k = (id(f), ch)
    v = _MISSING.get(k)
    if v is None:
        nd = _notdef(f)
        if nd is None:
            v = False
        else:
            try:
                m = f.getmask(ch)
                v = (tuple(m.size), bytes(m)) == nd
            except Exception:
                v = True
        _MISSING[k] = v
    return v


def draw_text(d, xy, text, pf, fill, anchor="lt"):
    """分段绘制文本（自动处理字体回退）。`anchor` 同 PIL：水平 l/m/r + 垂直 t/m/b。

    ★ 为什么不能直接 `d.text(..., font=pf.font)`：碰到雅黑没有的符号
      （`✓ ⚠ ℹ …`）就会画成豆腐块。这里按字符选字体、逐段推进 x。
    ★ 垂直定位统一用 `anchor="ls"`（左 + 基线），基线位置自己按 t/m/b 换算 ——
      比让 PIL 按整串算锚点稳（整串的 ascent/descent 会被回退字体带偏）。
    """
    text = str(text)
    if not text:
        return
    x, y = float(xy[0]), float(xy[1])
    ha, va = (anchor + "t")[0], (anchor + "t")[1]
    if ha == "m":
        x -= pf.measure(text) / 2.0
    elif ha == "r":
        x -= pf.measure(text)
    if va == "m":
        y += (pf._a - pf._d) / 2.0
    elif va == "b":
        y -= pf._d
    else:
        y += pf._a
    for seg, f in pf.runs(text):
        d.text((x, y), seg, font=f, fill=fill, anchor="ls")
        x += f.getlength(seg)


_TEXT_MASK = {}


def _text_mask(pf, text):
    """**1x 覆盖遮罩**（L）。`pf` 必须是 **1x 字号**的字体（见 `draw_text_1x`）。

    遮罩坐标约定：像素 (1,1) = 文本逻辑框的左上角（va='t' 的那个锚点），
    所以贴回画布时要整体偏 -1（见 `draw_text_1x` 的 `mo`）。
    """
    key = (id(pf), text)
    m = _TEXT_MASK.get(key)
    if m is None:
        w1 = max(1, int(math.ceil(pf.measure(text))))
        lh = max(1, int(pf._a + pf._d))
        m = Image.new("L", (w1 + 2, lh + 2), 0)
        dm = ImageDraw.Draw(m)
        x = 1.0
        for seg, f in pf.runs(text):
            dm.text((x, 1 + pf._a), seg, font=f, fill=255, anchor="ls")
            x += f.getlength(seg)
        if len(_TEXT_MASK) > 128:           # 文案是有限的，但动画会造中间串
            _TEXT_MASK.clear()
        _TEXT_MASK[key] = m
    return m


def draw_text_1x(cv, xy, text, pf, fill, anchor="lt"):
    """把**按 1x 字号排好**的文字画到 `SS` 倍画布上。`xy` 是 **SS 坐标**，`pf` 是 1x 字体。

    ★★ 为什么不跟着形状一起超采样（2026-09-20 第二十四批，实测）：
      FreeType 的 hinting 是在**最终字号**上做的。在 4× 画布上按 `4×px` 排字、
      再 BOX 缩回 1x 时，笔画落点 4 除不尽 → 每条笔画被摊到两个灰度像素上，
      屏幕上就是"中文发虚、笔画外面一层灰"。`shots/v_text_*.png` 把
      4x超采样 / 2x / 直接1x / 直接1x(BASIC) 四路摆在一起，"直接 1x"明显更利落。
      这不是"超采样不好" —— 形状是曲线，超采样正是对的；文字是**点阵**，
      它的清晰度来自"笔画落在整像素上"，跟超采样是两件事。

    做法：1x 上排好 → NEAREST 放大 SS 倍（块状、像素精确）→ 当遮罩贴回 SS 画布。
    最后那次 BOX 降采样是 SS×SS 的面积平均，对这块常量区域是**无损还原**。
    `fill` 收 RGB 或 RGBA（第 4 位当整体透明度，形变淡入淡出走它）。
    """
    text = str(text)
    if not text:
        return
    a = int(fill[3]) if len(fill) > 3 else 255
    if a <= 0:
        return
    m = _text_mask(pf, text)
    if a < 255:
        m = m.point(lambda v: v * a // 255)
    w1, lh = m.size[0] - 2, m.size[1] - 2
    x1, y1 = xy[0] / float(SS), xy[1] / float(SS)
    ha, va = (anchor + "t")[0], (anchor + "t")[1]
    if ha == "m":
        x1 -= w1 / 2.0
    elif ha == "r":
        x1 -= w1
    if va == "m":
        y1 -= lh / 2.0
    elif va == "b":
        y1 -= lh
    #   ★ 必须**先取整、再乘 SS**：取整才是"文字落在 1x 整像素上"，
    #     也就是清晰度的来源；先乘再取整就退回"落点带小数"的老毛病。
    mo = SS    # 遮罩自己在 (1,1) 处留了 1px 边，贴的时候扣掉
    cv.paste(tuple(fill[:3]),
             (int(round(x1)) * SS - mo, int(round(y1)) * SS - mo),
             m.resize((m.size[0] * SS, m.size[1] * SS), Image.NEAREST))


def font(kind, px):
    """取字体（带缓存 —— 动画每帧 `truetype()` 一次会明显拖慢）。`kind` = reg/bold。"""
    px = max(6, int(round(px)))
    key = (kind, px)
    if key not in _FONTS:
        _FONTS[key] = PFont(F_BOLD if kind == "bold" else F_REG, px)
    return _FONTS[key]


# ================================================================ 小工具
def hx(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    """两色按 t 混合（t=0 取 a）。颜色可以是 hex 字符串或 RGB 元组。"""
    if isinstance(a, str):
        a = hx(a)
    if isinstance(b, str):
        b = hx(b)
    t = max(0.0, min(1.0, float(t)))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def accent(kind):
    return ACCENT.get(kind, ACCENT["idle"])


def pad_for(scale=1.0):
    """窗口在"形状"四周要留出的投影空间（调用方拿它算窗口尺寸与摆放位置）。"""
    return max(6, int(round(SHADOW_PAD * float(scale))))


def default_layout(s=1.0):
    """胶囊内部的布局度量（**必须与 `剪映伴侣.ui_metrics()` 逐项一致**）。

    ★★ 为什么单独抽出来、又为什么强调"必须一致"：这个项目**已经**在这个坑里
      栽过一次 —— 第二十一批把胶囊布局从"小圆点 + 文字从 36px 起"改成
      "28px 圆形图标位 + 文字从 47px 起"，但 `_fit_width()` 里还留着旧的那套数，
      于是"按文案算出来的宽度"永远比"真正要用的宽度"少 13px，
      长文案被省略号吃掉关键信息（「…导出后无法自动**还原**」→ 用户不知道要还原什么）。
      现在的规矩是：宽度计算（`_fit_width`）和真正绘制（本模块）**读同一份数**。
      调用方把 `ui_metrics(s)` 原样传进来即可 —— 那里返回的就已经是本函数这个形状。
    """
    pad = max(9, int(round(10 * s)))
    return {"pad": pad,
            "icon_d": max(20, int(round(28 * s))),
            "tx": pad + max(20, int(round(28 * s))) + max(8, int(round(9 * s))),
            "pad_r": max(10, int(round(12 * s))),
            "bar_h": max(3, int(round(4 * s))),
            # ★ 进度弧的环心离球边多少（1x px）。**必须由这里给**：
            #   主程序的 `ui_metrics()` 早就返回 `ring_inset` 了，但渲染层当初
            #   又硬编码了一个 4.5 —— 典型"同一个数写两遍"（改了主程序那份不会生效）。
            #   ★ 5 → 7（第二十四批）：球面提亮之后，内壁高光和环只隔 2px，
            #     看着是"双边框"。外推 2px 才把"环"和"球边"分成两层。
            "ring_inset": max(4, int(round(7 * s)))}


def pack_layout(m):
    """把布局度量折成一个可当缓存键用的元组。"""
    return (m["pad"], m["icon_d"], m["tx"], m["pad_r"], m["bar_h"],
            m["ring_inset"])


def pack_sizes(m):
    """把字号折成缓存键用的元组（键顺序固定，别依赖 dict 顺序）。"""
    return tuple(m[k] for k in ("ball", "pct", "disc", "title", "sub"))


def default_sizes(s=1.0):
    """字号（**最终 1x 像素**，已经含 DPI 缩放）。

    ★ 语义必须钉死成"最终像素"：调用方传进来的 `ui_metrics(s)` 里那些 `*_px`
      本来就是按 scale 算好的，本模块若再乘一次 s 就是**双重缩放** ——
      128% DPI 下字会大出 28%，长文案立刻被窗口裁掉。
    """
    return {"ball": max(12, int(round(SZ_BALL * s))),
            "pct": max(10, int(round(SZ_PCT * s))),
            "disc": max(9, int(round(SZ_DISC * s))),
            "title": max(10, int(round(SZ_TITLE * s))),
            "sub": max(9, int(round(SZ_SUB * s)))}


class Metrics1x:
    """**1x 字号**的文字度量适配器（只提供 `measure()` / `metrics()`）。

    ★★ 为什么必须绕这一圈、而不是直接把 `PFont(px)` 交出去：
      本模块的形状是在 `SS` 倍的大画布上排版、最后一次性缩回 1x 的（超采样抗锯齿）。
      于是"按 px 量出来的宽"和"按 SS*px 量再缩回来"会差 1px 上下（FreeType hinting
      在不同字号下取整方式不同）。而项目的硬保证是
      **「量得下就必须画得下」**（`ellipsize` / `_fit_width` 全靠它，
      历史上已经因为"量宽和绘制不是同一个字体"吃掉过关键文案）。

    ★★ 第二十四批改动：**文字从"跟形状一起超采样"改成"在 1x 上排"**
      （原因见 `draw_text_1x`：4× 排再缩回会让中文笔画发虚）。
      所以这里的度量也从"按 SS 倍量再除回来"改成**直接按 1x 量** ——
      逐像素同源的规矩不变，只是"同源"的那一边换成了 1x。
      `ss` 参数保留只为签名兼容（旧调用点会传），**不再参与计算**。
    """

    def __init__(self, kind, px, ss=SS):
        self.px = max(1, int(round(px)))
        self._ss = 1.0
        self._f = font(kind, self.px)

    def measure(self, text):
        return int(math.ceil(self._f.measure(text) / self._ss))

    def metrics(self, key):
        return int(math.ceil(self._f.metrics(key) / self._ss))

    # 兼容 `font.font` 这类取值（有些调用点会顺手拿底层对象）
    @property
    def font(self):
        return self._f


def _rgba(rgb, a=255):
    """颜色 → RGBA 四元组。**hex 字符串和 RGB 元组都收**。

    ★ 为什么在这里兜一道（第二十三批真踩到）：强调色来自两条不同的调用路径 ——
      胶囊的图标用 `accent(kind)`（元组），卡片的按钮文字用主程序的
      `status_color(kind)`（hex 字符串）。只支持元组时，传 hex 的那条路会在
      **Pillow 内部**炸 `TypeError: 'str' object cannot be interpreted as an
      integer`，栈里完全看不出"是颜色传错了"（我照着栈追到 `ImageDraw.text`
      才明白）。颜色入口做一次归一化，这个坑就永久消失。
    """
    if isinstance(rgb, str):
        rgb = hx(rgb)
    return (rgb[0], rgb[1], rgb[2], max(0, min(255, int(a))))


def _fade(rgba, a):
    """淡入淡出：只缩 alpha（底是 RGB、混合是真的，所以直接缩就对）。"""
    return rgba[:3] + (int(rgba[3] * max(0.0, min(1.0, a))),)


def _mask(w, h, r):
    """形状遮罩（圆角矩形；r 取半高就是正圆）—— **尺寸就是像素尺寸**，不超采样。

    ★ 分成两个函数是有意的：本体内部本来就在 SS 倍画布上工作，
      再套一层超采样等于二次缩放（白花时间还会糊）。
      所以在 1x 画布上用 `aa_mask()`，在 SS 画布上直接用 `_mask()`。
    ★★ 圆角**必须自己夹到「不超过半宽/半高」**，不能指望 Pillow 里的内部夹取：
      球态圆角 = 直径/2，若比实际半宽还大，圆角矩形的直边段长度为负，
      画出来是"两头鼓包"（不是正圆）。第十二批在 Canvas 版上踩过一次；
      换成 Pillow 之后内部**恰好**也夹了，但那是实现细节，不能当契约。
    """
    w, h = int(w), int(h)
    r = max(0, int(round(r)))
    r = min(r, (w - 1) // 2, (h - 1) // 2)
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
    return m


def aa_mask(w, h, r):
    """**带抗锯齿**的形状遮罩（1x 尺寸）：超采样 SS 倍画、再面积平均缩回来。

    ★ 这是整批改动质感的**根**：边缘像素变成"覆盖率"（半透明）而不是台阶。
      旧版 Tk 画出来的边缘逐像素都是实的，48px 的圆放大就是一圈楼梯。
    """
    return _mask(w * SS, h * SS, r * SS).resize((int(w), int(h)), Image.BOX)


_GRAD_CACHE = {}
_RAMP_CACHE = {}


def _vgrad(w, h, top, bot):
    """竖向渐变（缓存 —— 稳态下每帧重建一次是白扔 1.3ms）。

    ★★ 返回的是一份**副本**，调用方随便改（第二十五批修）。
      为什么这里必须 `copy()` —— 这是个**真的踩了很久的隐形 bug**，值得写清楚：

      旧版是 `return g`（直接把缓存对象交出去），而两个调用方都在它上面**就地 paste**：
        · `_body` 往上叠"发丝亮线 + 内壁高光"（`paste((255,255,255), mask)`）；
        · `_do_card` 往上叠卡片本体。
      于是**同一个渐变被复用几次，那圈白就叠几次**：`alpha=74` 的白贴两遍 = 50%、
      五遍 = 82%、四十遍 = **255（纯白）**。实测（`_probe_grad_drift2.py`）：
      同一个球连渲 40 次，球顶那圈从 (121,121,124) 一路烧到 **(254,254,254)** ——
      屏幕上就是"球顶上箍了一圈白塑料"。
      更早没炸，是因为渲染结果本身有缓存（`_CACHE`）挡着"完全相同的参数"；
      而**任何参数一变就会重建**（正在跑时 `pct` 每 1% 一变、悬停时 hover 连续变、
      第二十五批之后动图每 100ms 变一帧）—— 也就是说这个 bug **专挑"最该好看"的
      动态过程发作**，静态截图反而看不出来。
      `copy()` 一张 272x272 的 RGB 是 ~0.1ms，而它省掉的是"重建 1.3ms" ——
      缓存的意义完全保留，只是不再把可变状态漏出去。
    """
    key = (w, h, top, bot)
    g = _GRAD_CACHE.get(key)
    if g is None:
        one = Image.new("RGB", (1, h))
        px = one.load()
        for y in range(h):
            t = y / max(1, h - 1)
            px[0, y] = tuple(int(round(top[i] + (bot[i] - top[i]) * t)) for i in range(3))
        g = one.resize((w, h), Image.BILINEAR)
        if len(_GRAD_CACHE) > 48:
            _GRAD_CACHE.clear()
        _GRAD_CACHE[key] = g
    #   ★ 缓存里那张是**只读母版**：谁也别拿它去 paste。
    return g.copy()


def _vramp(w, h, lo, hi, p0=0.0, p1=1.0, gamma=1.0):
    """竖直 alpha 渐变 `L` 图：`p0` 处是 `lo`，线性到 `p1` 处的 `hi`，两端外取端点值。

    用来做"发丝线只在顶部亮"这种**沿高度调制的遮罩**（缓存，键里带参数）。
    """
    key = (w, h, lo, hi, round(p0, 3), round(p1, 3), round(gamma, 3))
    g = _RAMP_CACHE.get(key)
    if g is None:
        one = Image.new("L", (1, h), 0)
        px = one.load()
        span = max(1e-6, p1 - p0)
        for y in range(h):
            t = (y / max(1, h - 1) - p0) / span
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            px[0, y] = max(0, min(255, int(round(lo + (hi - lo) * (t ** gamma)))))
        g = one.resize((w, h), Image.NEAREST)
        if len(_RAMP_CACHE) > 48:
            _RAMP_CACHE.clear()
        _RAMP_CACHE[key] = g
    return g


def _hairline(body, x0, y0, x1, y1, r, s):
    """画**"上亮下暗"的 1px 外圈** —— 这是"控件在任意背景上都不消失"的关键。

    ★ 为什么必须双色（本次改版踩的最大的一个坑）：
      旧版外圈只有一层黑边。在深色底（剪映暗色 UI / 暗壁纸）上，黑边**等于没画**，
      整颗球只剩"一团深色 + 一行白字"，像贴纸；在中灰底上更糟 ——
      形状色 (46,46,54)→(30,30,35) 和中灰背景 (58,58,66) 几乎同色，
      整个控件**直接消失**（评审图里就是这么暴露的）。
      改成双色环之后：深底靠上半圈的亮线分离、浅底靠下半圈的暗线分离。
    ★ 也不能是"一整圈均匀亮线" —— 那正是旧版"剪贴画感"的来源。
      所以亮线只走 y ∈ [0, 55%]，往下幂次衰减到 0；暗线从 45% 起渐入。
    """
    w = x1 - x0 + 1
    h = y1 - y0 + 1
    ring = Image.new("L", (w, h), 0)
    ImageDraw.Draw(ring).rounded_rectangle(
        [0, 0, w - 1, h - 1], max(0, int(round(r * SS))),
        outline=255, width=max(1, int(round(s * SS))))

    # 底：暗线（下半更重，模拟"背光面"）
    dark = ImageChops.multiply(ring, _vramp(w, h, EDGE_A, int(EDGE_A * 0.45),
                                            0.0, 1.0, 1.0))
    body.paste((0, 0, 0), (x0, y0), dark)
    # 顶：亮线（只在上半，幂次衰减）
    light = ImageChops.multiply(ring, _vramp(w, h, HAIR_A, 0, 0.0, 0.55, 1.35))
    body.paste((255, 255, 255), (x0, y0), light)


def _body(w, h, r, hover=0.0, s=1.0, pad=0):
    """形状本体。返回 **(RGB 画布, 形状遮罩 L)**，形状画在 `(pad*SS, pad*SS)`。

    ★ 底必须是 **RGB**：只有 RGB 底上的 `ImageDraw` 才会真的做 alpha 混合
      （RGBA 底是"替换"，半透明填充会变成挖洞 —— 见文件头的坑 1）。
    """
    p = int(round(pad * SS))
    W, H = int(round((w + 2 * pad) * SS)), int(round((h + 2 * pad) * SS))
    w2, h2 = int(round(w * SS)), int(round(h * SS))
    hover = max(0.0, min(1.0, float(hover)))

    body = _vgrad(w2, h2, mix(SURF_TOP, (86, 86, 99), hover * 0.5),
                  mix(SURF_BOT, (54, 54, 62), hover * 0.5))

    # ★ 外圈 1px "上亮下暗"发丝线（在深底/中底/浅底上都能把形状定住）
    _hairline(body, 0, 0, w2 - 1, h2 - 1, r, s)

    # 内壁高光：只沿顶部一圈，往下渐隐（交代受光方向 = "材质感" 的来源）
    in_ = max(1, int(round(s * SS)))
    rim = Image.new("L", (w2, h2), 0)
    ImageDraw.Draw(rim).rounded_rectangle(
        [in_, in_, w2 - in_ - 1, h2 - in_ - 1],
        max(0, int(round((r - s) * SS))), outline=255, width=in_)
    ramp = Image.new("L", (1, h2), 0)
    rp = ramp.load()
    span = max(1.0, h2 * 0.60)
    for y in range(h2):
        rp[0, y] = int(round(RIM_A * max(0.0, 1.0 - y / span) ** 1.5))
    rim = ImageChops.multiply(rim, ramp.resize((w2, h2), Image.BILINEAR))
    body.paste((255, 255, 255), (0, 0), rim)

    # 摆到带投影留白的大画布上（形状外的 RGB 是垃圾，最后会被遮罩裁掉）
    cv = Image.new("RGB", (W, H), SURF_BOT)
    cv.paste(body, (p, p))
    mask = Image.new("L", (W, H), 0)
    mask.paste(_mask(w2, h2, r * SS), (p, p))
    return cv, mask


def _arc(d, box, a0, a1, color, width):
    """带**圆头**的圆弧。PIL 的 `arc` 两端是平的，圆头要自己在两个端点各补一个圆。"""
    if abs(a1 - a0) < 0.25:
        a1 = a0 + 0.25
    d.arc(box, a0, a1, fill=color, width=width)
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    rx, ry = (box[2] - box[0]) / 2.0, (box[3] - box[1]) / 2.0
    for ang in (a0, a1):
        rad = math.radians(ang)
        x, y = cx + rx * math.cos(rad), cy + ry * math.sin(rad)
        d.ellipse([x - width / 2.0, y - width / 2.0,
                   x + width / 2.0, y + width / 2.0], fill=color)


def _to_rgba(rgb, mask):
    """把 RGB 内容按遮罩贴成 RGBA（**形状外必须是全零**，降采样才是正确预乘）。"""
    out = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
    out.paste(rgb.convert("RGBA"), (0, 0), mask)
    return out


def with_alpha(img, a):
    """整图透明度（卡片入场淡入用）。

    ★ ULW 下**不能**再走 Tk 的 `-alpha`（那会切回 `SetLayeredWindowAttributes`，
      和 `UpdateLayeredWindow` 打架），直接在图上缩 alpha。
    """
    a = max(0.0, min(1.0, float(a)))
    if a >= 0.999:
        return img
    out = img.copy()
    out.putalpha(out.getchannel("A").point(lambda v: int(v * a)))
    return out


# ================================================================ 球心动图
# ★★ 2026-09-20 第二十五批（用户："把剪的图片换成这个，没运行时就不动，
#    正在运行时就动"，并贴了一张 23 帧的小人动图）。
#
# 为什么是"渲染层自己认这张图"，而不是主程序解好帧再塞进来：
#   · 主程序那边只有"状态"（idle / 正在跑），**帧号是渲染层的事**
#     （它才知道球心那块可视区域到底多大、要不要给进度环让位）。
#   · 早加载会拖慢启动（全解 23 帧 ≈ 32ms），所以**懒加载**：
#     第一次真的要画头像时才解，失败就整条路退回"画字"（见 `render` 的契约）。
#
# 三道工序，各自只做一次或几次：
#   ① ①  解 GIF  → `avatar_frames()`：每帧**等比装进正方形**（一个像素都不裁），
#          两侧用"画面自己的边缘列"拉伸补满（edge extend）—— 因为最后要裁圆，
#          补的是边缘色就**看不出接缝**（直接填个均值色会在左右各留一条淡竖带）。
#   ② 裁圆    → `_avatar_disc(帧号, 直径)`：先 resize 再 `putalpha(圆遮罩)`。
#          ★ 顺序不能反：**先缩再不透明→透明**，就不会出现"缩放假发边"
#            （RGBA 的 resize 不预乘，先裁圆再缩会在圆周上带一圈背景色晕）。
#          按 (帧号, 直径) 记忆 —— 直径只在"进/出进度环"或换 DPI 时变，
#          所以一帧一辈子最多重算几次，稳态下 0 成本。
#   ③ 贴上去  → `draw_avatar()`：RGB 底 + L 掩码（本文件的老规矩，混合是真的）。
AVATAR_FILE = "ball_avatar.gif"
AVATAR_SIDE = 160       # 烘焙边长上限（源图 250x291 → 取 min(w,h)=250，再降到 160）。
                        #   ★ 160 够不够？**够，而且是算出来的不是拍的**：
                        #     屏幕上小人最大只有 62px（= 球 75px × 球心占比，见
                        #     `avatar_d`；1.5625 是本程序 scale 的上限），而 160 是
                        #     **4 倍超采样后**要画的那张（d×SS）在 scale 1 下的尺寸。
                        #     即"160 → 显示 40px" = 4 倍过采样，"160 → 显示 62px"
                        #     = 2.6 倍 —— 超过 2 倍之后，多出来的精度在 40~62px 上
                        #     **一个像素都看不出来**（`_probe_avside.py` 拿 160 和
                        #     250 两版对拍过最终 1x 输出）。
                        #   代价对比：250 版要 5.75MB 内存 + 46ms 烘焙，160 版
                        #     **2.36MB + 28ms**。省下的正是"看不见的那部分"。
AVATAR_MIN_D = 14       # 头像直径下限（1x / SS 空间都夹一刀，别算出 0）
AVATAR_INSET = 4        # 没有进度环时：头像直径 = 球径 - 2×4（1x px）——
                        #   ★ 这个数来自原型对拍（`_probe_avatar.py` 的 A~F 六案）：
                        #     "整帧装进正方形 + 缩 4" 在 48px 球里最读得出"一个人在动"。
AVATAR_GAP = 2          # 有进度环时：头像和环之间再留这么多（1x px）

_AV = None              # None=没加载过 · False=加载失败（退回画字） · list[RGBA]
_AV_MS = ()             # 逐帧**起始**时刻（ms），用来把墙上时钟映射成帧号
_AV_TOTAL = 0           # 一轮总时长（ms）
_AV_DISC = {}           # (帧号, 直径) → 圆形 RGBA（记忆化）


def res_dir():
    """随包资源所在目录：**打包后是 PyInstaller 的解包目录**，源码运行是脚本目录。

    ★ 为什么不复用主程序那套 `Path(sys.executable).parent`：那个是"**安装**目录"
      （放 使用说明.txt / 运行日志.txt / 草稿备份 这些**可写**的东西）。
      打进 exe 的资源（spec 里的 `datas`）在 onefile 下被解到 `sys._MEIPASS`，
      拿安装目录去找**一定找不到**。两条路的语义不一样，不能混。
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None) or str(Path(sys.executable).parent)
        return Path(base)
    return Path(__file__).resolve().parent


def avatar_path():
    return res_dir() / AVATAR_FILE


def _fit_square(src, side):
    """一帧 → side×side 的正方形：**等比缩到整个画面都装得下**（不裁任何像素）。

    ★ 为什么选"装下"而不是"裁满"（原型里两种都试了）：动图的主体是**全身**
      （头顶到脚），裁满必然砍掉头或脚中的一头，而成品是在 40px 的球心里看 ——
      少一截就更读不出是个人。两侧的空白用**边缘列拉伸**补：贴到圆里无接缝。
    ★ 一律先 `convert("RGBA")`：GIF 给的帧可能是 **P 模式**（调色板），
      对 P 模式做 resize 是在**调色板索引**上插值 —— 出来的是花花绿绿的噪点，
      不是图。这个坑不报错，只是画面变成一坨，所以必须显式转。
    """
    src = src if src.mode == "RGBA" else src.convert("RGBA")
    w, h = src.size
    side = max(1, int(side))
    k = min(side / float(w), side / float(h))
    nw, nh = max(1, int(round(w * k))), max(1, int(round(h * k)))
    #   ★ 降采样用 BOX（面积平均）：和本文件别处同一个理由 —— 它是**精确覆盖率**、
    #     不产生 LANCZOS 那种边缘过冲，还快一倍。3 倍的降采样倍率下两者看不出差别，
    #     而这一步是一次性的（23 帧），省下的是启动时间。
    r = src.resize((nw, nh), Image.BOX)
    if nw == side and nh == side:
        return r
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    x, y = (side - nw) // 2, (side - nh) // 2
    out.paste(r, (x, y))
    if x > 0:                      # 左右各把最外一列拉宽补满
        out.paste(r.crop((0, 0, 1, nh)).resize((x, nh), Image.NEAREST), (0, y))
        out.paste(r.crop((nw - 1, 0, nw, nh)).resize((side - nw - x, nh),
                                                     Image.NEAREST), (x + nw, y))
    if y > 0:                      # 上下同理（本图用不到，别的图会用）
        out.paste(r.crop((0, 0, nw, 1)).resize((nw, y), Image.NEAREST), (x, 0))
        out.paste(r.crop((0, nh - 1, nw, nh)).resize((nw, side - nh - y),
                                                     Image.NEAREST), (x, y + nh))
    return out


def avatar_frames():
    """球心动图的帧表（**懒加载，只解一次**）。

    · 成功 → `list[RGBA]`（正方形、不透明；圆是贴的时候才裁的）
    · 失败 → `False`（并且**记住失败**，别每帧都去开一次不存在的文件）

    ★ 为什么在**这里**就把边长压到 `AVATAR_SIDE`，而不是留到贴的时候按需缩：
      这一步是"一次投 28ms 换 3.4MB 内存"的分界线 —— 压完就能**丢掉原始帧**
      （250x291 的 RGBA 一帧 291KB，23 帧 6.7MB），不压就得一直拎着。
    ★ 返回 `False` 而不是抛异常：这是**外观**的一部分，一张素材缺失绝不该把
      界面带崩 —— 调用方（`render`）看到 False 就照旧画那个字。
    """
    global _AV, _AV_MS, _AV_TOTAL
    if _AV is not None:
        return _AV
    try:
        im = Image.open(avatar_path())
        raw, ms, acc = [], [], 0
        for f in ImageSequence.Iterator(im):
            ms.append(acc)
            #   ★ 每一帧的停留时长**只有在这里读得到**（`Iterator` 边走边给
            #     `.info["duration"]`）—— 所以时长和像素必须**同一趟**收完，
            #     再 `Image.open` 一遍纯属白解一次图（32ms）。
            acc += max(10, int(f.info.get("duration") or 100))
            raw.append(f.convert("RGBA"))
        if not raw:
            _AV = False
            return _AV
        side = min(min(raw[0].size), AVATAR_SIDE)
        _AV = [_fit_square(f, side) for f in raw]
        _AV_MS, _AV_TOTAL = tuple(ms), max(1, acc)
    except Exception as e:                            # 缺文件 / 坏 GIF / 无解码器
        print(f"[ui] 球心动图不可用({e}) → 退回画字", flush=True)
        _AV = False
    return _AV


def avatar_count():
    """帧数（不可用时 0）—— 调用方拿它判断"要不要按时钟推进"。"""
    f = avatar_frames()
    return 0 if not f else len(f)


def avatar_index_at(ms):
    """墙上时钟（进入"正在运行"那一刻起的毫秒数）→ 帧号（循环）。

    ★ 用**逐帧真实时长**累加出来的时间轴，而不是 `ms // 100`：这张图恰好每帧
      100ms，但"恰好"不能写进代码 —— 换一张不均匀的动图就会跳帧。
    """
    if not _AV_MS:
        avatar_frames()
    n = len(_AV_MS)
    if n <= 1:
        return 0
    t = int(ms) % _AV_TOTAL
    lo, hi = 0, n - 1
    while lo < hi:                    # 找最后一个起始时刻 <= t
        mid = (lo + hi + 1) // 2
        if _AV_MS[mid] <= t:
            lo = mid
        else:
            hi = mid - 1
    return lo


def avatar_d(ball, ring_inset, ring=False, s=1.0):
    """球心头像的直径（1x px）—— **纯函数**，测试直接对着它断言。

    · 没有进度环（待机）→ 球径 - 2×`AVATAR_INSET`（尽量填满球心）。
    · 有进度环（正在跑）→ 缩到**环内沿以内**。★ 这一步不是"美观微调"：
      48px 球 + ring_inset 7 时，环的内沿只到直径 ~31px，而待机头像是 40px ——
      不缩的话头像会**整个盖住进度环**（环在球心半径 17px 上，头像半径 20px），
      屏幕上就是"正在跑但看不到进度"。缩完之后环正好成了头像的外圈，
      读起来像"带进度环的头像"，而不是两个元素打架。
    """
    s = float(s)
    ball = float(ball)
    if ring:
        w_ring = max(2.0, 2.5 * s)              # 与 `_render` 里画环的线宽同源
        return max(AVATAR_MIN_D, ball - 2.0 * (float(ring_inset) + w_ring
                                               + AVATAR_GAP * s))
    return max(AVATAR_MIN_D, ball - 2.0 * AVATAR_INSET * s)


def _avatar_disc(fi, d):
    """直径 d 的**圆形**头像（RGBA）—— 按 (帧号, 直径) 记忆化。"""
    key = (int(fi), int(d))
    got = _AV_DISC.get(key)
    if got is not None:
        return got
    fr = avatar_frames()
    if not fr:
        return None
    src = fr[int(fi) % len(fr)]
    d = max(1, int(d))
    sq = src if src.size == (d, d) else src.resize((d, d), Image.LANCZOS)
    sq = sq.copy()
    sq.putalpha(aa_mask(d, d, d / 2.0))
    if len(_AV_DISC) > 256:
        _AV_DISC.clear()
    _AV_DISC[key] = sq
    return sq


def draw_avatar(cv, fi, cx, cy, d, a=1.0, ring_inset=7.0, ring=False, s=1.0):
    """把第 `fi` 帧贴到 `cv`（**RGB** 底）的 (cx, cy) 圆心上，直径 `d`（SS 空间）。

    ★ 逐帧的动画**必须**走这里：它只在"帧号或直径变了"时才真做一次 resize+裁圆
      （见 `_avatar_disc`），稳态下就是一次 `paste`。
    ★ 返回 True/False = 画没画成。画不成时调用方要**接着把那个字画上** ——
      宁可球心是"剪"，也不能是空的。
    """
    d = int(round(d))
    if d < 2:
        return False
    disc = _avatar_disc(fi, d)
    if disc is None:
        return False
    box = (int(round(cx - d / 2.0)), int(round(cy - d / 2.0)))
    mask = disc.getchannel("A")
    a = max(0.0, min(1.0, float(a)))
    if a < 0.999:                      # 形变淡入：只缩遮罩（底是 RGB，混合是真的）
        mask = mask.point(lambda v: int(v * a))
    cv.paste(disc.convert("RGB"), box, mask)
    return True


# ================================================================ 主渲染
_CACHE = {}
_CACHE_MAX = 32


def _cached(key, build):
    """渲染结果缓存。

    ★ 为什么必须有：动画里大量帧其实是**同一个状态**（进度数字没变、只有悬停亮度
      变了一丝），缓存把"重复帧"的成本直接降到 0（连 PIL 都不进）。
    ★ 返回的是**共享对象**，调用方只读（拿去做 `premultiplied_bytes` / `blit`）；
      需要改就先 `copy()`（见 `with_alpha`）。
    """
    img = _CACHE.get(key)
    if img is None:
        img = build()
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = img
    return img


def render(w, h, r, *, scale=1.0, kind="idle", glyph="", title="", sub="",
           pct=None, bar=None, ball_a=1.0, pill_a=0.0, hover=0.0, ball_x=0.0,
           layout=None, sizes=None, avatar=None):
    """渲染一帧悬浮窗 → `(w+2P, h+2P)` 的 RGBA 图，`P = pad_for(scale)`。

    · `ball_a` / `pill_a`：球内容 / 胶囊内容各自的透明度（形变动画用）。
      **两者永不同时可见**（由 `morph_alphas` 保证）—— 这里也照这条规矩画，
      所以中间帧不会出现"半个字叠在球上"。
    · `ball_x`：展开时球贴在窗口**右端**，传 `w - h` 进来。
    · `layout`：胶囊内部度量（见 `default_layout`）。**调用方必须传
      `ui_metrics(scale)`** —— 宽度计算和绘制读同一份数，才能保证"量得下就画得下"。
    · `sizes`：字号（**最终 1x 像素**）覆盖，只给需要改的键
      （`_fit_width` 到上限还放不下时降主文案字号走它）。不传则按 `scale` 推。
    · `avatar`：球心动图**帧号**（`int`）或 `None`。
      · `None` = 不画动图，球心 / 胶囊图标位照旧画 `glyph`（"✓ / × / !" 那些
        **有信息量**的字，以及 `busy` 的百分比数字）。
      · `0` = 动图**静止在第一帧**（"没运行时就不动"）。
      · `n>0` = 那一帧（"正在运行时就动"由调用方按时钟推进帧号，见 `avatar_index_at`）。
      ★ 给了帧号就**同时**顶掉球心和胶囊图标位里的字 —— 一次形变（球↔横条）里
        不能"球上是小人、展开变成剪字"，那是同一张脸上换脸。
        代价是 `busy` 的百分比数字不再画在球心；进度由**进度环**（收起）
        和**底部进度条**（展开）承担，数字仍由 `ball_label` / `pill_glyph` 给出。
      ★ 帧号**必须进缓存键**（否则 23 帧全命中同一张图，屏上就一个定格）。
      ★ 动图取不到时**自动退回画字**，不会因为一张素材把界面带崩。
    """
    L = default_layout(scale) if layout is None else layout
    S = default_sizes(scale)
    if sizes:
        S.update({k: v for k, v in sizes.items() if k in S})
    av = None if avatar is None else int(avatar)
    key = (round(float(w)), round(float(h)), round(float(r), 1),
           round(float(scale), 3), kind, glyph, title, sub,
           None if pct is None else round(float(pct), 1),
           None if bar is None else round(float(bar), 2),
           round(ball_a, 2), round(pill_a, 2), round(hover, 2),
           round(float(ball_x), 1), pack_layout(L), pack_sizes(S), av)
    return _cached(key, lambda: _render(*key))


def _render(w, h, r, scale, kind, glyph, title, sub, pct, bar,
            ball_a, pill_a, hover, ball_x, lay, szs, avatar=None):
    s = float(scale)
    # `lay` / `szs` 进缓存前被拍成了元组（元组才可哈希、才能当缓存键）——
    #   这里还原成字典，下面的绘制代码就不用记"第几个是 pad"。
    lay = dict(zip(("pad", "icon_d", "tx", "pad_r", "bar_h", "ring_inset"), lay))
    sz = dict(zip(("ball", "pct", "disc", "title", "sub"), szs))
    P = pad_for(s)
    ox = oy = P * SS
    w2, h2 = int(round(w * SS)), int(round(h * SS))
    cv, mask = _body(w, h, r, hover=hover, s=s, pad=P)
    d = ImageDraw.Draw(cv, "RGBA")          # ★ RGB 底 → 混合是真的

    # ——— 球的内容（动图 / 进度环 + 数字）———
    if ball_a > 0.004:
        bx = ox + int(round(ball_x * SS))
        #   ★ 环心离球边多少 —— 取 `lay["ring_inset"]`（**单一来源**，见 default_layout）。
        inset = float(lay["ring_inset"]) * SS
        lw = max(2, int(round(2.5 * s * SS)))
        box = [bx + inset, oy + inset, bx + h2 - inset, oy + h2 - inset]
        #   ★ 有环 = 正在跑且有百分比。头像的直径**取决于有没有环**（见 `avatar_d`
        #     的注释：不缩就会被头像整个盖住，屏幕上看不到进度）。
        ring = (kind == "busy" and pct is not None)
        if ring:
            d.ellipse(box, outline=_fade(GROOVE, ball_a), width=lw)
            frac = max(0.0, min(1.0, float(pct) / 100.0))
            if frac > 0.002:
                _arc(d, box, 270, 270 + 360 * frac,
                     _fade(_rgba(accent(kind)), ball_a), lw)
            txt, px = glyph, sz["pct"]
        else:
            txt, px = glyph, sz["ball"]
        cx, cy = bx + h2 / 2.0, oy + h2 / 2.0
        #   ★ 动图优先、字是兜底：画得成就不画字（"剪"由小人顶掉），
        #     素材缺失 / 解码失败时**照旧画那个字** —— 球心绝不能是空的。
        drawn = (avatar is not None and draw_avatar(
            cv, avatar, cx, cy,
            avatar_d(h2 / SS, float(lay["ring_inset"]), ring=ring, s=s) * SS,
            a=ball_a, ring_inset=float(lay["ring_inset"]), ring=ring, s=s))
        if not drawn:
            draw_text_1x(cv, (cx, cy + 1 * s * SS), txt,
                         font("bold", px), _fade(_rgba(LABEL), ball_a), "mm")

    # ——— 胶囊的内容（图标盘 + 两行字 + 底部进度条）———
    if pill_a > 0.004:
        #   ★ 布局全部走 `lay`（= ui_metrics）：图标位左边缘 / 直径 / 文字起点 /
        #     左右留白 / 条高，一处都不再硬编码 —— 见 default_layout 的注释。
        lpad, idia_1x = float(lay["pad"]), float(lay["icon_d"])
        lpad_r, bar_h = float(lay["pad_r"]), float(lay["bar_h"])
        #   ★ 有进度条时内容整体上移一点点，否则视觉重心会掉到那条线上
        icy = oy + h2 / 2.0 - (2.5 * s * SS if bar is not None else 0)
        idia = idia_1x * SS
        dcx = ox + lpad * SS + idia / 2.0
        d.ellipse([dcx - idia / 2.0, icy - idia / 2.0,
                   dcx + idia / 2.0, icy + idia / 2.0], fill=_fade(DISC, pill_a))
        #   ★ 球心那个小人**在横条里是同一个**（图标位）—— 否则一悬停展开，
        #     球上的小人就"变回一个剪字"，同一次形变里换了张脸。
        drawn = (avatar is not None and draw_avatar(
            cv, avatar, dcx, icy, avatar_d(idia_1x, 0.0, ring=False, s=s) * SS,
            a=pill_a, ring_inset=0.0, ring=False, s=s))
        if not drawn and glyph:
            draw_text_1x(cv, (dcx, icy + s * SS), glyph, font("bold", sz["disc"]),
                         _fade(_rgba(accent(kind)), pill_a), "mm")
        tx = ox + float(lay["tx"]) * SS
        draw_text_1x(cv, (tx, icy - 7.5 * s * SS), title,
                     font("reg", sz["title"]), _fade(_rgba(LABEL), pill_a), "lm")
        draw_text_1x(cv, (tx, icy + 8.5 * s * SS), sub,
                     font("reg", sz["sub"]), _fade(_rgba(LABEL2), pill_a), "lm")
        if bar is not None:
            bh = max(2, int(round(bar_h * SS)))
            by = oy + h2 - (6 * s) * SS - bh
            x0 = ox + lpad * SS
            x1 = ox + (w * SS) - lpad_r * SS
            if x1 > x0 + bh:
                d.rounded_rectangle([x0, by, x1, by + bh], bh // 2,
                                    fill=_fade(TRACK, pill_a))
                fw = (x1 - x0) * max(0.0, min(1.0, float(bar)))
                if fw > bh * 1.05:
                    d.rounded_rectangle([x0, by, x0 + fw, by + bh], bh // 2,
                                        fill=_fade(_rgba(accent(kind)), pill_a))

    # ——— 贴遮罩成 RGBA → 缩回 1x（唯一一次降采样）→ 垫投影 ———
    W, H = int(round(w + 2 * P)), int(round(h + 2 * P))
    out = _to_rgba(cv, mask).resize((W, H), Image.BOX)
    sm = _mask(w2, h2, r * SS).filter(ImageFilter.GaussianBlur(SHADOW_BLUR * s * SS * 0.5))
    sm = sm.resize((int(round(w)), int(round(h))), Image.BOX)
    shadow = Image.new("L", (W, H), 0)
    shadow.paste(sm, (P, P + int(round(SHADOW_DY * s))))
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # ★★ 投影尾巴**硬截断到 0**（第二十四批）。为什么不能靠"留白够大"了事：
    #   α 一旦落在 1~2/255 这个量级，人眼看不出、但 Windows 的分层窗口命中测试
    #   照样把它算成"不透明"→ 白吃一圈鼠标。归零之后：
    #     ① 视觉上没有任何损失（2/255 不可见）；
    #     ② 吃点击的环**收窄**到"α ≥ 2 的那一圈"（实测约 9px，比旧版 7px 时代还紧）；
    #     ③ 窗口边界不再硬切投影（旧版边界上还留着 α≈9 的灰，屏幕上就是一圈脏边）。
    lut = [0 if int(v * SHADOW_ALPHA / 255) < 2 else int(v * SHADOW_ALPHA / 255)
           for v in range(256)]
    sh.putalpha(shadow.point(lut))
    return Image.alpha_composite(sh, out)


def card_buttons(w, h, r, scale=1.0, n=2):
    """卡片按钮的命中矩形（**窗口坐标**，已含投影留白 P）。

    ★ 为什么要在这里给：改用逐像素 alpha 上屏之后，卡片上**没有任何子控件**了
      （Tk 的子窗口会盖住我们的位图），按钮的悬停高亮和点击命中都得自己算矩形。
      矩形必须和 `_do_card` 里画的分隔线/高亮块**同源**，否则会出现
      "看到按钮在这儿、点下去没反应"——那种偏差光看截图发现不了。
    """
    P = pad_for(scale)
    bh = max(1, int(round(CARD_BTN_H * float(scale))))
    y0 = P + h - bh
    y1 = P + h
    mid = P + w / 2.0
    if n <= 1:
        return [(P, y0, P + w, y1)]
    half = [(P, y0, mid, y1), (mid, y0, P + w, y1)]
    return half[:n] if n <= 2 else half


CARD_LH = 1.52      # 卡片正文行高（× 正文字号）。**主程序算卡片高度时也要用它** ——
                    #   否则"折了几行 → 卡片多高"会出现两套算法，文字就贴边/被裁。
CARD_PAD = 15       # 卡片内边距（1x）


def card_metrics(scale=1.0, sizes=None):
    """卡片布局的**唯一口径**：正文行高 + 标题行高 + 内边距（都是 1x 像素）。

    主程序用它在**折行之前**算卡片高度；`_do_card` 用同一份数排版。
    """
    s = float(scale)
    S = default_card_sizes(s)
    if sizes:
        S.update({k: v for k, v in sizes.items() if k in S})
    t_h = Metrics1x("bold", S["title"]).metrics("linespace")
    b_h = Metrics1x("reg", S["body"]).metrics("linespace")
    pad = max(8, int(round(CARD_PAD * s)))
    icon_d = max(16, int(round(26 * s)))
    return {"pad": pad,
            "icon_d": icon_d,
            "tx": pad + icon_d + max(8, int(round(10 * s))),
            "title_px": S["title"], "body_px": S["body"], "icon_px": S["icon"],
            "t_h": t_h, "b_h": b_h,
            "l_h": max(b_h, int(round(S["body"] * CARD_LH)))}


def default_card_sizes(s=1.0):
    return {"title": max(11, int(round(SZ_CARD_T * s))),
            "body": max(10, int(round(SZ_CARD_B * s))),
            "icon": max(10, int(round(SZ_CARD_I * s)))}


def pack_card_sizes(m):
    return tuple(m[k] for k in ("title", "body", "icon"))


def render_card(w, h, r, *, scale=1.0, kind="ok", glyph="", title="", lines=(),
                buttons=None, alpha=1.0, sizes=None):
    """通知 / 确认卡片：圆角 + 投影 + 极淡内描边；**图标对齐标题首行**。

    ★ 旧版图标对齐"整块的竖直中心"，于是"有正文 / 没正文"两种卡片的图标位置
      会跳一下，看着像两个不同控件。现在对齐**标题行的中线**。
    ★ `buttons` = [(文字, 是否悬停, 文字颜色或 None), ...]，两枚，左右各一。
    ★ `alpha`：整卡透明度（入场淡入用）。**贵的那一遍只算一次**（缓存键不含
      alpha），alpha 只是在 1x 小图上缩一道 —— 淡入三帧不会各渲染一遍。
    """
    s = float(scale)
    S = default_card_sizes(s)
    if sizes:
        S.update({k: v for k, v in sizes.items() if k in S})
    base = _cached(
        ("card", round(float(w)), round(float(h)), round(float(r), 1), round(s, 3),
         kind, glyph, title, tuple(str(x) for x in lines),
         None if not buttons else tuple((str(b[0]), bool(b[1]), b[2]) for b in buttons),
         pack_card_sizes(S)),
        lambda: _do_card(int(round(w)), int(round(h)), float(r), s, kind, glyph,
                         title, tuple(str(x) for x in lines), buttons, S))
    return with_alpha(base, alpha)


def _do_card(w, h, r, s, kind, glyph, title, lines, buttons, S):
    P = pad_for(s)
    W, H = int(round((w + 2 * P) * SS)), int(round((h + 2 * P) * SS))
    x0, y0 = P * SS, P * SS
    x1, y1 = int(round((P + w) * SS)), int(round((P + h) * SS))
    rad = max(0, int(round(r * SS)))

    # ★ 卡片底比悬浮球**再亮一档**（球是"按钮"、卡是"纸"），靠明度分层而不是描边。
    #   取 `CARD_TOP/CARD_BOT` 常量（单一来源）—— 回归测试要能断言"卡比球亮一档"。
    cv = _vgrad(W, H, CARD_TOP, CARD_BOT)
    d = ImageDraw.Draw(cv, "RGBA")
    _hairline(cv, x0, y0, x1, y1, r, s)

    # ★ 布局度量走 `card_metrics`（**和主程序算卡片高度时读的是同一个函数**）：
    #   否则"折了几行 → 卡片该多高"会有两套算法，差 1px 就让最后一行贴到卡边。
    cm = card_metrics(s, S)
    pad = cm["pad"] * SS
    idia = cm["icon_d"] * SS
    f_t = font("bold", S["title"])
    f_b = font("reg", S["body"])
    t_h = cm["t_h"] * SS
    l_h = cm["l_h"] * SS
    lines = [str(x) for x in lines]
    block = t_h + (len(lines) * l_h + 2 * s * SS if lines else 0)
    # ★ 有按钮行时，文字块要在"卡片高 − 按钮行高"里居中 —— 否则多行正文会
    #   压到按钮那一条上（评审图里已经贴到分隔线了）。
    _btns = 0 if not buttons else int(round(CARD_BTN_H * s * SS))
    top = y0 + ((h * SS) - _btns - block) / 2.0
    d.ellipse([x0 + pad, top + t_h / 2.0 - idia / 2.0,
               x0 + pad + idia, top + t_h / 2.0 + idia / 2.0], fill=DISC)
    if glyph:
        draw_text_1x(cv, (x0 + pad + idia / 2.0, top + t_h / 2.0 + s * SS), glyph,
                     font("bold", SZ_CARD_I * s), _rgba(accent(kind)), "mm")
    btx = x0 + cm["tx"] * SS
    draw_text_1x(cv, (btx, top + t_h / 2.0), title, f_t, _rgba(LABEL), "lm")
    # ★ 多行必须**逐行画**（PIL 的 `text()` 不支持"多行 + 锚点"）
    for i, ln in enumerate(lines):
        draw_text_1x(cv, (btx, top + t_h + 2 * s * SS + i * l_h + l_h / 2.0), ln,
                     f_b, _rgba(LABEL2), "lm")

    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([x0, y0, x1, y1], rad, fill=255)

    if buttons:
        bh = int(round(CARD_BTN_H * s * SS))
        by = y1 - bh
        line = _rgba((255, 255, 255), 30)
        d.line([x0, by, x1, by], fill=line, width=max(1, int(round(s * SS))))
        d.line([(x0 + x1) / 2.0, by, (x0 + x1) / 2.0, y1], fill=line,
               width=max(1, int(round(s * SS))))
        for i, (label, hot, col) in enumerate(buttons):
            if hot:
                # ★ 高亮块要和卡片圆角**相交**：上两角是直角、下两角跟着卡片圆角。
                #   直接画圆角矩形会让上角也圆掉（iOS 的 alert 不是这样）；
                #   直接画直角矩形又会在下面两角**戳出卡片** —— 而卡片外面是真
                #   透明，戳出去就是浮在桌面上的一个白角。
                hm = Image.new("L", (W, H), 0)
                bb = ([x0, by + 1, int((x0 + x1) / 2.0), y1 - 1] if i == 0
                      else [int((x0 + x1) / 2.0), by + 1, x1, y1 - 1])
                ImageDraw.Draw(hm).rectangle(bb, fill=255)
                hm = ImageChops.multiply(hm, mask).point(
                    lambda v: int(v * BTN_HOT / 255))
                cv.paste((255, 255, 255), (0, 0), hm)
            cx = x0 + (x1 - x0) * (0.25 if i == 0 else 0.75)
            draw_text_1x(cv, (cx, (by + y1) / 2.0), label,
                         font("reg", SZ_CARD_T * s),
                         _rgba(col if col else LABEL), "mm")

    out = _to_rgba(cv, mask)
    # 投影：比球**更大更淡 + 略往下**（卡片悬在球头顶，离"桌面"更远 → 影子更散）
    #   ★ 第二十四批：和球共用同一套规矩（模糊 → resize 回 1x → α<2 硬截断到 0）。
    #   ★★ 这里修掉一个**真 bug**（也是"看着廉价"里最刺眼的一处）：
    #     旧版遮罩用的是 `_mask(W, H, rad)` —— **整张画布**的圆角矩形，而不是卡片本体。
    #     模糊之后的尾巴于是直接顶到窗口四边，被矩形硬切一刀 ⇒ 卡片周围挂着一圈
    #     "方框脏边"；同时那圈 α≈118 会把窗口外圈全变成命中区，白吃一圈鼠标
    #     （Windows 分层窗口的命中规则：α 为 0 才放行）。
    #     球的写法本来是对的（`_mask(w2, h2, r*SS)` 只取本体尺寸），卡片漏了这一层缩放。
    #     现在两边共用 `SHADOW_BLUR/SHADOW_ALPHA/SHADOW_DY` 同一组常量。
    sm = _mask(int(round(w * SS)), int(round(h * SS)), rad).filter(
        ImageFilter.GaussianBlur(SHADOW_BLUR * s * SS * 0.5))
    #   ★ 缩回的是**卡片本体**尺寸（`w × h`），不是画布尺寸 —— 缩成 `w+2P` 等于把
    #     遮罩拉伸铺满整窗，边界上又会留一圈实心灰（本轮第二次踩，前面那次是遮罩本身取错）。
    sm = sm.resize((int(round(w)), int(round(h))), Image.BOX)
    canvas = Image.new("L", (w + 2 * P, h + 2 * P), 0)
    canvas.paste(sm, (P, P + int(round(SHADOW_DY * s))))
    cl = [0 if int(v * SHADOW_ALPHA / 255) < 2 else int(v * SHADOW_ALPHA / 255)
          for v in range(256)]
    sh = Image.new("RGBA", (w + 2 * P, h + 2 * P), (0, 0, 0, 0))
    sh.putalpha(canvas.point(cl))
    return Image.alpha_composite(sh, out.resize((w + 2 * P, h + 2 * P), Image.BOX))


# ================================================================ 上屏
_u32, _gdi = ctypes.windll.user32, ctypes.windll.gdi32
GWL_EXSTYLE, WS_EX_LAYERED, ULW_ALPHA = -20, 0x00080000, 0x00000002
AC_SRC_OVER, AC_SRC_ALPHA, DIB_RGB_COLORS = 0x00, 0x01, 0


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BMIH), ("bmiColors", wintypes.DWORD * 3)]


class _BF(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class _PT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _SZ(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


def toplevel_hwnd(widget):
    """拿真正的顶层 HWND（Tk 的 `winfo_id()` 可能只是它的包装窗，要往上找）。"""
    h = widget.winfo_id()
    for _ in range(8):
        p = _u32.GetParent(h)
        if not p:
            break
        h = p
    return h


def premultiplied_bytes(img):
    """RGBA → 预乘 alpha 的 **BGRA** 字节串（ULW 要的就是这个）。

    ★ 用 `ImageChops.multiply` 做预乘、用 `merge` 换字节序 —— 全程 C 实现。
      逐像素 Python 循环在 256x66 上每帧一万七千次，动画会明显掉帧。
    """
    r_, g_, b_, a_ = img.convert("RGBA").split()
    return Image.merge("RGBA", (ImageChops.multiply(b_, a_),
                                ImageChops.multiply(g_, a_),
                                ImageChops.multiply(r_, a_), a_)).tobytes()


def blit(hwnd, img, x=None, y=None):
    """把一张 RGBA 图设成这个窗口的**整窗外观**（逐像素 alpha）。返回是否成功。

    `x` / `y` 传 None = **位置不动**（`UpdateLayeredWindow` 的 `pptDst` 收 None 就是
    这个语义）—— 位置由窗口自己的摆位逻辑管（Tk `geometry()` / `SetWindowPos`），
    两边都设位置的话动画中途会互相打架。尺寸永远跟着位图走，所以"窗口该多大"
    完全由渲染出的图决定，不会再出现"画布宽了窗口没跟着宽"。

    ★ 幂等：重复调用只是换位图。每次都补一次 `WS_EX_LAYERED` 是有意的 ——
      窗口被销毁重建（Tk 某些路径会重建顶层窗）、或者 `SetParent()` 换父窗之后
      风格位可能丢，而这里是唯一能保证它还在的地方；重复设没有副作用。
    ★ 失败（极老的系统 / 某些远程桌面模式）返回 False，调用方**必须有退路**。
    """
    W, H = img.size
    raw = premultiplied_bytes(img)
    hdc_dst = _u32.GetDC(0)
    hdc_src = _gdi.CreateCompatibleDC(hdc_dst)
    bmi = _BMI()
    bmi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
    bmi.bmiHeader.biWidth = W
    bmi.bmiHeader.biHeight = -H          # 负 = 自顶向下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    ppv = ctypes.c_void_p()
    hbm = _gdi.CreateDIBSection(hdc_src, ctypes.byref(bmi), DIB_RGB_COLORS,
                                ctypes.byref(ppv), None, 0)
    if not hbm:
        _gdi.DeleteDC(hdc_src)
        _u32.ReleaseDC(0, hdc_dst)
        return False
    ctypes.memmove(ppv, raw, len(raw))
    _gdi.SelectObject(hdc_src, hbm)
    ex = _u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    _u32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
    bf = _BF(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    p_dst = (None if (x is None or y is None)
             else ctypes.byref(_PT(int(x), int(y))))
    ok = _u32.UpdateLayeredWindow(hwnd, hdc_dst, p_dst,
                                  ctypes.byref(_SZ(W, H)), hdc_src,
                                  ctypes.byref(_PT(0, 0)), 0, ctypes.byref(bf),
                                  ULW_ALPHA)
    _gdi.DeleteObject(hbm)
    _gdi.DeleteDC(hdc_src)
    _u32.ReleaseDC(0, hdc_dst)
    return bool(ok)
