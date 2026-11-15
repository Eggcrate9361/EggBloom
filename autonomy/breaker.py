# -*- coding: utf-8 -*-
"""autonomy.breaker —— 连续失败熔断（设计 §15 第 4 条）。

同一能力连续失败 2 次 → blocked 30 天；第 3 次不再发起下载。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict


def _path(data_dir) -> Path:
    return Path(data_dir) / "autonomy" / "blocked.json"


def _load(data_dir) -> Dict[str, Any]:
    try:
        p = _path(data_dir)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save(data_dir, data) -> None:
    p = _path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def is_blocked(data_dir, need: str, *, now: float = None) -> Dict[str, Any]:
    now = float(time.time() if now is None else now)
    rec = _load(data_dir).get(str(need or ""))
    if not isinstance(rec, dict):
        return {"blocked": False}
    until = float(rec.get("blocked_until") or 0)
    if until and until > now:
        return {"blocked": True, "until": until, "reason": rec.get("reason", ""),
                "failures": rec.get("failures", 0)}
    return {"blocked": False}


def fail(data_dir, need: str, reason: str = "", *, days: int = 30) -> Dict[str, Any]:
    data = _load(data_dir)
    key = str(need or "")
    rec = data.get(key) or {"failures": 0}
    rec["failures"] = int(rec.get("failures") or 0) + 1
    rec["last_error"] = str(reason or "")
    rec["last_at"] = time.time()
    if rec["failures"] >= 2:
        rec["blocked_until"] = time.time() + days * 86400
        rec["reason"] = str(reason or "连续失败")
    data[key] = rec
    _save(data_dir, data)
    return rec


def success(data_dir, need: str) -> None:
    data = _load(data_dir)
    data.pop(str(need or ""), None)
    _save(data_dir, data)
