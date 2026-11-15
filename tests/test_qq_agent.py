# -*- coding: utf-8 -*-
"""harness/qq_agent 单测（方案 v2 §2/§6）。

三类东西必须钉住：
  1. **意图闸**：闲聊不能被拖进 agent（中位 21 秒是底线），查类必须进。
  2. **真跑一遍循环**：用假 chat_fn 走完"模型调 vision → 原语执行 → 模型收尾"，
     证明 tool_functions / tool_schemas / 预算 / trace 是通的。
  3. **失败收敛**：超时/中止不许挂空，要给一句能和第三类断言对齐的话。

运行：python tests/test_qq_agent.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import qq_agent as qa  # noqa: E402
from harness.qq_primitives import PrimitiveSet  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


# ── 1. 意图闸 ────────────────────────────────────────────────────
YES = [
    "你知道我是谁吗",                    # 手册菜谱 1
    "你列表里有没有高浓度神经矿区这个群",   # 菜谱 2
    "你能不能看看这张图是什么",
    "你刚刚有没有收藏一个表情包",
    "帮我查一下上次聊到哪",
    "你还记得我叫什么吗",
    "你能听歌吗",
]
NO = ["哈哈", "在吗", "晚安", "行吧", "今天好累", "嗯", "算了", "1"]
for t in YES:
    check(f"该进 agent：{t}", qa.should_use_agent(t) is True, t)
for t in NO:
    check(f"不该进 agent：{t}", qa.should_use_agent(t) is False, t)
check("太短不进", qa.should_use_agent("看") is False)
check("带图 + 有一定长度就进", qa.should_use_agent("送你一个表情包", has_media=True) is True)
check("带图但只有两个字不进", qa.should_use_agent("哈", has_media=True) is False)

# ── 2. 占位与手册 ────────────────────────────────────────────────
p1, p2 = qa.placeholder_for("你知道我是谁吗"), qa.placeholder_for("你知道我是谁吗")
check("占位是确定性的", p1 == p2, p1)
check("占位在名单里", p1 in qa.PLACEHOLDERS, p1)
check("占位 3–6 字", all(3 <= len(x) <= 6 for x in qa.PLACEHOLDERS),
      [len(x) for x in qa.PLACEHOLDERS])

man = qa.load_manual(ROOT)
check("手册读得到", len(man) > 500, len(man))
check("手册替换了 %APP_ROOT%", "%APP_ROOT%" not in man)
check("手册带菜谱", "菜谱" in man and "看某人头像" in man)
sp = qa.build_system_prompt("我是小a", ROOT)
check("system prompt = 人格 + 手册", sp.startswith("我是小a") and "操作手册" in sp)
check("读不到手册也不崩", qa.build_system_prompt("我是小a", os.path.join(ROOT, "nope")) == "我是小a")

# ── 3. 真跑一遍 agent 循环（假 chat_fn）──────────────────────────
calls = []


def fake_vision(path, question):
    calls.append((path, question))
    return {"ok": True, "text": "银发蓝眼，泳装，背景是海"}


ps = PrimitiveSet(sandbox_root=ROOT, vision=fake_vision)


def chat_fn(messages, tools):
    """第一轮要 vision，第二轮给结论 —— 与真模型同形。"""
    used = any(m.get("role") == "tool" for m in messages)
    if not used:
        return ("", [{"id": "c1", "type": "function",
                      "function": {"name": "vision",
                                   "arguments": '{"path": "docs/qq_agent_manual.md",'
                                                ' "question": "画风"}'}}], {})
    return ("看到了，银发蓝眼那个", [], {})


agent = qa.QQAgent(primitives=ps, chat_fn=chat_fn, root=ROOT)
r = agent.run("你看看这张图什么画风", session_id="t1", system_prompt="我是小a")
check("跑通并拿到答案", r["ok"] is True and "银发蓝眼" in r["text"], r)
check("trace 里记到了 vision", "vision" in r["tools"], r["tools"])
check("原语真的被调了", len(calls) == 1, calls)
check("步数在预算内（私聊 ≤3）", 0 < r["steps"] <= qa.MAX_STEPS_PRIVATE, r)
check("tool_results 带 ok 标记（第三类断言要吃它）",
      r["tool_results"] == [["vision", True]], r["tool_results"])

# 工具定义形状
schemas = agent.tool_schemas()
check("工具定义 9 个", len(schemas) == 9, len(schemas))
check("工具定义是 function 形状",
      all(s["type"] == "function" and s["function"]["name"] for s in schemas))
check("tool_functions 覆盖全部原语",
      set(agent.tool_functions()) == {s["name"] for s in ps.specs()})

# ── 4. 失败收敛 ─────────────────────────────────────────────────
class _Res:
    def __init__(self, **kw):
        self.ok, self.answer, self.steps = kw.get("ok", False), kw.get("answer", ""), kw.get("steps", 0)
        self.trace, self.error = kw.get("trace", []), kw.get("error")
        self.aborted, self.usage_total = kw.get("aborted", False), {}


a_abort = qa.QQAgent(primitives=ps, chat_fn=chat_fn, root=ROOT,
                     runner=lambda **kw: _Res(ok=False, aborted=True, steps=2))
r = a_abort.run("查一下那个群", is_group=True)
check("中止/超时 → 收敛成'没查到'", r["ok"] is False and "没查到" in r["text"], r)

a_empty = qa.QQAgent(primitives=ps, chat_fn=chat_fn, root=ROOT,
                     runner=lambda **kw: _Res(ok=False, answer="", steps=1))
r = a_empty.run("查一下那个群")
check("空答案不硬编", r["text"] == "" and r["ok"] is False, r)


def _boom(**kw):
    raise RuntimeError("模型挂了")


a_boom = qa.QQAgent(primitives=ps, chat_fn=chat_fn, root=ROOT, runner=_boom)
r = a_boom.run("查一下那个群")
check("runner 抛异常 → 不炸，带 error", r["ok"] is False and "模型挂了" in r["error"], r)

# ── 5. 群聊步数更紧 ─────────────────────────────────────────────
seen = {}


def _cap_steps(**kw):
    seen.update(kw)
    return _Res(ok=True, answer="行", steps=1)


a_cap = qa.QQAgent(primitives=ps, chat_fn=chat_fn, root=ROOT, runner=_cap_steps)
a_cap.run("群里问一句", is_group=True)
check("群聊按 2 步下发", seen.get("max_steps") == qa.MAX_STEPS_GROUP, seen.get("max_steps"))
a_cap.run("私聊问一句", is_group=False)
check("私聊按 3 步下发", seen.get("max_steps") == qa.MAX_STEPS_PRIVATE, seen.get("max_steps"))
check("QQ 主链不写第二份 session_log", seen.get("persist") is False)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
