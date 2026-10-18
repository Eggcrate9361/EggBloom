# -*- coding: utf-8 -*-
"""把 GPT 生成的 5 格横排图样剪成单张透明 PNG，供螺旋卡片使用。

用法：
  python scripts/slice_card_icons.py [--check]

- 自动检测 5 个图样的包围盒（按列投影分组，不依赖等分网格）
- 亮度反相映射做去背（米白底 → alpha 0，线条保留原色）
- 每张统一放到 512x512 画布中央（图样缩放到占 78% 内）
- 输出 webui/card-icons/01.png ... 15.png
"""
import sys, glob, os
from PIL import Image

# 三张生图 → 页序号映射（按图样内容识别：圆环/接线/均衡器/散点/取景框 = 01-05 …）
SRC = [
    (r"C:/Users/Administrator/.workbuddy/clipboard-images/*f9ee9e33*", [1, 2, 3, 4, 5]),
    (r"C:/Users/Administrator/.workbuddy/clipboard-images/*23d79577*", [6, 7, 8, 9, 10]),
    (r"C:/Users/Administrator/.workbuddy/clipboard-images/*3076d111*", [11, 12, 13, 14, 15]),
]
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webui", "card-icons")
CANVAS = 512
CONTENT_RATIO = 0.78      # 图样在画布中的占比
BG_DIST = 26              # 与背景色的曼哈顿距离阈值（>此值认为是内容）
COL_MIN = 2               # 一列至少这么多内容像素才算有效列


def content_mask(img):
    """返回二值 mask 的列/行投影（内容=True）。"""
    w, h = img.size
    px = img.convert("RGB").load()
    bg = px[2, 2]
    cols = [0] * w
    rows = [0] * h
    for y in range(0, h, 2):               # 隔行采样，够用且快
        for x in range(w):
            r, g, b = px[x, y]
            if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > BG_DIST:
                cols[x] += 1
                rows[y] += 1
    return cols, rows


def groups_from(cols, min_count):
    """把列投影分成连续组（每组 = 一个图样的水平范围）。"""
    gs, cur = [], None
    for x, c in enumerate(cols):
        if c >= min_count:
            if cur is None:
                cur = [x, x]
            else:
                cur[1] = x
        else:
            if cur is not None and x - cur[1] > 24:      # 间隔 >24px 视为断开
                gs.append(cur); cur = None
            elif cur is not None:
                pass
    if cur:
        gs.append(cur)
    return gs


def slice_one(img, box, out_path):
    """裁切 + 去背 + 居中缩放。"""
    crop = img.crop(box).convert("RGB")
    # 去背：亮度越接近背景越透明
    bg_lum = sum(img.getpixel((2, 2))) / 3.0
    lum = crop.convert("L").load()
    w, h = crop.size
    src = crop.load()
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dst = out.load()
    for y in range(h):
        for x in range(w):
            l = lum[x, y]
            # 背景亮度 ~248；线条最暗 ~20。反相映射
            a = int(max(0.0, min(1.0, (bg_lum - 12 - l) / (bg_lum - 32))) * 255)
            r, g, b = src[x, y]
            dst[x, y] = (r, g, b, a)
    # ★ 清除中央残留（AI 可能在图样中心写了数字/编号；中心本应留白）
    #   只擦**金色/深色**像素 —— 灰色图样线条（环等）必须保留，
    #   否则环会被擦出缺口（76px 全擦实测踩过）。
    ER = (64, 52)
    for y in range(max(0, h // 2 - ER[1]), min(h, h // 2 + ER[1])):
        for x in range(max(0, w // 2 - ER[0]), min(w, w // 2 + ER[0])):
            r0, g0, b0, a0 = dst[x, y]
            if a0 == 0:
                continue
            # ★ 只擦**强金色**（AI 画的金色编号）——灰环的抗锯齿边缘偏暖，
            #   判定放宽会把环擦断（13 卡实测踩过）
            goldish = r0 > 140 and (r0 - b0) > 60 and (r0 - g0) > 20
            if goldish:
                dst[x, y] = (0, 0, 0, 0)

    # ★ 质心锚定：按内容强度加权求质心，把**质心**放到画布中心。
    #   （bbox 居中不行：生成图里"环 + 左右横线"本身错位 ~7px，
    #     环的像素量最大 → 质心≈环心 → 序号与环对齐；对称图样质心≈几何中心）
    cx = cy = wsum = 0.0
    for y in range(h):
        for x in range(w):
            a = dst[x, y][3]
            if a:
                cx += x * a; cy += y * a; wsum += a
    if wsum <= 0:
        cx, cy = w / 2.0, h / 2.0
    else:
        cx /= wsum; cy /= wsum
    scale = (CANVAS * CONTENT_RATIO) / max(w, h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    out = out.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    px = int(round(CANVAS / 2 - cx * scale))
    py = int(round(CANVAS / 2 - cy * scale))
    canvas.paste(out, (px, py), out)
    canvas.save(out_path)
    return (w, h), (nw, nh), (round(cx), round(cy))


def main():
    check = "--check" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)
    for pattern, ids in SRC:
        files = glob.glob(pattern)
        if not files:
            print("!! 找不到：", pattern); continue
        img = Image.open(files[0]).convert("RGB")
        W, H = img.size
        # ★ 5 等分网格 + 格内收紧到内容包围盒（GPT 生图是等分布；
        #   "环 + 两侧横线"这类图样用列分组会被拆开，网格法稳）
        print("%s 尺寸=%s 按 5 等分网格" % (os.path.basename(files[0])[:12], img.size))
        step = W / 5.0
        merged = []
        for i in range(5):
            # ★ 以格中心为锚，只取 ±0.42 格宽窗口内的内容 ——
            #   等分格边界会混入隔壁图样的元素（取景框括号跨格实测踩过）
            c_mid = (i + 0.5) * step
            w_half = step * 0.42
            cx0, cx1 = int(max(0, c_mid - w_half)), int(min(W, c_mid + w_half))
            sub = img.crop((cx0, 0, cx1, H)).convert("RGB")
            cols2, _ = content_mask(sub)
            xs = [x for x, c in enumerate(cols2) if c >= 1]
            if xs:
                merged.append([cx0 + min(xs), cx0 + max(xs)])
            else:
                merged.append([cx0, cx1])
        for i, g in enumerate(merged):
            x0, x1 = g
            # 行范围：在该图样列范围内找内容行
            sub = img.crop((x0, 0, x1 + 1, H)).convert("RGB")
            _, rws = content_mask(sub)
            ys = [y for y, c in enumerate(rws) if c >= 1]
            y0, y1 = (min(ys), max(ys)) if ys else (0, H)
            pad = 10
            box = (max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad))
            if i < len(ids):
                out = os.path.join(OUT_DIR, "%02d.png" % ids[i])
                if check:
                    print("   #%02d box=%s" % (ids[i], box))
                else:
                    print("   #%02d box=%s -> %s" % (ids[i], box, slice_one(img, box, out)))
        if len(merged) != 5:
            print("   ⚠ 检出 %d 组（期望 5）—— 需人工核对" % len(merged))
    print("完成 ->", OUT_DIR)


if __name__ == "__main__":
    main()
