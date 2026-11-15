# -*- coding: utf-8 -*-
"""1.5.10 H4：分离与真实 —— 五个替身接口的契约快照。

1.6.0 的 NullGate / NullBudget / NullPersona / NullDrop / NullRules 必须与现实现
**同签名、同返回结构**。这里把当前返回值钉住：以后谁改了签名或结构，测试先红。
只读契约，不改任何行为。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import reply_budget  # noqa: E402
import reply_drop  # noqa: E402
import persona_form  # noqa: E402
import speak_gate  # noqa: E402
import de_ai  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


print("\n[1] speak_gate.allow(batch, cfg, *, stats, rnd, now) -> (bool, str)")
ok, why = speak_gate.allow({"text": "在吗", "sender": "1"}, {}, now=0.0)
check("返回二元组 (bool, str)", isinstance(ok, bool) and isinstance(why, str), (ok, why))
ok2, why2 = speak_gate.allow({"text": "x", "forced": True}, {}, now=0.0)
check("forced 恒 True", ok2 is True, (ok2, why2))

print("\n[2] reply_budget: pick / check / fit")
b = reply_budget.pick(rng=0.0)
# 契约键：name / parts_min / parts_max / max_chars / max_total（1.5.9 §3.3）
_keys = {"name", "parts_min", "parts_max", "max_chars", "max_total"}
check("pick(rng=0.0) 返回 dict 且键齐全", isinstance(b, dict) and _keys <= set(b), b)
check("条数区间在 1–3", 1 <= int(b.get("parts_min") or 0) <= int(b.get("parts_max") or 0) <= 3, b)
res = reply_budget.check("好", b)
check("check 返回三元组 (bool, str, dict)", isinstance(res, tuple) and len(res) == 3
      and isinstance(res[0], bool) and isinstance(res[1], str) and isinstance(res[2], dict), res)
check("fit 返回 str", isinstance(reply_budget.fit("这是一句很长很长的话，需要被压短一些才符合预算", b), str))
check("prompt_line 返回 str", isinstance(reply_budget.prompt_line(b), str))

print("\n[3] persona_form: apply / decorate")
form = persona_form.apply(None, cfg={})
check("apply 返回 dict", isinstance(form, dict), type(form))
check("decorate 返回 str", isinstance(persona_form.decorate("你好", form, trigger="在吗", rng=0.0), str))

print("\n[4] reply_drop.drop_reason(...) -> str")
check("返回 str", isinstance(reply_drop.drop_reason(new_msgs=0, age=0.0), str))
check("翻篇时以「话头翻篇」开头",
      reply_drop.drop_reason(new_msgs=5, turns=2, age=5).startswith("话头翻篇"))

print("\n[5] de_ai 规则段注入 -> str")
blk = de_ai.de_ai_rule_block({})
check("de_ai_rule_block(cfg) 返回 str", isinstance(blk, str), type(blk))
check("空配置也返回字符串（可为空串）", blk is not None)

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
