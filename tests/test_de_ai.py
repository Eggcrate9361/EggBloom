# -*- coding: utf-8 -*-
"""de_ai 后处理自检 —— 重点盯「问号补全」的正反例。

为什么单独立一个文件（2026-10-22）：
  人格要求短句、可以只有两个字，模型于是把标点全省 ——
  实测「啥啊 / 图呢 / 就一句离谱我咋看」三句都是问句却一个问号都没有，
  读起来不知道是陈述还是发问。提示词里写了「问句带问号」也不稳定
  （5 个场景只出 3 个），所以补了规则层的 `_add_missing_question_marks` 兜底。
  这个文件锁住它的边界：该补的补、不该补的一个别动。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import de_ai  # noqa: E402


class TestQuestionMarks(unittest.TestCase):
    def setUp(self):
        self.f = de_ai._add_missing_question_marks

    # ── 该补的 ────────────────────────────────────────────────────
    def test_should_add(self):
        cases = {
            "啥啊": "啥啊？",
            "图呢": "图呢？",
            "饭呢": "饭呢？",
            "就一句离谱我咋看": "就一句离谱我咋看？",
            "你咋了": "你咋了？",
            "这是不是你干的": "这是不是你干的？",
            "谁": "谁？",
            "你打什么游戏": "你打什么游戏？",
            "你到底来不来": "你到底来不来？",
            "你这有啥好玩的": "你这有啥好玩的？",
            "你在干啥": "你在干啥？",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.f(src), want)

    def test_multiline(self):
        src = "啥啊\n图呢\n就一句离谱我咋看"
        want = "啥啊？\n图呢？\n就一句离谱我咋看？"
        self.assertEqual(self.f(src), want)

    def test_only_last_line_gets_mark(self):
        # 只有疑问词结尾的那行补，其他行原样
        src = "草 我也无聊\n你那有啥好玩的"
        want = "草 我也无聊\n你那有啥好玩的？"
        self.assertEqual(self.f(src), want)

    # ── 不该动的 ─────────────────────────────────────────────────
    def test_already_has_punctuation_untouched(self):
        for s in ["你咋了？", "真的假的！", "行吧。", "等会儿…", "走吧，", "啥啊；"]:
            with self.subTest(s=s):
                self.assertEqual(self.f(s), s)

    def test_statements_with_soft_tails_untouched(self):
        """「呢/吧/啊」结尾的陈述句，猜不准就不动 —— 宁可漏，不要错。"""
        for s in ["我在呢", "等呢", "行吧", "好吧", "算了啊", "我也无聊", "别了吧", "几点了"]:
            with self.subTest(s=s):
                self.assertEqual(self.f(s), s)

    def test_ambiguous_tails_left_alone(self):
        """「还是」在陈述句里太常见，宁可不补。"""
        for s in ["我还是不去了", "还是算了吧", "这玩意电机坏了还是电容坏了"]:
            with self.subTest(s=s):
                self.assertEqual(self.f(s), s)

    def test_negative_usage_untouched(self):
        self.assertEqual(self.f("没什么"), "没什么")
        self.assertEqual(self.f("没啥好看的"), "没啥好看的")

    def test_emoji_marker_line_untouched(self):
        s = "[表情:呆滞]"
        self.assertEqual(self.f(s), s)
        # 表情行混在正常行之间
        src = "啥啊\n[表情:呆滞]\n图呢"
        want = "啥啊？\n[表情:呆滞]\n图呢？"
        self.assertEqual(self.f(src), want)

    def test_empty(self):
        self.assertEqual(self.f(""), "")
        self.assertEqual(self.f("\n\n"), "\n\n")

    def test_trailing_whitespace_trimmed(self):
        self.assertEqual(self.f("图呢  "), "图呢？")


class TestPostProcessIntegration(unittest.TestCase):
    """补问号要接在整条后处理链最后，且不与既有清理打架。"""

    def _pp(self, text):
        cfg = {"de_ai": {"enabled": True, "post_process": True,
                         "fix_question_marks": True}}
        return de_ai.de_ai_post_process(text, cfg)

    def test_full_chain(self):
        # 破折号转逗号 + 补问号，两条都要生效
        src = "你看这个——咋回事"
        out = self._pp(src)
        self.assertNotIn("——", out)
        self.assertTrue(out.rstrip().endswith("？"), out)

    def test_can_be_disabled(self):
        cfg = {"de_ai": {"enabled": True, "post_process": True,
                         "fix_question_marks": False}}
        self.assertEqual(de_ai.de_ai_post_process("图呢", cfg), "图呢")

    def test_off_when_master_switch_off(self):
        cfg = {"de_ai": {"enabled": False, "post_process": False}}
        self.assertEqual(de_ai.de_ai_post_process("图呢", cfg), "图呢")

    def test_repeated_exclamation_still_collapsed(self):
        # 既有行为不能被新规则破坏
        out = self._pp("太离谱了！！！")
        self.assertEqual(out, "太离谱了！")

    def test_default_config_has_the_flag(self):
        self.assertTrue(de_ai.DEFAULT_DE_AI_CONFIG.get("fix_question_marks"))


class TestHalfwidthPunctuation(unittest.TestCase):
    """半角 ? ! → 全角（2026-10-22）。

    实测模型会吐 `晚上好?这才十点四十多吧` —— 半角问号在中文里像英文输入法
    没切过来，本身就是一种"不像人"。
    """

    def _pp(self, text, **over):
        cfg = {"de_ai": {"enabled": True, "post_process": True,
                         "fix_question_marks": True, **over}}
        return de_ai.de_ai_post_process(text, cfg)

    def test_chinese_halfwidth_question_converted(self):
        self.assertEqual(self._pp("晚上好?这才十点吧"), "晚上好？这才十点吧")

    def test_chinese_halfwidth_bang_converted(self):
        self.assertEqual(self._pp("我服了!这叫啥事"), "我服了！这叫啥事")

    def test_pure_english_line_untouched(self):
        # 纯英文/纯符号不该被转 —— 那是英文语境
        self.assertEqual(self._pp("ok?"), "ok?")
        self.assertEqual(self._pp("why!"), "why!")

    def test_double_bang_preserved(self):
        # 「！！」是两个语气符不是残留，只有 3 个以上才压
        self.assertEqual(self._pp("真的假的！！"), "真的假的！！")

    def test_triple_collapsed(self):
        self.assertEqual(self._pp("草！！！！！"), "草！")

    def test_mixed_line_converts_only_punct(self):
        # 含汉字的行整行转，英文单词保留
        self.assertEqual(self._pp("这 code 有问题?"), "这 code 有问题？")


if __name__ == "__main__":
    unittest.main(verbosity=2)
