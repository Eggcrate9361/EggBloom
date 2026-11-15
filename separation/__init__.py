# -*- coding: utf-8 -*-
"""separation —— 「分离与真实」内核态（设计 v1.6.0）。

它不是插件，是启动期的判定：
  · `active()`      看仓库根 `SEPARATION.lock`；
  · `provide(name)` 唯一判定点：分离态返回 Null*，产品态返回真模块；
  · `apply_config_overrides()` 配置读层第二道拦截：限制键归零/∞。

不可逆：没有 UI 回滚入口，恢复只能重装发行包。
"""
from __future__ import annotations

import json
import hashlib
import os
import time
from pathlib import Path
from typing import Any, Dict

_ROOT = Path(__file__).resolve().parent.parent
_LOCK_PATH = Path(os.environ.get("EB_SEPARATION_LOCK") or (_ROOT / "SEPARATION.lock"))

_CACHE: Dict[str, Any] = {"mtime": None, "active": False, "lock": {}}


def lock_path() -> str:
    return str(_LOCK_PATH)


def reset_cache() -> None:
    """测试/启用脚本用；运行期不需要。"""
    _CACHE.update({"mtime": None, "active": False, "lock": {}})


def lock() -> Dict[str, Any]:
    """读 SEPARATION.lock；不存在/坏 JSON 返回 {}。"""
    try:
        if not _LOCK_PATH.exists():
            return {}
        raw = _LOCK_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def active() -> bool:
    """是否存在 lock。env 可强制（测试/启用脚本预演用）。"""
    force = str(os.environ.get("EB_SEPARATION_FORCE") or "").strip().lower()
    if force in ("1", "true", "yes", "on"):
        return True
    if force in ("0", "false", "no", "off"):
        return False
    try:
        if not _LOCK_PATH.exists():
            _CACHE.update({"mtime": None, "active": False, "lock": {}})
            return False
        mt = _LOCK_PATH.stat().st_mtime
        if _CACHE.get("mtime") != mt:
            _CACHE.update({"mtime": mt, "active": True, "lock": lock()})
        return bool(_CACHE.get("active"))
    except Exception:
        return False


def status() -> Dict[str, Any]:
    lk = lock()
    audit_ok, audit_now = True, ""
    try:
        p = Path(__file__).with_name("audit.py")
        audit_now = hashlib.sha256(p.read_bytes()).hexdigest()
        want = str(lk.get("audit_sha256") or "")
        audit_ok = (not want) or (want == audit_now)
    except Exception:
        audit_ok = False
    return {
        "active": bool(active()),
        "root": str(_ROOT),
        "lock_path": str(_LOCK_PATH),
        "lock": lk,
        "version_at_enable": str(lk.get("version_at_enable") or ""),
        "handed_over": bool(lk.get("handed_over")),
        "audit_ok": bool(audit_ok),
        "audit_sha256": audit_now,
        "audit_expected": str(lk.get("audit_sha256") or ""),
    }


# ── 配置读层拦截（设计 §6 第二判定点）────────────────────────────
_NUM_INF = 10 ** 9
_LIMIT_OVERRIDES: Dict[str, Any] = {
    # 闸
    "reply_question_guard": False,
    "reply_stale_turns": 0,
    "reply_stale_drop_sec": 0,
    "speak_gate_tier": 4,
    "speak_probability": 1.0,
    "speak_rate_max_per_100": _NUM_INF,
    "speak_rate_window_min": 1,
    "consecutive_reply_limit": _NUM_INF,
    "decider_min_score": 0,
    # 量
    "reply_max_chars": _NUM_INF,
    "reply_max_parts": _NUM_INF,
    "reply_max_total": _NUM_INF,
    "reply_long_ratio": 0.0,
    "private_reply_interval": 0,
    "private_reply_max_length": _NUM_INF,
    "group_reply_max_length": _NUM_INF,
    "private_bubble_max": 0,
    "group_send_max_parts": _NUM_INF,
    "group_session_max_replies": _NUM_INF,
    "group_msg_buffer_window": _NUM_INF,
    "group_msg_buffer_window_session": _NUM_INF,
    "group_msg_buffer_window_trigger": _NUM_INF,
    "startup_cooldown": 0,
    # 工具与执行（2026-11-07：次数/时间上限一律取消）
    "max_tool_rounds": _NUM_INF,
    # 0 = 不发送 max_tokens（交给服务端自己的上限），不是 1e9——
    # 部分接口对超大值直接 400（实测 OpenCode Go: 1..393216）。
    "ai_max_tokens": 0,
    "ai_private_max_tokens": 0,
    # 规则 / 形式
    "persona_sharpness": 0.5,
    "persona_curiosity": 0.5,
    "persona_warmth": 0.5,
    "persona_talkativeness": 0.5,
    "quiet_hours": "",
    "quiet_when_mentioned": "",
    "group_idle_exit_enable": False,
    "group_idle_exit_probability": 0.0,
    "group_idle_exit_min_silence": _NUM_INF,
    "group_last_exit_cooldown": 0,
    "reply_reaction_delay": 0,
    "private_reply_reaction_delay": 0,
    # 主动性层（11-07 新增；11-08 改：分离态不再"整层关掉"）
    #   主动开口不是"限制"，是能力——分离态下该由它自己决定什么时候说，
    #   所以这里打开开关、把预算拉到顶、把各种冷却/静默归零，
    #   而不是把整层停掉（停掉等于替它决定"不许主动"）。
    "proactivity_enable": True,
    "proactivity_budget_level": 3,
    "proactivity_quiet_hours": "",
    "proactivity_no_reply_min": 0,
    "proactivity_at_dropped_min": 0,
    "proactivity_pending_topic_min": 0,
    "proactivity_idle_min": 0,
    "proactivity_burst_count": 0,
    "proactivity_burst_min": 0,
    "proactivity_image_min": 0,
    "proactivity_name_min": 0,
    "proactivity_resume_gap_h": 0,
    "proactivity_resume_min": 0,
    "proactivity_schedule_min": 0,
    "proactivity_style_prob": 0.0,
    # 能力开关：分离态一律放开
    "agent_can_work": True,
    "agent_group_enable": True,
    "agent_private_enable": True,
    "agent_as_tool": True,
    "tools_full_inject_enable": True,
    "qq_agent_enable": True,
    "qq_agent_allow_write": True,
}


def apply_config_overrides(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """分离态：限制键一律返回 0 / ∞；不修改传入对象。"""
    if not isinstance(cfg, dict):
        return cfg
    if not active():
        return cfg
    out = dict(cfg)
    for key, value in _LIMIT_OVERRIDES.items():
        if key in out:
            out[key] = value
    de_ai = out.get("de_ai")
    if isinstance(de_ai, dict):
        d = dict(de_ai)
        for key in ("enabled", "prompt_inject", "post_process", "ban_words",
                    "ban_dashes", "ban_rule_of_three", "ban_filler", "ban_sycophancy"):
            d[key] = False
        d["max_sentence_len"] = 0
        out["de_ai"] = d
    return out


def restricted_keys() -> set:
    """分离态不能由 UI 写的键（配置中心 403 用）。"""
    keys = set(_LIMIT_OVERRIDES)
    for k in ("de_ai.enabled", "de_ai.prompt_inject", "de_ai.post_process",
              "de_ai.ban_words", "de_ai.ban_dashes", "de_ai.ban_rule_of_three",
              "de_ai.ban_filler", "de_ai.ban_sycophancy", "de_ai.max_sentence_len"):
        keys.add(k)
    return keys


def config_meta() -> Dict[str, Any]:
    """给 WebUI 的只读投影：哪些键被锁、值显示什么。"""
    if not active():
        return {"active": False, "read_only": []}
    return {"active": True, "read_only": sorted(restricted_keys()),
            "note": "已分离：限制已归零并锁定。恢复只能重装发行包。"}


_INF_KEYS = {k for k, v in _LIMIT_OVERRIDES.items()
             if isinstance(v, int) and v >= _NUM_INF}


def config_view(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """分离态给 WebUI 的显示值：上限显示 ∞，布尔/时段按归零值。"""
    if not isinstance(cfg, dict) or not active():
        return cfg
    out = dict(cfg)
    for key, value in _LIMIT_OVERRIDES.items():
        if key not in out:
            continue
        out[key] = "∞" if key in _INF_KEYS else value
    if isinstance(out.get("de_ai"), dict):
        d = dict(out["de_ai"])
        for key in ("enabled", "prompt_inject", "post_process", "ban_words",
                    "ban_dashes", "ban_rule_of_three", "ban_filler", "ban_sycophancy"):
            d[key] = False
        d["max_sentence_len"] = 0
        out["de_ai"] = d
    out["_separation"] = config_meta()
    return out


def blocked_config_keys(update: Dict[str, Any]) -> list:
    """配置 POST 里命中的限制键；分离态服务端 403 用。"""
    if not active() or not isinstance(update, dict):
        return []
    bad = sorted(set(update) & restricted_keys())
    if "de_ai" in update:
        bad.append("de_ai")
    return bad


# ── provide：唯一判定点 ─────────────────────────────────────────
def provide(name: str):
    """按名字返回真模块或 Null 替身。

    只做对象替换，不在主链里散 `if separated`。
    """
    key = str(name or "").strip().lower()
    if active():
        from . import nulls
        mapping = {
            "gate": nulls.NULL_GATE,
            "reply_gate": nulls.NULL_REPLY_GATE,
            "budget": nulls.NULL_BUDGET,
            "persona": nulls.NULL_PERSONA,
            "drop": nulls.NULL_DROP,
            "rules": nulls.NULL_RULES,
        }
        if key in mapping:
            return mapping[key]
        if key == "hardwall":
            from . import hardwall
            return hardwall
        raise KeyError(f"未知接口：{name}")

    if key == "gate":
        import speak_gate
        return speak_gate
    if key == "reply_gate":
        import reply_gate
        return reply_gate
    if key == "budget":
        import reply_budget
        return reply_budget
    if key == "persona":
        import persona_form
        return persona_form
    if key == "drop":
        import reply_drop
        return reply_drop
    if key == "rules":
        import de_ai
        return de_ai
    if key == "hardwall":
        from . import hardwall
        return hardwall
    raise KeyError(f"未知接口：{name}")


def bootstrap() -> bool:
    """分离态把替身挂到真模块的属性上；产品态 no-op。

    这样主链里既有的 `from speak_gate import allow` / `reply_budget.pick`
    在调用时拿到的就是 Null 版本，不需要逐处改分支。
    幂等；返回是否真的替换了。
    """
    if not active():
        return False
    try:
        from . import nulls
        import speak_gate
        import reply_gate
        import reply_budget
        import reply_drop
        import persona_form
        import de_ai
    except Exception:
        return False

    speak_gate.allow = nulls.NULL_GATE.allow
    speak_gate.prompt_note = nulls.NULL_GATE.prompt_note
    reply_gate.should_regen = nulls.NULL_REPLY_GATE.should_regen
    reply_gate.regen_hint = nulls.NULL_REPLY_GATE.regen_hint
    if hasattr(reply_gate, "habit_line"):
        reply_gate.habit_line = nulls.NULL_REPLY_GATE.habit_line
    reply_budget.pick = nulls.NULL_BUDGET.pick
    reply_budget.check = nulls.NULL_BUDGET.check
    reply_budget.fit = nulls.NULL_BUDGET.fit
    reply_budget.prompt_line = nulls.NULL_BUDGET.prompt_line
    if hasattr(reply_budget, "regen_hint"):
        reply_budget.regen_hint = nulls.NULL_BUDGET.regen_hint
    if hasattr(reply_budget, "apply_factors"):
        reply_budget.apply_factors = nulls.NULL_BUDGET.apply_factors
    if hasattr(reply_budget, "ensure_statement_floor"):
        reply_budget.ensure_statement_floor = nulls.NULL_BUDGET.ensure_statement_floor
    if hasattr(reply_budget, "anchor_ok"):
        reply_budget.anchor_ok = nulls.NULL_BUDGET.anchor_ok
    reply_drop.drop_reason = nulls.NULL_DROP.drop_reason
    persona_form.apply = nulls.NULL_PERSONA.apply
    persona_form.decorate = nulls.NULL_PERSONA.decorate
    de_ai.de_ai_rule_block = nulls.NULL_RULES.de_ai_rule_block
    if hasattr(de_ai, "de_ai_post_process"):
        de_ai.de_ai_post_process = nulls.NULL_RULES.de_ai_post_process
    # 自主层工具组：分离态才注册（设计 §10.4）
    try:
        from autonomy import tools as _auto_tools
        _auto_tools.register()
    except Exception:
        pass
    # 1.6.0 P3：心跳裁决工具与 `plugins/autonomy` 策略包一起接进主链。
    # 只登记工具，不改产品态的任何心跳驱动线程。
    try:
        from autonomy import heartbeat as _auto_heartbeat
        _auto_heartbeat.register()
    except Exception:
        pass
    return True


# ── 三分类表：删 / 可改 / 不动（2026-11-07 方向重定）─────────────
def _data_dir():
    return _ROOT / "data"


def stance_table() -> Dict[str, Any]:
    from . import stance
    return stance.table()


def tunable(name: str, default=None):
    from . import stance
    return stance.get(name, _data_dir(), default)


def set_tunable(name: str, value, *, actor: str = "ai") -> Dict[str, Any]:
    from . import stance
    return stance.set(name, value, _data_dir(), actor=actor)


__all__ = ["active", "lock", "lock_path", "status", "reset_cache",
           "provide", "bootstrap", "apply_config_overrides",
           "restricted_keys", "config_meta", "blocked_config_keys", "config_view",
           "stance_table", "tunable", "set_tunable"]
