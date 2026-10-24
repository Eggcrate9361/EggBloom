# -*- coding: utf-8 -*-
"""日内情绪基线：只改倾向，不改标签。

mood_engine 原本只有"瞬时情绪"：一次事件 +1/-1，再按时间衰减。它没有
"这个点在犯困"这种底色 —— 凌晨和晚上同样静默一天，标签都是 neutral。

加了日内基线后必须守住三条，否则会变成"情绪永远不会变"：

  [1] 单独一条基线不足以把 neutral 顶成 happy/sad（结构保证，不靠默认值自觉）
  [2] 用户把基线段改到 999 也越不过硬夹
  [3] 基线只在读取时叠加，绝不写回 score（写回会被 update() 当成事件累积）
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import time_ctx  # noqa: E402
import plugins.ego.mood_engine as me  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def main():
    periods = time_ctx._PERIODS
    eng = me.MoodEngine()

    print("[1] 基线本身不得改标签：结构保证")
    for period_name in [r[1] for r in periods]:
        val = me._CIRCADIAN_BASELINE.get(period_name, 0.0)
        # _format 的判据是 score >= 2 → happy，<= -1 → sad
        check(f"  基线 {period_name}={val} 单独不足以改标签",
              me._BASELINE_FLOOR <= val <= me._BASELINE_CEIL
              and 0 + val < 2 and 0 + val > -1)
    check("基线表覆盖所有时段",
          set(me._CIRCADIAN_BASELINE) == {r[1] for r in periods})

    print("\n[2] 越界被夹住")
    eng.config["circadian_baseline"] = {"凌晨": 999, "晚上": -999}
    check("用户把基线段改爆也被夹住",
          -1 < eng._baseline_score() < 2, eng._baseline_score())
    # 当前时段是什么，取决于跑测试的钟点，所以两个方向各测一次
    eng.config["circadian_baseline"] = {r[1]: 999 for r in periods}
    check("全部时段填 999 仍被夹在上限", eng._baseline_score() <= me._BASELINE_CEIL,
          eng._baseline_score())
    eng.config["circadian_baseline"] = {r[1]: -999 for r in periods}
    check("全部时段填 -999 仍被夹在下限", eng._baseline_score() >= me._BASELINE_FLOOR,
          eng._baseline_score())

    print("\n[3] 只在读取时叠：不写回、不累积")
    eng.config["circadian_baseline"] = dict(me._CIRCADIAN_BASELINE)
    eng.moods["g1"] = {"score": 0, "updated": time.time()}
    first = eng.get_mood("g1")["score"]
    second = eng.get_mood("g1")["score"]
    check("连读两次基线不累积（没写回 score）", abs(first - second) < 1e-9,
          (first, second))
    check("score 本体没被基线污染", eng.moods["g1"]["score"] == 0,
          eng.moods["g1"]["score"])
    # 早退路径（没有这个群的情绪记录）也要叠基线
    eng.moods.pop("never", None)
    got = eng.get_mood("never")
    check("没记录时也叠基线（新群/重启后最常见的情况）",
          abs(got["score"] - eng._baseline_score()) < 1e-9, got["score"])
    # 开关关掉时基线不参与
    eng.config["mood_enabled"] = False
    off = eng.get_mood("g1")["score"]
    check("mood_enabled=False 时也叠（早退分支不能漏）", abs(off - eng._baseline_score()) < 1e-9,
          off)

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())