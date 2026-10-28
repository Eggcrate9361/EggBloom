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
    check("没样本 → 抬到结构性地板", line == reply_drop.STRUCTURAL_FLOOR, (line, src))
    check("来源标注清楚", "样本不足" in src, src)


def test_profile_follows_distribution():
    """线必须跟着实测走，不能是常数 —— 这是本机制存在的理由。

    线 = max(结构性地板, p99 × 系数)。
    地板是绝对下限（120s），分布负责校准。所以：
      · 分布比地板高 → 线 = 分布线（跟着实测走）
      · 分布比地板低 → 线 = 地板（托住，不误杀）
    """
    p = reply_drop.LatencyProfile()
    for a in REAL_AGES:
        p.note(a)
    p99 = p.p99()
    check("攒够样本后有 p99", p99 is not None, p99)
    line, src = p.hard_line()
    _expect = max(reply_drop.STRUCTURAL_FLOOR, p99 * reply_drop.P99_MULT)
    check("线 = max(地板, p99×系数)", abs(line - _expect) < 0.01, (line, _expect))
    check("线高于真机所有正常样本（不再误杀搜索链）",
          line > max(REAL_AGES), (line, max(REAL_AGES)))
    check("来源写明依据", "实测p99" in src, src)

    # 整体变慢 → 线跟着升（这条必须在地板之上才看得出）
    p3 = reply_drop.LatencyProfile()
    for a in [x * 2 for x in REAL_AGES]:
        p3.note(a)
    line3, _ = p3.hard_line()
    check("整体变慢 → 线跟着升", line3 > line, (line3, line))

    # 整体变快 → 线被地板托住（不会跟着塌）
    p2 = reply_drop.LatencyProfile()
    for a in [x / 2 for x in REAL_AGES]:
        p2.note(a)
    line2, _ = p2.hard_line()
    check("整体变快 → 线不低于地板",
          line2 >= reply_drop.STRUCTURAL_FLOOR, (line2, reply_drop.STRUCTURAL_FLOOR))
    check("整体变快 → 线不高于变慢时", line2 <= line, (line2, line))


def test_profile_addressed_is_tighter():
    """★ 已被 2026-10-27 的真机修正推翻 —— 断言的是**错误设计**。

    原断言：「被点名 → 对方在等，线要收窄（以为你聋了比晚回更伤）」。
    真机证明它会「被点名 + 要搜索 = 必丢」（见 test_addressed_does_not_tighten）。
    留着这个测试等于把错设计钉死，所以改成显式记录"不再收紧"。
    """
    p = reply_drop.LatencyProfile()
    for a in REAL_AGES:
        p.note(a)
    plain, _ = p.hard_line(addressed=False)
    addr, src = p.hard_line(addressed=True)
    check("被点名不再收紧（真机修正）", addr >= plain, (addr, plain))
    check("仍有下限（不能窄到 0）", addr >= 15.0, addr)
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


def test_test_groups_do_not_pollute():
    """★ 测试脚本 import bot_core 时会灌样本，不能带偏真实分布。

    真机实测：8 条样本全来自 `_test_poke_pipeline_` 之类（age 0.1–5.2s），
    真实群样本 0 条。全局池子被测试的快样本占满 → 线被拉到远低于真实搜索链，
    于是真实群的慢回复反而被误杀 —— 正好是这次要修的问题。
    """
    p = reply_drop.LatencyProfile()
    for _ in range(30):
        p.note(0.2, group_id="_test_poke_pipeline_")
    check("测试群样本不进池", p.stats().get("n", 0) == 0, p.stats())
    check("测试群样本不进分桶", not p._by_group, p._by_group)

    # 真实群的慢样本要能正常进池
    p.note(58.0, group_id="123456789")
    check("真实群样本进池", p.stats().get("n", 0) == 1, p.stats())

    # 前缀判定
    check("_test_ 前缀识别", reply_drop.LatencyProfile.is_test_group("_test_x"))
    check("test_ 前缀识别", reply_drop.LatencyProfile.is_test_group("test_x"))
    check("真实群号不误判", not reply_drop.LatencyProfile.is_test_group("123456789"))
    check("空群号不误判", not reply_drop.LatencyProfile.is_test_group(""))


def test_profile_group_buckets():
    p = reply_drop.LatencyProfile()
    for i in range(10):
        p.note(50 + i, group_id="g1")
    for i in range(10):
        p.note(5 + i, group_id="g2")
    with p._lock:
        b1 = sorted(p._by_group.get("g1", []))
        b2 = sorted(p._by_group.get("g2", []))
    check("按群分桶：g1", len(b1) == 10 and b1[0] == 50, b1[:3])
    check("按群分桶：g2", len(b2) == 10 and b2[0] == 5, b2[:3])
    check("两个群不互相污染", b1[-1] != b2[-1], (b1[-1], b2[-1]))


def test_addressed_does_not_tighten():
    """★ 被点名**不收紧**线（2026-10-27 真机修正）。

    原设计是"对方在等，宽容度收窄到一半"，真机证明是错的：

      18:30:47 [工具轮次] 达到上限 3 轮，收尾      ← 搜到了 11 月那条
      18:30:54 [丢弃:过时 51.4s（线 45s）] 本批不发
      18:31:18 [丢弃:过时 48.7s（线 45s）] 本批不发

    线 45 = 兜底 90 × 0.5。而带搜索的回复结构上就要 50 秒
    → 被点名 + 要搜索 = 必丢。用户等来的是"一声不吭"。

    直觉错在：「以为你聋了比晚回更伤」没错，但我把它实现成了
    "把回复丢掉"—— 而不回复才是"以为你聋了"的最强形式。
    """
    p = reply_drop.LatencyProfile()
    for a in REAL_AGES:
        p.note(a)
    plain, _ = p.hard_line(addressed=False)
    addr, src = p.hard_line(addressed=True)
    check("被点名不收紧", addr >= plain, (addr, plain))
    check("两者相等（系数 1.0）", addr == plain, (addr, plain))

    # ★ 真机那两次：必须照发
    for age in (51.4, 48.7):
        why = reply_drop.drop_reason(new_msgs=0, age=age, turns=2,
                                     hard_sec=addr, addressed=True)
        check(f"真机 age={age}s 被点名 → 照发", why == "", why)


def test_structural_floor():
    """★ 线永远不低于结构性地板。

    带搜索的回复结构上就要 50 秒，线画在 45 秒 = 必丢。
    地板保证线只挡真卡死，不参与日常判断。
    """
    p = reply_drop.LatencyProfile()
    line, src = p.hard_line()
    check("无样本也不低于地板", line >= reply_drop.STRUCTURAL_FLOOR, line)
    check("地板高于带搜索链的耗时", reply_drop.STRUCTURAL_FLOOR >= 65,
          reply_drop.STRUCTURAL_FLOOR)

    # 样本全是快的 → 线也不能塌到地板以下
    p2 = reply_drop.LatencyProfile()
    for _ in range(30):
        p2.note(0.2)
    line2, src2 = p2.hard_line()
    check("样本很快时线仍守地板", line2 >= reply_drop.STRUCTURAL_FLOOR,
          (line2, src2))
    check("抬到地板时来源写明", "地板" in src2 or "抬到" in src2, src2)


def test_line_is_per_group():
    """★ 线按群取：不同群速度差很多，混在一起会互相污染。

    冷群（没人抢机器）本来就慢，线要跟着它的分布走；
    拿活跃群的线去卡冷群，冷群的慢回复会被误杀。

    注意地板（120s）会吃掉一部分差异：快群的线被地板托住，
    慢群的线由分布顶起 —— 这正是设计意图（地板托底，分布校准）。
    """
    p = reply_drop.LatencyProfile()
    # 活跃群：快
    for i in range(30):
        p.note(5 + i * 0.1, group_id="hot_group")
    # 冷群：慢（带搜索），且慢到超过地板
    for i in range(30):
        p.note(200 + i, group_id="cold_group")

    hot_line, hot_src = p.hard_line(group_id="hot_group")
    cold_line, cold_src = p.hard_line(group_id="cold_group")
    check("冷群线明显更宽（慢群用分布顶起）", cold_line > hot_line,
          (hot_line, cold_line))
    check("冷群线覆盖它的慢样本", cold_line > 229, cold_line)
    check("活跃群线守地板（不被分布带偏）",
          hot_line >= reply_drop.STRUCTURAL_FLOOR, hot_line)

    # 群样本不够 → 回退全局池
    p2 = reply_drop.LatencyProfile()
    for i in range(30):
        p2.note(20 + i, group_id="g1")
    for i in range(3):
        p2.note(60, group_id="g2")
    line_g2, src_g2 = p2.hard_line(group_id="g2")
    check("群样本不够时回退全局池（不直接用兜底）",
          "实测p99" in src_g2, src_g2)
    check("回退后线仍合理", 20 < line_g2 < 200, line_g2)


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
               test_addressed_does_not_tighten,
               test_structural_floor,
               test_profile_ignores_bad_values,
               test_profile_bounded_history,
               test_test_groups_do_not_pollute,
               test_profile_group_buckets,
               test_line_is_per_group,
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