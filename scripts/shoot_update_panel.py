# -*- coding: utf-8 -*-
"""scripts/shoot_update_panel.py —— 截「配置中心 → 功能 → 更新」这一块

用途：改样式后自己先看一眼（用户要求「做成跟 UI 风格一样，截图给我」）。
产物：data/_update_panel.png（整页）与 data/_update_panel_zoom.png（该小节放大）

    python scripts/shoot_update_panel.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data")
os.makedirs(OUT, exist_ok=True)
BASE = "http://127.0.0.1:8080"

from playwright.sync_api import sync_playwright  # noqa: E402


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 980},
                                device_scale_factor=2)
        page.goto(BASE, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2500)

        # 进配置中心
        clicked = page.evaluate("""() => {
            const it = [...document.querySelectorAll('.ni')].find(n => /配置/.test(n.textContent));
            if (it) { it.click(); return true; }
            return false;
        }""")
        if not clicked:
            print("找不到配置中心入口")
            return 1
        page.wait_for_timeout(1800)

        # 收起机械闸片架（非导航页会盖住内容）
        page.evaluate("() => { try { window.__pvMechToggle && window.__pvMechToggle(); } catch(e){} }")
        page.wait_for_timeout(400)

        # 跳到「更新」小节
        page.evaluate("() => { try { window.__cfGo && window.__cfGo('update'); } catch(e){} }")
        page.wait_for_timeout(1200)

        sec = page.query_selector('[data-sec="update"]')
        if not sec:
            print("页面上没有 update 小节")
            return 1

        # 触发一次「检查更新」，让版本号与结果区有内容（用本地 manifest）
        try:
            page.evaluate("""() => {
                const el = document.querySelector('input#sUpdateManifestUrl, [data-k="update_manifest_url"] input');
                if (el) { el.value = 'file:///D:/git/EggBloom/dist/latest.json'; el.dispatchEvent(new Event('input', {bubbles:true})); }
            }""")
            page.click("#cfUpdCheck")
            page.wait_for_timeout(2500)
        except Exception as e:
            print("检查更新点击失败（不影响截图）:", e)

        page.evaluate("(el) => el.scrollIntoView({block:'center'})", sec)
        page.wait_for_timeout(600)

        full = os.path.join(OUT, "_update_panel.png")
        page.screenshot(path=full)
        zoom = os.path.join(OUT, "_update_panel_zoom.png")
        sec.screenshot(path=zoom)
        print("已截图：", full)
        print("已截图：", zoom)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
