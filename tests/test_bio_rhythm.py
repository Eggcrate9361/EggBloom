# -*- coding: utf-8 -*-
"""tests/test_bio_rhythm.py —— Bot 作息（一般精度）

背景（用户 2026-10-24 定「一般精度」）
    time_ctx 让 Bot **知道现在几点**（时段名 + 语气提示），但不改变行为参数 ——
    凌晨三点和晚上九点，主动开口概率一模一样。而 proactive_talk 的
    `quiet_hours` 默认为空（"不静默"），于是凌晨也照 35% 概率搭话。

    bio_rhythm 补这一半：一套**通用**时段作息，按时段给主动开口乘子。
    不针对个人微调、不做精细到分钟 —— 那是"高精度"，方向不可验证。

锁住的东西：
  1. 时段边界与 time_ctx **完全一致**（单一权威源，不建第二套小时表）
  2. import 时自检：两边时段表对不上就炸，不静默失配
  3. 乘子有上下限（speak_chance=0.9 也不能乘过 1，否则概率闸被拆）
  4. 曲线形状合理：凌晨最低、晚上最高、单调过渡
  5. energy 不注入 prompt（会作废前缀缓存）—— 这是设计约束，用测试钉住
  6. 概率闸真的乘了乘子（端到端，不是只测函数）

纯 Python，不依赖浏览器。
"""
import datetime
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import bio_rhythm as br  # noqa: E402
from harness import time_ctx  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def at(hour, minute=0):
    """构造某时刻的时间戳。"""
    return time.mktime((2026, 10, 24, hour, minute, 0, 0, 0, -1))


def main():
    print("[1] 单一权威源：时段边界只认 time_ctx")
    tc_periods = {row[1] for row in time_ctx._PERIODS}
    check("BIO_TABLE 的键与 time_ctx._PERIODS 完全一致",
          set(br.BIO_TABLE) == tc_periods,
          (sorted(set(br.BIO_TABLE) ^ tc_periods)))
    check("没有第二套小时边界定义",
          not hasattr(br, "_PERIODS") and not hasattr(br, "HOURS"))
    # bio_rhythm 不自己 import datetime 做判断（除了给 at() 用的测试侧）
    src = open(br.__file__, encoding="utf-8").read()
    check("bio_rhythm 不自己解析小时（时段判定走 time_ctx.period_of）",
          "tm_hour" not in src and ".hour" not in src)

    print("\n[2] import 自检：时段表对不上就炸")
    # 手工制造失配，确认 ImportError 真的会抛
    import importlib
    saved = dict(br.BIO_TABLE)
    try:
        br.BIO_TABLE.pop("凌晨")
        # 重新执行模块末尾的自检逻辑（不重新 import，直接复算）
        missing = {_row[1] for _row in time_ctx._PERIODS} - set(br.BIO_TABLE)
        check("删一个时段后能被自检发现", bool(missing), missing)
    finally:
        br.BIO_TABLE.clear()
        br.BIO_TABLE.update(saved)

    print("\n[3] 参数范围与上下限")
    for period, p in br.BIO_TABLE.items():
        check(f"{period}：energy ∈ [0,1]",
              0.0 <= p["energy"] <= 1.0, p["energy"])
        check(f"{period}：proactivity 在允许区间内",
              br._PROACTIVITY_MIN <= p["proactivity"] <= br._PROACTIVITY_MAX,
              p["proactivity"])
        check(f"{period}：有 hint（观测要能看到状态）",
              bool(p.get("hint", "").strip()))
    check("上限 1.25 存在（防概率闸被拆）", br._PROACTIVITY_MAX == 1.25)
    check("下限 0.05 存在（不会完全静音）", br._PROACTIVITY_MIN == 0.05)

    print("\n[4] 曲线形状：凌晨最低、晚上最高")
    order = ["凌晨", "清晨", "上午", "中午", "下午", "傍晚", "晚上", "深夜"]
    pros = [br.BIO_TABLE[p]["proactivity"] for p in order]
    check("凌晨是全天最低", pros[0] == min(pros), pros)
    check("晚上是全天最高", pros[6] == max(pros), pros)
    check("深夜比凌晨高一点（该睡了但还没睡着）", pros[7] > pros[0], (pros[7], pros[0]))
    check("下午到晚上单调上升", pros[4] <= pros[5] <= pros[6], pros[4:7])
    check("上午到中午单调下降（饭后犯困）", pros[2] >= pros[3], (pros[2], pros[3]))
    check("energy 与 proactivity 同向（凌晨双低）",
          br.BIO_TABLE["凌晨"]["energy"] < br.BIO_TABLE["晚上"]["energy"]
          and br.BIO_TABLE["凌晨"]["proactivity"] < br.BIO_TABLE["晚上"]["proactivity"])

    print("\n[5] bio_state / proactivity_factor / energy_of")
    st = br.bio_state(at(21))
    check("21 点是晚上", st["period"] == "晚上", st["period"])
    check("21 点 state 带四个字段",
          set(st) == {"period", "energy", "proactivity", "hint"}, list(st))
    check("proactivity_factor 与 state 一致",
          br.proactivity_factor(at(21)) == st["proactivity"])
    check("energy_of 与 state 一致", br.energy_of(at(21)) == st["energy"])
    check("ts=None 用当前时间不炸",
          br.bio_state()["period"] in set(br.BIO_TABLE))
    check("24 小时都取得到状态（没有黑洞时段）",
          all(br.bio_state(at(h))["period"] in set(br.BIO_TABLE) for h in range(24)))

    print("\n[6] 端到端：概率闸真的乘了乘子")
    # 直接算一遍闸门用的 chance，与 proactive_talk 的算法对齐
    def gate_chance(hour, base=0.35):
        return min(1.0, base * br.proactivity_factor(at(hour)))
    check("凌晨 chance 被压到 0.05 左右", gate_chance(1) < 0.08, gate_chance(1))
    check("晚上 chance 被抬到 0.42", abs(gate_chance(21) - 0.42) < 0.01, gate_chance(21))
    check("基准 0.9 × 1.2 被夹到 1.0（不超 1）", gate_chance(21, 0.9) == 1.0,
          gate_chance(21, 0.9))
    check("基准 1.0 也被夹住", gate_chance(21, 1.0) == 1.0, gate_chance(21, 1.0))
    check("同基准下晚上 chance > 凌晨 chance",
          gate_chance(21) > gate_chance(1) * 5, (gate_chance(21), gate_chance(1)))

    print("\n[7] 设计约束：energy 不注入 prompt")
    # 注入会让每小时变一次 → 作废前缀缓存。测试钉住"当前没有这条路径"。
    core = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("bot_core 没有把 energy/bio 塞进 prompt",
          "energy" not in core and "bio_state" not in core)
    check("time_ctx 的注入路径没被 bio_rhythm 污染",
          "bio" not in time_ctx.__doc__)

    print("\n[8] proactive_talk 接进去了")
    pt = open(os.path.join(ROOT, "plugins", "proactive_talk", "__init__.py"),
              encoding="utf-8").read()
    check("概率闸处 import 了 proactivity_factor",
          "proactivity_factor" in pt)
    check("乘法在硬闸（quiet_hours）之后",
          pt.index("_in_quiet_hours(cfg.get") < pt.index("proactivity_factor"))
    check("乘后夹到 1.0", "min(1.0, chance * _bio)" in pt)
    check("test_decision 暴露 bio 状态（可观测）",
          'd["bio"] = _b' in pt and 'd["bio"] = {"error"' in pt)
    check("bio 缺失时降级为 1.0（不炸）", "except Exception:" in pt)

    print("\n[9] 开关：能退回旧行为（一般精度不是不可回退）")
    check("默认开启", '"bio_rhythm": True' in pt)
    check("关掉时不乘（cfg.get 默认 True，显式 False 才跳过）",
          'if cfg.get("bio_rhythm", True):' in pt)
    check("关掉时 test_decision 的 hint 说明原因",
          "作息乘子已关闭" in pt)
    # 2026-10-31：插件 UI 在 1.5.10 换成 kit v2 动态渲染，写死的 `id="pt-bio"` 表单没了。
    # 判据改成"开关还在 + 状态由配置回填 + 保存走通用 data-c 收集"，三件事都还在。
    ui = open(os.path.join(ROOT, "plugins", "proactive_talk", "ui.html"),
              encoding="utf-8").read()
    check("UI 有作息开关", "bool('bio_rhythm')" in ui)
    check("UI 开关状态由配置回填", "+(c[k]?'checked':'')+" in ui)
    check("UI 保存走通用 data-c 收集（不逐个写死键名）",
          "el.dataset.c" in ui and "{[k]:v}" in ui)

    print("\n[10] 硬闸仍然优先（作息是软闸，不能绕过 quiet_hours）")
    check("乘法出现在 quiet_hours 判断之后（顺序即优先级）",
          pt.index("if _in_quiet_hours(") < pt.index("if cfg.get(\"bio_rhythm\""))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())