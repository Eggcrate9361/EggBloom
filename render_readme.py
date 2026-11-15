# -*- coding: utf-8 -*-
"""README.md → GitHub 风格 HTML 预览（本地渲染，不发网）。

为什么要做：README 是 markdown，GitHub 的渲染效果（表格/徽章/折叠块/内联 HTML）
在本地看不出来。用户要确认"好不好看"，就得渲染一版。

做法：
  1. markdown 库转 HTML（README 用到的语法有限：表格/列表/代码块/引用/details/链接/图片）
  2. 套一份近似 GitHub 的 CSS（浅色主题，GitHub 的 md 容器宽度 1012px）
  3. 图片相对路径保持原样，HTML 放仓库根目录 → 直接能加载 assets/
  4. 无头浏览器截图（首屏 + 整页），同时交付 HTML 本体

    python render_readme.py
→ README_preview.html / README_preview.png
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "README.md")
OUT_HTML = os.path.join(ROOT, "README_preview.html")

import markdown  # noqa: E402

# ── CSS：近似 GitHub 的 markdown 容器 ──────────────────────────────
# 取 GitHub 浅色主题的几个关键值：字体栈、行高、表格斑马纹、代码块底色、引用左边线
CSS = """
:root {
  --fg: #1F2328; --fg-muted: #59636E; --border: #D1D9E0;
  --bg: #FFFFFF; --bg-subtle: #F6F8FA; --accent: #0969DA;
  --table-stripe: #F6F8FA;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans",
               Helvetica, Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 16px; line-height: 1.6;
}
.md {
  max-width: 1012px; margin: 0 auto; padding: 32px 32px 64px;
}
.md h1, .md h2, .md h3, .md h4 {
  margin: 24px 0 16px; font-weight: 600; line-height: 1.25;
}
.md h1 { font-size: 2em; padding-bottom: .3em; border-bottom: 1px solid var(--border); }
.md h2 { font-size: 1.5em; padding-bottom: .3em; border-bottom: 1px solid var(--border); }
.md h3 { font-size: 1.25em; }
.md h4 { font-size: 1em; }
.md p { margin: 0 0 16px; }
.md a { color: var(--accent); text-decoration: none; }
.md a:hover { text-decoration: underline; }
.md table {
  border-collapse: collapse; display: block; width: max-content;
  max-width: 100%; overflow: auto; margin: 0 0 16px;
  font-size: 14px;
}
.md table th, .md table td {
  border: 1px solid var(--border); padding: 6px 13px;
}
.md table th { background: var(--bg-subtle); font-weight: 600; }
.md table tr:nth-child(2n) { background: var(--table-stripe); }
.md code {
  background: rgba(129, 139, 152, .12); padding: .2em .4em;
  border-radius: 6px; font-size: 85%;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
.md pre {
  background: var(--bg-subtle); padding: 16px; border-radius: 6px;
  overflow: auto; margin: 0 0 16px; line-height: 1.45;
}
.md pre code { background: none; padding: 0; font-size: 100%; }
.md blockquote {
  margin: 0 0 16px; padding: 0 1em; color: var(--fg-muted);
  border-left: .25em solid var(--border);
}
.md ul, .md ol { margin: 0 0 16px; padding-left: 2em; }
.md li + li { margin-top: .25em; }
.md hr { height: 1px; border: 0; background: var(--border); margin: 24px 0; }
.md img { max-width: 100%; vertical-align: middle; }
.md details {
  border: 1px solid var(--border); border-radius: 6px;
  padding: 8px 16px; margin: 0 0 16px; background: var(--bg-subtle);
}
.md details summary { cursor: pointer; font-weight: 600; padding: 4px 0; }
.md details[open] summary { margin-bottom: 8px; }
/* 徽章行居中（README 里 <p align=center> 包着 shields.io 图） */
.md p[align="center"] { text-align: center; }
/* 顶部 hero 表格（内联 HTML）占满宽度 */
.md > table:first-child { width: 100%; }
"""

# ── markdown → HTML ────────────────────────────────────────────────
# README 用到的扩展：tables（表格）、fenced_code（代码块）、sane_lists
# details/summary 是内联 HTML，markdown 库默认会放行（不转义）
md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
body = md.convert(open(SRC, encoding="utf-8").read())

# shields.io 徽章在无网环境下会裂图，加个 onerror 兜底成文字
body = re.sub(
    r'<img src="(https://img\.shields\.io/[^"]+)" alt="([^"]*)"[^>]*>',
    r'<img src="\1" alt="\2" loading="lazy" '
    r'onerror="this.outerHTML=this.alt">',
    body,
)

html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EggBloom README 预览</title>
<style>{CSS}</style>
</head><body><article class="md">
{body}
</article></body></html>
"""

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print("已生成:", OUT_HTML, f"({len(html)} 字符)")