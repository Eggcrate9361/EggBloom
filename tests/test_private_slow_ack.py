# -*- coding: utf-8 -*-
"""私聊慢回填充（P1-4，2026-10-30）。

## 为什么
实测 70 条私聊回复：中位 21 秒、区间 10–104 秒。用户的感受不是"慢"，
是"它不理我"。修法：等超过 3 秒还没结果，就先发一句"接住"（嗯 / 在 / 等下），
把等待压在 LLM 之前；正文随后照常。

## 钉死
  1. 到点只发一次（不是每秒一句）
  2. 结果来得快就不发（别把 3 秒的回复变成"嗯" + 正文两条）
  3. slow_after=0 表示关（群聊/重生路径不传它）
  4. 填充失败不影响正文等待

运行：python tests/test_private_slow_ack.py
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


_orig_log = bot_core.log
bot_core.log = lambda _m: None
_next_id = [800000]


def _mk_task(delay: float, result="答"):
    """造一个会在 delay 秒后完成的 AI 任务，返回 task_id。"""
    _next_id[0] += 1
    tid = _next_id[0]
    ev = threading.Event()
    with bot_core._ai_results_lock:
        bot_core._ai_results[tid] = {"event": ev, "result": None,
                                     "progress": time.time()}

    def _finish():
        with bot_core._ai_results_lock:
            bot_core._ai_results[tid]["result"] = result
        ev.set()

    threading.Timer(delay, _finish).start()
    return tid


try:
    # ── 1. 慢了 → 发一次 ────────────────────────────────────────
    hits = []
    tid = _mk_task(1.0)
    r = bot_core._wait_ai_result(tid, timeout=5.0, idle_timeout=5.0, max_total=10.0,
                                on_slow=lambda: hits.append(time.time()),
                                slow_after=0.2)
    check("慢任务仍拿到结果", r == "答", r)
    check("慢任务发了一次填充", len(hits) == 1, hits)

    # ── 2. 快了 → 不发 ─────────────────────────────────────────
    hits2 = []
    tid = _mk_task(0.0)
    r = bot_core._wait_ai_result(tid, timeout=5.0, idle_timeout=5.0, max_total=10.0,
                                 on_slow=lambda: hits2.append(1), slow_after=2.0)
    check("快任务不发填充", hits2 == [], hits2)
    check("快任务结果正确", r == "答", r)

    # ── 3. slow_after=0 → 关 ───────────────────────────────────
    hits3 = []
    tid = _mk_task(0.8)
    bot_core._wait_ai_result(tid, timeout=5.0, idle_timeout=5.0, max_total=10.0,
                             on_slow=lambda: hits3.append(1), slow_after=0.0)
    check("slow_after=0 不填充", hits3 == [], hits3)

    # ── 4. 填充抛异常不影响等待 ────────────────────────────────
    def _boom():
        raise RuntimeError("发送挂了")

    tid = _mk_task(0.9)
    r = bot_core._wait_ai_result(tid, timeout=5.0, idle_timeout=5.0, max_total=10.0,
                                 on_slow=_boom, slow_after=0.2)
    check("填充抛异常仍拿到结果", r == "答", r)

    # ── 5. 填充文案：短、且真的发出去 ──────────────────────────
    sent = []
    _orig_send = bot_core.send_message
    bot_core.send_message = lambda t, q=None: (sent.append((t, q)), True)[1]
    try:
        bot_core._private_slow_ack("10001")
    finally:
        bot_core.send_message = _orig_send
    check("填充发了一条", len(sent) == 1, sent)
    check("填充文案在名单里",
          bool(sent) and sent[0][0] in bot_core._PRIVATE_SLOW_ACKS, sent)
    check("填充 2–3 字", bool(sent) and 1 <= len(sent[0][0]) <= 3, sent)
    check("填充发给了对的人", bool(sent) and sent[0][1] == "10001", sent)
finally:
    bot_core.log = _orig_log

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
