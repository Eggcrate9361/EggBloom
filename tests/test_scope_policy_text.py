# -*- coding: utf-8 -*-
"""
T7 · P4 · S4 —— 能力档位的「模型可见文本」验证（照搬 dsh-sandbox-policy）。

参考源的三条约束，逐条验：
  1. **只说档位、不枚举已挂载能力**（原文：「它不枚举已挂载的能力」）
  2. **默认档位不注入** → 开关关 / 未配置时 prompt 逐字节不变
  3. 放进 dynamic_parts（**追加**），不碰 static_prompt → **保住前缀缓存**（A8）

运行：python -m unittest tests.test_scope_policy_text -v
"""
import hashlib
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_core  # noqa: E402
from harness.scope_registry import ScopeRegistry  # noqa: E402


class TestScopePolicyText(unittest.TestCase):
    def setUp(self):
        self._orig_cfg = bot_core._config
        self._orig_reg = bot_core._SCOPE_REGISTRY
        self._snap = dict(self._orig_cfg)
        self.tmp = tempfile.mkdtemp(prefix="scopetext_")
        self.reg = ScopeRegistry(events_path=os.path.join(self.tmp, "ev.jsonl"),
                                 on_warn=lambda m: None)

    def tearDown(self):
        import shutil
        bot_core._config = self._orig_cfg
        bot_core._SCOPE_REGISTRY = self._orig_reg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, switch):
        c = dict(self._snap)
        c["capability_registry_enable"] = switch
        bot_core._config = c
        bot_core._SCOPE_REGISTRY = self.reg

    # ── 约束 2：默认/关闭时不注入 ──
    def test_switch_off_no_text(self):
        self._cfg(False)
        self.assertEqual(bot_core._scope_policy_block("group", "1"), "")

    def test_default_preset_no_text(self):
        self._cfg(True)
        self.assertEqual(bot_core._scope_policy_block("group", "1"), "",
                         "standard（默认档）不该说话 —— 否则未配置的人 prompt 会变")

    def test_no_overrides_no_text(self):
        self._cfg(True)
        self.reg.set_preset("group", "1", "standard")      # 显式切到默认档
        self.assertEqual(bot_core._scope_policy_block("group", "1"), "")

    def test_registry_missing_no_text(self):
        self._cfg(True)
        bot_core._SCOPE_REGISTRY = False
        self.assertEqual(bot_core._scope_policy_block("group", "1"), "")
        bot_core._SCOPE_REGISTRY = None                    # 触发懒加载失败路径

    # ── 偏离默认才说话 ──
    def test_readonly_produces_text(self):
        self._cfg(True)
        self.reg.set_preset("group", "1", "readonly")
        t = bot_core._scope_policy_block("group", "1")
        self.assertIn("readonly", t)
        self.assertIn("能力档位", t)

    def test_full_produces_text(self):
        self._cfg(True)
        self.reg.set_preset("group", "1", "full")
        self.assertIn("full", bot_core._scope_policy_block("group", "1"))

    def test_custom_produces_text_and_names_base_preset(self):
        self._cfg(True)
        self.reg.set_capability("group", "1", "reply", False)
        t = bot_core._scope_policy_block("group", "1")
        self.assertIn("自定义", t)
        self.assertIn("standard", t, "custom 应说明它是在哪个档位之上的")

    # ── 约束 1：不枚举能力 ──
    def test_does_not_enumerate_capabilities(self):
        self._cfg(True)
        self.reg.set_preset("group", "1", "readonly")
        self.reg.set_capability("group", "1", "reply", False)
        t = bot_core._scope_policy_block("group", "1")
        for cap in ("search_web", "generate_image", "control_power", "get_group_members",
                    "web_operate", "send_user_file"):
            self.assertNotIn(cap, t, "档位文本里不得枚举具体能力：命中 %s" % cap)

    def test_tells_model_how_to_behave_on_denial(self):
        self._cfg(True)
        self.reg.set_preset("group", "1", "readonly")
        t = bot_core._scope_policy_block("group", "1")
        self.assertIn("不被允许", t, "要告诉模型被拒时怎么说")
        self.assertIn("不要反复重试", t)
        self.assertIn("不要假装", t)

    # ── 约束 3：追加而非改写前缀 ──
    def test_appended_to_dynamic_not_static(self):
        """static_prompt 的指纹必须与开关无关（档位文本只进 dynamic_parts）。"""
        fp_before = bot_core._static_prompt_fingerprint(True)
        self._cfg(True)
        self.reg.set_preset("group", "1", "readonly")
        fp_after = bot_core._static_prompt_fingerprint(True)
        self.assertEqual(fp_before, fp_after,
                         "档位文本不得影响 static_prompt 指纹（否则作废前缀缓存）")

    def test_static_prompt_bytes_unchanged(self):
        self._cfg(True)
        self.reg.set_preset("group", "1", "readonly")
        bot_core._STATIC_PROMPT_CACHE.clear()
        a = bot_core._build_static_prompt(True)
        self._cfg(False)
        bot_core._STATIC_PROMPT_CACHE.clear()
        b = bot_core._build_static_prompt(True)
        self.assertEqual(hashlib.sha256(a.encode()).hexdigest(),
                         hashlib.sha256(b.encode()).hexdigest(),
                         "★ static_prompt 必须与档位无关（逐字节）")

    # ── 接线（源码级）──
    def test_wired_in_dynamic_parts(self):
        with open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('_scope_policy_block("group", group_id)', src,
                      "档位文本必须接进群聊 dynamic_parts")
        seg = src[src.index("P4·S4：能力档位"):src.index("P4·S4：能力档位") + 300]
        # 2026-10-18：档位文本已从 dynamic_parts 挪到 stable_parts（进可缓存前缀），
        # 断言只要求「走追加、不碰 static_prompt」，不再锁死具体列表名。
        self.assertTrue(
            "stable_parts.append" in seg or "dynamic_parts.append" in seg,
            "必须走 append（追加），不碰 static_prompt")
        self.assertNotIn("static_prompt", seg, "不得改写 static_prompt")


if __name__ == "__main__":
    unittest.main(verbosity=2)
