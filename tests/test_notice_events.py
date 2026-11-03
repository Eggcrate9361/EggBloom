# -*- coding: utf-8 -*-
"""tests/test_notice_events.py —— 事件总线扩到四个（2026-11-03）

执行总表 P1-6 / §8.1 #5：核心此前只发 `message.in` / `bot.replied` 两个事件。
戳一戳与撤回属于 **notice 类**，插件拿不到就无从判定
（主动戳一戳的冷却、撤回插件、未来的 vitals 都要用）。

锁四件事：
  A. `notice.poke` 载荷字段固定 `{group_id, operator, target, ts}`
  B. ★ 它发在「目标是不是 Bot」的过滤**之前** —— 群友互戳也要有事件，
     否则"没人戳我"这件事永远不可判
  C. `notice.recall` 载荷固定 `{group_id, operator, message_id, ts}`
  D. 群撤回带群号、私聊撤回 group_id 是空串（不是 None，也不是 "None"）
"""
import os
import sys

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

GID = "1000000003"        # 合成号：真实号不进仓库
USER = "1000000009"
OTHER = "1000000001"


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
    events = []

    class _PM:
        def emit_event(self, name, payload=None):
            events.append((name, dict(payload or {})))
            return []

    def last(name):
        hits = [p for n, p in events if n == name]
        return hits[-1] if hits else {}

    orig_pm = bot_core.get_plugin_manager
    orig_send, orig_send_g = bot_core.smart_send, bot_core.smart_send_group
    bot_core.get_plugin_manager = lambda: _PM()
    bot_core.smart_send = lambda *a, **k: True
    bot_core.smart_send_group = lambda *a, **k: True
    try:
        print("[A] notice.poke 载荷")
        events.clear()
        # 群友互戳：target 不是 Bot → 主链会早退，但事件必须已经发出（判据 B）
        bot_core._handle_poke_event({
            "notice_type": "notify", "sub_type": "poke",
            "user_id": USER, "target_id": OTHER, "group_id": GID,
        })
        names = [n for n, _ in events]
        check("★ 群友互戳也发 notice.poke（发在过滤之前）", "notice.poke" in names, names)
        p = last("notice.poke")
        check("★ 字段正好四个", set(p) == {"group_id", "operator", "target", "ts"}, p)
        check("operator = 戳的人", p.get("operator") == USER, p.get("operator"))
        check("target = 被戳的人", p.get("target") == OTHER, p.get("target"))
        check("group_id 带群号", p.get("group_id") == GID, p.get("group_id"))
        check("ts 是数字", isinstance(p.get("ts"), (int, float)), type(p.get("ts")).__name__)

        print("\n[D] 私聊戳：group_id 是空串")
        events.clear()
        bot_core._handle_poke_event({
            "notice_type": "notify", "sub_type": "poke",
            "user_id": USER, "target_id": OTHER,
        })
        p2 = last("notice.poke")
        check("私聊戳也发事件", bool(p2), events)
        check("group_id 空串（不是 None / 'None'）", p2.get("group_id") == "", p2.get("group_id"))

        print("\n[C] notice.recall 载荷")
        events.clear()
        bot_core._handle_recall_event({
            "notice_type": "group_recall", "user_id": USER,
            "group_id": GID, "message_id": 12345,
        })
        names = [n for n, _ in events]
        check("群撤回发 notice.recall", "notice.recall" in names, names)
        r = last("notice.recall")
        # 四个必填字段只增不改；刀 2 又补了四个给插件判定用的事实
        check("★ 四个必填字段都在", {"group_id", "operator", "message_id", "ts"} <= set(r), r)
        check("★ 刀 2 补了判定事实",
              {"buffer_cancelled", "last_bot_msg", "session_active", "idle_seconds"} <= set(r), r)
        check("buffer_cancelled 是布尔", isinstance(r.get("buffer_cancelled"), bool))
        check("last_bot_msg 是数字", isinstance(r.get("last_bot_msg"), (int, float)))
        check("★ 带 notice_type（订阅方靠它分群/私聊）",
              r.get("notice_type") == "group_recall", r.get("notice_type"))
        check("message_id 转成字符串", r.get("message_id") == "12345", r.get("message_id"))
        check("operator = 撤回的人", r.get("operator") == USER, r.get("operator"))

        print("\n[D] 私聊撤回：group_id 是空串")
        events.clear()
        bot_core._handle_recall_event({
            "notice_type": "friend_recall", "user_id": USER, "message_id": 678,
        })
        r2 = last("notice.recall")
        check("私聊撤回也发事件", bool(r2), events)
        check("group_id 空串", r2.get("group_id") == "", r2.get("group_id"))
        check("message_id 正确", r2.get("message_id") == "678", r2.get("message_id"))

        print("\n[E] 四个事件都在核心代码里")
        src = open(bot_core.__file__, encoding="utf-8").read()
        for ev in ("message.in", "bot.replied", "notice.poke", "notice.recall"):
            check(f"核心会发 {ev}", f'"{ev}"' in src)

        print("\n[F] 事件派发失败不能影响主流程")
        class _Boom:
            def emit_event(self, name, payload=None):
                raise RuntimeError("插件炸了")

        bot_core.get_plugin_manager = lambda: _Boom()
        ok = True
        try:
            bot_core._handle_poke_event({"user_id": USER, "target_id": OTHER, "group_id": GID})
        except Exception as e:
            ok = False
            check("派发异常被吞掉", False, f"{type(e).__name__}: {e}")
        check("★ 插件抛异常不影响 poke 处理", ok)
    finally:
        bot_core.get_plugin_manager = orig_pm
        bot_core.smart_send, bot_core.smart_send_group = orig_send, orig_send_g

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
