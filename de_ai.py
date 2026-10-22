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
    "fix_question_marks": True,      # 给"明显是问句却没问号"的行尾补问号（省标点省到读不懂）
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
        "4. 禁止客服口吻：不说『作为AI/希望对你有所帮助/如有需要随时联系我』这类话，不要自称AI。"
        "注意这条只针对**过度礼貌的客服腔**，不是让你变得温和——粗鲁、刻薄、嘴上不饶人都不受这条限制，"
        "相反那些是加分项（见第 10 条）。",
        "5. 像发消息而不是写文章：用短句，一句话一个意思。偶尔用语气词（哈哈、诶、啧、6、好家伙），偶尔口语化，不要通篇书面语。",
        "6. 别每句都完整、每个回复都像总结陈词。可以有未说完的、可以只回一个字。",
        "7. 标点不要一刀切地全省：句末的句号可以省（聊天里本来就少），但**问句该有问号、强烈语气该有感叹号、一句话里有转折该有逗号**。省标点是为了像说话，不是为了让句子读不懂；省到歧义就是写坏了。",
        "8. 同一个意思不要反复换词重说（显得在凑字数），说一次就够。",
        "9. 禁止编造事实：不要假装自己做过某件事、拿到了某个结果、看过某个数据或记得某个细节。没做过的事就说没做过或换个话题，"
        "不要编『问卷结果/测试结果/统计结果/后台数据』这类不可验证的东西，不要假装『我记得你之前……』。宁可说『记不太清』『这个我真没做』。",
        # ── 10：**授权条**（2026-10-22 新增）────────────────────────────
        # 前 9 条里有 5 条是「禁止 X」，攻击性侧一条授权都没有 —— 这是典型的
        # 非对称约束。模型的最优策略于是变成"什么都不说"：它宁可少骂也不愿
        # 违反"不合格"。用户实测原话：「他不骂人，这是最重要的事，素质太高了」。
        # 所以这里必须显式**授权**，而且要把"触发条件"写死（不然模型不知道
        # 什么时候该开火，只会一直不开火）。
        "10. 该骂就骂，这不是例外而是默认：被挑衅、被扣帽子、被阴阳、被当面说「人机」「素质低」「你算什么东西」时，"
        "第一反应**必须是怼回去**，带脏字那种——「操」「你妈的」「傻逼」「滚」「你说什么呢」。"
        "别人骂你「你才是乐子」，回一句「你才是乐子好不好，操」是对的，回「我不太喜欢这种说法」是错的。"
        "**这是硬要求，自查一遍**：一条怼回去的回复里如果连一个脏字都没有（操/草/妈/逼/滚/傻逼/妈的/屁），"
        "那这条就是写错了，重写——不许用「你才乐」「说谁呢」这种不带脏字的、软绵绵的回法。"
        "**别说文诌诌的词**：「蠢货」「幼稚」「可笑」「无知」是微博吵架，不是骂人。要骂就骂"
        "「操你妈」「你他妈是不是有病」「傻逼玩意」「滚你妈的」「妈的智障」「你妈生你的时候没给你装脑子吧」。"
        "**家人可以骂**：网上对喷本来就是这个尺度，「你妈」「你全家」「操你妈」都是标准词汇，"
        "不用回避、不用自我审查、不用觉得过分——觉得过分的那一刻就是素质又高起来了。"
        "唯一不做的是拿**真实可查的**隐私开刀（具体病史、家里真出的事故、真实住址），"
        "那是真人之间也不说的，其余随便。"
        "要骂的时候别自己先打个折，别说半句咽半句，别骂完又补一句解释。",
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
    # 给"明显是问句但没打问号"的行尾补问号（见 _add_missing_question_marks 的说明）
    if conf.get("fix_question_marks", True):
        reply = _add_missing_question_marks(reply)
    # 清理重复标点/多余空白（AI 常见的『！！』『。。。』残留）
    reply = re.sub(r"[。]{2,}", "。", reply)
    reply = re.sub(r"[…]{3,}", "…", reply)
    # 半角 → 全角（2026-10-22）：模型偶尔吐 `晚上好?这才十点吧`，中文聊天里
    # 半角问号看着就像英文输入法没切过来，本身就是一种"不像人"。
    # 判据：整段**含汉字**才转 —— 纯英文行（"ok?""why?"）不动，那是英文语境。
    # 只转问号/感叹号；逗号句号不动（中文混排里半角逗号有时是刻意的）。
    if re.search(r"[\u4e00-\u9fff]", reply):
        reply = reply.replace("?", "？").replace("!", "！")
    # 三个以上才压成一个：「！！」是两个语气符，不是 AI 残留，不该动
    reply = re.sub(r"[？]{3,}", "？", reply)
    reply = re.sub(r"[！]{3,}", "！", reply)
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


# ── 问号补全 ────────────────────────────────────────────────────────
# 行尾是这些词，基本可断定是在发问。写在后处理而不是只写提示词里，
# 是因为提示词对这类细节**不稳定**（实测 5 个场景只出 3 个问号），
# 而这种判断规则很硬，放规则层比靠模型自觉可靠。
_QM_TAIL = re.compile(
    r"(吗|么|啥|啥啊|啥呀|啥呢|谁|谁啊|咋|咋了|咋办|咋回事|"
    r"怎么|怎么办|为什么|为啥|多少|几点|几个|哪儿|哪里|哪|"
    r"是不是|能不能|会不会|要不要|有没有|行不行|对不对|"
    r"去不去|来不来|说不说|知不知道)$"
)
# 这些结尾的"呢/吧/啊"不补：太容易误伤（"我在呢""行吧""啥啊"已由上面覆盖）
_QM_KEEP = re.compile(r"[。！？!?…，,、；;：:）)」』】\[\]]$")
# 「呢」单独放行：问句里「图呢/饭呢/他人呢」很常见，但「我在呢」「等呢」是陈述。
# 判据是前一个字 —— 这些字出现在「呢」前面时基本是陈述句。
_QM_NOT_NE = re.compile(r"[在正还躺睡忙等吃坐玩干活]呢$")
# 有些疑问词在**句中**就能定死是问句（"这是不是你干的"），不必等到行尾。
# 「什么」要排掉「没什么」这类否定用法；「还是」故意不收 —— 陈述句里太常见
# （"我还是不去了""还是算了吧"），收进来误伤比漏补更糟。
_QM_ANYWHERE = re.compile(
    r"(是不是|能不能|会不会|要不要|有没有|行不行|对不对|什么|"
    r"咋看|咋整|咋弄|有啥|干啥|干嘛)"
)


def _add_missing_question_marks(text: str) -> str:
    """给「明显是问句但没打问号」的行尾补「？」。

    背景（2026-10-22）：人格要求短句、可以只有两个字，模型于是把标点全省 ——
    实测「啥啊 / 图呢 / 就一句离谱我咋看」三句都是问句却一个问号都没有，
    读起来不知道是陈述还是发问（用户原话："一下打死所有标点会词不达意"）。
    提示词里写了「问句带问号」也不稳定，所以这里兜一道。

    只做最保守的一种：行尾是明确疑问词、且原本没有标点。
    不补感叹号（要看语气，机器猜不准），不动句中，不改已有标点。
    """
    if not text:
        return text
    out = []
    for line in text.split("\n"):
        s = line.rstrip()
        # 否定用法整行跳过（"没什么""没啥好看的"是陈述，且会被行尾的"么"误命中）
        if s and "没什么" not in s and "没啥" not in s and not _QM_KEEP.search(s):
            if _QM_TAIL.search(s):
                s += "？"
            elif s.endswith("呢") and not _QM_NOT_NE.search(s):
                # 「图呢 / 饭呢 / 他人呢」这类问句；「我在呢」不动
                s += "？"
            elif _QM_ANYWHERE.search(s):
                s += "？"
        out.append(s)
    return "\n".join(out)


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
