# -*- coding: utf-8 -*-
"""工具多轮续轮判据（2026-10-23 加）。

为什么要测：
  `max_tool_rounds` 的判据错一个符号，表现只是「多烧一轮 token」或者
  「少查一次就硬答」，日志里一切正常、回复也不报错 —— 属于典型的静默失败。
  而它恰好又是「模型假装查过」的诱因之一：只给一轮，模型拿不到正文，
  只能凭搜索摘要硬写，写不出来就开始编。

运行：python -m unittest tests.test_tool_rounds -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

go = bot_core._should_continue_tool_round


class TestContinue(unittest.TestCase):
    def test_round_one_with_success_continues(self):
        """第 1 轮拿到东西、上限 3 → 还有余量，继续。"""
        self.assertTrue(go(1, [True], 3))

    def test_reaches_limit_stops(self):
        """上限 3，跑完第 3 轮 → 停。"""
        self.assertFalse(go(3, [True], 3))

    def test_over_limit_stops(self):
        self.assertFalse(go(5, [True], 3))


class TestStopOnTotalFailure(unittest.TestCase):
    def test_all_failed_stops_immediately(self):
        """全部失败 → 停下来如实说，不再耗一轮问同一个问题。"""
        self.assertFalse(go(1, [False, False], 3))

    def test_any_success_keeps_going(self):
        """只要有一个成功，就还值得继续。"""
        self.assertTrue(go(1, [False, True], 3))

    def test_blocked_counts_as_failure(self):
        """被拒（权限/白名单）不算拿到东西。"""
        self.assertFalse(go(1, [False], 3))


class TestEdge(unittest.TestCase):
    def test_limit_one_behaves_like_old(self):
        """max_tool_rounds=1 → 退回旧行为：一轮就收尾。"""
        self.assertFalse(go(1, [True], 1))

    def test_limit_zero_treated_as_one(self):
        """配错成 0/负数也至少跑一轮，不能变成"永远不调工具"。"""
        self.assertFalse(go(1, [True], 0))
        self.assertFalse(go(1, [True], -5))

    def test_bad_limit_falls_back(self):
        """配置写成字符串/None 不抛异常。"""
        self.assertTrue(go(1, [True], None))
        self.assertTrue(go(1, [True], "3"))

    def test_empty_flags_stops(self):
        """这一轮一个工具都没真跑（被归一化丢掉了）→ 不续轮。"""
        self.assertFalse(go(1, [], 3))

    def test_bad_round_no_stops(self):
        self.assertFalse(go(None, [True], 3))


class TestConfigDefault(unittest.TestCase):
    def test_default_present(self):
        """配置项存在且 >= 1，否则整条多轮逻辑等于不存在。"""
        self.assertGreaterEqual(bot_core._config.get("max_tool_rounds", 0), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)