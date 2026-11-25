# -*- coding: utf-8 -*-
"""tests/test_proactive_premodel.py —— 规则能定的不调模型

背景（2026-11-25 生产数据）：24 小时里 `no_reply` 进模型 73 次，模型一次都没 say；
而 prompt 自己就写着「多数时候该安静」。这 73 次调用换来的全是"不说"。

判据：
  [A] 陈述/吐槽类上一条 → 规则直接判不说，模型一次都不调
  [B] 问句、承诺类上一条 → 放给模型（保留"该补一句"的能力）
  [C] 不知道她上一条说了什么（bot_text 空）→ 不拦，交给模型
  [D] 其它 kind 不受影响
  [E] 规则判 skip 时同样要 mark_fired（否则同一观察每 30 秒重新判）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

from harness.proactivity import arbitrator as A  # noqa: E402

PASS, FAIL = 0, 0
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


def base_cfg(**kw):
    c = {"proactivity_enable": True, "proactivity_budget_level": 1,
         "proactivity_quiet_hours": "", "recall_feedback_by": "main"}
    c.update(kw)
    return c


def obs(bot_text, kind="no_reply", target="1000000006"):
    return {"kind": kind, "scene": "private", "target": target, "since": 1000.0,
            "elapsed_s": 600.0, "dedupe_key": "%s:%s" % (kind, target),
            "priority": 30, "evidence": {"bot_text": bot_text}}


def run_one(o):
    """跑一轮仲裁，返回 (结果, 模型被调了几次, fired 记录)。"""
    calls = {"n": 0}
    fired = []

    def chat_fn(messages, max_tokens=160):
        calls["n"] += 1
        return '{"act":"say","text":"在","reason":"测试"}'

    r = A.arbitrate([o], cfg=base_cfg(), now=2000.0,
                    is_fired=lambda k: False, last_sent=lambda s, t: 0,
                    hourly_count=lambda s: 0, chat_fn=chat_fn,
                    mark_fired=lambda k, n: fired.append(k))
    return r, calls["n"], fired


def main():
    print("=== A) 陈述类上一条：规则判不说 ===")
    for text in ("行，那我就先睡了", "哈哈这也太抽象了", "图我看了，挺离谱"):
        r, n, fired = run_one(obs(text))
        check("A-%s 不调模型" % text[:6], r is None and n == 0, (r, n))

    print("\n=== B) 问句 / 承诺：放给模型 ===")
    for text in ("你刚才说的那个是啥意思？", "等下给你发", "我回头帮你问问"):
        r, n, _ = run_one(obs(text))
        check("B-%s 调了模型" % text[:6], n == 1, n)

    print("\n=== C) 不知道上一条说了什么：不拦 ===")
    r, n, _ = run_one(obs(""))
    check("C1 空 bot_text 仍走模型", n == 1, n)

    print("\n=== D) 其它 kind 不受影响 ===")
    r, n, _ = run_one(obs("行，那我先睡了", kind="at_dropped"))
    check("D1 at_dropped 仍走模型", n == 1, n)

    print("\n=== E) 规则判 skip 也要 mark_fired ===")
    r, n, fired = run_one(obs("行，那我先睡了"))
    check("E1 记了去重键", len(fired) == 1, fired)
    check("E2 原因写清楚", bool(A.premodel_skip(obs("行，那我先睡了"))), "")

    print("\n=== F) 纯函数边界 ===")
    check("F1 非 no_reply 返回空串", A.premodel_skip({"kind": "idle_contact"}) == "")
    check("F2 缺 evidence 不炸", A.premodel_skip({"kind": "no_reply"}) == "")

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
