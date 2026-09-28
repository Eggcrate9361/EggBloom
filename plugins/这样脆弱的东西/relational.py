# -*- coding: utf-8 -*-
"""关系分桶：同一个人对不同对象，说话方式完全不同。

之前关系层把所有互动混成一锅，得出"他亲昵又带攻击性"这种没用的结论——
实际上那是**对不同对象**的混合：
  - 对机器人（私聊）：省话、直接使唤、偶尔喵喵汪汪
  - 对群里众人：更展开、更谨慎
  - 对某个人（@ 或引用回复）：可能又骂又爱

分桶的依据（全部来自平台自己的记录，不是猜的）：
  - `scope == private` → 一对一私聊，对象就是机器人
  - `scope == group:*` → 群里公开说话
  - `at`（消息以 @昵称 开头）→ 主动点名某人
  - `reply_to`（引用标记里的对象）→ 在回复某人

每桶都给出：条数、长度、语气词密度、礼貌/攻击比、亲昵称呼、原话样本。
亲昵与攻击指向**同一个人**时，标记为「玩笑式亲密」——
这是关系层里最容易误读的信号，必须显式说出来。
"""
import re
from collections import Counter, defaultdict

_AFFECTION = ("爱你", "宝宝", "亲爱的", "喜欢你", "想你", "么么", "抱抱",
              "老婆", "老公", "宝贝", "喵")
_AGGRO = ("傻子", "混蛋", "滚", "白痴", "废物", "垃圾", "闭嘴", "傻逼",
          "你是傻", "笨", "蠢", "欺负", "霸凌")
_POLITE = ("谢谢", "多谢", "辛苦", "麻烦", "劳烦", "不好意思", "请")
_NICK_CALL = ("牢a", "小a", "宝宝", "喵", "汪", "笨蛋", "傻逼", "老头", "哥", "姐")


def _len_of(text):
    t = re.sub(r"\[[^\]]{1,20}\]", "", text or "")
    return len(re.sub(r"\s+", "", t))


def _bucket_of(m, bot_names=None):
    """这条消息在跟谁说话。返回 (bucket_key, person)。

    ⚠ 群里 @ 到机器人，不是"对某个同学"—— 是**对机器人**。
    之前没认出机器人的名字，把「@占位名片A 爱你哟」错分成了
    "对某人的 25 条亲昵"，实际上那是他对机器人的态度。
    """
    scope = m.get("scope") or ""
    if scope == "private":
        # 一对一私聊：对象就是机器人（这是平台结构决定的，不是猜的）
        return "bot", "对机器人（私聊）"
    if scope.startswith("group"):
        rt = m.get("reply_to") or ""
        at = m.get("at") or ""
        bots = bot_names or set()
        if at and at in bots:
            return "bot_at", "对机器人（群里点名）"
        if rt and rt in bots:
            return "bot_reply", "对机器人（群里回复）"
        if at:
            return f"person:{at}", f"对 {at}（群里点名）"
        if rt:
            return f"person:{rt}", f"对 {rt}（回复）"
        return "group_public", "对群里众人（公开）"
    return "unknown", "对象未知"


def _style(msgs):
    texts = [(m.get("text") or "") for m in msgs]
    n = len(texts) or 1
    aff = sum(1 for t in texts if any(w in t for w in _AFFECTION))
    agg = sum(1 for t in texts if any(w in t for w in _AGGRO))
    pol = sum(1 for t in texts if any(w in t for w in _POLITE))
    lens = [_len_of(t) for t in texts if _len_of(t)]
    tone = sum(t.count(w) for t in texts for w in
               ("啊", "吧", "嘛", "呢", "呀", "哦", "唉", "哎"))
    return {
        "n": len(texts),
        "avg_len": round(sum(lens) / len(lens), 1) if lens else 0,
        "affection": round(aff / n, 3),
        "aggression": round(agg / n, 3),
        "politeness": round(pol / n, 3),
        "tone_density": round(tone / n, 2),
        "aff_msgs": [t for t in texts if any(w in t for w in _AFFECTION)][:4],
        "agg_msgs": [t for t in texts if any(w in t for w in _AGGRO)][:4],
    }


def _nicknames(msgs):
    """他对别人用的称呼。"""
    c = Counter()
    for m in msgs:
        t = m.get("text") or ""
        for w in _NICK_CALL:
            if w in t:
                c[w] += 1
    return [[w, n] for w, n in c.most_common(8) if n >= 1]


def build(msgs, nickmap=None, bot_names=None):
    """完整的关系分桶。msgs 需带 scope / at / reply_to / mid。"""
    nickmap = nickmap or {}
    bots = set(bot_names or ())
    buckets = defaultdict(list)
    for m in msgs:
        key, label = _bucket_of(m, bots)
        m["_bucket"] = key
        m["_bucket_label"] = label
        buckets[key].append(m)

    out = {"buckets": {}, "nicknames": _nicknames(msgs)}
    for key, items in buckets.items():
        if not items:
            continue
        label = items[0].get("_bucket_label", key)
        st = _style(items)
        info = {
            "label": label,
            "n": st["n"],
            "avg_len": st["avg_len"],
            "affection": st["affection"],
            "aggression": st["aggression"],
            "politeness": st["politeness"],
            "tone_density": st["tone_density"],
            "samples": [],
        }
        # 亲昵与攻击指向同一个人 → 玩笑式亲密
        if st["affection"] > 0.05 and st["aggression"] > 0.05:
            info["playful_intimacy"] = True
        # 样本：优先有原文引用的
        seen = set()
        for m in items:
            t = (m.get("text") or "").strip()
            if not t or m["mid"] in seen or len(t) > 60:
                continue
            seen.add(m["mid"])
            info["samples"].append({"mid": m["mid"], "text": t})
            if len(info["samples"]) >= 4:
                break
        out["buckets"][key] = info

    # 对具体人的分桶按消息数排序
    out["buckets"] = dict(sorted(out["buckets"].items(),
                                 key=lambda kv: -kv[1]["n"]))
    return out


def to_features(rel):
    """转成账本能跟踪的关系层结论。"""
    feats = []
    for key, info in (rel.get("buckets") or {}).items():
        if info["n"] < 3:
            continue
        bits = [f"{info['n']} 条", f"平均 {info['avg_len']} 字"]
        if info.get("tone_density"):
            bits.append(f"语气词 {info['tone_density']}/条")
        if info.get("playful_intimacy"):
            bits.append("亲昵与攻击指向同一人（玩笑式亲密）")
        if info.get("affection") and info["affection"] >= 0.1:
            bits.append(f"亲昵表达 {info['affection']:.0%}")
        if info.get("aggression") and info["aggression"] >= 0.1:
            bits.append(f"攻击/调侃 {info['aggression']:.0%}")
        feats.append({
            "label": info["label"],   # label 已自含"对…"，不再拼前缀
            "value": "，".join(bits),
            "confidence": 0.7 if info["n"] >= 10 else 0.5,
            "evidence": [s["mid"] for s in info.get("samples", [])[:3]],
            "source": "heuristic",
        })
    nk = rel.get("nicknames") or []
    if nk:
        feats.append({
            "label": "称呼体系",
            "value": "、".join(f"「{w}」×{n}" for w, n in nk[:8]),
            "confidence": 0.7,
            "evidence": [],
            "source": "heuristic",
        })
    return feats
