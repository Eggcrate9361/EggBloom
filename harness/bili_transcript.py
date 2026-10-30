# -*- coding: utf-8 -*-
"""harness/bili_transcript.py —— B 站链接 → 字幕稿（2026-10-30）。

## 从哪来的

思路和签名算法来自 `rudyz666/dsh-bili-asr`（MIT，DSH 插件，纯 Node）。
那边完整的链路是「链接 → 字幕轨 → 没有字幕就本地 whisper 转写 → 三层校对」。
**这台机器没有 whisper，也没有 ffmpeg**，所以这里只搬不需要任何本地依赖的那一半：

    链接 → x/player/v2（失败再走 WBI 签名的 x/player/wbi/v2）→ 字幕 JSON → 带时间戳文本

对"贴个视频链接就想知道讲了什么"已经够用；真视频画面另有 `harness/video_frames.py`
（抽 4 帧拼成联系表，交给多模态主模型）。

## 为什么留着 WBI 那一段

`x/player/v2` 在很多视频上直接给空（风控 / 需要登录态），而 WBI 签名版
`x/player/wbi/v2` 能拿到同样的数据。签名只要三步：nav 拿 img_key/sub_key →
按固定 64 表重排取前 32 位得 mixin → `md5(排序后的query + mixin)` 当 `w_rid`。

## 边界

- 只读公开接口，不登录、不投币、不刷量。
- 拿不到字幕就**如实返回空**，绝不猜内容（配合【能力边界】）。
- HTTP 走注入，单测不碰网络。
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Callable, Dict, List, Optional

API = "https://api.bilibili.com"

#: WBI mixin 表（b 站前端固定 64 项，别问为什么，照抄）。
_MIXIN_TAB = (46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
              27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
              37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
              22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52)

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"(?:^|[^0-9A-Za-z])av(\d{3,})", re.I)
_B23_RE = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+", re.I)
_P_RE = re.compile(r"[?&]p=(\d+)")

#: whisper 中文幻觉模板（字幕里也常见）。命中整行直接丢。
_HALLUCINATION = ("请不吝点赞", "订阅转发", "字幕由", "amara.org", "字幕志愿者",
                  "本字幕由", "由 Amara")


def envelope(ok: bool, data: Any = None, error: str = "") -> Dict[str, Any]:
    return {"ok": bool(ok), "data": data, "error": str(error or "")}


# ── 链接解析 ────────────────────────────────────────────────────────
def parse_bili_url(text: str) -> Dict[str, Any]:
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
    """把 nav 给的 img_key+sub_key 按固定表重排，取前 32 位。"""
    raw = str(raw or "")
    return "".join(raw[i] for i in _MIXIN_TAB if i < len(raw))[:32]


def sign_wbi(params: Dict[str, Any], img_key: str, sub_key: str) -> str:
    """算 WBI 签名，返回 `k=v&...&w_rid=xxx`（键已排序、值已转义）。"""
    from urllib.parse import quote
    mix = mixin_key(f"{img_key}{sub_key}")
    items = sorted((str(k), str(v)) for k, v in (params or {}).items())
    q = "&".join(f"{k}={quote(v, safe='')}" for k, v in items)
    w_rid = hashlib.md5((q + mix).encode("utf-8")).hexdigest()
    return f"{q}&w_rid={w_rid}"


def _keys_from_nav(nav: Dict[str, Any]) -> tuple:
    """从 nav 响应里取 (img_key, sub_key)。取不到返回 ("","")。"""
    try:
        w = ((nav or {}).get("data") or {}).get("wbi_img") or {}
        img = str(w.get("img_url") or "").split("/")[-1].split(".")[0]
        sub = str(w.get("sub_url") or "").split("/")[-1].split(".")[0]
        return img, sub
    except Exception:
        return "", ""


# ── 文本清洗（从那边"规则层"里取最小可用的一半）──────────────────
def clean_cues(cues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """去掉幻觉行、合并连续重复行、去多余空白。时间戳保留。"""
    out: List[Dict[str, Any]] = []
    for c in (cues or []):
        t = str((c or {}).get("text") or "").strip()
        if not t:
            continue
        if any(h in t for h in _HALLUCINATION):
            continue
        t = re.sub(r"[ \t]{2,}", " ", t)
        if out and out[-1]["text"] == t:      # 连续重复只留一条
            continue
        out.append({"from": float(c.get("from") or 0), "to": float(c.get("to") or 0),
                    "text": t})
    return out


def cues_to_text(cues: List[Dict[str, Any]], head: str = "") -> str:
    """`[mm:ss] 文本` 一行一条；超过 1 小时用 h:mm:ss。"""
    lines = []
    if head:
        lines.append(head)
    for c in (cues or []):
        sec = int(float(c.get("from") or 0))
        ts = (f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600
              else f"{sec // 60:02d}:{sec % 60:02d}")
        lines.append(f"[{ts}] {c.get('text', '')}")
    return "\n".join(lines)


# ── 抓取 ────────────────────────────────────────────────────────────
class BiliTranscript:
    """B 站字幕抽取。`http_json(url, params)` 由调用方注入（默认自带 requests）。

    `cookie`：可选，形如 `SESSDATA=xxx`。**字幕轨要登录态**：匿名调
    `x/player/v2` 一样返回 200，但 `data.subtitle.subtitles` 是空的 ——
    所以没有 cookie 时只能拿到标题 / UP / 时长，拿不到稿子。

    返回值统一是 `{"ok", "data", "error"}`，`data` 里放 `{title, owner, duration,
    cues, text, source}`。
    """

    def __init__(self, http_json: Optional[Callable[[str, dict], dict]] = None,
                 timeout: float = 15.0, max_cues: int = 4000,
                 cookie: str = ""):
        self._injected = http_json is not None
        self._http = http_json or self._fetch
        self.timeout = timeout
        self.max_cues = max_cues
        self.cookie = str(cookie or "").strip()

    # -- 自带 session 的抓取 --------------------------------------
    def _fetch(self, url: str, params: Optional[dict] = None) -> dict:
        s = _session()
        h = {"Cookie": self.cookie} if self.cookie else None
        r = s.get(url, params=params or None, timeout=self.timeout, headers=h)
        try:
            return r.json()
        except Exception:
            return {}

    # -- 基础 ----------------------------------------------------
    def _json(self, url: str, params: Optional[dict] = None) -> dict:
        try:
            r = self._http(url, params or {})
            return r if isinstance(r, dict) else {}
        except Exception:
            return {}

    def resolve_short(self, short_url: str) -> str:
        """b23.tv 短链 → 真实地址。取不到返回空串。"""
        url = str(short_url or "").strip()
        if not url:
            return ""
        try:
            if self._injected:
                import requests
                r = requests.get(url, timeout=self.timeout, allow_redirects=True,
                                 headers={"User-Agent": _UA})
            else:
                r = _session().get(url, timeout=self.timeout, allow_redirects=True)
            return str(r.url or "")
        except Exception:
            return ""

    def video_info(self, bvid: str = "", aid: int = 0) -> Dict[str, Any]:
        params = {"bvid": bvid} if bvid else {"aid": aid}
        j = self._json(f"{API}/x/web-interface/view", params)
        if j.get("code") not in (0, None):
            return {}
        return j.get("data") or {}

    def subtitles(self, bvid: str, cid: int) -> List[Dict[str, Any]]:
        """拿字幕轨列表：先普通接口，失败再走 WBI 签名版。"""
        j = self._json(f"{API}/x/player/v2", {"bvid": bvid, "cid": cid})
        subs = _pick_subtitles(j)
        if subs:
            return subs
        nav = self._json(f"{API}/x/web-interface/nav",
                         {"ts": int(time.time())})
        img, sub = _keys_from_nav(nav)
        if not (img and sub):
            return []
        q = sign_wbi({"bvid": bvid, "cid": str(cid), "wts": int(time.time()),
                      "platform": "web", "web_location": "1550101"}, img, sub)
        j2 = self._json(f"{API}/x/player/wbi/v2?{q}", {})
        return _pick_subtitles(j2)

    def cues(self, subtitle_url: str) -> List[Dict[str, Any]]:
        url = str(subtitle_url or "")
        if url.startswith("//"):
            url = "https:" + url
        j = self._json(url, {})
        body = j.get("body")
        if not isinstance(body, list):
            return []
        out = []
        for b in body[: self.max_cues]:
            try:
                out.append({"from": float(b.get("from") or 0),
                            "to": float(b.get("to") or 0),
                            "text": str(b.get("content") or "")})
            except Exception:
                continue
        return out

    # -- 主流程 --------------------------------------------------
    def run(self, text: str, p: Optional[int] = None) -> Dict[str, Any]:
        info = parse_bili_url(text)
        if not info:
            return envelope(False, error="这句里没有 BV/av/b23 链接")
        if info.get("short_url"):
            real = self.resolve_short(info["short_url"])
            info2 = parse_bili_url(real)
            if not info2:
                return envelope(False, error="短链跳转后没找到 BV 号")
            info = {**info2, "p": info.get("p") or info2.get("p")}
        page = int(p or info.get("p") or 1)
        bvid = info.get("bvid") or ""
        view = self.video_info(bvid, int(info.get("aid") or 0))
        if not view:
            return envelope(False, error="取视频信息失败（可能被风控或链接无效）")
        if not bvid:
            bvid = str(view.get("bvid") or "")
        pages = view.get("pages") or [{}]
        idx = min(max(0, page - 1), len(pages) - 1)
        cid = int((pages[idx] or {}).get("cid") or view.get("cid") or 0)
        subs = self.subtitles(bvid, cid)
        if not subs:
            return envelope(True, data={
                "title": view.get("title", ""), "owner": _owner(view),
                "duration": view.get("duration", 0), "cues": [], "text": "",
                "source": "none",
                "note": "这个视频没有字幕轨（本地转写需要 whisper，本机没有）"})
        sub = _prefer(subs)
        cues = clean_cues(self.cues(sub.get("subtitle_url", "")))
        head = f"# {view.get('title', '')} · {_owner(view)}"
        return envelope(True, data={
            "title": view.get("title", ""), "owner": _owner(view),
            "duration": view.get("duration", 0), "cues": cues,
            "text": cues_to_text(cues, head=head),
            "source": "subtitle", "lan": sub.get("lan", "")})


def _owner(view: Dict[str, Any]) -> str:
    return str(((view or {}).get("owner") or {}).get("name") or "")


def _pick_subtitles(j: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        subs = ((j.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        return [s for s in subs if isinstance(s, dict)]
    except Exception:
        return []


def _prefer(subs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """优先中文字幕轨，其次带 url 的第一条。"""
    for s in subs:
        if str(s.get("lan") or "").startswith("zh") and s.get("subtitle_url"):
            return s
    for s in subs:
        if s.get("subtitle_url"):
            return s
    return subs[0] if subs else {}


_SESSION: Dict[str, Any] = {"s": None}
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _session():
    """进程内单例 session，**先访问一次首页**。

    不预热就是 HTTP 412（B 站风控）：实测裸调 `x/web-interface/view` 一律 412，
    先 GET 一次 https://www.bilibili.com/ 拿到 buvid3 / b_nut 就正常了。
    """
    if _SESSION["s"] is None:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9",
                          "Referer": "https://www.bilibili.com/"})
        try:
            s.get("https://www.bilibili.com/", timeout=10)
        except Exception:
            pass
        _SESSION["s"] = s
    return _SESSION["s"]


def reset_session() -> None:
    """丢到当前 session（换号/被风控时用）。"""
    _SESSION["s"] = None


def _default_http_json(url: str, params: dict) -> dict:
    r = _session().get(url, params=params or None, timeout=15)
    try:
        return r.json()
    except Exception:
        return {}


__all__ = ["API", "parse_bili_url", "mixin_key", "sign_wbi", "clean_cues",
           "cues_to_text", "BiliTranscript", "envelope", "reset_session"]
