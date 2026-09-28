# -*- coding: utf-8 -*-
"""
Autonomous Web Agent - 自包含 observe -> think -> act -> observe 闭环
====================================================================
插件内自主完成多步网页任务。不直接 import bot_core（避免循环依赖），
由宿主（bot_core.handle_web_operate）注入 chat 回调：

    chat(messages, tools=..., max_tokens=...) ->
        (content, tool_calls, usage)  或  {"content":..., "tool_calls":...}

每步把最新快照喂回模型，模型用 ref 调用工具，动作后自动 re-snapshot，
直到模型调用 done / 达到 max_steps / 出错。
"""

import json
from typing import Any, Dict, List

WEB_AGENT_SYSTEM_PROMPT = (
    "你是网页自动化 Agent。你会看到页面的结构化快照：每个可交互元素形如\n"
    '  - button "登录" [ref=e5]\n'
    "你必须通过 ref 来操作元素，禁止编造 CSS 选择器。\n"
    "工作循环：读快照 -> 选一个动作调用工具 -> 系统回传新快照 -> 再判断。\n"
    "规则：\n"
    "1) 一次只调用一个工具；动作后系统会自动返回最新快照。\n"
    "2) 需要读文字/取数据用 web__get_text 或 web__extract。\n"
    "3) 表单填写优先用 web__fill(ref,value)；下拉用 web__select(ref,value)。\n"
    "4) 找不到合适元素时，先 web__scroll 或 web__snapshot 重新观察，别硬猜。\n"
    "5) 任务完成（或确认无法完成）时，必须调用 web__done，并在 answer 里给出最终结论/数据。\n"
    "6) 保持步骤精简，不做无关操作。"
)

# 提供给 Agent 的工具子集（OpenAI function-calling 格式）
WEB_AGENT_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web__snapshot",
            "description": "重新获取当前页面可交互元素快照（ref 列表）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web__get_text",
            "description": "获取当前页面正文文本。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web__click",
            "description": "点击某个 ref 元素。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "快照中的元素编号，如 e5"},
                    "description": {"type": "string", "description": "元素语义描述，供视觉兜底用（可选）"},
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web__fill",
            "description": "在某个 ref 输入框填入文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["ref", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web__select",
            "description": "选择下拉框某项。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "value": {"type": "string", "description": "option 的 value 或可见文本"},
                },
                "required": ["ref", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web__press",
            "description": "对某元素按键（Enter/Tab 等）；ref 可省略则按全局键。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "key": {"type": "string", "description": "如 Enter / Tab / Escape"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web__press_key",
            "description": "页面级按键（无需选中元素），适配键盘驱动的网页，如按 1/2/方向键/Enter 答题。",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string", "description": "如 1 / 2 / Enter / ArrowRight / ArrowLeft"}},
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web__scroll",
            "description": "滚动页面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["down", "up", "bottom", "top"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web__extract",
            "description": "从页面或某 ref 范围内提取数据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "限定范围的元素编号（可选）"},
                    "target_desc": {"type": "string", "description": "要提取什么（可选）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web__navigate",
            "description": "导航到新的 URL。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web__done",
            "description": "任务完成时调用，给出最终答案。",
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "string", "description": "最终结论/数据"}},
                "required": ["answer"],
            },
        },
    },
]


def _normalize_chat_return(ret):
    """兼容 (content, tool_calls, usage) 元组 或 {'content','tool_calls'} 字典。"""
    if isinstance(ret, dict):
        return ret.get("content") or "", ret.get("tool_calls") or [], (ret.get("usage") or {})
    try:
        content, tool_calls, usage = ret
        return (content or ""), (tool_calls or []), (usage or {})
    except Exception:
        return (ret or ""), [], {}


def _exec_tool(name, args, snap, actions, page):
    """执行一个 Agent 工具调用，返回 (result_dict, done_flag, final_answer)。"""
    if name == "web__snapshot":
        return {"snapshot": snap.capture()[:4000], "ref_count": len(snap.refs)}, False, None
    if name == "web__get_text":
        text = (page.inner_text("body") or "").strip()
        return {"text": text[:6000], "length": len(text)}, False, None
    if name == "web__click":
        r = actions.act(args.get("ref"), "click", args)
        return r.as_dict(), False, None
    if name == "web__fill":
        r = actions.act(args.get("ref"), "fill", args)
        return r.as_dict(), False, None
    if name == "web__select":
        r = actions.act(args.get("ref"), "select", args)
        return r.as_dict(), False, None
    if name == "web__press":
        ref = args.get("ref")
        key = args.get("key") or "Enter"
        if ref:
            r = actions.act(ref, "press", {"value": key})
            return r.as_dict(), False, None
        page.keyboard.press(key)
        return {"ok": True, "pressed": key}, False, None
    if name == "web__press_key":
        key = args.get("key") or args.get("value") or "Enter"
        page.keyboard.press(key)
        page.wait_for_timeout(250)
        return {"ok": True, "pressed": key, "snapshot": snap.capture()[:4000]}, False, None
    if name == "web__scroll":
        direction = args.get("direction") or "down"
        amount = {"down": 500, "up": -500, "bottom": 10 ** 9, "top": -10 ** 9}.get(direction, 500)
        page.evaluate(f"window.scrollBy(0,{amount})")
        page.wait_for_timeout(250)
        return {"ok": True, "scrolled": direction, "snapshot": snap.capture()[:4000]}, False, None
    if name == "web__extract":
        ref = args.get("ref")
        h = snap.resolve_handle(ref) if ref else page.query_selector("body")
        text = ((h.inner_text() if h else "") or "").strip()[:6000]
        return {"text": text, "target_desc": args.get("target_desc", "")}, False, None
    if name == "web__navigate":
        url = (args.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "missing url"}, False, None
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        return {"ok": True, "url": page.url, "title": page.title(), "snapshot": snap.capture()[:4000]}, False, None
    if name == "web__done":
        return {"ok": True, "done": True}, True, args.get("answer", "")
    return {"ok": False, "error": f"unknown tool: {name}"}, False, None


def run_autonomous(page, snap, actions, goal, max_steps, chat_fn, config, logs=None):
    logs = logs if logs is not None else []
    try:
        initial = snap.capture()
    except Exception as e:
        initial = f"(初始快照失败: {e})"
    messages = [
        {"role": "system", "content": WEB_AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": f"目标：{goal}\n\n当前页面快照：\n{initial[:4000]}"},
    ]
    trace = []
    for step in range(max_steps):
        try:
            content, tool_calls, usage = _normalize_chat_return(chat_fn(messages, tools=WEB_AGENT_TOOLS))
        except Exception as e:
            logs.append({"level": "error", "text": f"[autonomous] chat 调用异常: {e}"})
            return {"ok": False, "error": f"LLM 调用失败: {e}", "logs": logs,
                    "result": {"steps": step, "answer": "", "trace": trace}}

        if not tool_calls:
            # 模型直接给文字结论：视为完成
            logs.append({"level": "success", "text": f"[autonomous] 第 {step+1} 步完成（无工具调用）"})
            return {"ok": True, "logs": logs,
                    "result": {"steps": step + 1, "answer": content or "", "trace": trace}}

        messages.append({"role": "assistant", "content": content or "", "tool_calls": tool_calls})
        done = False
        final_answer = ""
        for tc in tool_calls:
            fn = (tc.get("function") or {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments") or "{}"
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except Exception:
                    args = {}
            else:
                args = raw_args or {}
            tc_id = tc.get("id", "")
            try:
                result, is_done, answer = _exec_tool(name, args, snap, actions, page)
            except Exception as e:
                result, is_done, answer = {"ok": False, "error": str(e)}, False, None
                logs.append({"level": "error", "text": f"[autonomous] {name} 执行异常: {e}"})
            trace.append({"step": step + 1, "tool": name, "args": args, "ok": result.get("ok", "snapshot" in result or "text" in result)})
            logs.append({"level": "info", "text": f"[autonomous] {step+1} {name} -> {str(result)[:100]}"})
            payload = json.dumps(result, ensure_ascii=False)
            # 动作后自动 re-observe（除 done / snapshot 已含快照外的动作）
            if "snapshot" not in result and name not in ("web__done",):
                try:
                    payload_obj = result if isinstance(result, dict) else {}
                    payload_obj["snapshot"] = snap.capture()[:4000]
                    payload = json.dumps(payload_obj, ensure_ascii=False)
                except Exception:
                    pass
            messages.append({"role": "tool", "tool_call_id": tc_id, "content": payload})
            if is_done:
                done = True
                final_answer = answer or ""
        if done:
            logs.append({"level": "success", "text": f"[autonomous] done（{step+1} 步）"})
            return {"ok": True, "logs": logs,
                    "result": {"steps": step + 1, "answer": final_answer, "trace": trace}}

    logs.append({"level": "warn", "text": f"[autonomous] 达到 max_steps={max_steps} 未完成"})
    return {"ok": False, "error": f"达到最大步数 {max_steps}，任务未完成", "logs": logs,
            "result": {"steps": max_steps, "answer": "", "trace": trace}}
