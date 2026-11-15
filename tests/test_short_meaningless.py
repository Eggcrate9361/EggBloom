# -*- coding: utf-8 -*-
"""tests/test_short_meaningless.py —— 单字真回答不许被"无意义"吞掉

线上实证（2026-10-13 17:54 群[20001]）：
    跳过无意义消息: [占位昵称2] 对
旧版 _is_short_meaningless 里 `len(set(text)) <= 2 and len(text) <= 1`
是条死逻辑 —— 数学上恒等于 `len(text) <= 1`（单字符的 set 大小必为 1），
等于"所有单字消息一律无意义"。「对/不/有/没」这种正经回答全被杀。
真·敷衍单字（嗯/哦/哈/额/噗）本来就在 _SHORT_PHRASES 白名单里。

钉死两个方向：
  ① 单字真回答 → 不算无意义（进正常决策/回复链）；
  ② 敷衍词 / 纯数字 / 纯符号 → 仍然拦（白名单与 _is_nonsense 负责）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
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


def main():
    print("\n=== ① 单字真回答不再被误杀（线上事故钉死）===")
    check("★「对」不算无意义", not bot_core.is_meaningless_message("对"))
    for s in ["不", "有", "没", "是", "可", "要", "去", "来"]:
        check(f"「{s}」不算无意义", not bot_core.is_meaningless_message(s))

    print("\n=== ② 真·敷衍仍然拦（白名单/数字/纯符号）===")
    for s in ["嗯", "哦", "哈", "额", "噗", "1", "2", "666", "。。。"]:
        check(f"「{s}」算无意义", bot_core.is_meaningless_message(s))

    print("\n=== ③ 死逻辑本体：_is_short_meaningless 不再按长度一刀切 ===")
    check("单字「对」→ False", not bot_core._is_short_meaningless("对"))
    check("白名单单字「嗯」→ True（仍由名单拦）",
          bot_core._is_short_meaningless("嗯"))
    check("纯数字「1」→ True", bot_core._is_short_meaningless("1"))
    check("超过8字 → False（不管内容）",
          not bot_core._is_short_meaningless("嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯"))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
