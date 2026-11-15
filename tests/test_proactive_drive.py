# -*- coding: utf-8 -*-
"""tests/test_proactive_drive.py —— 主动开口的冲动值闸门（2026-11-06）

锁四件事：
  · 惦记的事放越久，冲动值越高；
  · 沉默越久，冲动值越高；
  · 刚聊过 / 刚主动找过 → 冲动值不够，不开口；
  · trigger_mode=drive 时，每日上限与绝对最小间隔仍然拦得住。

旧概率模式的等价性仍由 tests/test_proactive_gate.py 的 200 条 baseline 锁住。
"""
import datetime
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import proactive_gate as pg  # noqa: E402

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
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


def _cfg(**kw):
    c = {
        "trigger_mode": "drive",
        "urge_threshold": 0.62,
        "min_interval_min": 180,
        "hard_min_interval_min": 20,
        "max_per_day": 3,
        "active_cooldown_min": 30,
        "quiet_hours": "",
    }
    c.update(kw)
    return c


def main():
    now = time.time()
    trg = pg.make_trigger(pg.TRIGGER_HEARTBEAT, target="10001")

    print("=== ① 惦记的事：放 6 小时，冲动值过线 ===")
    st = {
        "pending_topics": {"10001": "他上次说的问卷"},
        "pending_topics_ts": {"10001": now - 6 * 3600},
        "last_user_ts_by": {"10001": now - 10 * 3600},
        "last_proactive_by": {"10001": now - 5 * 3600},
    }
    u, why = pg.compute_urge(_cfg(), st, "10001", now=now, rng=lambda: 0.0)
    check("urge >= 0.62", u >= 0.62, f"{u:.3f}")
    check("reason 提到惦记", "惦记" in why, why)
    ev = pg.evaluate(trg, _cfg(), st, now=now, rng=lambda: 0.0)
    check("drive 放行", ev.get("ok"), ev.get("blocked_by"))
    check("eval 带 urge", ev.get("urge", 0) >= 0.62, str(ev.get("urge")))

    print("=== ② 没惦记的事，沉默 20 小时也能过 ===")
    st2 = {"last_user_ts_by": {"10001": now - 20 * 3600},
           "last_proactive_by": {"10001": now - 30 * 3600}}
    u2, _ = pg.compute_urge(_cfg(), st2, "10001", now=now, rng=lambda: 0.0)
    check("沉默 20h 过线", u2 >= 0.62, f"{u2:.3f}")
    check("沉默 20h 放行", pg.evaluate(trg, _cfg(), st2, now=now, rng=lambda: 0.0).get("ok"))

    print("=== ③ 刚聊过 / 刚找过 → 冲动值不够 ===")
    st3 = {"last_user_ts_by": {"10001": now - 2 * 3600},
           "last_proactive_by": {"10001": now - 3 * 3600}}
    u3, _ = pg.compute_urge(_cfg(), st3, "10001", now=now, rng=lambda: 0.0)
    check("刚聊过分数低", u3 < 0.62, f"{u3:.3f}")
    e3 = pg.evaluate(trg, _cfg(), st3, now=now, rng=lambda: 0.0)
    check("刚聊过被拦", not e3.get("ok"), e3.get("blocked_by"))

    print("=== ④ 每日上限 / 绝对最小间隔 ===")
    st4 = dict(st)
    st4["daily"] = {"date": datetime.datetime.now().strftime("%Y-%m-%d"),
                    "proactive": 3, "ai_calls": 0}
    e4 = pg.evaluate(trg, _cfg(), st4, now=now, rng=lambda: 0.0)
    check("每日上限拦截", e4.get("gate") == "daily_cap", e4.get("gate"))
    st5 = dict(st)
    st5["last_proactive_by"] = {"10001": now - 600}
    e5 = pg.evaluate(trg, _cfg(), st5, now=now, rng=lambda: 0.0)
    check("绝对最小间隔拦截", e5.get("gate") == "hard_min_interval", e5.get("gate"))

    print("=" * 58)
    if FAIL:
        print(f"失败 {FAIL} 条：")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print(f"通过 {PASS} / 失败 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
