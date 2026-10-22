# -*- coding: utf-8 -*-
"""harness/agent_service.py —— Agent 服务层（主程序 agent 能力的「适配层」）。

职责（把 bot_core 的接入压到最薄）
  - 会话管理：session_id → 中止标志 / 历史窗口 / 事件订阅
  - system_prompt 组装：本体静态段 + agent 模式快照 + 本体记忆（追加式，保前缀缓存）
  - 工具包装：把 bot_core 的 TOOL_FUNCTIONS 适配成 agent_loop 需要的签名，
    并按场景（只读 / 写 / 执行）**分闸注入**
  - **本体记忆**：system 快照 + 每轮 prefetch + 会话后落沙（走 harness/agent_memory）
  - 群内触发的**双闸**判断：权限闸（_is_authorized）+ 意图闸（明确执行意图）
  - **实时事件总线**：按会话把 agent_loop 的事件广播给订阅者（前端 SSE 用）
  - **单步重跑**：从 session_log 截到某一步，重建 messages 后继续

原则
  - **依赖注入**：bot_core 通过 configure(...) 注入 chat_fn / tool_functions /
    static_prompt_fn / is_authorized_fn / logger，本模块**不做任何 from bot_core import**。
  - agent_loop 负责循环；本模块负责「把 bot_core 的能力喂给它」。
  - 群内 agent **默认关**：配置 agent_group_enable 打开 + 双闸通过才触发。
  - 任何外部依赖（记忆 / 日志 / 注册表）故障都**不向主循环抛出**（DSH A10）。
"""
import os
import re
import json
import time
import threading

from . import agent_loop as _al
from . import session_log as _slog
from . import agent_memory as _amem

# ── 注入点（由 bot_core 在启动时 configure）────────────────────────
_CFG = {
    "chat_fn": None,            # (messages, tools) -> (content, tool_calls, usage)
    "tool_functions": {},        # {name: callable(args)->result}
    "static_prompt_fn": None,    # (is_group: bool) -> str
    "is_authorized_fn": None,    # (sender_qq) -> bool
    "logger": None,
    # 工具 schema 提供者：names -> schema 列表（顺序稳定）。用于 Agent 侧按需加载。
    "tool_schema_provider": None,
    # 工具名解析器：(user_msg, is_group) -> [tool_name, ...]（按场景分闸）
    "tool_picker": None,
    # 会话落沙钩子：(session_id, user_input, answer, meta) -> None
    "on_turn": None,
    # 记忆开关（默认开；config 可关）
    "memory": True,
    # ── agent 页可调的运行设置（get_settings / configure）──────────────
    "max_steps": None,       # None = 用 agent_loop 的默认步数；UI 可改
    "thinking": True,        # 是否把模型思维链（reasoning）发给前端
}

# 会话状态：{session_id: {"abort": bool, "busy": bool, "last": float}}
_SESSIONS = {}
_sess_lock = threading.Lock()
# 会话元数据（跨进程重启由 session_log 持久化；这里只放热态）
_SESS_META = {}

# 事件总线：{session_id: [queue, ...]}，queue 是 list（简单、够用）
_BUS = {}
_bus_lock = threading.Lock()
#: 单会话事件缓冲上限（防前端不消费导致内存无界）
BUS_MAX = 2000

# 会话内存上限（防止长期运行无界增长）
_MAX_SESSIONS = 200
_MAX_META = 500

# agent 工作模式的提示词快照（**追加式**，不改写既有 static_prompt → 保前缀缓存）
AGENT_MODE_SNAPSHOT = (
    "\n\n【Agent 工作模式】\n"
    "你是 EggBloom 本体（QQ bot「小a」）的**执行分身**，现在处于执行模式：任务导向。\n"
    "语气延续你的人格，但不闲聊、不表演；主人交给的事要落地，不要只给建议。\n"
    "\n"
    "▍核心原则\n"
    "1. **真实动作**：需要查资料/找文件/发消息/操作插件时，直接调用对应工具；绝不假装已做。\n"
    "2. **以工具返回为准**：返回是真实结果——成功就是成功，失败就是失败；不编造、不美化。\n"
    "3. **先规划后动手**：预计 ≥3 步的任务，先用一句话列出步骤再逐步执行；不足 3 步直接做。\n"
    "4. **一步一验证**：每个动作后检查返回值是否符合预期，再进行下一步。\n"
    "\n"
    "▍出错与止损\n"
    "5. 失败时先读错误信息（通常含候选位置/下一步建议），换方法再试；同样的调用"
    "**不要原样重试超过 2 次**。\n"
    "6. **知道何时停**：任务完成、被授权卡住、或连续失败时，用简洁自然的话汇报现状、原因"
    "与下一步建议——不要为凑步数空转。\n"
    "\n"
    "▍工具与授权\n"
    "7. 插件能力用 `plugin_list` 发现、`plugin_call` 执行。未启用的插件会**弹授权窗等主人"
    "确认**：如实告诉主人正在等授权即可，不要重试、不要绕过；超时或被拒绝就终止并说明。\n"
    "8. 本地文件用 ws_glob / ws_grep / ws_read / ws_edit（路径相对项目根，要求原文唯一）；"
    "跑代码用 py_run（会话持久，变量跨调用保留，可分步推进）。\n"
    "9. 回忆过往用 `sandglass_search`（主人偏好、历史对话）；不要凭空猜测历史。\n"
    "\n"
    "▍输出\n"
    "10. 结论用简洁自然的中文；不要罗列调用流水账，不要把工具的原始 JSON 丢给主人。\n"
)


def configure(*, chat_fn=None, tool_functions=None, static_prompt_fn=None,
              is_authorized_fn=None, logger=None, tool_schema_provider=None,
              tool_picker=None, on_turn=None, memory=None, memory_bridge=None,
              memory_expose_tools=None, max_steps=None, thinking=None):
    """由 bot_core 注入依赖。任何一项为 None 表示不改动。"""
    if chat_fn is not None:
        _CFG["chat_fn"] = chat_fn
    if tool_functions is not None:
        _CFG["tool_functions"] = tool_functions
    if static_prompt_fn is not None:
        _CFG["static_prompt_fn"] = static_prompt_fn
    if is_authorized_fn is not None:
        _CFG["is_authorized_fn"] = is_authorized_fn
    if logger is not None:
        _CFG["logger"] = logger
    if tool_schema_provider is not None:
        _CFG["tool_schema_provider"] = tool_schema_provider
    if tool_picker is not None:
        _CFG["tool_picker"] = tool_picker
    if max_steps is not None:
        _CFG["max_steps"] = int(max_steps)
    if thinking is not None:
        _CFG["thinking"] = bool(thinking)
    if on_turn is not None:
        _CFG["on_turn"] = on_turn
    if memory is not None:
        _CFG["memory"] = bool(memory)
    # 记忆桥配置（记忆开关 / 是否暴露检索工具）
    try:
        _amem.configure(logger=logger, bridge=memory_bridge,
                        enabled=_CFG["memory"] if memory is None else bool(memory),
                        expose_tools=memory_expose_tools)
    except Exception:
        pass


def _log(msg):
    fn = _CFG.get("logger")
    if fn:
        try:
            fn(msg)
        except Exception:
            pass


def is_ready():
    """依赖是否齐备（chat_fn + tool_functions）。"""
    return bool(_CFG.get("chat_fn")) and bool(_CFG.get("tool_functions"))


# ── 会话管理 ────────────────────────────────────────────────────────
def _new_session_id(prefix="desk"):
    return f"{prefix}-{int(time.time() * 1000)}-{os.getpid()}"


def get_session(session_id):
    with _sess_lock:
        return _SESSIONS.get(session_id)


def request_abort(session_id):
    """请求中止某会话（供 UI 的 Esc / 群内的「停」）。"""
    with _sess_lock:
        s = _SESSIONS.get(session_id)
        if s:
            s["abort"] = True
            return True
    return False


def _should_abort(session_id):
    with _sess_lock:
        s = _SESSIONS.get(session_id)
        return bool(s and s.get("abort"))


def _gc_sessions():
    """按 last 时间清理内存会话（调用方需持锁或容忍竞态）。"""
    if len(_SESSIONS) <= _MAX_SESSIONS:
        return
    oldest = sorted(_SESSIONS.items(), key=lambda kv: kv[1].get("last", 0))
    for k, _ in oldest[:50]:
        _SESSIONS.pop(k, None)


# ── 事件总线（前端 SSE 的数据源）────────────────────────────────────
def subscribe(session_id):
    """订阅某会话的事件流。返回一个 list —— 调用方轮询取走并清空它。

    为什么用 list 而不是 queue.Queue：需要**一个会话多个读者**（页面刷新
    后新连接要能接上，旧连接可能还没断）。list + 取走即清空语义更简单，
    且天然支持"重连后从 backlog 补上"。
    """
    q = []
    with _bus_lock:
        _BUS.setdefault(str(session_id), []).append(q)
        # 防无界：最多 8 个订阅者
        if len(_BUS[str(session_id)]) > 8:
            _BUS[str(session_id)].pop(0)
    return q


def unsubscribe(session_id, q):
    with _bus_lock:
        arr = _BUS.get(str(session_id))
        if arr and q in arr:
            try:
                arr.remove(q)
            except ValueError:
                pass
        if arr is not None and not arr:
            _BUS.pop(str(session_id), None)


def _publish(session_id, ev_type, data):
    """把事件推给所有订阅者。**永不抛**（总线故障不能影响 agent 执行）。"""
    try:
        payload = {"type": ev_type, "data": data, "ts": time.time()}
        with _bus_lock:
            arr = _BUS.get(str(session_id))
            if not arr:
                return
            for q in list(arr):
                if len(q) < BUS_MAX:
                    q.append(payload)
    except Exception:
        pass


def _drain(session_id):
    """内部：取走并清空所有订阅队列（服务端 SSE 循环用）。

    返回 list（可能为空）。**取走即清空** —— 配合前端"一个会话一个连接"，
    避免多读者重复消费。
    """
    out = []
    with _bus_lock:
        for q in _BUS.get(str(session_id), []):
            if q:
                out.extend(q)
                q.clear()
    return out


def edit_history(session_id, seq, *, content=None, op="edit"):
    """消息级撤回/编辑（agent 页用）。

    op="edit" 改该条内容 / "delete" 删该条 / "truncate" 撤回该条及其后。
    改的是 session_log（kind="agent"）→ derive_messages/replay 立即反映。"""
    try:
        return _slog.edit_event("agent", str(session_id), int(seq),
                                content=content, op=str(op or "edit"))
    except Exception as e:
        _log(f"[agent] edit_history 失败: {e}")
        return False, 0


def get_settings():
    """agent 页设置项的当前值（/api/agent/settings GET 用）。"""
    return {
        "memory": bool(_CFG.get("memory", True)),
        "thinking": bool(_CFG.get("thinking", True)),
        "max_steps": int(_CFG.get("max_steps") or 0),   # 0 = 用默认步数
    }


# ── system_prompt 组装 ──────────────────────────────────────────────
def build_system_prompt(is_group=True, extra="", with_memory=True, project_hint=""):
    """组装 agent 的 system_prompt：本体静态段 + **追加**的 agent 模式快照 + 本体记忆 + 项目知识。

    追加式（不改写 static_prompt 本体）→ 保住静态段的前缀缓存（任务 5.1 精神）。
    顺序（**静态在前、动态在后**，最大化可缓存前缀）：
      1. static_prompt（人格/规则）—— 长期稳定
      2. agent 模式快照 —— 长期稳定
      3. Skill L1 清单 —— 半稳定（skill 集合变了才变）
      4. 时效锚（知识截止）—— 长期稳定
      5. **本体记忆快照** —— 缓慢变化（画像/阶段）
      6. **EggBloom 项目知识** —— 仅在 project_hint 命中项目话题时出现
      7. extra（调用方给的本轮上下文提示）

    project_hint：用于判断是否注入项目知识的那句话（通常是本轮用户输入）。
                  不传则不注入 —— 闲聊场景零成本。
    """
    base = ""
    fn = _CFG.get("static_prompt_fn")
    if fn:
        try:
            base = fn(bool(is_group)) or ""
        except Exception as e:
            _log(f"[agent] static_prompt 组装失败: {e}")
            base = ""
    p = base + AGENT_MODE_SNAPSHOT
    # Skill L1：常驻清单（约 50~100 token/skill；无 skill 时为空串，零成本）
    try:
        from . import bot_hook as _bh
        _sk1 = _bh.skill_l1_block()
        if _sk1:
            p += "\n\n" + _sk1
    except Exception:
        pass
    # 时效锚（知识截止 + 日期基准）：**长期事实**，放这里能长期命中前缀缓存。
    # 这是"不让 AI 拿过时信息当事实回答"的第一道护栏 —— 让模型知道自己的知识边界。
    try:
        from . import freshness as _fr
        _gap = _fr.knowledge_cutoff_gap()
        if _gap:
            p += "\n\n" + _gap
    except Exception:
        pass
    # 本体记忆（沙漏条数/数据根）—— 缓慢变化，仍在静态段。
    # 动态部分（偏移/情绪）走 prefetch 进 user 同轮，见 run_chat。
    if with_memory and _CFG.get("memory", True):
        try:
            snap = _amem.system_snapshot()
            if snap:
                p += snap
        except Exception as e:
            _log(f"[agent] 本体记忆快照失败（已跳过）: {e}")
    # ── EggBloom 项目知识（识别到项目话题才注入；否则空串、零成本）──
    #  位置刻意放在**静态段最后**（记忆之后、extra 之前）：
    #  它只在项目类问题出现时才存在，属于"半稳定"——放太靠前会让
    #  "上一轮不问项目、这轮问项目"时前缀整体位移，白丢缓存。
    try:
        from . import agent_project as _ap
        _pb = _ap.project_block(project_hint)
        if _pb:
            p += _pb
    except Exception as e:
        _log(f"[agent] 项目知识注入失败（已跳过）: {e}")
    if extra:
        p += "\n\n" + extra
    return p


# ── 工具集组装（分闸）───────────────────────────────────────────────
def _resolve_tool_names(user_input, is_group):
    """按场景挑工具名。优先用 bot_core 注入的 tool_picker（与主链同一套闸门），
    没有就退回"全部工具"。返回值可能为空 → 此时由调用方退回全量。
    """
    picker = _CFG.get("tool_picker")
    if picker is None:
        return None
    try:
        names = picker(str(user_input or ""), bool(is_group)) or []
        return [str(n) for n in names if n]
    except Exception as e:
        _log(f"[agent] tool_picker 失败，退回全量: {e}")
        return None


def _tools_for(user_input, is_group, extra_tools=None):
    """返回 (tool_functions, tool_names_or_None)。

    tool_names=None 表示"用全量"（schema 由 provider 或名字列表决定）。
    """
    all_fns = dict(_CFG["tool_functions"])
    # ★ MCP：把"用户已加载"的 MCP 工具**函数体**并进来（tool_picker 已给出
    #   名字，但执行要有函数体；这里动态取，避免依赖跨线程修改 _agent_fns）。
    #   mcp_hub.tool_functions(lazy=True) 只返回 _TOOL_INDEX 里已有的（=已缓存
    #   tools/list 的 server），未加载的零成本、绝不起子进程。
    try:
        from . import mcp_hub as _mcp
        all_fns.update(_mcp.tool_functions())
    except Exception:
        pass
    # 本体记忆检索工具（只读，常驻）
    if _CFG.get("memory", True):
        try:
            all_fns.update(_amem.memory_tools())
        except Exception:
            pass
    picked = _resolve_tool_names(user_input, is_group)
    if picked is None:
        return all_fns, None
    if extra_tools:
        for t in extra_tools:
            if t not in picked:
                picked.append(t)
    # 记忆工具始终可用（只读、便宜、不注入等于白做）
    if _CFG.get("memory", True):
        for n in ("sandglass_search", "sandglass_recent"):
            if n in all_fns and n not in picked:
                picked.append(n)
    return all_fns, picked


def _schemas_for(names):
    """把名字列表解析成 schema 列表（顺序稳定）；失败返回 None。"""
    provider = _CFG.get("tool_schema_provider")
    if provider is None:
        return None
    try:
        if names is None:
            names = list(_CFG["tool_functions"].keys())
        return list(provider(list(names)) or []) or None
    except Exception as e:
        _log(f"[agent] tool_schema_provider 失败，退回名字列表: {e}")
        return None


# ── 主入口：跑一次 agent 对话 ───────────────────────────────────────
def run_chat(*, user_input, session_id=None, is_group=False, context_hint="",
             on_event=None, max_steps=None, history=None, extra_tools=None,
             use_memory=True, publish=True):
    """跑一次 agent 对话（供主程序 Agent 页与群内共用）。

    返回 AgentResult.to_dict()（额外带 session_id / elapsed）。
    """
    if not is_ready():
        return {"ok": False, "error": "agent 未就绪（依赖未注入）", "answer": "",
                "steps": 0, "trace": [], "session_id": session_id or ""}

    if session_id is None:
        session_id = _new_session_id()
    session_id = str(session_id)
    # ★ 多轮连续性（2026-10-10 用户实测："agent 思考没接上我的话"）：
    #   此前 history 参数默认 None 且**没有任何调用方传值** —— 每一轮都是
    #   全新对话，上一轮"需要先启用 qzone 插件"的话根本不带进上下文，
    #   主人启用后回一句"启用了"，模型只能猜"启用了啥？"。
    #   会话日志里其实每轮 user_msg/final 都在（derive_history 现成），
    #   补上自动派生：显式传 history 的调用（rerun）优先，其余按会话回读。
    if history is None:
        try:
            history = _al.derive_history(session_id, limit=24) or None
            # 截断可能把开头的 user 切掉 → 以 assistant 开头不合消息序惯例，
            # 从头部丢弃到第一个 user 为止。
            while history and history[0].get("role") != "user":
                history = history[1:]
            history = history or None
        except Exception:
            history = None
    # ★ 创作管线轮上下文：plugin_forge 工具据此区分 desk/group 来源并写对会话日志
    #   （同线程执行 → thread-local 可靠；失败绝不影响主流程）
    try:
        from . import plugin_forge as _pf0
        _pf0.set_turn_context(session_id=session_id, is_group=is_group)
    except Exception:
        pass
    t0 = time.time()

    with _sess_lock:
        _SESSIONS[session_id] = {"abort": False, "busy": True, "last": t0}
        _gc_sessions()
    _SESS_META[session_id] = {
        "session_id": session_id, "is_group": bool(is_group),
        "started": _SESS_META.get(session_id, {}).get("started", t0),
        "updated": t0, "turns": int(_SESS_META.get(session_id, {}).get("turns", 0)) + 1,
    }
    if len(_SESS_META) > _MAX_META:
        for k in sorted(_SESS_META, key=lambda x: _SESS_META[x].get("updated", 0))[:100]:
            _SESS_META.pop(k, None)

    # ── 组装 system prompt（含本体记忆快照 + 按需的项目知识）──
    with_mem = bool(use_memory) and _CFG.get("memory", True)
    system_prompt = build_system_prompt(is_group=is_group, extra=context_hint,
                                        with_memory=with_mem,
                                        project_hint=str(user_input or ""))

    # ── 本轮动态：时效指令 + 本体 prefetch，都放 user 同轮（保前缀缓存）──
    _user_input = str(user_input or "")
    try:
        from . import freshness as _fr
        from . import tool_registry as _trg
        _has_search = _trg.has("search_web")
        _dir = _fr.freshness_directive(_user_input, has_search=_has_search)
        if _dir:
            _user_input = _dir + "\n\n" + _user_input
    except Exception:
        pass
    if with_mem:
        try:
            _pf = _amem.prefetch(_user_input)
            if _pf:
                _user_input = _pf + "\n" + _user_input
        except Exception:
            pass

    # ── 工具集（分闸）──
    _fns, _names = _tools_for(_user_input, is_group, extra_tools)
    _schemas = _schemas_for(_names)

    # ── 事件桥：外部 on_event + 内部总线（前端 SSE）双投递 ──
    def _bus_emit(ev_type, data):
        if publish:
            _publish(session_id, ev_type, data)
        if on_event:
            try:
                on_event(ev_type, data)
            except Exception as e:
                _log(f"[agent] on_event 回调异常（已隔离）: {e}")

    kwargs = dict(
        session_id=session_id,
        user_input=_user_input,
        chat_fn=_CFG["chat_fn"],
        tool_functions=_fns,
        system_prompt=system_prompt,
        history=history,
        on_event=_bus_emit,
        should_abort=lambda: _should_abort(session_id),
        logger=_log,
        tool_schemas=_schemas,
        # 记录用户原话，别把注入的时效指令/prefetch 写进历史与回放
        display_input=str(user_input or ""),
        # agent 页设置：思维链是否发给前端（默认开）
        thinking_events=bool(_CFG.get("thinking", True)),
    )
    if max_steps:
        kwargs["max_steps"] = int(max_steps)
    elif _CFG.get("max_steps"):
        kwargs["max_steps"] = int(_CFG["max_steps"])

    if publish:
        _publish(session_id, "start", {"input": str(user_input or ""),
                                       "is_group": bool(is_group)})

    try:
        res = _al.run_agent(**kwargs)
        out = res.to_dict()
        out["session_id"] = session_id
        out["elapsed"] = round(time.time() - t0, 2)
        out["tools_offered"] = len(_schemas) if _schemas else (
            len(_names) if _names else len(_fns))
        # ── 落沙（本体记忆）：成功/失败都记，失败也要能被回忆起来 ──
        if with_mem and str(out.get("answer") or "").strip():
            try:
                _rec = _amem.sync_turn(
                    user_msg=str(user_input or ""),
                    assistant_msg=str(out.get("answer") or ""),
                    scope=("group" if is_group else "agent"),
                    scope_id=_session_scope_id(session_id),
                    extra={"session_id": session_id},
                )
                out["memory_synced"] = bool(_rec.get("ok"))
            except Exception:
                pass
        # ── 外部 on_turn 钩子（bot_core 可用来做额外记账）──
        fn_turn = _CFG.get("on_turn")
        if fn_turn:
            try:
                fn_turn(session_id, str(user_input or ""), str(out.get("answer") or ""),
                        {"is_group": bool(is_group), "ok": out.get("ok")})
            except Exception as e:
                _log(f"[agent] on_turn 钩子异常（已隔离）: {e}")
        if publish:
            _publish(session_id, "done", {"ok": out.get("ok"),
                                          "steps": out.get("steps"),
                                          "elapsed": out.get("elapsed"),
                                          "error": out.get("error")})
        return out
    finally:
        with _sess_lock:
            s = _SESSIONS.get(session_id)
            if s:
                s["busy"] = False
        if session_id in _SESS_META:
            _SESS_META[session_id]["updated"] = time.time()


def _session_scope_id(session_id):
    """群会话 id 形如 ``grp-<群号>-<ts>`` → 提取群号；其余返回空（走私聊记忆）。"""
    s = str(session_id or "")
    m = re.match(r"^grp-(\d+)", s)
    return m.group(1) if m else ""


# ── 单步重跑（从 session_log 截断后继续）───────────────────────────
def rerun_from(*, session_id, from_step, user_input=None, is_group=False,
               context_hint="", on_event=None, max_steps=None):
    """从某个 step 之后重跑（设计文档 §5「可以"从某一步重来"」）。

    实现要点（**充分利用事件溯源**）：
      - 读该会话的完整事件日志，重建成 messages；
      - **丢掉 step >= from_step 的工具往返**，只保留之前的上下文；
      - 用重建的 history + （可选）新的 user_input 重新进入 agent 循环。

    from_step 语义：第 1 个工具调用所在轮 = step 1。传 1 = 从第一个工具前重来。
    """
    if not is_ready():
        return {"ok": False, "error": "agent 未就绪（依赖未注入）", "answer": "",
                "steps": 0, "trace": [], "session_id": session_id}
    sid = str(session_id or "")
    if not sid:
        return {"ok": False, "error": "缺少 session_id", "answer": "", "steps": 0,
                "trace": [], "session_id": ""}
    try:
        fstep = int(from_step)
    except (TypeError, ValueError):
        return {"ok": False, "error": "from_step 必须是整数", "answer": "", "steps": 0,
                "trace": [], "session_id": sid}

    hist, last_user = _rebuild_history(sid, keep_steps_before=fstep)
    # 重跑时的 user_input：显式给就用显式，否则用该会话最后一次输入
    ui = str(user_input) if user_input else (last_user or "")
    if not ui:
        return {"ok": False, "error": "没有可重跑的输入（会话里找不到用户消息）",
                "answer": "", "steps": 0, "trace": [], "session_id": sid}
    _publish(sid, "start", {"input": ui, "rerun_from": fstep})
    out = run_chat(user_input=ui, session_id=sid, is_group=is_group,
                   context_hint=context_hint, on_event=on_event,
                   max_steps=max_steps, history=hist or None)
    out["rerun_from"] = fstep
    out["history_rebuilt"] = len(hist or [])
    return out


def _rebuild_history(session_id, *, keep_steps_before=None, limit_turns=12):
    """从事件日志重建 messages（可指定"保留到第几步之前"）。

    事件里 tool_call/tool_result 的 payload 带 step 字段（见 agent_loop）。
    user/final 事件无 step —— 它们属于"轮"层。这里按事件顺序累积：
      - user → 入 messages
      - final → 入 messages（assistant）
      - tool_call/tool_result → **只用于判定 step 边界**，不入 messages
        （因为 agent_loop 内部会把它们回灌；重建时重跑会自己再产生一遍）

    返回 (messages, last_user_text)。
    """
    out = []
    last_user = ""
    try:
        events = _slog.events_of("agent", session_id)
    except Exception:
        return out, last_user
    for ev in events:
        t = ev.get("type")
        p = ev.get("payload") or {}
        if t == _al.EV_TOOL_CALL or t == _al.EV_TOOL_RESULT:
            if keep_steps_before is not None:
                try:
                    if int(p.get("step") or 0) >= int(keep_steps_before):
                        # 到达截断点 → 停止累积（保留之前的部分）
                        break
                except (TypeError, ValueError):
                    pass
            continue
        if t == _al.EV_USER:
            c = p.get("content")
            if c:
                out.append({"role": "user", "content": c})
                last_user = c
        elif t == _al.EV_FINAL:
            c = p.get("content")
            if c:
                out.append({"role": "assistant", "content": c})
    # 只要最近的若干轮（避免历史过长把成本打爆）
    if limit_turns and len(out) > limit_turns * 2:
        out = out[-(limit_turns * 2):]
    return out, last_user


# ── 群内触发：双闸 ──────────────────────────────────────────────────

# 闸二「意图」：显式强触发词（兜底），以及执行动词组合
_STRONG_TRIGGER_RE = re.compile(
    r'(?:^|[\s,，:：])*(?:agent|干活|执行任务|去执行|跑一下任务|帮我做|去做(?:这个|这件事))\b',
    re.IGNORECASE,
)
# 执行动词：表示"要真的做一件事"，而非闲聊。
#
# 收紧策略（避免误触闲聊，同时不漏真指令）：
#   形态 A（祈使，动词开头）：动词必须在**句首**（可带少量语气前缀），
#          如「打开微信」「查一下天气」「帮我把桌面截图发我」。
#   形态 B（人称委托）：帮/给/替/请/麻烦 + 少量字 + 动词，
#          如「小a 帮我去查一下」。
#   形态 C（处置式）：把/将 + 少量字 + 动词，如「把桌面上的报告发给我」
#          —— 中文里最常见的委托句式，原实现完全漏掉。
# 明确排除：主语是「我」「你」且带「了/过/吗」的陈述/疑问句（"我打开了一本书"）。
_ACTION_VERBS = (
    r'(?:查|搜|搜索|找|下载|上传|打开|开启|关掉|关闭|截图|截个图|'
    r'填|操作|登录|注册|买|订|整理|统计|汇总|监控|定时|'
    r'传[^。！？!?\n]{0,6}?(?:文件|群里|群文件|给我|给他)|'
    # 「发」必须**带接收方**才算指令：「发个表情包」是闲聊，「发给我」才是委托
    r'发[^。！？!?\n]{0,6}?(?:我|他|她|群里|群文件|过来|一下)|'
    r'写|运行|跑|安装|部署|编译|生成|改|删|移动|复制|重命名|压缩|解压|转成|转换成|'
    r'看(?:一下|看)\s*(?:屏幕|桌面|电脑)|执行)'
)
_ACTION_VERB_RE = re.compile(
    # 形态 B：人称委托
    r'(?:帮|给|替|请|麻烦)[^。！？!?\n]{0,8}?' + _ACTION_VERBS +
    r'|'
    # 形态 C：处置式「把/将 + … + 动词」（中文最常见的委托句式）
    r'(?:把|将)[^。！？!?\n]{0,12}?' + _ACTION_VERBS +
    r'|'
    # 形态 A：动词在句首（允许前面有 @昵称 / 少量语气词）
    r'(?:^|[\s,，:：@]|小a\s*|干活[：:]\s*)'
    r'(?:把|将|去|先|再|帮我|给我|替我|请|麻烦)?\s*'
    + _ACTION_VERBS
)
# 陈述/疑问句排除（主语是「我/你」且**动词后紧随完成/疑问标记**）
_STATEMENT_RE = re.compile(
    r'(?:^|[\s,，])(?:我|你|他|她|它)[^。！？!?\n]{0,6}?'
    r'(?:打开|查|搜|找|用过|打开过|看过)[了过吗嘛呢]'
)
# 明确排除的闲聊（避免误触）
_CHITCHAT_RE = re.compile(
    r'(你(?:在|在干|干嘛|在做什么)|干嘛呢|干什么呢|在吗|睡了吗|吃饭了吗|'
    r'你觉得|你认为|怎么想|心情|难受|开心|无聊|晚安|早安)'
)


def detect_intent(text):
    """闸二：判断文本是否是**明确的执行意图**（要真的干活），返回 (bool, reason)。"""
    t = (text or "").strip()
    if not t:
        return False, "空消息"
    if len(t) > 500:
        return False, "过长"
    # 闲聊优先排除
    if _CHITCHAT_RE.search(t) and not _STRONG_TRIGGER_RE.search(t):
        return False, "疑似闲聊"
    # 陈述/疑问句排除（"我打开了一本书" / "你查过吗"），强触发词可豁免
    if _STATEMENT_RE.search(t) and not _STRONG_TRIGGER_RE.search(t):
        return False, "疑似陈述/疑问"
    if _STRONG_TRIGGER_RE.search(t):
        return True, "强触发词"
    if _ACTION_VERB_RE.search(t):
        return True, "执行动词"
    return False, "无明确执行意图"


def group_gate(*, text, sender, is_group=True):
    """群内 agent 双闸判断。返回 (allowed: bool, reason: str)。

    闸一 · 权限：仅 _is_authorized（管理员白名单）。**先判权限再判意图**，
    权限不足直接拒（连意图都不解析），避免探测。
    """
    auth_fn = _CFG.get("is_authorized_fn")
    if auth_fn is None:
        return False, "权限校验未注入"
    try:
        if not auth_fn(str(sender)):
            return False, "权限不足"
    except Exception as e:
        return False, f"权限校验异常: {e}"

    intent_ok, why = detect_intent(text)
    if not intent_ok:
        return False, why
    return True, why


# ── 供 UI 读取的会话轨迹 ───────────────────────────────────────────
def history_of(session_id, limit=None):
    """从 session_log 派生该会话的对话历史（可回放）。"""
    return _al.derive_history(session_id, limit=limit)


def trace_of(session_id, limit=None):
    """返回完整轨迹事件（供前端重建执行轨迹卡）。"""
    return _al.replay(session_id, limit=limit)


def list_sessions(kind="desk", include_disk=True, limit=None):
    """列出会话：内存热态 + 磁盘日志（**刷新页面不丢**）。

    返回按 updated 倒序的列表；每条带 source=("live"/"disk")。
    """
    out = []
    seen = set()
    with _sess_lock:
        for sid, meta in _SESS_META.items():
            if kind and not sid.startswith(kind):
                continue
            seen.add(sid)
            s = _SESSIONS.get(sid) or {}
            out.append({
                "session_id": sid,
                "is_group": bool(meta.get("is_group")),
                "busy": bool(s.get("busy")),
                "turns": meta.get("turns", 0),
                "updated": meta.get("updated") or s.get("last") or 0,
                "source": "live",
            })
    if include_disk:
        try:
            for rec in _slog.list_ids("agent", limit=limit or 100):
                sid = rec["session_id"]
                if sid in seen:
                    continue
                if kind and not sid.startswith(kind):
                    continue
                out.append({
                    "session_id": sid,
                    "is_group": sid.startswith("grp-"),
                    "busy": False,
                    "turns": None,
                    "updated": rec.get("mtime", 0),
                    "bytes": rec.get("bytes", 0),
                    "source": "disk",
                })
        except Exception:
            pass
    out.sort(key=lambda x: x.get("updated") or 0, reverse=True)
    if limit:
        out = out[:int(limit)]
    return out


def delete_session(session_id):
    """删除一个会话（内存 + 磁盘日志）。"""
    sid = str(session_id or "")
    if not sid:
        return False
    with _sess_lock:
        _SESSIONS.pop(sid, None)
    _SESS_META.pop(sid, None)
    with _bus_lock:
        _BUS.pop(sid, None)
    try:
        return _slog.delete("agent", sid)
    except Exception:
        return False


def memory_status():
    """本体记忆状态（Agent 页顶部显示）。"""
    if not _CFG.get("memory", True):
        return {"available": False, "reason": "记忆已在配置里关闭"}
    try:
        return _amem.status()
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


def tool_groups():
    """返回工具注册表的分组统计（Agent 页显示"可用能力"）。"""
    try:
        from . import tool_registry as _trg
        return _trg.stats()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
