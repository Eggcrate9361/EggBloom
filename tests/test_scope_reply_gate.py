# -*- coding: utf-8 -*-
"""
T7 · P4 · S3 —— 4 个发言出口收敛到注册表（`reply` 能力）+ 逐能力覆盖验证。

S3 的两件事：
  1. **注册表加逐能力覆盖** —— 否则 `reply` 在三个档位里都放行，接线就成了空动作；
     有了覆盖才能"让某个群闭嘴"，`custom` 也才真的可达（照搬 permission-presets：
     不匹配任何预设的组合 = custom，可看不可选，且**可以离开**）。
  2. **发言出口收敛** —— 4 处调用全部经 `_group_allowed()`，在其中叠加 `reply` 能力判定；
     开关默认关 → 零行为变化。旧层（reply_enabled + 白名单）原样保留。

运行：python -m unittest tests.test_scope_reply_gate -v
"""
import os
import re
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_core  # noqa: E402
from harness.scope_registry import ScopeRegistry  # noqa: E402


class TestReplyGate(unittest.TestCase):
    def setUp(self):
        self._orig_cfg = bot_core._config
        self._orig_reg = bot_core._SCOPE_REGISTRY
        self._cfg_snapshot = dict(self._orig_cfg)
        self.tmp = tempfile.mkdtemp(prefix="scopereply_")
        self.reg = ScopeRegistry(events_path=os.path.join(self.tmp, "ev.jsonl"),
                                 on_warn=lambda m: None)

    def tearDown(self):
        import shutil
        bot_core._config = self._orig_cfg
        bot_core._SCOPE_REGISTRY = self._orig_reg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, switch, extra=None):
        c = dict(self._cfg_snapshot)
        c["capability_registry_enable"] = switch
        if extra:
            c.update(extra)
        bot_core._config = c
        bot_core._SCOPE_REGISTRY = self.reg

    # ── 逐能力覆盖（新增能力）──
    def test_override_denies_single_capability(self):
        self.reg.set_capability("group", "1", "reply", False)
        self.assertFalse(self.reg.is_allowed("group", "1", "reply"))
        self.assertTrue(self.reg.is_allowed("group", "1", "search_web"),
                        "只该影响被覆盖的那一个能力")

    def test_override_can_grant(self):
        self.reg.set_preset("group", "1", "readonly")
        self.assertFalse(self.reg.is_allowed("group", "1", "generate_image"))
        self.reg.set_capability("group", "1", "generate_image", True)
        self.assertTrue(self.reg.is_allowed("group", "1", "generate_image"))

    def test_custom_is_derived_not_selectable(self):
        self.reg.set_capability("group", "1", "reply", False)
        r = self.reg.resolve("group", "1")
        self.assertEqual(r["preset"], "custom", "组合不匹配任何预设 → custom（推导）")
        self.assertEqual(r["named_preset"], "standard", "具名预设仍是 standard")
        self.assertFalse(self.reg.set_preset("group", "1", "custom")["changed"],
                         "custom 不可主动选")

    def test_can_leave_custom(self):
        self.reg.set_capability("group", "1", "reply", False)
        self.assertEqual(self.reg.resolve("group", "1")["preset"], "custom")
        self.assertTrue(self.reg.clear_overrides("group", "1")["changed"])
        self.assertEqual(self.reg.resolve("group", "1")["preset"], "standard",
                         "应当能离开 custom 回到具名预设")

    def test_override_idempotent_and_validated(self):
        self.assertTrue(self.reg.set_capability("g", "1", "reply", False)["changed"])
        self.assertFalse(self.reg.set_capability("g", "1", "reply", False)["changed"],
                         "同值重复设置必须零变化")
        self.assertFalse(self.reg.set_capability("g", "1", "不存在的能力", False)["changed"],
                         "封闭词汇外拒绝")

    def test_override_survives_replay(self):
        self.reg.set_capability("group", "1", "reply", False)
        self.reg.set_preset("group", "2", "readonly")
        reg2 = ScopeRegistry(events_path=os.path.join(self.tmp, "ev.jsonl"),
                             on_warn=lambda m: None)
        self.assertFalse(reg2.is_allowed("group", "1", "reply"))
        self.assertEqual(reg2.resolve("group", "2")["preset"], "readonly")

    def test_switch_to_named_preset_clears_custom(self):
        self.reg.set_capability("group", "1", "reply", False)
        self.reg.set_preset("group", "1", "full")
        self.assertEqual(self.reg.resolve("group", "1")["preset"], "full",
                         "切到具名预设应离开 custom")
        self.assertTrue(self.reg.is_allowed("group", "1", "reply"))

    def test_forged_override_rejected_by_invariant(self):
        ok, why = self.reg.validate_event({"scope": "g:1", "overrides": {"reply": "yes"}})
        self.assertFalse(ok, "覆盖值必须是布尔")
        ok2, why2 = self.reg.validate_event({"scope": "g:1", "overrides": {"伪造能力": True}})
        self.assertFalse(ok2, "词汇外的覆盖键必须拒绝")

    # ── ★ 零行为变化：开关关 / standard / 软依赖 三种情况都放行 ──
    def test_switch_off_allows(self):
        self._cfg(switch=False)
        self.assertTrue(bot_core._group_allowed("123"))

    def test_switch_on_standard_allows(self):
        self._cfg(switch=True)
        self.assertTrue(bot_core._group_allowed("123"), "standard 下 reply 必须放行")

    def test_soft_dependency_denies_nothing(self):
        self._cfg(switch=True)
        bot_core._SCOPE_REGISTRY = False
        self.assertTrue(bot_core._group_allowed("123"), "注册表缺失 → 软依赖，不得拦")

        class _Boom:
            def is_allowed(self, *a, **k):
                raise RuntimeError("炸")
        bot_core._SCOPE_REGISTRY = _Boom()
        self.assertTrue(bot_core._group_allowed("123"), "注册表异常 → 按允许处理")

    # ── 旧层仍优先（语义未变）──
    def test_legacy_layer_still_first(self):
        self._cfg(switch=True, extra={"group_restrictions": {"123": {"reply_enabled": False}}})
        self.assertFalse(bot_core._group_allowed("123"),
                         "旧层 reply_enabled=False 仍然拒绝（且注册表根本没被问到）")

    # ── 新能力：让某个群闭嘴（开关开才有意义）──
    def test_registry_can_silence_a_group(self):
        self._cfg(switch=True)
        self.assertTrue(bot_core._group_allowed("123"))
        self.reg.set_capability("group", "123", "reply", False)
        self.assertFalse(bot_core._group_allowed("123"), "覆盖后该群应静默")
        self.assertTrue(bot_core._group_allowed("999"), "其它群不受影响")

    # ── 出口收敛（源码级）：4 个发言出口都必须经 _group_allowed ──
    def test_exits_converge_on_single_gate(self):
        with open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        # 函数定义 1 处 + 调用若干；至少 4 个调用点
        calls = len(re.findall(r"_group_allowed\(", src)) - 1
        self.assertGreaterEqual(calls, 4, "发言出口应 ≥4 处（当前 %d）" % calls)
        # 不得有出口绕过它直接查旧层（旧层只在 _group_block_reason 内部使用）
        body = src[src.index("def _group_allowed"):]
        body = body[:body.find("\ndef ", 10)]
        self.assertIn("_group_block_reason(gid)", body)
        self.assertIn("capability_registry_enable", body, "新层判定必须在同一个判定点里")


if __name__ == "__main__":
    unittest.main(verbosity=2)
