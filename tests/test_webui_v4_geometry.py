# -*- coding: utf-8 -*-
"""v4 前端几何契约测试。

为什么要有这个文件
------------------
v4 的动效是"框架级"的：游标要在条目之间滑行、条目要按高度抬起。
这类效果一旦几何错了，**看起来就像渲染坏了**，而且很难从代码上看出来。

本测试锁定三类真实踩过的坑：

  A. **类名撞车**（静态检查，不需要浏览器）
     实测：`pending` 既被用作"未迁移页面"容器（`.pending{padding:48px 0}`），
     又被用作导航项修饰类（`.ni.pending`）→ 导航项吃到 48px 上下内边距，
     56px 的条目被撑成 **96px**，游标随之全部错位。
     命名冲突是"看着像坏了"的头号原因。

  B. **游标与条目错位**（浏览器几何）
     实测：游标挂在 `.rail` 下、条目在 `.nav` 里 → 差了整个品牌区高度（107px），
     切到第 6 项时错位达 **271px**。

  C. **透视把游标推歪**（浏览器几何）
     `translateZ` 在 perspective 下会按 Z 缩放视觉位置，偏移量**随位置变化**，
     无法用常数补偿 → 游标一律不用 translateZ，"浮起"交给影子表达。

依赖 Playwright（系统 Python 才有）。缺失时**跳过 B/C 但仍跑 A**，不会让套件变红。
"""
import io
import os

# 自动找到本机 ms-playwright 下的 chromium（pip 的 playwright 不带浏览器，
# 浏览器是单独下载、版本号跟库绑定）。详见 tests/_pw_chrome.py。
sys_ = __import__("sys")
sys_.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw_chrome import resolve_chrome as _resolve_chrome  # noqa: E402
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
V4 = os.path.join(ROOT, "webui", "webui_artistic_v4.html")

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + ("" if cond else "  " + str(detail)))


def skip(name):
    SKIP.append(name)
    print(f"  [SKIP] {name}")


if not os.path.exists(V4):
    print(f"[!] 找不到 {V4}")
    sys.exit(2)

src = io.open(V4, encoding="utf-8").read()
css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", src, re.S))

print("=== A. 类名撞车（静态，不需要浏览器）===")
# 单类规则里会**影响布局盒子**的那些类
LAYOUT_PROPS = ("padding", "margin", "height", "min-height", "max-height",
                "width", "max-width", "min-width", "display", "position", "flex", "grid")
standalone = {}
for m in re.finditer(r"(?m)^\s*\.([a-zA-Z][\w-]*)\s*\{([^}]*)\}", css):
    cls, body = m.group(1), m.group(2)
    if any(p + ":" in body for p in LAYOUT_PROPS):
        standalone.setdefault(cls, body.strip()[:70])

# 被当作**修饰类**用到的类：`.A.B` 里后一个，以及 `className='base' + ' mod'` 里**追加**的字面量。
# 只从 CSS 复合选择器取 —— CSS 才算数（JS 里 `className='panel in'` 的第一个是基类，
# 早期版本把它也算成修饰类，产生了 cursor / panel 两个假阳性）。
compound = set()
for m in re.finditer(r"\.([a-zA-Z][\w-]*)\.([a-zA-Z][\w-]*)", css):
    compound.add(m.group(2))
# JS 里以字符串拼接追加的类（' todo' 这种，前面有空格的）
for m in re.finditer(r"className\s*=[^;]*?(\+\s*'(\s[\w-]+)')", src):
    for tok in m.group(2).split():
        compound.add(tok)

collide = sorted(set(standalone) & compound)
check("★ 没有「既是独立布局类、又被当修饰类」的名字", not collide,
      [(c, standalone[c]) for c in collide])
if collide:
    print("        撞车的类：" + ", ".join(collide))
    print("        修法：把其中一侧改名（本次把导航修饰类 pending → todo）")

# 反向确认：确实检查到了已知的复合用法
check("检查逻辑有效（能识别出 .ni.on 这类复合选择器）", "on" in compound or "todo" in compound,
      sorted(compound)[:8])

print("\n=== B/C. 游标与条目的几何（需要 Playwright）===")
try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    skip(f"未安装 Playwright（{type(e).__name__}）→ 跳过几何检查")
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
        skip("Playwright 起不了浏览器（%s）→ 跳过几何检查" % type(e).__name__)
        sync_playwright = None

if sync_playwright:
    ROW_EXPECT = 62   # 行高加大后激活项字号能到 20px（导航是目录，不是列表）
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=_resolve_chrome())
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.route("**/api/**", lambda r: r.abort())      # 无后端也确定
        pg.goto("file:///" + V4.replace("\\", "/"), wait_until="load")
        pg.wait_for_timeout(900)

        geo = pg.evaluate("""() => {
          const items = Array.from(document.querySelectorAll('.ni'));
          const cursors = document.querySelectorAll('.cursor');
          const hs = items.map(e => Math.round(e.getBoundingClientRect().height));
          const tops = items.map(e => Math.round(e.getBoundingClientRect().top));
          const spac = tops.slice(1).map((t,i) => t - tops[i]);
          return {
            n: items.length,
            heights: hs, spacings: spac,
            cursorCount: cursors.length,
            cursorParent: cursors[0] ? cursors[0].parentElement.className : null,
            cursorTransform: cursors[0] ? getComputedStyle(cursors[0]).transform : null
          };
        }""")

        check(f"导航 {geo['n']} 项", geo["n"] == 15, geo["n"])
        check("★ 所有条目高度一致（撞车 bug 会撑成 96px）",
              len(set(geo["heights"])) == 1, sorted(set(geo["heights"])))
        check(f"★ 条目高度 == {ROW_EXPECT}px", set(geo["heights"]) == {ROW_EXPECT},
              sorted(set(geo["heights"])))
        check(f"★ 条目间距 == {ROW_EXPECT}px", set(geo["spacings"]) == {ROW_EXPECT},
              sorted(set(geo["spacings"])))
        check("只有一个游标", geo["cursorCount"] == 1, geo["cursorCount"])
        check("★ 游标挂在 .nav 内（与条目同坐标原点）", geo["cursorParent"] == "nav",
              geo["cursorParent"])

        # 逐个切页，验游标与当前条目严格对齐
        # 阈值 3px：解构主义主题给 .ni 加了 skewX + 透视 translateZ，
        # getBoundingClientRect 返回的是**投影后的包围盒**，会比布局盒膨胀约 2px。
        # 这是投影伪差（肉眼不可见），不是对齐 bug —— 曾经的真实 bug 是 271px。
        TOL = 3
        worst = 0
        for i in (0, 5, 13, 1, 14, 7):
            pg.evaluate("(i) => go(i)", i)
            pg.wait_for_timeout(620)          # 等 470ms 滑动结束
            d = pg.evaluate("""() => {
              const c = document.querySelector('.cursor').getBoundingClientRect();
              const on = document.querySelector('.ni.on').getBoundingClientRect();
              return {dt: Math.round(c.top - on.top), dh: Math.round(c.height - on.height)};
            }""")
            worst = max(worst, abs(d["dt"]))
            check(f"第 {i+1} 项：游标与条目对齐（Δtop={d['dt']}px, Δh={d['dh']}px）",
                  abs(d["dt"]) <= TOL and abs(d["dh"]) <= TOL, d)
        check(f"★ 最大错位 {worst}px（曾达 271px；阈值 {TOL}px = 投影膨胀容差）",
              worst <= TOL, worst)
        b.close()

print("\n" + "=" * 64)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)} / 跳过 {len(SKIP)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
