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
#: 「不喜欢」黑名单（2026-11-02）：{sha1: {rel, phash, ts, reason}}
#: 进了这里：不再被采集回来、不会被 match_meme 挑中；文件保留（可恢复）。
BLOCK_FILE = os.path.join(COLLECT_DIR, "blocked.json")

ALLOWED_EXT = {".gif", ".jpg", ".jpeg", ".png", ".webp"}
MIN_BYTES = 2 * 1024
MAX_BYTES = 3 * 1024 * 1024


def _looks_like_meme(path: str, cfg=None) -> str:
    """这张图像不像表情包；不像就返回原因（空串 = 像）。

    ★ 2026-11-02 用户：「表情包会把普通图片放进去」。
      群里随手拍的手机照片也被收进了表情库（3000×4000 那种）。
      判据只看**尺寸形态**，不猜内容：
        · 最长边 > `meme_collect_max_dim`（默认 1600）→ 像照片；
        · 长宽比 > 3 或 < 1/3 → 像截图/长图；
      PIL 不可用 / 读图失败 → 放行（不因为环境问题把采集关死）。
    """
    cfg = cfg if isinstance(cfg, dict) else _cfg()
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return ""
    try:
        max_dim = int(float(cfg.get("meme_collect_max_dim", 1600) or 0))
    except (TypeError, ValueError):
        max_dim = 1600
    if max_dim > 0 and max(w, h) > max_dim:
        return f"像普通照片（{w}×{h}，最长边超过 {max_dim}）"
    if h and (w / float(h) > 3.0 or w / float(h) < (1.0 / 3.0)):
        return f"像长截图（{w}×{h}）"
    # 2026-11-02 用户：「游戏截图」也被收进来了 —— 横屏 16:9/21:9 的图基本是
    # 截图/录屏，不是表情包（表情包多为方图或竖图）。
    try:
        _land = float(cfg.get("meme_collect_landscape_ratio", 1.35) or 0)
    except (TypeError, ValueError):
        _land = 1.35
    if _land > 0 and h and (w / float(h)) > _land:
        return f"像游戏/横屏截图（{w}×{h}，宽高比 {w / float(h):.2f}）"
    return ""


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
#: 跳过日志的去重表（(文件, 原因) → 上次打印时间）
_SKIP_LOCK = threading.Lock()
_SKIP_SEEN = {}


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


def _log_skip(src: str, reason: str) -> None:
    """跳过时说清原因（同一 (文件, 原因) 60 秒内只报一次，避免刷屏）。

    ★ 2026-11-02 真机：群里 41 张图只入库 28 张，其余 13 张**一声不吭**
      就没了 —— 用户体感是"应该是拿了，但是是没拿"。静默丢弃是最难查的一类 bug。
    """
    key = (str(src or ""), str(reason or ""))
    now = time.time()
    with _SKIP_LOCK:
        last = _SKIP_SEEN.get(key)
        if last is not None and now - last < 60:
            return
        _SKIP_SEEN[key] = now
        if len(_SKIP_SEEN) > 500:                 # 防无限增长
            for k in list(_SKIP_SEEN)[:250]:
                _SKIP_SEEN.pop(k, None)
    try:
        _LOG(f"[表情采集] 跳过 {os.path.basename(src)}：{reason}")
    except Exception:
        pass


#: 审核状态（2026-11-02）：新收的默认 pending，审核通过才进发送池。
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
_MIGRATE_MARK = ".approval_migrated"


def status_of(rel: str, idx: dict = None) -> str:
    """一张图的审核状态；索引里没有的按 approved（旧图不改行为）。"""
    rel = str(rel or "").replace("\\", "/")
    idx = _index() if idx is None else idx
    for v in idx.values():
        if isinstance(v, dict) and str(v.get("path") or "").replace("\\", "/") == rel:
            return str(v.get("status") or STATUS_APPROVED)
    return STATUS_APPROVED


def set_status(rel: str, status: str) -> bool:
    """改一张图的审核状态（pending/approved/rejected）。"""
    rel = str(rel or "").replace("\\", "/")
    status = str(status or "").strip().lower()
    if not rel or status not in (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED):
        return False
    with _LOCK:
        idx = _index()
        hit = False
        for v in idx.values():
            if isinstance(v, dict) and str(v.get("path") or "").replace("\\", "/") == rel:
                v["status"] = status
                v["reviewed_at"] = time.time()
                hit = True
        if not hit:
            return False
        _save_index(idx)
        return True


def set_status_many(rels, status: str) -> int:
    """批量改状态；返回改成功的条数（页面多选用）。"""
    rels = {str(r or "").replace("\\", "/") for r in (rels or []) if str(r or "").strip()}
    status = str(status or "").strip().lower()
    if not rels or status not in (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED):
        return 0
    with _LOCK:
        idx = _index()
        n = 0
        for v in idx.values():
            if not isinstance(v, dict):
                continue
            rel = str(v.get("path") or "").replace("\\", "/")
            if rel in rels:
                v["status"] = status
                v["reviewed_at"] = time.time()
                n += 1
        if n:
            _save_index(idx)
        return n


def list_by_status(status: str = "") -> list:
    """列出某个状态的图（空 = 全部），按收藏时间倒序。"""
    idx = _index()
    st = str(status or "").strip().lower()
    out = []
    for v in idx.values():
        if not isinstance(v, dict):
            continue
        cur = str(v.get("status") or STATUS_APPROVED)
        if st and cur != st:
            continue
        if st == STATUS_REJECTED:
            continue                      # 被拒的不回列表（已进黑名单/待删）
        out.append({"path": str(v.get("path") or ""), "tags": v.get("tags") or [],
                    "ts": float(v.get("ts") or 0), "status": cur,
                    "src": str(v.get("src") or ""),
                    "from_group": str(v.get("from_group") or "")})
    out.sort(key=lambda x: -x["ts"])
    return out


def approved_rels() -> set:
    """可以拿去发的图（旧图没有 status 字段 → 视为 approved）。"""
    out = set()
    for v in _index().values():
        if not isinstance(v, dict):
            continue
        if str(v.get("status") or STATUS_APPROVED) == STATUS_APPROVED:
            out.add(str(v.get("path") or "").replace("\\", "/"))
    return out


def migrate_status(default: str = STATUS_PENDING) -> int:
    """一次性迁移：给没有 status 的旧条目补上（默认进审核区）。

    只在 `collected/.approval_migrated` 不存在时跑一次；
    用户明确要"新收的先审核"，所以旧图也一起进审核区 —— 想直接放行就把
    配置 `meme_require_approval` 关掉。
    """
    mark = os.path.join(COLLECT_DIR, _MIGRATE_MARK)
    if os.path.exists(mark):
        return 0
    with _LOCK:
        idx = _index()
        n = 0
        for v in idx.values():
            if isinstance(v, dict) and not v.get("status"):
                v["status"] = str(default or STATUS_PENDING)
                n += 1
        if n:
            _save_index(idx)
        try:
            os.makedirs(COLLECT_DIR, exist_ok=True)
            open(mark, "w", encoding="utf-8").write(str(time.time()))
        except Exception:
            pass
        return n


def _blocklist() -> dict:
    """读「不喜欢」名单。坏文件当空（这条链不许抛）。"""
    try:
        if os.path.isfile(BLOCK_FILE):
            with open(BLOCK_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def _save_blocklist(d: dict) -> bool:
    try:
        os.makedirs(COLLECT_DIR, exist_ok=True)
        tmp = BLOCK_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, BLOCK_FILE)
        return True
    except Exception as e:
        _LOG(f"[表情采集] 黑名单写入失败: {e}")
        return False


def block(rel: str, reason: str = "user-blocked") -> bool:
    """把一张表情包加进「不喜欢」名单（按 rel 路径找 sha；找不到就按路径存）。"""
    rel = str(rel or "").strip().replace("\\", "/")
    if not rel:
        return False
    idx = _index()
    sha = ""
    phash = None
    for k, v in idx.items():
        if isinstance(v, dict) and str(v.get("path") or "").replace("\\", "/") == rel:
            sha, phash = k, v.get("phash")
            break
    key = sha or ("rel:" + rel)
    with _LOCK:
        d = _blocklist()
        d[key] = {"rel": rel, "phash": phash, "ts": time.time(), "reason": str(reason)[:60]}
        return _save_blocklist(d)


def unblock(rel: str) -> bool:
    rel = str(rel or "").strip().replace("\\", "/")
    if not rel:
        return False
    with _LOCK:
        d = _blocklist()
        gone = [k for k, v in d.items()
                if isinstance(v, dict) and str(v.get("rel") or "") == rel]
        if not gone and rel not in d:
            return True
        for k in gone:
            d.pop(k, None)
        d.pop(rel, None)
        return _save_blocklist(d)


def blocked_rels() -> set:
    """被拉黑的 rel 路径集合（match_meme 用来跳过）。"""
    out = set()
    for v in _blocklist().values():
        if isinstance(v, dict) and v.get("rel"):
            out.add(str(v["rel"]).replace("\\", "/"))
    return out


def is_blocked(rel: str = "", sha: str = "", phash=None, max_dist: int = 4) -> bool:
    """这张图是否在「不喜欢」名单里（rel / sha1 / 感知哈希三选一命中即算）。"""
    d = _blocklist()
    if not d:
        return False
    rel = str(rel or "").strip().replace("\\", "/")
    if sha and sha in d:
        return True
    for k, v in d.items():
        if not isinstance(v, dict):
            continue
        if rel and str(v.get("rel") or "").replace("\\", "/") == rel:
            return True
        try:
            if (phash is not None and v.get("phash") is not None
                    and _hamming(int(phash), int(v["phash"])) <= int(max_dist)):
                return True
        except Exception:
            pass
    return False


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
                  from_group: str = "", pure_image=None) -> str:
    """把一张图收进图库。返回相对路径；跳过返回空串。

    ★ 2026-11-02 真机：群里 41 张图只入库 28 张，剩下 13 张**一声不吭**就没了，
      用户体感正是"应该是拿了，但是是没拿"。现在每条跳过都打一行原因
      （同一 (文件, 原因) 60 秒内只报一次）。
    """
    cfg = _cfg()

    def _skip(p: str, reason: str) -> str:
        _log_skip(p, reason)
        return ""

    src = str(path or "").strip()
    if not bool(cfg.get("meme_collect_enable", True)):
        return _skip(src, "采集总开关关着")
    if not src or not os.path.isfile(src):
        return _skip(src, "图没落地（本地找不到这张）")
    ext = os.path.splitext(src)[1].lower()
    if ext not in ALLOWED_EXT:
        return _skip(src, f"这个格式不收（{ext or '没扩展名'}）")
    try:
        sz = os.path.getsize(src)
    except Exception:
        return _skip(src, "读不了这个文件")
    _lo, _hi = _limits(cfg)
    if sz < _lo:
        return _skip(src, f"图太小（{sz}B，下限 {_lo // 1024}KB）")
    if sz > _hi:
        return _skip(src, f"图太大（{sz / 1024 / 1024:.1f}MB，上限 {_hi // 1024 // 1024}MB）")
    # ★ 2026-11-02：只收"像表情包"的图 —— 群里随手发的照片/截图别进库。
    if bool(cfg.get("meme_collect_require_pure_image", True)) and pure_image is False:
        return _skip(src, "这条消息带文字，按普通图片处理（不收进表情库）")
    _bad = _looks_like_meme(src, cfg)
    if _bad:
        return _skip(src, _bad)
    try:
        with open(src, "rb") as f:
            raw = f.read(_hi + 1)
        sha = hashlib.sha1(raw).hexdigest()
    except Exception:
        return _skip(src, "读不了这个文件")

    with _LOCK:
        idx = _index()
        if sha in idx:
            return _skip(src, f"这张已经存过了（{str((idx.get(sha) or {}).get('path') or '')}）")
        # 视觉去重（2026-11-01）：同图不同编码（QQ 重新压缩/转格式）sha 不同，
        # 但看起来一模一样。默认开，阈值 4/64 位（很保守，宁漏不误杀）。
        _ph = None
        if bool(cfg.get("meme_collect_phash_dedupe", True)):
            _maxd = int(cfg.get("meme_collect_phash_dist", 4) or 0)
            _ds, _dv = visual_dup_of(src, idx, max_dist=_maxd)
            if _ds:
                return _skip(src, f"与已收的 {str(_dv.get('path'))} 视觉重复")
        # ★ 2026-11-02：用户「不喜欢」的图不许再收回来（sha 或感知哈希命中都算）。
        try:
            _blk_ph = _dhash(src)
        except Exception:
            _blk_ph = None
        if is_blocked(sha=sha, phash=_blk_ph):
            return _skip(src, "这张在你标记过「不喜欢」的名单里")
        cap = int(cfg.get("meme_collect_daily_cap", DEFAULT_DAILY_CAP) or 0)
        if cap > 0 and _today_count(idx) >= cap:
            return _skip(src, f"今天的采集额度满了（{cap} 张）")
        try:
            os.makedirs(COLLECT_DIR, exist_ok=True)
            name = sha[:12] + ext
            dst = os.path.join(COLLECT_DIR, name)
            if not os.path.exists(dst):
                shutil.copyfile(src, dst)
        except Exception as _e:
            return _skip(src, f"写不进图库（{type(_e).__name__}）")
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
        _need_review = bool(cfg.get("meme_require_approval", True))
        idx[sha] = {"path": rel, "tags": [str(t) for t in (tags or [])],
                    "ts": time.time(), "src": str(source or "")[:40],
                    "from_group": str(from_group or "")[:24],
                    "status": STATUS_PENDING if _need_review else STATUS_APPROVED,
                    "phash": _ph if _ph is not None else _dhash(dst)}
        _save_index(idx)

    try:
        import meme_engine
        meme_engine.reload_index()
    except Exception:
        pass
    # 行为账本（2026-10-30；2026-11-01 补来源与引用）：
    # 用户要它能答「什么时候、从谁那儿、存了哪张」。以前只记群不记人、
    # 也不带图库路径，问起来只能说"捡了张图"。
    try:
        import self_actions as _acts
        _tags_txt = "、".join(str(t) for t in (tags or [])[:2]) or "未打标签"
        _acts.record("collect", scene=("group" if from_group else "private"),
                     target=str(from_group or sender or ""),
                     group_id=str(from_group or ""),
                     source=str(sender or ""),
                     ref=str(rel or ""),
                     detail=f"{_tags_txt}")
    except Exception:
        pass
    try:
        _LOG(f"[表情采集] 入库 {rel} 标签={tags or '无'}")
    except Exception:
        pass
    return rel


def maybe_collect_async(path: str, *, source: str = "auto", pure_image=None,
                        from_group: str = "") -> None:
    """后台收（下载完立刻返回，不挡回复）。"""
    def _run():
        try:
            maybe_collect(path, source=source, from_group=from_group, pure_image=pure_image)
        except Exception:
            pass
    try:
        threading.Thread(target=_run, daemon=True, name="meme-collect").start()
    except Exception:
        pass


__all__ = ["maybe_collect", "maybe_collect_async", "set_classifier", "set_describer", "set_logger",
           "MEME_DIR", "COLLECT_DIR", "INDEX_FILE", "TAGS_FILE"]
