# -*- coding: utf-8 -*-
"""回复预算（2026-10-27 · 1.5.9 §B3）

## 为什么

「写短一点」在 prompt 里存在过三个版本，长度从来没被管住。而决策器里的
数字约束（followup_max_chars=40）管住了。所以这里给分布，不给形容词：
60% 只说 1 条 ≤8 字，30% 1–2 条 ≤16 字，10% 2–3 条 ≤40 字。

## 契约

1. 概率分布加起来是 1，`pick` 在边界上不会漏档
2. `check` 先按档位判、再按硬上限兜底（档位是目标，硬上限是底线）
3. 表情标记与结构标记不算字数
4. 超配额用 `fit` 取最短一条，**不做截断** —— 半句比长句更假
5. `anchor_ok` 守下限：与触发消息共享 2-gram 或 ≥8 字，防退化成「哦」

运行：python tests/test_reply_budget.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import reply_budget as rb

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


def test_distribution():
    total = sum(b[1] for b in rb.BUDGETS)
    check("概率合计为 1", abs(total - 1.0) < 1e-9, total)
    check("rng=0 → A", rb.pick(0.0)["name"] == "A", rb.pick(0.0))
    check("rng=0.59 → A", rb.pick(0.59)["name"] == "A", rb.pick(0.59))
    check("rng=0.60 → B", rb.pick(0.60)["name"] == "B", rb.pick(0.60))
    check("rng=0.89 → B", rb.pick(0.89)["name"] == "B", rb.pick(0.89))
    check("rng=0.90 → C", rb.pick(0.90)["name"] == "C", rb.pick(0.90))
    check("rng=0.999 → C", rb.pick(0.999)["name"] == "C", rb.pick(0.999))


def test_prompt_line():
    a = rb.prompt_line(rb.pick(0.0))
    check("A 档说 1 条 8 字", "1 条" in a and "8 个字" in a, a)
    check("A 档禁问", "不要问" in a, a)
    c = rb.prompt_line(rb.pick(0.95))
    check("C 档给区间", "2–3 条" in c and "40 个字" in c, c)
    check("都说了不要解释/总结", all("不要解释" in rb.prompt_line(rb.pick(x))
                                for x in (0.1, 0.7, 0.95)))


def test_visible():
    check("表情标记不算字数", rb.visible("[表情:笑]") == "")
    check("图片标记不算字数", rb.visible("[图片] 一碗面") == "一碗面")
    check("空白不算字数", rb.visible(" 你 吃 了 吗 ") == "你吃了吗")


def test_check():
    a = rb.pick(0.0)
    ok, why, st = rb.check("吃了吗", a)
    check("合规短句通过", ok, (ok, why, st))
    ok, why, st = rb.check("我今天真的是一点都不想去上课了你知道吗", a)
    check("超字数被挡", not ok and "超过" in why, why)
    ok, why, st = rb.check("呃\n好的\n行吧", a)
    check("超条数被挡", not ok and "条超过" in why, why)
    ok, why, st = rb.check("", a)
    check("空回复被挡", not ok and "空" in why, why)
    # 硬上限兜底：C 档也压不过 16 字
    c = rb.pick(0.95)
    long_line = "这" * 30
    ok, why, st = rb.check(long_line, c, {"max_chars": 16})
    check("硬上限压过档位", not ok and "16" in why, why)


def test_fit():
    """★ 2026-10-27 改判据：原来取**最短**的一条，真机证明是错的。

    真机（18:55）模型给 2 条共 33 字（超 B 档 32 字）：
      "你问点能查的行不行？"              (23 字) ← 敷衍
      "[10-27 18:55] 记不住，我又不是录音机" (10 字) ← 正面回答
    旧版取最短 → 发了敷衍那句，把有内容的扔了。

    「最短」和「最该发」不是一回事 —— 短的常常是空的（"嗯""行吧"）。
    """
    # ① 纯敷衍的短句不该被选中
    text = "第一句比较长一点点\n短\n第三句也还行吧"
    got = rb.fit(text, rb.pick(0.95))
    check("不选纯敷衍的短句", got != "短", got)
    check("选的是有内容的那条", got in ("第一句比较长一点点", "第三句也还行吧"), got)

    # ② 真机那组：短的才是正面回答
    real = "你问点能查的行不行？\n[10-27 18:55] 记不住，我又不是录音机"
    got2 = rb.fit(real, rb.pick(0.95))
    check("真机组：选有内容的那条", "记不住" in got2, got2)
    check("真机组：不选敷衍那句", "你问点能查" not in got2, got2)

    # ③ 完整句优先于半截
    half = "这句话还没说完，\n我也不知道"
    got3 = rb.fit(half, rb.pick(0.95))
    check("不选以逗号收尾的半截", not got3.endswith("，"), got3)

    # ④ 单条时原样返回
    check("单条原样返回", rb.fit("就一句话", rb.pick(0.95)) == "就一句话")
    check("空输入安全", rb.fit("", rb.pick(0.95)) == "")


def test_anchor_ok():
    check("够长直接通过", rb.anchor_ok("我今天不太想去上课", "去上课吗"))
    check("短但与触发共享 2-gram", rb.anchor_ok("上课", "去上课吗"))
    check("短且无关联 → 判空", not rb.anchor_ok("哦", "去上课吗"))
    check("嗯+无关触发 → 判空", not rb.anchor_ok("嗯", "显卡多少钱"))


def test_regen_hint():
    hint = rb.regen_hint("共 42 字超过 8", rb.pick(0.0))
    check("重生提示带原因与数字", "42" in hint and "8" in hint, hint)


def main():
    print("=" * 58)
    print("回复预算（B3）")
    print("=" * 58)
    for fn in (test_distribution, test_prompt_line, test_visible, test_check,
               test_fit, test_anchor_ok, test_regen_hint):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
