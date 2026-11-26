# -*- coding: utf-8 -*-
"""tests/test_proactive_action.py —— 主动层吐统一动作（S3）

背景：群聊说 reply/skip/observe、主动层说 say/poke/image、私聊没有枚举。
S1 建了词表、S2 收了私聊，这一片把主动层也接上，并在运行账里留统一动作。

判据：
  [A] say → TEXT、poke → POKE
  [B] image / video 是"产物直发"，归 TOOL（不是发表情包）
  [C] 仲裁返回值同时保留 act（下游照旧）和 action（账本用）
  [D] 运行账 _recent 里记到 action
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

from harness.action_policy import Action, from_proactive_act  # noqa: E402
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


def obs(kind="no_reply", target="1000000006"):
    return {"kind": kind, "scene": "private", "target": target, "since": 1000.0,
            "elapsed_s": 600.0, "dedupe_key": "%s:%s" % (kind, target),
            "evidence": {"bot_text": "你刚才说的那个是啥？"}, "priority": 30}


def run(act_json):
    calls = {"n": 0}

    def chat_fn(messages, max_tokens=160):
        calls["n"] += 1
        return act_json

    return A.arbitrate([obs()], cfg=base_cfg(), now=2000.0,
                       is_fired=lambda k: False, last_sent=lambda s, t: 0,
                       hourly_count=lambda s: 0, chat_fn=chat_fn)


def main():
    print("=== A) 动作映射 ===")
    check("A1 say → TEXT", from_proactive_act("say") is Action.TEXT)
    check("A2 poke → POKE", from_proactive_act("poke") is Action.POKE)
    check("A3 skip → SKIP", from_proactive_act("skip") is Action.SKIP)

    print("\n=== B) 产物直发归 TOOL ===")
    check("B1 image → TOOL", from_proactive_act("image") is Action.TOOL)
    check("B2 video → TOOL", from_proactive_act("video") is Action.TOOL)
    check("B3 与 self_actions 的 image 不是一回事（那边是 MEME）",
          Action.of("image") is Action.MEME)

    print("\n=== C) 仲裁返回值 ===")
    r = run('{"act":"say","text":"在","reason":"该说一句"}')
    check("C1 仍有 act（下游照旧）", r and r.get("act") == "say", r)
    check("C2 多了 action=TEXT", r and r.get("action") is Action.TEXT,
          r.get("action") if r else None)
    check("C3 decision 带 by/kind", bool(r and (r.get("decision") or {}).get("by") == "model"))
    check("C4 str(action) 还是人类可读",
          r and str(r.get("action")) == "reply", str(r.get("action")) if r else None)

    print("\n=== D) 运行账 ===")
    src = open(os.path.join(ROOT, "plugins", "proactivity", "__init__.py"),
               encoding="utf-8").read()
    check("D1 _recent 记统一动作", '"action": str(action.get("action") or act)' in src)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
