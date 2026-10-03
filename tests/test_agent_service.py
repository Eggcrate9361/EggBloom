# -*- coding: utf-8 -*-
"""Agent 服务层单测：群内双闸 + 意图识别 + 会话管理 + prompt 组装 + 端到端。

覆盖用户要求中最关键的「群聊里的功能不能随便用」→ 双闸（权限 + 意图）。
"""
import os
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import agent_service as svc  # noqa: E402
from harness import session_log as slog  # noqa: E402
from harness import agent_loop as al  # noqa: E402

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)


# ── 隔离 ────────────────────────────────────────────────────────────
_tmp = tempfile.mkdtemp(prefix="agentsvc_")
_orig_slog = slog.SESSION_LOG_DIR
_orig_spill = al._spill.SPILL_DIR
slog.SESSION_LOG_DIR = os.path.join(_tmp, "session_log")
al._spill.SPILL_DIR = os.path.join(_tmp, "spill")
slog.reset_cache()

try:
    # ══ 意图闸：正例必须触发 ══════════════════════════════════════
    POS = [
        "小a 帮我去查一下明天的天气",
        "@小a 干活：把这个网页填了",
        "帮我把桌面截图发我",
        "你给我搜一下最近的新闻",
        "打开微信",
        "agent 帮我整理一下文件",
        "小a 帮我把这个文件传到群文件",
        "去查下明天的机票",
        "干活：把问卷填了",
        "给我截图看看",
        "搜一下最近AI新闻",
    ]
    for t in POS:
        ok, why = svc.detect_intent(t)
        check(f"意图正例：{t[:16]}", ok is True)

    # ══ 意图闸：闲聊/陈述必须不触发（避免"随便用"）═══════════════
    NEG = [
        "小a 你觉得今天怎么样",
        "你在干嘛",
        "晚安",
        "哈哈哈哈哈",
        "我觉得好无聊啊",
        "今天天气不错",
        "我打开了一本书",
        "别打开那个",
        "你查过吗",
        "好想你啊",
        "在忙什么",
        "这个截图真好看",
        "我不想去",
        "发个表情包",
        "怎么了",
        "嗯嗯",
    ]
    for t in NEG:
        ok, why = svc.detect_intent(t)
        check(f"意图反例：{t[:16]}", ok is False)

    # ══ 强触发词兜底 ══════════════════════════════════════════════
    ok, why = svc.detect_intent("agent 帮我整理一下文件")
    check("强触发词识别", ok and why == "强触发词")

    # ══ 双闸 · 闸一：权限（未注入 → 拒）═══════════════════════════
    svc.configure(chat_fn=None, tool_functions=None,
                  is_authorized_fn=None, static_prompt_fn=None)
    allowed, why = svc.group_gate(text="帮我把文件传上去", sender="111")
    check("★权限未注入 → 拒", allowed is False)

    # ══ 双闸 · 闸一：非管理员 → 拒（即使意图明确）════════════════
    svc.configure(is_authorized_fn=lambda q: str(q) == "999")
    allowed, why = svc.group_gate(text="小a 帮我去查天气", sender="111")
    check("★非管理员 → 拒（意图明确也没用）", allowed is False and "权限" in why)

    allowed, why = svc.group_gate(text="小a 帮我去查天气", sender="999")
    check("★管理员 + 明确意图 → 放行", allowed is True)

    allowed, why = svc.group_gate(text="你在干嘛", sender="999")
    check("★管理员 + 闲聊 → 拒", allowed is False)

    # ══ 优先级：先判权限再判意图（非管理员闲聊也是权限不足）══════
    allowed, why = svc.group_gate(text="你在干嘛", sender="111")
    check("★非管理员 → 权限不足（不泄露意图判定）", allowed is False and "权限" in why)

    # ══ 会话管理 ═════════════════════════════════════════════════
    svc._SESSIONS.clear()
    svc.configure(
        chat_fn=lambda msgs, tools: ("答复", None, {"total_tokens": 5}),
        tool_functions={"x": lambda a: "y"},
        static_prompt_fn=lambda is_group: "BASE_PROMPT",
        is_authorized_fn=lambda q: True,
    )
    s = svc.run_chat(user_input="测试", session_id="sess-1")
    check("run_chat 有 session_id", s.get("session_id") == "sess-1")
    check("run_chat ok", s.get("ok") is True)
    check("run_chat answer", s.get("answer") == "答复")
    check("会话已登记", svc.get_session("sess-1") is not None)

    # 中止
    svc._SESSIONS["sess-abort"] = {"abort": True, "busy": True, "last": 0}
    check("request_abort 生效", svc.request_abort("sess-abort") is True)
    check("未知会话 abort 返回 False", svc.request_abort("nope") is False)

    # ══ system_prompt 组装：base + 追加 agent 快照（不改写 base）═══
    p = svc.build_system_prompt(is_group=True)
    check("★prompt 含 base 前缀", p.startswith("BASE_PROMPT"))
    check("★prompt 追加了 agent 快照", "【Agent 工作模式】" in p)
    check("★追加式：base 未被改写", "BASE_PROMPT" in p)
    p2 = svc.build_system_prompt(is_group=True, extra="上下文X")
    check("extra 被追加", p2.endswith("上下文X"))

    # ══ 未就绪 ═══════════════════════════════════════════════════
    svc._CFG["chat_fn"] = None
    r = svc.run_chat(user_input="x")
    check("★未就绪返回错误", r.get("ok") is False and "未就绪" in (r.get("error") or ""))

    # ══ 端到端：run_chat → session_log 可回放 ════════════════════
    slog.reset_cache()
    svc.configure(
        chat_fn=lambda msgs, tools: ("最终答案", None, {}),
        tool_functions={"t": lambda a: "r"},
        static_prompt_fn=lambda is_group: "B",
        is_authorized_fn=lambda q: True,
    )
    svc.run_chat(user_input="端到端问题", session_id="sess-e2e")
    hist = svc.history_of("sess-e2e")
    check("★端到端：历史可回放", hist == [
        {"role": "user", "content": "端到端问题"},
        {"role": "assistant", "content": "最终答案"},
    ])
    tr = svc.trace_of("sess-e2e")
    check("★端到端：轨迹可回放", len(tr) >= 2)

    # ══ is_ready ═════════════════════════════════════════════════
    check("is_ready 依赖齐备", svc.is_ready() is True)

finally:
    slog.SESSION_LOG_DIR = _orig_slog
    al._spill.SPILL_DIR = _orig_spill
    svc._SESSIONS.clear()
    svc._CFG.update({"chat_fn": None, "tool_functions": {},
                     "static_prompt_fn": None, "is_authorized_fn": None})
    slog.reset_cache()
    shutil.rmtree(_tmp, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
