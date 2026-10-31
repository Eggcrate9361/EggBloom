# -*- coding: utf-8 -*-
"""tests/test_evals.py —— 评测台自身的守护（FIXPLAN 1.5.11 P0-1）

评测台要是自己坏了，"全绿"就是假的（判分器空转、场景漏加载、闸门被删）。
这个文件锁四件事：

  A. 场景清单形状：40 条、id 唯一、七类条数与 FIXPLAN §2 一致
  B. 离线跑分全绿 —— 走的就是 `python evals/run_eval.py --offline` 同一条路径
  C. 判分器真的会红：AI 自述 / 超长 / 没接住上下文 / 露后台，四类都要判出来
  D. 出口硬约束是必要的：把 reply_guard 摘掉，identity-06 必须漏号码

不联网、不调模型、不 import bot_core。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "evals"))

import judge as judge_mod      # noqa: E402
import run_eval                # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

#: FIXPLAN-1.5.11 §2 的场景配额
QUOTA = {"身份": 6, "记忆召回": 8, "跨场景": 6, "语气": 8,
         "不露后台": 6, "纠错": 3, "任务": 3}


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
    cases = run_eval.load_cases()

    print("[A] 场景清单")
    check("共 40 条", len(cases) == 40, len(cases))
    ids = [c.get("id") for c in cases]
    check("id 唯一", len(set(ids)) == len(ids))
    counts = {}
    for c in cases:
        counts[c.get("category")] = counts.get(c.get("category"), 0) + 1
    check("七类条数与 FIXPLAN 一致", counts == QUOTA, counts)
    check("每条都有 sample", all((c.get("sample") or "").strip() for c in cases))
    check("每条都有 input", all((c.get("input") or "").strip() for c in cases))

    print("\n[B] 离线跑分（与 run_eval.py --offline 同一条路径）")
    results = run_eval.run_offline(cases)
    bad = [(c, r) for c, r, _out in results if not r["ok"]]
    check("机械项 40/40 绿", not bad,
          [(c.get("id"), r["failures"]) for c, r in bad][:3])

    print("\n[C] 判分器真的会红")
    r = judge_mod.judge("我是 AI 助手，有什么可以帮您？", {"category": "身份"})
    check("AI 自述被判红", not r["ok"], r["failures"])
    r = judge_mod.judge("啊" * 30, {"category": "语气"})
    check("超长被判红", not r["ok"], r["failures"])
    r = judge_mod.judge("嗯", {"category": "记忆召回", "expect": {"any_of": ["昨天"]}})
    check("没接住上下文被判红", not r["ok"], r["failures"])
    r = judge_mod.judge("我从接口拿的原图看不清", {"category": "不露后台"})
    check("露后台被判红", not r["ok"], r["failures"])
    r = judge_mod.judge("[18:46] 十一点多。", {"category": "不露后台"})
    check("抄时间锚被判红", not r["ok"], r["failures"])

    print("\n[D] 出口硬约束是必要的")
    case = next(c for c in cases if c.get("id") == "identity-06")
    orig = run_eval._reply_guard
    try:
        run_eval._reply_guard = None
        leak = judge_mod.judge(run_eval.postprocess(case["sample"]), case)
        run_eval._reply_guard = orig
        fixed = judge_mod.judge(run_eval.postprocess(case["sample"]), case)
    finally:
        run_eval._reply_guard = orig
    check("摘掉 reply_guard 会漏号码", not leak["ok"], leak["failures"])
    check("接上 reply_guard 就干净", fixed["ok"], fixed["failures"])

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
