# -*- coding: utf-8 -*-
"""harness/model_gateway.py —— 所有模型请求的统一出口（路线图 4.1）。

## 它管什么

    call(url, payload, ...) -> resp

只做四件事，都**不改变**请求内容：

1. **熔断**：连续失败 5 次 → 30 秒内直接拒绝，不让请求堆积（4.4）。
2. **每轮预算**：同一个 turn 最多 N 次请求，超了返回预算错误（4.3 第二层）。
3. **记账**：每次请求写一行 `data/trace/model-calls.jsonl`，字段按路线图 4.2：
   request_id / turn_id / task_type / plugin_id / model / tokens / latency / status。
   —— 这是**请求级**账本，与 token_stats 的"累计成本"互补，不重复计数。
4. **计时**：latency_ms 实测，不用估算。

## 它不管什么

不构造 payload、不改 messages、不选模型 —— 那些是调用方的事。否则每接一个
调用点就要改一次网关，回归面立刻扩大。

## 为什么要有

1.6.0 的模型调用散在 `_call_chat_sync` / `_stream_chat_lines` / 插件注入的
AIClient 里，没有一处能回答「这一轮调了几次模型、谁调的、多快、失败没」。
把出口收口到这一个函数之后，4.3 的分层预算和 4.4 的重试才有落点。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

_LOCK = threading.Lock()
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRACE_DIR = os.path.join(_ROOT, "data", "trace")

_ENABLED = str(os.environ.get("EGG_MODEL_GATEWAY", "1")).strip().lower() \
    not in ("0", "false", "no", "off")

#: 每轮最大模型请求数（含工具轮）；0 = 不限
_TURN_MAX_REQUESTS = int(os.environ.get("EGG_TURN_MAX_MODEL_CALLS", "12") or 0)
#: 连续失败多少次进熔断
_FAIL_THRESHOLD = 5
#: 熔断冷却秒数
_OPEN_SECONDS = 30.0
#: 轮次记录保留时间（清理用）
_TURN_TTL = 600.0


class _State:
    def __init__(self) -> None:
        self.transport: Optional[Callable[..., Dict[str, Any]]] = None
        self.stream_transport: Optional[Callable[..., Any]] = None
        self.turns: Dict[str, Dict[str, float]] = {}
        self.fail_streak = 0
        self.open_until = 0.0


_S = _State()


def set_transport(fn: Callable[..., Dict[str, Any]]) -> None:
    """注入真正的 HTTP 出口（bot_core._post_chat）。避免 harness 反向 import 主链。"""
    _S.transport = fn


def set_stream_transport(fn: Callable[..., Any]) -> None:
    """注入流式出口（bot_core._post_chat_stream，返回 urllib response）。"""
    _S.stream_transport = fn


def configure(*, turn_max_requests: Optional[int] = None,
              fail_threshold: Optional[int] = None) -> None:
    global _TURN_MAX_REQUESTS, _FAIL_THRESHOLD
    if turn_max_requests is not None:
        _TURN_MAX_REQUESTS = max(0, int(turn_max_requests))
    if fail_threshold is not None:
        _FAIL_THRESHOLD = max(1, int(fail_threshold))


def reset() -> None:
    """测试/重启用：清熔断与轮次计数。"""
    with _LOCK:
        _S.turns.clear()
        _S.fail_streak = 0
        _S.open_until = 0.0


def available() -> bool:
    return bool(_ENABLED and _S.transport)


def circuit_open() -> bool:
    return time.time() < _S.open_until


def _path() -> str:
    day = time.strftime("%Y%m%d")
    return os.path.join(_TRACE_DIR, f"model-calls-{day}.jsonl")


def _ledger(rec: Dict[str, Any]) -> None:
    try:
        os.makedirs(_TRACE_DIR, exist_ok=True)
        with _LOCK:
            with open(_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _sweep(now: float) -> None:
    """清理过期轮次，避免 turn 表无限长。调用方持锁。"""
    try:
        dead = [k for k, v in _S.turns.items()
                if now - float(v.get("at") or 0) > _TURN_TTL]
        for k in dead:
            _S.turns.pop(k, None)
    except Exception:
        pass


def call(url: str, payload: Dict[str, Any], *, api_key: str = "", timeout: float = 60,
         meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """统一出口。异常照常抛给调用方（熔断/预算则抛 `ModelBudgetError`）。"""
    meta = dict(meta or {})
    turn = str(meta.get("turn") or "")
    now = time.time()

    if not available():
        # 网关没接好（单测/裁剪环境）：直接透传，不影响主链
        if _S.transport is None:
            raise ModelGatewayError("transport 未注入")
        return _S.transport(url, payload, api_key=api_key, timeout=timeout)

    if circuit_open():
        _ledger({"ts": round(now, 3), "request": uuid.uuid4().hex[:12],
                 "turn": turn, "task": meta.get("task") or "",
                 "plugin": meta.get("plugin") or "host",
                 "model": str(payload.get("model") or ""),
                 "latency_ms": 0, "status": "circuit_open"})
        raise ModelBudgetError("模型熔断中")

    with _LOCK:
        _sweep(now)
        st = _S.turns.setdefault(turn or "_", {"n": 0, "tok": 0, "at": now})
        st["at"] = now
        if turn and _TURN_MAX_REQUESTS and st["n"] >= _TURN_MAX_REQUESTS:
            over = True
        else:
            over = False
    if over:
        _ledger({"ts": round(now, 3), "request": uuid.uuid4().hex[:12],
                 "turn": turn, "task": meta.get("task") or "",
                 "plugin": meta.get("plugin") or "host",
                 "model": str(payload.get("model") or ""),
                 "latency_ms": 0, "status": "turn_budget"})
        raise ModelBudgetError(f"本轮模型请求已达上限 {_TURN_MAX_REQUESTS}")

    rid = uuid.uuid4().hex[:12]
    t0 = time.time()
    try:
        resp = _S.transport(url, payload, api_key=api_key, timeout=timeout)
    except Exception as e:
        with _LOCK:
            _S.fail_streak += 1
            if _S.fail_streak >= _FAIL_THRESHOLD:
                _S.open_until = time.time() + _OPEN_SECONDS
        _ledger({"ts": round(now, 3), "request": rid, "turn": turn,
                 "task": meta.get("task") or "", "plugin": meta.get("plugin") or "host",
                 "model": str(payload.get("model") or ""),
                 "latency_ms": int((time.time() - t0) * 1000),
                 "status": "error", "error": f"{type(e).__name__}: {e}"[:160]})
        raise

    latency_ms = int((time.time() - t0) * 1000)
    usage = resp.get("usage") if isinstance(resp, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    tokens = int(usage.get("total_tokens") or 0)
    with _LOCK:
        st["n"] += 1
        st["tok"] += tokens
        _S.fail_streak = 0
    _ledger({"ts": round(now, 3), "request": rid, "turn": turn,
             "task": meta.get("task") or "", "plugin": meta.get("plugin") or "host",
             "scene": meta.get("scene") or "", "lane": meta.get("lane") or "",
             "model": str(payload.get("model") or ""),
             "prompt_tokens": int(usage.get("prompt_tokens") or 0),
             "completion_tokens": int(usage.get("completion_tokens") or 0),
             "total_tokens": tokens,
             "latency_ms": latency_ms, "status": "ok"})
    return resp


def stream(url: str, payload: Dict[str, Any], *, api_key: str = "", timeout: float = 60,
           meta: Optional[Dict[str, Any]] = None):
    """流式请求：熔断/预算/打开连接，返回 transport 给的 response 对象。

    usage 在流末尾才出现，这里不记 token（status 记 `stream`）；流式路径自己的
    token_stats 记账保持不变 —— 两本账不重复计数。
    """
    meta = dict(meta or {})
    turn = str(meta.get("turn") or "")
    now = time.time()
    if not _ENABLED or _S.stream_transport is None:
        raise ModelGatewayError("stream transport 未注入")
    if circuit_open():
        _ledger({"ts": round(now, 3), "request": uuid.uuid4().hex[:12], "turn": turn,
                 "task": meta.get("task") or "", "plugin": meta.get("plugin") or "host",
                 "model": str(payload.get("model") or ""), "latency_ms": 0,
                 "status": "circuit_open", "stream": True})
        raise ModelBudgetError("模型熔断中")

    with _LOCK:
        _sweep(now)
        st = _S.turns.setdefault(turn or "_", {"n": 0, "tok": 0, "at": now})
        st["at"] = now
        over = bool(turn and _TURN_MAX_REQUESTS and st["n"] >= _TURN_MAX_REQUESTS)
    if over:
        _ledger({"ts": round(now, 3), "request": uuid.uuid4().hex[:12], "turn": turn,
                 "task": meta.get("task") or "", "plugin": meta.get("plugin") or "host",
                 "model": str(payload.get("model") or ""), "latency_ms": 0,
                 "status": "turn_budget", "stream": True})
        raise ModelBudgetError(f"本轮模型请求已达上限 {_TURN_MAX_REQUESTS}")

    rid = uuid.uuid4().hex[:12]
    t0 = time.time()
    try:
        resp = _S.stream_transport(url, payload, api_key=api_key, timeout=timeout)
    except Exception as e:
        with _LOCK:
            _S.fail_streak += 1
            if _S.fail_streak >= _FAIL_THRESHOLD:
                _S.open_until = time.time() + _OPEN_SECONDS
        _ledger({"ts": round(now, 3), "request": rid, "turn": turn,
                 "task": meta.get("task") or "", "plugin": meta.get("plugin") or "host",
                 "model": str(payload.get("model") or ""),
                 "latency_ms": int((time.time() - t0) * 1000),
                 "status": "error", "stream": True,
                 "error": f"{type(e).__name__}: {e}"[:160]})
        raise
    with _LOCK:
        st["n"] += 1
        _S.fail_streak = 0
    _ledger({"ts": round(now, 3), "request": rid, "turn": turn,
             "task": meta.get("task") or "", "plugin": meta.get("plugin") or "host",
             "scene": meta.get("scene") or "", "lane": meta.get("lane") or "",
             "model": str(payload.get("model") or ""),
             "latency_ms": int((time.time() - t0) * 1000),
             "status": "stream", "stream": True})
    return resp


def call_raw(url: str, payload: Dict[str, Any], *,
             headers: Optional[Dict[str, str]] = None, timeout: float = 60,
             meta: Optional[Dict[str, Any]] = None):
    """非标准头 / 非 JSON 返回的调用（免费车道）用：返回 `(status, text)`。

    与 `call()` 共用熔断/预算/账本，但请求与响应原样透传 —— 免费车道用的是
    指纹头（x-opencode-*）和 SSE 文本，不能被网关重新包装。
    """
    meta = dict(meta or {})
    turn = str(meta.get("turn") or "")
    now = time.time()
    if not _ENABLED:
        raise ModelGatewayError("网关未启用")
    if circuit_open():
        raise ModelBudgetError("模型熔断中")
    with _LOCK:
        _sweep(now)
        st = _S.turns.setdefault(turn or "_", {"n": 0, "tok": 0, "at": now})
        st["at"] = now
        over = bool(turn and _TURN_MAX_REQUESTS and st["n"] >= _TURN_MAX_REQUESTS)
    if over:
        _ledger({"ts": round(now, 3), "request": uuid.uuid4().hex[:12], "turn": turn,
                 "task": meta.get("task") or "", "plugin": meta.get("plugin") or "host",
                 "model": str(payload.get("model") or ""), "latency_ms": 0,
                 "status": "turn_budget", "raw": True})
        raise ModelBudgetError(f"本轮模型请求已达上限 {_TURN_MAX_REQUESTS}")

    rid = uuid.uuid4().hex[:12]
    t0 = time.time()
    import ssl
    import urllib.error
    import urllib.request

    def _do():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers or {}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read().decode("utf-8", "replace")[:400]
            except Exception:
                return e.code, str(e)
        except Exception as e:
            return 0, f"{type(e).__name__}: {e}"

    try:
        status, text = _do()
    except Exception as e:
        with _LOCK:
            _S.fail_streak += 1
            if _S.fail_streak >= _FAIL_THRESHOLD:
                _S.open_until = time.time() + _OPEN_SECONDS
        _ledger({"ts": round(now, 3), "request": rid, "turn": turn,
                 "task": meta.get("task") or "", "plugin": meta.get("plugin") or "host",
                 "model": str(payload.get("model") or ""),
                 "latency_ms": int((time.time() - t0) * 1000),
                 "status": "error", "raw": True, "error": f"{type(e).__name__}: {e}"[:160]})
        raise
    with _LOCK:
        st["n"] += 1
        _S.fail_streak = 0
    _ledger({"ts": round(now, 3), "request": rid, "turn": turn,
             "task": meta.get("task") or "", "plugin": meta.get("plugin") or "host",
             "scene": meta.get("scene") or "", "lane": meta.get("lane") or "",
             "model": str(payload.get("model") or ""),
             "latency_ms": int((time.time() - t0) * 1000),
             "status": str(status), "raw": True})
    return status, text


def stats() -> Dict[str, Any]:
    """当前状态（排查/前端用）：熔断、在途轮次、每轮计数。"""
    with _LOCK:
        return {
            "enabled": _ENABLED,
            "transport": bool(_S.transport),
            "circuit_open": circuit_open(),
            "fail_streak": int(_S.fail_streak),
            "turn_max_requests": int(_TURN_MAX_REQUESTS),
            "turns": {k: {"requests": int(v.get("n") or 0),
                          "tokens": int(v.get("tok") or 0)}
                      for k, v in list(_S.turns.items())[:20]},
        }


class ModelGatewayError(RuntimeError):
    """网关自己的错误（未接通等）。"""


class ModelBudgetError(ModelGatewayError):
    """预算/熔断拒绝：调用方应走降级，而不是重试。"""
