# -*- coding: utf-8 -*-
"""发言闸（1.5.9 §3.8 / 提交 6，2026-10-28）。

方案称它是"所有改动里性价比最高的一条"，因为它省的是**没说话的那次调用**：
没命中档位就一次模型都不调。

这个文件钉住：
1. 四档判据（1 只认点名 / 2 关键词 / 3 关键词+概率 / 4 全响应）。
2. **被点名一律放行**，任何频控都拦不住（"叫它不应"比话多更伤）。
3. 发言率上限：窗口内 回复/消息 > N/100 → 跳过；样本 <20 不判。
4. 连说自限：连说 N 条后 5 分钟内静默；超时放行。
5. 真跑群流水线：tier=1 + 非触发消息 → 不调模型、不发消息，日志写 `[跳过:档1…]`；
   被 @ 的消息照常进决策器。

运行：python tests/test_speak_gate.py
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
import speak_gate  # noqa: E402

GID = "_test_speak_gate_"
SENDER = "10086001"
_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def test_config():
    print("\n[1] 配置解析与夹值")
    c = speak_gate.get_config({})
    check("默认档 3", c["tier"] == 3, c)
    check("默认概率 0.15", c["probability"] == 0.15, c)
    check("默认上限 10/100", c["max_per_100"] == 10, c)
    check("默认连说 2", c["consecutive_limit"] == 2, c)
    check("档位越界夹住", speak_gate.get_config({"speak_gate_tier": 9})["tier"] == 4)
    check("概率越界夹住", speak_gate.get_config({"speak_probability": -1})["probability"] == 0.0)


def test_tiers():
    print("\n[2] 四档判据")
    b = {"text": "今天天气不错", "forced": False}
    check("档 1 非触发 → 跳过", speak_gate.allow(b, {"speak_gate_tier": 1})[0] is False,
          speak_gate.allow(b, {"speak_gate_tier": 1}))
    check("档 2 无话头 → 跳过",
          speak_gate.allow(b, {"speak_gate_tier": 2})[0] is False)
    hit = {"text": "有人知道这个怎么办吗", "forced": False}
    check("档 2 命中话头 → 放行",
          speak_gate.allow(hit, {"speak_gate_tier": 2})[0] is True)
    check("档 3 概率命中（rnd=0）→ 放行",
          speak_gate.allow(b, {"speak_gate_tier": 3, "speak_probability": 0.15},
                           rnd=0.0)[0] is True)
    check("档 3 概率未中（rnd=0.99）→ 跳过",
          speak_gate.allow(b, {"speak_gate_tier": 3, "speak_probability": 0.15},
                           rnd=0.99)[0] is False)
    check("档 4 全放行", speak_gate.allow(b, {"speak_gate_tier": 4})[0] is True)
    check("★ 被点名在档 1 也放行",
          speak_gate.allow({"text": "随便说点什么", "forced": True},
                           {"speak_gate_tier": 1})[0] is True)
    # 真机回放发现：戳一戳批次 forced=False，会被当普通消息挡掉 85%
    check("★ 戳一戳算被点名",
          speak_gate.allow({"text": "（戳了戳你，这是第 1 次）", "source": "poke"},
                           {"speak_gate_tier": 1})[0] is True)
    # 真机话头：短句里的第二人称（"你好"、"你几岁"这类）
    check("短句带第二人称 → 有话头",
          speak_gate.allow({"text": "那你今年几岁"}, {"speak_gate_tier": 2})[0] is True)
    check("长句带第二人称 → 不算话头（那是两个人聊）",
          speak_gate.allow({"text": "你们昨天说的那个事后来处理好了大家都挺关心的"},
                           {"speak_gate_tier": 2})[0] is False)


def test_rate_limit():
    print("\n[3] 发言率上限")
    cfg = {"speak_gate_tier": 4, "speak_rate_max_per_100": 10}
    check("20 条消息回 1 次 → 放行",
          speak_gate.allow({"text": "x"}, cfg,
                           stats={"in": 20, "out": 1})[0] is True)
    check("★ 100 条消息回 20 次 → 跳过",
          speak_gate.allow({"text": "x"}, cfg,
                           stats={"in": 100, "out": 20})[0] is False,
          speak_gate.allow({"text": "x"}, cfg, stats={"in": 100, "out": 20}))
    check("样本不足（10 条回 5 次）不判",
          speak_gate.allow({"text": "x"}, cfg,
                           stats={"in": 10, "out": 5})[0] is True)
    check("★ 被点名不受发言率限制",
          speak_gate.allow({"text": "x", "forced": True}, cfg,
                           stats={"in": 100, "out": 20})[0] is True)


def test_consecutive():
    print("\n[4] 连说自限")
    cfg = {"speak_gate_tier": 4, "consecutive_reply_limit": 2}
    check("连说 2 条 + 刚说过 → 跳过",
          speak_gate.allow({"text": "x"}, cfg,
                           stats={"consecutive": 2, "last_out_ago": 30})[0] is False)
    check("冷却已过（>300s）→ 放行",
          speak_gate.allow({"text": "x"}, cfg,
                           stats={"consecutive": 2, "last_out_ago": 400})[0] is True)
    check("只说了 1 条 → 放行",
          speak_gate.allow({"text": "x"}, cfg,
                           stats={"consecutive": 1, "last_out_ago": 5})[0] is True)


# ══════════════════════════════════════════════════════════════
# 真跑群流水线
# ══════════════════════════════════════════════════════════════

class _Run:
    def __init__(self, text, forced=False, cfg=None):
        self.text = text
        self.forced = forced
        self.cfg = cfg or {}
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
            return "嗯"

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
        bot_core._config["persona_sharpness"] = 0.0
        bot_core._config["persona_curiosity"] = 0.0
        bot_core._config["persona_warmth"] = 0.0
        for k, v in self.cfg.items():
            bot_core._config[k] = v
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
        bot_core._pipeline_upsert(GID, sender=SENDER, text=self.text,
                                  forced=self.forced)
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


def test_pipeline_blocked_zero_token():
    print("\n[5] 真跑：tier=1 + 非触发 → 零模型调用、不发消息")
    with _Run("今天天气不错", forced=False, cfg={"speak_gate_tier": 1}) as r:
        r.run()
        check("★ 没调模型", r.calls == 0, r.calls)
        check("★ 没发消息", not r.sent, r.sent)
        hit = [l for l in r.logs if "[跳过:" in l and "档1" in l]
        check("★ 日志写明档位", bool(hit), [l for l in r.logs if "跳过" in l])


def test_pipeline_forced_passes():
    print("\n[6] 真跑：被 @ + tier=1 → 照常回（任何频控拦不住）")
    with _Run("小a 在吗", forced=True, cfg={"speak_gate_tier": 1}) as r:
        r.run()
        check("调了模型", r.calls == 1, r.calls)
        check("发出去了", len(r.sent) == 1, r.sent)


def main():
    test_config()
    test_tiers()
    test_rate_limit()
    test_consecutive()
    test_pipeline_blocked_zero_token()
    test_pipeline_forced_passes()

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
