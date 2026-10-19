# -*- coding: utf-8 -*-
"""
2.0 记忆相对注入 · 阶段A 回归测试（检索分数透传 + 阈值闸）

**2026-10-19 更新**：检索实现已从上游 `nexsandglass/search_router` 切到
EggBloom 自研 `sandglass_lite`（薄适配）。本测试同步改测 lite 的等价实现 ——
验证意图不变（分数降序、裁剪到 [0,1]、阈值闸、默认路径兼容），只换被测对象。
两套实现的融合公式相同（密度 × 信任 + simhash 近似加成），故断言可平移。

验证三层：
1. sandglass_lite._score_candidates：返回带分降序、分数裁到 [0,1]。
2. sandglass_lite.search_scored：四元组带分降序；
   search() 仍三元组、行为不变（下游 bot_core.search_memory 依赖）。
3. sandglass_bridge：search_scored_context 出口 +
   recall_history_block(min_score>0) 阈值闸：低分滤除、高分注入带 (0.xx)、
   默认 min_score=0 走旧路径逐字节不变。
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

# ★ 沙漏路径隔离：bridge 的检索出口在本机有真实数据，隔离避免读写生产记忆
from _sandbox import sandbox_neurobase  # noqa: E402
sandbox_neurobase()


class TestScoreCandidates(unittest.TestCase):
    def test_scored_sorted_and_clipped(self):
        from sandglass_lite import _score_candidates, query_tokens
        cands = [
            (1, "2026-01-01 10:00:00", "露营装备 帐篷 睡袋 清单"),
            (2, "2026-01-02 10:00:00", "完全无关的内容"),
            (3, "2026-01-03 10:00:00", "周末去露营 帐篷"),
        ]
        scored = _score_candidates(cands, query_tokens("露营 帐篷"), "露营 帐篷")
        self.assertEqual(len(scored), 3)
        # 降序
        scores = [s for s, _ in scored]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # 命中词多的条目（1/3）必须排在无关条目（2）之前
        top_ln = scored[0][1][0]
        self.assertIn(top_ln, (1, 3), "首位应是命中条目")
        worst = scored[-1][1][0]
        self.assertEqual(worst, 2, "无关条目必须垫底")
        # 裁剪 [0,1]
        self.assertTrue(all(0.0 <= s <= 1.0 for s in scores))

    def test_sand_density_compat(self):
        """lite 的排序与打分保持确定性：同输入两次调用结果一致。"""
        from sandglass_lite import _score_candidates, query_tokens
        cands = [(i, "ts", f"露营 帐篷 item{i}") for i in range(1, 6)]
        tokens = query_tokens("露营 帐篷")
        a = [it for _, it in _score_candidates(cands, tokens, "露营 帐篷")]
        b = [it for _, it in _score_candidates(cands, tokens, "露营 帐篷")]
        self.assertEqual(a, b, "同输入两次打分必须同序")

    def test_search_scored_clips_and_sorts(self):
        """lite 的 _score_candidates → 分数裁到 [0,1] 且降序（search_scored 契约）。"""
        from sandglass_lite import _score_candidates, query_tokens
        hits = [(1, "ts1", "某市铁路学院 专科"),
                (2, "ts2", "南华大学 食堂")]
        scored = _score_candidates(hits, query_tokens("铁路学院"), "铁路学院")
        self.assertTrue(scored)
        scores = [s for s, _ in scored]
        self.assertEqual(scores, sorted(scores, reverse=True), "必须降序")
        self.assertTrue(all(0.0 <= s <= 1.0 for s in scores), "分数须在 [0,1]")
        self.assertEqual(scored[0][1][0], 1, "命中的 1 号应排首位")

    def test_search_returns_triples(self):
        """search() 旧契约：三元组（下游 bot_core.search_memory 依赖）。"""
        import sandglass_lite as sl
        orig = sl.idx_search
        try:
            sl.idx_search = lambda q, limit=100: [
                (1, "ts1", "某市铁路学院 专科"), (2, "ts2", "南华大学 食堂")]
            out = sl.search("铁路学院", limit=2)
            for item in out:
                self.assertEqual(len(item), 3, f"应三元组: {item}")
        finally:
            sl.idx_search = orig

    def test_no_hit_returns_empty(self):
        """无命中 → 空列表（不是"退化成任意命中"）。

        回归的真实 bug（2026-10-19）：全量扫描兜底曾用**整个查询串**做子串匹配，
        长查询永远匹配不到 → 退化成返回任意 3 条。修法：按关键词匹配。
        """
        import sandglass_lite as sl
        out = sl.search("zzzz不存在的词qqqq", limit=3)
        self.assertEqual(out, [], f"无命中必须为空，实际 {out}")


class TestBridgeThreshold(unittest.TestCase):
    def setUp(self):
        import sandglass_bridge as sb
        self.sb = sb
        self._orig = (sb._enabled, sb.is_available, sb._search_context_for_message,
                      getattr(sb, "search_scored_context", None))

    def tearDown(self):
        sb = self.sb
        sb._enabled, sb.is_available, sb._search_context_for_message = self._orig[:3]
        if self._orig[3] is not None:
            sb.search_scored_context = self._orig[3]
        else:
            try:
                del sb.search_scored_context
            except AttributeError:
                pass

    def test_min_score_filters_low(self):
        sb = self.sb
        sb._enabled = True
        sb.is_available = lambda: True
        sb.search_scored_context = lambda msg, limit=5: [
            (0.81, "高相关：露营装备清单"),
            (0.44, "中相关：上次说的帐篷"),
            (0.20, "低相关：半年前的旧账"),
        ]
        sb._search_context_for_message = lambda msg, limit=3: "不该被调用"
        out = sb.recall_history_block("露营", min_score=0.35)
        self.assertIn("露营装备清单 (0.81)", out)
        self.assertIn("上次说的帐篷 (0.44)", out)
        self.assertNotIn("半年前的旧账", out, "低于阈值必须滤除")

    def test_min_score_all_below_returns_empty(self):
        sb = self.sb
        sb._enabled = True
        sb.is_available = lambda: True
        sb.search_scored_context = lambda msg, limit=5: [(0.1, "都不该进")]
        out = sb.recall_history_block("x", min_score=0.35)
        self.assertEqual(out, "")

    def test_default_keeps_legacy_path(self):
        sb = self.sb
        sb._enabled = True
        sb.is_available = lambda: True
        sb._search_context_for_message = lambda msg, limit=3: "  - 旧路径命中"
        sb.search_scored_context = lambda msg, limit=5: (_ for _ in ()).throw(
            AssertionError("默认路径不该走 scored"))
        out = sb.recall_history_block("任意")
        self.assertIn("旧路径命中", out)
        self.assertNotIn("(0.", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
