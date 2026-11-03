# -*- coding: utf-8 -*-
"""tests/test_idle_exit_context.py —— 退场语 / 主动消息要进上下文与记忆（2026-11-04）

用户指出：「在没有引用的情况下，撤回反馈、空闲退场应该都没有进入上下文或者记忆」。

分三条路核实过：
  · 撤回反馈 → 走 ABI 的 ctx.send_text → `_plugin_send_text`（8de7fa6 起已补）
  · 主动说话 → 同一条路（同上）
  · 空闲退场 → 走 `smart_send_group` 这条**独立**路径，只记了
    `_record_bot_group_msg`（重复检测用），既没进 group_chat_history、
    也没喂沙漏 —— 这就是本用例钉的那个洞。

判据：
  ① 退场语进群上下文（role=assistant，且不超过群历史上限）
  ② 退场语进长期记忆（memory_feed 只补 Bot 一行，text 空 + user_pre_logged）
  ③ 两处退场发送点都接了（源码断言，防止只补一处）
"""
import os
import sys
import types

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
    gid = "777001"
    fed = []
    fake = types.ModuleType("sandglass_bridge")
    fake.memory_feed = lambda msg, scope="group": fed.append(dict(msg))
    old_mod = sys.modules.get("sandglass_bridge")
    sys.modules["sandglass_bridge"] = fake
    try:
        print("[A] 退场语进群上下文")
        bot_core.group_chat_history.pop(gid, None)
        bot_core._record_exit_into_context(gid, "唉 又没人理我")
        gh = bot_core.group_chat_history.get(gid) or []
        check("★ 历史里有一条", len(gh) == 1, gh)
        check("★ 角色是 assistant", gh and gh[0].get("role") == "assistant", gh[:1])
        check("★ 内容就是退场语", gh and "又没人理我" in gh[0].get("content", ""), gh[:1])
        check("带时间戳", bool(gh and gh[0].get("ts")), gh[:1])

        print("\n[B] 退场语进长期记忆（只补 Bot 一行）")
        check("★ 调了 memory_feed", len(fed) == 1, fed)
        m = fed[0] if fed else {}
        check("★ text 空 + user_pre_logged", m.get("text") == "" and m.get("user_pre_logged") is True, m)
        check("★ reply 是退场语", "又没人理我" in str(m.get("reply")), m)
        check("group_id 正确", m.get("group_id") == gid, m)

        print("\n[C] 群历史上限仍然生效")
        cap = bot_core._history_cap()
        for i in range(cap + 5):
            bot_core._record_exit_into_context(gid, f"第{i}次退场")
        gh2 = bot_core.group_chat_history.get(gid) or []
        check(f"★ 不超过 {cap} 条", len(gh2) <= cap, len(gh2))
        check("留下最新那条", gh2 and gh2[-1].get("content") == f"第{cap + 4}次退场", gh2[-1:])

        print("\n[D] 两处退场发送点都接了（防止只补一处）")
        src = open(bot_core.__file__, encoding="utf-8", errors="replace").read()
        check("★ 定义了这个 helper", "def _record_exit_into_context(" in src)
        n = src.count("_record_exit_into_context(gid, exit_phrase)")
        check("★ 两个发送点都调了", n == 2, n)
        check("helper 里同时写历史与记忆",
              "group_chat_history" in src[src.find("def _record_exit_into_context("):
                                         src.find("def _record_bot_group_msg(")]
              and "memory_feed" in src[src.find("def _record_exit_into_context("):
                                        src.find("def _record_bot_group_msg(")])

    finally:
        if old_mod is not None:
            sys.modules["sandglass_bridge"] = old_mod
        else:
            sys.modules.pop("sandglass_bridge", None)
        bot_core.group_chat_history.pop(gid, None)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
