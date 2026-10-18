# -*- coding: utf-8 -*-
"""P0-4 单测：`/memes/*` 路径校验（目录穿越）。

缺陷（手册 §1.3(8)）：
  `_serve_meme` 直接 `meme_dir / rel` 读盘，**没有 realpath + 前缀校验**。
  `/memes/../../bot_config.json` → `PROJECT/memes/memes/../../bot_config.json`
  = `PROJECT/bot_config.json` → 可直接读出 onebot_token。

影响面（如实标注，不夸大）：该响应不带 ACAO、且有 Host 校验挡 DNS rebinding，
**属本地加固缺陷，不是远程可利用漏洞**。但它确实能读到程序目录下的任意文件。

注：手册把它描述为「静态分支判定失败后不 return 导致落到 memes 分支」。
回查控制流后发现那个描述不准确 —— `/memes/x.png` 本来就要先经过静态分支、
失败后再落到 memes 分支，**落下是正常路径**（静态分支只服务 webui/ 下的资源）。
真正的漏洞在 `_serve_meme` 缺容器校验，本测试直接针对这一点。
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "webui"))

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


import webui_server as W  # noqa: E402


class _Self:
    """假的 HTTPServer handler：只收集响应，不碰 socket。"""

    def __init__(self):
        self.code = None
        self.headers = []
        self.body = b""

    def send_response(self, c):
        self.code = c

    def send_header(self, k, v):
        self.headers.append((k, v))

    def end_headers(self):
        pass

    def write(self, d):
        self.body += d

    @property
    def wfile(self):
        return self


print("=== A. _safe_meme_rel：只放行安全相对路径 ===")
ok_cases = {
    "a.png": "a.png",
    "sub/a.png": "sub/a.png",
    "a/./b.png": "a/b.png",
    "sub\\a.png": "sub/a.png",
}
for src, want in ok_cases.items():
    got = W._safe_meme_rel(src)
    check(f"放行 {src!r} → {want!r}", got == want, got)

bad_cases = ["", None, "..", "../secret.txt", "../../bot_config.json",
             "a/../../secret.txt", "..\\..\\secret.txt", "/etc/passwd",
             "/memes/x.png", "//a.png", "C:/Windows/win.ini", "c:\\Windows\\win.ini",
             "~/secret", "./../x"]
for src in bad_cases:
    check(f"★ 拒绝 {src!r}", W._safe_meme_rel(src) is None, W._safe_meme_rel(src))

print("\n=== B. _within：容器判定 ===")
with tempfile.TemporaryDirectory() as td:
    base = os.path.join(td, "base")
    os.makedirs(os.path.join(base, "sub"), exist_ok=True)
    inside = os.path.join(base, "sub", "a.png")
    open(inside, "wb").close()
    outside = os.path.join(td, "secret.txt")
    open(outside, "wb").write(b"TOKEN")

    check("容器内文件 → True", W._within(inside, base))
    check("★ 容器外的文件 → False", not W._within(outside, base))
    check("父目录 → False", not W._within(td, base))
    # 大小写不敏感（Windows）
    check("大小写差异不影响判定（Windows）", W._within(inside.upper(), base))

    print("\n=== C. _serve_meme 端到端：穿越被挡住、正常图能出 ===")
    memes = os.path.join(base, "memes", "memes")
    os.makedirs(memes, exist_ok=True)
    open(os.path.join(memes, "ok.png"), "wb").write(b"\x89PNG-fake")
    _saved_dirs = W._meme_dirs
    W._meme_dirs = lambda: [type(W.PROJECT)(memes)] if W.PROJECT else []
    try:
        s = _Self()
        served = W._serve_meme(s, "ok.png")
        check("正常表情包 → 服务成功", served is True, served)
        check("正常表情包 → 200 且写出内容", s.code == 200 and s.body == b"\x89PNG-fake",
              (s.code, s.body[:20]))

        for evil in ("../secret.txt", "../../secret.txt", "..\\..\\secret.txt",
                     "sub/../../secret.txt"):
            s2 = _Self()
            r = W._serve_meme(s2, evil)
            check(f"★ 穿越 {evil!r} → 拒绝且不写出任何内容",
                  r is False and s2.body == b"" and s2.code is None,
                  (r, s2.code, s2.body[:20]))
    finally:
        W._meme_dirs = _saved_dirs

src = open(os.path.join(ROOT, "webui", "webui_server.py"), encoding="utf-8").read()

print("\n=== E. 同类第二处：/api/memes/tags 是**写盘**路径（比读更严重）===")
check("★ tags 端点已先做 _safe_meme_rel 校验",
      "_safe_meme_rel(name)" in src)
check("★ tags 端点校验写入目标仍在 meme 目录内",
      "not _within(target, d_base)" in src and "not _within(tag_file.resolve(), d_base)" in src)
check("★ tags 端点非法名返回可读错误而非写盘",
      "'非法的表情包名'" in src)
check("★ import 端点也加了容器校验（纵深防御）",
      "_within(dest.resolve(), meme_dir.resolve())" in src)

print("\n=== D. 结构性证据：裸拼接已消失 ===")
check("★ 读路径已无 `= meme_dir / rel` 赋值形式", "= meme_dir / rel\n" not in src and "file_path = meme_dir" not in src)
check("★ 有 _within / _safe_meme_rel 两个校验函数",
      "def _within(" in src and "def _safe_meme_rel(" in src)
check("★ 主循环用 resolve() 后的路径做容器校验",
      "_within(file_path, base)" in src)
check("★ rglob 回退路径也校验容器（防符号链接逃逸）",
      "candidate.resolve()" in src and "_within(candidate.resolve()" in src)
check("★ 回退路径只取 basename（不含上溯）", "Path(safe_rel).name" in src)

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
