# -*- coding: utf-8 -*-
"""AI 任务等待判据：按"进展"续期，而不是"总时长"（2026-10-27）

## 线上事故

群 123456789，两次被 @ 问「讲解一下精神分析引论」，一次都没回：

```
09:20:50 提交 AI 任务
09:20:58 历史摘要压缩完（8s，一次独立的 LLM 调用）
09:21:09 首轮模型回来说"要搜索"（11s）
09:21:12 搜完（3s）
09:21:20 30 秒到点 → 放弃 → 「AI决定不回复: (空)」
09:21:39 二轮模型终于返回（8712 tokens，¥0.004356）→ 没人要了
```

一条已经生成好的回复被丢掉，钱照付，群里一句没有；日志还说"AI 决定不回复"，
看日志的人只会去查决策器。

## 判据为什么不能是"总时长"

旧代码用**用户消息里有没有 URL / 问卷填表**来预判耗时。但真正的时间花在
"模型自己决定调不调工具"：搜一轮 = 两次模型调用 + 一次搜索，跟措辞无关。

## 本测试锁什么

1. 一直没有 event 也没有 progress → 在 timeout 附近放弃，并置"超时"标记
2. 持续有 progress → 超过 timeout 也不放弃，续到 max_total 才停
3. 结果正常返回时不置超时标记（那才是"模型决定不回复"）

运行：python tests/test_ai_wait_timeout.py
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def _register(tid):
    """造一个真实的 _ai_results 条目（不启动 worker）。"""
    ev = threading.Event()
    with bot_core._ai_results_lock:
        bot_core._ai_results[tid] = {"event": ev, "result": None,
                                     "progress": time.time()}
    return ev


def _drop(tid):
    with bot_core._ai_results_lock:
        bot_core._ai_results.pop(tid, None)


def test_gives_up_without_progress():
    print("\n[1] 毫无进展 → 按 timeout 放弃，并标记为超时")
    tid = 90001
    bot_core._mark_ai_timeout(False)
    _register(tid)
    try:
        t0 = time.time()
        out = bot_core._wait_ai_result(tid, timeout=0.4, idle_timeout=0.4, max_total=3.0)
        dt = time.time() - t0
        check("返回 None", out is None, out)
        check("★ 耗时接近 timeout，没有拖到 max_total（%.2fs）" % dt, dt < 1.6, f"{dt:.2f}s")
        check("★ 置了'超时'标记", bot_core._ai_timed_out() is True)
    finally:
        _drop(tid)


def test_keeps_waiting_while_progressing():
    print("\n[2] 一直有进展 → 超过 timeout 仍继续等")
    tid = 90002
    bot_core._mark_ai_timeout(False)
    _register(tid)
    stop = threading.Event()

    def beat():
        # 每 0.2s 打一次 progress，持续 2 秒
        end = time.time() + 2.0
        while time.time() < end and not stop.is_set():
            with bot_core._ai_results_lock:
                e = bot_core._ai_results.get(tid)
                if e is not None:
                    e["progress"] = time.time()
            time.sleep(0.2)

    th = threading.Thread(target=beat, daemon=True)
    th.start()
    try:
        t0 = time.time()
        out = bot_core._wait_ai_result(tid, timeout=0.4, idle_timeout=0.6, max_total=1.6)
        dt = time.time() - t0
        check("返回 None（始终没结果）", out is None, out)
        check("★ 远超 timeout=0.4s，说明被进展续期了（%.2fs）" % dt, dt > 0.9, f"{dt:.2f}s")
        check("★ 但没有超过 max_total=1.6s（%.2fs）" % dt, dt < 2.6, f"{dt:.2f}s")
    finally:
        stop.set()
        _drop(tid)


def test_result_clears_flag():
    print("\n[3] 正常拿到结果 → 不置超时标记（那是'模型决定不回复'）")
    tid = 90003
    bot_core._mark_ai_timeout(True)      # 先弄脏，确认会被清掉
    ev = _register(tid)

    def finish():
        time.sleep(0.15)
        with bot_core._ai_results_lock:
            e = bot_core._ai_results.get(tid)
            if e is not None:
                e["result"] = "好的"
        ev.set()

    threading.Thread(target=finish, daemon=True).start()
    try:
        out = bot_core._wait_ai_result(tid, timeout=2.0, idle_timeout=2.0, max_total=4.0)
        check("拿到结果", out == "好的", out)
        check("★ 超时标记已清除", bot_core._ai_timed_out() is False)
    finally:
        _drop(tid)


def test_missing_entry():
    print("\n[4] 任务条目不存在 → 直接返回，不算超时")
    bot_core._mark_ai_timeout(True)
    out = bot_core._wait_ai_result(999999, timeout=1.0)
    check("返回 None", out is None, out)
    check("★ 不算超时", bot_core._ai_timed_out() is False)


def main():
    test_gives_up_without_progress()
    test_keeps_waiting_while_progressing()
    test_result_clears_flag()
    test_missing_entry()

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
