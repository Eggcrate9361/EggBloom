# -*- coding: utf-8 -*-
"""模型能力表（model_caps）与「窗口驱动」的预算/阈值验证。

背景（2026-10-21）：
  实际模型 deepseek-v4-flash 的窗口是 1M，但代码里还写着 65536（压力分母）
  和 48000 字符（工具预算，注释称「≈24k token」）。两个都是 64k 时代的数字，
  导致：① 压力预警在只占真实窗口 3.7% 时报 60% warn；② 1M 窗口兑现不了任何收益。

  本测试锁三件事：
    · 窗口的唯一来源是 model_caps（表 / env / 默认三档，且能说明来源）；
    · 压力判定是「窗口轨 + 成本轨」双轨，1M 窗口下 40k prompt 仍会 warn；
    · 工具结果预算随窗口缩放，小窗口仍守 1500 floor。

运行：python -m unittest tests.test_model_caps -v
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import model_caps as caps          # noqa: E402
import token_stats as ts           # noqa: E402
from harness import tool_runner as tr  # noqa: E402


class TestModelCaps(unittest.TestCase):
    def test_deepseek_v4_is_1m(self):
        r = caps.resolve("deepseek-v4-flash")
        self.assertEqual(r["window"], 1048576, "deepseek-v4-flash 官方窗口 1M")
        self.assertEqual(r["max_output"], 393216, "官方输出上限 384K")
        self.assertEqual(r["source"], "table")

    def test_legacy_flash_name_also_1m(self):
        self.assertEqual(caps.context_window("deepseek-flash"), 1048576)

    def test_ordered_match_prefers_specific(self):
        self.assertEqual(caps.context_window("moonshot-v1-8k"), 8192)
        self.assertEqual(caps.context_window("moonshot-v1-128k"), 131072)

    def test_unknown_falls_back_and_says_so(self):
        r = caps.resolve("some-unknown-model-xyz")
        self.assertEqual(r["source"], "default")
        self.assertEqual(r["window"], caps.DEFAULT_CONTEXT_WINDOW)

    def test_env_override_wins(self):
        old = os.environ.get("EGGBLOOM_CTX_WINDOW")
        try:
            os.environ["EGGBLOOM_CTX_WINDOW"] = "32768"
            r = caps.resolve("deepseek-v4-flash")
            self.assertEqual(r["window"], 32768)
            self.assertEqual(r["source"], "env")
        finally:
            if old is None:
                os.environ.pop("EGGBLOOM_CTX_WINDOW", None)
            else:
                os.environ["EGGBLOOM_CTX_WINDOW"] = old

    def test_budget_is_window_times_safety(self):
        w = caps.context_window("deepseek-v4-flash")
        self.assertEqual(caps.prompt_budget_tokens("deepseek-v4-flash"),
                         int(w * caps.SAFETY_RATIO))
        self.assertEqual(caps.prompt_budget_chars("deepseek-v4-flash"),
                         int(int(w * caps.SAFETY_RATIO) * caps.CHARS_PER_TOKEN))

    def test_chars_to_tokens_consistent(self):
        self.assertEqual(caps.chars_to_tokens(0), 0)
        self.assertEqual(caps.chars_to_tokens(160), 101)


class TestPressureDualTrack(unittest.TestCase):
    def setUp(self):
        with ts._anchor_lock:
            ts._anchors.clear()

    tearDown = setUp

    def test_window_track_still_works(self):
        rec = ts.note_context_anchor("g", prompt_tokens=900, window=1000)
        self.assertEqual(rec["level"], "high")
        self.assertIn("window", rec["drivers"])

    def test_cost_track_fires_on_big_window(self):
        """1M 窗口下 40k prompt 只占 3.8%，但成本上已该提醒。"""
        rec = ts.note_context_anchor("g", prompt_tokens=40000, window=1048576)
        self.assertEqual(rec["level"], "warn")
        self.assertIn("cost", rec["drivers"], "成本轨未生效")
        self.assertNotIn("window", rec["drivers"])

    def test_cost_high(self):
        rec = ts.note_context_anchor("g", prompt_tokens=200000, window=1048576)
        self.assertEqual(rec["level"], "high")
        self.assertIn("cost", rec["drivers"])

    def test_small_prompt_stays_low(self):
        rec = ts.note_context_anchor("g", prompt_tokens=3800, window=1048576)
        self.assertEqual(rec["level"], "low")
        self.assertEqual(rec["drivers"], [])

    def test_default_window_comes_from_model_caps(self):
        """不传 window 时必须问 model_caps，而不是回到写死的 65536。"""
        import model_caps as mc
        old = mc.context_window
        try:
            mc.context_window = lambda model=None: 1048576
            rec = ts.note_context_anchor("g", prompt_tokens=1000)
            self.assertEqual(rec["window"], 1048576)
        finally:
            mc.context_window = old

    def test_window_override_recomputes_drivers(self):
        ts.note_context_anchor("g", prompt_tokens=30000, window=1048576)
        got = ts.context_pressure("g", window=32768)
        self.assertEqual(got["level"], "high")
        self.assertIn("window", got["drivers"])


class TestToolBudgetScaled(unittest.TestCase):
    def test_budget_scales_with_window(self):
        self.assertEqual(tr._tool_result_budget(1048576), 6291)
        self.assertEqual(tr._tool_result_budget(131072), 1500, "小窗口守 floor")
        self.assertEqual(tr._tool_result_budget(10_000_000), 8000, "守 ceil")

    def test_context_chars_scales(self):
        self.assertEqual(tr._context_chars(1048576), int(1048576 * 1.6 * 0.8))
        self.assertEqual(tr._context_chars(131072), int(131072 * 1.6 * 0.8))

    def test_adaptive_budget_floors_when_room_gone(self):
        # 已用远超预算 → 仍给 floor，不退化成 0（模型至少要能看全一条结果）
        got = tr.adaptive_budget([{"role": "user", "content": "x" * 10_000_000}],
                                 steps_left=5, window_tokens=1048576)
        self.assertEqual(got, tr.BUDGET_FLOOR)

    def test_adaptive_budget_ceils_when_empty(self):
        got = tr.adaptive_budget([], steps_left=1, window_tokens=1048576)
        self.assertEqual(got, tr.BUDGET_CEIL)


class TestWiring(unittest.TestCase):
    """接线：能力必须被真实调用点用到，而不是只存在于模块里。"""

    def test_bot_core_has_total_gate(self):
        with open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _ctx_over_budget", src, "总量闸函数不存在")
        self.assertGreaterEqual(src.count("_ctx_over_budget"), 2,
                                "总量闸没有被调用（只有定义）")
        seg = src[src.find("[上下文闸]"):src.find("[上下文闸]") + 320]
        # 收尾入口 2026-10-27 起统一为 _finalize_reply（它内部才调 _stream_chat_lines）。
        # 断言"闸门触发后确实走收尾"，不锁具体函数名 —— 锁死名字会让
        # 合理重构被判失败，而真正要防的是"触发后什么都不做直接 continue"。
        self.assertTrue("_finalize_reply" in seg or "_stream_chat_lines" in seg,
                        "闸门触发后没有强制收尾")

    def test_token_stats_no_hardcoded_65536_window(self):
        with open(os.path.join(_ROOT, "token_stats.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('"65536"', src, "窗口默认值又写回 65536 了")
        self.assertIn("model_caps", src, "压力分母没接 model_caps")

    def test_tool_runner_no_hardcoded_48000(self):
        with open(os.path.join(_ROOT, "harness", "tool_runner.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("CONTEXT_CHARS = 48000", src, "字符预算又写死 48000 了")


if __name__ == "__main__":
    unittest.main(verbosity=2)
