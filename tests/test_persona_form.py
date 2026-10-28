# -*- coding: utf-8 -*-
"""persona 四参数 → 形式参数（1.5.9 §5.2 / 提交 11）。

方案口径：**0–1 的长期系数，情绪是短期偏移，两者相加作用到同一套形式参数。**
默认值按方案：sharpness 0.75 / curiosity 0.55 / warmth 0.35。

这个文件钉住：
1. `apply()` 只**叠**在情绪那份 form 上，不删键、不改语义。
2. 三个系数各自单调：调高 → 对应概率/分档单调变化（方案 12.1 第 8 条的验收方式）。
3. 全 0 时是恒等变换（可回退：调坏就归零）。
4. 默认值下三个出口都有**轻微但不为零**的效果 —— 默认要能感觉到，不能是噪音。
5. `decorate()` 三种微调各自可触发；对方在提问时不追加追问。
6. 真跑一轮群流水线：默认值下这句会被形式微调，且日志写明。

运行：python tests/test_persona_form.py
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
import persona_form  # noqa: E402

GID = "_test_persona_form_"
SENDER = "10086001"
_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


class _Rng:
    """随机数恒 0.0：概率判定全命中，取值取第 0 个。"""

    def __init__(self, value=0.0):
        self.value = value

    def random(self):
        return self.value

    def choice(self, seq):
        return seq[0] if seq else None


_NEUTRAL = {"valence": 0.0, "arousal": 0.0, "budget_shift": 0,
            "delay_scale": 1.0, "emoji_bonus": 0.0,
            "soft_punct": False, "filler_pool": "mid"}

_ZERO = {"persona_sharpness": 0.0, "persona_curiosity": 0.0, "persona_warmth": 0.0}
_DEFAULT = {}          # 走 DEFAULT 值（0.75 / 0.55 / 0.35）


def test_apply_zero_floor():
    """0 不是"关"，是这一维最低：语气池走 low、不注入、不追问、不加表情。"""
    print("\n[1] 三系数全 0 → 各维落到最低档（不是恒等，是地板）")
    f = persona_form.apply(_NEUTRAL, _ZERO)
    check("emoji_bonus 不加", f["emoji_bonus"] == 0.0, f["emoji_bonus"])
    check("soft_punct 不被强制", f["soft_punct"] is False, f["soft_punct"])
    check("语气池落 low（最冷）", f["filler_pool"] == "low", f["filler_pool"])
    check("不注入语气词", f["filler_prob"] == 0.0, f["filler_prob"])
    check("不追问", f["followup_prob"] == 0.0, f["followup_prob"])


def test_apply_defaults():
    print("\n[2] 方案默认值 0.75 / 0.55 / 0.35 → 轻微但不为零")
    f = persona_form.apply(_NEUTRAL, _DEFAULT)
    check("sharpness 0.75 → high 语气池", f["filler_pool"] == "high", f["filler_pool"])
    check("语气词概率 ≈0.135（每 7~8 条一次，真机回测后下调）",
          0.1 <= f["filler_prob"] <= 0.2, f["filler_prob"])
    check("追问概率 ≈0.165（每 6 条一次）",
          0.1 <= f["followup_prob"] <= 0.25, f["followup_prob"])
    check("表情加成 ≈0.07", 0.05 <= f["emoji_bonus"] <= 0.1, f["emoji_bonus"])
    check("warmth 0.35 不强制软化", f["soft_punct"] is False, f["soft_punct"])
    check("软化概率 ≈0.30", 0.2 <= f["soft_prob"] <= 0.4, f["soft_prob"])


def test_monotonic():
    print("\n[3] 单调性：系数调高 → 对应参数单调上升（方案验收第 8 条）")
    prev = None
    ok = True
    for s in (0.0, 0.2, 0.5, 0.75, 1.0):
        f = persona_form.apply(_NEUTRAL, {"persona_sharpness": s})
        if prev is not None and f["filler_prob"] < prev:
            ok = False
        prev = f["filler_prob"]
    check("sharpness → 语气词概率不降", ok, prev)

    prev, ok = None, True
    for c in (0.0, 0.2, 0.55, 0.8, 1.0):
        f = persona_form.apply(_NEUTRAL, {"persona_curiosity": c})
        if prev is not None and f["followup_prob"] < prev:
            ok = False
        prev = f["followup_prob"]
    check("curiosity → 追问概率不降", ok, prev)

    prev, ok = None, True
    for w in (0.0, 0.2, 0.35, 0.6, 1.0):
        f = persona_form.apply(_NEUTRAL, {"persona_warmth": w})
        if prev is not None and f["emoji_bonus"] < prev:
            ok = False
        prev = f["emoji_bonus"]
    check("warmth → 表情加成不降", ok, prev)

    check("越界值被夹住",
          persona_form.apply(_NEUTRAL, {"persona_warmth": 9})["emoji_bonus"]
          == persona_form.apply(_NEUTRAL, {"persona_warmth": 1.0})["emoji_bonus"])


def test_decorate_identity_and_all():
    print("\n[4] decorate：零参数原样，默认参数会动")
    out0 = persona_form.decorate("没事。", persona_form.apply(_NEUTRAL, _ZERO), "今天考试挂了两科")
    check("全 0 + 情绪不软 → 原样", out0 == "没事。", out0)

    form = persona_form.apply(_NEUTRAL, _DEFAULT)
    old = persona_form.random
    persona_form.random = _Rng(0.0)
    try:
        out = persona_form.decorate("没事。", form, "昨天考试挂了两科，回家路上还下雨了")
    finally:
        persona_form.random = old
    check("句首语气词（默认 sharpness 生效）", out.startswith("啧"), out)
    check("追加短追问（默认 curiosity 生效）", "然后呢" in out, out)


def test_no_followup_on_judgement():
    """真机 217 条回测的反例：判断/答复类陈述不该被追一句"后来呢"。"""
    print("\n[5b] 判断类陈述不追问（真机反例）")
    form = persona_form.apply(_NEUTRAL, {"persona_curiosity": 1.0,
                                         "persona_sharpness": 0.0})
    for trig in ("铁路学院啊，还行吧", "记不太清了", "图片打不开"):
        old = persona_form.random
        persona_form.random = _Rng(0.0)
        try:
            out = persona_form.decorate("行吧", form, trig)
        finally:
            persona_form.random = old
        check(f"不追问: {trig}", "后来呢" not in out and "然后呢" not in out, out)


def test_no_followup_on_question():
    print("\n[5] 对方在提问时不追问（已经在问了，不需要再追）")
    # sharpness 归零：否则默认 0.75 会给句首加"啧"，测的就不是追问了
    form = persona_form.apply(_NEUTRAL, {"persona_curiosity": 1.0,
                                         "persona_sharpness": 0.0})
    old = persona_form.random
    persona_form.random = _Rng(0.0)
    try:
        out = persona_form.decorate("28", form, "今天几号？")
    finally:
        persona_form.random = old
    check("没有追加追问", "然后呢" not in out, out)


# ══════════════════════════════════════════════════════════════
# 真跑一轮群流水线
# ══════════════════════════════════════════════════════════════

class _Run:
    def __init__(self, trigger, reply, cfg=None):
        self.trigger = trigger
        self.reply = reply
        self.cfg = cfg or {}
        self.sent = []
        self.logs = []

    def __enter__(self):
        self._orig = (bot_core.call_deepseek, bot_core.smart_send_group,
                      bot_core.PLUGIN_OK, bot_core._dispatch_typed_before_reply,
                      bot_core.startup_time, bot_core.MEME_OK, bot_core.try_send_meme,
                      bot_core.log)
        self._orig_cfg = dict(bot_core._config)
        self._orig_rnd = persona_form.random

        def fake_ai(user_msg, images=None, *a, **kw):
            return self.reply

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
        for k, v in self.cfg.items():
            bot_core._config[k] = v
        persona_form.random = _Rng(0.0)
        return self

    def __exit__(self, *exc):
        (bot_core.call_deepseek, bot_core.smart_send_group, bot_core.PLUGIN_OK,
         bot_core._dispatch_typed_before_reply, bot_core.startup_time,
         bot_core.MEME_OK, bot_core.try_send_meme, bot_core.log) = self._orig
        persona_form.random = self._orig_rnd
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


def test_pipeline_zero_is_unchanged():
    print("\n[6] 真跑：三系数归零 → 发出去的字与模型原文一致")
    with _Run("今天考试挂了两科", "没事。", cfg=_ZERO) as r:
        r.run()
        check("发了一条", len(r.sent) == 1, r.sent)
        check("★ 一个字都没改", r.sent and r.sent[0] == "没事。", r.sent)


def test_pipeline_defaults_take_effect():
    print("\n[7] 真跑：默认系数 → 同一条回复被形式微调")
    with _Run("昨天考试挂了两科，回家路上还下雨了", "没事。",
              cfg={"persona_sharpness": 0.75, "persona_curiosity": 0.55,
                   "persona_warmth": 0.35}) as r:
        r.run()
        txt = r.sent[0] if r.sent else ""
        check("发了", bool(txt), r.sent)
        check("★ 句首语气词", txt.startswith("啧"), txt)
        check("★ 追加追问（叙事触发）", "然后呢" in txt, txt)
        check("日志记了微调", any("[persona]" in l for l in r.logs), r.logs[-3:])


def main():
    test_apply_zero_floor()
    test_apply_defaults()
    test_monotonic()
    test_decorate_identity_and_all()
    test_no_followup_on_judgement()
    test_no_followup_on_question()
    test_pipeline_zero_is_unchanged()
    test_pipeline_defaults_take_effect()

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
