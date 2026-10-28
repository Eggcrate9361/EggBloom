# -*- coding: utf-8 -*-
"""tests/test_memory_scope.py —— 记忆的会话隔离与「统一记忆」

背景（2026-10-25 实测）
    用户报「没有触发统一记忆」。查下来**存没问题**：

        2026-10-24 23:52:11 | user | [(/ω＼)(1234567890)] 你看现在养大肥鱼很火诶
        2026-10-25 11:39:18 | user | [(/ω＼)(1234567890)] [引用] 你看现在养大肥鱼很火诶
        2026-10-25 11:44:45 | user | [群:123456789] [(/ω＼)(1234567890)] 你想不想养大肥鱼

    三条都在库里。**坏在取**：`_scope_match` 把群聊和私聊硬隔开，
    群聊检索只认 `[群:{gid}]` 行 —— 于是 11:40 刚在私聊学会「不是真鱼，
    是 deepseek 那个桌面大肥鱼」，11:44 群里再问同一件事，
    bot 又答「养鱼？你当我这是鱼塘啊」。记忆存了两份、取时各看各的，
    等于白记。

    用户决定放宽（明确知道隐私代价），于是新增配置 `memory_scope_share`。

这个文件锁四件事：
  1. 默认（关）行为与改前**逐字节相同** —— 群只看本群、私聊只看本人
  2. 打开后：私聊行在群里可召回、群行在私聊可召回
  3. **群与群之间始终隔离**（放宽不等于全放开）
  4. **别人的私聊始终隔离**（这是底线，放宽也不碰）

纯 Python，不联网。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import sandglass_bridge as sb  # noqa: E402

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


GROUP_A = "[群:123456789] [小明(10001)] 群A 里说的话"
GROUP_B = "[群:123456787] [小红(10002)] 群B 里说的话"
PM_ME = "[(/ω＼)(1234567890)] 我私聊说的话"
PM_OTHER = "[(别的)(999999)] 别人私聊说的话"


def with_share(value):
    sb._scope_share = lambda v=value: v


def main():
    orig = sb._scope_share
    try:
        print("[1] 默认（关）—— 与改前完全一致")
        with_share(False)
        check("群聊保留本群行", sb._scope_match(GROUP_A, ("group", "123456789")))
        check("群聊排除他群行", not sb._scope_match(GROUP_B, ("group", "123456789")))
        check("群聊排除私聊行", not sb._scope_match(PM_ME, ("group", "123456789")))
        check("私聊保留本人行", sb._scope_match(PM_ME, ("private", "1234567890")))
        check("私聊排除群行", not sb._scope_match(GROUP_A, ("private", "1234567890")))
        check("私聊排除别人的行", not sb._scope_match(PM_OTHER, ("private", "1234567890")))

        print("\n[2] 打开后 —— 私聊与群互通")
        with_share(True)
        check("群聊能召回私聊行", sb._scope_match(PM_ME, ("group", "123456789")))
        check("私聊能召回群行", sb._scope_match(GROUP_A, ("private", "1234567890")))
        check("群聊仍保留本群行", sb._scope_match(GROUP_A, ("group", "123456789")))
        check("私聊仍保留本人行", sb._scope_match(PM_ME, ("private", "1234567890")))

        print("\n[3] 打开后 —— 群与群之间仍然隔离")
        check("A 群看不到 B 群", not sb._scope_match(GROUP_B, ("group", "123456789")))
        check("B 群看不到 A 群", not sb._scope_match(GROUP_A, ("group", "123456787")))

        print("\n[4] 打开后 —— 别人的私聊仍然隔离")
        check("私聊看不到别人的私聊",
              not sb._scope_match(PM_OTHER, ("private", "1234567890")))

        print("\n[5] 边界")
        with_share(False)
        check("scope 为空时不过滤", sb._scope_match(GROUP_B, None))
        check("scope key 为空时不过滤", sb._scope_match(GROUP_B, ("group", "")))

        print("\n[6] 配置键已在三处登记")
        import config
        check("DEFAULT_CONFIG 有 memory_scope_share",
              "memory_scope_share" in config.DEFAULT_CONFIG)
        check("默认值是 False（不默认放宽）",
              config.DEFAULT_CONFIG.get("memory_scope_share") is False,
              config.DEFAULT_CONFIG.get("memory_scope_share"))
        import io
        ex = io.open(os.path.join(ROOT, "bot_config.json.example"),
                     encoding="utf-8-sig").read()
        check("example 已登记", '"memory_scope_share"' in ex)
        v3 = io.open(os.path.join(ROOT, "webui", "webui_artistic_v3.html"),
                     encoding="utf-8").read()
        check("v3 标记/填充/收集各一处",
              v3.count('id="sMemoryScopeShare"') == 1
              and v3.count("t('sMemoryScopeShare'") == 1
              and v3.count("chk('sMemoryScopeShare')") == 1)
    finally:
        sb._scope_share = orig

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())