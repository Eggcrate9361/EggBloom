# -*- coding: utf-8 -*-
"""tests/test_qq_agent_wiring.py —— A3 接线回归（bot_core → bridge → QQAgent）。

单元测试（test_qq_agent_bridge）证明"决策层是对的"，这一层证明
**bot_core 真的把线接上了**：

  1. 默认关（配置里没有这个键）→ `_qq_agent_reply` 返回 ""、"不占位、不调模型"。
  2. 开着但不在名单里 → 同样不接管。
  3. 开了 + 名单命中 + 意图闸过 → 占位先发、agent 用原语、答案原样返回，
     并且工具留痕进了 tool_runner 台账（第三类断言要吃这个）。
  4. agent 挂了 → 返回 ""（调用方回落老链路），不抛。
  5. 群聊只认 group:<号>，私聊号不会把群也开了。

不调真实 LLM / 不连 OneBot：chat / placeholder / log 全部替身。
运行：python tests/test_qq_agent_wiring.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402
from harness import tool_runner as tr  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


_ORIG_LOG = bot_core.log
_ORIG_CFG = dict(bot_core._config)


def _reset_cfg(**kw):
    bot_core._config.clear()
    bot_core._config.update(_ORIG_CFG)
    for k in ("qq_agent_enable", "qq_agent_allow", "qq_agent_hosts",
              "qq_agent_allow_write"):
        bot_core._config.pop(k, None)
    bot_core._config.update(kw)


# ── 替身 ────────────────────────────────────────────────────────
sent = []
chat_calls = []


def fake_placeholder(text, is_group, group_id, sender):
    sent.append((text, is_group, group_id, sender))


def fake_chat_ok(messages, tools=None, on_reasoning=None):
    chat_calls.append(len(messages))
    if not any(m.get("role") == "tool" for m in messages):
        return ("", [{"id": "c1", "type": "function",
                      "function": {"name": "fs_read",
                                   "arguments": '{"path": "docs/qq_agent_manual.md"}'}}], {})
    return ("查到了，手册在", [], {})


def fake_chat_boom(messages, tools=None, on_reasoning=None):
    raise RuntimeError("模型挂了")


Q = "帮我查一下上次的记录"          # 动词命中「查」+ 对象命中「记录」

try:
    bot_core.log = lambda _m: None
    bot_core._qq_agent_placeholder = fake_placeholder

    # ── 1. 默认关 ───────────────────────────────────────────────
    _reset_cfg()
    bot_core._qq_agent_chat = fake_chat_ok
    tr.clear_tool_ledger()
    sent.clear()
    chat_calls.clear()
    out = bot_core._qq_agent_reply(Q, [], is_group=False, sender="123456")
    check("没配开关 → 不接管", out == "", out)
    check("没配开关 → 不发占位", sent == [], sent)
    check("没配开关 → 不调模型", chat_calls == [], chat_calls)

    # ── 2. 开着但不在名单 ───────────────────────────────────────
    _reset_cfg(qq_agent_enable=True, qq_agent_allow=["999999"])
    out = bot_core._qq_agent_reply(Q, [], is_group=False, sender="123456")
    check("名单不命中 → 不接管", out == "", out)
    check("名单不命中 → 不调模型", chat_calls == [], chat_calls)

    # ── 3. 开着 + 名单命中 ──────────────────────────────────────
    _reset_cfg(qq_agent_enable=True, qq_agent_allow=["123456"])
    tr.clear_tool_ledger()
    sent.clear()
    chat_calls.clear()
    bot_core._qq_agent_chat = fake_chat_ok
    out = bot_core._qq_agent_reply(Q, [], is_group=False, sender="123456")
    check("名单命中 → 接管并返回答案", out == "查到了，手册在", out)
    check("占位先发了一条", len(sent) == 1, sent)
    check("占位发给对了人",
          bool(sent) and sent[0][1] is False and sent[0][3] == "123456", sent)
    check("模型被调了（两轮）", len(chat_calls) == 2, chat_calls)
    names = [c[1] for c in tr.recent_tool_calls()]
    check("工具留痕进了主台账", "fs_read" in names, names)
    check("同一轮只记一次（桥去重生效）", names.count("fs_read") == 1, names)
    oks = [c[2] for c in tr.recent_tool_calls() if c[1] == "fs_read"]
    check("台账里 fs_read 是成功的", bool(oks) and all(oks), oks)

    # ── 4. 闲聊照样不接管 ───────────────────────────────────────
    sent.clear()
    chat_calls.clear()
    out = bot_core._qq_agent_reply("哈哈哈", [], is_group=False, sender="123456")
    check("闲聊不接管", out == "" and not chat_calls, (out, chat_calls))

    # ── 5. agent 挂了 → 回落 ────────────────────────────────────
    sent.clear()
    bot_core._qq_agent_chat = fake_chat_boom
    out = bot_core._qq_agent_reply(Q, [], is_group=False, sender="123456")
    check("agent 挂 → 返回空串（调用方回落）", out == "", out)

    # ── 6. 群聊只认 group: 前缀 ─────────────────────────────────
    _reset_cfg(qq_agent_enable=True, qq_agent_allow=["777"])
    sent.clear()
    chat_calls.clear()
    bot_core._qq_agent_chat = fake_chat_ok
    out = bot_core._qq_agent_reply(Q, [], is_group=True, group_id="777", sender="1")
    check("裸号码不开群", out == "" and not chat_calls, (out, chat_calls))

    _reset_cfg(qq_agent_enable=True, qq_agent_allow=["group:777"])
    sent.clear()
    chat_calls.clear()
    out = bot_core._qq_agent_reply(Q, [], is_group=True, group_id="777", sender="1")
    check("group:777 → 群聊接管", out == "查到了，手册在", out)
    check("群聊占位打到群里",
          bool(sent) and sent[0][1] is True and sent[0][2] == "777", sent)

    # ── 7. 1.6.0 铺路：本轮上下文进 agent 的 system prompt ──────
    _reset_cfg(qq_agent_enable=True, qq_agent_allow=["123456"])
    seen = []

    def chat_capture(messages, tools=None, on_reasoning=None):
        seen.append(list(messages))
        return ("办完了", [], {})

    bot_core._qq_agent_chat = chat_capture
    out = bot_core._qq_agent_reply(
        Q, [], is_group=False, sender="123456",
        extra_ctx={"ego": "【情绪】开心", "memory": "【画像】喜欢猫"})
    sys_text = "\n".join(m.get("content") or "" for m in (seen[0] if seen else [])
                         if m.get("role") == "system")
    check("agent 的 system prompt 带 ego", "【情绪】开心" in sys_text, sys_text[-160:])
    check("agent 的 system prompt 带画像", "【画像】喜欢猫" in sys_text, sys_text[-160:])
    check("上下文不影响答案", out == "办完了", out)
finally:
    bot_core.log = _ORIG_LOG
    _reset_cfg()

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
