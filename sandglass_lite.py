# -*- coding: utf-8 -*-
"""sandglass_lite —— EggBloom 自有的沙漏薄适配层（自研，不依赖 nexsandglass）。

## 为什么存在

原先 EggBloom 通过 `nexsandglass/`（上游 NexSandglass 项目，MIT）接入记忆能力，
但全量内嵌了 31 个模块（约 7,500 行）—— 审计后确认只用到 6 模块 16 符号，
其余（MCP Server / TTY Wrapper / 思维层 / 偏移率 / 情绪熵 / 画像 / 织布机）从不触发。

本模块用 ~500 行复刻三个真正被使用的能力，代码归 EggBloom 自有：

  ① 落沙写入    —— 追加 `sandglass.txt`，格式与既有 4080 行数据**逐字节兼容**
  ② 原文检索    —— 倒排索引（中文 2-gram + 英文 n-gram）+ 密度×信任融合分
  ③ 按日期回忆  —— 直接扫文件按行首时间戳取（关键词检索对日期是瞎的）
  ④ 决策粒子    —— 从消息中提取「A 还是 B」类选择，供情绪推断使用

## 数据兼容（硬约束）

既有 `sandglass.txt` 每行格式：
    2026-06-18 00:08:58 | user | [群:123] [昵称(456)] 正文
                        ^^ ts   ^^ role    ^^ 正文（前缀由 bridge 拼好）
- `_parse_line` 必须容忍正文含 ` | `（只 split 两次）
- 索引文件 `sandglass.idx` 格式：`token:行号,行号,...`，与上游一致（可互操作）

## 与上游的关系

数据层完全兼容：本模块读写的文件，上游 NexSandglass 也能读；反之亦然。
若将来要回退到上游，删掉本模块、把 bridge 的 import 指回 `nexsandglass/` 即可。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time as _time
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# 路径解析（与上游 sandglass_paths 规则一致：环境变量 > 程序目录旁 > 主目录）
# ══════════════════════════════════════════════════════════════

def _app_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def resolve_home() -> str:
    """记忆数据根。顺序：NEXSANDBASE_HOME 环境变量 → <app>/.neurobase → ~/.neurobase。"""
    env = os.environ.get("NEXSANDBASE_HOME")
    if env:
        return env
    portable = os.path.join(_app_root(), ".neurobase")
    home = os.path.join(os.path.expanduser("~"), ".neurobase")
    if os.path.isdir(portable):
        return portable
    if os.path.isdir(home):
        return home
    return portable if os.access(_app_root(), os.W_OK) else home


_NB = resolve_home()
_SANDGLASS = os.path.join(_NB, "sandglass.txt")
_INDEX = os.path.join(_NB, "sandglass.idx")
_PARTICLES = os.path.join(_NB, "decision_particles.txt")
_TRUST_DB = os.path.join(_NB, "shadow_sand.db")

_required_dirs = (_NB, os.path.join(_NB, "persona"), os.path.join(_NB, "archive"))


def validate() -> dict:
    """建目录并返回状态（供 bridge 启动调用）。"""
    created = []
    for d in _required_dirs:
        try:
            if not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
                created.append(d)
        except OSError as e:
            logger.warning("沙漏目录创建失败 %s: %s", d, e)
    return {"nb": _NB, "created": created, "ok": True}


# ══════════════════════════════════════════════════════════════
# 分词（与上游 _tokenize 语义一致 —— 保证 idx 可互换）
# ══════════════════════════════════════════════════════════════

def tokenize(text: str) -> set:
    """通用分词：中文 2-gram + 英文 2+ 词 + 英文 2~4 字符滑窗。不索引单汉字。"""
    tokens = set()
    t = (text or "").lower()
    tokens.update(re.findall(r"[a-zA-Z0-9_]{2,}", t))
    eng = "".join(re.findall(r"[a-z0-9]", t))
    for n in (2, 3, 4):
        for i in range(len(eng) - n + 1):
            tokens.add(eng[i:i + n])
    chars = "".join(re.findall(r"[\u4e00-\u9fff]", text or ""))
    for i in range(len(chars) - 1):
        tokens.add(chars[i:i + 2])
    return {x for x in tokens if x}


def query_tokens(text: str) -> set:
    """查询分词：单汉字仅当查询整体只有一个汉字时保留。"""
    tokens = tokenize(text)
    chars = "".join(re.findall(r"[\u4e00-\u9fff]", text or ""))
    if len(chars) != 1:
        tokens -= set(chars)
    return tokens


def parse_line(line: str):
    """`ts | role | text` → (ts, role, text)；非标准行 → (None, None, None)。"""
    if not line or " | " not in line:
        return None, None, None
    parts = line.strip().split(" | ", 2)
    if len(parts) < 3:
        return None, None, None
    return parts[0], parts[1], parts[2].strip()


# ══════════════════════════════════════════════════════════════
# ① 落沙写入
# ══════════════════════════════════════════════════════════════

_TRIVIAL = re.compile(
    r'(好的|明白了|没问题|请稍等|我来看看|是的|对的|你说得对|当然可以|'
    r'不用担心|不客气|谢谢|可以|好|嗯|OK|ok|嗯嗯|好的呢|没问题呢|知道了|收到)'
)

_lock = threading.Lock()


def info_value(text: str) -> float:
    """低价值回复过滤（沿用上游判据，保持行为一致）。"""
    score = 0.3
    if len(text) > 50:
        score += 0.2
    if re.search(r'\d+', text):
        score += 0.2
    if re.search(r'[。：；]', text):
        score += 0.1
    if any(k in text for k in ('建议', '需要', '注意', '因为', '方案',
                               '步骤', '第一种', '第二种', '推荐',
                               '区别', '对比', '优点是', '缺点是')):
        score += 0.2
    s = text.strip()
    if _TRIVIAL.match(s) and len(s) <= 10:
        score = 0.0
    return min(score, 1.0)


def log_message(text: str, sender: str = "agent") -> bool:
    """写一条到沙漏。明文追加 + 文件锁（指数退避）。

    agent 低价值回复（纯确认词）不落沙 —— 与上游一致，避免污染检索。
    """
    try:
        text = str(text or "")
        if not text.strip():
            return False
        if sender == "agent" and info_value(text) < 0.3:
            return False

        os.makedirs(_NB, exist_ok=True)
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {sender} | {text}\n"

        lock_path = _SANDGLASS + ".lock"
        got = False
        for _ in range(3):
            deadline = _time.time() + 5
            while _time.time() < deadline:
                try:
                    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(fd)
                    got = True
                    break
                except FileExistsError:
                    _time.sleep(0.01)
            if got:
                break
        if not got:
            logger.error("落沙锁 3 次重试均超时（15s），强制写入（可能并发冲突）")

        try:
            with open(_SANDGLASS, "a", encoding="utf-8") as f:
                f.write(line)
        finally:
            try:
                os.unlink(lock_path)
            except OSError:
                pass

        _index_incremental(text, _line_count())
        return True
    except Exception as e:
        logger.error("沙漏写入失败: %s", e)
        return False


def log_conversation(user_msg: str, agent_msg: str) -> int:
    n = 0
    if user_msg and log_message(user_msg, "user"):
        n += 1
    if agent_msg and log_message(agent_msg, "agent"):
        n += 1
    return n


def _line_count() -> int:
    try:
        with open(_SANDGLASS, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def count() -> int:
    """沙漏总行数。"""
    return _line_count()


# ══════════════════════════════════════════════════════════════
# ② 倒排索引
# ══════════════════════════════════════════════════════════════

_idx_cache: dict | None = None
_idx_mtime: float = 0.0
_idx_lock = threading.Lock()


def _read_idx_file() -> dict:
    """读 sandglass.idx（格式 `token:行号,行号,...`）。"""
    idx = {}
    try:
        if not os.path.isfile(_INDEX):
            return {}
        with open(_INDEX, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.startswith("#") or ":" not in line:
                    continue
                tok, _, nums = line.partition(":")
                if not tok or not nums:
                    continue
                try:
                    idx[tok] = [int(x) for x in nums.split(",") if x]
                except ValueError:
                    continue
    except Exception as e:
        logger.warning("读取索引失败: %s", e)
        return {}
    return idx


def rebuild_index() -> int:
    """全量重建索引。返回词条数。原子替换。"""
    global _idx_cache, _idx_mtime
    try:
        if not os.path.isfile(_SANDGLASS):
            return 0
        idx = defaultdict(list)
        with open(_SANDGLASS, "r", encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                ts, _role, text = parse_line(line)
                if not ts:
                    continue
                for tok in tokenize(text):
                    idx[tok].append(n)

        tmp = _INDEX + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("# AUTO-GENERATED BY EGGBLOOM SANGLASS_LITE — DO NOT EDIT\n")
            f.write("# token:line_number,line_number,...\n")
            f.write("# delete this file and call rebuild_index() to regenerate\n\n")
            for tok in sorted(idx):
                f.write(f"{tok}:{','.join(str(x) for x in sorted(set(idx[tok])))}\n")
        os.replace(tmp, _INDEX)

        with _idx_lock:
            _idx_cache = None
            _idx_mtime = 0.0
        return len(idx)
    except Exception:
        logger.warning("rebuild_index() 失败", exc_info=True)
        return 0


def _load_index() -> dict:
    """带 mtime 缓存地取索引。"""
    global _idx_cache, _idx_mtime
    with _idx_lock:
        try:
            if os.path.isfile(_INDEX):
                mt = os.path.getmtime(_INDEX)
                if _idx_cache is not None and mt == _idx_mtime:
                    return _idx_cache
                _idx_cache = _read_idx_file()
                _idx_mtime = mt
                return _idx_cache
        except Exception:
            pass
    return _read_idx_file()


def _index_incremental(text: str, line_no: int) -> None:
    """落沙后就地补索引（不重建全量）。失败静默 —— 下次 rebuild 会补上。"""
    global _idx_cache, _idx_mtime
    try:
        if line_no <= 0 or not text:
            return
        with _idx_lock:
            if _idx_cache is None:
                _idx_cache = _read_idx_file()
            if not _idx_cache:
                return
            for tok in tokenize(text):
                lst = _idx_cache.setdefault(tok, [])
                if not lst or lst[-1] != line_no:
                    lst.append(line_no)
        return
    except Exception:
        pass


def idx_search(query: str, limit: int = 100) -> list:
    """倒排检索 → [(行号, ts, text), ...]。

    ⚠️ 覆盖度闸（EggBloom 修正，上游无此闸）：
    上游分词含英文 2~4 字符滑窗（`zzzz` → `zz`/`zzz`/`zzzz`），短片段会命中海量噪声行
    —— 实测搜「zzzz不存在的词qqqq」返回 3 条完全无关记录（"第一种方案要注入QQ…"），
    因为 `qq` 命中了"QQ"。这就是"检索无命中却返回垃圾"的根因。

    修法：命中行必须覆盖查询关键词的**主体**（覆盖率 ≥ 0.5），
    且至少命中一个**中文词或英文全词**（长度 ≥ 3 的英文词 / ≥ 2 字中文词），
    而不是靠 2 字符滑窗碎片堆数量。
    """
    try:
        toks = query_tokens(query)
        if not toks:
            return []
        idx = _load_index()
        if not idx:
            rebuild_index()
            idx = _load_index()
        if not idx:
            return []

        # 强词元：中文 ≥2 字、英文 ≥3 字母（排除 2 字符滑窗碎片）
        strong = {t for t in toks if len(t) >= 2 and (
            re.search(r"[\u4e00-\u9fff]", t) or re.fullmatch(r"[a-z0-9_]{3,}", t))}

        # 统计每行的命中词元（只数强词元，弱碎片只用于加分不用于定性）
        hits = defaultdict(set)
        for t in toks:
            for ln in idx.get(t, ()):  # noqa: 弱碎片也读，但下面按强词元过滤
                if t in strong:
                    hits[ln].add(t)

        if not hits:
            return []

        need = max(1, int(len(strong) * 0.5)) if strong else 1
        cand = {ln for ln, ts_ in hits.items() if len(ts_) >= need}
        if not cand:
            return []

        out = []
        with open(_SANDGLASS, "r", encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if n in cand:
                    ts, _r, text = parse_line(line)
                    if ts and text:
                        out.append((n, ts, text))
                        if len(out) >= limit:
                            break
        return out
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════
# 信任分（影子沙）—— 检索融合用；无 DB 时退化为中性 0.5
# ══════════════════════════════════════════════════════════════

def _trust_scores(line_nums) -> dict:
    """从 shadow_sand.db 读 trust 分。缺库/缺表 → {}（调用方用中性值）。"""
    out = {}
    if not line_nums:
        return out
    try:
        import sqlite3
        if not os.path.isfile(_TRUST_DB):
            return out
        conn = sqlite3.connect(_TRUST_DB, timeout=3)
        try:
            nums = [int(x) for x in line_nums][:500]
            q = ",".join("?" * len(nums))
            for ln, sc in conn.execute(
                    f"SELECT line_num, score FROM trust WHERE line_num IN ({q})", nums):
                out[int(ln)] = float(sc)
        finally:
            conn.close()
    except Exception:
        pass
    return out


def trust_bump(line_nums, helpful: bool = True) -> None:
    """反馈信任分（好评/差评）。失败静默。"""
    try:
        import sqlite3
        if not line_nums or not os.path.isfile(_TRUST_DB):
            return
        conn = sqlite3.connect(_TRUST_DB, timeout=3)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS trust ("
                         "line_num INTEGER PRIMARY KEY, score REAL DEFAULT 0.5,"
                         "helpful INTEGER DEFAULT 0, unhelpful INTEGER DEFAULT 0,"
                         "retrievals INTEGER DEFAULT 0,"
                         "updated_at TEXT DEFAULT (datetime('now')))")
            col = "helpful" if helpful else "unhelpful"
            delta = 0.05 if helpful else -0.05
            for ln in line_nums:
                conn.execute(
                    f"INSERT INTO trust(line_num, score, {col}, retrievals) "
                    f"VALUES(?, 0.5, 1, 1) "
                    f"ON CONFLICT(line_num) DO UPDATE SET "
                    f"{col} = {col} + 1, retrievals = retrievals + 1, "
                    f"score = MAX(0.0, MIN(1.0, score + ?))",
                    (int(ln), delta))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# ③ 融合检索
# ══════════════════════════════════════════════════════════════

def _simhash(text: str, bits: int = 128) -> int:
    """SimHash 指纹（零依赖，md5 哈希）。空文本 → -1。"""
    toks = tokenize(text or "")
    if not toks:
        return -1
    v = [0] * bits
    for tk in toks:
        h = int(hashlib.md5(tk.encode()).hexdigest(), 16)
        for i in range(bits):
            v[i] += 1 if (h >> (i % 128)) & 1 else -1
    fp = 0
    for i in range(bits):
        if v[i] > 0:
            fp |= (1 << i)
    return fp


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _score_candidates(candidates, q_tokens, query):
    """密度 × 信任 + simhash 近似加成 → [(score, item), ...] 降序。

    与上游 search_router._score_candidates 同公式，保证注入侧阈值语义不变。
    """
    q_fp = _simhash(query)
    if q_fp == -1:
        q_fp = 0
    trust = _trust_scores([c[0] for c in candidates])
    scored = []
    for item in candidates:
        ln = item[0]
        text = item[2] if len(item) > 2 else ""
        matched = len(q_tokens & query_tokens(text))
        density = matched / max(len(q_tokens), 1)
        t = trust.get(ln, 0.5)
        fp = _simhash(text[:500])
        sim_bonus = 0.0 if fp == -1 else min(1.0 / (1 + _hamming(q_fp, fp) / 128), 0.5)
        scored.append((density * t + sim_bonus, item))
    scored.sort(key=lambda x: -x[0])
    return scored


def search(query: str, limit: int = 10, month: str = "") -> list:
    """检索沙漏 → [(行号, ts, 明文), ...]。month 参数保留（兼容旧签名）。"""
    try:
        if not query or not str(query).strip():
            return []
        cand = idx_search(query, max(limit * 4, 30))
        if not cand:
            cand = _scan_search(query, max(limit * 4, 30))
        if not cand:
            return []
        toks = query_tokens(query)
        ranked = [item for _s, item in _score_candidates(cand, toks, query)]
        return ranked[:limit]
    except Exception:
        logger.warning("search(%r) 失败", query, exc_info=True)
        return []


def search_scored(query: str, limit: int = 10) -> list:
    """带分数检索 → [(行号, ts, 明文, score), ...]。无候选 → []。"""
    try:
        if not query or not str(query).strip():
            return []
        cand = idx_search(query, max(limit * 4, 30))
        if not cand:
            cand = _scan_search(query, max(limit * 4, 30))
        if not cand:
            return []
        toks = query_tokens(query)
        out = []
        for score, item in _score_candidates(cand, toks, query)[:limit]:
            out.append((item[0], item[1], item[2], max(0.0, min(1.0, score))))
        return out
    except Exception:
        return []


def _scan_search(query: str, limit: int) -> list:
    """全量扫描兜底（索引缺失/无命中时）。

    判据：**关键词**（中文 2+ 字 / 英文 3+ 字母）出现在该行即算命中。
    不能用整个查询串做子串匹配 —— 长查询（如"zzzz不存在的词qqqq"）永远匹配不到，
    会把"无命中"退化成"任意命中"（实测踩过：返回 3 条完全无关的记录）。
    """
    try:
        if not os.path.isfile(_SANDGLASS):
            return []
        kws = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', str(query))
        if not kws:
            return []
        kws = [k.lower() for k in kws[:3]]
        out = []
        with open(_SANDGLASS, "r", encoding="utf-8", errors="ignore") as f:
            for n, line in enumerate(f, 1):
                low = line.lower()
                if any(k in low for k in kws):
                    ts, _r, text = parse_line(line)
                    if ts and text:
                        out.append((n, ts, text))
                        if len(out) >= limit:
                            break
        return out
    except Exception:
        return []


def recent(n: int = 10) -> list:
    """最近 N 条 → [(行号, ts, 明文), ...]。逆向流式，不读全文件。"""
    try:
        if n <= 0 or not os.path.isfile(_SANDGLASS):
            return []
        chunk = 4096
        with open(_SANDGLASS, "rb") as f:
            f.seek(0, 2)
            pos = f.tell()
            found = []
            buf = b""
            while pos > 0 and len(found) < n:
                size = min(chunk, pos)
                pos -= size
                f.seek(pos)
                buf = f.read(size) + buf
                found = buf.split(b"\n")
                if len(found) > n + 1:
                    found = found[-(n + 1):]
                buf = b"" if (len(found) == 1 and not found[0]) else (
                    found[0] if pos > 0 else b"")
            found = [x for x in found if x.strip()][-n:]

        total = count()
        out = []
        for i, raw in enumerate(found):
            ts, _r, text = parse_line(raw.decode("utf-8", errors="ignore").strip())
            if not ts:
                continue
            out.append((total - len(found) + i + 1, ts, text))
        return out
    except Exception:
        logger.warning("recent(%d) 失败", n, exc_info=True)
        return []


# ══════════════════════════════════════════════════════════════
# ④ 决策粒子（自研正则版 —— 替代上游 717 行三层标签架构）
# ══════════════════════════════════════════════════════════════

_DIRECTION_MAP = {
    "frugal": ["免费", "不花钱", "省钱", "性价比", "开源", "自己搞", "本地",
               "free", "open source", "self-host"],
    "spend":  ["花钱", "付费", "买", "效率优先", "buy", "pay", "subscription"],
    "drift":  ["不管了", "随便", "放弃", "不纠结", "whatever", "give up", "fine"],
}

_TAG_MAP = [
    (re.compile(r"免费|不花钱|省钱|性价比|开源|free|open source"), ["成本观", "性价比优先"]),
    (re.compile(r"付费|买|花钱|效率优先|buy|pay|subscription"), ["成本观", "愿意投入"]),
    (re.compile(r"Python|Rust|Go|代码|code|编程"), ["技术选型", "工具偏好"]),
    (re.compile(r"自己|手写|不靠|本地|离线|local|offline|self-host"), ["独立性", "动手派"]),
    (re.compile(r"不管了|随便|放弃|不纠结|whatever|give up|fine"), ["决策疲劳", "红牌"]),
]


def _direction(text: str) -> str:
    low = (text or "").lower()
    for name, kws in _DIRECTION_MAP.items():
        if any(k.lower() in low for k in kws):
            return name
    return "neutral"


def _tags(text: str) -> str:
    out = []
    for pat, tags in _TAG_MAP:
        if pat.search(text or ""):
            out.extend(tags)
    # 去重保序
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return ",".join(uniq[:4]) if uniq else "未分类"


def _extract_options(text: str) -> str:
    """从「A 还是 B」中提取选项对。"""
    m = re.search(r"(.{2,20}?)还是(.{2,20})", text or "")
    if m:
        return f"{m.group(1).strip()}_{m.group(2).strip()}"
    m = re.search(r"([^，。！？\s]{2,12})[，。]?\s*(?:or|或)\s*([^，。！？\s]{2,12})", text or "")
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    return (text or "").strip()[:30] or "未识别"


def log_particle(question: str, choice: str, ts: str = "") -> None:
    """落一粒决策粒子。

    行格式与上游一致（五段）：
        ts | options | choice | direction | emotion | tags
    保证既有 decision_particles.txt 可继续解析。
    """
    try:
        ts = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        options = _extract_options(question)
        direction = _direction(choice or question)
        tags = _tags((question or "") + " " + (choice or ""))
        emotion = _detect_mood((question or "") + " " + (choice or ""))

        os.makedirs(_NB, exist_ok=True)
        with open(_PARTICLES, "a", encoding="utf-8") as f:
            f.write(f"{ts} | {options} | {choice} | {direction} | {emotion} | {tags}\n")
    except Exception:
        logger.warning("决策粒子写入失败", exc_info=True)


# 情绪词表（自研小表 —— 上游 emotion_vocab 有 184 行七分类，这里只留高频）
_MOOD_WORDS = {
    "开心": ["开心", "高兴", "好耶", "太好了", "笑死", "哈哈", "爽", "喜欢", "爱了", "妙"],
    "低落": ["难过", "伤心", "哭了", "emo", "累", "烦", "崩溃", "裂开", "麻了"],
    "生气": ["生气", "气死", "火大", "无语", "离谱", "服了", "怒"],
    "焦虑": ["焦虑", "紧张", "害怕", "担心", "慌", "急"],
    "惊讶": ["震惊", "惊讶", "卧槽", "我去", "天哪", "居然", "不是吧"],
}


def _detect_mood(text: str) -> str:
    """单情绪词命中 → 情绪名（旧接口，保留兼容）。"""
    return detect_mood(text)[0]


def detect_mood(text: str) -> tuple:
    """情绪判定 → (情绪名, 命中词数)。

    返回命中数是为了让调用方能区分「真的平稳」与「压根没信号」——
    bridge 的 prefetch 据此决定要不要注入（无信号 = 零 token 成本）。

    多情绪共存时按 `_MOOD_WORDS` 的声明顺序取首个命中的（确定性）。
    命中数 = 全部情绪词的出现次数总和。
    """
    t = text or ""
    hits = 0
    first = ""
    for mood, words in _MOOD_WORDS.items():
        n = sum(t.count(w) for w in words)
        if n:
            hits += n
            if not first:
                first = mood
    return (first or "neutral"), hits


# ══════════════════════════════════════════════════════════════
# 公开接口（供 bridge 调用）
# ══════════════════════════════════════════════════════════════

def available() -> bool:
    """数据根是否可写（bridge 的 is_available 判据）。"""
    try:
        return os.path.isdir(_NB) and os.access(_NB, os.W_OK)
    except Exception:
        return False


def status() -> dict:
    """状态摘要（替代上游 provider.system_prompt_block 的元信息层）。"""
    try:
        if not available():
            return {"status": "unavailable"}
        return {"status": "active", "messages": count(), "home": _NB}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def __all__():
    return ["log_message", "log_conversation", "count", "search", "search_scored",
            "recent", "rebuild_index", "idx_search", "log_particle",
            "parse_line", "tokenize", "query_tokens", "validate", "available", "status"]