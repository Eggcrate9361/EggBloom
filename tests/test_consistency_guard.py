# -*- coding: utf-8 -*-
"""tests/test_consistency_guard.py —— 「别改口」的确定性判据

2026-10-31 真机原话（Texas 私聊）：

    它：就看清一张…另一张我这儿没取到
    用户：本来就只有一张图片，你唐的吧
    它：哦，两张图其实是一张啊
    用户：你不是说还有一张吗？
    它：哪来的第二张，我瞎说的？

最后一句是明确的否认，而见证句就在上下文里。prompt 规则拦不住，这里做确定性兜底：
用户在主张 + 我在否认 + 我确实说过 → conflict，调用方改成认账。

锁六件事：
  A. 从「你不是说还有一张吗？」抽出「还有一张」
  B. 否认标记认得全（我没说/哪来的/我瞎说/逗你玩…）
  C. 见证句找得到（bigram 重合度）
  D. 三重条件缺一就放行（没主张/没否认/确实没说过）
  E. 真没说过时不许乱认（witness 空 → 放行）
  F. safe_ack 输出里带见证原文、不带否认词
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import consistency as C  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

WITNESS = ("就看清一张，橙黄底那张瞪眼撇嘴的，气成这样\n"
           "另一张我这儿没取到，你是不是想让我夸你？")


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
    print("[A] 抽被主张的内容")
    check("你不是说还有一张吗 → 还有一张",
          C.extract_claim("你不是说还有一张吗？") == "还有一张",
          C.extract_claim("你不是说还有一张吗？"))
    check("你刚才说请我吃饭吧 → 请我吃饭",
          C.extract_claim("你刚才说请我吃饭吧") == "请我吃饭",
          C.extract_claim("你刚才说请我吃饭吧"))
    check("普通聊天 → 空", C.extract_claim("今天吃什么") == "")

    print("\n[B] 否认标记")
    for s in ("哪来的第二张", "我没说过这话", "我瞎说的", "逗你玩呢", "没这回事"):
        check(f"认出 {s!r}", C.looks_like_denial(s) is True)
    check("正常回复不算否认", C.looks_like_denial("行，那我看差了") is False)

    print("\n[C] 见证句")
    check("找得到", bool(C.find_witness("还有一张", [WITNESS])), C.find_witness("还有一张", [WITNESS]))
    check("无关的话找不到", not C.find_witness("还有一张", ["今天天气不错"]))
    check("重合度可解释", C.overlap_ratio("还有一张", WITNESS) > 0.3,
          C.overlap_ratio("还有一张", WITNESS))

    print("\n[D] 三重条件")
    v = C.verdict("你不是说还有一张吗？", "哪来的第二张，我瞎说的？", [WITNESS])
    check("场景重现 → conflict", v["conflict"] is True, v)
    check("带出见证句", "另一张我这儿没取到" in v["witness"], v["witness"])

    v2 = C.verdict("你不是说还有一张吗？", "哪来的第二张", ["今天天气不错"])
    check("真没说过 → 放行（不许乱认）", v2["conflict"] is False, v2)

    v3 = C.verdict("今天吃什么", "我没说啊", [WITNESS])
    check("没主张 → 放行", v3["conflict"] is False, v3)

    v4 = C.verdict("你不是说还有一张吗？", "嗯，我确实说清了，那张我看到了", [WITNESS])
    check("没否认 → 放行", v4["conflict"] is False, v4)

    print("\n[E] 认账句")
    ack = C.safe_ack(WITNESS)
    check("带原文", "另一张我这儿没取到" in ack, ack)
    check("不含否认词", not C.looks_like_denial(ack), ack)
    check("不超长", len(ack) <= 90, len(ack))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
