# -*- coding: utf-8 -*-
"""
T7 · P4 · S6 —— 审计留痕与只读查询接口验证。

设计稿 S6 = 「审计留痕（并入 1.7 里 P10 降级的那半）」。
参考源 dsh-permission-presets 的职责分离：审计属于**本模块**（谁在何时改了什么），
执行行为属于各执行方。所以这里验两件事：
  1. 注册表能给出完整留痕（谁 / 何时 / 哪个作用域 / 从什么到什么）与**降级计数**
  2. 只读接口 `/api/scope-policy` 已接线（写入端点留给 S5，等前端风格）

运行：python -m unittest tests.test_scope_audit -v
"""
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from harness.scope_registry import ScopeRegistry  # noqa: E402


class TestScopeAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="scopeaudit_")
        self.ev = os.path.join(self.tmp, "ev.jsonl")
        self.warns = []
        self.reg = ScopeRegistry(events_path=self.ev, on_warn=self.warns.append)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── 留痕内容 ──
    def test_audit_records_who_when_what(self):
        self.reg.set_preset("group", "111", "readonly", by="admin")
        self.reg.set_capability("group", "111", "generate_image", True, by="admin")
        a = self.reg.audit(10)
        self.assertEqual(len(a), 2)
        self.assertEqual(a[0]["scope"], "group:111")
        self.assertEqual(a[0]["to"], "readonly")
        self.assertEqual(a[0]["by"], "admin")
        self.assertGreater(a[0]["ts"], 0)
        self.assertEqual(a[1]["overrides"], {"generate_image": True})

    def test_audit_scoped_query(self):
        self.reg.set_preset("group", "1", "readonly")
        self.reg.set_preset("group", "2", "full")
        only1 = self.reg.events("group", "1")
        self.assertEqual(len(only1), 1)
        self.assertEqual(only1[0]["scope"], "group:1")

    def test_audit_survives_replay(self):
        self.reg.set_preset("group", "1", "readonly", by="webui")
        reg2 = ScopeRegistry(events_path=self.ev, on_warn=lambda m: None)
        a = reg2.audit(5)
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["by"], "webui", "回放后留痕必须还在")

    # ── 降级计数 ──
    def test_degraded_counter_starts_zero(self):
        self.assertEqual(self.reg.stats()["degraded"], 0)

    def test_degraded_counts_bad_explicit_preset(self):
        self.reg.resolve("group", "1", explicit="不存在的档")
        self.assertEqual(self.reg.stats()["degraded"], 1)
        self.assertTrue(self.warns)

    def test_degraded_counts_internal_failure(self):
        # 破坏一个**确实在成功路径上**的点：词汇表（stats 已做防御，不受影响）
        self.reg._vocab = None
        r = self.reg.resolve("group", "1")
        self.assertTrue(r["degraded"], "内部异常应降级为安全默认")
        self.assertIn("reply", r["capabilities"], "降级时低风险放行")
        self.assertEqual(self.reg.stats()["degraded"], 1)
        self.assertNotIn("error", self.reg.stats(), "stats 不该因内部被破坏而失败")

    # ── 统计 ──
    def test_stats_shape(self):
        self.reg.set_preset("group", "1", "readonly")
        self.reg.set_capability("group", "2", "reply", False)
        s = self.reg.stats()
        self.assertEqual(s["scopes"], 2)
        self.assertEqual(s["events"], 2)
        self.assertEqual(s["overrides"], 1)
        self.assertEqual(s["default_preset"], "standard")
        self.assertGreaterEqual(s["caps"], 16)
        self.assertEqual(s["presets"], 3)

    # ── 接口接线（源码级；接口本身要起 HTTP 才能真跑）──
    def test_readonly_endpoint_wired(self):
        with open(os.path.join(_ROOT, "webui", "webui_server.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"/api/scope-policy"', src, "只读查询端点未接线")
        self.assertIn("reg.audit(50)", src, "端点应回留痕")
        self.assertIn("reg.stats()", src, "端点应回统计")
        # 写入端点属 S5（等前端风格）；此刻不该存在
        self.assertNotIn("/api/scope-policy/set", src,
                         "写入端点属于 S5，S6 不应提前加")

    def test_presets_endpoint_exposes_custom_as_reserved(self):
        ps = self.reg.presets()
        self.assertIn("custom", ps)
        self.assertTrue(ps["custom"].get("reserved"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
