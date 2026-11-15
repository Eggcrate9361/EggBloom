# -*- coding: utf-8 -*-
"""插件注入预算的**豁免**（2026-10-30，真机复盘）。

## 现象

    14:17:08 [私聊] [插件预算] 共 80 字｜截断 ego:328→80
    14:29:47 [私聊] [插件预算] 共 79 字｜截断 ego:331→79

每轮都砍掉 ego 的 3/4；`plugin_inject_daily_chars=600` 意味着聊七八句后
当天**彻底没有** ego 注入。用户体感：「越聊越不像它 / 像重新认识」。

## 判据

ego 注入的是**人格、情绪、关系、记忆**，不是"注入噪音"（搜索词/术语/状态）。
它不该和那些插件抢同一个 80 字额度。

## 钉死

  1. `ego` 的内容不截断、不占别人的额度
  2. 其它插件照旧受单条 / 每日上限约束
  3. 关掉预算（enabled=False）时行为不变

运行：python tests/test_plugin_budget_exempt.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import plugin_budget as pb  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


BUDGET = {"enabled": True, "max_chars": 40, "daily_chars": 40}
EGO = "【情绪】开心，关系不错，记得他昨天说累。" * 8      # 远超 40 字
OTHER = "【术语】xxx " * 12                              # 也远超 40 字

tmp = tempfile.mkdtemp(prefix="pbudget_test_")
try:
    led = pb.Ledger(os.path.join(tmp, "ledger.json"))
    text, rep = pb.plan([("ego", 10, EGO), ("web_search", 90, OTHER)],
                        BUDGET, led, now=1000.0)
    kept_names = [n for n, _ in rep["kept"]]
    trimmed_names = [n for n, _a, _b in rep["trimmed"]]
    dropped_names = [n for n, _r in rep["dropped"]]

    check("ego 进入注入", "ego" in kept_names, rep)
    check("ego 没被截断", "ego" not in trimmed_names, rep)
    check("ego 字数完整", f"ego {len(EGO)}" in [f"{n} {c}" for n, c in rep["kept"]],
          rep["kept"])
    check("ego 全文在结果里", EGO.strip() in text, len(text))
    check("其它插件仍受预算", ("web_search" in trimmed_names) or
          ("web_search" in dropped_names), rep)
    check("其它插件没全塞进去", OTHER.strip() not in text)

    # 每日上限也不该拦 ego：把账本记满
    led2 = pb.Ledger(os.path.join(tmp, "ledger2.json"))
    led2.charge("ego", 10 ** 6)
    text2, rep2 = pb.plan([("ego", 10, EGO)], BUDGET, led2, now=1001.0)
    check("ego 不受每日上限", EGO.strip() in text2, rep2)

    # 关掉预算：行为与原来一致（谁都不截）
    text3, rep3 = pb.plan([("ego", 10, EGO), ("web_search", 90, OTHER)],
                          {"enabled": False}, None, now=1002.0)
    check("关预算时全量注入", EGO.strip() in text3 and OTHER.strip() in text3)

    # 普通插件在同样预算下确实会被砍（证明豁免不是把整个预算关掉）
    text4, rep4 = pb.plan([("web_search", 90, OTHER)], BUDGET, None, now=1003.0)
    check("普通插件单独跑仍被砍", OTHER.strip() not in text4, len(text4))
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
