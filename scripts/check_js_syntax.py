# -*- coding: utf-8 -*-
"""内联 <script> 块的语法体检（改完 v4 立刻跑一次，比开浏览器看报错快得多）。

踩过的坑：三处括号/三元问题在浏览器里只表现为「go is not defined」——
顶层函数声明因为前面的语法错误**整块都不执行**，报错位置还指向不相干的行，
很容易误判成"页面加载失败"。所以先做纯语法检查。

用法：python scripts/check_js_syntax.py
返回码 0 = 语法通过。
"""
import io
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
V4 = os.path.join(ROOT, "webui", "webui_artistic_v4.html")

NODE_CANDIDATES = [
    os.environ.get("NODE_EXE", ""),   # 需要指定时自己设，例如 WorkBuddy 自带的 node
    "node",
]

s = io.open(V4, encoding="utf-8").read()
blocks = re.findall(r"<script>(.*?)</script>", s, re.S)
if not blocks:
    print("[!] 没找到内联 script")
    sys.exit(2)

fd, tmp = tempfile.mkstemp(suffix=".js")
os.close(fd)
io.open(tmp, "w", encoding="utf-8").write("\n".join(blocks))

node = next((n for n in NODE_CANDIDATES if n == "node" or os.path.exists(n)), None)
if not node:
    print("[!] 找不到 node，跳过语法检查")
    os.remove(tmp)
    sys.exit(0)

r = subprocess.run([node, "--check", tmp], capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
os.remove(tmp)
lines = "\n".join(blocks).splitlines()
if r.returncode != 0:
    m = re.search(r"_check\.js:(\d+)|" + re.escape(os.path.basename(tmp)) + r":(\d+)", r.stderr)
    if m:
        ln = int(m.group(1) or m.group(2))
        for i in range(max(0, ln - 4), min(len(lines), ln + 2)):
            mark = ">>" if i == ln - 1 else "  "
            print("%s %5d| %s" % (mark, i + 1, lines[i].rstrip()[:150]))
        print()
    print(r.stderr.strip()[:800])
    sys.exit(1)

print("[OK] 内联 script 语法通过（%d 个块 / %d 行）" % (len(blocks), len(lines)))