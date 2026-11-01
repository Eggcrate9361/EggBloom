# -*- coding: utf-8 -*-
"""截图验证：全局蒸馏进度条（用拦截把 distill_status 伪造成"运行中"）。"""
import io, sys, urllib.parse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

PID = "这样脆弱的东西"
with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1360, "height": 900}, device_scale_factor=2)

    def handler(route):
        body = route.request.post_data or "{}"
        if '"distill_status"' in body:
            route.fulfill(status=200, content_type="application/json", body='''{
              "running": true, "progress": 42, "stage": "L2 取证 7/15",
              "data_count": 660, "tokens_used": 123456, "confidence": 0.68,
              "log": ["L2 取证：5 批 × 3 块语料"], "stages": [], "done": false, "error": null
            }''')
        else:
            route.continue_()

    pg.route("**/api/plugins/action", handler)
    pg.goto("http://127.0.0.1:8080/page?plugin_id=" + urllib.parse.quote(PID),
            wait_until="networkidle", timeout=30000)
    pg.wait_for_timeout(2500)
    pg.screenshot(path=r"D:\git\EggBloom\data\_distill_globalbar.png")
    txt = pg.inner_text("#globalProg") if pg.query_selector("#globalProg") else "(无)"
    print("banner:", txt.replace("\n", " | ")[:120])
    b.close()
