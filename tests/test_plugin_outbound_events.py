# -*- coding: utf-8 -*-
"""1.5.10 F1/F2 回归：插件出口收口 + 两个核心事件。

F1：直连 send_private_msg / send_group_msg 的插件必须被拒绝加载；插件唯一出口是 ctx.send_text。
F2：核心发 message.in / bot.replied 两个事件，载荷字段固定。
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402
import plugins.plugin_manager as pm_mod  # noqa: E402
from plugins.abi import PluginContext  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


print("\n[1] F1 静态扫描：直连发送接口被拒")
tmp = tempfile.mkdtemp(prefix="f1scan_")
try:
    with io.open(os.path.join(tmp, "evil.py"), "w", encoding="utf-8") as f:
        f.write("def go():\n    ctx.http_post('/send_private_msg', {})\n")
    hits = pm_mod.scan_forbidden_send(tmp)
    check("★ 直连 send_private_msg 被抓到", len(hits) == 1, hits)

    with io.open(os.path.join(tmp, "evil.py"), "w", encoding="utf-8") as f:
        f.write("def go():\n    ctx.send_text('hi', target='1')\n")
    check("走 ctx.send_text 的插件放行", pm_mod.scan_forbidden_send(tmp) == [])
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

_p = io.open(os.path.join(ROOT, "plugins", "proactive_talk", "__init__.py"), encoding="utf-8").read()
check("★ proactive_talk 已无直连发送", not pm_mod._FORBIDDEN_SEND_RE.search(_p))

print("\n[2] F1 出口：ctx.send_text / bot_core._plugin_send_text")
calls = []


class _Mgr:
    _host_api = {"send_text": lambda text, target=None, group_id=None: calls.append((text, target, group_id)) or True}


ctx = PluginContext(name="t", manager=_Mgr())
check("ctx.send_text 转发到宿主能力", ctx.send_text("你好", target="10001") is True and calls == [("你好", "10001", None)], calls)

sent = []
_orig_g, _orig_p = bot_core.smart_send_group, bot_core.smart_send
bot_core.smart_send_group = lambda gid, text: sent.append(("group", gid, text)) or True
bot_core.smart_send = lambda text, target=None: sent.append(("private", target, text)) or True
try:
    ok1 = bot_core._plugin_send_text("群消息", group_id="20002")
    ok2 = bot_core._plugin_send_text("私聊消息", target="10003")
    check("★ 群/私聊分别走 smart_send_group / smart_send",
          ok1 and ok2 and sent == [("group", "20002", "群消息"), ("private", "10003", "私聊消息")], sent)
finally:
    bot_core.smart_send_group, bot_core.smart_send = _orig_g, _orig_p

print("\n[3] F2 事件：message.in / bot.replied 载荷")
events = []


class _PM:
    def emit_event(self, name, payload=None):
        events.append((name, dict(payload or {})))
        return []


_orig_pm = bot_core.get_plugin_manager
bot_core.get_plugin_manager = lambda: _PM()
try:
    bot_core._emit_plugin_event("message.in", {"group_id": "1", "sender": "2",
                                               "text": "hi", "ts": 1.0, "at_bot": False})
    check("message.in 载荷字段齐全", events and set(events[0][1]) == {"group_id", "sender", "text", "ts", "at_bot"}, events)

    # api_call 成功发送 → bot.replied
    class _Resp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({"retcode": 0}).encode("utf-8")

    orig = (bot_core.REQUESTS_OK, bot_core.urllib.request.urlopen)
    bot_core.REQUESTS_OK = False
    bot_core.urllib.request.urlopen = lambda *a, **k: _Resp()
    try:
        bot_core.api_call("send_private_msg", {"user_id": 10003, "message": "hello"})
        bot_core.api_call("get_status", {})
    finally:
        bot_core.REQUESTS_OK, bot_core.urllib.request.urlopen = orig
    replied = [e for e in events if e[0] == "bot.replied"]
    check("★ 只有 send_*_msg 成功才发 bot.replied", len(replied) == 1, events)
    # H5 起 bot.replied 额外带 trace_id（只加字段，不改既有字段名）
    check("bot.replied 载荷字段齐全",
          replied and {"group_id", "text", "ts", "reply_to"} <= set(replied[0][1]), replied)
    check("非发送接口不发事件", len(events) == 2, events)
finally:
    bot_core.get_plugin_manager = _orig_pm

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
