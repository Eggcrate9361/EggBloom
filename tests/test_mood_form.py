# -*- coding: utf-8 -*-
"""情绪二维与形式参数（2026-10-27 · 1.5.9 §C.1）

## 为什么

旧 `mood_engine` 是单维 `score ∈ [-10,10]`，只有"开心/难过"一条轴。
但决定说话**长短快慢**的是兴奋度（arousal），不是好坏（valence）：
开心可以是话痨也可以是懒得理人。

更实际的问题：`get_prompt_block()` 只要不是 neutral 就返回
「【当前心情】开心（4）」进 system —— 模型会把"心情"和分数念出来，
与 1.5.8 刚修掉的汇报腔同源。

## 契约

1. 情绪只改形式参数（档位 / 延迟 / 表情概率），**不进 prompt**
2. 中性必须正好是 `budget_shift=0 / delay_scale=1.0` —— 否则"没情绪"也在改行为
3. 关闭情绪（`mood_enabled=False`）与没有情绪记录逐字节等价
4. 只有 |valence| 极端时才给一句不带数字的提示

运行：python tests/test_mood_form.py
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


def _engine():
    from plugins.ego.mood_engine import _mood, _NEUTRAL_FORM
    return _mood, _NEUTRAL_FORM


def _zero_baseline():
    """把日内基线固定为 0。

    ★ 为什么必须固定（2026-10-27 修）：`_effective()` 会叠加日内基线，
      而基线**随当前时刻变**（晚上 +1.2、凌晨 -0.8）。
      于是"score=-9 → valence=-0.9 → 该给负向提示"这个断言，
      在晚上跑就变成 (-9+1.2)/10 = -0.78，**差 0.02 刚好不触发** ——
      测试在晚上失败、白天通过，取决于跑测试的时刻。

      这类"随时间漂移的判据"必须在测试里被固定住，否则它测的是钟点。
    """
    m, _ = _engine()
    old = m.config.get("circadian_baseline")
    m.config["circadian_baseline"] = {"凌晨": 0.0, "清晨": 0.0, "上午": 0.0,
                                      "中午": 0.0, "下午": 0.0, "傍晚": 0.0,
                                      "晚上": 0.0, "深夜": 0.0}
    return old


def _restore_baseline(old):
    m, _ = _engine()
    if old is None:
        m.config.pop("circadian_baseline", None)
    else:
        m.config["circadian_baseline"] = old


def _set(score=0.0, arousal=0.0, gid="__test_mood__"):
    import time
    m, _ = _engine()
    m.moods[gid] = {"score": float(score), "arousal": float(arousal),
                    "updated": time.time()}
    return gid


def test_neutral_is_zero():
    m, neutral = _engine()
    gid = _set(0.0, 0.0)
    f = m.get_form_params(gid)
    check("中性档位不偏移", f["budget_shift"] == 0, f)
    check("中性延迟系数为 1", abs(f["delay_scale"] - 1.0) < 0.02, f)
    check("中性表情加成接近 0", abs(f["emoji_bonus"]) < 0.02, f)
    check("中性不打软标点", f["soft_punct"] is False, f)
    check("中性语气池为 mid", f["filler_pool"] == "mid", f)
    check("关闭情绪 == 中性", neutral["budget_shift"] == 0
          and neutral["delay_scale"] == 1.0, neutral)


def test_arousal_drives_form():
    m, _ = _engine()
    # 1.5.9 §C.1 / #4：arousal 值域改 −1~1（0 = 中性），旧的 0.85/0.15
    # 要减 0.3 才是新域里的同一个点。
    hi = _set(4.0, 0.55)
    f_hi = m.get_form_params(hi)
    check("高兴奋 → 档位上移", f_hi["budget_shift"] == 1, f_hi)
    check("高兴奋 → 延迟更短", f_hi["delay_scale"] < 0.9, f_hi)
    lo = _set(-2.0, -0.5)
    f_lo = m.get_form_params(lo)
    check("低兴奋 → 档位下移", f_lo["budget_shift"] == -1, f_lo)
    check("低兴奋 → 软标点", f_lo["soft_punct"] is True, f_lo)
    check("低兴奋 → 延迟更长", f_lo["delay_scale"] >= 0.99, f_lo)
    check("低兴奋 → 表情加成不为正", f_lo["emoji_bonus"] <= 0, f_lo)


def test_valence_drives_pool():
    m, _ = _engine()
    g1 = _set(6.0, 0.2)
    check("正向 → high 语气池", m.get_form_params(g1)["filler_pool"] == "high",
          m.get_form_params(g1))
    g2 = _set(-6.0, 0.2)
    check("负向 → low 语气池", m.get_form_params(g2)["filler_pool"] == "low",
          m.get_form_params(g2))


def test_prompt_block():
    m, _ = _engine()
    _old_bl = _zero_baseline()
    try:
        mid = _set(1.0, 0.1)
        check("普通情绪不注入", m.get_prompt_block(mid) == "", m.get_prompt_block(mid))
        hi = _set(9.0, 0.3)
        blk = m.get_prompt_block(hi)
        check("极端正向给短提示", "心情不错" in blk, blk)
        check("提示里没有数字", not any(ch.isdigit() for ch in blk), blk)
        lo = _set(-9.0, 0.3)
        blk2 = m.get_prompt_block(lo)
        check("极端负向给短提示", "烦" in blk2, blk2)
        check("旧格式已消失", "【当前心情】" not in blk + blk2, blk + blk2)
    finally:
        _restore_baseline(_old_bl)


def test_mood_disabled():
    m, _ = _engine()
    gid = _set(9.0, 0.6)
    old = m.config.get("mood_enabled", True)
    m.config["mood_enabled"] = False
    try:
        f = m.get_form_params(gid)
        check("关闭情绪 → 全中性", f["budget_shift"] == 0 and f["delay_scale"] == 1.0, f)
        check("关闭情绪 → 不注入", m.get_prompt_block(gid) == "", m.get_prompt_block(gid))
    finally:
        m.config["mood_enabled"] = old


def test_old_data_compat():
    """旧 mood.json 没有 arousal 字段，不能读成"低迷"。"""
    import time
    m, _ = _engine()
    gid = "__test_mood_old__"
    m.moods[gid] = {"score": 0.0, "updated": time.time()}
    f = m.get_form_params(gid)
    check("旧数据中性不落到下移档", f["budget_shift"] == 0, f)
    check("旧数据中性 arousal = 0（新域原点）", abs(f["arousal"]) <= 0.02, f)


def test_arousal_domain_migration():
    """旧 mood.json 里 0.3 = 中性；迁移后必须是 0，否则历史情绪全被读成亢奋。"""
    import time
    m, _ = _engine()
    gid = "__test_mood_migrate__"
    old_marker = m.config.pop("_arousal_domain", None)
    old_mood = m.moods.get(gid)
    m.moods[gid] = {"score": 0.0, "arousal": 0.3, "updated": time.time()}
    try:
        m._migrate_arousal_domain()
        check("★ 0.3 → 0.0", abs(m.moods[gid]["arousal"]) < 1e-6, m.moods[gid])
        m._migrate_arousal_domain()          # 第二次：标记已写，不该再减
        check("幂等：第二次不再减 0.3", abs(m.moods[gid]["arousal"]) < 1e-6, m.moods[gid])
        check("标记已写", m.config.get("_arousal_domain") == "bipolar", m.config.get("_arousal_domain"))
        f = m.get_form_params(gid)
        check("迁移后是中性档", f["budget_shift"] == 0 and f["soft_punct"] is False, f)
    finally:
        if old_mood is None:
            m.moods.pop(gid, None)
        else:
            m.moods[gid] = old_mood
        if old_marker is None:
            m.config.pop("_arousal_domain", None)
        else:
            m.config["_arousal_domain"] = old_marker


def main():
    print("=" * 58)
    print("情绪二维与形式参数（C.1）")
    print("=" * 58)
    for fn in (test_neutral_is_zero, test_arousal_drives_form,
               test_valence_drives_pool, test_prompt_block,
               test_mood_disabled, test_old_data_compat,
               test_arousal_domain_migration):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
