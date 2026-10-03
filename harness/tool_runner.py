# -*- coding: utf-8 -*-
"""harness/tool_runner.py —— 工具执行的**单点实现**（主链与 agent 共用）。

为什么要有这一层（用户要求："功能重复就优化，看逻辑、看用途优化，不是直接删"）
    在此之前有两处各自实现"调工具 + 拿结果 + 回灌给模型"：
      - `bot_core._call_deepseek_sync` 的自建 while 循环（主回复链，每次群聊/私聊都走）
      - `harness/agent_loop.run_agent` 的循环（agent 侧）
    两处逻辑本来一致，但**能力不同步**：agent 侧有 spill / 预算闸 / 事件溯源，
    主链侧没有。结果是"同一个项目里，两套工具执行语义"。

    本模块把"执行一个工具调用"抽成**唯一实现**，两边都调它 → 能力自动一致，
    以后加东西只需加一处（这也是"极低成本"：维护成本不翻倍）。

职责边界
    - 只负责**执行与产出**：参数补齐、越界防护、执行、retention+spill、返回统一结构。
    - **不负责**：循环控制、消息数组组装、LLM 调用、token 统计（那些留给调用方）。
      这样两边的循环仍可保持各自的特性（主链保持"工具一轮后流式收尾"的既有行为，
      agent 侧保持多轮自主推进）。

统一返回结构（函数式，便于两边消费）
    {
      "ok": bool,                 # 工具是否成功执行（不含"命中预算"这种业务拒绝）
      "blocked": str|None,        # 被拒绝的原因（越界/预算），None=真的执行了
      "tool": str,
      "inline": str,              # 回灌给模型的文本（已按预算截断 + 落盘提示）
      "full_text": str,           # 完整结果（流式/日志用）
      "truncated": bool,          # 预算事实
      "spilled": bool,
      "path": str|None,           # 落盘绝对路径（可恢复）
      "omitted_bytes": int,
      "elapsed": float,
      "error": str|None,          # 执行异常
    }
"""
import json
import time

from . import spill as _spill

DEFAULT_MAX_TOOL_RESULT_TOKENS = 1500


def _parse_args(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip() else {}
        except Exception:
            return {}
    return {}


def normalize_tool_calls(tool_calls, *, session_id="", step=0):
    """归一化工具调用列表：兼容 OpenAI / Anthropic(平铺) 结构，补稳定 id，剔除无名项。

    返回 [{"id","type","function":{"name","arguments"}}]。
    """
    out = []
    for i, tc in enumerate(tool_calls or ()):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if not isinstance(fn, dict):
            fn = {"name": tc.get("name") or "", "arguments": tc.get("arguments")}
        elif fn.get("name") is None and tc.get("name"):
            fn = {"name": tc.get("name"), "arguments": fn.get("arguments")}
        if not fn.get("name"):
            continue
        tc_id = tc.get("id") or f"call_{session_id}_{step}_{i}"
        out.append({"id": tc_id, "type": "function", "function": fn})
    return out


def execute_tool_call(tc, *, tool_functions, offered_names=None,
                      group_id=None, target_qq=None, is_group=False,
                      max_tokens=DEFAULT_MAX_TOOL_RESULT_TOKENS,
                      logger=None):
    """执行**一个**工具调用，返回统一结构（见模块 docstring）。

    参数
      tc              : 已归一化的工具调用 dict
      tool_functions  : {name: callable(args)->result}
      offered_names   : 本轮提供给模型的工具名集合（越界防护）；None=不校验
      group_id/target_qq/is_group : 参数自动补齐（群聊补 group_id，私聊补 target_qq）
      max_tokens      : 单条结果的内联预算（超预算落盘 + 给恢复路径）
    """
    log = logger or (lambda m: None)
    fn = tc.get("function") or {}
    name = fn.get("name") or ""
    args = _parse_args(fn.get("arguments"))
    tc_id = tc.get("id") or ""

    base = {"tool": name, "ok": False, "blocked": None, "inline": "",
            "full_text": "", "truncated": False, "spilled": False, "path": None,
            "omitted_bytes": 0, "elapsed": 0.0, "error": None, "id": tc_id}

    # ── 参数补齐（不覆盖模型显式给的值）──
    if is_group and group_id and "group_id" not in args:
        args["group_id"] = group_id
    elif (not is_group) and target_qq and "target_qq" not in args:
        args["target_qq"] = target_qq

    func = (tool_functions or {}).get(name)

    # ── 越界防护：本轮没提供给模型的工具不该被执行 ──
    #  （按需加载后工具集变小，模型"幻觉"调用会变多；如实回灌而非静默执行）
    if offered_names is not None and name not in offered_names:
        if func is None:
            msg = f"未知工具: {name}"
        else:
            avail = "、".join(sorted(offered_names)) or "无"
            msg = (f"本轮未提供工具 {name}（本轮只提供：{avail}）。"
                   f"请使用既有工具，或用自然语言直接回答。")
        base.update(inline=msg, full_text=msg, blocked="not_offered")
        return base

    if func is None:
        msg = f"未知工具: {name}"
        base.update(inline=msg, full_text=msg, blocked="unknown")
        return base

    # ── 执行（异常隔离：单个工具炸了不能带崩整轮）──
    t0 = time.time()
    try:
        raw = func(args)
        ok = True
        err = None
    except Exception as e:
        raw = f"工具执行异常: {type(e).__name__}: {e}"
        ok = False
        err = f"{type(e).__name__}: {e}"
    elapsed = round(time.time() - t0, 2)

    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)

    # ── retention + spill ──
    #  **硬纪律**：truncated 是"预算事实"，绝不等于"没找到/没权限"（并列不同字段）。
    sp = _spill.spill_text(text, kind="tool", tag=name, max_inline_tokens=max_tokens)
    inline = sp.get("inline", text)
    spilled = bool(sp.get("spilled"))
    path = sp.get("path")

    # 落盘/截断时必须给**恢复提示**，否则模型会以为看到的就是全部 → 比不截断更糟
    if spilled and path:
        inline += (f"\n（完整内容较长，已保存到：{path}；需要时用读取工具取全文）")
    elif sp.get("truncated"):
        inline += "\n（内容较长已截断；如需完整内容请缩小查询范围后重新调用）"

    base.update(ok=ok, inline=inline, full_text=text,
                truncated=bool(sp.get("truncated")), spilled=spilled, path=path,
                omitted_bytes=int(sp.get("omitted_bytes") or 0),
                elapsed=elapsed, error=err)
    if err:
        log(f"  → {name}: 执行异常（已隔离）: {err}")
    return base


def make_tool_message(tc_id, inline):
    """构造回灌给模型的 tool 消息。"""
    return {"role": "tool", "tool_call_id": tc_id, "content": inline}
