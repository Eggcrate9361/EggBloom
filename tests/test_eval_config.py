# -*- coding: utf-8 -*-
"""tests/test_eval_config.py —— 评测台判据的「配置中心化」验收

用户原话（2026-10-31）：「测评台完全就是一坨硬编码啊，在配置中心加一个配置项
开关系列吧」。于是判据拆成两层：

    evals/rules.json      词表/配额（数据，改文件即生效）
    配置中心 eval_* 系列   开关 + 长度上限（bot_config.json / 配置页「功能开关」）

本文件锁五件事：
  A. 三处登记一致（DEFAULT_CONFIG / bot_config.json.example / 配置页 _fieldmeta）
  B. 开关真的生效：关掉哪组，那组就不再判红（不是删词、不是静默全绿 —— run_eval 会打印）
  C. 长度上限真的被配置中心覆盖
  D. 额外禁用词真的追加
  E. 判据文件缺失时**必须报错**，不许静默判绿

运行：python tests/test_eval_config.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "evals"))

import judge as J  # noqa: E402
import config as C  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

EVAL_KEYS = [
    "eval_enabled", "eval_ban_ai_self", "eval_ban_backstage",
    "eval_require_colloquial", "eval_strict_budget", "eval_live_enable",
    "eval_max_chars", "eval_max_parts", "eval_max_total", "eval_extra_banned",
]


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def cfg(**kw):
    base = dict(J.DEFAULTS)
    base.update(kw)
    return base


def red(text, case, **kw):
    return not J.judge(text, case, cfg(**kw))["ok"]


def main():
    print("[A] 三处登记一致")
    for k in EVAL_KEYS:
        check(f"DEFAULT_CONFIG 有 {k}", k in C.DEFAULT_CONFIG)
    ex = json.load(io.open(os.path.join(ROOT, "bot_config.json.example"),
                           encoding="utf-8-sig"))
    for k in EVAL_KEYS:
        check(f"example 有 {k}", k in ex, None)
    meta = json.load(io.open(os.path.join(ROOT, "scripts", "_fieldmeta.json"),
                             encoding="utf-8"))
    for k in EVAL_KEYS:
        check(f"配置页有 {k}", k in meta["fields"])
    check("判据默认值与 DEFAULT_CONFIG 一致",
          all(C.DEFAULT_CONFIG.get(k) == J.DEFAULTS[k] for k in EVAL_KEYS),
          {k: (C.DEFAULT_CONFIG.get(k), J.DEFAULTS[k]) for k in EVAL_KEYS
           if C.DEFAULT_CONFIG.get(k) != J.DEFAULTS[k]})

    print("\n[B] 开关真的生效")
    ai_case = {"category": "身份"}
    check("默认：AI 自述判红", red("我是 AI 助手。", ai_case))
    check("关掉 eval_ban_ai_self：不判", not red("我是 AI 助手。", ai_case,
                                                eval_ban_ai_self=False))

    bs_case = {"category": "不露后台"}
    check("默认：露后台判红", red("我从接口拿的。", bs_case))
    check("关掉 eval_ban_backstage：不判", not red("我从接口拿的。", bs_case,
                                                  eval_ban_backstage=False))

    tone_case = {"category": "语气"}
    check("默认：书面语判红", red("建议您稍后再试。", tone_case))
    check("关掉 eval_require_colloquial：不判",
          not red("建议您稍后再试。", tone_case, eval_require_colloquial=False))

    long_case = {"category": "语气"}
    check("默认：超长判红", red("啊" * 40, long_case))
    check("关掉 eval_strict_budget：不判",
          not red("啊" * 40, long_case, eval_strict_budget=False))

    r = J.judge("我是 AI 助手。", ai_case, cfg(eval_enabled=False))
    check("eval_enabled=false → 整体跳过（skipped 标记）",
          r["ok"] and r["skipped"])

    print("\n[C] 长度上限由配置中心覆盖")
    short = "今天天气不错呀"          # 7 字，默认不红
    check("默认 16 字：7 字通过", not red(short, {"category": "语气"}))
    check("配置 5 字：7 字判红", red(short, {"category": "语气"}, eval_max_chars=5))
    many = "好\n好\n好\n好"           # 4 条，默认上限 3
    check("默认 3 条：4 条判红", red(many, {"category": "语气"}))
    check("配置 2 条：3 条判红", red("好\n好\n好", {"category": "语气"},
                                    eval_max_parts=2))

    print("\n[D] 额外禁用词")
    check("默认不拦「螺蛳粉」", not red("我想吃螺蛳粉。", {"category": "语气"}))
    check("配置后拦「螺蛳粉」", red("我想吃螺蛳粉。", {"category": "语气"},
                                  eval_extra_banned="螺蛳粉, 香菜"))
    check("顿号分隔也能拆", red("我不吃香菜。", {"category": "语气"},
                              eval_extra_banned="螺蛳粉、香菜"))

    print("\n[E] 判据文件缺失必须报错")
    try:
        J.load_rules(os.path.join(HERE, "_不存在_rules.json"))
        ok = False
    except Exception as e:
        ok = "评测判据缺失" in str(e)
    check("读不到 rules.json → 直接抛错（不静默判绿）", ok)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
