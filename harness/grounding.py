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
证据 = 调用方给的**真实上下文**（最近消息、长期记忆轮次、自己上次说的）。

找不到证据的句子**整句删掉**（不做改写：改写容易造出更怪的句子）。
全删光就返回空串，由调用方决定是"这条不发"还是"换个说法"。

## 刻意保守

  · 只在**短窗口**里判（主张后面 ≤12 字），长句覆盖不到就放过 —— 宁可漏，不可误杀
  · 否定式放过：「没听到」「听不见」「看不到」是诚实表达，不能删
  · 阈值取得高（默认 0.34 双字重合），普通闲聊几乎不会被碰
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

#: 主张后面截多长去比对（短窗口，避免误杀）
_WINDOW = 10
#: 双字重合率阈值
MIN_OVERLAP = 0.25

_SENT_SPLIT = re.compile(r"(?<=[。！？!?])|\n")


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
    return out


def ungrounded(text: str, evidence_texts=None, min_overlap: float = MIN_OVERLAP,
               user_texts=None, own_texts=None) -> list:
    """哪些主张在证据里找不到。evidence_texts 可传 str / list。

    `user_texts`：**对方**说过的话。给偏好类判据用：
      对象只出现在对方的话里、从没出现在自己的话里 → 这是把对方的喜好
      挂到了自己身上（真机：「我喜欢喝酒」→「我就想安静喝口酒」）。
    `own_texts`：**自己**说过的话（偏好判据的对照面；不传时退回 evidence）。
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
        if c["kind"] == "pref":
            # 自己的偏好：对象在"对方说过的话"里有重合、在自己能看到的东西里没有
            # → 这是把对方的喜好挂到了自己身上
            if q and _char_rate(q, user_only) >= 0.4 and _char_rate(q, own_only) < 0.4:
                bad.append(c)
            continue
        if not _grounded(q, evidence, min_overlap):
            bad.append(c)
    return bad


def sanitize(text: str, evidence_texts=None, min_overlap: float = MIN_OVERLAP,
             user_texts=None, own_texts=None):
    """删掉无据的句子。返回 (新文本, 被删的句子列表)。"""
    raw = str(text or "")
    if not raw.strip():
        return raw, []
    bad = ungrounded(raw, evidence_texts, min_overlap=min_overlap,
                     user_texts=user_texts, own_texts=own_texts)
    if not bad:
        return raw, []
    spans = [(c["at"], c["at"] + max(len(c["quote"]), 1)) for c in bad]

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
