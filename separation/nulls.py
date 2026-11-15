# -*- coding: utf-8 -*-
"""separation.nulls —— 分离态替身（与真模块同签名、同返回结构）。

设计 §6：不注册规则 / 不注册闸 / 不注册预算；主链拿到的对象变成"恒放行"。
这些类只做一件事：把接口撑住，不改变调用方形状。
"""
from __future__ import annotations

from typing import Any, Dict


class NullGate:
    """替身：speak_gate.allow / prompt_note。"""

    def allow(self, batch=None, cfg=None, *args, **kwargs):
        return True, ""

    def prompt_note(self, cfg=None):
        return ""


class NullReplyGate:
    """替身：reply_gate.should_regen / regen_hint / habit_line。"""

    def should_regen(self, mode=None, reply="", *args, **kwargs):
        return False

    def regen_hint(self):
        return ""

    def habit_line(self, *args, **kwargs):
        return ""


class NullBudget:
    """替身：reply_budget.pick / check / fit / prompt_line。

    不设上限，但返回结构保持和真模块一致；调用方拿到的 `max_chars`
    是"足够大"而不是 0（0 在部分调用点会被 `or 默认值` 吃掉）。
    """

    _UNLIMITED = 10 ** 9

    def pick(self, rng=None, shift=0, long_ratio=None):
        return {"name": "∞", "parts_min": 1, "parts_max": self._UNLIMITED,
                "max_chars": self._UNLIMITED, "max_total": self._UNLIMITED}

    def check(self, text: str, b: Dict, hard=None):
        return True, "", {}

    def fit(self, text: str, b=None, trigger: str = ""):
        return text

    def prompt_line(self, b=None):
        return ""

    def regen_hint(self, *args, **kwargs):
        return ""

    def visible(self, text):
        return text

    def apply_factors(self, b, factors=None):
        return dict(b or {})

    def ensure_statement_floor(self, b, need_statement=False):
        return dict(b or {})

    def anchor_ok(self, reply, trigger="", **kwargs):
        return True


class NullPersona:
    """替身：persona_form.apply / decorate。"""

    def apply(self, form=None, cfg=None):
        return dict(form or {})

    def decorate(self, text, form=None, trigger="", rng=None):
        return text


class NullDrop:
    """替身：reply_drop.drop_reason。"""

    def drop_reason(self, *args, **kwargs):
        return ""


class NullRules:
    """替身：de_ai.de_ai_rule_block / 规则段。"""

    def de_ai_rule_block(self, cfg=None):
        return ""

    def de_ai_post_process(self, text, cfg=None):
        return text

    def segment(self, *args, **kwargs):
        return ""


NULL_GATE = NullGate()
NULL_REPLY_GATE = NullReplyGate()
NULL_BUDGET = NullBudget()
NULL_PERSONA = NullPersona()
NULL_DROP = NullDrop()
NULL_RULES = NullRules()
