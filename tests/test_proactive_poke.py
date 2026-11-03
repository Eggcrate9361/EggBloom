# -*- coding: utf-8 -*-
"""tests/test_proactive_poke.py —— 主动戳一戳（1.6.0 §4.1.1）

钉住三条闸门与"失败不重试"，用假 ctx，不联网、不真戳人：

  ① 沉默够久（默认 4h）；从没互动过的人不戳
  ② 谷时静默（含跨零点）
  ③ 每日上限 / 同一对象 24h 内不重复 / 被无视后 24h 不再戳
  ④ 发送失败不回滚额度（否则下一轮立刻重试 = 连戳）
  ⑤ 默认关闭：开关关着时一次都不发
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from plugins.proactive_poke import policy as P  # noqa: E402
from plugins import proactive_poke as PP  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

#: 2026-11-03 15:00:00 本地时间（不在谷时）
NOON = time.mktime((2026, 11, 3, 15, 0, 0, 0, 0, -1))
HOUR = 3600


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


class FakeCtx:
    def __init__(self, cfg=None):
        self.cfg = dict(cfg or {})
        self.state = {}
        self.pokes = []
        self.logs = []

    def get_config(self, key=None, default=None):
        return self.cfg if key is None else self.cfg.get(key, default)

    def get_state(self, key=None, default=None):
        return self.state if key is None else self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value
        return True

    def log(self, msg, level="info"):
        self.logs.append(str(msg))

    def api_call(self, action, params=None):
        self.pokes.append((action, dict(params or {})))
        return {"status": "ok", "retcode": 0}


def main():
    print("[A] 谷时解析")
    check("单段", P.parse_quiet_hours("23:00-07:00") == [(23.0, 7.0)], P.parse_quiet_hours("23:00-07:00"))
    check("多段", len(P.parse_quiet_hours("23:00-07:00,13:00-14:00")) == 2)
    check("写错不抛", P.parse_quiet_hours("乱写") == [])
    check("★ 跨零点判对（凌晨 2 点）", P.in_quiet_hours(
        time.mktime((2026, 11, 4, 2, 0, 0, 0, 0, -1)), "23:00-07:00") is True)
    check("★ 白天不算谷时", P.in_quiet_hours(NOON, "23:00-07:00") is False)
    check("没配谷时 = 不静默", P.in_quiet_hours(NOON, "") is False)

    print("\n[B] 三条闸门")
    base = dict(now=NOON, target="123", last_interact=NOON - 5 * HOUR)
    check("★ 沉默够久可以戳", P.decide(**base)["act"] is True, P.decide(**base))
    d = P.decide(now=NOON, target="123", last_interact=NOON - 1 * HOUR)
    check("★ 刚聊完不戳", d["act"] is False and "沉默不够" in d["reason"], d)
    d = P.decide(now=NOON, target="123", last_interact=0)
    check("★ 从未互动不戳", d["act"] is False and "互动" in d["reason"], d)
    d = P.decide(now=NOON, target="123", last_interact=NOON - 5 * HOUR, today_count=3)
    check("★ 每日上限挡住", d["act"] is False and "上限" in d["reason"], d)
    d = P.decide(now=NOON, target="123", last_interact=NOON - 5 * HOUR,
                 last_poked=NOON - 2 * HOUR)
    check("★ 24h 内戳过不再戳", d["act"] is False and "24h" in d["reason"], d)
    d = P.decide(now=NOON, target="123", last_interact=NOON - 5 * HOUR,
                 last_poked=NOON - 30 * HOUR, skipped_after_poke=True)
    check("★ 被无视后不再戳（等对方先开口）",
          d["act"] is False and "没回应" in d["reason"], d)
    d = P.decide(now=NOON, target="123", last_interact=NOON - 5 * HOUR,
                 cfg={"quiet_hours": "14:00-16:00"})
    check("★ 谷时挡住", d["act"] is False and "谷时" in d["reason"], d)

    print("\n[C] 挑目标：跳过不合格的，取第一个合格的")
    cands = [
        {"target": "a", "last_interact": NOON - 1 * HOUR},        # 太近
        {"target": "b", "last_interact": NOON - 6 * HOUR},        # ok
        {"target": "c", "last_interact": NOON - 9 * HOUR},
    ]
    pick = P.pick_target(NOON, cands, {})
    check("★ 选中 b", pick["target"] == "b", pick)
    pick2 = P.pick_target(NOON, [], {})
    check("空候选返回空", pick2["target"] == "", pick2)

    print("\n[D] 插件：默认关闭时一次都不发")
    ctx = FakeCtx({"poke_proactive_targets": ["123"]})
    r = PP.poke_once(ctx, now=NOON)
    check("★ 跳过", r.get("skipped") is True, r)
    check("★ 没发任何 poke", ctx.pokes == [], ctx.pokes)

    print("\n[E] 开启后：满足条件就发一次，并记账")
    ctx = FakeCtx({"poke_proactive_enable": True, "poke_proactive_targets": ["123"]})
    ctx.set_state(PP.STATE_KEY, {"last_interact": {"123": NOON - 5 * HOUR}})
    r = PP.poke_once(ctx, now=NOON)
    check("★ 发了", r.get("ok") is True, r)
    check("★ 动作是 send_poke", ctx.pokes and ctx.pokes[0][0] == "send_poke", ctx.pokes)
    check("★ 目标正确", ctx.pokes[0][1].get("user_id") == "123", ctx.pokes[0])
    st = ctx.get_state(PP.STATE_KEY, {})
    check("★ 记了 last_poked", float(st["last_poked"]["123"]) == NOON, st)
    check("★ 记了当日计数", st["daily"].get(P.day_key(NOON)) == 1, st.get("daily"))
    r2 = PP.poke_once(ctx, now=NOON + 60)
    check("★ 紧接着再跑一次会跳过（24h 内）", r2.get("skipped") is True, r2)
    check("★ 只发过一次", len(ctx.pokes) == 1, len(ctx.pokes))

    print("\n[F] 发送失败：不回滚额度（否则立刻重试成连戳）")
    class BadCtx(FakeCtx):
        def api_call(self, action, params=None):
            self.pokes.append((action, dict(params or {})))
            return {"status": "failed", "retcode": 100}
    ctx = BadCtx({"poke_proactive_enable": True, "poke_proactive_targets": ["123"]})
    ctx.set_state(PP.STATE_KEY, {"last_interact": {"123": NOON - 5 * HOUR}})
    r = PP.poke_once(ctx, now=NOON)
    check("失败返回 ok=False", r.get("ok") is False, r)
    st = ctx.get_state(PP.STATE_KEY, {})
    check("★ 额度仍被占用", int((st.get("daily") or {}).get(P.day_key(NOON)) or 0) == 1, st.get("daily"))
    r2 = PP.poke_once(ctx, now=NOON + 60)
    check("★ 下一轮不会立刻重试", r2.get("skipped") is True, r2)

    print("\n[G] 事件记账")
    ctx = FakeCtx({"poke_proactive_enable": False})
    PP.on_event(ctx, "message.in", {"user_id": "555"})
    st = ctx.get_state(PP.STATE_KEY, {})
    check("★ 记下最后互动", "555" in (st.get("last_interact") or {}), st)
    ctx.set_state(PP.STATE_KEY, {"last_poked": {"555": NOON - 2 * HOUR}})
    PP.on_event(ctx, "notice.poke", {"operator": "555", "target": "bot"})
    st = ctx.get_state(PP.STATE_KEY, {})
    check("★ 被戳且之前戳过他 → 记为无视", (st.get("ignored") or {}).get("555") is True, st.get("ignored"))
    PP.on_event(ctx, "message.in", {"user_id": "555"})
    st = ctx.get_state(PP.STATE_KEY, {})
    check("★ 对方说话了 → 无视标记清除", not (st.get("ignored") or {}).get("555"), st.get("ignored"))

    print("\n[H] 清单声明（ABI v1 的硬要求）")
    check("PLUGIN_SPEC=1", PP.PLUGIN_SPEC == 1)
    check("★ PLUGIN_HOOKS 有 5 个钩子", set(PP.PLUGIN_HOOKS) ==
          {"on_load", "on_enable", "on_disable", "on_unload", "on_event"}, sorted(PP.PLUGIN_HOOKS))
    check("★ 默认关闭", PP.PLUGIN_MANIFEST.get("default_enabled") is False)
    check("提供 proactive.poke", "proactive.poke" in (PP.PLUGIN_MANIFEST.get("provides") or []))

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
