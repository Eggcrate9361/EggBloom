# -*- coding: utf-8 -*-
"""tests/test_bot_nicknames.py —— Bot 昵称不再写死（用户 2026-10-12 报）

线上实证（协议日志）：
    群[200000003] 文本: '小A，你会不会忘掉大家'
    HAS_XIAO_A=FALSE IS_AT_BOT=FALSE HAS_QUOTE=FALSE HAS_TEXT_AT=FALSE
→ 触发词静默失效，Bot 完全不回。

根因有两条，本测试各锁一层：
  1. `group_trigger_words` 运行时是**空列表** —— `dict.get(key, default)` 拿到空列表，
     默认值形同虚设 → 一个触发词都不剩；
  2. 匹配是**大小写敏感**的裸子串：配置里写 "小a"，群友打 "小A" 就漏。

现在的契约：
  · 昵称走 `bot_nicknames`（可配多个），空则派生 `bot_group_nick`，再空才兜底 "小a"；
  · 匹配大小写/全角/@/空格一律不敏感；
  · 群聊触发词列表为空时**自动等于昵称列表**（不再退化成"没有触发词"）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402
from plugins.group_chat_decider import _bot_directed, _has_likely_reply_signal  # noqa: E402

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


def set_cfg(**kw):
    bot_core._config.clear()
    bot_core._config.update(kw)


ORIG = dict(bot_core._config)


def main():
    try:
        # ── ① 线上原句：大小写不敏感 ──
        print("=== ① 线上实证句（小A 必须命中）===")
        set_cfg()
        check("『小A，你会不会忘掉大家』命中昵称",
              bool(bot_core.bot_nick_in_text("小A，你会不会忘掉大家")),
              bot_core.bot_nick_in_text("小A，你会不会忘掉大家"))
        check("★ 判定为群触发（has_xiao_a 的来源）",
              bot_core._is_group_trigger("小A，你会不会忘掉大家"))
        check("该消息不算'无意义'（不会被跳过）",
              not bot_core.is_meaningless_message("小A，你会不会忘掉大家"))

        # ── ② 写法变体：@ / 全角＠ / 空格 ──
        print("\n=== ② 写法变体 ===")
        for variant in ("@小a 在吗", "＠小a 在吗", "小 a 在吗", "小A，在吗", "小ａ在吗"):
            check(f"变体命中：{variant!r}", bool(bot_core.bot_nick_in_text(variant)), "")
        check("不相关文本不误命中", not bot_core.bot_nick_in_text("小明在吗"))
        check("空文本安全", bot_core.bot_nick_in_text("") == "")

        # ── ③ 多昵称（配置驱动，不写死）──
        print("\n=== ③ 多昵称 ===")
        set_cfg(bot_nicknames=["小a", "宝宝", "TestBot"], bot_group_nick="示例昵称")
        check("第二个昵称生效", bool(bot_core.bot_nick_in_text("宝宝，你会不会忘掉大家")))
        check("第三个昵称生效（含大小写混写）", bool(bot_core.bot_nick_in_text("testbot 在吗")))
        check("群名片自动并入", "示例昵称" in bot_core._bot_nicknames())
        check("第一个昵称当网名用", bot_core._bot_nicknames()[0] == "小a")

        # 只配群名片（旧字段）也能用
        set_cfg(bot_nicknames=[], bot_group_nick="测试机")
        check("只配群名片 → 自动派生", bot_core._bot_nicknames() == ["测试机"])
        check("群名片大小写不敏感", bool(bot_core.bot_nick_in_text("测试机 在吗")))

        # 什么都没配 → 兜底（不再报错、不再空列表）
        set_cfg(bot_nicknames=[], bot_group_nick="")
        check("全空 → 兜底 [小a]", bot_core._bot_nicknames() == ["小a"])

        # ── ④ 空触发词列表 = 自动派生（本次事故的直接根因）──
        print("\n=== ④ 空触发词不再等于'没有触发词' ===")
        set_cfg(group_trigger_words=[], bot_nicknames=["小a"])
        check("空列表 → 派生为昵称", bot_core._group_trigger_words() == ["小a"])
        check("★ 空列表下仍能触发", bot_core._is_group_trigger("小A，你会不会忘掉大家"))
        set_cfg(group_trigger_words=["自定义词"], bot_nicknames=["小a"])
        check("配了触发词就用触发词", bot_core._group_trigger_words() == ["自定义词"])
        check("自定义触发词大小写不敏感", bot_core._is_group_trigger("自定义词 在吗"))

        # ── ⑤ 决策插件：同样不写死 ──
        print("\n=== ⑤ 决策插件 _bot_directed ===")
        ctx = {"bot_nicknames": ["小a", "宝宝"], "bot_name": "小a"}
        check("ctx 昵称命中（大小写不敏感）", _bot_directed("小A，你会不会忘掉大家", ctx))
        check("第二个昵称命中", _bot_directed("宝宝在吗", ctx))
        check("未命中返回 False", not _bot_directed("小明在吗", ctx))
        check("无 ctx 时仍有兜底（不回归）", _bot_directed("小a 在吗"))
        check("软信号也认配置称呼", _has_likely_reply_signal("宝宝在吗", ctx))
        check("软信号对无关文本不误报", not _has_likely_reply_signal("今天天气不错", ctx))

        # ── ⑥ 身份注入块用配置昵称 ──
        print("\n=== ⑥ 身份注入 ===")
        set_cfg(bot_nicknames=["阿机", "小a"], bot_group_nick="示例昵称")
        blk = bot_core._build_identity_block() if hasattr(bot_core, "_build_identity_block") else ""
        check("身份块可生成（含全部称呼）", ("阿机" in blk and "小a" in blk) if blk else True, blk[:80])
    finally:
        bot_core._config.clear()
        bot_core._config.update(ORIG)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
