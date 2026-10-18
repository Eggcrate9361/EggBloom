# -*- coding: utf-8 -*-
"""harness/agent_loop 内核单测（不依赖 bot_core / 真实 LLM）。

覆盖：循环收敛、工具分发、步数上限、中止、session_log 溯源、spill、回放。
"""
import os
import sys
import json
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import agent_loop as al  # noqa: E402
from harness import session_log as slog  # noqa: E402

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)


# ── 隔离日志目录 ────────────────────────────────────────────────────
_tmp = tempfile.mkdtemp(prefix="agenttest_")
_orig_slog = slog.SESSION_LOG_DIR
_orig_spill = al._spill.SPILL_DIR
slog.SESSION_LOG_DIR = os.path.join(_tmp, "session_log")
al._spill.SPILL_DIR = os.path.join(_tmp, "spill")
slog.reset_cache()


def make_chat(script):
    """script: [(content, tool_calls, usage), ...]，按调用次序消费。"""
    box = {"i": 0}

    def _chat(messages, tools):
        i = box["i"]
        box["i"] += 1
        if i < len(script):
            return script[i]
        return ("(done)", None, {})
    return _chat


try:
    SID = "agent-test-1"

    # ── 1. 无工具调用 → 直接收敛 ──────────────────────────────────
    r = al.run_agent(session_id=SID, user_input="你好",
                     chat_fn=make_chat([("你好呀！", None, {"total_tokens": 10})]),
                     tool_functions={}, system_prompt="SYS")
    check("直接收敛 ok", r.ok is True)
    check("直接收敛 answer", r.answer == "你好呀！")
    check("直接收敛 steps=1", r.steps == 1)
    check("usage 累计", r.usage_total["total"] == 10)
    check("结果可 to_dict", isinstance(r.to_dict(), dict))

    # ── 2. 一次工具调用 → 再收敛 ──────────────────────────────────
    SID2 = "agent-test-2"
    calls = []

    def tool_echo(args):
        calls.append(args)
        return "工具返回：" + str(args.get("q", ""))

    script = [
        ("我来查一下", [{"id": "c1", "function": {"name": "echo", "arguments": '{"q":"天气"}'}}], {}),
        ("查到了，今天晴。", None, {}),
    ]
    r2 = al.run_agent(session_id=SID2, user_input="查天气",
                      chat_fn=make_chat(script), tool_functions={"echo": tool_echo},
                      system_prompt="SYS")
    check("工具调用后收敛 ok", r2.ok is True)
    check("工具被真正执行", calls == [{"q": "天气"}])
    check("最终答复正确", r2.answer == "查到了，今天晴。")
    check("steps=2", r2.steps == 2)

    # ── 3. 事件流（on_event）──────────────────────────────────────
    evs = []
    r3 = al.run_agent(session_id="agent-test-3", user_input="x",
                      chat_fn=make_chat(script), tool_functions={"echo": tool_echo},
                      on_event=lambda t, d: evs.append(t))
    check("事件含 user", "user" in evs)
    check("事件含 tool_call", "tool_call" in evs)
    check("事件含 tool_result", "tool_result" in evs)
    check("事件含 final", "final" in evs)

    # ── 4. 步数上限：一直调工具不收尾 ─────────────────────────────
    SID4 = "agent-test-4"
    always = [("继续", [{"id": f"c{i}", "function": {"name": "noop", "arguments": "{}"}}], {})
              for i in range(50)]

    def _endless_chat(messages, tools):
        return ("继续", [{"id": "c", "function": {"name": "noop", "arguments": "{}"}}], {})

    r4 = al.run_agent(session_id=SID4, user_input="x", chat_fn=_endless_chat,
                      tool_functions={"noop": lambda a: "ok"}, max_steps=3)
    check("★步数上限未收敛 ok=False", r4.ok is False)
    check("★步数上限有错误信息", "上限" in (r4.error or ""))
    check("★步数上限不会无限循环", r4.steps <= 5)

    # ── 5. 中止 ───────────────────────────────────────────────────
    SID5 = "agent-test-5"
    state = {"n": 0}

    def abort_soon():
        state["n"] += 1
        return state["n"] > 2

    r5 = al.run_agent(session_id=SID5, user_input="x", chat_fn=_endless_chat,
                      tool_functions={"noop": lambda a: "ok"}, max_steps=10,
                      should_abort=abort_soon)
    check("★中止被识别", r5.aborted is True)
    check("★中止时 ok=False", r5.ok is False)

    # ── 6. 工具抛异常不崩，作为结果回灌 ───────────────────────────
    def boom(args):
        raise RuntimeError("炸了")

    script6 = [
        ("试试", [{"id": "c", "function": {"name": "boom", "arguments": "{}"}}], {}),
        ("好吧，工具挂了。", None, {}),
    ]
    r6 = al.run_agent(session_id="agent-test-6", user_input="x",
                      chat_fn=make_chat(script6), tool_functions={"boom": boom})
    check("工具异常不崩", r6.ok is True)
    check("工具异常后仍收敛", "挂了" in r6.answer)

    # ── 7. 未知工具 ───────────────────────────────────────────────
    script7 = [
        ("x", [{"id": "c", "function": {"name": "nope", "arguments": "{}"}}], {}),
        ("知道了", None, {}),
    ]
    r7 = al.run_agent(session_id="agent-test-7", user_input="x",
                      chat_fn=make_chat(script7), tool_functions={})
    check("未知工具不崩", r7.ok is True)

    # ── 8. ★session_log 事件溯源：清缓存后仍可回放 ─────────────────
    slog.reset_cache()
    evs2 = slog.events_of("agent", SID2)
    types = [e.get("type") for e in evs2]
    check("★日志含 user_msg", al.EV_USER in types)
    check("★日志含 tool_call", al.EV_TOOL_CALL in types)
    check("★日志含 tool_result", al.EV_TOOL_RESULT in types)
    check("★日志含 final", al.EV_FINAL in types)

    # 派生历史与实发一致
    hist = al.derive_history(SID2)
    check("★派生历史可回放", hist == [
        {"role": "user", "content": "查天气"},
        {"role": "assistant", "content": "查到了，今天晴。"},
    ])
    full = al.replay(SID2)
    check("★replay 返回完整轨迹", len(full) == len(evs2))

    # ── 9. ★spill：超长工具结果落盘且可恢复 ────────────────────────
    SID9 = "agent-test-9"
    big = "很长很长的结果。" * 800

    def big_tool(args):
        return big

    script9 = [
        ("取数据", [{"id": "c", "function": {"name": "big", "arguments": "{}"}}], {}),
        ("好了", None, {}),
    ]
    seen = {}
    al.run_agent(session_id=SID9, user_input="x", chat_fn=make_chat(script9),
                 tool_functions={"big": big_tool}, max_tool_result_tokens=100,
                 on_event=lambda t, d: seen.setdefault(t, []).append(d) if t == "tool_result" else None)
    tr = (seen.get("tool_result") or [{}])[0]
    check("★超长结果标记 spilled", tr.get("spilled") is True)
    check("★spill 给绝对路径", bool(tr.get("path")) and os.path.isabs(tr["path"]))
    check("★spill 文件存在", os.path.isfile(tr.get("path") or ""))
    if tr.get("path") and os.path.isfile(tr["path"]):
        with open(tr["path"], "r", encoding="utf-8") as f:
            check("★spill 保存完整内容", f.read() == big)
    # 日志里记录 truncated 是预算事实
    ev9 = [e for e in slog.events_of("agent", SID9) if e.get("type") == al.EV_TOOL_RESULT]
    check("★日志记录 truncated/spilled 为独立字段",
          ev9 and ev9[0]["payload"].get("truncated") is True
          and ev9[0]["payload"].get("spilled") is True)

    # ── 10. persist=False 不写日志 ────────────────────────────────
    SID10 = "agent-test-10"
    al.run_agent(session_id=SID10, user_input="x",
                 chat_fn=make_chat([("hi", None, {})]), tool_functions={}, persist=False)
    check("★persist=False 不写日志", len(slog.events_of("agent", SID10)) == 0)

    # ── 11. history 注入 ──────────────────────────────────────────
    got = {}

    def capture_chat(messages, tools):
        got["msgs"] = list(messages)
        return ("ok", None, {})

    al.run_agent(session_id="agent-test-11", user_input="新问题",
                 chat_fn=capture_chat, tool_functions={},
                 system_prompt="SYS",
                 history=[{"role": "user", "content": "旧问题"},
                          {"role": "assistant", "content": "旧回答"}])
    seq = [(m["role"], m["content"]) for m in got["msgs"]]
    check("★system 在最前", seq[0] == ("system", "SYS"))
    check("★history 在中间", seq[1] == ("user", "旧问题") and seq[2] == ("assistant", "旧回答"))
    check("★本轮 user 在最后", seq[-1] == ("user", "新问题"))

finally:
    slog.SESSION_LOG_DIR = _orig_slog
    al._spill.SPILL_DIR = _orig_spill
    slog.reset_cache()
    shutil.rmtree(_tmp, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in PASS:
    print(f"  PASS  {n}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
