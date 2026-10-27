# -*- coding: utf-8 -*-
"""丢弃判据：按话头翻篇，不按生成耗时（2026-10-27 · 1.5.9 §B5）

## 为什么

旧判据是「生成超过 25 秒就丢」。这个数是按当时模型 11–26 秒的生成速度
反推的：换快模型就过严（发出陈年回复），换慢模型就误杀。

## 修订（2026-10-27 晚，12 次丢弃全量统计）

```
12 次丢弃：过时 11 / 话头翻篇 1
过时值：26.0 29.0 29.4 30.8 31.1 31.1 38.1 49.4 55.1 59.6 64.7
gen_send 分布（n=86）：p50=0.1 p90=14.2 p95=18.6 max=26.5
```

被丢的全在 26–65 秒，而配置里 `reply_stale_drop_sec=25`。
一次带搜索的回复 = 3 轮工具 ×（模型+搜索）+ 收尾，结构上不低于 25 秒
→ **凡搜索必丢**。这就是「所有调用搜索工具的问题都无法回复」的真身。

现在只问一件事：**这条回复发出去，还接得上话吗？**
没人说话 → 不丢（没人被打扰，晚回不算过时）。
线改由实测 p99 导出，不再拍数字。

## 契约

1. `new_msgs >= turns` → 丢，原因写「话头翻篇 N 条新消息」（主判据）
2. 1 条新消息不丢 —— 还在同一话头里
3. 时间不是主判据：**没新消息时 60 秒也照发**
4. 时间只做极端兜底，线由分布导出（见 reply_drop.LatencyProfile）
5. 官腔与时间无关，命中即丢
6. turns=0 关掉翻篇判据；hard_sec=0 关掉时间兜底（都是合法配置）

运行：python tests/test_reply_drop.py
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


def _why(**kw):
    import bot_core
    return bot_core._drop_reason(**kw)


import reply_drop  # noqa: E402

#: 真机实测 age 样本（丢弃日志 + 正常发送）
REAL_AGES = [26.0, 29.0, 29.4, 30.8, 31.1, 31.1, 38.1, 49.4,
             55.1, 59.6, 64.7, 8.0, 9.5, 11.0, 12.4, 14.2, 15.0, 16.8,
             18.6, 20.2, 21.0, 22.5, 26.5, 7.2, 13.3]


def test_turns_is_primary():
    check("2 条新消息 → 丢", _why(new_msgs=2, age=5).startswith("话头翻篇"), _why(new_msgs=2, age=5))
    check("1 条新消息 → 不丢", _why(new_msgs=1, age=30) == "", _why(new_msgs=1, age=30))
    check("0 条新消息 → 不丢（哪怕 30 秒）", _why(new_msgs=0, age=30) == "", _why(new_msgs=0, age=30))
    check("原因里带条数", "3 条" in _why(new_msgs=3, age=1), _why(new_msgs=3, age=1))


def test_real_dropped_values_now_pass():
    """★ 本次修订的核心：真机被误杀的那 11 条，现在都要能发出去。"""
    print("   —— 旧版（线 25）把这 11 条全丢：")
    for age in (26.0, 29.0, 29.4, 30.8, 31.1, 38.1, 49.4, 55.1, 59.6, 64.7):
        why = _why(new_msgs=0, age=age, hard_sec=90)
        check(f"  age={age}s 无人说话 → 照发", why == "", why)


def test_time_is_backstop():
    check("超过兜底线（90 秒）才丢", _why(new_msgs=0, age=200).startswith("过时"),
          _why(new_msgs=0, age=200))
    check("89 秒 < 线 → 不丢", _why(new_msgs=0, age=89) == "", _why(new_msgs=0, age=89))
    check("原因里带上线，便于事后核对", "线" in _why(new_msgs=0, age=200),
          _why(new_msgs=0, age=200))


def test_official():
    check("官腔命中即丢", _why(new_msgs=0, age=1, text="作为AI，我建议你早点睡") == "官腔",
          _why(new_msgs=0, age=1, text="作为AI，我建议你早点睡"))
    check("正常短句不误伤", _why(new_msgs=0, age=1, text="那你就是待炒的那颗") == "",
          _why(new_msgs=0, age=1, text="那你就是待炒的那颗"))


def test_switch_off():
    check("turns=0 关掉话头判据", _why(new_msgs=5, age=1, turns=0) == "",
          _why(new_msgs=5, age=1, turns=0))
    check("hard_sec=0 关掉时间兜底", _why(new_msgs=0, age=9999, hard_sec=0) == "",
          _why(new_msgs=0, age=9999, hard_sec=0))
    check("hard_sec=0 但翻篇 → 仍丢",
          _why(new_msgs=2, age=9999, hard_sec=0).startswith("话头翻篇"))


# ── 分布导出线（本次新增的核心机制）────────────────────────────────────

def test_profile_needs_samples():
    p = reply_drop.LatencyProfile()
    check("没样本 → p99 为 None", p.p99() is None)
    line, src = p.hard_line()
    check("没样本 → 用兜底线", line == reply_drop.FALLBACK_HARD, (line, src))
    check("来源标注清楚", "样本不足" in src, src)


def test_profile_follows_distribution():
    """线必须跟着实测走，不能是常数 —— 这是本机制存在的理由。"""
    p = reply_drop.LatencyProfile()
    for a in REAL_AGES:
        p.note(a)
    p99 = p.p99()
    check("攒够样本后有 p99", p99 is not None, p99)
    line, src = p.hard_line()
    check("线 = p99 × 系数", abs(line - p99 * reply_drop.P99_MULT) < 0.01, (line, p99))
    check("线高于真机所有正常样本（不再误杀搜索链）",
          line > max(REAL_AGES), (line, max(REAL_AGES)))
    check("来源写明依据", "实测p99" in src, src)

    p2 = reply_drop.LatencyProfile()
    for a in [x / 2 for x in REAL_AGES]:
        p2.note(a)
    line2, _ = p2.hard_line()
    check("整体变快 → 线跟着降", line2 < line, (line2, line))

    p3 = reply_drop.LatencyProfile()
    for a in [x * 2 for x in REAL_AGES]:
        p3.note(a)
    line3, _ = p3.hard_line()
    check("整体变慢 → 线跟着升", line3 > line, (line3, line))


def test_profile_addressed_is_tighter():
    """被点名 → 对方在等，线要收窄（以为你聋了比晚回更伤）。"""
    p = reply_drop.LatencyProfile()
    for a in REAL_AGES:
        p.note(a)
    plain, _ = p.hard_line(addressed=False)
    addr, src = p.hard_line(addressed=True)
    check("被点名时线更紧", addr < plain, (addr, plain))
    check("但仍有下限（不能窄到 0）", addr >= 15.0, addr)
    check("来源说明在等", "在等" in src, src)


def test_profile_ignores_bad_values():
    p = reply_drop.LatencyProfile()
    for bad in (0, -5, None, "x", 99999):
        p.note(bad)
    check("非法值不进样本", p.stats().get("n", 0) == 0, p.stats())


def test_profile_bounded_history():
    p = reply_drop.LatencyProfile(history=30)
    for i in range(100):
        p.note(10 + i * 0.01)
    check("历史有上限（不会无限长）", p.stats()["n"] <= 30, p.stats()["n"])


def test_stats_shape():
    p = reply_drop.LatencyProfile()
    check("空样本 stats 安全", p.stats() == {"n": 0}, p.stats())
    for a in REAL_AGES:
        p.note(a, addressed=True)
    st = p.stats()
    for k in ("n", "p50", "p90", "p99", "max", "addressed_n"):
        check(f"stats 含 {k}", k in st, list(st))
    check("p90 <= p99 <= max", st["p90"] <= st["p99"] <= st["max"], st)
    check("addressed 单独计数", st["addressed_n"] == len(REAL_AGES), st)


def test_defaults_from_config():
    """配置已从"设计值"降级为"覆盖开关"。"""
    import io
    import bot_core
    check("默认 2 条算翻篇", int(bot_core._config.get("reply_stale_turns", 2)) == 2,
          bot_core._config.get("reply_stale_turns"))
    cfg = io.open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()
    check("时间兜底不再写死秒数",
          '"reply_stale_drop_sec": None' in cfg,
          [ln for ln in cfg.splitlines() if "reply_stale_drop_sec" in ln])
    check("config 不再把 25 当设计值", '"reply_stale_drop_sec": 25' not in cfg)


def test_wired():
    import io
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("pipeline 用统一判据", "_why = reply_drop.drop_reason(" in src)
    check("数的是时间线上的新消息", "_e[0] > _seq_first" in src)
    check("日志能检索到翻篇原因", "[丢弃:{_why}]" in src)
    check("bot_core 导入 reply_drop", "import reply_drop" in src)
    check("使用分布导出线", "_latency.hard_line(" in src)
    check("无论丢不丢都记样本", "_latency.note(" in src)
    check("日志带上线来源（可事后核对）", "_hard_src" in src)
    check("日志带上样本分布", "p99=" in src)


def main():
    print("=" * 58)
    print("丢弃判据（话头翻篇为主，时间由分布导出）")
    print("=" * 58)
    for fn in (test_turns_is_primary,
               test_real_dropped_values_now_pass,
               test_time_is_backstop,
               test_official,
               test_switch_off,
               test_profile_needs_samples,
               test_profile_follows_distribution,
               test_profile_addressed_is_tighter,
               test_profile_ignores_bad_values,
               test_profile_bounded_history,
               test_stats_shape,
               test_defaults_from_config,
               test_wired):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()