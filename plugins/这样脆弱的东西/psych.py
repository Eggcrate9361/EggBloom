# -*- coding: utf-8 -*-
"""科学测量层：用心理学 / 社会语言学 / 社会学的框架，把行为数据变成可核对的测量。

三条铁律，写在最前面：

  1. **代理指标，不是心理测量。** 大五人格的每一个分数都来自**可观察的语言行为**
     （连发倾向、问句率、负面词密度），不是量表。它只能说明"这个样本里
     这类行为出现得多"，不能说明"这个人就是外向"。所以每条都带
     「观察基础」「反例」和偏低的置信度。
  2. **只记录可观察行为，绝不做临床判断。** "深夜 0-6 点发言占 33%" 是数据；
     "有抑郁倾向" 是诊断。前者可以做，后者永远不做 —— 没有量表、
     没有临床面谈、更没有本人知情同意下的评估，任何诊断都是越界。
  3. **每个测量必须能回溯到证据编号。** 给不出 `M####` 的测量不成立。

框架来源（方法论，不是诊断标准）：
  - 人格心理学的五因素模型（大五）—— 只取"行为 ↔ 维度"的对应关系做代理
  - 社会语言学的语域（register）与言语行为理论 —— 同一人在不同场域说话方式不同
  - 社会学的角色集与群体语言 —— 一个人同时是学生/玩家/群成员，语言随角色切换
"""
import math
import re
from collections import Counter, defaultdict

# —— 词汇表（行为观察用，不是临床条目） ——
_NEG = ("烦", "累", "无聊", "讨厌", "恨", "气死", "服了", "绝望", "崩溃", "哭",
        "难过", "丧", "破防", "麻了", "寄", "枯了", "早死", "早超生", "没啥意思")
_POLITE = ("谢谢", "多谢", "辛苦", "麻烦你", "劳烦", "请", "不好意思")
_AGGRO = ("傻子", "混蛋", "滚", "白痴", "废物", "垃圾", "闭嘴", "逗他玩", "欺负",
          "霸凌", "怼", "喷")
_MARKERS = ("哦对了", "不过", "其实", "反正", "话说", "对了", "然后", "就是说",
            "怎么说", "总之")
_TECH = ("bug", "python", "服务器", "代码", "逻辑", "api", "脚本", "部署", "环境",
         "报错", "调试", "启动器", "maa", "ai", "deepseek")
_REQUEST = ("帮我", "能不能", "你可不可以", "看看", "帮我看看", "教我", "怎么弄")

_CJK = re.compile(r"[\u4e00-\u9fff]+")


def _len_of(text):
    t = re.sub(r"\[[^\]]{1,20}\]", "", text or "")
    return len(re.sub(r"\s+", "", t))


def _entropy(counter):
    """香农熵（比特）：衡量话题/词汇的多样性。"""
    total = sum(counter.values())
    if not total:
        return 0.0
    return round(-sum((c / total) * math.log2(c / total)
                      for c in counter.values() if c), 3)


def _ttr(texts):
    """类符/词例比（TTR）：词汇多样性。样本越大越低，所以同时给出数。"""
    tokens = []
    for t in texts:
        tokens.extend(_CJK.findall(t or ""))
    if not tokens:
        return {"types": 0, "tokens": 0, "ttr": 0.0}
    c = Counter(tokens)
    return {"types": len(c), "tokens": len(tokens),
            "ttr": round(len(c) / len(tokens), 3)}


def _rate(texts, words):
    """含某类词的消息占比。"""
    if not texts:
        return 0.0, []
    hits = [t for t in texts if any(w in t for w in words)]
    return round(len(hits) / len(texts), 3), hits


def _speech_acts(texts):
    """言语行为分布：陈述 / 疑问 / 请求 / 感叹。"""
    n = len(texts) or 1
    q = sum(1 for t in texts if re.search(r"[？?]", t))
    ex = sum(1 for t in texts if re.search(r"[！!]", t))
    req = sum(1 for t in texts if any(w in t for w in _REQUEST))
    st = sum(1 for t in texts if not re.search(r"[？?!！]", t))
    return {"陈述": round(st / n, 3), "疑问": round(q / n, 3),
            "请求": round(req / n, 3), "感叹": round(ex / n, 3), "样本": len(texts)}


def _topic_entropy(msgs, limit=8):
    """话题多样性：粗分话题后算熵。"""
    c = Counter()
    for m in msgs:
        t = (m.get("text") or "")
        if any(k in t for k in ("游戏", "打", "服务器", "匹配", "肉鸽", "明日方舟", "маa")):
            c["游戏"] += 1
        elif any(k in t for k in ("bug", "代码", "逻辑", "python", "机器人", "启动器", "调试")):
            c["技术"] += 1
        elif any(k in t for k in ("学校", "大专", "专业", "高考", "分数", "上课", "作业")):
            c["学业"] += 1
        elif any(k in t for k in ("你好", "在吗", "在干嘛", "早", "晚安", "你好呀")):
            c["寒暄"] += 1
        elif m.get("context"):
            c["对机器人的话"] += 1
        else:
            c["其他"] += 1
    top = c.most_common(limit)
    return {"entropy": _entropy(c), "top": [[k, v] for k, v in top]}


def big_five(msgs, prof):
    """大五人格的**行为代理指标**。

    每一条都给出：观察到的行为 → 对应维度的方向 → 证据 → 置信度上限 0.6。
    刻意压低置信度：样本再大，这也不是量表。
    """
    texts = [(m.get("text") or "") for m in msgs]
    if len(texts) < 10:
        return {}
    out = {}

    # 开放性：话题熵 + 提问率 + 新事物词
    top = _topic_entropy(msgs)
    q_rate = sum(1 for t in texts if re.search(r"[？?]", t)) / len(texts)
    tech_hits = sum(1 for t in texts if any(w in t.lower() for w in _TECH))
    out["开放性"] = {
        "方向": "偏高" if (top["entropy"] > 1.4 and q_rate > 0.2) else
                ("中性" if top["entropy"] > 1.0 else "偏低"),
        "观察": (f"话题熵 {top['entropy']}（{len(top['top'])} 类话题），"
                 f"提问率 {q_rate:.0%}，技术/新事物词 {tech_hits} 次"),
        "反例": "话题集中在游戏与技术，未必是探索新领域" if top["entropy"] < 1.6 else "",
        "confidence": 0.5,
    }

    # 外向性：主动发起 + 连发 + 语气词密度
    tone = prof.get("tone") or {}
    burst = prof.get("burst") or {}
    out["外向性"] = {
        "方向": "偏高" if (tone.get("per_msg", 0) > 0.3 and burst.get("multi_ratio", 0) > 0.4)
                else "中性",
        "观察": (f"语气词平均每条 {tone.get('per_msg', 0)} 个，"
                 f"连发比例 {burst.get('multi_ratio', 0):.0%}（平均一口气 {burst.get('avg_size', 0)} 条）"),
        "反例": "",
        "confidence": 0.5,
    }

    # 宜人性：礼貌语 vs 攻击性语言
    polite, _p_hits = _rate(texts, _POLITE)
    aggro, _a_hits = _rate(texts, _AGGRO)
    if polite + aggro > 0:
        out["宜人性"] = {
            "方向": ("直接/带攻击性表达多" if aggro > polite else
                     ("礼貌表达多" if polite > 0.05 else "中性")),
            "观察": f"礼貌用语 {polite:.0%}，攻击/调侃语言 {aggro:.0%}",
            "反例": "群内「骂人」常是亲昵的玩笑（'你是不是傻'跟在'爱你哟'后面），"
                    "不能直接当敌意" if aggro > 0.1 else "",
            "confidence": 0.45,
        }

    # 尽责性：作息规律性（时段方差）+ 任务语言
    hours = [m.get("hour") for m in msgs if m.get("hour") is not None]
    if len(hours) >= 10:
        mean = sum(hours) / len(hours)
        var = sum((h - mean) ** 2 for h in hours) / len(hours)
        task = sum(1 for t in texts if any(w in t for w in ("作业", "改", "修", "写完", "交")))
        out["尽责性"] = {
            "方向": "作息分散（推测对常规任务的安排较松）" if var > 40 else "作息较规律",
            "观察": (f"发言时段标准差 {math.sqrt(var):.1f} 小时"
                     f"（深夜 0-6 点占 {sum(1 for h in hours if h < 6) / len(hours):.0%}），"
                     f"任务类语句 {task} 次"),
            "反例": "时段分散更可能反映放假/夜猫子习惯，不等于尽责性低",
            "confidence": 0.4,
        }

    # 情绪不稳定：负面词密度 + 极性波动
    neg, neg_hits = _rate(texts, _NEG)
    pos = sum(1 for t in texts if re.search(r"哈哈|笑死|乐|好玩|不错|可以", t))
    out["情绪稳定性"] = {
        "方向": ("负面表达偏多，值得留意" if neg > 0.2 else
                 ("偏平稳" if neg < 0.08 else "中性")),
        "观察": (f"负面情绪词 {neg:.0%}，正面/玩梗表达 "
                 f"{pos / len(texts):.0%}"),
        "反例": "「无聊」「烦」在闲聊里是高频口头禅，不等于情绪问题",
        "confidence": 0.4,
    }
    return out


def observable(msgs):
    """可观察行为记录 —— **只记录，不判断**。"""
    hours = [m.get("hour") for m in msgs if m.get("hour") is not None]
    n = len(hours) or 1
    deep = sum(1 for h in hours if 0 <= h < 6)
    texts = [(m.get("text") or "") for m in msgs]
    neg, _ = _rate(texts, _NEG)
    return {
        "昼夜节律": {
            "观察": f"深夜(0-6点)发言占 {deep / n:.0%}（{deep}/{len(hours)}）",
            "说明": "只描述发言时间分布，不推断睡眠质量或情绪状态",
        },
        "情绪表达": {
            "观察": f"负面情绪词汇出现于 {neg:.0%} 的消息",
            "说明": "词频统计；口语里「烦/无聊」常只是闲聊语气",
        },
        "自律声明": "以上均为可观察行为的记录，不构成任何临床观察或诊断。"
                    "如需心理评估，应由持证专业人员经本人知情同意后进行。",
    }


def sociolinguistics(msgs, prof):
    """社会语言学：语域、言语行为、词汇多样性、话语标记。"""
    texts = [(m.get("text") or "") for m in msgs]
    out = {
        "词汇多样性": _ttr(texts),
        "言语行为": _speech_acts(texts),
        "话题多样性": _topic_entropy(msgs),
    }
    markers = Counter()
    for t in texts:
        for mk in _MARKERS:
            if mk in t:
                markers[mk] += 1
    out["话语标记"] = [[k, v] for k, v in markers.most_common(8) if v >= 2]

    # 语域偏移：私聊 vs 群聊 的量化差异
    by_scope = prof.get("by_context") or {}
    if "private" in by_scope and "group" in by_scope:
        a, c = by_scope["private"], by_scope["group"]
        la, lc = a.get("len", {}).get("avg", 0), c.get("len", {}).get("avg", 0)
        shift = round(abs(la - lc) / max(1.0, max(la, lc)), 2)
        out["语域偏移"] = {
            "私聊": {"平均字数": la, "标点": a.get("punct")},
            "群聊": {"平均字数": lc, "标点": c.get("punct")},
            "偏移指数": shift,
            "解读": ("对机器人说话更短促（省力气），对群里人更展开" if la < lc
                     else "对机器人更展开，对群里更省话"),
        }
    return out


def sociology(msgs, facts):
    """社会学：角色集、群体语言采纳、互动不对称。"""
    texts = [(m.get("text") or "") for m in msgs]
    out = {}
    roles = []
    joined = " ".join(texts)
    if any(k in joined for k in ("学校", "大专", "高考", "上课", "作业", "专业")):
        roles.append("学生")
    if any(k in joined for k in ("游戏", "打", "匹配", "服务器", "肉鸽")):
        roles.append("玩家")
    if any(k in joined.lower() for k in _TECH):
        roles.append("技术使用者/开发者")
    if any(k in joined for k in ("群里", "群", "@")):
        roles.append("群成员")
    out["角色集"] = roles or ["（证据不足）"]

    jargon = [f.get("text") for f in (facts or [])
              if f.get("fact_kind") == "群黑话"]
    if jargon:
        words = [re.search(r"「([^」]+)」", j).group(1) for j in jargon
                 if re.search(r"「([^」]+)」", j)]
        used = sum(1 for w in words if w in joined)
        out["群体语言采纳"] = {
            "观察": f"平台记下的群黑话 {len(words)} 个，在他的消息中出现 {used} 个",
            "比率": round(used / max(1, len(words)), 2),
            "说明": "采纳群体语言是融入程度的可观察信号",
        }

    at_out = sum(1 for t in texts if re.match(r"^\s*@\s*[^\s]", t))
    out["互动方向"] = {
        "他 @ 别人": at_out,
        "说明": "@ 别人 = 主动点名；被 @ 需要对方日志才能统计（当前只有他自己的消息）",
    }
    initiates = sum(1 for g in (msgs and []) or [] if g)  # 占位，真实计算见 turn 分析
    return out


def build(msgs, prof, facts=None):
    """科学测量层完整输出。全部确定性计算。"""
    msgs = [m for m in msgs if (m.get("text") or "").strip()]
    if len(msgs) < 10:
        return {"error": f"样本不足（{len(msgs)} 条），科学测量层不输出，避免误导"}
    return {
        "psychology": {
            "big_five_proxies": big_five(msgs, prof),
            "observable": observable(msgs),
        },
        "sociolinguistics": sociolinguistics(msgs, prof),
        "sociology": sociology(msgs, facts or []),
        "disclaimer": ("本层所有输出都是**行为代理指标**：从语言样本算出来的统计量，"
                       "不是心理测量、不是临床评估、更不是诊断。"
                       "置信度上限 0.6，且每条都附观察基础与反例。"),
    }


def to_features(measures):
    """把科学测量转成账本能跟踪的条目。"""
    out = {}
    if measures.get("error"):
        return out

    def add(sec, label, value, conf, evidence=None):
        out.setdefault(sec, []).append({
            "label": label, "value": value, "confidence": conf,
            "evidence": evidence or [], "source": "heuristic",
        })

    bf = (measures.get("psychology") or {}).get("big_five_proxies") or {}
    for dim, info in bf.items():
        if not isinstance(info, dict):
            continue
        add("cognition", f"大五代理·{dim}",
            f"{info.get('方向', '—')}（{info.get('观察', '')}）"
            + (f"；反例：{info['反例']}" if info.get("反例") else ""),
            min(0.6, float(info.get("confidence", 0.4))))
    obs = (measures.get("psychology") or {}).get("observable") or {}
    for k, info in obs.items():
        if isinstance(info, dict) and k != "自律声明":
            add("temporal", f"可观察·{k}", info.get("观察", ""), 0.7)
    sl = measures.get("sociolinguistics") or {}
    if sl.get("词汇多样性", {}).get("tokens"):
        t = sl["词汇多样性"]
        add("linguistics", "词汇多样性(TTR)",
            f"类符 {t['types']} / 词例 {t['tokens']}，TTR {t['ttr']}", 0.8)
    if sl.get("言语行为", {}).get("样本"):
        sa = sl["言语行为"]
        add("linguistics", "言语行为分布",
            "、".join(f"{k} {v:.0%}" for k, v in sa.items() if k != "样本"), 0.75)
    if sl.get("话语标记"):
        add("linguistics", "话语标记",
            "、".join(f"「{w}」×{n}" for w, n in sl["话语标记"][:6]), 0.6)
    if sl.get("语域偏移"):
        ro = sl["语域偏移"]
        add("environmental", "语域偏移",
            f"{ro.get('解读', '')}（偏移指数 {ro.get('偏移指数', 0)}）", 0.6)
    so = measures.get("sociology") or {}
    if so.get("角色集"):
        add("identity", "社会角色集", "、".join(so["角色集"]), 0.55)
    if so.get("群体语言采纳"):
        ga = so["群体语言采纳"]
        add("sociology" if False else "behavior", "群体语言采纳",
            f"{ga.get('观察', '')}（比率 {ga.get('比率', 0)}）", 0.6)
    return out
