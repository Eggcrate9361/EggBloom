# -*- coding: utf-8 -*-
"""tests/test_autonomy_eval.py —— 自主性策略层机械用例的守护

FIXPLAN 1.5.11 对 autonomy 的归属只有一句：不接线，先把 `guard()` 语义锁死。
锁在 `evals/autonomy_eval.py` + `evals/autonomy_cases.json`。

这个文件锁四件事：
  A. 用例能加载、条数对、id 唯一（别悄悄少跑）
  B. 真跑全绿（13 条：越权 / 同级 / 未确认 / 已确认 / 未登记 / 空动作 / 提示词等级同源）
  C. **判分器真的会红**：把 guard 换成"永远 allow"，必须报红
  D. 两条要害语义单独钉死：未登记动作即便 confirmed 也 deny；L3 未确认是 need_confirm 不是 deny

跑法：python tests/test_autonomy_eval.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "evals"))

import autonomy_eval as AE  # noqa: E402

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


class FakePolicy:
    """按 cases 里的 expect 全给对的假策略；用来验证判分器本身。"""

    def __init__(self, verdict="allow", prompt_level=None):
        self.verdict = verdict
        self.prompt_level = prompt_level

    def guard(self, action, level, confirmed=False):
        return {"verdict": self.verdict, "reason": "fake"}

    def permission_prompt(self, level):
        lv = self.prompt_level if self.prompt_level is not None else level
        return f"【自主性·权限 L{lv}】…"


def main():
    policy = AE.load_policy()
    cases, prompts = AE.load_cases()

    print("[A] 用例清单")
    check("guard 用例 10 条", len(cases) == 10, len(cases))
    check("提示词用例 3 条", len(prompts) == 3, len(prompts))
    ids = [c["id"] for c in cases] + [c["id"] for c in prompts]
    check("id 唯一", len(set(ids)) == len(ids), ids)

    print("\n[B] 真跑全绿")
    results = AE.run(policy)
    bad = [r for r in results if not r["ok"]]
    check("全绿（13 条）", not bad, [(r["id"], r["why"]) for r in bad][:3])

    print("\n[C] 判分器真的会红")
    always_allow = AE.run(FakePolicy(verdict="allow"))
    check("永远 allow → 必须报红",
          any(not r["ok"] for r in always_allow), always_allow[:2])
    check("报红的是越权那条",
          any((not r["ok"]) and r["id"] == "guard-01" for r in always_allow),
          [r["id"] for r in always_allow if not r["ok"]])
    wrong_prompt = AE.run(FakePolicy(prompt_level=0))
    check("提示词等级与 level 不同源 → 报红",
          any((not r["ok"]) and r["kind"] == "prompt" for r in wrong_prompt),
          wrong_prompt)

    print("\n[D] 两条要害语义")
    r = policy.guard("kernel.format", 3, confirmed=True)
    check("未登记动作：即便 confirmed 也 deny",
          r["verdict"] == "deny", r)
    r = policy.guard("delete", 3, confirmed=False)
    check("L3 未确认 → need_confirm（不是 deny）",
          r["verdict"] == "need_confirm", r)
    r = policy.guard("msg.send", 2)
    check("L2 发消息 → allow", r["verdict"] == "allow", r)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
