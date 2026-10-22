# -*- coding: utf-8 -*-
"""WebUI v4 精选截图 —— 给 README「演示」节用。

为什么用 Playwright 而不是 chrome --screenshot：
  WebUI 是 SPA，页面切换全靠 JS 点导航，`--screenshot` 只能拍到首屏。

⚠️ 隐私红线（2026-10-22 教训，差点把用户数据截进公开仓库）：
  **日志/聊天记录页绝对不能截图进 README。** 第一版截了 logs 页，里面带着
  群号、QQ 号、群友昵称和真实聊天内容，等于把用户隐私提交到公开仓库。
  选页标准只有两条：**不含任何真实会话数据** + **不含第三方版权素材**。
  按这个标准，表情包页（memes）也不行 —— 图是 KIFFEL/VRCHAT 等同人素材，
  授权状态不明，不能进公开仓库。最终选用 behavior（纯配置参数，零隐私）。

踩过的三个坑：
  1. `.ni:has-text("设置")` 点不动 —— 页面名和 PAGES 数组里的 name 不一致：
     设置→配置中心 / 聊天记录→日志 / 监控→通知 / 行为→群聊行为。
     按 name 匹配必挂 4 个。
  2. `.nav` 被主动藏了（`left:-9999px; opacity:0`），v4 没有传统侧栏；
     导航是左下角 `.rack` + 中央「机械闸片架」，只能走 JS click。
  3. 切页不会自动收机械导航，非导航页截图前要先 `__pvMechToggle()` 收起来，
     否则 `.mech` 浮层会盖住页面内容。

截图清单（用户要求"截几张加个导航页"）：
  nav       ：仪表盘首屏 + 展开的机械闸片架（就是"导航页"）
  protocol  ：协议驱动配置（只有本地端口和标准 QQ 路径，无敏感信息）
  config    ：配置中心（供应商卡片，KEY 不落界面）
  behavior  ：群聊行为参数（纯配置，零隐私）
  agent     ：Agent 工具台

    python shoot_webui.py
→ docs/screenshots/<name>.png
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "screenshots")
os.makedirs(OUT, exist_ok=True)
BASE = "http://127.0.0.1:8080"

# (文件名, .ni 下标, 说明) —— 下标来自探针实测的 .ni 文本顺序
SHOTS = [
    ("nav",      0,  "导航页：仪表盘首屏 + 展开的机械闸片架"),
    ("protocol", 1,  "协议"),
    ("config",   7,  "配置中心"),
    ("behavior", 5,  "群聊行为（纯配置参数，零隐私）"),
    ("agent",    14, "Agent"),
]

from playwright.sync_api import sync_playwright  # noqa: E402


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 900},
                                device_scale_factor=2)
        page.goto(BASE, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        for fname, idx, desc in SHOTS:
            try:
                # ① JS 点击导航（绕过收起状态下的可见性检查）
                page.evaluate(
                    "document.querySelectorAll('.ni')[%d].click()" % idx)
                page.wait_for_timeout(2200)      # 等该页数据/图表渲染
                page.evaluate("window.scrollTo(0,0)")
                page.wait_for_timeout(400)

                # ② 只有导航页展开机械闸片架；其余页必须先收起，
                #    否则 .mech 浮层会盖住页面内容（切页不会自动收）
                if fname == "nav":
                    page.evaluate("window.__pvMechToggle && window.__pvMechToggle()")
                    page.wait_for_timeout(900)       # 等展开动画（steps 动画约 560ms）
                else:
                    page.evaluate(
                        "if (window.__pvMechOpen) window.__pvMechToggle()")
                    page.wait_for_timeout(500)

                out = os.path.join(OUT, f"{fname}.png")
                page.screenshot(path=out)
                print(f"[OK] {fname:<10} {desc}  {os.path.getsize(out)/1024:.0f} KB")
            except Exception as e:
                print(f"[--] {fname:<10} 失败: {str(e)[:90]}")

        browser.close()


if __name__ == "__main__":
    main()
