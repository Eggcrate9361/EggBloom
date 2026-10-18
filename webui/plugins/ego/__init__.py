# -*- coding: utf-8 -*-
"""
EGO 插件：人格 + 记忆 + 节奏 + 说话风格 + 黑话学习
对外统一接口，内部按子模块拆分。
"""
from .personality_engine import (
    PLUGIN_META as _PERSONALITY_META,
    get_personality,
    save_personality,
    get_config as personality_get_config,
    set_config as personality_set_config,
    get_examples,
    save_examples,
    build_personality_prompt,
    build_memory_context,
    post_process,
    before_reply as _personality_before_reply,
    after_reply as _personality_after_reply,
)
from .super_memory import (
    PLUGIN_META as _MEMORY_META,
    SuperMemory,
    before_reply as _memory_before_reply,
    after_reply as _memory_after_reply,
    get_config as memory_get_config,
    set_config as memory_set_config,
)
from .chat_style import (
    PLUGIN_META as _STYLE_META,
    ChatStyleLearner,
    before_reply as _style_before_reply,
    get_config as style_get_config,
    set_config as style_set_config,
)
from .humanlike_rhythm import (
    PLUGIN_META as _RHYTHM_META,
    HumanlikeRhythm,
    before_reply as _rhythm_before_reply,
    after_reply as _rhythm_after_reply,
    get_config as rhythm_get_config,
    set_config as rhythm_set_config,
    get_stats as rhythm_get_stats,
)
from .slang_learner import (
    PLUGIN_META as _SLANG_META,
    SlangLearner,
    start_learning,
    get_pending,
    get_injected,
    get_stats as _slang_get_stats,
    inject_selected,
    reject_selected,
)

PLUGIN_META = {
    "name": "EGO",
    "version": "0.1.0",
    "description": "人格 / 记忆 / 节奏 / 说话方式 / 黑话",
    "author": "EggBloom",
    "group_only": False,
    "enabled": True,
    "dependencies": [],
    "hooks": ["before_reply", "after_reply"],
    "priority": 20,
    "estimated_tokens": 0,
    "page": "plugins/ego/ui.html",
}

# Submodule references for advanced access
_memory = SuperMemory()
_style = ChatStyleLearner()
_rhythm = HumanlikeRhythm()
_slang = SlangLearner()


# ------------------------------------------------------------------
# Unified hooks
# ------------------------------------------------------------------
def before_reply(message="", group_id="", context=None, **kwargs):
    try:
        ctx = dict(context or {})
        ctx.setdefault("group_id", group_id)
        result = _personality_before_reply(message=message, group_id=group_id, context=ctx, **kwargs)
        if result:
            return result
        result = _memory_before_reply(message=message, group_id=group_id, context=ctx, **kwargs)
        if result:
            return result
        result = _style_before_reply(message=message, group_id=group_id, context=ctx, **kwargs)
        if result:
            return result
        result = _rhythm_before_reply(message=message, group_id=group_id, context=ctx, **kwargs)
        if result:
            return result
    except Exception as e:
        print(f"[EGO] before_reply error: {e}")
    return None


def after_reply(message="", group_id="", reply="", context=None, **kwargs):
    try:
        ctx = dict(context or {})
        ctx.setdefault("group_id", group_id)
        _personality_after_reply(message=message, group_id=group_id, reply=reply, context=ctx, **kwargs)
    except Exception as e:
        print(f"[EGO] after_reply personality error: {e}")
    try:
        _memory_after_reply(message=message, group_id=group_id, reply=reply, context=ctx, **kwargs)
    except Exception as e:
        print(f"[EGO] after_reply memory error: {e}")
    try:
        _rhythm_after_reply(message=message, group_id=group_id, reply=reply, context=ctx, **kwargs)
    except Exception as e:
        print(f"[EGO] after_reply rhythm error: {e}")


# ------------------------------------------------------------------
# Config facade
# ------------------------------------------------------------------
def get_config():
    cfg = {}
    try:
        cfg.update(personality_get_config())
    except Exception:
        pass
    try:
        cfg.update(memory_get_config())
    except Exception:
        pass
    try:
        cfg.update(style_get_config())
    except Exception:
        pass
    try:
        cfg.update(rhythm_get_config())
    except Exception:
        pass
    return cfg


def set_config(params):
    results = {}
    try:
        results["personality"] = personality_set_config(params)
    except Exception as e:
        results["personality_error"] = str(e)
    try:
        results["memory"] = memory_set_config(params)
    except Exception as e:
        results["memory_error"] = str(e)
    try:
        results["style"] = style_set_config(params)
    except Exception as e:
        results["style_error"] = str(e)
    try:
        results["rhythm"] = rhythm_set_config(params)
    except Exception as e:
        results["rhythm_error"] = str(e)
    return results


# ------------------------------------------------------------------
# Memory wrappers
# ------------------------------------------------------------------
def memory_stats(scope_id="default"):
    try:
        from .super_memory import DB_PATH
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM messages WHERE scope_id=?", (scope_id,))
        messages = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM facts WHERE scope_id=?", (scope_id,))
        facts = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM events WHERE scope_id=?", (scope_id,))
        events = cur.fetchone()[0]
        conn.close()
        return {"messages": messages, "facts": facts, "events": events}
    except Exception as e:
        return {"messages": 0, "facts": 0, "events": 0, "error": str(e)}


def memory_recall(scope_id="default", user_id="", query="", limit=10):
    try:
        sm = SuperMemory()
        items = sm.recall_key_memories(scope="group", scope_id=scope_id, current_msg=query, limit=limit)
        return {"items": items}
    except Exception as e:
        return {"items": [], "error": str(e)}


# ------------------------------------------------------------------
# Style wrapper
# ------------------------------------------------------------------
def style_features(scope_id="default", user_id=""):
    try:
        cs = ChatStyleLearner()
        return cs.get_features(scope_id=scope_id, user_id=user_id)
    except Exception as e:
        return {"features": [], "error": str(e)}


# ------------------------------------------------------------------
# Slang wrappers with names expected by webui
# ------------------------------------------------------------------
def slang_start_learning(topics):
    try:
        return start_learning(topics)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def slang_get_pending(search="", source="", min_quality=0):
    try:
        items = get_pending(search=search, source=source, min_quality=min_quality)
        if isinstance(items, dict):
            return items
        return {"pending": list(items or [])}
    except Exception as e:
        return {"pending": [], "error": str(e)}


def slang_inject_selected(ids):
    try:
        return inject_selected(ids)
    except Exception as e:
        return {"injected": 0, "error": str(e)}


def slang_reject_selected(ids):
    try:
        return reject_selected(ids)
    except Exception as e:
        return {"rejected": 0, "error": str(e)}


def slang_get_stats():
    try:
        return _slang_get_stats()
    except Exception as e:
        return {"pending": 0, "injected": 0, "rejected": 0, "total_learned": 0, "error": str(e)}


__all__ = [
    "PLUGIN_META",
    "before_reply",
    "after_reply",
    "get_personality",
    "save_personality",
    "get_config",
    "set_config",
    "get_examples",
    "save_examples",
    "memory_stats",
    "memory_recall",
    "style_features",
    "slang_start_learning",
    "slang_get_pending",
    "slang_inject_selected",
    "slang_reject_selected",
    "slang_get_stats",
    "_memory",
    "_slang",
    "_style",
    "_rhythm",
]