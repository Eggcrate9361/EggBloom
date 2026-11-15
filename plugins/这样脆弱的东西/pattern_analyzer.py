# -*- coding: utf-8 -*-
"""L2 模式整合（启发式）。

跨消息找**节律**：什么时候出现、说多长、多久一次、话题散不散。
输出 {key: {"value", "confidence", "evidence"}}，同样带证据。

这里的原则和别处一致：算得出来才写。算不出来就写"样本不足"，
而不是给一个看起来像结论的句子。
"""
import datetime
import re
from collections import Counter

from .feature_extractor import NEGATIVE_WORDS, POSITIVE_WORDS, TOPIC_KEYWORDS


def analyze(msgs):
    """对规范化消息做模式整合。"""
    patterns = {}
    if not msgs:
        return patterns
    texts = [m["text"] for m in msgs]

    # ── 作息节律 ──
    hours = [m["hour"] for m in msgs if m["hour"] is not None]
    if len(hours) >= 5:
        top = Counter(hours).most_common(3)
        night = sum(1 for h in hours if h >= 23 or h <= 5)
        day = sum(1 for h in hours if 8 <= h <= 18)
        if night > day:
            slot = "深夜型"
        elif day > night:
            slot = "白天型"
        else:
            slot = "昼夜均分"
        patterns["作息节律"] = _p(
            f"{slot}，活跃时段 {'、'.join(f'{h}时×{c}' for h, c in top)}"
            f"（深夜 {night} 条 / 白天 {day} 条）",
            min(0.8, 0.35 + len(hours) * 0.03),
            [m["mid"] for m in msgs if m.get("hour") == top[0][0]][:3],
        )

    # ── 周内模式 ──
    weekdays = []
    for m in msgs:
        wd = _weekday(m.get("date"))
        if wd is not None:
            weekdays.append(wd)
    if len(weekdays) >= 6:
        wk = sum(1 for w in weekdays if w < 5)
        we = len(weekdays) - wk
        patterns["周内分布"] = _p(
            f"工作日 {wk} 条 / 周末 {we} 条",
            0.5, [],
        )

    # ── 情绪基调 ──
    pos = sum(1 for t in texts if any(w in t for w in POSITIVE_WORDS))
    neg = sum(1 for t in texts if any(w in t for w in NEGATIVE_WORDS))
    if pos + neg >= 3:
        ratio = pos / (pos + neg)
        trend = "乐观积极" if ratio > 0.6 else ("偏悲观" if ratio < 0.4 else "情绪平稳")
        patterns["情绪基调"] = _p(
            f"{trend}（正面 {pos} / 负面 {neg}）",
            min(0.8, 0.4 + (pos + neg) * 0.03),
            [m["mid"] for m in msgs
             if any(w in m["text"] for w in POSITIVE_WORDS)][:2],
        )

    # ── 数据跨度（修正排序：按日期升序） ──
    dates = sorted({m["date"] for m in msgs if m.get("date")})
    if len(dates) >= 2:
        patterns["数据跨度"] = _p(
            f"{dates[0]} ~ {dates[-1]}（{len(dates)} 天有记录）",
            0.9, [],
        )
    elif len(dates) == 1:
        patterns["数据跨度"] = _p(f"仅 {dates[0]} 一天，节律类结论不可靠", 0.9, [])

    # ── 兴趣广度 ──
    topic_hits = Counter()
    for t in texts:
        low = t.lower()
        for topic, kws in TOPIC_KEYWORDS.items():
            if any(kw.lower() in low for kw in kws):
                topic_hits[topic] += 1
    if topic_hits:
        n = len(topic_hits)
        diversity = "兴趣广泛" if n >= 4 else ("兴趣集中" if n <= 2 else "兴趣适中")
        patterns["兴趣广度"] = _p(f"{diversity}（命中 {n} 个话题域）",
                                  min(0.8, 0.4 + n * 0.08), [])

    # ── 消息切分风格 ──
    if len(texts) >= 5:
        lens = [len(t) for t in texts]
        avg = sum(lens) / len(lens)
        short_ratio = sum(1 for l in lens if l <= 40) / len(lens)
        if short_ratio >= 0.7:
            value = f"短消息为主（{short_ratio:.0%} 少于 40 字），偏好一条一条发"
        elif avg > 80:
            value = "长消息为主，习惯一次说清楚"
        else:
            value = f"长短混合（平均 {avg:.0f} 字）"
        patterns["消息风格"] = _p(value, 0.7,
                                  [max(msgs, key=lambda m: len(m["text"]))["mid"]])

    # ── 连续发送习惯 ──
    runs = _bursts(msgs)
    if runs:
        avg_run = sum(runs) / len(runs)
        rank = sorted(msgs, key=lambda m: m["mid"])
        patterns["连发习惯"] = _p(
            f"平均一次连发 {avg_run:.1f} 条，最长 {max(runs)} 条",
            min(0.7, 0.35 + len(runs) * 0.02),
            [rank[0]["mid"]] if rank else [],
        )

    # ── 样本充分度（自我诚实度：告诉读者这份文件有多可信） ──
    unique = len(msgs)
    if unique < 30:
        grade, conf = "很薄（几十条以内），只够做风格草图", 0.9
    elif unique < 200:
        grade, conf = "偏薄，语言层可信、认知/关系层只能算线索", 0.85
    elif unique < 1000:
        grade, conf = "中等，可支撑 L1-L2 结论", 0.8
    else:
        grade, conf = "充分", 0.8
    patterns["样本充分度"] = _p(f"{unique} 条独立消息 —— {grade}", conf, [])

    return patterns


def _p(value, confidence, evidence):
    return {"value": value, "confidence": round(float(confidence), 2),
            "evidence": [e for e in (evidence or []) if e]}


def _weekday(date_str):
    try:
        return datetime.date.fromisoformat(date_str).weekday()
    except (ValueError, TypeError):
        return None


def _bursts(msgs):
    """按 同一日期+同一小时 分组，组内条数即连发长度。"""
    if not any(m.get("date") for m in msgs):
        return []
    buckets = Counter((m.get("date"), m.get("hour")) for m in msgs
                      if m.get("date") and m.get("hour") is not None)
    runs = [c for c in buckets.values() if c >= 2]
    return runs


def _extract_hour(ts):
    """兼容旧接口：从时间字符串提取小时。"""
    s = str(ts or "").strip()
    if not s:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    try:
        import time as _t
        return _t.localtime(float(s)).tm_hour
    except (ValueError, TypeError, OSError, OverflowError):
        return None
