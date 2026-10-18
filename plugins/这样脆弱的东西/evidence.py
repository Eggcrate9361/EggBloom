# -*- coding: utf-8 -*-
"""证据层：给每条消息一个编号，让每一条结论都能回溯到原文。

蒸馏最容易退化成"印象派"——说得头头是道，却没有一句能指回原话。
这个模块把原材料变成**可引用的证据**：编号、时间归一、跨源合并、
平台噪声剥离、切片投喂、引用校验。

关于三种"重复"，必须分清楚，否则结论会跑偏：
  - 跨源重复：同一条消息被 chat_logs / super_memory / private_memory 各记一份
    → 这是**同一次发言**，合并，只保留来源清单。
  - 同源重复：同一来源里同一句话在多个时间点出现
    → 这是**真的重复发言**（刷屏），保留累计次数，是行为特征。
  - 平台噪声：[引用] 前缀、URL 本身
    → 剥离成标记位，不污染语言统计，但保留为可查证据。

约定：消息编号 = M0001、M0002 …… 本次蒸馏内稳定唯一。
"""
import hashlib
import os
import re
from collections import Counter

# ── 正则 ────────────────────────────────────────────────
# URL 正则：必须在遇到中日文字符时停下，否则 "\S+" 会把链接后面的中文一起吃掉
URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+"
                    r"|www\.[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+")
_URL_RE = URL_RE
_QUOTE_RE = re.compile(r"\[\s*引用\s*\]\s*")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"\w", re.UNICODE)
_HOUR_RE = re.compile(r"(\d{1,2}):(\d{2})")
_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_SRC_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_MID_RE = re.compile(r"\bM(\d{3,5})\b")


# ── 文本清洗 ────────────────────────────────────────────

def clean_text(text):
    """剥离平台痕迹：引用标记、多余空白。URL 保留（它是证据）。"""
    t = _QUOTE_RE.sub("", text or "")
    return re.sub(r"\s+", " ", t).strip()


# 群聊 @ 提及：`@某人 内容` 是**发言者在称呼别人**，不是他被叫。
# 这个方向搞反会直接产出"他的名字是 XXX"这种错误结论，所以要显式标出来。
_AT_RE = re.compile(r"^\s*@\s*([^\s@]{1,20})\s+")


def at_target(text):
    """如果这条消息是 @某人的发言，返回被称呼的名字。"""
    m = _AT_RE.match(text or "")
    return m.group(1) if m else ""


def is_noise(text):
    """纯符号串 / 只剩 URL —— 标记出来给统计降权。

    只标记不删除：可以查证，且"这个人只发链接"本身就是行为特征。
    """
    stripped = _URL_RE.sub(" ", text or "").strip()
    return not _WORD_RE.search(stripped)


def has_link(text):
    return bool(_URL_RE.search(text or ""))


# ── 指纹（跨轮身份）─────────────────────────────────────

def fingerprint(raw):
    """一条原始记录的身份：来源 + 时间 + 内容。

    连续蒸馏靠它判断"这条消息我上轮是不是已经吃过了"，
    也让消息编号能够跨轮保持稳定 —— 否则增量蒸馏会让老引用指向错的消息。
    """
    key = f"{raw.get('source', '')}|{raw.get('timestamp', '')}|{raw.get('text', '')}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


# ── 时间 ────────────────────────────────────────────────

def _time_parts(ts, source=""):
    """从各种时间格式里抠出 (date, hour)。拿不到就 None。

    支持 "16:40:41" / "2026-08-10 12:54" / 1689020400.5 / 来源文件名里的日期。
    """
    s = str(ts or "").strip()
    date = hour = None

    m = _DATE_RE.search(s)
    if m:
        date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    m = _HOUR_RE.search(s)
    if m:
        try:
            hour = int(m.group(1))
        except ValueError:
            hour = None

    if date is None or hour is None:
        try:
            import time as _t
            lt = _t.localtime(float(s))
            date = date or f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d}"
            if hour is None:
                hour = lt.tm_hour
        except (ValueError, TypeError, OSError, OverflowError):
            pass

    if date is None:
        m = _SRC_DATE_RE.search(str(source or ""))
        if m:
            date = m.group(1)
    return date, hour


# ── 规范化 ──────────────────────────────────────────────

def normalize(messages, id_map=None, start=1):
    """规范化消息：清洗 → 排序 → 跨源合并 → 编号。

    参数：
      id_map  已有的「指纹 → 编号」映射（来自账本）。
              有了它，同一条消息在任何一轮蒸馏里都拿到同一个编号，
              历史上写下的 `M0007` 永远指向同一次发言。
      start   新消息的起始编号。

    返回 (msgs, index, id_map, next_id)。
    """
    prepared = []
    for i, raw in enumerate(messages or []):
        raw_text = (raw.get("text") or "").strip()
        text = clean_text(raw_text)
        if not text:
            continue
        source = str(raw.get("source") or "")
        date, hour = _time_parts(raw.get("timestamp"), source)
        prepared.append({
            "text": text,
            "ts": str(raw.get("timestamp") or ""),
            "date": date,
            "hour": hour,
            "source": source,
            "kind": raw.get("kind") or "message",
            # 上下文字段：场景（私聊/群聊）、机器人当时的回复、发送者是否为推测。
            # 没有它们就做不了场景对比、按对象分析、图灵成对样本 ——
            # 之前漏了这三个键，导致 291 条的 scope 全是 None。
            "scope": raw.get("scope") or "",
            "context": raw.get("context") or "",
            "assumed_speaker": bool(raw.get("assumed_speaker")),
            "quoted": bool(_QUOTE_RE.search(raw_text)),
            "fp": fingerprint(raw),
            "_seq": i,
        })

    # 有日期的按时间排前面，没日期的按原顺序接在后面（稳定排序）
    prepared.sort(key=lambda m: (
        1 if not m["date"] else 0,
        m["date"] or "",
        m["hour"] if m["hour"] is not None else 99,
        m["_seq"],
    ))

    merged = []
    seen = {}
    for m in prepared:
        key = m["text"]
        hit = seen.get(key)
        if hit is None:
            m["occurrences"] = 1
            m["sources"] = [m["source"]] if m["source"] else []
            m["fps"] = [m["fp"]]
            m["kinds"] = {(m["source"] or "").split("/")[0]}
            m["source_counts"] = {m["source"]: 1} if m["source"] else {}
            m["scope_counts"] = {(m.get("scope") or "未知"): 1}
            m["repeats"] = 0          # 同一渠道内的重复 = 真的又发了一遍
            seen[key] = m
            merged.append(m)
            continue

        # 同一条话被记录第二次：判断是"另一本日志记了同一次发言"还是"真的又发了一遍"
        hit["occurrences"] += 1
        hit["source_counts"][m["source"]] = hit["source_counts"].get(m["source"], 0) + 1
        if m["source"] and m["source"] not in hit["sources"]:
            hit["sources"].append(m["source"])
        kind = (m["source"] or "").split("/")[0]
        if kind in hit["kinds"]:
            hit["repeats"] += 1
        else:
            hit["kinds"].add(kind)
        if m["fp"] not in hit["fps"]:
            hit["fps"].append(m["fp"])

        # 补齐上下文字段。这些不在合并键里，却决定了能不能做场景对比、
        # 按对象分析、以及图灵测试的成对样本 —— 之前在这一步被整个丢掉，
        # 导致 291 条的 scope 全是 None、机器人的真实回复一条都没带上。
        for k in ("scope", "context", "assumed_speaker", "at"):
            if not hit.get(k) and m.get(k):
                hit[k] = m[k]
        sc = m.get("scope") or "未知"
        hit.setdefault("scope_counts", {})
        hit["scope_counts"][sc] = hit["scope_counts"].get(sc, 0) + 1

    # 编号：优先复用已有的，新的往后排
    used = dict(id_map or {})
    numeric = [int(x[1:]) for x in used.values() if isinstance(x, str) and x[1:].isdigit()]
    cursor = max(int(start or 1), (max(numeric) + 1) if numeric else 1)
    index = {}
    for m in merged:
        mid = None
        for fp in m["fps"]:
            if fp in used:
                mid = used[fp]
                break
        if mid is None:
            mid = f"M{cursor:04d}"
            cursor += 1
        for fp in m["fps"]:
            used.setdefault(fp, mid)
        m["mid"] = mid
        m["noise"] = is_noise(m["text"])
        m["link"] = has_link(m["text"])
        m["at"] = at_target(m["text"])
        m.pop("_seq", None)
        index[mid] = m
    return merged, index, used, cursor


# ── 统计 ────────────────────────────────────────────────

def stats(msgs, raw_total=None, counts=None):
    """整体统计：既能喂给模式分析，也是"这份文件有多可信"的依据。

    刻意把「发言」和「平台事实」分开计数 —— 好感度、群黑话这类是机器记下的
    结构化数据，拿它去凑"这人说了多少句话"会虚高，等级判定也会失真。
    """
    talks = [m for m in msgs if m.get("kind", "message") == "message"]
    facts = [m for m in msgs if m.get("kind") == "fact"]
    texts = [m.get("text") or "" for m in talks]
    lengths = [len(t) for t in texts]
    hours = [m.get("hour") for m in talks if m.get("hour") is not None]
    dates = sorted({m.get("date") for m in talks if m.get("date")})

    return {
        "raw_total": int(raw_total if raw_total is not None else len(msgs)),
        "unique": len(talks),
        "fact_count": len(facts),
        "merged_duplicates": max(0, int(raw_total if raw_total is not None
                                        else len(msgs)) - len(msgs)),
        "chars": sum(lengths),
        "avg_len": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        "max_len": max(lengths) if lengths else 0,
        "noise_count": sum(1 for m in talks if m.get("noise")),
        "link_count": sum(1 for m in talks if m.get("link")),
        "repeat_total": sum(m.get("repeats", 0) for m in talks),
        "span": (f"{dates[0]} ~ {dates[-1]}" if len(dates) >= 2
                 else (dates[0] if dates else "未知")),
        "days": len(dates),
        "hour_hist": dict(sorted(Counter(hours).items())),
        "sources": counts or {},
    }


# ── 引用 ────────────────────────────────────────────────

def cited_ids(text):
    """从文本里抽出所有引用的消息编号。"""
    return [f"M{int(n):04d}" for n in _MID_RE.findall(str(text or ""))]


def verify_citations(text, index):
    ok, bad = [], []
    for mid in cited_ids(text):
        if mid in index:
            if mid not in ok:
                ok.append(mid)
        elif mid not in bad:
            bad.append(mid)
    return ok, bad


def quote(index, mids, limit=3, width=40):
    """把编号还原成原文片段，供人格文件里展示（读者可直接核对）。"""
    parts = []
    for mid in (mids or [])[:limit]:
        m = index.get(mid)
        if not m:
            continue
        t = m["text"]
        if len(t) > width:
            t = t[:width] + "…"
        parts.append(f'{mid}「{t}」')
    return " ".join(parts)


# ── 投喂 LLM ────────────────────────────────────────────

def corpus_text(msgs, max_chars=None):
    """渲染带编号的原文语料。

    同一条消息的真实重复用 ×N 标出 —— 信息不丢（"这人把同一句话发了 8 遍"
    是硬特征），token 却省下来了。
    """
    lines = []
    used = 0
    for m in msgs:
        head = m["mid"]
        if m["date"]:
            head += f" {m['date']}"
        if m["hour"] is not None:
            head += f" {m['hour']:02d}:xx"
        marks = []
        if m.get("kind") == "fact":
            marks.append("平台事实")
        if m.get("at"):
            marks.append(f"他@了「{m['at']}」")
        if m.get("occurrences", 1) > 1:
            marks.append(f"共{m['occurrences']}次")
        if m.get("repeats"):
            marks.append(f"其中重复发{m['repeats']}次")
        if m.get("quoted"):
            marks.append("引用回复")
        if m.get("link"):
            marks.append("含链接")
        suffix = ("　[" + "；".join(marks) + "]") if marks else ""
        line = f"[{head}] {m['text']}{suffix}"
        if max_chars and used + len(line) > max_chars:
            lines.append(f"…（语料超出本次窗口，余下 {len(msgs)} 条中的后续部分已省略）")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def chunk(msgs, chunk_chars=12000):
    """按字符预算分块（尽量不拆散相邻消息）。"""
    chunks, buf, size = [], [], 0
    for m in msgs:
        n = len(m["text"]) + 32
        if buf and size + n > chunk_chars:
            chunks.append(buf)
            buf, size = [], 0
        buf.append(m)
        size += n
    if buf:
        chunks.append(buf)
    return chunks or [[]]


def talks(msgs):
    """只留"这个人说过的话"，剔掉平台记下的结构化事实。

    统计词频、作息、句式这些不能用事实（否则「好感度评分 72」里的
    「好感度」会被当成口头禅），但证据编号里两者都要有。
    """
    return [m for m in (msgs or []) if m.get("kind", "message") == "message"]


def digest(msgs, raw_total=None):
    """给 LLM 的原料清单。

    刻意写得"不好看"——模型必须知道自己的证据基础有多薄，
    才不会拿几十条消息编出一个人生。
    """
    s = stats(msgs, raw_total=raw_total)
    hours = s["hour_hist"]
    busiest = "、".join(f"{h}时×{c}" for h, c in
                        sorted(hours.items(), key=lambda kv: -kv[1])[:4]) or "未知"
    parts = [
        f"独立发言 {s['unique']} 条（原始记录 {s['raw_total']} 条，"
        f"其中 {s['merged_duplicates']} 条是同一句被多个日志重复记录）",
        f"总字数 {s['chars']}，平均 {s['avg_len']} 字/条，最长 {s['max_len']} 字",
        f"覆盖 {s['days']} 个日期：{s['span']}",
        f"活跃时段：{busiest}",
    ]
    if s["fact_count"]:
        parts.append(f"另有平台结构化事实 {s['fact_count']} 条"
                     f"（好感度、群黑话、群情绪等，标着「平台事实」）")
    if s["repeat_total"]:
        parts.append(f"同一条消息被重复发送共 {s['repeat_total']} 次")
    if s["link_count"]:
        parts.append(f"含链接的消息 {s['link_count']} 条")
    if s["noise_count"]:
        parts.append(f"纯链接/符号类消息 {s['noise_count']} 条")
    return "；\n".join(parts) + "。"
