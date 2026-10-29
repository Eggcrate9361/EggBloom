# -*- coding: utf-8 -*-
"""
bot_core 事件处理器单元测试：request / group_increase / group_decrease / friend_add /
group_ban / poke / recall（撤回反馈）。poke 与 recall 原为主循环内联逻辑，已提取为
_handle_poke_event / _handle_recall_event 以便测试。

运行方式（项目根目录）：
    python -m unittest tests.test_bot_core_events -v
    # 或
    python tests/test_bot_core_events.py

说明：所有外部副作用均被 mock（api_call / send_message / smart_send_group /
_notify_admin / _append_target_and_reload / resolve_qq_nick / log / call_deepseek /
表情包相关），测试不触网、不发真实消息、不调 AI、不写配置、不写日志文件。
"""
import os
import sys
import time
import unittest
from unittest import mock

# 允许以脚本方式直接运行（sys.path 补项目根目录）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_core  # noqa: E402

# 基类会 mock 掉 _request_allowed 以隔离 handler；测试真实判定逻辑时需要还原
_REAL_REQUEST_ALLOWED = bot_core._request_allowed


class _EventHandlersBase(unittest.TestCase):
    """公共 mock 环境：把所有外部副作用替换为可控 Mock。"""

    def setUp(self):
        self.api_call = mock.Mock(return_value={"retcode": 0})
        self.send_message = mock.Mock(return_value=True)
        self.smart_send_group = mock.Mock(return_value=True)
        self.notify_admin = mock.Mock()
        self.append_target = mock.Mock()
        self.resolve_nick = mock.Mock(side_effect=lambda u: f"昵称{u}" if u else "")
        self.log = mock.Mock()
        self.auto_accept = mock.Mock(return_value={})
        self.group_restriction = mock.Mock(return_value=None)
        self.request_allowed = mock.Mock(return_value=(True, "allowed"))

        self._patchers = [
            mock.patch.object(bot_core, "api_call", self.api_call),
            mock.patch.object(bot_core, "send_message", self.send_message),
            mock.patch.object(bot_core, "smart_send_group", self.smart_send_group),
            mock.patch.object(bot_core, "_notify_admin", self.notify_admin),
            mock.patch.object(bot_core, "_append_target_and_reload", self.append_target),
            mock.patch.object(bot_core, "resolve_qq_nick", self.resolve_nick),
            mock.patch.object(bot_core, "log", self.log),
            mock.patch.object(bot_core, "_auto_accept_cfg", self.auto_accept),
            mock.patch.object(bot_core, "_group_restriction", self.group_restriction),
            mock.patch.object(bot_core, "_request_allowed", self.request_allowed),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._stop_patchers)

    def _stop_patchers(self):
        for p in self._patchers:
            p.stop()

    def _set_aa(self, **kw):
        """设置 _auto_accept_cfg 返回值（默认全开）。"""
        cfg = {
            "enabled": True,
            "friend": True,
            "group_add": True,
            "group_invite": True,
            "add_to_targets": True,
            "welcome_new_member": True,
        }
        cfg.update(kw)
        self.auto_accept.return_value = cfg


# ══════════════════════════════════════════════════════════════
# request 事件（_handle_request_event）：好友/加群/入群申请自动同意
# ══════════════════════════════════════════════════════════════

class TestRequestEvent(_EventHandlersBase):
    def test_auto_accept_disabled_skips(self):
        self._set_aa(enabled=False)
        bot_core._handle_request_event({"post_type": "request", "request_type": "friend", "user_id": "111"})
        self.api_call.assert_not_called()
        self.send_message.assert_not_called()
        self.append_target.assert_not_called()

    def test_friend_request_allowed(self):
        self._set_aa()
        bot_core._handle_request_event({"post_type": "request", "request_type": "friend",
                                        "user_id": "111", "flag": "F1"})
        self.api_call.assert_called_once()
        args = self.api_call.call_args[0]
        self.assertEqual(args[0], "set_friend_add_request")
        self.assertTrue(args[1]["approve"])
        self.append_target.assert_called_once_with("target_qqs", "111")
        self.send_message.assert_called_once()
        # 2026-10-12：欢迎语不再写死"小a"，取配置里的第一个昵称
        self.assertEqual(self.send_message.call_args[0][0],
                         f"你好，我是{bot_core._bot_nicknames()[0]}～")

    def test_friend_request_denied(self):
        self._set_aa()
        self.request_allowed.return_value = (False, "denied")
        bot_core._handle_request_event({"post_type": "request", "request_type": "friend",
                                        "user_id": "111", "flag": "F1"})
        params = self.api_call.call_args[0][1]
        self.assertFalse(params["approve"])
        self.append_target.assert_not_called()
        self.send_message.assert_not_called()

    def test_friend_request_add_to_targets_off(self):
        self._set_aa(add_to_targets=False)
        bot_core._handle_request_event({"post_type": "request", "request_type": "friend",
                                        "user_id": "111", "flag": "F1"})
        self.append_target.assert_not_called()
        self.send_message.assert_called_once()  # 欢迎语仍发

    def test_group_add_allowed(self):
        self._set_aa()
        bot_core._handle_request_event({"post_type": "request", "request_type": "group",
                                        "sub_type": "add", "user_id": "222",
                                        "group_id": "8888", "flag": "G1"})
        args = self.api_call.call_args
        self.assertEqual(args[0][0], "set_group_add_request")
        self.assertEqual(args[0][1]["sub_type"], "add")
        self.assertTrue(args[0][1]["approve"])
        self.append_target.assert_called_once_with("group_ids", "8888")
        self.smart_send_group.assert_called_once()
        self.assertEqual(self.smart_send_group.call_args[0][0], "8888")

    def test_group_invite_allowed_no_welcome(self):
        self._set_aa()
        bot_core._handle_request_event({"post_type": "request", "request_type": "group",
                                        "sub_type": "invite", "user_id": "222",
                                        "group_id": "8888", "flag": "G2"})
        args = self.api_call.call_args
        self.assertEqual(args[0][0], "set_group_add_request")
        self.assertEqual(args[0][1]["sub_type"], "invite")
        self.assertTrue(args[0][1]["approve"])
        self.append_target.assert_called_once_with("group_ids", "8888")
        self.smart_send_group.assert_not_called()  # 邀请分支不发送欢迎语

    def test_group_invite_disabled(self):
        self._set_aa(group_invite=False)
        bot_core._handle_request_event({"post_type": "request", "request_type": "group",
                                        "sub_type": "invite", "user_id": "222", "flag": "G3"})
        self.api_call.assert_not_called()

    def test_unknown_request_type(self):
        self._set_aa()
        bot_core._handle_request_event({"post_type": "request", "request_type": "weird", "user_id": "111"})
        self.api_call.assert_not_called()
        texts = " ".join(str(c) for c in self.log.call_args_list)
        self.assertIn("未知 request_type", texts)


# ══════════════════════════════════════════════════════════════
# request 判定辅助（_request_allowed）：白/黑名单逻辑
# ══════════════════════════════════════════════════════════════

class TestRequestAllowed(_EventHandlersBase):
    def setUp(self):
        super().setUp()
        # 还原真实的 _request_allowed（基类 mock 掉了它），测真实判定逻辑
        self._real_ra = mock.patch.object(bot_core, "_request_allowed", _REAL_REQUEST_ALLOWED)
        self._real_ra.start()
        self.addCleanup(self._real_ra.stop)

    def _cfg(self, denylist=None, allowlist=None):
        cfg = {}
        if denylist:
            cfg["denylist"] = denylist
        if allowlist:
            cfg["allowlist"] = allowlist
        return cfg

    def test_denylist_rejects(self):
        self.auto_accept.return_value = self._cfg(denylist=["111"])
        allow, reason = bot_core._request_allowed("111")
        self.assertFalse(allow)
        self.assertEqual(reason, "denied")

    def test_allowlist_only(self):
        self.auto_accept.return_value = self._cfg(allowlist=["111", "222"])
        self.assertEqual(bot_core._request_allowed("111"), (True, "allowed"))
        self.assertEqual(bot_core._request_allowed("333"), (False, "not_allowlisted"))

    def test_no_lists_allows(self):
        self.auto_accept.return_value = {}
        self.assertEqual(bot_core._request_allowed("111"), (True, "allowed"))


# ══════════════════════════════════════════════════════════════
# group_increase（_handle_group_increase）：入群欢迎 / 通知 / 自动改名片
# ══════════════════════════════════════════════════════════════

class TestGroupIncrease(_EventHandlersBase):
    def test_member_join_welcome_and_notify(self):
        self._set_aa()
        bot_core.QQ_NICK_CACHE["333"] = "旧昵称"
        try:
            bot_core._handle_group_increase({"post_type": "notice", "notice_type": "group_increase",
                                             "group_id": 10001, "user_id": "333"})
            self.notify_admin.assert_called_once()
            self.assertIn("333", self.notify_admin.call_args[0][0])
            self.smart_send_group.assert_called_once()
            self.assertEqual(self.smart_send_group.call_args[0][0], "10001")
            self.assertEqual(self.smart_send_group.call_args[0][1], "进群了？坐！")
            # 昵称缓存被清理
            self.assertNotIn("333", bot_core.QQ_NICK_CACHE)
        finally:
            bot_core.QQ_NICK_CACHE.pop("333", None)

    def test_bot_self_join_no_welcome(self):
        self._set_aa()
        bot_core._handle_group_increase({"post_type": "notice", "notice_type": "group_increase",
                                         "group_id": 10001, "user_id": str(bot_core.BOT_QQ)})
        self.notify_admin.assert_not_called()
        self.smart_send_group.assert_not_called()

    def test_welcome_disabled_no_send_but_notify(self):
        self._set_aa(welcome_new_member=False)
        bot_core._handle_group_increase({"post_type": "notice", "notice_type": "group_increase",
                                         "group_id": 10001, "user_id": "333"})
        self.notify_admin.assert_called_once()  # 通知仍发
        self.smart_send_group.assert_not_called()  # 欢迎语不发

    def test_invite_operator_appended_to_welcome(self):
        self._set_aa()
        bot_core._handle_group_increase({"post_type": "notice", "notice_type": "group_increase",
                                         "group_id": 10001, "user_id": "333",
                                         "sub_type": "invite", "operator_id": "999"})
        self.assertEqual(self.smart_send_group.call_args[0][1], "进群了？坐！ （昵称999 拉进来的）")

    def test_new_member_card_calls_api(self):
        self._set_aa()
        self.group_restriction.return_value = "新名片"
        bot_core._handle_group_increase({"post_type": "notice", "notice_type": "group_increase",
                                         "group_id": 10001, "user_id": "333"})
        args = self.api_call.call_args
        self.assertEqual(args[0][0], "set_group_card")
        self.assertEqual(args[0][1]["group_id"], 10001)
        self.assertEqual(args[0][1]["user_id"], 333)
        self.assertEqual(args[0][1]["card"], "新名片")

    def test_missing_group_id_noop(self):
        bot_core._handle_group_increase({"post_type": "notice", "notice_type": "group_increase",
                                         "user_id": "333"})
        self.notify_admin.assert_not_called()
        self.smart_send_group.assert_not_called()
        self.api_call.assert_not_called()


# ══════════════════════════════════════════════════════════════
# group_decrease（_handle_group_decrease）：退群记录 / 会话清理
# ══════════════════════════════════════════════════════════════

class TestGroupDecrease(_EventHandlersBase):
    def test_member_leave_logs_and_clears_nick(self):
        bot_core.QQ_NICK_CACHE["333"] = "旧昵称"
        try:
            bot_core._handle_group_decrease({"post_type": "notice", "notice_type": "group_decrease",
                                             "group_id": 10001, "user_id": "333", "sub_type": "leave"})
            self.assertIn("333", str(self.log.call_args_list[0][0][0]))
            self.assertNotIn("333", bot_core.QQ_NICK_CACHE)
            self.assertNotIn("10001", bot_core.GROUP_SESSION)
        finally:
            bot_core.QQ_NICK_CACHE.pop("333", None)

    def test_bot_leave_cleans_session_and_buffer(self):
        timer = mock.Mock()
        bot_core.GROUP_SESSION["10001"] = {"remaining": 5, "last_msg_time": 1}
        bot_core.GROUP_MSG_BUFFER["10001"] = {"timer": timer}
        try:
            bot_core._handle_group_decrease({"post_type": "notice", "notice_type": "group_decrease",
                                             "group_id": 10001, "user_id": str(bot_core.BOT_QQ),
                                             "sub_type": "kick_me"})
            self.assertNotIn("10001", bot_core.GROUP_SESSION)
            self.assertNotIn("10001", bot_core.GROUP_MSG_BUFFER)
            timer.cancel.assert_called_once()
        finally:
            bot_core.GROUP_SESSION.pop("10001", None)
            bot_core.GROUP_MSG_BUFFER.pop("10001", None)

    def test_missing_group_id_noop(self):
        bot_core._handle_group_decrease({"post_type": "notice", "notice_type": "group_decrease",
                                         "user_id": "333"})
        self.assertTrue(self.log.call_count == 0)


# ══════════════════════════════════════════════════════════════
# friend_add（_handle_friend_add）：写监听名单 + 欢迎语 + 通知管理员
# ══════════════════════════════════════════════════════════════

class TestFriendAdd(_EventHandlersBase):
    def test_normal_add(self):
        self._set_aa()
        bot_core._handle_friend_add({"post_type": "notice", "notice_type": "friend_add",
                                     "user_id": "444"})
        self.append_target.assert_called_once_with("target_qqs", "444")
        self.send_message.assert_called_once()
        # 2026-10-12：欢迎语不再写死"小a"，取配置里的第一个昵称
        self.assertEqual(self.send_message.call_args[0][0],
                         f"你好，我是{bot_core._bot_nicknames()[0]}～")
        self.notify_admin.assert_called_once()
        self.assertIn("444", self.notify_admin.call_args[0][0])

    def test_add_to_targets_off(self):
        self._set_aa(add_to_targets=False)
        bot_core._handle_friend_add({"post_type": "notice", "notice_type": "friend_add",
                                     "user_id": "444"})
        self.append_target.assert_not_called()
        self.send_message.assert_called_once()  # 欢迎语仍发

    def test_welcome_empty_disables(self):
        # 显式空串 = 关闭欢迎语；名单与管理员通知不受影响
        self._set_aa(welcome_private="")
        bot_core._handle_friend_add({"post_type": "notice", "notice_type": "friend_add",
                                     "user_id": "444"})
        self.send_message.assert_not_called()
        self.append_target.assert_called_once()
        self.notify_admin.assert_called_once()

    def test_welcome_whitespace_disables(self):
        self._set_aa(welcome_private="   ")
        bot_core._handle_friend_add({"post_type": "notice", "notice_type": "friend_add",
                                     "user_id": "444"})
        self.send_message.assert_not_called()

    def test_welcome_unconfigured_uses_default(self):
        # 未配置 welcome_private（auto_accept 无此键）→ 默认欢迎语
        self._set_aa()
        bot_core._handle_friend_add({"post_type": "notice", "notice_type": "friend_add",
                                     "user_id": "444"})
        self.send_message.assert_called_once()
        # 2026-10-12：欢迎语不再写死"小a"，取配置里的第一个昵称
        self.assertEqual(self.send_message.call_args[0][0],
                         f"你好，我是{bot_core._bot_nicknames()[0]}～")
        self.append_target.assert_called_once()
        self.notify_admin.assert_called_once()

    def test_welcome_custom(self):
        self._set_aa(welcome_private="专属欢迎语")
        bot_core._handle_friend_add({"post_type": "notice", "notice_type": "friend_add",
                                     "user_id": "444"})
        self.send_message.assert_called_once()
        self.assertEqual(self.send_message.call_args[0][0], "专属欢迎语")

    def test_missing_user_id_noop(self):
        self._set_aa()
        bot_core._handle_friend_add({"post_type": "notice", "notice_type": "friend_add"})
        self.append_target.assert_not_called()
        self.send_message.assert_not_called()
        self.notify_admin.assert_not_called()


# ══════════════════════════════════════════════════════════════
# group_ban（_handle_group_ban）：纯日志事件
# ══════════════════════════════════════════════════════════════

class TestGroupBan(_EventHandlersBase):
    def test_ban_logs_only(self):
        bot_core._handle_group_ban({"post_type": "notice", "notice_type": "group_ban",
                                    "group_id": 10001, "user_id": "333",
                                    "operator_id": "444", "duration": 600})
        self.api_call.assert_not_called()
        self.send_message.assert_not_called()
        self.smart_send_group.assert_not_called()
        texts = " ".join(str(c) for c in self.log.call_args_list)
        self.assertIn("群禁言", texts)
        self.assertIn("600", texts)


# ══════════════════════════════════════════════════════════════
# poke（_handle_poke_event）：群戳 → 会话 + AI 回复；私戳 → AI 回复 + 会话登记
# ══════════════════════════════════════════════════════════════

class TestPokeEvent(_EventHandlersBase):
    """额外 mock：call_deepseek / track_bot_reply / 表情包相关 / MEME_OK=False。

    2026-10-25：群戳不再自己回——它只把"被戳"写成一条入站事件，交给群回复
    流水线。所以这里断言的是"进了缓冲区、没调模型、没发送、没提前写会话额度"。
    """

    def setUp(self):
        super().setUp()
        # 快照可变全局状态，测试结束后还原（避免用例间互相污染）
        self._snapshot = (dict(bot_core.GROUP_SESSION), dict(bot_core.POKE_COUNTS),
                          dict(bot_core.PRIVATE_SESSION),
                          dict(bot_core.GROUP_MSG_BUFFER),
                          dict(bot_core.GROUP_PIPELINE))
        self.addCleanup(self._restore_globals)
        self.call_deepseek = mock.Mock(return_value="别戳啦～")
        self.track_bot_reply = mock.Mock()
        self.match_meme = mock.Mock(return_value=None)
        self.send_group_image = mock.Mock(return_value=True)
        self.send_image = mock.Mock(return_value=True)
        self._poke_patchers = [
            mock.patch.object(bot_core, "call_deepseek", self.call_deepseek),
            mock.patch.object(bot_core, "track_bot_reply", self.track_bot_reply),
            mock.patch.object(bot_core, "match_meme", self.match_meme),
            mock.patch.object(bot_core, "send_group_image", self.send_group_image),
            mock.patch.object(bot_core, "send_image", self.send_image),
            mock.patch.object(bot_core, "MEME_OK", False),
        ]
        for p in self._poke_patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._poke_patchers])

    def _restore_globals(self):
        # 入站会起真 Timer：先取消，否则 3 秒后回调打到已还原的 mock 上
        for buf in bot_core.GROUP_MSG_BUFFER.values():
            t = (buf or {}).get("timer")
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
        bot_core.GROUP_SESSION.clear(); bot_core.GROUP_SESSION.update(self._snapshot[0])
        bot_core.POKE_COUNTS.clear(); bot_core.POKE_COUNTS.update(self._snapshot[1])
        bot_core.PRIVATE_SESSION.clear(); bot_core.PRIVATE_SESSION.update(self._snapshot[2])
        bot_core.GROUP_MSG_BUFFER.clear(); bot_core.GROUP_MSG_BUFFER.update(self._snapshot[3])
        bot_core.GROUP_PIPELINE.clear(); bot_core.GROUP_PIPELINE.update(self._snapshot[4])

    def _grp_poke(self, **kw):
        ev = {"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
              "group_id": 10001, "user_id": "10086001", "target_id": str(bot_core.BOT_QQ)}
        ev.update(kw)
        return ev

    def _reset_group(self):
        buf = bot_core.GROUP_MSG_BUFFER.pop("10001", None)
        if buf and buf.get("timer"):
            try:
                buf["timer"].cancel()
            except Exception:
                pass
        bot_core.GROUP_PIPELINE.pop("10001", None)
        bot_core.GROUP_SESSION.pop("10001", None)

    def test_group_poke_enters_pipeline_without_replying(self):
        self._reset_group()
        recent_before = len(bot_core.GROUP_RECENT_MSGS.get("10001", []))
        timeline_before = len(bot_core.GROUP_TIMELINE.get("10001", []))
        bot_core._handle_poke_event(self._grp_poke())
        buf = bot_core.GROUP_MSG_BUFFER.get("10001")
        self.assertIsNotNone(buf, "戳没有写进群缓冲区")
        self.assertIn("戳了戳你，这是第 1 次", buf["text"])
        # 关键：不过决策器的那条路要留着，所以 forced 必须是 False
        # 2026-10-29（1.5.10）：戳一戳改成"视同点名" —— 原实现 forced=False，
        # 要过决策器 observe + 发言闸概率两道闸，真机多次"戳了不理"。
        self.assertTrue(buf["forced"])
        self.assertEqual(buf["poke_uid"], "10086001")
        self.assertIsNotNone(buf["timer"])
        self.call_deepseek.assert_not_called()
        self.smart_send_group.assert_not_called()
        # 额度后置：入站时不许写，否则决策器 skip 也白送 2 轮免 @
        self.assertNotIn("10001", bot_core.GROUP_SESSION)
        # 系统事件不进近况/时间线（否则回抓会把"戳了戳你"当群友发言）
        self.assertEqual(len(bot_core.GROUP_RECENT_MSGS.get("10001", [])), recent_before)
        self.assertEqual(len(bot_core.GROUP_TIMELINE.get("10001", [])), timeline_before)

    def test_group_poke_other_target_ignored(self):
        self._reset_group()
        bot_core._handle_poke_event(self._grp_poke(target_id="222", user_id="111"))
        self.smart_send_group.assert_not_called()
        self.call_deepseek.assert_not_called()
        self.assertNotIn("10001", bot_core.GROUP_MSG_BUFFER)
        self.assertNotIn("10001", bot_core.GROUP_SESSION)

    def test_group_poke_count_escalates_vibe(self):
        # 同人同群连续两次 → 同一批，正文里能看到"第 1 次 / 第 2 次"
        bot_core.POKE_COUNTS.clear()
        self._reset_group()
        bot_core._handle_poke_event(self._grp_poke())
        bot_core._handle_poke_event(self._grp_poke())
        buf = bot_core.GROUP_MSG_BUFFER.get("10001")
        self.assertIsNotNone(buf)
        self.assertIn("第 1 次", buf["text"])
        self.assertIn("第 2 次", buf["text"])
        self.assertEqual(buf.get("merged_n") or 0, 0)  # 戳不回抓历史
        bot_core.POKE_COUNTS.clear()

    def test_group_poke_count_survives_minutes_apart(self):
        """相隔 2 分钟的两次戳要累加。

        2026-10-25 线上实测：20:17 与 20:19 两次戳都显示「第 1 次」，
        因为旧默认 poke_timeout=30 秒。用户看到的就是「计数不动」。
        """
        bot_core.POKE_COUNTS.clear()
        self._reset_group()
        t0 = 1_700_000_000.0
        with mock.patch.object(bot_core.time, "time", return_value=t0):
            bot_core._handle_poke_event(self._grp_poke())
        _buf = bot_core.GROUP_MSG_BUFFER.pop("10001", None)
        if _buf and _buf.get("timer"):
            _buf["timer"].cancel()
        with mock.patch.object(bot_core.time, "time", return_value=t0 + 120):
            bot_core._handle_poke_event(self._grp_poke())
        buf = bot_core.GROUP_MSG_BUFFER.get("10001")
        self.assertIsNotNone(buf)
        self.assertIn("第 2 次", buf["text"], "相隔 2 分钟的戳被重置成第 1 次")
        bot_core.POKE_COUNTS.clear()

    def test_group_poke_count_resets_after_timeout(self):
        """超过 poke_timeout（默认 600 秒）才重新从 1 数。"""
        bot_core.POKE_COUNTS.clear()
        self._reset_group()
        t0 = 1_700_000_000.0
        with mock.patch.object(bot_core.time, "time", return_value=t0):
            bot_core._handle_poke_event(self._grp_poke())
        _buf = bot_core.GROUP_MSG_BUFFER.pop("10001", None)
        if _buf and _buf.get("timer"):
            _buf["timer"].cancel()
        with mock.patch.object(bot_core.time, "time", return_value=t0 + 601):
            bot_core._handle_poke_event(self._grp_poke())
        buf = bot_core.GROUP_MSG_BUFFER.get("10001")
        self.assertIsNotNone(buf)
        self.assertIn("第 1 次", buf["text"])
        self.assertNotIn("第 2 次", buf["text"])
        bot_core.POKE_COUNTS.clear()

    def test_private_poke_numeric_sends_and_registers_session(self):
        bot_core.PRIVATE_SESSION.clear()
        # 2026-10-28：私聊戳一戳要过私聊名单（与私聊消息同一条门槛）。
        # 名单内的人戳 → 照旧回复 + 登记会话。
        with mock.patch.object(bot_core, "TARGET_QQS", ["10086001"]):
            bot_core._handle_poke_event({"post_type": "notice", "notice_type": "notify",
                                         "sub_type": "poke", "user_id": "10086001"})
        self.send_message.assert_called_once()
        self.assertEqual(self.send_message.call_args[0][1], "10086001")
        self.assertIn("10086001", bot_core.PRIVATE_SESSION)  # 登记私聊活跃会话
        bot_core.PRIVATE_SESSION.clear()

    def test_private_poke_stranger_ignored(self):
        """名单外的人戳一戳：不回复、不反戳、不登记会话。

        2026-10-28 线上：非名单用户（Texas）戳一下就走完了「反戳 + 调模型 +
        回复 + 表情包」，而同一批人发私聊消息是被名单挡住的 —— 两条入口，
        只接了一根线。这里钉住"名单外没有任何回应"。
        """
        bot_core.PRIVATE_SESSION.clear()
        with mock.patch.object(bot_core, "TARGET_QQS", ["1234567890"]), \
                mock.patch.object(bot_core, "_send_poke_back") as _pb:
            bot_core._handle_poke_event({"post_type": "notice", "notice_type": "notify",
                                         "sub_type": "poke", "user_id": "1234567892",
                                         "target_id": ""})
        self.send_message.assert_not_called()
        self.call_deepseek.assert_not_called()
        _pb.assert_not_called()
        self.assertNotIn("1234567892", bot_core.PRIVATE_SESSION)
        bot_core.PRIVATE_SESSION.clear()

    def test_private_poke_nt_uid_not_guessed(self):
        """NT UID（u_xxx）取不到 QQ 号 → 判为不在名单，不猜成 TARGET_QQS[0]。

        旧版把 u_ 兜底成监听目标，等于把陌生人的戳记到主人头上：回复会发进
        主人私聊，还白烧一次模型。取不到身份就别回应，比猜错安全。
        """
        with mock.patch.object(bot_core, "TARGET_QQS", ["100"]), \
                mock.patch.object(bot_core, "_send_poke_back") as _pb:
            bot_core.PRIVATE_SESSION.clear()
            bot_core._handle_poke_event({"post_type": "notice", "notice_type": "notify",
                                         "sub_type": "poke", "user_id": "u_abc", "target_id": "u_abc"})
            self.send_message.assert_not_called()
            self.call_deepseek.assert_not_called()
            _pb.assert_not_called()
            self.assertNotIn("u_abc", bot_core.PRIVATE_SESSION)
            bot_core.PRIVATE_SESSION.clear()


# ══════════════════════════════════════════════════════════════
# recall（_handle_recall_event）：群/私聊撤回反馈（仅活跃会话中触发）
# ══════════════════════════════════════════════════════════════

class TestRecallEvent(_EventHandlersBase):
    """额外 mock：_config 为受控 dict（撤回反馈开关/冷却/自定义文案）。"""

    def setUp(self):
        super().setUp()
        self._snapshot = (dict(bot_core.GROUP_SESSION), dict(bot_core.GROUP_MSG_BUFFER),
                          dict(bot_core.GROUP_RECENT_MSGS), dict(bot_core.RECALL_FEEDBACK_TIME),
                          dict(bot_core.PRIVATE_SESSION))
        self.addCleanup(self._restore_globals)
        self.cfg = {
            "recall_feedback_enable": True,
            "recall_feedback_cooldown": 60,
            "recall_feedback_pre_messages": [],
            "recall_feedback_post_messages": [],
        }
        self._cfg_patch = mock.patch.object(bot_core, "_config", self.cfg)
        self._cfg_patch.start()
        self.addCleanup(self._cfg_patch.stop)

    def _restore_globals(self):
        bot_core.GROUP_SESSION.clear(); bot_core.GROUP_SESSION.update(self._snapshot[0])
        bot_core.GROUP_MSG_BUFFER.clear(); bot_core.GROUP_MSG_BUFFER.update(self._snapshot[1])
        bot_core.GROUP_RECENT_MSGS.clear(); bot_core.GROUP_RECENT_MSGS.update(self._snapshot[2])
        bot_core.RECALL_FEEDBACK_TIME.clear(); bot_core.RECALL_FEEDBACK_TIME.update(self._snapshot[3])
        bot_core.PRIVATE_SESSION.clear(); bot_core.PRIVATE_SESSION.update(self._snapshot[4])

    def _grp_recall(self, **kw):
        ev = {"post_type": "notice", "notice_type": "group_recall",
              "group_id": 10001, "user_id": "111", "message_id": 9}
        ev.update(kw)
        return ev

    def test_group_recall_no_session_skips(self):
        bot_core.GROUP_SESSION.pop("10001", None)
        bot_core._handle_recall_event(self._grp_recall())
        self.smart_send_group.assert_not_called()

    def test_group_recall_with_session_sends(self):
        bot_core.GROUP_SESSION["10001"] = {"remaining": 5, "last_msg_time": time.time()}
        bot_core.RECALL_FEEDBACK_TIME.pop("group_10001", None)
        bot_core.GROUP_RECENT_MSGS["10001"] = [(time.time(), "111", "你好")]
        try:
            bot_core._handle_recall_event(self._grp_recall())
            self.smart_send_group.assert_called_once()
            self.assertEqual(self.smart_send_group.call_args[0][0], "10001")
            self.assertIn("group_10001", bot_core.RECALL_FEEDBACK_TIME)  # 已记冷却
        finally:
            bot_core.GROUP_SESSION.pop("10001", None)
            bot_core.GROUP_RECENT_MSGS.pop("10001", None)
            bot_core.RECALL_FEEDBACK_TIME.pop("group_10001", None)

    def test_group_recall_cancels_pending_buffer(self):
        timer = mock.Mock()
        bot_core.GROUP_SESSION["10001"] = {"remaining": 5, "last_msg_time": time.time()}
        bot_core.GROUP_MSG_BUFFER["10001"] = {"timer": timer}
        try:
            bot_core._handle_recall_event(self._grp_recall())
            timer.cancel.assert_called_once()
            self.assertNotIn("10001", bot_core.GROUP_MSG_BUFFER)  # 缓冲已清（截断）
            self.smart_send_group.assert_called_once()  # 发截断反馈
        finally:
            bot_core.GROUP_SESSION.pop("10001", None)
            bot_core.GROUP_MSG_BUFFER.pop("10001", None)

    def test_group_recall_feedback_disabled(self):
        self.cfg["recall_feedback_enable"] = False
        bot_core.GROUP_SESSION["10001"] = {"remaining": 5, "last_msg_time": time.time()}
        try:
            bot_core._handle_recall_event(self._grp_recall())
            self.smart_send_group.assert_not_called()
        finally:
            bot_core.GROUP_SESSION.pop("10001", None)

    def test_group_recall_cooldown_skips(self):
        bot_core.GROUP_SESSION["10001"] = {"remaining": 5, "last_msg_time": time.time()}
        bot_core.RECALL_FEEDBACK_TIME["group_10001"] = time.time()
        try:
            bot_core._handle_recall_event(self._grp_recall())
            self.smart_send_group.assert_not_called()
        finally:
            bot_core.GROUP_SESSION.pop("10001", None)
            bot_core.RECALL_FEEDBACK_TIME.pop("group_10001", None)

    def test_group_recall_user_not_recently_active(self):
        bot_core.GROUP_SESSION["10001"] = {"remaining": 5, "last_msg_time": time.time()}
        bot_core.GROUP_RECENT_MSGS["10001"] = [(time.time(), "999", "别人发的")]
        try:
            bot_core._handle_recall_event(self._grp_recall())
            self.smart_send_group.assert_not_called()  # 撤回者最近没发消息
        finally:
            bot_core.GROUP_SESSION.pop("10001", None)
            bot_core.GROUP_RECENT_MSGS.pop("10001", None)

    def test_private_recall_no_session_skips(self):
        bot_core.PRIVATE_SESSION.clear()
        bot_core._handle_recall_event({"post_type": "notice", "notice_type": "friend_recall",
                                       "user_id": "10086001"})
        self.send_message.assert_not_called()

    def test_private_recall_with_session_sends(self):
        bot_core.PRIVATE_SESSION["10086001"] = {"last_msg_time": time.time()}
        try:
            bot_core._handle_recall_event({"post_type": "notice", "notice_type": "friend_recall",
                                           "user_id": "10086001"})
            self.send_message.assert_called_once()
            self.assertEqual(self.send_message.call_args[0][1], "10086001")
        finally:
            bot_core.PRIVATE_SESSION.clear()

    def test_private_recall_session_expired(self):
        bot_core.PRIVATE_SESSION["10086001"] = {"last_msg_time": time.time() - bot_core.PRIVATE_SESSION_IDLE_TIMEOUT - 10}
        try:
            bot_core._handle_recall_event({"post_type": "notice", "notice_type": "friend_recall",
                                           "user_id": "10086001"})
            self.send_message.assert_not_called()
        finally:
            bot_core.PRIVATE_SESSION.clear()

    def test_private_recall_disabled(self):
        self.cfg["recall_private_enable"] = False
        bot_core.PRIVATE_SESSION["10086001"] = {"last_msg_time": time.time()}
        try:
            bot_core._handle_recall_event({"post_type": "notice", "notice_type": "friend_recall",
                                           "user_id": "10086001"})
            self.send_message.assert_not_called()  # 私聊开关关闭，即使群聊开关开着也不回
        finally:
            bot_core.PRIVATE_SESSION.clear()

    def test_private_recall_cooldown_skips(self):
        bot_core.PRIVATE_SESSION["10086001"] = {"last_msg_time": time.time()}
        bot_core.RECALL_FEEDBACK_TIME["private_10086001"] = time.time()
        try:
            bot_core._handle_recall_event({"post_type": "notice", "notice_type": "friend_recall",
                                           "user_id": "10086001"})
            self.send_message.assert_not_called()
        finally:
            bot_core.PRIVATE_SESSION.clear()
            bot_core.RECALL_FEEDBACK_TIME.pop("private_10086001", None)

    def test_private_recall_custom_messages(self):
        self.cfg["recall_private_post_messages"] = ["自定义私聊撤回文案"]
        bot_core.PRIVATE_SESSION["10086001"] = {"last_msg_time": time.time()}
        try:
            bot_core._handle_recall_event({"post_type": "notice", "notice_type": "friend_recall",
                                           "user_id": "10086001"})
            self.send_message.assert_called_once_with("自定义私聊撤回文案", "10086001")
        finally:
            bot_core.PRIVATE_SESSION.clear()

    def test_private_recall_group_switch_does_not_affect(self):
        # 群聊开关关闭不影响私聊：recall_feedback_enable=False 时私聊仍可回复
        self.cfg["recall_feedback_enable"] = False
        bot_core.PRIVATE_SESSION["10086001"] = {"last_msg_time": time.time()}
        try:
            bot_core._handle_recall_event({"post_type": "notice", "notice_type": "friend_recall",
                                           "user_id": "10086001"})
            self.send_message.assert_called_once()
        finally:
            bot_core.PRIVATE_SESSION.clear()

    def test_private_recall_qq_in_parens(self):
        # 撤回 user_id 形如 "u_xxx (123)" → 按 (123) 查会话，仍回给原 user_id
        bot_core.PRIVATE_SESSION["123"] = {"last_msg_time": time.time()}
        try:
            bot_core._handle_recall_event({"post_type": "notice", "notice_type": "friend_recall",
                                           "user_id": "u_abc (123)"})
            self.send_message.assert_called_once()
            self.assertEqual(self.send_message.call_args[0][1], "u_abc (123)")
        finally:
            bot_core.PRIVATE_SESSION.clear()

    def test_private_recall_invalid_uid(self):
        bot_core.PRIVATE_SESSION.clear()
        bot_core._handle_recall_event({"post_type": "notice", "notice_type": "friend_recall",
                                       "user_id": "None"})
        self.send_message.assert_not_called()



class TestDeciderRecent(unittest.TestCase):
    """1.5.10：最近决策环形缓冲（插件页展示"为什么回/没回"）。"""

    def test_record_and_action(self):
        import plugins.group_chat_decider as d
        before = len(d._RECENT_DECISIONS)
        class _D:
            reason = "测试原因"
            atmosphere = "neutral"
            confidence = 0.42
        d._record_decision("123", "在吗", _D(), {"action": "skip"})
        self.assertEqual(len(d._RECENT_DECISIONS), before + 1)
        r = d.PLUGIN_ACTIONS["recent"]({})
        self.assertTrue(r["ok"])
        latest = r["decisions"][0]
        self.assertEqual(latest["act"], "skip")
        self.assertEqual(latest["reason"], "测试原因")
        self.assertAlmostEqual(latest["confidence"], 0.42, places=2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
