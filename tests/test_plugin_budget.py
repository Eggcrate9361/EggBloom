# -*- coding: utf-8 -*-
"""harness/plugin_budget 单测（1.5.10 F3/F4）。

覆盖：注入排序全序（类别 → priority → 名字）、字数统计、边界截断、
预算两维度（单条合计 / 每插件每天）、账本落盘与换天、坏账本不炸。

运行：python tests/test_plugin_budget.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import plugin_budget as pb  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


_TMP = tempfile.mkdtemp(prefix="pbudget_")

try:
    # ── 1. 排序：全序，不依赖注册顺序 ────────────────────────────────
    a = ("web_search", 30, "搜索结果")
    b = ("super_memory", 50, "记忆")
    c = ("group_chat_decider", 90, "决策")
    d = ("vitals", 30, "状态")
    got = [x[0] for x in pb.order([a, b, c, d])]
    check("决策排第一（priority 最差也不影响）", got[0] == "group_chat_decider", got)
    check("记忆在搜索前、状态在记忆前",
          got.index("vitals") < got.index("super_memory") < got.index("web_search"), got)

    # 同类别 → 按 priority
    e = ("ego", 60, "x")
    f = ("personality_engine", 10, "y")
    got2 = [x[0] for x in pb.order([e, f])]
    check("同类别按 priority 升序", got2 == ["personality_engine", "ego"], got2)

    # 同类别同 priority → 按名字（否则取决于注册顺序）
    g = ("chat_style", 30, "1")
    h = ("humanlike_rhythm", 30, "2")
    got3 = [x[0] for x in pb.order([h, g])]
    check("同类别同 priority 按名字兜底", got3 == ["chat_style", "humanlike_rhythm"], got3)
    check("order 不改入参", [x[0] for x in [h, g]] == ["humanlike_rhythm", "chat_style"])

    # 未知插件落在 DEFAULT_RANK，不抛
    check("未知插件有默认档位", pb.rank_of("某个新插件") == pb.DEFAULT_RANK)
    check("priority 脏值当 50", pb.sort_key("x", "abc")[1] == 50)

    # ── 2. 字数与截断 ───────────────────────────────────────────────
    check("字数不数空白", pb.text_len(" 你 好\n啊 ") == 3)
    check("空串字数 0", pb.text_len(None) == 0)

    long_text = "第一句话在这里。" + "第二句也很长啊。" * 10
    cut, trimmed = pb.clip(long_text, 20)
    check("截断标记为真", trimmed is True)
    check("截断不超限", pb.text_len(cut) <= 20, pb.text_len(cut))
    check("截断切在句末", cut.endswith("。"), repr(cut[-6:]))
    check("短文本不截", pb.clip("短句。", 20) == ("短句。", False))
    check("limit<=0 视为不截", pb.clip(long_text, 0) == (long_text, False))

    # ── 3. 配置 ─────────────────────────────────────────────────────
    cfg0 = pb.config_of(None)
    check("默认 80/600", (cfg0["max_chars"], cfg0["daily_chars"]) == (80, 600), cfg0)
    check("默认启用", cfg0["enabled"] is True)
    cfg1 = pb.config_of({"plugin_inject_max_chars": "120",
                         "plugin_inject_daily_chars": -5,
                         "plugin_inject_budget_enabled": False})
    check("脏值回落默认", cfg1["daily_chars"] == pb.DEFAULT_DAILY_CHARS, cfg1)
    check("关闭开关读得到", cfg1["enabled"] is False)
    check("字符串数字能转", cfg1["max_chars"] == 120, cfg1)

    # ── 4. 账本 ─────────────────────────────────────────────────────
    lp = os.path.join(_TMP, "plugin_budget.json")
    L = pb.Ledger(lp)
    check("新账本为空", L.used_today("ego") == 0)
    check("不限时 remaining=-1", L.remaining("ego", 0) == -1)
    check("remaining 按日上限算", L.remaining("ego", 600) == 600)
    L.charge("ego", 100)
    check("记账后 remaining 减少", L.remaining("ego", 600) == 500, L.used_today("ego"))
    check("save 返回 True", L.save() is True)

    L2 = pb.Ledger(lp)          # 模拟重启：新实例读同一份账本
    check("账本跨实例累计", L2.used_today("ego") == 100, L2.used_today("ego"))
    import time as _time
    _TOMORROW = _time.time() + 86400
    check("换天会清空", L2.roll(now=_TOMORROW) is True)
    check("换天后用量为 0", L2.used_today("ego") == 0)
    check("换天写入新日期", L2.date != L.date)

    bad = os.path.join(_TMP, "bad.json")
    io.open(bad, "w", encoding="utf-8").write("{不是 json")
    L3 = pb.Ledger(bad)
    check("坏账本不抛且记为空", L3.used_today("ego") == 0)

    # ── 5. plan：单条合计上限 ────────────────────────────────────────
    budget = {"enabled": True, "max_chars": 80, "daily_chars": 600}
    items = pb.order([
        ("group_chat_decider", 10, "决" * 20),
        ("ego", 20, "人" * 200),          # 会被截到剩下的 60 字
        ("web_search", 60, "搜" * 30),    # 预算已用完 → 跳过
    ])
    text5, rep5 = pb.plan(items, budget, ledger=None)
    check("单条合计不超 80", pb.text_len(text5) <= 80, pb.text_len(text5))
    check("合计等于 80", pb.text_len(text5) == 80, pb.text_len(text5))
    check("报告 total 与文本一致", rep5["total"] == pb.text_len(text5))
    check("被截的那个记进 trimmed", [x[0] for x in rep5["trimmed"]] == ["ego"],
          rep5["trimmed"])
    check("放不下的记进 dropped", [x[0] for x in rep5["dropped"]] == ["web_search"],
          rep5["dropped"])
    check("describe 有话说", "插件预算" in pb.describe(rep5))

    # ── 6. plan：每天上限（按插件）────────────────────────────────────
    Ld = pb.Ledger(os.path.join(_TMP, "daily.json"))
    b2 = {"enabled": True, "max_chars": 0, "daily_chars": 25}
    it = [("ego", 20, "人" * 20)]
    t1, r1 = pb.plan(it, b2, ledger=Ld)
    check("首次全量注入", pb.text_len(t1) == 20 and not r1["dropped"], r1)
    t2, r2 = pb.plan(it, b2, ledger=Ld)      # 今日已用 20，剩 5 → 截不出来就丢
    check("超出每日预算后丢弃", t2 == "" and r2["dropped"][0][1] == "今日预算用完", r2)
    check("账本累计到 20", Ld.used_today("ego") == 20, Ld.used_today("ego"))

    # 另一个插件不受影响（每天是**按插件**分开算的）
    t3, r3 = pb.plan([("web_search", 60, "搜" * 20)], b2, ledger=Ld)
    check("别的插件还有自己的额度", pb.text_len(t3) == 20, r3)

    # ── 7. plan：关闭预算 ───────────────────────────────────────────
    ts, rs = pb.plan([("ego", 20, "人" * 500)], {"enabled": False,
                                                "max_chars": 80, "daily_chars": 600})
    check("关掉预算就不截", pb.text_len(ts) == 500, pb.text_len(ts))
    check("关掉预算不记账", not rs["dropped"] and not rs["trimmed"])

    # ── 8. plan：杂项 ───────────────────────────────────────────────
    te, _ = pb.plan([("ego", 20, ""), ("ego2", 20, "   ")], budget)
    check("空注入不产生空行", te == "")
    tt, rt = pb.plan([("ego", 20, "这句话挺长的应该放不下")], {"enabled": True,
                                                             "max_chars": 3,
                                                             "daily_chars": 600})
    check("额度放不下一句 → 整段丢掉",
          tt == "" and rt["dropped"][0][1] == "单条预算用完", rt)
    check("describe 无问题时为空串", pb.describe({"dropped": [], "trimmed": [], "total": 1}) == "")

finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
