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

# ★ 预算**不再是写死的死值**（用户："去掉预算定制，不是给一个死的值，是尽量降低成本"）。
#   按**剩余上下文**自适应：剩余越多给得越宽，越到后期收得越紧 —— 成本随占用上升而下降。
#   这里只作为"调用方没给预算"时的兜底下限；agent 侧每步会按剩余空间算出实际预算。
#
# 2026-10-21：窗口不再写死。原来 CONTEXT_CHARS=48000（注释说「≈24k token」，
# 按 1.6 字/token 其实是 30k）+ 默认预算固定 1500，都是 64k 时代的假设。
# 实际模型是 1M 窗口时：① 预算与窗口完全脱钩，1M 兑现不了任何收益；
# ② 长会话里工具结果全被 spill 落盘，模型只能反复读文件空转。
# 现在统一问 model_caps（环境变量 EGGBLOOM_CTX_WINDOW 仍最优先）。
try:
    from .. import model_caps as _caps
except Exception:                                    # 包外直接跑脚本时
    try:
        import model_caps as _caps
    except Exception:
        _caps = None

_WINDOW_TOKENS = _caps.context_window() if _caps else 131072
_CHARS_PER_TOKEN = _caps.CHARS_PER_TOKEN if _caps else 1.6
_SAFETY_RATIO = _caps.SAFETY_RATIO if _caps else 0.8

#: 单条工具结果默认预算 = 窗口的 0.6%。1M → 约 6.3k；128k → 786（回落 floor 1500）。
#  取 0.6% 而不是 1%：20 轮工具链最坏情况下总量仍可控（1M 窗口 ≈ 126k，占比 12%）。
_TOOL_RESULT_RATIO = 0.006


def _tool_result_budget(window_tokens):
    """按窗口算单条工具结果的默认预算（floor 1500 / ceil 8000）。"""
    try:
        return max(1500, min(8000, int(int(window_tokens) * _TOOL_RESULT_RATIO)))
    except (TypeError, ValueError):
        return 1500


DEFAULT_MAX_TOOL_RESULT_TOKENS = _tool_result_budget(_WINDOW_TOKENS)
BUDGET_FLOOR = 800            # 再窄也要能看全一个工具结果
BUDGET_CEIL = 12000           # 富余时也不无限给（成本纪律）
CONTEXT_CHARS = int(_WINDOW_TOKENS * _CHARS_PER_TOKEN * _SAFETY_RATIO)  # 按真实窗口推导


def _context_chars(window_tokens=None):
    """可用字符预算。给 window_tokens 时按该窗口算（测试/换模型用）。"""
    if window_tokens is None:
        return CONTEXT_CHARS
    try:
        return int(int(window_tokens) * _CHARS_PER_TOKEN * _SAFETY_RATIO)
    except (TypeError, ValueError):
        return CONTEXT_CHARS


def adaptive_budget(messages, steps_left=1, used_chars=None, window_tokens=None):
    """按**剩余上下文**算本步的工具结果预算（不是常量）。

    messages      : 即将发给模型的消息（用于估算已用字符）
    steps_left    : 预计还剩几步 —— 剩下的步越多，每步分到的越少
    window_tokens : 覆盖窗口（None = 用当前模型的真实窗口）
    """
    try:
        used = used_chars if used_chars is not None else sum(
            len(json.dumps(m, ensure_ascii=False)) for m in (messages or []))
    except Exception:
        used = 0
    room = max(0, _context_chars(window_tokens) - int(used))
    per_step = int(room / max(1, int(steps_left)) * 0.5)
    return max(BUDGET_FLOOR, min(BUDGET_CEIL, per_step))

#: 需要打"时效戳"的工具：这些工具返回的是**会变的外部事实**。
#  命中即记录时刻，过期后结果前会加显式警告（见 harness.freshness）。
_FRESHNESS_TOOLS = frozenset({
    "search_web", "web_fetch", "web_operate", "get_system_info",
})

# ── 本轮工具调用台账（2026-10-23）──────────────────────────────
# 事后校验用：模型说「已经发过去了」时，得有地方查它这轮到底调没调工具。
# 为什么必须是**模块级**而不是挂在调用方：execute_tool_call 被主链、agent_loop、
# skill 三条路径调用，调用方各不相同；只有这里能保证每一次真实执行都过一遍。
# 只存 (时间, 工具名, 是否成功, 成败说明)，不存完整结果 —— 结果是长文本，
# 存下来等于把上下文复制一份，而且事后校验只关心"调没调、成没成"。
_TOOL_LEDGER = []
_TOOL_LEDGER_MAX = 200


def note_tool_call(name, ok, detail=""):
    """把一次工具执行记进本轮台账。"""
    _TOOL_LEDGER.append((time.time(), str(name or ""), bool(ok), str(detail or "")[:120]))
    if len(_TOOL_LEDGER) > _TOOL_LEDGER_MAX:
        del _TOOL_LEDGER[:len(_TOOL_LEDGER) - _TOOL_LEDGER_MAX]


def recent_tool_calls(window_sec=180.0):
    """最近 window 秒内执行过的工具 [(ts, name, ok, detail), ...]，按时间正序。"""
    try:
        now = time.time()
        return [r for r in _TOOL_LEDGER if now - r[0] <= window_sec]
    except Exception:
        return []


def clear_tool_ledger():
    """清空台账。测试用；正常流程不清 —— 靠时间窗自然过期。"""
    _TOOL_LEDGER.clear()


def _compact_text(text, list_keep=12, str_head=160, str_tail=60):
    """结构化精简：JSON 优先，长列表留前 N 项 + 总数，长串留头尾，空值丢弃。
    纯文本不是 JSON 时原样返回（那种情况压缩≈截断，交给 spill 更诚实）。"""
    try:
        obj = json.loads(text)
    except Exception:
        return None

    def walk(v):
        if isinstance(v, str):
            if len(v) <= 240:
                return v
            return v[:str_head] + "…(略" + str(len(v) - str_head - str_tail) + "字)…" + v[-str_tail:]
        if isinstance(v, list):
            if len(v) <= list_keep:
                return [walk(x) for x in v]
            out = [walk(x) for x in v[:list_keep]]
            out.append("…其余 %d 项已省略（共 %d 项）" % (len(v) - list_keep, len(v)))
            return out
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items() if x not in (None, "", [], {})}
        return v

    try:
        return json.dumps(walk(obj), ensure_ascii=False)
    except Exception:
        return None


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
                      session_id="default",
                      max_tokens=DEFAULT_MAX_TOOL_RESULT_TOKENS,
                      logger=None):
    """执行**一个**工具调用，返回统一结构（见模块 docstring）。

    参数
      tc              : 已归一化的工具调用 dict
      tool_functions  : {name: callable(args)->result}
      offered_names   : 本轮提供给模型的工具名集合（越界防护）；None=不校验
      group_id/target_qq/is_group : 参数自动补齐（群聊补 group_id，私聊补 target_qq）
      session_id      : 会话标识（用于时效戳的记录与过期判定）
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

    # ── 时效性：外部信息类工具**成功**后打时间戳（供过期判定/标注）──
    #  只在成功时打戳 —— 失败的结果本来就不该被当事实引用。
    #  同时把"过期警告"拼在结果前面：模型看到后不会把旧结果当成当前情况。
    if ok and name in _FRESHNESS_TOOLS:
        try:
            from . import freshness as _fr
            _fr.mark(session_id, name)
            _stale = _fr.stale_note(session_id, name)
            if _stale:
                text = _stale.strip() + "\n\n" + text
        except Exception:
            pass

    # ★ 先**压缩**再谈截断（用户："尽量降低成本"，而不是给个死值然后砍掉）：
    #   超预算时优先做结构化精简（长列表→前若干+总数、长串→头尾、空值丢弃）。
    #   压完能放下就直接回灌 —— 不再 spill 落盘。落盘文件本身就超预算，
    #   模型去读它必然又被截断 → 陷入"spill→read→spill"空转（实测 7 步零进展）。
    try:
        if max_tokens and len(text) > int(max_tokens) * 4:
            compacted = _compact_text(text)
            if compacted and len(compacted) <= int(max_tokens) * 4:
                text = compacted + "\n（返回较长，已按结构精简：长列表只留前若干项并给出总数；要看某一项请缩小范围再查）"
    except Exception:
        pass

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
    # 记台账（成功与失败都记：失败也是"真的调过"，事后校验要看的是这个区别）
    note_tool_call(name, ok, err or "")
    if err:
        log(f"  → {name}: 执行异常（已隔离）: {err}")
    return base


def make_tool_message(tc_id, inline):
    """构造回灌给模型的 tool 消息。"""
    return {"role": "tool", "tool_call_id": tc_id, "content": inline}
