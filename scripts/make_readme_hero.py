# -*- coding: utf-8 -*-
"""scripts/make_readme_hero.py —— 生成 README 顶部主视觉图，并附带整套落地页修改指南

═══════════════════════════════════════════════════════════════════
✏️  改 README 落地页的完整指南（改完存盘 → GitHub Desktop → Commit → Push）

  改文字        搜那句中文，直接改，保存
  改主视觉图    改本脚本开头几个常量，然后跑：
                    python scripts/make_readme_hero.py
                （产物 assets/readme-hero.png，不要手工修图——会被覆盖）
  改颜色        全局替换这三个值（来自 webui v4 的设计变量）：
                    16130F  墨  --ink
                    F0EEEA  纸  --paper
                    C9A96E  金  --accent
                改完主视觉图也要重跑一次脚本，图里的色才会跟上
  改链接        搜 href= ，换成你的地址
  改徽章文字    搜 img.shields.io ，URL 里 ? 前面那段就是要显示的字；
                中文要先做 URL 编码（python: urllib.parse.quote("文字")）
  改完不生效    GitHub 页面 Ctrl+F5 强刷；shields.io 是 CDN，可能缓存几分钟

  为什么标题是图片：GitHub 的 README 会剥掉 <script>、禁掉 @font-face，
  像素字体只能渲染成图，才能保证每个人看到的都一模一样。
═══════════════════════════════════════════════════════════════════

GitHub 的 README 会剥掉 <script>、禁掉 @font-face，所以像素字体的标题
只能**渲染成图片**才能保证每个人看到的都一样。这个脚本就是干这个的。

改这里就能改外观（都不用碰 README）：
    BG / INK / PAPER / ACCENT   四个色值（与 webui v4 同一套）
    TITLE / SUBTITLE            标题文字
    W / H                       画布尺寸

用法
    python scripts/make_readme_hero.py
产物
    assets/readme-hero.png
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "readme-hero.png")
FONT = os.path.join(ROOT, "webui", "fonts", "fusion-pixel-12px-sc.ttf")

BG = "#16130F"        # v4 --ink
GRID = "#1E1A16"      # 网格线，比底色亮一档
PAPER = "#F0EEEA"     # v4 --paper
ACCENT = "#C9A96E"    # v4 --accent
DIM = "#8A7350"       # v4 --accent-dim

TITLE = "EGGBLOOM"
SUBTITLE = "DIGITAL LIFEFORM // QQ"
W, H = 1200, 360


def hourglass(d, cx, cy, scale, frame, sand):
    """和 scripts/make_icons.py 同一套几何，放大版。"""
    s = scale

    def box(x, y, w, h):
        return [cx + x * s, cy + y * s, cx + (x + w) * s, cy + (y + h) * s]

    d.rectangle(box(-8, -14, 16, 3), fill=frame)          # 上横档
    d.rectangle(box(-8, 11, 16, 3), fill=frame)           # 下横档
    d.line([(cx - 6 * s, cy - 10 * s), (cx, cy)], fill=frame, width=max(2, int(2 * s)))
    d.line([(cx + 6 * s, cy - 10 * s), (cx, cy)], fill=frame, width=max(2, int(2 * s)))
    d.line([(cx, cy), (cx - 6 * s, cy + 10 * s)], fill=frame, width=max(2, int(2 * s)))
    d.line([(cx, cy), (cx + 6 * s, cy + 10 * s)], fill=frame, width=max(2, int(2 * s)))
    d.polygon([(cx - 4 * s, cy - 9 * s), (cx + 4 * s, cy - 9 * s), (cx, cy - 2 * s)], fill=sand)
    d.polygon([(cx - 4 * s, cy + 10 * s), (cx + 4 * s, cy + 10 * s), (cx, cy + 3 * s)], fill=sand)
    d.rectangle([cx - s // 2, cy - s // 2, cx + s // 2, cy + s // 2], fill=sand)


def main() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    # 12px 网格（v4 的网格语义），很淡
    for x in range(0, W, 24):
        d.line([(x, 0), (x, H)], fill=GRID, width=1)
    for y in range(0, H, 24):
        d.line([(0, y), (W, y)], fill=GRID, width=1)

    # 四角像素括号
    t = 3
    for (ox, oy, dx, dy) in ((24, 24, 1, 1), (W - 24, 24, -1, 1), (24, H - 24, 1, -1), (W - 24, H - 24, -1, -1)):
        x0, x1 = sorted((ox, ox + 26 * dx))
        y0, y1 = sorted((oy, oy + t * dy))
        d.rectangle([x0, y0, x1, y1], fill=ACCENT)
        x0, x1 = sorted((ox, ox + t * dx))
        y0, y1 = sorted((oy, oy + 26 * dy))
        d.rectangle([x0, y0, x1, y1], fill=ACCENT)

    # 左侧沙漏
    hourglass(d, 210, H // 2, 7, PAPER, ACCENT)

    # 右侧标题
    f_title = ImageFont.truetype(FONT, 108)
    f_sub = ImageFont.truetype(FONT, 30)
    x = 340
    d.text((x, 96), TITLE, font=f_title, fill=PAPER)
    tw = d.textlength(TITLE, font=f_title)
    d.rectangle([x, 232, x + tw, 238], fill=ACCENT)
    d.text((x, 262), SUBTITLE, font=f_sub, fill=ACCENT)

    im.save(OUT)
    print(f"已生成 {OUT}（{W}x{H}，{os.path.getsize(OUT) // 1024} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
