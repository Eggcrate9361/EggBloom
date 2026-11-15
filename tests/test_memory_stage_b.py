# -*- coding: utf-8 -*-
"""
2.0 记忆相对注入 · 阶段B 回归测试（harness/memory_context builder）

打桩两源，验证：阈值闸 / 跨源去重 / 预算与条数封顶 / 排序 / 包装格式 /
源降级（一源挂另一源照常、全挂返回空）/ 开关。
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from harness import memory_context as mc  # noqa: E402


class TestBuilder(unittest.TestCase):
    def setUp(self):
        mc.set_config(tau=0.35, budget=400, max_items=4, width=140,
                      per_source_limit=6, enabled=True)
        self._sand = []
        self._sm = []
        mc.set_sources(
            sandglass=lambda message, limit=6: list(self._sand)[:limit],
            super_mem=lambda message, scope, scope_id, limit=6: list(self._sm)[:limit],
        )

    def tearDown(self):
        mc._reset_sources()

    # ── 阈值 ──
    def test_tau_filters_low_scores(self):
        self._sand = [(0.81, "高相关露营装备"), (0.20, "低相关旧账")]
        self._sm = [{"content": "向量高相关", "sim": 0.66, "rank_score": 0.5}]
        items = mc.collect("露营 装备")
        texts = [t for _, t in items]
        self.assertIn("高相关露营装备", texts)
        self.assertIn("向量高相关", texts)
        self.assertNotIn("低相关旧账", texts)

    def test_all_below_tau_returns_empty_block(self):
        self._sand = [(0.1, "都不该进"), (0.2, "也不该进")]
        self._sm = [{"content": "半吊子", "sim": 0.3, "rank_score": 0.0}]
        self.assertEqual(mc.collect("x"), [])
        self.assertEqual(mc.build_block("x"), "")

    # ── 排序 ──
    def test_ranked_desc_by_score(self):
        self._sand = [(0.5, "中"), (0.9, "最高")]
        self._sm = [{"content": "低", "sim": 0.4, "rank_score": 0.0}]
        items = mc.collect("话题")
        self.assertEqual([s for s, _ in items], [0.9, 0.5, 0.4])

    # ── 跨源去重 ──
    def test_cross_source_dedup_keeps_higher(self):
        long_text = "同一条历史记录的完整内容需要超过二十个字符才会触发包含式去重逻辑"
        self._sand = [(0.5, long_text)]
        self._sm = [{"content": long_text, "sim": 0.88, "rank_score": 0.0}]
        items = mc.collect("任意")
        self.assertEqual(len(items), 1, f"跨源同文应去重: {items}")
        self.assertEqual(items[0][0], 0.88, "去重应保高分")

    # ── 预算 / 条数封顶 ──
    def test_budget_caps_total_chars(self):
        self._sand = [(0.9 - i * 0.01, "内容" * 60) for i in range(10)]
        block = mc.build_block("x")
        body_lines = [l for l in block.splitlines()[1:]]
        total = sum(len(l) for l in body_lines)
        self.assertLessEqual(total, 400 + 20)  # 预算 + 末行容差
        self.assertLessEqual(len(body_lines), 4, "条数上限同时生效")

    def test_width_truncation(self):
        self._sand = [(0.9, "超" * 500)]
        block = mc.build_block("x")
        for l in block.splitlines()[1:]:
            self.assertLessEqual(len(l), 140 + 12)

    # ── 包装格式 ──
    def test_block_format(self):
        self._sand = [(0.81, "露营装备清单")]
        block = mc.build_block("露营")
        self.assertTrue(block.startswith("【记忆】"))
        self.assertNotIn("(-", block)  # 分数必须 ∈ [0,1]，不该出现负分标注
        self.assertIn("- (0.81) 露营装备清单", block)
        self.assertIn("不要提及你在查记录", block)

    # ── 源降级 ──
    def test_one_source_dead_other_survives(self):
        def boom(message, limit=6):
            raise RuntimeError("沙漏挂了")
        mc.set_sources(
            sandglass=boom,
            super_mem=lambda message, scope, scope_id, limit=6: [
                {"content": "SM还活着", "sim": 0.7, "rank_score": 0.0}],
        )
        items = mc.collect("x")
        self.assertEqual([t for _, t in items], ["SM还活着"])

    def test_both_sources_dead_returns_empty(self):
        def boom1(message, limit=6):
            raise RuntimeError
        def boom2(message, scope, scope_id, limit=6):
            raise RuntimeError
        mc.set_sources(sandglass=boom1, super_mem=boom2)
        self.assertEqual(mc.build_block("x"), "")

    # ── 开关 / 空输入 ──
    def test_disabled(self):
        self._sand = [(0.9, "不该进")]
        mc.set_config(enabled=False)
        self.assertEqual(mc.build_block("x"), "")

    def test_empty_message(self):
        self._sand = [(0.9, "不该进")]
        self.assertEqual(mc.build_block(""), "")

    # ── super_mem 无向量退化 rank_score ──
    def test_sm_fallback_rank_score(self):
        self._sm = [{"content": "无向量按importance", "sim": 0.0, "rank_score": 0.6}]
        items = mc.collect("x")
        self.assertEqual(items, [(0.6, "无向量按importance")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
