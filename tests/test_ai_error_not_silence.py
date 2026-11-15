# -*- coding: utf-8 -*-
"""AI 异常 ≠ 模型沉默（2026-10-28，1.5.9 发布前阻塞项）

## 线上事故

群里连着三条被 @ 提问，全部没有回复：

```
[16:46:27] 工具调用: ['search_web', 'search_web']
[16:46:27] AI 不重试（NameError 不可重试（非传输层错误））: name 'merged_text' is not defined
[16:46:27] AI 失败 (已重试0次): name 'merged_text' is not defined
[16:46:27] 群[123456789] AI决定不回复: (空)        ← 假象
[16:48:04] 群[123456789] AI决定不回复: (空)
[16:49:22] 群[123456789] AI决定不回复: (空)
```

最后一行是**假的**。真实原因是代码抛了 NameError（`_call_deepseek_sync` 里
引用了一个不存在的 `merged_text`），worker 捕获后把 result 置 None 并
`event.set()`，等待方看到的是"正常取回一个 None"，于是清掉超时标记，
上层打印「AI决定不回复」。

**它不是模型决定沉默，是代码崩了。** 但日志让这两件事长得一模一样，
排查方向直接跑偏到决策器/发言闸/频控上去 —— 真机就是这么绕了两小时。

## 本测试锁两件事

1. `_ai_worker` 的异常分支必须把异常信息放进 entry，`_wait_ai_result`
   必须把它带出来（`_ai_error()` 非空）；
2. 上层日志必须**三分**：崩溃 / 超时 / 沉默，不能再用同一句话。

运行：python tests/test_ai_error_not_silence.py
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
    ev = threading.Event()
    with bot_core._ai_results_lock:
        bot_core._ai_results[tid] = {"event": ev, "result": None,
                                     "progress": time.time()}
    return ev


def _drop(tid):
    with bot_core._ai_results_lock:
        bot_core._ai_results.pop(tid, None)


def test_error_is_propagated():
    """worker 抛异常 → 等待方必须拿到错误字符串（不是"沉默"）。"""
    print("\n[1] 异常必须传到等待方，不能变成'模型决定不回复'")
    tid = 91001
    bot_core._mark_ai_timeout(False)
    bot_core._mark_ai_error("")
    ev = _register(tid)

    def _boom():
        # 复刻 worker 的异常分支（与 _ai_worker 里那段保持一致）
        try:
            raise NameError("name 'merged_text' is not defined")
        except Exception as e:
            with bot_core._ai_results_lock:
                entry = bot_core._ai_results.get(tid)
                if entry:
                    entry["result"] = None
                    entry["error"] = f"{type(e).__name__}: {e}"
            ev.set()

    threading.Thread(target=_boom, daemon=True).start()
    try:
        out = bot_core._wait_ai_result(tid, timeout=2.0)
        check("返回值仍是 None（行为不变）", out is None, out)
        err = bot_core._ai_error()
        check("★ 错误被带出来了", bool(err), repr(err))
        check("★ 错误里含真实原因", "merged_text" in err, repr(err))
        check("★ 没有被误标成超时", bot_core._ai_timed_out() is False)
    finally:
        _drop(tid)


def test_normal_none_has_no_error():
    """模型真的返回 None/空（没有异常）→ 不许有错误串，应走'沉默'分支。"""
    print("\n[2] 正常取回 None 时不得有错误串")
    tid = 91002
    bot_core._mark_ai_timeout(False)
    bot_core._mark_ai_error("")
    ev = _register(tid)
    with bot_core._ai_results_lock:
        bot_core._ai_results[tid]["result"] = None
    ev.set()
    try:
        out = bot_core._wait_ai_result(tid, timeout=2.0)
        check("返回 None", out is None, out)
        check("★ 没有错误串", bot_core._ai_error() == "", repr(bot_core._ai_error()))
        check("★ 没有超时标记", bot_core._ai_timed_out() is False)
    finally:
        _drop(tid)


def test_timeout_clears_stale_error():
    """上一轮的异常不该污染下一轮的超时判定。"""
    print("\n[3] 上一轮的异常清了之后，超时仍能正确标记")
    tid = 91003
    bot_core._mark_ai_error("NameError: boom")
    bot_core._mark_ai_timeout(False)
    _register(tid)
    try:
        out = bot_core._wait_ai_result(tid, timeout=0.3, idle_timeout=0.3,
                                       max_total=1.0)
        check("超时返回 None", out is None, out)
        check("★ 置了超时标记", bot_core._ai_timed_out() is True)
    finally:
        _drop(tid)


def test_source_log_three_way_branch():
    """日志分支必须是三分（崩溃 / 超时 / 沉默），不是两分。"""
    print("\n[4] 源码里「崩溃」分支存在且排在超时之前")
    import io
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    i_err = src.find("[崩溃]")
    i_to = src.find("[超时放弃]")
    check("存在 [崩溃] 分支", i_err > 0, i_err)
    check("存在 [超时放弃] 分支", i_to > 0, i_to)
    # 崩溃必须先判：异常时 timed_out 可能是 False，若先判超时就会漏掉
    check("★ [崩溃] 判定在 [超时放弃] 之前", 0 < i_err < i_to, f"{i_err} vs {i_to}")
    check("[崩溃] 文案点明'不是沉默也不是超时'",
          "不是模型决定沉默" in src[i_err - 200:i_err + 200])


def main():
    test_error_is_propagated()
    test_normal_none_has_no_error()
    test_timeout_clears_stale_error()
    test_source_log_three_way_branch()
    print("\n" + "=" * 56)
    if _fail:
        print(f"FAILED ({len(_fail)}): " + ", ".join(_fail))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()