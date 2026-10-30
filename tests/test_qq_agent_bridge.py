# -*- coding: utf-8 -*-
"""harness/qq_agent_bridge 单测（A3 接主链的决策层，2026-10-30）。

钉死四件事：
  1. **默认关**：配置里没写就是不开；名单空 = 谁都不许（不是全都允许）。
  2. **名单形状**：私聊号、group:<号>、* 三种写法各管各的，不串。
  3. **真跑一遍**：占位先发 → agent 用原语调一次 fs_read → 拿到答案 →
     tool_results 转写进 note_call（第三类断言吃这份）。
  4. **失败不接管**：模型挂了 / 没注入 chat → text 为空，调用方回落老链路。

运行：python tests/test_qq_agent_bridge.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import qq_agent_bridge as qb  # noqa: E402
from harness import qq_agent as qa  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


# ── 1. 默认关 ────────────────────────────────────────────────────
check("空配置 = 关", qb.enabled({}) is False)
check("enable=False 就是关", qb.enabled({qb.KEY_ENABLE: False}) is False)
check("enable=True 才开", qb.enabled({qb.KEY_ENABLE: True}) is True)
check("默认关时不接管（哪怕问的是查类）",
      qb.should_try({}, "你知道我是谁吗", sender="123") is False)
check("开关开着但名单空 → 不接管",
      qb.should_try({qb.KEY_ENABLE: True}, "你知道我是谁吗", sender="123") is False)

# ── 2. 允许名单 ──────────────────────────────────────────────────
cfg_me = {qb.KEY_ENABLE: True, qb.KEY_ALLOW: ["123456"]}
check("名单里的私聊号 → 允许", qb.allowed(cfg_me, is_group=False, sender="123456") is True)
check("名单里的私聊号 → 不扩到别人",
      qb.allowed(cfg_me, is_group=False, sender="999999") is False)
check("私聊号不会漏进同号群",
      qb.allowed(cfg_me, is_group=True, group_id="123456") is False)
check("private: 前缀等价",
      qb.allowed({qb.KEY_ENABLE: True, qb.KEY_ALLOW: ["private:123456"]},
                 is_group=False, sender="123456") is True)

cfg_g = {qb.KEY_ENABLE: True, qb.KEY_ALLOW: ["group:777"]}
check("group: 只开一个群", qb.allowed(cfg_g, is_group=True, group_id="777") is True)
check("group: 不扩到别的群", qb.allowed(cfg_g, is_group=True, group_id="888") is False)
check("group: 不会漏进私聊", qb.allowed(cfg_g, is_group=False, sender="777") is False)
check("* = 全放（调试用）",
      qb.allowed({qb.KEY_ENABLE: True, qb.KEY_ALLOW: ["*"]},
                 is_group=True, group_id="1") is True)
check("名单为空串/空列表都当空",
      qb.allowed({qb.KEY_ENABLE: True, qb.KEY_ALLOW: ["  ", ""]},
                 is_group=False, sender="123456") is False)

# ── 3. 闸串联 ────────────────────────────────────────────────────
check("三层都过 → 接管", qb.should_try(cfg_me, "你知道我是谁吗", sender="123456") is True)
check("闲聊照样不接管（闸在名单之后）",
      qb.should_try(cfg_me, "哈哈哈", sender="123456") is False)
check("带图但话太短 → 不接管",
      qb.should_try(cfg_me, "哈", has_media=True, sender="123456") is False)

# ── 4. 真跑一遍（假 chat_fn + 假占位）────────────────────────────
placeholders = []
notes = []


def fake_placeholder(text, is_group, group_id, sender):
    placeholders.append((text, is_group, group_id, sender))


def fake_note(name, ok):
    notes.append((name, ok))


rounds = []


def chat_fn(messages, tools, on_reasoning=None):
    """第一轮要 fs_read，第二轮给结论 —— 与真模型同形。"""
    rounds.append(len(messages))
    if len([m for m in messages if m.get("role") == "tool"]) == 0:
        return ("", [{"id": "c1", "type": "function",
                      "function": {"name": "fs_read",
                                   "arguments": '{"path": "docs/qq_agent_manual.md"}'}}], {})
    return ("手册我翻到了", [], {})


br = qb.Bridge(deps={"chat": chat_fn, "placeholder": fake_placeholder,
                     "note_call": fake_note},
               root=ROOT, cfg=cfg_me, log=lambda _m: None)
r = br.run("你翻一下操作手册", is_group=False, sender="123456",
           system_prompt="我是小a")

check("被接管", r["used"] is True, r)
check("拿到答案", r["ok"] is True and "手册我翻到了" in r["text"], r)
check("占位先发且只发一次", len(placeholders) == 1, placeholders)
check("占位是名单里的短句",
      bool(placeholders) and placeholders[0][0] in qa.PLACEHOLDERS, placeholders)
check("占位带对了收件人",
      bool(placeholders) and placeholders[0][1:] == (False, "", "123456"), placeholders)
check("工具留痕转写成 note_call", notes == [("fs_read", True)], notes)
check("tool_results 保留 ok 标记", r["tool_results"] == [["fs_read", True]], r["tool_results"])
check("真的跑了两轮", len(rounds) == 2, rounds)

# 台账里已经记过（execute_tool_call 自己会记）→ 桥不再补一遍
led = []
notes2 = []


def ledger_dep():
    return list(led)


def chat_sim(messages, tools=None, on_reasoning=None):
    if not any(m.get("role") == "tool" for m in messages):
        led.append((time.time(), "fs_read", True, ""))   # 模拟执行时记账
        return ("", [{"id": "c1", "type": "function",
                      "function": {"name": "fs_read",
                                   "arguments": '{"path": "docs/qq_agent_manual.md"}'}}], {})
    return ("看完了", [], {})


br_dd = qb.Bridge(deps={"chat": chat_sim, "ledger": ledger_dep,
                        "note_call": lambda n, o: notes2.append((n, o))},
                  root=ROOT, cfg=cfg_me)
r_dd = br_dd.run("帮我查一下这个文件", sender="123456")
check("台账已记过 → 桥不重复记", notes2 == [], notes2)
check("去重不影响答案", r_dd["text"] == "看完了", r_dd)

# 没注入 chat：装配失败 → 不接管（text 空），且不抛
br_noc = qb.Bridge(deps={"placeholder": fake_placeholder}, root=ROOT, cfg=cfg_me)
r2 = br_noc.run("你知道我是谁吗", is_group=False, sender="123456")
check("没注入 chat → 不接管", r2["used"] is False and r2["text"] == "", r2)
check("没注入 chat → 带 error 不抛", "chat" in r2["error"], r2["error"])


# ── 5. 失败不接管（回落老链路）───────────────────────────────────
def boom_chat(messages, tools, on_reasoning=None):
    raise RuntimeError("模型挂了")


br_boom = qb.Bridge(deps={"chat": boom_chat, "placeholder": fake_placeholder,
                          "note_call": fake_note},
                    root=ROOT, cfg=cfg_me)
r3 = br_boom.run("你翻一下操作手册", sender="123456")
check("模型挂 → ok=False 且 text 为空（调用方回落）",
      r3["ok"] is False and r3["text"] == "", r3)
check("模型挂 → 不抛异常", isinstance(r3, dict))
check("模型挂时占位已经发出去了（能接受：占位是短句）",
      len(placeholders) == 3, len(placeholders))


# ── 6. 群聊步数仍按 2 步预算 ────────────────────────────────────
seen = {}


def cap_chat(messages, tools, on_reasoning=None):
    return ("行", [], {})


import harness.qq_agent as _qa  # noqa: E402

_orig = _qa.QQAgent.run


def spy_run(self, *a, **kw):
    seen.update(kw)
    return _orig(self, *a, **kw)


_qa.QQAgent.run = spy_run
try:
    br_g = qb.Bridge(deps={"chat": cap_chat, "placeholder": fake_placeholder},
                     root=ROOT, cfg={qb.KEY_ENABLE: True, qb.KEY_ALLOW: ["group:777"]})
    br_g.run("群里问一句", is_group=True, group_id="777")
finally:
    _qa.QQAgent.run = _orig
check("群聊按 is_group=True 下发", seen.get("is_group") is True, seen)
check("群聊 session 带群号", seen.get("session_id") == "qq_g_777", seen.get("session_id"))

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
