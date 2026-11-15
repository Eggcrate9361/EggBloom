# -*- coding: utf-8 -*-
"""1.5.7 修复的两个缺陷的回归测试。

① 判重误杀：中文短句用「字符集合交集」判相似，导致"你谁啊"被判成和
   "你叫谁妈呢"重复 → 触发重新生成，再命中就整条丢弃（该说话时沉默）。
② 配置不生效：桌面端「最大轮数」写 `session_max_rounds`，但主链路只读
   `group_session_max_replies` → 用户改了没反应。

运行：python -m unittest tests.test_bugfixes -v
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_core as bc  # noqa: E402


class TestDedupGuard(unittest.TestCase):
    """判重只在"真的是同一句"时命中，不能因为共用几个汉字就判重。"""

    def setUp(self):
        self.recent = ["你叫谁妈呢", "吃饭了吗", "行吧你赢了"]

    def test_different_short_replies_are_not_duplicates(self):
        for text in ("你谁啊", "妈", "吃了吗", "行吧", "算了不说了", "你可以的"):
            dup, matched = bc.is_similar_to_recent(self.recent, text)
            self.assertFalse(dup, f"「{text}」被误判成与「{matched}」重复")

    def test_single_char_reply_survives(self):
        """单字回复（6/草/妈/？）是最容易被误杀的，必须能发出去。"""
        for ch in ("6", "草", "妈", "？"):
            dup, matched = bc.is_similar_to_recent(["你叫谁妈呢", "草"], ch)
            if ch == "草":
                continue  # 与最近一条完全相同，判重是正确行为
            self.assertFalse(dup, f"单字「{ch}」被误判成与「{matched}」重复")

    def test_true_duplicate_detected(self):
        dup, matched = bc.is_similar_to_recent(self.recent, "你叫谁妈呢")
        self.assertTrue(dup)
        self.assertEqual(matched, "你叫谁妈呢")

    def test_near_duplicate_detected(self):
        """只差一两个字的复读仍应判重（避免原地复读）。"""
        dup, _ = bc.is_similar_to_recent(["吃饭了吗"], "吃饭了吗啊")
        self.assertTrue(dup)

    def test_repeat_chars_exempt(self):
        """连续重复字（老大老大老大）不判重——这是原文刻意保留的豁免。"""
        dup, _ = bc.is_similar_to_recent(["哈哈哈哈"], "哈哈哈哈哈")
        self.assertFalse(dup)

    def test_empty_inputs(self):
        self.assertEqual(bc.is_similar_to_recent([], "x"), (False, ""))
        self.assertEqual(bc.is_similar_to_recent(["x"], ""), (False, ""))
        self.assertEqual(bc.is_similar_to_recent(None, "x"), (False, ""))

    def test_long_paraphrase_not_duplicate(self):
        dup, _ = bc.is_similar_to_recent(["我不是你妈你别乱叫"], "你叫谁妈呢")
        self.assertFalse(dup)


class TestSessionRoundBudget(unittest.TestCase):
    """UI 的「最大轮数」必须真的生效。"""

    def setUp(self):
        self._old = bc._config
        bc._config = dict(bc._config)

    def tearDown(self):
        bc._config = self._old

    def test_explicit_group_key_wins(self):
        bc._config["group_session_max_replies"] = 5
        bc._config["session_max_rounds"] = 12
        self.assertEqual(bc._session_round_budget(), 5)

    def test_falls_back_to_ui_value(self):
        bc._config.pop("group_session_max_replies", None)
        bc._config["session_max_rounds"] = 12
        self.assertEqual(bc._session_round_budget(), 12,
                         "UI 的 session_max_rounds 仍未接线")

    def test_invalid_value_uses_safe_default(self):
        bc._config["group_session_max_replies"] = "不是数字"
        self.assertEqual(bc._session_round_budget(), 20)

    def test_zero_treated_as_unset(self):
        bc._config["group_session_max_replies"] = 0
        bc._config["session_max_rounds"] = 9
        self.assertEqual(bc._session_round_budget(), 9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
