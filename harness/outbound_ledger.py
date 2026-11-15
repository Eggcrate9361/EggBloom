# -*- coding: utf-8 -*-
"""harness/outbound_ledger.py —— 出站动作账本（1.6.0 出站动作统一）。

## 这一层解决什么

1.6.0 已有 `harness/outbound.py` 作为**唯一发送出口**（一个 worker 串行队列），
但"发出去之后"这件事散在四处：主链回复写 `chat_history`、插件发送写
`chat_history` + `chat_logs`、表情包写 `_note_sent_meme`、戳一戳只写
`self_actions`。后果是三个真机问题同根：

* 12:00 主动问「你那边现在几点了」，12:31 对方回「12点」，模型回「12点干嘛？」
  —— 不是没写进上下文，是那条记录没有 `kind / by / result`，模型不知道
  「这是我自己主动提的问、还等一个回答」；而 `_build_cached_messages` 里
  强化块用 `consistency_own_window_min`（默认 30 分钟）硬切，
  12:00:36 → 12:31:11 = 30 分 35 秒，刚好被判过期。
* 表情 / 图片 / 戳一戳没有统一归属，日志里查不出「这张图是谁决定发的」。
* 系统侧随机发图（prob_bonus、回敬、退场、戳一戳）与模型显式发图混在一起。

## 设计边界（重要）

* **不新建第二个 send()**。发送仍只经 `harness/outbound.py::send()`；
  本模块在 worker 处理完一个 job 之后旁路记账，任何异常都吞掉，
  绝不改变 `job["ok"]` / 返回值 / 发送时序。
* **不改 `chat_history` 的写入形状**。账本是「事实记录 + prompt 投影」，
  投影产物作为一条 system 消息注入，与现有历史行并存、不重复、不互斥。
* 落盘 JSONL，路径跟随 `EGGBLOOM_USER_DATA`；测试可用
  `EGGBLOOM_OUTBOUND_LEDGER` 单独重定向，不会污染真机数据。

## 事件字段

`{ts, scene, target, kind, action, content_ref, by, result, question}`

* `by`: `model`（主链模型回复）/ `rule`（系统侧规则或随机）/ `plugin`（插件，
  含主动消息）/ `user`（人工或工具指定）。默认 `model`。
* `kind`: `text` / `image` / `video` / `poke`（与 outbound job 一致）。
* `action`: 按**本次动作意图**切，不按文件格式切：
  `文本` / `表情` / `图片·理解` / `图片·采集` / `图片·生成` / `图片·展示` /
  `视频` / `戳一戳`。
* `question`: 文本里是不是一句提问（用于"还等回答"的判定）。
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

#: 发送链上的调用方标记。submit() 在同线程里读出来存进 job，
#: 这样 worker（另一线程）也能拿到来源，不需要插件改 ABI。
_ORIGIN: contextvars.ContextVar = contextvars.ContextVar(
    "eggbloom_outbound_origin", default="")

_LOCK = threading.RLock()
#: 内存里保留最近多少条（查询只用内存，落盘只做归档/复盘）
_MEM_CAP = 400
_MEM: List[Dict[str, Any]] = []
_MAX_FILE_BYTES = 2 * 1024 * 1024

#: 待接住的主动消息最长挂多久（小时）。远宽于原来的 30 分钟硬窗；
#: 真正的终止条件是「之后出现过 by=model 的出站」，不是时间。
DEFAULT_PENDING_MAX_HOURS = 12.0

#: 真机主动消息常常不带问号（「你那边现在几点了」），只认 `?` 会漏判。
#: 疑问词出现在末尾 6 字内即可；`content_ref` 已裁到 160 字，不会长到误命中。
_QUESTION_RE = re.compile(
    r"[?？]|"
    r"(?:几点|多少|是不是|有没有|怎么|为啥|为什么|什么|哪|谁|吗|呢|吧)"
    r"[^\s]{0,3}\s*$"
)


# --------------------------------------------------------------------------
# 调用方标记
# --------------------------------------------------------------------------
def mark(origin: str) -> Any:
    """标注本次调用链的出站来源，返回 reset 用的 token。

    用法（务必 try/finally）：

        tok = outbound_ledger.mark("plugin:proactivity")
        try:
            smart_send(...)
        finally:
            outbound_ledger.reset(tok)
    """
    return _ORIGIN.set(str(origin or ""))


def reset(token) -> None:
    try:
        _ORIGIN.reset(token)
    except Exception:
        pass


def current_origin() -> str:
    try:
        return str(_ORIGIN.get() or "")
    except Exception:
        return ""


# --------------------------------------------------------------------------
# 落盘
# --------------------------------------------------------------------------
def ledger_path() -> str:
    """账本文件路径。测试可用 EGGBLOOM_OUTBOUND_LEDGER 单独隔离。"""
    override = os.environ.get("EGGBLOOM_OUTBOUND_LEDGER")
    if override:
        return str(override)
    root = (os.environ.get("EGGBLOOM_USER_DATA")
            or os.path.join(os.path.expanduser("~"), "AppData", "Local", "EggBloom_v3"))
    return os.path.join(root, "outbound_ledger.jsonl")


def _append(event: Dict[str, Any]) -> None:
    p = ledger_path()
    try:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        if os.path.isfile(p) and os.path.getsize(p) > _MAX_FILE_BYTES:
            try:
                os.replace(p, p + ".1")
            except Exception:
                pass
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_recent(limit: int = 400) -> List[Dict[str, Any]]:
    """从落盘文件读回最近若干条（进程重启后内存缓冲为空时用）。"""
    p = ledger_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            rows = f.readlines()[-max(1, int(limit)):]
        out = []
        for r in rows:
            try:
                e = json.loads(r)
                if isinstance(e, dict):
                    out.append(e)
            except Exception:
                continue
        return out
    except Exception:
        return []


# --------------------------------------------------------------------------
# 从 outbound job 提取内容（绝不把 base64 图片写进账本）
# --------------------------------------------------------------------------
def _content_ref(kind: str, payload: dict) -> str:
    try:
        t = payload.get("text")
        if t:
            return str(t)[:160]
        m = payload.get("message")
        if isinstance(m, str):
            return m[:160]
        if isinstance(m, (list, tuple)):
            parts = []
            for seg in m[:6]:
                if not isinstance(seg, dict):
                    continue
                d = seg.get("data") or {}
                st = str(seg.get("type") or "")
                if st == "text":
                    parts.append(str(d.get("text") or ""))
                elif st in ("image", "video"):
                    fp = str(d.get("file") or "")
                    if fp.startswith("base64://"):
                        parts.append("[%s:内嵌]" % st)
                    else:
                        parts.append("[%s:%s]" % (st, os.path.basename(fp.replace("\\", "/"))))
                else:
                    parts.append("[%s]" % (st or "?"))
            return "".join(parts)[:160]
    except Exception:
        pass
    return ""


def _action_of(kind: str, payload: dict, origin: str) -> str:
    """按动作意图归类。origin 可显式带 `action=xxx` 覆盖。"""
    try:
        m = re.search(r"action=([\w\u4e00-\u9fff·/]+)", origin or "")
        if m:
            return m.group(1)
    except Exception:
        pass
    if kind == "poke":
        return "戳一戳"
    if kind == "video":
        return "视频"
    if kind == "text":
        return "文本"
    if kind == "image":
        # sub_type=1 是表情包（QQ 表情），其余按图片展示处理。
        try:
            raw = payload.get("message")
            if isinstance(raw, (list, tuple)):
                for seg in raw:
                    if isinstance(seg, dict) and seg.get("type") == "image":
                        d = seg.get("data") or {}
                        if str(d.get("sub_type") or "") in ("1", "True", "true"):
                            return "表情"
        except Exception:
            pass
        return "图片·展示"
    return str(kind or "未知")


def _by_of(origin: str) -> str:
    """来源判定。**没打标就是 unknown，不许默认成 model。**

    ★ 2026-11-15：默认值原来是 `model`，而全仓库只有主链与插件打了标 ——
    入群欢迎、撤回反馈、慢回填充这些规则文本全被记成"模型自己发的"，
    进而把 `pending_outreach()` 里挂着的那条真实主动提问误判成"已接住"。
    宁可记 unknown（不参与挂起/清除判定），也不要伪造归因。

    ★ 2026-11-15 补 `agent`：QQ Agent / 子 agent 的答复是**模型写的**，但不是
    "主链那一轮的正式回复"。归成 `model` 会把挂着的主动提问误判成已接住，
    归成 `rule` 又是撒谎。单列一类，`pending_outreach` 照旧只认 `model`。
    """
    o = str(origin or "")
    for b in ("model", "agent", "rule", "plugin", "user"):
        if o.startswith(b):
            return b
    return "unknown"


def build_event(job: dict) -> Dict[str, Any]:
    """把一个 outbound job 转成账本事件（纯函数，便于单测）。"""
    kind = str(job.get("kind") or "text")
    target = str(job.get("target") or "")
    is_group = bool(job.get("is_group"))
    payload = job.get("payload") or {}
    origin = str(job.get("origin") or "")
    ref = _content_ref(kind, payload)
    ok = bool(job.get("ok"))
    q = bool(kind == "text" and ok and _QUESTION_RE.search(ref or ""))
    return {
        "ts": float(job.get("ts") or time.time()),
        "scene": "group" if is_group else "private",
        "target": target,
        "kind": kind,
        "action": _action_of(kind, payload, origin),
        "content_ref": ref,
        "by": _by_of(origin),
        "origin": origin,
        "result": "ok" if ok else "failed",
        "question": q,
    }


def record_job(job: dict, *, log_fn=None) -> Optional[Dict[str, Any]]:
    """worker 处理完一个 job 后调用。**所有异常都吞掉**。"""
    try:
        ev = build_event(job)
        with _LOCK:
            _MEM.append(ev)
            if len(_MEM) > _MEM_CAP:
                del _MEM[: len(_MEM) - _MEM_CAP]
            _append(ev)
        if log_fn is not None:
            try:
                log_fn(log_line(ev))
            except Exception:
                pass
        return ev
    except Exception:
        return None


def log_line(ev: Dict[str, Any]) -> str:
    """统一归因日志：一条出站一行，来源/动作/结果齐全。"""
    try:
        tm = time.strftime("%H:%M:%S", time.localtime(float(ev.get("ts") or 0)))
        ref = str(ev.get("content_ref") or "")[:40].replace("\n", " ")
        return ("[动作:%s] %s %s:%s by=%s result=%s %s"
                % (ev.get("action"), tm, ev.get("scene"), ev.get("target"),
                   ev.get("by"), ev.get("result"), ("「%s」" % ref) if ref else ""))
    except Exception:
        return "[动作:?] 日志生成失败"


# --------------------------------------------------------------------------
# 上下文投影：把"我主动开的口、还等回答"交给模型
# --------------------------------------------------------------------------
def _events(scene: str, target: str) -> List[Dict[str, Any]]:
    with _LOCK:
        rows = [dict(e) for e in _MEM
                if e.get("scene") == scene and e.get("target") == target
                and e.get("result") == "ok"]
    if not rows:
        rows = [e for e in load_recent()
                if e.get("scene") == scene and e.get("target") == target
                and e.get("result") == "ok"]
    return rows


def pending_outreach(scene: str, target: str, *,
                     max_age_hours: float = DEFAULT_PENDING_MAX_HOURS) -> Optional[Dict[str, Any]]:
    """找「最近一条还没有被主链回复接住的主动消息」。

    终止条件是**之后出现过 by=model 的成功出站**（说明这轮已经答过了），
    不是时间窗。时间只作为兜底，防止陈年旧问一直挂在 prompt 里。
    """
    rows = _events(scene, target)
    if not rows:
        return None
    rows.sort(key=lambda e: float(e.get("ts") or 0))
    found = None
    for e in rows:
        by = str(e.get("by") or "")
        if by == "plugin" and str(e.get("kind") or "") == "text":
            found = e
        elif by == "model" and found is not None:
            # 只有**主链模型回复**才算接住。规则文本（入群欢迎 / 撤回反馈 /
            # 慢回填充）与 plugins 的其它动作都不该清掉待接住的主动提问。
            found = None
    if not found:
        return None
    try:
        age_h = (time.time() - float(found.get("ts") or 0)) / 3600.0
        if max_age_hours > 0 and age_h > max_age_hours:
            return None
    except Exception:
        pass
    return found


def context_block(cache_key: str, *,
                  max_age_hours: float = DEFAULT_PENDING_MAX_HOURS) -> str:
    """给 `_build_cached_messages` 的 system 块。cache_key = `p_<qq>` / `g_<gid>`。

    返回空串表示这轮没有需要接住的主动消息（调用方直接跳过即可）。
    """
    try:
        if not cache_key or cache_key[:2] not in ("p_", "g_"):
            return ""
        scene = "group" if cache_key.startswith("g_") else "private"
        target = cache_key[2:]
        ev = pending_outreach(scene, target, max_age_hours=max_age_hours)
        if not ev:
            return ""
        tm = time.strftime("%H:%M", time.localtime(float(ev.get("ts") or 0)))
        txt = str(ev.get("content_ref") or "").strip()[:120]
        if not txt:
            return ""
        hint = ("这是一句提问，对方现在这句话很可能就是在回答它；"
                "先接住它，别当成新话题、也别重复问同一个问题。"
                if ev.get("question") else
                "对方现在这句话可能是接着它说的，别当成完全新开的话题。")
        return ("【我主动开口说过的话（原文，注意这是**我自己**说的）】\n"
                "%s 我说：「%s」\n%s" % (tm, txt, hint))
    except Exception:
        return ""


# --------------------------------------------------------------------------
# 测试/运维
# --------------------------------------------------------------------------
def reset_memory() -> None:
    with _LOCK:
        _MEM.clear()


def recent(scene: str = "", target: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    with _LOCK:
        rows = [dict(e) for e in _MEM]
    if scene:
        rows = [e for e in rows if e.get("scene") == scene]
    if target:
        rows = [e for e in rows if e.get("target") == target]
    return rows[-max(1, int(limit)):]
