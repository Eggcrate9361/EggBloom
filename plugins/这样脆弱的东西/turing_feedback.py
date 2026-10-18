# -*- coding: utf-8 -*-
"""图灵反馈：把"像不像"拆成**可修正的维度**。

之前图灵测试的问题：跑完只给一个分数（"3/5 轮通过"）——
分数告诉你像不像，**不告诉你哪里不像**，所以没法改进。

这一层做的事：拿图灵测试每一轮的「真实发言」和「模型生成的发言」，
在**可量化的风格维度**上各算一遍，然后逐维度对比：

    长度      生成平均 32 字 vs 真实 9 字   → 太长 ✗
    无标点率  生成 5% vs 真实 47%          → 太书面 ✗
    语气词    生成 0.1/条 vs 真实 0.27/条  → 太少 ✗
    感叹      生成 0% vs 真实 1%           → 接近 ✓

每个不匹配的维度都直接指向**注入卡里对应的那条参数**——
所以报告的最后一列就是"该调哪条参数"。这才能闭环。

全部确定性计算，不需要额外 LLM 调用。
"""
import re
from collections import Counter

from .style_profile import _len_of, _punct_profile, _tone_words, _EMOJI


def _side_stats(texts):
    """对一组文本算风格参数。"""
    texts = [t for t in (texts or []) if (t or "").strip()]
    if not texts:
        return {}
    lengths = [_len_of(t) for t in texts]
    tone = _tone_words(texts)
    emote = sum(1 for t in texts if _EMOJI.search(t or ""))
    return {
        "n": len(texts),
        "长度": {"平均": round(sum(lengths) / len(lengths), 1),
                 "最长": max(lengths)},
        "标点": _punct_profile(texts),
        "语气词密度": tone.get("per_msg", 0),
        "表情使用率": round(emote / len(texts), 2),
    }


def _diff(real, gen):
    """逐维度对比，返回 [维度, 真实值, 生成值, 判定, 建议]。"""
    rows = []
    if not real or not gen:
        return rows

    def num(v):
        return v if isinstance(v, (int, float)) else 0

    # 长度
    ra, ga = num(real["长度"]["平均"]), num(gen["长度"]["平均"])
    if ra:
        ratio = ga / ra if ra else 0
        verdict = "接近" if 0.7 <= ratio <= 1.4 else ("太长" if ratio > 1.4 else "太短")
        rows.append(["长度（平均字数）", ra, ga, verdict,
                     "注入卡「长度」参数" if verdict != "接近" else ""])
    # 标点
    for dim in ("无标点", "句号", "问号", "感叹"):
        rv, gv = num(real["标点"].get(dim)), num(gen["标点"].get(dim))
        if rv >= 0.05 or gv >= 0.05:
            d = abs(rv - gv)
            verdict = "接近" if d <= 0.15 else ("生成太书面" if rv > gv else "生成太随意")
            rows.append([f"{dim}率", f"{rv:.0%}", f"{gv:.0%}", verdict,
                         "注入卡「标点」参数" if verdict != "接近" else ""])
    # 语气词
    rt, gt = num(real.get("语气词密度")), num(gen.get("语气词密度"))
    if max(rt, gt) >= 0.05:
        verdict = "接近" if (abs(rt - gt) <= 0.12) else ("太少" if gt < rt else "太多")
        rows.append(["语气词密度", rt, gt, verdict,
                     "注入卡「口头禅」参数" if verdict != "接近" else ""])
    # 表情
    re_, ge = num(real.get("表情使用率")), num(gen.get("表情使用率"))
    if max(re_, ge) >= 0.1:
        verdict = "接近" if abs(re_ - ge) <= 0.2 else ("生成没用表情" if ge < re_ else "生成用太多")
        rows.append(["表情使用率", f"{re_:.0%}", f"{ge:.0%}", verdict,
                     "注入卡「表情」参数" if verdict != "接近" else ""])
    return rows


def dimension_report(rounds):
    """对图灵测试的每一轮做维度对比，汇总成整体报告。"""
    rounds = rounds or []
    real_texts = [str(r.get("real") or "") for r in rounds if r.get("real")]
    gen_texts = [str(r.get("generated") or "") for r in rounds if r.get("generated")]
    if not real_texts or not gen_texts:
        return {"error": "没有可对比的样本（需要真实发言与生成发言各至少 1 条）"}

    real, gen = _side_stats(real_texts), _side_stats(gen_texts)
    rows = _diff(real, gen)
    bad = [r for r in rows if r[3] != "接近"]
    ok = [r for r in rows if r[3] == "接近"]
    # 按偏差程度排序：明显不像的排前面
    bad.sort(key=lambda r: -abs(
        (float(str(r[2]).rstrip("%")) - float(str(r[1]).rstrip("%")))
        if _isnum(r[1]) and _isnum(r[2]) else 1))

    return {
        "rounds": len(rounds),
        "real_samples": len(real_texts),
        "generated_samples": len(gen_texts),
        "real": real,
        "generated": gen,
        "dimensions": [{"维度": r[0], "真实": r[1], "生成": r[2],
                        "判定": r[3], "该调": r[4]} for r in rows],
        "mismatches": [{"维度": r[0], "真实": r[1], "生成": r[2],
                        "判定": r[3], "该调": r[4]} for r in bad],
        "verdict": (f"{len(bad)} 个维度不像、{len(ok)} 个接近"
                    + (f"；最该调：{bad[0][0]}（真实 {bad[0][1]} vs 生成 {bad[0][2]}）"
                       if bad else "—— 各维度都在合理范围内")),
    }


def _isnum(v):
    try:
        float(str(v).rstrip("%"))
        return True
    except (TypeError, ValueError):
        return False


def markdown(report):
    """渲染成 Markdown 段落。"""
    if not report or report.get("error"):
        return []
    out = ["### 🎭 图灵测试反馈（哪里不像）", ""]
    out.append(f"> {report['verdict']}"
               f"（{report['rounds']} 轮盲测，真实样本 {report['real_samples']} 条 / "
               f"生成样本 {report['generated_samples']} 条）")
    out.append("")
    if report["mismatches"]:
        out.append("| 维度 | 真实 | 模型生成 | 判定 | 该调注入卡的哪条 |")
        out.append("|------|------|----------|------|------------------|")
        for r in report["mismatches"]:
            out.append(f"| {r['维度']} | {r['真实']} | {r['生成']} | "
                       f"{r['判定']} | {r['该调'] or '—'} |")
        out.append("")
        out.append("这些维度是可以直接修的：改注入卡里对应的参数，"
                   "下一轮盲测就能看出有没有起效。")
    else:
        out.append("- 所有可量化维度都在合理范围内。")
    out.append("")
    return out
