# -*- coding: utf-8 -*-
"""tests/test_grounding.py —— 事实围栏（2026-11-04）

用例全部取自**真机日志**，不是编的：
  16:28 「刚还骂我记性短呢」        —— 那句是它自己说的
  16:45 「刚说别找你」              —— 前面根本没有
  16:45 「一直没听到你那边的声响」  —— 纯文字 bot 声称能听见
  15:35 「十五个小时了」            —— 时间对不上（2026-11-05 起归 [I] 管）

同时必须证明**不误杀**：「没听到」「听不见」是诚实表达，
普通闲聊（「今天好累」「吃了吗」）一句都不能删。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import grounding as G  # noqa: E402

PASS = 0
FAIL = 0
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


def main():
    print("[A] 捏造感官：一律删（你没有耳朵）")
    ev = ["上课呢高数", "讲微分呢", "不跟你说这个"]
    out, dropped = G.sanitize("我躺下没多久又醒了，一直没听到你那边的声响", ev)
    check("★ 感官句被删", out == "" and dropped, (out, dropped))

    out2, dropped2 = G.sanitize("我听到你那边有声音了\n行吧我去睡了", ev)
    check("★ 只删那一句，别的留下", "行吧我去睡了" in out2 and "听到" not in out2, out2)

    print("\n[B] 诚实表达不许删")
    # 诚实 = 否认**能力**：「听不到」「收不到」
    for s in ("听不到你说话", "看不见图", "收不到语音", "我哪听得见"):
        out3, d3 = G.sanitize(s, ev)
        check(f"「{s}」原样保留", out3 == s and not d3, (out3, d3))
    # 「我没听到」不带对象 → 判据不介入（宁可漏，不可误杀）。
    #   真正露馅的形态是「没听到你那边的声响」，见 [A]。
    out3b, _d3b = G.sanitize("我没听到", ev)
    check("「我没听到」（无对象）不判", out3b == "我没听到", out3b)

    print("\n[C] 指认对方说过什么：证据里没有就删")
    ev2 = ["没有吧", "我没听到", "啥声响？"]
    out4, d4 = G.sanitize("装，接着装\n刚说别找你，这会儿又听不到了", ev2)
    check("★ 捏造的「刚说别找你」被删", "别找你" not in out4, out4)
    check("保留「装，接着装」", "装，接着装" in out4, out4)

    ev3 = ["别找我了，我要睡了"]
    out5, d5 = G.sanitize("你刚说别找你，怎么又来了", ev3)
    check("★ 真有依据就不删", "别找你" in out5 and not d5, (out5, d5))

    print("\n[D] 指认自己：证据里没有也删")
    ev4 = ["今天好累", "上班呢"]
    out6, _ = G.sanitize("我昨天说了要给你看个东西", ev4)
    check("★ 无据的自我主张被删", "昨天" not in out6, out6)
    ev5 = ["我昨天说了给你看个东西", "好啊"]
    out7, d7 = G.sanitize("我昨天说了给你看个东西", ev5)
    check("★ 有依据就保留", "昨天" in out7 and not d7, (out7, d7))

    print("\n[E] 不误杀：普通闲聊一句都不动")
    for s in ("今天好累啊", "吃了吗", "我刚看完一集动画", "你那个游戏打到哪了",
              "行，闭嘴", "微分啊，dy 跟 dx 一除就完事"):
        out8, d8 = G.sanitize(s, ["随便什么"])
        check(f"「{s[:12]}」不动", out8 == s and not d8, (out8, d8))

    print("\n[F] 边界")
    check("空串安全", G.sanitize("", ev)[0] == "")
    check("None 安全", G.sanitize(None, ev)[0] in ("", "None"))
    check("证据为空时，指认类会被删",
          G.sanitize("你刚说你好", [])[0] == "", G.sanitize("你刚说你好", [])[0])
    # 感官永远无据：即使证据里真出现了"声音"也不放行
    out9, d9 = G.sanitize("我听到你那边有声音", ["我这边有声音啊"])
    check("★ 感官不看证据（没耳朵）", out9 == "" and d9, (out9, d9))

    print("\n[H] 偏好挪用：把对方的喜好挂自己身上")
    own = ["下班了", "毁灭完了谁陪你喝"]
    usr = ["我最喜欢喝酒了", "周末去喝一杯"]
    bad = G.ungrounded("我就想安静喝口酒", own + usr, user_texts=usr, own_texts=own)
    check("★ 「我就想安静喝口酒」判为挪用",
          any(c["kind"] == "pref" for c in bad), bad)
    out_h, dropped_h = G.sanitize("毁灭完了谁陪你喝\n我就想安静喝口酒",
                                  own + usr, user_texts=usr, own_texts=own)
    check("★ 只删挪用那句", "我就想安静喝口酒" not in out_h and "谁陪你喝" in out_h,
          (out_h, dropped_h))
    # 自己真说过就不用删
    own2 = ["我就想安静喝口酒"]
    bad2 = G.ungrounded("我就想安静喝口酒", own2 + usr, user_texts=usr, own_texts=own2)
    check("★ 自己说过就不删", not bad2, bad2)
    # 对方没说过、自己也没说过 → 不算挪用（可能是新的偏好，不判）
    bad3 = G.ungrounded("我爱吃火锅", ["今天累"], user_texts=["喜欢喝酒"], own_texts=["今天累"])
    check("★ 无对照时不误判", not bad3, bad3)

    print("\n[I] 时长对不上：比「看得见的跨度」还长就是拍脑袋（2026-11-05）")
    # 真机 15:35「十五个小时了」，实际不到 12 小时。
    #   尺子 = now - since，since 是这段对话**看得见的最早时间戳**。
    NOW = 1_700_000_000.0
    H = 3600.0
    ev_t = ["上课呢高数", "不跟你说这个"]

    out_i, d_i = G.sanitize("都十五个小时了", ev_t, now=NOW, since=NOW - 6 * H)
    check("★ 只看得见 6 小时 → 「十五个小时」删掉",
          out_i == "" and d_i, (out_i, d_i))

    out_i2, d_i2 = G.sanitize("都十五个小时了", ev_t, now=NOW, since=NOW - 20 * H)
    check("真有 20 小时就别删", out_i2 == "都十五个小时了" and not d_i2, (out_i2, d_i2))

    out_i3, d_i3 = G.sanitize("跟你聊了两天，困了\n行吧我去睡了", ev_t,
                              now=NOW, since=NOW - 6 * H)
    check("★ 只删超长那句，别的留下",
          "两天" not in out_i3 and "行吧我去睡了" in out_i3, (out_i3, d_i3))

    out_i4, d_i4 = G.sanitize("跟你聊了两天", ev_t, now=NOW, since=NOW - 6 * H)
    check("★ 「两天」对 6 小时也算编", out_i4 == "" and d_i4, (out_i4, d_i4))

    out_i5, d_i5 = G.sanitize("聊了两个小时", ev_t, now=NOW, since=NOW - 6 * H)
    check("向上取整不算编（2h 的活说 2h）", "两个小时" in out_i5 and not d_i5, (out_i5, d_i5))

    for s in ("过两天再说", "一年前说过这个", "改天吧", "十分钟就好", "三点半了"):
        out_i6, d_i6 = G.sanitize(s, ev_t, now=NOW, since=NOW - 6 * H)
        check(f"「{s}」不归时长管", out_i6 == s and not d_i6, (out_i6, d_i6))

    out_i7, d_i7 = G.sanitize("都十五个小时了", ev_t)          # 没给锚点
    check("★ 没有时间戳就不判（没有尺子不量）",
          out_i7 == "都十五个小时了" and not d_i7, (out_i7, d_i7))

    out_i8, d_i8 = G.sanitize("都十五个小时了", ["他说十五个小时了", "嗯"],
                              now=NOW, since=NOW - 6 * H)
    check("证据里原样出现过 = 复述，放行", "十五个小时" in out_i8 and not d_i8,
          (out_i8, d_i8))

    print("\n[G] 接线：主动说话真的过围栏")
    import inspect
    try:
        import plugins.proactive_talk as PT
        src = inspect.getsource(PT._proactive_once)
        check("★ 主动说话里有事实围栏", "事实围栏" in src)
        check("★ 用的是 grounding.sanitize", "grounding" in src and "sanitize" in src)
        check("★ 证据来自 recent_msgs / 记忆轮次 / 上次主动",
              "recent_msgs" in src and "_fetch_memory_rounds" in src)
        check("★ 删到空就不发", "全部内容都无据" in src)
        check("★ 围栏异常不影响发送", "不影响发送" in src)
    except Exception as e:
        check("能导入 proactive_talk", False, e)

    print("\n[H] 持续性情绪自述（11-05 真机「那我继续瞎生气」）")
    ev_plain = "哦，我不改"
    bad_mood = G.ungrounded("那我继续瞎生气，你继续看着", ev_plain)
    check("★ 无据的持续情绪被判红",
          any(c.get("kind") == "mood" for c in bad_mood), bad_mood)
    check("★ 当下情绪表达不判（傲娇是人格，不是编造）",
          not any(c.get("kind") == "mood"
                  for c in G.ungrounded("我生气了，哼", ev_plain)))
    check("★ 证据里真出现过情绪就放行",
          not any(c.get("kind") == "mood"
                  for c in G.ungrounded("那我继续瞎生气", ["我刚才就在生气"])))
    out_h, drop_h = G.sanitize("不改就不改\n\n那我继续瞎生气，你继续看着", ev_plain)
    check("★ sanitize 删整句而不是只删两个字",
          "不改就不改" in out_h and "继续瞎生气" not in out_h, (out_h, drop_h))
    check("★ 同族词也算证据（闹脾气 ≈ 生气）",
          not any(c.get("kind") == "mood"
                  for c in G.ungrounded("我还生气呢", ["刚才闹脾气来着"])))

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
