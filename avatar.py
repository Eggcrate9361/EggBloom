# -*- coding: utf-8 -*-
"""avatar.py —— 头像：取图、看描述、缓存（2026-10-30）。

## 为什么单独做

QQ 头像不是消息，主链从来拿不到它，所以被问「我的头像是谁」只能答
"头像那边是空白"（真机 14:30 / 15:26 两次）。而 QQ 头像是**公开 URL**：

    https://q1.qlogo.cn/g?b=qq&nk=<QQ号>&s=640

不需要协议层，也不需要特殊权限。取一次、看一次、存下来，之后零成本。

## 缓存策略

- 图片：`data/avatars/<uid>.jpg`，7 天有效（换头像不频繁）；
- 描述：`data/avatars/descriptions.json`，与图片同寿命；
- 只在**被问到头像**时才去取/看，平时不花视觉的钱。

## 失败姿态

取不到图 / 看不懂 → 返回空串，调用方就当没有这段（不编、不说"空白"）。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "avatars")
DESC_FILE = os.path.join(DATA_DIR, "descriptions.json")
TTL_DAYS = 7
MAX_BYTES = 4 * 1024 * 1024


def url_for(uid, size: int = 640) -> str:
    return f"https://q1.qlogo.cn/g?b=qq&nk={str(uid).strip()}&s={int(size)}"


def _desc_store() -> dict:
    try:
        with open(DESC_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_desc(uid: str, text: str) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        d = _desc_store()
        d[uid] = {"desc": text, "ts": time.time()}
        tmp = DESC_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, DESC_FILE)
    except Exception:
        pass


def image_path(uid) -> str:
    return os.path.join(DATA_DIR, f"{str(uid).strip()}.jpg")


def fetch(uid) -> str:
    """下载头像到本地，返回路径；失败返回空串。缓存在有效期内直接复用。"""
    u = str(uid or "").strip()
    if not u:
        return ""
    path = image_path(u)
    try:
        if os.path.isfile(path) and time.time() - os.path.getmtime(path) < TTL_DAYS * 86400:
            return path
    except Exception:
        pass
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        req = urllib.request.Request(url_for(u), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read(MAX_BYTES + 1)
        if not data or len(data) > MAX_BYTES:
            return ""
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return path
    except Exception:
        return ""


def describe(uid, describe_fn=None, *, ttl_days: float = TTL_DAYS) -> str:
    """头像的内容描述（缓存）。describe_fn: path -> str（通常是 bot_core.describe_image）。"""
    u = str(uid or "").strip()
    if not u:
        return ""
    rec = _desc_store().get(u) or {}
    try:
        if rec.get("desc") and time.time() - float(rec.get("ts") or 0) < ttl_days * 86400:
            return str(rec["desc"])
    except Exception:
        pass
    if not callable(describe_fn):
        return ""
    path = fetch(u)
    if not path:
        return ""
    try:
        text = str(describe_fn(path) or "").strip()
    except Exception:
        return ""
    if not text:
        return ""
    text = text[:300]
    _save_desc(u, text)
    return text


__all__ = ["url_for", "fetch", "describe", "image_path", "DATA_DIR", "DESC_FILE"]
