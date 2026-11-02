# -*- coding: utf-8 -*-
"""deep_task 长任务要持续打进展（2026-11-02 真机：93 秒被判超时）。

## 事故

```
09:34:35 工具调用: ['deep_task']
09:36:00 AI 任务 1 等待超时（已等 93s，最后一次进展在 88s 前；本批已放弃）
09:36:01   → deep_task → {"ok": true, "answer": "审查完了，代码全读过了…"}
```

子 agent **干完了**，答案也回来了 —— 但主链的等待判据是"多久没进展"
（`_wait_ai_result`），而 deep_task 期间一个进展都没打，于是 45 秒 idle 判死。
用户收到的是超时提示，那份 85 秒的审查没人要。

## 契约

1. `tool_deep.set_progress(fn)`：bot_core 每轮把 `_tick_progress` 注入；
2. `deep_task` 开始/结束各打一次（force），中间靠 `run_chat(on_event=…)` 每一步打一次；
3. 打点**节流 3 秒**，避免把 event 洪峰变成 progress 洪峰；
4. 私聊 hard 上限 150 → 240 秒（idle 仍是 45 秒，真卡死照旧放弃）。

运行：python tests/test_deep_task_progress.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import tool_deep as td  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


# ── 1. 注入 ticker 后，调用期间会打点 ────────────────────────────────
ticks = []
td.set_progress(lambda: ticks.append(time.time()))


def slow_runner(**kw):
    """模拟子 agent：跑 1 秒，并像 run_chat 那样发事件。"""
    cb = kw.get("on_event")
    for _ in range(4):
        time.sleep(0.25)
        if cb:
            cb("step", {"n": 1})
    return {"ok": True, "answer": "干完了", "steps": 4}


r = td.deep_task("审查插件", _runner=slow_runner)
check("任务成功", r.get("ok") is True, r.get("error"))
check("★ 期间有打点（否则会被当卡死）", len(ticks) >= 2, len(ticks))
check("打点带 story：开始一次 + 结束一次",
      len(ticks) >= 2, ticks)

# ── 2. 节流：短时间内的密集事件不会刷爆 ─────────────────────────────
before = len(ticks)


def spam_runner(**kw):
    cb = kw.get("on_event")
    for _ in range(50):
        if cb:
            cb("tool", {})
    return {"ok": True, "answer": "ok", "steps": 1}


td.deep_task("刷屏测试", _runner=spam_runner)
delta = len(ticks) - before
check("★ 50 个事件只打 2~3 次点（节流 3 秒）", 2 <= delta <= 3, delta)

# ── 3. 没注入 ticker 时不炸 ─────────────────────────────────────────
td.set_progress(None)
try:
    r2 = td.deep_task("没 ticker", _runner=lambda **k: {"ok": True, "answer": "ok"})
    check("没有 ticker 也能正常跑", r2.get("ok") is True, r2)
except Exception as e:
    check("没有 ticker 也能正常跑", False, f"{type(e).__name__}: {e}")

# ── 4. ticker 自己抛异常不能影响任务 ────────────────────────────────
def bad_tick():
    raise RuntimeError("ticker 坏了")


td.set_progress(bad_tick)
try:
    r3 = td.deep_task("坏 ticker", _runner=lambda **k: {"ok": True, "answer": "ok"})
    check("★ ticker 抛异常不影响任务", r3.get("ok") is True, r3.get("error"))
except Exception as e:
    check("★ ticker 抛异常不影响任务", False, f"{type(e).__name__}: {e}")

td.set_progress(None)

# ── 5. 主链确实注入了 ticker、且放宽了私聊硬顶 ──────────────────────
src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
check("主链注入 set_progress(progress)", "_tdeep_ctx.set_progress(progress)" in src)
check("私聊硬顶 150 → 240",
      "max_total=240.0" in src and "max_total=150.0" not in src)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
