# -*- coding: utf-8 -*-
"""tests/test_outbound_gate.py —— 一条出口（2026-11-04）

之前围栏在私聊、群聊各拼一遍参数，**插件发送完全不过** ——
主动说话那条路漏在外面，于是出了「刚说别找你」「我就想安静喝口酒」。

现在三处都走 `_guard_outbound`：
  私聊回复 / 群聊回复 / 插件发送（主动说话、撤回反馈、退场语…）

判据：
  ① 三条路都调用同一个入口
  ② 插件发送时，无据的话会被拦下且**真的不发**
  ③ 正常的话照常发
  ④ 群/私聊的证据取自各自的 history（不串号）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402

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


def main():
    old_mode = bc._config.get("grounding_guard")
    sent = []
    orig_send = bc.smart_send
    orig_send_g = bc.smart_send_group
    try:
        bc._config["grounding_guard"] = "drop"
        bc.smart_send = lambda text, target=None, *a, **k: (sent.append(("p", text)) or True)
        bc.smart_send_group = lambda gid, text, *a, **k: (sent.append(("g", text)) or True)

        qq = "999111"
        gid = "888222"
        bc.chat_history[qq] = [
            {"role": "user", "content": "我最喜欢喝酒了", "ts": 0},
            {"role": "assistant", "content": "下班了", "ts": 0},
        ]
        bc.group_chat_history[gid] = [
            {"role": "user", "content": "[老大(1)] 今天喝点", "ts": 0},
        ]

        print("[A] 插件发送也过闸（主动说话那条路）")
        sent.clear()
        ok = bc._plugin_send_text("我就想安静喝口酒", target=qq)
        check("★ 挪用的偏好被拦下", ok is False, ok)
        check("★ 真的一条都没发", not sent, sent)

        sent.clear()
        ok = bc._plugin_send_text("行，我也接着躺", target=qq)
        check("★ 正常的话照发", ok is True and sent, (ok, sent))
        check("★ 发的是原文", sent and sent[0][1].strip() == "行，我也接着躺", sent)

        print("\n[B] 群聊插件发送")
        sent.clear()
        ok = bc._plugin_send_text("我昨天说了要给你看个东西", group_id=gid)
        check("★ 无据的自我主张被拦", ok is False, ok)
        sent.clear()
        ok = bc._plugin_send_text("行吧我也接着躺", group_id=gid)
        check("★ 群聊正常话照发", ok is True and sent and sent[0][0] == "g", (ok, sent))

        print("\n[C] 统一入口本身")
        out = bc._guard_outbound("刚说别找你", scene="private", target=qq)
        check("★ 私聊证据里没有就删", out == "", out)
        out2 = bc._guard_outbound("刚说喝酒", scene="private", target=qq)
        check("★ 私聊证据里有就留（喝酒对得上）", "喝酒" in out2, out2)

        print("\n[D] 证据不串号")
        # 群里没说过"喝酒"之外的事；拿群里没出现过的指认，群证据里找不到
        out3 = bc._guard_outbound("你刚说买显卡", scene="group", target=gid)
        check("★ 群证据独立（找不到就删）", out3 == "", out3)

        print("\n[E] 三处共用同一个函数（源码断言）")
        src = open(bc.__file__, encoding="utf-8", errors="replace").read()
        check("★ 定义了 _guard_outbound", "def _guard_outbound(" in src)
        n = src.count("_guard_outbound(")
        check("★ 至少 3 处调用（私聊/群聊/插件）", n >= 3, n)
        check("私聊用它", 'scene="private"' in src and 'where=f"私聊[' in src)
        check("群聊用它", 'scene="group"' in src and 'where=f"群[' in src)
        check("插件发送前拦", "插件整条无据，不发" in src or "where=f\"插件[" in src)

    finally:
        bc.smart_send = orig_send
        bc.smart_send_group = orig_send_g
        bc.chat_history.pop("999111", None)
        bc.group_chat_history.pop("888222", None)
        if old_mode is None:
            bc._config.pop("grounding_guard", None)
        else:
            bc._config["grounding_guard"] = old_mode

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
