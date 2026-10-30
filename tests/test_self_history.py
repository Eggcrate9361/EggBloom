# -*- coding: utf-8 -*-
"""自我检索：行为账本可查 + agent 原语 + 主链工具（2026-10-30）。

用户要的点：账本不能"写死塞 prompt"，模型得能自己按条件检索。

钉死：
  1. search 支持关键词/类型/时间窗/条数，按时间倒序
  2. hours=None 表示不限时间
  3. A3 原语 self_history 能调通（envelope ok）
  4. 主链工具 self_history 注册进 work 组

运行：python tests/test_self_history.py
"""
import importlib
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="selfhist_test_")
import self_actions as sa  # noqa: E402

sa.DATA_DIR = TMP
sa.LOG_FILE = os.path.join(TMP, "self_actions.jsonl")
sa._MEM = None

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


try:
    now = time.time()
    sa.record("say", scene="group", target="777", detail="天王盖地虎", ts=now - 60)
    sa.record("say", scene="private", target="10001", detail="今天好累", ts=now - 600)
    sa.record("meme", scene="group", target="777", detail="得意", ts=now - 3600)
    sa.record("say", scene="group", target="777", detail="很久以前的话", ts=now - 72 * 3600)

    r = sa.search(query="天王", now=now)
    check("按关键词查", [x["detail"] for x in r] == ["天王盖地虎"], r)

    r2 = sa.search(kind="meme", hours=24, now=now)
    check("按类型查", len(r2) == 1 and r2[0]["kind"] == "meme", r2)

    r3 = sa.search(hours=1, now=now)
    check("时间窗生效", all(now - float(x["ts"]) <= 3600.5 for x in r3), r3)

    r4 = sa.search(hours=None, limit=2, now=now)
    check("不限时间 + 倒序", len(r4) == 2 and r4[0]["detail"] == "天王盖地虎", r4)

    r5 = sa.search(scene="private", now=now)
    check("按场景查", [x["detail"] for x in r5] == ["今天好累"], r5)

    # A3 原语
    import harness.qq_primitives as qp
    ps = qp.PrimitiveSet()
    names = {s["name"] for s in ps.specs()}
    check("原语清单里有 self_history", "self_history" in names, sorted(names))
    env = ps.call("self_history", query="天王", hours=24, limit=5)
    check("原语返回 ok", env.get("ok") is True, env)
    check("原语带数据", (env.get("data") or {}).get("count") == 1, env)

    # 主链工具
    import harness.tool_self as ts
    fns = ts.register_all()
    check("工具函数可调", ts.self_history(query="天王")["ok"] is True)
    check("注册返回函数表", "self_history" in fns, fns)
    from harness import tool_registry as tr
    check("注册进注册表", "self_history" in tr.all_names(), tr.all_names()[-5:])
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
