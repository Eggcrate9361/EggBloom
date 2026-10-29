# -*- coding: utf-8 -*-
"""把插件 UI 套件（kit.css）内联进所有插件页（1.5.10 精致化第一步）。

为什么内联而不是外链：插件页由 `/page?plugin_id=` 直接吐 HTML，相对路径解析不到
`plugins/_ui/kit.css`，加静态路由又要改主程序。内联最稳，且页面可离线自洽。
幂等：已含 `/*PV-KIT*/` 标记的页面跳过；`<!--PV-KIT-->` 占位符会被替换。
"""
import io
import os

ROOT = r"D:\git\EggBloom"
KIT = io.open(os.path.join(ROOT, "plugins", "_ui", "kit.css"), encoding="utf-8").read()
MARK = "/*PV-KIT*/"
BLOCK = '<style id="pv-kit">' + MARK + "\n" + KIT + "</style>"

changed, skipped = [], []
for base, dirs, files in os.walk(os.path.join(ROOT, "plugins")):
    dirs[:] = [d for d in dirs if d not in ("__pycache__", "data", "_ui", "assets")]
    for f in files:
        if f != "ui.html":
            continue
        p = os.path.join(base, f)
        t = io.open(p, encoding="utf-8").read()
        if MARK in t:
            skipped.append(p)
            continue
        if "<!--PV-KIT-->" in t:
            t = t.replace("<!--PV-KIT-->", BLOCK, 1)
        elif "</head>" in t:
            t = t.replace("</head>", BLOCK + "\n</head>", 1)
        else:
            t = BLOCK + "\n" + t
        io.open(p, "w", encoding="utf-8").write(t)
        changed.append(p)

print(f"injected kit into {len(changed)} pages, skipped {len(skipped)}")
for p in changed:
    print("  +", os.path.relpath(p, ROOT))
