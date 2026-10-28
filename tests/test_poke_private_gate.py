# -*- coding: utf-8 -*-
"""私聊戳一戳必须过私聊名单（2026-10-28）。

现象（真机 00:08）：不在 `target_qqs` 里的陌生人只要戳一戳，Bot 就
反戳 + 调模型 + 发回复 + 发表情包，全程走完；而同一批人发的**私聊消息**
是被名单挡住的（`sender not in TARGET_QQS → continue`）。

根因：`_handle_poke_event` 的私聊分支从来没有名单判定 —— 名单只写在消息
路径上，poke 是第二条独立入口，接的时候没接这根线。同一个概念两处定义，
只补了一处。

这个文件钉住：
1. `_qq_of` 能把 `123456` / `昵称(123456)` 收敛成数字 QQ，`u_xxx` 取不到。
2. 名单外私戳：不调模型、不发送、**不反戳**。
3. 名单内私戳：一次模型、一次发送、反戳一次、写 chat_history。
4. 好友「自动回复已关闭」：私戳同样静默。
5. 群戳也过群名单（这条入口此前同样绕开了 `_group_allowed`）。
6. 名字不再被猜成 TARGET_QQS[0]（旧 `u_` 兜底会把回复发进主人私聊）。

运行：python tests/test_poke_private_gate.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

OWNER = "1234567890"
FRIEND2 = "1234567891"
STRANGER = "1234567892"
GID_OK = "123456789"
GID_NO = "111111111"

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


class _Ctx:
    """一次性替换掉所有会出网/落盘的函数，退出时还原。"""

    def __init__(self):
        self.calls = {"ai": 0, "sent": [], "poke_back": 0, "meme": 0}
        self._orig = {}

    def __enter__(self):
        o = self._orig
        o["TARGET_QQS"] = bot_core.TARGET_QQS
        o["call_deepseek"] = bot_core.call_deepseek
        o["send_message"] = bot_core.send_message
        o["_send_poke_back"] = bot_core._send_poke_back
        o["send_image"] = bot_core.send_image
        o["resolve_qq_nick"] = bot_core.resolve_qq_nick
        o["_pipeline_upsert"] = bot_core._pipeline_upsert
        o["_group_allowed"] = bot_core._group_allowed
        o["log"] = bot_core.log
        o["MEME_OK"] = bot_core.MEME_OK
        o["match_meme"] = bot_core.match_meme
        o["_config"] = dict(bot_core._config)
        o["chat_history"] = dict(bot_core.chat_history)
        o["PRIVATE_SESSION"] = dict(bot_core.PRIVATE_SESSION)

        bot_core.TARGET_QQS = [OWNER, FRIEND2]
        bot_core.log = lambda *a, **k: None
        bot_core.call_deepseek = self._fake_ai
        bot_core.send_message = self._fake_send
        bot_core._send_poke_back = self._fake_poke_back
        bot_core.send_image = self._fake_image
        bot_core.resolve_qq_nick = lambda uid: "某个昵称"
        bot_core._group_allowed = lambda gid: str(gid) == GID_OK
        bot_core.MEME_OK = False
        bot_core.chat_history = {}
        bot_core.PRIVATE_SESSION = {}
        bot_core._config["poke_timeout"] = 600
        bot_core._config["friend_restrictions"] = {}
        return self

    def __exit__(self, *exc):
        o = self._orig
        bot_core.TARGET_QQS = o["TARGET_QQS"]
        bot_core.call_deepseek = o["call_deepseek"]
        bot_core.send_message = o["send_message"]
        bot_core._send_poke_back = o["_send_poke_back"]
        bot_core.send_image = o["send_image"]
        bot_core.resolve_qq_nick = o["resolve_qq_nick"]
        bot_core._pipeline_upsert = o["_pipeline_upsert"]
        bot_core._group_allowed = o["_group_allowed"]
        bot_core.log = o["log"]
        bot_core.MEME_OK = o["MEME_OK"]
        bot_core.match_meme = o["match_meme"]
        bot_core._config.clear()
        bot_core._config.update(o["_config"])
        bot_core.chat_history = o["chat_history"]
        bot_core.PRIVATE_SESSION = o["PRIVATE_SESSION"]
        return False

    def _fake_ai(self, *a, **kw):
        self.calls["ai"] += 1
        return "戳我干嘛"

    def _fake_send(self, text, target_qq=None):
        self.calls["sent"].append((str(target_qq), text))
        return True

    def _fake_poke_back(self, *a, **kw):
        self.calls["poke_back"] += 1
        return True

    def _fake_image(self, *a, **kw):
        self.calls["meme"] += 1
        return True


def test_qq_of():
    print("\n[1] _qq_of：三种 UID 形态的收敛")
    check("数字原样", bot_core._qq_of("123456") == "123456", bot_core._qq_of("123456"))
    check("昵称(QQ) 抠出数字",
          bot_core._qq_of("Texas(1234567892)") == "1234567892",
          bot_core._qq_of("Texas(1234567892)"))
    check("u_ UID 取不到 → 空串", bot_core._qq_of("u_abc123") == "", bot_core._qq_of("u_abc123"))
    check("空值 → 空串", bot_core._qq_of("") == "", bot_core._qq_of(""))


def test_private_allowed():
    print("\n[2] _private_allowed：名单判定与私聊消息共用 target_qqs")
    with _Ctx():
        check("名单内（本人）", bot_core._private_allowed(OWNER) is True)
        check("名单内（第二人）", bot_core._private_allowed(FRIEND2) is True)
        check("名单外 → False", bot_core._private_allowed(STRANGER) is False)
        check("名单外展示形态 → False",
              bot_core._private_allowed("Texas(1234567892)") is False)
        check("名单内展示形态 → True",
              bot_core._private_allowed("大鱼(1234567890)") is True)
        check("u_ UID → False（不猜身份）", bot_core._private_allowed("u_abc") is False)


def test_stranger_private_poke_blocked():
    print("\n[3] 名单外私戳 → 不调模型、不发送、不反戳")
    with _Ctx() as c:
        bot_core._handle_poke_event({"user_id": STRANGER, "target_id": ""})
        check("★ 没调模型", c.calls["ai"] == 0, c.calls)
        check("★ 没发消息", not c.calls["sent"], c.calls["sent"])
        check("★ 没反戳", c.calls["poke_back"] == 0, c.calls["poke_back"])
        check("没写 chat_history", STRANGER not in bot_core.chat_history,
              list(bot_core.chat_history))
        check("没登记私聊会话", STRANGER not in bot_core.PRIVATE_SESSION,
              list(bot_core.PRIVATE_SESSION))


def test_stranger_uid_poke_never_hits_owner():
    print("\n[4] NT UID 私戳 → 不会把回复发进主人私聊（旧版 u_ 兜底）")
    with _Ctx() as c:
        bot_core._handle_poke_event({"user_id": "u_xyz789", "target_id": ""})
        check("没调模型", c.calls["ai"] == 0, c.calls)
        check("★ 一条都没发（尤其没发给 TARGET_QQS[0]）", not c.calls["sent"], c.calls["sent"])


def test_whitelisted_private_poke_replies():
    print("\n[5] 名单内私戳 → 一次模型、一次发送、反戳一次")
    with _Ctx() as c:
        bot_core._handle_poke_event({"user_id": OWNER, "target_id": ""})
        check("调了一次模型", c.calls["ai"] == 1, c.calls["ai"])
        check("发了一条", len(c.calls["sent"]) == 1, c.calls["sent"])
        check("发给戳的人", c.calls["sent"][0][0] == OWNER, c.calls["sent"])
        check("反戳一次", c.calls["poke_back"] == 1, c.calls["poke_back"])
        hist = bot_core.chat_history.get(OWNER) or []
        check("chat_history 记了这轮", len(hist) == 2, hist)
        check("登记了私聊会话", OWNER in bot_core.PRIVATE_SESSION,
              list(bot_core.PRIVATE_SESSION))


def test_friend_restriction_blocks_poke():
    print("\n[6] 好友「自动回复已关闭」→ 私戳也静默")
    with _Ctx() as c:
        bot_core._config["friend_restrictions"] = {OWNER: {"reply_enabled": False}}
        bot_core._handle_poke_event({"user_id": OWNER, "target_id": ""})
        check("没调模型", c.calls["ai"] == 0, c.calls["ai"])
        check("没发送", not c.calls["sent"], c.calls["sent"])
        check("没反戳", c.calls["poke_back"] == 0, c.calls["poke_back"])


def test_group_poke_group_whitelist():
    print("\n[7] 群戳也过群名单（此前这条入口绕开了 _group_allowed）")
    ups = []
    with _Ctx() as c:
        bot_core._pipeline_upsert = lambda *a, **kw: ups.append((a, kw)) or "created"
        bot_core._handle_poke_event({"user_id": STRANGER, "group_id": GID_NO})
        check("★ 名单外的群不建批", not ups, ups)
        check("★ 名单外的群不反戳", c.calls["poke_back"] == 0, c.calls["poke_back"])
        bot_core._handle_poke_event({"user_id": STRANGER, "group_id": GID_OK})
        check("名单内的群照旧建批", len(ups) == 1, ups)
        check("名单内的群照旧反戳", c.calls["poke_back"] == 1, c.calls["poke_back"])


def main():
    test_qq_of()
    test_private_allowed()
    test_stranger_private_poke_blocked()
    test_stranger_uid_poke_never_hits_owner()
    test_whitelisted_private_poke_replies()
    test_friend_restriction_blocks_poke()
    test_group_poke_group_whitelist()

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
