# -*- coding: utf-8 -*-
"""tests/test_memory_forgetting.py —— 记忆时间衰减（1.5.8 计划 #29）

背景
    计划原文：「记忆加遗忘：system_snapshot 1200 字符，超过 N 天未命中的
    降权淘汰」。

    核实后：字符截断早就有了（system_snapshot 的 max_chars=1200），
    真正的缺口是**打分公式里没有任何时间因素**：

        score = density × trust + sim_bonus

    于是三个月前的记忆和今天写的，只要关键词匹配，分数一模一样。
    后果不是"老记忆冒出来"这么温和 —— 是记忆库随使用时间单调增长，
    而检索质量不随之变化，等于没有遗忘机制：该淡掉的还在前排。

本文件锁：
  1. 衰减是乘法且保序（同年龄排序不变，τ 闸语义保持）
  2. 有下限（长期事实不被误杀：一年前也只减半，不归零）
  3. 时间取"最近一次命中"，不是"写入时间"（命中过就刷新）
  4. 拿不到时间不猜（None → 不衰减，不默认当成很老）
  5. 脏数据不炸（负天数、非数字、超长年龄）

纯 Python，不依赖浏览器。
"""
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import sandglass_lite as sl  # noqa: E402

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


def _ts(days_ago):
    return (datetime.datetime.now()
            - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _score(rows, query):
    """rows: [(行号, ts, text)] → {行号: 分数}"""
    toks = sl.query_tokens(query)
    return {it[0]: s for s, it in sl._score_candidates(rows, toks, query)}


def main():
    print("[1] age_factor 的形状")
    check("当天 → 1.0（不放大也不压）", sl.age_factor(0) == 1.0)
    check("7 天前约 0.85", abs(sl.age_factor(7) - 0.8507) < 0.01, sl.age_factor(7))
    check("30 天前正好减半", abs(sl.age_factor(30) - 0.5) < 1e-6, sl.age_factor(30))
    check("半衰期是参数化的（改一处全局生效）",
          sl._AGE_HALFLIFE_DAYS == 30.0 and sl._AGE_FLOOR == 0.5)
    check("单调不增", all(sl.age_factor(a) >= sl.age_factor(b)
                          for a, b in zip([0, 7, 30, 90, 365], [7, 30, 90, 365, 3650])))

    print("\n[2] 下限保护：长期事实不被误杀")
    for d in (60, 180, 365, 3650):
        check("%d 天前仍是 0.5（不是 0）" % d, sl.age_factor(d) == 0.5, sl.age_factor(d))
    check("再老也不归零", sl.age_factor(99999) >= sl._AGE_FLOOR)

    print("\n[3] 脏数据不炸、不猜")
    check("负天数（未来时钟）→ 1.0", sl.age_factor(-5) == 1.0)
    check("None → 1.0（不默认当成很老）", sl.age_factor(None) == 1.0)
    check("非数字 → 1.0", sl.age_factor("脏数据") == 1.0)
    check("超过 10 年按 10 年算", sl.age_factor(99999) == sl.age_factor(3650))
    check("取不到时间戳 → None（调用方不衰减）",
          sl._line_age_days("") is None and sl._line_age_days(None) is None)
    check("脏时间戳 → None", sl._line_age_days("根本不是时间") is None)
    check("合法时间戳能算天数",
          abs(sl._line_age_days(_ts(10)) - 10) < 0.01, sl._line_age_days(_ts(10)))
    check("短格式（mm-dd HH:MM）也能解析",
          sl._line_age_days("10-01 12:00") is not None, sl._line_age_days("10-01 12:00"))

    print("\n[4] 端到端：同内容、差年龄 → 新的分高")
    # 90000+ 行号：避开本机 shadow_sand.db 的既有记录（行 1/2 有 122 天前的
    # updated_at，会盖过行自身 ts —— 那是"命中过就刷新"的正确行为，
    # 但会让这个用例失去对照意义）
    rows = [(90001, _ts(0), "花生过敏 不能吃"),
            (90002, _ts(90), "花生过敏 不能吃")]
    sc = _score(rows, "花生")
    check("两条同内容只有年龄不同，分数确实不同", sc[90001] != sc[90002],
          sc)
    check("新的是旧的 2 倍（30 天半衰期 × 3 个半衰期 = 对折，sim 加成同值）",
          abs(sc[90001] / sc[90002] - 2.0) < 0.05, sc[90001] / sc[90002])
    check("新的排在前面", max(sc, key=sc.get) == 90001, sc)

    print("\n[5] 保序：同年龄下相对排序不变")
    # 两条**文本不同**的记忆（一条长一条短，base 分本就不同）。
    # 衰减是乘法 → 同年龄下相对次序必须保持，且整体同比例压低。
    rows2 = [(90011, _ts(0), "花生过敏 不能吃 很严重"),
             (90012, _ts(0), "花生")]
    sc2 = _score(rows2, "花生")
    rows3 = [(90021, _ts(200), "花生过敏 不能吃 很严重"),
             (90022, _ts(200), "花生")]
    sc3 = _score(rows3, "花生")
    order2 = sorted(sc2, key=sc2.get, reverse=True)
    order3 = sorted(sc3, key=sc3.get, reverse=True)
    # 比**文本**顺序，不比行号 —— 两组行号本来就得不同（否则撞真实 trust 库）
    txt2 = [dict(rows2 and [(r[0], r[2]) for r in rows2])[ln][:6] for ln in order2]
    txt3 = [dict([(r[0], r[2]) for r in rows3])[ln][:6] for ln in order3]
    check("同年龄时排序与无衰减时一致", txt2 == txt3, (txt2, txt3))
    # 用比例判，不用绝对差：浮点下 0.9999999 与 0.9999999 的差是 1e-8 级
    r_new = sc2[order2[0]] / sc2[order2[1]]
    r_old = sc3[order3[0]] / sc3[order3[1]]
    check("老一组的相对比例与新的那组一致（乘法保序）",
          abs(r_new - r_old) < 1e-6, (r_new, r_old))
    check("老一组整体按比例压低（约对折）",
          abs(sc3[order3[0]] / sc2[order2[0]] - 0.5) < 0.05,
          sc3[order3[0]] / sc2[order2[0]])

    print("\n[6] 命中过就刷新：recency 优先于写入时间")
    # 打桩 recency，让一条 90 天前写的记忆"3 天前刚被检索过"
    orig = sl._trust_recency
    sl._trust_recency = lambda nums: {90031: 3.0}
    try:
        rows4 = [(90031, _ts(90), "花生过敏"), (90032, _ts(90), "花生过敏")]
        sc4 = _score(rows4, "花生")
        # 预期比值 = age_factor(3) / age_factor(90)，不是 1.0/0.5 ——
        # age_factor(3)=0.933 不是 1.0（半天衰期都没到）
        expect = sl.age_factor(3.0) / sl.age_factor(90.0)
        check("被检索过的那条按 3 天算，不按 90 天",
              abs(sc4[90031] / sc4[90032] - expect) < 0.01,
              (sc4[90031] / sc4[90032], expect))
    finally:
        sl._trust_recency = orig
    check("恢复后走真实库", orig is sl._trust_recency)

    print("\n[7] 打桩缺失时不猜")
    orig2 = sl._trust_recency
    sl._trust_recency = lambda nums: {}
    try:
        rows5 = [(90041, _ts(90), "花生过敏")]
        sc5 = _score(rows5, "花生")
        base_rows = [(90042, _ts(90), "花生过敏")]
        sc5b = _score(base_rows, "花生")
        check("recency 空 → 退回行自身 ts（与对照同分）",
              abs(sc5[90041] - sc5b[90042]) < 1e-9, (sc5, sc5b))
    finally:
        sl._trust_recency = orig2

    print("\n[8] 阈值闸语义没被破坏")
    # τ=0.35：一条全新高相关记忆应仍能过闸，一条很老的不应被无脑放过
    import harness.memory_context as mc
    fresh = _score([(90051, _ts(0), "花生过敏 不能吃 会进医院")], "花生过敏")
    old = _score([(90052, _ts(400), "花生过敏 不能吃 会进医院")], "花生过敏")
    tau = mc._CFG.get("tau", 0.35)
    check("全新记忆仍高于 τ", list(fresh.values())[0] > tau, (fresh, tau))
    check("很老的同内容记忆分数被压低（可能掉到 τ 以下）",
          list(old.values())[0] < list(fresh.values())[0], (fresh, old))
    check("τ 本身没被改动（衰减不靠改阈值实现）", tau == 0.35, tau)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())