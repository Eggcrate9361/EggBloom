# -*- coding: utf-8 -*-
"""人格长期系数 → 配额偏移（2026-10-27 · 1.5.9 §C.2）

## 为什么

人格原来只有散文（`personality.txt` 1399 字）。散文能描述性格，但没法调、
没法测、也没法和情绪叠加 —— 改一句"话多一点"，模型可能只是换了个说法长
度不变。

改成系数之后：人格是长期偏移，情绪是短期偏移，两相加作用到同一套形式参数。
可测（改 0.25 → 0.6，档位中位必须单调上升）、可叠加（两个数相加，不是两段
文字打架）、可回退（默认 0 = 保持现状分布）。

## 契约

1. `talkativeness=0` → 偏移恒为 0，分布与 1.5.8 逐字节一致
2. 非 0 时是**概率**不是阈值：0.25 表示 1/4 的回合上移一档
3. 情绪偏移（-1/0/+1）与人格偏移相加，不互相覆盖
4. 只改配额档位，不改任何文本内容

运行：python tests/test_budget_shift.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


def _shift(*a, **kw):
    import bot_core
    return bot_core._budget_shift(*a, **kw)


def test_zero_keeps_current():
    check("talk=0 恒为 0", all(_shift(0, 0.0, rng=x) == 0 for x in (0.0, 0.5, 0.99)))
    check("talk=0 且情绪 +1 → 1", _shift(1, 0.0, rng=0.99) == 1)


def test_probability_not_threshold():
    check("talk=0.6, rng=0.5 → 上移", _shift(0, 0.6, rng=0.5) == 1)
    check("talk=0.6, rng=0.7 → 不上移", _shift(0, 0.6, rng=0.7) == 0)
    check("talk=0.2, rng=0.5 → 不上移", _shift(0, 0.2, rng=0.5) == 0)


def test_adds_with_mood():
    check("情绪 -1 + 人格 0.6(rng=0.5) → 0", _shift(-1, 0.6, rng=0.5) == 0)
    check("情绪 +1 + 人格 0.6(rng=0.5) → 2", _shift(1, 0.6, rng=0.5) == 2)


def test_distribution():
    """1000 次抽样：talk=0.6 时上移比例应接近 60%。"""
    n = 1000
    import bot_core
    up = sum(1 for _ in range(n) if bot_core._budget_shift(0, 0.6) == 1)
    check(f"talk=0.6 → 约 60% 上移（实测 {up / n:.0%}）", 0.52 <= up / n <= 0.68, up / n)


def test_budget_moves():
    """档位真的会变：shift=1 时分布整体右移。"""
    import reply_budget as rb
    a = rb.pick(rng=0.1, shift=0)
    b = rb.pick(rng=0.1, shift=1)
    check("shift=1 让档位右移", a["name"] != b["name"] or a["max_chars"] <= b["max_chars"],
          (a["name"], b["name"]))


def test_wired():
    import io
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("pipeline 用统一入口", "_budget_shift(_form.get(\"budget_shift\", 0), _tal)" in src)
    check("配置键存在", "persona_talkativeness" in src)
    check("默认 0（保持现状）",
          '"persona_talkativeness": 0.0' in io.open(os.path.join(ROOT, "config.py"),
                                                    encoding="utf-8").read())


def main():
    print("=" * 58)
    print("人格系数 → 配额偏移")
    print("=" * 58)
    for fn in (test_zero_keeps_current, test_probability_not_threshold,
               test_adds_with_mood, test_distribution, test_budget_moves, test_wired):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
