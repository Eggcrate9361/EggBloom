# -*- coding: utf-8 -*-
"""scripts/make_flow_diagram.py —— 生成「群里的一次发言决策」示意图

README 的「它在群里具体是这样」用图比用代码块更清楚。
与 make_readme_hero.py 同一套色板与网格（WebUI v4 设计变量）。

    python scripts/make_flow_diagram.py
→ assets/gate-flow.png（1200x520）
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "gate-flow.png")
FONT = os.path.join(ROOT, "webui", "fonts", "fusion-pixel-12px-sc.ttf")

BG = "#16130F"
GRID = "#1E1A16"
PAPER = "#F0EEEA"
ACCENT = "#C9A96E"
DIM = "#8A7350"
LINE = "#3A332B"

W, H = 960, 540
U = 24  # 网格单位


def main() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    for x in range(0, W, U):
        d.line([(x, 0), (x, H)], fill=GRID, width=1)
    for y in range(0, H, U):
        d.line([(0, y), (W, y)], fill=GRID, width=1)

    f_h = ImageFont.truetype(FONT, 26)
    f_t = ImageFont.truetype(FONT, 22)
    f_s = ImageFont.truetype(FONT, 18)
    f_xs = ImageFont.truetype(FONT, 15)

    # 标题
    d.text((48, 40), "一次发言决策", font=f_h, fill=PAPER)
    d.rectangle([48, 78, 48 + 130, 80], fill=ACCENT)
    d.text((48, 92), "每条群消息都要过这几道闸", font=f_xs, fill=DIM)

    # 入口
    y0 = 150
    rows = [
        ("收到消息", "群里有人说话，先不下判断", PAPER),
        ("值不值得回", "价值 / 时机 / 风险 多维评分（链式思考）", PAPER),
        ("在叫我吗", "@我 / 叫我名字 / 引用我 —— 引用别人不算", PAPER),
        ("他在追问吗", "刚回过他 → 接话窗口内直接放行（300 秒）", ACCENT),
        ("真没人说话了", "别急着走，潜下去之前再看 25 秒", ACCENT),
        ("决定", "回 / 不回 / 再等等", PAPER),
    ]

    box_x, box_w, box_h = 96, 400, 48
    gap = 62
    for i, (title, note, color) in enumerate(rows):
        cy = y0 + i * gap

        # 连接线（上一格 → 本格）
        if i > 0:
            py = y0 + (i - 1) * gap + box_h
            d.line([(box_x + box_w // 2, py), (box_x + box_w // 2, cy)], fill=LINE, width=2)
            mx = box_x + box_w // 2
            d.polygon([(mx - 5, cy - 7), (mx + 5, cy - 7), (mx, cy)], fill=ACCENT)

        # 方块
        d.rectangle([box_x, cy, box_x + box_w, cy + box_h], fill="#1B1713", outline=LINE, width=1)
        d.rectangle([box_x, cy, box_x + 4, cy + box_h], fill=color)

        # 文字垂直居中（像素字体基线偏上，按 bbox 校正）
        bb = d.textbbox((0, 0), title, font=f_t)
        d.text((box_x + 22, cy + (box_h - (bb[3] - bb[1])) // 2 - bb[1]), title, font=f_t, fill=color)

        bb2 = d.textbbox((0, 0), note, font=f_s)
        d.text((box_x + box_w + 32, cy + (box_h - (bb2[3] - bb2[1])) // 2 - bb2[1]),
               note, font=f_s, fill=DIM)

    # 右下角装饰刻度
    for i in range(7):
        px = W - 48 - i * 13
        d.rectangle([px, H - 44, px + 4, H - 40], fill=ACCENT if i < 4 else LINE)

    im.save(OUT)
    print(f"已生成 {OUT}（{W}x{H}，{os.path.getsize(OUT) // 1024} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())