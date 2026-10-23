# -*- coding: utf-8 -*-
"""ds_search.py — DeepSeek 原生网页搜索 / 抓取。

2026-10-23 起，搜索的主路径走这里，`web_access` 的 Bing/360 抓取降为兜底。

为什么换：
    DuckDuckGo/Bing/360 那条链的本质是**扒搜索页 HTML**。它有三个绕不过去的
    毛病，都不是调参能解决的：

      1. 没有权威性信号。扒回来的东西只有标题和摘要，判断不了
         「这是官网」「这是三年前的帖子」。
      2. 没有时间信号。搜「原神最新版本」会把 2024 年的答案和今天的一起返回。
      3. 正文要自己再抓一遍。3 个 URL 并发抓，一篇死站就吃掉 8 秒。

    DeepSeek 的 Search 接口直接给结构化结果：`rerank_score`（相关性）、
    `authority`（权威性）、`date`（发布时间）、`summary`（给模型读的长摘要）、
    `content`（正文，可选）。这些东西原本要自己算，现在白拿。

接口（https://api-docs.deepseek.com/zh-cn/guides/search/）：
    POST https://api.deepseek.com/v1/tools/search
    POST https://api.deepseek.com/v1/tools/fetch

计费：按请求次数，每 1000 次 5 元。**无结果、失败、超时不计费**，
所以「搜不到就重试」不会白花钱。相对模型 token 费用可以忽略：
一次搜索 0.005 元，而单次对话本身约 0.003 元 —— 搜索贵，但要联网的时候
本来也躲不掉。

失败时的行为：返回 None，由调用方决定是降级到 HTML 抓取还是直接说没查到。
这里不吞异常、也不自己降级 —— 降级策略属于调用方。
"""

import json
import os
import urllib.error
import urllib.request

#: 官方端点。配置里给的是 /chat/completions，这里按根域名拼。
DS_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
DS_FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"

#: 单次搜索的返回条数。5 条足够回答一个问题，再多是白占 token。
DEFAULT_COUNT = 5

#: rerank_score 达到这个值才算「优质结果」（官方口径：≥1.5）。
GOOD_SCORE = 1.5

#: 超过这个时间就放弃。群聊里等回复，超过 8 秒用户已经走了。
DEFAULT_TIMEOUT = 8

# ── 上下文预算 ────────────────────────────────────────────
#  ★ 2026-10-23 实测：官方 `summary` 号称 600~800 字，实际中位数 1300 字，
#    最长见到 2461 字。5 条全带上，格式化后是 6000~8600 字。
#    而群聊里要回答的话通常不到 10 个字 —— 为了答「7.1」塞进去八千字原文，
#    每次搜索都要多付约 3 倍的 input token，而且长上下文本身会拉低回复质量
#    （模型被原文带着走，开始大段复述官方公告）。
#
#    所以要截。但截哪里、截多少不能拍脑袋：
#       · 只截 summary，不截标题 —— 标题本身就是答案的高密度形态
#       · 前 3 条给足（它们命中了检索意图），后面的只留一句
#       · 总预算兜底，防止个别超长 summary 把盘子撑爆
DEFAULT_MAX_ITEMS = 3
SUMMARY_BUDGET = 500      # 单条 summary 的字数上限
TOTAL_BUDGET = 3000       # 整段格式化结果的字数上限


class DSSearchError(Exception):
    """搜索/抓取失败。`retryable` 表示重试可能有意义。"""

    def __init__(self, message, status=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _api_key():
    """取 DeepSeek 的 key。

    走 config 的解析链（提供商专属凭据 > 全局凭据 > 环境变量），
    不直接读 bot_config.json —— 明文 key 早就迁进凭据管理器了。
    """
    try:
        from config import get_provider_api_key
        k = get_provider_api_key("deepseek")
        if k:
            return k
    except Exception:
        pass
    try:
        from config import get_api_key
        return get_api_key() or ""
    except Exception:
        return os.environ.get("DEEPSEEK_API_KEY", "") or ""


def available():
    """能不能用这条路径。没有 key 就没得谈。"""
    return bool(_api_key())


def _post(url, payload, timeout=DEFAULT_TIMEOUT):
    key = _api_key()
    if not key:
        raise DSSearchError("未配置 DeepSeek API Key", status=401, retryable=False)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        code = e.code
        # 408/429/5xx 等一会儿可能就好；其余 4xx 重试也没用
        retryable = code in (408, 429) or code >= 500
        detail = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
            detail = (json.loads(body).get("error") or {}).get("message") or body[:120]
        except Exception:
            pass
        raise DSSearchError(f"HTTP {code} {detail}".strip(), status=code, retryable=retryable)
    except urllib.error.URLError as e:
        raise DSSearchError(f"网络不可达：{e.reason}", retryable=True)
    except Exception as e:
        raise DSSearchError(f"{type(e).__name__}: {e}", retryable=True)


def search(query, count=DEFAULT_COUNT, need_content=False, ori_question="",
           timeout=DEFAULT_TIMEOUT):
    """搜一次，返回结构化结果列表。搜不到返回空列表。

    参数：
        query        提炼过的检索词（1~128 字符）
        count        返回条数（1~50）
        need_content 是否让服务端顺手把正文抓回来。开着就不用自己再抓一遍，
                     但请求会慢一些、返回体也大。默认关。
        ori_question 用户原话。官方说这个字段有助于理解搜索意图 ——
                     我们手里正好有原句，白给的信息，传上。

    返回的每一项已做归一化，见 _normalize_item。
    """
    q = str(query or "").strip()
    if not q:
        return []
    q = q[:128]

    payload = {"query": q, "count": max(1, min(50, int(count)))}
    if need_content:
        payload["need_content"] = True
    oq = str(ori_question or "").strip()[:128]
    # 原话和检索词一样时不必重复传，省一次无意义的意图解析
    if oq and oq != q:
        payload["ori_question"] = oq

    data = _post(DS_SEARCH_URL, payload, timeout=timeout)
    raw = data.get("results") or []
    return [_normalize_item(x) for x in raw if isinstance(x, dict)]


def _normalize_item(x):
    """把官方返回的一条结果转成我们内部用的形状。

    统一成 dict，字段名保持原样，只做类型兜底：官方 schema 里
    site_name / date / site_icon 都可能是 null，直接用会在拼接时炸。
    """
    def _s(v):
        return str(v).strip() if v is not None else ""

    try:
        score = float(x.get("rerank_score") or 0)
    except Exception:
        score = 0.0
    try:
        auth = int(x.get("authority")) if x.get("authority") is not None else -1
    except Exception:
        auth = -1

    return {
        "title": _s(x.get("title")),
        "url": _s(x.get("url")),
        "snippet": _s(x.get("snippet")),
        "summary": _s(x.get("summary")),
        "content": _s(x.get("content")),
        "site_name": _s(x.get("site_name")),
        "date": _s(x.get("date")),
        "authority": auth,
        "score": score,
    }


def fetch(url, timeout=DEFAULT_TIMEOUT):
    """抓单个网页正文，返回 {"title": str, "content": str}。

    调官方 Fetch，比自己扒 HTML 稳：站点反爬、编码、正文提取都不用管。
    失败抛 DSSearchError（403 被策略拒、404 没抓到内容）。
    """
    u = str(url or "").strip()
    if not u.startswith(("http://", "https://")):
        raise DSSearchError("URL 必须是 http/https", status=400)
    data = _post(DS_FETCH_URL, {"url": u[:8192]}, timeout=timeout)
    return {
        "title": str(data.get("title") or "").strip(),
        "content": str(data.get("content") or "").strip(),
    }


# ── 对外形状 ──────────────────────────────────────────────
#  ★ 2026-10-23 二次清理：本文件原来还有一整套 _fmt_date / _authority_note /
#    _clip / _sort_key / format_for_model / quality_of、时效加权、下载站降权。
#    它们和 search_core.py 里的同名实现是**两份各自演化的代码** ——
#    改排序规则要改两处，改截断策略要改两处，迟早不同步。
#
#    这是 10-23 已经吃过一次的同类错误：一条规则只能有一个权威源。
#    现在本文件只负责「把官方 API 的东西拿回来并归一化」，
#    排序、截断、判质量、拼话术全部交给 search_core。


def to_core(x, source="ds"):
    """官方返回的一条 → search_core 的统一形状。

    不在模块顶部 import search_core，是为了让本文件能单独拉起来跑
    （比如在命令行里单独探一下接口是否通）。
    """
    import search_core

    d = _normalize_item(x)
    return search_core.make(
        url=d["url"], title=d["title"], snippet=d["snippet"],
        summary=d["summary"], content=d["content"], date=d["date"],
        authority=d["authority"], score=d["score"],
        site_name=d["site_name"], source=source,
    )


def to_core_list(results, source="ds"):
    """整批转换。非 dict 的条目直接丢 —— 官方偶尔会给 null 坑位。"""
    return [to_core(x, source) for x in (results or []) if isinstance(x, dict)]


__all__ = [
    "search", "fetch", "available", "to_core", "to_core_list",
    "DSSearchError", "DEFAULT_COUNT", "DEFAULT_TIMEOUT",
]
