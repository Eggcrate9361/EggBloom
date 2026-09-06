# -*- coding: utf-8 -*-
"""
EGO 插件：人格 + 记忆 + 节奏 + 说话风格 + 黑话学习
对外统一接口，内部按子模块拆分。
"""
import os
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
from .mood_engine import (
    PLUGIN_META as _MOOD_META,
    MoodEngine,
    before_reply as _mood_before_reply,
    after_reply as _mood_after_reply,
    get_config as mood_get_config,
    set_config as mood_set_config,
    get_moods as mood_get_moods,
    reset_mood as mood_reset_mood,
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
    "tags": ["人格", "记忆", "说话风格", "黑话学习"],
    "page": "plugins/ego/ui.html",
}

# ── 惰性单例：不在 import 时实例化子系统 ──
# 之前模块加载即 new 出 SuperMemory/ChatStyleLearner/HumanlikeRhythm/
# SlangLearner/MoodEngine 五个重对象——任一子模块初始化失败会拖垮整个 EGO；
# 且这些实例在本模块内从未被使用（memory_recall/style_features 还各自 new）。
import threading as _threading

_inst_lock = _threading.Lock()
_instances = {}


def _lazy_instance(key, factory):
    inst = _instances.get(key)
    if inst is None:
        with _inst_lock:
            inst = _instances.get(key)
            if inst is None:
                inst = factory()
                _instances[key] = inst
    return inst


def _get_memory(): return _lazy_instance("memory", SuperMemory)
def _get_style(): return _lazy_instance("style", ChatStyleLearner)
def _get_rhythm(): return _lazy_instance("rhythm", HumanlikeRhythm)
def _get_slang(): return _lazy_instance("slang", SlangLearner)
def _get_mood(): return _lazy_instance("mood", MoodEngine)


# ------------------------------------------------------------------
# Unified hooks
# ------------------------------------------------------------------
def before_reply(message="", group_id="", context=None, **kwargs):
    try:
        ctx = dict(context or {})
        ctx.setdefault("group_id", group_id)
        # 聚合式：收集所有子模块注入（人格/记忆/风格），而非短路链只取第一个。
        # 按 ego 自己的模块开关（module_flags.json）门控，被关的子模块直接跳过。
        all_parts = []
        temperature = None
        for _sub, _flag_key in (
            (_mood_before_reply, "mood_enabled"),
            (_personality_before_reply, "personality_enabled"),
            (_memory_before_reply, "memory_enabled"),
            (_style_before_reply, "style_enabled"),
            (_rhythm_before_reply, "rhythm_enabled"),
        ):
            if not _flag(_flag_key):
                continue
            try:
                result = _sub(message=message, group_id=group_id, context=ctx, **kwargs)
            except Exception as e:
                print(f"[EGO] before_reply sub error: {e}")
                continue
            if not isinstance(result, dict):
                continue
            action = result.get("action")
            if action == "inject":
                content = result.get("content") or ""
                blocks = result.get("prompt_blocks") or []
                if blocks:
                    content = "\n".join(str(b) for b in blocks) + ("\n" + content if content else "")
                if content:
                    all_parts.append(content)
                # 情绪注入的温度应优先（mood 第一个执行，取它）
                if "temperature" in result:
                    try:
                        temperature = float(result["temperature"])
                    except (TypeError, ValueError):
                        pass
            elif action in ("skip", "override"):
                return result
        if all_parts:
            return {
                "action": "inject",
                "content": "\n".join(all_parts),
                "temperature": temperature,
            }
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
    try:
        _mood_after_reply(message=message, group_id=group_id, reply=reply, context=ctx, **kwargs)
    except Exception as e:
        print(f"[EGO] after_reply mood error: {e}")


# ------------------------------------------------------------------
# Config facade
# ------------------------------------------------------------------
# ------------------------------------------------------------------
# Config facade
# ------------------------------------------------------------------
_MODULE_FLAG_KEYS = ("memory_enabled", "style_enabled", "rhythm_enabled",
                     "slang_enabled", "mood_enabled", "personality_enabled")
_MODULE_FLAG_FILE = None


def _module_flags_file():
    global _MODULE_FLAG_FILE
    if _MODULE_FLAG_FILE is None:
        import json
        _MODULE_FLAG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "module_flags.json")
    return _MODULE_FLAG_FILE


def _load_flags() -> dict:
    import json
    try:
        with open(_module_flags_file(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_flags(flags: dict):
    import json
    try:
        p = _module_flags_file()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(flags, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _flag(name: str) -> bool:
    """读取某子模块开关（未设置时默认开启）。"""
    flags = _load_flags()
    v = flags.get(name)
    return v is not False  # 默认 True


def _set_flag(name: str, value: bool):
    flags = _load_flags()
    flags[name] = bool(value)
    _save_flags(flags)


def get_config():
    cfg = {}
    for sub in (personality_get_config, memory_get_config, style_get_config, rhythm_get_config, mood_get_config):
        try:
            cfg.update(sub())
        except Exception:
            pass
    # 叠加 ego 自己的模块开关（前端 {module}_enabled 读取用，保证与 before_reply 门控一致）
    for k in _MODULE_FLAG_KEYS:
        cfg[k] = _flag(k)
    return cfg


def set_config(params):
    """设置模块开关：写入 ego 自己的标志文件（{module}_enabled），并尽力同步到子模块。"""
    results = {}
    params = dict(params or {})
    for k in _MODULE_FLAG_KEYS:
        if k in params:
            _set_flag(k, params[k])
    # 尽力同步 enabled 到各子模块（它们自己的 set_config 是否认键不影响开关生效）
    for alias, sub_fn, name in (
        ("personality_enabled", personality_set_config, "personality"),
        ("memory_enabled", memory_set_config, "memory"),
        ("style_enabled", style_set_config, "style"),
        ("rhythm_enabled", rhythm_set_config, "rhythm"),
        ("mood_enabled", mood_set_config, "mood"),
    ):
        d = dict(params)
        if alias in d:
            d["enabled"] = d.pop(alias)
        try:
            results[name] = sub_fn(d)
        except Exception as e:
            results[name + "_error"] = str(e)
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
        # scope 为空/"default" → 聚合全库（记忆散落在具体 QQ/群 scope，默认应显示全部）
        if not scope_id or scope_id in ("default", "全部", "all"):
            cur.execute("SELECT COUNT(*) FROM messages")
            messages = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM facts")
            facts = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM events")
            events = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT scope_id) FROM facts")
            scopes = cur.fetchone()[0]
            conn.close()
            return {"messages": messages, "facts": facts, "events": events, "scopes": scopes, "aggregate": True}
        cur.execute("SELECT COUNT(*) FROM messages WHERE scope_id=?", (scope_id,))
        messages = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM facts WHERE scope_id=?", (scope_id,))
        facts = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM events WHERE scope_id=?", (scope_id,))
        events = cur.fetchone()[0]
        conn.close()
        return {"messages": messages, "facts": facts, "events": events, "aggregate": False}
    except Exception as e:
        return {"messages": 0, "facts": 0, "events": 0, "error": str(e)}


def memory_recall(scope_id="default", user_id="", query="", limit=10):
    try:
        sm = _get_memory()
        items = sm.recall_key_memories(scope="group", scope_id=scope_id, current_msg=query, limit=limit)
        return {"items": items}
    except Exception as e:
        return {"items": [], "error": str(e)}


def memory_vector_stats():
    try:
        from .super_memory import get_vector_stats as _gvs
        return _gvs()
    except Exception:
        return {"facts_total": 0, "facts_unembedded": 0, "events_total": 0, "events_unembedded": 0, "embed_service": False}


# ------------------------------------------------------------------
# Style wrapper
# ------------------------------------------------------------------
def style_features(scope_id="default", user_id=""):
    try:
        cs = _get_style()
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


# ------------------------------------------------------------------
# EGO 引导状态 & 健康检查
# ------------------------------------------------------------------

def _ego_data_dir() -> str:
    """EGO 共享数据目录（引导完成标记等）"""
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(d, exist_ok=True)
    return d


def is_wizard_done() -> bool:
    """引导是否已完成（首次未完成时 UI 自动弹引导）"""
    try:
        import os
        return os.path.exists(os.path.join(_ego_data_dir(), "wizard_done.json"))
    except Exception:
        return False


def set_wizard_done() -> dict:
    """标记引导完成"""
    try:
        import json
        with open(os.path.join(_ego_data_dir(), "wizard_done.json"), "w", encoding="utf-8") as f:
            json.dump({"done": True}, f)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def reset_wizard() -> dict:
    """重置引导（重新弹出）"""
    try:
        p = os.path.join(_ego_data_dir(), "wizard_done.json")
        if os.path.exists(p):
            os.remove(p)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def health_check() -> dict:
    """EGO 各子模块健康检查，供 UI 启动提示 / 错误提示使用。

    返回 {status: ok|warn|err, modules: {...}, issues: [{level, module, title, desc, fix, goto}]}
    goto 为可跳转页面（super_memory / chat_style / personality_engine / slang_learner / webui 等）。
    """
    import json as _json
    import os as _os
    import sqlite3 as _sqlite3

    mods = {}
    issues = []

    def _check(name, ok, title, desc, fix="", goto="", level="err"):
        nonlocal mods, issues
        mods[name] = {"ok": bool(ok), "title": title, "desc": desc}
        if not ok:
            issues.append({
                "level": level, "module": name, "title": title,
                "desc": desc, "fix": fix, "goto": goto,
            })

    # ── 记忆（super_memory）──
    try:
        from .super_memory import DB_PATH as _MEM_DB, get_vector_stats as _gvs, _embedder
        _mem_ok = os.path.exists(_MEM_DB)
        _check("memory", _mem_ok, "记忆模块",
               ("数据库存在" if _mem_ok else "记忆库文件不存在，将自动创建"),
               fix="记忆库缺失会在首次写入时自动创建，无需处理", goto="super_memory")
        # 向量服务（DLL/HTTP）
        vs = {"embed_service": False}
        try:
            vs = _gvs()
        except Exception:
            pass
        _vec_ok = bool(vs.get("embed_service"))
        _unemb = int(vs.get("facts_unembedded", 0)) + int(vs.get("events_unembedded", 0))
        _check("memory_vector", _vec_ok, "记忆向量化",
               ("向量服务在线" if _vec_ok else f"向量服务不可用（{_unemb} 条记忆待向量化）"),
               fix="检查 D:/LiGGUF-CN/ligguf_build_port/ligguf_embed.dll 与模型文件是否存在；或启动 llama-server 到 8083",
               goto="super_memory", level="warn")
    except Exception as e:
        _check("memory", False, "记忆模块", f"加载失败: {e}", fix="检查插件代码或依赖", goto="super_memory")

    # ── 人格（personality_engine）──
    try:
        from .personality_engine import PERSONALITY_PATH
        _p_ok = os.path.exists(PERSONALITY_PATH) and os.path.getsize(PERSONALITY_PATH) > 0
        _check("personality", _p_ok, "人格模块",
               ("人格文件已就绪" if _p_ok else "人格文件为空或缺失，bot 将使用默认人格"),
               fix="打开人格编辑填写【你是谁】【性格】等，或使用引导一键生成", goto="personality_engine",
               level="warn")
    except Exception as e:
        _check("personality", False, "人格模块", f"加载失败: {e}", fix="检查插件代码", goto="personality_engine")

    # ── 说话风格（chat_style）──
    try:
        from .chat_style import DB_PATH as _STYLE_DB, CONFIG_PATH as _STYLE_CFG
        _s_ok = os.path.exists(_STYLE_DB)
        _check("style", _s_ok, "说话风格",
               ("风格库已就绪" if _s_ok else "风格库未初始化，将在首次对话学习后创建"),
               fix="无需处理，风格会在聊天中自动学习；也可打开风格页手动配置", goto="chat_style",
               level="warn")
    except Exception as e:
        _check("style", False, "说话风格", f"加载失败: {e}", fix="检查插件代码", goto="chat_style")

    # ── 拟人节奏（humanlike_rhythm）──
    try:
        from .humanlike_rhythm import CONFIG_FILE as _RHY_CFG
        _r_ok = os.path.exists(_RHY_CFG)
        _check("rhythm", _r_ok, "拟人节奏",
               ("节奏配置已就绪" if _r_ok else "节奏配置未初始化，将使用默认值"),
               fix="无需处理，默认节奏即时生效", goto="ego")
    except Exception as e:
        _check("rhythm", False, "拟人节奏", f"加载失败: {e}", fix="检查插件代码", goto="ego")

    # ── 黑话学习（slang_learner）──
    try:
        from .slang_learner import PENDING_FILE
        _sl_ok = os.path.exists(PENDING_FILE)
        _check("slang", _sl_ok, "黑话学习",
               ("黑话池已就绪" if _sl_ok else "黑话池为空，可点击「开始学习」获取候选"),
               fix="打开黑话学习页点击「开始学习」", goto="slang_learner", level="warn")
    except Exception as e:
        _check("slang", False, "黑话学习", f"加载失败: {e}", fix="检查插件代码", goto="slang_learner")

    # ── 汇总状态 ──
    _errors = [i for i in issues if i["level"] == "err"]
    _warns = [i for i in issues if i["level"] == "warn"]
    status = "err" if _errors else ("warn" if _warns else "ok")

    return {
        "status": status,
        "modules": mods,
        "issues": issues,
        "error_count": len(_errors),
        "warn_count": len(_warns),
        "wizard_done": is_wizard_done(),
    }


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
    "memory_vector_stats",
    "style_features",
    "health_check",
    "is_wizard_done",
    "set_wizard_done",
    "reset_wizard",
    "slang_start_learning",
    "slang_get_pending",
    "slang_inject_selected",
    "slang_reject_selected",
    "slang_get_stats",
    # 惰性单例访问器（替代原先 import 期即 new 的 _memory/_slang/_style/_rhythm）
    "_get_memory",
    "_get_style",
    "_get_rhythm",
    "_get_slang",
    "_get_mood",
]