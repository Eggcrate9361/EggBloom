# -*- coding: utf-8 -*-
"""反问闸（1.5.9 §3.6 / 提交 5 的可选第三步，2026-10-28 按方案归位）。

按方案：`reply_question_guard` 管的是**末句问号**，值是 off / regen / regen_private，
默认 off；**不剪文本**，只决定要不要重生一次。

这个文件钉住：
1. 三种模式与容错（旧布尔值一律按 off，不能让它突然开始花重生钱）。
2. 末句问号的判定：尾部装饰、表情标记、全角问号都要认。
3. `[口癖]` 观测行只在"有问题号或收尾腔"时产出。
4. 真跑一轮群流水线：mode=regen 且首条以问号结尾 → 只重生一次，发出的是重生那条；
   mode=off → 一次都不重生。

运行：python tests/test_reply_gate.py
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
import reply_gate  # noqa: E402

GID = "_test_reply_gate_"
SENDER = "10086001"
_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def test_guard_mode():
    print("\n[1] 模式解析与容错")
    check("字符串 regen", reply_gate.guard_mode({"reply_question_guard": "regen"}) == "regen")
    check("字符串 regen_private",
          reply_gate.guard_mode({"reply_question_guard": "regen_private"}) == "regen_private")
    check("默认 off", reply_gate.guard_mode({}) == "off")
    check("★ 旧布尔 true → off（含义已变，不许自动开重生）",
          reply_gate.guard_mode({"reply_question_guard": True}) == "off")
    check("拼错 → off", reply_gate.guard_mode({"reply_question_guard": "regen2"}) == "off")


def test_ends_with_question():
    print("\n[2] 末句问号判定")
    check("普通问号", reply_gate.ends_with_question("你吃了吗？") is True)
    check("半角问号", reply_gate.ends_with_question("吃了?") is True)
    check("尾部装饰", reply_gate.ends_with_question("吃了？") is True)
    check("表情标记在最后", reply_gate.ends_with_question("吃了？[表情:笑]") is True)
    check("句号结尾", reply_gate.ends_with_question("吃了。") is False)
    check("感叹号结尾", reply_gate.ends_with_question("吃了！") is False)
    check("空串", reply_gate.ends_with_question("") is False)
    check("中间有问号但结尾没有",
          reply_gate.ends_with_question("你不是说你吃了吗。") is False)


def test_habit_line():
    print("\n[3] [口癖] 观测行")
    line = reply_gate.habit_line("你吃了吗？我先去忙？")
    check("有两个问号", "问号=2" in line, line)
    check("带原文", "原文=" in line, line)
    check("无口癖 → 空串", reply_gate.habit_line("吃了。") == "",
          reply_gate.habit_line("吃了。"))
    tail = reply_gate.habit_line("那我先睡了，早点睡")
    check("收尾腔被记下", "收尾=1" in tail, tail)


def test_should_regen():
    print("\n[4] 要不要重生")
    q = "你吃了吗？"
    check("off → 不重生", reply_gate.should_regen("off", q) is False)
    check("regen + 问号 → 重生", reply_gate.should_regen("regen", q) is True)
    check("regen + 陈述 → 不重生", reply_gate.should_regen("regen", "吃了。") is False)
    check("regen_private + 群聊未被 @ → 不重生",
          reply_gate.should_regen("regen_private", q) is False)
    check("regen_private + 群聊被 @ → 重生",
          reply_gate.should_regen("regen_private", q, addressed=True) is True)
    check("regen_private + 私聊 → 重生",
          reply_gate.should_regen("regen_private", q, is_private=True) is True)


# ══════════════════════════════════════════════════════════════
# 真跑一轮群流水线
# ══════════════════════════════════════════════════════════════

class _Run:
    """第一次生成返回 reply1，被要求重生时返回 reply2。"""

    def __init__(self, trigger, reply1, reply2=None, mode="off"):
        self.trigger = trigger
        self.replies = [reply1] + ([reply2] if reply2 is not None else [])
        self.mode = mode
        self.calls = 0
        self.sent = []
        self.logs = []

    def __enter__(self):
        self._orig = (bot_core.call_deepseek, bot_core.smart_send_group,
                      bot_core.PLUGIN_OK, bot_core._dispatch_typed_before_reply,
                      bot_core.startup_time, bot_core.MEME_OK, bot_core.try_send_meme,
                      bot_core.log)
        self._orig_cfg = dict(bot_core._config)

        def fake_ai(user_msg, images=None, *a, **kw):
            self.calls += 1
            i = min(self.calls - 1, len(self.replies) - 1)
            return self.replies[i]

        def fake_send(gid, text, *a, **kw):
            self.sent.append(text)
            return True

        bot_core.call_deepseek = fake_ai
        bot_core.smart_send_group = fake_send
        bot_core.PLUGIN_OK = False
        bot_core._dispatch_typed_before_reply = lambda *a, **kw: []
        bot_core.startup_time = time.time() - 10 ** 6
        bot_core.MEME_OK = False
        bot_core.try_send_meme = lambda gid, txt, prob_bonus=0.0: None
        bot_core.log = lambda m: self.logs.append(str(m))
        bot_core._config["quiet_hours"] = ""
        bot_core._config["reply_reaction_delay"] = 0
        bot_core._config["meme_enable"] = False
        bot_core._config["reply_question_guard"] = self.mode
        # persona 三系数全归零：否则默认 0.75 有 22.5% 概率给句首加"啧"，
        # 这条用例就变成"跑几次错一次"的偶发失败
        bot_core._config["persona_curiosity"] = 0.0
        bot_core._config["persona_warmth"] = 0.0
        bot_core._config["persona_sharpness"] = 0.0
        return self

    def __exit__(self, *exc):
        (bot_core.call_deepseek, bot_core.smart_send_group, bot_core.PLUGIN_OK,
         bot_core._dispatch_typed_before_reply, bot_core.startup_time,
         bot_core.MEME_OK, bot_core.try_send_meme, bot_core.log) = self._orig
        bot_core._config.clear()
        bot_core._config.update(self._orig_cfg)
        _clean()
        return False

    def run(self):
        _clean()
        bot_core._pipeline_upsert(GID, sender=SENDER, text=self.trigger, forced=True)
        buf = bot_core.GROUP_MSG_BUFFER.get(GID) or {}
        if buf.get("timer"):
            buf["timer"].cancel()
        bot_core.process_buffered_message(GID)
        return self


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
    for d in (bot_core.GROUP_SESSION, bot_core.GROUP_PIPELINE,
              bot_core.GROUP_BOT_REPLIES, bot_core.GROUP_RECENT_MSGS,
              bot_core.GROUP_TIMELINE):
        d.pop(GID, None)


def test_pipeline_off_records_only():
    print("\n[5] 真跑：mode=off → 不重生，只记 [口癖]")
    with _Run("今天吃啥", "你吃了吗？", mode="off") as r:
        r.run()
        check("只调一次模型", r.calls == 1, r.calls)
        check("发出去的是原句", r.sent and r.sent[0] == "你吃了吗？", r.sent)
        check("有 [口癖] 日志", any("[口癖]" in l for l in r.logs),
              [l for l in r.logs if "口癖" in l])


def test_pipeline_regen_once():
    print("\n[6] 真跑：mode=regen + 末句问号 → 重生一次，发重生那条")
    with _Run("今天吃啥", "你吃了吗？", reply2="吃了，面条。", mode="regen") as r:
        r.run()
        check("★ 重生了一次（共两次生成）", r.calls == 2, r.calls)
        check("★ 发的是重生那条", r.sent and r.sent[0] == "吃了，面条。", r.sent)
        check("日志有 [反问闸]", any("[反问闸]" in l for l in r.logs),
              [l for l in r.logs if "反问闸" in l])


def test_pipeline_statement_no_regen():
    print("\n[7] 真跑：陈述句结尾 → 即使 mode=regen 也不重生")
    with _Run("今天吃啥", "吃了。", mode="regen") as r:
        r.run()
        check("只调一次模型", r.calls == 1, r.calls)
        check("照发", r.sent and r.sent[0] == "吃了。", r.sent)


def main():
    test_guard_mode()
    test_ends_with_question()
    test_habit_line()
    test_should_regen()
    test_pipeline_off_records_only()
    test_pipeline_regen_once()
    test_pipeline_statement_no_regen()

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
