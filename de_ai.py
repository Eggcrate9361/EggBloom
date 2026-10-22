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
    "fix_time_conflict": True,       # 删掉自己回复里跟当前钟点矛盾的时段词（"大清早"说在下午）
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

    # 删掉自己回复里跟当前钟点矛盾的时段词（"下午一点四十"却说"大清早"）
    if conf.get("fix_time_conflict", True):
        reply = _strip_time_contradictions(reply)

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
#
# ★ 2026-10-22 第二轮（用户反馈「私聊问句没问号」）：
#   第一版只用「整词结尾」判定，实测 52 条常见问句只补上 30 条，同时
#   把 `嘴占着呢` / `我连你啥人都没摸清呢` 这类**陈述**判成问句。
#   **误补比漏补严重** —— 漏补只是少一个标点，误补是把陈述改成疑问。
#   所以这一版分三层：先收误补（黑名单），再按句式补（白名单），
#   最后才是宽泛的疑问词兜底。
_QM_TAIL = re.compile(
    # 「么」要排掉「什么/这么/那么/怎么/多么」——只有真·句末语气词才算问
    r"(吗|(?<![这那什怎多])么|啥|啥啊|啥呀|啥呢|谁|谁啊|咋|咋了|咋办|咋回事|咋样|"
    r"怎么|怎么办|为什么|为啥|多少|几点|几个|哪儿|哪里|哪|"
    r"是不是|能不能|会不会|要不要|有没有|行不行|对不对|"
    r"去不去|来不来|说不说|知不知道)$"
)
# 正反问的简写形态：`吃了没` `行不` `来不` `成不`。
# 中文口语里极常见，第一版完全没覆盖。只看行尾两个字就够：
# 「没/不」结尾且不是否定陈述（"我没有""不是"这类整句太短，另由 _QM_KEEP_SOFT 挡）。
_QM_MEI_BU = re.compile(r"[\u4e00-\u9fff](没|不)$")
# 这几行以「没/不」结尾但意思是陈述，不能补。
_QM_MEI_BU_KEEP = re.compile(r"^(没有|不是|不对|不行|不要|不用|没事|没门|不干)$")

# 这些结尾的"呢/吧/啊"不补：太容易误伤（"我在呢""行吧""啥啊"已由上面覆盖）
_QM_KEEP = re.compile(r"[。！？!?…，,、；;：:）)」』】\[\]]$")
# ── 误补黑名单（第二轮新增，优先级最高）──
# 「呢」结尾的陈述句：`嘴占着呢` `我连你啥人都没摸清呢` `正琢磨中午吃啥呢`
# 判据是**「呢」前面的字**。第一版只排了少数几个动词，漏得太多。
# 现在反过来：只有在「呢」前面是**明确的疑问词**时才当问句（图呢/饭呢/他人呢/多少钱呢），
# 其余一律当陈述。宁可漏，不要错。
_QM_NE_QUESTION = re.compile(
    r"(啥|谁|哪|几|多少|怎么|为什么|为啥|咋|什么|图|饭|人|钱|东西)呢$")
# 「呢」的**叙述用法**：`正琢磨中午吃啥呢` `忙着呢`。
# 这里的「呢」是持续体标记，不是疑问 —— 即便尾部挂着"啥呢"也不能补。
_QM_NE_NARRATIVE = re.compile(
    r"(正|正在|还|才|一直)(琢磨|寻思|合计|想|看|等|忙|弄|搞)")
_QM_NE_ZHE = re.compile(r"着呢$")
# 「吧」结尾一律不动：`走吧` `行吧` `算了吧` 都是陈述或提议，没有疑问语气。
_QM_BA = re.compile(r"吧$")
# 「啊/呀」结尾：只在**含明确疑问词**时才补。`没忙啊` `刚在吃饭啊` 是回答。
_QM_A_YOU = re.compile(r"[啊呀]$")
# ★ 「X什么X」是**否定祈使**，不是疑问：`睡什么睡`（别睡了）`看什么看`
#   `哭什么哭` `急什么急`。这类句子被 `_QM_ANYWHERE` 的「什么」误命中，
#   实测把「睡什么睡，下午一点四十」补成了问号。
#   形态：同一个单字/双字动词被"什么"夹在中间。
_QM_X_SHENME_X = re.compile(
    r"([\u4e00-\u9fff]{1,2})什么\1"
)
# 另有「有什么好X的」是**反问/否定**（"这有什么好笑的"=不值得笑）。
# ★ 但「有啥好玩的」是**真的在问**（"推荐点什么"）—— 不能一起排掉。
#   区别在主语：带「这/那」指代的才是反问。"你这有啥好玩的"里「这」属于
#   "你这"，是对方身上，仍是问句，所以只排「这有什么好」这种紧邻形态。
_QM_YOU_SHENME_HAO = re.compile(
    r"(没什么好|有啥好不|(这|那)\s*有什么好)[\u4e00-\u9fff]{0,4}的$")

# 有些疑问词在**句中**就能定死是问句（"这是不是你干的"），不必等到行尾。
# 「什么」要排掉「没什么」这类否定用法；「还是」故意不收 —— 陈述句里太常见
# （"我还是不去了""还是算了吧"），收进来误伤比漏补更糟。
_QM_ANYWHERE = re.compile(
    r"(是不是|能不能|会不会|要不要|有没有|行不行|对不对|什么|"
    r"咋看|咋整|咋弄|有啥|干啥|干嘛)"
)
# 疑问词在**句首**也算问句（第二轮新增）：`谁说的` `哪去了` `你怎么知道`。
# 但必须排除「我知道」「谁都不去」这类陈述 —— 用「疑问词 + 后接谓语」的形态判。
# 放到最后兜底，前面的黑名单已经先挡过一遍。
# 注意：`多少 / 几点 / 几个` 故意不放在这里 —— 「几点了」这类既可能是问句
# 也可能是"都几点了"的陈述尾巴，宁可漏（既有测试也锁了它不许补）。
_QM_LEADING = re.compile(
    r"^(谁|哪|什么|怎么|为啥|为什么|咋)"
    r"(?!都|也|不|没)"          # 「谁都不去」「哪也没去」是陈述
)
# 「谁说的 / 你怎么知道 / 这是谁干的」这类：疑问词 + 动词
_QM_VERB_SHAPE = re.compile(
    r"(谁(说的|告诉|知道|干|弄|让|叫)|哪(去|来|弄|搞|买|找)|"
    r"怎么(知道|搞|弄|想|回事|办)|为什么|为啥(不|没|要)|"
    r"什么(时候|玩意|东西|意思)|啥时候|多少(钱|个|次|岁|人|斤))"
)


def _add_missing_question_marks(text: str) -> str:
    """给「明显是问句但没打问号」的行尾补「？」。

    背景（2026-10-22 第一轮）：人格要求短句、可以只有两个字，模型于是把标点
    全省 —— 实测「啥啊 / 图呢 / 就一句离谱我咋看」三句都是问句却一个问号都没有，
    读起来不知道是陈述还是发问（用户原话："一下打死所有标点会词不达意"）。
    提示词里写了「问句带问号」也不稳定，所以这里兜一道。

    第二轮（用户反馈「私聊问句没问号」）：第一版只看**行尾整词**，52 条真实
    问句只补上 30 条，同时把 `嘴占着呢` / `我知道呢` 这类陈述误判成问句。
    **误补比漏补严重** —— 漏补只是少个标点，误补把陈述改成疑问，语义都变了。
    所以改成三层：
      ① 误补黑名单（呢/吧/啊 结尾的陈述）优先拦掉；
      ② 句式白名单（正反问、疑问词+动词、句首疑问词）；
      ③ 宽泛疑问词兜底。
    不补感叹号（要看语气，机器猜不准），不动句中，不改已有标点。
    """
    if not text:
        return text
    out = []
    for line in text.split("\n"):
        s = line.rstrip()
        # 已有标点 / 否定用法 / 空行：一律不动
        if (not s or _QM_KEEP.search(s)
                or "没什么" in s or "没啥" in s):
            out.append(s)
            continue
        # 「睡什么睡」「有什么好看的」是否定祈使/陈述，不是疑问
        if _QM_X_SHENME_X.search(s) or _QM_YOU_SHENME_HAO.search(s):
            out.append(s)
            continue

        # ① 误补黑名单 ───────────────────────────────────────────
        _ne_q = False
        if s.endswith("呢"):
            # 叙述用法（正琢磨…呢 / 忙着呢）与「非疑问词的 X呢」都不补
            if (_QM_NE_ZHE.search(s) or _QM_NE_NARRATIVE.search(s)
                    or not _QM_NE_QUESTION.search(s)):
                out.append(s)
                continue
            # 走到这里说明「呢」前面是明确疑问词（图呢 / 饭呢 / 他人呢）→ 确定是问句
            _ne_q = True
        elif _QM_BA.search(s):
            out.append(s)
            continue
        elif _QM_A_YOU.search(s):
            # `没忙啊` `刚在吃饭啊` 是陈述；只有含疑问词才当问句
            if not (_QM_TAIL.search(s) or _QM_ANYWHERE.search(s)):
                out.append(s)
                continue

        # ② 句式白名单 ───────────────────────────────────────────
        if (_ne_q or _QM_TAIL.search(s) or _QM_VERB_SHAPE.search(s)
                or _QM_LEADING.search(s)):
            out.append(s + "？")
            continue
        # 正反问简写：吃了没 / 行不 / 来不（整句就是一个否定词的不算）
        if _QM_MEI_BU.search(s) and not _QM_MEI_BU_KEEP.search(s):
            out.append(s + "？")
            continue
        # ③ 宽泛兜底
        if _QM_ANYWHERE.search(s):
            out.append(s + "？")
            continue
        out.append(s)
    return "\n".join(out)


# ── 自相矛盾检测（2026-10-22 真机反馈）─────────────────────────────
# 现象（QQ 群 829854136，13:39–13:40 连续两轮）：
#   13:37:49  我：睡什么睡 / 现在一点半，你才刚要躺下啊     ← 时间正确
#   13:39:42  我：你他妈才傻逼，**大清早**不干正事跑来找骂   ← 凭空造出"大清早"
#   13:40:14  我：还有，**现在下午一点四十，谁大清早**      ← 又回来打自己脸
# 用户原话：「他自己说大清早，后面似乎是没识别出自己的消息，反驳自己」。
#
# 注意：这**不是**时间注入失败 —— 13:37 那轮它明确说过"现在一点半"，prompt 里
# 也有【精确时间】。问题是模型在骂人的时候顺着"跑来找骂"的语感节奏，把
# "大清早"当成一个**语气词**写出来了（"大清早不干正事"是个固定搭配的骂人口吻），
# 完全没跟时钟比对。然后它自己看了一眼，发现不对，于是**又**在同一个回复里
# 补了一句纠正 —— 于是群里看到的是"我在反驳我自己"。
#
# 已有 `time_ctx.mismatch_note()` 管的是「**对方**说的时段跟时钟对不上」。
# 这里补的是它的镜像：「**自己**说的时段跟时钟对不上」，而且是**事后清理**——
# 检测到矛盾句就直接把那句（或那个词）删掉，不指望模型自觉。
#
# 为什么删而不改：改成正确时段会破坏原句的语义（"大清早不干正事"→"下午不干
# 正事"不通顺），而删掉这个词或整句，剩下的部分仍然成立。**宁短勿错**。
_TIME_PERIOD_WORDS = (
    # (词, 合法小时区间)
    ("大清早", lambda h: 5 <= h <= 9),
    ("一大清早", lambda h: 5 <= h <= 9),
    ("大早上", lambda h: 5 <= h <= 9),
    ("清早", lambda h: 4 <= h <= 9),
    ("凌晨", lambda h: h <= 5),
    ("半夜", lambda h: h <= 5),
    ("深更半夜", lambda h: h <= 5),
    ("早上", lambda h: 4 <= h <= 10),
    ("早晨", lambda h: 4 <= h <= 10),
    ("上午", lambda h: 7 <= h <= 11),
    ("中午", lambda h: 10 <= h <= 13),
    ("大中午", lambda h: 10 <= h <= 13),
    ("下午", lambda h: 11 <= h <= 17),
    ("大下午", lambda h: 11 <= h <= 17),
    ("傍晚", lambda h: 16 <= h <= 19),
    ("晚上", lambda h: h >= 17 or h <= 3),
    ("大晚上", lambda h: h >= 17 or h <= 3),
    ("深夜", lambda h: h >= 21 or h <= 3),
)
#: 「自我纠正」的典型句式：模型发现前文不对、于是补一句纠正自己。
#: 这类句子本身就是"自相矛盾"的信号 —— 哪怕时段词都对，只要出现
#: "还有，现在…谁…"这种结构，就是在群里公开打自己脸。
#: 但**不能只看句式** —— "现在三点了，谁还上班"是正常陈述，不是自我纠正。
#: 所以改成三条件联合（见 `_is_self_correction`），不靠单条正则。
_CLOCK_NUM = r"[0-9０-９一二三四五六七八九十两半]{1,3}"
_CLOCK_RE = re.compile(_CLOCK_NUM + r"\s*[点:：]")
#: 反问/纠正里常见的填充词
_CORRECTIVE_CUE = re.compile(r"(谁|哪|这不|才|还)")
#: 问候语里的时段词不算"时段判断"（"晚上好"在下午也是正常问候语，
#: 被 `mismatch_note` 单独处理，不该在这里被删）
_GREETING_SUFFIX = ("好", "安", "快乐")


def _is_self_correction(s: str, hour: int) -> bool:
    """判断这一句是不是"发现前面说错了、于是补一句纠正自己"。

    真机样本：`还有，现在下午一点四十，谁大清早`
      · 含矛盾时段词「大清早」（13 点不合法）
      · 含钟点数字「一点四十」
      · 含反问线索「谁」
    → 三个条件同时成立，判定为自我纠正，整句删掉。

    反例（必须不判）：`现在三点了，谁还上班`（无矛盾时段词）、
    `大晚上不睡觉`（时段词合法，在 13 点则只删词不删句）。
    """
    has_bad_word = any(w in s and not ok(hour)
                       for w, ok in _TIME_PERIOD_WORDS)
    if not has_bad_word:
        return False
    if not _CLOCK_RE.search(s):
        return False
    return bool(_CORRECTIVE_CUE.search(s))


def _strip_time_contradictions(text: str, ts=None) -> str:
    """删掉**自己**回复里跟当前钟点矛盾的时段词，以及"自我纠正"句。

    典型输入（13:40 的真实回复）：
        「可爱个屁\\n这是骂你，你给我正经挨着\\n还有，现在下午一点四十，谁大清早」
    处理后：
        「可爱个屁\\n这是骂你，你给我正经挨着」

    ★ 只删**矛盾**的时段词，不删对的。13:37 说的"现在一点半"、13:43 说的
      "下午一点四十"都必须原样保留 —— 那是正确的时间感，删了才是伤害。
    """
    if not text:
        return text
    import time as _time
    hour = _time.localtime(ts).tm_hour

    out_lines = []
    for line in text.split("\n"):
        s = line.rstrip()
        if not s:
            out_lines.append(s)
            continue

        # ① 整句是"自我纠正"（"还有，现在下午一点四十，谁大清早"）→ 整句删掉。
        #    这种句子在群里读起来就是我刚骂完又反驳自己，留着比少一句更伤。
        if _is_self_correction(s, hour):
            continue

        # ② 句中的时段词跟钟点矛盾 → 只删词，不删句。
        #    判据用词本身的合法区间；`大清早` 在 13 点就是矛盾，`下午` 在 13 点就合法。
        _hit_bad = False
        for word, ok in _TIME_PERIOD_WORDS:
            if word not in s or ok(hour):
                continue
            # 例外：问候语里的时段词不删（"晚上好""早安"是寒暄，不是时间判断；
            # 跟钟点对不上的问候由 time_ctx.mismatch_note 走"点出来"的路径，
            # 这里直接删会变成「好?这才十点吧」，比原样更怪）
            if any((word + tail) in s for tail in _GREETING_SUFFIX):
                continue
            s = s.replace(word, "")
            _hit_bad = True
        if _hit_bad:
            # 删词后可能残留「，不干正事」这种孤立逗号 → 清理
            s = re.sub(r"[，,、]\s*[，,、]", "，", s)
            s = re.sub(r"^[，,、]\s*", "", s)
            s = re.sub(r"\s+", " ", s).strip()
            if not s:
                continue
        out_lines.append(s)

    # 全部被删空 → 返回原文（宁可发一句带矛盾的，也不发空消息）
    joined = "\n".join(out_lines).strip()
    if not joined:
        return text
    return joined


def _split_long(text: str, max_len: int) -> str:
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
