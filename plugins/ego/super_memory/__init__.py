# -*- coding: utf-8 -*-
"""
super_memory 超级记忆插件
核心：记忆不是存档，是让 bot 真的“记得你”。
"""
import json
import os
import re
import sqlite3
import time
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "super_memory.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
EXPORT_PATH = os.path.join(DATA_DIR, "persona_export.json")

PLUGIN_META = {
    "name": "super_memory",
    "version": "0.2.0",
    "description": "超级记忆：主动召回、关键事实提取、人格沉淀、行为驱动。",
    "author": "EggBloom",
    "hooks": ["before_reply", "after_reply"],
    "priority": 40,
    "enabled": True,
    "page": "plugins/super_memory/ui.html",
    "tags": ["记忆", "API可接入"],
    "config_schema": {
        "enabled": {"type": "bool", "default": True},
        "memory_enabled": {"type": "bool", "default": True},
        "extract_facts": {"type": "bool", "default": True},
        "recall_top_k": {"type": "int", "default": 5},
        "persona_injection": {"type": "bool", "default": True},
        "affection_enabled": {"type": "bool", "default": True},
        "auto_consolidate": {"type": "bool", "default": True},
        "memory_decay_days": {"type": "int", "default": 30},
        "use_vector_recall": {"type": "bool", "default": True},
        "embed_url": {"type": "str", "default": "http://127.0.0.1:8083/v1/embeddings"},
        "embed_model": {"type": "str", "default": "bge-small-zh"},
        # 内置默认：模型与 LiGGUF DLL 随插件打包在 models/（体量小，无需外部依赖）
        "embed_exe": {"type": "str", "default": ""},
        "embed_model_path": {"type": "str", "default": os.path.join("models", "bge-small-zh-v1.5-f16.gguf")},
        "embed_dll": {"type": "str", "default": os.path.join("models", "ligguf_embed.dll")},
        "embed_min_sim": {"type": "float", "default": 0.35},
    },
}

os.makedirs(DATA_DIR, exist_ok=True)


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[SuperMemory] 保存失败 {path}: {e}")


class _Embedder:
    """向量化器：优先进程内 LiGGUF DLL（ctypes），失败自动降级到 llama-server HTTP。

    线程安全：LiGGUF vdb 内部非线程安全，所有 DLL 调用经 _DLL_LOCK 串行化；
    HTTP 兜底可并发（llama-server 自带线程池）。DLL 不可用时 `_dll_ok=False` 永久走 HTTP。
    """

    _dll_ok = True
    _dll = None

    def __init__(self, url: str = "", model: str = "bge-small-zh", dll_path: str = "", model_path: str = ""):
        self.url = url or "http://127.0.0.1:8083/v1/embeddings"
        self.model = model or "bge-small-zh"
        self.dll_path = dll_path or ""
        self.model_path = model_path or ""
        self._fail_count = 0

    def _init_dll(self) -> bool:
        if not self._dll_ok or self._dll is not None or not self.dll_path:
            return self._dll is not None
        try:
            import ctypes
            import os

            if not os.path.exists(self.dll_path):
                self._dll_ok = False
                return False
            dll = ctypes.CDLL(self.dll_path)
            fn = dll.vdb_embed_export
            fn.restype = ctypes.c_int
            fn.argtypes = [
                ctypes.c_char_p, ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_float), ctypes.c_int,
            ]
            self._dll = fn
            return True
        except Exception:
            self._dll_ok = False
            return False

    def _available(self) -> bool:
        if self._init_dll():
            return True
        if self._fail_count >= 5:
            return False
        return True

    def embed(self, text: str) -> Optional[List[float]]:
        if not text:
            return None
        # ── 路径 1：进程内 LiGGUF DLL（首选，零依赖、最快）──
        if self._init_dll():
            with _DLL_LOCK:
                try:
                    import ctypes

                    buf = (ctypes.c_float * 512)()
                    rc = self._dll(
                        self.model_path.encode("utf-8", "ignore"),
                        text[:800].encode("utf-8", "ignore"),
                        buf, 512,
                    )
                    if rc == 0:
                        self._fail_count = 0
                        return [float(x) for x in buf]
                    self._fail_count += 1
                except Exception:
                    self._fail_count += 1
                    self._dll_ok = False  # DLL 异常永久切 HTTP，避免每轮重试
        # ── 路径 2：HTTP 兜底（llama-server）──
        if not self._available():
            return None
        try:
            import urllib.request

            payload = json.dumps({"input": [text[:800]], "model": self.model}).encode("utf-8")
            req = urllib.request.Request(self.url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
            items = data.get("data") or []
            if items and items[0].get("embedding"):
                self._fail_count = 0
                return items[0]["embedding"]
            self._fail_count += 1
            return None
        except Exception:
            self._fail_count += 1
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        if not texts:
            return []
        if self._init_dll():
            return [self.embed(t) for t in texts]
        if not self._available():
            return [None] * len(texts)
        return [self.embed(t) for t in texts]


_DLL_LOCK = threading.Lock()
_embedder = _Embedder()


_EMBED_ASCII_CACHE = os.path.join(os.path.expanduser("~"), "AppData", "Local", "EggBloom_v3", "models")


def _ascii_mirror(path):
    """原生 LiGGUF DLL / llama-server 无法读取含非 ASCII 字符的路径（如中文目录名）。
    模型随插件打包分发，运行时如有需要镜像到 ASCII 缓存目录，确保可被加载。"""
    p = os.path.abspath(str(path or ""))
    if not p or not os.path.exists(p):
        return p
    if all(ord(c) < 128 for c in p):
        return p
    try:
        os.makedirs(_EMBED_ASCII_CACHE, exist_ok=True)
        target = os.path.join(_EMBED_ASCII_CACHE, os.path.basename(p))
        if not os.path.exists(target) or os.path.getsize(target) != os.path.getsize(p):
            import shutil
            shutil.copyfile(p, target)
        return target
    except Exception:
        return p


def _apply_embed_config(cfg):
    """统一 embed 路径来源链：插件 data/config.json > bot_config.json(embed_*) > 内置默认。
    相对路径（models/...）解析到插件目录，支持随包分发；未配置时走 HTTP 兜底（_Embedder 自动降级）。"""
    try:
        from config import load_config
        base = load_config()
    except Exception:
        base = {}

    def _res(p):
        p = str(p or "")
        if p and not os.path.isabs(p):
            p = os.path.join(PLUGIN_DIR, p)
        return p

    _embedder.dll_path = _ascii_mirror(_res(cfg.get("embed_dll") or base.get("embed_dll") or ""))
    _embedder.model_path = _ascii_mirror(_res(cfg.get("embed_model_path") or base.get("embed_model_path") or ""))
    _embedder.url = str(cfg.get("embed_url") or base.get("embed_url") or "http://127.0.0.1:8083/v1/embeddings")
    _embedder.model = str(cfg.get("embed_model") or base.get("embed_model") or "bge-small-zh")


def _vec_sim(a, b):
    """余弦相似度，a/b 为等长 float 列表。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        import math

        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


class SuperMemory:
    def __init__(self):
        self.config: Dict[str, Any] = _load_json(CONFIG_PATH, {})
        for key, schema in PLUGIN_META.get("config_schema", {}).items():
            if key not in self.config or self.config[key] in ("", None):
                self.config[key] = schema.get("default", False)
        _apply_embed_config(self.config)
        self._lock = threading.Lock()
        self._init_db()

    def count_private(self, scope_id: str) -> int:
        """统计某私聊作用域已写入的轮次数（迁移脚本幂等用）。"""
        try:
            conn = sqlite3.connect(DB_PATH)
            n = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE scope='private' AND scope_id=?",
                (str(scope_id),),
            ).fetchone()[0]
            conn.close()
            return n
        except Exception:
            return 0

    def _init_db(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    user_msg TEXT NOT NULL,
                    bot_reply TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    importance REAL DEFAULT 0.5,
                    learned INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_messages_scope ON messages(scope, scope_id, timestamp);

                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    confidence REAL DEFAULT 0.7,
                    source_msg TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    last_used REAL NOT NULL,
                    use_count INTEGER DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(scope_id, user_id);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    participants TEXT DEFAULT '',
                    emotion TEXT DEFAULT '',
                    timestamp REAL NOT NULL,
                    importance REAL DEFAULT 0.6
                );
                CREATE INDEX IF NOT EXISTS idx_events_scope ON events(scope_id, timestamp);

                CREATE TABLE IF NOT EXISTS persona_kernel (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL DEFAULT 0.8,
                    last_updated REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_key ON persona_kernel(scope_id, key);

                CREATE TABLE IF NOT EXISTS affection (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    score INTEGER DEFAULT 0,
                    interactions INTEGER DEFAULT 0,
                    last_interaction REAL NOT NULL,
                    mood TEXT DEFAULT 'neutral'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_affection_user ON affection(scope_id, user_id);
            """)
            conn.commit()
            # 迁移：老库补 embedding 列
            try:
                fcols = [r[1] for r in conn.execute('PRAGMA table_info(facts)').fetchall()]
                if 'embedding' not in fcols:
                    conn.execute('ALTER TABLE facts ADD COLUMN embedding TEXT DEFAULT ""')
                ecols = [r[1] for r in conn.execute('PRAGMA table_info(events)').fetchall()]
                if 'embedding' not in ecols:
                    conn.execute('ALTER TABLE events ADD COLUMN embedding TEXT DEFAULT ""')
                conn.commit()
            except Exception:
                pass
            # 迁移：分层字段 tier（permanent 永久 / long 长期 / short 短期）
            try:
                fcols = [r[1] for r in conn.execute('PRAGMA table_info(facts)').fetchall()]
                if 'tier' not in fcols:
                    conn.execute('ALTER TABLE facts ADD COLUMN tier TEXT DEFAULT "long"')
                ecols = [r[1] for r in conn.execute('PRAGMA table_info(events)').fetchall()]
                if 'tier' not in ecols:
                    conn.execute('ALTER TABLE events ADD COLUMN tier TEXT DEFAULT "long"')
                mcols = [r[1] for r in conn.execute('PRAGMA table_info(messages)').fetchall()]
                if 'tier' not in mcols:
                    conn.execute('ALTER TABLE messages ADD COLUMN tier TEXT DEFAULT "long"')
                conn.commit()
            except Exception:
                pass
            conn.close()
        except Exception as e:
            print(f"[SuperMemory] 初始化数据库失败: {e}")

    def get_config(self) -> Dict[str, Any]:
        return dict(self.config)

    def set_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            for key, value in data.items():
                if key in PLUGIN_META.get("config_schema", {}):
                    self.config[key] = value
            _save_json(CONFIG_PATH, self.config)
            return self.get_config()

    # ── 事实提取 ──

    # 分层判定：permanent 永久 / long 长期 / short 短期
    TIER_RULES = {
        "permanent": {"label": "永久", "note": "重要身份/生日/重大事件，永不遗忘"},
        "long": {"label": "长期", "note": "常规记忆，按 decay_days 衰减遗忘"},
        "short": {"label": "短期", "note": "琐碎内容，快速清理"},
    }

    def _classify_tier(self, importance: float = 0.5, use_count: int = 1, category: str = "") -> str:
        """根据重要度/使用次数/类型自动分层"""
        cat = str(category or "")
        if cat in ("重要日期", "身份") or importance >= 0.85 or use_count >= 6:
            return "permanent"
        if importance >= 0.6 or use_count >= 3:
            return "long"
        return "short"

    def extract_facts(self, scope_id: str, user_id: str, text: str) -> List[Dict[str, str]]:
        facts: List[Dict[str, str]] = []
        text = text.strip()
        if not text or len(text) > 200:
            return facts

        patterns = [
            (r"我是(.+?)(?=[，。！？\s]|$)", "身份", lambda m: m.group(1).strip()),
            (r"我叫(.+?)(?=[，。！？\s]|$)", "身份", lambda m: m.group(1).strip()),
            (r"我的([^，。！？\s]+?)是(.+?)(?=[，。！？\s]|$)", "偏好", lambda m: f"{m.group(1)}：{m.group(2)}"),
            (r"([^，。！？\s]+?)是(.+?)(?=[，。！？\s]|$)", "偏好", lambda m: f"{m.group(1)}：{m.group(2)}"),
            (r"我喜欢(.+?)(?=[，。！？\s]|$)", "偏好", lambda m: f"喜欢：{m.group(1)}"),
            (r"我不喜欢(.+?)(?=[，。！？\s]|$)", "偏好", lambda m: f"不喜欢：{m.group(1)}"),
            (r"我在(.+?)(?=[，。！？\s]|$)", "位置", lambda m: m.group(1).strip()),
            (r"我的生日是(.+?)(?=[，。！？\s]|$)", "重要日期", lambda m: f"生日：{m.group(1)}"),
            (r"今天(.+?)了", "事件", lambda m: f"事件：{m.group(1)}"),
            (r"(.+?)是我的(.+?)(?=[，。！？\s]|$)", "关系", lambda m: f"{m.group(2)}：{m.group(1)}"),
        ]

        for pattern, category, extractor in patterns:
            matches = re.finditer(pattern, text)
            for m in matches:
                try:
                    content = extractor(m)
                    if content and len(content) < 50:
                        facts.append({"category": category, "content": content})
                except Exception:
                    continue

        return facts[:3]

    def _upsert_fact(self, conn, scope_id: str, user_id: str, category: str, content: str, source_msg: str, confidence: float = 0.7):
        now = time.time()
        cur = conn.execute(
            "SELECT id, confidence, use_count FROM facts WHERE scope_id=? AND user_id=? AND category=? AND content=?",
            (str(scope_id), str(user_id), category, content),
        )
        row = cur.fetchone()
        if row:
            new_conf = min(1.0, (row[1] * row[2] + confidence) / (row[2] + 1))
            new_count = row[2] + 1
            tier = self._classify_tier(new_conf, new_count, category)
            conn.execute(
                "UPDATE facts SET confidence=?, use_count=?, last_used=?, source_msg=?, tier=? WHERE id=?",
                (new_conf, new_count, now, source_msg[:200], tier, row[0]),
            )
        else:
            emb = ""
            try:
                if _embedder.embed(content):
                    emb = json.dumps(_embedder.embed(content))
            except Exception:
                pass
            tier = self._classify_tier(confidence, 1, category)
            conn.execute(
                "INSERT INTO facts (scope_id, user_id, category, content, confidence, source_msg, created_at, last_used, embedding, tier) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(scope_id), str(user_id), category, content, confidence, source_msg[:200], now, now, emb, tier),
            )

    def _store_facts(self, scope_id: str, user_id: str, facts: List[Dict[str, str]], source_msg: str):
        if not facts:
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            for fact in facts:
                self._upsert_fact(conn, scope_id, user_id, fact["category"], fact["content"], source_msg)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[SuperMemory] 存储事实失败: {e}")

    # ── 事件提取 ──

    def extract_events(self, scope_id: str, text: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        text = text.strip()
        if not text or len(text) > 300:
            return events

        triggers = [
            ("开播", "直播", 0.7),
            ("退场", "状态", 0.5),
            ("来了", "出现", 0.4),
            ("走了", "离开", 0.4),
            ("拜拜", "告别", 0.3),
            ("晚安", "作息", 0.4),
            ("早", "作息", 0.3),
            ("生日", "重要日期", 0.9),
            ("纪念日", "重要日期", 0.9),
            ("分手", "关系变化", 0.9),
            ("脱单", "关系变化", 0.9),
            ("失业", "重大事件", 0.8),
            ("中奖", "重大事件", 0.9),
            ("赢了", "成就", 0.6),
            ("输了", "挫折", 0.6),
        ]

        for keyword, event_type, importance in triggers:
            if keyword in text:
                summary = text[:80]
                emotion = ""
                if any(kw in text for kw in ["开心", "哈哈", "笑死", "乐"]):
                    emotion = "开心"
                elif any(kw in text for kw in ["难过", "哭", "泪", "惨"]):
                    emotion = "难过"
                elif any(kw in text for kw in ["生气", "气死", "烦"]):
                    emotion = "生气"
                events.append({
                    "event_type": event_type,
                    "summary": summary,
                    "emotion": emotion,
                    "importance": importance,
                })
                break

        return events[:1]

    def _store_events(self, scope_id: str, events: List[Dict[str, Any]]):
        if not events:
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            now = time.time()
            for event in events:
                emb = ""
                try:
                    if _embedder.embed(event["summary"]):
                        emb = json.dumps(_embedder.embed(event["summary"]))
                except Exception:
                    pass
                conn.execute(
                    "INSERT INTO events (scope_id, event_type, summary, participants, emotion, timestamp, importance, embedding, tier) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(scope_id), event["event_type"], event["summary"], "", event.get("emotion", ""), now, event.get("importance", 0.5), emb, self._classify_tier(event.get("importance", 0.5), 1)),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[SuperMemory] 存储事件失败: {e}")

    # ── 关键记忆召回 ──

    def recall_key_memories(self, scope: str, scope_id: str, current_msg: str = "", limit: int = 5) -> List[Dict[str, Any]]:
        try:
            conn = sqlite3.connect(DB_PATH)
            now = time.time()
            decay_days = self.config.get("memory_decay_days", 30)
            decay_sec = decay_days * 86400

            use_vec = bool(self.config.get("use_vector_recall", True)) and bool(current_msg)
            qvec = None
            if use_vec:
                qvec = _embedder.embed(current_msg)
                if not qvec:
                    use_vec = False
            min_sim = float(self.config.get("embed_min_sim", 0.35))

            def _sim(emb_text):
                if not use_vec or not emb_text:
                    return 0.0
                try:
                    ev = json.loads(emb_text)
                    return _vec_sim(qvec, ev)
                except Exception:
                    return 0.0

            # 1. 高置信度事实（向量优先）
            raw_facts = []
            cur = conn.execute(
                "SELECT category, content, confidence, last_used, embedding, tier FROM facts WHERE scope_id=? AND confidence > 0.6 AND (? = 0 OR (embedding IS NOT NULL AND embedding != '')) ORDER BY confidence DESC, last_used DESC LIMIT 100",
                (str(scope_id), 1 if use_vec else 0),
            )
            for row in cur.fetchall():
                raw_facts.append((row, _sim(row[4])))

            # 2. 重要事件（向量优先）
            raw_events = []
            cur = conn.execute(
                "SELECT event_type, summary, emotion, importance, timestamp, embedding, tier FROM events WHERE scope_id=? AND importance > 0.5 AND (? = 0 OR (embedding IS NOT NULL AND embedding != '')) ORDER BY timestamp DESC LIMIT 100",
                (str(scope_id), 1 if use_vec else 0),
            )
            for row in cur.fetchall():
                age = now - row[4]
                if age >= decay_sec:
                    continue
                decayed = row[3] * max(0.0, (decay_sec - age) / decay_sec)
                if decayed <= 0.3:
                    continue
                raw_events.append((row, decayed, _sim(row[5])))

            # 向量阈值动态放宽：严格阈值下不足 limit 条时，逐级下调 0.35→0.25→0.15→0
            sim_floor = min_sim
            if use_vec:
                while sim_floor > 0.0:
                    pass_n = sum(1 for _, s in raw_facts if s >= sim_floor) + \
                             sum(1 for _, _, s in raw_events if s >= sim_floor)
                    if pass_n >= limit:
                        break
                    if sim_floor >= 0.25:
                        sim_floor = 0.15
                    else:
                        sim_floor = 0.0

            facts = []
            for row, sim in raw_facts:
                if use_vec and sim < sim_floor:
                    continue
                importance = sim if use_vec else row[2]
                tier = str(row[5] or "long") if len(row) > 5 else "long"
                facts.append({
                    "type": "fact",
                    "content": f"[{row[0]}] {row[1]}",
                    "confidence": row[2],
                    "last_used": row[3],
                    "importance": importance,
                    "sim": sim,
                    "tier": tier,
                })

            events = []
            for (row, decayed, sim) in raw_events:
                if use_vec and sim < sim_floor:
                    continue
                importance = sim if use_vec else decayed
                tier = str(row[6] or "long") if len(row) > 6 else "long"
                events.append({
                    "type": "event",
                    "content": f"[{row[0]}] {row[1]}",
                    "emotion": row[2],
                    "importance": importance,
                    "timestamp": row[4],
                    "sim": sim,
                    "tier": tier,
                })

            # 3. 高好感度用户
            affection = []
            cur = conn.execute(
                "SELECT user_id, score, interactions, mood FROM affection WHERE scope_id=? ORDER BY score DESC LIMIT ?",
                (str(scope_id), limit),
            )
            for row in cur.fetchall():
                affection.append({
                    "type": "affection",
                    "content": f"用户 {row[0]}：好感度 {row[1]}，互动 {row[2]} 次，状态 {row[3]}",
                    "score": row[1],
                    "importance": min(1.0, row[1] / 100.0),
                })

            conn.close()

            all_memories = facts + events + affection
            tier_boost = {"permanent": 0.25, "long": 0.0, "short": -0.15}
            for m in all_memories:
                m["tier"] = m.get("tier", "long")
                m["rank_score"] = m.get("importance", 0) + tier_boost.get(m["tier"], 0.0)
            all_memories.sort(key=lambda x: x.get("rank_score", 0), reverse=True)
            return all_memories[:limit * 2]
        except Exception:
            return []

    def get_prompt_block(self, scope: str, scope_id: str, current_msg: str = "") -> str:
        if not self.config.get("inject_into_prompt", True):
            return ""
        memories = self.recall_key_memories(scope, scope_id, current_msg, limit=self.config.get("recall_top_k", 5))
        if not memories:
            return ""
        lines = []
        for m in memories:
            if m["type"] == "fact":
                lines.append(m["content"])
            elif m["type"] == "event":
                t = datetime.fromtimestamp(m.get("timestamp", time.time())).strftime("%m-%d %H:%M")
                lines.append(f"[{t}] {m['content']}")
            elif m["type"] == "affection":
                lines.append(m["content"])
        return "【核心记忆】以下记忆已按重要度筛选，请自然融入回复：\n" + "\n".join(lines)

    # ── 人格沉淀 ──

    def _update_persona_kernel(self, scope_id: str, facts: List[Dict[str, str]]):
        try:
            conn = sqlite3.connect(DB_PATH)
            now = time.time()
            category_map = {
                "身份": "user_identity",
                "偏好": "user_preferences",
                "位置": "user_location",
                "重要日期": "important_dates",
                "关系": "relationships",
                "事件": "key_events",
            }
            for fact in facts:
                key = category_map.get(fact["category"], "misc")
                cur = conn.execute(
                    "SELECT id, confidence FROM persona_kernel WHERE scope_id=? AND key=?",
                    (str(scope_id), key),
                )
                row = cur.fetchone()
                if row:
                    conn.execute(
                        "UPDATE persona_kernel SET value=?, confidence=?, last_updated=? WHERE id=?",
                        (fact["content"], 0.9, now, row[0]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO persona_kernel (scope_id, key, value, confidence, last_updated) VALUES (?, ?, ?, ?, ?)",
                        (str(scope_id), key, fact["content"], 0.9, now),
                    )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[SuperMemory] 更新人格核心失败: {e}")

    def get_persona_export(self, scope_id: str) -> Dict[str, Any]:
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.execute(
                "SELECT key, value, confidence FROM persona_kernel WHERE scope_id=? ORDER BY confidence DESC, last_updated DESC",
                (str(scope_id),),
            )
            rows = cur.fetchall()
            conn.close()
            persona: Dict[str, Any] = {}
            for key, value, conf in rows:
                if conf > 0.7:
                    persona[key] = value
            return persona
        except Exception:
            return {}

    # ── 好感度 ──

    def update_affection(self, scope_id: str, user_id: str, text: str):
        if not self.config.get("affection_enabled", True):
            return
        positive = ["哈哈", "笑死", "好棒", "厉害", "谢谢", "爱了", "好人", "优秀", "nb", "真棒", "牛", "强", "不错", "贴贴"]
        negative = ["垃圾", "废物", "傻逼", "sb", "脑残", "菜鸡", "没用", "恶心", "滚"]
        if any(kw in text for kw in negative):
            score = -15
        elif any(kw in text for kw in positive):
            score = 8
        else:
            score = 1
        try:
            conn = sqlite3.connect(DB_PATH)
            now = time.time()
            cur = conn.execute(
                "SELECT score, interactions FROM affection WHERE scope_id=? AND user_id=?",
                (str(scope_id), str(user_id)),
            )
            row = cur.fetchone()
            if row:
                new_score = max(-50, min(100, row[0] + score))
                conn.execute(
                    "UPDATE affection SET score=?, interactions=interactions+1, last_interaction=? WHERE scope_id=? AND user_id=?",
                    (new_score, now, str(scope_id), str(user_id)),
                )
            else:
                new_score = max(0, min(100, 20 + score))
                conn.execute(
                    "INSERT INTO affection (scope_id, user_id, score, interactions, last_interaction) VALUES (?, ?, ?, 1, ?)",
                    (str(scope_id), str(user_id), new_score, now),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[SuperMemory] 更新好感度失败: {e}")

    def get_affection_context(self, scope_id: str, user_id: str) -> str:
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.execute(
                "SELECT score, interactions, mood FROM affection WHERE scope_id=? AND user_id=?",
                (str(scope_id), str(user_id)),
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                return ""
            score, interactions, mood = row
            level = "陌生" if score < 10 else "熟悉" if score < 40 else "好友" if score < 80 else "死党"
            mood_map = {"neutral": "平静", "happy": "开心", "sad": "低落", "angry": "生气"}
            mood_text = mood_map.get(mood, mood)
            return f"【用户关系】{user_id}：好感度 {score}，互动 {interactions} 次，{level}，当前情绪 {mood_text}。"
        except Exception:
            return ""

    # ── 自动整理 ──

    def consolidate(self, scope: str, scope_id: str):
        if not self.config.get("auto_consolidate", True):
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            now = time.time()
            decay_days = float(self.config.get("memory_decay_days", 30))
            # 分层遗忘：permanent 永不清理；long 按 decay_days；short 按 decay_days/3
            long_cutoff = now - decay_days * 86400
            short_days = max(3.0, decay_days / 3.0)
            short_cutoff = now - short_days * 86400
            # messages：long/short 分别清理
            conn.execute(
                "DELETE FROM messages WHERE scope=? AND scope_id=? AND timestamp < ? AND tier='short'",
                (scope, str(scope_id), short_cutoff),
            )
            conn.execute(
                "DELETE FROM messages WHERE scope=? AND scope_id=? AND timestamp < ? AND tier='long' AND importance < 0.7",
                (scope, str(scope_id), long_cutoff),
            )
            # facts：short 超期清理；long 超期且低置信度清理
            conn.execute(
                "DELETE FROM facts WHERE scope_id=? AND tier='short' AND last_used < ?",
                (str(scope_id), short_cutoff),
            )
            conn.execute(
                "DELETE FROM facts WHERE scope_id=? AND tier='long' AND last_used < ? AND confidence < 0.6",
                (str(scope_id), long_cutoff),
            )
            # events：short 超期清理；long 超期且低重要度清理
            conn.execute(
                "DELETE FROM events WHERE scope_id=? AND tier='short' AND timestamp < ?",
                (str(scope_id), short_cutoff),
            )
            conn.execute(
                "DELETE FROM events WHERE scope_id=? AND tier='long' AND timestamp < ? AND importance < 0.6",
                (str(scope_id), long_cutoff),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[SuperMemory] 整理记忆失败: {e}")

    # ── 导出人格 ──

    def export_persona(self, scope_id: str) -> Dict[str, Any]:
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.execute(
                "SELECT key, value, confidence FROM persona_kernel WHERE scope_id=?",
                (str(scope_id),),
            )
            rows = cur.fetchall()
            conn.close()
            persona = {}
            for key, value, conf in rows:
                if conf > 0.6:
                    persona[key] = value
            return persona
        except Exception:
            return {}


_memory = SuperMemory()


def on_load():
    print("[SuperMemory] 已加载")


def on_unload():
    print("[SuperMemory] 已卸载")


def set_ai_client(client):
    pass


def get_config():
    return _memory.get_config()


def set_config(data):
    result = _memory.set_config(data)
    # 刷新 embedder 运行时配置（embed_dll / embed_model_path / embed_url 热更新）
    try:
        cfg = _load_json(CONFIG_PATH, {})
        _apply_embed_config(cfg)
        # 配置变化后强制重载 DLL 句柄
        _embedder._dll = None
        _embedder._dll_ok = True
    except Exception:
        pass
    return result


def get_tier_stats(scope_id: str = "default") -> dict:
    """分层统计：permanent/long/short 各表条目数"""
    try:
        conn = sqlite3.connect(DB_PATH)
        tiers = {"permanent": 0, "long": 0, "short": 0}
        for table in ("messages", "facts", "events"):
            for tier in tiers:
                try:
                    n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE scope_id=? AND tier=?", (str(scope_id), tier)).fetchone()[0]
                    tiers[tier] += n
                except Exception:
                    pass
        # 各表独立计数
        per_table = {}
        for table in ("messages", "facts", "events"):
            per_table[table] = {}
            for tier in tiers:
                try:
                    per_table[table][tier] = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE scope_id=? AND tier=?", (str(scope_id), tier)).fetchone()[0]
                except Exception:
                    per_table[table][tier] = 0
        conn.close()
        return {"ok": True, "tiers": tiers, "per_table": per_table,
                "rules": _memory.TIER_RULES}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def set_tier(scope_id: str = "default", table: str = "facts", item_id: int = 0, tier: str = "long") -> dict:
    """手动调整某条记忆的分层（permanent 可保命，short 可加速清理）"""
    if table not in ("messages", "facts", "events"):
        return {"ok": False, "error": f"未知表: {table}"}
    if tier not in _memory.TIER_RULES:
        return {"ok": False, "error": f"未知分层: {tier}"}
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(f"UPDATE {table} SET tier=? WHERE id=? AND scope_id=?", (tier, int(item_id), str(scope_id)))
        conn.commit()
        conn.close()
        return {"ok": True, "table": table, "id": int(item_id), "tier": tier}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_vector_stats():
    """向量化统计：unembedded 表示尚未生成 embedding 的记忆条目数（供 UI 标注）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        total_f = conn.execute("SELECT COUNT(*) FROM facts WHERE scope_id != ''").fetchone()[0]
        unemb_f = conn.execute("SELECT COUNT(*) FROM facts WHERE embedding IS NULL OR embedding = ''").fetchone()[0]
        total_e = conn.execute("SELECT COUNT(*) FROM events WHERE scope_id != ''").fetchone()[0]
        unemb_e = conn.execute("SELECT COUNT(*) FROM events WHERE embedding IS NULL OR embedding = ''").fetchone()[0]
        conn.close()
        return {
            "facts_total": total_f, "facts_unembedded": unemb_f,
            "events_total": total_e, "events_unembedded": unemb_e,
            "embed_service": _embedder._available() and _embedder.embed("test") is not None,
        }
    except Exception:
        return {"facts_total": 0, "facts_unembedded": 0, "events_total": 0, "events_unembedded": 0, "embed_service": False}


def before_reply(scope: str = "", scope_id: str = "", message: str = "", user_id: str = "", context: Optional[Dict[str, Any]] = None, group_id: str = "", **kwargs) -> Optional[Dict[str, Any]]:
    if not _memory.config.get("enabled", True):
        return None
    if not _memory.config.get("memory_enabled", True):
        return None
    scope = scope or context.get("scope", "group") if context else "group"
    scope_id = scope_id or group_id or context.get("group_id", "") if context else group_id
    user_id = user_id or context.get("sender", "") if context else ""
    # 私聊（group_id 为空）时以 sender 作为会话作用域，保证记忆召回/写入同 key
    if not scope_id and scope == "private":
        scope_id = user_id or "private"
    parts: List[str] = []
    if scope in ("private", "group") and scope_id:
        block = _memory.get_prompt_block(scope, scope_id, message)
        if block:
            parts.append(block)
    if user_id and _memory.config.get("affection_enabled", True):
        aff = _memory.get_affection_context(scope_id, user_id)
        if aff:
            parts.append(aff)
    if _memory.config.get("persona_injection", True):
        persona = _memory.get_persona_export(scope_id)
        if persona:
            lines = [f"{k}: {v}" for k, v in persona.items()]
            parts.append("【长期人格】\n" + "\n".join(lines))
    if parts:
        return {"action": "inject", "prompt_blocks": parts}
    return None


def after_reply(scope: str = "", scope_id: str = "", message: str = "", reply: str = "", user_id: str = "", context: Optional[Dict[str, Any]] = None, **kwargs) -> Optional[Dict[str, Any]]:
    if not _memory.config.get("enabled", True):
        return None
    scope = scope or context.get("scope", "group") if context else "group"
    scope_id = scope_id or context.get("group_id", "") if context else ""
    user_id = user_id or context.get("sender", "") if context else ""
    # 私聊（group_id 为空）时以 sender 作为会话作用域，保证记忆写入/召回同 key
    if not scope_id and scope == "private":
        scope_id = user_id or "private"
    if scope in ("private", "group") and scope_id:
        importance = 0.5
        if any(kw in message for kw in ["?", "？", "什么", "为什么", "怎么", "如何"]):
            importance = 0.6
        if any(kw in message for kw in ["谢谢", "厉害", "不错", "哈哈", "笑死"]):
            importance = 0.7
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO messages (scope, scope_id, user_msg, bot_reply, timestamp, importance, tier) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (scope, str(scope_id), message[:500], reply[:500], time.time(), importance, _memory._classify_tier(importance, 1)),
        )
        conn.commit()
        conn.close()

        facts = []
        events = []
        if _memory.config.get("extract_facts", True):
            facts = _memory.extract_facts(scope_id, user_id or "", message)
            _memory._store_facts(scope_id, user_id or "", facts, message)
            _memory._update_persona_kernel(scope_id, facts)
            events = _memory.extract_events(scope_id, message)
            _memory._store_events(scope_id, events)

        if user_id and _memory.config.get("affection_enabled", True):
            _memory.update_affection(scope_id, user_id, message)
        _memory.consolidate(scope, scope_id)
    return None
