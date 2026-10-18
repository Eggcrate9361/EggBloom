# -*- coding: utf-8 -*-
"""harness/plugin_forge.py —— 插件创作管线（生成 → staging → 管理员审批 → 启用）。

为什么需要它（2026-10-10 用户需求）
    agent 遇到内置能力不覆盖的"新功能请求"时，不该嘴上答应也不该直接往
    plugins/ 里写代码（未审批的代码进扫描路径 = 安全事故）。正确闭环：
      1. 查内置文档（docs/PLUGIN_DEV.md 等）验证可行性 —— skill 指引模型做；
      2. 按 ABI v2 模板生成插件代码，调 plugin_forge 工具**提交 staging**；
      3. staging = plugins/_pending/<name>/ —— 插件扫描器跳过 "_" 前缀目录，
         **未批准的代码永远不会被加载**（实测 plugin_manager.discover 规则）；
      4. 管理员 QQ 收通知（_notify_admin 注入通道）+ Agent 页弹窗审批；
      5. 同意 → 移入 plugins/ + 热加载 + enable；拒绝 → 留在 staging 备查；
      6. 全程落请求记录（_requests.json）—— Agent 页主动 / 群聊请求 双历史。

与权限通道（plugin_tools._ask_permission）的区别
    权限通道是**本轮阻塞等待**（不授权任务就卡住）；创作审批是**异步**的 ——
    agent 提交后继续回复"已提交审批"，主人什么时候点都可以（可能下一轮、
    可能明天开机）。所以审批状态必须持久化（_requests.json）+ 全局可查
    （/api/agent/forge），而不是挂在某个会话的事件流里。

安全边界
    · 插件名 ^[a-z][a-z0-9_]{1,30}$（与 import 包名规则一致，防路径注入）；
    · 文件路径必须相对、无 ".."、无盘符、无绝对路径；
    · 每个 .py 过 compile() 语法校验；PLUGIN_MANIFEST 用 ast.literal_eval 提取
      （**不执行**插件代码）并校验必填字段与 api_version；
    · ABI 三原则静态检查：禁止 import bot_core/__main__/config/data_store 等
      主程序模块（plugins.abi 是官方模板入口，放行）；
    · 同名插件（正式目录或 staging 已有 pending）拒绝覆盖；
    · 真正生效的唯一路径 = 管理员在 Agent 页点「同意启用」。
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import threading
import time
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)                       # resources/app
_PLUGIN_DIR = os.path.join(_ROOT, "plugins")
_STAGING = os.path.join(_PLUGIN_DIR, "_pending")     # "_"前缀 → discover 跳过
_REQ_FILE = os.path.join(_STAGING, "_requests.json")
_LOCK = threading.RLock()
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
# ABI 三原则：真插件不得 import 这些主程序模块（plugins.abi 放行 —— 官方模板入口）
_BANNED_IMPORTS = ("bot_core", "__main__", "config", "data_store", "webui",
                   "sandglass_bridge", "protocol_manager", "cred_store")
_MAX_FILES = 12
_MAX_FILE_BYTES = 60_000

# 注入点（bot_core 启动 agent 时接线；未注入时静默跳过，绝不抛）
_ADMIN_NOTIFIER = None       # (text: str) -> None   管理员 QQ 通知
_EVENT_HOOK = None           # (request_dict) -> None  写 session_log 弹前端窗

# 当前轮上下文（run_chat 进入时设置；工具在同一线程执行 → thread-local 可靠）
_CTX = threading.local()


def set_turn_context(session_id="", is_group=False):
    """agent_service.run_chat 在每次执行开始时调用，标记本轮来源。"""
    _CTX.session_id = str(session_id or "")
    _CTX.is_group = bool(is_group)


def _turn_context():
    return (getattr(_CTX, "session_id", ""), bool(getattr(_CTX, "is_group", False)))


def set_admin_notifier(fn):
    global _ADMIN_NOTIFIER
    _ADMIN_NOTIFIER = fn


def set_event_hook(fn):
    global _EVENT_HOOK
    _EVENT_HOOK = fn


def _notify_admin(text):
    try:
        if _ADMIN_NOTIFIER:
            _ADMIN_NOTIFIER(text)
    except Exception:
        pass


def _emit_event(req):
    try:
        if _EVENT_HOOK:
            _EVENT_HOOK(dict(req))
    except Exception:
        pass


# ── 请求记录（落盘）──────────────────────────────────────────────────
def _load_requests():
    try:
        if os.path.isfile(_REQ_FILE):
            with open(_REQ_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, list):
                return d
    except Exception:
        pass
    return []


def _save_requests(rows):
    os.makedirs(_STAGING, exist_ok=True)
    tmp = _REQ_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows[-300:], f, ensure_ascii=False, indent=1)
    os.replace(tmp, _REQ_FILE)


def _update_record(rid, **fields):
    with _LOCK:
        rows = _load_requests()
        for r in rows:
            if r.get("request_id") == rid:
                r.update(fields)
                r["updated_at"] = time.time()
                break
        _save_requests(rows)
        return rows


def history(limit=50):
    """创作历史（新→旧）。Agent 页「插件创作」区数据源。"""
    with _LOCK:
        rows = _load_requests()
    return list(reversed(rows[-int(limit):]))


def pending():
    return [r for r in history(300) if r.get("status") == "pending"]


# ── 静态校验（不执行插件代码）────────────────────────────────────────
def _check_relpath(p):
    p = str(p or "").strip().replace("\\", "/")
    if (not p or p.startswith("/") or re.match(r"^[a-zA-Z]:", p)
            or ".." in p.split("/") or p.startswith(".")):
        return None
    return p


def _extract_assign(tree, name):
    """从 AST 里取模块级 `name = <字面量>` 的值（literal_eval，不执行）。"""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except Exception:
                        return _SENTINEL
    return None


_SENTINEL = object()


def _extract_hooks(tree):
    """PLUGIN_HOOKS 的键集合（字面量 dict，或 dict( 调用 —— 官方模板写法）。
    返回 None=没找到 / []=空 / [k1,k2]=键列表。不执行任何代码。"""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if not (isinstance(t, ast.Name) and t.id == "PLUGIN_HOOKS"):
                continue
            v = node.value
            if isinstance(v, ast.Dict):
                return [k.value for k in v.keys if isinstance(k, ast.Constant)]
            if (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                    and v.func.id == "dict"):
                return [kw.arg for kw in v.keywords if kw.arg]
            return None
    return None


def _validate_files(files):
    """files: {relpath: content} → (ok, errors, manifest)。"""
    errs = []
    if not isinstance(files, dict) or not files:
        return False, ["files 必须是非空对象 {路径: 内容}"], None
    if len(files) > _MAX_FILES:
        return False, ["文件数 %d 超上限 %d" % (len(files), _MAX_FILES)], None
    clean = {}
    for p, c in files.items():
        rp = _check_relpath(p)
        if not rp:
            errs.append("非法路径: %r" % p)
            continue
        if not isinstance(c, str) or len(c.encode("utf-8")) > _MAX_FILE_BYTES:
            errs.append("文件过大或非文本: %s" % rp)
            continue
        clean[rp] = c
    if errs:
        return False, errs, None
    if "__init__.py" not in clean:
        return False, ["缺少 __init__.py（插件唯一必须文件）"], None

    manifest = None
    try:
        tree = ast.parse(clean["__init__.py"], filename="__init__.py")
    except SyntaxError as e:
        return False, ["__init__.py 语法错误: %s" % e], None

    # 其余 .py 也过语法
    for rp, c in clean.items():
        if rp.endswith(".py") and rp != "__init__.py":
            try:
                ast.parse(c, filename=rp)
            except SyntaxError as e:
                errs.append("%s 语法错误: %s" % (rp, e))

    # 禁 import 主程序（ABI 三原则）
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        for m in mods:
            top = m.split(".")[0]
            if top in _BANNED_IMPORTS:
                errs.append("违反 ABI 三原则：import 了主程序模块 %s"
                            "（只允许通过 ctx 访问能力）" % m)

    spec = _extract_assign(tree, "PLUGIN_SPEC")
    if spec != 1:
        errs.append("缺少 PLUGIN_SPEC = 1（真插件 ABI 声明）")
    hooks = _extract_hooks(tree)
    if hooks is None:
        errs.append("缺少 PLUGIN_HOOKS = {...} 声明式钩子表")
    elif hooks == []:
        errs.append("PLUGIN_HOOKS 必须非空（至少一个钩子）")
    mf = _extract_assign(tree, "PLUGIN_MANIFEST")
    if mf is _SENTINEL:
        errs.append("PLUGIN_MANIFEST 必须是纯字面量 dict（校验需要，不能是表达式）")
    elif not isinstance(mf, dict):
        errs.append("缺少 PLUGIN_MANIFEST")
    else:
        manifest = mf
        for k in ("name", "version", "api_version", "description"):
            if k not in mf:
                errs.append("PLUGIN_MANIFEST 缺少字段: %s" % k)
        av = mf.get("api_version")
        if av is not None and (not isinstance(av, int) or av < 1 or av > 2):
            errs.append("api_version 必须是 1 或 2（当前 ABI 上限 2）")
    return (not errs), errs, manifest


# ── 工具一：plugin_forge（提交创作）─────────────────────────────────
def _pick(args, *names, default=""):
    """参数契约（沿用 P0-4 教训）：fn(args) 单字典 与 具名关键字 两种都支持。"""
    if isinstance(args, dict):
        for n in names:
            if n in args:
                return args[n]
        return default
    return args if args is not None else default


def plugin_forge(plugin_name="", description="", files=None, motivation="", **_kw):
    """提交一个**新插件**进 staging 等待主人审批（不直接生效）。"""
    # 参数契约（P0-4 教训）：tool_runner 可能把**整个 args 字典**塞进第一个位置参数。
    #   两种调用都要正确：① plugin_forge({"plugin_name":..,"files":{...}})
    #                    ② plugin_forge(plugin_name=.., files={...})
    if isinstance(plugin_name, dict):
        _a = plugin_name
        files = _a.get("files") if files is None else files
        description = _a.get("description") or _a.get("desc") or description
        motivation = _a.get("motivation") or motivation
        plugin_name = _a.get("plugin_name") or _a.get("name") or _a.get("plugin") or ""
    elif isinstance(files, dict) and isinstance(files.get("files"), dict):
        files = files["files"]   # 整体 args 塞进 files 位的变体
    name = str(plugin_name or "").strip()
    desc = str(description or "").strip()
    mot = str(motivation or "").strip()
    files = files if isinstance(files, dict) else {}

    if not _NAME_RE.match(name or ""):
        return {"error": "插件名不合规：需 ^[a-z][a-z0-9_]{1,30}$（小写字母开头，"
                         "字母数字下划线），如 weather_card"}
    if not desc:
        return {"error": "缺少 description（一句话说明插件干什么）"}
    if not isinstance(files, dict):
        return {"error": "files 必须是 {相对路径: 文件内容} 对象"}

    ok, errs, manifest = _validate_files(files)
    if not ok:
        return {"error": "静态校验未通过，未提交", "problems": errs[:8],
                "hint": "对照 docs/PLUGIN_DEV.md 的最小模板修正后重新提交"}
    mf_name = str((manifest or {}).get("name") or "")
    if mf_name and mf_name != name:
        return {"error": "PLUGIN_MANIFEST.name(%s) 与 plugin_name(%s) 不一致" % (mf_name, name)}

    target_formal = os.path.join(_PLUGIN_DIR, name)
    target_stage = os.path.join(_STAGING, name)
    with _LOCK:
        if os.path.exists(target_formal):
            return {"error": "正式目录已存在同名插件 %s —— 请改用别的名字，"
                             "或让主人在插件页处理已有的" % name}
        existing_pending = [r for r in _load_requests()
                            if r.get("plugin") == name and r.get("status") == "pending"]
        if existing_pending:
            return {"error": "%s 已有一条待审批创作（request_id=%s），先等主人处理"
                            % (name, existing_pending[0].get("request_id"))}
        if os.path.exists(target_stage):
            shutil.rmtree(target_stage)          # 旧的已决记录残留 → 覆盖
        os.makedirs(target_stage)
        for rp, c in files.items():
            fp = os.path.join(target_stage, *rp.split("/"))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8", newline="\n") as f:
                f.write(c)

    sid, is_group = _turn_context()
    rec = {
        "request_id": "forge-%s" % uuid.uuid4().hex[:10],
        "plugin": name,
        "description": desc,
        "version": str((manifest or {}).get("version") or "1.0.0"),
        "motivation": mot[:500],
        "source": "group" if is_group else "desk",
        "session_id": sid,
        "status": "pending",
        "files": sorted(files.keys()),
        "created_at": time.time(),
    }
    with _LOCK:
        rows = _load_requests()
        rows.append(rec)
        _save_requests(rows)

    src_txt = "群聊请求" if is_group else "Agent 页请求"
    _notify_admin("【插件创作审批】Agent 按%s生成了新插件「%s」（%s · v%s），"
                  "已放入待审批区。请到 WebUI 的 Agent 页点「同意启用」或「拒绝」。"
                  % (src_txt, name, desc[:40], rec["version"]))
    _emit_event(dict(rec, message="Agent 创作了新插件「%s」（%s），等待主人审批。"
                     % (name, desc[:40])))
    return {"ok": True, "request_id": rec["request_id"], "status": "pending",
            "staging": "plugins/_pending/%s" % name,
            "hint": "已提交审批（QQ 已通知管理员，Agent 页有弹窗与创作记录）。"
                    "**插件尚未生效** —— 如实告诉主人等他审批，不要假装已可用。"}


def plugin_forge_status(keyword="", **_kw):
    """查询插件创作记录（pending 与历史）。"""
    kw = str(_pick(keyword, "keyword", default="") or "").strip().lower()
    rows = history(100)
    if kw:
        rows = [r for r in rows if kw in str(r.get("plugin", "")).lower()
                or kw in str(r.get("description", "")).lower()]
    out = [{"plugin": r.get("plugin"), "description": (r.get("description") or "")[:50],
            "status": r.get("status"), "source": r.get("source"),
            "request_id": r.get("request_id"),
            "at": time.strftime("%m-%d %H:%M", time.localtime(r.get("created_at", 0)))}
           for r in rows[-20:]]
    pend = [r for r in out if r["status"] == "pending"]
    return {"records": list(reversed(out)),
            "pending_count": len(pend),
            "hint": "status: pending=等主人审批 / approved=已启用 / rejected=已拒绝"}


# ── 审批决议（前端 → bot_core → 这里）───────────────────────────────
def resolve(request_id, allow):
    """管理员在 Agent 页点「同意启用/拒绝」。

    同意：staging/<name> → plugins/<name> + 热加载 + enable（default_enabled
    即便为 False 也强制启用一次 —— 主人点同意就是授权）。
    拒绝：代码留在 staging 备查（不删除 —— 用户没要求删，保留可追溯）。
    """
    rid = str(request_id or "")
    with _LOCK:
        rows = _load_requests()
        rec = next((r for r in rows if r.get("request_id") == rid), None)
    if not rec:
        return {"ok": False, "error": "没有这条创作请求（或已被处理）: %s" % rid}
    if rec.get("status") != "pending":
        return {"ok": False, "error": "该请求已是 %s，不能重复处理" % rec.get("status")}
    name = str(rec.get("plugin") or "")
    src_dir = os.path.join(_STAGING, name)
    if not allow:
        _update_record(rid, status="rejected", decided_at=time.time())
        _notify_admin("【插件创作】「%s」已被主人拒绝，代码保留在 plugins/_pending/%s 备查。"
                      % (name, name))
        return {"ok": True, "status": "rejected"}

    if not os.path.isdir(src_dir):
        _update_record(rid, status="failed", decided_at=time.time(),
                       error="staging 目录不存在")
        return {"ok": False, "error": "staging 目录丢失：%s" % src_dir}
    target = os.path.join(_PLUGIN_DIR, name)
    if os.path.exists(target):
        return {"ok": False, "error": "正式目录已存在同名插件，拒绝移动（防覆盖）"}
    try:
        shutil.move(src_dir, target)
    except Exception as e:
        return {"ok": False, "error": "移入正式目录失败: %s" % e}

    load_err = ""
    try:
        import importlib
        importlib.invalidate_caches()
        from plugins.plugin_manager import init_plugin_manager, get_plugin_manager
        init_plugin_manager()
        pm = get_plugin_manager()
        if pm:
            if not pm.load(name):
                entry = pm._plugins.get(name) or {}
                load_err = str(entry.get("error") or "加载失败")
            else:
                pm.enable(name)
    except Exception as e:
        load_err = "%s: %s" % (type(e).__name__, e)

    if load_err:
        _update_record(rid, status="approved_load_failed", decided_at=time.time(),
                       error=load_err)
        _notify_admin("【插件创作】「%s」已同意并移入 plugins/，但热加载失败：%s"
                      "（重启 Bot 后生效，或修复后在插件页启用）" % (name, load_err[:120]))
        return {"ok": True, "status": "approved_load_failed", "error": load_err,
                "hint": "代码已就位，重启 Bot 后生效"}
    _update_record(rid, status="approved", decided_at=time.time())
    _notify_admin("【插件创作】「%s」已批准并启用，现在可以直接使用了。" % name)
    return {"ok": True, "status": "approved", "plugin": name}


# ── 工具注册（与 plugin_tools.register_into 同一模式）────────────────
def register_into(registry):
    """把两个创作工具登记进 harness.tool_registry。返回注册数。"""
    n = 0
    tools = [
        ("plugin_forge",
         {"type": "function", "function": {
             "name": "plugin_forge",
             "description": "**创作新插件**：当主人的需求超出内置能力且 docs/PLUGIN_DEV.md 的 "
                            "ctx 能力可以覆盖时，按 ABI v2 模板生成插件代码并提交。"
                            "代码进入 staging（plugins/_pending/）**等待主人在 Agent 页审批**，"
                            "批准后才生效 —— 提交后如实说'已提交审批'，不要说'已完成'。"
                            "提交前务必先读 docs/PLUGIN_DEV.md 的最小模板并逐项自查。",
             "parameters": {"type": "object", "properties": {
                 "plugin_name": {"type": "string",
                                 "description": "插件名：^[a-z][a-z0-9_]{1,30}$，如 weather_card"},
                 "description": {"type": "string", "description": "一句话说明插件干什么"},
                 "motivation": {"type": "string", "description": "为什么造它（主人的原话需求摘要）"},
                 "files": {"type": "object",
                           "description": "{相对路径: 完整内容}，必须含 __init__.py"
                                          "（PLUGIN_SPEC/PLUGIN_MANIFEST/PLUGIN_HOOKS 三件套）"}},
                 "required": ["plugin_name", "description", "files"]}}},
         plugin_forge),
        ("plugin_forge_status",
         {"type": "function", "function": {
             "name": "plugin_forge_status",
             "description": "查询插件创作记录与审批状态（pending/approved/rejected）。"
                            "主人问'上次那个插件弄好了吗'时用。",
             "parameters": {"type": "object", "properties": {
                 "keyword": {"type": "string", "description": "可选，按插件名/简介过滤"}}}}},
         plugin_forge_status),
    ]
    for tname, schema, fn in tools:
        try:
            registry.register(tname, schema, fn=fn, groups=("plugin",), source="plugin")
            n += 1
        except Exception:
            pass
    return n
