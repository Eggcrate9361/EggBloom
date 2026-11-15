# -*- coding: utf-8 -*-
"""v4 字体落地审计：像素字体到底有没有真的生效。

为什么需要它
------------
"CSS 里写了 font-family:像素体" ≠ "屏幕上真的是像素体"。
中间有三道容易悄悄失败的关口：
  1. @font-face 文件没加载成功（路径/格式问题）→ 静默回退
  2. 后面的规则把 font-family 覆盖回去（同 specificity 后者胜）
  3. 字体加载了，但该字符**不在**字体的字符集里（中文最容易）
     → 逐字符回退，中文悄悄用回系统字体，拉丁还是像素体

所以本审计同时看三样东西：字体加载状态、计算后的 font-family、
以及**实际渲染宽度**（宽度和系统字体重合 = 没生效）。

用法： python scripts/audit_fonts_v4.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
V4 = os.path.join(ROOT, "webui", "webui_artistic_v4.html")
URL = "file:///" + V4.replace("\\", "/")

from playwright.sync_api import sync_playwright

# 本机只装了 chromium-1228（随 IDE 附带），playwright pip 包要的版本号未必一致。
# 允许用环境变量 PW_CHROME 指定，否则按顺位找本地已有的 chrome。
_CANDIDATES = [
    os.environ.get("PW_CHROME") or "",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright", "chromium-1228",
                 "chrome-win64", "chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright",
                 "chromium_headless_shell-1228", "chrome-headless-shell-win64",
                 "chrome-headless-shell.exe"),
]


def _launch(pw):
    for c in _CANDIDATES:
        if c and os.path.exists(c):
            return pw.chromium.launch(executable_path=c)
    return pw.chromium.launch()


JS = r"""
async () => {
  await document.fonts.ready;
  const faces = [];
  document.fonts.forEach(f => faces.push({family: f.family, weight: f.weight,
                                          style: f.style, status: f.status}));

  const probe = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return {sel, missing: true};
    const cs = getComputedStyle(el);
    return {sel, text: (el.textContent || '').trim().slice(0, 24),
            family: cs.fontFamily, size: cs.fontSize, weight: cs.fontWeight};
  };
  const sels = ['.brand h1', '.brand .en', '.brand .tag', '.top h2', '.ttlEn',
                '.top .sec', '.hd .lab', '.st .v', '.st .lb', '.kv dt', '.kv dd',
                'body', '.log', '.btn'];
  const computed = sels.map(probe);

  // 像素级判定：同一段文字在不同字体下的渲染宽度。
  // 与系统字体宽度重合 → 说明该字体根本没被用上。
  const cv = document.createElement('canvas');
  cv.width = 900; cv.height = 60;
  const cx = cv.getContext('2d');
  const measure = (font, text) => { cx.font = font; return Math.round(cx.measureText(text).width * 100) / 100; };
  const F = 24;
  const tests = [
    {label: '拉丁 EggBloom', text: 'EggBloom'},
    {label: '中文 仪表盘',   text: '仪表盘'},
    {label: '中文 会话记录', text: '会话记录'},
    {label: '数字 1234567890', text: '1234567890'},
  ];
  const stacks = {
    Silkscreen: "'Silkscreen'",
    PressStart2P: "'Press Start 2P'",
    FusionPixelSC: "'FusionPixelSC'",
    sysSans: "'Microsoft YaHei',sans-serif",
    sysSerif: "Georgia,serif",
    sysMono: "Consolas,monospace",
  };
  const widths = {};
  for (const [k, st] of Object.entries(stacks)) {
    widths[k] = tests.map(t => measure(`${F}px ${st}`, t.text));
  }
  return {faces, computed, tests: tests.map(t => t.label), widths,
          fontCheck: {
            Silkscreen: document.fonts.check('16px Silkscreen'),
            PressStart2P: document.fonts.check("16px 'Press Start 2P'"),
            FusionPixelSC: document.fonts.check('16px FusionPixelSC'),
          }};
}
"""


def main():
    with sync_playwright() as pw:
        b = _launch(pw)
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.route("**/api/**", lambda r: r.abort())
        pg.goto(URL, wait_until="load")
        pg.wait_for_timeout(1200)
        data = pg.evaluate(JS)
        pg.screenshot(path=os.path.join(ROOT, "_font_audit.png"), full_page=False)
        b.close()

    print("=== 1. @font-face 加载状态 ===")
    for f in data["faces"]:
        print(f"  [{f['status']:8}] {f['family']:18} weight={f['weight']} style={f['style']}")
    print("  document.fonts.check:", json.dumps(data["fontCheck"], ensure_ascii=False))

    print("\n=== 2. 关键元素的计算 font-family ===")
    for c in data["computed"]:
        if c.get("missing"):
            print(f"  --       {c['sel']:16} (DOM 里没有)")
        else:
            print(f"  {c['sel']:16} {c['size']:>6} w{c['weight']:<4} {c['family'][:72]}")
            print(f"  {'':16} «{c['text']}»")

    print("\n=== 3. 渲染宽度对照（同字号 px，同文本）===")
    labels = data["tests"]
    keys = list(data["widths"].keys())
    print("  " + "文本".ljust(18) + "".join(k.ljust(15) for k in keys))
    for i, lb in enumerate(labels):
        row = lb.ljust(18)
        for k in keys:
            row += str(data["widths"][k][i]).ljust(15)
        print("  " + row)
    print("\n  判读：若 FusionPixelSC 列的**中文**宽度 == sysSans 列 → 中文字形没进字体（或没生效）")
    print("        若 Silkscreen 列 == sysSans 列 → 拉丁像素体也没生效")
    print(f"\n[截图] {os.path.join(ROOT, '_font_audit.png')}")


if __name__ == "__main__":
    sys.exit(main())
