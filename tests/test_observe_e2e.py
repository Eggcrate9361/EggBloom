# -*- coding: utf-8 -*-
"""`observe` 端到端验证（2026-10-25）。

上一个文件 `test_decider_observe.py` 只做源码级检查（grep 分支在不在）。
那类测试能防"分支被删"，但防不住"分支写了却没生效"—— 而这次的事故恰恰是
**消费逻辑存在、某条路径上没走到**。

所以这个文件真的跑 `process_buffered_message`：
用一个假插件管理器返回 `observe`，断言
  ① 主模型**没有被调用**（不该开口时真的没开口）
  ② 没有往群里发东西
  ③ 观察窗口被真的排上了（GROUP_OBSERVE 有记录）

对照实验同时验证反例：同一个流程、同样 `in_session=True`，
把假插件改成返回 `inject` → 必须真的调到主模型。
没有这组对照，①"没调用主模型"可能只是因为流程在别处就断了。

事故路径复刻（日志群 829854136 11:46）：`in_session=True` → 走的是
10293 那一段插件裁决循环，而不是前面"无触发预决策"那一段。

运行：python tests/test_observe_e2e.py
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

GID = "_test_observe_e2e_"
_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


class _FakePM:
    """假的插件管理器：只回答主链会问的那几个问题。

    ★ `results` 是**列表**，不是一个 action —— 真实事故里决策器和 ego 同时
      返回了结果：决策器 observe、ego inject。只造一个 observe 测不出问题：

      真实链路里 ego 的 inject 会置 `plugin_intervened=True`，从而**绕过**
      「会话内未指向Bot，跳过」那道闸（10360 附近）；没有 inject 时流程会被
      那道闸拦下，于是"主模型没被调用"这个断言在修复前也成立 —— 测试就失去
      判别力了。（实测验证过：只给 observe 时，禁用修复后仍显示通过。）
    """

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def has_hook(self, hook_name):
        return hook_name == "before_reply"

    def is_loaded(self, name):
        return True

    def is_enabled(self, name):
        return True

    def get_meta(self, name):
        # 决策器 priority 5 最低（最先被消费），ego 20
        return {"priority": 5 if name == "group_chat_decider" else 20, "name": name}

    def execute_hooks(self, hook_name, **kwargs):
        self.calls.append(hook_name)
        if hook_name != "before_reply":
            return []
        return [(n, dict(r)) for n, r in self.results]


# ego 那份注入（事故里真实存在），用来复刻"plugin_intervened 被置位"
_EGO_INJECT = ("ego", {"action": "inject",
                       "content": "当前对话状态：话题=#sl\n【核心记忆】…",
                       "temperature": 0.75})


def _run(plugin_results, *, in_session=True):
    """跑一轮 process_buffered_message，返回 (是否调用主模型, 发送记录)。"""
    seen = {"ai": 0, "sent": []}
    orig = (bot_core.call_deepseek, bot_core.smart_send_group,
            getattr(bot_core, "get_plugin_manager", None),
            getattr(bot_core, "PLUGIN_OK", None),
            getattr(bot_core, "_dispatch_typed_before_reply", None))
    orig_cfg = dict(bot_core._config)
    fake = _FakePM(plugin_results)

    def fake_call(user_msg, images=None, *a, **kw):
        seen["ai"] += 1
        return "这是一句回复"

    def fake_send(gid, text, *a, **kw):
        seen["sent"].append(text)
        return True

    bot_core.call_deepseek = fake_call
    bot_core.smart_send_group = fake_send
    bot_core.get_plugin_manager = lambda: fake
    bot_core.PLUGIN_OK = True
    bot_core._dispatch_typed_before_reply = lambda *a, **kw: []
    bot_core.startup_time = time.time() - 10 ** 6
    # 关掉一切会干扰判定的旁路（生图/表情/静默时段/搜索）
    bot_core._config["quiet_hours"] = ""
    # 发言闸（1.5.9 §3.8 提交 6）在决策器之前就拦非触发批次；
    # 这两个用例测的是决策/观察链本身，把档位开到 4（全响应）绕开它。
    bot_core._config["speak_gate_tier"] = 4
    bot_core._config["meme_enable"] = False
    bot_core._config["memory_scope_share"] = False
    # 回复延迟清零，测试别等
    bot_core._config["reply_reaction_delay"] = 0

    bot_core.GROUP_SESSION[GID] = {"remaining": 9, "last_msg_time": time.time()}
    bot_core.GROUP_OBSERVE.pop(GID, None)
    bot_core.GROUP_MSG_BUFFER[GID] = {
        "text": "[碱式碳酸大脑] #sl", "sender": "2047271391", "time": time.time(),
        "deadline": time.time() + 5,
        "is_at_bot": False, "has_xiao_a": False, "has_bot_nick": False,
        "has_quote": False, "quote_me": False, "has_text_at": False,
        "in_session": in_session, "has_context": False, "images": [],
        "timer": None, "merged_n": 0,
    }
    try:
        bot_core.process_buffered_message(GID)
    finally:
        rec = bot_core.GROUP_OBSERVE.pop(GID, None)
        if rec and rec.get("timer"):
            try:
                rec["timer"].cancel()
            except Exception:
                pass
        bot_core.GROUP_MSG_BUFFER.pop(GID, None)
        bot_core.GROUP_SESSION.pop(GID, None)
        (bot_core.call_deepseek, bot_core.smart_send_group,
         bot_core.get_plugin_manager, bot_core.PLUGIN_OK,
         bot_core._dispatch_typed_before_reply) = orig
        bot_core._config.clear()
        bot_core._config.update(orig_cfg)
    return seen, rec


def main():
    print("[0] 前提：这条消息确实会走 in_session 那条路")
    # in_session=True 时不会进"无触发预决策"，正是事故路径
    check("未 @ 未叫小a（事故原始形态）", True)

    print("\n[1] 决策器 observe + ego inject（事故原样）→ 必须真的不开口")
    seen, rec = _run([
        ("group_chat_decider", {"action": "observe", "after": 25,
                                "reason": "[Decider v3] 测试用：这会儿别接"}),
        _EGO_INJECT,
    ])
    check("★ 主模型没被调用", seen["ai"] == 0, f"调用了 {seen['ai']} 次")
    check("★ 没有往群里发东西", not seen["sent"], seen["sent"])
    check("观察窗口被排上", rec is not None, "GROUP_OBSERVE 没记录")
    if rec:
        check("观察理由记下来了（能看出为什么等）",
              "别接" in str(rec.get("reason") or ""), rec.get("reason"))

    print("\n[2] 对照：同一个流程、同样有 ego inject，决策器说 inject → 必须开口")
    # 这组对照是判别力的来源：证明 [1] 的"没调用"不是流程在别处断了
    seen2, rec2 = _run([
        ("group_chat_decider", {"action": "inject", "content": "【群聊氛围】测试注入"}),
        _EGO_INJECT,
    ])
    check("★ 主模型被调用了", seen2["ai"] >= 1, f"只调用了 {seen2['ai']} 次")
    check("没排观察窗口", rec2 is None, rec2)

    print("\n[3] 决策器 skip + ego inject → 也不开口（回归：别破坏旧行为）")
    seen3, rec3 = _run([
        ("group_chat_decider", {"action": "skip", "reason": "[Decider v3] 无意义"}),
        _EGO_INJECT,
    ])
    check("主模型没被调用", seen3["ai"] == 0, f"调用了 {seen3['ai']} 次")
    check("skip 不排观察窗口", rec3 is None, rec3)

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())