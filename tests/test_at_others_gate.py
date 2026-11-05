# -*- coding: utf-8 -*-
"""tests/test_at_others_gate.py —— 放宽「@别人就闭嘴」的抢话闸（11-05）

真机 17:42:31：群里有人引用 DeepSeek v41f 的「刘建国是谁」再 @ 了别人，
旧逻辑 `has_any_at and not is_at_bot` 无条件 continue → 整条吞掉，连决策器
都看不到。用户拍板「放宽抢话判定」。

边界必须同时守住：
  · 放行 ≠ 直接回复。只是不在这层硬跳，后面还有缓冲区/概率门/决策器，
    提示词里「别抢话」的引用注记也还在 —— 不会变成见人就插嘴。
  · 没有任何指向我的信号时，仍然硬跳（群友互聊还是别插嘴）。
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402

PASS, FAIL, FAILURES = 0, 0, []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def skip(**kw):
    base = dict(has_any_at=True, is_at_bot=False, has_xiao_a=False,
                quote_me=False, has_bot_nick=False,
                has_reply_seg=False, relax=True)
    base.update(kw)
    return bc._should_skip_at_others(**base)


def main():
    print("[A] 基本边界")
    check("★ 没 at 段 → 不归这条闸管", not skip(has_any_at=False))
    check("★ at 指向 bot → 不跳", not skip(is_at_bot=True))
    check("★ 喊了小a → 不跳", not skip(has_xiao_a=True))
    check("★ @别人且毫无指向我的信号 → 仍硬跳", skip())

    print("\n[B] 放宽的三种信号")
    check("★ 引用的是我说过的话 → 放行",
          not skip(quote_me=True))
    check("★ 文本里点到我的名字 → 放行",
          not skip(has_bot_nick=True))
    check("★ 引用别人来追问 → 放行（真机 17:42 那条）",
          not skip(has_reply_seg=True))
    check("★ 多信号叠加也放行",
          not skip(quote_me=True, has_bot_nick=True, has_reply_seg=True))

    print("\n[C] 开关可回退")
    check("★ relax=False 时退回旧行为（一律硬跳）",
          skip(has_reply_seg=True, quote_me=True, relax=False))
    check("★ relax=False 但 at 指向 bot 仍不跳",
          not skip(is_at_bot=True, relax=False))

    print("\n[D] 静态接线：放行不等于直接回")
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8",
                  errors="replace").read()
    check("★ 主流程调用纯函数", "_should_skip_at_others(" in src)
    check("★ 有配置开关", "group_relax_at_others" in src)
    check("★ 放行只打日志、不 return/continue",
          "[放宽抢话] @他人但有引用/点到我，交给决策器" in src)
    check("★ 「别抢话」的引用注记仍在（最终闸门没拆）",
          "不是在叫我" in src and "别抢话" in src)
    check("★ 有 reply 段判据（不依赖可能被配置关掉的 has_quote）",
          '_has_reply_seg = any(' in src)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  - " + x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
