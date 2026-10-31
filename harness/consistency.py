# -*- coding: utf-8 -*-
"""harness/consistency.py —— 「别改口」的确定性兜底（自述账本 + 否认检测）

## 为什么还要这一层

人格文件里已经写了「说过的话要认」，prompt 里也已经摆了「你上一条说的」原文
（`_build_cached_messages`）。但提示词是概率的：2026-10-31 真机，模型在被用户
一句「你唐的吧」之后，**照样**把「另一张我这儿没取到」改口成「哪来的第二张，我瞎说的？」。

参考 dsh-qqbot-bridge 的做法（AGENTS.md：唯一事实来源 / 契约测试 / 显式状态机）：
**不靠模型自觉，靠可判定的状态。** 这里的"状态"就是"我说过什么"。

## 判据（全部纯规则，零成本）

1. 用户这句话是不是在**主张我说过某话**（「你不是说…」「你刚才说…」）
2. 我的回复是不是在**否认**（「我没说」「哪来的」「我瞎说的」「逗你玩」）
3. 从用户话里抽出**被主张的内容**，和最近几条我自己说过的话比字符 bigram 重合度
4. 重合度够高 = 我确实说过 → 判定 conflict，调用方改成认账，不许发否认

宁漏不误伤：三个条件缺一条就放行。
"""
import re

#: 用户"主张我说过"的触发词
_CLAIM_TRIGGERS = (
    "你不是说", "你不是讲", "你不是答应", "你刚才说", "你刚刚说", "你刚才讲",
    "你说过", "你前面说", "你之前说", "你刚说",
)

#: 回复里的"否认"标记
_DENY_MARKERS = (
    "我没说", "没说过", "我没讲过", "哪来的", "哪有什么", "没这回事",
    "我瞎说", "瞎说的", "逗你玩", "骗你的", "我什么时候说", "我哪说",
    "我哪句说", "没有第二",
)

#: 抽取被主张内容时要去掉的尾巴
_TAIL = "吗呀啊呢吧哈嘛哦啦。？！?!，, \t\n"

#: 只滤掉纯虚词 bigram（"的"~"我"这种），**不要**把 一张/还有 这类实词也滤掉 ——
#: 2026-10-31 踩过：_STOP 放太宽把「还有一张」三个 bigram 全滤空，判据直接失效。
_STOP = set("的了是我你他她它")


def _bigrams(s: str) -> set:
    s = re.sub(r"[\s，。！？、；：,.!?;:\"'「」（）()]+", "", str(s or ""))
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def extract_claim(user_text: str) -> str:
    """从用户话里抽出"他说我说过的那句"。抽不到返回空串。"""
    t = str(user_text or "")
    pos = -1
    for trig in _CLAIM_TRIGGERS:
        i = t.find(trig)
        if i >= 0 and (pos < 0 or i < pos):
            pos, hit = i, trig
    if pos < 0:
        return ""
    claim = t[pos + len(hit):]
    # 去掉句尾语气词与标点："还有一张吗？" → "还有一张"
    while claim and claim[-1] in _TAIL:
        claim = claim[:-1]
    return claim.strip()


def looks_like_denial(reply: str) -> bool:
    return any(m in str(reply or "") for m in _DENY_MARKERS)


def overlap_ratio(claim: str, witness: str) -> float:
    """字符 bigram 重合度（以 claim 为分母：claim 的内容有没有出现在见证句里）。"""
    a, b = _bigrams(claim), _bigrams(witness)
    if not a:
        return 0.0
    a = {g for g in a if not (set(g) <= _STOP)}
    if not a:
        return 0.0
    return len(a & b) / len(a)


def find_witness(claim: str, own_msgs, min_overlap: float = 0.3):
    """在"我自己说过的话"里找见证句；找不到返回空串。"""
    best, best_s = "", 0.0
    for m in own_msgs or []:
        s = str(m or "")
        if not s:
            continue
        r = overlap_ratio(claim, s)
        if r > best_s:
            best, best_s = s, r
    return best if best_s >= min_overlap else ""


def verdict(user_text: str, reply: str, own_msgs, min_overlap: float = 0.3) -> dict:
    """判定这条回复是不是在否认自己说过的话。

    返回 {conflict, claim, witness, reason}。conflict=True 时调用方应当改口。
    """
    out = {"conflict": False, "claim": "", "witness": "", "reason": ""}
    claim = extract_claim(user_text)
    if not claim:
        out["reason"] = "用户没在主张我说过话"
        return out
    if not looks_like_denial(reply):
        out["reason"] = "回复没有否认"
        return out
    witness = find_witness(claim, own_msgs, min_overlap=min_overlap)
    if not witness:
        out["reason"] = "我确实没说过"
        return out
    out.update({"conflict": True, "claim": claim, "witness": witness,
                "reason": "用户在主张、我在否认、且我确实说过"})
    return out


def safe_ack(witness: str, limit: int = 60) -> str:
    """确定性的认账句：不否认、不解释，直接把说过的话摆出来。"""
    w = re.sub(r"\s+", " ", str(witness or "")).strip()
    if len(w) > limit:
        w = w[:limit] + "…"
    return f"我前面说的是「{w}」，这点我不改。"


__all__ = ["extract_claim", "looks_like_denial", "overlap_ratio",
           "find_witness", "verdict", "safe_ack"]
