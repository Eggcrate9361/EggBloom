# -*- coding: utf-8 -*-
"""tests/test_action_policy.py —— 动作词表（第 3 步地基）

判据：
  [A] 词表本身：str 语义不能变（日志/JSON/比较全靠它）
  [B] 四套旧写法能翻译成同一套动作
  [C] 认不出的值不猜：`of()` 返回 None，`from_group_act()` 保守当 skip
  [D] 群聊决策点已经改用这张表（防止新加动作时又各写一份字符串）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from harness.action_policy import (Action, describe, from_group_act,  # noqa: E402
                                   from_proactive_kind)

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


def main():
    print("=== A) str 语义 ===")
    check("A1 与裸字符串相等", Action.TEXT == "reply" and Action.SKIP == "skip")
    check("A2 str() 输出原值而不是 Action.TEXT", str(Action.TEXT) == "reply", str(Action.TEXT))
    check("A3 可当字典键用", {"reply": 1}.get(Action.TEXT) == 1)
    check("A4 f-string 也是原值", f"{Action.OBSERVE}" == "observe", f"{Action.OBSERVE}")

    print("\n=== B) 四套旧写法归一到同一套动作 ===")
    check("B1 群聊 reply == 主动层 say", from_group_act("reply") is from_proactive_kind("say"))
    check("B2 群聊 skip == 主动层 skip", from_group_act("skip") is from_proactive_kind("skip"))
    check("B3 主动层 poke/meme 认得",
          from_proactive_kind("poke") is Action.POKE
          and from_proactive_kind("meme") is Action.MEME)
    check("B4 工具类", from_proactive_kind("task") is Action.TOOL)
    check("B5 大小写/空格不影响", Action.of("  REPLY ") is Action.TEXT)

    print("\n=== C) 认不出不猜 ===")
    check("C1 of() 默认返回 None", Action.of("nonsense") is None)
    check("C2 from_group_act 保守当 skip", from_group_act("nonsense") is Action.SKIP)
    check("C3 已经是 Action 就原样返回", Action.of(Action.POKE) is Action.POKE)

    print("\n=== D) 群聊决策点已接线 ===")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("D1 四个返回都走词表", src.count('"act": _AP.Action.') == 4,
          src.count('"act": _AP.Action.'))
    check("D2 不再有裸字符串 act", '"act": "reply"' not in src and '"act": "skip"' not in src)

    print("\n=== E) 日志用词 ===")
    check("E1 describe 是中文短语", describe(Action.TEXT) == "说话")

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
