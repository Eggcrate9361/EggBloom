# -*- coding: utf-8 -*-
"""v4 导航↔页面 同步回归（用户反馈："页面有时候跟不上导航栏的切换"）

为什么要有这个文件
------------------
`go(i)` 里有一条 420ms 的 `busy` 窗口。旧实现写的是
    if (busy || i === cur) return;
→ 窗口期内的切页请求会被**静默丢弃**：导航连跳 5 页，内容还停在第 1 页，
而且**不会自己补上**（只有下一次切换才会重新对齐）。
这类 bug 静态检查、页面契约测试都抓不到 —— 必须用**时序**去撞它。

现在 go() 改成"合并到最新"：busy 期间记住最后一个目标，渲染完补切一次。
本文件就是守这条线的：任何让"导航页 ≠ 内容页"的改动都会在这里红。

运行：python tests/test_webui_v4_nav_sync.py
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
V4 = os.path.join(WEBUI, "webui_artistic_v4.html")

PASS, FAIL, SKIP = [], [], []


def check(name, cond, extra=None):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "!!", name,
                          "" if extra is None else "  → %s" % (extra,)))


try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    SKIP.append("未安装 Playwright（%s）" % type(e).__name__)
    sync_playwright = None

# 库装了 ≠ 浏览器在。Playwright 的浏览器是**单独下载**的、版本号还跟库绑定，
# 所以 import 成功完全可能 launch 失败（CI 的 windows-latest 就是这种状态）。
# 这一段先试真正起一个浏览器：起不来就按本文件的设计意图**跳过几何检查**，
# 但前面的静态检查（A 段）照跑 —— 不能让"环境缺浏览器"把整个套件判红。
if sync_playwright:
    try:
        with sync_playwright() as _pw:
            _b = _pw.chromium.launch(executable_path=_resolve_chrome())
            _b.close()
    except Exception as e:
        SKIP.append("Playwright 起不了浏览器（%s）" % type(e).__name__)
        # 说明：本文件没有 skip() 辅助，SKIP 列表在末尾会打印出来，
        # 而且下面 `if sync_playwright:` 的整段会被跳过 —— 效果等同 skip。
        sync_playwright = None

# 页面 id 顺序（PAGES 数组）—— 用来把"内容标题"换算成页号
NAMES = ["仪表盘", "协议", "群管理", "表情包", "生图", "群聊行为", "Token",
         "配置中心", "Bot", "日志", "通知", "控制台", "记忆", "插件", "Agent"]

PROBE = """() => {
  const h2 = document.querySelector('.top h2');
  const on = document.querySelector('.ni.on .tx');
  return { nav: window.__pvPage,
           title: h2 ? h2.textContent.trim() : null,
           rack: on ? on.textContent.trim() : null,
           pageAttr: document.documentElement.getAttribute('data-page') };
}"""

if sync_playwright:
    print("\n=== 导航↔页面 同步（时序）===")
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=_resolve_chrome())
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.route("**/api/**", lambda r: r.abort())
        pg.goto("file:///" + V4.replace("\\", "/"), wait_until="load")
        pg.wait_for_timeout(1200)

        cases = [
            ("连切 5 页 @60ms（快速滚轮）", "for (let k = 1; k <= 5; k++) setTimeout(() => go(k), k * 60);", 5),
            ("连切 8 页 @30ms（连按方向键）", "for (let k = 6; k <= 13; k++) setTimeout(() => go(k), k * 30);", 13),
            ("回到第 1 页再连切 4 页", "go(0); setTimeout(() => { for (let k = 1; k <= 4; k++) setTimeout(() => go(k), k * 25); }, 900);", 4),
        ]
        for label, js, want in cases:
            pg.evaluate("(js) => eval(js)", js)
            pg.wait_for_timeout(2600)
            r = pg.evaluate(PROBE)
            idx = NAMES.index(r["title"]) if r["title"] in NAMES else None
            check("%s → 导航第 %d 页" % (label, want),
                  r["nav"] == want, "导航=%s" % r["nav"])
            check("  ↳ 内容跟上（标题=%s）" % r["title"], idx == want,
                  "内容页号=%s 期望=%s" % (idx, want))
            check("  ↳ 侧边导轨也跟上（%s）" % r["rack"], r["rack"] == r["title"],
                  "导轨=%s 内容=%s" % (r["rack"], r["title"]))

        # 真实按键路径（pvMech 的 turn() → place() → go()）
        pg.evaluate("() => go(0)")
        pg.wait_for_timeout(1000)
        for _ in range(5):
            pg.keyboard.press("ArrowDown")
            pg.wait_for_timeout(35)
        pg.wait_for_timeout(2600)
        r = pg.evaluate(PROBE)
        idx = NAMES.index(r["title"]) if r["title"] in NAMES else None
        check("连按 ↓ 5 次 → 内容与导航一致", r["nav"] == idx,
              "导航=%s 内容=%s" % (r["nav"], idx))

        # ★★ 中间态回归（用户 2026-10-10 报："内容页刷新速度匹配不上导航滚动，
        #    速度快时选中的会不匹配内容"）。上面所有用例都是**等最终态**——
        #    旧实现（选中态排在 _pre/busy 之后）也能全过，抓不到滚动过程中的
        #    "选中停在旧页"。这里改为：快速连发 go(1..6)，在请求还在飞的窗口里
        #    密集采样，断言**选中态（.ni.on / 幽灵页码）每一步都等于最后一次
        #    请求的目标**（跟手零延迟），内容允许滞后但最终必须追上。
        MID = """() => {
          const ons = Array.from(document.querySelectorAll('.ni'));
          const sel = ons.findIndex(n => n.classList.contains('on'));
          const gh = document.getElementById('ghost');
          return { sel: sel, pv: window.__pvPage,
                   ghost: gh ? (parseInt(gh.textContent, 10) - 1) : null };
        }"""
        pg.evaluate("() => go(0)")
        pg.wait_for_timeout(1300)
        pg.evaluate("() => { for (let k = 1; k <= 6; k++) setTimeout(() => go(k), k * 50); }")
        samples = []
        for _ in range(7):
            pg.wait_for_timeout(60)
            samples.append(pg.evaluate(MID))
        sels = [s["sel"] for s in samples]
        check("快速滚动中间态：选中跟手（采样末 sel=6）",
              sels[-1] == 6, "sel 轨迹=%s" % sels)
        check("  ↳ 选中单调前进（不回退、不卡旧页）",
              all(b >= a for a, b in zip(sels, sels[1:])) and sels[0] >= 1,
              "sel 轨迹=%s" % sels)
        check("  ↳ 幽灵页码与选中一致",
              all(s["ghost"] == s["sel"] for s in samples),
              "ghost=%s" % [s["ghost"] for s in samples])
        check("  ↳ __pvPage 与选中一致（导航↔选中零漂移）",
              all(s["pv"] == s["sel"] for s in samples),
              "pv=%s" % [s["pv"] for s in samples])
        pg.wait_for_timeout(2600)
        r = pg.evaluate(PROBE)
        idx = NAMES.index(r["title"]) if r["title"] in NAMES else None
        check("  ↳ 内容最终追上最后一页（第 7 页）", idx == 6,
              "内容=%s 期望=6" % idx)
        b.close()

print("\n" + "=" * 64)
print("通过 %d / 失败 %d / 跳过 %d" % (len(PASS), len(FAIL), len(SKIP)))
if FAIL:
    print("失败项：", FAIL)
