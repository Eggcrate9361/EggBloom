# -*- coding: utf-8 -*-
"""灵魂内核：脆弱度 / 人格指纹 / 灵魂对比 / 封印守护。

这个插件处理的东西不是"文件"，而是一件脆弱之物——
一个从对话里凝结出来的人格。数据越薄，它越脆；
被改动，它就有裂痕；两次蒸馏之间，它悄悄变了。
"""
import hashlib
import re

from . import level_classifier


# ── 脆弱度 ──────────────────────────────────────────────

def fragility(features, level, data_volume, confidence=None):
    """计算脆弱度指数（0~1，越大越脆弱）。

    由四维合成：
      - 数据量（L0-L4 阶梯）：数据越少越脆
      - 置信度：越低越脆
      - 覆盖度：feature 数量越少越脆
      - 数据源广度：来源越单一越脆
    """
    info = level_classifier.level_info(level or "L0")
    budget = info.get("budget", 200000)

    # 1. 数据量维度：按等级阶梯映射
    level_weights = {"L0": 1.0, "L1": 0.75, "L2": 0.5, "L3": 0.28, "L4": 0.08}
    data_frag = level_weights.get(level, 0.75)

    # 2. 置信度维度
    items = []
    for sec in (features or {}).values():
        items.extend(sec)
    if confidence is None:
        confidence = round(sum(f.get("confidence", 0) for f in items) / len(items), 2) if items else 0.0
    conf_frag = 1.0 - float(confidence)

    # 3. 覆盖度维度：按等级应有的条目数 vs 实际条目数
    expected = level_classifier.expected_entries(level)
    actual = len(items)
    cover_frag = max(0.0, 1.0 - actual / max(1, expected))

    # 4. 数据源广度维度（由 data_volume 与等级的期望对比）
    vol_ratio = min(1.0, data_volume / max(1, info.get("min", 100)))
    src_frag = 1.0 - vol_ratio

    # 5. 证据维度：有多少条结论能指回原文 —— 无据的结论等于没有
    with_ev = sum(1 for f in items if f.get("evidence"))
    ev_frag = 1.0 if not items else round(1.0 - with_ev / len(items), 2)

    index = round(0.32 * data_frag + 0.24 * conf_frag + 0.16 * cover_frag
                  + 0.08 * src_frag + 0.20 * ev_frag, 3)
    index = max(0.0, min(1.0, index))

    return {
        "index": index,
        "label": _fragility_label(index),
        "confidence": confidence,
        "feature_count": len(items),
        "evidenced_count": with_ev,
        "dimensions": {
            "数据量": round(data_frag, 2),
            "置信度": round(conf_frag, 2),
            "覆盖度": round(cover_frag, 2),
            "来源广度": round(src_frag, 2),
            "证据缺口": round(ev_frag, 2),
        },
    }


def _fragility_label(index):
    if index >= 0.85:
        return "一碰就碎"
    if index >= 0.65:
        return "还很薄"
    if index >= 0.45:
        return "正在成形"
    if index >= 0.25:
        return "趋于凝实"
    return "凝实稳固"


# ── 人格指纹 ────────────────────────────────────────────

def fingerprint(markdown):
    """对人格文件生成指纹（SHA-256，取前 32 位十六进制）。"""
    text = (markdown or "").encode("utf-8")
    return hashlib.sha256(text).hexdigest()[:32]


# ── 灵魂对比 ────────────────────────────────────────────

def soul_diff(a_md, b_md):
    """对比两份人格文件，返回变化摘要（added/removed/changed + 情感倾向）。"""
    fa = _extract_facts(a_md)
    fb = _extract_facts(b_md)
    keys_a = set(fa.keys())
    keys_b = set(fb.keys())

    added = [{"key": k, "value": fb[k]} for k in (keys_b - keys_a)]
    removed = [{"key": k, "value": fa[k]} for k in (keys_a - keys_b)]
    changed = []
    for k in keys_a & keys_b:
        if fa[k] != fb[k]:
            changed.append({"key": k, "from": fa[k], "to": fb[k]})

    total = len(added) + len(removed) + len(changed)
    if total == 0:
        summary = "两次蒸馏之间，这个人没有变。"
    else:
        summary = f"这个人变了 {total} 处：新增 {len(added)}，消失 {len(removed)}，改写 {len(changed)}。"
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "total": total,
        "summary": summary,
    }


def _extract_facts(markdown):
    """从人格文件提取「标签: 值」事实对。"""
    facts = {}
    for line in (markdown or "").splitlines():
        line = line.strip()
        m = re.match(r"-\s*\*\*(.+?)\*\*[：:]\s*(.+)$", line)
        if m:
            facts[m.group(1).strip()] = m.group(2).strip()
    return facts


# ── 封印守护 ────────────────────────────────────────────

def seal(markdown):
    """封印：返回 (指纹, 密封校验和)。"""
    fp = fingerprint(markdown)
    digest = hashlib.sha256((markdown or "").encode("utf-8")).hexdigest()
    return {"fingerprint": fp, "checksum": digest}


def verify(markdown, checksum):
    """校验封印是否完好。"""
    current = hashlib.sha256((markdown or "").encode("utf-8")).hexdigest()
    return {"intact": current == checksum, "current": current, "sealed": checksum}
