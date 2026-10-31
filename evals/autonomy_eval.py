# -*- coding: utf-8 -*-
"""evals/autonomy_eval.py —— 自主性插件策略层的机械用例

FIXPLAN 1.5.11 对 autonmy 的归属只有一句：**不接线，先把 `guard()` 的语义锁死**
（接线是 1.6.0）。锁的方式就是这里：把《自主性插件-设计》§2 的裁决规则表变成
可执行用例，红了就是语义漂移。

    python evals/autonomy_eval.py          # 默认
    python evals/autonomy_eval.py --json

用例在 `evals/autonomy_cases.json`；判据在 `plugins/autonomy/policy.py`。
两边都不改代码就能加用例 —— 与评测台（reply 判据）同一套思路。

## 为什么单独一个脚本

reply 评测（`run_eval.py`）判的是"模型说的话"，这里是"代码的裁决语义"，
混在一起会让 `RESULT: 40 场` 那个数字失去意义。`run_all_tests.py --evals`
两个都跑，各自一行结果。
"""
import argparse
import importlib.util
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

CASES = os.path.join(_HERE, "autonomy_cases.json")
POLICY = os.path.join(_ROOT, "plugins", "autonomy", "policy.py")


def load_policy(path=POLICY):
    """按文件加载策略层 —— `plugins/autonomy` 没有 `__init__.py`（接线在 1.6.0），
    直接 `import plugins.autonomy.policy` 会 ImportError。"""
    spec = importlib.util.spec_from_file_location("eb_autonomy_policy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_cases(path=CASES):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases") or [], data.get("prompt_cases") or []


def run(policy=None, path=CASES):
    """跑全部用例，返回 [{id, ok, why}]。"""
    policy = policy or load_policy()
    cases, prompt_cases = load_cases(path)
    results = []

    for c in cases:
        r = policy.guard(c.get("action", ""), c.get("level", 0),
                         bool(c.get("confirmed", False)))
        got = r.get("verdict")
        want = c.get("expect")
        ok = got == want
        why = "" if ok else f"期望 {want}，实际 {got}（{r.get('reason')}）"
        results.append({"id": c.get("id"), "kind": "guard", "ok": ok, "why": why})

    for c in prompt_cases:
        lv = int(c.get("level"))
        text = policy.permission_prompt(lv)
        # 提示词里的等级必须与 guard 用的 level 同源：断言里面那个 L 数字就是 lv
        m = re.search(r"L(\d)", text)
        got = int(m.group(1)) if m else None
        ok = got == lv
        why = "" if ok else f"提示词等级 {got} != 传入 {lv}"
        results.append({"id": c.get("id"), "kind": "prompt", "ok": ok, "why": why})

    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="自主性策略层机械用例")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--file", default=CASES)
    args = ap.parse_args(argv)

    try:
        results = run(path=args.file)
    except Exception as e:
        print(f"跑不下去：{e}", file=sys.stderr)
        return 2

    bad = [r for r in results if not r["ok"]]
    if args.json:
        print(json.dumps({"total": len(results), "passed": len(results) - len(bad),
                          "cases": results}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(("  [OK]   " if r["ok"] else "  [FAIL] ") + str(r["id"]) + ("  " + r["why"] if r["why"] else ""))
        print(f"\nRESULT: autonomy {len(results)} 场 / {len(results) - len(bad)} 过 / {len(bad)} 红")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
