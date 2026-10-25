# -*- coding: utf-8 -*-
"""戳一戳接入流水线后的行为（2026-10-25）。

`_handle_poke_event` 只把"被戳"写成一条入站事件，真正的判断与发送在
`_pipeline_step` 里。这个文件跑完整的一轮，钉住四件事：

1. 决策器 skip → 不调模型、不发送、**不写**免 @ 追问权。
2. 决策器放行 → 只调一次模型、只发一次，**发送之后**才写追问权（poke_owner 是戳的人）。
3. 戳与别人的消息合并成一批 → 只有一次生成；额度记给戳的人，不记给说话的人。
4. 发送抛异常 → 不写追问权（额度只给真的被回应的人）。

运行：python tests/test_poke_pipeline.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

GID = "_test_poke_pipeline_"
POKER = "10086001"
OTHER = "10086002"
_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


class _FakePM:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def has_hook(self, hook_name):
        return hook_name == "before_reply"

    def is_loaded(self, name):
        return True

    def is_enabled(self, name):
        return True

    def get_meta(self, name):
        return {"priority": 5, "name": name}

    def execute_hooks(self, hook_name, **kwargs):
        self.calls += 1
        if hook_name != "before_reply":
            return []
        return [(n, dict(r)) for n, r in self.results]


def _clean():
    buf = bot_core.GROUP_MSG_BUFFER.pop(GID, None)
    if buf and buf.get("timer"):
        try:
            buf["timer"].cancel()
        except Exception:
            pass
    rec = bot_core.GROUP_OBSERVE.pop(GID, None)
    if rec and rec.get("timer"):
        try:
            rec["timer"].cancel()
        except Exception:
            pass
    bot_core.GROUP_SESSION.pop(GID, None)
    bot_core.GROUP_PIPELINE.pop(GID, None)
    bot_core.GROUP_BOT_REPLIES.pop(GID, None)


def _run(plugin_results, *, poke_text=None, merge_text=None, merge_sender=OTHER,
         merge_forced=True, send_raises=False):
    """建一个戳批次（可选合并一条普通消息），同步跑一轮。返回观测结果。"""
    _clean()
    seen = {"ai": 0, "sent": []}
    orig = (bot_core.call_deepseek, bot_core.smart_send_group,
            bot_core.get_plugin_manager, bot_core.PLUGIN_OK,
            bot_core._dispatch_typed_before_reply, bot_core.startup_time)
    orig_cfg = dict(bot_core._config)
    fake = _FakePM(plugin_results)

    def fake_call(user_msg, images=None, *a, **kw):
        seen["ai"] += 1
        return "戳我干嘛"

    def fake_send(gid, text, *a, **kw):
        if send_raises:
            raise RuntimeError("模拟发送失败")
        seen["sent"].append(text)
        return True

    bot_core.call_deepseek = fake_call
    bot_core.smart_send_group = fake_send
    bot_core.get_plugin_manager = lambda: fake
    bot_core.PLUGIN_OK = True
    bot_core._dispatch_typed_before_reply = lambda *a, **kw: []
    bot_core.startup_time = time.time() - 10 ** 6
    bot_core._config["quiet_hours"] = ""
    bot_core._config["meme_enable"] = False
    bot_core._config["reply_reaction_delay"] = 0
    try:
        bot_core._pipeline_upsert(
            GID, sender=POKER,
            text=poke_text or f"[小明] （戳了戳你，这是第 1 次）",
            forced=False, source="poke", poke_uid=POKER)
        if merge_text is not None:
            bot_core._pipeline_upsert(
                GID, sender=merge_sender, text=merge_text,
                flags={"at_bot": True} if merge_forced else {},
                forced=merge_forced)
        buf = bot_core.GROUP_MSG_BUFFER.get(GID) or {}
        if buf.get("timer"):
            buf["timer"].cancel()          # 手动同步跑，避免 3 秒后重复触发
        batched = dict(buf)
        bot_core.process_buffered_message(GID)
    finally:
        (bot_core.call_deepseek, bot_core.smart_send_group,
         bot_core.get_plugin_manager, bot_core.PLUGIN_OK,
         bot_core._dispatch_typed_before_reply, bot_core.startup_time) = orig
        bot_core._config.clear()
        bot_core._config.update(orig_cfg)
    return seen, batched, fake


def test_batch_carries_poke_source():
    print("\n[1] 戳写进批次：forced=False、source=poke、poke_uid 记发起人")
    _clean()
    try:
        bot_core._pipeline_upsert(GID, sender=POKER, text="[小明] （戳了戳你，这是第 1 次）",
                                  forced=False, source="poke", poke_uid=POKER)
        buf = bot_core.GROUP_MSG_BUFFER.get(GID) or {}
        check("forced=False（要过决策器）", buf.get("forced") is False, buf.get("forced"))
        check("poke_uid 是戳的人", buf.get("poke_uid") == POKER, buf.get("poke_uid"))
        check("没写会话额度", GID not in bot_core.GROUP_SESSION)
    finally:
        _clean()


def test_decider_skip_writes_nothing():
    print("\n[2] 决策器 skip → 不调模型、不发送、不写追问权")
    seen, buf, fake = _run([("group_chat_decider",
                             {"action": "skip", "reason": "这会儿不想理"})])
    check("批次确实是戳", buf.get("poke_uid") == POKER, buf.get("poke_uid"))
    check("决策插件被问了一次", fake.calls == 1, fake.calls)
    check("★ 主模型没被调用", seen["ai"] == 0, seen["ai"])
    check("★ 没往群里发东西", not seen["sent"], seen["sent"])
    check("★ 没写追问权", GID not in bot_core.GROUP_SESSION,
          bot_core.GROUP_SESSION.get(GID))
    _clean()


def test_decider_reply_sends_once_and_unlocks():
    print("\n[3] 决策器放行 → 一次生成、一次发送，发送后才写追问权")
    seen, buf, _ = _run([("group_chat_decider",
                          {"action": "inject", "content": "【群聊氛围】可以接"})])
    check("★ 只调一次模型", seen["ai"] == 1, seen["ai"])
    check("★ 只发一次", len(seen["sent"]) == 1, seen["sent"])
    gs = bot_core.GROUP_SESSION.get(GID) or {}
    check("★ 发送后写了追问权", bool(gs), gs)
    check("poke_owner 是戳的人", gs.get("poke_owner") == POKER, gs.get("poke_owner"))
    cap = bot_core._config.get("poke_session_replies", 2)
    check("额度等于 poke_session_replies", gs.get("remaining") == cap, gs.get("remaining"))
    _clean()


def test_merged_batch_counts_once_and_unlocks_poker():
    print("\n[4] 戳 + 别人的触发消息合成一批 → 一次生成；额度只记给戳的人")
    # 合并后 forced=True：`_decide` 直接放行，但生成前还有一次插件注入/跳过
    # 的兜底（旧行为，不改），所以这里给 inject。
    seen, buf, _ = _run([("group_chat_decider",
                          {"action": "inject", "content": "【群聊氛围】可以接"})],
                        merge_text="[小红] 小a 在吗", merge_sender=OTHER,
                        merge_forced=True)
    check("只有一个批次", buf.get("text", "").count("戳了戳你") == 1, buf.get("text"))
    check("合并后 forced=True（触发取或）", buf.get("forced") is True, buf.get("forced"))
    check("poke_uid 仍是戳的人", buf.get("poke_uid") == POKER, buf.get("poke_uid"))
    check("只调一次模型", seen["ai"] == 1, seen["ai"])
    check("只发一次", len(seen["sent"]) == 1, seen["sent"])
    gs = bot_core.GROUP_SESSION.get(GID) or {}
    check("额度记给戳的人，不记给说话的人",
          gs.get("poke_owner") == POKER, gs.get("poke_owner"))
    _clean()


def test_send_failure_writes_nothing():
    print("\n[5] 发送抛异常 → 不写追问权")
    seen, buf, _ = _run([("group_chat_decider",
                          {"action": "inject", "content": "【群聊氛围】可以接"})],
                        send_raises=True)
    check("模型调过了", seen["ai"] == 1, seen["ai"])
    check("★ 发送失败后没有追问权", GID not in bot_core.GROUP_SESSION,
          bot_core.GROUP_SESSION.get(GID))
    _clean()


def main():
    test_batch_carries_poke_source()
    test_decider_skip_writes_nothing()
    test_decider_reply_sends_once_and_unlocks()
    test_merged_batch_counts_once_and_unlocks_poker()
    test_send_failure_writes_nothing()

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
