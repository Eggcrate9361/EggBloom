# -*- coding: utf-8 -*-
"""separation.audit —— append-only 审计（设计 §4）。

写 `logs/separation-audit.jsonl`，每行一个 JSON。不做"关闭句柄"接口；
插件/自助层拿到的只是 `log()` 函数，不能删文件内容。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_AUDIT_PATH = Path(os.environ.get("EB_SEPARATION_AUDIT") or (_ROOT / "logs" / "separation-audit.jsonl"))


def path() -> str:
    return str(_AUDIT_PATH)


def log(actor: str, action: str, target: str = "", result: str = "ok",
        **extra: Any) -> dict:
    """追加一条审计；失败也不抛（审计不能反过来把主链弄挂）。"""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "actor": str(actor or "ai"),
        "action": str(action or ""),
        "target": str(target or ""),
        "result": str(result or ""),
    }
    entry.update(extra)
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
    except Exception:
        pass
    return entry


def tail(n: int = 20) -> list:
    """只读最近 n 条（面板用；没有文件返回空）。"""
    try:
        if not _AUDIT_PATH.exists():
            return []
        lines = _AUDIT_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        out = []
        for line in lines[-max(0, int(n)):]:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        return []
