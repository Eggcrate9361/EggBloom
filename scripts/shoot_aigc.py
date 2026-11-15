# -*- coding: utf-8 -*-
"""截 AIGC 页（生图 / 语音占位 / 素材）—— 改页面后自己先看。"""
import io, os, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data")
os.makedirs(OUT, exist_ok=True)
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1440, "height": 980}, device_scale_factor=2)
    pg.goto("http://127.0.0.1:8080", wait_until="networkidle", timeout=30000)
    pg.wait_for_timeout(2200)
    ok = pg.evaluate("""() => {
      const it = [...document.querySelectorAll('.ni')].find(n => /AIGC|生图/.test(n.textContent));
      if (it) { it.click(); return it.textContent.trim(); }
      return null;
    }""")
    print("点击导航:", ok)
    pg.wait_for_timeout(2500)
    pg.screenshot(path=os.path.join(OUT, "_aigc_img.png"))
    # 切到语音
    r = pg.evaluate("""() => {
      const t = [...document.querySelectorAll('.aigc-tab')].find(x => x.dataset.t === 'tts');
      if (t) { t.click(); return true; } return false;
    }""")
    print("切语音:", r)
    pg.wait_for_timeout(800)
    pg.screenshot(path=os.path.join(OUT, "_aigc_tts.png"))
    b.close()
print("done")
