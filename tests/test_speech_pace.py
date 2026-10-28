# -*- coding: utf-8 -*-
"""说话节奏：全动态（2026-10-27）

## 三版

    固定 3 秒              拍脑袋
    2.5 × 中位数           窗口动，系数仍拍
    三判据取最大 + 结果校准  ← 这一版

## 判据

    quiet = max(3.0, P90×1.5, median×2.5) × 校准倍数
    cap   = max(8.0, quiet×4.0)

为什么取最大而不是单一分位：实测那批间隔是 [0,2,2,1,2]，P90=2 算出来 quiet=2.3，
而 38 秒那条距上一条正好 2 秒 —— 差 0.3 秒就越界，整段被切成两半。
段内停顿和段间停顿在分布上重叠，一个分位分不开。

校准倍数：每次判定「他说完了」之后，看他多久又开口。
在 quiet 一半内又开口 → 判早了，倍数 ×1.10（上限 3.0）；
超过 3 倍 quiet 才开口 → 判对了，倍数 ×0.97（下限 0.6）。

## 契约

1. 无样本 → 保守 5 秒（「还不知道」，不是设计值）
2. 有 3 个以上间隔 → 切到三判据
3. 同一批数据，四种节奏的人都能收成一批（回放测试）
4. 锁不可重入：`_quiet_locked` 不许调 `median_gap`（曾因此死锁）

运行：python tests/test_speech_pace.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from speech_pace import SpeechPace, RATIO_MIN, RATIO_MAX

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


def _seeded(gaps, key="u"):
    p = SpeechPace()
    t = 100.0
    p.note(key, t)
    for g in gaps:
        t += g
        p.note(key, t)
    return p


def test_cold_start():
    p = SpeechPace()
    check("首次不产生间隔", p.note("u", 1000.0) is None)
    check("无样本 quiet 保守 5 秒", abs(p.quiet_needed("u") - 5.0) < 1e-9, p.quiet_needed("u"))
    check("无样本 cap 10 秒", abs(p.hard_cap("u") - 10.0) < 1e-9, p.hard_cap("u"))
    check("样本不足 3 个仍用兜底",
          abs(_seeded([1.0, 1.0]).quiet_needed("u") - 5.0) < 1e-9,
          _seeded([1.0, 1.0]).quiet_needed("u"))


def test_three_judges():
    fast = _seeded([1.0, 1.0, 1.0])
    check("1 秒一条 → quiet 3.0（3 秒下限生效）",
          abs(fast.quiet_needed("u") - 3.0) < 1e-6, fast.quiet_needed("u"))
    mid = _seeded([2.0, 2.0, 2.0])
    check("2 秒一条 → quiet 5.0（median×2.5 生效）",
          abs(mid.quiet_needed("u") - 5.0) < 1e-6, mid.quiet_needed("u"))
    slow = _seeded([8.0, 8.0, 8.0])
    # 8 秒一条：P90×1.5=12，median×2.5=20，取最大 → 20
    check("8 秒一条 → quiet 20.0（median×2.5 生效）",
          abs(slow.quiet_needed("u") - 20.0) < 1e-6, slow.quiet_needed("u"))


def test_replay_one_batch():
    """16:30 那批真实时间戳 [34,34,36,38,39,41]：四种节奏都必须收成一批。"""
    ts = [34, 34, 36, 38, 39, 41]

    def batches(pace):
        out, cur = [], None
        for t in ts:
            pace.note("u", float(t))
            q, c = pace.quiet_needed("u"), pace.hard_cap("u")
            if cur is not None and t > cur["deadline"]:
                out.append(cur)
                cur = None
            if cur is None:
                cur = {"first": t, "items": [t]}
            else:
                cur["items"].append(t)
            cur["deadline"] = min(t + q, cur["first"] + c)
        if cur:
            out.append(cur)
        return out

    for name, seed in (("冷启动", []), ("1 秒一条", [1.0, 1.0, 1.0]),
                       ("2 秒一条", [2.0, 2.0, 2.0]), ("8 秒一条", [8.0, 8.0, 8.0])):
        got = batches(_seeded(seed))
        check(f"「{name}」收成 1 批", len(got) == 1, [b["items"] for b in got])


def test_ratio_learning():
    p = _seeded([2.0, 2.0, 2.0])
    base = p.quiet_needed("u")
    p.mark_settled("u", 106.0)   # 上一条是 106（_seeded 喂到那）
    p.note("u", 106.5)          # 判定后 0.5 秒又开口 → 判早了
    up = p.quiet_needed("u")
    check("判早了 → 倍数上调", p.ratio("u") > 1.0, p.ratio("u"))
    check("判早了 → quiet 变长", up > base, (base, up))
    p.mark_settled("u", 300.0)
    p.note("u", 500.0)          # 判定后 200 秒才开口（远超 3×quiet）→ 判对了
    check("判对了 → 倍数回落", p.ratio("u") < up, p.ratio("u"))


def test_ratio_bounds():
    p = _seeded([2.0, 2.0, 2.0])
    for i in range(40):
        p.mark_settled("u", 500.0 + i * 2)
        p.note("u", 500.1 + i * 2)
    check("倍数有上限", p.ratio("u") <= RATIO_MAX + 1e-9, p.ratio("u"))
    check("倍数有下限", p.ratio("u") >= RATIO_MIN - 1e-9, p.ratio("u"))


def test_no_deadlock():
    """note() 会在锁内算 quiet；如果内部再调带锁方法就会挂住。"""
    p = SpeechPace()
    done = []
    for i in range(5):
        p.note("u", 100.0 + i)
        done.append(p.quiet_needed("u"))
    check("锁内计算不死锁", len(done) == 5, done)
    snap = p.snapshot()
    check("snapshot 可调用", "u" in snap, snap)


def test_per_person_isolated():
    p = SpeechPace()
    t = 100.0
    p.note("a", t)
    for g in (1.0, 1.0, 1.0):
        t += g
        p.note("a", t)
    t2 = 100.0
    p.note("b", t2)
    for g in (8.0, 8.0, 8.0):
        t2 += g
        p.note("b", t2)
    check("节奏按人隔离", p.quiet_needed("a") < p.quiet_needed("b"),
          (p.quiet_needed("a"), p.quiet_needed("b")))


def test_wired():
    import io
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("bot_core 用了 SpeechPace", "_PRIVATE_PACE = speech_pace.SpeechPace()" in src)
    check("私聊进入时采样", "_PRIVATE_PACE.note(sender, now)" in src)
    check("判完记一笔用于校准", "_PRIVATE_PACE.mark_settled(sender, time.time())" in src)
    check("有开关可退回固定值", "speech_pace_enable" in src)


def test_conversation_gap_not_burst():
    """2026-10-28 真机事故回归：对话轮间隔不能当成连发样本。

    Texas（1234567892）的真实节奏：每 27-31 秒发一条，发完**等回复**。
    旧版 MAX_SAMPLE=120 把这些间隔全进了样本 → P90≈30s →
    quiet=69-93s、cap=300s+，私聊回复等 2 分 14 秒，
    对方等不到只好自问自答，bot 的回复变成复读用户自己给的答案。
    """
    print("对话轮间隔（~30s）不得抬高连发窗口")
    # 复刻今晚的真实节奏：27-31 秒一条，连续 6 轮
    p = _seeded([27.0, 31.0, 28.0, 30.0, 29.0, 30.0])
    q = p.quiet_needed("u")
    cap = p.hard_cap("u")
    check("quiet 不超过 10 秒（冷启动兜底）", q <= 10.0, f"quiet={q:.1f}")
    check("cap 不超过 20 秒", cap <= 20.0, f"cap={cap:.1f}")
    check("30s 间隔没有进样本", p.median_gap("u") == 5.0,
          f"median={p.median_gap('u')}")
    # 真连发（1-3 秒）仍然被学习
    p2 = _seeded([2.0, 2.5, 3.0, 2.0, 2.5])
    q2 = p2.quiet_needed("u")
    check("真连发仍会学习（quiet≈3-8s）", 3.0 <= q2 <= 8.0, f"quiet={q2:.1f}")
    # 混合：先连发几句、再用 30s 间隔聊 —— 连发样本仍在，窗口不夸张
    p3 = _seeded([2.0, 2.0, 2.0, 30.0, 30.0])
    q3 = p3.quiet_needed("u")
    check("混合节奏下 quiet 有界（≤15s）", q3 <= 15.0, f"quiet={q3:.1f}")


def main():
    print("=" * 58)
    print("说话节奏（全动态）")
    print("=" * 58)
    for fn in (test_cold_start, test_three_judges, test_replay_one_batch,
               test_ratio_learning, test_ratio_bounds, test_no_deadlock,
               test_per_person_isolated, test_wired,
               test_conversation_gap_not_burst):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
