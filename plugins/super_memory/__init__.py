# -*- coding: utf-8 -*-
"""
super_memory 适配层：转发到 EGO 子模块 plugins.ego.super_memory。
前端以 plugin_id='super_memory' 请求，保证独立完整面板可用。
"""
import threading

from plugins.ego.super_memory import (
    PLUGIN_META,
    _memory,
    DB_PATH,
    get_config as _get_config,
    set_config as _set_config,
)

PLUGIN_META["page"] = "plugins/super_memory/ui.html"

_lock = threading.Lock()


def _do_stats(params=None):
    params = params or {}
    scope_id = str(params.get("scope_id") or "default").strip()
    try:
        import sqlite3

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM messages WHERE scope_id=?", (scope_id,))
        messages = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM facts WHERE scope_id=?", (scope_id,))
        facts = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM events WHERE scope_id=?", (scope_id,))
        events = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM persona_kernel WHERE scope_id=?", (scope_id,))
        persona = cur.fetchone()[0]
        emb_facts = 0
        emb_events = 0
        try:
            cur.execute("SELECT COUNT(*) FROM facts WHERE embedding IS NOT NULL AND embedding!=''")
            emb_facts = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM events WHERE embedding IS NOT NULL AND embedding!=''")
            emb_events = cur.fetchone()[0]
        except Exception:
            pass
        conn.close()
        return {
            "messages": messages,
            "facts": facts,
            "events": events,
            "persona": persona,
            "emb_facts": emb_facts,
            "emb_events": emb_events,
        }
    except Exception as e:
        return {"error": str(e)}


def _do_test_embed(params=None):
    """测试本地 embedding 服务是否可用，返回耗时与相似度样例。"""
    params = params or {}
    text = str(params.get("text") or "测试")
    try:
        from plugins.ego.super_memory import _embedder, _vec_sim

        t0 = __import__("time").time()
        v1 = _embedder.embed(text)
        if not v1:
            return {"ok": False, "error": "embedding 服务不可用(8083 未启动)"}
        v2 = _embedder.embed(text + "的同类描述")
        t1 = __import__("time").time()
        sim = _vec_sim(v1, v2)
        return {"ok": True, "latency_ms": round((t1 - t0) * 1000, 1), "dim": len(v1), "sample_sim": round(sim, 4)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _do_recall(params=None):
    params = params or {}
    scope_id = str(params.get("scope_id") or "default").strip()
    query = str(params.get("query") or "")
    limit = int(params.get("limit") or 5)
    try:
        items = _memory.recall_key_memories(scope="group", scope_id=scope_id, current_msg=query, limit=limit)
        return {"items": items or []}
    except Exception as e:
        return {"items": [], "error": str(e)}


def _do_tier_stats(params=None):
    params = params or {}
    scope_id = str(params.get("scope_id") or "default").strip()
    from plugins.ego.super_memory import get_tier_stats
    return get_tier_stats(scope_id)


def _do_set_tier(params=None):
    params = params or {}
    from plugins.ego.super_memory import set_tier as _st
    return _st(
        scope_id=str(params.get("scope_id") or "default").strip(),
        table=str(params.get("table") or "facts").strip(),
        item_id=int(params.get("item_id") or 0),
        tier=str(params.get("tier") or "long").strip(),
    )


PLUGIN_ACTIONS = {
    "get_config": lambda params: _get_config(),
    "set_config": lambda params: _set_config(params or {}),
    "stats": _do_stats,
    "test_embed": _do_test_embed,
    "recall": _do_recall,
    "tier_stats": _do_tier_stats,
    "set_tier": _do_set_tier,
}
