# -*- coding: utf-8 -*-
"""观测链：主动消息 / 戳一戳要落 chat_logs（P2-1，2026-10-30）。

## 背景
10-29 16:04 用户第一句就是"我怎么就是稀客了"—— 但那句"稀客"在任何日志里
都**查不到**：主动消息、戳一戳只 `print` 到 stdout（给 GUI 看的 `chat_log`），
chat_logs 文件里没有。复盘只能靠用户证词反推。

## 钉死
  1. `save_event_log` 写进 `chat_logs/QQ_<号>/chat_<日期>.md`，用 `[事件]` 标记
  2. 群事件写 `chat_logs/GROUP_<号>/`
  3. 插件出口（`_plugin_send_text`）发出去的消息也落盘
  4. 戳一戳处理里真的调了它（源码级断言 —— 那条路径要连模型，不能真跑）

运行：python tests/test_event_log.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


tmp = tempfile.mkdtemp(prefix="event_log_test_")
_orig_dir = bot_core.CHAT_LOG_DIR
_orig_log = bot_core.log
try:
    bot_core.CHAT_LOG_DIR = tmp
    bot_core.log = lambda _m: None

    # ── 1. 私聊事件 ─────────────────────────────────────────────
    bot_core.save_event_log("123456", "戳一戳", "某人（123456）戳了你")
    import datetime
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    p = os.path.join(tmp, "QQ_123456", f"chat_{today}.md")
    check("私聊事件文件生成了", os.path.isfile(p), p)
    body = open(p, encoding="utf-8").read() if os.path.isfile(p) else ""
    check("带 [戳一戳] 标记", "[戳一戳]" in body, body[:120])
    check("带正文", "戳了你" in body, body[:120])

    # ── 2. 群事件 ───────────────────────────────────────────────
    bot_core.save_event_log("777", "戳一戳", "群友戳了你", is_group=True)
    gp = os.path.join(tmp, "GROUP_777", f"chat_{today}.md")
    check("群事件写到 GROUP_ 目录", os.path.isfile(gp), gp)

    # ── 3. 插件出口也落盘 ───────────────────────────────────────
    sent = {}

    def fake_smart_send(text, target=None):
        sent["text"] = text
        sent["target"] = target
        return True

    _orig_send = bot_core.smart_send
    bot_core.smart_send = fake_smart_send
    try:
        ok = bot_core._plugin_send_text("早安，今天也要好好吃饭", target="654321")
    finally:
        bot_core.smart_send = _orig_send
    check("插件出口发送成功", ok is True, ok)
    pp = os.path.join(tmp, "QQ_654321", f"chat_{today}.md")
    check("插件消息落盘了", os.path.isfile(pp), pp)
    pbody = open(pp, encoding="utf-8").read() if os.path.isfile(pp) else ""
    check("标成 [插件发送]", "[插件发送]" in pbody, pbody[:120])

    # ── 4. 戳一戳路径确实调了它（源码断言）──────────────────────
    import io
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    i = src.find("def _handle_poke_event(")
    seg = src[i:i + 2000]
    check("戳一戳处理里调了 save_event_log", "save_event_log(" in seg, seg[:200])
    check("戳一戳落的是事件标记", '"戳一戳"' in seg, "")
finally:
    bot_core.CHAT_LOG_DIR = _orig_dir
    bot_core.log = _orig_log
    shutil.rmtree(tmp, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
