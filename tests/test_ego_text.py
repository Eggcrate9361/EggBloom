# -*- coding: utf-8 -*-
"""ego 人格链的文本注入（2026-10-23 改）。

这一层的问题都不是报错型失败：注入错了、话题提取错了、示例结构坏了，
模型照样回话，只是回得不对 —— 从回复内容根本看不出来根因在插件里。
所以每条都要有断言。

真实语料基线（运行版 group_chat_decider.db，214 条群消息）：
  · 情绪 neutral 203 / happy 11，模式 normal 210 / playful 4
  · 话题字段 msg[:8] 提取，214 条全部是昵称前缀，无一例外

运行：python -m unittest tests.test_ego_text -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from plugins.ego.personality_engine import (ConversationState, ExampleRetriever,
                                            build_personality_prompt,
                                            _strip_sender_prefix)


class TestStripSenderPrefix(unittest.TestCase):
    """群消息带昵称前缀，不剥掉就会把昵称当成内容。"""

    def test_bracket_prefix(self):
        self.assertEqual(_strip_sender_prefix("[碱式碳酸大脑] 这游戏真好玩"), "这游戏真好玩")

    def test_kaomoji_nick(self):
        self.assertEqual(_strip_sender_prefix("[(/ω＼)] 不告诉你"), "不告诉你")

    def test_colon_prefix(self):
        self.assertEqual(_strip_sender_prefix("外莱塔尼亚：写个代码"), "写个代码")

    def test_plain_untouched(self):
        self.assertEqual(_strip_sender_prefix("普通一句"), "普通一句")

    def test_no_content_left(self):
        """只有一个昵称、没有正文 → 返回空，不要把昵称当话题。"""
        self.assertEqual(_strip_sender_prefix("[某人] "), "")

    def test_none_and_empty(self):
        self.assertEqual(_strip_sender_prefix(None), "")
        self.assertEqual(_strip_sender_prefix(""), "")


class TestExtractTopic(unittest.TestCase):
    def setUp(self):
        self.st = ConversationState("_t_topic")

    def test_topic_is_content_not_nickname(self):
        """核心回归：话题必须来自正文，不能是 `[昵称]`。"""
        t = self.st._extract_topic("[碱式碳酸大脑] 这游戏真好玩啊兄弟们")
        self.assertNotIn("碱式碳酸大脑", t)
        self.assertIn("游戏", t)

    def test_topic_not_default(self):
        self.assertNotEqual(self.st._extract_topic("[谁] 聊点别的"), "default")

    def test_multi_message_takes_first_line(self):
        """merged_text 是**多条消息拼的**，取 12 字会跨行截到下一个昵称。"""
        t = self.st._extract_topic("[蒋锴城] 你在哪\n[蒋锴城] 你认识群主吗")
        self.assertEqual(t, "你在哪")

    def test_skips_media_placeholder(self):
        """`[图片]` 是占位符不是内容，拿来当话题等于没话题。

        注意 `[分享]标题` 不算占位符：它后面带的标题是真实内容，可以当话题。
        被当作前缀剥掉的是 `[分享]` 这个标记本身。
        """
        for m in ("[(/ω＼)] [图片]", "[某人] [语音]", "[某人] [表情]"):
            with self.subTest(m=m):
                self.assertEqual(self.st._extract_topic(m), "",
                                 f"占位符不该当话题: {m}")

    def test_share_card_keeps_title(self):
        """分享卡片剥掉 `[分享]` 标记，标题本身是内容。"""
        self.assertEqual(self.st._extract_topic("[某人] [分享]Assumptions"), "Assumptions")

    def test_placeholder_then_real_content(self):
        t = self.st._extract_topic("[某人] [图片]\n[某人] 这啥啊")
        self.assertEqual(t, "这啥啊")


class TestToPrompt(unittest.TestCase):
    """默认值不写进 prompt：写了就是每轮一句恒定的废话，还会盖掉模型自己的判断。"""

    def setUp(self):
        self.st = ConversationState("_t_prompt")
        # 显式归零，避免读到别的用例落下的状态文件
        self.st.current_topic = None
        self.st.mode = "normal"
        self.st.emotion = "neutral"
        self.st.turn_count = 0

    def test_all_default_is_empty(self):
        self.assertEqual(self.st.to_prompt(), "")

    def test_single_turn_not_mentioned(self):
        self.st.turn_count = 1
        self.assertEqual(self.st.to_prompt(), "", "单独一轮不算连续追问")

    def test_repeated_turn_mentioned(self):
        self.st.turn_count = 3
        self.assertIn("连续追问", self.st.to_prompt())

    def test_deviations_are_reported(self):
        self.st.current_topic = "写代码报错"
        self.st.mode = "serious"
        self.st.emotion = "sad"
        out = self.st.to_prompt()
        self.assertIn("写代码报错", out)
        self.assertIn("serious", out)
        self.assertIn("sad", out)


class TestExampleRetrieval(unittest.TestCase):
    def setUp(self):
        self.r = ExampleRetriever()
        self.st = ConversationState("_t_retr")

    def test_identity_question_hits_identity_scene(self):
        """问身份必须命中身份示例 —— 这是最不能自由发挥的一类。

        「你是真人吗」和示例「你是不是AI啊」只共享「你是」一个二元组，
        字面重合度必然不达标，所以单独做了问法枚举。
        """
        for q in ("你是真人吗", "你是不是AI", "你是GPT吗", "你到底是不是人"):
            with self.subTest(q=q):
                ex = self.r.retrieve(q, self.st, 2)
                self.assertTrue(ex, f"身份问句没命中示例: {q}")
                users = " ".join(str(e.get("user", "")) for e in ex)
                self.assertTrue(("AI" in users) or ("机器人" in users),
                                f"命中的不是身份示例: {users}")

    def test_unrelated_gets_nothing(self):
        """无关联的句子宁可返回空，也不配错误示例。"""
        for q in ("今天天气不错", "你好"):
            with self.subTest(q=q):
                self.assertEqual(self.r.retrieve(q, self.st, 2), [],
                                 f"不该配示例: {q}")

    def test_scene_match_still_works(self):
        self.assertTrue(self.r.retrieve("这游戏真好玩", self.st, 2))
        self.assertTrue(self.r.retrieve("我好累啊", self.st, 2))


class TestPromptAssembly(unittest.TestCase):
    def test_multiline_example_indented(self):
        """多行示例的第二行要缩进，否则编号结构断掉、示例作废。

        群里真人回复几乎都是多行短句，这恰恰是最该示范的形态。
        """
        st = ConversationState("_t_asm")
        st.current_topic = None
        st.mode = "normal"
        st.emotion = "neutral"
        st.turn_count = 0
        ex = [{"user": "问一句", "bot": "第一行\n第二行"}]
        out = build_personality_prompt(st, "", ex, "问一句")
        for line in out.split("\n"):
            if "第二行" in line:
                self.assertTrue(line.startswith(" "),
                                f"多行示例第二行掉顶格了: {line!r}")
                return
        self.fail("没找到第二行")

    def test_empty_everything_returns_empty(self):
        st = ConversationState("_t_asm2")
        st.current_topic = None
        st.mode = "normal"
        st.emotion = "neutral"
        st.turn_count = 0
        self.assertEqual(build_personality_prompt(st, "", [], ""), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)