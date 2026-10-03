# -*- coding: utf-8 -*-
"""harness/agent_memory.py —— Agent 的**本体记忆桥**。

设计依据：docs/plan/agent-design.md §7「本体记忆层（共享，不分家）」。
用户在需求里明确：**记忆走本体（super_memory + sandglass 全套）**，
所以 agent 不允许自己另起一套记忆，必须接到既有本体上。

本模块是**适配层**，只做四件事：
  1. ``system_snapshot()``  —— 把沙漏本体注入 system prompt（长期稳定，可命中前缀缓存）
  2. ``prefetch()``         —— 每轮对话前注入**最动态**的信号（偏移/情绪），放 user 同轮
  3. ``sync_turn()``        —— 会话后落沙（走 sandglass_bridge.memory_feed 统一分发入口）
  4. ``memory_tools()``     —— 把沙漏的检索能力暴露成 agent 可调工具

**依赖方向（重要）**：
  本模块**不 import bot_core**，只依赖仓库根上的 ``sandglass_bridge`` 与
  ``nexsandglass``。这两个是**本体**（不是插件），任何 agent 入口（主程序页 /
  群内 / 未来 CLI）都应共享同一份记忆。

**降级策略（DSH A10：绝不向主循环抛异常）**：
  sandglass 未初始化 / 导入失败 / 任何内部异常 → 本模块一律返回空串或空列表，
  并在日志里留一行，**绝不让记忆故障变成对话故障**。
  这与 bot_core 既有做法一致（sandglass_bridge 本身就是"非阻塞懒加载"）。
"""
from __future__ import annotations

import json
import threading
import time

# ── 注入点（由 bot_core / 测试在启动时 configure）───────────────────
_CFG = {
    "logger": None,
    # 记忆后端：默认走仓库根的 sandglass_bridge（本体）
    "bridge": None,
    # 记忆开关：可用 config.memory_enable 关掉（关掉后本模块全部为 no-op）
    "enabled": True,
    # 是否把沙漏检索暴露成 agent 工具（默认开；关掉只影响工具，不影响注入）
    "expose_tools": True,
}

_LOCK = threading.Lock()
# 探测结果缓存：{"ok": bool, "reason": str, "at": float}
_PROBE = {"ok": None, "reason": "", "at": 0.0, "transient": False}
# 就绪后缓存久（30s）；**未就绪时只缓存 1.5s** —— 沙漏是后台线程异步加载的，
# 启动后头几秒"还没好"是常态，缓存太久会让 agent 页一直显示"不可用"。
_PROBE_TTL_OK = 30.0
_PROBE_TTL_FAIL = 1.5


def configure(*, logger=None, bridge=None, enabled=None, expose_tools=None):
    """由 bot_core 注入依赖。任一为 None 表示不改动。"""
    if logger is not None:
        _CFG["logger"] = logger
    if bridge is not None:
        _CFG["bridge"] = bridge
    if enabled is not None:
        _CFG["enabled"] = bool(enabled)
    if expose_tools is not None:
        _CFG["expose_tools"] = bool(expose_tools)
    with _LOCK:
        _PROBE["ok"] = None        # 配置变了，探测缓存作废
        _PROBE["at"] = 0.0


def _log(msg):
    fn = _CFG.get("logger")
    if fn:
        try:
            fn(msg)
        except Exception:
            pass


def _bridge():
    """取记忆桥。未注入时**惰性**导入本体（sandglass_bridge 在本体根目录）。

    顺带**触发本体的懒初始化**：sandglass_bridge 的后台加载线程只由
    ``_ensure_init()`` 拉起，而全仓只有 bot_core 在一处调它（主程序启动时）。
    如果 agent 页在 bot 未启动前就被打开（或测试环境），沙漏会永远是
    "未就绪"。这里补一次触发 —— 幂等、非阻塞，不会重复起线程。
    """
    b = _CFG.get("bridge")
    if b is None:
        try:
            import sandglass_bridge as _sb
            b = _sb
        except Exception as e:
            _log(f"[agent-mem] 记忆桥不可用: {e}")
            return None
    # 触发懒初始化（幂等；已就绪时是纯内存判断）
    try:
        if hasattr(b, "_ensure_init"):
            b._ensure_init()
    except Exception:
        pass
    return b


def is_available(force=False):
    """记忆是否真的可用（探测结果带 TTL 缓存，避免每轮都去探一遍）。

    ★ 关键区分——"**还在加载**"和"**真的没有**"是两回事：

      · ``_init_attempted=True`` 但 ``_initialized=False``
        → 后台线程正在加载（典型耗时 0.3~2s）。这是**瞬时状态**，
          绝不能缓存成 False：否则启动后头 1.5 秒里每一轮 agent 都会
          被告知"记忆不可用"，而记忆其实马上就要好了。
      · ``_init_attempted=False``
        → 从没试过。尝试拉一次，同样是瞬时状态，不缓存。
      · 明确抛异常 / 桥装不上 / 配置关闭
        → 这才是**稳定事实**，缓存住，别每轮重试。

    实测依据：运行副本冷启动时 ``is_available()`` 先返回 False（约
    0.5s 后才 True）。旧实现把这个 False 缓存 1.5s，导致 agent 在启动
    窗口内检索记忆永远拿到"不可用"。
    """
    if not _CFG.get("enabled", True):
        return False
    now = time.time()
    with _LOCK:
        if not force and _PROBE["ok"] is not None and not _PROBE.get("transient"):
            ttl = _PROBE_TTL_OK if _PROBE["ok"] else _PROBE_TTL_FAIL
            if (now - _PROBE["at"]) < ttl:
                return bool(_PROBE["ok"])
    ok, reason, transient = False, "", False
    try:
        b = _bridge()
        if b is None:
            ok, reason = False, "桥不可导入"
        else:
            # sandglass_bridge.is_available() 是纯内存判断，很便宜
            ok = bool(b.is_available())
            if not ok:
                # 判断是"还在加载"还是"真的起不来"
                attempted = bool(getattr(b, "_init_attempted", False))
                initialized = bool(getattr(b, "_initialized", False))
                if attempted and not initialized:
                    transient = True
                    reason = "沙漏正在后台加载"
                elif not attempted:
                    transient = True
                    reason = "沙漏尚未开始初始化"
                else:
                    reason = "沙漏初始化失败（provider 不可用）"
    except Exception as e:
        ok, reason = False, f"{type(e).__name__}: {e}"
    with _LOCK:
        _PROBE.update({"ok": ok, "reason": reason, "at": now, "transient": transient})
    return ok


def status():
    """给前端「沙漏时间轴」用的一行状态（永不抛）。"""
    try:
        b = _bridge()
        if b is None:
            return {"available": False, "reason": "记忆桥不可导入"}
        if not _CFG.get("enabled", True):
            return {"available": False, "reason": "记忆已按配置关闭"}
        st = b.get_status() or {}
        out = dict(st)
        out["available"] = (st.get("status") == "active")
        if not out["available"] and not out.get("reason"):
            out["reason"] = st.get("error") or "沙漏未就绪"
        return out
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


def _ready_bridge(timeout=1.2):
    """取一个**已就绪**的桥；若还在后台加载则短暂等待。

    为什么需要"等"：``_bridge()`` 只在**第一次**调用时拉起加载线程，随后
    立刻返回；此时 ``is_available()`` 还是 False。如果直接用它的返回值判
    "记忆不可用"，那**打开页面的头一次调用**必然拿到"不可用"——即使记忆
    在 0.3 秒后就绪。实测运行副本冷启动约 0.5s 完成初始化。

    等待是**有界**的（默认 1.2s），且只在"确实在加载中"时才等；
    真起不来（attempted 但 provider 坏）立即返回，不拖慢对话。
    """
    b = _bridge()
    if b is None:
        return None
    try:
        if b.is_available():
            return b
    except Exception:
        pass
    # 只在"正在后台加载"时等；其它情况不浪费时间
    attempted = bool(getattr(b, "_init_attempted", False))
    initialized = bool(getattr(b, "_initialized", False))
    if initialized or not attempted:
        return b            # 要么已就绪、要么明确失败 → 交给调用方处理
    deadline = time.time() + max(0.0, float(timeout))
    while time.time() < deadline:
        time.sleep(0.05)
        try:
            if b.is_available():
                return b
        except Exception:
            return b
    return b


# ── 1. system prompt 本体快照（长期稳定 → 命中前缀缓存）──────────────
def system_snapshot(max_chars=1200):
    """把沙漏本体（你是谁 / 往哪走 / 有哪些人）注入 system prompt。

    **为什么放 system 而不是 user**：这些是**长期事实**（人格画像、阶段、
    人物关系），几乎不随轮次变化 → 放在 system 段能长期命中前缀缓存。
    而"本轮要不要核实"这种**本轮信息**放 user 同轮（见 prefetch）。

    max_chars 是硬闸：沙漏的画像块可能很长（V2.9.8 四层问答式），
    注入 agent 时截断到预算内，避免把 system prompt 撑爆。
    """
    if not _CFG.get("enabled", True):
        return ""
    try:
        b = _ready_bridge()
        if b is None or not b.is_available():
            return ""
        # light=False：agent 是"干活"场景，需要完整画像上下文（不是私聊闲聊）
        block = b.get_memory_prompt_block("", light=False) or ""
        block = block.strip()
        if not block:
            return ""
        if len(block) > max_chars:
            # 按段落边界截断（避免把一句话切成两半）
            cut = block[:max_chars].rfind("\n\n")
            block = block[:cut] if cut > max_chars * 0.3 else block[:max_chars]
            block += "\n（本体记忆较长，已截断；需要细节用 sandglass_search 工具检索。）"
        return "\n\n【本体记忆（NexSandglass + super_memory）】\n" + block
    except Exception as e:
        _log(f"[agent-mem] system_snapshot 失败（已降级为空）: {e}")
        return ""


# ── 2. 每轮 prefetch（动态信号 → 放 user 同轮）──────────────────────
def prefetch(query="", max_chars=400):
    """每轮对话前的**最动态**信号：偏移方向 + 情绪风向 + 场景。

    **为什么放 user 同轮**：这些值每轮都可能变。塞进 system 会让前缀缓存
    每轮失效（DeepSeek 的磁盘缓存按前缀匹配计费），成本会翻好几倍。
    放 user 同轮只影响本轮，代价最小。

    取数路径：``sandglass_bridge._provider.prefetch()``（provider 层接口）。
    bridge 本身不暴露 prefetch，故这里**转一层**；provider 拿不到就降级为空串。
    """
    if not _CFG.get("enabled", True):
        return ""
    try:
        b = _ready_bridge()
        if b is None or not b.is_available():
            return ""
        provider = getattr(b, "_provider", None)
        if provider is None or not hasattr(provider, "prefetch"):
            return ""
        txt = provider.prefetch(query) or ""
        txt = str(txt).strip()
        if not txt:
            return ""
        if len(txt) > max_chars:
            txt = txt[:max_chars]
        return "【当前状态】" + txt
    except Exception as e:
        _log(f"[agent-mem] prefetch 失败（已降级为空）: {e}")
        return ""


# ── 3. 会话后落沙（走统一分发入口，不复制插件逻辑）──────────────────
def sync_turn(user_msg="", assistant_msg="", *, scope="agent", scope_id="",
              sender="", sender_nick="", timestamp=None, extra=None):
    """把一轮 agent 对话落进本体记忆。

    **为什么走 memory_feed**：任务 2.3 已经把「一条消息 → 四个记忆模块」
    收敛成**一个入口**（sandglass_bridge.memory_feed）。agent 侧再写一遍
    sandglass/super_memory/self_learning 就是语义分裂。所以这里只构造
    msg 字典调 feed，各模块各自取自己关心的字段。

    返回 dict（各模块是否落盘），失败返回全 False —— 调用方**不需要**关心。
    """
    out = {"ok": False, "detail": {}}
    if not _CFG.get("enabled", True):
        return out
    u = str(user_msg or "").strip()
    a = str(assistant_msg or "").strip()
    if not u and not a:
        return out
    try:
        b = _ready_bridge()
        if b is None or not b.is_available():
            return out
        # scope=group → 走群记忆；其余（desk/agent）归到私聊记忆（group_id 空）
        group_id = scope_id if scope == "group" else ""
        msg = {
            "text": u,
            "group_id": str(group_id or ""),
            "sender": str(sender or ""),
            "sender_nick": str(sender_nick or ""),
            "timestamp": timestamp,
        }
        if a:
            msg["reply"] = a
        detail = b.memory_feed(msg, scope=("group" if group_id else "private")) or {}
        # extra：允许调用方补字段（如 agent 侧的 session_id），由 feed 忽略未识别键
        if isinstance(extra, dict):
            msg.update(extra)
        out = {"ok": True, "detail": detail}
        return out
    except Exception as e:
        _log(f"[agent-mem] sync_turn 失败（不影响主流程）: {e}")
        return out


# ── 4. 记忆检索：暴露给 agent 作为工具 ──────────────────────────────
SANDBOX_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sandglass_search",
        "description": (
            "检索本体长期记忆（NexSandglass 沙漏）。当你需要回忆「以前聊过什么」"
            "「主人对此的态度」「某个人的偏好」时调用。返回按相关度排序的记忆条目。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词或自然语言描述"},
                "limit": {"type": "integer", "description": "返回条数，默认 5，最大 20"},
            },
            "required": ["query"],
        },
    },
}

SANDBOX_RECENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sandglass_recent",
        "description": "读取本体记忆里最近 N 条落沙记录（按时间倒序），用于了解近期发生的事。",
        "parameters": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "条数，默认 8，最大 30"},
            },
            "required": [],
        },
    },
}

MEMORY_TOOLS = [SANDBOX_SEARCH_SCHEMA, SANDBOX_RECENT_SCHEMA]


def _fmt_rows(rows, empty_msg):
    """把 sandglass_vault 的 ``[(行号, 时间, 明文), ...]`` 渲染成人话列表。

    对形状容错（dict / 单值 / 三元组都收）——因为 vault 有 legacy fallback 路径，
    返回形状在不同后端下可能略有差异，渲染层不该因此崩掉。
    """
    if not rows:
        return empty_msg
    lines = []
    for r in rows:
        if isinstance(r, dict):
            lines.append(f"- [{r.get('ts') or r.get('time') or ''}] {str(r.get('text') or '')[:300]}")
        elif isinstance(r, (list, tuple)) and len(r) >= 3:
            lines.append(f"- [{r[1]}] {str(r[2])[:300]}")
        elif isinstance(r, (list, tuple)) and len(r) == 2:
            lines.append(f"- {str(r[1])[:300]}")
        else:
            lines.append(f"- {str(r)[:300]}")
    return "\n".join(lines)


def _tool_search(args):
    """sandglass_search 的实现。返回**纯文本**（便于模型直接读，不用解 JSON）。"""
    if not _CFG.get("enabled", True):
        return "（记忆功能已关闭）"
    q = str((args or {}).get("query") or "").strip()
    if not q:
        return _err("缺少参数 query：请给出要检索的关键词或一句话描述")
    try:
        limit = int((args or {}).get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(20, limit))
    try:
        b = _ready_bridge()
        if b is None or not b.is_available():
            return "（本体记忆当前不可用，无法检索）"
        from sandglass_vault import search  # sys.path 由 sandglass_bridge 注入
        rows = search(q, limit=limit) or []
        if not rows:
            return f"没有检索到与「{q}」相关的记忆。"
        return f"检索到 {len(rows)} 条与「{q}」相关的记忆（按相关度排序）：\n" + _fmt_rows(rows, "")
    except Exception as e:
        return _err(f"记忆检索失败：{type(e).__name__}: {e}")


def _tool_recent(args):
    """sandglass_recent 的实现。"""
    if not _CFG.get("enabled", True):
        return "（记忆功能已关闭）"
    try:
        n = int((args or {}).get("n") or 8)
    except (TypeError, ValueError):
        n = 8
    n = max(1, min(30, n))
    try:
        b = _ready_bridge()
        if b is None or not b.is_available():
            return "（本体记忆当前不可用）"
        from sandglass_vault import recent
        rows = recent(n) or []
        if not rows:
            return "本体记忆里还没有记录。"
        return f"最近 {len(rows)} 条记忆：\n" + _fmt_rows(rows, "")
    except Exception as e:
        return _err(f"读取最近记忆失败：{type(e).__name__}: {e}")


def _err(msg):
    """工具错误：**人话 + 下一步**（DSH A19：非人类产出者必须自带来源）。"""
    return json.dumps({"error": msg}, ensure_ascii=False)


def memory_tools():
    """返回 {tool_name: callable}。调用方（agent_service）负责合并进工具表。

    只读工具 → 可常驻注入（无副作用），所以默认开启。
    """
    if not _CFG.get("enabled", True) or not _CFG.get("expose_tools", True):
        return {}
    return {
        "sandglass_search": _tool_search,
        "sandglass_recent": _tool_recent,
    }


def memory_tool_schemas():
    """返回 OpenAI schema 列表（与 memory_tools() 的键一一对应）。"""
    if not _CFG.get("enabled", True) or not _CFG.get("expose_tools", True):
        return []
    return [dict(s) for s in MEMORY_TOOLS]


def probe():
    """启动自检（供 bot_core 打印一行诊断）。"""
    st = status()
    return {
        "enabled": bool(_CFG.get("enabled", True)),
        "available": bool(st.get("available")),
        "reason": st.get("reason", ""),
        "messages": st.get("messages"),
        "stage": st.get("stage"),
        "tools": sorted(memory_tools().keys()),
    }
