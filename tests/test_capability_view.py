# -*- coding: utf-8 -*-
"""
能力视图（【我现在的处境】）测试。

背景（2026-10-22 用户实测四条）：
  · 「不知道自己的群聊工具链」
  · 「分不清白名单」
  · 「不知道白名单上的管理员有谁」
  · 「不知道如果不是管理员怎么回复工具使用」
根因是同一个：**运行时真实状态从未进过 prompt**。模型手上只有按需裁剪的
工具 schema，没有"我能用什么 / 谁有权让我用 / 我该怎么回"的整体视图。

本测试锁住：注入存在、内容正确、缺失时不瞎编、不泄漏 QQ 号。
"""
import io
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_core  # noqa: E402


class TestCapabilityBlock(unittest.TestCase):
    def setUp(self):
        self._orig = dict(bot_core._config)
        self._orig_bot_qq = getattr(bot_core, "BOT_QQ", "")
        self._orig_nick = bot_core.resolve_qq_nick
        bot_core.resolve_qq_nick = lambda q: {"10001": "主人甲",
                                              "20002": "群友乙"}.get(str(q), "")
        bot_core.BOT_QQ = "99999"
        bot_core._config["target_qqs"] = ["10001"]
        bot_core._config["command_admins"] = []
        bot_core._config["command_permission"] = "strict"
        bot_core._config["group_tool_allowlist"] = ["search_web", "take_screenshot"]

    def tearDown(self):
        bot_core._config.clear()
        bot_core._config.update(self._orig)
        bot_core.BOT_QQ = self._orig_bot_qq
        bot_core.resolve_qq_nick = self._orig_nick

    def _blk(self, **kw):
        kw.setdefault("is_group", True)
        kw.setdefault("tool_names", ("search_web", "take_screenshot", "web_operate"))
        return bot_core._capability_block(**kw)

    # ── 1. 工具链 ──
    def test_lists_actual_tool_names(self):
        b = self._blk(is_group=True, sender="10001")
        self.assertIn("【我现在的处境】", b)
        self.assertIn("search_web", b)
        self.assertIn("take_screenshot", b)
        self.assertIn("联网搜索", b)          # 中文解释也要在，模型才知道是干什么的

    def test_says_cannot_when_not_listed(self):
        b = self._blk(is_group=True, sender="10001")
        self.assertIn("干不了", b, "必须明确'名单里没有的就说干不了'，否则模型会编")

    def test_empty_toolset_no_crash(self):
        b = bot_core._capability_block(is_group=True, group_id="1", sender="10001",
                                       tool_names=())
        self.assertNotIn("**真有**", b)

    # ── 2. 白名单（群里 vs 私聊）──
    def test_group_allowlist_explained(self):
        b = self._blk(is_group=True, sender="10001")
        self.assertIn("白名单", b)
        self.assertIn("只在群里", b)
        self.assertIn("私聊", b)

    def test_private_has_no_allowlist_note(self):
        b = self._blk(is_group=False, sender="10001")
        self.assertNotIn("只在群里", b)

    def test_empty_allowlist_means_unrestricted(self):
        bot_core._config["group_tool_allowlist"] = []
        b = self._blk(is_group=True, sender="10001")
        self.assertIn("同一套工具", b)

    # ── 3. 管理员 ──
    def test_admin_names_present(self):
        b = self._blk(is_group=True, sender="20002")
        self.assertIn("主人甲", b)

    def test_no_qq_numbers_leaked(self):
        b = self._blk(is_group=True, sender="10001")
        self.assertIsNone(re.search(r"\b10001\b|\b99999\b", b),
                          f"prompt 里不许出现 QQ 号：{b}")

    def test_unresolved_admin_nick_does_not_leak_number(self):
        """昵称解析失败时**不许**回退成 QQ 号。

        这是最容易漏的一条：resolve_qq_nick 是异步的，冷启动第一次调用一定
        返回 None。老实现是 `_nm or _q`，于是 prompt 里直接写进了裸号码，
        而群聊规则明令"不许出现 QQ 号" —— 模型可能把号码复述到群里。
        """
        bot_core.resolve_qq_nick = lambda q: ""       # 全部解析失败
        bot_core._config["target_qqs"] = ["2872362241", "3150007406"]
        b = self._blk(is_group=True, sender="20002")
        self.assertIsNone(re.search(r"2872362241|3150007406", b),
                          f"解析失败也不许泄漏 QQ 号：{b}")
        self.assertIn("主人", b, "仍要告诉模型'有主人这回事'，只是不给号码")

    def test_partial_admin_nick_resolution(self):
        """部分解析成功：已解析的给昵称，未解析的不给号码。"""
        bot_core.resolve_qq_nick = lambda q: {"10001": "主人甲"}.get(str(q), "")
        bot_core._config["target_qqs"] = ["10001", "3150007406"]
        b = self._blk(is_group=True, sender="20002")
        self.assertIn("主人甲", b)
        self.assertNotIn("3150007406", b)

    def test_sender_is_admin_branch(self):
        b = self._blk(is_group=True, sender="10001")
        self.assertIn("是主人", b)
        self.assertNotIn("不是**主人", b)

    def test_sender_not_admin_branch(self):
        b = self._blk(is_group=True, sender="20002")
        self.assertIn("不是**主人", b)
        # 关键：要给出"没权限时怎么说"，而不是只标一句"无权限"
        self.assertTrue("这我不能替你办" in b or "使唤我" in b)
        self.assertIn("别答应下来再装失败", b)

    def test_no_admin_configured(self):
        bot_core._config["target_qqs"] = []
        b = self._blk(is_group=True, sender="20002")
        self.assertIn("没配管理员", b)

    def test_bot_qq_not_listed_as_admin(self):
        bot_core._config["target_qqs"] = ["99999", "10001"]
        b = self._blk(is_group=True, sender="20002")
        self.assertIn("主人甲", b)
        self.assertNotIn("99999", b)


class TestCapabilityWiring(unittest.TestCase):
    """接线：块必须真的进 stable_parts，且工具集必须**先**算出来。"""

    def test_capability_block_called_before_messages_built(self):
        with io.open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        seg = src[src.find("def _call_deepseek_sync"):]
        seg = seg[:seg.find("if _web_hint:")]
        i_tools = seg.find("_offered_tool_names = {t[")
        i_cap = seg.find("_capability_block(")
        i_msg = seg.find("messages = _build_cached_messages")
        self.assertGreater(i_tools, 0, "没找到工具集解析")
        self.assertGreater(i_cap, 0, "_capability_block 没被调用")
        self.assertGreater(i_msg, 0, "没找到 messages 构建")
        self.assertLess(i_tools, i_cap,
                        "工具集必须在能力块之前算出来（否则块里没有工具名）")
        self.assertLess(i_cap, i_msg,
                        "能力块必须写进 stable_parts 再构建 messages")
        self.assertIn("stable_parts.append(_cap_block)", seg)


if __name__ == "__main__":
    unittest.main(verbosity=2)