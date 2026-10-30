# -*- coding: utf-8 -*-
"""bili_script —— B 站链接读稿插件（2026-10-30）。

## 职责边界（重要）

能力在 `harness/bili_transcript.py`（抓取、WBI 签名、清洗），**本插件只管策略**：
什么时候读、读到了注什么、用户能不能关、登录态存在哪。

这样分层的原因：能力层要能被别的路径复用（agent 原语、以后的"追问细节"），
而且不该因为用户关掉插件就消失。

## 登录在插件里

B 站字幕轨要登录态：匿名调 `x/player/v2` 返回 200 但 `subtitles` 恒为空。
所以这里自己管 `SESSDATA`：存在插件自己的 `data/config.json`，不进 bot_config，
也不进版本库。`login_set` 会拿 `x/web-interface/nav` 校验一次（`isLogin`）。

为什么不是扫码登录：这台机器**没有任何二维码库**（qrcode/segno/reportlab 全无），
而把 qrcode_key 交给外部在线二维码服务等于把登录令牌发出去 —— 不做。
要扫码的话，装一个 `segno`（纯 Python、零依赖）就能补上。

## 注入什么

视频稿动辄几千字，而注入预算是**每条回复几十字**（reply_budget / F3）。
所以这里只注标题 + 前几行 + "共 N 条，要细节再问"，原文留在缓存里等追问。
"""

import json
import os
import sys
import time

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(PLUGIN_DIR))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

PLUGIN_META = {
    "name": "bili_script",
    "version": "1.0.0",
    "description": "B站链接读稿：读字幕轨并注入摘要，登录在插件内完成。",
    "author": "EggBloom",
    "group_only": False,
    "enabled": True,
    "dependencies": [],
    "hooks": ["before_reply"],
    "priority": 55,          # 排在记忆(40)/语气(30)之后，搜索(60)之前
    "estimated_tokens": 120,
    "tags": ["B站", "读稿", "字幕"],
    "page": "plugins/bili_script/ui.html",
}

DATA_DIR = os.path.join(PLUGIN_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
CACHE_PATH = os.path.join(DATA_DIR, "cache.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")

_DEFAULTS = {
    "enabled": True,
    "auto_read": True,       # 消息里出现链接就自动读
    "cookie": "",            # SESSDATA=xxx（插件内登录）
    "max_chars": 200,        # 注入上限
    "ttl_hours": 24,         # 缓存有效期
    "max_lines": 3,          # 注入的前几行
}

_UA_FALLBACK = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


# ── 小工具 ────────────────────────────────────────────────────────
def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else dict(default)
    except Exception:
        return dict(default)


def _save(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def _cfg():
    d = dict(_DEFAULTS)
    d.update(_load(CONFIG_PATH, {}))
    return d


def _bump(key):
    st = _load(STATS_PATH, {})
    st[key] = int(st.get(key) or 0) + 1
    st["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(STATS_PATH, st)


def _mask(cookie: str) -> str:
    c = str(cookie or "")
    if not c:
        return ""
    v = c.split("SESSDATA=", 1)[-1].split(";", 1)[0]
    return (v[:4] + "…" + v[-4:]) if len(v) > 10 else "…"


def _cookie_ok(cookie: str) -> dict:
    """拿 nav 校验一次登录态。不抛。"""
    if not str(cookie or "").strip():
        return {"ok": False, "error": "没有 cookie"}
    try:
        from harness.bili_transcript import BiliTranscript
        bt = BiliTranscript(cookie=cookie)
        j = bt._json("https://api.bilibili.com/x/web-interface/nav", {})
        data = j.get("data") or {}
        return {"ok": bool(data.get("isLogin")), "uname": data.get("uname") or "",
                "mid": data.get("mid") or 0}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── 缓存 ──────────────────────────────────────────────────────────
def _cache_get(key: str, ttl_hours: int):
    c = _load(CACHE_PATH, {})
    ent = c.get(key)
    if not isinstance(ent, dict):
        return None
    if time.time() - float(ent.get("ts") or 0) > max(1, ttl_hours) * 3600:
        return None
    return ent.get("data")


def _cache_put(key: str, data: dict):
    c = _load(CACHE_PATH, {})
    c[key] = {"ts": time.time(), "data": data}
    if len(c) > 200:                      # 只留最近 200 条，别让缓存无限长
        keep = sorted(c.items(), key=lambda kv: kv[1].get("ts", 0))[-200:]
        c = dict(keep)
    _save(CACHE_PATH, c)


# ── 核心：读一个链接 ──────────────────────────────────────────────
def read_link(url: str, use_cache: bool = True) -> dict:
    """链接 → `{ok, data:{title,owner,source,cues,text}, error}`（带 24h 缓存）。"""
    from harness.bili_transcript import BiliTranscript, parse_bili_url
    cfg = _cfg()
    info = parse_bili_url(url)
    key = (info.get("bvid") or "") + "#" + str(info.get("p") or 1) + "#" + info.get("short_url", "")
    if use_cache and key:
        hit = _cache_get(key, int(cfg.get("ttl_hours") or 24))
        if hit:
            _bump("cache_hits")
            return {"ok": True, "data": hit, "error": "", "cached": True}
    try:
        r = BiliTranscript(cookie=str(cfg.get("cookie") or "")).run(str(url or ""))
    except Exception as e:
        return {"ok": False, "data": None, "error": f"{type(e).__name__}: {e}"}
    if r.get("ok") and key:
        _cache_put(key, r.get("data") or {})
    return r


def _inject_text(data: dict, max_chars: int, max_lines: int) -> str:
    """把稿子压成能塞进预算的一小段。"""
    if not data:
        return ""
    title = str(data.get("title") or "").strip()
    owner = str(data.get("owner") or "").strip()
    cues = data.get("cues") or []
    head = f"【B站文稿】{title}" + (f" · {owner}" if owner else "")
    if not cues:
        return (f"{head}\n（这个视频没有字幕轨，我读不到稿子 —— 别编内容，"
                f"直接说没字幕、让对方讲几句）")[:max_chars]
    body = " ".join(str(c.get("text") or "") for c in cues[: int(max_lines) * 3])
    tail = f"（共 {len(cues)} 条，要细节我再翻）"
    out = f"{head}\n{body}…{tail}"
    return out[:max_chars]


# ── 钩子 ──────────────────────────────────────────────────────────
def before_reply(message: str = "", group_id: str = "", context: dict = None, **kwargs):
    """消息里有 B 站链接就顺手读一份稿子注入。读不到就放行，绝不阻塞回复。"""
    try:
        cfg = _cfg()
        if not cfg.get("enabled") or not cfg.get("auto_read"):
            return None
        msg = str(message or "")
        if not msg or ("bili" not in msg.lower() and "BV" not in msg and "av" not in msg.lower()):
            return None
        from harness.bili_transcript import parse_bili_url
        if not parse_bili_url(msg):
            return None
        t0 = time.time()
        r = read_link(msg)
        if not r.get("ok"):
            return None
        data = r.get("data") or {}
        if not data.get("cues") and not data.get("title"):
            return None
        _bump("read_ok")
        if data.get("cues"):
            _bump("with_subtitle")
        text = _inject_text(data, int(cfg.get("max_chars") or 200),
                            int(cfg.get("max_lines") or 3))
        if not text:
            return None
        if not cfg.get("cookie"):
            _bump("no_cookie_hint")
        return {"action": "inject", "content": text,
                "meta": {"plugin": "bili_script",
                         "elapsed": round(time.time() - t0, 2),
                         "cached": bool(r.get("cached"))}}
    except Exception:
        return None


# ── 扫码登录（弹 cmd 窗口显示二维码）────────────────────────────
def _qr_ascii(text: str) -> str:
    """文本 → 终端二维码（半块字符，1 字符 = 1 模块，省宽度）。

    用 segno（纯 Python、零依赖）。没装就返回空串 —— 调用方转成"请粘贴 cookie"。
    """
    try:
        import segno
        qr = segno.make(str(text), error="m")
        m = [list(row) for row in qr.matrix]
        out = []
        for i in range(0, len(m), 2):
            top = m[i]
            bot = m[i + 1] if i + 1 < len(m) else [0] * len(top)
            out.append("".join("█" if t and b else "▀" if t else "▄" if b else " "
                               for t, b in zip(top, bot)))
        return "\n".join(out)
    except Exception:
        return ""


def _pop_qr_window(qr_text: str, tip: str = "") -> bool:
    """把二维码弹到一个新的 cmd 窗口里（用户拿手机扫）。

    为什么要弹窗：二维码走半块字符，直接在 WebUI 的 DOM 里画不好对齐；
    cmd 里用等宽字体天然是正方形，扫得出来。窗口用 chcp 65001 保证不乱码。
    """
    try:
        import subprocess
        import tempfile
        script = (
            "# -*- coding: utf-8 -*-\n"
            "import sys\n"
            "print(" + repr(qr_text) + ")\n"
            "print()\n"
            "print(" + repr(tip or "用 B 站 App 扫码登录；扫完这个窗口可以关掉") + ")\n"
            "try:\n"
            "    input()\n"
            "except Exception:\n"
            "    pass\n"
        )
        p = os.path.join(tempfile.gettempdir(), "egg_bili_qr.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(script)
        cmd = (f'start "" cmd /c "chcp 65001 >nul & mode con cols=100 lines=60 & '
               f'{sys.executable} \\"{p}\\""')
        subprocess.Popen(cmd, shell=True)
        return True
    except Exception:
        return False


def login_qr_start(params: dict = None) -> dict:
    """生成 B 站扫码登录二维码，并弹窗显示。返回 qrcode_key 供轮询。"""
    p = params or {}
    if not _qr_ascii("test"):
        return {"ok": False, "error": "没装 segno，无法生成二维码；可改用粘贴 SESSDATA"}
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": _UA_FALLBACK, "Referer": "https://www.bilibili.com/"})
        r = s.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                  timeout=15)
        j = r.json()
        if j.get("code") != 0:
            return {"ok": False, "error": f"生成二维码失败：{j.get('message') or j.get('code')}"}
        d = j.get("data") or {}
        key, url = str(d.get("qrcode_key") or ""), str(d.get("url") or "")
        if not key or not url:
            return {"ok": False, "error": "接口没返回 qrcode_key"}
        cfg = _cfg()
        cfg["qr_key"] = key
        cfg["qr_ts"] = time.time()
        _save(CONFIG_PATH, cfg)
        art = _qr_ascii(url)
        popped = _pop_qr_window(art) if p.get("pop", True) else False
        _bump("qr_start")
        return {"ok": True, "qrcode_key": key, "popped": popped,
                "ascii": art, "url": url,
                "hint": "已弹出窗口，用 B 站 App 扫码；扫完点『我扫完了』" if popped
                        else "二维码已生成，可直接扫码"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def login_qr_poll(params: dict = None) -> dict:
    """查扫码状态；成功时把 cookie 存下来。

    B 站的状态码：0 成功 / 86101 未扫码 / 86090 已扫码待确认 / 86038 已过期。
    """
    p = params or {}
    cfg = _cfg()
    key = str(p.get("qrcode_key") or cfg.get("qr_key") or "")
    if not key:
        return {"ok": False, "status": "none", "error": "还没有二维码，先点扫码登录"}
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": _UA_FALLBACK, "Referer": "https://www.bilibili.com/"})
        r = s.get("https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                  params={"qrcode_key": key}, timeout=15)
        j = r.json()
        d = j.get("data") or {}
        code = d.get("code")
        if code == 0:
            # cookie 在 Set-Cookie 里（requests 已收进 session.cookies）
            jar = {c.name: c.value for c in s.cookies}
            ck = "; ".join(f"{k}={v}" for k, v in jar.items()
                           if k in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3"))
            if not ck or "SESSDATA" not in ck:
                # 兜底：从返回的 url 查询串里取
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(str(d.get("url") or "")).query)
                ck = "; ".join(f"{k}={v[0]}" for k, v in q.items()
                               if k in ("SESSDATA", "bili_jct", "DedeUserID"))
            if "SESSDATA" not in ck:
                return {"ok": False, "status": "error", "error": "登录成功但没拿到 SESSDATA"}
            chk = _cookie_ok(ck)
            cfg["cookie"] = ck
            cfg.pop("qr_key", None)
            _save(CONFIG_PATH, cfg)
            _bump("login")
            return {"ok": True, "status": "ok", "uname": chk.get("uname", ""),
                    "cookie_tail": _mask(ck)}
        if code == 86101:
            return {"ok": True, "status": "waiting", "hint": "还没扫码"}
        if code == 86090:
            return {"ok": True, "status": "scanned", "hint": "已扫码，请在手机上确认"}
        if code == 86038:
            cfg.pop("qr_key", None)
            _save(CONFIG_PATH, cfg)
            return {"ok": True, "status": "expired", "hint": "二维码过期了，重新点扫码登录"}
        return {"ok": True, "status": "unknown", "hint": f"状态码 {code}"}
    except Exception as e:
        return {"ok": False, "status": "error", "error": f"{type(e).__name__}: {e}"}


# ── 面板动作 ──────────────────────────────────────────────────────
def get_config() -> dict:
    cfg = _cfg()
    return {
        "enabled": bool(cfg.get("enabled")),
        "auto_read": bool(cfg.get("auto_read")),
        "max_chars": int(cfg.get("max_chars") or 200),
        "ttl_hours": int(cfg.get("ttl_hours") or 24),
        "max_lines": int(cfg.get("max_lines") or 3),
        "logged_in": bool(cfg.get("cookie")),
        "cookie_tail": _mask(cfg.get("cookie")),
    }


def set_config(params: dict = None) -> dict:
    if not isinstance(params, dict):
        return {"ok": False, "error": "params must be dict"}
    cfg = _cfg()
    for k in ("enabled", "auto_read", "max_chars", "ttl_hours", "max_lines"):
        if k in params:
            cfg[k] = params[k]
    _save(CONFIG_PATH, cfg)
    return {"ok": True, "config": get_config()}


def login_set(params: dict = None) -> dict:
    """在插件里登录：粘贴 SESSDATA（或整条 Cookie），校验后存下。"""
    p = params or {}
    raw = str(p.get("cookie") or "").strip()
    if not raw:
        return {"ok": False, "error": "cookie 不能为空"}
    cookie = raw if "SESSDATA=" in raw else f"SESSDATA={raw}"
    chk = _cookie_ok(cookie)
    if not chk.get("ok"):
        return {"ok": False, "error": f"这个 SESSDATA 无效（{chk.get('error') or 'nav 未登录'}）"}
    cfg = _cfg()
    cfg["cookie"] = cookie
    _save(CONFIG_PATH, cfg)
    _bump("login")
    return {"ok": True, "uname": chk.get("uname", ""), "cookie_tail": _mask(cookie)}


def login_status() -> dict:
    cfg = _cfg()
    ck = str(cfg.get("cookie") or "")
    if not ck:
        return {"logged_in": False, "cookie_tail": "", "uname": "",
                "hint": "未登录：现在只能读到标题/UP/时长，读不到字幕稿"}
    chk = _cookie_ok(ck)
    return {"logged_in": bool(chk.get("ok")), "cookie_tail": _mask(ck),
            "uname": chk.get("uname", ""),
            "hint": "" if chk.get("ok") else "登录已失效，请重新粘贴 SESSDATA"}


def logout() -> dict:
    cfg = _cfg()
    cfg["cookie"] = ""
    _save(CONFIG_PATH, cfg)
    return {"ok": True}


def probe(params: dict = None) -> dict:
    """试跑：给一个链接，返回能注入的那段 + 元信息。"""
    p = params or {}
    url = str(p.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "需要一个链接"}
    t0 = time.time()
    r = read_link(url, use_cache=not bool(p.get("fresh")))
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error") or "读取失败"}
    d = r.get("data") or {}
    cfg = _cfg()
    return {"ok": True, "title": d.get("title", ""), "owner": d.get("owner", ""),
            "source": d.get("source", ""), "count": len(d.get("cues") or []),
            "elapsed": round(time.time() - t0, 2), "cached": bool(r.get("cached")),
            "inject": _inject_text(d, int(cfg.get("max_chars") or 200),
                                   int(cfg.get("max_lines") or 3)),
            "head": [str(c.get("text") or "") for c in (d.get("cues") or [])[:6]]}


def get_stats() -> dict:
    st = _load(STATS_PATH, {})
    st["cache_size"] = len(_load(CACHE_PATH, {}))
    st["logged_in"] = bool(_cfg().get("cookie"))
    return st


def clear_cache() -> dict:
    _save(CACHE_PATH, {})
    return {"ok": True}


PLUGIN_ACTIONS = {
    "get_config": get_config,
    "set_config": set_config,
    "login_set": login_set,
    "login_qr_start": login_qr_start,
    "login_qr_poll": login_qr_poll,
    "login_status": login_status,
    "logout": logout,
    "probe": probe,
    "get_stats": get_stats,
    "clear_cache": clear_cache,
}
