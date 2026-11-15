# -*- coding: utf-8 -*-
"""tests/test_proactivity_arbitrator.py —— 仲裁层（离线，注入假模型）

判据：
  [A] 预算档位 0..3 映射、越界钳制
  [B] 硬闸门：总开关 / 单行为 / 预算耗尽 / 冷却 / 去重 / 静默时段 / 撤回未交棒
  [C] 规则拦下时模型一次都不调（混合≠让模型推翻规则）
  [D] 优先级仲裁：多条同时命中只让最高一条进模型，只发一个
  [E] 模型输出容错：```json 包裹 / 多余文字 / 非法 JSON / 未知 act
  [F] 模型判 skip / 模型挂掉 → 都不发，且留痕写明原因
  [G] say 时返回文案与原始 observation
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

from harness.proactivity import arbitrator as A  # noqa: E402

PASS, FAIL = 0, 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def obs(kind="no_reply", priority=30, scene="private", target="1000000006"):
    return {"kind": kind, "scene": scene, "target": target, "since": 1000.0,
            "elapsed_s": 600.0, "dedupe_key": "%s:%s" % (kind, target),
            "evidence": {"bot_text": "醒得挺早啊你"}, "priority": priority}


def base_cfg(**kw):
    c = {"proactivity_enable": True, "proactivity_budget_level": 1,
         "proactivity_quiet_hours": "", "recall_feedback_by": "main"}
    c.update(kw)
    return c


def main():
    now = 2000.0
    print("=== A) 档位 ===")
    check("0 档 per_hour=0", A.level_of(base_cfg(proactivity_budget_level=0))["per_hour"] == 0)
    check("1 档 per_hour=1", A.level_of(base_cfg())["per_hour"] == 1)
    check("3 档 per_hour=6", A.level_of(base_cfg(proactivity_budget_level=3))["per_hour"] == 6)
    check("越界钳到 3", A.level_of(base_cfg(proactivity_budget_level=99))["level"] == 3)
    check("脏值退回默认 1", A.level_of(base_cfg(proactivity_budget_level="x"))["level"] == 1)

    print("\n=== B) 硬闸门 ===")
    def gate(cfg=None, **kw):
        p = dict(cfg=cfg or base_cfg(), now=now, fired_recently=False,
                 session_last_sent=0.0, scene_sent_last_hour=0)
        p.update(kw)
        return A.gate(obs(), **p)
    check("默认通过", gate()["ok"] is True, gate())
    check("总开关关", gate(cfg=base_cfg(proactivity_enable=False))["ok"] is False)
    check("单行为关", gate(cfg=base_cfg(proactivity_no_reply_enable=False))["ok"] is False)
    check("预算档位=0", gate(cfg=base_cfg(proactivity_budget_level=0))["ok"] is False)
    check("预算耗尽", gate(scene_sent_last_hour=1)["ok"] is False)
    check("会话冷却中", gate(session_last_sent=now - 10)["ok"] is False)
    check("去重命中", gate(fired_recently=True)["ok"] is False)
    # 静默时段：用本地时间造一个凌晨 3 点
    night = time.mktime((2026, 11, 6, 3, 0, 0, 0, 0, -1))
    check("凌晨 3 点在 0-7 静默段",
          A.in_quiet_hours(night, base_cfg(proactivity_quiet_hours="0-7")) is True)
    noon = time.mktime((2026, 11, 6, 12, 0, 0, 0, 0, -1))
    check("中午不静默", A.in_quiet_hours(noon, base_cfg(proactivity_quiet_hours="0-7")) is False)
    check("跨零点 23-7 也认",
          A.in_quiet_hours(night, base_cfg(proactivity_quiet_hours="23-7")) is True)
    check("撤回未交棒被硬拦",
          A.gate(obs("recall", 70), cfg=base_cfg(proactivity_recall_enable=True),
                 now=now, fired_recently=False, session_last_sent=0.0,
                 scene_sent_last_hour=0)["ok"] is False)
    check("交棒后且开关开才放行",
          A.gate(obs("recall", 70),
                 cfg=base_cfg(proactivity_recall_enable=True,
                              recall_feedback_by="proactivity"),
                 now=now, fired_recently=False, session_last_sent=0.0,
                 scene_sent_last_hour=0)["ok"] is True)

    print("\n=== C) 规则拦下不调模型 ===")
    called = {"n": 0}
    def chat(messages, max_tokens=160):
        called["n"] += 1
        return '{"act":"say","text":"在吗"}'
    r = A.arbitrate([obs()], cfg=base_cfg(proactivity_enable=False), now=now,
                    is_fired=lambda k: False, last_sent=lambda s, t: 0,
                    hourly_count=lambda s: 0, chat_fn=chat)
    check("总开关关时不发", r is None, r)
    check("模型一次都没调", called["n"] == 0, called)

    print("\n=== D) 优先级仲裁 ===")
    seen = []
    def chat2(messages, max_tokens=160):
        seen.append(messages)
        return '{"act":"say","text":"补一句"}'
    batch = [obs("no_reply", 30), obs("pending_topic", 50), obs("at_dropped", 60)]
    r2 = A.arbitrate(batch, cfg=base_cfg(proactivity_budget_level=3), now=now,
                     is_fired=lambda k: False, last_sent=lambda s, t: 0,
                     hourly_count=lambda s: 0, chat_fn=chat2)
    check("只调一次模型", len(seen) == 1, len(seen))
    check("选中的是最高优先级 at_dropped", r2 and r2["obs"]["kind"] == "at_dropped", r2)

    print("\n=== E) 模型输出容错 ===")
    check("标准 JSON", A.parse_model_decision('{"act":"say","text":"在吗"}')["act"] == "say")
    check("```json 包裹",
          A.parse_model_decision('```json\n{"act":"say","text":"在吗"}\n```')["text"] == "在吗")
    check("前后有多余文字",
          A.parse_model_decision('好的：{"act":"say","text":"嗯"} 就这样')["text"] == "嗯")
    check("非法 JSON → skip", A.parse_model_decision("我随便说说")["act"] == "skip")
    check("未知 act → skip", A.parse_model_decision('{"act":"shout","text":"喂"}')["act"] == "skip")
    check("skip 时 text 强制为空",
          A.parse_model_decision('{"act":"skip","text":"不该发"}')["text"] == "")

    print("\n=== F) 模型判 skip / 挂掉 → 不发且留痕 ===")
    logs = []
    fired = []
    def log(m):
        logs.append(m)
    r3 = A.arbitrate([obs()], cfg=base_cfg(), now=now,
                     is_fired=lambda k: False, last_sent=lambda s, t: 0,
                     hourly_count=lambda s: 0,
                     chat_fn=lambda m, max_tokens=160: '{"act":"skip","reason":"不值得"}',
                     log=log, mark_fired=lambda dk, ts: fired.append(dk))
    check("skip 不发", r3 is None, r3)
    check("跳过留痕带原因", any("模型判定" in x and "不值得" in x for x in logs), logs)
    check("skip 也记去重（防 at_dropped 每 30s 重刷）",
          fired == [obs()["dedupe_key"]], fired)

    def boom(messages, max_tokens=160):
        raise RuntimeError("模型挂了")
    r4 = A.arbitrate([obs()], cfg=base_cfg(), now=now,
                     is_fired=lambda k: False, last_sent=lambda s, t: 0,
                     hourly_count=lambda s: 0, chat_fn=boom, log=log)
    check("模型挂掉保守跳过", r4 is None, r4)
    check("没有模型入口也跳过",
          A.model_decide(obs(), None)["act"] == "skip")

    print("\n=== H) 戳一戳作为模型可选动作 ===")
    check("ALLOWED_ACTS 含 poke", "poke" in A.ALLOWED_ACTS, A.ALLOWED_ACTS)
    check("私聊 poke 保留且 text 强制为空",
          A.parse_model_decision('{"act":"poke","text":"不该发"}', scene="private")
          == {"act": "poke", "text": "", "reason": ""})
    check("群聊 poke 硬拦（QQ 戳一戳只支持私聊）",
          A.parse_model_decision('{"act":"poke","text":""}', scene="group")["act"] == "skip")
    check("⑤ 冷场默认开", A.BEHAVIORS["idle_contact"]["enabled"] is True)
    check("⑤ 可被单行为开关关掉",
          A.gate(obs("idle_contact", 20),
                 cfg=base_cfg(proactivity_idle_contact_enable=False),
                 now=now, fired_recently=False, session_last_sent=0.0,
                 scene_sent_last_hour=0)["ok"] is False)
    logs2 = []
    r6 = A.arbitrate([obs("idle_contact", 20)], cfg=base_cfg(), now=now,
                     is_fired=lambda k: False, last_sent=lambda s, t: 0,
                     hourly_count=lambda s: 0,
                     chat_fn=lambda m, max_tokens=160:
                     '{"act":"poke","text":"","reason":"很久没互动"}',
                     log=lambda m: logs2.append(m))
    check("★ 模型选 poke 时仲裁返回 act=poke（不发文字）",
          r6 and r6["act"] == "poke" and r6["text"] == "", r6)
    check("poke 也算一次主动动作（过预算/冷却）", bool(r6), logs2)

    print("\n=== I) 拟人：学 MaiBot 的三段式 ===")
    sp = A._system_prompt()
    check("三段都在（是谁 / 什么时候说 / 什么味）",
          "平时在网上跟人聊天" in sp and "不想聊的时候就不回" in sp
          and "说话比较随意" in sp, sp[:220])
    check("有具体语感，不是形容词堆砌",
          all(x in sp for x in ("啊？", "刚看到", "刚刷到个视频")), sp[:300])
    check("不刻意：不设定场景、不教演戏",
          not any(x in sp for x in ("手上有别的事", "允许话说一半", "你不是助手")), sp[:300])
    check("人格短（不堆长清单）", len(sp) < 700, len(sp))
    check("五个预设",
          {"xiaoa", "tsundere", "entertain", "gentle", "sharp"} <= set(A._PERSONAS),
          sorted(A._PERSONAS))
    check("默认小a", "小a" in str(A._persona_of({}).get("label", "")), A._persona_of({}))
    check("男娘傲娇仍可选",
          "像女孩子" in A._persona_of({"proactivity_persona": "tsundere"})["personality"])
    check("切毒舌向生效",
          "嘴上不饶人" in A._persona_of({"proactivity_persona": "sharp"})["personality"])
    check("切娱乐向生效",
          "B 站评论区" in A._persona_of({"proactivity_persona": "entertain"})["reply"])
    check("情境只写发生什么，不写死台词",
          not any("像这样：" in v for v in A._KIND_VOICE.values()),
          [k for k, v in A._KIND_VOICE.items() if "像这样：" in v])
    check("未知人格回落小a",
          A._persona_of({"proactivity_persona": "不存在"}) is A._PERSONAS["xiaoa"])

    import config as _C
    _orig_lc = _C.load_config
    try:
        _C.load_config = lambda: {"proactivity_persona": "gentle",
                                  "proactivity_personality": "测试人格",
                                  "proactivity_behavior_style": "测试策略",
                                  "proactivity_reply_style": "测试风格"}
        sp2 = A._system_prompt()
        check("单独填的一段会覆盖预设",
              all(x in sp2 for x in ("测试人格", "测试策略", "测试风格")), sp2[:200])
        _C.load_config = lambda: {"proactivity_style_pool": ["换个味道A"],
                                  "proactivity_style_prob": 1.0}
        check("概率=1 必定注入备用风格", "换个味道A" in A._system_prompt())
        _C.load_config = lambda: {"proactivity_style_pool": ["换个味道B"],
                                  "proactivity_style_prob": 0.0}
        check("概率=0 不注入", "换个味道B" not in A._system_prompt())
    finally:
        _C.load_config = _orig_lc

    _p = A.build_prompt({"kind": "autonomy_done", "scene": "private",
                         "target": "1", "elapsed_s": 90,
                         "evidence": {"mode": "analyze", "job": "视频解析",
                                      "tail": "弹钢琴"}}, ("say", "skip"))
    _c = _p[1]["content"]
    check("观测写成人话，不是数据表", "观察到" not in _c and "你现在在私聊里" in _c, _c[:80])
    check("工程字段被翻成中文", "mode" not in _c and "看/理解" in _c, _c[:160])
    check("过去了多久用分钟说", "过去 1 分钟" in _c, _c)

    print("\n=== G) say 返回动作 ===")
    r5 = A.arbitrate([obs()], cfg=base_cfg(), now=now,
                     is_fired=lambda k: False, last_sent=lambda s, t: 0,
                     hourly_count=lambda s: 0, chat_fn=chat)
    check("返回 text", r5 and r5["text"] == "在吗", r5)
    check("带原始 observation", r5 and r5["obs"]["kind"] == "no_reply", r5)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
