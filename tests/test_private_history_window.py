# -*- coding: utf-8 -*-
"""tests/test_private_history_window.py —— 私聊长对话不该被静默截断（2026-11-06）

## 问题

真机一轮普通私聊的链条原来有三道闸：

  ① 存储 `chat_history[sender] = ch[-24:]`          只存 12 轮
  ② 组装 `chat_history[target][-private_history_rounds:]` 只传 16 条（8 轮）
  ③ `_build_cached_messages` 的 `history_recent_budget_chars` 6000 字，
     超了丢最旧的整条（**会打 `[历史预算]` 日志**）

①② 都是**静默**的：第 1~4 轮凭空消失，日志里一个字都没有。
长对话被问回开头，模型只能答「你没说过」。③ 才是真正的成本闸，而且有日志。

实测（改之前，24 条 × 每条约 120 字）：
    存储 24 → 切片 16 → 进 prompt 16；换成 400 字长句时预算再丢 2 条 → 14 条。

## 现在

  · 存储上限 24 → 60（`_PRIVATE_HISTORY_CAP`），单条回复 500 → 1500 字
  · 组装默认 16 → 32 条；切片时**打一行 `[私聊历史]`**，说明丢了什么
  · 真正的成本闸仍是 6000 字，不变；它会打 `[历史预算]`

判据：
  [A] 常量与配置默认值
  [B] 60 条存储 → 只传 32 条，且打了日志（不再静默）
  [C] 短对话 32 条全进 prompt（不再有"8 轮悬崖"）
  [D] 超长对话仍由字符预算兜底，且会被日志记下
  [E] 源码里不再有 `[-24:]` 和 `reply[:500]` 这两处老写法
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402

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


def build(hist, user_msg="现在说什么"):
    """用打桩 HTTP 抓一次真实的 messages（顺手把待测历史塞进全局）。"""
    cap = {}
    orig = bc._post_chat
    saved = dict(bc.chat_history)
    try:
        bc.chat_history.clear()
        bc.chat_history["2011257385"] = hist
        bc._post_chat = lambda url, payload, **k: (
            cap.setdefault("messages", payload.get("messages")),
            {"choices": [{"message": {"content": "（桩）"}}], "usage": {}})[1]
        bc._call_deepseek_sync(user_msg, target_qq="2011257385", sender="2011257385")
    except Exception as e:
        print("  （调用异常不影响抓包）", type(e).__name__)
    finally:
        bc._post_chat = orig
        bc.chat_history.clear()
        bc.chat_history.update(saved)
    msgs = cap.get("messages") or []
    return [m for m in msgs if m.get("role") in ("user", "assistant")]


def main():
    print("=== A) 常量与默认 ===")
    check("存储上限 60", bc._PRIVATE_HISTORY_CAP == 60, bc._PRIVATE_HISTORY_CAP)
    check("单条回复 1500 字", bc._HISTORY_ENTRY_STORE_CHARS == 1500,
          bc._HISTORY_ENTRY_STORE_CHARS)
    check("组装默认 32 条（cfg）", bc._config.get("private_history_rounds") == 32,
          bc._config.get("private_history_rounds"))
    check("字符预算仍是 6000", bc._config.get("history_recent_budget_chars") == 6000,
          bc._config.get("history_recent_budget_chars"))

    print("\n=== B) 60 条存储 → 只传 32 条，且有日志 ===")
    logs = []
    orig_log = bc.log
    orig_hist = dict(bc.chat_history)
    try:
        bc.log = lambda msg: logs.append(str(msg))
        hist = [{"role": "user" if i % 2 == 0 else "assistant",
                 "content": f"第{i//2+1}轮 内容", "ts": 1793920000 + i * 60}
                for i in range(60)]
        bc.chat_history.clear()
        bc.chat_history["2011257385"] = hist
        conv = build(hist)
        # 末尾那条是当前用户消息
        n_hist = len(conv) - 1
        check("进 prompt 的历史 = 32 条", n_hist == 32, n_hist)
        check("打了 [私聊历史] 日志", any("[私聊历史]" in x for x in logs), logs[-3:])
        check("日志说明丢了 28 条", any("28" in x for x in logs if "[私聊历史]" in x),
              [x for x in logs if "[私聊历史]" in x])
    finally:
        bc.log = orig_log
        bc.chat_history.clear()
        bc.chat_history.update(orig_hist)

    print("\n=== C) 短对话不再有「8 轮悬崖」 ===")
    hist2 = [{"role": "user" if i % 2 == 0 else "assistant",
              "content": f"第{i//2+1}轮 短句", "ts": 1793920000 + i * 60}
             for i in range(32)]
    n2 = len(build(hist2)) - 1
    check("32 条全进（>16）", n2 == 32, n2)

    print("\n=== D) 超长对话仍由字符预算兜底 ===")
    logs2 = []
    orig_log = bc.log
    try:
        bc.log = lambda msg: logs2.append(str(msg))
        hist3 = [{"role": "user" if i % 2 == 0 else "assistant",
                  "content": f"第{i//2+1}轮 " + "很" * 400,
                  "ts": 1793920000 + i * 60} for i in range(32)]
        n3 = len(build(hist3)) - 1
        check("预算把条数压下来了", n3 < 32, n3)
        check("打了 [历史预算] 日志", any("[历史预算]" in x for x in logs2),
              logs2[-3:])
    finally:
        bc.log = orig_log

    print("\n=== E) 老写法已消失（源码级，防回退） ===")
    src = open(bc.__file__, encoding="utf-8", errors="replace").read()
    check("没有 [-24:] 硬截", "[-24:]" not in src, "")
    check("没有 reply[:500] 硬截", "reply[:500]" not in src or
          src.count("reply[:500]") == 0, src.count("reply[:500]"))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for n in FAILURES:
        print(f"  FAIL  {n}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
