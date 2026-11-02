# -*- coding: utf-8 -*-
"""子 agent 的空结论（2026-11-02 真机：deep_task 跑了 35 秒回空）。

## 事故

`deep_task` 被调用后跑了 35 秒，session_log 最后一行为：

```
{"type": "thinking", "content": "重要发现：…"}      ← 有思考
{"type": "final", "content": "", "steps": 10}       ← 没正文
```

`agent_loop` 在"这一轮没有工具调用"时直接 `ok=True, answer=""` 收尾 ——
调用方只能报「多步任务跑完了但没有结论」。根因：推理模型的思维链也吃
completion 预算（1500），一份代码审查的结论被思考吃光，正文就是空串。

## 契约（改后）

1. 没正文 → 先补一轮"只要结论"（收敛轮，`max_tokens=2500`）；
2. 还没有 → 用思维链尾部当「草稿结论」交出去（标明不完整，总比空好）；
3. 仍然没有 → `ok=False` + `模型没有给出结论（只有思考过程）`，不再假装成功；
4. `deep_task` 把这种失败翻成"你自己接着做，别重复派"，让主模型不至于卡住。

运行：python tests/test_agent_empty_answer.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import agent_loop as AL  # noqa: E402
from harness import tool_deep as td  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


def run(chat_fn, user_input="看看插件有没有问题"):
    return AL.run_agent(session_id="t-empty", user_input=user_input,
                        chat_fn=chat_fn, tool_functions={}, system_prompt="你是测试",
                        max_steps=6, persist=False, thinking_events=False)


# ── 1. 只有思考、没有正文 → 收敛轮拿到结论 ──────────────────────────
calls = {"n": 0}


def chat_recover(messages, tools=None, **kw):
    calls["n"] += 1
    if calls["n"] == 1:
        return {"content": "", "tool_calls": [], "usage": {},
                "reasoning": "我在想这份代码有没有问题…"}
    return {"content": "看完了，有两处不对：X 和 Y", "tool_calls": [], "usage": {}}


res = run(chat_recover)
check("★ 第一轮空正文后补收敛轮", calls["n"] >= 2, calls["n"])
check("★ 收敛轮拿到结论就算成功", res.ok is True and "两处" in res.answer, res.answer)

# ── 2. 连思考都只有、收敛轮也要不到 → 用思维链草稿兜底 ─────────────
def chat_thinking_only(messages, tools=None, **kw):
    return {"content": "", "tool_calls": [], "usage": {},
            "reasoning": "L2 取证那段整批塞一个 try 里，预算超了直接抛"}


res2 = run(chat_thinking_only)
check("★ 无正文时用思维链草稿兜底", res2.ok is True and "L2 取证" in res2.answer,
      res2.answer[:40])
check("★ 草稿标明不完整", "可能不完整" in res2.answer, res2.answer[:30])

# ── 3. 彻底什么都没有 → 如实失败，不假装成功 ────────────────────────
def chat_nothing(messages, tools=None, **kw):
    return {"content": "   ", "tool_calls": [], "usage": {}}


res3 = run(chat_nothing)
check("★ 什么都没有 → ok=False", res3.ok is False, res3.ok)
check("★ 错误信息写清「没有给出结论」", "没有给出结论" in (res3.error or ""), res3.error)

# ── 4. 正常有正文 → 不多打一次收敛轮 ────────────────────────────────
calls2 = {"n": 0}


def chat_normal(messages, tools=None, **kw):
    calls2["n"] += 1
    return {"content": "一切正常", "tool_calls": [], "usage": {}}


res4 = run(chat_normal)
check("正常回复只调一次", calls2["n"] == 1, calls2["n"])
check("正常回复不被兜底污染", res4.answer == "一切正常", res4.answer)

# ── 5. deep_task 对"没结论"的措辞要对模型有用 ───────────────────────
r = td.deep_task("审查插件", _runner=lambda **k: {"ok": False,
                                                 "error": "模型没有给出结论（只有思考过程）"})
check("★ deep_task 告诉模型自己接着做", "接着做" in str(r.get("error")),
      r.get("error"))
r2 = td.deep_task("审查插件", _runner=lambda **k: {"ok": True, "answer": "  "})
check("★ 空结论同样翻成人话", "接着做" in str(r2.get("error")), r2.get("error"))

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
