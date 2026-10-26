# -*- coding: utf-8 -*-
"""1.5.7「像人」改动的验证：自我记忆 / 当前局面 / 决策结论落地 / 账本角色化。

背景（2026-10-21）：
  三个结构缺陷导致「接不上玩笑、自己的话上下矛盾」：
    ① 决策结论（氛围/策略）在无触发分支被丢弃 → 主模型裸奔重猜；
    ② 摘要账本把 assistant 行压成匿名 30 字 → 超过最近 4 轮就认不出自己说的话；
    ③ 只给消息流水、不给局面 → 不知道谁在跟自己说、说到第几轮、上轮接没接。

运行：python -m unittest tests.test_humanness -v
"""
import os
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_core as bc  # noqa: E402


class TestSelfLines(unittest.TestCase):
    def setUp(self):
        bc.group_chat_history["g_self"] = [
            {"role": "user", "content": "[小明(10001)] 你好"},
            {"role": "assistant", "content": "嗯"},
            {"role": "user", "content": "[小明(10001)] 叫妈妈"},
            {"role": "assistant", "content": "你叫谁妈呢"},
            {"role": "user", "content": "[小红(10002)] 哈哈"},
            {"role": "assistant", "content": "笑啥"},
        ]

    tearDown = lambda self: bc.group_chat_history.pop("g_self", None)

    def test_only_assistant_lines_with_prefix(self):
        blk = bc._self_lines_block("g_self")
        self.assertIn("我: 你叫谁妈呢", blk)
        self.assertIn("我: 笑啥", blk)
        self.assertNotIn("小明", blk, "只应包含自己说过的话")

    def test_chronological_order(self):
        blk = bc._self_lines_block("g_self")
        self.assertLess(blk.index("嗯"), blk.index("笑啥"), "应按时间正序")

    def test_limit(self):
        blk = bc._self_lines_block("g_self", limit=1)
        self.assertIn("笑啥", blk)
        self.assertNotIn("你叫谁妈呢", blk)

    def test_empty_safe(self):
        self.assertEqual(bc._self_lines_block("不存在"), "")


class TestSituation(unittest.TestCase):
    def setUp(self):
        now = time.time()
        bc.GROUP_TIMELINE["g_sit"] = [
            (now - 60, "10001", "小明", "在吗"),
            (now - 50, "10001", "小明", "妈妈"),
            (now - 40, "10001", "小明", "叫你呢"),
        ]
        bc.GROUP_BOT_REPLIES["g_sit"] = [(now - 30, "吃饭了吗")]

    def tearDown(self):
        bc.GROUP_TIMELINE.pop("g_sit", None)
        bc.GROUP_BOT_REPLIES.pop("g_sit", None)

    def test_contains_structure_facts(self):
        blk = bc._situation_block("g_sit")
        self.assertIn("小明", blk)
        self.assertIn("连续 3 条", blk, "应统计连续发言")
        self.assertIn("上一句原文：叫你呢", blk)
        self.assertIn("吃饭了吗", blk, "应带上自己上一轮的反应")

    def test_marks_not_yet_replied_when_bot_silent(self):
        """这轮开始后 Bot 没说过话 → 必须如实写「还没接话」。"""
        now = time.time()
        bc.GROUP_TIMELINE["g_sit"] = [(now - 10, "10001", "小明", "妈妈")]
        bc.GROUP_BOT_REPLIES["g_sit"] = [(now - 600, "很久以前的话")]
        blk = bc._situation_block("g_sit")
        self.assertIn("这轮你还没接话", blk)

    def test_marks_already_replied(self):
        now = time.time()
        bc.GROUP_TIMELINE["g_sit"] = [(now - 10, "10001", "小明", "妈妈")]
        bc.GROUP_BOT_REPLIES["g_sit"] = [(now - 5, "你叫谁妈呢")]
        blk = bc._situation_block("g_sit")
        self.assertIn("这轮你已经接过一句", blk)

    def test_empty_safe(self):
        self.assertEqual(bc._situation_block("不存在"), "")

    def test_before_excludes_later_messages(self):
        """批次定稿后到的消息不能算进本批的「刚说话的人」。"""
        now = time.time()
        bc.GROUP_TIMELINE["g_sit"] = [
            (now - 60, "10001", "小明", "现在呢"),
            (now - 5, "10002", "小红", "下一批的话"),
        ]
        blk = bc._situation_block("g_sit", before=now - 30)
        self.assertIn("现在呢", blk)
        self.assertNotIn("下一批的话", blk)
        self.assertNotIn("小红", blk)

    def test_before_excludes_later_bot_reply(self):
        """Bot 在本批之后发的回复，不能让本批显示成「已经接过一句」。"""
        now = time.time()
        bc.GROUP_TIMELINE["g_sit"] = [(now - 10, "10001", "小明", "妈妈")]
        bc.GROUP_BOT_REPLIES["g_sit"] = [(now - 1, "下一批才该出现的回复")]
        blk = bc._situation_block("g_sit", before=now - 5)
        self.assertIn("这轮你还没接话", blk)
        self.assertNotIn("下一批才该出现的回复", blk)


class TestGroupContextSnapshot(unittest.TestCase):
    """【群聊近况】必须按批次截止时间取快照。

    线上 20:19:55 的实证：戳批次注入了 26 条近况，其中含 20:19:28 的
    「现在呢」—— 模型先答了它；这条消息自己又成一批，20:20:11 再答一遍。
    """

    def setUp(self):
        self.now = time.time()
        bc.GROUP_TIMELINE["g_snap"] = [
            (self.now - 60, "10001", "小明", "旧话题"),
            (self.now - 5, "10002", "小红", "下一批的消息"),
        ]
        bc.GROUP_BOT_REPLIES["g_snap"] = [
            (self.now - 2, "下一批的回复"),
        ]

    def tearDown(self):
        bc.GROUP_TIMELINE.pop("g_snap", None)
        bc.GROUP_BOT_REPLIES.pop("g_snap", None)

    def test_before_filters_timeline_and_bot_replies(self):
        blk = bc._build_group_context_block("g_snap", "999", before=self.now - 30)
        self.assertIn("旧话题", blk)
        self.assertNotIn("下一批的消息", blk)
        self.assertNotIn("下一批的回复", blk)

    def test_without_before_keeps_all(self):
        blk = bc._build_group_context_block("g_snap", "999")
        self.assertIn("旧话题", blk)
        self.assertIn("下一批的消息", blk)
        self.assertIn("下一批的回复", blk)


class TestLedgerRole(unittest.TestCase):
    """账本必须能区分'我说的'与'别人说的'，且自己那句不能被砍到 30 字。"""

    def setUp(self):
        long_reply = "我不是你妈你别乱叫" + "啊" * 40   # 50 字
        hist = []
        for i in range(4):
            hist.append({"role": "user", "content": f"[小明(10001)] 第{i}句"})
            hist.append({"role": "assistant", "content": long_reply if i == 0 else f"回{i}"})
        # 再补 4 轮，让最早的 8 条全部落入账本区
        for i in range(4):
            hist.append({"role": "user", "content": f"[小明(10001)] 后{i}"})
            hist.append({"role": "assistant", "content": f"答{i}"})
        self.hist = hist
        self.long_reply = long_reply
        self.key = "g_ledger_test"

    def tearDown(self):
        for d in (bc._HISTORY_SUMMARY_LINES, bc._HISTORY_SUMMARY_TEXT, bc._HISTORY_LAST_WINDOW):
            d.pop(self.key, None)

    def test_ledger_marks_own_lines(self):
        msgs = bc._build_cached_messages(self.hist, self.key, "SYS", [], "在吗")
        ledger = next((m["content"] for m in msgs
                       if m["role"] == "system" and "【之前对话摘要】" in m["content"]), "")
        self.assertTrue(ledger, "未生成摘要账本")
        self.assertIn("我: ", ledger, "assistant 行未标记，模型认不出自己说的话")
        self.assertIn("[小明(10001)]", ledger, "user 行的说话人前缀被破坏")

    def test_own_line_not_truncated_to_30(self):
        msgs = bc._build_cached_messages(self.hist, self.key, "SYS", [], "在吗")
        ledger = next((m["content"] for m in msgs
                       if m["role"] == "system" and "【之前对话摘要】" in m["content"]), "")
        self.assertIn(self.long_reply[:40], ledger,
                      "自己那句话被砍到 30 字，立场句丢失")


class TestRecentTurns(unittest.TestCase):
    """RECENT_TURNS=4 必须真的是 4 轮（8 条），不是 4 条（2 轮）。

    2026-10-21 修：原实现直接拿 4 去切 messages 列表，而列表是一问一答交替的
    → 实际只有 2 轮原文，其余全被压成摘要行。
    """

    def setUp(self):
        self.key = "g_recent_test"
        hist = []
        for i in range(10):
            hist.append({"role": "user", "content": f"[小明(10001)] 问{i}"})
            hist.append({"role": "assistant", "content": f"答{i}"})
        self.hist = hist[-16:]
        for d in (bc._HISTORY_SUMMARY_LINES, bc._HISTORY_SUMMARY_TEXT,
                  bc._HISTORY_LAST_WINDOW):
            d.pop(self.key, None)

    tearDown = lambda self: [d.pop(self.key, None) for d in
                             (bc._HISTORY_SUMMARY_LINES, bc._HISTORY_SUMMARY_TEXT,
                              bc._HISTORY_LAST_WINDOW)]

    def test_eight_entries_keep_verbatim(self):
        msgs = bc._build_cached_messages(self.hist, self.key, "SYS", [], "现在")
        contents = [m["content"] for m in msgs if m["role"] != "system"]
        self.assertEqual(contents[:-1], [h["content"] for h in self.hist[-8:]],
                         "最近 4 轮（8 条）没有全部以原文出现")

    def test_older_entries_go_to_ledger(self):
        msgs = bc._build_cached_messages(self.hist, self.key, "SYS", [], "现在")
        ledger = next((m["content"] for m in msgs
                       if m["role"] == "system" and "【之前对话摘要】" in m["content"]), "")
        self.assertIn("问2", ledger, "更早的消息应进摘要账本")

    def test_assistant_ledger_line_keeps_more_than_30(self):
        long_reply = "我不是你妈你别乱叫" + "啊" * 50
        hist = [{"role": "user", "content": "[小明(10001)] 问"},
                {"role": "assistant", "content": long_reply}] * 6
        msgs = bc._build_cached_messages(hist, self.key, "SYS", [], "现在")
        ledger = next((m["content"] for m in msgs
                       if m["role"] == "system" and "【之前对话摘要】" in m["content"]), "")
        self.assertIn(long_reply[:90], ledger, "自己的话在账本里被砍太短")


class TestWiring(unittest.TestCase):
    def test_decider_content_not_dropped(self):
        with open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        anchor = src.find('if _pn == "group_chat_decider"')
        self.assertGreater(anchor, 0)
        seg = src[anchor:anchor + 500]
        self.assertIn('_pr.get("content")', seg, "决策 content 仍被丢弃")

    def test_blocks_injected_before_call(self):
        with open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        call = src.find("reply = call_deepseek(merged_text_for_ai")
        self.assertGreater(call, 0)
        checks = (
            "_self_blk = _self_lines_block",
            "_sit_blk = _situation_block",
            "_bg_parts.append(_decider_ctx_text)",
            "【现场资料·不是对方说的话】",
            "scene_context: str = ''",
            '"\\n\\n".join(_bg_parts)',
        )
        for needle in checks:
            idx = src.find(needle)
            self.assertGreater(idx, 0, f"缺少注入语句：{needle}")
            self.assertLess(idx, call, f"注入在模型调用之后，不会生效：{needle}")

    def test_scene_context_goes_to_system_message(self):
        with open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        anchor = src.find("if scene_context:")
        self.assertGreater(anchor, 0, "缺少 scene_context 注入")
        seg = src[anchor:anchor + 500]
        self.assertIn("dynamic_parts.append", seg,
                      "现场资料没有进 system 消息")
        self.assertNotIn("merged_text_for_ai", seg,
                         "现场资料又被拼回用户消息")

    def test_permission_present(self):
        blk = bc._seg_group_rules(True)
        self.assertIn("说话许可", blk)
        self.assertIn("可以不给答案", blk)


class TestSendSplitting(unittest.TestCase):
    """1.5.7 分条发送语义：分行优先 → 100 字总闸 → 标点拆句 → 5 条上限。"""

    def setUp(self):
        self.sent = []
        self._api, self._rem, self._sleep = bc.api_call, bc._remember_own_group_msg, time.sleep
        bc.api_call = lambda a, p=None: (self.sent.append(p.get("message")),
                                         {"retcode": 0, "data": {"message_id": 1}})[1]
        bc._remember_own_group_msg = lambda *a, **k: None
        time.sleep = lambda x: None

    def tearDown(self):
        bc.api_call, bc._remember_own_group_msg = self._api, self._rem
        time.sleep = self._sleep

    def test_lines_split_first(self):
        """模型自己分了行 → 按行发，即使总字数超 100 也不合并。"""
        bc.smart_send_group(1, "第一句\n第二句\n第三句")
        self.assertEqual(len(self.sent), 3)

    def test_under_threshold_splits_by_punct(self):
        bc.smart_send_group(1, "输麻了。这游戏太抽象。不玩了。")
        self.assertEqual(len(self.sent), 3)

    def test_over_threshold_sends_block(self):
        """> 100 有效字 → 整块一条（拆开就是刷屏）。"""
        bc.smart_send_group(1, "我今天真的输麻了这游戏太抽象了。" * 8)
        self.assertEqual(len(self.sent), 1, "超 100 字应整块发送")

    def test_max_parts_caps_lines(self):
        """12 行不得逐行发成 12 条 —— 尾部合并到上限。"""
        bc.smart_send_group(1, "\n".join(["草"] * 12))
        self.assertEqual(len(self.sent), bc.GROUP_SEND_MAX_PARTS)

    def test_max_parts_caps_punct(self):
        bc.smart_send_group(1, "这句。" * 16)
        self.assertEqual(len(self.sent), bc.GROUP_SEND_MAX_PARTS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
