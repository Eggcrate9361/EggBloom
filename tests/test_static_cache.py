# -*- coding: utf-8 -*-
"""tests/test_static_cache.py —— 页面加载的缓存策略

背景（2026-10-25）
    用户反馈「页面加载缓慢」。实测不是前端渲染慢（切页 68~104 ms），
    而是**每次加载都完整重传**：

        文档   582,066 B   ← Cache-Control: no-store，浏览器连副本都不留
        字体   623,808 B   ← Cache-Control: no-cache 但没有 ETag/Last-Modified，
                              "可以存但每次要问"，服务器又没凭证可比，只能整份重发

    两者合计 1.2 MB，**每次刷新都从磁盘读一遍**。这台机器上程序装在
    E 盘（USB 移动硬盘），这笔开销尤其难看。

    修法：
      · HTML 从 no-store 改成 no-cache + ETag + Last-Modified
      · 静态资源加 ETag + Last-Modified，命中回 304（连读盘都省掉）
      · woff2 额外给 max-age=604800（字体基本不变）

    ★ Last-Modified 是关键的一环：只给 ETag 时 Chromium 对**顶层导航**
      根本不发条件请求（实测三次加载都没收到 If-None-Match）；
      补上 Last-Modified 之后它改用 If-Modified-Since，304 才生效。

实测效果：
    文档 582,066 → 300 B，字体 623,808 → 0 B。1.2 MB 降到 300 B。

这个文件锁四件事：
  1. HTML 不能再出现 no-store（那等于关掉缓存）
  2. HTML 与静态资源都要带 ETag 和 Last-Modified
  3. 带匹配的 If-None-Match 时回 304 且正文为空
  4. 内容变了（mtime 变）不能还回 304 —— 否则用户看到旧页面

纯 Python + 真起 HTTP 服务，不依赖浏览器。
"""
import os
import sys
import threading
import socketserver
import urllib.request
import urllib.error
import importlib.util
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def _load():
    spec = importlib.util.spec_from_file_location(
        "wui_cache_test", os.path.join(ROOT, "webui", "webui_server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    mod = _load()
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), mod.Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def get(path, headers=None):
        req = urllib.request.Request(base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    try:
        print("[1] 首页的缓存头")
        st, hd, body = get("/")
        check("首页 200", st == 200, st)
        cc = (hd.get("Cache-Control") or "").lower()
        check("没有 no-store（那等于关掉缓存）", "no-store" not in cc, cc)
        check("含 no-cache（改了能立刻看到）", "no-cache" in cc, cc)
        check("带 ETag", bool(hd.get("ETag")), hd.get("ETag"))
        check("带 Last-Modified（顶层导航靠它校验）",
              bool(hd.get("Last-Modified")), hd.get("Last-Modified"))
        check("首屏确实是完整文档（不是空壳）", len(body) > 100000, len(body))

        print("\n[2] 带 If-None-Match 应回 304")
        st2, hd2, body2 = get("/", {"If-None-Match": hd.get("ETag")})
        check("状态 304", st2 == 304, st2)
        check("正文为空", len(body2) == 0, len(body2))

        print("\n[3] 带 If-Modified-Since 也应回 304")
        st3, hd3, _ = get("/", {"If-Modified-Since": hd.get("Last-Modified")})
        check("状态 304", st3 == 304, st3)

        print("\n[4] 静态资源（字体）")
        fpath = "/fonts/fusion-pixel-12px-sc.woff2"
        st4, hd4, body4 = get(fpath)
        if st4 != 200:
            check("字体文件存在", False, st4)
        else:
            check("字体 200", True)
            check("带 ETag", bool(hd4.get("ETag")), hd4.get("ETag"))
            check("带 Last-Modified", bool(hd4.get("Last-Modified")),
                  hd4.get("Last-Modified"))
            check("字体给长缓存",
                  "max-age" in (hd4.get("Cache-Control") or ""),
                  hd4.get("Cache-Control"))
            st5, _h5, body5 = get(fpath, {"If-None-Match": hd4.get("ETag")})
            check("字体带 ETag 回 304", st5 == 304, st5)
            check("字体 304 正文为空", len(body5) == 0, len(body5))

        print("\n[5] 内容变了不能还回 304")
        # 用 If-Modified-Since 指向很久以前 → 必须 200
        st6, _h6, body6 = get("/", {"If-Modified-Since":
                                    "Thu, 01 Jan 1970 00:00:00 GMT"})
        check("过期校验值 → 200", st6 == 200, st6)
        check("且带完整正文", len(body6) > 100000, len(body6))
    finally:
        srv.shutdown()

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
