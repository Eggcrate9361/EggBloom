# -*- coding: utf-8 -*-
"""
2.0 记忆相对注入 · 阶段A 回归测试（检索分数透传 + 阈值闸）

验证三层：
1. search_router._score_candidates：返回带分降序、分数裁到 [0,1]，
   sand_density 剥分后顺序与旧实现一致（兼容）。
2. SearchRouter.search_scored：stub 三路检索器 → 四元组带分降序；
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
_NEX = os.path.join(_ROOT, "nexsandglass")
for p in (_ROOT, _NEX):
    if p not in sys.path:
        sys.path.insert(0, p)


class TestScoreCandidates(unittest.TestCase):
    def test_scored_sorted_and_clipped(self):
        from search_router import _score_candidates
        cands = [
            (1, "2026-01-01 10:00:00", "露营装备 帐篷 睡袋 清单"),
            (2, "2026-01-02 10:00:00", "完全无关的内容"),
            (3, "2026-01-03 10:00:00", "周末去露营 帐篷"),
        ]
        from sandglass_vault import _query_tokens
        scored = _score_candidates(cands, _query_tokens("露营 帐篷"), "露营 帐篷")
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
        """sand_density == _score_candidates 剥分，顺序不变。"""
        from search_router import _score_candidates, sand_density
        cands = [(i, "ts", f"露营 帐篷 item{i}") for i in range(1, 6)]
        from sandglass_vault import _query_tokens
        tokens = _query_tokens("露营 帐篷")
        a = [it for _, it in _score_candidates(cands, tokens, "露营 帐篷")]
        b = sand_density(cands, tokens, "露营 帐篷")
        self.assertEqual(a, b)


class _Stub:
    def __init__(self, hits):
        self._hits = hits
    def search(self, query, limit=30):
        return self._hits


class TestSearchScored(unittest.TestCase):
    def _router(self, hits):
        from search_router import SearchRouter
        return SearchRouter(shadow=_Stub([]), fts5=_Stub(hits),
                            idx=_Stub([]), tfidf=_Stub([]), mmap=_Stub([]))

    def test_returns_scored_tuples(self):
        hits = [(1, "ts1", "某市铁路学院 专科"),
                (2, "ts2", "南华大学 食堂")]
        r = self._router(hits)
        out = r.search_scored("铁路学院", limit=2)
        self.assertTrue(out)
        for item in out:
            self.assertEqual(len(item), 4, f"应四元组: {item}")
            self.assertTrue(0.0 <= item[3] <= 1.0)
        # search() 旧契约：三元组
        old = r.search("铁路学院", limit=2)
        for item in old:
            self.assertEqual(len(item), 3)

    def test_mmap_fallback_gets_floor_score(self):
        from search_router import SearchRouter
        r = SearchRouter(shadow=_Stub([]), fts5=_Stub([]), idx=_Stub([]),
                         tfidf=_Stub([]), mmap=_Stub([(7, "ts", "兜底命中")]))
        out = r.search_scored("兜底", limit=3)
        self.assertEqual(out, [(7, "ts", "兜底命中", 0.3)])


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
