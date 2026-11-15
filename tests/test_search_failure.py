# -*- coding: utf-8 -*-
"""搜索失败的处理 —— 锁住「别把失败提示当搜索结果喂回去」。

背景（2026-10-22，真实私聊）：
  问「恋人不行你看过吗」→ 模型输出 [搜索:恋人不行] → 三个引擎都没搜到，
  `search_web` 返回的是一句提示语
      "搜索「恋人不行」失败或无结果。如实告诉用户没搜到，**不要编造搜索结果**。"
  旧实现把这句话原样拼进「[系统搜索结果]」喂回模型，于是模型同时收到
      "如实告诉用户没搜到"  +  "请直接基于以上搜索结果回答用户问题"
  两句互斥的指令，只能硬凑出敷衍的一问：
      「没看过诶 / 不过好像听说过，百合的吧 / 讲啥的，姬撩直女那种？」
  下一轮又催「说啊，恋人不行到底讲啥」。

这个文件盯三件事：
  ① 失败提示要能认出来（_is_search_failure）
  ② 失败时喂回去的**不能**是「请基于以上搜索结果作答」那套
  ③ 失败一次就别让模型反复重搜硬凑
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_core  # noqa: E402


class TestIsSearchFailure(unittest.TestCase):
    def test_failure_prompts_recognized(self):
        cases = [
            "搜索「恋人不行」失败或无结果。如实告诉用户没搜到，**不要编造搜索结果**。",
            "搜索失败",
            "未找到相关内容",
            "没有找到",
            "无结果",
            "",
            None,
        ]
        for c in cases:
            with self.subTest(c=c):
                self.assertTrue(bot_core._is_search_failure(c))

    def test_real_results_not_treated_as_failure(self):
        cases = [
            "【恋人不行】搜索结果：\n1. 恋爱喜剧动画（acg. fandom.com）：简介…",
            "今天北京天气：晴 25℃",
            "没有找到"  # ← 这个是真的失败，见上面
        ]
        for c in cases[:2]:
            with self.subTest(c=c):
                self.assertFalse(bot_core._is_search_failure(c))


class TestSearchTagLoop(unittest.TestCase):
    """用假 search_web / call_deepseek 跑一遍 _apply_search_tag_loop。"""

    def setUp(self):
        self._orig_search = bot_core.search_web
        self._orig_call = bot_core.call_deepseek
        self.retry_prompts = []
        self.search_queries = []

    def tearDown(self):
        bot_core.search_web = self._orig_search
        bot_core.call_deepseek = self._orig_call

    def _fake_search(self, result):
        def _s(query):
            self.search_queries.append(query)
            return result
        return _s

    def _fake_call(self, reply):
        def _c(msg, tools, **kw):
            self.retry_prompts.append(msg)
            return reply
        return _c

    def _run(self, first_reply, search_result, second_reply="好了"):
        bot_core.search_web = self._fake_search(search_result)
        bot_core.call_deepseek = self._fake_call(second_reply)
        return bot_core._apply_search_tag_loop(
            first_reply, "恋人不行你看过吗", group_id=0, is_group=False,
            temperature=0.8)

    # ── 失败时 ────────────────────────────────────────────────────
    def test_failure_does_not_feed_results_instruction(self):
        self._run("[搜索:恋人不行]",
                  "搜索「恋人不行」失败或无结果。如实告诉用户没搜到，**不要编造搜索结果**。")
        self.assertTrue(self.retry_prompts, "应该重答过一次")
        p = self.retry_prompts[0]
        self.assertNotIn("请直接基于以上搜索结果回答用户问题", p)
        self.assertNotIn("必须用现有信息作答", p)
        self.assertIn("没成功", p)
        # 明确禁止把问题推回去
        self.assertIn("不要把问题推回去让对方替你查", p)

    def test_failure_stops_research_loop(self):
        """模型再吐 [搜索:] 也不该再搜一次（否则它会一直搜到超时）。"""
        bot_core.search_web = self._fake_search(
            "搜索「x」失败或无结果。如实告诉用户没搜到。")
        bot_core.call_deepseek = self._fake_call("[搜索:恋人不行]")
        bot_core._apply_search_tag_loop(
            "[搜索:恋人不行]", "恋人不行你看过吗", group_id=0,
            is_group=False, temperature=0.8)
        self.assertEqual(len(self.search_queries), 1,
                         f"只该搜一次，实际 {self.search_queries}")

    def test_failure_reply_has_no_search_tag_left(self):
        out = self._run("[搜索:恋人不行]", "搜索「x」失败或无结果。", "[搜索:恋人不行]")
        self.assertNotIn("[搜索:", out)

    # ── 成功时：老行为不能变 ──────────────────────────────────────
    def test_success_still_feeds_results(self):
        self._run("[搜索:恋人不行]", "【恋人不行】搜索结果：\n1. 恋爱喜剧（fandom）：简介…")
        p = self.retry_prompts[0]
        self.assertIn("请直接基于以上搜索结果回答用户问题", p)
        self.assertIn("【恋人不行】搜索结果", p)

    def test_success_allows_second_search(self):
        """成功时仍允许第二轮搜索（老行为）。"""
        bot_core.search_web = self._fake_search("【x】搜索结果：\n1. 某结果")
        bot_core.call_deepseek = self._fake_call("[搜索:再搜一次]")
        bot_core._apply_search_tag_loop(
            "[搜索:第一次]", "问题", group_id=0, is_group=False, temperature=0.8)
        self.assertEqual(len(self.search_queries), 2, self.search_queries)


class TestToolWeb(unittest.TestCase):
    """_tool_web 失败时的返回要带上「别推回去」。"""

    def setUp(self):
        self._orig = bot_core.search_web

    def tearDown(self):
        bot_core.search_web = self._orig

    def test_failed_tool_result_tells_model_not_to_push_back(self):
        bot_core.search_web = lambda q: "搜索「x」失败或无结果。如实告诉用户没搜到。"
        out = bot_core._tool_web("x")
        self.assertIn("❌", out)
        self.assertIn("别把问题推回去让对方替你查", out)
        # 失败提示原文可以带，但不能作为"结果"呈现
        self.assertIn("失败或无结果", out)

    def test_empty_result_marked_failed(self):
        bot_core.search_web = lambda q: ""
        out = bot_core._tool_web("x")
        self.assertIn("❌", out)

    def test_success_passes_through(self):
        bot_core.search_web = lambda q: "【x】搜索结果：\n1. 真的结果"
        out = bot_core._tool_web("x")
        self.assertNotIn("❌", out)
        self.assertIn("真的结果", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
