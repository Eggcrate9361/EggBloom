# -*- coding: utf-8 -*-
"""tests/test_session_log_trace.py —— 事件日志的 trace_id（1.6.0 P0 前置 C）

1.6.0 的 P0 判据是「agent 与闲聊是同一条路径」，证据就是**同一个 trace id**。
1.5.10 只在日志行尾挂了 `trace=`；`harness/session_log.py` 的事件里没有这个字段。
这里补上，并锁住五件事：

  A. 设了当前 trace → 事件带 `trace_id`
  B. 显式传 trace_id → 覆盖上下文
  C. 没有上下文 → **不写字段**（旧日志形状不变，读取方要容错）
  D. 落盘后重读（模拟重启）→ 字段还在
  E. 派生 messages 不受影响（trace 只加不改变语义）
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import session_log as sl  # noqa: E402

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


def main():
    td = tempfile.mkdtemp(prefix="eb_slog_")
    sl.SESSION_LOG_DIR = td
    sl.reset_cache()

    print("[A] 上下文里的 trace 会写进事件")
    sl.set_trace("trace-aaa")
    e1 = sl.append_user_msg("10001", "在吗")
    e2 = sl.append_bot_reply("10001", "在")
    check("user_msg 带 trace", e1.get("trace_id") == "trace-aaa", e1)
    check("bot_reply 带同一个 trace", e2.get("trace_id") == "trace-aaa", e2)

    print("\n[B] 显式 trace_id 覆盖上下文")
    e3 = sl.append("group", "10001", "user_msg", {"content": "x"}, trace_id="trace-bbb")
    check("用了显式的", e3.get("trace_id") == "trace-bbb", e3)

    print("\n[C] 没有上下文")
    sl.set_trace("")
    e4 = sl.append("group", "10001", "user_msg", {"content": "y"})
    check("不写空字段", "trace_id" not in e4, e4)

    print("\n[D] 落盘后重读（模拟重启）")
    sl.reset_cache()
    evs = sl.events_of("group", "10001")
    traces = [e.get("trace_id") for e in evs]
    check("四条事件都在", len(evs) == 4, evs)
    check("前两条是 trace-aaa", traces[:2] == ["trace-aaa", "trace-aaa"], traces)
    check("第三条是 trace-bbb", traces[2] == "trace-bbb", traces)
    check("第四条没有 trace", traces[3] is None, traces)

    print("\n[E] 派生 messages 不受影响")
    msgs = sl.derive_messages("group", "10001")
    check("内容仍是原样", [m.get("content") for m in msgs] == ["在吗", "在", "x", "y"],
          msgs)
    check("角色对得上", [m.get("role") for m in msgs] == ["user", "assistant", "user", "user"],
          msgs)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
