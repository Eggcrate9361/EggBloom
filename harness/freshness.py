# -*- coding: utf-8 -*-
"""harness/freshness.py —— 时效性护栏：**不让模型拿过时信息当事实说。**

问题（为什么这是独立一层，而不是"提示词里加一句"）
    LLM 的训练数据有截止日期，但它说话的语气**不会区分**：
      "今天几号我看看"（实时）和 "现在的最新款是 iPhone 15"（半年前的记忆）
    用的是同一种笃定口吻。在群聊里这是最致命的错误源 ——
    群里的话题几乎全是**实时**的（今天的比赛、刚发生的事、某人几小时前说过的话），
    模型一旦用旧印象去接，就会答得又顺又错，而且**没有任何外部信号能让人察觉**。

    所以这件事不能靠"提醒模型注意时效"，必须做成**结构性护栏**：
    让"不确定/可能过时"变成模型看得见的、必须处理的状态。

三道护栏
    1. `knowledge_cutoff_gap()` —— **显式告诉模型"你的知识有个终点，且已过去 N 天"**。
       大多数部署不会说这件事，模型于是默认自己"知道现在发生什么"。
       把差距量化后（"距你的知识截止已 428 天"），模型对"最新/当前/现在"
       这类问题会自然转向工具，而不是硬答。
    2. `classify_query()` —— 判断用户这句话是否**时效敏感**。
       敏感 → 强制附上"必须联网核实"的指令，并在有工具时优先给搜索工具。
       不敏感 → 一点附加成本都不加（闲聊零开销）。
    3. `stale_guard()` —— 工具结果**过期即失效**：
       带时间戳的缓存（搜索/天气/行情）超过 TTL 后不再当作事实可用，
       而是明确标注"这是 N 分钟前的结果"，让模型自己决定是否重取。

设计纪律（与 harness 其余部分一致）
    · `truncated` / `error` / `stale` 三个字段**永远分离**：
      "因为太长截断"、"因为出错没拿到"、"因为过期不敢用"是三件不同的事。
    · 本模块**不联网、不发请求**，只做判断与文本生成 —— 零成本、零副作用、可单测。
    · 任何一步失败都静默降级为空串（绝不阻断回复）。
"""
import datetime
import re
import time

# ── 1. 知识截止 ────────────────────────────────────────────────────
# 说明：这里给的是**部署方声明的模型知识截止**，不是"我记得的"。
# 可通过 bot_config.json 的 knowledge_cutoff 覆盖（格式 YYYY-MM-DD）。
DEFAULT_CUTOFF = "2024-07-01"
_CUTOFF_CACHE = {"text": None, "key": None}


def _parse_date(s):
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(str(s).strip(), fmt).date()
        except Exception:
            continue
    return None


def days_since_cutoff(cutoff=None, ts=None):
    """距知识截止已过去多少天（无法解析返回 None）。"""
    d = _parse_date(cutoff or DEFAULT_CUTOFF)
    if d is None:
        return None
    now = datetime.datetime.fromtimestamp(ts if ts is not None else time.time()).date()
    return max(0, (now - d).days)


def knowledge_cutoff_gap(cutoff=None, ts=None):
    """生成"知识截止 + 时间差距"的提示块。

    **这是本模块最重要的一句话**：把模型对自身时效性的隐性高估，变成显式事实。
    返回 '' 表示无法计算（降级）。
    """
    d = _parse_date(cutoff or DEFAULT_CUTOFF)
    if d is None:
        return ""
    gap = days_since_cutoff(cutoff, ts)
    if gap is None:
        return ""
    if gap < 30:
        level = "你的知识与当前时间基本同步，但仍需注意实时事件。"
    elif gap < 200:
        level = "你的知识已有一段时间，涉及'最新/当前'的问题应核实。"
    elif gap < 500:
        level = "你的知识明显滞后，涉及'最新/当前/现在/今年'的问题**不要凭记忆回答**。"
    else:
        level = ("你的知识已严重滞后，几乎所有'当前状态'类问题都应核实，"
                 "凭记忆回答大概率已经过时。")
    return (f"【时效性】你的训练知识截止于 {d.isoformat()}，"
            f"距今已 {gap} 天。{level}\n"
            f"· 涉及价格、版本、排名、赛事、新闻、人物职位、天气等**会变的信息**，"
            f"必须先核实再回答；无法核实时要**明说**'这可能不是最新的'。\n"
            f"· 不要用'根据我的了解/据我所知'来掩盖不确定性 —— "
            f"如果没查，就说不确定。")


# ── 2. 时效敏感度判定 ──────────────────────────────────────────────
# 强时效信号：命中直接判定为"必须核实"
_STRONG_TIME_RE = re.compile(
    r'(最新|目前|现在|当前|如今|实时|今天|今日|昨天|昨晚|前天|明天|本周|这周|'
    r'本月|这个月|今年|刚刚|刚才|最近|近期|新出|新发布|上线了|更新了|'
    r'行情|股价|价格|多少钱|涨价|降价|比分|赛程|排名|榜单|票房|天气|气温|'
    r'预报|新闻|头条|发生了什么|怎么回事|怎么样了|进展|进展如何|'
    r'latest|current|now|today|yesterday|tomorrow|this (week|month|year)|'
    r'recent|price|score|weather|news|update)',
    re.IGNORECASE)

# 弱时效信号：配合"求事实"语气才算（避免"我现在就想睡"被误判）
_WEAK_TIME_RE = re.compile(
    r'(是(?:什么|谁|哪|多少)|有没有|出了吗|上市|发布|支持|可以吗|能不能|值得|'
    r'推荐|选哪个|哪个好|怎么样|好不好)',
    re.IGNORECASE)

# 明确与时效无关（情绪/闲聊/创作）→ 强信号也降级
_TIMELESS_RE = re.compile(
    r'(你(?:在|在干嘛|干嘛呢)|心情|难受|开心|无聊|晚安|早安|'
    r'写一(?:首|段|篇)|编一(?:个|段)|假设|假如|如果.*会|'
    r'翻译|解释一下|什么是|定义|讲个笑话|唱(?:首|个))',
    re.IGNORECASE)


def classify_query(text):
    """判断一句话是否**时效敏感**。返回 (敏感: bool, 原因: str)。

    三档：
      strong  → 强时效词，直接判定需要核实
      weak    → 弱时效词 + 求证语气，也需要核实
      none    → 闲聊/情绪/创作/纯定义题，零附加成本
    """
    t = (text or "").strip()
    if not t:
        return False, "空消息"
    if _TIMELESS_RE.search(t):
        return False, "非时效场景（情绪/创作/定义）"
    if _STRONG_TIME_RE.search(t):
        m = _STRONG_TIME_RE.search(t)
        return True, f"强时效信号（{m.group(0)}）"
    if _WEAK_TIME_RE.search(t):
        return True, "求证语气（可能需要最新信息）"
    return False, "无时效信号"


def freshness_directive(text, *, has_search=True):
    """时效敏感时给出的**行为指令**（拼到本轮尾部，不污染长前缀）。

    设计：指令放在**当前用户消息附近**（尾部），而不是 system prompt ——
    因为它是**本轮**事实，且能保住 system prompt 的前缀缓存。
    """
    sensitive, why = classify_query(text)
    if not sensitive:
        return ""
    if not has_search:
        return (f"【时效要求】这句话涉及会变化的信息（{why}），但你本轮**没有联网工具**。"
                f"请**明确说明**你无法核实、你的信息可能已过时，"
                f"不要给出确定结论。")
    return (f"【时效要求】这句话涉及会变化的信息（{why}）。"
            f"**先用搜索工具核实，再回答**；"
            f"搜索结果里要优先采信日期最新的来源，并在回答里带上具体时间/日期。"
            f"若搜索失败，如实说'没查到最新情况'，而不是改用记忆回答。")


# ── 3. 工具结果过期护栏 ────────────────────────────────────────────
#: 各类外部结果的默认 TTL（秒）。超过即视为"不足以当事实引用"。
TTL_SECONDS = {
    "search_web": 30 * 60,        # 搜索：半小时
    "web_fetch": 30 * 60,
    "web_operate": 15 * 60,
    "weather": 30 * 60,
    "stock": 5 * 60,              # 行情：5 分钟
    "default": 60 * 60,
}

# 每会话的工具结果时间戳：{session: {tool: ts}}
_STAMPS = {}
_stamp_lock = None


def _get_lock():
    global _stamp_lock
    if _stamp_lock is None:
        import threading
        _stamp_lock = threading.Lock()
    return _stamp_lock


def mark(session_id, tool_name, ts=None):
    """记录某工具上次成功返回的时刻。"""
    with _get_lock():
        _STAMPS.setdefault(session_id or "__default__", {})[tool_name] = \
            ts if ts is not None else time.time()


def age_of(session_id, tool_name, ts=None):
    """该工具结果距今多少秒（无记录返回 None）。"""
    with _get_lock():
        rec = _STAMPS.get(session_id or "__default__") or {}
        t0 = rec.get(tool_name)
    if not t0:
        return None
    return max(0.0, (ts if ts is not None else time.time()) - t0)


def is_stale(session_id, tool_name, ts=None, ttl=None):
    """结果是否已过期。"""
    age = age_of(session_id, tool_name, ts)
    if age is None:
        return False
    limit = ttl if ttl is not None else TTL_SECONDS.get(
        tool_name, TTL_SECONDS["default"])
    return age > limit


def stale_note(session_id, tool_name, ts=None, ttl=None):
    """过期时给出的**显式标注**（模型看到后不会把它当当前事实）。"""
    age = age_of(session_id, tool_name, ts)
    if age is None:
        return ""
    limit = ttl if ttl is not None else TTL_SECONDS.get(
        tool_name, TTL_SECONDS["default"])
    if age <= limit:
        return ""
    mins = int(age // 60)
    if mins < 60:
        human = f"{mins} 分钟前"
    elif mins < 1440:
        human = f"{mins // 60} 小时前"
    else:
        human = f"{mins // 1440} 天前"
    return (f"\n\n[时效警告] 以上结果是 **{human}** 取得的，可能已经变化。"
            f"若用户问的是当前情况，请重新核实后再回答，不要直接当成现状。")


def reset():
    """清空时间戳记录（测试用）。"""
    with _get_lock():
        _STAMPS.clear()
        _CUTOFF_CACHE["text"] = None


# ── 4. 时间基准纠偏 ────────────────────────────────────────────────
def date_anchor(ts=None, cutoff=None):
    """给出一段"以当前真实日期为基准"的时间锚（防止模型用训练期日期推断）。

    模型的常见错误：被问"今年"时，用训练数据里的年份回答。
    显式给出当前年月日 + 明确"这是权威基准，不要用你的记忆推断"，可显著降低该类错误。
    """
    now = datetime.datetime.fromtimestamp(ts if ts is not None else time.time())
    wd = ("一", "二", "三", "四", "五", "六", "日")[now.weekday()]
    return (f"【时间基准】现在是 {now.year} 年 {now.month} 月 {now.day} 日（星期{wd}）。"
            f"这是**权威基准**。涉及年份/日期/年龄/时长时以它为准，"
            f"不要用你的训练知识去推断'现在是什么时候'。")
