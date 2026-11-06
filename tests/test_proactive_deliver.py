# -*- coding: utf-8 -*-
"""tests/test_proactive_deliver.py —— 主动开口的日志顺序（2026-11-06）

## 事故

真机 08:02：

```
08:02:36 [发送] 私聊 → Texas(2011257385): retcode=0
08:02:40 [发送] 私聊 → Texas(2011257385): retcode=0
08:02:43 [Plugin:proactive_talk] 主动开口 → 2011257385: 醒得挺早啊你 / 我这刚处理完一堆破事
```

日志比发送晚 7 秒 —— 因为气泡之间有 0.8–4 秒打字延时，而 `主动开口 →` 那行
写在 `_send_bubbles()` **之后**。排查时看着像"先发出去、后记日志"，甚至被
误读成"这两条不是它发的"（后续 bot 否认自己说过话时，这个时序更致命）。

## 现在的约定

`_deliver()` 是唯一的发送入口：

  ① **先**打「主动开口 → 谁: 发什么」—— 决定时刻
  ② 再发（中间是真实打字延时）
  ③ 只有**部分失败/全失败**时才补一行「主动开口结果 → x/y 条成功」

判据：
  [A] 决定日志在发送**之前**
  [B] 全部成功时不刷结果行（少一行噪音）
  [C] 部分失败时结果行带真实比例
  [D] 全失败返回 0，且结果行 0/N
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import plugins.proactive_talk as pt  # noqa: E402

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
    events = []
    orig_log, orig_send = pt._log, pt._send_bubbles
    try:
        pt._log = lambda msg, level="info": events.append(("log", str(msg)))
        pt._send_bubbles = lambda qq, bubbles: (
            events.append(("send", list(bubbles))), len(bubbles))[1]

        print("=== A) 决定日志在发送之前 ===")
        events.clear()
        n = pt._deliver("2011257385", ["醒得挺早啊你", "我这刚处理完一堆破事"])
        check("返回值=2", n == 2, n)
        check("第一条是日志", events and events[0][0] == "log", events)
        check("日志里写的是『主动开口 → 谁』",
              "主动开口" in events[0][1] and "2011257385" in events[0][1], events)
        check("第二条才是发送", len(events) > 1 and events[1][0] == "send", events)
        check("日志含要发的内容", "醒得挺早" in events[0][1], events)

        print("\n=== B) 全部成功不刷结果行 ===")
        check("只有两条事件（log + send）", len(events) == 2, events)

        print("\n=== C) 部分失败要带比例 ===")
        events.clear()
        pt._send_bubbles = lambda qq, bubbles: (
            events.append(("send", list(bubbles))), 1)[1]
        n = pt._deliver("2011257385", ["一", "二"])
        check("返回值=1", n == 1, n)
        tail = [e for e in events if e[0] == "log"][-1][1]
        check("结果行 1/2", "1/2" in tail, tail)
        check("结果行在发送之后", events[-1][0] == "log", events)

        print("\n=== D) 全失败 ===")
        events.clear()
        pt._send_bubbles = lambda qq, bubbles: 0
        n = pt._deliver("2011257385", ["一", "二"])
        check("返回值=0（调用方据此报错）", n == 0, n)
        tail = [e for e in events if e[0] == "log"][-1][1]
        check("结果行 0/2", "0/2" in tail, tail)
    finally:
        pt._log, pt._send_bubbles = orig_log, orig_send

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
