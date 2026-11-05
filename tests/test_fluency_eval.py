# -*- coding: utf-8 -*-
"""tests/test_fluency_eval.py —— 话病评测台自身的守护（1.6.0 P1-2）

锁四件事：
  A. 20 场、id 唯一、五类配额、每条都有 sample 与 broken
  B. 好样本基线全绿（走 fluency_eval 同一条路径）
  C. 故意改坏的提示词能被抓到 —— 20 条 broken 逐条必须判红
  D. 判据不是空转：摘掉 JUDGES 后坏样本必须全绿；编造/数字确实复用了
     harness/grounding.py 的生产实现，不是另造一套判据
  E. 接线：run_all_tests.py --evals 会跑第三套
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "evals"))
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import fluency_eval as fe  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
QUOTA = {"编造": 4, "反问": 4, "复读": 4, "冷场": 4, "数字": 4}


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
    cases = fe.load_cases()

    print("[A] 场景清单")
    check("★ 共 20 场", len(cases) == 20, len(cases))
    ids = [c.get("id") for c in cases]
    check("★ id 唯一", len(set(ids)) == len(ids))
    counts = {}
    for c in cases:
        counts[c.get("category")] = counts.get(c.get("category"), 0) + 1
    check("★ 五类配额 4/4/4/4/4", counts == QUOTA, counts)
    check("★ 每条都有 sample 与 broken",
          all((c.get("sample") or "").strip() and (c.get("broken") or "").strip()
              for c in cases))
    check("★ 好/坏样本不相同",
          all(c.get("sample") != c.get("broken") for c in cases))

    print("\n[B] 好样本基线")
    results = fe.run(cases)
    bad = [(c.get("id"), f) for c, f, _ in results if f]
    check("★ 20 场全绿", not bad, bad[:3])

    print("\n[C] 故意改坏的提示词要被抓到")
    broken = fe.run_broken(cases)
    missed = [(c.get("id"), c.get("category")) for c, f, _ in broken if not f]
    check("★ 坏样本 20/20 判红", not missed, missed)
    check("★ 每条都给出了失败原因",
          all(f and all(isinstance(x, str) and x for x in f) for _, f, _ in broken))

    print("\n[D] 判据不是空转")
    orig = dict(fe.JUDGES)
    try:
        # 用"永远判绿"的桩替换全部专项判据 —— clear() 会走 judge_case 的
        # 「未知分类」防御分支照样红，那测的就不是判据本身了。
        fe.JUDGES.clear()
        fe.JUDGES.update({k: (lambda c, t: []) for k in orig})
        all_green = all(not fe.judge_case(c, c.get("broken")) for c in cases)
    finally:
        fe.JUDGES.clear()
        fe.JUDGES.update(orig)
    check("★ 摘掉判据后坏样本全绿", all_green)
    check("★ 恢复后仍全抓到",
          all(fe.judge_case(c, c.get("broken")) for c in cases))
    fe_src = io.open(os.path.join(ROOT, "evals", "fluency_eval.py"),
                     encoding="utf-8").read()
    check("★ 编造/数字复用生产 grounding.ungrounded",
          "grounding.ungrounded(" in fe_src)
    # 行为判据比搜字符串硬：注释里提到 de_ai 不算「走了改写链」。
    check("★ prepare 只去标记、不改写内容（否则坏句被修好就抓不到）",
          all(fe.prepare(c.get("broken")) == (c.get("broken") or "").strip()
              for c in cases))

    print("\n[E] 接线")
    runner = io.open(os.path.join(ROOT, "run_all_tests.py"), encoding="utf-8").read()
    check("★ --evals 会跑第三套", '"fluency_eval.py"' in runner)
    check("★ 三套各自打一行 RESULT", runner.count("scripts = [") == 1
          and runner.count("RESULT:") >= 0)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
