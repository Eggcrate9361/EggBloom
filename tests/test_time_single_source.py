# -*- coding: utf-8 -*-
"""时间线单一来源（1.5.9 提交 9，2026-10-28）。

核查报告留了一条：「`de_ai.py` 还有一套独立的时段词表，与 `harness/time_ctx.py`
的 `_PERIODS` 边界不一致（time_ctx 上午从 8 点，de_ai 从 7 点）→ 同一时刻可能
判出相反结论」。

现在 de_ai 只声明"这个词算哪个时段"，边界一律从 `time_ctx.hours_of()` 取。

这个文件钉住：
1. `hours_of()` 的边界就是 `_PERIODS`（跨天会折回 0–23）。
2. de_ai 每个词的合法小时集合 == time_ctx 对该词所属时段的定义（同一权威）。
3. 真机语料的行为：13:39 的「大清早」要删、13:40 的「下午一点四十」要留、
   23:28 的「大半夜」要留（旧表把它误删了）。
4. 拿不到 time_ctx 时整条判据跳过，不崩、不误删。

运行：python tests/test_time_single_source.py
"""
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from harness import time_ctx  # noqa: E402
import de_ai  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def _ts(h, m=0):
    return datetime.datetime.now().replace(
        hour=h, minute=m, second=0, microsecond=0).timestamp()


def test_hours_of():
    print("\n[1] hours_of 的边界来自 _PERIODS")
    check("清晨 = 5–7", sorted(time_ctx.hours_of("清晨")) == [5, 6, 7],
          sorted(time_ctx.hours_of("清晨")))
    check("上午 = 8–10", sorted(time_ctx.hours_of("上午")) == [8, 9, 10])
    check("中午+下午 = 11–16",
          sorted(time_ctx.hours_of("中午", "下午")) == list(range(11, 17)))
    check("深夜跨天折回 = {23}", sorted(time_ctx.hours_of("深夜")) == [23])
    check("空参数 = 空集", time_ctx.hours_of() == frozenset())
    check("未知名 = 空集", time_ctx.hours_of("不存在的时段") == frozenset())


def test_same_authority():
    print("\n[2] de_ai 的词表与 time_ctx 同源")
    spec = dict(de_ai._TIME_PERIOD_SPEC)
    bad = []
    for word, hours in (de_ai._TIME_PERIOD_WORDS or ()):
        want = time_ctx.hours_of(*spec[word])
        if set(hours) != set(want):
            bad.append((word, sorted(hours), sorted(want)))
    check("★ 每个词的窗口都等于 time_ctx 的定义", not bad, bad[:3])
    check("词表已加载（拿到 time_ctx）", bool(de_ai._TIME_PERIOD_WORDS))


def test_real_corpus():
    print("\n[3] 真机语料（super_memory.db 里的原文）")
    # 13:39 真机：大清早 → 删词
    out = de_ai._strip_time_contradictions(
        "理由倒是挺充分\n行，那我也不客气\n你他妈才傻逼，大清早不干正事跑来找骂",
        _ts(13, 39))
    check("13:39 「大清早」被删", "大清早" not in out and "不干正事" in out, out)

    # 13:40 真机：整句自我纠正 → 整句删，且"下午一点四十"不能被动
    out = de_ai._strip_time_contradictions(
        "可爱个屁\n这是骂你，你给我正经挨着\n还有，现在下午一点四十，谁大清早",
        _ts(13, 40))
    check("13:40 自我纠正句整句删", "谁大清早" not in out, out)
    check("前两行保留", out.startswith("可爱个屁") and "正经挨着" in out, out)

    # 13:43 真机：这句是**对的**，一个字都不能动
    src = "睡什么睡，下午一点四十？\n\n你让我睡我就睡？我不用上工的吗？"
    out = de_ai._strip_time_contradictions(src, _ts(13, 43))
    check("★ 13:43 「下午一点四十」原样保留", out == src, out)

    # 23:28 真机：「大半夜」是对的（旧表 h<=5 会把它删掉）
    src = "爱慕就说一遍呗 说两遍是想让我感动吗\n\n大半夜的不睡觉在这爱慕 你也挺行"
    out = de_ai._strip_time_contradictions(src, _ts(23, 28))
    check("★ 23:28 「大半夜」保留（旧实现误删）", out == src, out)

    # 11:08 真机：「大中午」是对的
    src = "咕咕嘎嘎\n叫我干嘛呀，大中午的？"
    out = de_ai._strip_time_contradictions(src, _ts(11, 8))
    check("11:08 「大中午」保留", out == src, out)

    # 11:38 真机：问候语不删
    src = "刀下留人\n\n我重新组织：您好，群主大人，早上好"
    out = de_ai._strip_time_contradictions(src, _ts(11, 38))
    check("11:38 「早上好」是问候，不删", out == src, out)

    # 反例：8:30 说「大清早」确实错（time_ctx 眼里 8 点已是上午）
    out = de_ai._strip_time_contradictions("大清早的吵什么\n行吧", _ts(8, 30))
    check("8:30 「大清早」被删（边界跟随 time_ctx）", "大清早" not in out, out)


def test_fallback():
    print("\n[4] 拿不到 time_ctx → 整条判据跳过（不误删）")
    old = de_ai._TIME_PERIOD_WORDS
    de_ai._TIME_PERIOD_WORDS = None
    try:
        src = "大清早的吵什么\n行吧"
        out = de_ai._strip_time_contradictions(src, _ts(13, 0))
        check("不崩且原样返回", out == src, out)
    finally:
        de_ai._TIME_PERIOD_WORDS = old


def main():
    test_hours_of()
    test_same_authority()
    test_real_corpus()
    test_fallback()

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
