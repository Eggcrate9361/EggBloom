# -*- coding: utf-8 -*-
"""把 webui/fonts/*.woff2 内嵌成 data URI，生成**单文件**版界面。

为什么需要
----------
字体现在是外链（`src:url('fonts/xxx.woff2')`）。真实运行时没问题——后端
(webui_server.py) 和 Electron 都会提供 webui/ 目录。但两种场景会翻车：
  ① 双击打开 webui_v4.html（file://）→ 字体拿不到 → 像素字回落成系统字，
     评审时看到的观感和真实运行**不一样**（这正是设计评审最怕的）；
  ② 单文件分发（发给别人看、贴到聊天里）→ 必须连带 fonts/ 目录。

代价（实测）
----------
字体合计 608 KB（其中中文像素字 FusionPixelSC 占 588 KB），
base64 后 811 KB —— 全内嵌会让 HTML 从 380 KB 变成 1.19 MB（×3.1）。

所以这里**不动源文件**，只额外产出一个 standalone 版本：
源文件保持外链（可读、可 diff、版本库里干净），需要单文件时现生成。

用法
----
python scripts/embed_fonts.py            # → webui/webui_v4_standalone.html
python scripts/embed_fonts.py --out x.html
"""
import base64
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "webui", "webui_artistic_v4.html")
FONTS = os.path.join(ROOT, "webui", "fonts")

BANNER = """<!--
  ⚠ 这是**自动生成的单文件版**（字体已内嵌为 data URI，可直接双击打开）。
  源文件是 webui/webui_artistic_v4.html —— 要改界面请改源文件，然后跑
  scripts/embed_fonts.py 重新生成本文件。
  手工编辑这里不会影响源文件，下次生成会被覆盖。
-->"""


def main():
    out = None
    args = sys.argv[1:]
    if "--out" in args:
        i = args.index("--out")
        out = args[i + 1] if len(args) > i + 1 else None
    dst = out or os.path.join(ROOT, "webui", "webui_v4_standalone.html")

    s = io.open(SRC, encoding="utf-8").read()
    total = 0
    n = 0

    def repl(m):
        nonlocal total, n
        url = m.group(1)
        name = os.path.basename(url)
        fp = os.path.join(FONTS, name)
        if not os.path.isfile(fp):
            print("  ! 缺字体文件，跳过：%s" % name)
            return m.group(0)
        raw = open(fp, "rb").read()
        total += len(raw)
        n += 1
        b64 = base64.b64encode(raw).decode("ascii")
        print("  + %-34s %6.1f KB → base64 %6.1f KB" % (name, len(raw) / 1024, len(b64) / 1024))
        return "src:url(data:font/woff2;base64,%s) format('woff2')" % b64

    s2 = re.sub(r"src:url\('(fonts/[^']+\.woff2)'\) format\('woff2'\)", repl, s)
    if n == 0:
        print("没有找到可内嵌的 @font-face（源文件的写法变了？）")
        return 1

    s2 = s2.replace("<head>", "<head>\n" + BANNER, 1)
    io.open(dst, "w", encoding="utf-8").write(s2)
    a = os.path.getsize(SRC) / 1024
    b = os.path.getsize(dst) / 1024
    print("\n内嵌 %d 个字体（原始 %.1f KB）" % (n, total / 1024))
    print("写出：%s" % dst)
    print("体积：%.1f KB → %.1f KB（×%.2f）" % (a, b, b / a))
    return 0


if __name__ == "__main__":
    sys.exit(main())