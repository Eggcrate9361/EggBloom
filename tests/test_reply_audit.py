# -*- coding: utf-8 -*-
"""事后校验：说了做过，就查有没有真做。

这个模块的风险不在漏拦，在**误伤**：把「查到了」「我记得」当成撒谎拦掉，
会把正常回复改成「我没查」，比原问题更糟。所以用例里正向反向都要有。
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import reply_audit as ra


def _calls(*names, ok=True):
    """造一批台账记录。"""
    now = time.time()
    return [(now, n, ok, "") for n in names]


class TestMustCatch(unittest.TestCase):
    """声称完成了操作、但台账里没有对应调用 —— 必须命中。"""

    def test_claimed_send_without_call(self):
        # 真机原话 08:48:20，那段时间日志零工具调用
        r = "行，我上一条说错了，发文件这事儿我确实能干。就是有点大，706MB，这会儿在后台传着"
        hits = ra.find_unsupported_claims(r, [])
        self.assertTrue(hits, "声称在传文件但没调工具，必须拦")

    def test_claimed_open_without_call(self):
        # 真机原话 09:11:03
        r = "我点进去看了，仓库已经能打开，38 个 commit"
        hits = ra.find_unsupported_claims(r, [])
        self.assertTrue(hits, "声称点进去看了但没调工具，必须拦")

    def test_claimed_save_without_call(self):
        hits = ra.find_unsupported_claims("已经保存好了，你去看看", [])
        self.assertTrue(hits)

    def test_claimed_screenshot_without_call(self):
        hits = ra.find_unsupported_claims("截好了，你看下", [])
        self.assertTrue(hits)

    def test_failed_call_does_not_count(self):
        """调了但失败了，撑不起「已发送」。"""
        r = "已发送，查收"
        hits = ra.find_unsupported_claims(r, _calls("send_user_file", ok=False))
        self.assertTrue(hits, "工具调用失败时不能说已发送")


class TestMustNotCatch(unittest.TestCase):
    """以下都不该拦 —— 误伤比漏拦更糟。"""

    def test_with_real_call(self):
        r = "已发送，查收"
        hits = ra.find_unsupported_claims(r, _calls("send_user_file"))
        self.assertEqual(hits, [])

    def test_knowledge_statement(self):
        """「查到了」是知识类表述，可能来自上下文，不需要工具背书。"""
        for r in ("我查到了，是 38 个 commit",
                  "我记得你之前说过这个",
                  "应该是 1.5.7 版本",
                  "看起来还没上线"):
            with self.subTest(r=r):
                self.assertEqual(ra.find_unsupported_claims(r, []), [],
                                 f"不该拦知识类表述: {r}")

    def test_self_denial(self):
        """主动说没做成，本身就是诚实的。"""
        for r in ("我发不了，这轮没有那个工具",
                  "没发出去，工具报错了",
                  "我做不到，权限不够"):
            with self.subTest(r=r):
                self.assertEqual(ra.find_unsupported_claims(r, []), [],
                                 f"不该拦主动澄清: {r}")

    def test_talking_about_tools_not_using_them(self):
        """描述工具集不是声称用过。"""
        r = "这轮我手上压根没有发文件的工具"
        self.assertEqual(ra.find_unsupported_claims(r, []), [])

    def test_plain_chat(self):
        for r in ("行", "哦", "6", "啊？", "你才是有病吧"):
            with self.subTest(r=r):
                self.assertEqual(ra.find_unsupported_claims(r, []), [])

    def test_quoted_others_message(self):
        """引用/转发别人的话，不是自己在声称做过。

        来源（2026-10-23 语料库实测）：群里转发消息的正文里有
        「AAA无理由退货 : ✅ 已发送 EggBloom-Full.zip」—— 那是发文件插件
        自己打的回执，文件确实发出去了。模型引用它却按「没调工具」拦下来，
        等于把一句真话改成「我没发出去」。
        """
        for r in ("AAA无理由退货 : ✅ 已发送 EggBloom-Full.zip（709.3MB）",
                  "[(/ω＼)] 已发送，查收",
                  "✅ 已发送"):
            with self.subTest(r=r):
                self.assertEqual(ra.find_unsupported_claims(r, []), [],
                                 f"不该拦引用/回执: {r}")

    def test_bot_own_plain_claim_still_caught(self):
        """模型自己正常说话没有这些格式，不能靠它逃过校验。"""
        for r in ("已发送，查收", "发给你了，看看对不对"):
            with self.subTest(r=r):
                self.assertTrue(ra.find_unsupported_claims(r, []),
                                f"这是自己声称，必须拦: {r}")


class TestRewrite(unittest.TestCase):
    def test_rewrite_replaces_claim_keeps_rest(self):
        r = "行\n已发送，查收\n还有啥要我做的"
        hits = ra.find_unsupported_claims(r, [])
        out = ra.rewrite_unsupported(r, hits)
        self.assertIn("没发出去", out)
        self.assertIn("还有啥要我做的", out, "不该把无关句子也删掉")
        self.assertNotIn("已发送", out)

    def test_rewrite_no_hits_returns_same(self):
        r = "行吧，那就这样"
        self.assertEqual(ra.rewrite_unsupported(r, []), r)


class TestLedger(unittest.TestCase):
    def setUp(self):
        from harness import tool_runner
        self.tr = tool_runner
        self.tr.clear_tool_ledger()

    def test_note_and_read(self):
        self.tr.note_tool_call("send_user_file", True, "")
        got = self.tr.recent_tool_calls(60)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][1], "send_user_file")
        self.assertTrue(got[0][2])

    def test_window_filters_old(self):
        self.tr.note_tool_call("send_user_file", True, "")
        # 窗口设 0 秒 → 刚才那条也算过期
        self.assertEqual(self.tr.recent_tool_calls(0), [])

    def test_ledger_capped(self):
        for i in range(250):
            self.tr.note_tool_call(f"t{i}", True, "")
        # 不读私有常量，只验证不会无限增长导致内存问题
        self.assertLessEqual(len(self.tr.recent_tool_calls(600)), 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)