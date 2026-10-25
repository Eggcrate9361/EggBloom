# -*- coding: utf-8 -*-
"""
web_access.py — 统一联网搜索/抓取模块

目标：
- 减少对单引擎的依赖，降低被拦截概率
- 提供可重试、可降级的网页获取能力
- 供 bot_core.search_web 和 plugins.web_search 共用
"""

import html
import re
import time
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional

import search_core

# 简单内存缓存，避免同一查询 30 秒内重复请求
_QUERY_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 30.0


def _now() -> float:
    return time.time()


def _get_cached(query: str) -> Optional[str]:
    hit = _QUERY_CACHE.get(query)
    if not hit:
        return None
    ts, data = hit
    if _now() - ts > _CACHE_TTL:
        _QUERY_CACHE.pop(query, None)
        return None
    return data


def _set_cache(query: str, data: str) -> None:
    _QUERY_CACHE[query] = (_now(), data)


def _fetch_html(url: str, timeout: int = 15, headers: Optional[dict] = None) -> Optional[str]:
    """带 UA 的网页抓取，失败返回 None。"""
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


def _strip_tags(text: str) -> str:
    """剥掉真实标签。单独抽出来，是因为它要被调用两次（解码前后各一次）。

    ★ 2026-10-24：内联高亮标签单独处理。搜索结果里 `<em>`/`<b>` 是引擎
      给的**高亮**，长在词内部：

          <em>原神</em>官方网站     →  原神 官方网站   （错，词被拆开）
          <em>原神</em>官方网站     →  原神官方网站     （对）

      原来所有标签一律换成空格，模型看到的是「核 你有约…核装备与核工程 学院
      召开 心理委员 培训会」这种被切碎的句子。块级标签本来就该换空格
      （它们是天然分隔），内联的不该。
      `<a>` 不列入内联：链接是结构边界，相邻两个链接不能粘成一个词。
    """
    text = re.sub(r'<!--.*?-->', ' ', text, flags=re.DOTALL)
    text = re.sub(r'<!\[CDATA\[.*?\]\]>', ' ', text, flags=re.DOTALL)
    # 内联 SVG 整段丢掉。图标、logo 都是内联 SVG，它们的 `<path d="M4.89...">`
    # 属性值里没有尖括号问题，但**量极大**（B站首页光图标路径就上万字符），
    # 而且一个字的信息都没有 —— 不先单独整段吃掉，剥标签只会留下一堆坐标数字。
    text = re.sub(r'<svg\b.*?</svg>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    # 内联标签删掉（不换空格），避免把词拆开
    text = re.sub(r'</?(?:em|b|i|strong|mark|font|span|u|sup|sub)\b[^>]*>',
                  '', text, flags=re.IGNORECASE)
    return re.sub(r'<[^>]+>', ' ', text)


def _clean_text(text: str) -> str:
    """HTML 片段 → 干净文本。

    ★ 2026-10-23 修：原来是「先剥标签、再解码实体」，顺序反了。
      页面对特殊字符做二次转义时（`&lt;img alt="..." src="..."&gt;`），
      剥标签那一步看到的还是一串无害的实体，什么也剥不到；解码发生在之后，
      标签就此复活，原样进了结果。实测搜「哔哩哔哩 wiki」，BWiki 那条的正文是：

          <img alt="坎蒂丝" src="https://patchwiki.biligame.com/images/..." ...>

      一条带完整 URL 的 img 原样交给模型。

      正确顺序是**剥 → 解 → 再剥**：第一遍剥真实标签，解码让转义的标签现形，
      再剥一遍收掉它们。

    ★ 2026-10-23 二次修：不能再套一层循环。第一版写成「解码+剥标签」循环两轮，
      结果更糟 —— 第二轮解码时，页面里合法出现的 `&lt;`（讲代码的文档、
      被转义的尖括号）会变成 `<`，然后与后面的文字连成"标签"，把正常正文
      一起吃掉。BWiki 首页因此抽出 353 字的导航，还夹着 `<img ...>`。
      解码只做一次，够用。
    """
    text = _strip_tags(str(text or ''))
    try:
        text = html.unescape(text)
    except Exception:
        pass
    text = _strip_tags(text)        # 收掉被转义过的标签
    text = re.sub(r'&ensp;|&nbsp;|&#\d+;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ══════════════════════════════════════════════════════════════
# 查询词提炼（2026-10-23）
# ══════════════════════════════════════════════════════════════
# 为什么必须做：整句丢给搜索引擎，虚词会被当成主体。
# 实测（2026-10-23，Bing）：
#   「帮我查一下原神最新版本」 → 返回「帮」字的字典释义、《广韵》《正韵》……
#   「你知道MaiBot吗」         → 返回「你」字的字形源流、屠洪刚的歌
#   「今天北京时间几点」       → 返回日历网、黄历、今天是第 43 周
#   「恋人不行这个动画好看吗」 → 返回李荣浩的《恋人》
# 换成「原神 最新版本」「MaiBot」「北京时间」后，同一个引擎立刻给出对的结果。
# 所以问题不在引擎，在查询词 —— 主语被语气词埋住了。

#: 句首的客套/请求语。整段剥，因为它们从不出现在答案里。
#  顺序有讲究：**长的在前**。「请问能帮我」这种叠用的，
#  如果先剥「请问」再剥「帮我」，中间会剩下一个「能」卡住，
#  后面所有模式都匹配不上（实测：「请问能帮我查一下原神最新版本是什么吗」
#  只剥掉了「请问」，剩下「能帮我查一下原神最新版本」）。
_QUERY_PREFIX_PATTERNS = (
    r"^(?:请问|麻烦|帮我|帮忙|能不能|可不可以|可以|想|要|请|能|你能|您能)+",
    r"^(?:查一下|查查|搜一下|搜搜|查一查|搜一搜|查询|搜索|问一下|问下)",
    r"^(?:你知道|你知不知道|你知道不知道|你知道的话|知不知道|晓得不)",
    r"^(?:告诉我|说说|讲讲|介绍下|介绍一下|解释下|解释一下)",
)

#: 句中的语气助词、提问标记。这些词出现的地方就是"主语结束的地方"，
#: 直接删掉不会损失实体信息。
#  ★ 2026-10-23：**这里只放多字词**。单字语气词（吗/呢/吧/么/啊…）不能做
#    全文替换：`replace("么", " ")` 会把「有什么新内容」切成「有什 新内容」，
#    实测提炼出来的检索词直接搜不到东西。单字一律走句尾剥离，见
#    _QUERY_TAIL_PARTICLES。
_QUERY_NOISE = (
    "这个", "那个", "这种", "那种", "这些", "那些",
    "是什么", "是啥", "怎么样", "怎么", "如何", "为什么", "为啥",
    "好看吗", "好看么", "真的吗", "对吗", "行吗", "好吗",
    "我想知道", "想问下", "想问一下",
)

#: 句尾语气词。只在**结尾**剥，剥完还有内容才认。
_QUERY_TAIL_PARTICLES = re.compile(r"[了吗呢吧啊呀哈哦嘛啦么]+$")

#: 时间虚词。「今天」「现在」这类词对搜索引擎是强噪声：
#: 「今天北京时间几点」会被理解成"今天的日期"，而不是"北京时间"。
_QUERY_TIME_NOISE = ("今天", "现在", "此刻", "当前", "目前", "最近", "刚才", "刚刚")

#: 结尾的疑问标点
_QUERY_PUNCT_RE = re.compile(r"[？?！!。，,、；;：:~…\s]+$")


def normalize_query(query: str) -> str:
    """把一句人话提炼成搜索关键词。

    保守原则：**只删虚词，不动实体**。宁可当一个词都没删掉、原样搜，
    也不能把真正的关键词删了 —— 那种失败比搜索跑偏更糟（会返回空）。

    返回：提炼后的查询。删空了就返回原句（不做破坏性处理）。
    """
    original = str(query or "").strip()
    if not original:
        return original

    q = original
    # 1. 剥句首客套语（可能叠多层：「请问能帮我查一下…」）
    for _ in range(3):
        before = q
        for pat in _QUERY_PREFIX_PATTERNS:
            q = re.sub(pat, "", q)
        q = q.strip(" ，,、")
        if q == before:
            break

    # 2. 删句中噪声词
    for w in _QUERY_NOISE:
        q = q.replace(w, " ")

    # 2.5 剥句尾语气词与结尾标点
    #     ★ 2026-10-23：剥完标点可能露出语气词，剥完语气词又可能露出标点，
    #       所以要交替跑几轮。实测「原神7.1有什么新内容？」只做一轮会留下
    #       尾标点，Bing 会把「？」算进检索词。
    for _ in range(3):
        before = q
        q = _QUERY_PUNCT_RE.sub("", q.strip()).strip()
        tail = _QUERY_TAIL_PARTICLES.sub("", q).strip()
        if len(tail) >= 2:
            q = tail
        if q == before:
            break

    # 3. 时间虚词：只有**其他内容还够长**时才删。
    #    「今天天气」删掉「今天」只剩「天气」，也还行；但「今天几号」删完就空了。
    without_time = q
    for w in _QUERY_TIME_NOISE:
        without_time = without_time.replace(w, " ")
    without_time = re.sub(r"\s+", " ", without_time).strip()
    if len(without_time) >= 2:
        q = without_time

    # 4. 收尾清理
    q = re.sub(r"\s+", " ", q).strip()
    q = _QUERY_PUNCT_RE.sub("", q).strip()

    # 5. 兜底：删空了、或删到只剩一两个字的，退回原句。
    #    实测只剩一个字时搜索必然跑偏：查「帮」返回的是这个字的字典释义，
    #    《广韵》《正韵》全出来了。此时原句至少还有上下文。
    if len(q) < 2:
        return original
    # 单个汉字在中文搜索里歧义太大（「今」「几号」），
    # 只有原本就短、没有删掉东西时才允许放行。
    if len(q) == 1 and q != original:
        return original
    return q


def _parse_bing(html: str, limit: int = 8):
    """Bing SERP HTML → [SearchResult]。**纯函数，不联网**。

    ★ 2026-10-23 重写。原实现返回 (summaries, urls) 两个**平行列表**，
      摘要和链接没有对应关系 —— 摘要 A 配链接 B 是常态。
      后果是下游只能"拿一堆摘要 + 另一堆链接"，既不能把正文归到某条结果上，
      也不能判断这篇是官网还是垃圾站。这是它达不到 DeepSeek 水平的根本原因，
      不是解析器不够聪明的问题。

      改成**按结果块解析**：b_algo 容器里同时有标题链接和摘要，一起取。
      块外的东西一概不看（这是 2026-10-13 搜索事故定下来的规矩：
      旧版抓页面任意 b_caption/<p>，把广告和侧栏当结果）。

    解析和抓取拆开，是为了能在离线测试里喂 HTML 夹具 ——
    只认 b_algo、跳过 bing 自家链接这两条规则是事故里定下来的，
    没有测试盯着，下次改版就又回去了。
    """
    if not html:
        return []

    out = []
    # 每个结果块以 <li class="b_algo" 开头，切块后在块内解析
    chunks = re.split(r'<li[^>]+class="[^"]*b_algo[^"]*"', html)[1:]
    for ch in chunks[:max(limit * 2, 12)]:
        m_link = re.search(r'<h2[^>]*>\s*<a[^>]+href="(https?://[^"]+)"', ch)
        if not m_link:
            continue
        href = m_link.group(1)
        if not _is_usable_url(href):
            continue
        m_title = re.search(r'<h2[^>]*>\s*<a[^>]*>(.*?)</a>', ch, re.DOTALL)
        title = _clean_text(m_title.group(1)) if m_title else ""
        m_sn = re.search(r'<p[^>]*>(.*?)</p>', ch, re.DOTALL)
        snippet = _clean_text(m_sn.group(1)) if m_sn else ""
        if not title and len(snippet) < 20:
            continue
        # 日期常在摘要前面挂着（「2026年9月23日 · 全新版本已推出」），
        # 也常单独出现在 b_algoSlug / news_dt 里
        date_raw = ""
        m_dt = re.search(r'class="[^"]*(?:news_dt|b_algoSlug|b_attribution)[^"]*"[^>]*>(.*?)</',
                         ch, re.DOTALL)
        if m_dt:
            date_raw = _clean_text(m_dt.group(1))
        out.append(search_core.make(
            url=href, title=title, snippet=snippet,
            date=search_core.parse_date(date_raw) or search_core.parse_date(snippet),
            source="bing", rank_in_source=len(out) + 1,
        ))
        if len(out) >= limit:
            break
    return out


def _bing_search(query: str, limit: int = 8):
    """抓 Bing SERP 再解析。"""
    q = urllib.parse.quote(query)
    url = f"https://www.bing.com/search?q={q}&setlang=zh-cn&count=20"
    return _parse_bing(_fetch_html(url), limit)


def _parse_ddg(html: str, limit: int = 8):
    """DuckDuckGo html 端点 → [SearchResult]。**纯函数，不联网**。

    链接是跳转壳（`/l/?uddg=<编码真链>`），要抠 uddg 参数还原。
    """
    if not html:
        return []

    out = []
    chunks = re.split(r'<div[^>]+class="[^"]*result[^"]*"', html)[1:]
    for ch in chunks[:max(limit * 2, 12)]:
        m_link = re.search(r'class="result__a"[^>]*href="([^"]+)"', ch)
        if not m_link:
            continue
        href = m_link.group(1)
        m_real = re.search(r'uddg=([^&]+)', href)
        link = urllib.parse.unquote(m_real.group(1)) if m_real else href
        if not _is_usable_url(link):
            continue
        m_title = re.search(r'class="result__a"[^>]*>(.*?)</a>', ch, re.DOTALL)
        title = _clean_text(m_title.group(1)) if m_title else ""
        m_sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', ch, re.DOTALL)
        snippet = _clean_text(m_sn.group(1)) if m_sn else ""
        if not title and len(snippet) < 20:
            continue
        out.append(search_core.make(
            url=link, title=title, snippet=snippet,
            date=search_core.parse_date(snippet), source="ddg",
            rank_in_source=len(out) + 1,
        ))
        if len(out) >= limit:
            break
    return out


def _duckduckgo_search(query: str, limit: int = 8):
    """抓 DDG html 端点再解析。不接进主链（实测每次吃满超时）。

    ★ 2026-10-23：超时从 15 缩到 6。实测这个源在本机**每次**都吃满超时
      （连续测三次都是 10.0s 返回空），也就是说它现在是纯负担：
      进到降级链就等于白等 10 秒，而用户那边是在群里等回复。
      缩短超时是为了让失败快点过，不是指望它成功。
    """
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    return _parse_ddg(_fetch_html(url, timeout=6), limit)


def _parse_so(html: str, limit: int = 8):
    """360 搜索 HTML → [SearchResult]。**纯函数，不联网**。

    ★ 2026-10-24 重写。前两版都取 `<a href>`，而 360 的 href **全是跳转壳**
      （`https://www.so.com/link?m=…`），`_is_usable_url` 的 `_SELF_HOSTS`
      里有 "so.com"，于是每一条都被过滤 —— 实测 7 个结果块、0 条通过，
      这个引擎等于不存在。

      真实目标地址在锚点的 **`data-mdurl`** 属性里：

          <h3 class="res-title">
            <a href="https://www.so.com/link?m=…"
               data-mdurl="https://ys.mihoyo.com/?from_channel=adbdpz" …>

      这个做法参考了两个开源实现（2026-10-24 查证）：
        · dsh-web-search-so360 —— "优先读锚点上的 data-mdurl（360 内嵌的
          真实目标地址），缺失时回退抓取 /link?m=… 中转页并解析其 JS 跳转"
        · TongYi-Lite 的 websearch_direct 记录 —— "360 结果块内嵌
          data-mdurl 真实 URL"；并实测百度会进惩罚态、搜狗连发 4~5 次触发
          反爬，只有 360 与必应 CN 能稳定连发

      **不实现"回退解跳转壳"**：实测把 `/link?m=…` 请求下来，页内
      `location.replace` 指向的是 `https://hao.360.com/?src=hj_so`（广告兜底页），
      不是目标文章。解出来是假地址，比没有更糟 —— 多一次请求还污染结果。
      缺 data-mdurl 的块直接跳过（实测 49 个块里 38 个有，每查询至少 3 条）。

      位置分用 `data-result-pos`（引擎给的真实名次），不是枚举顺序 ——
      跳过若干块之后，枚举序号会让后面的结果冒充第 1 名。
    """
    if not html:
        return []

    out = []
    chunks = re.split(r'<li[^>]+class="[^"]*res-list[^"]*"', html)[1:]
    if not chunks:
        # 360 改版后有时用 h3 直接包结果，退一步按 h3 切
        chunks = re.split(r'<h3[^>]*>', html)[1:]

    for ch in chunks[:max(limit * 3, 16)]:
        # 真实地址优先取 data-mdurl；没有就是广告位/站内页，跳过
        m_md = re.search(r'data-mdurl="([^"]+)"', ch)
        url = m_md.group(1).replace("&amp;", "&") if m_md else ""
        if not url:
            continue
        # 站内页（image.so.com / hao.360.com 之类）仍然滤掉
        if not _is_usable_url(url):
            continue
        m_title = re.search(r'<h3[^>]*>(.*?)</h3>', ch, re.DOTALL)
        if not m_title:
            m_title = re.search(r'<a[^>]*>(.*?)</a>', ch, re.DOTALL)
        title = _clean_text(m_title.group(1)) if m_title else ""
        m_sn = re.search(r'<p[^>]*class="[^"]*(?:res-desc|res-rich|mh-txt)[^"]*"[^>]*>(.*?)</p>',
                         ch, re.DOTALL)
        if not m_sn:
            m_sn = re.search(r'<p[^>]*>(.*?)</p>', ch, re.DOTALL)
        snippet = _clean_text(m_sn.group(1)) if m_sn else ""
        if not title and len(snippet) < 20:
            continue
        # 名次用引擎给的 data-result-pos，不是枚举序号：
        # 跳过广告之后枚举序号会让第 4 名冒充第 1 名，位置分就虚高了
        m_pos = re.search(r'data-result-pos="(\d+)"', ch)
        pos = int(m_pos.group(1)) if m_pos else (len(out) + 1)
        out.append(search_core.make(
            url=url, title=title, snippet=snippet,
            date=search_core.parse_date(snippet), source="so",
            rank_in_source=pos,
        ))
        if len(out) >= limit:
            break
    return out


def _so_search(query: str, limit: int = 8):
    """抓 360 SERP 再解析。"""
    url = f"https://www.so.com/s?q={urllib.parse.quote(query)}"
    return _parse_so(_fetch_html(url), limit)


# ══════════════════════════════════════════════════════════════
# 结果清洗与合并（2026-10-23）
# ══════════════════════════════════════════════════════════════
#: 搜索引擎自家的域名。它们是搜索页本身，不是结果，抓进来只会是导航。
#: 按**域名后缀**匹配（`host == s` 或 `host.endswith("." + s)`），
#: 不做整条 URL 的子串匹配 —— 后者会误伤 `example.com/?ref=bing.com` 这种，
#: 而前者天然覆盖所有子域。
#: ★ 2026-10-25 加 "360.com"：原来只有 "360.cn"，于是 `hao.360.com`、
#:   `ranks.hao.360.com` 这类 360 自家导航/榜单页全都漏进结果里，
#:   还占掉了抓正文的名额（实测一条查询的第 3 位就是 ranks.hao.360.com）。
_SELF_HOSTS = (
    "bing.com", "microsoft.com", "google.com", "duckduckgo.com",
    "so.com", "360.cn", "360.com", "sogou.com", "baidu.com",
)
#: 搜索页自身的路径 / 查询特征。这些不是域名，仍按整条 URL 匹配。
_SELF_PATH_HINTS = ("javascript:", "void(0)", "/s?q=", "/search?", "/link?url=")


def _is_usable_url(url: str) -> bool:
    """这个 URL 值不值得抓正文。

    判两件事：
      1. 是绝对 http(s) 地址（`javascript:`、相对路径一律不行）
      2. 不是搜索引擎自己的页面（搜索页、跳转壳、导航页）
    域名按后缀匹配：`www.bing.com` / `cn.bing.com` 都命中 "bing.com"。
    """
    u = str(url or "").strip()
    if not u.startswith(("http://", "https://")):
        return False
    low = u.lower()
    if any(h in low for h in _SELF_PATH_HINTS):
        return False
    try:
        host = urllib.parse.urlparse(low).hostname or ""
    except Exception:
        return False
    if not host:
        return False
    for s in _SELF_HOSTS:
        if host == s or host.endswith("." + s):
            return False
    return True


def _url_key(url: str) -> str:
    """URL 归一化，用于去重：去协议、去 www、去追踪参数、去锚点。"""
    try:
        p = urllib.parse.urlparse(str(url or ""))
        host = (p.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = (p.path or "").rstrip("/")
        return f"{host}{path}"
    except Exception:
        return str(url or "")


#: 站点首页的各种写法。`/main/`、`/home`、`/index.html` 都指同一个页面。
_HOME_PATHS = frozenset(("", "/main", "/home", "/index", "/index.html",
                         "/default.html", "/zh", "/cn", "/zh-cn"))


def _is_home_page(url: str) -> bool:
    """这个 URL 是不是站点首页（不管带不带 /main 之类的尾巴）。"""
    try:
        p = urllib.parse.urlparse(str(url or ""))
        return ((p.path or "").rstrip("/").lower() or "") in _HOME_PATHS
    except Exception:
        return False


def _merge_results(batches, limit: int = 8):
    """把多个引擎的结果合并去重 → [SearchResult]。

    ★ 2026-10-23：两处重写。
      ① 原实现是"第一个有结果的引擎就 break"，也就是 Bing 一通就永远不会看
         360。单一来源的偏置会一直存在：同一个查询在 Bing 上偏到音乐，
         在 360 上可能就正常。
      ② 原实现是列表首尾拼接（Bing 8 条 + 360 2 条 = 前 8 条全是 Bing），
         而 `_rank_in_source` 是打分的**主判据**。首尾拼接等于让 360 的第 1 名
         在打分上等同于 Bing 的第 9 名 —— 可它是那个引擎认为最相关的结果。

      所以改成**按位置交错**：先取各引擎第 1 名，再取各引擎第 2 名，依此类推。
      这样"排第几"跨引擎可比，打分才站得住。

    去重只跟**别的引擎**比，不跟同批次比 —— 同一个引擎返回的条目本来就不会
    重复；而同一站点的列表页（「第1名…」「第2名…」）句式高度雷同，
    跨条目比较会把不同结果误删。要解决的只是"两个源抓了同一篇文章"。

    ★ 2026-10-23 追加：**站内同标题也要去重**。实测搜「原神最新版本」，
      Bing 同一页给了两条：

          https://ys.mihoyo.com/main/   → key = ys.mihoyo.com/main
          https://ys.mihoyo.com/        → key = ys.mihoyo.com

      标题完全一样（都是官网首页），URL key 不同，两个都进了结果。
      这是同一个页面，占掉两个名额、并且在给模型的文本里出现两遍。
      判据：同域名 **且** 标题相同（或一方是首页）→ 判重。
      URL key 那条路只看路径，天生拦不住这种。
    """
    lists = [list(b or ()) for b in (batches or ()) if b]
    if not lists:
        return []

    seen_url = set()
    out = []
    depth = max((len(x) for x in lists), default=0)
    for i in range(depth):
        for batch in lists:
            if i >= len(batch):
                continue
            it = batch[i]
            if not isinstance(it, dict):
                continue
            u = str(it.get("url") or "")
            if not _is_usable_url(u):
                continue
            key = _url_key(u)
            if key in seen_url:
                continue
            text = str(it.get("title") or "") + " " + str(it.get("snippet") or "")
            if len(text.strip()) < 12:
                continue
            if _same_page(it, out):
                continue
            # 跨源判重：同一篇文章的标题/摘要在不同源会被截成不同长度
            others = [str(x.get("title") or "") + " " + str(x.get("snippet") or "")
                      for x in out if x.get("source") != it.get("source")]
            if _looks_same(text, others):
                continue
            seen_url.add(key)
            out.append(it)
            if len(out) >= limit:
                # 交错会打乱"位置"的含义，所以出队前重排一次原始位置：
                # 以在本引擎内的真实序号为准，而不是合并后的顺序
                return out
    return out


def _same_page(it, pool) -> bool:
    """这条和池子里已有的某条是不是**同一个页面**（不管 URL 写得多不一样）。

    两种情况算同一个页面：
      · 同域名 + 标题完全相同。站内两个入口指向同一份内容。
      · 同域名 + 两方都是首页。`/main/` 和 `/` 是同一个首页。

    只比同域名的东西 —— 不同站点的同名标题是不同页面（「公司简介」到处都是）。
    """
    try:
        my_host = search_core.domain_of(it.get("url"))
        my_title = re.sub(r"\s+", "", str(it.get("title") or ""))
        if not my_host:
            return False
        for x in (pool or ()):
            if search_core.domain_of(x.get("url")) != my_host:
                continue
            other_title = re.sub(r"\s+", "", str(x.get("title") or ""))
            if my_title and my_title == other_title:
                return True
            if _is_home_page(it.get("url")) and _is_home_page(x.get("url")):
                return True
        return False
    except Exception:
        return False


def _summary_key(text: str) -> set:
    """摘要的比对键：去掉标点和空白后的连续三字组。

    中文里同一句话几乎不会被逐字改写，但会被截在不同位置，
    也会多出「……」「_百度百科」这类尾巴。所以不比整串，比三字组的重合率。
    """
    t = re.sub(r"[\s，。！？、；：,.!?;:~…\-—_（）()【】\[\]\"'“”‘’|/\\]+", "", str(text or ""))
    return {t[i:i + 3] for i in range(len(t) - 2)} or ({t} if t else set())


def _looks_same(text: str, pool) -> bool:
    """这条摘要和池子里已有的某条是不是同一件事。

    ★ 2026-10-23：原实现比前 40 字前缀。实测两个源抓同一条新闻时，
      摘要开头几乎从不逐字一致（一个带站点名、一个带栏目标签），
      前缀比对等于没做去重 —— 同一篇内容在结果里出现两遍，白占一次抓正文的名额。
      改成三字组重合：前缀相同或重合率够高，满足其一即判重。
    """
    t = str(text or "").strip()
    if not t:
        return True
    a = _summary_key(t)
    if not a:
        return False
    for x in (pool or ()):
        if t[:40] == x[:40]:
            return True
        b = _summary_key(x)
        if not b:
            continue
        # 重合率按较短那条算：长的被截出来一半，也还是同一件事
        if len(a & b) / min(len(a), len(b)) >= 0.6:
            return True
    return False


#: 页面容器标签。正文永远在其中一个里，导航树也是 —— 区别在块本身的成分。
_BLOCK_TAG = re.compile(r'<(?:div|article|section|main|td)\b', re.IGNORECASE)
#: 用于回溯子树边界：同时认得开标签和闭标签。
_SUBTREE_TAG = re.compile(r'<(/?)(?:div|article|section|main|td)\b[^>]*>',
                          re.IGNORECASE)
#: 抽正文时整段丢弃的标签。它们从不承载正文。
_DROP_TAGS = re.compile(
    r'<(script|style|noscript|nav|header|footer|aside|form|iframe)\b[^>]*>.*?</\1>',
    re.DOTALL | re.IGNORECASE)


def _subtree(html, start, cap=12000):
    """从 start 处的容器开标签起，截到**这个容器真正闭合**的位置。

    ★ 2026-10-23 实测缺陷：这一版之前是 `html[start:start + 12000]` ——
      一个定长窗口，**没有块边界**。后果很隐蔽：

        搜「B站」，胜出的是 `<div class="left-entry__item home-page-entry">`
        （一个导航项）的 12000 字窗口。那个窗口早就越过了导航项本身，
        把后面的兄弟节点、整片页面全吞进来了。于是「一个导航项的子树」
        和「整页」被统计成同一串文字，链接密度 0.01、长度 3329 ——
        打分上完美冒充正文。

      定长窗口的问题不是"窗口太小"，而是**拿邻居的量去评价自己**：
      任何一个靠前的浅层节点都会把整页内容算成自己的。
      修法是回溯标签深度：div/article/section/main/td 这几类没有自闭合形式，
      数开闭标签就能精确找到闭合点。
    """
    depth = 0
    limit = start + cap * 4          # 搜寻上限，防病态页面（未闭合）扫到底
    for m in _SUBTREE_TAG.finditer(html, start, limit):
        if m.group(1):               # 闭标签
            depth -= 1
            if depth <= 0:
                return html[start:m.end()]
        else:                        # 开标签
            depth += 1
    return html[start:start + cap]   # 未正常闭合（截断页）→ 退回定长


#: 正文至少要凑出几个带句末标点的句子。判据在下文 _sentence_count 里解释。
MIN_SENTENCES = 2


def _sentence_count(text):
    """数这段文字里有几个**以句末标点收尾**的片段。

    ★ 2026-10-23：这是第四个正文判据，前三个都失败了（链接密度、片段均长、
      句末标点密度作为**排序权重**使用），这一条与前三个不同 ——
      前三个都在给块打分，它们失败是因为导航块的分数也能很高。
      这一条是**准入门槛**：整页凑不出两句完整的话，那就不是内容页。

      实测（2026-10-23，`len` / 带句末标点的句子数）：

        B站首页      2289 字 / 0 句   → 挡掉
        BWiki 首页    240 字 / 0 句   → 挡掉
        网易云弹窗    190 字 / 0 句   → 挡掉
        公告（真正文） 48 字 / 3 句   → 放行
        简介（真正文） 41 字 / 2 句   → 放行
        MaiBot 文档   228 字 / 7 句   → 放行

      注意「段落长度 ≥15 字」这个过滤条件**必须去掉**。第一版加了它，
      结果是 48 字的公告里三句全被滤掉、"成句数"变成 1，和导航页无法区分 ——
      判据本身没错，是被那个看起来合理的过滤条件毁了。
      短公告的句子就是短，但它有句号，导航条目没有句号。

      门槛定 2：导航页一个句号都没有，正文页最少也有 2 个。两边都不挤。
    """
    s = str(text or "")
    # 直接数句末标点：一个句号就是一句话的结束。
    # 不用 split 再过滤，因为任何过滤条件都会重新引入"多长才算一句"的问题。
    return sum(1 for c in s if c in "。！？!?")


def _looks_like_code_block(text):
    """这个块是不是前端模板/脚本残留。

    判据与 search_core._looks_like_code 一致，但这里独立一份是**故意的**：
    web_access 不该在抓取阶段 import search_core 的格式化逻辑（分层会乱）。
    两处判据要一起改 —— 所以判据本身写成同一串标记，改的时候 grep 得到。

    ★ 2026-10-23 补：模板定界符（`{/`、`{if `、`{list `、`{each `、`{else}`）。
      漏了这批的直接后果：网易云首页抽出的 760 字弹窗里有 `{/if}` 和
      `{list suggests as item}`，按老表一个标记都不命中，整段蒙混过关。
      散文里不会出现这些符号，任何**一个**命中就足以判死。

    ★ 2026-10-23 撤掉一条：原本还有「`</` 出现 ≥3 次判死」。删了，因为
      讲 HTML 的技术文章里 `</div>` 本来就该出现，实测把正常正文判成了代码。
      而且这条判据从一开始就多余 —— 标签残留是 `_clean_text` 的责任，
      它剥干净了这里就看不见 `</`；它没剥干净，说明是实体转义问题，
      也不是"像代码"。**一项职责只能有一个地方负责。**
    """
    s = str(text or "")
    if not s:
        return False
    # 模板定界符：散文里不可能出现，命中一个就够。
    for t in ("{/", "{if ", "{list ", "{each ", "{else}"):
        if t in s:
            return True
    markers = ("${", "{{", "}}", "escape(", "function(", "=>{", "===", "!==",
               "className", "style=\"", "var ", "const ", "let ", "document.",
               "window.", "undefined", "return ")
    hit = sum(1 for m in markers if m in s)
    return hit >= 3 or s.count("${") >= 3 or s.count("{if ") >= 2


def _block_text(html, budget=3000):
    """从整页 HTML 里挑出「正文那个块」，而不是把整页文字都倒出来。

    ★ 2026-10-23：这一版换掉了原来的 `_nav_heavy`（先抽全文再判断是不是导航）。
      为什么换 —— 实测三个判据都不成立：

        · **链接密度**：MaiBot 文档首页 0.21，BWiki 导航页 0.52。
          正常文档站本来就有大量链接（侧边栏、下一页），数字上比导航页还低，
          区分不了。
        · **片段均长**（导航项短、正文长）：导航页 5.7~7.3，正文页 28.3，
          看起来能用 —— 但 api-docs.deepseek.com 的正常文档页也是 5.7
          （技术文档全是短句、代码块、列表）。这一刀会砍掉整个文档类站点。
        · **句末标点密度**：导航页恰好没有标点，正文页有 —— 方向对，
          但技术文档的表格和列表也没有，同样误伤。

      根因是这些判据都在**看文本长相**，而导航和正文的文本长相本来就重叠。
      换个角度：不要去判断「这段是不是导航」，而是**直接找正文在哪里**。
      正文容器的特征是「文字多、链接少」，用 `文本量 × (1 - 链接密度)²`
      给每个 div/article/section 打分，取最高的那个块。
      这个思路是 Readability 的做法，实测有效：

        MaiBot 文档首页 → dens=0.19，拿到的是产品介绍正文
        腾讯新闻正文页 → dens=0.00，拿到的是完整文章
        BWiki 版本历史 → 抽出来的块里导航被压掉，不再是一屏菜单

      代价是比直接 `_clean_text(html)` 多一次正则扫描（约 10ms），
      但抓正文本来就是网络为主，这点开销可以忽略。
    """
    html = _DROP_TAGS.sub('', str(html or ''))
    if not html:
        return ""

    best_score = 0.0
    best_text = ""
    # 只扫前 400 个容器起点：正文总在页面靠前的位置，扫全文只是白花时间
    for m in list(_BLOCK_TAG.finditer(html))[:400]:
        chunk = _subtree(html, m.start())
        text = _clean_text(chunk)
        if not text:
            continue
        # ★ 2026-10-23：下限从「200 字」改成「120 字，或者能凑出 2 句完整话」。
        #   纯长度门槛会误杀短公告：一条 126 字的维护通知有 5 个句号，
        #   是货真价实的正文，但按老门槛直接被丢。
        #   而骨架残留在另一侧 —— 它没有句号，所以由成句门槛挡住，
        #   不需要长度门槛再挡一次。
        if len(text) < 120 and _sentence_count(text) < MIN_SENTENCES:
            continue
        links = "".join(_clean_text(x) for x in
                        re.findall(r'<a\b[^>]*>(.*?)</a>', chunk,
                                   re.DOTALL | re.IGNORECASE))
        density = len(links) / max(1, len(text))
        # 平方是为了让"链接占比高"的块掉得足够狠：
        # 0.5 密度 → 只剩 1/4 分，0.7 密度 → 只剩 1/10 分
        score = len(text) * (1.0 - density) ** 2
        if _looks_like_code_block(text):
            # ★ 模板/脚本残留块有个讨厌的性质：文字多、链接少，
            #   在打分上正好是"正文"。网易云音乐那种页面就是靠这个骗过判据的。
            #   所以要在打分之前直接排除，不是打完分再挑次优。
            continue
        # ★ 成句数量作为准入门槛（不是排序权重）。理由见 _sentence_count。
        #   B站首页那 3000 字导航能拿到最高分，是因为它够长、链接密度 0.00 ——
        #   SPA 用 div 做导航，`<a>` 里一个字都没有，密度这个信号在这里彻底失效。
        #   成句数不看链接，只看"这段文字有没有在说事"，是唯一没被绕过的判据。
        if _sentence_count(text) < MIN_SENTENCES:
            continue
        if score > best_score:
            best_score, best_text = score, text

    if not best_text:
        # 一个正文块都没找到 —— 两种可能，必须分开对待：
        #   · 页面根本没有容器（老式纯 <p>）→ 退回整页文本
        #   · 有容器但全被判成模板/脚本/太短 → 这页没有可读正文，返回空
        # ★ 2026-10-23 实测缺陷：原来不分情况一律退回整页。网易云首页所有块
        #   都被模板判据排除后，兜底把整页 3000 字倒了出来 —— 那不是"退而求其次"，
        #   那是把**导航菜单**当成正文交给模型。空着比给错的强。
        if _BLOCK_TAG.search(html):
            return ""
        whole = _clean_text(html)
        return whole[:budget] if len(whole) > 200 else ""
    return best_text[:budget]


def _attach_contents(items, max_pages: int = 3, timeout: int = 8):
    """给排在前面的几条结果抓正文，**写回它自己的 content**。

    ★ 2026-10-23：原来是「抓 3 个 URL → 回一串正文文本」，正文和结果是脱钩的
      —— 看起来有 3 段正文，但不知道哪段属于哪条结果。模型拿到的是一堆
      无归属的文本，只能瞎猜哪段在说哪件事。这就是自己扒网页达不到
      DeepSeek 水平的关键缺口之一（那边 content 字段直接挂在结果上）。

    并发抓：串行抓 3 篇要 15 秒（一篇慢站就吃满超时，后面那篇还没开始），
    而这是在群里等回复。并发之后总耗时 ≈ 最慢那一篇。
    """
    items = [dict(x) for x in (items or ())]
    targets = []
    for i, it in enumerate(items):
        if len(targets) >= max_pages:
            break
        if _is_usable_url(it.get("url")):
            targets.append((i, it.get("url")))
    if not targets:
        return items

    def _one(url):
        """返回 (正文, 失败原因)。原因非空才记一行 —— 静默失败没法排查。

        实测（2026-10-25，5 个查询共 15 条）：只有 7 条抓到正文（47%）。
        B站、知乎整类抓不到（反爬）；360 自家导航页能抓到但没正文。
        原来这些都返回空串、不留痕迹，看起来像"没配这个功能"。
        """
        try:
            html = _fetch_html(url, timeout=timeout)
            if not html:
                return "", "抓不到页面"
            # 正文由 _block_text 挑块，这里不再单独去 nav/aside ——
            # 去标签的责任已经收进 _DROP_TAGS，两处都做会漏掉新的标签名
            txt = _block_text(html)
            return txt, ("" if txt else "页面无正文块")
        except Exception as e:
            return "", type(e).__name__

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(targets)) as ex:
            got = list(ex.map(_one, [u for _i, u in targets]))
    except Exception:
        got = [_one(u) for _i, u in targets]

    fails = []
    for (idx, _u), (t, why) in zip(targets, got):
        if t:
            items[idx]["content"] = t
        else:
            host = ""
            try:
                host = urllib.parse.urlparse(items[idx].get("url") or "").hostname or ""
            except Exception:
                pass
            fails.append(f"{host}（{why}）")
    if fails:
        # 一行汇总，不逐条刷屏。这条日志的意义：抓正文这套机制坏了能看见，
        # 而不是等模型回答偏了再回头猜。
        print(f"[web_access] 正文抓取失败 {len(fails)}/{len(targets)}："
              + "、".join(fails[:3]))
    return items


def check_connectivity(timeout: int = 4) -> dict:
    """轻量连通性检测：尝试访问搜索引擎入口，用于 web_search 插件连接监测。

    返回 {"ok": bool, "detail": str}。不抓搜索结果，只做一次短超时访问。
    """
    urls = [
        "https://www.bing.com",
        "https://html.duckduckgo.com/html/?q=test",
        "https://www.so.com",
    ]
    ok_host = ""
    for url in urls:
        try:
            import urllib.parse
            host = urllib.parse.urlparse(url).netloc
        except Exception:
            host = url
        html = _fetch_html(url, timeout=timeout)
        if html:
            ok_host = host
            break
    if ok_host:
        return {"ok": True, "detail": f"网络可达（{ok_host}）"}
    return {"ok": False, "detail": "无法访问任一搜索源（Bing/DDG/360）"}


# ★ 2026-10-23：这里原本有 _NAV_MARKERS + assess_quality()，判断「这批摘要够不够
#   回答」，判据是「摘要平均字数」和「摘要里有没有导航文案」。
#   现在删掉了，两个原因：
#     ① 它只能看摘要长度和关键词 —— 因为扒 HTML 拿不到别的信号。统一结果层
#        （search_core.quality_of）能看 score / 正文量，判据更硬，两套并存会吵架。
#     ② 它真正想拦的是「抓到页面骨架」。那件事已经由 _nav_heavy 在**抓正文时**
#        做掉了（不进正文 = 不占预算），不必等到摘要层再判一次。
#   质量判定和话术的唯一权威源是 search_core（quality_of / QUALITY_HINT）。


def _own_search(query: str, nq: str):
    """自己的搜索：Bing + 360 并发 → 合并去重 → 补相关分 → 抓正文。

    返回 [SearchResult]，失败返回空列表。**不返回是否「成功」** ——
    空列表就是空列表，由调用方决定要不要兜底。
    """
    batches = []
    for engine in (_bing_search, _so_search):
        try:
            got = engine(nq, 8)
        except Exception as e:
            print(f"[web_access] {engine.__name__} 失败: {str(e)[:80]}")
            got = []
        if got:
            batches.append(got)
    if not batches:
        return []

    merged = _merge_results(batches, limit=8)
    if not merged:
        return []

    # 补本地相关分。DeepSeek 有真 rerank，这里只能按词面重合近似 ——
    # 精度差一截，但足以把明显不相关的压下去。
    merged = search_core.fill_scores(merged, nq)

    # 只给排在前面的几条抓正文：后面那些本来也进不了给模型的文本
    ranked = search_core.rank(merged, nq)
    head = ranked[:3]
    head = _attach_contents(head, max_pages=3)
    rest = ranked[3:]
    return head + rest


def _search_via_deepseek(query: str, nq: str):
    """DeepSeek 原生搜索。返回 (results, handled)。

    handled=False 表示这条路没走通（没 key / 网络错 / 429），调用方该用别的路。
    handled=True 且 results 为空表示「确实没有结果」。

    ★ 2026-10-23：转换工作交给 ds_search.to_core_list。这里原来自己拼
      `dict | dict` 去合并字段，可读性差且容易漏字段 —— 现在只调一次。
    """
    try:
        import ds_search
    except Exception:
        return [], False

    try:
        if not ds_search.available():
            return [], False
        results = ds_search.search(nq, count=8, ori_question=query)
    except Exception as e:
        print(f"[web_access] DeepSeek 搜索未走通: {str(e)[:100]}")
        return [], False

    return ds_search.to_core_list(results, source="ds"), True


def _query_variants(nq):
    """由长到短的查询变体，用于「不够好就砍短再试」。

    只**砍尾部**，不重排、不改写、不同义替换 —— 检索里唯一能确定安全的
    操作就是去掉词，任何改写都可能引入原本没有的意思。

    「养大肥鱼 很火 游戏」→ ['养大肥鱼 很火 游戏', '养大肥鱼 很火', '养大肥鱼']

    没有空格的中文串只返回自己（砍哪一刀都可能把实体劈开）。
    实测查询都是工具调用时按空格分开传进来的，所以这条路用得很少。
    """
    parts = [p for p in re.split(r"\s+", str(nq or "").strip()) if p]
    if len(parts) <= 1:
        return [nq] if nq else []
    out = []
    for i in range(len(parts), 0, -1):
        v = " ".join(parts[:i])
        # 剩一个词就停：再砍下去只剩单字，检索必然跑偏（实测「帮」返回字典释义）
        if len(v) < 2:
            break
        if v not in out:
            out.append(v)
    return out


def _render(items, nq, query, via):
    """统一的输出拼装。via 只用于日志，不进给模型的文本。"""
    key_note = "" if nq == query else f"（检索词：{nq}）"
    body = search_core.format_for_model(items, nq)
    if not body:
        return ""
    parts = [f"【{nq}】搜索结果{key_note}：", body]
    hint = search_core.quality_hint(search_core.quality_of(items))
    if hint:
        parts.append("\n\n" + hint)
    return "\n".join(parts)


def search_web(query: str, retries: int = 2) -> str:
    """统一搜索入口。

    流程（2026-10-23 二次重排，用户要求以自家搜索为主）：
      1. 提炼查询词 —— 整句直接搜会把虚词当主体，见 normalize_query
      2. **自家抓取**（Bing + 360 → 合并 → 补分 → 抓正文）
      3. 自家没出结果才用 DeepSeek 兜底
      4. 同一 query 30 秒内缓存

    顺序为什么反过来：
      自家这条路零搜索费，只有上下文 token；DeepSeek 每千次 5 元。
      在结果质量接近的前提下，先用免费的。
      但前提是质量真的接近 —— 所以结果对象补了三个原本缺失的信号：
      时间（从摘要开头抽）、权威性（按域名估）、相关性（词面重合）。
      没有这三样，自家结果排不了序，也就谈不上"接近"。

    天气/股票由上层（bot_core.search_web）单独走专用 API，这里只做通用兜底。
    """
    if not query or not query.strip():
        return "搜索关键词为空。"

    cached = _get_cached(query)
    if cached:
        return cached

    nq = normalize_query(query)

    # ── 主路径：自家抓取（不够好就先砍短再免费试一次）──
    #
    # ★ 2026-10-25 加「砍短重试」。实测这是「问题与答案不符合」的主要机制：
    #   用户的口语修饰词会把实体淹掉，引擎于是去买不相干的东西。
    #
    #     恋人不行 这个动画 好看吗   0.99 → 李荣浩《恋人》MV
    #     恋人不行                  1.87 → 我怎么可能成为你的恋人（正确）
    #     推荐几个便宜好用的键盘      0.93 → 人民日报推荐100本书单
    #     便宜好用的键盘             1.68 → 什么值得买键盘推荐（正确）
    #     养大肥鱼 很火 游戏          1.72 → 桌宠页
    #     养大肥鱼                  1.95 → b站养deepseek大肥鱼（正确）
    #
    #   「这个动画」「推荐几个」这类词在检索里是噪声：它们描述的是**用户想
    #   要什么形式**，不是**要找什么东西**。Bing 不会区分，只能按字面匹配。
    #
    #   所以：从尾部逐个砍词，每砍一次免费搜一遍，一旦够好就停。
    #   代价是多的抓取请求（每次 ~300ms），比直接走 DeepSeek（¥0.005）便宜；
    #   而且只有**判不够好**时才砍，好结果不受影响。
    items, nq_used, verdict = [], nq, "empty"
    best = None          # (分数, items, nq) —— 全都不够好时留最好的那份
    for _v in _query_variants(nq):
        _items = _own_search(query, _v)
        if not _items:
            continue
        _vq = search_core.quality_of(_items)
        _score = _vq.get("top_score") or 0.0
        if best is None or _score > best[0]:
            best = (_score, _items, _v)
        if _vq.get("verdict") == "good":
            items, nq_used, verdict = _items, _v, "good"
            break
    if verdict != "good" and best is not None:
        # 没有变体够格 —— 用分数最高的那份（可能是缩短过的，也可能就是原句）
        _score, items, nq_used = best
        verdict = search_core.quality_of(items).get("verdict") or "thin"

    text = _render(items, nq_used, query, "own") if items else ""

    # ── 自家结果「能用」才停手 ──
    # ★ 2026-10-24 修。原来只判 `if not text` —— 也就是**只有一条结果都没有**
    #   才兜底。而 _render 对质量差的结果照样产出非空文本，只在末尾附一句
    #   「结果质量：偏薄」。于是 quality_of 算出了 thin、甚至把理由写进了给
    #   模型的话里，路由决策却完全不看它 —— 同一件事两处判据，互不相认。
    #
    #   现在：verdict 不是 good 就走兜底。兜底拿不到结果时**保留自家那份**
    #   —— thin 的结果也比"没搜到"有用，不能因为追求质量反而什么都不给。
    if verdict != "good":
        ds_items, handled = _search_via_deepseek(query, nq)
        if handled and ds_items:
            ds_text = _render(ds_items, nq, query, "ds")
            if ds_text:
                _set_cache(query, ds_text)
                return ds_text

    if not text:
        result = f"搜索「{nq}」未找到相关结果。"
        _set_cache(query, result)
        return result

    _set_cache(query, text)
    return text


__all__ = ["search_web", "check_connectivity", "normalize_query"]
