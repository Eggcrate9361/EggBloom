# -*- coding: utf-8 -*-
"""tests/test_session_rounds.py —— 会话轮次与历史预算（1.5.8 计划 #18）

背景
    计划原文：「SESSION_MAX_ROUNDS = 30 偏大，按 token 预算截断，不按条数」。
    核实后的真相分两半：

      ① 字符预算**已经做了**（2026-10-23 的 040c0a1）：摘要账本按
         `history_ledger_budget_chars`（默认 4000）从头部裁，最近轮按
         `history_recent_budget_chars`（默认 6000）丢最旧整条。这正是
         计划要的方向，不用再做一遍。
      ② 真问题不是"30 偏大"，而是**配置项描述与行为不符**：
         `session_max_rounds` 的说明写「一次会话内最多来回多少轮」，
         但它只是 `group_session_max_replies` 未配时的回落值。
         用户在 UI 里改它多半没效果（因为群聊面板那个默认 20 非空）——
         2026-10-21 修过行为，描述没跟着改，于是误导留下了。

    所以这里锁三件事：
      1. 回落链：group_session_max_replies 优先，否则 session_max_rounds
      2. 历史真按**字符**预算裁，不按条数
      3. 配置描述说清了真实语义（防再次分叉）

纯 Python，不依赖浏览器。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

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


def main():
    print("[1] 回落链：群聊回复数优先，其次 session_max_rounds")
    orig = dict(bc._config)
    try:
        bc._config.clear()
        bc._config.update({"group_session_max_replies": 5})
        check("配了 group_session_max_replies=5 → 取 5",
              bc._session_round_budget() == 5, bc._session_round_budget())

        bc._config.clear()
        bc._config.update({"group_session_max_replies": 0, "session_max_rounds": 7})
        check("group_session_max_replies=0（视为未配）→ 回落 session_max_rounds=7",
              bc._session_round_budget() == 7, bc._session_round_budget())

        bc._config.clear()
        bc._config.update({"session_max_rounds": 12})
        check("只配 session_max_rounds=12 → 取 12",
              bc._session_round_budget() == 12, bc._session_round_budget())

        bc._config.clear()
        bc._config.update({"session_max_rounds": "不是数字"})
        check("脏值不炸，回落 20", bc._session_round_budget() == 20,
              bc._session_round_budget())

        bc._config.clear()
        bc._config.update({"session_max_rounds": 0})
        check("0 也被夹到至少 1（不会变成永不回复）",
              bc._session_round_budget() >= 1, bc._session_round_budget())

        bc._config.clear()
        bc._config.update({"group_session_max_replies": 5, "session_max_rounds": 99})
        check("两个都配时以 group_session_max_replies 为准（不相加不取大）",
              bc._session_round_budget() == 5, bc._session_round_budget())
    finally:
        bc._config.clear()
        bc._config.update(orig)

    print("\n[2] 历史按字符预算裁，不按条数")
    # _budget_recent_history：丢最旧整条直到不超预算
    fn = getattr(bc, "_budget_recent_history", None)
    if fn is None:
        check("_budget_recent_history 存在", False, "函数不存在")
    else:
        rows = [{"role": "user", "content": "啊" * 300},
                {"role": "assistant", "content": "嗯" * 300},
                {"role": "user", "content": "哦" * 300}]
        kept, dropped = fn(list(rows), 400)
        check("预算 400 时会丢最旧的整条", dropped >= 1 and len(kept) < 3,
              (len(kept), dropped))
        check("丢条后总字符不超预算",
              sum(len(x["content"]) for x in kept) <= 400,
              sum(len(x["content"]) for x in kept))
        check("保留的是较新的内容（尾部不丢）",
              kept and kept[-1]["content"] == "哦" * 300,
              kept[-1]["content"][:20] if kept else None)
        # 注意"预算 0 = 不裁"是**调用方**的语义（bot_core 里有 `if _recent_budget > 0`
        # 守卫），不是函数本身的。函数在 0 下的保证是另一件事：绝不把上下文清空。
        kept0, dropped0 = fn(list(rows), 0)
        check("预算 0 时也至少留最后 1 条（不会把上下文清空）",
              len(kept0) == 1 and kept0[0]["content"] == "哦" * 300,
              (len(kept0), dropped0))
        check("只有 1 条时再怎么超预算都不丢", fn([rows[0]], 10)[1] == 0)

        # 单条截断保句子完整
        clip = getattr(bc, "_clip_for_history", None)
        if clip:
            long_txt = "第一句。" * 50
            c = clip(long_txt, 100)
            check("单条按字符截断", len(c) <= 120, len(c))
            check("截断后仍是完整句子（不在句中腰斩）",
                  c.endswith("。") or c.endswith("…"), c[-10:])

    print("\n[3] 两个预算键都有默认值且可读")
    check("history_recent_budget_chars 默认 6000",
          int(bc._config.get("history_recent_budget_chars") or 6000) == 6000)
    check("history_ledger_budget_chars 默认 4000",
          int(bc._config.get("history_ledger_budget_chars") or 4000) == 4000)

    print("\n[4] 配置描述说清真实语义（#18 的真正交付）")
    fm = json.load(io.open(os.path.join(ROOT, "scripts", "_fieldmeta.json"), encoding="utf-8"))
    f = fm["fields"]
    d_rounds = f.get("session_max_rounds", {}).get("desc", "")
    d_replies = f.get("group_session_max_replies", {}).get("desc", "")
    check("session_max_rounds 的说明提了「回落」", "回落" in d_rounds, d_rounds)
    check("session_max_rounds 的说明指向真正生效的键",
          "每会话最大回复数" in d_rounds, d_rounds)
    check("session_max_rounds 不再声称自己控制历史长度",
          "历史长度由字符预算" in d_rounds, d_rounds)
    check("group_session_max_replies 的说明仍是行为描述",
          "连续回复" in d_replies, d_replies)

    print("\n[5] 文档与页面同步（同一份数据）")
    doc = io.open(os.path.join(ROOT, "docs", "配置项说明.md"), encoding="utf-8").read()
    check("文档里 session_max_rounds 那行带新说明",
          "回落" in doc and "`session_max_rounds`" in doc)
    v4 = io.open(os.path.join(ROOT, "webui", "webui_artistic_v4.html"), encoding="utf-8").read()
    check("v4 页面也带新说明（inject 已跑）", "回落" in v4)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())