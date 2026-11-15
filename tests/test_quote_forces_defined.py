# -*- coding: utf-8 -*-
"""tests/test_quote_forces_defined.py —— 群消息循环里引用了未定义变量（2026-10-19）

## 线上实证

```
[13:30:00] 错误: name 'quote_forces' is not defined
Traceback (most recent call last):
  File "...\\bot_core.py", line 10500, in main
    if is_at_bot or has_xiao_a or has_bot_nick or quote_forces:
                                                  ^^^^^^^^^^^^
NameError: name 'quote_forces' is not defined
```

`quote_forces` 只在 `process_buffered_message()` 里定义（`quote_forces = has_quote and quote_me`，
2026-10-25 起该函数主体改名为 `_pipeline_step()`），
而主事件循环 `main()` 里那处直接引用 —— **自 1.5.5 首发即存在**。
因为它在"缓冲区建好之后"才抛，异常跳过了 `continue` 与"立即建立会话"那段，
于是「被@/叫名字/引用 → 立刻开会话」的逻辑从来没生效过，
而且**每条群消息都白打一次堆栈**。

## 现在的契约

主循环用自己的 `_quote_me`（引用的原主是 Bot 才算"引用我必回"），
并把结果写进缓冲区的 `quote_me` 字段（此前 `buffer.get("quote_me")` 恒 False）。
`_pipeline_step` 内部仍用局部 `quote_forces`，两者互不越界。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

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
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    lines = src.split("\n")

    print("[1] 主循环不再引用未定义变量")
    # 合法持有者：① _pipeline_step（群批次主体，局部定义 quote_forces）
    #            ② _image_understanding_needed（**形参**就叫 quote_forces）
    ALLOWED = {"_pipeline_step", "_image_understanding_needed"}
    bad = []
    for i, ln in enumerate(lines, 1):
        if re.search(r"or quote_forces\s*:", ln):
            # 往上找最近的 def
            fn = ""
            for j in range(i - 1, max(0, i - 400), -1):
                m = re.match(r"^def (\w+)", lines[j - 1])
                if m:
                    fn = m.group(1)
                    break
            if fn not in ALLOWED:
                bad.append(f"L{i} in {fn or '?'}: {ln.strip()[:70]}")
    check("`or quote_forces:` 只出现在合法作用域内", not bad, bad)
    # 再确认 _image_understanding_needed 确实以它作形参（否则上面就放过了一个真 bug）
    check("_image_understanding_needed 的形参确实叫 quote_forces",
          re.search(r"def _image_understanding_needed\([^)]*quote_forces", src) is not None)

    print("\n[2] 主循环用 _quote_me，且先定义后使用")
    # ★ 2026-10-27 结构变更：判据抽到引用注记那段（`quote_me`，含三条兜底），
    #   `_quote_me` 只是它的只读副本 —— 不再在 12000 行处重算一遍。
    #   原来分两处算，会出现「prompt 里写'引用我，必回'、forced 却按'不是我'
    #   放行给决策器」，同一件事两个消费者结论打架。
    init_pos = src.find("quote_me = False")
    judge_pos = src.find('quote_me = bool(quoted_sender) and str(quoted_sender) == str(BOT_QQ)')
    def_pos = src.find("_quote_me = bool(quote_me)")
    use_pos = src.find("or has_bot_nick or _quote_me)")
    check("找到 quote_me 的初值（每条消息重置）", init_pos > 0, f"init@{init_pos}")
    check("找到「原主是 Bot」的判据", judge_pos > 0, f"judge@{judge_pos}")
    check("找到 _quote_me 的定义", def_pos > 0, f"def@{def_pos}")
    check("找到 _quote_me 的使用", use_pos > 0, f"use@{use_pos}")
    check("★ 初值 → 判据 → 定义 → 使用，顺序不能倒",
          0 < init_pos < judge_pos < def_pos < use_pos,
          f"init@{init_pos} judge@{judge_pos} def@{def_pos} use@{use_pos}")
    check("_quote_me 只做副本，不重算判据（防两处判据打架）",
          "_quote_me = (bool(has_quote)" not in src)

    print("\n[3] 结果写进缓冲区（此前 quote_me 恒 False）")
    check("缓冲区写入 quote_me", '"quote_me": _quote_me' in src)
    check("_pipeline_step 真的读它",
          'buffer.get("quote_me", False)' in src)

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