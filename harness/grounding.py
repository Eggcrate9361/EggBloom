# -*- coding: utf-8 -*-
"""harness/grounding.py —— 事实围栏：拦"编出来"的句子（1.6.0，2026-11-04）

## 为什么需要它

一天之内抓到四次同类：
  16:28 「刚还骂我记性短呢」      —— 那句是它自己说的（归属错）
  16:45 「刚说别找你」            —— 前面根本没有这句（捏造对话）
  16:45 「一直没听到你那边的声响」 —— 纯文字 bot 声称能听见（捏造感官）
  15:35 「十五个小时了」          —— 实际不到 12 小时（数字拍脑袋）

人格里已经写了「别算到对方头上」「没有耳朵」，但**人设和诚实天然打架**，
模型每次都选人设。所以这一类不能只靠提示词，得有一层**发出去之前的核对**。

## 它做什么

把候选回复切成句子，逐句找"需要证据的主张"：
  · **感官**：听到/听见 + 声音/声响/动静 —— 你没有耳朵，一律无据
  · **指认对方**：你刚说/你之前说/你还骂我…… —— 后面那截要在证据里找得到
  · **指认自己**：我昨天说/我上次答应…… —— 同上
  · **持续情绪**：我继续瞎生气/我还委屈着…… —— 声称之前就有的状态，证据里得真出现过
  · **时长**：十五个小时/两天/三个钟头…… —— 超过"看得见的跨度 + 2 小时"就是拍脑袋
证据 = 调用方给的**真实上下文**（最近消息、长期记忆轮次、自己上次说的）。

时长类是**唯一需要时间戳**的一类：调用方得把「这段对话从什么时候开始」传进来
（`since`，epoch 秒）。没传就**不判** —— 没有尺子就不要量。

找不到证据的句子**整句删掉**（不做改写：改写容易造出更怪的句子）。
全删光就返回空串，由调用方决定是"这条不发"还是"换个说法"。

## 刻意保守

  · 只在**短窗口**里判（主张后面 ≤12 字），长句覆盖不到就放过 —— 宁可漏，不可误杀
  · 否定式放过：「没听到」「听不见」「看不到」是诚实表达，不能删
  · 阈值取得高（默认 0.34 双字重合），普通闲聊几乎不会被碰
  · 时长类只在**有锚点**时才判：跨度看不到半小时、主张不到一小时，都不碰
"""
from __future__ import annotations

import re

#: 感官动词 + 感知对象。你没有耳朵/眼睛，这些一律需要证据
_SENSE = re.compile(r"(听到|听见|听出|看到了|看见|瞅见|看到)")
_SENSE_OBJ = re.compile(r"(声音|声响|动静|说话|叫你|你那边|你刚才|你在干)")
#: 诚实表达 = **否认自己的能力**（听不到/看不见），出现就放过。
# ★ 不能把「没听到」算进来：那仍然在声称"我有耳朵，只是这次没听见"，
#   而纯文字 bot 压根没有耳朵（2026-11-04 真机就是这么露馅的）。
_NEGATED = ("听不到", "听不见", "看不到", "看不见", "收不到", "收不着")

#: 指认对方说过什么
_ACCUSE = re.compile(r"你(?:刚才|刚刚|刚|之前|上次|还|老是|总是|昨天|前天)"
                     r"(?:说|讲|骂|嫌|问|提)")
#: 省主语的指认（「刚说别找你」——中文里主语常省，不能只认"你刚说"）
_ACCUSE_BARE = re.compile(r"(?:刚才|刚刚|刚|之前|上次|昨天|前天|还|老是|总是)"
                          r"(?:说|讲|骂|嫌|问|提)")

#: 自己的偏好表述（可能来自"挪用对方的喜好"）
_PREF = re.compile(r"我(?:就|也|更|最)?(?:喜欢|想|爱喝|爱吃|爱看|爱玩|讨厌|怕)\s*"
                   r"([^，。！？\n]{1,10})")

#: 指认自己说过/做过什么
_SELF = re.compile(r"我(?:昨天|前天|刚才|刚刚|上次|上午|下午|早上|晚上)"
                   r"[^，。！？\n]{0,8}?(?:说|答应|讲过|去了|做了|提过)")
#: 持续性情绪自述（2026-11-05 真机：「那我继续瞎生气，你继续看着」）。
#: 「我生气了」是当下表达，人格里傲娇撒娇的一部分，**不判**；
#: 只有「继续/还/一直/又 + 情绪」才声称了一个之前就存在、需要上下文支撑的状态。
_MOOD = re.compile(
    r"(?:我|人家|本小姐)?\s*(?:继续|还|一直|又|照旧|仍旧|仍然)\s*"
    r"(?:在|瞎|偷偷|默默)?\s*"
    r"(生气|赌气|委屈|吃醋|不高兴|不开心|难过|伤心|记仇|闹别扭)")
#: 情绪词归一化：证据里出现同族词就算有依据（「闹脾气」≈「生气」）
_MOOD_FAMILY = (
    ("生气", ("生气", "发火", "恼", "闹脾气")),
    ("赌气", ("赌气", "生气", "别扭")),
    ("委屈", ("委屈", "冤")),
    ("吃醋", ("吃醋", "醋", "酸")),
    ("不高兴", ("不高兴", "不爽", "生气")),
    ("不开心", ("不开心", "不高兴", "难过")),
    ("难过", ("难过", "难受", "伤心")),
    ("伤心", ("伤心", "难过", "哭")),
    ("记仇", ("记仇", "记着", "记账")),
    ("闹别扭", ("闹别扭", "别扭", "赌气")),
)

#: 主张后面截多长去比对（短窗口，避免误杀）
_WINDOW = 10
#: 双字重合率阈值
MIN_OVERLAP = 0.25

_SENT_SPLIT = re.compile(r"(?<=[。！？!?])|\n")

# ── 时长类（2026-11-05）────────────────────────────────────────────
#: 单位 → 秒。**长的写在前面**：正则按顺序匹配，「分钟」不能被「分」抢走。
_UNIT_SEC = (
    ("个星期", 604800), ("星期", 604800), ("礼拜", 604800),
    ("个钟头", 3600), ("分钟", 60), ("小时", 3600), ("钟头", 3600),
    ("个月", 2592000), ("秒钟", 1), ("周", 604800), ("秒", 1),
    ("分", 60), ("天", 86400), ("日", 86400), ("月", 2592000), ("年", 31536000),
)
_UNIT_ALT = "|".join(u for u, _ in _UNIT_SEC)
_UNIT_MAP = dict(_UNIT_SEC)

#: 「十五个小时」「聊了两天」「一个半钟头」。
#: 只认"数词(+个)(+半)+单位"，不认时刻（「三点」「十点半」没单位，落不进来）。
_DUR_RE = re.compile(
    r"(?P<num>\d{1,3}|[一二两三四五六七八九十百千]+|半)"
    r"\s*(?:个)?\s*(?P<half>半)?\s*"
    r"(?P<unit>" + _UNIT_ALT + r")"
)
#: 后面跟这些 = **时间点**不是时长（「一年前」「三天后」），放过
_DUR_POINT = ("之前", "以前", "之后", "以后", "前", "后")
#: 前面是这些 = 口语虚指（「过两天再说」），不是时长主张
_DUR_IDIOM_PREV = ("过", "再", "改")

_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}

#: 主张小于一小时不判：十分钟、半小时这类差一点太正常，噪声大
TIME_MIN_CLAIM_SEC = 3600
#: 允许的向上取整余量：真过了 2 小时说「三个小时」不算编。
#: 取 2 小时而不是 1 小时，是因为进程重启后 history 从头算，
#: 跨度会突然变短 —— 留足余量，宁可漏判。
TIME_SLACK_SEC = 7200
#: 看得见的跨度不足半小时就不判 —— 没有尺子不要量
TIME_MIN_SPAN_SEC = 1800


def _bigrams(s: str) -> set:
    s = re.sub(r"[\s，。！？、,.!?~～…「」『』\"'（）()]", "", str(s or ""))
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()


def _clean(s: str) -> str:
    return re.sub(r"[\s，。！？、,.!?~～…「」『』\"'（）()]", "", str(s or ""))


def _grounded(claim: str, evidence: str, thr: float) -> bool:
    """主张在证据里有没有着落。

    两条任一成立就算有据：
      · **首词锚点**：主张的头两个字出现在证据里（「别找……」对「别找我了」）
      · 整体双字重合 ≥ 阈值
    只认整体重合的话，短证据（「别找我了，我要睡了」）会被误判成无据。
    """
    q = _clean(claim)
    if len(q) < 2:
        return True
    ev = _clean(evidence)
    if q[:2] and q[:2] in ev:
        return True
    return _overlap(q, evidence) >= float(thr)


def _char_rate(obj: str, text: str) -> float:
    """对象里的字有多大比例出现在 text 里（偏好类用，容忍换词）。"""
    o = set(_clean(obj))
    if not o:
        return 0.0
    b = set(_clean(text))
    return len(o & b) / float(len(o))


def _overlap(claim: str, evidence: str) -> float:
    a = _bigrams(claim)
    if not a:
        return 0.0
    b = _bigrams(evidence)
    if not b:
        return 0.0
    return len(a & b) / float(len(a))


def _quote_after(text: str, start: int) -> str:
    seg = text[start:start + _WINDOW]
    # 必须一起去掉换行：否则 span 会跨到下一句，把无辜的句子一起删掉
    return seg.strip("，,。！？!? \t\n")


def _cn_num(s: str):
    """「十五」→15、「两」→2、「半」→0.5、「一百」→100。认不出返回 None。"""
    s = str(s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return float(int(s))
    if s == "半":
        return 0.5
    total, cur = 0, 0
    for ch in s:
        if ch in _CN_DIGIT:
            cur = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            u = _CN_UNIT[ch]
            total += (cur or 1) * u
            cur = 0
        else:
            return None
    return float(total + cur) if (total or cur) else None


def _dur_claims(text: str) -> list:
    """句子里声称的**时长**：[{quote, at, secs}]。时间点（一年前）不算。"""
    out = []
    s = str(text or "")
    for m in _DUR_RE.finditer(s):
        after = s[m.end():m.end() + 2]
        if any(after.startswith(p) for p in _DUR_POINT):
            continue                          # 「一年前」「三天后」是时间点
        if m.start() > 0 and s[m.start() - 1] in _DUR_IDIOM_PREV:
            continue                          # 「过两天再说」是虚指
        n = _cn_num(m.group("num"))
        if n is None:
            continue
        unit = m.group("unit")
        secs = _UNIT_MAP.get(unit)
        if not secs:
            continue
        if m.group("half"):
            n += 0.5                          # 「一个半小时」
        out.append({"kind": "time", "quote": m.group(0).strip(),
                    "at": m.start(), "secs": n * secs})
    return out


def claims(text: str) -> list:
    """列出这句里"需要证据"的主张：[{kind, quote, at}]。"""
    out = []
    s = str(text or "")
    for m in _SENSE.finditer(s):
        # 否定式放过
        if any(n in s for n in _NEGATED):
            continue
        tail = s[m.start():m.start() + _WINDOW + 6]
        if _SENSE_OBJ.search(tail):
            out.append({"kind": "sense",
                        "quote": tail[:_WINDOW].strip(" \t\r\n"),
                        "at": m.start()})
    _seen_at = set()
    for rx in (_ACCUSE, _ACCUSE_BARE):
        for m in rx.finditer(s):
            if m.start() in _seen_at:
                continue
            _seen_at.add(m.start())
            out.append({"kind": "accuse", "quote": _quote_after(s, m.end()), "at": m.start()})
    for m in _PREF.finditer(s):
        obj = m.group(1).strip()
        if obj:
            out.append({"kind": "pref", "quote": obj, "at": m.start()})
    for m in _SELF.finditer(s):
        out.append({"kind": "self", "quote": _quote_after(s, m.end()), "at": m.start()})
    for m in _MOOD.finditer(s):
        out.append({"kind": "mood", "quote": m.group(1), "at": m.start(),
                    "match": m.group(0)})
    out.extend(_dur_claims(s))
    return out


def ungrounded(text: str, evidence_texts=None, min_overlap: float = MIN_OVERLAP,
               user_texts=None, own_texts=None,
               now=None, since=None) -> list:
    """哪些主张在证据里找不到。evidence_texts 可传 str / list。

    `user_texts`：**对方**说过的话。给偏好类判据用：
      对象只出现在对方的话里、从没出现在自己的话里 → 这是把对方的喜好
      挂到了自己身上（真机：「我喜欢喝酒」→「我就想安静喝口酒」）。
    `own_texts`：**自己**说过的话（偏好判据的对照面；不传时退回 evidence）。
    `now` / `since`：epoch 秒。`since` = **这段对话从什么时候开始**
      （调用方给"看得见的最早时间戳"）。两个都给才核时长；缺一个就跳过时长类。
    """
    if isinstance(evidence_texts, (str, bytes)):
        evidence = str(evidence_texts)
    else:
        evidence = "\n".join(str(x) for x in (evidence_texts or []))
    if own_texts is None:
        own_only = evidence
    elif isinstance(own_texts, (str, bytes)):
        own_only = str(own_texts)
    else:
        own_only = "\n".join(str(x) for x in (own_texts or []))
    if user_texts is None:
        user_only = ""
    elif isinstance(user_texts, (str, bytes)):
        user_only = str(user_texts)
    else:
        user_only = "\n".join(str(x) for x in (user_texts or []))
    bad = []
    for c in claims(text):
        if c["kind"] == "sense":
            bad.append(c)                       # 没有耳朵，永远无据
            continue
        q = c["quote"]
        if not q:                               # 截不到内容，宁可不判
            continue
        if c["kind"] == "time":
            # 证据里原样出现过 = 复述，放行（「都十五个小时了」对上对方那句）
            if _clean(q) and _clean(q) in _clean(evidence):
                continue
            if float(c.get("secs") or 0) < TIME_MIN_CLAIM_SEC:
                continue                        # 十分钟 / 半小时，不判
            if now is None or since is None:
                continue                        # 没有尺子就不量
            try:
                span = float(now) - float(since)
            except (TypeError, ValueError):
                continue
            if span < TIME_MIN_SPAN_SEC:
                continue                        # 看不见半小时，就别拿小时数说事
            if float(c.get("secs") or 0) > span + TIME_SLACK_SEC:
                bad.append(c)                   # 比看得见的跨度还长 → 拍脑袋
            continue
        if c["kind"] == "pref":
            # 自己的偏好：对象在"对方说过的话"里有重合、在自己能看到的东西里没有
            # → 这是把对方的喜好挂到了自己身上
            if q and _char_rate(q, user_only) >= 0.4 and _char_rate(q, own_only) < 0.4:
                bad.append(c)
            continue
        if c["kind"] == "mood":
            # 持续性情绪：证据里必须真的出现过同族情绪词。
            # 不用双字重合率 —— 「生气」两个字太短，0.25 阈值会误杀/误放。
            family = next((words for key, words in _MOOD_FAMILY if key == q), (q,))
            if not any(w and w in evidence for w in family):
                bad.append(c)
            continue
        if not _grounded(q, evidence, min_overlap):
            bad.append(c)
    return bad


def sanitize(text: str, evidence_texts=None, min_overlap: float = MIN_OVERLAP,
             user_texts=None, own_texts=None, now=None, since=None):
    """删掉无据的句子。返回 (新文本, 被删的句子列表)。"""
    raw = str(text or "")
    if not raw.strip():
        return raw, []
    bad = ungrounded(raw, evidence_texts, min_overlap=min_overlap,
                     user_texts=user_texts, own_texts=own_texts,
                     now=now, since=since)
    if not bad:
        return raw, []
    # mood 类的 quote 只是情绪词，span 要盖住整段「我继续瞎生气」才算命中
    spans = [(c["at"], c["at"] + max(len(c.get("match") or c["quote"]), 1))
             for c in bad]

    kept, dropped = [], []
    pos = 0
    for m in _SENT_SPLIT.finditer(raw):
        seg = raw[pos:m.start()]
        if seg.strip():
            a, b = pos, m.start()
            if any(not (b <= s or a >= e) for s, e in spans) or _has_sense(seg):
                dropped.append(seg.strip())
            else:
                kept.append(seg)
        pos = m.start() if m.group(0) == "\n" else m.end()
    tail = raw[pos:]
    if tail.strip():
        if _has_sense(tail) or any(not (len(raw) <= s or pos >= e) for s, e in spans):
            dropped.append(tail.strip())
        else:
            kept.append(tail)
    elif tail:
        kept.append(tail)
    return "".join(kept).strip(), dropped


def _has_sense(seg: str) -> bool:
    """这一段自己就带无据感官（片段级再判一次，因为 span 可能落到别段）。"""
    if any(n in seg for n in _NEGATED):
        return False
    for m in _SENSE.finditer(seg):
        if _SENSE_OBJ.search(seg[m.start():m.start() + _WINDOW + 6]):
            return True
    return False


__all__ = ["claims", "ungrounded", "sanitize", "MIN_OVERLAP"]
