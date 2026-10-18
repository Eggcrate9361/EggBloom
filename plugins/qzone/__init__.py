# -*- coding: utf-8 -*-
"""qzone —— QQ 空间全套（真插件 ABI v2）

## 为什么存在

驱动的 QQ 空间动作已经是现成的（9 个），但此前没有任何地方能把它们用起来。
本插件把 9 个动作按**风险分四层**封装，并接上调度器与 AI，让"发说说"变成
可控、有上限、可审计的能力 —— 而不是裸露的一组 HTTP 端点。

## 风险分四层（默认全关，逐项开启）

| 层 | 动作 | 风险 |
|---|---|---|
| read    | get_qzone_msg_list、get_qzone_feeds | 无 |
| interact| like_qzone、unlike_qzone、comment_qzone | 低 |
| write   | send_qzone_msg | 中（发出去人人可见） |
| manage  | delete_qzone_msg、set_qzone_msg_right、set_qzone_ban | 中（不可逆/影响他人） |

四层各自一个开关，默认全部关闭。要发说说，必须显式把 `write` 打开。

## 频率硬上限（防风控，不放宽）

- 每天 **≤ 2 条** 说说
- 相邻两条间隔 **≥ 4 小时**

读/互动/管 不受此上限约束（只有 `send_qzone_msg` 受限）。

## 内容三来源

1. **手动写** —— WebUI「试运行」里直接填 content 发；
2. **聊天里被叫去发** —— 群里/私聊说"发条说说 xxx"，经 before_reply 钩子拦截；
3. **AI 按人设生成** —— 调度器到点广播 `qzone.publish`，本插件调 AI 生成正文。

## 定时集成

调度器（plugins/scheduler）到点广播事件，本插件在 `on_event` 里订阅：

    def on_event(ctx, event_name, payload):
        if event_name == "qzone.publish":
            ...   # 生成内容 → ctx.api_call("send_qzone_msg", {...})

## 已知限制（来自驱动文档，不自行绕过）

- `get_qzone_feeds` 的「按时间游标翻页尚未实现」—— 只能按页码，page_num 仅首页可靠。
- `unlike_qzone` 的取消赞端点文档标"待真机核实"。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "qzone",
    "version": "1.0.0",
    "api_version": 2,               # 与 abi.PLUGIN_API_VERSION 对齐（on_event / api_call 需 v2）
    "author": "EggBloom",
    "description": "QQ 空间全套：9 个动作分四层（读/互动/写/管），默认全关；发说说有频率硬上限",
    "dependencies": [],
    "priority": 40,
    "default_enabled": False,       # 涉及对外发布，默认不启用，由用户显式打开
}

# ── 频率硬上限（防风控，手册明写"不要放宽"）──
MAX_PER_DAY = 2
MIN_INTERVAL_SEC = 4 * 3600         # 4 小时

# ── 能力四层 ──
LAYERS = {
    "read": ("get_qzone_msg_list", "get_qzone_feeds"),
    "interact": ("like_qzone", "unlike_qzone", "comment_qzone"),
    "write": ("send_qzone_msg",),
    "manage": ("delete_qzone_msg", "set_qzone_msg_right", "set_qzone_ban"),
}
ALL_ACTIONS = tuple(a for acts in LAYERS.values() for a in acts)

# ugc_right 取值（来自驱动 rules，非推测）
UGC_RIGHTS = (1, 4, 16, 64, 128)
UGC_LABEL = {
    1: "所有人可见", 4: "好友可见", 16: "部分好友可见",
    64: "仅自己可见", 128: "部分好友不可见",
}

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_PATH = os.path.join(os.path.dirname(_PLUGIN_DIR), "state", "qzone.json")

_lock = threading.Lock()
_ctx = None


# ══════════════════════════════════════════════════════════════
#  状态（自己管 plugins/state/qzone.json）
# ══════════════════════════════════════════════════════════════

_DEFAULT_STATE = {
    "enabled": False,                 # 总开关
    "layers": {"read": False, "interact": False, "write": False, "manage": False},
    "persona": "",                    # AI 生成内容时的人设提示（空则用默认）
    # 发布记录：[{ts, tid, content}...]，用于频率上限判定
    "publish_log": [],
    "last_error": "",
}


def _load_state() -> dict:
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(_DEFAULT_STATE)
        merged = dict(_DEFAULT_STATE)
        merged.update(data)
        # layers 做二级合并，避免旧状态缺键
        layers = dict(_DEFAULT_STATE["layers"])
        layers.update(data.get("layers") or {})
        merged["layers"] = layers
        if not isinstance(merged.get("publish_log"), list):
            merged["publish_log"] = []
        return merged
    except (OSError, ValueError):
        return dict(_DEFAULT_STATE)


def _save_state(state: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _STATE_PATH)
        return True
    except OSError:
        return False


_state = _load_state()


def _log(msg: str, level: str = "info"):
    if _ctx is not None:
        try:
            _ctx.log(msg, level)
            return
        except Exception:
            pass
    print(f"[qzone] [{level}] {msg}")


# ══════════════════════════════════════════════════════════════
#  参数校验（错误必须可读，不许静默）
# ══════════════════════════════════════════════════════════════

def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _validate_ugc_right(ugc_right, target_uins):
    """校验 ugc_right 枚举与 target_uins 联动规则（来自驱动 rules）。"""
    try:
        ur = int(ugc_right)
    except (TypeError, ValueError):
        return f"ugc_right 必须是整数（1/4/16/64/128），收到 {ugc_right!r}"
    if ur not in UGC_RIGHTS:
        return (f"ugc_right={ur} 非法，只能是 " +
                "、".join(f"{k}({UGC_LABEL[k]})" for k in UGC_RIGHTS))
    if ur in (16, 128) and not (target_uins and len(target_uins)):
        need = "可见名单" if ur == 16 else "不可见名单"
        return f"ugc_right={ur}（{UGC_LABEL[ur]}）时必须提供 target_uins（{need}的 QQ 号数组）"
    return None


def _norm_uint_list(v, field: str):
    """把 target_uins 统一成 uint 列表；非法返回 (None, 错误)。"""
    if v is None or v == "":
        return [], None
    if isinstance(v, str):
        # 允许 "123,456" / "123|456" 形式
        parts = [p for p in v.replace("|", ",").split(",") if p.strip()]
        v = parts
    if not isinstance(v, (list, tuple)):
        return None, f"{field} 必须是 QQ 号数组或逗号分隔字符串，收到 {type(v).__name__}"
    out = []
    for item in v:
        try:
            n = int(item)
        except (TypeError, ValueError):
            return None, f"{field} 含非整数项：{item!r}"
        if n <= 0:
            return None, f"{field} 必须为正整数 QQ 号，收到 {n}"
        out.append(n)
    return out, None


# ══════════════════════════════════════════════════════════════
#  频率硬上限
# ══════════════════════════════════════════════════════════════

def _today_bounds(now: float):
    d = datetime.fromtimestamp(now)
    start = d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return start, start + 86400


def check_publish_limit(now: float | None = None) -> dict:
    """检查是否还能发说说。返回 {allowed, reason, used_today, next_ok_in}。

    规则（硬上限，不放宽）：
      - 每天 ≤ MAX_PER_DAY 条
      - 距上一条 ≥ MIN_INTERVAL_SEC 秒
    """
    now = time.time() if now is None else now
    start, end = _today_bounds(now)
    with _lock:
        log = [r for r in _state.get("publish_log") or []
               if isinstance(r, dict) and start <= float(r.get("ts") or 0) < end]
        last_ts = max((float(r.get("ts") or 0) for r in log), default=0)

    used = len(log)
    if used >= MAX_PER_DAY:
        return {
            "allowed": False,
            "reason": f"今天已发 {used} 条，达到每日上限 {MAX_PER_DAY} 条（防风控，不放宽）",
            "used_today": used,
            "next_ok_in": int(max(0, end - now)),
        }
    if last_ts and (now - last_ts) < MIN_INTERVAL_SEC:
        remain = int(MIN_INTERVAL_SEC - (now - last_ts))
        return {
            "allowed": False,
            "reason": (f"距上一条说说仅 {int(now - last_ts)} 秒，"
                       f"需间隔 ≥ {MIN_INTERVAL_SEC // 3600} 小时（还需 {remain // 60} 分钟）"),
            "used_today": used,
            "next_ok_in": remain,
        }
    return {"allowed": True, "reason": "", "used_today": used, "next_ok_in": 0}


def _record_publish(tid: str, content: str):
    with _lock:
        _state.setdefault("publish_log", []).append({
            "ts": time.time(), "tid": tid or "", "content": (content or "")[:80],
        })
        # 只保留最近 30 天，避免无限增长
        cutoff = time.time() - 30 * 86400
        _state["publish_log"] = [
            r for r in _state["publish_log"]
            if float(r.get("ts") or 0) >= cutoff
        ]
        _save_state(_state)


# ══════════════════════════════════════════════════════════════
#  能力开关
# ══════════════════════════════════════════════════════════════

def _layer_of(action: str):
    for layer, acts in LAYERS.items():
        if action in acts:
            return layer
    return None


def _gate(action: str):
    """检查动作是否被允许。返回 None 表示放行，否则返回错误 dict。"""
    if not _state.get("enabled"):
        return _err("QQ 空间插件未启用（在插件页打开总开关）")
    layer = _layer_of(action)
    if layer is None:
        return _err(f"未知动作 {action}")
    if not _state.get("layers", {}).get(layer):
        return _err(f"能力层「{layer}」未开启 —— 该动作属于{layer}层，需在设置里显式打开")
    return None


# ══════════════════════════════════════════════════════════════
#  9 个动作的实现（全部走 ctx.api_call 唯一出口）
# ══════════════════════════════════════════════════════════════

def _api(action: str, params: dict):
    """调用宿主 OneBot 动作；未注入时给出可读原因。"""
    if _ctx is None:
        return None, "插件上下文未就绪（插件可能未加载）"
    r = _ctx.api_call(action, params)
    if r is None:
        return None, "宿主 api_call 未注入或调用失败（检查驱动连接 / API 是否可用）"
    return r, None


def do_get_qzone_msg_list(params: dict) -> dict:
    p = params or {}
    out = {}
    if p.get("target_uin"):
        try:
            out["target_uin"] = int(p["target_uin"])
        except (TypeError, ValueError):
            return _err(f"target_uin 必须是 QQ 号整数，收到 {p['target_uin']!r}")
    try:
        out["pos"] = int(p.get("pos") or 0)
        out["num"] = int(p.get("num") or 20)
    except (TypeError, ValueError):
        return _err("pos / num 必须是整数")
    if out["pos"] < 0:
        return _err("pos 不能为负")
    if not (1 <= out["num"] <= 100):
        return _err("num 取值 1–100")
    r, e = _api("get_qzone_msg_list", out)
    return _err(e) if e else {"ok": True, "raw": r}


def do_get_qzone_feeds(params: dict) -> dict:
    p = params or {}
    try:
        page_num = int(p.get("page_num") or 1)
        count = int(p.get("count") or 10)
    except (TypeError, ValueError):
        return _err("page_num / count 必须是整数")
    if page_num < 1:
        return _err("page_num 从 1 起")
    if not (1 <= count <= 50):
        return _err("count 取值 1–50")
    r, e = _api("get_qzone_feeds", {"page_num": page_num, "count": count})
    return _err(e) if e else {"ok": True, "raw": r}


def do_like_qzone(params: dict) -> dict:
    p = params or {}
    tid = str(p.get("tid") or "").strip()
    if not tid:
        return _err("缺少 tid（说说 ID，来自 get_qzone_msg_list / send_qzone_msg）")
    out = {"tid": tid, "abstime": int(p.get("abstime") or 0)}
    if p.get("target_uin"):
        try:
            out["target_uin"] = int(p["target_uin"])
        except (TypeError, ValueError):
            return _err("target_uin 必须是 QQ 号整数")
    r, e = _api("like_qzone", out)
    return _err(e) if e else {"ok": True, "raw": r}


def do_unlike_qzone(params: dict) -> dict:
    p = params or {}
    tid = str(p.get("tid") or "").strip()
    if not tid:
        return _err("缺少 tid")
    out = {"tid": tid, "abstime": int(p.get("abstime") or 0)}
    if p.get("target_uin"):
        try:
            out["target_uin"] = int(p["target_uin"])
        except (TypeError, ValueError):
            return _err("target_uin 必须是 QQ 号整数")
    r, e = _api("unlike_qzone", out)
    if e:
        return _err(e)
    return {"ok": True, "raw": r,
            "note": "驱动文档标注「取消赞端点待真机核实」，请以实际返回为准"}


def do_comment_qzone(params: dict) -> dict:
    p = params or {}
    tid = str(p.get("tid") or "").strip()
    content = str(p.get("content") or "").strip()
    if not tid:
        return _err("缺少 tid")
    if not content:
        return _err("评论内容不许为空")
    out = {"tid": tid, "content": content}
    if p.get("target_uin"):
        try:
            out["target_uin"] = int(p["target_uin"])
        except (TypeError, ValueError):
            return _err("target_uin 必须是 QQ 号整数")
    if p.get("images"):
        imgs = p["images"]
        if isinstance(imgs, str):
            imgs = [x for x in imgs.split(",") if x.strip()]
        out["images"] = imgs
    r, e = _api("comment_qzone", out)
    return _err(e) if e else {"ok": True, "raw": r}


def do_send_qzone_msg(params: dict) -> dict:
    """发说说 —— 唯一受频率硬上限约束的动作。"""
    p = params or {}
    content = str(p.get("content") or "").strip()
    if not content:
        return _err("说说正文不许为空")

    uins, e = _norm_uint_list(p.get("target_uins"), "target_uins")
    if e:
        return _err(e)
    ugc_right = p.get("ugc_right", 1)
    verr = _validate_ugc_right(ugc_right, uins)
    if verr:
        return _err(verr)

    # 频率硬上限（发之前先判，避免发出去才发现超限）
    lim = check_publish_limit()
    if not lim["allowed"]:
        return _err(lim["reason"])

    out = {"content": content, "ugc_right": int(ugc_right)}
    if p.get("images"):
        imgs = p["images"]
        if isinstance(imgs, str):
            imgs = [x for x in imgs.split(",") if x.strip()]
        if not isinstance(imgs, (list, tuple)) or not all(str(x).strip() for x in imgs):
            return _err("images 必须是字符串数组（file:// / http:// / base64://）")
        out["images"] = list(imgs)
    if uins:
        out["target_uins"] = uins

    r, err = _api("send_qzone_msg", out)
    if err:
        return _err(err)

    # 成功后记账（用于频率上限）
    tid = ""
    if isinstance(r, dict):
        data = r.get("data") if isinstance(r.get("data"), dict) else r
        tid = str((data or {}).get("tid") or (data or {}).get("id") or "")
    _record_publish(tid, content)
    return {"ok": True, "raw": r, "tid": tid, "content": content,
            "ugc_right": int(ugc_right), "ugc_label": UGC_LABEL[int(ugc_right)]}


def do_delete_qzone_msg(params: dict) -> dict:
    tid = str((params or {}).get("tid") or "").strip()
    if not tid:
        return _err("缺少 tid（来自 get_qzone_msg_list / send_qzone_msg）")
    if not (params or {}).get("confirm"):
        return _err("删除不可逆，需传 confirm=true 表示确认")
    r, e = _api("delete_qzone_msg", {"tid": tid})
    if e:
        return _err(e)
    # 从记账里移除（若存在），避免占着当日额度
    with _lock:
        _state["publish_log"] = [x for x in (_state.get("publish_log") or [])
                                 if str(x.get("tid") or "") != tid]
        _save_state(_state)
    return {"ok": True, "raw": r, "deleted_tid": tid}


def do_set_qzone_msg_right(params: dict) -> dict:
    p = params or {}
    tid = str(p.get("tid") or "").strip()
    if not tid:
        return _err("缺少 tid")
    if "ugc_right" not in p:
        return _err("缺少 ugc_right（必填）")
    uins, e = _norm_uint_list(p.get("target_uins"), "target_uins")
    if e:
        return _err(e)
    verr = _validate_ugc_right(p.get("ugc_right"), uins)
    if verr:
        return _err(verr)
    out = {"tid": tid, "ugc_right": int(p["ugc_right"])}
    if uins:
        out["target_uins"] = uins
    r, err = _api("set_qzone_msg_right", out)
    return _err(err) if err else {"ok": True, "raw": r}


def do_set_qzone_ban(params: dict) -> dict:
    p = params or {}
    if not p.get("user_id"):
        return _err("缺少 user_id（目标 QQ 号）")
    try:
        uid = int(p["user_id"])
    except (TypeError, ValueError):
        return _err("user_id 必须是 QQ 号整数")
    if uid <= 0:
        return _err("user_id 必须为正整数")
    enable = p.get("enable", True)
    if isinstance(enable, str):
        enable = enable.strip().lower() in ("1", "true", "yes", "on")
    r, e = _api("set_qzone_ban", {"user_id": uid, "enable": bool(enable)})
    if e:
        return _err(e)
    return {"ok": True, "raw": r, "user_id": uid,
            "action": "拉黑" if enable else "解除拉黑"}


# 动作名 → 实现（键 = 驱动动作名，透明）
_HANDLERS = {
    "get_qzone_msg_list": do_get_qzone_msg_list,
    "get_qzone_feeds": do_get_qzone_feeds,
    "like_qzone": do_like_qzone,
    "unlike_qzone": do_unlike_qzone,
    "comment_qzone": do_comment_qzone,
    "send_qzone_msg": do_send_qzone_msg,
    "delete_qzone_msg": do_delete_qzone_msg,
    "set_qzone_msg_right": do_set_qzone_msg_right,
    "set_qzone_ban": do_set_qzone_ban,
}


def call(action: str, params: dict | None = None) -> dict:
    """统一入口：门禁 → 参数校验 → 调 API。所有失败都给可读原因。"""
    if action not in _HANDLERS:
        return _err(f"未知动作 {action}（支持：{'、'.join(ALL_ACTIONS)}）")
    gate = _gate(action)
    if gate is not None:
        return gate
    try:
        return _HANDLERS[action](params or {})
    except Exception as e:
        _state["last_error"] = f"{action}: {e}"
        _save_state(_state)
        return _err(f"{action} 执行异常：{e}")


# ══════════════════════════════════════════════════════════════
#  调度器集成：AI 按人设生成内容
# ══════════════════════════════════════════════════════════════

_DEFAULT_PERSONA = (
    "你是 QQ 空间里一个有点文艺、说话简短的年轻人。"
    "写一条 30–80 字的说说，口语、自然、不要 emoji 堆砌，不要话题标签，"
    "只输出说说正文本身，不要引号、不要解释。"
)


def generate_content(extra_hint: str = "") -> str:
    """让 AI 按人设生成一条说说正文。失败返回空串。"""
    if _ctx is None:
        return ""
    persona = (_state.get("persona") or "").strip() or _DEFAULT_PERSONA
    prompt = persona if not extra_hint else f"{persona}\n\n额外要求：{extra_hint}"
    try:
        r = _ctx.ai_chat([{"role": "user", "content": prompt}], max_tokens=200)
    except Exception as e:
        _log(f"AI 生成失败: {e}", "warn")
        return ""
    if r is None:
        return ""
    text = r if isinstance(r, str) else (
        (r.get("content") if isinstance(r, dict) else "") or
        (r.get("text") if isinstance(r, dict) else "") or ""
    )
    text = str(text).strip().strip('"').strip("「」").strip()
    return text[:500]


def publish(hint: str = "", ugc_right: int = 1) -> dict:
    """按人设生成并发布一条说说（调度器 / 聊天触发都走这里）。"""
    content = generate_content(hint)
    if not content:
        return _err("AI 未能生成内容（检查 AI 客户端是否注入，或人设是否为空）")
    return call("send_qzone_msg", {"content": content, "ugc_right": ugc_right})


def on_event(ctx, event_name, payload):
    """订阅调度器广播。qzone.publish → 生成并发布一条说说。"""
    if event_name != "qzone.publish":
        return None
    payload = payload or {}
    if not _state.get("enabled"):
        ctx.log("收到 qzone.publish 但插件未启用，跳过", "warn")
        return None
    hint = str(payload.get("hint") or payload.get("topic") or "")
    ugc_right = payload.get("ugc_right", 1)
    try:
        ugc_right = int(ugc_right)
    except (TypeError, ValueError):
        ugc_right = 1
    r = publish(hint=hint, ugc_right=ugc_right)
    if r.get("ok"):
        ctx.log(f"定时发布成功：{r.get('content', '')[:40]}")
    else:
        ctx.log(f"定时发布失败：{r.get('error')}", "warn")
    return r


# ══════════════════════════════════════════════════════════════
#  聊天触发：被叫去发说说
# ══════════════════════════════════════════════════════════════

import re as _re

_CMD_RE = _re.compile(
    r"^\s*(?:发(?:一)?条说说|发说说|发个说说|发空间|发条空间)\s*[:：]?\s*(.*)$"
)


def on_message(ctx, event) -> str | None:
    """群/私聊里"发条说说 xxx" → 发说说。返回回复文本，或 None 不干预。"""
    if not _state.get("enabled") or not _state.get("layers", {}).get("write"):
        return None
    text = (event or {}).get("text") or ""
    m = _CMD_RE.match(text)
    if not m:
        return None
    body = (m.group(1) or "").strip()
    if body:
        content = body[:500]
    else:
        content = generate_content()
        if not content:
            return "想发说说但一时想不出内容（AI 没生成出来）"
    r = call("send_qzone_msg", {"content": content})
    if r.get("ok"):
        return f"说说发出去了：{content}"
    return f"发说说失败：{r.get('error')}"


# ══════════════════════════════════════════════════════════════
#  ABI 钩子
# ══════════════════════════════════════════════════════════════

def on_load(ctx):
    global _ctx, _state
    _ctx = ctx
    _state = _load_state()
    layers = _state.get("layers", {})
    on = [k for k, v in layers.items() if v]
    ctx.log(f"已加载（总开关 {'开' if _state.get('enabled') else '关'}；"
            f"已开能力层：{'、'.join(on) if on else '无'}）")


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
    "on_message": on_message,
    "on_event": on_event,
}


# ══════════════════════════════════════════════════════════════
#  WebUI 动作（/api/plugins/action → plugin_id=qzone）
# ══════════════════════════════════════════════════════════════

def _a_status(params):
    lim = check_publish_limit()
    return {
        "enabled": bool(_state.get("enabled")),
        "layers": dict(_state.get("layers") or {}),
        "layers_meta": {k: list(v) for k, v in LAYERS.items()},
        "persona": _state.get("persona") or "",
        "limit": {
            "max_per_day": MAX_PER_DAY,
            "min_interval_hours": MIN_INTERVAL_SEC // 3600,
            "used_today": lim["used_today"],
            "can_publish_now": lim["allowed"],
            "block_reason": lim["reason"],
        },
        "publish_log": (_state.get("publish_log") or [])[-10:],
        "last_error": _state.get("last_error") or "",
        "ugc_options": [{"value": k, "label": UGC_LABEL[k]} for k in UGC_RIGHTS],
        "actions": list(ALL_ACTIONS),
    }


def _a_set_config(params):
    p = params or {}
    global _state
    with _lock:
        if "enabled" in p:
            _state["enabled"] = bool(p["enabled"])
        if "persona" in p:
            _state["persona"] = str(p["persona"] or "")
        layers = p.get("layers")
        if isinstance(layers, dict):
            cur = dict(_state.get("layers") or {})
            for k in LAYERS:
                if k in layers:
                    cur[k] = bool(layers[k])
            _state["layers"] = cur
        _save_state(_state)
    return {"ok": True, "status": _a_status({})}


def _a_reset_limit(_params):
    """清空今日发布记录（仅用于调试/误操作恢复，需 confirm）。"""
    if not (_params or {}).get("confirm"):
        return _err("清空发布记录需传 confirm=true")
    with _lock:
        _state["publish_log"] = []
        _save_state(_state)
    return {"ok": True, "note": "发布记录已清空"}


def _a_run(params):
    """试运行：直接执行一个动作，返回**原始 JSON**（不是只显示成功）。"""
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
    # 试运行也走门禁 —— 避免"试运行"变成绕过开关的后门
    return call(action, args)


def _a_generate(params):
    """仅生成内容，不发布（用于预览）。"""
    hint = str((params or {}).get("hint") or "")
    content = generate_content(hint)
    if not content:
        return _err("AI 未能生成内容（检查 AI 客户端 / 人设）")
    return {"ok": True, "content": content}


def execute_action(action, params=None):
    return PLUGIN_ACTIONS.get(action, lambda p: _err(f"未知动作 {action}"))(params or {})


PLUGIN_ACTIONS = {
    "status": _a_status,
    "set_config": _a_set_config,
    "reset_limit": _a_reset_limit,
    "run": _a_run,
    "generate": _a_generate,
}

PLUGIN_UI = {"page": "plugins/qzone/ui.html", "title": "QQ 空间"}

__all__ = [
    "PLUGIN_SPEC", "PLUGIN_MANIFEST", "PLUGIN_HOOKS", "PLUGIN_ACTIONS",
    "PLUGIN_UI", "execute_action", "call", "publish", "check_publish_limit",
    "LAYERS", "ALL_ACTIONS", "MAX_PER_DAY", "MIN_INTERVAL_SEC",
]
