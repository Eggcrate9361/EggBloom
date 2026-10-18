# -*- coding: utf-8 -*-
"""scripts/make_icons.py —— 生成应用图标（纯代码绘制，零设计软件）

风格锚定 v4 前端：像素硬边、有限色板、无渐变。
配色直接取自 webui_artistic_v4.html 的 CSS 变量：
    --ink #16130f / --paper #f0eeea / --accent #c9a96e / --accent-dim #8a7350

画在 32×32 的整数网格上（硬边），再按目标尺寸降采样并**吸附回色板**，
保证 16px 任务栏下不糊、不脏。

产物
    icon.png            256px，BrowserWindow / 任务栏图标
    icon-1024.png       主视觉（大图、安装页用）
    icon.ico            多尺寸（16/24/32/48/64/128/256），打 exe 用
    tray-icon.png       32px 浅色版，深色任务栏上的托盘图标
    assets/icon/*.png   两个方案的各尺寸与对比预览

用法
    python scripts/make_icons.py
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "icon")

INK = (0x16, 0x13, 0x0F)
PAPER = (0xF0, 0xEE, 0xEA)
ACCENT = (0xC9, 0xA9, 0x6E)
ACCENT_DIM = (0x8A, 0x73, 0x50)
PALETTE = (INK, PAPER, ACCENT, ACCENT_DIM)

BASE = 32          # 逻辑网格
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _new():
    return Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))


def hourglass(d, frame, sand, glass=2):
    """经典沙漏：上下横档 + 玻璃壁 + 沙。整体收窄一圈，给 1px 描边留位置。"""
    d.rectangle([8, 4, 23, 5], fill=frame)        # 上横档
    d.rectangle([8, 26, 23, 27], fill=frame)      # 下横档
    d.line([(10, 7), (15, 15)], fill=frame, width=glass)
    d.line([(21, 7), (16, 15)], fill=frame, width=glass)
    d.line([(15, 16), (10, 24)], fill=frame, width=glass)
    d.line([(16, 16), (21, 24)], fill=frame, width=glass)
    d.rectangle([15, 15, 16, 16], fill=sand)      # 颈部的沙
    d.polygon([(12, 8), (19, 8), (16, 12)], fill=sand)   # 上仓沙
    d.polygon([(12, 25), (19, 25), (16, 19)], fill=sand)  # 下仓沙


def variant_a():
    """A：纯沙漏 —— 16px 下最稳。"""
    im = _new()
    d = ImageDraw.Draw(im)
    hourglass(d, INK, ACCENT)
    return halo(im)


def variant_b():
    """B：实心气泡 + 反白沙漏 —— 16px 下靠"剪影"取胜。"""
    im = _new()
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([3, 4, 28, 22], radius=5, fill=INK)   # 气泡本体
    d.polygon([(8, 22), (8, 28), (14, 22)], fill=INK)          # 气泡尾巴
    d.rectangle([10, 8, 21, 9], fill=PAPER)                    # 反白横档
    d.rectangle([10, 17, 21, 18], fill=PAPER)
    d.line([(12, 11), (15, 14)], fill=PAPER, width=1)
    d.line([(19, 11), (16, 14)], fill=PAPER, width=1)
    d.line([(15, 15), (12, 16)], fill=PAPER, width=1)
    d.line([(16, 15), (19, 16)], fill=PAPER, width=1)
    d.polygon([(14, 11), (17, 11), (15, 13)], fill=ACCENT)     # 沙
    d.polygon([(14, 16), (17, 16), (15, 15)], fill=ACCENT)
    return halo(im)


def variant_tray():
    """托盘与主图标同构（有浅色描边，深浅背景都读得出）。"""
    return variant_a()


def halo(img, color=PAPER):
    """给整张图套 1px 浅色描边 —— 深色任务栏上不至于"消失"。"""
    from PIL import ImageFilter
    grown = img.filter(ImageFilter.MaxFilter(3))
    solid = Image.new("RGBA", img.size, color + (255,))
    solid.putalpha(grown.getchannel("A"))
    return Image.alpha_composite(solid, img)


def _nearest(rgb):
    return min(PALETTE, key=lambda c: sum((a - b) ** 2 for a, b in zip(rgb, c)))


def snap(img, size):
    """降采样 + 吸附回色板：保证小尺寸不出现中间色和锯齿脏边。"""
    r = img.resize((size, size), Image.BOX)
    px = r.load()
    for y in range(size):
        for x in range(size):
            red, green, blue, alpha = px[x, y]
            if alpha < 96:
                px[x, y] = (0, 0, 0, 0)
            else:
                c = _nearest((red, green, blue))
                px[x, y] = (c[0], c[1], c[2], 255)
    return r


def save_set(img, stem, root_dir):
    """一套尺寸落到磁盘：<stem>-{size}.png（只留 32/64/256，避免文件爆炸）。"""
    os.makedirs(root_dir, exist_ok=True)
    made = []
    for s in (32, 64, 128, 256):
        p = os.path.join(root_dir, f"{stem}-{s}.png")
        snap(img, s).save(p)
        made.append(p)
    return made


def preview(a, b):
    """对比图：两方案 × (256/64/32/16)，深色底模拟任务栏。"""
    cell, pad = 280, 18
    sheet = Image.new("RGB", (cell * 2 + pad * 3, cell + pad * 3 + 30), (0x0A, 0x0A, 0x0F))
    for col, (im, tag) in enumerate(((a, "A"), (b, "B"))):
        x0 = pad + col * (cell + pad)
        big = snap(im, 256)
        sheet.paste(big, (x0 + (cell - 256) // 2, pad), big)
        x = x0
        for s in (64, 32, 16):
            t = snap(im, s)
            sheet.paste(t, (x, pad + 280), t)
            x += s + 10
    p = os.path.join(OUT, "_preview.png")
    sheet.save(p)
    return p


def main():
    os.makedirs(OUT, exist_ok=True)
    a, b, tray = variant_a(), variant_b(), variant_tray()

    # 主视觉
    a.resize((1024, 1024), Image.NEAREST).save(os.path.join(ROOT, "icon-1024.png"))
    snap(a, 256).save(os.path.join(ROOT, "icon.png"))
    snap(tray, 32).save(os.path.join(ROOT, "tray-icon.png"))

    # 多尺寸 ico（从 256 主图切，Windows 资源图标需要多尺寸）
    snap(a, 256).save(os.path.join(ROOT, "icon.ico"),
                      sizes=[(s, s) for s in ICO_SIZES])

    # 方案留档
    save_set(a, "a-hourglass", OUT)
    save_set(b, "b-bubble", OUT)
    snap(tray, 256).save(os.path.join(OUT, "tray-256.png"))
    p = preview(a, b)

    print("icon.png / icon-1024.png / icon.ico / tray-icon.png 已更新")
    print("方案与预览：", OUT)
    print("预览图：", p)


if __name__ == "__main__":
    main()
