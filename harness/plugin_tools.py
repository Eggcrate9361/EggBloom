# -*- coding: utf-8 -*-
"""harness/plugin_tools.py —— 把插件能力接成 agent 可调用的工具。

为什么需要它
------------
agent 的工具此前只有三处来源：`bot_core.TOOL_FUNCTIONS` + skill 内部工具 + MCP 工具。
**插件动作一个都不在里面** —— 于是"让 agent 发 QQ 空间 / 建定时任务 / 主动聊天"时，
agent 手上根本没有这些工具（而各插件的 `PLUGIN_ACTIONS` 里其实都有）。
用户对 agent 的定位就是"**集成调用插件功能**"，所以这条桥是 agent 能不能干活的关键。

设计（两段式，省 token）
------------------------
  `plugin_list`  —— 发现：列出插件、**简介**（META.description / MANIFEST.description）、
                    可调用动作、是否启用。模型不知道有啥时先调它。
  `plugin_call`  —— 执行：`{plugin, action, params}` → 路由到 `PLUGIN_ACTIONS[action](params)`。
两段式的好处：`plugin_call` 的描述里只放一句摘要 + 极简清单，细节按需再取，
不必把 12 个插件的全部动作塞进每一次请求。

安全
----
  · **尊重启用开关**：`plugins/plugin_state.json` 里没开的插件（如 qzone 默认关）
    一律拒绝，并回一句让主人去插件页打开 —— 避免 agent 绕过用户的显式选择对外发布。
  · 任何异常都返回 `{"error": ...}`，绝不打断 agent 循环。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

_PLUGIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
_STATE_FILE = os.path.join(_PLUGIN_DIR, "plugin_state.json")

GROUP_PLUGIN = "plugin"          # 工具注册用的组名（registry 的组是自由字符串）

# ── 权限询问（"真 agent" 该有的东西）─────────────────────────────────
# 插件没启用时不干巴巴拒绝，而是**弹窗问主人**：同意就打开插件并继续执行。
#   · 工具在 agent 线程里**阻塞等待**（默认 180s），前端点允许/拒绝后由
#     bot_core 的 /api/agent/permission 唤醒；
#   · 询问事件写进 session_log（kind="agent"），前端流式读到就弹窗 ——
#     这条通道和 agent 其它事件完全一致，断线重连也不丢。
_PERM_LOCK = threading.Lock()
_PERM_WAIT = {}                  # request_id -> {"ev": Event, "allow": bool}
_PERM_NOTIFIER = None            # 由 bot_core 注入：(request_dict) -> None
PERM_TIMEOUT = 180

# ── 「记住这个插件」的信任表 ────────────────────────────────────────
# 2026-10-12 用户原话："不要次次都询问"。同意一次并勾了"记住"之后，
# 该插件的后续调用直接放行（仍然只放开启这一个动作，不放开其它权限）。
_TRUSTED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "agent_trusted_plugins.json")
_TRUSTED = set()
_TRUST_LOCK = threading.Lock()


def _load_trusted():
    global _TRUSTED
    try:
        if os.path.isfile(_TRUSTED_FILE):
            data = json.loads(open(_TRUSTED_FILE, "r", encoding="utf-8").read())
            if isinstance(data, list):
                _TRUSTED = {str(x) for x in data if str(x).strip()}
    except Exception:
        pass


def _save_trusted():
    try:
        os.makedirs(os.path.dirname(_TRUSTED_FILE), exist_ok=True)
        tmp = _TRUSTED_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(_TRUSTED), f, ensure_ascii=False, indent=1)
        os.replace(tmp, _TRUSTED_FILE)
    except Exception as e:
        print(f"[plugin_tools] 保存信任表失败: {e}")


def is_trusted(plugin: str) -> bool:
    with _TRUST_LOCK:
        return str(plugin) in _TRUSTED


def trust_plugin(plugin: str) -> bool:
    with _TRUST_LOCK:
        _TRUSTED.add(str(plugin))
        _save_trusted()
    return True


def untrust_plugin(plugin: str) -> bool:
    with _TRUST_LOCK:
        _TRUSTED.discard(str(plugin))
        _save_trusted()
    return True


def trusted_plugins():
    with _TRUST_LOCK:
        return sorted(_TRUSTED)


_load_trusted()


def set_permission_notifier(fn):
    global _PERM_NOTIFIER
    _PERM_NOTIFIER = fn


def resolve_permission(request_id, allow, remember=False):
    """前端点「允许/拒绝」后调用。返回是否找到这个请求。

    remember=True（前端勾了"不再询问该插件"）→ 把插件写进信任表，
    之后同一插件的调用直接放行，不再弹窗。
    """
    with _PERM_LOCK:
        rec = _PERM_WAIT.get(str(request_id))
    if not rec:
        return False
    rec["allow"] = bool(allow)
    rec["ev"].set()
    if remember and rec.get("plugin"):
        trust_plugin(rec["plugin"])
    return True


def pending_permissions():
    with _PERM_LOCK:
        return sorted(_PERM_WAIT.keys())


def _ask_permission(plugin, action, desc, reason):
    """发询问并阻塞等待。返回 True=同意 / False=拒绝 / None=超时。

    已在信任表里的插件**不再询问**（用户勾过"不再询问该插件"）。
    """
    if is_trusted(plugin):
        return True
    rid = "%s-%s-%s" % (plugin, action, uuid.uuid4().hex[:8])
    ev = threading.Event()
    with _PERM_LOCK:
        _PERM_WAIT[rid] = {"ev": ev, "allow": False}
    req = {"request_id": rid, "plugin": plugin, "action": action,
           "desc": desc or "", "reason": reason, "timeout": PERM_TIMEOUT,
           "message": "插件「%s」当前未启用，是否打开它并继续执行 %s？" % (plugin, action),
           "plan": "打开插件「%s」→ 执行 %s" % (plugin, action)}
    try:
        if _PERM_NOTIFIER:
            _PERM_NOTIFIER(req)
    except Exception:
        pass
    got = ev.wait(PERM_TIMEOUT)
    with _PERM_LOCK:
        rec = _PERM_WAIT.pop(rid, None)
    if not got:
        return None
    return bool((rec or {}).get("allow"))


def enable_plugin(name):
    """打开插件：写 plugin_state.json + 通知 plugin_manager（与插件页开关同一套）。"""
    try:
        st = {}
        if os.path.isfile(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                st = json.load(f) or {}
        for k in ("enabled", "已启用"):
            m = st.get(k)
            if not isinstance(m, dict):
                m = {}
            m[name] = True
            m[name.lower()] = True
            st[k] = m
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _STATE_FILE)
    except Exception as e:
        return False, str(e)
    try:
        from plugins.plugin_manager import init_plugin_manager, get_plugin_manager
        init_plugin_manager()
        pm = get_plugin_manager()
        if pm:
            pm.enable(name)
    except Exception:
        pass          # 管理器热态没跟上也没关系：状态表已落盘，下次启动生效
    return True, ""


def _state_enabled():
    """读插件启停表 → {name: bool}（大小写都归一）"""
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            st = json.load(f) or {}
        em = st.get("enabled") or st.get("已启用") or {}
        return {str(k).lower(): bool(v) for k, v in (em or {}).items()}
    except Exception:
        return {}


def _meta_of(mod, name):
    """统一取元数据：老式 PLUGIN_META 与 ABI v1 的 PLUGIN_MANIFEST 都认。"""
    meta = dict(getattr(mod, "PLUGIN_META", None) or {})
    man = dict(getattr(mod, "PLUGIN_MANIFEST", None) or {})
    merged = {}
    merged.update(man)          # manifest 先
    merged.update(meta)         # META 覆盖（老式插件更具体）
    merged.setdefault("name", name)
    desc = (merged.get("description") or "").strip()
    enabled = merged.get("enabled", merged.get("default_enabled", False))
    tags = merged.get("tags") or []
    return desc, bool(enabled), [str(t) for t in tags][:6]


def _discover(ttl=60.0):
    """→ [(name, desc, actions, enabled, tags)]，导入失败的静默跳过。

    ★ 带缓存（默认 60s）：原先**每次调用都要重新导入全部插件**，
    agent 一轮里可能调好几次 plugin_list → 白白多花几十秒（实测跑到超时）。
    """
    global _CACHE_AT, _DISCOVER_CACHE
    now = time.time()
    with _CACHE_LOCK:
        if _DISCOVER_CACHE is not None and (now - _CACHE_AT) < ttl:
            return list(_DISCOVER_CACHE)
    rows = _discover_uncached()
    with _CACHE_LOCK:
        _DISCOVER_CACHE = list(rows)
        _CACHE_AT = now
    return rows


_DISCOVER_CACHE = None
_CACHE_AT = 0.0
_CACHE_LOCK = threading.Lock()


def _discover_uncached():
    """真正扫描一次（慢：要导入每个插件）。"""
    try:
        from plugins.plugin_manager import PluginManager
        names = PluginManager().discover()
    except Exception:
        names = []
    st = _state_enabled()
    out = []
    for name in names:
        try:
            mod = __import__("plugins." + name, fromlist=["PLUGIN_ACTIONS", "PLUGIN_META",
                                                          "PLUGIN_MANIFEST"])
        except Exception:
            continue
        acts = getattr(mod, "PLUGIN_ACTIONS", None)
        acts = sorted(acts.keys()) if isinstance(acts, dict) else []
        desc, enabled, tags = _meta_of(mod, name)
        if name.lower() in st:                 # 用户在插件页显式开/关过 → 以状态为准
            enabled = st[name.lower()]
        out.append((name, desc, acts, enabled, tags))
    return out


def _pick(args, *names, default=""):
    """统一参数契约：tool_runner 调的是 **fn(args)——一个字典**，
    而函数签名写的是具名参数 → 直接拿整个字典当参数用就会炸
    （实测：`plugin_list {"keyword":"qzone"}` → AttributeError: 'dict' has no 'strip'）。
    两种调用方式都支持：字典（取其中的键）或直接关键字。"""
    if isinstance(args, dict):
        for n in names:
            if n in args:
                return args[n]
        return default
    return args if args is not None else default


def plugin_list(keyword="", **_kw):
    """发现工具：插件 / 简介 / 动作 / 是否启用。keyword 可选，按名字或简介过滤。

    ★ 体积纪律（实测踩过）：**不带 keyword 时只给紧凑目录**（名字 + 短简介 + 动作数），
      否则 12 个插件的全量动作会超出 tool 结果预算 → 结果被 spill 落盘并截断 →
      agent 去读溢出文件 → 又被截断溢出 → 陷入"spill→read→spill"空转（7 步无任何进展）。
      要看某插件的动作，用 keyword 精确查询它。"""
    keyword = _pick(keyword, "keyword", "kw", "name", default="")
    kw = (keyword or "").strip().lower()
    rows = []
    for name, desc, acts, enabled, tags in _discover():
        if kw and kw not in name.lower() and kw not in desc.lower() and \
           not any(kw in t.lower() for t in tags):
            continue
        row = {
            "plugin": name,
            "enabled": enabled,
        }
        if kw:
            row["desc"] = desc or "（该插件没写简介）"
            row["actions"] = acts
            row["tags"] = tags
        else:
            row["desc"] = (desc or "")[:40]
            row["n_actions"] = len(acts)
        rows.append(row)
    if not rows:
        return {"plugins": [], "hint": "没有匹配的插件"}
    hint = ("用 plugin_call(plugin=..., action=..., params={}) 调用；未启用的插件需主人先在插件页打开"
            if kw else "要看某个插件的动作，用 plugin_list(keyword=\"插件名\") 精确查询")
    return {"plugins": rows, "hint": hint}


def plugin_call(plugin="", action="", params=None, **_kw):
    """执行工具：路由到该插件的 PLUGIN_ACTIONS[action](params)。"""
    # 若是 fn(args) 形式（整个字典塞进 plugin）→ 拆开取
    if isinstance(plugin, dict):
        _a = plugin
        params = _a.get("params") if params is None else params
        action = _a.get("action") or ""
        plugin = _a.get("plugin") or ""
    plugin = str(plugin or "").strip()
    action = str(action or "").strip()
    if not plugin or not action:
        return {"error": "缺少 plugin 或 action（先调 plugin_list 看有哪些）"}
    if plugin not in [r[0] for r in _discover()]:
        return {"error": "没有这个插件：%s（先调 plugin_list）" % plugin}
    for name, desc, acts, enabled, _t in _discover():
        if name != plugin:
            continue
        if not enabled:
            # ★ 不直接拒绝：**先问主人**（同意就打开插件并继续执行）
            allow = _ask_permission(plugin, action, desc, "插件未启用")
            if allow is None:
                return {"error": "等待主人授权超时（%d 秒），已放弃。可以让主人先在插件页打开「%s」。"
                                 % (PERM_TIMEOUT, plugin)}
            if not allow:
                return {"denied": True,
                        "error": "主人拒绝了启用「%s」，任务终止（不要重试）。" % plugin}
            ok, err = enable_plugin(plugin)
            if not ok:
                return {"error": "启用「%s」失败：%s" % (plugin, err)}
        if action not in acts:
            return {"error": "插件 %s 没有动作 %s；可用：%s" % (plugin, action, ", ".join(acts) or "（无）")}
        break
    try:
        mod = __import__("plugins." + plugin, fromlist=["PLUGIN_ACTIONS"])
        fn = (getattr(mod, "PLUGIN_ACTIONS", None) or {}).get(action)
        if not callable(fn):
            return {"error": "插件 %s 的动作 %s 不可调用" % (plugin, action)}
        res = fn(params if isinstance(params, dict) else {})
        return {"ok": True, "plugin": plugin, "action": action, "result": res}
    except Exception as e:
        return {"error": "插件 %s.%s 执行失败：%s" % (plugin, action, e)}


# ── 注册进工具 registry（这样 agent 的工具挑选/schema 解析才认识它）────────
def register_into(registry):
    """把两个工具登记到 harness.tool_registry。返回成功注册的个数。"""
    n = 0
    brief = []
    for name, desc, acts, _en, _t in _discover():
        brief.append("%s(%s)" % (name, (desc or "")[:24]))
    catalog_hint = "已装插件：" + "；".join(brief[:14])
    tools = [
        ("plugin_list",
         {"type": "function", "function": {
             "name": "plugin_list",
             "description": "列出已装插件及其**简介、可调用动作、启用状态**。"
                            "不确定有哪些插件/能干什么时先调它。" + ("　" + catalog_hint if catalog_hint else ""),
             "parameters": {"type": "object",
                            "properties": {"keyword": {"type": "string",
                                                       "description": "可选，按名字/简介/标签过滤"}}}}},
         plugin_list),
        ("plugin_call",
         {"type": "function", "function": {
             "name": "plugin_call",
             "description": "**调用插件的能力**（发 QQ 空间、建定时任务、主动聊天、人格蒸馏…）。"
                            "参数：plugin=插件名，action=动作名，params=该动作的参数对象。"
                            "不确定插件名或动作名时先用 plugin_list 查。未启用的插件会被拒绝。",
             "parameters": {"type": "object",
                            "properties": {
                                "plugin": {"type": "string", "description": "插件名，如 qzone/scheduler/proactive_talk"},
                                "action": {"type": "string", "description": "动作名，如 run/add/send_now"},
                                "params": {"type": "object", "description": "该动作的参数对象，可为空"}},
                            "required": ["plugin", "action"]}}},
         plugin_call),
    ]
    for tname, schema, fn in tools:
        try:
            registry.register(tname, schema, fn=fn, groups=(GROUP_PLUGIN,), source="plugin")
            n += 1
        except Exception:
            pass
    return n
