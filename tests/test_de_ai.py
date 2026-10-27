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


class TestQuestionMarksCoverage(unittest.TestCase):
    """问号补全的第二轮：补齐常见句式，同时收回「呢/啊」结尾的误判。

    第一版只覆盖「吗/么/啥/谁/咋/什么」这类**整词结尾**，实测 52 条常见问句
    只补上 30 条。用户反馈「私聊问句没问号」，逐条分类后发现两类问题：
      · 漏：`吃了没`（没结尾）、`行不`（不结尾）、`谁说的`（疑问词在句首但行尾是普通字）
      · 误：`嘴占着呢` / `我连你啥人都没摸清呢` 被硬加问号 —— 加了反而读不通

    误补比漏补更糟：漏补只是少一个标点，误补是**把陈述改成疑问**，语义都变了。
    所以这一版的原则是「先把误补收干净，再按句式收紧地补」。
    """

    def setUp(self):
        self.f = de_ai._add_missing_question_marks

    # ── 1. 「没 / 不」结尾的正反问 ──
    def test_mei_and_bu_tails(self):
        """「吃了没 / 行不」是正反问，中文里极常见，第一版全漏。"""
        add = ["吃了没", "睡了没", "有事没", "好了没", "到了没", "写完没",
               "行不", "来不", "去不", "成不"]
        for s in add:
            with self.subTest(s=s):
                self.assertEqual(self.f(s), s + "？")

    # ── 2. 疑问词在句首 / 句中 ──
    def test_question_word_leading(self):
        """「谁说的 / 哪去了 / 你怎么知道」—— 疑问词不在行尾但是问句。"""
        add = ["谁说的", "谁告诉你的", "哪来的", "哪去了", "你哪去了",
               "怎么搞的", "啥时候走", "你怎么知道", "这是谁干的",
               "你为啥不早说"]
        for s in add:
            with self.subTest(s=s):
                self.assertEqual(self.f(s), s + "？")

    # ── 3. 误补必须收干净（比漏补更严重）──
    def test_ne_statements_not_questioned(self):
        """「呢」结尾的陈述句不许加问号 —— 第一版有 9/12 被误补。

        这些都是实测里模型真实说过的句子，加了问号就读不通：
        「嘴占着呢？」「我连你啥人都没摸清呢？」
        """
        keep = ["嘴占着呢", "我连你啥人都没摸清呢", "正琢磨中午吃啥呢",
                "我知道呢", "他说呢", "看呢", "在路上呢", "还早呢",
                "在呢", "忙着呢"]
        for s in keep:
            with self.subTest(s=s):
                self.assertEqual(self.f(s), s, f"陈述句被误加了问号：{s}")

    def test_a_statements_not_questioned(self):
        """「啊」结尾的陈述句也不加：`没忙啊` `刚在吃饭啊` 是回答不是提问。"""
        keep = ["没忙啊", "没事啊", "刚在吃饭啊", "不知道啊", "没看见啊",
                "我也无聊啊", "算了吧", "好吧"]
        for s in keep:
            with self.subTest(s=s):
                self.assertEqual(self.f(s), s)

    # ── 4. 原有行为不能被改坏 ──
    def test_first_round_still_passes(self):
        """第一版锁住的用例必须继续成立（这次是收窄，不是推翻）。"""
        add = {"啥啊": "啥啊？", "图呢": "图呢？", "饭呢": "饭呢？",
               "谁": "谁？", "你在干啥": "你在干啥？", "你咋了": "你咋了？"}
        for src, want in add.items():
            with self.subTest(src=src):
                self.assertEqual(self.f(src), want)

    def test_x_shenme_x_is_negation_not_question(self):
        """「X什么X」是否定祈使，不是疑问（2026-10-22 发现的第三类误补）。

        真机句子 `睡什么睡，下午一点四十` 被 `_QM_ANYWHERE` 的「什么」误命中，
        补成了问号 —— 但它的意思是"别睡了"，是祈使不是发问。
        同类：`看什么看` `哭什么哭` `急什么急` `笑什么笑`。
        """
        keep = ["睡什么睡", "看什么看", "哭什么哭", "急什么急", "笑什么笑",
                "睡什么睡，下午一点四十", "这有什么好笑的",
                "没什么好说的"]
        for s in keep:
            with self.subTest(s=s):
                self.assertEqual(self.f(s), s)


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


class TestHonestPhrases(unittest.TestCase):
    """1.5.9 §A.3⑤：诚实短句不许被后处理剪掉。

    现象：模型说「这个我无法完成」，ban_sycophancy 命中 `replace(phrase, "")`
    → 输出「这个我」。残句比套话更假，而且等于惩罚诚实。
    这三句与「作为AI / 希望对你有所帮助」性质不同：前者是必要表达，后者是客服腔。
    """

    def _pp(self, text):
        cfg = {"de_ai": {"enabled": True, "post_process": True,
                         "ban_sycophancy": True}}
        return de_ai.de_ai_post_process(text, cfg)

    def test_honest_refusals_kept(self):
        for s in ("这个我无法完成", "我无法做到", "这事我帮不了你"):
            with self.subTest(s=s):
                self.assertEqual(self._pp(s), s)

    def test_real_sycophancy_still_removed(self):
        # 不能整句都是套话：删空会触发"回退原文"保护（那是另一条既有行为）
        out = self._pp("作为AI，这事我确实做不到")
        self.assertNotIn("作为AI", out)
        self.assertIn("这事我确实做不到", out)

    def test_diagnose_does_not_flag_honest_refusal(self):
        r = de_ai.de_ai_diagnose("这个我无法完成")
        self.assertFalse(any("我无法完成" in h for h in r["hits"]), r)


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


class TestTimeContradiction(unittest.TestCase):
    """自己回复里的时段词跟当前钟点矛盾 → 删掉（2026-10-22 真机反馈）。

    真机群聊的真实三轮（用户原话："他自己说大清早，后面似乎是没
    识别出自己的消息，反驳自己"）：

      13:37:49  我：睡什么睡 / 现在一点半，你才刚要躺下啊     ← 正确
      13:39:42  我：你他妈才傻逼，**大清早**不干正事跑来找骂   ← 凭空"大清早"
      13:40:14  我：还有，**现在下午一点四十，谁大清早**      ← 公开打自己脸

    注意这不是时间注入失败（13:37 它自己说了"一点半"），是模型骂人时
    顺着语感把"大清早"当语气词写出来，然后又自己发现不对。
    已有 `time_ctx.mismatch_note()` 管"对方说的时段"，这里是它的镜像。
    """

    def _pp(self, text, *, hour=13):
        # 构造一个固定 13:xx 的时间戳（用本地时区当天的 13:30）
        import datetime
        d = datetime.datetime.now().replace(hour=hour, minute=30,
                                            second=0, microsecond=0)
        ts = d.timestamp()
        return de_ai._strip_time_contradictions(text, ts=ts)

    def test_real_world_line(self):
        """真机原句：整句"自我纠正"要删掉。"""
        src = "可爱个屁\n这是骂你，你给我正经挨着\n还有，现在下午一点四十，谁大清早"
        want = "可爱个屁\n这是骂你，你给我正经挨着"
        self.assertEqual(self._pp(src), want)

    def test_wrong_period_word_removed(self):
        """句中错误的时段词只删词不删句 —— 剩下的部分仍然通顺。"""
        out = self._pp("你他妈才傻逼，大清早不干正事跑来找骂")
        self.assertNotIn("大清早", out)
        self.assertIn("傻逼", out)          # 骂人的部分要留住
        self.assertIn("不干正事", out)

    def test_correct_period_kept(self):
        """正确的时间感一个字都不能动（13:37 那句"现在一点半"）。"""
        keep = [
            "现在一点半，你才刚要躺下啊",
            "睡什么睡，下午一点四十",       # 「下午」在 13 点是合法的
            "中午吃了吗",
        ]
        for s in keep:
            with self.subTest(s=s):
                self.assertEqual(self._pp(s), s)

    def test_night_words_allowed_at_night(self):
        """小时数不同，判定也就不同 —— 半夜说"大晚上"是对的。"""
        self.assertEqual(self._pp("大晚上不睡觉", hour=23), "大晚上不睡觉")
        self.assertNotIn("大晚上", self._pp("大晚上不睡觉", hour=13))

    def test_all_removed_falls_back_to_original(self):
        """全都删空 → 回退原文，绝不发空消息。"""
        src = "大清早"
        self.assertEqual(self._pp(src), src)

    def test_no_period_word_untouched(self):
        for s in ["你才是乐子", "图呢？", ""]:
            with self.subTest(s=s):
                self.assertEqual(self._pp(s), s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
