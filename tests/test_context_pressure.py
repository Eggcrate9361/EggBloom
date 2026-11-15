# -*- coding: utf-8 -*-
"""
T7 批二 · P23 上下文压力的「锚点式」测量 验证。

设计要点（对应 dsh-token-meter）：
  · **锚点式**：取真实一次调用的实测 prompt_tokens 打点，**不靠累加推算**
    （累加会因缓存命中/工具轮/重试而漂移，"当前占用"不可信）。
  · **A20**：拿不到实测才用字数启发式，且必须带 `estimated=True` —— 近似值要明说。
  · 三级：low(<60%) / warn(≥60%) / high(≥85%)，供"该压缩了"判断用。
  · 纯新增，不影响既有累加统计（record/snapshot/history 未动）。

运行：python -m unittest tests.test_context_pressure -v
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import token_stats as ts  # noqa: E402


class TestContextPressure(unittest.TestCase):
    def setUp(self):
        with ts._anchor_lock:
            ts._anchors.clear()

    tearDown = setUp

    # ── 实测锚点 ──
    def test_measured_anchor_not_estimated(self):
        rec = ts.note_context_anchor("g1", prompt_tokens=40000, scene="group")
        self.assertEqual(rec["prompt_tokens"], 40000)
        self.assertFalse(rec["estimated"], "实测值不得标成估算")
        self.assertEqual(rec["window"], ts._CONTEXT_WINDOW)

    def test_ratio_and_level_boundaries(self):
        w = 1000
        for tok, lvl in ((100, "low"), (599, "low"), (600, "warn"),
                         (849, "warn"), (850, "high"), (999, "high")):
            rec = ts.note_context_anchor("g", prompt_tokens=tok, window=w)
            self.assertEqual(rec["level"], lvl,
                             f"{tok}/{w}={tok/w:.3f} 应为 {lvl}，实际 {rec['level']}")

    # ── 估算路径：必须明说（A20）──
    def test_estimated_when_no_measurement(self):
        rec = ts.note_context_anchor("q1", prompt_chars=1600, scene="private")
        self.assertTrue(rec["estimated"], "无实测时必须标 estimated=True（A20）")
        self.assertGreater(rec["prompt_tokens"], 0)
        self.assertEqual(rec["scene"], "private")

    def test_measured_wins_over_chars(self):
        """两者都给 → 用实测，不算估算。"""
        rec = ts.note_context_anchor("g", prompt_tokens=5000, prompt_chars=99999)
        self.assertEqual(rec["prompt_tokens"], 5000)
        self.assertFalse(rec["estimated"])

    def test_no_input_returns_none(self):
        self.assertIsNone(ts.note_context_anchor("g"))
        self.assertIsNone(ts.note_context_anchor("g", prompt_tokens=0, prompt_chars=0))

    # ── 查询 ──
    def test_query_one_scope(self):
        ts.note_context_anchor("g1", prompt_tokens=30000)
        got = ts.context_pressure("g1")
        self.assertEqual(got["prompt_tokens"], 30000)
        self.assertIn("level", got)
        self.assertEqual(ts.context_pressure("不存在"), {})

    def test_query_all_sorted_by_pressure_desc(self):
        ts.note_context_anchor("low", prompt_tokens=1000)
        ts.note_context_anchor("high", prompt_tokens=60000)
        ts.note_context_anchor("mid", prompt_tokens=30000)
        order = [r["scope"] for r in ts.context_pressure()]
        self.assertEqual(order, ["high", "mid", "low"], "未按压力降序（排查要一眼看到最满的）")

    def test_window_override_recomputes(self):
        ts.note_context_anchor("g", prompt_tokens=30000, window=65536)
        got = ts.context_pressure("g", window=32768)     # 换更小的窗口
        self.assertEqual(got["window"], 32768)
        self.assertEqual(got["level"], "high", "窗口变小后压力应升级")
        self.assertEqual(ts.context_pressure("g")["window"], 65536, "原锚点不该被改写")

    # ── 健壮性（它在回复路径上，绝不能抛）──
    def test_never_raises(self):
        self.assertIsNone(ts.note_context_anchor(None, prompt_tokens="不是数字"))
        self.assertIsNone(ts.note_context_anchor("g", prompt_tokens=-5))
        ts.note_context_anchor("g", prompt_tokens=100, window=0)     # 窗口非法 → 回落
        self.assertTrue(ts.context_pressure("g")["window"] > 0)
        self.assertIsInstance(ts.context_pressure(), list)           # 全量查询不炸

    def test_latest_anchor_wins(self):
        ts.note_context_anchor("g", prompt_tokens=1000)
        ts.note_context_anchor("g", prompt_tokens=50000)
        self.assertEqual(ts.context_pressure("g")["prompt_tokens"], 50000)

    # ── 接线（源码级）：真实调用点必须打锚点，且只用实测 ──
    def test_wired_at_real_call_sites(self):
        with open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("note_context_anchor", src, "bot_core 未打锚点")
        self.assertGreaterEqual(src.count("note_context_anchor"), 2,
                                "至少两处真实调用点（流式收尾 + 主同步调用）")
        # 主调用点必须用实测 prompt_tokens，不得在那里退化成估算
        seg = src[src.find("P23 锚点（实测）"):src.find("P23 锚点（实测）") + 600]
        self.assertIn("prompt_tokens=", seg)
        self.assertNotIn("prompt_chars=", seg, "真实调用点不该用估算")

    def test_exposed_in_api(self):
        with open(os.path.join(_ROOT, "webui", "webui_server.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("context_pressure", src, "/api/token-stats 未回上下文压力")


if __name__ == "__main__":
    unittest.main(verbosity=2)
