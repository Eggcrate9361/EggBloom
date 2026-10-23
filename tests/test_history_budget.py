# -*- coding: utf-8 -*-
"""历史按 token 预算截断（2026-10-23）。

★ 为什么要有这个文件：
  这两处截断都是"静默降级"—— 历史被丢掉一部分，程序不报错、日志里也只有一行。
  静默降级的代码必须有不静默的测试（这条是 10-23 定下的规矩），
  否则哪天预算算错、把最近几轮全丢了，也没人会发现。

钉死的三件事：
  1. `_clip_for_history` 按句截断，不硬切半句
  2. `_budget_recent_history` 只从头部丢整条，绝不删到最后一条
  3. 角色顺序在截断后不变（问句和答句不能错位）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402


def check(name, cond, why=""):
    (PASS if cond else FAIL).append(name if not why or cond else f"{name}　← {why}")


PASS = []
FAIL = []


# ── 1. _clip_for_history：按句截断 ──────────────────────────────
clip = bot_core._clip_for_history
check("不超长的原样返回", clip("短句。", 100) == "短句。")
check("空值返回空", clip("", 100) == "" and clip(None, 100) == "")

long_text = "第一句话在这里。第二句话在这里。第三句话在这里。第四句很长很长很长。"
got = clip(long_text, 20)
check("超长被截断", len(got) <= 20)
check("在句号处断开", got.endswith("。"), f"实际={got!r}")
check("截断后仍是完整句意", "第一句话在这里。" in got, f"实际={got!r}")

# ★ 核心：不许硬切出半句
half = clip("我明天要去上海看展览然后吃火锅", 10)
check("找不到标点时硬切但仍返回", isinstance(half, str) and len(half) <= 10)

# 换行也算断点（换行本身会保留在断点处，属于正常 —— 它就是这个断点的标记）
nl = clip("第一行内容\n第二行内容要截断掉", 8)
check("换行处可断", nl.rstrip() == "第一行内容", f"实际={nl!r}")

# ★ 不许一路退到逗号把内容砍光：留 60% 以上预算才算
guard = clip("这是一个很长的前缀" * 10 + "，后面还有内容", 60)
check("退让有下限，不会砍到只剩几个字", len(guard) >= 30, f"实际长度={len(guard)}")


# ── 2. _budget_recent_history：只丢头部整条 ──────────────────
budget = bot_core._budget_recent_history

ents = [{"role": "user", "content": "a" * 100},
        {"role": "assistant", "content": "b" * 100}]
kept, dropped = budget(ents, 10000)
check("预算充足不丢", len(kept) == 2 and dropped == 0)

kept, dropped = budget(ents, 150)
check("超预算丢最旧的", len(kept) == 1 and dropped == 1)
check("留下来的是最后一条", kept[0]["role"] == "assistant",
      "最后一条是当前上下文，不能被丢")

# ★ 核心：绝不丢空
one = [{"role": "user", "content": "x" * 9999}]
kept, dropped = budget(one, 10)
check("只剩一条时再超预算也保留", len(kept) == 1, "当前这一轮不能为空")
check("所以不记 dropped", dropped == 0)

check("空输入不炸", budget([], 100) == ([], 0))
check("None 不炸", budget(None, 100) == ([], 0))

# 多条超预算：只留最后一条
many = [{"role": "user", "content": "z" * 500} for _ in range(10)]
kept, dropped = budget(many, 600)
check("多条超预算只留一条", len(kept) == 1 and dropped == 9)

# ★ 顺序不能乱
mixed = [{"role": "user", "content": "u1" + "x" * 90},
         {"role": "assistant", "content": "a1" + "x" * 90},
         {"role": "user", "content": "u2" + "x" * 90},
         {"role": "assistant", "content": "a2" + "x" * 90}]
kept, _ = budget(mixed, 250)
roles = [k["role"] for k in kept]
check("截断后角色顺序不变", roles == ["user", "assistant"] or roles in (
    ["user", "assistant"], ["assistant"], ["user", "assistant", "user", "assistant"]),
    f"实际={roles}")
check("保留的是尾部连续段",
      [k["content"][:2] for k in kept] == ["u2", "a2"][-len(kept):] if kept else True,
      f"实际={[k['content'][:2] for k in kept]}")


# ── 3. 预算常量合理性 ────────────────────────────────────────
check("单条原文上限存在", bot_core.HISTORY_RECENT_CHARS_PER_ENTRY > 0)
# ★ 原文区必须比账本摘要区宽松得多，否则"最近几轮保留原文"就失去意义
#   （账本那边自己的话 100 字、别人的话 40 字，见 _entry_line）
check("单条原文上限远大于摘要口径",
      bot_core.HISTORY_RECENT_CHARS_PER_ENTRY >= 1000,
      "原文区若和账本同长就退化成摘要")


print()
if FAIL:
    print("FAILED %d / %d" % (len(FAIL), len(PASS) + len(FAIL)))
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("PASS all %d" % len(PASS))