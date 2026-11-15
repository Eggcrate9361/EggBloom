# -*- coding: utf-8 -*-
"""用例：协议页必须看得见高权限助手在不在（P0-3，2026-11-05）。

背景：助手是 SYSTEM 计划任务起的独立进程，掉了对协议页完全不可见，
以前只能翻 data/privhelper.log。验收：停掉 30 秒内变离线，起来自动恢复。

判据分两层：
  · 静态：后端有独立只读端点、前端有徽标和 <=30s 的轮询且切页会清掉
  · 行为：/health 在线 / 非高权限 / 连不上 三种情况都不抛异常，且给出下一步
"""
import io
import json
import os
import re
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import webui.webui_server as ws  # noqa: E402

SERVER = os.path.join(ROOT, "webui", "webui_server.py")
HTML = os.path.join(ROOT, "webui", "webui_artistic_v4.html")

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


class _Resp:
    """privclient.health() 用 with urlopen(...) as r，得支持上下文管理。"""

    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def stub(payload=None, exc=None):
    if exc is not None:
        return mock.patch("urllib.request.urlopen", side_effect=exc)
    return mock.patch("urllib.request.urlopen", return_value=_Resp(payload))


def main():
    src = io.open(SERVER, encoding="utf-8", errors="replace").read()
    html = io.open(HTML, encoding="utf-8", errors="replace").read()

    print("[A] 后端：独立只读端点")
    check("★ 定义了 _privhelper_status", "def _privhelper_status(" in src)
    check("★ GET /api/protocol/privhelper 路由存在",
          'if path == "/api/protocol/privhelper":' in src
          and "_privhelper_status()" in src)
    check("★ health 超时 <= 1 秒", "timeout=0.5" in src)
    hud = src[src.find("def _protocol_hud("):src.find("def _privhelper_status(")]
    check("★ /api/status 的轻量口径没被塞 HTTP 探测",
          "urlopen" not in hud and "privhelper" not in hud)

    print("\n[B] 前端：徽标 + 轮询")
    check("★ 协议页有助手徽标", 'id="pHelperState"' in html)
    check("★ 显示在线/离线两种文案",
          "助手：在线" in html and "助手：离线" in html
          and "助手：在线 · 高权限" in html)
    check("★ 调的是新端点", "API('/protocol/privhelper'" in html)
    i = html.find("window.__pHelperTimer = setInterval")
    blk = html[i:i + 400] if i >= 0 else ""
    m = re.search(r"\}\s*,\s*(\d+)\s*\)", blk)
    check("★ 轮询间隔 <= 30 秒（验收判据）",
          bool(m) and 0 < int(m.group(1)) <= 30000,
          m.group(1) if m else blk[:80])
    check("★ 切页会清掉定时器",
          "clearInterval(window.__pHelperTimer)" in html
          and "document.body.contains(el)" in blk)
    check("★ 请求失败也显示离线（不抛给页面）",
          "catch (e) { paintHelper(null); }" in html)

    print("\n[C] 行为：三种 /health 结果")
    with stub({"ok": True, "elevated": True, "pid": 4656, "port": 3007}):
        r = ws._privhelper_status()
    check("★ 在线且高权限", r.get("online") is True
          and r.get("elevated") is True and r.get("pid") == 4656, r)
    check("★ 在线时 hint 为空", not r.get("hint"), r.get("hint"))

    with stub({"ok": True, "elevated": False, "pid": 1, "port": 3007}):
        r2 = ws._privhelper_status()
    check("★ 在线但权限不足会说清楚", r2.get("online") is True
          and r2.get("elevated") is False and "注入不了 QQ" in (r2.get("hint") or ""), r2)

    with stub(exc=OSError("connection refused")):
        r3 = ws._privhelper_status()
    check("★ 连不上返回 offline 而不是抛异常", r3.get("online") is False, r3)
    check("★ 离线给了可执行的下一步",
          "Start-ScheduledTask" in (r3.get("hint") or ""), r3.get("hint"))

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
