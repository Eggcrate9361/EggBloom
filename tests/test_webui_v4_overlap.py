# -*- coding: utf-8 -*-
"""
v4 遮挡审计（用户要求："检查一些各元素的遮挡问题"）。

为什么要有这个文件
------------------
v4 是**自由画布构图**（导航浮在左上、标题绝对定位、内容是斜置平面、
canvas 与刻度尺浮在右侧）。这类构图最容易出的问题是**互相遮挡**：
  · 轮盘压在内容面板上 → 点不到面板
  · 标题被面板盖住 → 看不见
  · 品牌/提示飘到面板中间 → 视觉噪音
  · 浮层（书架）忘了收起 → 长期占着内容
之前这些都只能靠肉眼发现，所以这里做成可重复的审计。

审计两类：
  A. **视觉遮挡**：轮盘 / 标题 / 品牌 / 提示 与 内容面板的矩形是否相交
  B. **真·遮挡**：面板上每个按钮，其**点击点最上层元素**是不是它自己
     （elementFromPoint）—— 这才是"用户真的点不到"的判据

运行：python tests/test_webui_v4_overlap.py
"""
import os

# 自动找到本机 ms-playwright 下的 chromium（pip 的 playwright 不带浏览器，
# 浏览器是单独下载、版本号跟库绑定）。详见 tests/_pw_chrome.py。
sys_ = __import__("sys")
sys_.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw_chrome import resolve_chrome as _resolve_chrome  # noqa: E402
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBUI = os.path.join(ROOT, "webui")
sys.path.insert(0, WEBUI)
sys.path.insert(0, ROOT)

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000d4944415478da63f8ffff3f0005fe02fea735c1d40000000049454e44ae426082")

# A. 不得与内容面板相交的元素（这些都是"浮在画布上"的构件）
FLOATERS = [".rack", ".brand", ".hint", ".top h2"]
# ★ 提醒：本文件曾漏掉「基座已有类名撞车」这一类（.slab 被基座 display:none），
#   所以下面加了「方块必须真的可见」的断言。
# 故意允许的：巨型页码是水印（在面板之下）、canvas/刻度尺是背景层
ALLOWED = [".stage::before", ".pv-canvas", ".pv-ruler", ".pv-strip"]


def rect(page, sel):
    return page.evaluate("""(sel) => {
        const e = document.querySelector(sel);
        if (!e) return null;
        const r = e.getBoundingClientRect();
        const cs = getComputedStyle(e);
        if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return null;
        if (r.width < 2 || r.height < 2) return null;
        return {x: r.x, y: r.y, w: r.width, h: r.height,
                right: r.right, bottom: r.bottom};
    }""", sel)


def overlap(a, b):
    if not a or not b:
        return 0.0
    x = max(0, min(a["right"], b["right"]) - max(a["x"], b["x"]))
    y = max(0, min(a["bottom"], b["bottom"]) - max(a["y"], b["y"]))
    return x * y


def main():
    import functools
    import http.server
    import socketserver
    import threading
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: 需要 playwright（pip install playwright && playwright install chromium）")
        return 0

    # 库装了 ≠ 浏览器在。本文件全部内容都靠浏览器量遮挡，缺环境应跳过而非判红。
    try:
        _probe = sync_playwright().start()
        _probe_b = _probe.chromium.launch(executable_path=_resolve_chrome())
        _probe_b.close()
        _probe.stop()
    except Exception as e:
        print("SKIP: 起不了浏览器（%s）：%s" % (type(e).__name__, str(e)[:120]))
        return 0

    class Q(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), functools.partial(Q, directory=WEBUI))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    fails, checks = [], 0
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=_resolve_chrome(),
                               args=["--use-gl=angle", "--enable-webgl", "--ignore-gpu-blocklist"])  # 本机 chromium-1228
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                  body='{"bot":{"status":"running","uptime":4281},'
                                                       '"protocol_running":true,"groups":12,'
                                                       '"messages_today":4080,"memes":37}'))
        pg.route("**/memes/*", lambda r: r.fulfill(status=200, content_type="image/png", body=PNG))
        pg.goto(f"http://127.0.0.1:{port}/webui_artistic_v4.html", wait_until="load")
        pg.wait_for_timeout(1800)

        n = pg.evaluate("() => (window.PAGES ? PAGES.length : 15)")
        for i in range(n):
            pg.evaluate("(i) => go(i)", i)
            pg.wait_for_timeout(320)
            panel = rect(pg, ".panel")
            checks += 1
            if panel is None:
                continue
            # A. 浮动物不得压住面板
            for sel in FLOATERS:
                checks += 1
                ov = overlap(panel, rect(pg, sel))
                if ov > 4:
                    fails.append("第%d页 %s 压住面板 %.0fpx²" % (i + 1, sel, ov))
            # 浮动件之间也不该互相压（曾经"标记/品牌/提示"三者叠在一起）
            mv, top = rect(pg, ".rack"), rect(pg, ".top h2")
            checks += 1
            if overlap(mv, top) > 4:
                fails.append("第%d页 导航标记与标题互相压 %.0fpx²" % (i + 1, overlap(mv, top)))
            br = rect(pg, ".brand")
            checks += 2
            if mv and br and overlap(mv, br) > 4:
                fails.append("第%d页 导航标记与品牌互相压 %.0fpx²" % (i + 1, overlap(mv, br)))
            if br and rect(pg, ".hint") and overlap(br, rect(pg, ".hint")) > 4:
                fails.append("第%d页 品牌与提示互相压 %.0fpx²"
                             % (i + 1, overlap(br, rect(pg, ".hint"))))
            # 面板不该横向溢出视口
            checks += 1
            if panel["right"] > 1441 or panel["x"] < -1:
                fails.append("第%d页 面板横向溢出（x=%.0f right=%.0f）"
                             % (i + 1, panel["x"], panel["right"]))

            # B. 真·遮挡：面板上按钮的点击点最上层是不是它自己
            hidden = pg.evaluate("""() => {
                const p = document.querySelector('.panel'); if (!p) return [];
                const out = [];
                p.querySelectorAll('button, a, input, select, textarea').forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.width < 4 || r.height < 4) return;
                    const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
                    if (cx < 0 || cy < 0 || cx > innerWidth || cy > innerHeight) return;
                    const top = document.elementFromPoint(cx, cy);
                    const ok = !!top && (top === el || el.contains(top) || top.contains(el));
                    if (!ok) out.push((el.id || el.className || el.tagName) +
                                      ' 被 ' + (top ? (top.id || top.className || top.tagName) : 'null')
                                      + ' 覆盖');
                });
                return out;
            }""")
            for h in hidden:
                checks += 1
                fails.append("第%d页 %s" % (i + 1, h))

        # C. 导航闸片架必须在左走廊内（右边界 < 内容起点），且不含"长条"遮挡
        checks += 1
        rk = rect(pg, ".rack")
        pn = rect(pg, ".panel")
        if rk and pn and rk["right"] > pn["x"] + 1:
            fails.append("闸片架侵入内容区（右界 %.0f > 内容起点 %.0f）" % (rk["right"], pn["x"]))
        checks += 1
        if pg.evaluate("() => document.querySelectorAll('.rack .blk').length") < 10:
            fails.append("方块数量异常（导航没渲染出来）")
        # ★ 必须**真的可见**：这类"类名撞车导致 display:none"的坑就是这么抓到的
        checks += 1
        vis = pg.evaluate("""() => {
            const b = document.querySelector('.rack');
            if (!b) return {err: 'no .rack'};
            const cs = getComputedStyle(b);
            const r = b.getBoundingClientRect();
            return {display: cs.display, w: Math.round(r.width), h: Math.round(r.height),
                    op: cs.opacity};
        }""")
        if vis.get("err"):
            fails.append("NAV 标记不存在：%s" % vis["err"])  # 折叠竖块已按用户定稿移除
        elif vis.get("display") == "none" or (vis.get("w") or 0) < 4 or (vis.get("h") or 0) < 4:
            fails.append("NAV 标记被隐藏（display=%s 尺寸=%sx%s）"
                         % (vis.get("display"), vis.get("w"), vis.get("h")))

        b.close()
    srv.shutdown()

    print("\n=== v4 遮挡审计 ===")
    print("检查点：%d" % checks)
    if fails:
        print("✗ 发现 %d 个遮挡问题：" % len(fails))
        for f in fails[:24]:
            print("   -", f)
        return 1
    print("✓ 未发现遮挡")
    return 0


if __name__ == "__main__":
    sys.exit(main())