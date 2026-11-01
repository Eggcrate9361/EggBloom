# -*- coding: utf-8 -*-
"""插件可用性调查：逐个打开插件页，收集 JS 报错与失败请求。"""
import io, json, os, sys, urllib.parse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

PLUGS = ["bili", "board_voice", "ego", "group_chat_decider", "web_operator", "web_search",
         "vitals", "这样脆弱的东西", "qzone", "proactive_talk", "capabilities",
         "schedule", "scheduler", "demo_typed_hook", "echo_plugin"]
ONE = sys.argv[1] if len(sys.argv) > 1 else ""

out = []
with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox"])
    for pid in ([ONE] if ONE else PLUGS):
        page = b.new_page(viewport={"width": 1280, "height": 860})
        errs, fails = [], []
        page.on("pageerror", lambda e: errs.append(str(e)[:160]))
        page.on("console", lambda m: errs.append("console: " + m.text[:140]) if m.type == "error" else None)
        page.on("requestfailed", lambda r: fails.append(r.url.split("?")[0][-60:] + " " + (r.failure or "")))
        url = "http://127.0.0.1:8080/page?plugin_id=" + urllib.parse.quote(pid)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)
            title = page.title()
            body_len = len(page.inner_text("body"))
        except Exception as e:
            title, body_len = "ERR", 0
            errs.append("goto: " + str(e)[:120])
        out.append({"plugin": pid, "title": title, "text_len": body_len,
                    "errors": errs[:4], "failed_requests": fails[:4]})
        page.close()
    b.close()

for r in out:
    flag = "OK  " if not r["errors"] and not r["failed_requests"] and r["text_len"] > 20 else "问题"
    print(f"[{flag}] {r['plugin']:<20} title={r['title'][:24]!r} 文本={r['text_len']}")
    for e in r["errors"]:
        print("       err:", e)
    for f in r["failed_requests"]:
        print("       net:", f)
