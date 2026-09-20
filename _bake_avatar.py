# -*- coding: utf-8 -*-
"""把用户给的原始动图烘成球心素材 `ball_avatar.gif`（可反复重跑，结果稳定）。

★★ 为什么必须烘，而不是把用户那张原图直接塞进仓库：
   原图 = **337x270 / 694 帧 / 每帧 40ms / 5.1MB**（一轮 27.8 秒）。
   而球心最大只画到 ~75px（见 `ui_render.AVATAR_SIDE` 的注释）。不烘的话：
     · 仓库和安装包白胖 5MB；
     · `avatar_frames()` 是"整只 GIF 解完再逐帧缩"，694 帧 337x270 的 RGBA
       = **253MB** —— 在用户这台常年只剩 2GB 空闲的机器上就是一次 OOM 级尖峰。
   烘完 = 347 帧 / 96x96 / 80ms，运行时掩码常驻 ~3.2MB（和上一张照片素材同量级）。

三道手续，都可复算：
   ① **隔帧取样**（40ms → 80ms）：主体是慢速变形，12.5fps 够看；一轮仍是 27.8 秒
      （时间轴按"帧自己的时长"走，所以只改时长、不改节奏）。
   ② **整帧等比装进 96×96**，一个像素都不裁 —— **和线上 `_fit_square` 同一套规矩**
      （直接复用那个函数，别在这边另写一份，不然"素材里的构图"和"屏幕上贴的构图"
      就成两个来源了）。线稿并集包围盒本来就是整幅（人物会走到画面边缘），
      所以"按人物裁"在这张图上没有意义。
   ③ **不做反相、不改色**：素材只管忠实，怎么画是渲染层的事
      （`ui_render.classify_avatar` 认线稿，`_to_ink_mask` 把黑当透明、抬亮线条）。

用法：
    python _bake_avatar.py                # 默认源图 / 默认 96px
    python _bake_avatar.py <src.gif> 128
"""
import sys
from pathlib import Path

from PIL import Image, ImageSequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ui_render as _ur  # noqa: E402

DEFAULT_SRC = Path(r"C:\Users\xinyu\Downloads\778F7E7EB01122DC42082A2591101ED8.gif")
STEP = 2                    # 隔帧取样：2 → 时长翻倍（40ms → 80ms）
SIDE = 96                   # 烘出来的边长：≥ 屏幕上最大直径(~75px) 就不会被放大，且只有 3.2MB


def bake(src, side=SIDE, step=STEP, out=None):
    out = Path(out or (HERE / _ur.AVATAR_FILE))
    im = Image.open(src)
    keep, durs = [], []
    for i, f in enumerate(ImageSequence.Iterator(im)):
        d = max(10, int(f.info.get("duration") or 100))
        if i % step == 0:
            #   ★ 只 `copy()`（P 模式副本 ~9KB/帧），**不**在这里 convert：
            #     先全转 RGBA 就是上面说的 253MB 尖峰。
            #     另外 `ImageSequence.Iterator` 交出来的**是同一个对象**（内部 seek 复用），
            #     不 copy 的话列表里会全是最后一帧 —— 这个错不报错，只是所有帧长一样。
            keep.append(f.copy())
            durs.append(0)
        durs[-1] += d                       # 被丢掉的那一帧的时长，算到留下来的那帧头上
    if not keep:
        sys.exit("源图一帧都没解出来：" + str(src))

    frames = [_ur._fit_square(f, side).convert("L") for f in keep]
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=durs, loop=0, optimize=True, disposal=2)

    # 自证：尺寸 / 帧数 / 时长 / 体积 / 亮度分布（后者用来核对线稿判据的阈值）
    chk = Image.open(out)
    hist = frames[0].histogram()
    tot = float(side * side)
    dark = sum(hist[:16]) / tot * 100.0
    lit = sum(hist[128:]) / tot * 100.0
    print("源   %s" % src)
    print("源   %dx%d  %d 帧  %s KB" % (im.size + (getattr(im, "n_frames", len(durs)),
                                                   round(Path(src).stat().st_size / 1024.0, 1))))
    print("出   %s" % out)
    print("出   %dx%d  %d 帧  每帧 %sms  一轮 %.1fs  %s KB"
          % (chk.size + (chk.n_frames, durs[0], sum(durs) / 1000.0,
                         round(out.stat().st_size / 1024.0, 1))))
    print("第 0 帧亮度分布：近黑(<16) %.2f%% · 亮线(>=128) %.2f%%"
          "（渲染层的线稿判据：近黑 >= %d%% 且亮线 <= %d%%）"
          % (dark, lit, _ur.AVATAR_INK_BLACK * 100, _ur.AVATAR_INK_LIT * 100))
    print("判据判定 = %s（应当 = ink）" % _ur.classify_avatar(frames[0]))
    return out


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    side = int(sys.argv[2]) if len(sys.argv) > 2 else SIDE
    bake(src, side)
