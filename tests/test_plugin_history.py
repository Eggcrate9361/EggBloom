# -*- coding: utf-8 -*-
"""tests/test_plugin_history.py —— 插件发出的话必须进对话历史（2026-11-03）

## 线上问题

用户问：「主动说话不进记忆吗」。查下来：`_plugin_send_text` 只写 `chat_logs`
（给人看的落盘），**没写 `chat_history`**（给模型看的上下文）。

后果不是"日志缺一行"，是**模型失忆**：
  · 下次对话它不知道自己主动说过什么 → 重复开口；
  · 被质问「你刚才不是说了…」时，它会按【没说过就顶回去】的规则否认 ——
    人格规则本身没错，错在它真的看不到自己说过的话。

落盘与上下文是两件事，这个用例把两者一起钉住：**发送成功就要两边都有**。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

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
    # 记录 save_event_log 的调用（落盘那一半）
    saved = []
    orig_log = bot_core.save_event_log
    bot_core.save_event_log = lambda peer, kind, text, is_group=False: saved.append((peer, kind, text))
    # 发送桩：可控成功/失败
    state = {"ok": True}
    orig_send = bot_core.smart_send
    orig_send_g = bot_core.smart_send_group
    bot_core.smart_send = lambda text, target=None, *a, **kw: state["ok"]
    bot_core.smart_send_group = lambda gid, text, *a, **kw: state["ok"]

    try:
        print("[A] 私聊：发送成功 → chat_history 里有一条 assistant")
        bot_core.chat_history.pop("999001", None)
        ok = bot_core._plugin_send_text("老大在吗", target="999001")
        check("发送返回 True", ok is True)
        h = bot_core.chat_history.get("999001") or []
        check("★ 历史里有一条", len(h) == 1, h)
        check("★ 角色是 assistant", h and h[0].get("role") == "assistant", h[:1])
        check("★ 内容就是发出去的那句", h and h[0].get("content") == "老大在吗", h[:1])
        check("带时间戳（和主链同形状）", bool(h and h[0].get("ts")), h[:1])
        check("落盘也照旧有", any(t == "老大在吗" for _, _, t in saved), saved)

        print("\n[B] 群里同理，且遵守群历史上限")
        gid = "888001"
        bot_core.group_chat_history.pop(gid, None)
        bot_core._plugin_send_text("群里的主动话", group_id=gid)
        gh = bot_core.group_chat_history.get(gid) or []
        check("★ 群历史里有一条", len(gh) == 1 and gh[0].get("role") == "assistant", gh[:1])
        cap = bot_core._history_cap()
        for i in range(cap + 5):
            bot_core._plugin_send_text(f"第{i}句", group_id=gid)
        gh2 = bot_core.group_chat_history.get(gid) or []
        check(f"★ 不超过群上限 {cap}", len(gh2) <= cap, len(gh2))
        check("留下的是最新的", gh2 and gh2[-1].get("content") == f"第{cap + 4}句", gh2[-1:])

        # 2026-11-06：存储上限从写死的 24 提到 _PRIVATE_HISTORY_CAP（60）。
        # 判据跟着常量走，别再写死数字 —— 上次改上限就在这里假红过一次。
        pcap = bot_core._PRIVATE_HISTORY_CAP
        print(f"\n[C] 私聊上限 {pcap}")
        n_send = pcap + 6
        for i in range(n_send):
            bot_core._plugin_send_text(f"私聊第{i}句", target="999002")
        ch = bot_core.chat_history.get("999002") or []
        check(f"★ 不超过 {pcap} 条", len(ch) == pcap, len(ch))
        check("留下的是最新的", ch and ch[-1].get("content") == f"私聊第{n_send - 1}句", ch[-1:])

        print("\n[D] 发送失败：不进历史（没发出去的话不能当成说过）")
        state["ok"] = False
        bot_core.chat_history.pop("999003", None)
        r = bot_core._plugin_send_text("这句发不出去", target="999003")
        check("返回 False", r is False)
        check("★ 历史里没有", not (bot_core.chat_history.get("999003") or []),
              bot_core.chat_history.get("999003"))

        print("\n[E] 空文本：不发、不记")
        bot_core.chat_history.pop("999004", None)
        bot_core._plugin_send_text("   ", target="999004")
        check("★ 空文本不进历史", not (bot_core.chat_history.get("999004") or []))

        print("\n[E2] 长期记忆（沙漏）也要补：主动说的话不能第二天就忘")
        import types
        fed = []
        fake = types.ModuleType("sandglass_bridge")
        fake.memory_feed = lambda msg, scope="group": fed.append(dict(msg)) or {"sandglass": True}
        old_mod = sys.modules.get("sandglass_bridge")
        sys.modules["sandglass_bridge"] = fake
        try:
            state["ok"] = True
            bot_core._plugin_send_text("这句要进长期记忆", target="999006")
            check("★ 调了 memory_feed", len(fed) == 1, fed)
            m = fed[0] if fed else {}
            check("★ 只补 Bot 那一行（text 空 + user_pre_logged）",
                  m.get("text") == "" and m.get("user_pre_logged") is True, m)
            check("★ reply 是发出去的话", m.get("reply") == "这句要进长期记忆", m)
            check("★ 私聊 group_id 为空", m.get("group_id") == "", m)
            fed.clear()
            bot_core._plugin_send_text("群里的主动话", group_id="888002")
            check("群消息带 group_id", fed and fed[0].get("group_id") == "888002", fed)
        finally:
            if old_mod is not None:
                sys.modules["sandglass_bridge"] = old_mod
            else:
                sys.modules.pop("sandglass_bridge", None)
            bot_core.chat_history.pop("999006", None)
            bot_core.group_chat_history.pop("888002", None)

        print("\n[F] 主链已有的历史不能被冲掉")
        bot_core.chat_history["999005"] = [{"role": "user", "content": "之前说的话", "ts": time.time()}]
        state["ok"] = True
        bot_core._plugin_send_text("插件插一句", target="999005")
        h5 = bot_core.chat_history.get("999005") or []
        check("★ 原有条目还在", any(x.get("content") == "之前说的话" for x in h5), h5)
        check("★ 新条目追加在后面", h5 and h5[-1].get("content") == "插件插一句", h5[-1:])

    finally:
        bot_core.save_event_log = orig_log
        bot_core.smart_send = orig_send
        bot_core.smart_send_group = orig_send_g
        for k in ("999001", "999002", "999003", "999004", "999005"):
            bot_core.chat_history.pop(k, None)
        bot_core.group_chat_history.pop("888001", None)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
