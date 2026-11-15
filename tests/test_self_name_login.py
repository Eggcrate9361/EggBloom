# -*- coding: utf-8 -*-
"""tests/test_self_name_login.py —— 用登录账号的昵称认得自己（2026-11-01）

脱敏后的真机日志（群 200000001）：

    [13:48:52] 文本: '@SampleBot /塔罗牌' is_at_bot=False has_text_at=True
    [13:48:55] [决策放行] 触发: … @SampleBot /塔罗牌
    [13:48:55] 跳过定向他人的消息: [占位群名片] @SampleBot /塔罗牌

根因：配置里 `bot_qq=10003`、`bot_group_nick=占位名片`、`bot_nicknames=['占位昵称']`，
而实际登录账号是 **SampleBot(1000000002)** —— `_is_bot_self_name("SampleBot")` 不认，
于是把"@我"判成"@别人"，整条消息被丢。

修法：启动时记住 get_login_info 的 nickname / user_id，`_bot_nicknames()` 与
`_bot_self_names()` 都纳入。本用例锁住这三件事。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

# 停掉 import bot_core 带起来的守护线程：否则测试全过、退出码却是 0xC0000409
# （解释器关闭时 daemon 线程还在写 stdout，抢锁失败 → 拖垮退出码）。
# 详见 tests/_quiet_exit.py 开头的说明。
sys.path.insert(0, HERE)
from _quiet_exit import quiet_exit  # noqa: E402
quiet_exit()

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
    saved = (bot_core.LOGIN_NICK, bot_core.LOGIN_UID)

    print("[A] 没有登录信息时，@陌生名字仍判为他人")
    bot_core.LOGIN_NICK, bot_core.LOGIN_UID = "", ""
    check("@某个群友 → True", bot_core._is_at_others("@张三 你好") is True,
          bot_core._is_at_others("@张三 你好"))

    print("\n[B] 登录后，@登录昵称不再被当别人")
    bot_core.LOGIN_NICK = "SampleBot"
    bot_core.LOGIN_UID = "1000000002"
    check("_is_bot_self_name('SampleBot')",
          bot_core._is_bot_self_name("SampleBot") is True)
    check("_is_bot_self_name('1000000002')",
          bot_core._is_bot_self_name("1000000002") is True)
    check("@SampleBot /塔罗牌 → 不是定向他人",
          bot_core._is_at_others("@SampleBot /塔罗牌") is False,
          bot_core._is_at_others("@SampleBot /塔罗牌"))

    print("\n[C] 昵称列表包含登录昵称（触发词/点名检测也能用）")
    bot_core.LOGIN_NICK = ""
    before = bot_core._bot_nicknames()
    bot_core.LOGIN_NICK = "SampleBot"
    nicks = bot_core._bot_nicknames()
    check("SampleBot 在列表里", "SampleBot" in nicks, nicks)
    check("原有称呼一个没丢", all(n in nicks for n in before), (before, nicks))
    check("bot_nick_in_text 命中", bool(bot_core.bot_nick_in_text("@SampleBot 在吗")))

    print("\n[D] 大小写/全角不敏感（老规矩）")
    check("@Samplebot", bot_core._is_at_others("@Samplebot 在吗") is False)
    check("全角Ａ", bot_core._is_at_others("@ＳａｍｐｌｅＢｏｔ 在吗") is False)

    bot_core.LOGIN_NICK, bot_core.LOGIN_UID = saved

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
