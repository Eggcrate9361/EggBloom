# -*- coding: utf-8 -*-
"""风格画像：把「他怎么说话」从描述变成**可执行的参数**。

之前的缺口（用户一句"还是不够"点破的）：
  蒸馏出来的是一叠**结论**——「短句为主」「几乎不用句号」「爱用语气词」。
  但要让另一个模型照着说话，结论没用，**参数和例句**才有用：

    「平均 11 字，最长 26，P90 = 18」      → 模型知道该写多长
    「句号率 4%，问号率 21%，无标点率 46%」 → 模型知道标点怎么处理
    「'在干嘛'出现 9 次、'宝宝'7 次」       → 模型知道口头禅是哪些
    「一次连发 1.6 条，最多 5 条」          → 模型知道要不要拆成几条
    「私聊平均 8 字、群聊平均 19 字」        → 模型知道看场合
    「6 月平均 14 字 → 8 月平均 9 字」       → 模型知道他在变

  再配 8~15 条**真实原话**当风格锚点，模型才有东西可模仿。

全部是确定性计算，**一次 LLM 调用都不花**。
"""
import re
from collections import Counter, defaultdict

_TONE_WORDS = ("啊", "吧", "呢", "呗", "哦", "噢", "喔", "唉", "哎", "嘿", "哈",
               "嘛", "呀", "哟", "欸", "诶", "卧槽", "我草", "草", "服了", "绷不住了",
               "牢", "急", "乐", "蚌", "典")
_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27bf]|（[^)）]{1,12}）|\[[^\[\]]{1,6}\]")
# 注意：必须用字符类。之前写成 "？?"，那个 `?` 被当成量词（"？可有可无"），
# 结果问号率恒等于 100% —— 一个正则 bug 产出了一条看似合理的假结论。
_PUNCT = (("句号", r"[。．]"), ("问号", r"[？?]"), ("感叹", r"[！!]"),
          ("省略", r"…|\.{2,}|。{2,}"))
_OPENER = ("在吗", "在干嘛", "在不在", "早", "你好", "喂", "哈喽", "哎", "诶")
_CLOSER = ("晚安", "睡了", "明天见", "拜拜", "再见", "先这样", "我去", "回头说")


def _nums(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return {}
    def pct(p):
        return values[min(n - 1, int(round(p * (n - 1))))]
    return {"avg": round(sum(values) / n, 1), "median": pct(0.5),
            "p90": pct(0.9), "max": values[-1], "n": n}


def _len_of(text):
    """有效长度：去掉平台标记和空白。"""
    t = re.sub(r"\[[^\]]{1,20}\]", "", text or "")
    t = re.sub(r"\s+", "", t)
    return len(t)


def _punct_profile(texts):
    n = len(texts) or 1
    out = {}
    for label, pat in _PUNCT:
        hit = sum(1 for t in texts if re.search(pat, t))
        out[label] = round(hit / n, 3)
    no_punct = sum(1 for t in texts
                   if _len_of(t) >= 2 and not re.search(r"[。．.，,？！?!、；;：:\s]", t))
    out["无标点"] = round(no_punct / n, 3)
    out["样本"] = len(texts)
    return out


def _tone_words(texts, limit=10):
    c = Counter()
    for t in texts:
        for w in _TONE_WORDS:
            c[w] += t.count(w)
    total = sum(c.values())
    top = [[w, n] for w, n in c.most_common(limit) if n >= 2]
    return {"top": top, "total": total,
            "per_msg": round(total / max(1, len(texts)), 2)}


def _emotes(texts, limit=8):
    c = Counter()
    for t in texts:
        for m in _EMOJI.findall(t or ""):
            c[m] += 1
    return {"top": [[k, v] for k, v in c.most_common(limit) if v >= 2],
            "users": len([t for t in texts if _EMOJI.search(t or "")])}


def _bursts(msgs, gap_minutes=8):
    """按时间把消息聚成「一口气」——连发习惯只有这样才能看出来。"""
    keyed = [m for m in msgs if m.get("ts")]
    keyed.sort(key=lambda m: m["ts"])
    groups, cur = [], []
    last = None
    for m in keyed:
        t = m["ts"]
        if last:
            try:
                from datetime import datetime
                import time as _t
                fmt = "%Y-%m-%d %H:%M:%S" if len(t) > 16 else "%Y-%m-%d %H:%M"
                a = _t.mktime(_t.strptime(last, fmt))
                b = _t.mktime(_t.strptime(t, fmt))
                gap = (b - a) / 60.0
            except Exception:
                gap = 999
            if gap > gap_minutes and cur:
                groups.append(cur)
                cur = []
        cur.append(m)
        last = t
    if cur:
        groups.append(cur)
    sizes = [len(g) for g in groups if g]
    return {"bursts": len(sizes), "avg_size": round(sum(sizes) / len(sizes), 2) if sizes else 0,
            "max_size": max(sizes) if sizes else 0,
            "multi_ratio": round(sum(1 for s in sizes if s >= 2) / len(sizes), 2) if sizes else 0,
            "groups": groups}


def _open_close(groups):
    """开场白与收尾语：一组的第一条 / 最后一条。"""
    openers, closers = Counter(), Counter()
    for g in groups:
        if len(g) < 2:
            continue
        first = (g[0].get("text") or "").strip()
        last = (g[-1].get("text") or "").strip()
        for pat in _OPENER:
            if first.startswith(pat):
                openers[pat] += 1
                break
        for pat in _CLOSER:
            if pat in last:
                closers[pat] += 1
                break
    return {"openers": openers.most_common(6), "closers": closers.most_common(6)}


def _hour_band(hour):
    if hour is None:
        return "未知"
    if 0 <= hour < 6:
        return "深夜(0-6)"
    if 6 <= hour < 12:
        return "上午"
    if 12 <= hour < 18:
        return "下午"
    return "晚间(18-24)"


def _band_profile(msgs):
    """分时段说话方式：深夜和白天往往完全不同。"""
    bands = defaultdict(list)
    for m in msgs:
        bands[_hour_band(m.get("hour"))].append(m)
    out = {}
    for band, items in bands.items():
        texts = [m.get("text") or "" for m in items]
        if len(texts) < 3:
            continue
        out[band] = {
            "n": len(items),
            "len": _nums([_len_of(t) for t in texts]),
            "top_words": [[w, c] for w, c in Counter(
                re.sub(r"[^\u4e00-\u9fff]", " ", t)
                for t in texts).most_common(0)] or None,
        }
        # 分时段的口头禅
        wc = Counter()
        for t in texts:
            for w in re.findall(r"[\u4e00-\u9fff]{2,4}", t):
                wc[w] += 1
        out[band]["words"] = [[w, c] for w, c in wc.most_common(6) if c >= 2]
        out[band]["punct"] = _punct_profile(texts)
    return out


def _drift(msgs, by="month"):
    """分时段对比：人的说话方式会变，这能看出来。"""
    buckets = defaultdict(list)
    for m in msgs:
        d = str(m.get("date") or "")
        if not d:
            continue
        key = d[:7] if by == "month" else d[:4]
        buckets[key].append(m)
    out = {}
    for key in sorted(buckets):
        items = buckets[key]
        texts = [m.get("text") or "" for m in items]
        if len(texts) < 3:
            continue
        out[key] = {
            "n": len(items),
            "len": _nums([_len_of(t) for t in texts]),
            "punct": _punct_profile(texts),
        }
    return out


def _by_scope(msgs):
    """按场景（私聊对机器 / 群聊对众人）——同一人对不同对象说话方式不同。"""
    scopes = defaultdict(list)
    for m in msgs:
        sc = m.get("scope") or "未知"
        scopes[sc].append(m)
    out = {}
    for sc, items in scopes.items():
        texts = [m.get("text") or "" for m in items]
        if len(texts) < 3:
            continue
        out[sc] = {
            "n": len(items),
            "len": _nums([_len_of(t) for t in texts]),
            "punct": _punct_profile(texts),
            "tone": _tone_words(texts, 6),
        }
    return out


def _exemplars(msgs, n=12):
    """挑风格锚点：真实原话，覆盖不同长度与场景。

    刻意挑**有代表性的**而不是最有趣的：短句、长句、带语气词的、
    带标点习惯的、深夜的、群聊的——每种至少一条，让模型有东西可模仿。
    """
    scored = []
    for m in msgs:
        t = (m.get("text") or "").strip()
        L = _len_of(t)
        if L < 2 or L > 120 or m.get("noise"):
            continue
        if t.startswith("["):
            continue
        reasons = []
        if L <= 6:
            reasons.append("短句")
        elif L >= 30:
            reasons.append("长句")
        if re.search(r"[？?]", t):
            reasons.append("问句")
        if re.search(r"[！!]", t):
            reasons.append("感叹")
        if any(w in t for w in _TONE_WORDS):
            reasons.append("语气词")
        if m.get("scope") == "group":
            reasons.append("群聊")
        if m.get("hour") is not None and 0 <= m["hour"] < 5:
            reasons.append("深夜")
        if not reasons:
            continue
        scored.append((len(reasons), -abs(L - 12), m, reasons))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    out, seen = [], set()
    buckets = set()
    for _, _, m, reasons in scored:
        if m["mid"] in seen:
            continue
        key = tuple(sorted(reasons))[:2]
        if key in buckets and len(out) >= n // 2:
            continue
        buckets.add(key)
        seen.add(m["mid"])
        out.append({"mid": m["mid"], "text": t_style(m["text"]),
                    "len": _len_of(m["text"]), "why": "/".join(reasons[:3]),
                    "scope": m.get("scope") or "",
                    "when": (m.get("date") or "")})
        if len(out) >= n:
            break
    return out


def t_style(text):
    return (text or "").strip()


def build(msgs, exemplar_count=12):
    """完整风格画像。msgs 是**去重后的发言**（带 mid / date / hour / scope）。"""
    texts = [(m.get("text") or "") for m in msgs]
    lengths = [_len_of(t) for t in texts]
    prof = {
        "length": _nums(lengths),
        "punct": _punct_profile(texts),
        "tone": _tone_words(texts),
        "emote": _emotes(texts),
        "by_hour": {k: v for k, v in sorted(
            Counter(_hour_band(m.get("hour")) for m in msgs).items())},
        "by_context": _by_scope(msgs),
        "by_period": _drift(msgs, "month"),
        "exemplars": _exemplars(msgs, exemplar_count),
    }
    b = _bursts(msgs)
    prof["burst"] = {k: v for k, v in b.items() if k != "groups"}
    prof["open_close"] = _open_close(b.get("groups") or [])
    return prof


# ── 转成账本能跟踪的结论 ────────────────────────────────

def to_features(prof, mids=None):
    """把画像转成特征条目，让它进账本、被反复确认、也能看出漂移。

    返回按 section 分组的特征：linguistics / behavior / temporal / environmental。
    """
    out = {"linguistics": [], "behavior": [], "temporal": [], "environmental": []}
    pick = (mids or {}).get

    def ev(key):
        v = pick(key)
        return v if isinstance(v, list) else []

    L = prof.get("length") or {}
    if L.get("n"):
        out["linguistics"].append({
            "label": "句子长度分布",
            "value": (f"平均 {L['avg']} 字，中位 {L['median']}，"
                      f"P90 {L['p90']}，最长 {L['max']}（{L['n']} 条）"),
            "confidence": 0.9, "evidence": ev("len_max"), "source": "heuristic",
        })
    P = prof.get("punct") or {}
    if P.get("样本"):
        bits = [f"{k} {v:.0%}" for k, v in P.items()
                if k not in ("样本",) and isinstance(v, (int, float)) and v >= 0.15]
        if bits:
            out["linguistics"].append({
                "label": "标点习惯（量化）",
                "value": "、".join(bits) + f"（{P['样本']} 条样本）",
                "confidence": 0.85, "evidence": ev("punct"), "source": "heuristic",
            })
    T = prof.get("tone") or {}
    if T.get("top"):
        out["linguistics"].append({
            "label": "口头禅频率",
            "value": "、".join(f"「{w}」×{n}" for w, n in T["top"][:8])
                     + f"，平均每条 {T.get('per_msg', 0)} 个",
            "confidence": 0.8, "evidence": ev("tone"), "source": "heuristic",
        })
    E = prof.get("emote") or {}
    if E.get("top"):
        out["linguistics"].append({
            "label": "表情/颜文字使用",
            "value": "、".join(f"{k}×{v}" for k, v in E["top"][:6])
                     + f"（{E.get('users', 0)} 条消息里出现）",
            "confidence": 0.7, "evidence": ev("emote"), "source": "heuristic",
        })
    B = prof.get("burst") or {}
    if B.get("bursts"):
        out["behavior"].append({
            "label": "连发习惯",
            "value": (f"一口气平均 {B['avg_size']} 条、最多 {B['max_size']} 条，"
                      f"{B.get('multi_ratio', 0):.0%} 的场合会连发"),
            "confidence": 0.75, "evidence": ev("burst_max"), "source": "heuristic",
        })
    OC = prof.get("open_close") or {}
    if OC.get("openers"):
        out["behavior"].append({
            "label": "开场方式",
            "value": "、".join(f"「{w}」×{n}" for w, n in OC["openers"][:5]),
            "confidence": 0.6, "evidence": ev("opener"), "source": "heuristic",
        })
    if OC.get("closers"):
        out["behavior"].append({
            "label": "收尾方式",
            "value": "、".join(f"「{w}」×{n}" for w, n in OC["closers"][:5]),
            "confidence": 0.6, "evidence": ev("closer"), "source": "heuristic",
        })
    BH = prof.get("by_hour") or {}
    if BH:
        out["temporal"].append({
            "label": "时段分布",
            "value": "、".join(f"{k} {v} 条" for k, v in BH.items() if v),
            "confidence": 0.8, "evidence": ev("hour"), "source": "heuristic",
        })
    deep = None
    for band, info in (prof.get("band_profile") or {}).items():
        if band.startswith("深夜") and info.get("n", 0) >= 5:
            deep = info
    if deep:
        out["temporal"].append({
            "label": "深夜说话方式",
            "value": (f"深夜发 {deep['n']} 条，平均 {deep['len'].get('avg', 0)} 字"
                      f"（白天对比见环境层）"),
            "confidence": 0.7, "evidence": ev("deep_night"), "source": "heuristic",
        })
    BS = prof.get("by_context") or {}
    for sc, info in BS.items():
        name = {"private": "私聊（对机器人）", "group": "群聊（对众人）"}.get(sc, sc)
        out["environmental"].append({
            "label": f"{name}说话方式",
            "value": (f"{info['n']} 条，平均 {info['len'].get('avg', 0)} 字"
                      f"（中位 {info['len'].get('median', 0)}），"
                      f"标点：{'、'.join(f'{k} {v:.0%}' for k, v in (info.get('punct') or {}).items() if isinstance(v, (int, float)) and v >= 0.15) or '几乎不用'}"),
            "confidence": 0.75, "evidence": ev(f"scope_{sc}"), "source": "heuristic",
        })
    DR = prof.get("by_period") or {}
    if len(DR) >= 2:
        keys = sorted(DR)
        a, c = DR[keys[0]], DR[keys[-1]]
        out["temporal"].append({
            "label": "语言变化",
            "value": (f"{keys[0]} 平均 {a['len'].get('avg', 0)} 字 → "
                      f"{keys[-1]} 平均 {c['len'].get('avg', 0)} 字"
                      f"（{keys[0]} {a['n']} 条 / {keys[-1]} {c['n']} 条）"),
            "confidence": 0.6, "evidence": ev("drift"), "source": "heuristic",
        })
    return {k: v for k, v in out.items() if v}
