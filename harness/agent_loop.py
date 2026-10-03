# -*- coding: utf-8 -*-
"""harness/agent_loop.py —— Agent 运行时内核（主程序 agent 能力）。

设计：docs/plan/agent-design.md

定位
    这是主程序 agent 能力的**内核**，不是插件。两个入口共用它：
      - 主程序 Agent 对话页（webui → bot_core 薄路由 → 这里）
      - QQ 群内 Agent 模式（意图 + 权限双闸通过后 → 这里）
    区别只在输入/输出适配层；循环本身完全一致。

设计取舍（对齐 harness 高标准）
    - **依赖注入**：chat_fn / tool_functions / logger 由调用方传入，
      因此本模块**不 import bot_core**，可独立单测。
    - **session_log 事件溯源**：每轮用户输入 / 工具调用 / 模型输出都 append 到日志；
      历史从日志派生（可选）。铁律「模型可见即已记录」。
    - **retention + spill**：工具返回超长时按预算有界保留；超预算落盘，
      卡里给绝对路径 + read 提示，模型可再读（可恢复，不是简单截断）。
    - **可中止**：每次循环检查 abort 标志（用于 UI 的 Esc 打断）。
    - **步数与成本上限**：硬上限，绝不无限循环。

事件类型（写进 session_log，kind="agent"）
    user_msg   用户/调用方输入
    assistant  模型文字输出（中间或最终）
    tool_call  一次工具调用（含参数摘要）
    tool_result 一次工具结果（含是否 spill）
    final      最终答复
"""
import json
import time

from . import session_log as _slog
from . import spill as _spill

# 事件类型
EV_USER = "user_msg"
EV_ASSISTANT = "assistant"
EV_TOOL_CALL = "tool_call"
EV_TOOL_RESULT = "tool_result"
EV_FINAL = "final"

DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_TOOL_RESULT_TOKENS = 1500   # 单条工具结果的内联预算


class AgentAborted(Exception):
    """调用方请求中止当前 agent 运行。"""
    pass


class AgentResult:
    """一次 agent 运行的最终结果。"""

    __slots__ = ("ok", "answer", "steps", "trace", "error", "aborted",
                 "usage_total", "session_id")

    def __init__(self):
        self.ok = False
        self.answer = ""
        self.steps = 0
        self.trace = []
        self.error = None
        self.aborted = False
        self.usage_total = {"input": 0, "output": 0, "total": 0}
        self.session_id = ""

    def to_dict(self):
        return {
            "ok": self.ok,
            "answer": self.answer,
            "steps": self.steps,
            "trace": self.trace,
            "error": self.error,
            "aborted": self.aborted,
            "usage_total": self.usage_total,
            "session_id": self.session_id,
        }


def _accumulate_usage(acc, usage):
    if not isinstance(usage, dict):
        return
    acc["input"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    acc["output"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    acc["total"] += int(usage.get("total_tokens") or 0)


def _normalize_chat_return(ret):
    """兼容 (content, tool_calls, usage) 元组 或 {'content','tool_calls','usage'} 字典。"""
    if isinstance(ret, dict):
        return (ret.get("content") or "", ret.get("tool_calls") or [], ret.get("usage") or {})
    content, tool_calls, usage = (list(ret) + [None, None, None])[:3]
    return (content or ""), (tool_calls or []), (usage or {})


def _parse_args(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip() else {}
        except Exception:
            return {}
    return {}


def run_agent(*, session_id, user_input, chat_fn, tool_functions,
              system_prompt="", history=None, max_steps=DEFAULT_MAX_STEPS,
              on_event=None, should_abort=None, logger=None,
              max_tool_result_tokens=DEFAULT_MAX_TOOL_RESULT_TOKENS,
              persist=True, extra_tools=None):
    """跑一次 agent 循环，返回 AgentResult。

    参数（全部依赖注入，故本模块不依赖 bot_core）
      session_id           : 会话标识（用于 session_log 命名空间与回放）
      user_input           : 本次用户输入
      chat_fn(messages, tools) -> (content, tool_calls, usage)
      tool_functions       : {tool_name: callable(args_dict) -> result}
      system_prompt        : 系统提示词（调用方负责组装；本模块不碰）
      history              : 已有 messages（[{role,content}]），可为 None
      max_steps            : 工具轮数硬上限
      on_event(ev_type, data) : 事件回调（流式推给前端 / 群内进度）
      should_abort() -> bool  : 中止检查
      logger(msg)          : 日志
      persist              : 是否写 session_log（单测可关）

    返回 AgentResult。
    """
    t0 = time.time()
    res = AgentResult()
    res.session_id = str(session_id)
    log = logger or (lambda m: None)

    def _emit(ev_type, data):
        if on_event:
            try:
                on_event(ev_type, data)
            except Exception:
                pass

    def _check_abort():
        if should_abort and should_abort():
            raise AgentAborted()

    def _append_log(ev_type, payload):
        if not persist:
            return
        try:
            _slog.append("agent", session_id, ev_type, payload)
        except Exception as e:
            log(f"[agent] session_log 写入失败（不影响执行）: {e}")

    # ── 组装 messages ──────────────────────────────────────────────
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_input})

    _append_log(EV_USER, {"content": user_input})
    _emit("user", {"content": user_input})

    tools = list(tool_functions.keys())
    if extra_tools:
        tools += [t for t in extra_tools if t not in tools]

    try:
        for step in range(max_steps + 1):
            _check_abort()

            ret = chat_fn(messages, tools)
            content, tool_calls, usage = _normalize_chat_return(ret)
            _accumulate_usage(res.usage_total, usage)
            res.steps = step + 1

            # 无工具调用 → 收敛，得到最终答复
            if not tool_calls:
                res.ok = True
                res.answer = str(content or "").strip()
                _append_log(EV_FINAL, {"content": res.answer, "steps": step + 1})
                _emit("final", {"content": res.answer, "steps": step + 1,
                                "elapsed": round(time.time() - t0, 2)})
                return res

            # 记录模型的中间文字（若有）
            if content and str(content).strip():
                _append_log(EV_ASSISTANT, {"content": str(content).strip()})

            # 回灌 assistant 的工具调用消息
            messages.append({"role": "assistant", "content": content or "",
                             "tool_calls": [dict(tc) for tc in tool_calls]})

            for tc in tool_calls:
                _check_abort()
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                args = _parse_args(fn.get("arguments"))
                tc_id = tc.get("id") or ""

                _append_log(EV_TOOL_CALL, {"tool": name, "args": args, "step": step + 1})
                _emit("tool_call", {"tool": name, "args": args, "step": step + 1,
                                    "id": tc_id})

                t_tool = time.time()
                func = tool_functions.get(name)
                if func is None:
                    raw_result = f"未知工具: {name}"
                    ok = False
                else:
                    try:
                        raw_result = func(args)
                        ok = True
                    except Exception as e:
                        raw_result = f"工具执行异常: {e}"
                        ok = False
                elapsed = round(time.time() - t_tool, 2)

                # ── retention + spill：超长结果有界保留 / 落盘可恢复 ──
                text_result = raw_result if isinstance(raw_result, str) else json.dumps(
                    raw_result, ensure_ascii=False, default=str)
                sp = _spill.spill_text(
                    text_result, kind="agent_tool", tag=name,
                    max_inline_tokens=max_tool_result_tokens,
                )
                inline = sp["inline"]
                spilled = sp["spilled"]

                _append_log(EV_TOOL_RESULT, {
                    "tool": name, "ok": ok, "step": step + 1,
                    "truncated": sp["truncated"], "spilled": spilled,
                    "path": sp["path"], "omitted_bytes": sp["omitted_bytes"],
                })
                _emit("tool_result", {
                    "tool": name, "ok": ok, "step": step + 1, "id": tc_id,
                    "summary": text_result[:200].replace("\n", " "),
                    "elapsed": elapsed, "truncated": sp["truncated"],
                    "spilled": spilled, "path": sp["path"],
                    "omitted_bytes": sp["omitted_bytes"],
                    "full_text": text_result if not spilled else None,
                })

                messages.append({"role": "tool", "tool_call_id": tc_id, "content": inline})

        # 超过步数上限仍未收敛
        res.ok = False
        res.error = f"达到步数上限 {max_steps}，未收敛"
        res.answer = ""
        _append_log(EV_FINAL, {"content": "", "steps": res.steps, "error": res.error})
        _emit("final", {"content": "", "steps": res.steps, "error": res.error,
                        "elapsed": round(time.time() - t0, 2)})
        return res

    except AgentAborted:
        res.aborted = True
        res.ok = False
        res.error = "已中止"
        _emit("aborted", {"steps": res.steps})
        return res
    except Exception as e:
        res.ok = False
        res.error = f"{type(e).__name__}: {e}"
        _emit("error", {"error": res.error})
        return res


def derive_history(session_id, *, limit=None):
    """从 session_log 派生该 agent 会话的 messages（可回放）。"""
    out = []
    for ev in _slog.events_of("agent", session_id):
        t = ev.get("type")
        p = ev.get("payload") or {}
        if t == EV_USER:
            out.append({"role": "user", "content": p.get("content", "")})
        elif t == EV_FINAL:
            c = p.get("content")
            if c:
                out.append({"role": "assistant", "content": c})
    if limit:
        out = out[-int(limit):]
    return out


def replay(session_id, *, limit=None):
    """回放 agent 会话的完整轨迹（含工具调用），用于 UI 重建。"""
    events = _slog.events_of("agent", session_id)
    if limit:
        events = events[-int(limit):]
    return [{"type": e.get("type"), "payload": e.get("payload"), "seq": e.get("seq")}
            for e in events]
