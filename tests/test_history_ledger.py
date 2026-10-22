# -*- coding: utf-8 -*-
"""tests/test_history_ledger.py —— 历史摘要账本：前缀必须逐字节稳定

成本事故（2026-10-13 18-19 点，token_stats 实测）：40 次调用的缓存命中额
钉死在 ~3200 —— 恰好只有 static_prompt 命中，摘要+近4轮每次全 miss。
根因：旧摘要建立在滑动窗口上（history[-10:] 去最近4轮），每轮头部掉一条
→ 整段重建 → 文本变 → DeepSeek 前缀缓存断在 static 之后。

账本模式：每条历史只入账一次、行永不清除/重排，窗口滑动=尾部追加。
钉死：
  ① 连续多轮对话中，摘要文本只增长、**前缀逐字节不变**；
  ② 断代（历史整本换掉）→ 账本重建，不重复入账；
  ③ 最近 RECENT_TURNS 轮仍以原文出现（不进摘要，行为不变）；
  ④ static_prompt 永远第一段（结构不变）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

# 停掉 bot_core 带起来的守护线程：否则测试全过、退出码却是 0xC0000409
# （解释器关闭时 daemon 线程还在写 stdout，抢锁失败 → 拖垮退出码）。
# 详见 tests/_quiet_exit.py 开头的说明。
sys.path.insert(0, HERE)
from _quiet_exit import quiet_exit  # noqa: E402
quiet_exit()

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
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


def _summary_of(messages):
    """找摘要块（可能不存在：历史≤RECENT_TURNS 时无摘要）。"""
    for m in messages:
        if m.get("role") == "system" and str(m.get("content", "")).startswith("【之前对话摘要】"):
            return m["content"]
    return None


def main():
    key = "g_ledger_test"
    for d in (bot_core._HISTORY_SUMMARY_LINES, bot_core._HISTORY_LAST_WINDOW,
              bot_core._HISTORY_SUMMARY_TEXT):
        d.pop(key, None)

    print("\n=== ① 滑动窗口连续 12 轮：摘要前缀逐字节稳定 ===")
    history = []
    prev_summary = None
    grew_at_least_once = False
    for i in range(12):
        history.append({"role": "user", "content": f"[小明({i})] 第{i}个问题内容"})
        history.append({"role": "assistant", "content": f"第{i}个回答内容"})
        # 模拟真实存储：只带最近 16 条（gh[-16:]）
        window = history[-16:]
        msgs = bot_core._build_cached_messages(window, key, "STATIC", [], "当前消息")
        check(f"轮{i}：static 恒为第一段", msgs[0]["content"] == "STATIC")
        s = _summary_of(msgs)
        if s is None:
            check(f"轮{i}：历史≤4轮 → 无摘要块（正常）", True)
        elif prev_summary:
            if s != prev_summary:
                grew_at_least_once = True
                check(f"轮{i}：旧前缀逐字节保留（只增长）",
                      s.startswith(prev_summary),
                      f"\n prev={prev_summary[:60]!r}\n cur ={s[:60]!r}")
        if s is not None:
            prev_summary = s
    check("★ 12 轮里摘要确实增长过（账本在收条目）", grew_at_least_once)

    print("\n=== ② 断代：历史整本换掉 → 重建且不重复入账 ===")
    fresh = [{"role": "user", "content": f"[路人{i}] 全新话题{i}"} for i in range(12)]
    msgs2 = bot_core._build_cached_messages(fresh, key, "STATIC", [], "新消息")
    s2 = _summary_of(msgs2)
    ledger2 = bot_core._HISTORY_SUMMARY_LINES.get(key, [])
    check("断代后账本行数 = 本次 old 条数（没有把旧账叠加进来）",
          len(ledger2) == 4, str(len(ledger2)))
    check("断代后摘要含新内容", "全新话题" in s2, s2[:80])

    print("\n=== ③ 最近 4 轮（8 条）保持原文（不进摘要）===")
    last_msgs = msgs2[-6:-1]  # 摘要之后的若干条
    tail_user = msgs2[-1]["content"]
    check("当前消息在最后", tail_user == "新消息", repr(tail_user))
    recent_contents = [m.get("content") for m in msgs2 if m.get("role") != "system"]
    check("窗口最后 8 条以原文出现（4 轮）",
          any("全新话题11" in str(c) for c in recent_contents)
          and any("全新话题4" in str(c) for c in recent_contents),
          str(recent_contents)[:120])

    print("\n=== ④ 短历史（≤4轮）不产生摘要块 ===")
    msgs3 = bot_core._build_cached_messages(
        [{"role": "user", "content": "就一句话"}], key + "_short", "STATIC", [], "hi")
    check("无摘要块：static + 原文 + 当前消息",
          len(msgs3) == 3 and _summary_of(msgs3) is None
          and msgs3[-1]["content"] == "hi", str(msgs3)[:120])

    print("\n=== ⑤ 结构顺序：stable_parts 必须在滑动近轮之前（可缓存前缀区）===")
    hist = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"轮{i}"} for i in range(8)]
    msgs4 = bot_core._build_cached_messages(
        hist, key + "_order", "STATIC", ["DYN1"], "cur",
        stable_parts=["STABLE1", "STABLE2"])
    roles = [(m["role"], str(m.get("content"))[:8]) for m in msgs4]
    idx_stable = next(i for i, m in enumerate(msgs4) if m.get("content") == "STABLE1")
    idx_dyn = next(i for i, m in enumerate(msgs4) if m.get("content") == "DYN1")
    idx_recent = next(i for i, m in enumerate(msgs4) if m.get("content") == "轮4")
    check("顺序：static(0) < 摘要 < stable < 近轮 < dynamic < 当前",
          0 < idx_stable < idx_recent < idx_dyn, str(roles)[:160])
    check("stable 两块都在且保序",
          [m.get("content") for m in msgs4].count("STABLE1") == 1
          and next(i for i, m in enumerate(msgs4) if m.get("content") == "STABLE1")
          < next(i for i, m in enumerate(msgs4) if m.get("content") == "STABLE2"))
    check("当前消息在最后", msgs4[-1]["content"] == "cur")

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
