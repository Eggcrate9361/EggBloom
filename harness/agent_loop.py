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
import time

from . import session_log as _slog
from . import tool_runner as _tr
# `_spill` 保留为模块级别名：spill 是 agent 的存储约定，
# 外部（测试/诊断）通过它拿到 SPILL_DIR 做隔离，属于既有接口，不要删。
from . import spill as _spill  # noqa: F401

# 事件类型
EV_USER = "user_msg"
EV_ASSISTANT = "assistant"
EV_TOOL_CALL = "tool_call"
EV_TOOL_RESULT = "tool_result"
EV_FINAL = "final"

DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_TOOL_RESULT_TOKENS = _tr.DEFAULT_MAX_TOOL_RESULT_TOKENS  # 单点定义，避免两处漂移
#: 单步（一次模型返回）里最多执行几个工具调用
MAX_CALLS_PER_STEP = 8
#: 整个 run 里最多执行几次工具调用（成本硬闸；主程序 agent 也用它）
MAX_TOOL_CALLS = 40


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


# 参数解析复用单点实现（避免与 tool_runner 各写一份）
_parse_args = _tr._parse_args


def run_agent(*, session_id, user_input, chat_fn, tool_functions,
              system_prompt="", history=None, max_steps=DEFAULT_MAX_STEPS,
              on_event=None, should_abort=None, logger=None,
              max_tool_result_tokens=DEFAULT_MAX_TOOL_RESULT_TOKENS,
              persist=True, extra_tools=None, tool_loader=None,
              tool_schemas=None):
    """跑一次 agent 循环，返回 AgentResult。

    参数（全部依赖注入，故本模块不依赖 bot_core）
      session_id           : 会话标识（用于 session_log 命名空间与回放）
      user_input           : 本次用户输入
      chat_fn(messages, tools) -> (content, tool_calls, usage)
                             tools 可以是 name 列表，也可以是完整 schema 列表
      tool_functions       : {tool_name: callable(args_dict) -> result}
      system_prompt        : 系统提示词（调用方负责组装；本模块不碰）
      history              : 已有 messages（[{role,content}]），可为 None
      max_steps            : 工具轮数硬上限
      on_event(ev_type, data) : 事件回调（流式推给前端 / 群内进度）
      should_abort() -> bool  : 中止检查
      logger(msg)          : 日志
      persist              : 是否写 session_log（单测可关）
      tool_loader(names) -> schemas
                           : **按需加载**。给定工具名 → 返回 OpenAI schema 列表，
                             顺序由 loader 保证稳定（见 harness.tool_registry）。
                             轮内只调用并缓存一次 → 保前缀缓存。
      tool_schemas         : 直接给定 schema 列表（与 tool_loader 二选一）

    工具名来源（三合一，稳定拼接）
      1. tool_functions.keys()（本会话可用实现）
      2. extra_tools（调用方额外追加）
      3. tool_loader 的返回（若给了 loader 且 tool_functions 中缺 schema 时）

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
            except Exception as e:
                log(f"[agent] on_event 回调异常（已隔离）: {e}")

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

    tool_names = list(tool_functions.keys())
    for t in (extra_tools or ()):
        if t not in tool_names:
            tool_names.append(t)

    # ── 工具 schema 的**轮内一次性**解析 + 缓存 ─────────────────────
    #  关键：同一轮内多次调用 chat_fn（含一轮多工具）绝不能重复解析，
    #  也绝不能中途改变顺序 —— 否则前缀缓存失效。整个 run 只解析一次。
    _schemas_cache = {"value": None, "names_sig": None}

    def _resolve_schemas():
        if _schemas_cache["value"] is not None:
            return _schemas_cache["value"]
        if tool_schemas is not None:
            _schemas_cache["value"] = list(tool_schemas)
        elif tool_loader is not None:
            try:
                _schemas_cache["value"] = list(tool_loader(tool_names) or [])
            except Exception as e:
                log(f"[agent] tool_loader 失败，退回名字列表: {e}")
                _schemas_cache["value"] = list(tool_names)
        else:
            _schemas_cache["value"] = list(tool_names)
        _schemas_cache["names_sig"] = ",".join(tool_names)
        return _schemas_cache["value"]

    def _tools_for_call():
        """chat_fn 需要的工具参数：**有 schema 就传 schema，没有就传名字**。"""
        v = _resolve_schemas()
        if not v:
            return []
        first = v[0]
        if isinstance(first, dict) and "function" in first:
            return v
        return tool_names

    try:
        total_tool_calls = 0
        for step in range(max_steps + 1):
            _check_abort()
            step_tool_count = 0

            ret = chat_fn(messages, _tools_for_call())
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

            # ── 总工具预算已耗尽：不再空转，直接如实收敛 ──
            #  模型还在要工具但预算没了 → 继续循环只会浪费 token，没有产出。
            if total_tool_calls >= MAX_TOOL_CALLS:
                _tail = str(content or "").strip()
                res.ok = bool(_tail)
                res.answer = _tail or "工具调用预算已用完，任务未完成。"
                res.error = None if _tail else f"工具调用预算耗尽（{MAX_TOOL_CALLS} 次）"
                _append_log(EV_FINAL, {"content": res.answer, "steps": step + 1,
                                       "error": res.error, "reason": "tool_budget"})
                _emit("final", {"content": res.answer, "steps": step + 1,
                                "error": res.error, "elapsed": round(time.time() - t0, 2)})
                return res

            # 归一化 tool_calls（单点实现，主链共用同一套语义）
            norm_calls = _tr.normalize_tool_calls(tool_calls, session_id=session_id, step=step)
            if not norm_calls:
                # 有 tool_calls 字段但结构全不可用 → 视作无工具，收敛
                res.ok = True
                res.answer = str(content or "").strip()
                _append_log(EV_FINAL, {"content": res.answer, "steps": step + 1})
                _emit("final", {"content": res.answer, "steps": step + 1,
                                "elapsed": round(time.time() - t0, 2)})
                return res
            tool_calls = norm_calls

            # 回灌 assistant 的工具调用消息
            messages.append({"role": "assistant", "content": content or "",
                             "tool_calls": [dict(tc) for tc in tool_calls]})

            for tc in tool_calls:
                _check_abort()
                name = (tc.get("function") or {}).get("name") or ""
                tc_id = tc.get("id") or ""

                # 工具调用预算闸：单步调用数与总调用数双上限，防模型刷调用把成本打爆
                if total_tool_calls >= MAX_TOOL_CALLS:
                    raw_result = (f"已达本次任务工具调用上限 {MAX_TOOL_CALLS}，"
                                  f"请用已有信息给出结论，不要再调用工具。")
                    _append_log(EV_TOOL_RESULT, {"tool": name, "ok": False, "step": step + 1,
                                                 "error": "tool_budget"})
                    _emit("tool_result", {"tool": name, "ok": False, "step": step + 1,
                                          "id": tc_id, "summary": raw_result})
                    messages.append(_tr.make_tool_message(tc_id, raw_result))
                    continue
                if step_tool_count >= MAX_CALLS_PER_STEP:
                    raw_result = (f"单步工具调用数超限（{MAX_CALLS_PER_STEP}），"
                                  f"本次调用被忽略；请一次只调少量工具。")
                    _append_log(EV_TOOL_RESULT, {"tool": name, "ok": False, "step": step + 1,
                                                 "error": "per_step_budget"})
                    _emit("tool_result", {"tool": name, "ok": False, "step": step + 1,
                                          "id": tc_id, "summary": raw_result})
                    messages.append(_tr.make_tool_message(tc_id, raw_result))
                    continue
                total_tool_calls += 1
                step_tool_count += 1

                _append_log(EV_TOOL_CALL, {"tool": name, "step": step + 1,
                                           "args": _parse_args((tc.get("function") or {}).get("arguments"))})
                _emit("tool_call", {"tool": name, "step": step + 1, "id": tc_id,
                                    "args": _parse_args((tc.get("function") or {}).get("arguments"))})

                # ── 执行（单点实现：越界防护 + 异常隔离 + retention/spill）──
                r = _tr.execute_tool_call(
                    tc, tool_functions=tool_functions,
                    offered_names=None,          # agent 侧工具集由调用方控制，不做额外越界拦截
                    max_tokens=max_tool_result_tokens, logger=log,
                )

                _append_log(EV_TOOL_RESULT, {
                    "tool": name, "ok": r["ok"], "step": step + 1,
                    "blocked": r["blocked"], "truncated": r["truncated"],
                    "spilled": r["spilled"], "path": r["path"],
                    "omitted_bytes": r["omitted_bytes"], "error": r["error"],
                })
                _emit("tool_result", {
                    "tool": name, "ok": r["ok"], "step": step + 1, "id": tc_id,
                    "summary": (r["full_text"] or "")[:200].replace("\n", " "),
                    "elapsed": r["elapsed"], "truncated": r["truncated"],
                    "spilled": r["spilled"], "path": r["path"],
                    "omitted_bytes": r["omitted_bytes"],
                    "full_text": r["full_text"] if not r["spilled"] else None,
                })

                messages.append(_tr.make_tool_message(tc_id, r["inline"]))

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
