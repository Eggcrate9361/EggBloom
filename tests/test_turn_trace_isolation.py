# -*- coding: utf-8 -*-
"""tests/test_turn_trace_isolation.py —— turn 账本的目录隔离与决策阶段覆盖

判据：
  [A] 账本目录在**调用时**解析 `EGG_TURN_TRACE_DIR`，不是 import 时缓存
  [B] `_sandbox.sandbox_neurobase()` 之后，账本落点在生产目录之外
  [C] 写入后不会在仓库 `data/trace/` 留下文件
  [D] 私聊与群聊都必须有 `DECIDED` 写入点（私聊此前缺失）

为什么有 [A]：生产 `data/trace/turns-20261116.jsonl` 第一行是 `testturn01`
（target=g1 / user=u1），是测试写进真实账本的夹具。若目录在 import 时缓存，
测试再设置环境变量已经来不及。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

SB = sandbox_neurobase()

import harness.turn_trace as T  # noqa: E402

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


def main():
    print("=== A) 目录在调用时解析 ===")
    d1 = T._dir()
    check("A1 沙箱后目录不在仓库内",
          os.path.abspath(d1) != os.path.abspath(os.path.join(ROOT, "data", "trace")), d1)
    check("A2 沙箱目录在临时根下", os.path.abspath(d1).startswith(os.path.abspath(SB)), d1)

    old = os.environ.get("EGG_TURN_TRACE_DIR")
    alt = os.path.join(SB, "trace_alt")
    try:
        os.environ["EGG_TURN_TRACE_DIR"] = alt
        check("A3 改环境变量后立刻生效（未缓存）", T._dir() == alt, T._dir())
    finally:
        if old is None:
            os.environ.pop("EGG_TURN_TRACE_DIR", None)
        else:
            os.environ["EGG_TURN_TRACE_DIR"] = old

    print("\n=== B) 写入落点 ===")
    tid = "aaaabbbbcccc"
    before = set(os.listdir(os.path.join(ROOT, "data", "trace"))) \
        if os.path.isdir(os.path.join(ROOT, "data", "trace")) else set()
    ok = T.note(tid, "RECEIVED", scene="group", target="g1", user="u1", text_len=1)
    path = T._path()
    check("B1 note 返回真", ok is True)
    check("B2 账本写在沙箱目录", os.path.abspath(path).startswith(os.path.abspath(SB)), path)
    check("B3 文件真的存在", os.path.exists(path), path)

    print("\n=== C) 生产目录未被写入 ===")
    after = set(os.listdir(os.path.join(ROOT, "data", "trace"))) \
        if os.path.isdir(os.path.join(ROOT, "data", "trace")) else set()
    check("C1 仓库 data/trace 没有新增文件", before == after, sorted(after - before))
    prod_today = os.path.join(ROOT, "data", "trace", os.path.basename(path))
    leaked = False
    if os.path.exists(prod_today):
        try:
            leaked = tid in open(prod_today, encoding="utf-8").read()
        except OSError:
            leaked = False
    check("C2 生产账本里没有本轮夹具 turn", not leaked, prod_today)

    print("\n=== D) 两处场景都有决策写入点 ===")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check('D1 群聊记 DECIDED', '"DECIDED", scene="group"' in src)
    check('D2 私聊记 DECIDED', '"DECIDED", scene="private"' in src)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
