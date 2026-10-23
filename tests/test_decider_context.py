# -*- coding: utf-8 -*-
"""决策器场感数据（2026-10-23 新增）单测。

为什么单独立文件：
  这三个函数是"决策器能不能读空气"的全部输入，但它们**全部包在 try/except 里**
  —— 写错了不会报错，只会静默返回 -1，然后决策器的 prompt 里渲染成「无记录」。
  实测已经踩过两次：
    · `for t, _ in GROUP_BOT_REPLIES` 解包写成 t，引用 ts → NameError 被吞，
      share 永远是「无记录」
    · `batch_msg_count` 写成 merged_n - 1（语义反了，会报"这批 0 条"）
  静默降级的代码必须有不静默的测试。

运行：python -m unittest tests.test_decider_context -v
"""
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

GID = "_test_decider_ctx_"


class _Clean(unittest.TestCase):
    def setUp(self):
        bot_core.GROUP_RECENT_MSGS.pop(GID, None)
        bot_core.GROUP_BOT_REPLIES.pop(GID, None)
        bot_core.GROUP_LAST_BOT_MSG.pop(GID, None)

    def tearDown(self):
        self.setUp()


class TestLastReplyAgo(_Clean):
    def test_no_record_returns_negative(self):
        self.assertLess(bot_core._decider_last_reply_ago(GID), 0)

    def test_reports_seconds(self):
        bot_core.GROUP_LAST_BOT_MSG[GID] = time.time() - 42
        v = bot_core._decider_last_reply_ago(GID)
        self.assertTrue(40 <= v <= 45, v)

    def test_zero_timestamp_treated_as_no_record(self):
        """0 是"没发过"的哨兵，不能被当成 1970 年 → 报一个巨大的数。"""
        bot_core.GROUP_LAST_BOT_MSG[GID] = 0
        self.assertLess(bot_core._decider_last_reply_ago(GID), 0)


class TestBotShare(_Clean):
    def test_insufficient_sample(self):
        """少于 3 条 → -1（不拿两条消息算占比，那是噪声）。"""
        now = time.time()
        bot_core.GROUP_RECENT_MSGS[GID] = [(now - 5, "111", "在吗", None, [])]
        bot_core.GROUP_BOT_REPLIES[GID] = [(now - 3, "嗯")]
        self.assertLess(bot_core._decider_bot_share(GID), 0)

    def test_computes_ratio(self):
        """★ 回归：这个函数曾因解包变量名写错而永远返回 -1。"""
        now = time.time()
        bot_core.GROUP_RECENT_MSGS[GID] = [
            (now - 30, "111", "在吗", None, []),
            (now - 25, "222", "吃了吗", None, []),
            (now - 20, "111", "没", None, []),
        ]
        bot_core.GROUP_BOT_REPLIES[GID] = [(now - 15, "刚在刷图"), (now - 10, "没吃")]
        # 3 人说 + 2 条自己的 → 2/5 = 0.4
        self.assertAlmostEqual(bot_core._decider_bot_share(GID), 0.4, places=2)

    def test_outside_window_excluded(self):
        now = time.time()
        bot_core.GROUP_RECENT_MSGS[GID] = [
            (now - 30, "111", "a", None, []),
            (now - 25, "222", "b", None, []),
            (now - 20, "111", "c", None, []),
        ]
        # 10 分钟前的发言不算进 5 分钟窗口
        bot_core.GROUP_BOT_REPLIES[GID] = [(now - 600, "很久以前")]
        self.assertAlmostEqual(bot_core._decider_bot_share(GID), 0.0, places=2)

    def test_bad_rows_do_not_crash(self):
        bot_core.GROUP_RECENT_MSGS[GID] = [("bad", None, None)]
        bot_core.GROUP_BOT_REPLIES[GID] = [("bad",)]
        self.assertIsInstance(bot_core._decider_bot_share(GID), float)


class TestIdleGap(_Clean):
    def test_no_msgs(self):
        self.assertLess(bot_core._decider_idle_gap(GID), 0)

    def test_uses_last_message(self):
        now = time.time()
        bot_core.GROUP_RECENT_MSGS[GID] = [
            (now - 100, "111", "a", None, []),
            (now - 3, "222", "b", None, []),
        ]
        self.assertTrue(2 <= bot_core._decider_idle_gap(GID) <= 6)


class TestConversationClosed(_Clean):
    def test_hard_closers(self):
        now = time.time()
        for w in ("哦", "好", "行", "嗯", "收到", "知道了", "算了", "没事了"):
            with self.subTest(w=w):
                bot_core.GROUP_RECENT_MSGS[GID] = [
                    (now - 10, "111", "在吗", None, []),
                    (now - 2, "222", w, None, []),
                ]
                self.assertTrue(bot_core._decider_closed(GID), f"{w} 应判为闭合")

    def test_laughter_is_not_closure(self):
        """「哈哈」「6」是笑，不是结束 —— 判成闭合会让它错过接梗机会。"""
        now = time.time()
        for w in ("哈哈", "6", "笑死", "草"):
            with self.subTest(w=w):
                bot_core.GROUP_RECENT_MSGS[GID] = [
                    (now - 10, "111", "他说他会飞", None, []),
                    (now - 2, "222", w, None, []),
                ]
                self.assertFalse(bot_core._decider_closed(GID), f"{w} 不该判闭合")

    def test_insufficient_data_returns_none(self):
        """一条消息判断不了"对话停在哪"，返回 None 让决策器自己看文本。"""
        now = time.time()
        bot_core.GROUP_RECENT_MSGS[GID] = [(now - 2, "222", "哦", None, [])]
        self.assertIsNone(bot_core._decider_closed(GID))

    def test_trailing_punct_tolerated(self):
        now = time.time()
        bot_core.GROUP_RECENT_MSGS[GID] = [
            (now - 10, "111", "a", None, []),
            (now - 2, "222", "好吧。", None, []),
        ]
        self.assertTrue(bot_core._decider_closed(GID))


class TestBatchCount(unittest.TestCase):
    """batch_msg_count = merged_n + 1（历史条数 + 当前这条）。

    ★ 回归：第一版写成 merged_n - 1，语义反了 —— 会告诉决策器"这批 0 条"。
    这个断言直接打在源码上，因为它的计算在 process_buffered_message 内联，
    没有可单独调用的函数。
    """

    def test_formula_is_plus_one(self):
        import io
        with io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"batch_msg_count": int(buffer.get("merged_n") or 0) + 1', src,
                      "batch_msg_count 的算法不是 merged_n + 1")


if __name__ == "__main__":
    unittest.main(verbosity=2)