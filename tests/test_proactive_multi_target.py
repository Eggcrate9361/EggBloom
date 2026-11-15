# -*- coding: utf-8 -*-
"""tests/test_proactive_multi_target.py —— 主动消息支持多个人（2026-11-01）

用户："主动为什么只有一个人啊" —— 旧实现只有一个 `target_qq`，
`_resolve_target()` 永远返回一个人，所以主动只会盯着一号用户。

锁五件事：
  A. 目标解析：`target_qqs` > `target_qq` > 主配置 target_qqs；去重
  B. 闸门按人算：A 刚被搭过话、B 很久没搭 → 只有 B 放行
  C. 轮转公平：两人都放行时，先挑「最久没被搭话」的；发完换另一个
  D. 旧状态兼容：没有 `last_proactive_by` 时退回全局 `last_proactive_ts`
  E. `send_now(target=...)` 能指定发给谁
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import importlib  # noqa: E402
pt = importlib.import_module("plugins.proactive_talk")

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


class FakeCtx:
    def __init__(self, main_cfg=None):
        self.main_cfg = main_cfg or {}
        self.state = {}
        self.sent = []

    def get_config(self, k, d=None):
        return self.main_cfg.get(k, d)

    def get_state(self):
        return self.state

    def set_state(self, k, v):
        self.state[k] = v

    def send_text(self, text, target=""):
        self.sent.append((target, text))
        return True

    def log(self, msg, level="info"):
        pass


def main():
    print("[A] 目标解析")
    check("逗号/空格/分号都能拆",
          pt._parse_qqs("111,222 333；444,111") == ["111", "222", "333", "444"],
          pt._parse_qqs("111,222 333；444,111"))
    check("列表优先于单值",
          pt._resolve_targets({"target_qqs": ["111", "222"], "target_qq": "333"})
          == ["111", "222", "333"])
    check("旧单值仍可用", pt._resolve_targets({"target_qq": "999"}) == ["999"])
    pt._ctx = FakeCtx({"target_qqs": ["777", "888"]})
    check("空配置退回主配置", pt._resolve_targets({}) == ["777", "888"])
    pt._ctx = None

    print("\n[B] 闸门按人算")
    cfg = {"target_qqs": ["111", "222"], "min_interval_min": 180,
           "active_cooldown_min": 0, "speak_chance": 1.0, "quiet_hours": "",
           "bio_rhythm": False}
    now = 1000000.0
    state = {"last_proactive_by": {"111": now - 60, "222": now - 3600},
             "last_proactive_ts": now - 60}
    import time as _t
    real_time = _t.time
    _t.time = lambda: now
    try:
        d1 = pt._evaluate(cfg, state, target="111")
        d2 = pt._evaluate(cfg, state, target="222")
    finally:
        _t.time = real_time
    check("A 刚说过 → 拦", d1.get("ok") is False and "间隔" in str(d1.get("blocked_by")), d1)
    check("B 很久没搭 → 放行（若 AI 就绪）",
          ("ok" in d2), d2)

    print("\n[C] 轮转公平")
    pt._ctx = FakeCtx()
    pt._ctx.state = {"last_proactive_by": {"111": now - 60, "222": now - 7200},
                     "last_proactive_ts": now - 60}
    cfg2 = dict(cfg, min_interval_min=0, active_cooldown_min=0)
    orig = {
        "_load_config": pt._load_config,
        "_build_messages": pt._build_messages,
        "_ai_text": pt._ai_text,
        "_clean_bubbles": pt._clean_bubbles,
        "_send_bubbles": pt._send_bubbles,
        "_update_pending_topic": pt._update_pending_topic,
        "_ai_ready": pt._ai_ready,
    }
    sent = []
    pt._load_config = lambda: cfg2
    pt._build_messages = lambda *a, **k: []
    pt._ai_text = lambda *a, **k: "在吗"
    pt._clean_bubbles = lambda t, n: ["在吗"]
    pt._send_bubbles = lambda qq, b: (sent.append(qq) or 1)
    pt._update_pending_topic = lambda *a, **k: None
    pt._ai_ready = lambda: True
    try:
        r1 = pt._proactive_once(force=True)
        r2 = pt._proactive_once(force=True)
    finally:
        for k, v in orig.items():
            setattr(pt, k, v)
    check("第一次挑最久没搭的 222", r1.get("target") == "222", r1)
    check("第二次轮到 111", r2.get("target") == "111", r2)
    check("实际发出两次且对象不同", sent == ["222", "111"], sent)

    print("\n[D] 旧状态兼容（没有 last_proactive_by）")
    pt._ctx = FakeCtx()
    state_old = {"last_proactive_ts": now - 60}
    real_time = _t.time
    _t.time = lambda: now
    try:
        d = pt._evaluate(cfg2, state_old, target="111")
    finally:
        _t.time = real_time
    check("退回全局时间戳后被拦", d.get("ok") is False, d)

    print("\n[E] send_now 指定目标")
    captured = {}
    pt._ctx = FakeCtx()
    orig_once = pt._proactive_once
    pt._proactive_once = lambda force=False, target="": (captured.update(force=force, target=target) or {"ok": True})
    try:
        import threading
        r = pt.send_now({"target": "222"})
        import time as _tt
        _tt.sleep(0.2)
    finally:
        pt._proactive_once = orig_once
    check("强制 + 指定对象", captured.get("force") is True and captured.get("target") == "222", captured)
    check("返回提示", r.get("ok") is True and "222" in str(r.get("msg")), r)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
