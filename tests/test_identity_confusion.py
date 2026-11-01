# -*- coding: utf-8 -*-
"""tests/test_identity_confusion.py —— 2026-11-01 真机「分不清人」三件套

真机原话（群 829854136，18:10–18:14）：

    Texas：@AAA无理由退货  小a，你记得你说过这句话吗？
    Bot  ：我说过吗？                       ← ① 否定自己 4 秒前说的话
    Texas：[引用别人] 我一直在好奇…插件一直扣钱
    Bot  ：没换，我哪有钱 / 问错人了吧        ← ② 把引用别人的话当成在问自己
    日志 ：[**·ax1d] 我说你老大是野人呢       ← ③ 称呼被存成遮罩 `**`，人分不清

本用例锁住三处修复：
  A. 认账闸：回指「这句话」→ 用引用原文当主张；「我说过吗」算否认
  B. 触发口径：引用他人 ≠ 在叫我（会话中也不再强制回复）
  C. 遮罩称呼不能当显示名（`**` 退回群名片/昵称）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402
from harness import consistency as cons  # noqa: E402

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
    print("[A] 认账闸：回指 + 否认")
    text = ("[引用:我(Bot)｜引用我，必回] 撞小孩赔的\n"
            "[当前] @AAA无理由退货  小a，你记得你说过这句话吗？")
    check("主张取到引用原文", cons.extract_claim(text) == "撞小孩赔的",
          cons.extract_claim(text))
    check("「我说过吗？」算否认", cons.looks_like_denial("我说过吗？") is True)
    check("「我没说过」仍算否认", cons.looks_like_denial("我没说过啊") is True)
    check("「嗯，我说过」不算否认", cons.looks_like_denial("嗯，我说过") is False)
    v = cons.verdict(text, "我说过吗？", ["撞小孩赔的"])
    check("三条件齐 → conflict", v.get("conflict") is True, v)
    check("确实没说过 → 不拦", cons.verdict(text, "我说过吗？", ["别的话"])["conflict"] is False)

    print("\n[B] 触发口径：引用他人不强制回复")
    f = bot_core._compute_forced
    check("引用他人 + 会话中 → 不强制",
          f(is_at_bot=False, has_xiao_a=False, has_bot_nick=False,
            quote_me=False, has_quote=True, has_text_at=False,
            in_session=True) is False)
    check("引用我 → 强制",
          f(is_at_bot=False, has_xiao_a=False, has_bot_nick=False,
            quote_me=True, has_quote=True, has_text_at=False,
            in_session=False) is True)
    check("@我 → 强制",
          f(is_at_bot=True, has_xiao_a=False, has_bot_nick=False,
            quote_me=False, has_quote=False, has_text_at=False,
            in_session=False) is True)
    check("纯会话（无引用）→ 强制",
          f(is_at_bot=False, has_xiao_a=False, has_bot_nick=False,
            quote_me=False, has_quote=False, has_text_at=False,
            in_session=True) is True)
    check("文本 @ → 强制",
          f(is_at_bot=False, has_xiao_a=False, has_bot_nick=False,
            quote_me=False, has_quote=False, has_text_at=True,
            in_session=False) is True)

    print("\n[C] 遮罩称呼不能当名字")
    check("** 是遮罩", bot_core._alias_is_mask("**") is True)
    check("＊ 是遮罩", bot_core._alias_is_mask("＊") is True)
    check("小a 不是遮罩", bot_core._alias_is_mask("小a") is False)
    # 让别名返回 **，显示名应退回群名片
    _orig = bot_core.people_alias.get_alias
    bot_core.people_alias.get_alias = lambda qq: "**"
    gid, uid = "829854136", "2011257385"
    old = bot_core.GROUP_MEMBERS.get(gid)
    bot_core.GROUP_MEMBERS[gid] = dict(old or {})
    bot_core.GROUP_MEMBERS[gid][uid] = "Texas"
    try:
        check("遮罩别名被忽略、用群名片", bot_core._display_name(uid, gid) == "Texas",
              bot_core._display_name(uid, gid))
    finally:
        bot_core.people_alias.get_alias = _orig
        if old is None:
            bot_core.GROUP_MEMBERS.pop(gid, None)
        else:
            bot_core.GROUP_MEMBERS[gid] = old

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
