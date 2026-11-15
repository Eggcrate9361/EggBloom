# -*- coding: utf-8 -*-
"""超时之后才回来的结果要补发（2026-11-02 真机）。

## 事故

```
09:34:35 工具调用 deep_task
09:36:00 等待超时（93s）→ 本批放弃
09:36:01 deep_task 返回「审查完了…」→ 没人发送
09:36:06 主链又生成了一条回复（16119 tokens ¥0.011）→ 同样没人发送
```

钱付了、活干完了、答案扔了。`_wait_ai_result` 超时只是"这个等待者不再等"，
后台 worker 仍会把结果写进 entry，但**没有任何人取**。

## 契约

1. `_wait_ai_result(on_late=…)`：超时时挂一个迟到投递的监视线程；
2. 任务完成且拿到**非空文字**（且没有 error）→ 补发一次，带前缀「（刚才那事跑完了）」；
3. **只发一次**：投递时 `pop` entry 并打 `late_delivered`；正常取回的路径不受影响；
4. 没拿到文字/有异常 → 只记日志，绝不乱发；
5. 最多等 300 秒（超过就放弃，entry 本来 5 分钟后也会被清理）；
6. 监视线程自己出任何异常都不能影响主流程。

运行：python tests/test_late_delivery.py
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core as bc  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


def _mk_entry(result=None, error=None, done=False):
    ev = threading.Event()
    if done:
        ev.set()
    with bc._ai_results_lock:
        tid = max(bc._ai_results.keys(), default=0) + 1
        bc._ai_results[tid] = {"event": ev, "result": result, "progress": time.time(),
                               "error": error}
    return tid, ev


# ── 1. 超时后完成 → 补发一次 ────────────────────────────────────────
got = []
tid, ev = _mk_entry(result=None, done=False)
bc._spawn_late_delivery(tid, lambda t: got.append(t), max_wait=10)
with bc._ai_results_lock:
    bc._ai_results[tid]["result"] = "审查完了，有两处不对"
ev.set()
for _ in range(50):
    if got:
        break
    time.sleep(0.1)
check("★ 超时后完成的结果被补发", got == ["审查完了，有两处不对"], got)
check("补发后 entry 被清掉", tid not in bc._ai_results)

# ── 2. 空结果 / 异常结果 → 不乱发 ──────────────────────────────────
got2 = []
tid2, ev2 = _mk_entry(result="   ", done=False)
bc._spawn_late_delivery(tid2, lambda t: got2.append(t), max_wait=5)
ev2.set()
time.sleep(0.6)
check("★ 空结果不补发", got2 == [], got2)

got3 = []
tid3, ev3 = _mk_entry(result="有字", error="boom", done=False)
bc._spawn_late_delivery(tid3, lambda t: got3.append(t), max_wait=5)
ev3.set()
time.sleep(0.6)
check("★ 带异常的完成不补发", got3 == [], got3)

# ── 3. 正常取回路径不受影响（entry 先被 pop，监视器安静退出）────────
#   真实路径是：`_wait_ai_result` 等到事件 → pop entry → 返回结果。
#   这里模拟"已经正常取走"，监视器应当什么都看不到。
got4 = []
tid4, ev4 = _mk_entry(result="正常结果", done=True)
with bc._ai_results_lock:
    bc._ai_results.pop(tid4, None)          # ← 模拟等待方正常取回
bc._spawn_late_delivery(tid4, lambda t: got4.append(t), max_wait=5)
time.sleep(0.6)
check("已被正常取走的任务不会二次补发", got4 == [], got4)

# ── 4. 补发只发一次（两个监视器也不重复）───────────────────────────
got5 = []
tid5, ev5 = _mk_entry(result="一次就够", done=False)
bc._spawn_late_delivery(tid5, lambda t: got5.append(t), max_wait=5)
bc._spawn_late_delivery(tid5, lambda t: got5.append(t), max_wait=5)
ev5.set()
time.sleep(0.8)
check("★ 同一任务只补发一次", len(got5) <= 1, got5)

# ── 5. 投递回调抛异常不炸、不影响其它任务 ──────────────────────────
tid6, ev6 = _mk_entry(result="会抛", done=False)


def _boom(t):
    raise RuntimeError("发送失败")


bc._spawn_late_delivery(tid6, _boom, max_wait=5)
ev6.set()
time.sleep(0.6)
check("回调抛异常不崩（进程还活着）", True)
check("异常后 entry 也被清掉（不会反复重试）", tid6 not in bc._ai_results)

# ── 6. 主链确实把 on_late 传下去了 ─────────────────────────────────
src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
check("★ call_deepseek 两条等待路径都传 on_late", src.count("on_late=_late_deliver") == 2)
check("补发带前缀（免得像凭空插话）", "刚才那事跑完了" in src)
check("_wait_ai_result 签名收 on_late", "on_late=None" in src)
check("超时分支会挂监视线程", "_spawn_late_delivery(task_id, on_late)" in src)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
