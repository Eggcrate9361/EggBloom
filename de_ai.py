# -*- coding: utf-8 -*-
"""
de_ai 去AI味模块（EggBloom 本体）
把 GitHub 上社区去AI味 skill 的 33 项 AI 痕迹检测体系，提炼成适合
QQ 群聊/私聊场景的两层能力：
  1. prompt 注入：生成"群聊说话铁律"，before 注入 static prompt，从源头避免 AI 味
  2. 后处理：对模型生成的回复做规则级清理，兜底去掉最明显的 AI 痕迹

设计取舍（对齐社区共识）：
  - 不做同义词替换（容易产生新的 AI 味）
  - 不强行口语化、不加互动结尾
  - 只处理高置信的"结构痕迹"，避免误伤正常表达
"""

import re
from typing import Any, Dict, Optional

# ── 配置默认值 ──
DEFAULT_DE_AI_CONFIG = {
    "enabled": True,                 # 总开关
    "prompt_inject": True,           # 注入"说话铁律"
    "post_process": True,            # 规则级后处理
    "ban_words": True,               # 清理高频 AI 词
    "ban_dashes": True,              # 清理破折号（AI 最明显的痕迹）
    "ban_rule_of_three": True,       # 清理三点式排比
    "ban_filler": True,              # 清理套话/协作路标词
    "ban_sycophancy": True,          # 清理谄媚口吻
    "ban_fabrication": True,         # 清理编造"结果/数据/经历"类不可验证陈述
    "max_sentence_len": 0,           # 超长句拆分阈值（0=不拆）
}

# AI 高频词/套话（中文群聊版，来自 humanizer 33 项体系的本地化）
_HIGH_FREQ_PHRASES = [
    "总的来说", "总而言之", "综上所述", "值得注意的是", "显而易见",
    "值得一提的是", "值得一提的是", "毋庸置疑", "毫无疑问", "事实上",
    "本质上", "某种程度上", "换而言之", "换言之", "换言之",
    "众所周知", "不禁让人", "不禁让人思考", "引发思考", "引人深思",
    "有助于", "有利于", "能够帮助", "可以帮助", "助你", "助力",
    "深入探讨", "深入探索", "深度剖析", "深入浅出", "全方位",
    "全方位地", "多元化", "多层次", "多维度", "赋能", "抓手",
    "闭环", "沉淀", "对齐", "拉通", "共建", "共赢", "生态",
    "痛点", "红利", "赛道", "底层逻辑", "顶层设计",
]

# 谄媚/协作路标词（群聊场景下像客服/机器人的口吻）
_SYCOPHANCY_PHRASES = [
    "作为AI", "作为一个人工智能", "作为一个AI", "本AI", "本智能体",
    "我是一个AI", "我是AI", "我无法做到", "我无法完成", "我帮不了你",
    "希望对你有所帮助", "希望对你有帮助", "很高兴为您服务", "为您服务",
    "好的呢亲", "亲，", "亲爱的用户", "您的问题", "您的需求",
    "如果需要帮助", "如有需要", "随时找我", "随时联系我", "我会尽力",
    "我尽量", "希望可以帮到你", "能帮到你", "请问还有什么可以帮您",
]

# 协作路标词（AI 写文档/教程的痕迹）
_FILLER_PHRASES = [
    "以上就是", "以上是", "我为你", "让我为你", "现在让我们", "让我们来",
    "这里有个", "这里有一些", "接下来", "首先", "其次", "最后",
    "需要注意的是", "需要说明的是", "需要指出的是",
]

# 三点式排比检测：连续 2+ 个 "X、Y、Z" 或 "X，Y，Z"（结尾无实义）
_TRIPLE_PATTERN = re.compile(r"(?:[^，。！？；、\n]{2,8}[、，][^，。！？；、\n]{2,8}[、，][^，。！？；、\n]{2,8})(?=[。！？])")

# 破折号
_DASH_PATTERN = re.compile(r"[—–]|(--)|( ― )|( — )")

# 编造"结果/数据/经历"类不可验证陈述的高置信词。
# 这类词在闲聊里出现，几乎都是 AI 在假装"我做了某件事、拿到了某结果"。
# 后处理里命中即整体删除该词（宁可句子稍不完整，也不要让 bot 把假结果说出来）。
_FABRICATION_PHRASES = [
    "问卷结果", "测试结果", "测评结果", "评测结果", "调查结果", "统计结果",
    "体检结果", "检查结果", "分析结果", "结果显示", "后台显示", "后台数据",
    "我的数据", "系统显示", "数据显示", "记录显示", "记忆里", "我的记忆",
]


def get_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """从 bot_config 中取出 de_ai 子配置，缺省合并默认值。"""
    raw = (cfg or {}).get("de_ai") or {}
    merged = dict(DEFAULT_DE_AI_CONFIG)
    if isinstance(raw, dict):
        merged.update(raw)
    return merged


def de_ai_rule_block(cfg: Optional[Dict[str, Any]] = None) -> str:
    """生成注入 static prompt 的『说话铁律』文本。返回 '' 表示不注入。"""
    conf = get_config(cfg)
    if not conf.get("enabled") or not conf.get("prompt_inject"):
        return ""

    rules = [
        "【说话自然度·像真人】这几条是硬要求，违反任何一条都算不合格：",
        "1. 禁止任何 AI 腔套话：不用『总的来说/综上所述/值得注意的是/显而易见/由此可见』这类开头，不用『总结一下/以上是我的看法』作结尾。",
        "2. 禁止排比腔：不要为了显得全面而列三点式（如『X、Y、Z』连排）。想说什么直接说，一次说一件。",
        "3. 禁止破折号：不要用『——』『—』『–』。需要停顿就用逗号，需要解释就用冒号或句号。",
        "4. 禁止客服口吻：不说『作为AI/希望对你有所帮助/如有需要随时联系我』这类话，不要自称AI、不要过度礼貌。",
        "5. 像发消息而不是写文章：用短句，一句话一个意思。偶尔用语气词（哈哈、诶、啧、6、好家伙），偶尔口语化，不要通篇书面语。",
        "6. 别每句都完整、每个回复都像总结陈词。可以有未说完的、可以只回一个字、可以反问。",
        "7. 同一个意思不要反复换词重说（显得在凑字数），说一次就够。",
        "8. 禁止编造事实：不要假装自己做过某件事、拿到了某个结果、看过某个数据或记得某个细节。没做过的事就说没做过或换个话题，"
        "不要编『问卷结果/测试结果/统计结果/后台数据』这类不可验证的东西，不要假装『我记得你之前……』。宁可说『记不太清』『这个我真没做』。",
    ]
    return "\n".join(rules)


def de_ai_post_process(reply: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """规则级后处理：清理最明显的 AI 痕迹。仅在总开关+后处理开关都开时生效。"""
    if not reply:
        return reply
    conf = get_config(cfg)
    if not conf.get("enabled") or not conf.get("post_process"):
        return reply

    original = reply

    if conf.get("ban_dashes"):
        reply = _DASH_PATTERN.sub("，", reply)

    if conf.get("ban_sycophancy"):
        for phrase in _SYCOPHANCY_PHRASES:
            if phrase in reply:
                reply = reply.replace(phrase, "")

    if conf.get("ban_filler"):
        for phrase in _FILLER_PHRASES:
            if phrase in reply:
                reply = reply.replace(phrase, "")

    if conf.get("ban_words"):
        # AI 高频词只在句首/句号后出现时删除（作为句子的廉价开场），
        # 不挖句中——句中往往是正常用法，删除反而破坏句子
        reply = _strip_leading_phrases(reply, _HIGH_FREQ_PHRASES)

    if conf.get("ban_rule_of_three"):
        reply = _shrink_triples(reply)

    if conf.get("ban_fabrication"):
        for phrase in _FABRICATION_PHRASES:
            if phrase in reply:
                reply = reply.replace(phrase, "")

    # 删除短语后可能残留孤立标点：句首逗号、重复逗号、行首顿号等
    reply = _cleanup_residual(reply)
    # 清理重复标点/多余空白（AI 常见的『！！』『。。。』残留）
    reply = re.sub(r"[！!]{3,}", "！", reply)
    reply = re.sub(r"[。]{2,}", "。", reply)
    reply = re.sub(r"[…]{3,}", "…", reply)
    reply = re.sub(r"\n{3,}", "\n\n", reply)
    reply = reply.strip()

    max_len = int(conf.get("max_sentence_len") or 0)
    if max_len > 0 and len(reply) > max_len * 1.5:
        reply = _split_long(reply, max_len)

    # 若整句被删空，回退原文（避免发空消息）
    if not reply.strip():
        return original
    return reply


def _shrink_triples(text: str) -> str:
    """把『A、B、C』这类三点排比压缩为最前面的两项，去掉尾项。"""
    def _repl(m: re.Match) -> str:
        raw = m.group(0)
        parts = [p for p in re.split(r"[、，]", raw) if p.strip()]
        if len(parts) <= 2:
            return raw
        # 去掉最后一个，保留前两个 + 原分隔符风格
        keep = parts[:-1]
        sep = "、" if "、" in raw else "，"
        return sep.join(keep)
    try:
        return _TRIPLE_PATTERN.sub(_repl, text)
    except Exception:
        return text


def _strip_leading_phrases(text: str, phrases) -> str:
    """删除出现在句子开头（句首或句号/感叹号后）的高频词，不动句中。"""
    for phrase in phrases:
        if not phrase:
            continue
        # 句首位置：行首或紧跟 [。！？；!?;] 之后
        text = re.sub(r"(^|[。！？；!?;\n])\s*" + re.escape(phrase), r"\1", text)
        # 删除后若留下 "，短语"（前面逗号+短语）也清掉
        text = re.sub(r"(^|[。！？；!?;\n])\s*[，,、]\s*" + re.escape(phrase), r"\1", text)
    return text


def _cleanup_residual(text: str) -> str:
    """删除 AI 词后可能残留孤立标点：合并重复逗号、去掉句首/行首的逗号顿号分号。"""
    if not text:
        return text
    # 合并连续标点（破折号→逗号、删词后相邻逗号）
    text = re.sub(r"[，,、]+[，,、]+", "，", text)
    text = re.sub(r"[，,、]+[。]", "。", text)
    text = re.sub(r"[，,、]+[！!]", "！", text)
    text = re.sub(r"[，,、]+[？?]", "？", text)
    # 句首/行首的孤立逗号顿号分号去掉（短语被整段删除后残留）
    text = re.sub(r"(^|[。！？；!?;\n])[，,、；]", r"\1", text)
    # 破折号前一个逗号（"X——Y"→"X，Y" 前本身有逗号时）去重
    text = text.strip()
    return text


def _split_long(text: str, max_len: int) -> str:
    """把超长句按标点拆短，避免一口气甩一大段。"""
    parts = []
    buf = ""
    for ch in text:
        buf += ch
        if len(buf) >= max_len and ch in "。！？；":
            parts.append(buf)
            buf = ""
    if buf:
        parts.append(buf)
    return "\n".join(parts)


def de_ai_diagnose(reply: str) -> Dict[str, Any]:
    """诊断一条回复的 AI 痕迹（供面板/测试使用）。"""
    hits = []
    if _DASH_PATTERN.search(reply):
        hits.append("破折号")
    for p in _SYCOPHANCY_PHRASES:
        if p in reply:
            hits.append(f"客服口吻:{p}")
    for p in _FILLER_PHRASES:
        if p in reply:
            hits.append(f"协作路标:{p}")
    for p in _HIGH_FREQ_PHRASES:
        if p in reply:
            hits.append(f"AI高频词:{p}")
    for p in _FABRICATION_PHRASES:
        if p in reply:
            hits.append(f"编造事实:{p}")
    if _TRIPLE_PATTERN.search(reply):
        hits.append("三点式排比")
    if re.search(r"[！!]{3,}", reply):
        hits.append("重复感叹号")
    return {"hits": hits, "clean": not hits}
