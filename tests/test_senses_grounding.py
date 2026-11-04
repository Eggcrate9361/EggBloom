# -*- coding: utf-8 -*-
"""tests/test_senses_grounding.py —— 不许编造感官（2026-11-04 真机）

现场（QQ 2011257385 私聊）：

    插件发送：我躺下没多久又醒了，一直没听到你那边的声响
    对方    ：啥声响？
    小a     ：我说话的声音啊 还能有啥声响

一个纯文字 bot 声称能听见声音 —— 被追问时只能越编越离谱。
同一条里还有「刚说别找你」，而这句话在记录里根本不存在（编造对话）。

判据：
  ① 人格里有【没有耳朵】这条硬约束，且明确"听不到声音"
  ② 它在**会被注入**的段里（别写了等于没写 —— 之前【认账】就是死文字）
  ③ 主动说话的提示词里也说了一遍（主动找人聊天最容易编）
  ④ 主动提示词禁止编"你刚说……"
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402

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
    raw = open(os.path.join(ROOT, "plugins", "personality.txt"),
               encoding="utf-8", errors="replace").read()

    print("[A] 人格里有没有这条")
    check("★ 有【没有耳朵】", "【没有耳朵】" in raw)
    check("★ 明说听不到声音", "听不到声音" in raw)
    check("★ 禁止「听到/听见」", "「听到」「听见」" in raw)

    print("\n[B] 它在会被注入的段里（防死文字）")
    segs = {}
    for n in bc.prompt_segment_names():
        segs[n] = bc._persona_rule_segment(n) or ""
    hit = [n for n, s in segs.items() if "【没有耳朵】" in s]
    check("★ 出现在注入段里", bool(hit), sorted(segs))
    # 静态提示词里必须真的能看到
    try:
        prompt = bc._build_static_prompt(False) or ""
    except Exception:
        prompt = ""
    check("★ 私聊静态提示词含此条", "【没有耳朵】" in prompt)
    try:
        prompt_g = bc._build_static_prompt(True) or ""
    except Exception:
        prompt_g = ""
    check("群聊静态提示词也含此条", "【没有耳朵】" in prompt_g or not prompt_g)

    print("\n[C] 主动说话单独再说一遍")
    p = os.path.join(ROOT, "plugins", "proactive_talk", "__init__.py")
    src = open(p, encoding="utf-8", errors="replace").read()
    check("★ 禁止编「听到你那边的声响」", "听到你那边的声响" in src)
    check("★ 声明只有文字和图片", "你只有文字和图片" in src)
    check("★ 禁止编「你刚说……」", "你刚说" in src)

    print("\n[D] 不能误伤正常的听字用法")
    # 「没听到」这类描述自己的状态在别的场景可能合理，这里只要求"不许"出现，
    # 而不是把"听"字全局禁掉
    check("不是一刀切删字", "听" in raw)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
