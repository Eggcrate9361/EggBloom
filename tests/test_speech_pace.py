# -*- coding: utf-8 -*-
"""说话节奏：按人算"还要不要等"（2026-10-27 · 通用化）

## 为什么

固定 3 秒 / 8 秒都是从一次观测反推的：有人 1 秒一条被抢答，有人 6 秒一条
被拆成两批。换个人就不成立。

判据改成"他自己的节奏"：

    quiet = max(1.8, 2.5 × 他最近几条间隔的中位数)
    cap   = max(6.0, 4.0 × 同一个中位数)

没有历史时才退回 2.5 秒——那是"还不知道"，不是设计值。

## 契约

1. 首次发言不产生间隔样本，quiet/cap 用兜底
2. 间隔 > 120 秒不算连发，不进样本
3. 样本只保留最近 8 个，跟着人变
4. 说快的人等得短，说慢的人等得长，两端都夹在 min_quiet / cap 下限之上

运行：python tests/test_speech_pace.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from speech_pace import SpeechPace, DEFAULT_PACE, MIN_QUIET

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


def _feed(pace, key, gaps, start=1000.0):
    t = start
    pace.note(key, t)
    for g in gaps:
        t += g
        pace.note(key, t)
    return t


def test_cold_start():
    p = SpeechPace()
    check("首次发言不产生间隔", p.note("a", 1000.0) is None)
    check("无历史用兜底 pace", abs(p.median_gap("a") - DEFAULT_PACE) < 1e-9, p.median_gap("a"))
    check("兜底 quiet = 2.5×pace", abs(p.quiet_needed("a") - 6.25) < 1e-9, p.quiet_needed("a"))
    check("兜底 cap = 4×pace", abs(p.hard_cap("a") - 10.0) < 1e-9, p.hard_cap("a"))


def test_fast_talker():
    p = SpeechPace()
    _feed(p, "fast", [1.0, 1.0, 1.0, 1.0])
    check("1 秒一条 → pace≈1", abs(p.median_gap("fast") - 1.0) < 0.01, p.median_gap("fast"))
    check("1 秒一条 → quiet 只等 2.5 秒", abs(p.quiet_needed("fast") - 2.5) < 0.01, p.quiet_needed("fast"))
    check("1 秒一条 → cap 6 秒", abs(p.hard_cap("fast") - 6.0) < 0.01, p.hard_cap("fast"))


def test_slow_talker():
    p = SpeechPace()
    _feed(p, "slow", [8.0, 8.0, 8.0])
    check("8 秒一条 → pace≈8", abs(p.median_gap("slow") - 8.0) < 0.01, p.median_gap("slow"))
    check("8 秒一条 → quiet 等到 20 秒", abs(p.quiet_needed("slow") - 20.0) < 0.01, p.quiet_needed("slow"))
    check("8 秒一条 → cap 32 秒", abs(p.hard_cap("slow") - 32.0) < 0.01, p.hard_cap("slow"))


def test_mixed_is_median():
    p = SpeechPace()
    _feed(p, "mix", [1.0, 1.0, 2.0, 30.0])
    # 中位数取 (1,2) 中间 → 1.5，不被 30 秒那条带跑
    check("中位数不吃极端值", abs(p.median_gap("mix") - 1.5) < 0.01, p.median_gap("mix"))


def test_gap_outlier_ignored():
    p = SpeechPace()
    _feed(p, "x", [2.0, 2.0])
    before = p.median_gap("x")
    _feed(p, "x", [600.0])          # 10 分钟后再说话，不算连发
    check("超长间隔不进样本", abs(p.median_gap("x") - before) < 1e-9, p.median_gap("x"))


def test_history_window():
    p = SpeechPace(history=3)
    _feed(p, "h", [5.0, 5.0, 5.0, 1.0, 1.0, 1.0])
    check("只保留最近 3 个样本 → pace≈1", abs(p.median_gap("h") - 1.0) < 0.01, p.median_gap("h"))


def test_per_person():
    p = SpeechPace()
    _feed(p, "a", [1.0, 1.0])
    _feed(p, "b", [10.0, 10.0])
    check("节奏按人隔离", p.quiet_needed("a") < p.quiet_needed("b"),
          (p.quiet_needed("a"), p.quiet_needed("b")))
    p.forget("a")
    check("forget 后回到兜底", abs(p.quiet_needed("a") - 6.25) < 1e-9, p.quiet_needed("a"))


def test_min_quiet_floor():
    p = SpeechPace()
    _feed(p, "z", [0.2, 0.2, 0.2])
    check("极快的人也不低于 min_quiet", p.quiet_needed("z") >= MIN_QUIET - 1e-9,
          p.quiet_needed("z"))


def test_replay_real_batch():
    """用 2026-10-27 16:30 那批的真实时间戳回放：旧参数切 2 批，节奏判据 1 批。"""
    ts = [34, 34, 36, 38, 39, 41]      # 从日志 [事件] 收到 抄的秒数

    def _batches(quiet, cap, use_pace=False):
        p = SpeechPace()
        out, cur = [], None
        for t in ts:
            if use_pace:
                p.note("u", float(t)); q, c = p.quiet_needed("u"), p.hard_cap("u")
            else:
                q, c = quiet, cap
            if cur is None:
                cur = {"first": t, "items": [t]}
            else:
                cur["items"].append(t)
            cur["deadline"] = min(t + q, cur["first"] + c)
            if t >= cur["deadline"]:
                out.append(cur); cur = None
        if cur:
            out.append(cur)
        return out

    old_b = _batches(3, 5)
    new_b = _batches(0, 0, use_pace=True)
    check("旧参数会切分", len(old_b) >= 2, len(old_b))
    check("节奏判据全收进一批", len(new_b) == 1, [b["items"] for b in new_b])
    # 全新实例没有样本 → 返回兜底 2.5，这是"还不知道"，不是设计值
    check("无样本时退回兜底 pace", abs(SpeechPace().median_gap("u") - 2.5) < 1e-9)


def test_wired():
    import io
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("bot_core 用了 SpeechPace", "_PRIVATE_PACE = speech_pace.SpeechPace()" in src)
    check("私聊进入时采样", "_PRIVATE_PACE.note(sender, now)" in src)
    check("有开关可退回固定值", "speech_pace_enable" in src)


def main():
    print("=" * 58)
    print("说话节奏")
    print("=" * 58)
    for fn in (test_cold_start, test_fast_talker, test_slow_talker, test_mixed_is_median,
               test_gap_outlier_ignored, test_history_window, test_per_person,
               test_min_quiet_floor, test_replay_real_batch, test_wired):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
