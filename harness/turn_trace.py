# -*- coding: utf-8 -*-
"""harness/turn_trace.py —— 一条消息的完整生命周期记录（路线图 3.1）。

## 它做什么

只**记录**，不参与任何决策。每条入站消息/批次沿用它自己的 trace_id，在关键
阶段各写一行 JSONL：

    RECEIVED -> DECIDED -> GENERATED -> SENT -> COMMITTED
    其他终态：SKIPPED / DROPPED / FAILED

一行一条，字段固定（见 `FIELDS`），出问题也不影响主链 —— 这里所有异常都吞掉。
写盘失败只是少一行，不会让一条回复发不出去。

## 为什么放 harness/

bot_core 已经有 `_new_trace_id()` / `_cur_trace()` / `_phase_set()` 三件套，
但只活在内存和普通日志里：`grep trace=xxx` 能捞出一堆行，却拼不回「这条消息
走到哪一步、卡了多久、为什么没发」。本模块就是那条能拼回来的线。

`data/trace/turns-YYYYMMDD.jsonl`，按天分文件，不参与 git（data/ 已忽略）。
关掉：`EGG_TURN_TRACE=0`。

## 字段约定

| 字段 | 含义 |
| --- | --- |
| `ts` | 秒级 Unix 时间（保留 3 位小数） |
| `turn` | turn_id / trace_id，本条消息的生命周期主键 |
| `phase` | 阶段名，见上 |
| `scene` | `group` / `private` |
| `target` | 群号或用户号 |
| `user` | 触发这条消息的人 |
| `ms` | 本阶段耗时（能算出来才写） |
| `why` | 跳过/丢弃/失败的短原因 |
| `extra` | 其它短字段（文本长度、token、插件名等） |
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

_LOCK = threading.Lock()
_ENABLED = str(os.environ.get("EGG_TURN_TRACE", "1")).strip().lower() \
    not in ("0", "false", "no", "off")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR: Optional[str] = os.path.join(_ROOT, "data", "trace")

#: 单行上限，防止异常长文本把日志撑爆
_MAX_FIELD = 200
_TERMINAL_PHASES = {"MERGED", "SENT", "COMMITTED", "SKIPPED", "DROPPED", "FAILED"}
_LAST_PHASE: Dict[str, str] = {}


def enabled() -> bool:
    return _ENABLED


def _dir() -> str:
    """账本目录。

    ★ 2026-11-22：目录必须在**调用时**解析，不在 import 时缓存。生产
    `data/trace/turns-20261116.jsonl` 里出现过测试夹具 `testturn01`
    （target=g1 / user=u1）：测试写过真实账本。三层判定，先命中先用：

      ① `EGG_TURN_TRACE_DIR`（`tests/_sandbox.py` 会设，指到临时目录）
      ② 以 `python tests/xxx.py` 启动 → 落到系统临时目录（不依赖测试自觉调沙箱）
      ③ 其余才是仓库 `data/trace`（生产入口是 bot_core / webui_server，argv 不含 tests）
    """
    env = os.environ.get("EGG_TURN_TRACE_DIR")
    if env:
        return str(env)
    try:
        parts = os.path.abspath(sys.argv[0] or "").replace("\\", "/").split("/")
        if "tests" in parts:
            return os.path.join(tempfile.gettempdir(), "eggbloom_trace_test")
    except Exception:
        pass
    return _DIR or ""


def _path() -> str:
    day = datetime.now().strftime("%Y%m%d")
    return os.path.join(_dir(), f"turns-{day}.jsonl")


def messages_digest(messages) -> str:
    """prompt 指纹：`role + content` 的 sha256 前 12 位。

    为什么不存正文：正文里有群友原话和长期记忆，抄进账本等于把隐私复制到日志里。
    指纹只回答「两次请求是不是同一份 prompt」，不回答「里面写了什么」。
    """
    h = hashlib.sha256()
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        h.update(str(m.get("role") or "").encode("utf-8", "ignore"))
        h.update(b"\x1f")
        h.update(str(m.get("content") or "").encode("utf-8", "ignore"))
        h.update(b"\x1e")
    return h.hexdigest()[:12]


def note_prompt(turn_id: str, messages, model: str = "") -> bool:
    """记一次模型请求的 prompt 指纹（`PROMPT` 阶段）。

    工具链一轮会调多次模型，每次各记一条：想知道「哪一次开始跑偏」，看相邻两条的
    `hash` 与 `msgs` 即可。只记长度与指纹，不记正文。
    """
    if not turn_id:
        return False
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    chars = sum(len(str(m.get("content") or "")) for m in msgs)
    return note(turn_id, "PROMPT", hash=messages_digest(msgs),
                chars=chars, msgs=len(msgs), model=str(model or "")[:40])


def note(turn_id: str, phase: str, **fields: Any) -> bool:
    """记一个阶段。返回是否真的写进去了（失败不抛）。"""
    if not _ENABLED or not turn_id:
        return False
    rec: Dict[str, Any] = {
        "ts": round(time.time(), 3),
        "turn": str(turn_id),
        "phase": str(phase),
    }
    for k, v in fields.items():
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            rec[k] = v
        elif isinstance(v, (int, float)):
            rec[k] = v
        else:
            rec[k] = str(v)[:_MAX_FIELD]
    try:
        line = json.dumps(rec, ensure_ascii=False)
        with _LOCK:
            os.makedirs(_dir(), exist_ok=True)
            with open(_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
            _LAST_PHASE[str(turn_id)] = str(phase)
            if len(_LAST_PHASE) > 5000:
                _LAST_PHASE.pop(next(iter(_LAST_PHASE)))
        return True
    except Exception:
        return False


def is_terminal(turn_id: str) -> bool:
    """Return whether this process has recorded a terminal phase for a turn."""
    with _LOCK:
        return _LAST_PHASE.get(str(turn_id or "")) in _TERMINAL_PHASES


def is_observing(turn_id: str) -> bool:
    """Return whether a turn is waiting for its scheduled second look."""
    with _LOCK:
        return _LAST_PHASE.get(str(turn_id or "")) == "OBSERVING"


def begin(turn_id: str, **fields: Any) -> Dict[str, Any]:
    """记 RECEIVED 并返回一个可传给 `finish()` 的计时器。"""
    t = {"turn": str(turn_id), "t0": time.time()}
    note(turn_id, "RECEIVED", **fields)
    return t


def finish(timer: Optional[Dict[str, Any]], phase: str, **fields: Any) -> bool:
    """记一个终态/中间态，自动带上从 `begin()` 起的耗时。"""
    if not timer:
        return note("", phase, **fields)
    ms = int((time.time() - float(timer.get("t0") or time.time())) * 1000)
    fields.setdefault("ms", ms)
    return note(str(timer.get("turn") or ""), phase, **fields)


def tail(n: int = 20, day: Optional[str] = None) -> list:
    """读最近 n 行（调试用；文件不存在返回空表）。"""
    try:
        p = _path() if not day else os.path.join(
            _dir(), f"turns-{day}.jsonl")
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()[-max(1, int(n)):]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:
        return []
