# -*- coding: utf-8 -*-
"""tests/test_persona_tag.py —— persona_tag 的纯函数回归

锁死：
  1. 默认（focus 空 + TAG 空）不触发
  2. 明文号命中 → 提示里带「老大/亲近」
  3. 派生标签命中发送者 → 触发（公开版路径）
  4. 长度不符的发送者不派生（省成本）
  5. 昵称解析失败/为空 → 退化成只显示 QQ
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import persona_tag  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
QQ = "10003"            # 占位号（5 位）
Q10 = "1234567890"      # 10 位，用于标签路径
Q10B = "9876543210"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} {extra}")
        print(f"  [!!]   {name}  {extra}")


def main():
    print("[1] 默认关闭")
    check("focus 空 + 无标签 → 不触发", persona_tag.context(f"@{QQ}", "") == "")
    check("focus=None → 不触发", persona_tag.context(f"@{QQ}", None) == "")
    check("空白 → 不触发", persona_tag.context(f"@{QQ}", "  ") == "")
    check("空标签 → 不触发", persona_tag.context(f"{Q10}", "", None, "") == "")

    print("\n[2] 明文路径")
    check("不含号码 → 不触发", persona_tag.context("今天吃什么", QQ) == "")
    txt = persona_tag.context(f"@{QQ} 在吗", QQ)
    check("含号码 → 触发", bool(txt))
    check("文案含「老大」", "老大" in txt, txt[:40])
    check("文案含「亲近」", "亲近" in txt, txt[:40])
    check("空消息也认发送者", bool(persona_tag.context("", QQ, None, None, QQ)))
    check("user_msg=None 安全", persona_tag.context(None, QQ) == "")

    print("\n[3] 标签路径")
    d = persona_tag.derive(Q10)
    check("标签长度固定", len(d) == persona_tag._TAG_LEN, d)
    check("标签可复现", d == persona_tag.derive(Q10))
    check("标签不含明文", Q10 not in d)
    txt = persona_tag.context("随便聊聊", "", None, d, Q10)
    check("发送者标签命中 → 触发", bool(txt) and Q10 in txt, txt[:50])
    check("别的发送者 → 不触发",
          persona_tag.context("随便聊聊", "", None, d, Q10B) == "")
    check("文本里的号码命中", bool(persona_tag.context(f"我号 {Q10}", "", None, d)))

    print("\n[4] 成本闸（长度不符不派生）")
    check("5 位发送者不派生",
          persona_tag._match("10003", d) is False)
    check("10 位但不对 → 不触发",
          persona_tag._match(Q10B, d) is False)
    check("10 位且对 → 命中", persona_tag._match(Q10, d) is True)

    print("\n[5] 昵称与优先级")
    txt = persona_tag.context(f"{Q10} 在吗", "", lambda q: "示例昵称", d, Q10)
    check("昵称并入文案", "示例昵称（QQ " + Q10 in txt, txt[:50])

    def _boom(q):
        raise RuntimeError("nick cache down")

    txt = persona_tag.context(f"{Q10} 在吗", "", _boom, d, Q10)
    check("昵称异常仍可用", bool(txt) and Q10 in txt)
    txt = persona_tag.context(f"{Q10} 在吗", "", lambda q: "", d, Q10)
    check("昵称为空 → 只显示 QQ", ("QQ " + Q10) in txt and "（" not in txt)

    both = persona_tag.context(f"{QQ} 和 {Q10}", QQ, None, d, Q10)
    check("发送者优先于文本号码", Q10 in both and QQ not in both, both[:50])

    print("\n[6] 模块常量")
    check("TAG 为空或 16 位 hex",
          persona_tag.TAG == "" or
          (len(persona_tag.TAG) == persona_tag._TAG_LEN
           and all(c in "0123456789abcdef" for c in persona_tag.TAG)),
          persona_tag.TAG)
    check("模板不含数字", not any(ch.isdigit() for ch in persona_tag.TEMPLATE))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
