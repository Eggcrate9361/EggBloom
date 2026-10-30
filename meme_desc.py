# -*- coding: utf-8 -*-
"""meme_desc.py —— 发送侧的表情包台账（P1-3，2026-10-30）。

## 真机现象

    17:58 你自己发的表情包，你知道是什么意思吗
    17:58 「那些不是我挑的，我只负责甩一个情绪词，图是系统自己配的」
    18:04 这个说的是哪个表情包？你发出来看看
    18:04 「行，那我再发一张，你看看是哪个」

闭环反了：让用户来认 bot 自己发的图。根因不是"没有描述"，而是
**发送侧发完就忘** —— `match_meme()` 只按情绪词挑文件，history 里只有文字，
模型永远不知道自己刚发出去的是哪张、画的是什么。

## 做法

发一张表情包 = 往 history 补一行 `【表情包】` 记录：

    【表情包】vrchat/kipfel_001.gif｜标签：开心、害羞｜画面：银发少女笑着比心

三段来源：
  ① 相对路径 —— 永远有，零成本
  ② `tags.json` 的情绪标签 —— 本来就在磁盘上，零成本
  ③ 画面描述 —— 有缓存就用；没有就**后台**补一次（不挡回复）

描述缓存在 `memes/memes/descriptions.json`。这是运行态数据（跟 tags.json
一样不进 git）：换一台机器表情库不同，描述也就不该跟着仓库走。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Callable, Dict, List, Optional

TAGS_NAME = "tags.json"
DESC_NAME = "descriptions.json"

_LOCK = threading.Lock()
#: 路径 → (mtime, 解析后的 dict)。tags.json 40KB，每发一张都读盘没必要。
_CACHE: Dict[str, tuple] = {}
#: 正在后台补描述的 key，防同一张图并发打多次视觉。
_INFLIGHT = set()


def _read_json(path: str) -> Dict:
    """带 mtime 缓存的 JSON 读取。读不到一律返回 {}（这条链不许抛）。"""
    try:
        m = os.path.getmtime(path)
    except Exception:
        return {}
    with _LOCK:
        hit = _CACHE.get(path)
    if hit and hit[0] == m:
        return hit[1]
    d: Dict = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            d = raw
    except Exception:
        d = {}
    with _LOCK:
        _CACHE[path] = (m, d)
    return d


def rel_key(meme_dir: str, path: str) -> str:
    """表情包的稳定键：相对 meme_dir 的 posix 路径，退化到文件名。"""
    p = str(path or "")
    try:
        r = os.path.relpath(os.path.abspath(p), os.path.abspath(str(meme_dir)))
        if not r.startswith(".."):
            return r.replace("\\", "/")
    except Exception:
        pass
    return os.path.basename(p)


def _entry(tags: Dict, key: str):
    if key in tags:
        return tags[key]
    base = os.path.basename(key)
    return tags.get(base)


def tags_for(meme_dir: str, path: str) -> List[str]:
    """这张图的情绪标签。tags.json 有两种历史形状：list 或 {tags: [...]}。"""
    t = _read_json(os.path.join(str(meme_dir), TAGS_NAME))
    v = _entry(t, rel_key(meme_dir, path))
    if isinstance(v, dict):
        v = v.get("tags")
    if isinstance(v, str):
        v = [v]
    out = []
    for x in (v or []):
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out[:6]


def descs_path(meme_dir: str) -> str:
    return os.path.join(str(meme_dir), DESC_NAME)


def desc_for(meme_dir: str, path: str) -> str:
    """已缓存的画面描述（没有就空串，绝不现算）。"""
    d = _read_json(descs_path(meme_dir))
    key = rel_key(meme_dir, path)
    v = d.get(key)
    if v is None:
        v = d.get(os.path.basename(key))
    return str(v or "").strip()


def save_desc(meme_dir: str, key: str, text: str) -> bool:
    """把一条描述并进 descriptions.json。原子写；跨卷 os.replace 失败时降级。"""
    key = str(key or "").strip()
    text = str(text or "").strip()
    if not key or not text:
        return False
    p = descs_path(meme_dir)
    d = _read_json(p) if os.path.exists(p) else {}
    d = dict(d or {})
    d[key] = text[:200]
    tmp = p + ".tmp"
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        try:
            os.replace(tmp, p)
        except Exception:
            # 数据目录是 junction/跨卷时 os.replace 会 WinError 17
            import shutil
            shutil.copyfile(tmp, p)
            os.remove(tmp)
        with _LOCK:
            _CACHE.pop(p, None)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def note_line(meme_dir: str, path: str) -> str:
    """history 里记的那一行。没标签也没描述时只剩路径 —— 仍然比没有强。"""
    rel = rel_key(meme_dir, path)
    parts = []
    tags = tags_for(meme_dir, path)
    if tags:
        parts.append("标签：" + "、".join(tags[:4]))
    desc = desc_for(meme_dir, path)
    if desc:
        parts.append("画面：" + desc[:60])
    tail = ("｜" + "｜".join(parts)) if parts else ""
    return f"【表情包】{rel}{tail}"


def warm(meme_dir: str, path: str, describer: Optional[Callable[[str], str]],
         *, background: bool = True) -> str:
    """给一张新图补描述。已有缓存 → 直接返回；没有 → 后台跑（默认不等它）。

    `describer` 是宿主注入的视觉函数（`describe_image`）；传 None 表示这台
    机器没有视觉能力，那就只记标签，不编。
    """
    have = desc_for(meme_dir, path)
    if have:
        return have
    if not callable(describer):
        return ""
    key = rel_key(meme_dir, path)
    with _LOCK:
        if key in _INFLIGHT:
            return ""
        _INFLIGHT.add(key)

    def _job():
        try:
            t = str(describer(str(path)) or "").strip()
            if t:
                save_desc(meme_dir, key, t)
        except Exception:
            pass
        finally:
            with _LOCK:
                _INFLIGHT.discard(key)

    if background:
        try:
            threading.Thread(target=_job, daemon=True).start()
        except Exception:
            with _LOCK:
                _INFLIGHT.discard(key)
        return ""
    _job()
    return desc_for(meme_dir, path)


__all__ = ["TAGS_NAME", "DESC_NAME", "rel_key", "tags_for", "descs_path",
           "desc_for", "save_desc", "note_line", "warm"]
