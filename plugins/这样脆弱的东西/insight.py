# -*- coding: utf-8 -*-
"""确定性洞察层：不花一次 LLM 调用，就能算出来的那部分"完整重建"。

DESIGN 里 S8「完整重建」要求的东西，有一半根本不需要模型：
置信度热力图、薄弱区、一致性检查、可复现锚点 —— 这些**算得出来**，
而且算出来的比让模型凭空写的更可靠。之前它们一条都没落地。

所以这里把 S8 拆成两半：
  - 可推导的（本模块）：热力图 / 薄弱区 / 矛盾检测 / 锚点
  - 需要模型的（L4 才跑）：条件反射库 / 微反应映射 / 梗库

这样即使语料很薄、等级很低，人格文件里也永远有一节"我有多确定、哪里最虚"，
而不是把不确定藏起来。
"""
import re
from collections import Counter

# 反义对：两边都出现在同话题的结论里，就是一处需要人工确认的矛盾
ANTINOMY = [
    ("喜欢", "讨厌"), ("外向", "内敛"), ("主动", "被动"), ("话痨", "惜字如金"),
    ("积极", "消极"), ("常", "不常"), ("密集", "稀疏"), ("外放", "收敛"),
    ("短句", "长句"), ("会主动", "从不主动"), ("频繁", "很少"), ("高", "低"),
]

# 每层"缺数据"时该去补什么 —— 直接给用户可执行的建议
DATA_NEED = {
    "identity": "包含自我描述的消息（「我是…」「我叫…」「我在…」）",
    "linguistics": "更大的文本量；同一个人的长段落比碎片更有用",
    "behavior": "不同场景下的对话（不止一种话题、不止一个对话对象）",
    "cognition": "更长的对话链（连续多轮的追问与解释，而不是一句一停）",
    "relationship": "持续多天、跨场景的互动记录",
    "temporal": "带准确时间戳的消息（当前时间格式不全）",
    "environmental": "群聊与私聊两种场景的语料（用于对比）",
    "reconstruction": "整体语料量级提升（这一层是前面各层的合成）",
}

SECTION_TITLE = {
    "identity": "身份层", "linguistics": "语言层", "behavior": "行为层",
    "cognition": "认知层", "relationship": "关系层", "temporal": "时间层",
    "environmental": "环境层", "reconstruction": "完整重建",
}


# ── 基础 ────────────────────────────────────────────────

def _cf(e):
    try:
        return float(e.get("confidence", 0))
    except (TypeError, ValueError):
        return 0.0


def _keys(entries_by_section):
    """把 {section: [entry]} 摊平成 [(section, entry)]。"""
    return [(sec, e) for sec, items in (entries_by_section or {}).items()
            for e in (items or [])]


def _bigrams(text):
    t = re.sub(r"[^\u4e00-\u9fff]", "", str(text or ""))
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else set()


# 否定词：出现在反义词前面 3 个字以内就翻转极向（"从不主动" ≈ "被动"）
_NEG = ("不", "没", "无", "未", "别", "非", "拒绝", "从不", "从未")


def _polarity(text, a, b):
    """给一段话在 (a, b) 这对反义词上的极向打分。

    正 = 偏 a，负 = 偏 b，0 = 两边都提到（完整描述，不算矛盾）。
    这一步是为了区分「短句为主，偶有长句」和「一直很短」——
    前者是完整描述，后者才是有极向的断言。
    """
    score = 0
    for word, sign in ((a, 1), (b, -1)):
        for m in re.finditer(re.escape(word), text):
            window = text[max(0, m.start() - 3):m.start()]
            if any(x in window for x in _NEG):
                score -= sign
            else:
                score += sign
    return score


# ── 置信度热力图 ────────────────────────────────────────

def heatmap(entries_by_section, expected=None, llm_sections=None):
    """每一层的证据密度：条目数 / 平均置信度 / 有引用的比例。

    评分 = 0.45×条目覆盖率 + 0.35×平均置信度 + 0.20×引用率
    评分只用来指出"哪一层最虚"，不是给这个人的评分。
    """
    entries_by_section = entries_by_section or {}
    expected = expected or {}
    llm_sections = set(llm_sections or [])
    out = {}
    for sec, need in expected.items():
        items = entries_by_section.get(sec) or []
        active = [e for e in items if e.get("state", "active") == "active"]
        n = len(active)
        cover = min(1.0, n / max(1, need))
        avg = (sum(_cf(e) for e in active) / n) if n else 0.0
        with_ev = sum(1 for e in active if e.get("evidence"))
        evr = (with_ev / n) if n else 0.0
        score = round(0.45 * cover + 0.35 * avg + 0.20 * evr, 3)
        out[sec] = {
            "title": SECTION_TITLE.get(sec, sec),
            "entries": n,
            "expected": need,
            "avg_confidence": round(avg, 3),
            "evidence_ratio": round(evr, 2),
            "score": score,
            "grade": ("充分" if score >= 0.75 else
                      "够用" if score >= 0.5 else
                      "偏薄" if score >= 0.28 else "很薄"),
            "is_llm_layer": sec in llm_sections,
        }
    return out


def weak_areas(heat, limit=4):
    """最虚的几层 + 该补什么数据。"""
    rows = sorted(heat.items(), key=lambda kv: kv[1]["score"])
    out = []
    for sec, h in rows[:limit]:
        if h["score"] >= 0.75:
            continue
        if h["entries"] == 0:
            why = "这一层目前没有任何活跃结论"
        else:
            why = (f"{h['entries']} 条结论（期望 {h['expected']}），"
                   f"平均置信度 {h['avg_confidence']:.0%}，"
                   f"带证据 {h['evidence_ratio']:.0%}")
        out.append({
            "section": sec,
            "title": h["title"],
            "score": h["score"],
            "grade": h["grade"],
            "why": why,
            "collect": DATA_NEED.get(sec, "更多该场景的语料"),
        })
    return out


# ── 一致性检查（确定性） ────────────────────────────────

def contradictions(entries_by_section, limit=12):
    """找互相打架的结论。三种来源：

      1. 条目自己带了反例（counter）—— 已经确认的矛盾
      2. 同一个道理在两个层里说法**相反**，而且谈的是同一个话题
      3. 同一个 label 在不同层给出不同说法（口径不统一）

    两个必须守住的边界（否则全是误报）：
      - 反义判断要排除"自说自话"：一条结论里同时出现「短句为主…偶有长句」
        是**完整描述**，不是矛盾。所以要求反义词各自只出现在一边。
      - 同 label 跨层，若两条说法高度重叠，那是同义复述，不是不一致。
    """
    flat = _keys(entries_by_section)
    out = []
    seen = set()

    def add(kind, a, b, why):
        sig = tuple(sorted([a.get("eid") or a.get("label", ""),
                            b.get("eid") or b.get("label", "")])) + (kind,)
        if sig in seen:
            return False
        seen.add(sig)
        out.append({
            "kind": kind,
            "a": {"section": a.get("_sec"), "title": SECTION_TITLE.get(a.get("_sec"), a.get("_sec")),
                  "label": a.get("label"), "value": a.get("value")},
            "b": {"section": b.get("_sec"), "title": SECTION_TITLE.get(b.get("_sec"), b.get("_sec")),
                  "label": b.get("label"), "value": b.get("value")},
            "why": why,
        })
        return True

    for sec, items in (entries_by_section or {}).items():
        for e in items or []:
            if e.get("counter"):
                pseudo = {"eid": f"{e.get('eid')}::counter",
                          "label": f"{e.get('label')}（反例）", "_sec": sec,
                          "value": "、".join(e.get("counter") or [])}
                add("自带反例", dict(e, _sec=sec), pseudo,
                    "这条结论本身记录了反例，需要人工确认哪边成立")

    # 同 label 跨层：只在两条说法**明显不同**时才算不一致
    by_label = {}
    for sec, e in flat:
        by_label.setdefault(str(e.get("label")), []).append((sec, e))
    for label, group in by_label.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                s1, e1 = group[i]
                s2, e2 = group[j]
                v1, v2 = str(e1.get("value") or ""), str(e2.get("value") or "")
                shared = _bigrams(v1) & _bigrams(v2)
                if shared and len(shared) >= max(2, min(len(_bigrams(v1)),
                                                        len(_bigrams(v2))) // 3):
                    continue          # 说法高度重叠 = 同义复述
                if add("跨层同名不同值", dict(e1, _sec=s1), dict(e2, _sec=s2),
                       f"「{label}」在两层里说法不一致，需要统一口径"):
                    break

    # 反义对 + 话题重叠（要求各占一边，避免"自己跟自己矛盾"）
    for i in range(len(flat)):
        s1, e1 = flat[i]
        v1 = str(e1.get("value") or "")
        for j in range(i + 1, len(flat)):
            s2, e2 = flat[j]
            if e1.get("label") == e2.get("label"):
                continue
            v2 = str(e2.get("value") or "")
            shared = _bigrams(v1) & _bigrams(v2)
            if len(shared) < 2:
                continue
            for a, b in ANTINOMY:
                s_a = _polarity(v1, a, b)
                s_b = _polarity(v2, a, b)
                if s_a * s_b < 0 and abs(s_a) >= 1 and abs(s_b) >= 1:
                    add("反义冲突", dict(e1, _sec=s1), dict(e2, _sec=s2),
                        f"围绕「{'、'.join(list(shared)[:3])}」出现相反断言"
                        f"（{a} {s_a:+d} / {b} {s_b:+d}）")
                    break
            if len(out) >= limit:
                return out
    return out[:limit]


# ── 可复现锚点 ──────────────────────────────────────────

def anchors(entries_by_section, n=6):
    """最能"一眼认出是他"的几条：置信度高 + 被反复确认 + 有证据。"""
    scored = []
    for sec, e in _keys(entries_by_section):
        if e.get("state", "active") != "active":
            continue
        if not e.get("evidence"):
            continue
        score = _cf(e) + 0.05 * min(4, int(e.get("confirmations", 1) or 1))
        scored.append((score, sec, e))
    scored.sort(key=lambda kv: -kv[0])
    out = []
    for score, sec, e in scored[:n]:
        out.append({
            "section": sec,
            "title": SECTION_TITLE.get(sec, sec),
            "label": e.get("label"),
            "value": e.get("value"),
            "confidence": round(_cf(e), 2),
            "confirmations": int(e.get("confirmations", 1) or 1),
            "evidence": list(e.get("evidence") or [])[:4],
        })
    return out


def reflexes(entries_by_section, n=8):
    """把行为/认知层的结论改写成「触发 → 反应」，供人格注入卡用。

    只在结论本身写清了触发条件时才生成，不去替它编一个触发词。
    """
    out = []
    trigger_re = re.compile(r"(当|遇到|被|有人|如果|一|每次|看到|听到|被问)")
    for sec in ("behavior", "cognition", "relationship"):
        for e in (entries_by_section or {}).get(sec) or []:
            if e.get("state", "active") != "active" or _cf(e) < 0.5:
                continue
            value = str(e.get("value") or "")
            if not trigger_re.search(value):
                continue
            out.append({
                "when": value,
                "then": e.get("label") or "照他的习惯反应",
                "confidence": round(_cf(e), 2),
                "section": sec,
            })
            if len(out) >= n:
                return out
    return out


# ── 覆盖报告 ────────────────────────────────────────────

def coverage(planned, done, skipped=None, note=""):
    """这一轮到底覆盖了什么 —— 不把没跑的部分藏起来。

    planned/done 都是 section id 列表（S1..S8）。
    """
    planned, done = list(planned or []), list(done or [])
    missed = [s for s in planned if s not in done]
    return {
        "planned": planned,
        "done": done,
        "missed": missed,
        "ratio": round(len(done) / len(planned), 2) if planned else 1.0,
        "note": note,
        "complete": not missed,
    }


def reflex_note(heat):
    """一句话说清这份文件最该被补的地方。"""
    if not heat:
        return ""
    worst = min(heat.items(), key=lambda kv: kv[1]["score"])
    best = max(heat.items(), key=lambda kv: kv[1]["score"])
    if worst[1]["score"] >= 0.75:
        return "各层证据都比较扎实。"
    return (f"最扎实的是{best[1]['title']}（{best[1]['grade']}），"
            f"最虚的是{worst[1]['title']}（{worst[1]['grade']}）；"
            f"想让它更完整，优先补：{DATA_NEED.get(worst[0], '更多语料')}。")


# ── 名字方向守卫（确定性） ──────────────────────────────

_NAME_IN_QUOTES = re.compile(r"[「『\"'“]([^「」『』\"'“”]{2,20})[」』\"'”]")
_FORWARD_SPEAKER = re.compile(r"([^\s:：]{1,20})\s*[:：]\s")


def _corpus_names(msgs):
    """从语料里抠出两类名字：

      at_names      —— 出现在 @ 之后的名字（他在叫别人）
      speaker_names —— 转发消息里 `名字 : 内容` 形式的发言者标签（别人）

    这两类都不是"他本人的名字"。混淆它们会产出一个看起来很像结论、
    实际完全反了的身份事实 —— 而且会一路传进人格注入卡。
    """
    at_names, speaker_names = set(), set()
    for m in msgs or []:
        if m.get("at"):
            at_names.add(m["at"])
        text = m.get("text") or ""
        if "转发" in text:
            for nm in _FORWARD_SPEAKER.findall(text):
                nm = nm.strip()
                if nm and not nm.isdigit() and len(nm) >= 2:
                    speaker_names.add(nm)
    return at_names, speaker_names


def naming_guard(entries_by_section, msgs, penalty=0.5, bot_names=None):
    """身份层里"方向可能反了"的名字，降权并标注，交给人工定夺。

    三种结局，精度逐级递进：
      - 名字 = **机器人的名字** → 直接解决：「他被叫做X」是错的，
        X 是机器人的名字（他 @ 它是为了叫它）。结论退休。
      - 名字出现在他 @ 的对象位置 / 转发的发言者标签里 → 方向存疑，降权。
      - 都不是 → 不动。
    """
    bots = set(bot_names or ())
    at_names, speaker_names = _corpus_names(msgs)
    if not at_names and not speaker_names:
        return []
    out = []
    for sec, items in (entries_by_section or {}).items():
        if sec != "identity":
            continue
        for e in items or []:
            label = str(e.get("label") or "")
            value = str(e.get("value") or "")
            if not any(k in label for k in ("称呼", "名字", "昵称", "身份", "姓名")):
                continue
            for nm in _NAME_IN_QUOTES.findall(value):
                nm = nm.strip()
                if not nm:
                    continue

                # 终局判定：这个名字是机器人的 → 结论错误，直接退休
                if nm in bots:
                    old = float(e.get("confidence", 0.5))
                    e["confidence"] = round(old * penalty, 2)
                    e["state"] = "retired"
                    e["naming_resolved"] = (
                        f"「{nm}」是**机器人的名字**，不是他的称呼。"
                        f"他 @ 它 / 提它是在跟机器人说话。结论退休。")
                    e["evidence"] = []
                    e.setdefault("history", []).append({
                        "at": "", "event": "retired",
                        "note": e["naming_resolved"],
                    })
                    out.append({"section": sec, "label": label, "name": nm,
                                "where": ["机器人的名字（已确认）"],
                                "from": round(old, 2), "to": e["confidence"],
                                "resolved": True})
                    break

                where = []
                if nm in at_names:
                    where.append("出现在他 @ 的对象位置")
                if nm in speaker_names:
                    where.append("出现在转发消息的发言者标签里")
                if not where:
                    continue
                old = float(e.get("confidence", 0.5))
                e["confidence"] = round(old * penalty, 2)
                e["naming_warn"] = (
                    f"方向存疑：「{nm}」{'；'.join(where)}，"
                    f"很可能是**他称呼的对象**而不是他本人被这样称呼。"
                    f"已从 {old:.0%} 降到 {e['confidence']:.0%}，请人工确认。")
                out.append({"section": sec, "label": label, "name": nm,
                            "where": where, "from": round(old, 2),
                            "to": e["confidence"], "resolved": False})
                break
    return out
