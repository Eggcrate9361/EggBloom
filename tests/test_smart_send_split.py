# -*- coding: utf-8 -*-
"""smart_send 的切句逻辑：标点必须保留（2026-10-22 真机反馈）。

用户报「私聊还是没有问号」。查下来 de_ai._add_missing_question_marks 明明补上了，
但 `smart_send` 用 `re.split(r'[。！？!?\\n]+', text)` 切句 —— 标点当分隔符，
split 之后**丢失且不保留**。多句发送时每句都不带标点：

    你吃了吗？\\n我很好   →   旧: ['你吃了吗', '我很好']     问号没了
                             新: ['你吃了吗？', '我很好']    对

单句走 send_message(text) 发原文所以看不出问题，只有多句才暴露。
这个文件锁住"切句不丢标点"，以及切句本身的行为不变（条数、顺序）。
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def split_keep(text):
    """与 bot_core.smart_send 同款的切句逻辑（抽出来便于单测）。"""
    parts = re.split(r'([。！？!?\n]+)', text)
    out, buf = [], ""
    for p in parts:
        if p and re.fullmatch(r'[。！？!?\n]+', p):
            buf += p                       # 标点/换行归前一句
        else:
            if buf.strip():
                out.append(buf.strip())
            buf = p or ""
    if buf.strip():
        out.append(buf.strip())
    return out


class TestSmartSendSplit(unittest.TestCase):
    def test_punctuation_preserved(self):
        """问号/句号/感叹号不能在被切掉（本次修复的核心）。"""
        self.assertEqual(split_keep("你吃了吗？\n我很好"),
                         ["你吃了吗？", "我很好"])
        self.assertEqual(split_keep("午觉？"), ["午觉？"])
        self.assertEqual(split_keep("第一句。第二句！第三句？"),
                         ["第一句。", "第二句！", "第三句？"])

    def test_question_mark_survives_de_ai_roundtrip(self):
        """模拟完整链路：de_ai 补问号 → smart_send 切句 → 问号还在。"""
        import de_ai
        raw = "图呢\n饭呢\n吃了没"
        fixed = de_ai._add_missing_question_marks(raw)
        self.assertEqual(fixed, "图呢？\n饭呢？\n吃了没？")
        self.assertEqual(split_keep(fixed),
                         ["图呢？", "饭呢？", "吃了没？"])

    def test_split_behaviour_unchanged(self):
        """条数、顺序、内容不能变 —— 只修标点，不动结构。"""
        self.assertEqual(split_keep("睡什么睡\n现在一点半\n午觉？"),
                         ["睡什么睡", "现在一点半", "午觉？"])
        self.assertEqual(split_keep("单句不带标点"), ["单句不带标点"])
        self.assertEqual(split_keep("操，你才是乐子\n我乐你妈呢"),
                         ["操，你才是乐子", "我乐你妈呢"])

    def test_empty_and_whitespace(self):
        self.assertEqual(split_keep(""), [])
        self.assertEqual(split_keep("\n\n"), [])
        self.assertEqual(split_keep("   \n  "), [])

    def test_repeated_punctuation_collapsed_into_one_sentence(self):
        """「。。」这种重复标点仍算一句的分隔，不多切。"""
        self.assertEqual(split_keep("真的假的。。\n走吧"), ["真的假的。。", "走吧"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
