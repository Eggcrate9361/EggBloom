# -*- coding: utf-8 -*-
"""bili —— B 站插件（2026-10-30，P0）。

## 一个链接能读到四路

    信息   x/web-interface/view        标题/UP/封面/数据/分P
    文稿   x/player/wbi/v2 → subtitle  字幕轨（要登录态）
    弹幕   comment.bilibili.com/x.xml   免签名，一次 1200~3000 条
    热评   x/v2/reply?sort=2           免签名

能力在 `harness/bili_api.py`（可单测、可被别处复用），插件只管策略：
什么时候读、读完注入什么、用户能不能关、登录态存哪。

## 为什么注入的是压缩版

F3 预算：单条注入几十字。整篇稿子几千字直接注入会被砍掉，表现成"插件没反应"。
所以注入的是：标题 + 热度最高的两条弹幕梗 + 前两行稿子 + "共 N 条，要细节我再翻"。

## 登录

- 扫码：passport 的 generate → segno 渲染半块字符 → 弹 cmd 窗口。
- 兼容：粘贴 SESSDATA，`nav.isLogin` 校验后才存。
- cookie 只存在本插件 `data/config.json`，不进 bot_config、不进版本库。

## 在 agent 链路里是什么位置（关键，别当成"B站解析器"看）

这个插件不是给用户点着玩的解析页，它是 **agent 的能力供给方**。整条链是这样：

    QQ 消息
      ↓
    harness.qq_agent.should_use_agent()   意图闸：查/看/听 + 视频/链接 才进
      ↓ 命中
    before_reply 钩子（本插件）            先便宜地看一眼：有链接就整包拉下来，
      ↓                                    压缩成 config.inject_chars 字注入
    agent_loop.run_agent()                 模型带着 trace 决定还要不要深挖
      ↓ 需要细节时
    agent_tools() 里那 5 个工具            bili_read / bili_info / bili_transcript
      ↓                                    / bili_danmaku / bili_summary
    harness.bili_api（纯能力层）            真正的四路抓取，带 cookie、走缓存
      ↓
    reply_audit 第三类断言                 "没读到"必须是**真没读到**：
                                           拿不到字幕/弹幕时注入文本里明说"别编"，
                                           模型就没有瞎猜的余地

所以分工是三层，缺一层都会退化：

| 层 | 谁 | 严格不许做的事 |
|---|---|---|
| 能力 | `harness/bili_api.py` | 不许有开关、不许依赖插件；拿不到就返回空 |
| 策略 | 本插件 | 不许自己写抓取；不许注入超预算；不许假装读到 |
| 编排 | `harness/qq_agent.py`（A3） | 不许在闲聊上开 agent（中位 21 秒是底线） |

`PLUGIN_AGENT` 和 `agent_tools()` 就是给 A3 用的机读声明：前者说"我在链路里负责什么"，
后者给出可以直接注册成 agent 工具的定义。没接 A3 之前，它们只是被单测钉住的契约；
接上之后，模型自己决定调 `bili_transcript` 还是 `bili_summary`。
"""

import json
import os
import re
import sys
import time

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(PLUGIN_DIR))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

PLUGIN_META = {
    "name": "bili",
    "version": "1.6.1",
    "description": "B站：读信息/字幕稿/弹幕/热评，可生成 AI 摘要；登录在本插件内。",
    "author": "EggBloom",
    "group_only": False,
    "enabled": True,
    "dependencies": [],
    "hooks": ["before_reply"],
    "priority": 55,
    "estimated_tokens": 160,
    "tags": ["B站", "读稿", "弹幕", "热评"],
    "page": "plugins/bili/ui.html",
}

DATA_DIR = os.path.join(PLUGIN_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_DEFAULTS = {
    "enabled": True,
    "auto_read": True,
    "cookie": "",
    "inject_chars": 240,     # 回复里注入的上限
    "inject_lines": 2,       # 注入几行稿子
    "with_danmaku": True,    # 抓弹幕（免签名，便宜）
    "with_comments": True,   # 抓热评
    "ttl_hours": 24,
    "qr_key": "",
    "qr_ts": 0,
    # ── 写（1.6.0）：默认全关，功能是完整的 ──
    "write_enabled": False,
    "write_dry_run": True,
    "write_daily_max": 3,
    "write_video_cooldown_h": 24,
    "write_min_seconds": 20,
    "write_text_max": 180,
    "write_up_whitelist": [],
}


# ── 落盘 ──────────────────────────────────────────────────────────
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


def _bump(key, n=1):
    st = _load(STATS_PATH, {})
    st[key] = int(st.get(key) or 0) + int(n)
    st["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(STATS_PATH, st)


def _api():
    from harness.bili_api import BiliAPI
    return BiliAPI(cookie=str(_cfg().get("cookie") or ""))


def _mask(cookie: str) -> str:
    c = str(cookie or "")
    if not c:
        return ""
    v = c.split("SESSDATA=", 1)[-1].split(";", 1)[0]
    return (v[:4] + "…" + v[-4:]) if len(v) > 10 else "…"


# ── 缓存（按 bvid#p 存整包）──────────────────────────────────────
def _cache_path(key: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_#-]", "_", key)[:80]
    return os.path.join(CACHE_DIR, f"{safe}.json")


def _cache_get(key: str):
    p = _cache_path(key)
    d = _load(p, {})
    if not d or time.time() - float(d.get("ts") or 0) > max(1, int(_cfg().get("ttl_hours") or 24)) * 3600:
        return None
    _bump("cache_hits")
    return d.get("data")


def _cache_put(key: str, data: dict):
    os.makedirs(CACHE_DIR, exist_ok=True)
    _save(_cache_path(key), {"ts": time.time(), "data": data})


def _cache_size() -> int:
    try:
        return len([f for f in os.listdir(CACHE_DIR) if f.endswith(".json")])
    except Exception:
        return 0


def clear_cache() -> dict:
    try:
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".json"):
                os.remove(os.path.join(CACHE_DIR, f))
    except Exception:
        pass
    return {"ok": True}


# ── 读一个链接（核心）────────────────────────────────────────────
def read(url: str, fresh: bool = False) -> dict:
    """链接 → `{ok, data:{info, transcript, danmaku, comments, inject}, error}`。"""
    from harness.bili_api import parse_url, cues_to_text, hot_words
    cfg = _cfg()
    info0 = parse_url(str(url or ""))
    if not info0:
        return {"ok": False, "error": "这句里没有 BV/av/b23 链接"}
    if info0.get("short_url"):
        try:
            import requests
            r = requests.get(info0["short_url"], timeout=15, allow_redirects=True,
                             headers={"User-Agent": _UA})
            info0 = {**parse_url(str(r.url or "")), "p": info0.get("p")}
        except Exception:
            return {"ok": False, "error": "短链跳转失败"}
    page = int(info0.get("p") or 1)
    key = f"{info0.get('bvid') or info0.get('aid')}#{page}"
    if not fresh:
        hit = _cache_get(key)
        if hit:
            return {"ok": True, "data": hit, "cached": True, "error": ""}

    api = _api()
    info = api.view(info0.get("bvid", ""), int(info0.get("aid") or 0))
    if not info:
        return {"ok": False, "error": "取视频信息失败（链接无效或被风控）"}
    pages = info.get("pages") or [{}]
    cur = pages[min(max(0, page - 1), len(pages) - 1)] or {}
    cid = int(cur.get("cid") or info.get("cid") or 0)

    tr = api.transcript(info["bvid"], info["aid"], cid)
    cues = tr.get("cues") or []
    dms = api.danmaku(cid) if cfg.get("with_danmaku") else []
    cms = api.comments(info["aid"], ps=20) if cfg.get("with_comments") else []

    data = {
        "info": {**info, "page": page, "cid": cid,
                 "part": cur.get("part") or "", "page_count": len(pages)},
        "transcript": {"cues": cues, "lan_doc": tr.get("lan_doc", ""),
                       "source": tr.get("source", "none"),
                       "text": cues_to_text(cues, head=f"# {info.get('title', '')}")},
        "danmaku": {"total": len(dms), "hot": hot_words([d["text"] for d in dms]),
                    "samples": dms[:40]},
        "comments": cms[:12],
        "inject": "",
    }
    data["inject"] = _inject_text(data, int(cfg.get("inject_chars") or 240),
                                  int(cfg.get("inject_lines") or 2))
    _cache_put(key, data)
    _bump("read_ok")
    if cues:
        _bump("with_subtitle")
    return {"ok": True, "data": data, "cached": False, "error": ""}


def _inject_text(data: dict, max_chars: int, max_lines: int) -> str:
    """把整包压成能塞进回复预算的一小段。"""
    info = data.get("info") or {}
    title = str(info.get("title") or "").strip()
    owner = str(info.get("owner") or "").strip()
    if not title:
        return ""
    st = info.get("stat") or {}
    bits = []
    if st.get("view"):
        bits.append(f"{_fmt_num(st['view'])}播放")
    if owner:
        bits.append(owner)
    head = f"【B站】{title}" + (f"（{'·'.join(bits)}）" if bits else "")

    cues = (data.get("transcript") or {}).get("cues") or []
    hot = (data.get("danmaku") or {}).get("hot") or []
    parts = [head]
    if cues:
        body = " ".join(str(c.get("text") or "") for c in cues[: int(max_lines) * 4])
        parts.append(f"稿：{body}…（共 {len(cues)} 条）")
    elif hot:
        parts.append("没字幕轨；弹幕里反复出现：" + "、".join(
            f"{h['word']}" for h in hot[:5]))
    else:
        parts.append("没字幕轨，也没抓到弹幕 —— 别编内容，直接说没读到")
    if hot and cues:
        parts.append("弹幕高频：" + "、".join(h["word"] for h in hot[:5]))
    return "\n".join(parts)[:max_chars]


def _fmt_num(n: int) -> str:
    n = int(n or 0)
    if n >= 100000000:
        return f"{n / 100000000:.1f}亿"
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


# ── 钩子 ──────────────────────────────────────────────────────────
def before_reply(message: str = "", group_id: str = "", context: dict = None, **kwargs):
    try:
        cfg = _cfg()
        if not cfg.get("enabled") or not cfg.get("auto_read"):
            return None
        msg = str(message or "")
        if len(msg) < 12 and "BV" not in msg:
            return None
        from harness.bili_api import parse_url
        if not parse_url(msg):
            return None
        t0 = time.time()
        r = read(msg)
        if not r.get("ok"):
            return None
        text = (r.get("data") or {}).get("inject") or ""
        if not text:
            return None
        return {"action": "inject", "content": text,
                "meta": {"plugin": "bili", "elapsed": round(time.time() - t0, 2),
                         "cached": bool(r.get("cached"))}}
    except Exception:
        return None


# ── 面板动作 ──────────────────────────────────────────────────────
def get_config() -> dict:
    cfg = _cfg()
    return {"enabled": bool(cfg.get("enabled")), "auto_read": bool(cfg.get("auto_read")),
            "inject_chars": int(cfg.get("inject_chars") or 240),
            "inject_lines": int(cfg.get("inject_lines") or 2),
            "with_danmaku": bool(cfg.get("with_danmaku")),
            "with_comments": bool(cfg.get("with_comments")),
            "ttl_hours": int(cfg.get("ttl_hours") or 24),
            "logged_in": bool(cfg.get("cookie")),
            "cookie_tail": _mask(cfg.get("cookie")),
            "write": write_status()}


def set_config(params: dict = None) -> dict:
    if not isinstance(params, dict):
        return {"ok": False, "error": "params must be dict"}
    cfg = _cfg()
    for k in ("enabled", "auto_read", "inject_chars", "inject_lines",
              "with_danmaku", "with_comments", "ttl_hours"):
        if k in params:
            cfg[k] = params[k]
    _save(CONFIG_PATH, cfg)
    return {"ok": True, "config": get_config()}


def parse(params: dict = None) -> dict:
    """面板主入口：给链接，回整包（信息/文稿/弹幕/热评/注入文本）。"""
    p = params or {}
    url = str(p.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "需要一个链接"}
    r = read(url, fresh=bool(p.get("fresh")))
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error") or "读取失败"}
    return {"ok": True, "cached": bool(r.get("cached")), "data": r.get("data")}


def probe(params: dict = None) -> dict:
    r = parse(params)
    if r.get("ok"):
        r["inject_preview"] = (r.get("data") or {}).get("inject", "")
    return r


def login_status() -> dict:
    cfg = _cfg()
    ck = str(cfg.get("cookie") or "")
    if not ck:
        return {"logged_in": False, "cookie_tail": "", "uname": "",
                "hint": "未登录：能看到标题/弹幕/热评，但读不到字幕稿"}
    try:
        from harness.bili_api import BiliAPI
        j = BiliAPI(cookie=ck)._j("https://api.bilibili.com/x/web-interface/nav", {})
        d = j.get("data") or {}
        ok = bool(d.get("isLogin"))
        return {"logged_in": ok, "cookie_tail": _mask(ck), "uname": d.get("uname") or "",
                "hint": "" if ok else "登录已失效，请重新扫码或粘贴 SESSDATA"}
    except Exception as e:
        return {"logged_in": False, "cookie_tail": _mask(ck), "uname": "",
                "hint": f"校验失败：{type(e).__name__}"}


def login_set(params: dict = None) -> dict:
    raw = str((params or {}).get("cookie") or "").strip()
    if not raw:
        return {"ok": False, "error": "cookie 不能为空"}
    cookie = raw if "SESSDATA=" in raw else f"SESSDATA={raw}"
    cfg = _cfg()
    cfg["cookie"] = cookie
    _save(CONFIG_PATH, cfg)
    st = login_status()
    if not st.get("logged_in"):
        cfg["cookie"] = ""
        _save(CONFIG_PATH, cfg)
        return {"ok": False, "error": st.get("hint") or "登录校验不通过"}
    _bump("login")
    return {"ok": True, "uname": st.get("uname", ""), "cookie_tail": _mask(cookie)}


def logout() -> dict:
    cfg = _cfg()
    cfg["cookie"] = ""
    _save(CONFIG_PATH, cfg)
    return {"ok": True}


# ── 扫码登录（弹 cmd 窗口）────────────────────────────────────────
def _qr_ascii(text: str) -> str:
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
    """二维码弹到独立 cmd 窗口（等宽字体天然正方形，手机好扫）。"""
    try:
        import subprocess
        import tempfile
        script = (
            "# -*- coding: utf-8 -*-\n"
            "print(" + repr(qr_text) + ")\n"
            "print()\n"
            "print(" + repr(tip or "用 B 站 App 扫码登录；扫完这个窗口可以关掉") + ")\n"
            "try:\n    input()\nexcept Exception:\n    pass\n"
        )
        p = os.path.join(tempfile.gettempdir(), "egg_bili_qr.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(script)
        subprocess.Popen(
            f'start "" cmd /c "chcp 65001 >nul & mode con cols=100 lines=60 & '
            f'{sys.executable} \\"{p}\\""', shell=True)
        return True
    except Exception:
        return False


def login_qr_start(params: dict = None) -> dict:
    if not _qr_ascii("test"):
        return {"ok": False, "error": "没装 segno，无法生成二维码；可改用粘贴 SESSDATA"}
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": _UA, "Referer": "https://www.bilibili.com/"})
        j = s.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                  timeout=15).json()
        if j.get("code") != 0:
            return {"ok": False, "error": f"生成失败：{j.get('message') or j.get('code')}"}
        d = j.get("data") or {}
        key, url = str(d.get("qrcode_key") or ""), str(d.get("url") or "")
        if not key or not url:
            return {"ok": False, "error": "接口没返回 qrcode_key"}
        cfg = _cfg()
        cfg["qr_key"], cfg["qr_ts"] = key, time.time()
        _save(CONFIG_PATH, cfg)
        art = _qr_ascii(url)
        popped = _pop_qr_window(art) if (params or {}).get("pop", True) else False
        _bump("qr_start")
        return {"ok": True, "qrcode_key": key, "popped": popped, "ascii": art,
                "hint": "已弹出窗口，用 B 站 App 扫码" if popped else "二维码已生成"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def login_qr_poll(params: dict = None) -> dict:
    cfg = _cfg()
    key = str((params or {}).get("qrcode_key") or cfg.get("qr_key") or "")
    if not key:
        return {"ok": False, "status": "none", "error": "还没有二维码"}
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": _UA, "Referer": "https://www.bilibili.com/"})
        j = s.get("https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                  params={"qrcode_key": key}, timeout=15).json()
        d = j.get("data") or {}
        code = d.get("code")
        if code == 0:
            jar = {c.name: c.value for c in s.cookies}
            ck = "; ".join(f"{k}={v}" for k, v in jar.items()
                           if k in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3"))
            if "SESSDATA" not in ck:
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(str(d.get("url") or "")).query)
                ck = "; ".join(f"{k}={v[0]}" for k, v in q.items()
                               if k in ("SESSDATA", "bili_jct", "DedeUserID"))
            if "SESSDATA" not in ck:
                return {"ok": False, "status": "error", "error": "没拿到 SESSDATA"}
            cfg["cookie"] = ck
            cfg.pop("qr_key", None)
            _save(CONFIG_PATH, cfg)
            _bump("login")
            st = login_status()
            return {"ok": True, "status": "ok", "uname": st.get("uname", ""),
                    "cookie_tail": _mask(ck)}
        if code == 86101:
            return {"ok": True, "status": "waiting", "hint": "还没扫码"}
        if code == 86090:
            return {"ok": True, "status": "scanned", "hint": "已扫码，请在手机上确认"}
        if code == 86038:
            cfg.pop("qr_key", None)
            _save(CONFIG_PATH, cfg)
            return {"ok": True, "status": "expired", "hint": "二维码过期了，重新点一次"}
        return {"ok": True, "status": "unknown", "hint": f"状态码 {code}"}
    except Exception as e:
        return {"ok": False, "status": "error", "error": f"{type(e).__name__}: {e}"}


# ── AI 摘要 ──────────────────────────────────────────────────────
def summary(params: dict = None) -> dict:
    """把稿子 + 弹幕梗 + 热评压成 3–5 句。用主模型（和看图同一条通道）。"""
    p = params or {}
    data = p.get("data") or {}
    if not data:
        url = str(p.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "要么给 data，要么给链接"}
        r = read(url)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error")}
        data = r.get("data") or {}
    info = data.get("info") or {}
    tr = data.get("transcript") or {}
    cues = tr.get("cues") or []
    if not cues:
        return {"ok": False, "error": "没有字幕稿，没法摘要（可以先看弹幕/热评）"}
    body = " ".join(str(c.get("text") or "") for c in cues[:400])
    hot = "、".join(h["word"] for h in ((data.get("danmaku") or {}).get("hot") or [])[:10])
    cm = " / ".join((c.get("text") or "")[:60] for c in (data.get("comments") or [])[:5])
    prompt = (
        f"标题：{info.get('title', '')}\nUP：{info.get('owner', '')}\n"
        f"字幕稿：{body[:6000]}\n弹幕高频：{hot}\n热评：{cm}\n\n"
        "用 3–5 句中文说清：这视频讲了什么、重点在哪、有什么值得注意的观点或笑点。"
        "只依据上面的材料，别编。不要用列表，不要客套。")
    try:
        text = _llm(prompt, "你是读稿助手，只依据材料说话。", 700)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not text:
        return {"ok": False, "error": "模型返回空"}
    _bump("summary")
    return {"ok": True, "summary": text}


def _llm(prompt: str, system: str = "", max_tokens: int = 600,
         temperature: float = 0.3) -> str:
    """一次性调用主模型（关思考）。失败返回 ""，绝不抛。"""
    try:
        from config import load_config, get_provider_api_key
        from ai_client import AIClient
        cfg = load_config()
        pid = str(cfg.get("api_provider") or "openai")
        key = get_provider_api_key(pid) or cfg.get("api_key") or cfg.get("deepseek_api_key", "")
        if not key:
            return ""
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        client = AIClient(cfg.get("api_url"), key, cfg.get("model_name", "deepseek-flash"),
                          provider=pid, temperature=temperature,
                          extra_body={"thinking": {"type": "disabled"}})
        r = client.call(msgs, max_tokens=max_tokens)
        text = r[0] if isinstance(r, (tuple, list)) else (r or "")
        return str(text or "").strip()
    except Exception:
        return ""


def draft_comment(params: dict = None) -> dict:
    """按视频内容生成一条**能发的**评论草稿。不发送，只起草。

    素材：标题 / UP / 前几条热评 / 弹幕高频 / 稿子开头。
    约束：一条、15–40 字、口语、不引战、不复读标题、不空洞。
    """
    p = params or {}
    url = str(p.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "需要一个链接"}
    r = read(url)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error") or "读取失败"}
    d = r.get("data") or {}
    info = d.get("info") or {}
    cues = (d.get("transcript") or {}).get("cues") or []
    hot = "、".join(h["word"] for h in ((d.get("danmaku") or {}).get("hot") or [])[:8])
    cm = " / ".join((c.get("text") or "")[:50] for c in (d.get("comments") or [])[:4])
    head = " ".join(str(c.get("text") or "") for c in cues[:12])[:600]
    style = str(p.get("style") or "").strip()
    prompt = (
        f"视频标题：{info.get('title', '')}\nUP：{info.get('owner', '')}\n"
        f"稿子开头：{head or '（没有字幕）'}\n弹幕高频：{hot or '（无）'}\n"
        f"现有热评：{cm or '（无）'}\n\n"
        "写一条可以发在这个视频评论区的评论。要求：\n"
        "- 一条，15–40 字，像真人在弹幕/评论区随口说的，不要书面语；\n"
        "- 别复读标题，别写\"这视频真不错\"\"UP主辛苦了\"这种空话；\n"
        "- 不引战、不带节奏、不提政治，不装熟；\n"
        "- 只输出评论正文本身，不要引号、不要解释、不要开头结尾的客套。"
        + (f"\n风格偏好：{style}" if style else ""))
    txt = _llm(prompt, "你在 B 站评论区说话，短、具体、有梗但不油。", 260, 0.8)
    if not txt:
        return {"ok": False, "error": "模型没写出东西（检查 api key / 网络）"}
    # 模型偶尔会带引号或换行，这里收一下，再按写上限截断
    txt = txt.strip().strip('"“”').splitlines()[0].strip()
    cfg = _cfg()
    limit = int(cfg.get("write_text_max") or 180)
    if len(txt) > limit:
        txt = txt[:limit]
    _bump("draft")
    return {"ok": True, "draft": txt,
            "note": "草稿而已，没发出去；确认后再点执行",
            "has_transcript": bool(cues)}


def get_stats() -> dict:
    st = _load(STATS_PATH, {})
    st["cache_size"] = _cache_size()
    st["logged_in"] = bool(_cfg().get("cookie"))
    return st


# ── 写：发言（1.6.0）────────────────────────────────────────────
# 设计前提：**默认全关**。打开后还要过一道 dry_run，确认文案没问题再真发。
# 每一道闸的理由都写在 _write_guard 里 —— 这不是形式主义，写接口翻车是封号级别的。
AUDIT_PATH = os.path.join(DATA_DIR, "actions.jsonl")


def _audit(entry: dict) -> None:
    """每次写动作（含干跑、拒绝）都留痕。一行一条 JSON，方便直接 tail。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        e = dict(entry)
        e.setdefault("ts", time.time())
        e.setdefault("date", time.strftime("%Y-%m-%d"))
        e.setdefault("at", time.strftime("%Y-%m-%d %H:%M:%S"))
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _recent_actions(n: int = 40) -> list:
    """读审计日志最近 n 条（新的在前）。读不到返回空列表。"""
    try:
        with open(AUDIT_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        out = []
        for ln in reversed(lines):
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
        return out
    except Exception:
        return []


def _write_guard(kind: str, info: dict, text: str = "") -> tuple:
    """所有写动作的唯一闸门。返回 `(ok, reason)`；reason 是给人看的一句话。

    六道闸，从便宜到贵：
      1. 总开关         —— 默认关，用户开了才算
      2. 登录 + CSRF    —— SESSDATA 和 bili_jct 缺一不可
      3. 熔断           —— 最近连续 2 次失败就停手（不重试、不换姿势）
      4. 冷却           —— 两次写之间 ≥20 秒
      5. 日上限         —— 默认 3 次/天（全站），同视频 24h 内只发一次
      6. UP 白名单      —— 配了就只对这些 UP 发言（空=不限）
    """
    cfg = _cfg()
    if not cfg.get("write_enabled"):
        return False, "写功能未启用（插件里默认关闭）"
    if not str(cfg.get("cookie") or ""):
        return False, "未登录"
    from harness.bili_api import BiliAPI
    api = BiliAPI(cookie=str(cfg.get("cookie") or ""))
    if not api.csrf():
        return False, "cookie 里没有 bili_jct（CSRF）—— 请重新扫码登录"
    t = str(text or "").strip()
    if kind in ("comment", "danmaku"):
        if not t:
            return False, "内容为空"
        limit = int(cfg.get("write_text_max") or 180) if kind == "comment" else 100
        if len(t) > limit:
            return False, f"太长了（上限 {limit} 字）"

    recent = _recent_actions(30)
    # ★ 只有"真的打到接口但失败了"才算熔断计数。被闸门挡下的（blocked）不计 ——
    #   否则用户连打三次超长文案，写功能就被自己锁死了。
    fails = 0
    for a in recent:
        if a.get("blocked"):
            continue
        if a.get("ok"):
            break
        if not a.get("dry_run") and not a.get("blocked"):
            fails += 1
    if fails >= 2:
        return False, "连续两次写失败，已停手 —— 看「最近动作」里的失败原因"

    # ★ 冷却只看**真正发出去成功过**的记录；被挡下的尝试不该锁住用户。
    #   0 是合法配置（关掉冷却），所以不能写 `or 20`。
    _min_s = cfg.get("write_min_seconds")
    _min_s = 20.0 if _min_s is None else float(_min_s)
    last_ok = next((a for a in recent
                    if a.get("ok") and not a.get("dry_run")
                    and not a.get("blocked")), None)
    if last_ok and _min_s > 0 and time.time() - float(last_ok.get("ts") or 0) < _min_s:
        return False, f"太频繁了，歇一下（两次写之间至少 {int(_min_s)} 秒）"

    today = str(time.strftime("%Y-%m-%d"))
    today_ok = [a for a in recent
                if a.get("date") == today and a.get("ok") and not a.get("dry_run")]
    daily = int(cfg.get("write_daily_max") or 3)
    if len(today_ok) >= daily:
        return False, f"今天已经发过 {len(today_ok)} 次了（上限 {daily}）"

    bvid = str((info or {}).get("bvid") or "")
    cooldown_h = float(cfg.get("write_video_cooldown_h") or 24)
    if bvid:
        for a in recent:
            if a.get("bvid") == bvid and a.get("ok") and not a.get("dry_run"):
                if time.time() - float(a.get("ts") or 0) < cooldown_h * 3600:
                    return False, f"这个视频 {int(cooldown_h)} 小时内已经发过了"

    wl = [str(x).strip() for x in (cfg.get("write_up_whitelist") or []) if str(x).strip()]
    if wl and str((info or {}).get("owner_mid") or "") not in wl:
        return False, "这个 UP 不在白名单里"

    return True, ""


def write_status() -> dict:
    cfg = _cfg()
    recent = _recent_actions(30)
    today = str(time.strftime("%Y-%m-%d"))
    today_ok = [a for a in recent
                if a.get("date") == today and a.get("ok") and not a.get("dry_run")]
    fails = 0
    for a in recent:
        if a.get("blocked"):
            continue
        if a.get("ok"):
            break
        if not a.get("dry_run"):
            fails += 1
    return {"enabled": bool(cfg.get("write_enabled")),
            "dry_run": bool(cfg.get("write_dry_run")),
            "daily_max": int(cfg.get("write_daily_max") or 3),
            "used_today": len(today_ok),
            "cooldown_h": int(cfg.get("write_video_cooldown_h") or 24),
            "min_seconds": int(cfg.get("write_min_seconds") or 20),
            "whitelist": cfg.get("write_up_whitelist") or [],
            "text_max": int(cfg.get("write_text_max") or 180),
            "breaker": fails >= 2,
            "consecutive_failures": fails,
            "recent": recent[:8],
            "total": len(recent)}


def set_write_config(params: dict = None) -> dict:
    if not isinstance(params, dict):
        return {"ok": False, "error": "params must be dict"}
    cfg = _cfg()
    for k in ("write_enabled", "write_dry_run", "write_daily_max",
              "write_video_cooldown_h", "write_min_seconds", "write_text_max",
              "write_up_whitelist"):
        if k in params:
            cfg[k] = params[k]
    if isinstance(cfg.get("write_up_whitelist"), str):
        cfg["write_up_whitelist"] = [x.strip() for x in
                                     cfg["write_up_whitelist"].replace(",", " ").split()
                                     if x.strip()]
    _save(CONFIG_PATH, cfg)
    return {"ok": True, "status": write_status()}


def _do_write(kind: str, url: str, text: str = "", **kw) -> dict:
    """写动作的统一执行：闸门 → 干跑 → 真发 → 回读 → 审计。"""
    from harness.bili_api import BiliAPI, parse_url
    t0 = time.time()
    i = parse_url(str(url or ""))
    if not i:
        return {"ok": False, "error": "需要一个含 BV/av 的链接"}
    cfg = _cfg()
    api = BiliAPI(cookie=str(cfg.get("cookie") or ""))
    info = api.view(i.get("bvid", ""), int(i.get("aid") or 0)) or {}
    okg, why = _write_guard(kind, info, text)
    entry = {"kind": kind, "bvid": info.get("bvid", ""), "owner": info.get("owner", ""),
             "text": str(text or "")[:200], "elapsed": 0}
    if not okg:
        _audit({**entry, "ok": False, "error": why, "blocked": True})
        return {"ok": False, "error": why, "blocked": True}

    if cfg.get("write_dry_run"):
        _audit({**entry, "ok": True, "dry_run": True, "elapsed": round(time.time() - t0, 2)})
        return {"ok": True, "dry_run": True,
                "text": text or "",
                "hint": "干跑：闸门都过了，真发请关掉「先干跑」"}

    aid, cid = int(info.get("aid") or 0), int(info.get("cid") or 0)
    if kind == "comment":
        r = api.post_comment(aid, text, bvid=info.get("bvid", ""))
    elif kind == "danmaku":
        r = api.send_danmaku(cid, text, int(kw.get("progress_ms") or 0))
    elif kind == "like":
        r = api.like(aid, bool(kw.get("on", True)))
    elif kind == "coin":
        r = api.coin(aid, int(kw.get("n") or 1))
    elif kind == "follow":
        r = api.follow(int(info.get("owner_mid") or 0), bool(kw.get("on", True)))
    else:
        return {"ok": False, "error": f"未知写动作：{kind}"}

    verified = None
    if r.get("ok") and kind == "comment":
        u = api.nav_user()
        verified = bool(api.find_my_comment(aid, int(u.get("mid") or 0),
                                            int(r.get("rpid") or 0), pages=2))
    entry.update({"ok": bool(r.get("ok")), "rpid": r.get("rpid"),
                  "code": r.get("code"), "error": r.get("error", ""),
                  "verified": verified, "elapsed": round(time.time() - t0, 2)})
    _audit(entry)
    out = {"ok": bool(r.get("ok")), "error": r.get("error", ""),
           "code": r.get("code"), "rpid": r.get("rpid"),
           "elapsed": entry["elapsed"]}
    if kind == "comment" and r.get("ok"):
        out["verified"] = verified
        out["hint"] = ("回读到了，评论已可见" if verified
                       else "接口收下了，但前两页没读到自己那条 —— 可能审核中")
    return out


def post_comment(params: dict = None) -> dict:
    p = params or {}
    return _do_write("comment", p.get("url", ""), str(p.get("text") or ""))


def send_danmaku(params: dict = None) -> dict:
    p = params or {}
    return _do_write("danmaku", p.get("url", ""), str(p.get("text") or ""),
                     progress_ms=p.get("progress_ms") or 0)


def like_video(params: dict = None) -> dict:
    p = params or {}
    return _do_write("like", p.get("url", ""), on=bool(p.get("on", True)))


def coin_video(params: dict = None) -> dict:
    p = params or {}
    return _do_write("coin", p.get("url", ""), n=int(p.get("n") or 1))


def follow_up(params: dict = None) -> dict:
    p = params or {}
    return _do_write("follow", p.get("url", ""), on=bool(p.get("on", True)))


def write_log(params: dict = None) -> dict:
    n = int((params or {}).get("n") or 20)
    return {"ok": True, "items": _recent_actions(n)}


#: 给 A3（harness/qq_agent.py）的机读声明：这个插件在 agent 链路里负责什么。
#: 不是装饰 —— 单测会钉住形状，接 A3 时直接读它决定要不要注册工具、算多贵的成本。
PLUGIN_AGENT = {
    "role": "capability",              # capability=能力供给；不是 conversation=对话插件
    "channel": ["private", "group"],
    "intents": ["这个视频讲了什么", "帮我读稿", "有没有字幕", "弹幕在刷什么",
                "热评怎么说", "这链接是啥", "总结一下这个B站视频"],
    "tools": ["bili_read", "bili_info", "bili_transcript",
              "bili_danmaku", "bili_comments", "bili_summary", "bili_draft_comment",
              "bili_comment", "bili_send_danmaku", "bili_like",
              "bili_coin", "bili_follow"],
    # 写能力（1.6.0）：默认关，开了也先干跑。agent 调之前必须先看 write_status。
    "write": {"enabled_by_default": False, "dry_run_by_default": True,
              "requires": ["login", "bili_jct"], "guards": ["熔断", "冷却",
                                                            "日上限", "同视频冷却",
                                                            "UP 白名单"]},
    # 成本画像：给 A3 做预算决策用（网络请求 + 可能的 1 次 LLM）
    "cost": {"network": 3, "llm_calls": "0 或 1（只有 summary）", "est_seconds": "2–4"},
    # 降级链：任何一环拿不到，都必须落到"如实说"，不许猜
    "degrade": [
        "没登录 → 只有信息/弹幕/热评，字幕轨为空",
        "没字幕 → 用弹幕高频词顶上（弹幕免签名，最稳）",
        "弹幕也没有 → 注入文本里写明「别编内容」",
    ],
    "needs": ["network"],
    "gate": "harness.qq_agent.should_use_agent（查/看/听 + 视频/链接）",
}


def agent_tools() -> list:
    """给 agent 的工具定义（OpenAI function 形状，A3 直接注册）。

    五个工具刻意拆开而不是一个 `bili(url, what)`：
    模型一次只该取它需要的，`bili_read` 是"全都要"的贵选项。
    """
    url_p = {"type": "object",
             "properties": {"url": {"type": "string",
                                    "description": "含 BV 号 / av 号 / b23 短链的文本"}},
             "required": ["url"], "additionalProperties": False}
    return [
        {"name": "bili_info",
         "desc": "只读视频基本信息：标题、UP、时长、分P、播放/点赞/弹幕/评论数。最便宜。",
         "params": url_p},
        {"name": "bili_read",
         "desc": "一次取整包：信息 + 字幕稿 + 弹幕高频 + 热评 + 压缩注入文本。最全，也最慢。",
         "params": url_p},
        {"name": "bili_transcript",
         "desc": "只取字幕稿（带 mm:ss 时间戳）。需要已登录，否则返回空并说明原因。",
         "params": url_p},
        {"name": "bili_danmaku",
         "desc": "只取弹幕：总数 + 高频词 + 样例。免登录，最稳，适合判断观众反应。",
         "params": url_p},
        {"name": "bili_comments",
         "desc": "只取热评（按热度），含点赞数。免登录。",
         "params": url_p},
        {"name": "bili_summary",
         "desc": "把稿子 + 弹幕梗 + 热评压成 3–5 句摘要。会调一次模型；没稿子时会拒绝。",
         "params": {"type": "object", "properties": {"url": {"type": "string"}},
                    "required": ["url"], "additionalProperties": False}},
        # ── 写（默认关；调之前先看 write_status，被闸门挡住会返回 blocked=true）──
        {"name": "bili_draft_comment",
         "desc": "按视频内容起草一条评论（不发送）。适合先给用户看再决定发不发。",
         "params": {"type": "object", "properties": {"url": {"type": "string"},
                                                     "style": {"type": "string"}},
                    "required": ["url"], "additionalProperties": False}},
        {"name": "bili_comment",
         "desc": "在视频下发一条评论。需要登录 + bili_jct；默认干跑。"
                 "成功后会回读校验是否真的可见。",
         "params": {"type": "object",
                    "properties": {"url": {"type": "string"},
                                   "text": {"type": "string", "description": "评论正文"}},
                    "required": ["url", "text"], "additionalProperties": False}},
        {"name": "bili_send_danmaku",
         "desc": "发一条视频弹幕（1–100 字）。",
         "params": {"type": "object",
                    "properties": {"url": {"type": "string"},
                                   "text": {"type": "string"},
                                   "progress_ms": {"type": "integer",
                                                   "description": "出现在第几毫秒"}},
                    "required": ["url", "text"], "additionalProperties": False}},
        {"name": "bili_like",
         "desc": "给视频点赞/取消。",
         "params": {"type": "object", "properties": {"url": {"type": "string"},
                                                     "on": {"type": "boolean"}},
                    "required": ["url"], "additionalProperties": False}},
        {"name": "bili_coin",
         "desc": "给视频投币（1 或 2 枚）。",
         "params": {"type": "object", "properties": {"url": {"type": "string"},
                                                     "n": {"type": "integer", "enum": [1, 2]}},
                    "required": ["url"], "additionalProperties": False}},
        {"name": "bili_follow",
         "desc": "关注/取关该视频的 UP。",
         "params": {"type": "object", "properties": {"url": {"type": "string"},
                                                     "on": {"type": "boolean"}},
                    "required": ["url"], "additionalProperties": False}},
    ]


def agent_call(name: str, args: dict = None) -> dict:
    """agent 侧的统一入口：`agent_call("bili_danmaku", {"url": ...})`。

    返回 `{ok, data, error}`，**绝不抛** —— agent 拿到什么就说什么，
    拿到 `ok=False` 时它应该转述 error，而不是自己编一个结果。
    """
    a = args or {}
    url = str(a.get("url") or "")
    try:
        if name == "bili_info":
            from harness.bili_api import BiliAPI, parse_url
            i = parse_url(url)
            v = BiliAPI(str(_cfg().get("cookie") or "")).view(
                i.get("bvid", ""), int(i.get("aid") or 0))
            return {"ok": bool(v), "data": v, "error": "" if v else "取视频信息失败"}
        if name in ("bili_read", "probe"):
            return parse({"url": url})
        if name in ("bili_transcript", "bili_danmaku", "bili_comments"):
            r = read(url)
            if not r.get("ok"):
                return {"ok": False, "data": None, "error": r.get("error", "")}
            d = r.get("data") or {}
            if name == "bili_transcript":
                tr = d.get("transcript") or {}
                return {"ok": True, "data": tr, "error": "",
                        "note": "" if tr.get("cues") else "没有字幕轨（需要登录）"}
            if name == "bili_danmaku":
                return {"ok": True, "data": d.get("danmaku") or {}, "error": ""}
            return {"ok": True, "data": d.get("comments") or [], "error": ""}
        if name == "bili_summary":
            return summary({"url": url})
        if name == "bili_draft_comment":
            return draft_comment({"url": url, "style": a.get("style", "")})
        if name in ("bili_comment", "bili_send_danmaku", "bili_like",
                    "bili_coin", "bili_follow"):
            fn = {"bili_comment": post_comment, "bili_send_danmaku": send_danmaku,
                  "bili_like": like_video, "bili_coin": coin_video,
                  "bili_follow": follow_up}[name]
            return fn({**a, "url": url})
        return {"ok": False, "data": None, "error": f"没有这个工具：{name}"}
    except Exception as e:
        return {"ok": False, "data": None, "error": f"{type(e).__name__}: {e}"}


PLUGIN_ACTIONS = {
    "get_config": get_config,
    "set_config": set_config,
    "parse": parse,
    "probe": probe,
    "summary": summary,
    "login_set": login_set,
    "login_qr_start": login_qr_start,
    "login_qr_poll": login_qr_poll,
    "login_status": login_status,
    "logout": logout,
    "get_stats": get_stats,
    "clear_cache": clear_cache,
    # ── 写（1.6.0）──
    "write_status": write_status,
    "set_write_config": set_write_config,
    "post_comment": post_comment,
    "send_danmaku": send_danmaku,
    "like_video": like_video,
    "coin_video": coin_video,
    "follow_up": follow_up,
    "write_log": write_log,
    "draft_comment": draft_comment,
}
