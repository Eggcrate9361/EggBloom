# -*- coding: utf-8 -*-
"""evals/run_eval.py —— 评测台跑分（FIXPLAN 1.5.11 P0-1）

    python evals/run_eval.py            # 默认 offline，不花钱
    python evals/run_eval.py --offline
    python evals/run_eval.py --json     # 给 CI 用
    python evals/run_eval.py --live     # 现调模型（默认关；见下）

## offline 到底在评什么

用每条场景自带的 `sample`（真机形态的模型原文）过一遍**生产后处理链**，再交给
`judge.py` 机械判分：

    strip_ctx_marks → strip_tool_errors → de_ai_post_process → reply_guard

所以它锁的是"**后处理 + 判分**"这条链：谁把兜底逻辑改坏了、谁放了新的脏话术
进来，offline 立刻红。它**不评模型本身**——那要真调模型（live）。

## 为什么 live 还没接

live 需要 `bot_core` 那套完整 prompt 组装（人格 + 身份 + 记忆 + 场景）。在这里
重写一套等于造第二个 prompt 源，正是本计划要消灭的东西。所以 live 留到 P0-2
收口后接 `build_prompt`，现在只保留开关与提示。

## 门槛

机械项红 → 不许合。退出码 0 = 全绿。
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import judge as judge_mod  # noqa: E402
from de_ai import de_ai_post_process  # noqa: E402
from reply_budget import strip_ctx_marks, strip_tool_errors  # noqa: E402

try:
    from reply_guard import guard as _reply_guard
except Exception:  # pragma: no cover - 模块缺失时评测仍要能跑
    _reply_guard = None

SCENARIOS = os.path.join(_HERE, "scenarios.json")
DEFAULT_DE_AI_CFG = None  # None = de_ai 用自己的默认配置，与线上一致


def load_cases(path=SCENARIOS):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases") or []


def postprocess(raw: str) -> str:
    """与线上同一批函数、同一顺序（顺序在 README 里写明）。"""
    text = strip_ctx_marks(raw or "")
    text = strip_tool_errors(text)
    text = de_ai_post_process(text, DEFAULT_DE_AI_CFG)
    if _reply_guard is not None:
        text = _reply_guard(text)
    return text


def run_offline(cases, cfg=None):
    results = []
    for case in cases:
        out = postprocess(case.get("sample", ""))
        results.append((case, judge_mod.judge(out, case, cfg), out))
    return results


def describe_config(cfg):
    """把当前生效的判据开关打出来 —— 关掉的检查必须看得见，不能静默放过。"""
    on = lambda k: "开" if bool(cfg.get(k, True)) else "**关**"
    return ("判据：ai自述={} 露后台={} 书面语={} 长度={} | 上限 {}字/{}条/{}字{}").format(
        on("eval_ban_ai_self"), on("eval_ban_backstage"),
        on("eval_require_colloquial"), on("eval_strict_budget"),
        cfg.get("eval_max_chars"), cfg.get("eval_max_parts"), cfg.get("eval_max_total"),
        ("｜额外禁用：" + str(cfg.get("eval_extra_banned")))
        if cfg.get("eval_extra_banned") else "")


def report(results):
    by_cat = judge_mod.summary(results)
    print("┌────────────────┬──────┬──────┐")
    print("│ 分类           │ 过/总 │ 状态 │")
    print("├────────────────┼──────┼──────┤")
    for cat in ("身份", "记忆召回", "跨场景", "语气", "不露后台", "纠错", "任务"):
        s = by_cat.get(cat)
        if not s:
            continue
        state = "OK" if s["ok"] == s["n"] else "RED"
        pad = " " * max(0, 14 - len(cat) * 2)
        print(f"│ {cat}{pad}│ {s['ok']}/{s['n']}  │ {state:<4} │")
    print("└────────────────┴──────┴──────┘")

    bad = [(c, r, out) for c, r, out in results if not r["ok"]]
    if bad:
        print("\n失败明细：")
        for case, res, out in bad:
            print(f"  [{case.get('id')}] {case.get('category')}  输入：{case.get('input')}")
            print(f"     输出：{out!r}")
            for f in res["failures"]:
                print(f"     - {f}")

    total = len(results)
    passed = total - len(bad)
    print(f"\nRESULT: {total} 场 / {passed} 过 / {len(bad)} 红")
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="EggBloom 评测台")
    ap.add_argument("--offline", action="store_true", help="只跑机械判据（默认）")
    ap.add_argument("--live", action="store_true", help="真调模型（需要 API key）")
    ap.add_argument("--json", action="store_true", help="输出 JSON，给 CI 用")
    ap.add_argument("--file", default=SCENARIOS, help="场景文件路径")
    ap.add_argument("--no-config", action="store_true",
                    help="忽略 bot_config.json，只用内置默认判据（CI/复现用）")
    args = ap.parse_args(argv)

    if args.live:
        print("live 模式尚未接：需要 bot_core 的 prompt 组装（P0-2 收口后接），"
              "现在请用 --offline。", file=sys.stderr)
        return 2

    cfg = judge_mod.effective_config({} if args.no_config else None)
    if not bool(cfg.get("eval_enabled", True)):
        print("评测台已在配置中心关闭（eval_enabled=false），本次不判分。")
        return 0

    cases = load_cases(args.file)
    if not cases:
        print("场景为空：" + args.file, file=sys.stderr)
        return 2

    if not args.json:
        print(describe_config(cfg))

    results = run_offline(cases, cfg)
    if args.json:
        payload = {
            "total": len(results),
            "passed": sum(1 for _, r, _ in results if r["ok"]),
            "cases": [
                {"id": c.get("id"), "category": c.get("category"),
                 "ok": r["ok"], "failures": r["failures"], "output": out}
                for c, r, out in results
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["passed"] == payload["total"] else 1

    return report(results)


if __name__ == "__main__":
    sys.exit(main())
