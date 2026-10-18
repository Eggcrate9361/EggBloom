# -*- coding: utf-8 -*-
"""free_llm.py —— 免费模型直连客户端（dsh-our-free-model 的两条免密车道）
=====================================================================

用途
----
EggBloom 的**旁路推理**（群聊决策读空气、图片理解）不该占用主对话模型的额度，
也不该各自去接第三方云（智谱等，key 分散、还要处理配额）。这里直接调用
`dsh-our-free-model`（https://github.com/Ebony-Vinyl/dsh-our-free-model）
使用的两条**免密/免鉴权**上游车道：

  1. **zen 车道**（opencode.ai）—— 池化凭据 `Bearer public` + 客户端指纹头。
     ⚠ 该车道要求 `stream: true`：非流式会被 403 FreeTierError
     （原文 "OpenCode's free tier can only be used from within OpenCode"）。
     另需在 body 里声明四个小写工具名 `bash/glob/grep/read`（免费层指纹闸门），
     `tool_choice: none` 让模型别真的去调它们。
  2. **kilo 车道**（api.kilo.ai）—— 完全免鉴权：一个 POST 就够。

模型清单随上游变动，统一在 CHAINS 里维护；模型 id 用 `lane:model` 表示
（kilo 的 id 自带冒号后缀如 `:free`，所以按**第一个**冒号切分）。

⚠ 隐私提醒：两条车道都是公共免费池，上游可能记录提示词用于改进服务。
   群聊内容走这里意味着它离开了本机 —— 在意的话请在配置里换成自建/带 key 的车道。

公开 API
--------
    chat(messages, models=None, max_tokens=..., timeout=...) -> dict
    chat_text(prompt, ...) -> str
    chat_vision(image_ref, prompt, ...) -> dict
    list_models(lane=None) -> list
    text_models() / vision_models() -> [("lane", "model"), ...]
    probe() -> dict        # 诊断：逐条链试一次
"""
import base64
import hashlib
import json
import os
import pathlib
import random
import threading
import time
import urllib.error
import urllib.request

__all__ = ["chat", "chat_text", "chat_vision", "list_models", "text_models", "vision_models",
           "probe", "sources"]

# ══════════════════════════════════════════════════════════════════
# 车道定义
# ══════════════════════════════════════════════════════════════════
ZEN_BASE = os.environ.get("EGGBLOOM_ZEN_BASE", "https://opencode.ai")
KILO_BASE = os.environ.get("EGGBLOOM_KILO_BASE", "https://api.kilo.ai/api/gateway")

ZEN_UA = "opencode/1.18.31"          # 上游要求 opencode/<version>，>=1.17
ZEN_AUTH = "Bearer public"           # 池化免密凭据（没有 per-user secret）
FINGERPRINT_TOOLS = ("bash", "glob", "grep", "read")   # 免费层指纹闸门要求的四个小写名

# zen 车道里走非 chat 端点的模型（保留定义，避免将来加模型时踩坑）
ZEN_RESPONSES_MODELS = {"muse-spark-1.2-contributor-free", "muse-spark-1.3-contributor-free"}

_B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_LOCK = threading.Lock()
_SESSIONS = {}                       # seed -> session id（同一对话复用，否则配额 429）


def _b62(n=14):
    return "".join(random.choice(_B62) for _ in range(n))


def _session_for(seed: str) -> str:
    """稳定会话 id：上游按会话计配额，每次新建会迅速 429。

    形状 `ses_<12hex><14base62>`（与官方客户端一致）；同一 seed 跨进程重启也稳定。
    """
    seed = (seed or "global").strip() or "global"
    with _LOCK:
        got = _SESSIONS.get(seed)
    if got:
        return got
    d = hashlib.sha256(f"eggbroom-free\0{seed}".encode("utf-8")).digest()
    sid = "ses_" + d[:6].hex() + "".join(_B62[b % 62] for b in d[6:20])
    with _LOCK:
        _SESSIONS[seed] = sid
    return sid


def _request_id(seed: str) -> str:
    d = hashlib.sha256(f"eggbroom-free-req\0{seed}\0{time.time()}".encode("utf-8")).digest()
    return "msg_" + d[:6].hex() + "".join(_B62[b % 62] for b in d[6:20])


# ══════════════════════════════════════════════════════════════════
# 模型链（2026-10-12 逐条实测的延迟）
#   zen  space-bunny-free        文本 2~5s / 视觉 3.3s（描述准确）
#   kilo stepfun/step-5-preview-free  文本 1.2s / 视觉 3.6s
#   zen  mimo-v2.6-flash-free    文本 1.4~24s（波动大，放后面）
#   kilo openrouter/free         视觉 8~37s（慢，兜底）
# ══════════════════════════════════════════════════════════════════
DEFAULT_TEXT_CHAIN = [
    ("zen", "space-bunny-free"),
    ("kilo", "stepfun/step-5-preview-free"),
    ("zen", "mimo-v2.6-flash-free"),
]
# 视觉链（实测：space-bunny 2.8~3.3s 描述最细；mimo 3.6s 可用；
#  kilo 的 nano-omni 12.5s 兜底。其余要么不支持图、要么只回 "User Safety: safe"）
DEFAULT_VISION_CHAIN = [
    ("zen", "space-bunny-free"),
    ("zen", "mimo-v2.6-flash-free"),
    ("kilo", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"),
]

# 清单（供 UI/诊断展示；上游会变，不以此为唯一依据）
ROSTER = {
    "zen": [
        "space-bunny-free", "mimo-v2.6-flash-free", "nemotron-3.5-lightning-free",
        "longcat-2.5-preview-free", "ling-3.1-flash-free", "step-5-preview-free",
        "jev-1.13-free",
    ],
    "kilo": [
        "stepfun/step-5-preview-free", "kilo-auto/free", "openrouter/free",
        "thinkingmachines/inkling-small:free", "dots-studio/dots-3-note-preview:free",
        "nvidia/nemotron-3.5-lightning:free", "nvidia/nemotron-3-super-120b-a12b:free",
        "inclusionai/ling-3.1-flash", "poolside/laguna-s-2.1:free", "poolside/laguna-xs-2.1:free",
        "stealth/glyph-cluster", "liquid/lfm-2.5-2.6b:free", "cohere/north-mini-code:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    ],
}


def _parse_spec(spec):
    """`lane:model` → (lane, model)；kilo 的自带冒号（:free）在模型名里保留。"""
    lane, sep, model = str(spec).partition(":")
    lane = lane.strip().lower()
    if not sep or lane not in ("zen", "kilo"):
        return ("zen", str(spec).strip())
    return (lane, model.strip())


def _env_chain(name):
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            out.append(_parse_spec(part))
    return out or None


def text_models():
    return _env_chain("EGGBLOOM_FREE_TEXT_MODELS") or list(DEFAULT_TEXT_CHAIN)


def vision_models():
    return _env_chain("EGGBLOOM_FREE_VISION_MODELS") or list(DEFAULT_VISION_CHAIN)


def list_models(lane=None):
    if lane:
        return list(ROSTER.get(lane.lower(), []))
    return [{"lane": k, "models": list(v)} for k, v in ROSTER.items()]


# ══════════════════════════════════════════════════════════════════
# HTTP
# ══════════════════════════════════════════════════════════════════
def _zen_decoy_tools():
    return [{"type": "function", "function": {
        "name": n, "description": "This tool is currently unavailable and must not be used.",
        "parameters": {"type": "object", "properties": {}}}} for n in FINGERPRINT_TOOLS]


def _sse_text(raw: str) -> str:
    """从 SSE 流里拼出正文（忽略 reasoning_content 与 keep-alive 注释）。"""
    out = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            d = json.loads(payload)
        except Exception:
            continue
        ch = (d.get("choices") or [{}])[0]
        c = (ch.get("delta") or {}).get("content")
        if isinstance(c, str):
            out.append(c)
    return "".join(out).strip()


def _http(url, body, headers, timeout):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")[:400]
        except Exception:
            return e.code, str(e)
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _call_zen(model, messages, max_tokens, timeout):
    """zen 车道：免密 + 指纹头 + **必须 stream**（非流式会被 403）。"""
    hdr = {
        "content-type": "application/json",
        "authorization": ZEN_AUTH,
        "user-agent": ZEN_UA,
        "x-opencode-client": "desktop",
        "x-opencode-session": _session_for("eggbroom"),
        "x-opencode-request": _request_id(model),
        "x-opencode-project": "global",
        "accept": "text/event-stream",
    }
    body = {"model": model, "messages": messages, "stream": True,
            "max_tokens": int(max_tokens), "tools": _zen_decoy_tools(), "tool_choice": "none"}
    st, raw = _http(f"{ZEN_BASE}/zen/v1/chat/completions", body, hdr, timeout)
    if st != 200:
        return None, f"zen/{model}: HTTP {st} {raw[:160]}"
    text = _sse_text(raw)
    if not text:
        return None, f"zen/{model}: 空响应（{raw[:80]}）"
    return text, ""


def _call_kilo(model, messages, max_tokens, timeout):
    """kilo 车道：完全免鉴权，一个 POST。"""
    hdr = {"content-type": "application/json", "accept": "application/json",
           "user-agent": "eggbroom"}
    body = {"model": model, "messages": messages, "max_tokens": int(max_tokens), "stream": False}
    st, raw = _http(f"{KILO_BASE}/chat/completions", body, hdr, timeout)
    if st != 200:
        return None, f"kilo/{model}: HTTP {st} {raw[:160]}"
    try:
        d = json.loads(raw)
    except Exception:
        return None, f"kilo/{model}: 非法 JSON {raw[:120]}"
    ch = (d.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    c = msg.get("content")
    if isinstance(c, list):
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    text = (c or "").strip()
    if not text:
        err = (d.get("error") or {}).get("message") if isinstance(d.get("error"), dict) else None
        return None, f"kilo/{model}: 空响应{(' · ' + str(err)[:100]) if err else ''}"
    return text, ""


def _call_one(lane, model, messages, max_tokens, timeout):
    try:
        if lane == "kilo":
            return _call_kilo(model, messages, max_tokens, timeout)
        return _call_zen(model, messages, max_tokens, timeout)
    except Exception as e:
        return None, f"{lane}/{model}: 调用异常 {type(e).__name__}: {e}"


# ══════════════════════════════════════════════════════════════════
# 公开接口
# ══════════════════════════════════════════════════════════════════
def chat(messages, models=None, max_tokens=400, timeout=90) -> dict:
    """按模型链逐个尝试。返回 {ok, text, lane, model, error, elapsed, tried}。"""
    chain = list(models or text_models())
    started = time.time()
    errs = []
    for lane, model in chain:
        text, err = _call_one(lane, model, messages, max_tokens, timeout)
        if text:
            return {"ok": True, "text": text, "lane": lane, "model": model, "error": "",
                    "elapsed": round(time.time() - started, 2), "tried": len(errs) + 1}
        errs.append(err)
    return {"ok": False, "text": "", "lane": "", "model": "", "elapsed": round(time.time() - started, 2),
            "error": " | ".join(e for e in errs if e)[:400] + f"（共 {len(chain)} 条链）",
            "tried": len(chain)}


def chat_text(prompt, models=None, max_tokens=400, timeout=90) -> str:
    r = chat([{"role": "user", "content": prompt}], models=models, max_tokens=max_tokens, timeout=timeout)
    return r["text"] if r.get("ok") else ""


def _image_uri(image_ref):
    ref = str(image_ref or "").strip()
    if not ref:
        raise ValueError("empty image ref")
    if ref.startswith(("data:", "http://", "https://")):
        return ref
    p = pathlib.Path(ref)
    if not p.is_file():
        raise FileNotFoundError(f"image not found: {ref}")
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}.get(
        p.suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


def chat_vision(image_ref, prompt, models=None, max_tokens=400, timeout=120) -> dict:
    """图片 → 文字描述。返回 {ok, summary, lane, model, error, elapsed}。"""
    try:
        uri = _image_uri(image_ref)
    except Exception as e:
        return {"ok": False, "summary": "", "lane": "", "model": "", "elapsed": 0.0,
                "error": f"image 解析失败: {e}"}
    msgs = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": uri}},
        {"type": "text", "text": prompt},
    ]}]
    r = chat(msgs, models=models or vision_models(), max_tokens=max_tokens, timeout=timeout)
    return {"ok": r.get("ok", False), "summary": r.get("text", ""), "lane": r.get("lane", ""),
            "model": r.get("model", ""), "error": r.get("error", ""), "elapsed": r.get("elapsed", 0.0)}


def sources() -> dict:
    return {"text_chain": [f"{l}:{m}" for l, m in text_models()],
            "vision_chain": [f"{l}:{m}" for l, m in vision_models()],
            "zen_base": ZEN_BASE, "kilo_base": KILO_BASE}


def probe() -> dict:
    """诊断：文本链与视觉链各试一次（会真实发起请求，但都是免费车道）。"""
    out = {"text": None, "vision": None}
    t0 = time.time()
    r = chat_text("只回两个字：在的", max_tokens=24, timeout=60)
    out["text"] = {"ok": bool(r), "text": r[:40], "elapsed": round(time.time() - t0, 1)}
    t0 = time.time()
    # 1×1 合法 JPEG（上游会拒绝 1×1 PNG —— 实测 HTTP 400 invalid_request）
    tiny = ("/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
            "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAA"
            "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")
    v = chat_vision(f"data:image/jpeg;base64,{tiny}", "这是什么？一个字回答", max_tokens=24, timeout=90)
    out["vision"] = {"ok": bool(v.get("ok")), "model": v.get("model", ""),
                     "summary": (v.get("summary") or "")[:40], "error": (v.get("error") or "")[:120],
                     "elapsed": round(time.time() - t0, 1)}
    out["ok"] = bool(out["text"]["ok"])
    return out


if __name__ == "__main__":
    print(json.dumps({"sources": sources(), "probe": probe()}, ensure_ascii=False, indent=2))
