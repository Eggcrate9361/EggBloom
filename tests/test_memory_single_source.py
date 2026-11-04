# -*- coding: utf-8 -*-
"""tests/test_memory_single_source.py —— 记忆只留一个真相源（2026-11-04）

## 为什么删掉摘要账本

旧实现每轮重发两块历史：
  ① **append-only 摘要账本**：超出最近 N 轮的对话压成 40/100 字的行
  ② 最近 N 轮原文

账本是幻觉的温床 —— 模型把压缩句子当事实，在上面自由补全。真机证据：
  · 「刚说别找你」—— 这句话从没出现过
  · 「没听到你那边的声响」—— 纯文字 bot 声称能听见
  · 「刚还骂我记性短」—— 那句是它自己说的
它们全都是在摘要上二次创作的产物。

现在只有**一个真相源**：原文窗口（最后 history_raw_entries 条）。
更早的事交给长期记忆（沙漏/记忆轮次）—— 那是有明确来源的摘要。

## 判据

① 构建出来的 messages 里**没有**「之前对话摘要」块
② 代码里**没有**账本状态（`_HISTORY_SUMMARY_*`）
③ 原文窗口按 `history_raw_entries` 截，超出部分**丢弃**而不是压成摘要
④ 单条按句截断、整段按字符预算丢最旧 —— 这两道闸仍在
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


def hist(n):
    h = []
    for i in range(n):
        h.append({"role": "user", "content": f"[小明(10001)] 问{i}"})
        h.append({"role": "assistant", "content": f"答{i}"})
    return h


def main():
    key = "g_memsingle"
    old = bc._config.get("history_raw_entries")
    try:
        print("[A] 没有摘要账本了")
        src = open(bc.__file__, encoding="utf-8", errors="replace").read()
        check("★ 代码里没有 _HISTORY_SUMMARY_", "_HISTORY_SUMMARY_" not in src)
        check("★ 没有「之前对话摘要」块", "之前对话摘要" not in src)
        check("★ 没有 _drop_history_ledger", "_drop_history_ledger" not in src)

        msgs = bc._build_cached_messages(hist(20), key, "SYS", [], "现在")
        joined = "\n".join(str(m.get("content") or "") for m in msgs)
        check("★ 构建结果里没有摘要块", "之前对话摘要" not in joined)
        check("★ 没有任何 system 摘要消息",
              not any(m["role"] == "system" and "摘要" in str(m.get("content"))
                      for m in msgs))

        print("\n[B] 原文窗口按条数截断（不是压缩）")
        bc._config["history_raw_entries"] = 4
        msgs = bc._build_cached_messages(hist(20), key, "SYS", [], "现在")
        # 末条是「当前消息」，不算历史窗口
        convo = [m for m in msgs if m["role"] in ("user", "assistant")][:-1]
        check("★ 只保留 4 条原文", len(convo) == 4, len(convo))
        check("★ 保留的是最新的 4 条", "问19" in convo[-2]["content"], convo[-2:])
        check("★ 最旧的被丢弃（没有压成摘要）",
              "问0" not in "\n".join(m["content"] for m in convo))

        print("\n[C] 默认值 40 条")
        bc._config.pop("history_raw_entries", None)
        check("配置默认 40", bc._cfg_int("history_raw_entries", 40, 4, 80) == 40)
        msgs = bc._build_cached_messages(hist(40), key, "SYS", [], "现在")
        convo = [m for m in msgs if m["role"] in ("user", "assistant")][:-1]
        check("★ 40 条全保留（40 <= 40）", len(convo) == 40, len(convo))

        print("\n[D] 两道闸还在")
        bc._config["history_raw_entries"] = 40
        bc._config["history_recent_budget_chars"] = 200
        long_hist = [{"role": "user", "content": "[小明] " + "很长" * 200} for _ in range(10)]
        msgs = bc._build_cached_messages(long_hist, key, "SYS", [], "现在")
        convo = [m for m in msgs if m["role"] in ("user", "assistant")]
        total = sum(len(m["content"]) for m in convo)
        check("★ 字符预算生效", total <= 400 or len(convo) < 10, (total, len(convo)))
        check("单条被截断（不是整条塞满）", all(len(m["content"]) < 1200 for m in convo),
              max(len(m["content"]) for m in convo) if convo else 0)
    finally:
        bc._config.pop("history_recent_budget_chars", None)
        if old is None:
            bc._config.pop("history_raw_entries", None)
        else:
            bc._config["history_raw_entries"] = old

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
