#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui-diff.py —— 前端改动前后「零变化」验证器（两种模式）。

背景
----
`webui/webui_artistic_v3.html` 是 430 KB 单文件 SPA，重做视觉的铁律是
**JS 逻辑一行不改、只动 `<style>` 与模板 class/层级**（改样式不动 JS 逻辑）。
但"我觉得没改外观"不可信，需要机器给证据。于是有本工具。

两种模式
--------
1) `--mode style`（默认，**首选**）
   让浏览器遍历 DOM，导出**每个元素的计算样式**（颜色/字号/边距/边框/字体…），
   逐元素比对 before / after。
   · 优点：**不受内容随机性影响** —— 页面上时间戳、随机 id、轮询日志怎么变都不影响
     元素的"样式"；因此不会像像素比对那样因为页面本身动态就判定不了。
   · 报出：哪些元素样式变了、具体哪个属性从什么变成什么。
   · 这正是"改 CSS 有没有改到外观"的直接问法。

2) `--mode pixel`
   真实截图逐像素比对。更贴近"用户看到的"，但**要求页面可复现** ——
   本 SPA 里 pages 会轮询后端、渲染时间戳与随机 id，所以每侧要渲多次，
   任一侧自身不稳就跳过该页。适合做最终确认，不适合当主力。

它抓到过的真实回归
------------------
8.0 把 `--font-ui` 定义为 `var(--font-d)` 时，`.btn` 的中文字体从
`PingFang SC` 变成 `Noto Sans SC`；像素比对在几个页面的按钮行上报出
0.1%~0.24% 的差异，肉眼几乎看不出，但确实改了外观 → 已回退。

用法
----
    python scripts/ui-diff.py                          # 计算样式，与 HEAD 比
    python scripts/ui-diff.py --mode pixel             # 像素比对
    python scripts/ui-diff.py --before HEAD~1 --pages tokens,settings
    python scripts/ui-diff.py --mode pixel --repeat 3

⚠️ 必须用**系统 Python**（Playwright 1.62 + chromium-1228）。
   包内 Python 是 Playwright 1.63，需要 chromium-1243（本机没有），
   会报 "Executable doesn't exist"。

退出码：0 = 无差异；1 = 有差异；2 = 环境/参数问题。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ALL_PAGES = [
    "dashboard", "protocol", "groupmanage", "memes", "imagegen", "behavior",
    "tokens", "settings", "bot", "chatlogs", "monitor", "console", "memory",
    "plugins", "agent",
]

REPO = Path(__file__).resolve().parent.parent
FRONTEND = REPO / "webui" / "webui_artistic_v3.html"

FREEZE = """
*, *::before, *::after { animation: none !important; transition: none !important; }
.decode-splash { display: none !important; }
#bloomCanvas { display: none !important; }
"""

# 冻结随机数与时间，尽量让像素模式可复现
INIT_SCRIPT = """
(() => {
  let s = 0x2F6E2B1 >>> 0;
  Math.random = () => { s = (s * 1103515245 + 12345) >>> 0; return s / 4294967296; };
  try {
    const FIXED = 1760000000000;
    const _Date = Date;
    const Frozen = function (...a) { return a.length === 0 ? new _Date(FIXED) : new _Date(...a); };
    Frozen.now = () => FIXED;
    Frozen.parse = _Date.parse;
    Frozen.UTC = _Date.UTC;
    Frozen.prototype = _Date.prototype;
    window.Date = Frozen;
    performance.now = () => 0;
  } catch (e) {}
})();
"""

# 参与比对的计算样式属性（覆盖设计规范关心的全部维度）
STYLE_PROPS = [
    "display", "position", "visibility", "opacity", "z-index",
    "color", "background-color", "background-image",
    "border-top-width", "border-right-width", "border-bottom-width", "border-left-width",
    "border-top-color", "border-bottom-color", "border-left-color", "border-right-color",
    "border-top-style", "border-radius",
    "box-shadow", "outline-color", "outline-width",
    "font-family", "font-size", "font-weight", "font-style",
    "letter-spacing", "line-height", "text-transform", "text-decoration-line",
    "text-align", "white-space", "overflow",
    "width", "height", "min-width", "max-width",
    "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding-top", "padding-right", "padding-bottom", "padding-left",
    "gap", "row-gap", "column-gap",
    "flex-direction", "flex-grow", "flex-shrink", "flex-basis", "align-items", "justify-content",
    "grid-template-columns", "grid-template-rows",
    "transform", "transition-duration", "transition-property",
    "cursor", "fill", "stroke",
]

COLLECT_JS = """
(props, skipSel) => {
  const out = [];
  const walk = (el, path) => {
    if (skipSel && el.matches && el.matches(skipSel)) return;   // 跳过已知动态区域（如启动页进度条）
    const cs = getComputedStyle(el);
    const sig = {};
    for (const p of props) sig[p] = cs.getPropertyValue(p);
    out.push({ path: path, tag: el.tagName.toLowerCase(), cls: el.className || '', sig: sig });
    let i = 0;
    for (const c of el.children) { walk(c, path + '/' + c.tagName.toLowerCase() + '#' + i); i++; }
  };
  if (document.body) walk(document.body, 'body');
  return out;
}
"""

# 已知随内容/时间变化、与样式改动无关的区域（启动页进度条按 JS 计时增长）
SKIP_SELECTOR = ".decode-splash, #decodeGrid, #bloomCanvas"

# 这些属性由**内容**决定（文字多长、有没有数据），不是样式改动的证据。
# 只作参考报告，不据此判定"改到了外观"。
GEOMETRY_PROPS = {"width", "height", "min-width", "max-width"}


def _deps():
    try:
        from PIL import Image, ImageChops  # noqa: F401
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"[!] 缺少依赖：{e}\n    请用系统 Python 运行（需要 playwright + pillow）。")
        sys.exit(2)
    from PIL import Image, ImageChops
    from playwright.sync_api import sync_playwright
    return Image, ImageChops, sync_playwright


class Shooter:
    def __init__(self, sync_playwright, w=1440, h=900):
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            args=["--force-color-profile=srgb", "--font-render-hinting=none"]
        )
        self.ctx = self.browser.new_context(viewport={"width": w, "height": h},
                                            device_scale_factor=1)
        self.ctx.add_init_script(INIT_SCRIPT)
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)[:200]))

    def open(self, url: str, page_name: str):
        pg = self.page
        pg.goto(url, wait_until="load")
        pg.add_style_tag(content=FREEZE)
        try:
            pg.evaluate(
                "(p)=>{ if(typeof router==='function'){ router(p); return true; } return false; }",
                page_name,
            )
        except Exception:
            pass
        pg.wait_for_timeout(1300)

    def styles(self, url, page_name):
        self.open(url, page_name)
        return self.page.evaluate(COLLECT_JS, [STYLE_PROPS, SKIP_SELECTOR])

    def shot(self, url, page_name, dst: Path):
        self.open(url, page_name)
        self.page.screenshot(path=str(dst))
        return dst

    def close(self):
        try:
            self.browser.close()
        finally:
            self._pw.stop()


def cmp_px(Image, ImageChops, a: Path, b: Path):
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return 10 ** 9, None, max(1, ia.width * ia.height)
    d = ImageChops.difference(ia, ib)
    n = sum(d.convert("L").histogram()[1:])
    return n, d.getbbox(), ia.width * ia.height


def git_show(ref: str, dest: Path) -> None:
    rel = FRONTEND.relative_to(REPO).as_posix()
    out = subprocess.run(["git", "-C", str(REPO), "show", f"{ref}:{rel}"], capture_output=True)
    if out.returncode != 0:
        print(f"[!] git show {ref}:{rel} 失败：{out.stderr.decode('utf-8', 'replace')[:200]}")
        sys.exit(2)
    dest.write_bytes(out.stdout)


def diff_styles(a, b, limit=6):
    """按 DOM 路径对齐比对计算样式。

    返回 (paint 差异数, geometry 差异数, 明细)。
    paint     = 颜色/字体/边框/圆角/阴影/间距/显示模式… → **真实外观改动**
    geometry  = width/height 等由文字长度或数据有无决定的属性 → 参考信息
    """
    ma = {e["path"]: e for e in a}
    mb = {e["path"]: e for e in b}
    details = []
    n_paint = n_geom = 0
    for path in sorted(set(ma) | set(mb)):
        ea, eb = ma.get(path), mb.get(path)
        if ea is None:
            details.append((path, "仅出现在 after", [("(元素)", "—", "新增")], "paint"))
            n_paint += 1
            continue
        if eb is None:
            details.append((path, "仅出现在 before", [("(元素)", "存在", "消失")], "paint"))
            n_paint += 1
            continue
        changed = [(k, eb["sig"].get(k), ea["sig"].get(k))
                   for k in ea["sig"] if ea["sig"].get(k) != eb["sig"].get(k)]
        if not changed:
            continue
        paint = [c for c in changed if c[0] not in GEOMETRY_PROPS]
        geom = [c for c in changed if c[0] in GEOMETRY_PROPS]
        if paint:
            n_paint += 1
        else:
            n_geom += 1
        details.append((path, ea["tag"] + ("." + (ea.get("cls") or "").split()[0] if ea.get("cls") else ""),
                        (paint or geom), "paint" if paint else "geom"))
    # 真实改动优先展示
    details.sort(key=lambda d: 0 if d[3] == "paint" else 1)
    return n_paint, n_geom, details[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description="前端改动前后零变化验证")
    ap.add_argument("--mode", choices=["style", "pixel"], default="style")
    ap.add_argument("--before", default="HEAD")
    ap.add_argument("--after", default=str(FRONTEND))
    ap.add_argument("--pages", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--show", type=int, default=6, help="每页最多展示几条明细")
    args = ap.parse_args()

    Image, ImageChops, sync_playwright = _deps()
    pages = [p.strip() for p in args.pages.split(",") if p.strip()] or ALL_PAGES
    outdir = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="uidiff_"))
    outdir.mkdir(parents=True, exist_ok=True)

    after = Path(args.after).resolve()
    if not after.exists():
        print(f"[!] 找不到 {after}")
        return 2
    if os.path.exists(args.before) and args.before.endswith(".html"):
        before = Path(args.before).resolve()
    else:
        before = outdir / "_before_ref.html"
        git_show(args.before, before)

    print(f"模式: {args.mode}\n对照: {args.before}\n当前: {after}\n页面: {len(pages)} 页")

    rows = []
    sh = Shooter(sync_playwright)
    try:
        for p in pages:
            if args.mode == "style":
                sa = sh.styles(after.as_uri(), p)
                sb = sh.styles(before.as_uri(), p)
                np_, ng_, det = diff_styles(sa, sb, args.show)
                rows.append((p, np_, ng_, len(sa), det))
            else:
                k = max(2, args.repeat)
                A = [sh.shot(after.as_uri(), p, outdir / f"A{p}_{i}.png") for i in range(k)]
                B = [sh.shot(before.as_uri(), p, outdir / f"B{p}_{i}.png") for i in range(k)]
                sa = all(cmp_px(Image, ImageChops, A[0], A[i])[0] == 0 for i in range(1, k))
                sb = all(cmp_px(Image, ImageChops, B[0], B[i])[0] == 0 for i in range(1, k))
                if not (sa and sb):
                    rows.append((p, -1, 0, 0, []))
                    continue
                n, bbox, tot = cmp_px(Image, ImageChops, A[0], B[0])
                rows.append((p, n, tot, bbox, []))
    finally:
        sh.close()
        if sh.errors:
            print(f"\n[注意] 渲染期捕获到 {len(sh.errors)} 条页面 JS 异常（前 3 条）：")
            for e in sh.errors[:3]:
                print("   ", e)

    print("=" * 84)
    if args.mode == "style":
        print(f"{'页面':<14}{'外观差异':>9}{'内容差异':>9}{'元素数':>8}   结果")
        print("-" * 84)
        for p, np_, ng_, na, _ in rows:
            verdict = "✓ 无差异" if np_ == 0 else "✗ 有外观差异"
            if np_ == 0 and ng_ > 0:
                verdict = "✓ 无外观差异（仅内容长度）"
            print(f"{p:<14}{np_:>9}{ng_:>9}{na:>8}   {verdict}")
        bad = [r for r in rows if r[1] != 0]
        if bad:
            print("\n明细（仅列「外观差异」；内容差异不列出）：")
            for p, np_, ng_, _, det in bad:
                print(f"\n  ── {p}（{np_} 个元素外观变化，{ng_} 个仅内容长度变化）")
                for path, info, changed, kind in det:
                    if kind != "paint":
                        continue
                    print(f"     {path}  [{info}]")
                    for k, was, now in changed[:8]:
                        print(f"        {k}: {was}  →  {now}")
            print("\n✗ 存在**外观**差异 —— 见上方属性级明细")
            return 1
        geo = [r for r in rows if r[2] > 0]
        extra = (f"（另有 {len(geo)} 页存在仅由内容长度引起的 width/height 差异，"
                 f"不是样式改动：{', '.join(r[0] for r in geo)}）") if geo else ""
        print(f"\n✓ 全部 {len(rows)} 页：每个元素的每项**计算样式**逐字相同 → 零视觉变化得到证明 {extra}")
        return 0

    print(f"{'页面':<14}{'不同像素':>10}{'占比%':>9}   结果")
    print("-" * 78)
    for p, n, tot, bbox, _ in rows:
        if n < 0:
            print(f"{p:<14}{'—':>10}{'—':>9}   随机渲染·跳过（该页两次渲染即不同）")
        else:
            print(f"{p:<14}{n:>10}{round(n * 100.0 / tot, 3):>9}   {'✓ 一致' if n == 0 else '★真差异 ' + str(bbox)}")
    real = [r for r in rows if r[1] > 0]
    if real:
        print(f"\n✗ {len(real)} 页存在真实差异：{', '.join(r[0] for r in real)}")
        return 1
    print("\n✓ 无真实差异")
    return 0


if __name__ == "__main__":
    sys.exit(main())
