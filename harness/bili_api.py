# -*- coding: utf-8 -*-
"""harness/bili_api.py —— B 站只读能力层（2026-10-30）。

取代 `bili_transcript.py`：从"只会读稿"扩成"一个视频能读到的四路"。

    视频信息  x/web-interface/view
    字幕稿    x/player/wbi/v2（WBI 签名）→ subtitle_url → body[]
    弹幕      comment.bilibili.com/{cid}.xml（XML，免签名）
    热评      x/v2/reply?type=1&oid={aid}&sort=2

## 三条真机经验（都是踩出来的）

1. **字幕必须走 WBI 版**。旧接口 `/x/player/v2` 有时也能给出 `subtitle_url`，
   但**没有 auth_key**，下载会 403。WBI 版才带 auth_key。
2. **auth_key 是分钟级有效**：拿到就得马上下载，URL 不能截断、不能存起来以后用。
3. **弹幕 XML 是 UTF-8，但 requests 会猜成 ISO-8859-1** → 中文全变 `ä¸ ç½`。
   必须显式 `r.encoding = "utf-8"`。

## 边界

只读公开接口，不登录也能读到大部分；字幕轨要登录态（cookie）。
拿不到就**返回空数组 + 空字符串**，由调用方如实说，绝不猜内容。
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Dict, List, Optional

API = "https://api.bilibili.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_MIXIN_TAB = (46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
              27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
              37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
              22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52)

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"(?:^|[^0-9A-Za-z])av(\d{3,})", re.I)
_B23_RE = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+", re.I)
_P_RE = re.compile(r"[?&]p=(\d+)")

_HALLUCINATION = ("请不吝点赞", "订阅转发", "字幕由", "amara.org", "字幕志愿者",
                  "本字幕由", "由 Amara")


def envelope(ok: bool, data: Any = None, error: str = "") -> Dict[str, Any]:
    return {"ok": bool(ok), "data": data, "error": str(error or "")}


# ── 链接与签名 ────────────────────────────────────────────────────
def parse_url(text: str) -> Dict[str, Any]:
    """从任意文本里抠出 BV/av 号和分P。抠不到返回空 dict。"""
    s = str(text or "").strip()
    out: Dict[str, Any] = {}
    m = _P_RE.search(s)
    if m:
        try:
            out["p"] = max(1, int(m.group(1)))
        except Exception:
            out["p"] = 1
    bv = _BV_RE.search(s)
    if bv:
        out["bvid"] = bv.group(1)
        return out
    av = _AV_RE.search(s)
    if av:
        out["aid"] = int(av.group(1))
        return out
    short = _B23_RE.search(s)
    if short:
        out["short_url"] = short.group(0)
    return out


def mixin_key(raw: str) -> str:
    raw = str(raw or "")
    return "".join(raw[i] for i in _MIXIN_TAB if i < len(raw))[:32]


def sign_wbi(params: Dict[str, Any], img_key: str, sub_key: str) -> str:
    from urllib.parse import quote
    mix = mixin_key(f"{img_key}{sub_key}")
    items = sorted((str(k), str(v)) for k, v in (params or {}).items())
    q = "&".join(f"{k}={quote(v, safe='')}" for k, v in items)
    w_rid = hashlib.md5((q + mix).encode("utf-8")).hexdigest()
    return f"{q}&w_rid={w_rid}"


# ── 会话 ──────────────────────────────────────────────────────────
_SESSION: Dict[str, Any] = {"s": None, "cookie": ""}


def session(cookie: str = ""):
    """进程内单例 session，**先访问首页**拿 buvid3（不预热一律 412）。"""
    cookie = str(cookie or "").strip()
    if _SESSION["s"] is None or cookie != _SESSION["cookie"]:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                          "Referer": "https://www.bilibili.com/"})
        if cookie:
            s.headers["Cookie"] = cookie
        try:
            s.get("https://www.bilibili.com/", timeout=10)
        except Exception:
            pass
        _SESSION["s"], _SESSION["cookie"] = s, cookie
    return _SESSION["s"]


def reset_session() -> None:
    _SESSION["s"] = None
    _SESSION["cookie"] = ""


# ── 文本清洗 ──────────────────────────────────────────────────────
def clean_cues(cues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in (cues or []):
        t = str((c or {}).get("text") or "").strip()
        if not t or any(h in t for h in _HALLUCINATION):
            continue
        t = re.sub(r"[ \t]{2,}", " ", t)
        if out and out[-1]["text"] == t:
            continue
        out.append({"from": float(c.get("from") or 0), "to": float(c.get("to") or 0),
                    "text": t})
    return out


def fmt_ts(sec: float) -> str:
    s = int(float(sec or 0))
    return (f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600
            else f"{s // 60:02d}:{s % 60:02d}")


def cues_to_text(cues: List[Dict[str, Any]], head: str = "") -> str:
    lines = [head] if head else []
    for c in (cues or []):
        lines.append(f"[{fmt_ts(c.get('from'))}] {c.get('text', '')}")
    return "\n".join(lines)


def hot_words(danmaku: List[str], limit: int = 12) -> List[Dict[str, Any]]:
    """弹幕里的高频短句（2–8 字）。不引第三方分词，按整句计数就够用了。"""
    import collections
    cnt = collections.Counter()
    for t in (danmaku or []):
        s = str(t or "").strip()
        if 2 <= len(s) <= 8 and not s.isdigit():
            cnt[s] += 1
    return [{"word": w, "count": n} for w, n in cnt.most_common(limit) if n >= 2]


# ── 主体 ──────────────────────────────────────────────────────────
class BiliAPI:
    """只读 B 站接口。`http_json` 可注入（单测不碰网络）。"""

    def __init__(self, cookie: str = "", timeout: float = 15.0,
                 http_json: Optional[Any] = None, text_get: Optional[Any] = None):
        self.cookie = str(cookie or "").strip()
        self.timeout = timeout
        self._injected = http_json is not None
        self._http = http_json or self._fetch_json
        self._text = text_get or self._fetch_text

    # -- 抓取 ----------------------------------------------------
    def _fetch_json(self, url: str, params: Optional[dict] = None) -> dict:
        r = session(self.cookie).get(url, params=params or None, timeout=self.timeout)
        try:
            return r.json()
        except Exception:
            return {}

    def _fetch_text(self, url: str, params: Optional[dict] = None) -> str:
        r = session(self.cookie).get(url, params=params or None, timeout=self.timeout)
        # ★ requests 对 .xml 会猜成 ISO-8859-1 → 中文弹幕全变乱码
        r.encoding = "utf-8"
        return r.text or ""

    def _j(self, url: str, params: Optional[dict] = None) -> dict:
        try:
            r = self._http(url, params or {})
            return r if isinstance(r, dict) else {}
        except Exception:
            return {}

    # -- 视频信息 ------------------------------------------------
    def view(self, bvid: str = "", aid: int = 0) -> Dict[str, Any]:
        p = {"bvid": bvid} if bvid else {"aid": aid}
        j = self._j(f"{API}/x/web-interface/view", p)
        if j.get("code") not in (0, None):
            return {}
        return _norm_view(j.get("data") or {})

    # -- 字幕（WBI 优先）----------------------------------------
    def _wbi_params(self) -> tuple:
        nav = self._j(f"{API}/x/web-interface/nav", {"ts": int(time.time())})
        try:
            w = (nav.get("data") or {}).get("wbi_img") or {}
            img = str(w.get("img_url") or "").split("/")[-1].split(".")[0]
            sub = str(w.get("sub_url") or "").split("/")[-1].split(".")[0]
            return img, sub
        except Exception:
            return "", ""

    def subtitles(self, bvid: str, aid: int, cid: int) -> List[Dict[str, Any]]:
        """字幕轨列表。**WBI 版优先**（带 auth_key，旧接口给的 url 会 403）。"""
        img, sub = self._wbi_params()
        if img and sub:
            q = sign_wbi({"bvid": bvid, "cid": str(cid), "wts": int(time.time()),
                          "platform": "web", "web_location": "1550101"}, img, sub)
            j = self._j(f"{API}/x/player/wbi/v2?{q}")
            subs = _subs_of(j)
            if subs:
                return subs
        j = self._j(f"{API}/x/player/v2", {"bvid": bvid, "aid": aid, "cid": cid})
        return _subs_of(j)

    def cues(self, subtitle_url: str) -> List[Dict[str, Any]]:
        url = str(subtitle_url or "")
        if url.startswith("//"):
            url = "https:" + url
        j = self._j(url)
        body = j.get("body")
        if not isinstance(body, list):
            return []
        out = []
        for b in body:
            try:
                out.append({"from": float(b.get("from") or 0),
                            "to": float(b.get("to") or 0),
                            "text": str(b.get("content") or "")})
            except Exception:
                continue
        return out

    def transcript(self, bvid: str, aid: int, cid: int) -> Dict[str, Any]:
        subs = self.subtitles(bvid, aid, cid)
        if not subs:
            return {"ok": True, "cues": [], "lan": "", "source": "none"}
        sub = _prefer(subs)
        cues = clean_cues(self.cues(sub.get("subtitle_url", "")))
        return {"ok": True, "cues": cues, "lan": sub.get("lan", ""),
                "lan_doc": sub.get("lan_doc", ""), "source": "subtitle" if cues else "none"}

    # -- 弹幕 ----------------------------------------------------
    def danmaku(self, cid: int, limit: int = 3000) -> List[Dict[str, Any]]:
        """视频弹幕（XML）。返回 `[{t, mode, text}]`，按时间排序。"""
        xml = self._text(f"https://comment.bilibili.com/{cid}.xml", {})
        if not xml:
            return []
        out = []
        for m in re.finditer(r'<d p="([^"]*)">([^<]*)</d>', xml):
            parts = (m.group(1) or "").split(",")
            try:
                t = float(parts[0]) if parts and parts[0] else 0.0
                mode = int(parts[1]) if len(parts) > 1 and parts[1] else 1
            except Exception:
                t, mode = 0.0, 1
            txt = _unescape(m.group(2))
            if txt:
                out.append({"t": t, "mode": mode, "text": txt})
            if len(out) >= limit:
                break
        out.sort(key=lambda x: x["t"])
        return out

    # -- 热评 ----------------------------------------------------
    def comments(self, aid: int, ps: int = 20, pn: int = 1,
                 sort_hot: bool = True) -> List[Dict[str, Any]]:
        j = self._j(f"{API}/x/v2/reply",
                    {"type": 1, "oid": aid, "sort": 2 if sort_hot else 0,
                     "ps": ps, "pn": pn})
        reps = ((j.get("data") or {}).get("replies") or [])
        out = []
        for r in reps:
            if not isinstance(r, dict):
                continue
            c = r.get("content") or {}
            out.append({"user": str((r.get("member") or {}).get("uname") or ""),
                        "text": str(c.get("message") or "").strip(),
                        "like": int(r.get("like") or 0),
                        "rcount": int(r.get("rcount") or 0),
                        "ctime": int(r.get("ctime") or 0)})
        return out


# ── 辅助 ──────────────────────────────────────────────────────────
def _unescape(s: str) -> str:
    import html
    return html.unescape(str(s or "")).strip()


def _subs_of(j: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        subs = ((j.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        return [s for s in subs if isinstance(s, dict) and s.get("subtitle_url")]
    except Exception:
        return []


def _prefer(subs: List[Dict[str, Any]]) -> Dict[str, Any]:
    for s in subs:
        if str(s.get("lan") or "").startswith("zh"):
            return s
    return subs[0] if subs else {}


def _norm_view(d: Dict[str, Any]) -> Dict[str, Any]:
    st = d.get("stat") or {}
    pages = d.get("pages") or []
    return {
        "bvid": d.get("bvid", ""), "aid": int(d.get("aid") or 0),
        "cid": int(d.get("cid") or 0),
        "title": d.get("title", ""),
        "owner": str((d.get("owner") or {}).get("name") or ""),
        "owner_mid": int((d.get("owner") or {}).get("mid") or 0),
        "cover": d.get("pic", ""),
        "desc": d.get("desc", ""),
        "duration": int(d.get("duration") or 0),
        "pubdate": int(d.get("pubdate") or 0),
        "pages": [{"cid": int(p.get("cid") or 0), "page": int(p.get("page") or 1),
                   "part": p.get("part", ""),
                   "duration": int(p.get("duration") or 0)} for p in pages],
        "stat": {"view": int(st.get("view") or 0), "like": int(st.get("like") or 0),
                 "coin": int(st.get("coin") or 0), "favorite": int(st.get("favorite") or 0),
                 "share": int(st.get("share") or 0), "danmaku": int(st.get("danmaku") or 0),
                 "reply": int(st.get("reply") or 0)},
    }


__all__ = ["API", "parse_url", "mixin_key", "sign_wbi", "session", "reset_session",
           "clean_cues", "cues_to_text", "fmt_ts", "hot_words", "BiliAPI", "envelope"]
