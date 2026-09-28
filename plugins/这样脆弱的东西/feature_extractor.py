# -*- coding: utf-8 -*-
"""L1 原子特征提取（启发式基线）。

定位变了：这一层现在是**基线**，不是终点。
LLM 会在它之上做逐节深挖；启发式负责给出可量化、零成本、必然跑得出的
那一部分，并且**同样带证据编号** —— 让整份人格文件里没有一条"无主"的结论。

输入是 evidence.normalize() 出来的消息（含 mid / date / hour / dup / noise）。
输出 {section_key: [{"label","value","confidence","evidence","source"}]}
"""
import re
from collections import Counter

from .evidence import URL_RE

# ── 词表 ────────────────────────────────────────────────
TOPIC_KEYWORDS = {
    "游戏": {"游戏", "打游戏", "开黑", "王者", "原神", "崩铁", "LOL", "无畏契约",
             "Steam", "Switch", "ps5", "bug", "报错", "跑不起来"},
    "番剧": {"番", "动画", "新番", "追番", "看番", "动漫", "漫改", "声优", "OP", "ED"},
    "编程": {"代码", "编程", "python", "javascript", "bug", "报错", "写代码",
             "github", "api", "后端", "前端"},
    "生活": {"吃了", "喝了", "睡觉", "起床", "下班", "上班", "周末", "加班",
             "外卖", "奶茶", "咖啡"},
    "情感": {"喜欢", "讨厌", "难过", "开心", "生气", "分手", "表白", "暗恋", "恋爱", "单身"},
    "音乐": {"歌", "音乐", "歌手", "演唱会", "专辑", "单曲", "网易云", "QQ音乐"},
    "八卦": {"瓜", "八卦", "吃瓜", "热搜", "新闻", "爆料"},
    "AI/技术": {"ai", "模型", "提示词", "prompt", "token", "api", "部署", "本地模型",
                "微调", "训练"},
}

POSITIVE_WORDS = {"哈哈", "哈哈哈", "笑死", "太好笑了", "牛", "牛逼", "厉害", "赞",
                  "棒", "不错", "开心", "快乐", "爱了", "真香", "666", "yyds",
                  "绝了", "泪目", "感动", "恭喜", "谢谢", "好的", "可以"}
NEGATIVE_WORDS = {"难过", "伤心", "气死", "烦", "恶心", "垃圾", "什么鬼", "服了",
                  "吐了", "下头", "无语", "生气", "愤怒", "骂", "滚", "别吵",
                  "不行", "不要", "错误", "失败"}

TONE_WORDS = ("哈", "啊", "呢", "吧", "哦", "嘛", "耶", "嘞", "啦", "惹", "呀", "呗", "咯")

IDENTITY_PATTERNS = [
    (r"我(?:是|叫)(.{1,12}?)(?=[，。！？,.!?\s]|$)", "自我称呼"),
    (r"我(?:今年|岁数|年龄)(?:是|有)?(\d{1,2})", "年龄自述"),
]

# 这些串不是"语言风格"，是从链接/代码里漏出来的碎片
_ASCII_BLOCK = {
    "http", "https", "www", "com", "cn", "org", "net", "jpg", "jpeg", "png",
    "gif", "webp", "html", "htm", "json", "php", "asp", "css", "js", "api",
    "url", "type", "chat", "test", "img", "src", "utf", "the", "and", "for",
    "you", "are", "not", "with", "this", "that",
}


def _clean(text):
    """去掉 URL 再统计词 —— 很多"高频词"其实是链接碎片。"""
    return URL_RE.sub(" ", text)


def _ngrams(msgs, limit=8, min_weight=2):
    """中文词频（没有分词器的做法）。

    三条规则让它不至于吐出一堆滑窗碎片：
      1. 权重用**记录条数**（occurrences），所以一句被发了 13 遍的"你好"
         不会被当成只出现 1 次。
      2. 3/4 字候选，若内部某个子串的出现次数不低于它本身，
         说明它只是子串拼出来的，不算独立词 —— 丢掉。
      3. 与已保留的词互相包含时让位给更长的那个。
    """
    counter = Counter()
    where = {}
    for m in msgs:
        if m.get("noise"):
            continue
        text = _clean(m["text"])
        weight = max(1, int(m.get("occurrences", 1)))
        seen_in_msg = set()
        for n in (2, 3, 4):
            for i in range(len(text) - n + 1):
                gram = text[i:i + n]
                if not re.fullmatch(r"[\u4e00-\u9fff]+", gram):
                    continue
                if gram in seen_in_msg:
                    continue          # 同一条消息里的重复位置只算一次
                seen_in_msg.add(gram)
                counter[gram] += weight
                where.setdefault(gram, [])
                if m["mid"] not in where[gram] and len(where[gram]) < 3:
                    where[gram].append(m["mid"])

    cands = {w: c for w, c in counter.items() if c >= min_weight}
    if not cands:
        return []

    kept = []
    for word, count in sorted(cands.items(), key=lambda kv: (-len(kv[0]), -kv[1])):
        if len(word) > 2:
            sub_max = max((cands.get(word[i:i + k], 0)
                           for k in range(2, len(word))
                           for i in range(len(word) - k + 1)), default=0)
            if sub_max >= count:
                continue
        if any(word in k or k in word for k, _ in kept):
            continue
        kept.append((word, count))

    kept.sort(key=lambda kv: (-kv[1], -len(kv[0])))
    return [(w, c, where.get(w, [])) for w, c in kept[:limit]]


def _ascii_words(msgs, limit=8, min_weight=3):
    counter = Counter()
    where = {}
    for m in msgs:
        if m.get("noise"):
            continue
        text = _clean(m["text"])
        weight = max(1, int(m.get("occurrences", 1)))
        for w in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text):
            low = w.lower()
            if low in _ASCII_BLOCK:
                continue
            counter[low] += weight
            where.setdefault(low, [])
            if m["mid"] not in where[low] and len(where[low]) < 3:
                where[low].append(m["mid"])
    top = [w for w, c in counter.most_common(limit) if c >= min_weight]
    return [(w, counter[w], where.get(w, [])) for w in top]


def _examples(msgs, pred, limit=3):
    return [m["mid"] for m in msgs if pred(m["text"])][:limit]


def _mk(label, value, confidence, evidence=None, source="heuristic"):
    return {
        "label": label,
        "value": value,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "evidence": [e for e in (evidence or []) if e],
        "source": source,
    }


# ── 各层启发式 ───────────────────────────────────────────

def _extract_identity(msgs):
    feats = []
    for pat, label in IDENTITY_PATTERNS:
        hit = None
        for m in msgs:
            mm = re.search(pat, m["text"])
            if not mm:
                continue
            val = mm.group(1).strip()[:20]
            if len(val) >= 2 and "我是" not in val:
                hit = (label, val, m["mid"])
            break
        if hit:
            feats.append(_mk(hit[0], hit[1], 0.6, [hit[2]]))
    return feats


def _extract_linguistics(msgs):
    feats = []
    texts = [m["text"] for m in msgs if m.get("text")]
    if not texts:
        return feats

    words = _ngrams(msgs)
    if words:
        feats.append(_mk(
            "高频词",
            "、".join(f"{w}×{c}" for w, c, _ in words),
            min(0.9, 0.45 + len(words) * 0.05),
            [mid for _, _, mids in words for mid in mids][:6],
        ))

    ascii_words = _ascii_words(msgs)
    if ascii_words:
        feats.append(_mk(
            "英文词",
            "、".join(w for w, _, _ in ascii_words),
            min(0.8, 0.4 + len(ascii_words) * 0.05),
            [mid for _, _, mids in ascii_words for mid in mids][:5],
        ))

    # 开头模式（前 2 字）
    starters = Counter(m["text"][:2] for m in msgs if len(m["text"]) >= 2)
    top_starters = [(s, c) for s, c in starters.most_common(3) if c >= 2]
    if top_starters:
        feats.append(_mk(
            "开头模式", "、".join(s for s, _ in top_starters),
            min(0.85, 0.4 + len(top_starters) * 0.15),
            [mid for s, _ in top_starters
             for mid in _examples(msgs, lambda t, s=s: t.startswith(s))][:4],
        ))

    # 结尾语气
    endings = Counter(m["text"][-1] for m in msgs
                      if m["text"] and m["text"][-1] in TONE_WORDS)
    top_endings = [(e, c) for e, c in endings.most_common(3) if c >= 2]
    if top_endings:
        feats.append(_mk(
            "结尾语气", "、".join(e for e, _ in top_endings),
            min(0.85, 0.4 + len(top_endings) * 0.15),
            [mid for e, _ in top_endings
             for mid in _examples(msgs, lambda t, e=e: t.endswith(e))][:4],
        ))

    # 标点
    punct = Counter(ch for m in msgs for ch in m["text"] if ch in "！？…～。，、；：")
    top_punct = [(p, c) for p, c in punct.most_common(4) if c >= 2]
    if top_punct:
        feats.append(_mk(
            "标点习惯", "、".join(p for p, _ in top_punct),
            min(0.85, 0.4 + len(top_punct) * 0.12),
            [mid for p, _ in top_punct
             for mid in _examples(msgs, lambda t, p=p: p in t)][:4],
        ))

    # 表情 / 颜文字 / emoji
    # 注意：括号里带中文的是正文（比如"（语料 A 结束）"），不是颜文字。
    emo_re = re.compile(
        r"[（(][^)）\u4e00-\u9fff]{1,12}[)）]|\[表情:[^\]]+\]"
        r"|[\U0001F300-\U0001FAFF\u2600-\u27bf]")
    emo_counter = Counter()
    for m in msgs:
        for e in emo_re.findall(m["text"]):
            emo_counter[e] += 1
    if emo_counter:
        total_emo = sum(emo_counter.values())
        top_emo = [e for e, c in emo_counter.most_common(4) if c >= 2] or \
                  [e for e, _ in emo_counter.most_common(3)]
        density = total_emo / max(1, len(msgs))
        feats.append(_mk(
            "表情习惯",
            f"{'、'.join(top_emo)}（共 {total_emo} 次，{density:.2f} 次/条）",
            min(0.85, 0.4 + density * 0.3),
            [mid for e in top_emo for mid in _examples(msgs, lambda t, e=e: e in t)][:3],
        ))

    # 长度
    lengths = [len(t) for t in texts]
    avg = sum(lengths) / len(lengths)
    style = "短句为主" if avg < 20 else ("中等句长" if avg < 60 else "长句为主")
    longest = max(msgs, key=lambda m: len(m["text"]))
    feats.append(_mk("句式偏好", f"{style}（平均 {avg:.0f} 字，最长 {max(lengths)} 字）",
                     0.7, [longest["mid"]]))

    # 语气词
    tone_used = []
    for w in TONE_WORDS:
        mids = _examples(msgs, lambda t, w=w: w in t, limit=1)
        if mids:
            tone_used.append((w, mids[0]))
    if tone_used:
        feats.append(_mk("语气词", "、".join(w for w, _ in tone_used[:8]),
                         min(0.8, 0.3 + len(tone_used) * 0.06),
                         [mid for _, mid in tone_used[:4]]))

    # 重复发送：同一句话在同一渠道里被反复发 —— 这是真实行为，不是日志重复
    repeats = [(m, m.get("repeats", 0)) for m in msgs if m.get("repeats")]
    if repeats:
        repeats.sort(key=lambda kv: -kv[1])
        top = repeats[:3]
        feats.append(_mk(
            "重复发送",
            "、".join(f"「{m['text'][:12]}」×{n + 1}" for m, n in top),
            min(0.85, 0.5 + top[0][1] * 0.05),
            [m["mid"] for m, _ in top],
        ))

    # 引用回复习惯：[引用] 是平台给的回复引用标记
    quoted = [m["mid"] for m in msgs if m.get("quoted")]
    if quoted:
        feats.append(_mk("引用回复习惯", f"有 {len(quoted)} 次是引用对方的话再回复",
                         min(0.8, 0.4 + len(quoted) * 0.06), quoted[:4]))

    return feats


def _extract_behavior(msgs):
    feats = []
    if not msgs:
        return feats

    # 话题偏好
    topic_hits = Counter()
    topic_mids = {}
    for m in msgs:
        low = m["text"].lower()
        for topic, kws in TOPIC_KEYWORDS.items():
            if any(kw.lower() in low for kw in kws):
                topic_hits[topic] += 1
                topic_mids.setdefault(topic, [])
                if len(topic_mids[topic]) < 3:
                    topic_mids[topic].append(m["mid"])
    top_topics = [(t, c) for t, c in topic_hits.most_common(5) if c >= 2]
    if top_topics:
        feats.append(_mk("话题偏好", "、".join(f"{t}×{c}" for t, c in top_topics),
                         min(0.9, 0.4 + len(top_topics) * 0.12),
                         [mid for t, _ in top_topics
                          for mid in topic_mids.get(t, [])][:5]))

    # 情绪
    pos_mids = _examples(msgs, lambda t: any(w in t for w in POSITIVE_WORDS), 3)
    neg_mids = _examples(msgs, lambda t: any(w in t for w in NEGATIVE_WORDS), 3)
    pos = sum(1 for m in msgs if any(w in m["text"] for w in POSITIVE_WORDS))
    neg = sum(1 for m in msgs if any(w in m["text"] for w in NEGATIVE_WORDS))
    if pos + neg >= 2:
        if pos > neg * 1.5:
            mood = f"偏积极（正 {pos} / 负 {neg}）"
        elif neg > pos * 1.5:
            mood = f"偏消极（正 {pos} / 负 {neg}）"
        else:
            mood = f"情绪中性（正 {pos} / 负 {neg}）"
        feats.append(_mk("情绪模式", mood, 0.6, (pos_mids + neg_mids)[:4]))

    # 幽默
    humor_words = ("哈哈", "笑死", "噗", "绷不住", "整活", "玩梗", "抽象", "乐了", "草")
    humor_mids = _examples(msgs, lambda t: any(w in t for w in humor_words), 3)
    if len(humor_mids) >= 2:
        feats.append(_mk("幽默信号", f"出现玩梗/调侃类表达 ≥{len(humor_mids)} 次",
                         0.55, humor_mids))

    # 试探 / 追问（行为层最认人的部分）
    ask_mids = _examples(msgs, lambda t: ("?" in t) or ("？" in t), 3)
    if len(ask_mids) >= 2:
        feats.append(_mk("发问倾向", "习惯用问句推进互动", 0.5, ask_mids))

    link_mids = _examples(msgs, lambda t: bool(URL_RE.search(t)), 3)
    if link_mids:
        link_hits = sum(m.get("occurrences", 1) for m in msgs if URL_RE.search(m["text"]))
        feats.append(_mk("丢链接行为",
                         f"共 {link_hits} 次直接发链接/文件（含重复发送）",
                         min(0.8, 0.5 + link_hits * 0.03), link_mids))

    # 反复派活：同一条"要求对方做事"的话被重复发出
    task_words = ("帮我", "你来做", "做完", "发给我", "回传", "做一下", "看看")
    task_hits = [(m, m.get("occurrences", 1)) for m in msgs
                 if any(w in m["text"] for w in task_words)]
    if task_hits:
        task_hits.sort(key=lambda kv: -kv[1])
        feats.append(_mk(
            "派活行为",
            f"反复要求对方完成某件事（{len(task_hits)} 种说法，最多的一种发了 "
            f"{task_hits[0][1]} 次）",
            min(0.85, 0.5 + task_hits[0][1] * 0.04),
            [m["mid"] for m, _ in task_hits[:3]],
        ))

    # 消息长度节奏
    texts = [m["text"] for m in msgs]
    avg_len = sum(len(t) for t in texts) / len(texts)
    rhythm = ("话痨型（长消息多）" if avg_len > 60
              else ("正常" if avg_len > 20 else "惜字如金（短消息为主）"))
    feats.append(_mk("说话节奏", f"{rhythm}（平均 {avg_len:.0f} 字）", 0.6,
                     [max(msgs, key=lambda m: len(m["text"]))["mid"]]))

    return feats


def _extract_cognition(msgs):
    feats = []
    if not msgs:
        return feats

    for m in msgs:
        if re.search(r"(想|希望|打算|目标|梦想|要努力|加油|能不能|帮我)", m["text"]):
            feats.append(_mk("诉求句", m["text"][:48], 0.45, [m["mid"]]))
            break

    tech = {"python", "代码", "模型", "api", "部署", "token", "prompt", "本地"}
    tech_mids = _examples(
        msgs, lambda t: sum(1 for w in tech if w in t.lower()) >= 1, 3)
    if tech_mids:
        feats.append(_mk("技术话题痕迹", "语料里出现技术类词汇，存在技术语境",
                         0.5, tech_mids))
    return feats


def _extract_relationship(msgs):
    feats = []
    close_words = ("贴贴", "么么", "爱你", "亲爱的", "宝", "兄弟", "姐妹", "哥们", "咱")
    mids = _examples(msgs, lambda t: any(w in t for w in close_words), 4)
    if mids:
        feats.append(_mk("亲昵称呼", "使用亲昵称呼/语气",
                         min(0.75, 0.35 + len(mids) * 0.1), mids))
    return feats


def _extract_temporal(msgs):
    feats = []
    hours = [m["hour"] for m in msgs if m["hour"] is not None]
    if len(hours) >= 5:
        top = Counter(hours).most_common(4)
        mids = [m["mid"] for m in msgs if m.get("hour") == top[0][0]][:3]
        feats.append(_mk("活跃时段", "、".join(f"{h}时×{c}" for h, c in top),
                         min(0.8, 0.4 + len(hours) * 0.02), mids))
    dates = sorted({m["date"] for m in msgs if m["date"]})
    if len(dates) >= 2:
        feats.append(_mk("时间跨度",
                         f"{dates[0]} ~ {dates[-1]}（{len(dates)} 天有记录）",
                         0.9,
                         [m["mid"] for m in msgs if m.get("date") == dates[0]][:1] +
                         [m["mid"] for m in msgs if m.get("date") == dates[-1]][:1]))
    elif len(dates) == 1:
        feats.append(_mk("时间跨度", f"语料仅覆盖 {dates[0]} 一天，无法判断长期节律",
                         0.9, [m["mid"] for m in msgs if m.get("date")][:1]))
    return feats


def _extract_environmental(msgs):
    feats = []
    sources = Counter(s for m in msgs for s in (m.get("sources") or []))
    if sources:
        feats.append(_mk("语料来源渠道",
                         "、".join(f"{s}×{c}" for s, c in sources.most_common(4)),
                         0.85, [msgs[0]["mid"]]))
    return feats


# ── 汇总 ────────────────────────────────────────────────

_BUILDERS = {
    "identity": _extract_identity,
    "linguistics": _extract_linguistics,
    "behavior": _extract_behavior,
    "cognition": _extract_cognition,
    "relationship": _extract_relationship,
    "temporal": _extract_temporal,
    "environmental": _extract_environmental,
}


def extract_all(msgs, level="L1"):
    """按等级提取启发式基线，返回 {section_key: [feature]}。只算用得上的层。"""
    from . import sections as _sec
    wanted = {_sec.SECTIONS[s]["key"] for s in _sec.llm_sections_for_level(level)}
    return {key: fn(msgs) for key, fn in _BUILDERS.items() if key in wanted}


def extract_section(key, msgs):
    """单层提取（供增量重算用）。"""
    fn = _BUILDERS.get(key)
    return fn(msgs) if fn else []
