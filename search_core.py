# -*- coding: utf-8 -*-
"""search_core.py — 搜索结果的统一形状、排序、格式化。

为什么要有这一层：
    两条搜索路径（自己扒 SERP / DeepSeek 原生接口）产出的东西形状不同，
    原来的后果是**每个下游都得知道自己吃的是哪条**：格式化一套、判质量一套、
    排序一套。改一次规则要改两处，迟早不同步。

    2026-10-23 把形状统一成 SearchResult，两条路都转成它，之后排序、截断、
    质量判定、给模型的文本全部共用一份代码。差异只剩在**信号从哪来**：
      · DeepSeek 直接给 rerank_score / authority / date
      · 自己扒网页就得从 SERP 文本里抽日期、按域名估权威、用词重合度算相关
    信号精度不同，但下游行为一致。

SearchResult 是个普通 dict，不是类。理由：要能直接 json 序列化、能跨线程传、
能在测试里手写构造。用 dataclass 这些都要额外做一遍。
"""

import datetime
import re

# ── 权威性分级 ────────────────────────────────────────────
#  与 DeepSeek 的 authority 保持同一套量纲（0~4，≥3 视为权威），
#  这样下游判断不用分情况。数值相近不代表等价：那边是它的模型判的，
#  这边是按域名白名单估的，只是"够用来排序"。
#
#  原则：**宁可低估，不要高估**。给垃圾站打高分会让它在时效加分下冲到前面，
#  这个错误比"权威的没被识别出来"严重得多 —— 后者只是排序不变。

_AUTH_4 = (
    ".gov", ".gov.cn", ".edu", ".edu.cn", ".mil", ".ac.cn",
    "who.int", "un.org", "w3.org", "ietf.org", "iana.org",
)

#: 一手信息源：厂商官网、官方文档、代码仓库、百科
_AUTH_3 = (
    "github.com", "gitlab.com", "docs.", "developer.", "api-docs.",
    "wikipedia.org", "wikidata.org",
    "mihoyo.com", "hoyolab.com",
    "openai.com", "anthropic.com", "deepseek.com", "deepmind.google",
    "microsoft.com", "apple.com", "google.com", "nvidia.com",
    "python.org", "nodejs.org", "mozilla.org", "kernel.org",
    "tencent.com", "aliyun.com", "huaweicloud.com", "bytedance.com",
    "pypi.org", "npmjs.com", "crates.io",
)

#: 有编辑/审核的媒体与问答
_AUTH_2 = (
    "zhihu.com", "bilibili.com", "36kr.com", "sspai.com",
    "thepaper.cn", "xinhuanet.com", "people.com.cn", "cctv.com",
    "jiqizhixin.com", "qbitai.com", "infoq.cn",
    "stackoverflow.com", "stackexchange.com", "reddit.com",
    "medium.com", "arxiv.org", "acm.org", "ieee.org",
    "bbc.com", "reuters.com", "nytimes.com",
)

#: 明确的内容农场/聚合站。降权，但不排除 —— 有时候只有它有。
_LOW_QUALITY = (
    "so.com/link", "hao123", "wenku.baidu", "zhidao.baidu",
    "douban.com/note", "baijiahao.baidu", "toutiao.com",
    "qcloud.com/product", "csdn.net/download",
)


def domain_of(url):
    """取主机名，去掉 www. 前缀。"""
    try:
        import urllib.parse
        host = (urllib.parse.urlparse(str(url or "")).netloc or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def authority_of(url):
    """按域名估权威性，0~4；判不出来返回 -1。

    这是启发式，不是判断。它只服务于排序和"要不要提一句这是官方来源"，
    不应该被当成事实陈述给用户。
    """
    host = domain_of(url)
    if not host:
        return -1
    low = str(url or "").lower()
    for d in _LOW_QUALITY:
        if d in low:
            return 0
    for d in _AUTH_4:
        if host.endswith(d) or host == d.lstrip("."):
            return 4
    for d in _AUTH_3:
        # "docs." / "developer." 这类是前缀匹配，其余按域名后缀
        if d.endswith("."):
            if host.startswith(d):
                return 3
        elif host == d or host.endswith("." + d):
            return 3
    for d in _AUTH_2:
        if host == d or host.endswith("." + d):
            return 2
    return 1


# ── 日期解析 ──────────────────────────────────────────────
#  SERP 摘要前面常挂日期（「2026年9月23日 · 全新版本已推出」），
#  这是自己扒网页唯一能白拿到的时间信号。要抽，但不能瞎抽 ——
#  抽错比没有更糟：一个错的日期会在时效排序里把无关内容顶上来。

_DATE_PATTERNS = (
    # 2026年9月23日 / 2026 年 9 月 23 日
    (re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"), "ymd"),
    # 2026-09-23 / 2026/09/23 / 2026.09.23
    (re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})"), "ymd"),
    # 09-23（缺年份，按今年算；去年的会算错，所以只在没有年份时用）
    (re.compile(r"^(\d{1,2})\s*[-月]\s*(\d{1,2})\s*日?"), "md"),
)

_REL_PATTERNS = (
    (re.compile(r"(\d+)\s*秒\s*之?前"), "sec"),
    (re.compile(r"(\d+)\s*分钟?\s*之?前"), "min"),
    (re.compile(r"(\d+)\s*(?:个)?\s*小时\s*之?前"), "hour"),
    (re.compile(r"(\d+)\s*天\s*之?前"), "day"),
    (re.compile(r"(\d+)\s*(?:个)?\s*周\s*之?前"), "week"),
    (re.compile(r"(\d+)\s*(?:个)?\s*月\s*之?前"), "month"),
    (re.compile(r"(\d+)\s*年\s*之?前"), "year"),
)

_REL_WORDS = (
    ("刚刚", 0), ("今天", 0), ("昨天", 1), ("前天", 2),
    ("本周", 0), ("这周", 0), ("上周", 7), ("本月", 0), ("这个月", 0),
)


def _now_dt(today=None):
    """取「现在」的 datetime，用于处理小时/分钟级的相对时间。

    ★ 2026-10-23：date 减 timedelta(hours=11) 会**原样返回同一天** ——
      不足一天的差值在 date 粒度上被吃掉，于是「11 小时之前」被解析成今天。
      这类错误不会抛异常，只会让一条 20 小时前的旧闻排到最前面。
      所以小时级以下一律走 datetime 再取 .date()。
    """
    if isinstance(today, datetime.datetime):
        return today
    if isinstance(today, datetime.date):
        return datetime.datetime.combine(today, datetime.time.min)
    return datetime.datetime.now()


def parse_date(text, today=None):
    """从一段文本里抽日期，返回 'YYYY-MM-DD'；抽不出来返回 ''。

    ★ 只认**文本开头附近的绝对日期**。理由：摘要正文里出现的日期通常是
      内容里提到的事件时间（「2026/09/23 06:00 开始维护」），不是发布日。
      拿它当发布时间，会让一条 2024 年的文章因为正文提到 2026 而排到最前。
    """
    s = str(text or "").strip()
    if not s:
        return ""
    now = today or datetime.date.today()

    # 相对时间只在开头出现（「11 小时之前 · ...」）
    head = s[:24]
    for word, days in _REL_WORDS:
        if word in head:
            try:
                return (now - datetime.timedelta(days=days)).isoformat()[:10]
            except Exception:
                return ""
    for pat, unit in _REL_PATTERNS:
        m = pat.search(head)
        if not m:
            continue
        try:
            n = int(m.group(1))
        except Exception:
            continue
        try:
            if unit in ("sec", "min", "hour"):
                delta = {
                    "sec": datetime.timedelta(seconds=n),
                    "min": datetime.timedelta(minutes=n),
                    "hour": datetime.timedelta(hours=n),
                }[unit]
                return (_now_dt(today) - delta).date().isoformat()
            delta = {
                "day": datetime.timedelta(days=n),
                "week": datetime.timedelta(weeks=n),
                "month": datetime.timedelta(days=n * 30),
                "year": datetime.timedelta(days=n * 365),
            }[unit]
            return (now - delta).isoformat()[:10]
        except Exception:
            return ""

    # 绝对日期同样只在开头找
    head = s[:32]
    for pat, kind in _DATE_PATTERNS:
        m = pat.search(head)
        if not m:
            continue
        try:
            if kind == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                y, mo, d = now.year, int(m.group(1)), int(m.group(2))
                # 只有月日、且算出来在未来 → 是去年的
                if (mo, d) > (now.month, now.day):
                    y -= 1
            got = datetime.date(y, mo, d)
        except Exception:
            continue
        # 明显不合理的丢掉：未来日期、或早于 2000 年
        if got > now + datetime.timedelta(days=1):
            continue
        if got.year < 2000:
            continue
        return got.isoformat()
    return ""


# ── 相关性打分（本地近似 rerank）────────────────────────────
#  DeepSeek 有真 rerank 模型。这里只能做词面重合——不能理解同义和意图。
#  所以定位是"排序的兜底判据"，不是"质量分"。它只要能把明显不相关的压下去
#  就够用了；精度差异由下面的权威性和位置权重补。

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TOKEN_SPLIT = re.compile(r"[^\w\u4e00-\u9fff]+")

#: 中文里几乎不承载信息的词。命中它们不加分。
#  不删干净是因为删了会把「不一定」和「一定」变成同一个词。
_STOP_BIGRAMS = frozenset((
    "什么", "怎么", "为什", "么样", "这个", "那个", "可以", "我们", "你们",
    "他们", "一个", "这个", "是不", "不是", "有没", "没有", "知道", "告诉",
))


def _tokens(text):
    """切成可比较的词元：英文按词，中文按二元组。"""
    s = str(text or "").lower()
    if not s:
        return set()
    out = set()
    for part in _TOKEN_SPLIT.split(s):
        if not part:
            continue
        if _CJK_RE.search(part):
            for i in range(len(part) - 1):
                bg = part[i:i + 2]
                if bg not in _STOP_BIGRAMS:
                    out.add(bg)
            if len(part) == 1:
                out.add(part)
        else:
            out.add(part)
    return out


def relevance(title, snippet, query):
    """标题/摘要与查询的词面重合度，0~1。

    标题权重 0.7、摘要 0.3 —— 标题是这段内容的自我概括，比正文片段可靠。
    """
    qt = _tokens(query)
    if not qt:
        return 0.0
    tt = _tokens(title)
    st = _tokens(snippet)
    hit_t = len(qt & tt) / len(qt)
    hit_s = len(qt & st) / len(qt)
    return min(1.0, hit_t * 0.7 + hit_s * 0.3)


# ── 结果对象 ──────────────────────────────────────────────

def make(url="", title="", snippet="", summary="", content="",
         date="", authority=None, score=None, site_name="", source="",
         rank_in_source=None):
    """构造一条统一结果。

    不传 authority / score 就按 url 和文本自动估。
    `source` 记录来自哪个引擎（bing/so/ddg/ds），排查时有用。
    `rank_in_source` 是这条在该引擎结果里的原始位置（1 起）——
      fill_scores 拿它当主判据，所以引擎解析时必须传，别丢。
    """
    u = str(url or "").strip()
    t = str(title or "").strip()
    s = str(snippet or "").strip()
    d = str(date or "").strip()
    if d and len(d) > 10:
        d = d[:10]
    if authority is None:
        authority = authority_of(u)
    it = {
        "url": u,
        "title": t,
        "snippet": s,
        "summary": str(summary or "").strip(),
        "content": str(content or "").strip(),
        "date": d,
        "authority": int(authority),
        "score": float(score) if score is not None else 0.0,
        "site_name": str(site_name or "").strip() or domain_of(u),
        "source": str(source or ""),
    }
    if rank_in_source is not None:
        it["_rank_in_source"] = int(rank_in_source)
    return it


def fill_scores(items, query):
    """给结果补相关分（0~3，与 DeepSeek 同量纲）。

    ★ 2026-10-23：第一版只看词面重合，实测是错的。
      搜「原神最新版本」，「版本 历史 - 原神 WIKI」和官方「《原神》官方网站-
      全新7.1版本上线」拿到**一模一样**的 0.34 分 —— 中文不切词的二元组里
      「神最」这种跨词碎片既不在标题里也不在正文里，而标题写的是「全新7.1版本」，
      和「最新」一个词都不重合。词面分数在这里等于噪声。

      真正的信号是**引擎给的排列顺序**：Bing 第一条就是官方 7.1 页面。
      人家有完整的排序模型，我拿二元组重算一遍是自废武功。

      所以现在的组合是：
        引擎位置  0~1.5 分（第 1 条满 1.5，第 10 条约 0.3）—— 主判据
        词面重合  0~1.0 分 —— 辅助，能把"排第 5 但明显偏题"的压回去
        权威性    0~0.5 分 —— 官网/文档/仓库，弱信号
      合计上限 3.0，与 DeepSeek 的 rerank_score 同量纲，阈值可以共用。

    ★ 2026-10-23 二次修正：**位置分要按词面重合度打折**。实测缺陷：

        搜「恋人不行这个动画好看吗」，Bing 第 1 条返回的是「135编辑器」——
        一个跟动画毫无关系的产品页。它的词面重合是 0.0，
        但"排第 1"给了 1.5 分，**正好等于优质线 GOOD_SCORE**，
        于是 quality_of 判成 good，模型拿着这条去回答"这部动画好看吗"。

      问题在于位置分被当成了绝对可信的信号。可引擎排第 1 不一定因为相关 ——
      广告位、站点兜底页、引擎自己觉得"用户大概想找这个"都会排前面。
      改成把位置分乘以 `0.4 + 0.6 × 词面重合`：完全零重合只留 40%，
      足以让它掉到优质线以下（0.6 分），但只要有一点重合就几乎不打折。
      不能乘纯词面重合 —— 那等于回到第一版的错。

    跨引擎合并时位置会被摊平（两个引擎都排第 1 的两条谁靠前？），
    这个由 _merge_results 里的 `_rank_in_source` 保留原位置解决。
    """
    out = []
    for it in (items or []):
        it = dict(it)
        if it.get("score"):
            out.append(it)
            continue
        pos = it.get("_rank_in_source")
        if pos is None:
            pos = 5          # 没有位置信息（比如手写的测试数据）→ 当中间位置
        try:
            pos = int(pos)
        except Exception:
            pos = 5
        pos = max(1, pos)
        pos_score = max(0.0, 1.5 - 0.15 * (pos - 1))
        rel = relevance(it.get("title"), it.get("snippet") or it.get("summary"), query)
        try:
            auth = int(it.get("authority"))
        except Exception:
            auth = -1
        auth_score = 0.5 if auth >= 3 else (0.2 if auth == 2 else 0.0)
        # 位置分按相关度打折，见 docstring 里「恋人不行 / 135编辑器」那个例子
        pos_score *= 0.4 + 0.6 * rel
        it["score"] = round(min(3.0, pos_score + rel * 1.0 + auth_score), 3)
        out.append(it)
    return out


# ── 排序 ──────────────────────────────────────────────────
#  下面这几条是踩出来的，不是设计出来的。改之前先读一遍。

#: 问句里出现这些词，说明用户要的是**现在**，不是"关于这个主题最权威的那篇"。
_RECENCY_WORDS = (
    "最新", "最近", "现在", "今天", "今晚", "昨天", "本周", "这周",
    "本月", "今年", "当前", "目前", "实时", "刚刚", "刚才",
    "最新版", "新版", "更新", "上线", "发布", "发售",
)

#: ★ 2026-10-27：问句里出现**年份数字**，同样是在要"这一年的版本"。
#:
#: 起因是真机事故：用户问「今年明日方舟周年半周年限定是谁」，
#: 模型把检索词改写成「明日方舟 2026 半周年 限定 干员」——
#: **「今年」被改写掉了**，时效加权因此整个失效（wants_recency=False），
#: 于是 2025 年的旧闻排在 2026 年的新消息前面。
#: 模型答「十一月的还没宣」，而那条 11 月的消息其实就躺在搜索结果里。
#:
#: 所以：年份数字本身就是时效信号，且比「今年」更精确 ——
#: 问 2026 就该优先要 2026 年的内容。
_YEAR_RE = re.compile(r"(?<!\d)(20[0-4]\d)(?!\d)")


def query_years(query):
    """从问句里抽年份数字（2024~2049）。返回集合，空集表示没提年份。"""
    return set(_YEAR_RE.findall(str(query or "")))


def wants_recency(query):
    q = str(query or "").lower()
    if any(w in q for w in _RECENCY_WORDS):
        return True
    # 年份数字也算时效信号（见上面的真机事故）
    return bool(query_years(query))


def year_mismatch_count(items, query):
    """这批结果里，有多少条**年份与问句对不上**。

    判据：问句提到 2026，结果的发布日期是 2025 → 对不上。
    这类结果不是"没用"，而是**回答了另一个问题** ——
    模型把它们当成答案就会说出「还没宣」这种事实错误。

    没有日期的结果不计入（无从判断，不冤枉）。
    """
    years = query_years(query)
    if not years:
        return 0
    n = 0
    for it in (items or []):
        d = str(it.get("date") or "")[:10]
        if len(d) != 10:
            continue
        y = d[:4]
        if y not in years:
            n += 1
    return n

#: 下载站/入口页的标题特征。它们日期永远是最新的（页面天天被重抓），
#: 内容却不是答案 —— 问「原神最新版本」时返回「应用宝官网免费下载」毫无用处。
_AGGREGATOR_TITLE = (
    "下载", "免费安装", "官网入口", "正版软件", "手机游戏网",
    "手机版下载", "应用宝", "手游网", "攻略站",
)

#: 时效加权的上限。压到 0.35 是因为实测同主题结果的分差常在 0.3 以内，
#: 给太多会让「新但无关」压过「旧但正是答案」。
_RECENCY_BONUS_MAX = 0.35

#: 下载站/入口页的降权（query 不是在问下载时）。数值必须**大于**
#: "同主题分差"的常见量级（实测 0.2 上下）——它要表达的是
#: "这页不回答'最新版本是什么'"，不是"它稍微差一点"。
_AGGREGATOR_PENALTY = 0.25

#: 时效加权的半衰期（天）。一个月内的内容拿满分，90 天后归零。
_RECENCY_HALFLIFE_DAYS = 30


def _age_bonus(date_str, today=None):
    """这条结果有多新 —— 折算成 0~0.35 的加分。"""
    s = str(date_str or "")[:10]
    if not s or len(s) != 10:
        return 0.0
    try:
        y, m, d = (int(x) for x in s.split("-"))
        got = datetime.date(y, m, d)
        now = today or datetime.date.today()
        days = (now - got).days
        if days < 0:
            return _RECENCY_BONUS_MAX * 0.5      # 预发布页，不奖不罚
        if days <= _RECENCY_HALFLIFE_DAYS:
            return _RECENCY_BONUS_MAX
        span = 90 - _RECENCY_HALFLIFE_DAYS
        return _RECENCY_BONUS_MAX * max(0.0, 1.0 - (days - _RECENCY_HALFLIFE_DAYS) / span)
    except Exception:
        return 0.0


def _is_aggregator(it, query):
    """这条结果是不是"下载站/入口页"，且用户**不是在问下载**。

    为什么单独成函数（2026-10-29）：原来只在 final_score 里内联做"扣 0.25"，
    于是时效加分会把它救回来 —— 问「原神最新版本」时，下载站页面天天重抓、
    永远吃满 +0.35 时效分，扣 0.25 之后净赚 0.10，压过真正的更新说明。
    当日用例只赢 0.0008，隔一天就翻（真机：测试长期红）。
    判据改成"下载站不享受时效加分"后，这个顺序与日期无关。
    """
    q = str(query or "").lower()
    if "下载" in q or "安装" in q:
        return False                      # 问下载时它就是答案，不许降权/不许扣分
    title = str(it.get("title") or "")
    return any(w in title for w in _AGGREGATOR_TITLE)


def final_score(it, query):
    """相关度 + 时效（条件性）− 下载站降权。

    ★ 试过只按分数排：搜「原神最新版本」，前三是 5 月、8 月、10 月的公告，
      真正的 7.1（9/22）排第 7 —— 分数差只有 0.018。纯分数排序时，
      那点差异衡量的是浮点噪声，不是相关性。
    ★ 也试过只按日期排：第一名变成「应用宝官网免费下载」—— 它日期最新
      （页面天天重抓），但不是答案。
    ★ 所以用混合分。它只在分差本来就小的时候起作用，不会把明显更相关的
      结果挤下去。这是启发式，不保证对。

    ★ 2026-10-29：**下载站不吃时效分**。理由见 _is_aggregator ——
      "页面天天重抓"意味着它的日期不携带任何"这条更新"的证据；
      给它时效加分再扣固定分，等于让两套启发式互相抵账，结果随时钟漂移。
    """
    try:
        total = float(it.get("score") or 0)
    except Exception:
        total = 0.0
    agg = _is_aggregator(it, query)
    if wants_recency(query) and not agg:
        total += _age_bonus(it.get("date"))
    if agg:
        total -= _AGGREGATOR_PENALTY
    return total


def sort_key(it, query=""):
    return (final_score(it, query), str(it.get("date") or "")[:10] or "0000-00-00")


def rank(items, query=""):
    """排好序。调用方拿到的是新列表，不改原列表。"""
    return sorted(list(items or ()), key=lambda x: sort_key(x, query), reverse=True)


# ── 给模型读的文本 ────────────────────────────────────────
#  ★ 2026-10-23 实测：DeepSeek 的 summary 号称 600~800 字，实际中位数 1300 字，
#    最长 2461 字。5 条全带 → 6000~8600 字。而群里要回答的话通常不到 10 字 ——
#    为了答「7.1」塞八千字原文，多付三倍 token，还会把回复带成大段复述公告。
#    所以要截。但截哪里不能拍脑袋：标题本身就是答案的高密度形态，别动；
#    前几条给足（它们命中了检索意图），后面的只留一句。

DEFAULT_MAX_ITEMS = 3
SUMMARY_BUDGET = 500      # 单条摘要字数上限（第 1 条给双倍）
TOTAL_BUDGET = 3000       # 整段字数上限

#: 优质线。与 DeepSeek 的 rerank_score 阈值对齐。
GOOD_SCORE = 1.5


def _clip(text, limit):
    """按句截断，不硬切在字中间。

    硬切会留下「维护完成后，游戏将更新为全新「月之七」版本——「『空月之歌」
    这种半句话，模型读到会当成完整信息。
    """
    s = str(text or "").strip()
    if not s or len(s) <= limit:
        return s
    head = s[:limit]
    for i in range(len(head) - 1, max(0, limit - 120), -1):
        if head[i] in "。！？\n.!?":
            return head[:i + 1].rstrip()
    return head.rstrip() + "…"


def _fmt_date(iso):
    s = str(iso or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return ""


def _authority_note(auth):
    """权威性只在**有信息**时才说。

    不写「权威性 2」这种数字：模型看到数字会当成质量评分去引用，
    而 0 分和「未知」在它眼里没区别。只在 ≥3 时提一句。
    """
    try:
        a = int(auth)
    except Exception:
        return ""
    return "官方/权威来源" if a >= 3 else ""


def format_for_model(items, query="", max_items=None, with_content=True,
                     summary_budget=SUMMARY_BUDGET, total_budget=TOTAL_BUDGET):
    """排成给模型读的文本。空列表返回空串。"""
    if not items:
        return ""
    ordered = rank(items, query)
    if max_items is None:
        max_items = DEFAULT_MAX_ITEMS
    if max_items:
        ordered = ordered[:max_items]

    lines = []
    used = 0
    for i, it in enumerate(ordered, 1):
        # 第 1 条给双倍预算：它才是直接回答问题的那个
        budget = summary_budget * 2 if i == 1 else summary_budget

        bits = []
        d = _fmt_date(it.get("date"))
        if d:
            bits.append(d)
        site = it.get("site_name")
        if site:
            bits.append(site)
        a = _authority_note(it.get("authority"))
        if a:
            bits.append(a)
        head = f"{i}. " + (f"[{' · '.join(bits)}] " if bits else "") + (it.get("title") or "")

        # 优先 summary（长），没有退到 snippet（短）
        body = _clip(it.get("summary") or it.get("snippet") or "", budget)
        block = head + ("\n   " + body if body else "")

        if with_content:
            c = _content_excerpt(it.get("content"), body, q=query)
            if c:
                block += "\n   正文：" + c

        if used + len(block) > total_budget and lines:
            break
        lines.append(block)
        used += len(block)
    return "\n\n".join(lines)


#: 正文如果要带，至少这么长 —— 比这短的多半是"菜单 回到顶部"这类残留骨架。
CONTENT_MIN_CHARS = 120
#: 单条正文的展示上限。摘要已经把话说完了，正文只是补细节。
CONTENT_BUDGET = 700

#: 前端模板/脚本残留的特征。页面把模板字符串写在 HTML 文本里时，
#: 抽出来就是这种东西 —— 对模型是纯噪声，而且会被当成"页面内容"引用。
_CODE_MARKERS = (
    "${", "{{", "}}", "escape(", "function(", "=>{", "===", "!==",
    "className", "style=", "var ", "const ", "let ", "document.",
    "window.", "undefined", "null)", "return ",
)

#: 模板引擎的定界符。这几样**正常文字里不可能出现** ——
#: 中文英文的散文都不会写 `{/if}`、`{list x as y}`、`{each ...}`。
#: 单独列一张表，是因为它们比上面那批"关键词"硬得多：
#: 上面那些（`return `、`const `）在技术博客的正文里合法存在，
#: 下面这些不存在。任何**一个**命中就判死。
#: 注意不含 `</` —— 讲 HTML 的文章里 `</div>` 是合法正文，那个按次数判。
_TEMPLATE_TAGS = ("{/", "{if ", "{list ", "{each ", "{else}")


def _looks_like_code(text):
    """这段文本是不是前端模板/脚本残留。

    ★ 2026-10-23 实测缺陷：搜「恋人不行这个动画好看吗」，网易云音乐那条
      抽到的正文是：

          ${escape(x.user.nickname)} {if x.user.avatarDetail && ...} {/if}
          {if x.user.vipRights} ... redVipLevel == 1} ...

      页面把模板字符串写在了 HTML 文本里，`_block_text` 挑块时它文字量最多、
      链接最少，正好中了"正文"的判据。拿给模型等于是乱码。

    ★ 2026-10-23 第二次实测：原来的标记表**漏了模板定界符本身**。
      网易云首页抽出来那 760 字里有 `{/if}`、`{list suggests as item}`、
      `{list}`、`${item|escape}` —— 但表里只收了 `${`、`{{`、`}}`，
      `{/` 和 `{list ` 不算命中，于是一条本该被拦下的整段弹窗文案通过判定。
      补了 `_TEMPLATE_TAGS`，其中任何**一个**出现就直接判死：
      散文里不会写 `{/if}`，不存在误伤（技术博客里会合法出现 `return`，
      所以那批关键词才要求命中 ≥3 个）。
    """
    s = str(text or "")
    if not s:
        return False
    if any(t in s for t in _TEMPLATE_TAGS):
        return True
    if s.count("</") >= 3:
        # 讲 HTML 的文章里会出现 `</div>`，但连着三处就是没被剥掉的标签残留。
        # 所以这条按次数，不放进 _TEMPLATE_TAGS。
        return True
    hit = sum(1 for m in _CODE_MARKERS if m in s)
    if hit >= 3:
        return True
    # 单个特征极密集时也算：模板里 `${` 会反复出现
    return s.count("${") >= 3 or s.count("{if ") >= 2 or s.count("</") >= 3


def _content_excerpt(content, summary, q="", limit=CONTENT_BUDGET):
    """正文只有在**能补充摘要之外的信息**时才值得带上。

    ★ 2026-10-23 实测两个反面例子：

      ① 搜「原神最新版本」，摘要已经说清「全新7.1版本已推出」，
         而 wiki 那条抓到的正文是 800 字的「大版本由 1.0 开始每年一次、
         每个大版本 9 个小版本、地图一般在 N.0 更新…」——
         它一个字都没回答"最新是几"，纯粹占预算，还会把模型带偏去讲版本规律。

      ② 搜「MaiBot」，第 2 条的正文只有「菜单 回到顶部 Ar」——
         抽正文没抽到东西，但 `正文：` 这个前缀照样打出去了，
         模型读到会以为那就是正文。

    所以判据是二选一即可：
      · 太短（< CONTENT_MIN_CHARS）→ 不带。它不是正文，是骨架残留。
      · 和摘要几乎重复 → 不带。摘要本来就在上面，重复一遍纯浪费。
    两条都不满足才带，且按句截到 CONTENT_BUDGET。
    """
    c = str(content or "").strip()
    if len(c) < CONTENT_MIN_CHARS:
        return ""
    if _looks_like_code(c):
        return ""      # 模板/脚本残留，不是正文

    # 与摘要的重合：用连续三字组估，比按词可靠（中文不切词）
    def _tri(s):
        t = re.sub(r"[\s，。！？、；：,.!?;:~…\-—_（）()【】\[\]\"'“”‘’|/\\]+", "", str(s or ""))
        return {t[i:i + 3] for i in range(len(t) - 2)}

    a, b = _tri(c[:limit]), _tri(summary)
    if a and b and len(a & b) / min(len(a), len(b)) >= 0.7:
        return ""      # 正文基本就是摘要的重复，没有新信息

    return _clip(c, limit)


# ── 质量判定 ──────────────────────────────────────────────

def quality_of(items, query=""):
    """判这批结果够不够回答。三档：good / thin / empty。

    判据是 score 和有没有内容，不掺"摘要像不像导航"这类文本特征 ——
    那是扒网页时才需要绕的弯（因为它只有文本），统一后由内容量兜住。

    ★ 2026-10-27 加「年份对不上」这一档（stale_year）：
      问句提到 2026，结果全是 2025 年的 —— 它们**不是没用，是回答了另一个
      问题**。真机事故：用户问「今年明日方舟周年半周年限定是谁」，
      搜回 2025 年的旧闻，模型据此答「十一月的还没宣」，
      而 11 月的消息就在另一条结果的正文里（那条排在后面，因为没有时效加权）。
      这种"能答但答的是错年份"比"查不到"更危险：查不到模型会说没查到，
      答错年份模型会**笃定地说错话**。
    """
    lst = list(items or ())
    if not lst:
        return {"verdict": "empty", "good_count": 0, "top_score": 0.0,
                "reasons": ["没有搜到任何结果"]}

    scores = []
    for x in lst:
        try:
            scores.append(float(x.get("score") or 0))
        except Exception:
            scores.append(0.0)
    good = sum(1 for s in scores if s >= GOOD_SCORE)
    top = max(scores) if scores else 0.0

    has_text = any((x.get("summary") or x.get("snippet") or x.get("content")) for x in lst)

    if not has_text:
        return {"verdict": "empty", "good_count": 0, "top_score": round(top, 2),
                "reasons": ["结果里没有任何可读内容"]}

    # ── 年份对不上：有日期、且年份跟问句不是同一年 ──
    _ym = year_mismatch_count(lst, query)
    _dated = sum(1 for x in lst if len(str(x.get("date") or "")[:10]) == 10)
    if _ym and _dated and _ym == _dated:
        _ys = "、".join(sorted(query_years(query)))
        return {"verdict": "stale_year", "good_count": good,
                "top_score": round(top, 2),
                "reasons": [f"问的是 {_ys} 年，但 {_dated} 条结果全部是其他年份的"]}

    if good >= 1:
        return {"verdict": "good", "good_count": good, "top_score": round(top, 2),
                "reasons": []}

    reasons = [f"最高相关度只有 {top:.2f}（优质线 {GOOD_SCORE}）"]
    return {"verdict": "thin", "good_count": 0, "top_score": round(top, 2),
            "reasons": reasons}


QUALITY_HINT = {
    "thin": (
        "【结果质量：偏薄】{reasons}。"
        "\n这些结果可能不足以回答问题。把查到的那点说出来（具体的名词、数字、时间），"
        "再说清哪一部分没查到。不要凭常识补全没查到的部分。"
    ),
    "stale_year": (
        "【结果质量：年份对不上】{reasons}。"
        "\n这些内容讲的是**别的年份**的事，拿它们回答会说出事实错误。"
        "\n换一组关键词再搜一次（换个说法、加上年份或活动名），"
        "或者直接说自己没查到这一年的消息 —— **不要**根据别的年份下结论。"
    ),
    "empty": (
        "【结果质量：几乎没有可用内容】"
        "\n不要根据这批结果下结论。直接说没查到，可以补一句你确定的部分，"
        "但要说清那是常识不是查到的。"
    ),
}


def quality_hint(info):
    """把质量判定翻成给模型看的话。good 不返回任何东西。"""
    v = (info or {}).get("verdict")
    tpl = QUALITY_HINT.get(v)
    if not tpl:
        return ""
    reasons = "；".join((info or {}).get("reasons") or [])
    return tpl.replace("{reasons}", reasons).strip()


__all__ = [
    "make", "fill_scores", "relevance", "rank", "sort_key", "final_score",
    "format_for_model", "quality_of", "quality_hint", "parse_date",
    "authority_of", "domain_of", "wants_recency", "GOOD_SCORE",
]