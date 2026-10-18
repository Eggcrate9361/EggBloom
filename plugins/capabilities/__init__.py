# -*- coding: utf-8 -*-
"""capabilities —— 统一能力面板（真插件 ABI v2）

## 为什么是"一个"插件，而不是一堆

手册 M4 明确：**不要新建一堆插件**（会让插件列表膨胀，而"干净"是用户明确要求）。
所以项目里闲置的 OneBot 扩展动作全部收进这一个面板，UI 上一个列表，
每个动作一行、可手动触发、显示**原始返回**。

## 收录的动作（按手册优先级，★ 越多越优先）

| 组 | 动作 | 说明 |
|---|---|---|
| profile  | set_self_longnick | ★★★ 改个性签名（成本极低） |
| profile  | set_online_status / set_diy_online_status | ★★ 在线状态 |
| profile  | set_qq_profile | ★ 改昵称（昵称部分） |
| profile  | set_qq_avatar | ★ 改头像（**默认关**，有风控风险） |
| group    | get_group_signed_list | ★★ 群签到列表 |
| todo     | get_group_todo_list → set_group_todo / complete_group_todo / cancel_group_todo | ★ 群待办 |
| album    | get_group_album_list / get_group_album_media_list / upload_image_to_qun_album / del_group_album_media | ★ 群相册 |

## 本插件**不**收录发语音

手册 ★★★ 列了 `send_group_ai_record`（QQ 自带 AI 音色发语音），但要求
"先实测 `get_ai_characters` 能否拿到可用音色列表 —— 拿不到就跳过"。
**用户已明确决定不用语音功能**，故本插件不收录该项（不是"没做"，是"按决定不做"）。

## 三条硬约束（来自手册 1.3 已核实事实，照做不重蹈）

1. **`del_group_album_media` 是真名**。`delete_group_album_media` 在驱动源文件里
   零命中、不存在 —— 不要照抄失败的名字。
2. **群待办三个动作的 `message_id` 必须是本进程缓存过的群消息**，否则失败。
   所以本插件强制：先 `todo_list`（内部调 `get_group_todo_list` 拿 id），
   再从列表里选一条做 set/complete/cancel。**不允许手填裸 id**（那不是字符串 id，
   是 int32 hash，手填必错）。
3. **`set_online_status.status` 无官方枚举表**（提取方标"未能确认"），
   面板里如实标注，由用户实测填写。

## 分级开关

按风险/影响面分三档，默认全关（头像单独再加一道）：

    safe    只读动作（get_group_signed_list / todo_list / album_list / album_media_list）
    profile 改自己的资料（签名/在线状态/昵称）
    group   改群内内容或对外可见（群待办增删、相册上传/删除）
    avatar  改头像（默认关、需显式开 —— 风控风险）

总开关 + 四档，逐项开启。
"""
from __future__ import annotations

import json
import os
import threading
import time

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "capabilities",
    "version": "1.0.0",
    "api_version": 2,
    "author": "EggBloom",
    "description": "统一能力面板：闲置 OneBot 扩展动作（签名/在线状态/昵称/群签到/群待办/群相册）手动触发，显示原始返回",
    "dependencies": [],
    "priority": 45,
    "default_enabled": False,       # 会改资料/改群内内容，默认不启用
}

# ── 分组开关（默认全关）──
GROUPS = ("safe", "profile", "group", "avatar")
GROUP_LABEL = {
    "safe": "只读（群签到/群待办列表/群相册列表）",
    "profile": "改自己资料（签名/在线状态/昵称）",
    "group": "改群内内容（群待办/群相册）",
    "avatar": "改头像（风控风险，默认关）",
}

# ── 动作 → (分组, 说明, 是否只读) ──
ACTIONS = {
    # profile
    "set_self_longnick": ("profile", "改个性签名", False),
    "set_online_status": ("profile", "改在线状态（status 无枚举表，需实测）", False),
    "set_diy_online_status": ("profile", "改自定义在线状态", False),
    "set_qq_profile": ("profile", "改昵称 / 个人说明", False),
    "set_qq_avatar": ("avatar", "改头像（风控风险）", False),
    # 群
    "get_group_signed_list": ("safe", "群今日打卡列表", True),
    # 群待办
    "get_group_todo_list": ("safe", "群待办列表（取 id 用）", True),
    "set_group_todo": ("group", "设置群待办（需本进程缓存的 message_id）", False),
    "complete_group_todo": ("group", "完成群待办", False),
    "cancel_group_todo": ("group", "取消群待办", False),
    # 群相册
    "get_group_album_list": ("safe", "群相册列表", True),
    "get_group_album_media_list": ("safe", "相册内图片/视频列表", True),
    "upload_image_to_qun_album": ("group", "上传图片到群相册", False),
    "del_group_album_media": ("group", "删除相册内容（真名 del_ 不是 delete_）", False),
}

ALL_ACTIONS = tuple(ACTIONS.keys())

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_PATH = os.path.join(os.path.dirname(_PLUGIN_DIR), "state", "capabilities.json")

_lock = threading.Lock()
_ctx = None

_DEFAULT_STATE = {
    "enabled": False,
    "groups": {g: False for g in GROUPS},
    "last_error": "",
}
_state = dict(_DEFAULT_STATE)
_state["groups"] = dict(_DEFAULT_STATE["groups"])


def _load_state() -> dict:
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _fresh()
        merged = _fresh()
        merged.update({k: v for k, v in data.items() if k != "groups"})
        g = dict(_DEFAULT_STATE["groups"])
        g.update(data.get("groups") or {})
        merged["groups"] = g
        return merged
    except (OSError, ValueError):
        return _fresh()


def _fresh() -> dict:
    d = dict(_DEFAULT_STATE)
    d["groups"] = dict(_DEFAULT_STATE["groups"])
    return d


def _save_state() -> bool:
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _STATE_PATH)
        return True
    except OSError:
        return False


def _log(msg: str, level: str = "info"):
    if _ctx is not None:
        try:
            _ctx.log(msg, level)
            return
        except Exception:
            pass
    print(f"[capabilities] [{level}] {msg}")


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _gate(action: str):
    if not _state.get("enabled"):
        return _err("能力面板未启用（在插件页打开总开关）")
    meta = ACTIONS.get(action)
    if meta is None:
        return _err(f"未知动作 {action}")
    group = meta[0]
    if group == "avatar":
        if not (_state.get("groups", {}).get("avatar")):
            return _err("改头像默认关闭（有风控风险），需在设置里显式打开「avatar」组")
    elif not _state.get("groups", {}).get(group):
        return _err(f"动作 {action} 属于「{group}」组（{GROUP_LABEL[group]}），该组未开启")
    return None


# ══════════════════════════════════════════════════════════════
#  参数校验
# ══════════════════════════════════════════════════════════════

def _need_group(p: dict):
    gid = p.get("group_id")
    if gid in (None, ""):
        return None, "缺少 group_id（群号）"
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return None, f"group_id 必须是群号整数，收到 {p.get('group_id')!r}"
    if gid <= 0:
        return None, "group_id 必须为正整数"
    return gid, None


# 本进程缓存过的群待办 message_id（来自 get_group_todo_list）
_todo_cache: dict = {}          # group_id(str) -> {message_id(int): 摘要}


def _remember_todos(group_id: int, items):
    """把 get_group_todo_list 返回的 message_id 记下来，供后续操作校验。"""
    key = str(group_id)
    cached = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        mid = it.get("message_id")
        if mid is None:
            continue
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            continue
        cached[mid] = str(it.get("text") or "")[:60]
    _todo_cache[key] = cached


def _check_todo_id(group_id: int, message_id):
    """校验 message_id 是本进程缓存过的（手册 1.3(3)：否则必然失败）。"""
    key = str(group_id)
    cached = _todo_cache.get(key)
    if not cached:
        return f"请先调用 get_group_todo_list（群 {group_id}）取回待办 id —— 驱动要求 message_id 是本进程缓存过的群消息，手填必失败"
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return f"message_id 必须是整数（int32 hash，不是字符串），收到 {message_id!r}"
    if mid not in cached:
        return (f"message_id={mid} 不在本进程已缓存的群 {group_id} 待办里；"
                f"可用 id：{sorted(cached.keys()) or '（空）'}")
    return None


# ══════════════════════════════════════════════════════════════
#  动作实现（全部走 ctx.api_call 唯一出口）
# ══════════════════════════════════════════════════════════════

def _api(action: str, params: dict):
    if _ctx is None:
        return None, "插件上下文未就绪"
    r = _ctx.api_call(action, params)
    if r is None:
        return None, "宿主 api_call 未注入或调用失败（检查驱动连接）"
    return r, None


def do_set_self_longnick(p):
    nick = p.get("longNick")
    if nick is None:
        nick = p.get("long_nick")
    if nick is None:
        return _err("缺少 longNick（个性签名内容）")
    if not isinstance(nick, str):
        return _err("longNick 必须是字符串（驱动侧强校验 typeof === string）")
    r, e = _api("set_self_longnick", {"longNick": nick})
    return _err(e) if e else {"ok": True, "raw": r, "longNick": nick}


def do_set_online_status(p):
    if p.get("status") in (None, ""):
        return _err("缺少 status（必填）。注意：status 无官方枚举表，需实测填写")
    try:
        status = int(p["status"])
    except (TypeError, ValueError):
        return _err("status 必须是整数")
    if status == 0:
        return _err("status 不能为 0（驱动 f.int({nonZero:true})）")
    out = {"status": status}
    try:
        out["ext_status"] = int(p.get("ext_status") or 0)
        out["battery_status"] = int(p.get("battery_status") or 100)
    except (TypeError, ValueError):
        return _err("ext_status / battery_status 必须是整数")
    if out["ext_status"] < 0 or out["battery_status"] < 0:
        return _err("ext_status / battery_status 不能为负")
    r, e = _api("set_online_status", out)
    if e:
        return _err(e)
    return {"ok": True, "raw": r,
            "note": "status 无官方枚举表（提取方标注未能确认），若无效请换个值实测"}


def do_set_diy_online_status(p):
    if p.get("face_id") in (None, ""):
        return _err("缺少 face_id（必填）")
    try:
        face_id = int(p["face_id"])
        face_type = int(p.get("face_type") or 1)
    except (TypeError, ValueError):
        return _err("face_id / face_type 必须是整数")
    if face_id < 0 or face_type < 0:
        return _err("face_id / face_type 不能为负")
    out = {"face_id": face_id, "face_type": face_type, "wording": str(p.get("wording") or "")}
    r, e = _api("set_diy_online_status", out)
    return _err(e) if e else {"ok": True, "raw": r}


def do_set_qq_profile(p):
    nick = p.get("nickname")
    note = p.get("personal_note")
    if (nick in (None, "")) and (note in (None, "")):
        return _err("nickname / personal_note 至少给一个（否则无事可做）")
    out = {}
    if nick not in (None, ""):
        out["nickname"] = str(nick)
    if note not in (None, ""):
        out["personal_note"] = str(note)
    r, e = _api("set_qq_profile", out)
    return _err(e) if e else {"ok": True, "raw": r}


def do_set_qq_avatar(p):
    f = str(p.get("file") or "").strip()
    if not f:
        return _err("缺少 file（图片路径 / file:// / http:// / base64://）")
    if not (p.get("confirm") or p.get("_confirmed")):
        return _err("改头像有风控风险，需传 confirm=true 明确确认")
    r, e = _api("set_qq_avatar", {"file": f})
    return _err(e) if e else {"ok": True, "raw": r}


def do_get_group_signed_list(p):
    gid, e = _need_group(p)
    if e:
        return _err(e)
    r, err = _api("get_group_signed_list", {"group_id": gid})
    return _err(err) if err else {"ok": True, "raw": r}


def do_get_group_todo_list(p):
    gid, e = _need_group(p)
    if e:
        return _err(e)
    r, err = _api("get_group_todo_list", {"group_id": gid})
    if err:
        return _err(err)
    # 记账：把 message_id 缓存下来，后续 set/complete/cancel 才能通过校验
    items = []
    if isinstance(r, dict):
        data = r.get("data")
        items = data if isinstance(data, list) else (r.get("data") or [])
    _remember_todos(gid, items if isinstance(items, list) else [])
    count = len(_todo_cache.get(str(gid)) or {})
    return {"ok": True, "raw": r, "cached_ids": count,
            "note": f"已缓存 {count} 个待办 id（本进程内有效）；可直接用 set/complete/cancel"}


def _todo_action(action: str, fn_name: str, verify_only=False):
    def handler(p):
        gid, e = _need_group(p)
        if e:
            return _err(e)
        mid = p.get("message_id")
        if mid in (None, ""):
            return _err("缺少 message_id —— 请先用 action=get_group_todo_list 取回 id")
        verr = _check_todo_id(gid, mid)
        if verr:
            return _err(verr)
        if verify_only:
            return {"ok": True, "verified": int(mid), "group_id": gid}
        r, err = _api(fn_name, {"group_id": gid, "message_id": int(mid)})
        return _err(err) if err else {"ok": True, "raw": r, "group_id": gid, "message_id": int(mid)}
    return handler


def do_get_group_album_list(p):
    gid, e = _need_group(p)
    if e:
        return _err(e)
    r, err = _api("get_group_album_list", {"group_id": gid})
    return _err(err) if err else {"ok": True, "raw": r}


def do_get_group_album_media_list(p):
    gid, e = _need_group(p)
    if e:
        return _err(e)
    album_id = str(p.get("album_id") or "").strip()
    if not album_id:
        return _err("缺少 album_id（来自 get_group_album_list）")
    out = {"group_id": gid, "album_id": album_id, "attach_info": str(p.get("attach_info") or "")}
    r, err = _api("get_group_album_media_list", out)
    return _err(err) if err else {"ok": True, "raw": r}


def do_upload_image_to_qun_album(p):
    gid, e = _need_group(p)
    if e:
        return _err(e)
    album_id = str(p.get("album_id") or "").strip()
    album_name = str(p.get("album_name") or "").strip()
    f = str(p.get("file") or "").strip()
    if not album_id:
        return _err("缺少 album_id")
    if not album_name:
        return _err("缺少 album_name（非空）")
    if not f:
        return _err("缺少 file（图片路径 / file:// / http:// / base64://）")
    out = {"group_id": gid, "album_id": album_id, "album_name": album_name, "file": f}
    r, err = _api("upload_image_to_qun_album", out)
    return _err(err) if err else {"ok": True, "raw": r}


def do_del_group_album_media(p):
    gid, e = _need_group(p)
    if e:
        return _err(e)
    album_id = str(p.get("album_id") or "").strip()
    lloc = str(p.get("lloc") or "").strip()
    if not album_id:
        return _err("缺少 album_id")
    if not lloc:
        return _err("缺少 lloc（来自 get_group_album_media_list）")
    if not (p.get("confirm") or p.get("_confirmed")):
        return _err("删除相册内容不可逆，需传 confirm=true 确认")
    out = {"group_id": gid, "album_id": album_id, "lloc": lloc}
    r, err = _api("del_group_album_media", out)
    return _err(err) if err else {"ok": True, "raw": r}


_HANDLERS = {
    "set_self_longnick": do_set_self_longnick,
    "set_online_status": do_set_online_status,
    "set_diy_online_status": do_set_diy_online_status,
    "set_qq_profile": do_set_qq_profile,
    "set_qq_avatar": do_set_qq_avatar,
    "get_group_signed_list": do_get_group_signed_list,
    "get_group_todo_list": do_get_group_todo_list,
    "set_group_todo": _todo_action("set_group_todo", "set_group_todo"),
    "complete_group_todo": _todo_action("complete_group_todo", "complete_group_todo"),
    "cancel_group_todo": _todo_action("cancel_group_todo", "cancel_group_todo"),
    "get_group_album_list": do_get_group_album_list,
    "get_group_album_media_list": do_get_group_album_media_list,
    "upload_image_to_qun_album": do_upload_image_to_qun_album,
    "del_group_album_media": do_del_group_album_media,
}


def call(action: str, params: dict | None = None) -> dict:
    if action not in _HANDLERS:
        return _err(f"未知动作 {action}（支持：{'、'.join(ALL_ACTIONS)}）")
    gate = _gate(action)
    if gate is not None:
        return gate
    try:
        return _HANDLERS[action](params or {})
    except Exception as e:
        _state["last_error"] = f"{action}: {e}"
        _save_state()
        return _err(f"{action} 执行异常：{e}")


# ══════════════════════════════════════════════════════════════
#  ABI 钩子
# ══════════════════════════════════════════════════════════════

def on_load(ctx):
    global _ctx, _state
    _ctx = ctx
    _state = _load_state()
    on = [k for k, v in (_state.get("groups") or {}).items() if v]
    ctx.log(f"已加载（总开关 {'开' if _state.get('enabled') else '关'}；"
            f"已开分组：{'、'.join(on) if on else '无'}）")


def on_unload(ctx):
    pass


def on_enable(ctx):
    global _ctx
    _ctx = ctx


def on_disable(ctx):
    pass


PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_unload": on_unload,
    "on_enable": on_enable,
    "on_disable": on_disable,
}


# ══════════════════════════════════════════════════════════════
#  WebUI 动作
# ══════════════════════════════════════════════════════════════

def _a_status(_params):
    return {
        "enabled": bool(_state.get("enabled")),
        "groups": dict(_state.get("groups") or {}),
        "groups_meta": [{"key": g, "label": GROUP_LABEL[g]} for g in GROUPS],
        "actions": [
            {"action": a, "group": m[0], "desc": m[1], "readonly": m[2]}
            for a, m in ACTIONS.items()
        ],
        "todo_cached": {gid: sorted(ids.keys()) for gid, ids in _todo_cache.items()},
        "voice_skipped": True,
        "voice_note": "发语音（send_group_ai_record）按用户决定不收录",
        "last_error": _state.get("last_error") or "",
    }


def _a_set_config(params):
    p = params or {}
    global _state
    with _lock:
        if "enabled" in p:
            _state["enabled"] = bool(p["enabled"])
        groups = p.get("groups")
        if isinstance(groups, dict):
            cur = dict(_state.get("groups") or {})
            for g in GROUPS:
                if g in groups:
                    cur[g] = bool(groups[g])
            _state["groups"] = cur
        # 头像组二次确认：不允许在一次 set_config 里"顺手"打开脑袋
        if isinstance(groups, dict) and groups.get("avatar") and not p.get("confirm_avatar"):
            _state["groups"]["avatar"] = False
        _save_state()
    return {"ok": True, "status": _a_status({})}


def _a_run(params):
    """试运行：执行一个动作，返回**原始 JSON**。同样受门禁约束（不是后门）。"""
    p = params or {}
    action = str(p.get("action") or "").strip()
    if not action:
        return _err("缺少 action")
    args = p.get("params") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError as e:
            return _err(f"params 不是合法 JSON：{e}")
    if not isinstance(args, dict):
        return _err("params 必须是对象")
    if p.get("confirm"):
        args = dict(args, confirm=True)
    return call(action, args)


def execute_action(action, params=None):
    return PLUGIN_ACTIONS.get(action, lambda p: _err(f"未知动作 {action}"))(params or {})


PLUGIN_ACTIONS = {
    "status": _a_status,
    "set_config": _a_set_config,
    "run": _a_run,
}

PLUGIN_UI = {"page": "plugins/capabilities/ui.html", "title": "能力面板"}

__all__ = [
    "PLUGIN_SPEC", "PLUGIN_MANIFEST", "PLUGIN_HOOKS", "PLUGIN_ACTIONS",
    "PLUGIN_UI", "execute_action", "call", "ACTIONS", "ALL_ACTIONS", "GROUPS",
]
