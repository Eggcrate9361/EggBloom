# -*- coding: utf-8 -*-
import io, sys, urllib.parse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1360, "height": 980}, device_scale_factor=2)
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:160]))
    pg.goto("http://127.0.0.1:8080/page?plugin_id=" + urllib.parse.quote("proactive_talk"),
            wait_until="networkidle", timeout=30000)
    pg.wait_for_timeout(2000)
    pg.click("#pick-friends")
    pg.wait_for_timeout(2500)
    pg.screenshot(path=r"D:\git\EggBloom\data\_proactive_picker.png", full_page=True)
    rows = pg.eval_on_selector_all("#friend-list .pt-fr", "els => els.length")
    print("好友行数:", rows, "| JS 错误:", errs[:3])
    b.close()
