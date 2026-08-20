# -*- coding: utf-8 -*-
"""
web_access.py — 统一联网搜索/抓取模块

目标：
- 减少对单引擎的依赖，降低被拦截概率
- 提供可重试、可降级的网页获取能力
- 供 bot_core.search_web 和 plugins.web_search 共用
"""

import re
import time
import urllib.request
import urllib.parse
import urllib.error
from typing import List, Optional, Tuple

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


def _clean_text(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&ensp;|&nbsp;|&#\d+;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _bing_search(query: str, limit: int = 5) -> Tuple[List[str], List[str]]:
    """Bing HTML 搜索，返回 (summaries, urls)。"""
    q = urllib.parse.quote(query)
    url = f"https://www.bing.com/search?q={q}&setlang=zh-cn"
    html = _fetch_html(url)
    if not html:
        return [], []

    results: List[str] = []
    urls: List[str] = []

    # 模式1
    for m in re.finditer(r'class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>', html, re.DOTALL):
        text = _clean_text(m.group(1))
        if len(text) > 20:
            results.append(text)
        if len(results) >= limit:
            break

    # 模式2
    if len(results) < limit:
        for m in re.finditer(r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', html, re.DOTALL):
            text = _clean_text(m.group(1))
            if len(text) > 20 and text not in results:
                results.append(text)
            if len(results) >= limit:
                break

    # 模式3
    if len(results) < max(3, limit // 2):
        for m in re.finditer(r'<span[^>]*class="[^"]*b_paractl[^"]*"[^>]*>(.*?)</span>', html, re.DOTALL):
            text = _clean_text(m.group(1))
            if len(text) > 30 and text not in results:
                results.append(text)
            if len(results) >= limit:
                break

    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        link = m.group(1)
        if any(skip in link for skip in ['bing.com', 'microsoft.com', 'google.com', 'javascript:', 'void(0)']):
            continue
        if link not in urls:
            urls.append(link)
        if len(urls) >= 10:
            break

    for m in re.finditer(r'data-[uh]ref="(https?://[^"]+)"', html):
        link = m.group(1)
        if any(skip in link for skip in ['bing.com', 'microsoft.com', 'google.com']):
            continue
        if link not in urls:
            urls.append(link)
        if len(urls) >= 10:
            break

    return results, urls


def _duckduckgo_search(query: str, limit: int = 5) -> Tuple[List[str], List[str]]:
    """DuckDuckGo HTML 搜索，返回 (summaries, urls)。"""
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    html = _fetch_html(url)
    if not html:
        return [], []

    results: List[str] = []
    urls: List[str] = []

    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
        href = m.group(1)
        title = _clean_text(m.group(2))
        if title and href and href not in urls:
            results.append(title)
            urls.append(href)
        if len(results) >= limit:
            break

    # 摘要
    if len(results) < limit:
        for m in re.finditer(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL):
            snippet = _clean_text(m.group(1))
            if snippet and len(snippet) > 20:
                results.append(snippet)
            if len(results) >= limit:
                break

    return results, urls


def _so_search(query: str, limit: int = 5) -> Tuple[List[str], List[str]]:
    """360 搜索 HTML，返回 (summaries, urls)。"""
    url = f"https://www.so.com/s?q={urllib.parse.quote(query)}"
    html = _fetch_html(url)
    if not html:
        return [], []

    results: List[str] = []
    urls: List[str] = []

    for m in re.finditer(r'<h3[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
        href = m.group(1)
        title = _clean_text(m.group(2))
        if title and href and href not in urls:
            results.append(title)
            urls.append(href)
        if len(results) >= limit:
            break

    return results, urls


def _extract_page_texts(urls: List[str], max_pages: int = 3, timeout: int = 15) -> List[str]:
    """抓取页面正文，失败则跳过。"""
    texts: List[str] = []
    for url in urls[:max_pages]:
        html = _fetch_html(url, timeout=timeout)
        if not html:
            continue
        html = re.sub(r'<(script|style|nav|header|footer)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = _clean_text(html)
        if len(text) > 200:
            texts.append(text[:3000])
    return texts


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


def search_web(query: str, retries: int = 2) -> str:
    """
    统一搜索入口：
    1. 天气/股票仍优先走专用 API
    2. 普通搜索按 Bing -> DuckDuckGo -> 360 降级
    3. 同一 query 30 秒内缓存
    """
    if not query or not query.strip():
        return "搜索关键词为空。"

    # 天气/股票由上层单独处理，这里只做普通搜索兜底
    cached = _get_cached(query)
    if cached:
        return cached

    # 多引擎降级
    engines = [_bing_search, _duckduckgo_search, _so_search]
    summary = ""
    for engine in engines:
        summaries, urls = engine(query)
        if summaries or urls:
            summary = "\n\n".join(f"{i+1}. {s}" for i, s in enumerate(summaries))
            if urls:
                page_texts = _extract_page_texts(urls)
                if page_texts:
                    summary += "\n\n【页面正文】\n" + "\n---\n".join(page_texts)
            break

    if not summary:
        result = f"搜索「{query}」未找到相关结果。"
        _set_cache(query, result)
        return result

    result = f"【{query}】搜索结果：\n{summary}"
    _set_cache(query, result)
    return result


__all__ = ["search_web", "check_connectivity"]
