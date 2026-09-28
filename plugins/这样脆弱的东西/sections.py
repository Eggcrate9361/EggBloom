# -*- coding: utf-8 -*-
"""Persona-File v1.0 的 12 个 section 注册表 + 蒸馏提示词。

这个文件是整个插件的「行业标准」部分：每一节要问什么、按什么规矩问、
哪些字段必须存在、从哪个等级开始出现。

三条贯穿全部提示词的铁律（这是产出能不能信的分界线）：
  1. **只依据证据**：每条结论必须挂真实存在的消息编号，原文确实支持它。
  2. **允许没有**：证据不足就返回空，不许用常识补全成一个典型的人。
  3. **记录矛盾**：前后不一致就如实标成矛盾，不许和稀泥抹平。
"""

# ── 等级 → 包含的 section ────────────────────────────────
# 注意 S8：它的内容有相当一部分是**推导**出来的（热力图/薄弱区/一致性/锚点），
# 不需要模型，所以从 L0 起就会渲染；只有"条件反射库/微反应映射"这部分
# 才需要模型，限 L4。这样"我有多确定、哪里最虚"永远不会缺席。
LEVEL_SECTIONS = {
    "L0": ["S0", "S1", "S8", "S9", "S11"],
    "L1": ["S0", "S1", "S2", "S3", "S8", "S9", "S11"],
    "L2": ["S0", "S1", "S2", "S3", "S4", "S5", "S8", "S9", "S11"],
    "L3": ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S11"],
    "L4": ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11"],
}

# 各层的 LLM 取证起始等级（低于此等级只做推导/统计，不花调用）
LLM_MIN_LEVEL = {
    "S1": "L0", "S2": "L1", "S3": "L1", "S4": "L2",
    "S5": "L2", "S6": "L3", "S7": "L3", "S8": "L4",
}

# 取证优先级：预算不够时，从低优先级的开始放弃
PRIORITY = ["S2", "S3", "S1", "S5", "S4", "S6", "S7", "S8"]

# ── 12 个 section 定义 ───────────────────────────────────
#   sid    编号      key     特征字典的键（None = 自动生成，不调 LLM）
#   title  中文标题  level   从哪个等级开始出现
#   labels 审核页的字段建议（供前端填空用）
#   focus  该节的蒸馏取证指引（写进提示词）

SECTIONS = {
    "S0": {
        "key": None, "title": "元数据 Metadata", "icon": "📇", "level": "L0",
        "labels": [], "focus": "",
    },
    "S1": {
        "key": "identity", "title": "身份层 Identity", "icon": "🟢", "level": "L0",
        "labels": ["姓名/称呼", "年龄段", "性别表达", "角色定位", "社会身份",
                   "一句话人设", "身份演化", "多面性"],
        "focus": (
            "【身份层】找出这个人在原文里如何指称自己、把自己放在什么位置上。\n"
            "- 自我称呼、别人怎么叫他（原文里若有就用，没有就写缺证据）\n"
            "- 年龄段 / 学业或职业阶段：原文有明确线索才算，别按语气猜\n"
            "- 角色定位：他把自己描述成什么样的人\n"
            "- 社会身份：学生、打工人、某种爱好者……必须有原文依据\n"
            "- 一句话人设：用他自己的证据浓缩成 50 字以内\n"
            "禁忌 1：不要推断姓名、地域、学校、家庭等原文没写的信息。\n"
            "禁忌 2：**别把「他@的人」当成「他本人」**。出现在 @ 之后的名字、"
            "以及转发消息里「名字 : 内容」形式的发言者标签，都属于**别人**，"
            "不是他的名字。搞反会产出一条完全相反的结论。"
        ),
    },
    "S2": {
        "key": "linguistics", "title": "语言层 Linguistics", "icon": "🔵", "level": "L1",
        "labels": ["高频词", "特征词", "句式偏好", "开头模式", "结尾语气",
                   "标点习惯", "表情习惯", "语气词", "消息切分", "语言切换", "错别字/自造词"],
        "focus": (
            "【语言层】像语言学家一样描述这个人说话的**可复现形式特征**。\n"
            "- 高频词 / 口头禅：给出实际出现的词，不是概括\n"
            "- 特征词：频率不高但一眼认人的词\n"
            "- 句子长度倾向、单句还是连发、有没有把一句话拆成好几条\n"
            "- 开头怎么写、结尾怎么收、语气词清单\n"
            "- 标点习惯：爱用什么、几乎不用什么\n"
            "- 表情 / 颜文字 / 符号的使用密度与风格\n"
            "- 中英混用、错别字、自造词、梗词\n"
            "禁忌：URL、代码片段、英文技术名词不算语言风格，别当高频词。"
        ),
    },
    "S3": {
        "key": "behavior", "title": "行为层 Behavior", "icon": "🟡", "level": "L1",
        "labels": ["话题偏好", "情绪模式", "幽默风格", "冲突处理", "对话节奏",
                   "主动/被动", "追问习惯", "试探行为", "社交模式"],
        "focus": (
            "【行为层】这个人**做了**什么，而不是他是什么样的人。\n"
            "- 反复出现的动作模式：试探、追问、复读、丢链接、要人做任务……\n"
            "- 话题偏好：哪些话题他主动起头，哪些只被动回应\n"
            "- 情绪表达方式：外放还是内敛，正负面比例，什么会让他明显反应\n"
            "- 幽默 / 玩闹的类型，边界在哪\n"
            "- 发起还是回应、插话、追问、沉默的倾向\n"
            "- 消息节奏：连发几条、长句还是短句、回复快慢（有时间戳时）\n"
            "禁忌：把一次性的偶发行为写成习惯。"
        ),
    },
    "S4": {
        "key": "cognition", "title": "认知层 Cognition", "icon": "🔴", "level": "L2",
        "labels": ["价值观", "决策模式", "驱动力", "动机", "雷区/禁区",
                   "知识领域", "思维风格", "信息处理", "认知矛盾"],
        "focus": (
            "【认知层】这个人**为什么**这么做 —— 只写有证据的动机与判断方式。\n"
            "- 驱动力：原文里能看到的真实诉求（无聊、好奇、想被回应、测试对方……）\n"
            "- 判断方式：先试探还是直接问、凭感觉还是讲逻辑\n"
            "- 价值观倾向：从取舍里看，而不是从口号里看\n"
            "- 雷区：什么会让他明显变得不耐烦或收缩\n"
            "- 擅长的领域、明显不懂的领域\n"
            "- 认知矛盾：同一件事前后态度不一致，如实列出来\n"
            "禁忌：不要用「他大概是个理性的人」这种无证据的人格判断。"
        ),
    },
    "S5": {
        "key": "relationship", "title": "关系层 Relationship", "icon": "🟣", "level": "L2",
        "labels": ["依恋风格", "亲密度表达", "称呼策略", "信任建立",
                   "群体角色", "距离感维护", "关系演化"],
        "focus": (
            "【关系层】他**怎么对待对方** —— 这是全篇最容易出戏、也最关键的一层。\n"
            "- 称呼：怎么叫对方、什么时候换称呼\n"
            "- 亲密度表达：亲昵、戏弄、撒娇、保持距离，各自的触发条件\n"
            "- 信任方式：靠什么建立信任（反复验证？直接交付？）\n"
            "- 索取与给予的节奏：要人做事的语气、得到回应后的反应\n"
            "- 群体角色（若是群聊语料）：起哄者、话题发起者、接梗者\n"
            "- 关系演化：原文里前后态度的变化轨迹\n"
            "禁忌：把客套当亲密，把一次称呼当成依恋类型。"
        ),
    },
    "S6": {
        "key": "temporal", "title": "时间层 Temporal", "icon": "🟠", "level": "L3",
        "labels": ["活跃时段", "日常节律", "周内模式", "消息间隔",
                   "连发长度", "作息推断", "时间取向", "时段行为差异"],
        "focus": (
            "【时间层】把消息上的时间戳当数据用。\n"
            "- 活跃时段分布：几点最常出现，深夜还是白天\n"
            "- 同一时段的说话方式是否不同（夜里话多、话少、更情绪化）\n"
            "- 连续几条之间的间隔：抢答型还是慢回型（有时间戳时）\n"
            "- 一次连发几条、单条多少字\n"
            "- 作息推断：只在时间戳支撑下说，没时间戳就明说无法判断\n"
            "- 时间跨度内的变化趋势\n"
            "禁忌：没有时间戳就不要写作息；样本只有几天就别说「长期习惯」。"
        ),
    },
    "S7": {
        "key": "environmental", "title": "环境层 Environmental", "icon": "🌿", "level": "L3",
        "labels": ["场景切换", "设备/平台痕迹", "私聊 vs 群聊", "线上/线下",
                   "文化环境", "社交圈差异", "外部干扰"],
        "focus": (
            "【环境层】行为在**不同场合**下的差异。\n"
            "- 场景切换：同一话题在不同场合的说话方式对比\n"
            "- 平台 / 设备痕迹：从消息形式能看出的客户端、是否转发、是否截图\n"
            "- 私聊与群聊的行为差异（有群聊语料时）\n"
            "- 文化环境痕迹：网络文化、方言、地域梗、时代梗\n"
            "- 社交圈的差异：对不同人是否切换语气\n"
            "- 外部干扰：被打断、同时处理多件事的痕迹\n"
            "禁忌：语料只有单聊就不要编造群聊行为，写「语料不足」即可。"
        ),
    },
    "S8": {
        "key": "reconstruction", "title": "完整重建 Complete Reconstruction",
        "icon": "🧬", "level": "L4",
        "labels": ["条件反射库", "微反应映射", "梗库", "薄弱区",
                   "置信度热力图", "一致性检查", "可复现锚点"],
        "focus": (
            "【完整重建】把上面所有层压成可执行的「触发 → 反应」规则。\n"
            "- 条件反射：什么输入导致什么反应，具体到可复现\n"
            "- 梗库：他反复用的梗、来源、自造词\n"
            "- 薄弱区：哪一维证据最少，列出该补什么数据\n"
            "- 一致性检查：哪些结论互相冲突\n"
            "- 可复现锚点：最能一眼认出「就是他」的 5 条特征\n"
            "克制原则：宁可给出 10 条铁的，也不要给出 50 条软的。"
        ),
    },
    "S9": {
        "key": None, "title": "溯源项链 Provenance", "icon": "🔗", "level": "L0",
        "labels": [], "focus": "",
    },
    "S10": {
        "key": None, "title": "人格演化日志 Evolution Log", "icon": "🕰", "level": "L4",
        "labels": [], "focus": "",
    },
    "S11": {
        "key": None, "title": "蒸馏审计 Distillation Audit", "icon": "📋", "level": "L0",
        "labels": [], "focus": "",
    },
}

SECTION_ORDER = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11"]

# 需要 LLM 蒸馏的 section（其余自动生成）
LLM_SECTIONS = [s for s in SECTION_ORDER if SECTIONS[s]["key"]]

# 特征字典键 → 中文标题（UI 与渲染共用）
KEY_TITLES = {SECTIONS[s]["key"]: SECTIONS[s]["title"] for s in LLM_SECTIONS}

# 证据强度 → 置信度的统一口径（写进提示词，让模型有把尺子）
CONFIDENCE_RULE = (
    "confidence 按证据强度给：3 条以上独立证据且无反例 ≥0.90；"
    "2-3 条证据 0.70-0.89；只有 1 条 0.50-0.69；"
    "弱信号 0.30-0.49；纯猜测 ≤0.29（不要输出这种）。"
)


def for_level(level):
    """某等级应包含的 section 列表。"""
    return list(LEVEL_SECTIONS.get(level, LEVEL_SECTIONS["L0"]))


def _rank(level):
    return SECTION_LEVEL_RANK.get(level, 0)


SECTION_LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}


def llm_sections_for_level(level):
    """某等级里真正需要调 LLM 的 section（S8 只在 L4 才算 LLM 层）。"""
    out = []
    for sid in for_level(level):
        if not SECTIONS[sid]["key"]:
            continue
        if _rank(level) < _rank(LLM_MIN_LEVEL.get(sid, "L0")):
            continue
        out.append(sid)
    return out


def derived_sections_for_level(level):
    """有内容但不需要模型（靠推导/统计）的 section。"""
    return [s for s in for_level(level)
            if SECTIONS[s]["key"] and s not in llm_sections_for_level(level)]


def key_of(sid):
    return SECTIONS[sid]["key"]


def label_for(sid):
    s = SECTIONS[sid]
    return f"{s['icon']} {s['title']}"


# ── 共享系统提示词 ───────────────────────────────────────

_ANALYST = (
    "你是人格分析师，从真实聊天语料里提炼关于这个人的**可验证**结论。\n\n"
    "铁律（违反任何一条，这条结论就作废）：\n"
    "1. 只依据给定语料。每条结论必须挂真实存在的编号（形如 M0012），"
    "且原文确实支持它。不许引用不存在的编号。\n"
    "2. 没证据就不要写。语料薄是常态，宁缺毋滥 —— "
    "一条铁的证据胜过十条听起来合理的推断；也不要用常识补出一个「典型的人」。\n"
    "3. 前后不一致就如实记成「矛盾：X vs Y」，不要抹平。\n\n"
    "写法：value 是可核对的陈述句（不要形容词堆砌、不要「可能」「似乎」）；"
    "label 是 2-8 字的维度名。证据不足时 value 直接写「语料不足」。\n"
    "**不要展开推理过程**，直接按格式输出 JSON。"
)

MAP_FORMAT = (
    '{"sections": {"<层名>": {"entries": [{"label": "维度名", "value": "一句话陈述", '
    '"confidence": 0.0, "evidence": ["M0001", "M0007"], "counter": ["M0004"]}]}}}'
)

REDUCE_FORMAT = (
    '{"sections": {"<层名>": {"entries": [{"label": "维度名", "value": "一句话陈述", '
    '"confidence": 0.0, "evidence": ["M0001"], "counter": []}]}}}'
)

# 输出里用的层名（就是特征字典的键）
def _name_of(sid):
    return SECTIONS[sid]["key"] or sid


def split_batches(sids, per_call=2):
    """把要蒸馏的层切成若干批 —— 受上游 RPM 限制，一次问一层太奢侈。"""
    per_call = max(1, int(per_call or 1))
    return [sids[i:i + per_call] for i in range(0, len(sids), per_call)]


def plan_calls(sids, chunks=1, cfg=None, resume=None):
    """算这一轮要花多少次调用，并在预算不够时**按优先级降级**。

    这是"不留简化版"的关键一环：以前的写法是语料超窗口就静默截断、
    调用超预算就中途停下，用户完全看不到自己少了什么。现在改成：

      1. 先把 resume（上一轮没跑完的层）排到最前面
      2. 算出 map（每批 × 每块）+ reduce + critic 的总调用数
      3. 超预算就按顺序降级：合并批 → 砍 critic → 砍 reduce → 放弃低优先级层
      4. **把每一次降级都记下来**，让产出能如实报告"这轮覆盖了什么、少了什么"

    返回 {"batches", "chunks", "map_calls", "reduce", "critic", "estimated",
          "uncovered", "degraded", "budget"}
    """
    cfg = cfg or {}
    budget = max(1, int(cfg.get("max_llm_calls", 24) or 24))
    chunks = max(1, int(chunks or 1))
    resume = [s for s in (resume or []) if s in sids]
    ordered = resume + [s for s in sids if s not in resume]

    per_call = max(1, int(cfg.get("sections_per_call", 1) or 1))
    want_reduce = True
    want_critic = bool(cfg.get("critic_enabled", True))
    degraded = []

    def cost(per_call_now, do_reduce, do_critic, ids):
        batches = split_batches(ids, per_call_now)
        # 多块语料时，每批还要额外一次调用把各块观察合成
        per_batch = chunks + (1 if chunks > 1 else 0)
        need_global_reduce = do_reduce and (len(batches) > 1 or chunks > 1)
        return (len(batches) * per_batch
                + (1 if need_global_reduce else 0)
                + (1 if do_critic else 0)), batches

    total, batches = cost(per_call, want_reduce, want_critic, ordered)

    if total > budget:                      # 降级 1：合并批（几层挤一次调用）
        for pc in range(per_call + 1, len(ordered) + 1):
            t, b = cost(pc, want_reduce, want_critic, ordered)
            if t <= budget:
                degraded.append(f"合并取证批次：每批 {pc} 层（原 {per_call} 层）")
                per_call, total, batches = pc, t, b
                break

    if total > budget and want_critic:      # 降级 2：砍终检
        want_critic = False
        degraded.append("跳过 L4 终检对账（调用预算不足）")
        total, batches = cost(per_call, want_reduce, want_critic, ordered)

    if total > budget and want_reduce:      # 降级 3：砍定稿（账本会做确定性合并）
        want_reduce = False
        degraded.append("跳过 L3 定稿（由账本完成确定性合并）")
        total, batches = cost(per_call, want_reduce, want_critic, ordered)

    # 降级 4：合并语料块。
    # 这一步比"砍层"好得多 —— 块只是切片方式，合并之后语料**一点不丢**，
    # 只是每次调用要读得更长。先合并块，实在不行才放弃层。
    if total > budget:
        fixed = (1 if want_reduce else 0) + (1 if want_critic else 0)
        room = max(1, (budget - fixed) // max(1, len(batches)))
        if room < chunks:
            degraded.append(f"合并语料块：{chunks} 块 → {room} 块"
                            f"（每块更长，语料不丢）")
            chunks = room
            total, batches = cost(per_call, want_reduce, want_critic, ordered)
            # 合并块之后每批只剩一次调用，全局合并也就不需要了
            if total > budget and want_reduce and len(batches) == 1:
                want_reduce = False
                degraded.append("跳过 L3 定稿（已合并为单批）")
                total, batches = cost(per_call, want_reduce, want_critic, ordered)

    uncovered = []
    while total > budget and ordered:       # 降级 5：最后才放弃最低优先级的层
        drop = None
        for sid in reversed(PRIORITY):
            if sid in ordered:
                drop = sid
                break
        if drop is None:
            drop = ordered[-1]
        ordered.remove(drop)
        uncovered.append(drop)
        degraded.append(f"本轮未覆盖 {SECTIONS[drop]['title']}（预算不足，下轮优先补）")
        total, batches = cost(per_call, want_reduce, want_critic, ordered)

    return {
        "batches": batches,
        "chunks": chunks,
        "per_call": per_call,
        "map_calls": len(batches) * (chunks + (1 if chunks > 1 else 0)),
        "reduce": want_reduce and len(ordered) > 0,
        "critic": want_critic and len(ordered) > 0,
        "estimated": total,
        "uncovered": uncovered,
        "degraded": degraded,
        "budget": budget,
    }


def existing_block(entries_by_section, per_section=10):
    """把账本里已有的结论列出来，供模型做"增补"而不是"重新发明"。

    这是连续蒸馏和一次性蒸馏在提示词层面的唯一区别：
    一次性是空手起步，连续是接着上一轮往下写。
    """
    rows = []
    for section, items in (entries_by_section or {}).items():
        if not items:
            continue
        rows.append(f"### {section}")
        for e in items[:per_section]:
            state = e.get("state", "active")
            mark = {"active": "活跃", "suspect": "存疑",
                    "dormant": "沉眠", "retired": "已推翻"}.get(state, state)
            rows.append(
                f"- 【{mark}】{e.get('label')}：{e.get('value')} "
                f"（置信度 {e.get('confidence')}，已被确认 "
                f"{e.get('confirmations', 1)} 次，证据 {','.join((e.get('evidence') or [])[:4]) or '无'}）")
    return "\n".join(rows) if rows else "（账本还是空的，这是第一轮）"


_EXISTING_RULES = (
    "【这一轮的特殊之处：你不是从零开始】\n"
    "下面给出的是**已有的结论清单**（账本）。你要做的是拿新语料去**增补/修正**它，"
    "而不是重新发明一个人：\n"
    "1. 新语料**再次支持**某条已有结论 → 照原 label 原样输出那条结论"
    "（价值在于：它会被记账为「再次确认」，置信度会上升）。\n"
    "2. 新语料**修正**了某条已有结论 → 输出同样的 label，但 value 写新的说法。\n"
    "3. 新语料**反驳**了某条已有结论 → 输出同样的 label，并把反例编号放进 counter。\n"
    "4. 新语料里有已有清单**没有**的东西 → 用新 label 输出。\n"
    "5. **不要**因为新语料里没提到某条旧结论就把它删掉 —— 你没提它，对账本来说"
    "只是「这一轮没被提及」，它会自己降级，不需要你动手。\n"
    "6. 时间跨度上是「接着往下看」：新语料在时间上晚于旧结论，注意区分"
    "「一直如此」和「最近才变成这样」。\n"
    "7. 「高频词」「标点习惯」「句式偏好」「语气词」「表情习惯」「说话节奏」这类"
    "**统计型维度**由系统用全量语料重算，不要拿新增片段里的窄结论去改写它们。"
    "新语料没有改变整体判断时，原样返回那条结论，或者干脆不写这一条。\n"
)


def map_prompt(sids, target, corpus, digest_text, index_size, existing=None,
               chunk_hint=None):
    """取证提示词（第一阶段：从语料里捞观察）。可一次处理多层。

    existing   不为空 → 增量模式，从"从零提取"变成"接着往下写"
    chunk_hint (第几块, 共几块) → 大语料分块投喂时，说清这一块的边界
    """
    blocks = "\n\n".join(
        f"【第 {i + 1} 层】{SECTIONS[sid]['title']}（输出时的层名必须写 "
        f"「{_name_of(sid)}」）\n{SECTIONS[sid]['focus']}"
        for i, sid in enumerate(sids)
    )
    names = "、".join(_name_of(s) for s in sids)
    existing_part = ""
    if existing:
        existing_part = ("\n" + _EXISTING_RULES
                         + "\n【已有结论清单（账本）】\n" + existing + "\n")
    chunk_part = ""
    if chunk_hint and chunk_hint[1] > 1:
        chunk_part = (
            f"\n【语料分块说明】这是第 {chunk_hint[0]} / {chunk_hint[1]} 块语料。"
            "只就**这一块**里出现的证据下结论：这一块没提到的，不要否定，"
            "也不要凭印象补全 —— 其他块会由别的分析覆盖。\n")
    corpus_title = "【本轮新增语料】" if existing else "【语料】"
    return (
        f"【本次任务】依次处理以下 {len(sids)} 层，逐层取证：{names}\n\n"
        f"{blocks}\n\n"
        f"【蒸馏对象】{target}\n"
        f"【语料规模】{digest_text}\n"
        f"【可用编号范围】M0001 ~ M{index_size:04d}，引用超出范围的编号视为编造。\n"
        f"【语料特征说明】每条后面的方括号是标注："
        f"「共N次」表示这句在原始记录里出现 N 次；"
        f"「其中重复发M次」表示被同一个人反复发出 M 次（真实行为特征，可直接引用）；"
        f"「引用回复」表示是引用别人的话后作的回复；「含链接」表示消息带 URL；"
        f"「平台事实」表示这条不是他说的话，而是系统记录的结构化数据"
        f"（好感度、群黑话、群情绪等），可以引用但别当成他的发言。\n"
        f"⚠ 关键语义：「他@了「X」」表示**他在叫别人 X**，"
        f"绝不能反过来当成「他被叫做 X」。群聊里 @ 的方向弄反会直接产出"
        f"「他的名字是 X」这类错误结论。\n"
        f"{existing_part}{chunk_part}\n"
        f"【置信度标准】\n{CONFIDENCE_RULE}\n\n"
        f"【输出格式】只输出一个 JSON 对象，sections 下必须包含全部 "
        f"{len(sids)} 个层名：\n{MAP_FORMAT}\n"
        "某一层确实找不到有证据支撑的观察时，就把它写成 "
        '{"entries": []}，不要为了凑数编造。\n\n'
        f"{corpus_title}\n{corpus}"
    )


def reduce_prompt(sids, target, observations, corpus, digest_text, baseline=None,
                  stage="定稿"):
    """合并提示词（第二阶段：去重、核证据、重估置信度）。

    两个使用场合共用这一份：单批跨块合并（stage=块间合并）
    与全局定稿（stage=定稿）。刻意不再重复各层的取证指引 ——
    观察本身已经带着 label，重复贴一遍只会白白吃掉紧张的 token 额度。
    """
    names = "、".join(f"{_name_of(s)}（{SECTIONS[s]['title']}）" for s in sids)
    obs_blocks = []
    for sid in sids:
        items = observations.get(_name_of(sid)) or []
        if not items:
            obs_blocks.append(f"### {_name_of(sid)}：上一阶段没有捞到任何观察")
            continue
        lines = []
        for o in items:
            ev = ",".join(o.get("evidence") or []) or "无"
            cross = ("; 反例 " + ",".join(o["counter"])) if o.get("counter") else ""
            lines.append(f"- {o.get('label')}：{o.get('value')} "
                         f"(证据 {ev}{cross}; 原置信度 {o.get('confidence')})")
        obs_blocks.append(f"### {_name_of(sid)}\n" + "\n".join(lines))

    base_block = ""
    if baseline:
        rows = [f"- {k}：{v}" for k, v in baseline.items() if v]
        if rows:
            base_block = ("\n【统计基线（已算出的硬事实，可直接引用，不要重复描述）】\n"
                          + "\n".join(rows) + "\n")

    return (
        f"【本次任务】（{stage}）把下面的**原始观察**精炼成定稿条目，"
        f"涉及 {len(sids)} 层：{names}\n\n"
        f"【蒸馏对象】{target}　【语料规模】{digest_text}\n"
        f"{base_block}\n"
        f"【必须做的事】\n"
        f"1. 合并重复或同义的观察，一条只留最强版本。\n"
        f"2. 剔除证据不成立的观察：声称有证据但原文并不支持的，直接删。\n"
        f"3. 直接矛盾的观察，合并成一条「矛盾：X vs Y」，反例编号放进 counter。\n"
        f"4. 重估 confidence：\n{CONFIDENCE_RULE}\n"
        f"5. 引用的编号必须真实存在于语料中，逐条核对后再写。\n"
        f"6. 保留原始 label 的措辞，不要改写成百科式条目。\n"
        f"7. 每层最多留 8 条。宁可少而硬，不要多而软。\n"
        f"8. 不要展开推理过程，直接输出 JSON。\n\n"
        f"【输出格式】只输出一个 JSON 对象，sections 下必须包含全部 "
        f"{len(sids)} 个层名：\n{REDUCE_FORMAT}\n\n"
        f"【原始观察】\n" + "\n\n".join(obs_blocks) + "\n\n"
        f"【语料（用于核对证据）】\n{corpus}"
    )


def critic_prompt(target, entries_text, corpus, digest_text):
    """终检提示词：把整份人格文件送回原文对账，抓伪造与过度推断。"""
    return (
        "【本次任务】你是终审。下面是刚刚蒸馏出来的人格结论清单，"
        "请你**逐条**送回原文对账，判它站不站得住。\n\n"
        f"【蒸馏对象】{target}\n【语料规模】{digest_text}\n\n"
        "【判定标准】\n"
        "  supported   = 引用的原文确实支持这条结论\n"
        "  weak        = 方向没错，但证据偏薄或过度概括\n"
        "  unsupported = 原文不支持，或引用的是不存在的编号（编造）\n\n"
        "【必须做的事】\n"
        "1. 逐条核对 evidence 里的编号是否真实存在、原文是否真的支持。\n"
        "2. 抓「听起来很合理但原文没有」的类型化推断（例如从语气猜年龄、猜性别、猜职业）。\n"
        "3. 抓互相打架的条目。\n"
        "4. 抓被遗漏的明显特征——原文里一眼可见、但清单里没有的。\n\n"
        "【输出格式】只输出一个 JSON 对象：\n"
        '{"verdicts": [{"label": "维度名", "verdict": "supported|weak|unsupported", '
        '"reason": "一句话理由"}], '
        '"missing": [{"section": "层名", "label": "维度名", "value": "漏掉的特征", '
        '"confidence": 0.0, "evidence": ["M0011"]}]}\n'
        "missing[].section 只能取这些值之一："
        "identity / linguistics / behavior / cognition / "
        "relationship / temporal / environmental / reconstruction\n\n"
        f"【待审结论清单】\n{entries_text}\n\n"
        f"【原文】\n{corpus}"
    )


def analyst_system(sid=None):
    """分析师系统提示词。传 sid 时附加该层的一句话定位。"""
    if sid:
        spec = SECTIONS[sid]
        return f"{_ANALYST}\n\n【你的固定岗位】{spec['icon']} {spec['title']}（{sid}）"
    return _ANALYST
