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

import json
import os
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


def enabled() -> bool:
    return _ENABLED


def _path() -> str:
    day = datetime.now().strftime("%Y%m%d")
    return os.path.join(_DIR or "", f"turns-{day}.jsonl")


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
            os.makedirs(_DIR, exist_ok=True)
            with open(_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception:
        return False


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
            _DIR or "", f"turns-{day}.jsonl")
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()[-max(1, int(n)):]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:
        return []
