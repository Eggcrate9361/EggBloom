# -*- coding: utf-8 -*-
"""meme_collect.py —— 群聊表情包自动采集（2026-10-30）。

## 为什么

图库 267 张 GIF + 130 张 JPG，但常用情绪（嫌弃 8、疑惑 10、无语 0、烦躁 0）
池子太小 → 发来发去总是那几张。人肉加图不现实，所以让它自己在群里捡：
别人发的表情包 → 去重 → 入库 → 打情绪标签 → 立刻进匹配池。

## 三条边界

1. **只收群聊**（`extract_images` 里按 `message_type=group` 判断）：
   私聊里的图是对方的照片，收进表情包库是越界。
2. **只收小图**：jpg/png/gif/webp，2KB–3MB；大图/动图太大不收。
3. **按 sha1 去重**：同一张图只进一次；每日上限默认 30 张，防刷。

## 标签怎么来

优先用视觉模型判一个情绪词（`bot_core._classify_meme_emotion`，慢、走后台线程）；
分类失败就入库但**不打标签**——不打标签的不会被匹配到，等以后补，不会乱发。

## 存储

    memes/memes/collected/<sha1前12>.<ext>     # 图
    memes/memes/collected/index.json           # sha1 → {path, tags, ts, src}
    memes/memes/tags.json                      # 匹配用的标签库（追加）
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time

MEME_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memes", "memes")
COLLECT_DIR = os.path.join(MEME_DIR, "collected")
INDEX_FILE = os.path.join(COLLECT_DIR, "index.json")
TAGS_FILE = os.path.join(MEME_DIR, "tags.json")

ALLOWED_EXT = {".gif", ".jpg", ".jpeg", ".png", ".webp"}
MIN_BYTES = 2 * 1024
MAX_BYTES = 3 * 1024 * 1024
DEFAULT_DAILY_CAP = 30

_CLASSIFIER = None
_LOCK = threading.Lock()
_LOG = print


def set_logger(fn) -> None:
    global _LOG
    if callable(fn):
        _LOG = fn


def set_classifier(fn) -> None:
    """注入情绪分类器（path -> 情绪词）。宿主注入后不再自己 import bot_core。"""
    global _CLASSIFIER
    _CLASSIFIER = fn


def _cfg() -> dict:
    try:
        from config import load_config
        d = load_config() or {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _index() -> dict:
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_index(idx: dict) -> None:
    try:
        os.makedirs(COLLECT_DIR, exist_ok=True)
        tmp = INDEX_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=1)
        os.replace(tmp, INDEX_FILE)
    except Exception:
        pass


def _add_tags(rel_path: str, tags) -> None:
    tags = [str(t) for t in (tags or []) if str(t).strip()]
    if not tags:
        return
    try:
        with open(TAGS_FILE, "r", encoding="utf-8") as f:
            db = json.load(f)
        if not isinstance(db, dict):
            db = {}
    except Exception:
        db = {}
    if rel_path in db:
        return
    db[rel_path] = tags
    try:
        tmp = TAGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=1)
        os.replace(tmp, TAGS_FILE)
    except Exception:
        pass


def _classify(path: str):
    fn = _CLASSIFIER
    if fn is None:
        try:
            import bot_core
            fn = getattr(bot_core, "_classify_meme_emotion", None)
        except Exception:
            fn = None
    if not callable(fn):
        return []
    try:
        emo = fn([path])
        return [str(emo)] if emo else []
    except Exception:
        return []


def _today_count(idx: dict) -> int:
    day = time.strftime("%Y-%m-%d")
    n = 0
    for rec in idx.values():
        try:
            if time.strftime("%Y-%m-%d", time.localtime(float(rec.get("ts") or 0))) == day:
                n += 1
        except Exception:
            continue
    return n


def maybe_collect(path: str, *, tags=None, source: str = "auto",
                  from_group: str = "") -> str:
    """把一张图收进图库。返回相对路径；跳过返回空串。"""
    cfg = _cfg()
    if not bool(cfg.get("meme_collect_enable", True)):
        return ""
    src = str(path or "").strip()
    if not src or not os.path.isfile(src):
        return ""
    ext = os.path.splitext(src)[1].lower()
    if ext not in ALLOWED_EXT:
        return ""
    try:
        sz = os.path.getsize(src)
    except Exception:
        return ""
    if sz < MIN_BYTES or sz > MAX_BYTES:
        return ""
    try:
        with open(src, "rb") as f:
            raw = f.read(MAX_BYTES + 1)
        sha = hashlib.sha1(raw).hexdigest()
    except Exception:
        return ""

    with _LOCK:
        idx = _index()
        if sha in idx:
            return ""
        cap = int(cfg.get("meme_collect_daily_cap", DEFAULT_DAILY_CAP) or 0)
        if cap > 0 and _today_count(idx) >= cap:
            return ""
        try:
            os.makedirs(COLLECT_DIR, exist_ok=True)
            name = sha[:12] + ext
            dst = os.path.join(COLLECT_DIR, name)
            if not os.path.exists(dst):
                shutil.copyfile(src, dst)
        except Exception:
            return ""
        rel = "collected/" + name
        if not tags and bool(cfg.get("meme_collect_classify", True)):
            tags = _classify(src)
        _add_tags(rel, tags)
        idx[sha] = {"path": rel, "tags": [str(t) for t in (tags or [])],
                    "ts": time.time(), "src": str(source or "")[:40],
                    "from_group": str(from_group or "")[:24]}
        _save_index(idx)

    try:
        import meme_engine
        meme_engine.reload_index()
    except Exception:
        pass
    # 行为账本（2026-10-30）：它要记得"我捡了张图"。来源只记群，不记人。
    try:
        import self_actions as _acts
        _acts.record("collect", scene="group", target=str(from_group or ""),
                     detail="、".join(str(t) for t in (tags or [])[:2]) or "未打标签")
    except Exception:
        pass
    try:
        _LOG(f"[表情采集] 入库 {rel} 标签={tags or '无'}")
    except Exception:
        pass
    return rel


def maybe_collect_async(path: str, *, source: str = "auto",
                        from_group: str = "") -> None:
    """后台收（下载完立刻返回，不挡回复）。"""
    def _run():
        try:
            maybe_collect(path, source=source, from_group=from_group)
        except Exception:
            pass
    try:
        threading.Thread(target=_run, daemon=True, name="meme-collect").start()
    except Exception:
        pass


__all__ = ["maybe_collect", "maybe_collect_async", "set_classifier", "set_logger",
           "MEME_DIR", "COLLECT_DIR", "INDEX_FILE", "TAGS_FILE"]
