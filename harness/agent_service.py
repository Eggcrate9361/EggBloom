# -*- coding: utf-8 -*-
"""harness/agent_service.py —— Agent 服务层（主程序 agent 能力的「适配层」）。

职责（把 bot_core 的接入压到最薄）
  - 会话管理：session_id → 中止标志 / 历史窗口
  - system_prompt 组装：复用 bot_core 现有静态段 + 追加「agent 工作模式」快照
  - 工具包装：把 bot_core 的 TOOL_FUNCTIONS 适配成 agent_loop 需要的签名
  - 群内触发的**双闸**判断：权限闸（_is_authorized）+ 意图闸（明确执行意图）

原则
  - **依赖注入**：bot_core 通过 configure(...) 注入 chat_fn / tool_functions /
    static_prompt_fn / is_authorized_fn / logger，本模块**不做任何 from bot_core import**。
  - agent_loop 负责循环；本模块负责「把 bot_core 的能力喂给它」。
  - 群内 agent **默认关**：配置 agent_group_enable 打开 + 双闸通过才触发。
"""
import os
import re
import json
import time
import threading

from . import agent_loop as _al
from . import session_log as _slog

# ── 注入点（由 bot_core 在启动时 configure）────────────────────────
_CFG = {
    "chat_fn": None,            # (messages, tools) -> (content, tool_calls, usage)
    "tool_functions": {},        # {name: callable(args)->result}
    "static_prompt_fn": None,    # (is_group: bool) -> str
    "is_authorized_fn": None,    # (sender_qq) -> bool
    "logger": None,
    # 工具 schema 提供者：names -> schema 列表（顺序稳定）。用于 Agent 侧按需加载。
    "tool_schema_provider": None,
}

# 会话状态：{session_id: {"abort": bool, "busy": bool, "last": float}}
_SESSIONS = {}
_sess_lock = threading.Lock()

# agent 工作模式的提示词快照（**追加式**，不改写既有 static_prompt → 保前缀缓存）
AGENT_MODE_SNAPSHOT = (
    "\n\n【Agent 工作模式】你现在处于**执行模式**，而不是闲聊模式。规则：\n"
    "1. 需要真实操作（查资料、开应用、看屏幕、找文件、操作网页等）时，"
    "直接调用对应工具，不要用语言假装已做。\n"
    "2. 工具返回是**真实结果**，以它为准；失败就如实说失败并说明原因。\n"
    "3. 一次只推进一件事：先想清楚这一步要调什么工具，拿到结果再决定下一步。\n"
    "4. 任务完成或无法继续时，用**简洁自然**的话给出结论，不要罗列调用流水账。\n"
    "5. 不要输出工具调用的原始 JSON 给用户看。\n"
)


def configure(*, chat_fn=None, tool_functions=None, static_prompt_fn=None,
              is_authorized_fn=None, logger=None, tool_schema_provider=None):
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


def build_system_prompt(is_group=True, extra=""):
    """组装 agent 的 system_prompt：bot_core 现有静态段 + **追加**的 agent 模式快照。

    追加式（不改写 static_prompt 本体）→ 保住静态段的前缀缓存（任务 5.1 精神）。
    Skill 的 L1 元数据（极省）也在此追加——**只给清单**，正文靠 skill__load 按需载入。
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
    if extra:
        p += "\n\n" + extra
    return p


def run_chat(*, user_input, session_id=None, is_group=False, context_hint="",
             on_event=None, max_steps=None, history=None, extra_tools=None):
    """跑一次 agent 对话（供主程序 Agent 页与群内共用）。

    返回 AgentResult.to_dict()。
    """
    if not is_ready():
        return {"ok": False, "error": "agent 未就绪（依赖未注入）", "answer": "",
                "steps": 0, "trace": []}

    if session_id is None:
        session_id = _new_session_id()

    with _sess_lock:
        _SESSIONS[session_id] = {"abort": False, "busy": True, "last": time.time()}
        # 简单的会话上限，避免无界增长
        if len(_SESSIONS) > 200:
            oldest = sorted(_SESSIONS.items(), key=lambda kv: kv[1].get("last", 0))[:50]
            for k, _ in oldest:
                _SESSIONS.pop(k, None)

    system_prompt = build_system_prompt(is_group=is_group, extra=context_hint)

    # 工具按需加载：优先走注册表（顺序稳定 + 只在需要时给），失败则全量退回。
    _tool_names = list(_CFG["tool_functions"].keys())
    _provider = _CFG.get("tool_schema_provider")
    _schemas = None
    if _provider is not None:
        try:
            _schemas = list(_provider(_tool_names) or [])
        except Exception as e:
            _log(f"[agent] tool_schema_provider 失败，退回名字列表: {e}")
            _schemas = None

    kwargs = dict(
        session_id=session_id,
        user_input=user_input,
        chat_fn=_CFG["chat_fn"],
        tool_functions=_CFG["tool_functions"],
        system_prompt=system_prompt,
        history=history,
        on_event=on_event,
        should_abort=lambda: _should_abort(session_id),
        logger=_log,
        tool_schemas=_schemas,
    )
    if max_steps:
        kwargs["max_steps"] = int(max_steps)
    if extra_tools:
        kwargs["extra_tools"] = extra_tools

    try:
        res = _al.run_agent(**kwargs)
        out = res.to_dict()
        out["session_id"] = session_id
        return out
    finally:
        with _sess_lock:
            s = _SESSIONS.get(session_id)
            if s:
                s["busy"] = False


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
# 明确排除：主语是「我」「你」且带「了/过/吗」的陈述/疑问句（"我打开了一本书"）。
_ACTION_VERBS = (
    r'(?:查|搜|搜索|找|下载|上传|打开|开启|关掉|关闭|截图|截个图|'
    r'填|操作|登录|注册|买|订|整理|统计|汇总|监控|定时|'
    r'传[^。！？!?\n]{0,6}?(?:文件|群里|群文件|给我|给他)|'
    r'看(?:一下|看)\s*(?:屏幕|桌面|电脑)|执行)'
)
_ACTION_VERB_RE = re.compile(
    # 形态 B：人称委托
    r'(?:帮|给|替|请|麻烦)[^。！？!?\n]{0,8}?' + _ACTION_VERBS +
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


def list_sessions(kind="desk"):
    """列出内存中的会话（诊断用）。"""
    with _sess_lock:
        return [{"session_id": k, "busy": v.get("busy"), "last": v.get("last")}
                for k, v in _SESSIONS.items() if k.startswith(kind)]
