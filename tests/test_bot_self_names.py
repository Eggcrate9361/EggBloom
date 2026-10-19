# -*- coding: utf-8 -*-
"""tests/test_bot_self_names.py —— Bot 认不出"自己别的群的名片"（用户 2026-10-19 报）

## 线上实证

协议日志：
    群[200000001] 跳过定向他人的消息: [占位名片B] @蛋

实测该 bot 账号（10000001）的**真实群名片**：

| 群 | card | 说明 |
|---|---|---|
| 200000002 | `蛋` | 该群名片 |
| 200000001 | `""`（空） | 回退全局昵称 `占位名片A` |

用户在 200000001 用「@蛋」叫它（那是它另一个群的名片），
旧逻辑只比 `bot_group_nick` 一个值 → 判成"@别人" → 整条消息被跳过。

## 现在的契约

1. `_bot_self_names()` = QQ 号 + 配置昵称 + **各群真实群名片**（从 GROUP_MEMBERS 取）；
2. `_is_bot_self_name()` / `_is_at_others()` 用上面这个全集判定；
3. `_display_name(sender, group_id)` **群名片优先**，不再把别群名片当本群名字；
4. `QQ_NICK_CACHE` 只存全局昵称（不存群名片），避免跨群污染。

## 写盘隔离

import bot_core 会拉起插件与记忆子系统，`sandbox_neurobase()` 必须在最前面，
否则可能真写生产沙漏（见 tests/_sandbox.py 的事故记录）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

sys.path.insert(0, ROOT)
import bot_core  # noqa: E402

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
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


BOT = str(bot_core.BOT_QQ)
_ORIG_CFG = dict(bot_core._config)
_ORIG_MEMBERS = dict(bot_core.GROUP_MEMBERS)


def reset(groups=None, **cfg):
    bot_core._config.clear()
    bot_core._config.update(_ORIG_CFG)
    bot_core._config.update(cfg)
    bot_core.GROUP_MEMBERS.clear()
    for gid, members in (groups or {}).items():
        bot_core.GROUP_MEMBERS[gid] = dict(members)


# ══════════════════ 1. 自己的称呼全集 ══════════════════
def test_self_names():
    print("\n[1] _bot_self_names 汇总各群名片")
    reset(groups={
        "200000002": {BOT: "蛋"},
        "200000001": {BOT: "占位名片A"},
        "111": {"999": "路人甲"},          # 别人的名字不该进来
    }, bot_group_nick="占位名片A", bot_nicknames=["小a"])
    names = bot_core._bot_self_names()
    check("含 QQ 号", BOT in names, names)
    check("含配置昵称 小a", "小a" in names, names)
    check("含 bot_group_nick", "占位名片A" in names, names)
    check("★ 含别群名片「蛋」", "蛋" in names, names)
    check("不含别人的名字", "路人甲" not in names, names)

    reset()  # 没有成员表时不能崩
    check("空成员表不崩", isinstance(bot_core._bot_self_names(), set))


# ══════════════════ 2. @ 判定 ══════════════════
def test_at_detection():
    print("\n[2] _is_at_others 认自己 / 不认别人")
    reset(groups={
        "200000002": {BOT: "蛋"},
        "200000001": {BOT: "占位名片A"},
    }, bot_group_nick="占位名片A", bot_nicknames=["小a"])

    check("@蛋 不算定向他人", bot_core._is_at_others("@蛋 在吗") is False)
    check("@占位名片A 不算定向他人",
          bot_core._is_at_others("@占位名片A 在吗") is False)
    check("@QQ号 不算定向他人", bot_core._is_at_others(f"@{BOT} 在吗") is False)
    check("★ @老王 仍是定向他人", bot_core._is_at_others("@老王 在吗") is True)
    check("句末 @老王 仍是定向他人", bot_core._is_at_others("你问 @老王") is True)
    check("裸「蛋」不算定向他人（没有 @）",
          bot_core._is_at_others("蛋") is False)
    check("@我 且 @别人 → 仍是冲我说的",
          bot_core._is_at_others("@蛋 你别和 @老王 说话") is False)


# ══════════════════ 3. 显示名：群名片优先 ══════════════════
def test_display_name():
    print("\n[3] _display_name 群名片优先、不跨群串名")
    reset(groups={
        "200000002": {BOT: "蛋", "10000002": "小蛋"},
        "200000001": {BOT: "占位名片A", "10000002": "e蛋"},
    }, bot_group_nick="占位名片A", bot_nicknames=["小a"])
    # 全局缓存里塞一个"别群名片"，验证本群名片仍然优先
    bot_core.QQ_NICK_CACHE["10000002"] = "占位名片B"

    check("本群名片优先（e蛋，不是全局 占位名片B）",
          bot_core._display_name("10000002", "200000001") == "e蛋",
          bot_core._display_name("10000002", "200000001"))
    check("另一个群给另一个名字",
          bot_core._display_name("10000002", "200000002") == "小蛋",
          bot_core._display_name("10000002", "200000002"))
    check("★ 不认识本群时回退全局（不编造）",
          bot_core._display_name("10000002", "nope") == "占位名片B",
          bot_core._display_name("10000002", "nope"))
    check("不传 group_id → 全局昵称（兼容旧调用）",
          bot_core._display_name("10000002") == "占位名片B",
          bot_core._display_name("10000002"))
    check("Bot 自己 → 我(Bot)",
          bot_core._display_name(BOT, "200000001") == "我(Bot)",
          bot_core._display_name(BOT, "200000001"))
    check("空 sender → 群友", bot_core._display_name("") == "群友",
          bot_core._display_name(""))


# ══════════════════ 4. 全局缓存不存群名片 ══════════════════
def test_global_cache_not_polluted():
    print("\n[4] QQ_NICK_CACHE 只存全局昵称（源码级）")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("成员列表不再把 display（card 优先）写进全局缓存",
          "QQ_NICK_CACHE[uid] = nick or display" in src,
          "未找到 `QQ_NICK_CACHE[uid] = nick or display`")
    check("★ 旧写法已不存在（会把群名片写进全局）",
          "QQ_NICK_CACHE[uid] = display" not in src)


def main():
    test_self_names()
    test_at_detection()
    test_display_name()
    test_global_cache_not_polluted()

    bot_core._config.clear()
    bot_core._config.update(_ORIG_CFG)
    bot_core.GROUP_MEMBERS.clear()
    bot_core.GROUP_MEMBERS.update(_ORIG_MEMBERS)

    print("\n" + "=" * 52)
    print(f"PASS: {PASS}   FAIL: {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  ✗ " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())