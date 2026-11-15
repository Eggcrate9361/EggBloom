# -*- coding: utf-8 -*-
"""这样脆弱的东西 审计日志（JSONL，保留 2 年）。"""
import json
import os
import time

from .config import DATA_DIR, get_config

AUDIT_DIR = os.path.join(DATA_DIR, "audit")
AUDIT_FILE = os.path.join(AUDIT_DIR, "audit_log.jsonl")

os.makedirs(AUDIT_DIR, exist_ok=True)


def log(action, subject="", level="", data_volume=0, token_usage=0,
        consent_status="", outcome="", operator="", detail=""):
    """追加一条审计记录。"""
    if not get_config().get("audit_enabled", True):
        return
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "action": action,
        "operator": operator or "",
        "subject": str(subject or ""),
        "level": level or "",
        "data_volume": int(data_volume or 0),
        "token_usage": int(token_usage or 0),
        "consent_status": consent_status or "",
        "outcome": outcome or "",
        "detail": str(detail or "")[:500],
        "ethics_version": "v1.0",
    }
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[PersonaDistill] 审计日志写入失败: {e}")


def get_entries(limit=100):
    entries = []
    try:
        if not os.path.exists(AUDIT_FILE):
            return entries
        with open(AUDIT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    # 倒序返回（最新在前）
    entries.reverse()
    return entries[:limit]


def stats():
    """审计统计：最近记录 + 计数。"""
    entries = get_entries(limit=500)
    total = len(entries)
    actions = {}
    for e in entries:
        actions[e.get("action", "?")] = actions.get(e.get("action", "?"), 0) + 1
    return {
        "total": total,
        "actions": actions,
        "recent": entries[:20],
    }
