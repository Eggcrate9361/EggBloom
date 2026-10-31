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


def _limits(cfg=None) -> tuple:
    """单张大小的上下限（字节）。可配：`meme_collect_min_kb` / `meme_collect_max_mb`。"""
    cfg = cfg if isinstance(cfg, dict) else _cfg()
    try:
        lo = int(float(cfg.get("meme_collect_min_kb", MIN_BYTES / 1024)) * 1024)
    except (TypeError, ValueError):
        lo = MIN_BYTES
    try:
        hi = int(float(cfg.get("meme_collect_max_mb", MAX_BYTES / 1024 / 1024)) * 1024 * 1024)
    except (TypeError, ValueError):
        hi = MAX_BYTES
    return max(0, lo), max(lo + 1, hi)
DEFAULT_DAILY_CAP = 30

_CLASSIFIER = None
_DESCRIBER = None
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


def set_describer(fn) -> None:
    """注入画面描述器（path -> 描述）。宿主注入后不再自己 import bot_core。"""
    global _DESCRIBER
    _DESCRIBER = fn


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


def existing_entry(path: str, *, cfg=None):
    """库里已经有这张（或视觉重复）吗？返回 (rel, how)。

    how: "same" = 字节一致；"visual" = 感知哈希判重；"": 没有。
    2026-11-01：给 meme_save 用 —— 用户问"我刚存的是哪张"、重复时要能说清。
    """
    c = cfg if isinstance(cfg, dict) else _cfg()
    src = str(path or "").strip()
    if not src or not os.path.isfile(src):
        return "", ""
    try:
        with open(src, "rb") as f:
            sha = hashlib.sha1(f.read(_limits(c)[1] + 1)).hexdigest()
    except OSError:
        return "", ""
    idx = _index()
    if sha in idx:
        v = idx[sha]
        return str(v.get("path") or "") if isinstance(v, dict) else "", "same"
    if bool(c.get("meme_collect_phash_dedupe", True)):
        _maxd = int(c.get("meme_collect_phash_dist", 4) or 0)
        k, v = visual_dup_of(src, idx, max_dist=_maxd)
        if k:
            return str(v.get("path") or ""), "visual"
    return "", ""


def skip_reason(path: str, *, cfg=None) -> str:
    """这张图为什么收不进去（空串 = 可以收）。

    2026-10-31 真机：用户连说三次「存」，模型只能反复说"存不进去"，
    因为 maybe_collect 是全静默的。这里是同一套判据的**人话版**，
    给工具层回话用。
    """
    c = cfg if isinstance(cfg, dict) else _cfg()
    if not bool(c.get("meme_collect_enable", True)):
        return "采集总开关关着"
    src = str(path or "").strip()
    if not src or not os.path.isfile(src):
        return "图没落地（本地找不到这张）"
    ext = os.path.splitext(src)[1].lower()
    if ext not in ALLOWED_EXT:
        return f"这个格式不收（{ext or '没扩展名'}）"
    try:
        sz = os.path.getsize(src)
    except OSError:
        return "读不了这个文件"
    _lo, _hi = _limits(c)
    if sz < _lo:
        return f"图太小（{sz}B）"
    if sz > _hi:
        return f"图太大（{sz / 1024 / 1024:.1f}MB，上限 {_hi // 1024 // 1024}MB）"
    try:
        with open(src, "rb") as f:
            sha = hashlib.sha1(f.read(_hi + 1)).hexdigest()
    except OSError:
        return "读不了这个文件"
    try:
        idx = _index()
    except Exception:
        return ""
    if sha in idx:
        return "这张已经存过了"
    cap = int(c.get("meme_collect_daily_cap", DEFAULT_DAILY_CAP) or 0)
    if cap > 0 and _today_count(idx) >= cap:
        return f"今天的采集额度满了（{cap} 张）"
    return ""


def _dhash(path: str, size: int = 8):
    """64 位感知哈希（dHash）。同图不同编码/压缩也能对上。失败返回 None。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("L").resize((size + 1, size), Image.LANCZOS)
            px = list(im.getdata())
        bits = 0
        for r in range(size):
            base = r * (size + 1)
            for c in range(size):
                bits = (bits << 1) | (1 if px[base + c] > px[base + c + 1] else 0)
        return bits
    except Exception:
        return None


def _hamming(a, b) -> int:
    return bin(int(a) ^ int(b)).count("1")


def visual_dup_of(path: str, idx: dict, max_dist: int = 4):
    """在索引里找"视觉上同一张"的条目。返回 (sha, 条目) 或 (None, None)。

    为什么需要：sha1 只认字节。同一张表情包经 QQ 转发/重新编码后字节变了，
    又会被当成新图收一份 —— 用户库里看的是一堆重复。
    """
    ph = _dhash(path)
    if ph is None or not isinstance(idx, dict):
        return None, None
    for k, v in idx.items():
        if not isinstance(v, dict):
            continue
        ph2 = v.get("phash")
        if ph2 is None:
            continue
        try:
            if _hamming(ph, int(ph2)) <= max_dist:
                return k, v
        except (TypeError, ValueError):
            continue
    return None, None


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
    _lo, _hi = _limits(cfg)
    if sz < _lo or sz > _hi:
        return ""
    try:
        with open(src, "rb") as f:
            raw = f.read(_hi + 1)
        sha = hashlib.sha1(raw).hexdigest()
    except Exception:
        return ""

    with _LOCK:
        idx = _index()
        if sha in idx:
            return ""
        # 视觉去重（2026-11-01）：同图不同编码（QQ 重新压缩/转格式）sha 不同，
        # 但看起来一模一样。默认开，阈值 4/64 位（很保守，宁漏不误杀）。
        _ph = None
        if bool(cfg.get("meme_collect_phash_dedupe", True)):
            _maxd = int(cfg.get("meme_collect_phash_dist", 4) or 0)
            _ds, _dv = visual_dup_of(src, idx, max_dist=_maxd)
            if _ds:
                _LOG(f"[表情采集] 跳过：与已收的 {str(_dv.get('path'))} 视觉重复")
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
        # 采集后补画面描述（2026-10-30，配置 meme_collect_fill_desc）。
        # 有了描述，「那个蓝发竖大拇指的图」才找得回来；失败不影响入库。
        if bool(cfg.get("meme_collect_fill_desc", True)) and callable(_DESCRIBER):
            try:
                _desc = str(_DESCRIBER(dst) or "").strip()
                if _desc:
                    import meme_desc
                    meme_desc.save_desc(MEME_DIR, rel, _desc[:300])
            except Exception:
                pass
        idx[sha] = {"path": rel, "tags": [str(t) for t in (tags or [])],
                    "ts": time.time(), "src": str(source or "")[:40],
                    "from_group": str(from_group or "")[:24],
                    "phash": _ph if _ph is not None else _dhash(dst)}
        _save_index(idx)

    try:
        import meme_engine
        meme_engine.reload_index()
    except Exception:
        pass
    # 行为账本（2026-10-30）：它要记得"我捡了张图"。来源只记群，不记人。
    try:
        import self_actions as _acts
        _tags_txt = "、".join(str(t) for t in (tags or [])[:2]) or "未打标签"
        _acts.record("collect", scene=("group" if from_group else "private"),
                     target=str(from_group or ""),
                     detail=f"{rel}｜{_tags_txt}")
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


__all__ = ["maybe_collect", "maybe_collect_async", "set_classifier", "set_describer", "set_logger",
           "MEME_DIR", "COLLECT_DIR", "INDEX_FILE", "TAGS_FILE"]
