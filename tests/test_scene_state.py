# -*- coding: utf-8 -*-
"""tests/test_scene_state.py —— 场景状态第 2 步（只落账，不进 prompt）

判据：
  [A] 语气统计只用可观测特征（夸骂词、字数），且跨轮累积
  [B] 关系快照只在外部给了亲密度时才存在，不自己猜
  [C] 她答应的事会记成"欠着"，说"发你了"之后变成"已了结"，同时留一条对账事实
  [D] 简报最多三行、120 字，是人话（不含字段名），没内容就不出现
  [E] 简报换着说法，避免自身变成新八股
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

from autonomy import ledger as L  # noqa: E402

PASS, FAIL = 0, 0
FAILURES = []
T = 1_800_000_000.0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def say(s, text, dt=0, **kw):
    return L.observe_user_message(s, dict({"sender": "1", "text": text, "ts": T + dt}, **kw), T + dt)


def reply(s, text, dt=0):
    return L.observe_bot_reply(s, {"scene": "private", "user_id": "1",
                                   "text": text, "ts": T + dt, "trace_id": "t%d" % dt}, T + dt)


def main():
    print("=== A) 语气统计 ===")
    s = say(None, "你真烦，滚")
    check("A1 负面词把语气压下去", s["tone"] < -0.2, s["tone"])
    check("A2 记下被骂", s["last_kind"] == "被骂", s["last_kind"])
    s2 = say(s, "谢谢你啊，真厉害", 10)
    check("A3 正面词把语气拉回来", s2["tone"] > s["tone"], (s["tone"], s2["tone"]))
    check("A4 记下被夸", s2["last_kind"] == "被夸", s2["last_kind"])
    s3 = say(None, "哦")
    check("A5 极短敷衍略降", s3["tone"] < 0, s3["tone"])
    check("A6 样本数累计", say(s3, "嗯", 5)["tone_n"] == 2)

    print("\n=== A2) 情绪会过期（回放发现：一次被骂，两小时内每条简报都在说被怼）===")
    sneg = say(None, "你个傻子")
    b_now = L.render_state_brief(sneg, T + 60)
    b_old = L.render_state_brief(sneg, T + 900)
    check("A2-1 刚被骂时提这件事", ("怼" in b_now or "呛" in b_now or "顶" in b_now), b_now)
    check("A2-2 十五分钟后不再说被骂",
          not any(k in b_old for k in ("怼", "呛", "顶")), b_old)

    print("\n=== B) 关系快照 ===")
    check("B1 没给亲密度就是 None", say(None, "在吗")["relation_level"] is None)
    check("B2 给了就存下来", say(None, "在吗", affection=85)["relation_level"] == 85)

    print("\n=== C) 欠着的事与对账 ===")
    s = reply(say(None, "帮我弄下图"), "行，等下给你发", 5)
    rows = s.get("unresolved") or []
    check("C1 承诺记成 open", len(rows) == 1 and rows[0]["status"] == "open", rows)
    s = reply(s, "发你了", 60)
    check("C2 说发你了之后转成 done",
          (s.get("unresolved") or [{}])[0].get("status") == "done", s.get("unresolved"))
    check("C3 同时留一条对账事实", len(s.get("facts") or []) == 1, s.get("facts"))

    print("\n=== C2) 承诺识别的精度（用真实语料校准过）===")
    for label, txt, want in (
            ("等下发你", "行，等下发你", True),
            ("回头帮你问问", "我回头帮你问问", True),
            ("自述明天再说（曾误报）", "那我也睡了，明天再说", False),
            ("回头再说（太泛）", "回头再说吧", False),
            ("等我看看（太泛，宁可漏）", "等我看看", False)):
        st = reply(say(None, "帮我看看这个"), txt, 5)
        got = any(r.get("status") == "open" for r in (st.get("unresolved") or []))
        check("C2-%s" % label, got == want, (got, txt))

    print("\n=== D) 简报是人话 ===")
    # 用"还欠着一件事"的状态测：这时候简报必须有内容
    s_open = reply(say(None, "帮我弄下图"), "行，等下给你发", 5)
    b = L.render_state_brief(s_open, T + 120)
    # 只有一条普通入站、没有语气变化/没等回复 → 不该有简报
    _quiet = say(None, "在吗")
    check("D0 无事可说时不出简报", L.render_state_brief(_quiet, T + 120) == "",
          L.render_state_brief(_quiet, T + 120))
    check("D1 非空", bool(b), b)
    check("D2 不超过三行", len(b.splitlines()) <= 3, b)
    check("D3 不超过 120 字", len(b) <= 120, len(b))
    bad = [w for w in ("tone", "unresolved", "facts", "relation_level", "mood") if w in b]
    check("D4 不含字段名", not bad, bad)
    check("D5 空状态返回空串", L.render_state_brief({}, T) == "")

    print("\n=== D2) 话头没接上（比答应过的事更可靠）===")
    s2 = say(None, "在吗")
    s2 = reply(s2, "刚翻到你说的那个梗", 0)
    b2 = L.render_state_brief(s2, T + 10 * 60)
    check("D2-1 一分钟后才提", "上一条" in b2 or "刚说过" in b2, b2)
    check("D2-2 不带出原文前半截以外的内容", len(b2) <= 120, len(b2))
    b3 = L.render_state_brief(s2, T + 20)
    check("D2-3 一分钟内不提（别把正常节奏说成没人理）",
          "上一条" not in b3 and "刚说过" not in b3, b3)

    print("\n=== E) 换说法 ===")
    s2 = say(None, "你真烦，滚")
    variants = {L.render_state_brief(s2, T + i * 600) for i in range(4)}
    check("E1 同一状态至少两种说法", len(variants) >= 2, variants)

    print("\n=== F) 私聊入站也要喂状态 ===")
    # 2026-11-24 发现：`message.in` 只有群聊发，私聊侧 last_user_text / 语气 /
    # 连发计数从来没更新过。这属于"两头都有、中间没接"的老毛病，所以单独钉一次。
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("F1 入站事件有两个发出点（群 + 私聊）",
          src.count('_emit_plugin_event("message.in"') >= 2,
          src.count('_emit_plugin_event("message.in"'))
    check("F2 私聊那份带 group_id 空值", '"group_id": "",' in src)
    check("F3 入站事件带好感度快照",
          src.count('"affection": _affection_score(') >= 2,
          src.count('"affection": _affection_score('))

    print("\n=== G) 出站文本提取（群聊是消息段）===")
    import bot_core as B
    check("G1 私聊 str 直接取", B._outbound_text({"message": "吃饭呢"}) == "吃饭呢")
    check("G2 群聊段拼文本",
          B._outbound_text({"message": [
              {"type": "text", "data": {"text": "在的"}},
              {"type": "image", "data": {"file": "x"}},
              {"type": "text", "data": {"text": "，等下给你发"}}]}) == "在的，等下给你发")
    check("G3 没有 message 返回空", B._outbound_text({}) == "")
    check("G4 长文裁到 400", len(B._outbound_text({"message": "字" * 900})) == 400)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
