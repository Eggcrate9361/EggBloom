# -*- coding: utf-8 -*-
"""harness/plugin_bridge.py —— typed 钩子桥（补上 ABI v2 承诺却从未派发的缺口）。

## 为什么需要这个模块

一个已核实的事实：
ABI v2 在 `plugins/abi.py` 里声明了 `HOOK_BEFORE_REPLY` / `HOOK_AFTER_REPLY`
（契约分别是 `(ctx, event) -> Optional[dict]` 与 `(ctx, event, reply) -> Optional[str]`），
`plugin_manager` 也实现了 `has_typed_hook()` / `execute_typed_hooks()`，
**但 `bot_core.py` 从来没有派发过它们**（出现次数 = 0）。

后果：任何 spec 插件写 `on_before_reply` 都是死代码 —— 注册成功、永不执行。
harness 要往 prompt 注入计划/工具结果/观察，也必须走这两个钩子，所以这是前置条件。

## 设计原则（与 handbook 的验收标准对齐）

1. **并列追加，不动旧逻辑** —— 旧式 `execute_hooks("before_reply", ...)` 一行不改；
   typed 派发只在它旁边追加一次。这样"现有 5 个旧式钩子行为完全不变"是**结构上保证**的。
2. **失败隔离** —— 任何异常降级为受控结果 + 一条日志，绝不向主循环抛
   （对应 DSH 不变量 A10）。
3. **返回值归一** —— typed 钩子返回 `Optional[dict]`，但插件作者可能写成
   `{"action":"inject","content":...}`、`{"action":"skip","reason":...}`、
   `{"action":"override","reply":...}`，也可能只返回一个字符串或 `None`。
   本模块把它们**统一成与旧式完全相同的 action 协议**，让调用方无需区分两套。
4. **无插件时零成本** —— `has_typed_hook()` 为假时立即返回，不做任何扫描。

## 与旧式的语义差（必须知道，避免两套 skip 打架）

旧式 `before_reply` 是 `execute_hooks`（返回 `[(name, result)]`，**全部**收集）；
typed 是 `execute_typed_hooks`（同样全部收集，但只针对 **spec 插件**）。
两者收集到的是**不同插件集合**，所以它们的 action 必须汇聚到**同一个聚合器**里
再统一裁决 —— 这正是 `merge_before_reply()` 存在的原因。若分别处理，
"旧式说 skip、typed 说 override"这种组合会随机取胜。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

#: typed 钩子名（与 plugins/abi.py 保持一致；此处不 import 以免循环依赖）
HOOK_BEFORE_REPLY = "on_before_reply"
HOOK_AFTER_REPLY = "on_after_reply"

#: 合法的 action（与旧式协议一致）
_ACTIONS = ("inject", "skip", "override")

BEFORE_REPLY_HOOKS = (HOOK_BEFORE_REPLY, "before_reply")
AFTER_REPLY_HOOKS = (HOOK_AFTER_REPLY, "after_reply")


# ══════════════════════════════════════════════════════════════════
# 归一化
# ══════════════════════════════════════════════════════════════════
def normalize_result(name: str, result: Any) -> Optional[Dict[str, Any]]:
    """把 typed 钩子的任意返回值归一为旧式 action 协议。

    容忍的形态（都见过或都可能被插件作者写出来）：
      · None                      → 丢弃（= 不干预）
      · {"action": "inject", ...} → 原样透传（补全 content 字段）
      · {"action": "skip",   ...} → 原样透传（补全 reason 字段）
      · {"action": "override","reply": ...} → 原样透传
      · "一段文本"                 → 视为 inject
      · {"content": "..."}         → 补 action=inject
      · {"reply": "..."}           → 补 action=override
      · {"skip": True, ...}        → 补 action=skip
      · {"prompt_blocks": [...]}   → 视为 inject，blocks 合并成 content
      · 列表                       → 取第一个可归一的元素（宽容）
      · 其它不可识别               → 丢弃并记入 reason（宁可不动，不可乱动）
    """
    if result is None:
        return None

    if isinstance(result, (list, tuple)):
        for item in result:
            norm = normalize_result(name, item)
            if norm is not None:
                return norm
        return None

    if isinstance(result, str):
        s = result.strip()
        if not s:
            return None
        return {"action": "inject", "content": s, "_plugin": name}

    if not isinstance(result, dict):
        return None

    out = dict(result)
    out.setdefault("_plugin", name)

    action = out.get("action")

    # 无显式 action → 按字段推断（顺序体现优先级：skip > override > inject）
    if action not in _ACTIONS:
        if out.get("skip") is True or out.get("cancel") is True:
            action = "skip"
        elif out.get("reply") is not None:
            action = "override"
        elif out.get("content") is not None or out.get("prompt_blocks"):
            action = "inject"
        else:
            return None
    out["action"] = action

    # 字段补全（调用方按固定字段读取，缺字段会 KeyError）
    if action == "inject":
        content = out.get("content")
        blocks = out.get("prompt_blocks") or []
        if blocks:
            joined = "\n".join(str(b) for b in blocks if str(b).strip())
            content = (joined + ("\n" + str(content) if content else "")) if joined else content
        if not (content or "").strip():
            return None
        out["content"] = content
    elif action == "skip":
        out.setdefault("reason", "")
    else:  # override
        if out.get("reply") is None:
            return None

    return out


# ══════════════════════════════════════════════════════════════════
# 派发
# ══════════════════════════════════════════════════════════════════
def dispatch_before_reply(pm: Any, *, message: str, group_id: str = "",
                          context: Optional[dict] = None,
                          log_fn=None) -> List[Tuple[str, Dict[str, Any]]]:
    """派发 typed `on_before_reply`，返回归一化后的 `[(plugin_name, action_dict)]`。

    无 pm / 无注册插件 / 任何异常 → 返回 `[]`，**不抛**。
    这是"并列追加"的那一次调用：调用方把返回值和旧式结果一起交给
    `merge_before_reply()` 统一裁决。
    """
    if pm is None:
        return []
    try:
        if not pm.has_typed_hook(HOOK_BEFORE_REPLY):
            return []
    except Exception:
        return []

    event = {
        "message": message,
        "group_id": group_id,
        "context": context or {},
        "scope": "group" if group_id else "private",
    }

    out: List[Tuple[str, Dict[str, Any]]] = []
    try:
        results = pm.execute_typed_hooks(HOOK_BEFORE_REPLY, event=event)
    except Exception as e:                       # 桥自身失败也必须隔离
        _log(log_fn, f"[plugin_bridge] dispatch on_before_reply 失败: {e}")
        return []

    for name, res in (results or []):
        norm = normalize_result(name, res)
        if norm is not None:
            out.append((name, norm))
    return out


def dispatch_after_reply(pm: Any, *, message: str, group_id: str = "",
                         reply: str, context: Optional[dict] = None,
                         log_fn=None) -> Optional[str]:
    """派发 typed `on_after_reply`，返回**替换后的回复文本**（None = 不修改）。

    契约：`(ctx, event={...}, reply=...) -> Optional[str]`
      · 返回字符串       → 作为新的回复文本（空串视为"抑制发送"，与原语义一致）
      · 返回 dict        → 取 reply/text/content 字段；`{"action":"override"}` 亦支持
      · 返回 None / 其它 → 不改动
    优先级：按 priority 升序，**首个有效结果生效**（与旧式 `execute_hook` 短路一致）。
    """
    if pm is None:
        return None
    try:
        if not pm.has_typed_hook(HOOK_AFTER_REPLY):
            return None
    except Exception:
        return None

    event = {
        "message": message,
        "group_id": group_id,
        "context": context or {},
        "scope": "group" if group_id else "private",
    }

    try:
        results = pm.execute_typed_hooks(HOOK_AFTER_REPLY, event=event, reply=reply)
    except Exception as e:
        _log(log_fn, f"[plugin_bridge] dispatch on_after_reply 失败: {e}")
        return None

    for name, res in (results or []):
        if res is None:
            continue
        if isinstance(res, str):
            return res
        if isinstance(res, dict):
            for k in ("reply", "text", "content"):
                v = res.get(k)
                if isinstance(v, str):
                    return v
        _log(log_fn, f"[plugin_bridge] {name}.on_after_reply 返回值无法识别"
                     f"（{type(res).__name__}），已忽略")
    return None


# ══════════════════════════════════════════════════════════════════
# 聚合：让旧式与 typed 的结果在**同一处**裁决
# ══════════════════════════════════════════════════════════════════
def merge_before_reply(legacy: Any, typed: Any, *, priority_of=None):
    """合并旧式与 typed 的 before_reply 结果，按统一规则裁决。

    参数
        legacy : 旧式 `execute_hooks(...)` 返回值，`[(name, dict)]`（可能为 None）
        typed  : `dispatch_before_reply(...)` 返回值，`[(name, dict)]`
        priority_of : `name -> int`；缺省时所有插件视为 50

    裁决规则（与 bot_core 既有语义逐条对齐，不引入新行为）
        skip     : **单调**——任一来源给出 skip 即 skip（来自 Hermes/DSH 的
                   "guard 拒绝后下游无法推翻"；这也修掉了原实现里
                   "同一集合内 skip 与 inject 并存时随机取胜"的隐患）
        override : 取 priority 最小者（数值小 = 优先级高）
        inject   : 全部保留，按 priority 升序拼接
    返回：`{"skip": [reason...], "override": str|None, "injects": [(prio, name, content)]}`
    """
    def _prio(name):
        if priority_of is None:
            return 50
        try:
            return int(priority_of(name) or 50)
        except Exception:
            return 50

    skips: List[str] = []
    overrides: List[Tuple[int, str, str]] = []
    injects: List[Tuple[int, str, str]] = []

    for bucket in (legacy or [], typed or []):
        for item in bucket:
            if not (isinstance(item, (list, tuple)) and len(item) == 2):
                continue
            name, res = item
            if not isinstance(res, dict):
                continue
            action = res.get("action")
            if action == "skip":
                skips.append(f"{name}:{res.get('reason', '')}")
            elif action == "override":
                rp = res.get("reply")
                if isinstance(rp, str) and rp:
                    overrides.append((_prio(name), name, rp))
            elif action == "inject":
                content = res.get("content")
                if isinstance(content, str) and content.strip():
                    injects.append((_prio(name), name, content))

    overrides.sort(key=lambda x: x[0])
    injects.sort(key=lambda x: x[0])
    return {
        "skip": skips,
        "override": overrides[0][2] if overrides else None,
        "override_by": overrides[0][1] if overrides else None,
        "injects": injects,
    }


def apply_after_reply_override(current: str, replacement: Optional[str],
                               *, log_fn=None, tag: str = "") -> str:
    """把 typed after_reply 的返回安全地套到当前回复上。

    · replacement 为 None       → 原样返回
    · replacement 与 current 同 → 原样返回（避免无意义日志）
    · replacement 为空串        → 视为**抑制投递**，返回空串（调用方据此不发）
    """
    if replacement is None:
        return current
    if replacement == current:
        return current
    if replacement == "":
        _log(log_fn, f"{tag}[plugin_bridge] on_after_reply 返回空串 → 抑制发送")
        return ""
    _log(log_fn, f"{tag}[plugin_bridge] on_after_reply 覆盖回复: {replacement[:60]}")
    return replacement


def _log(log_fn, msg: str) -> None:
    if log_fn is None:
        return
    try:
        log_fn(msg)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
# 自检（供 main 启动时打一行诊断；无插件时静默）
# ══════════════════════════════════════════════════════════════════
def probe(pm: Any) -> Dict[str, Any]:
    """报告 typed 钩子的接线状态，用于启动日志与 /api 诊断。"""
    info = {"before_reply": False, "after_reply": False, "plugins": []}
    if pm is None:
        return info
    for key, hook in (("before_reply", HOOK_BEFORE_REPLY),
                      ("after_reply", HOOK_AFTER_REPLY)):
        try:
            info[key] = bool(pm.has_typed_hook(hook))
        except Exception:
            info[key] = False
    try:
        for name, e in (getattr(pm, "_plugins", {}) or {}).items():
            hooks = (e.get("hooks") or {})
            hit = [h for h in (HOOK_BEFORE_REPLY, HOOK_AFTER_REPLY) if h in hooks]
            if hit:
                info["plugins"].append({"name": name, "hooks": hit})
    except Exception:
        pass
    return info
