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
# 测试/运维可用环境变量把库指到别处（先例：tests/_sandbox.py 的 NEXSANDBASE_HOME）。
# 单测必须打这个，否则 import 时就会对**生产库**跑建表与迁移。
DB_PATH = os.environ.get("EGGBLOOM_SUPER_MEMORY_DB") or os.path.join(DATA_DIR, "super_memory.db")
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
        "embed_exe": {"type": "str", "default": "D:\\llama-b10068\\llama-server.exe"},
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


# ── 事实质量闸（2026-10-19）───────────────────────────────────────────
# 背景：`extract_facts` 的正则曾把对话碎片当人格存下来，导致 prompt 里出现
#   `user_preferences: 这：啥` / `你的谁呢` / `在叫我` / `群聊图片内容】（不：原图`
# 这类垃圾，每轮注入 → 模型被无意义碎片带偏，明显"人机感"。
#
# 主修复是删掉那条过于宽泛的 `X是Y` 正则；本闸是**二次拦截**，
# 只拦明显不是事实的形态，刻意**不做语义判断**（无 LLM、零成本）。

#: 空泛词 —— 单独出现时不构成事实（问句/代词/语气词）
_VAGUE_WORDS = {
    "这", "那", "啥", "谁", "什么", "怎么", "为什么", "哪", "哪个", "哪儿",
    "你", "我", "他", "她", "它", "您", "我们", "你们", "他们",
    "这个", "那个", "这些", "那些", "这样", "那样",
    "在", "是", "的", "了", "吧", "呢", "吗", "啊",
}

#: 疑问/揣测尾缀 —— 事实陈述不该以这些收尾
_QUESTION_TAILS = ("呢", "吗", "么", "？", "?", "呗", "嘛")


def _is_meaningful_fact(content: str) -> bool:
    """判断抽取结果是否够格当"事实"存下来。够格 → True。

    拦四类（都是实测见过的垃圾形态）：
      1. 过短/空泛：`这：啥`、`在叫我`、单字宾语
      2. 疑问句碎片：`你的谁呢`、`我叫什么`
      3. 括号残片：`群聊图片内容】（不：原图`（引用块/表情块被截断）
      4. `X：Y` 里主语是空泛代词：`这：啥`
    """
    c = (content or "").strip()
    if not c:
        return False
    # 必须含实义字符（中文/字母/数字）
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", c):
        return False
    # 1. 过短（`在叫我`=3、`这：啥`=3 全拦；保留 `学生`/`老师` 这类 ≥2 的身份）
    if len(c) < 2:
        return False
    # 2. 整条就是空泛词
    if c in _VAGUE_WORDS:
        return False
    # 3. 疑问尾缀
    if c.endswith(_QUESTION_TAILS):
        return False
    # 4. 括号残片
    if any(ch in c for ch in "【】（）[]{}"):
        return False
    # 5. `X：Y` —— 主语不能空泛、不能单字
    if "：" in c:
        subj, _, obj = c.partition("：")
        subj, obj = subj.strip(), obj.strip()
        if subj in _VAGUE_WORDS or len(subj) < 2:
            return False
        if not obj or obj in _VAGUE_WORDS:
            return False
    return True


# ── 身份事实质量闸（2026-10-31，1.5.11「记忆清洗」）────────────────────
# 真机遗留：群里随口一句「我是鸡蛋」「我是小狗」被抽成 `身份`，经
# `_update_persona_kernel` 进【长期人格】，每轮注入给所有人。用户原话
# 「[身份] 事实质量差（鸡蛋/小狗被抽成身份）」。
#
# 这里只做**形态判断**（零成本、不调 LLM）：身份是有词尾规律的
# （学生 / 程序员 / 老师 / 上海人），玩笑是随口的裸名词（鸡蛋 / 小狗 / 饿）。

#: 像「身份」的词尾
_IDENTITY_SUFFIXES = (
    "生", "员", "师", "家", "士", "工", "长", "商", "医", "警", "兵",
    "人", "手", "迷", "党", "控", "户", "客", "友",
)

#: 玩笑/接话的尾巴 —— 「我是来吃饭的」「我是你爹吧」不是身份
_IDENTITY_TAILS = ("的", "了", "吧", "啦", "哦", "嘛", "啊", "呀", "哈",
                   "呢", "吗", "着", "过", "呗", "咯", "哇", "嘞")

#: 一眼玩笑的信号：同一句里出现，就不当身份
_IDENTITY_JOKE_MARKERS = ("哈哈", "hhh", "233", "笑死", "草", "😂", "🤣",
                          "乐死", "绷不住", "乐子")


def _identity_fact_confidence(text: str, value: str) -> float:
    """判断「我是 X」抽出来的 X 像不像身份 → 置信度（0 = 丢弃，不存库）。"""
    v = (value or "").strip()
    if not v or len(v) > 12:
        return 0.0
    if v.endswith(_IDENTITY_TAILS):
        return 0.0
    t = text or ""
    if any(k in t for k in _IDENTITY_JOKE_MARKERS):
        return 0.0
    # 「我叫X」一定是自报名字 —— 名字不会有身份词尾（「小明」「老王」）
    if "我叫" in t and 2 <= len(v) <= 6:
        return 0.9
    if v.endswith(_IDENTITY_SUFFIXES):
        return 0.9 if len(v) >= 3 else 0.8
    if "是一名" in t or "是一位" in t or re.search(r"我是[做干].{1,12}的", t):
        return 0.8
    return 0.0


def _clean_identity(value: str) -> str:
    """剥掉「我是一名 / 我是一位」里的量词，只留身份本身。"""
    v = (value or "").strip()
    for prefix in ("一名", "一位", "一个", "名", "位"):
        if v.startswith(prefix) and len(v) > len(prefix) + 1:
            return v[len(prefix):].strip()
    return v


def _user_scope_filter(scope: str, focus_user: str) -> Tuple[str, tuple]:
    """记忆按人的 SQL 片段（facts / events 共用）。

    2026-10-31：群里「事件：吃饭」会跨人出现 —— events 原本只有 scope_id，
    没有"这是谁的事"。现在写入带 `user_id`，召回按人过滤：
      · group  + focus_user → **严格**只取本人的（老库里 user_id 为空的行
        无法归属，宁可不提也不串话）；
      · private + focus_user → 本作用域本来就只有一个人，老行（空 user_id）
        仍然算他的，避免升级后私聊记忆"失忆"；
      · 无 focus_user → 不过滤（保持旧行为）。
    """
    uid = str(focus_user or "")
    if not uid:
        return "", ()
    if str(scope) == "group":
        return " AND user_id=?", (uid,)
    return " AND (user_id=? OR user_id='' OR user_id IS NULL)", (uid,)


def _affection_level(score: int) -> str:
    """好感度 → 关系档（2026-10-21 调整阈值）。

    旧阈值 <10 陌生 / <40 熟悉 / <80 好友，但新用户初始分就是 20（=「熟悉」），
    陌生人档只在 10 分以下才够得着 —— 等于没有。现在：
      · 初始分降到 10，并让「陌生」覆盖到 20：头几次说话都还算生。
      · 20–50 熟悉（能嘟囔但仍客气）、50–80 好友（可以互怼）、80+ 死党（傲娇）。
    """
    s = int(score)
    return "陌生" if s < 20 else "熟悉" if s < 50 else "好友" if s < 80 else "死党"


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
                    user_id TEXT DEFAULT '',
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
                    user_id TEXT DEFAULT '',
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
            # 迁移：记忆按人（2026-10-31，1.5.11「记忆清洗」）
            #   ① events 只有 scope_id，群聊里所有人的事件混在一起 →
            #      「事件：吃饭」会跨人出现。补 user_id + 索引，召回按人过滤。
            #   ② persona_kernel 是作用域级、不区分人 → 「我是学生」谁先说就顶谁，
            #      再注入给全群。改成按人一行，唯一索引跟着换（老行 user_id=''，
            #      无法归属，group 侧不再注入）。
            # 两处都是幂等 ALTER，重复启动无副作用。
            try:
                ecols = [r[1] for r in conn.execute('PRAGMA table_info(events)').fetchall()]
                if 'user_id' not in ecols:
                    conn.execute('ALTER TABLE events ADD COLUMN user_id TEXT DEFAULT ""')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_events_user ON events(scope_id, user_id, timestamp)')
                pcols = [r[1] for r in conn.execute('PRAGMA table_info(persona_kernel)').fetchall()]
                if 'user_id' not in pcols:
                    conn.execute('ALTER TABLE persona_kernel ADD COLUMN user_id TEXT DEFAULT ""')
                conn.execute('DROP INDEX IF EXISTS idx_persona_key')
                conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_key ON persona_kernel(scope_id, user_id, key)')
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
        """从消息中抽取结构化事实。

        ⚠ 2026-10-19 修：此前第 4 条正则 `([^，。！？\\s]+?)是(.+?)` **过于宽泛** ——
        任何含「是」的短句都会被抽成「偏好」，实测把对话碎片当人格存了下来：
            「这是啥」     → 偏好「这：啥」
            「你是谁呢」   → 偏好「你的谁呢」
            「是在叫我」   → 偏好「在叫我」
        这些垃圾经 `get_prompt_block` 每轮注入 prompt（`【长期人格】user_preferences`）
        → 模型看到一堆无意义碎片 → 明显「人机感」。用户 2026-10-19 反馈
        「越来越像机器人」的根因之一。

        处置：① **删掉**那条宽泛正则（保留「我的X是Y」这种带明确主语的）；
              ② 加 `_is_meaningful_fact` 质量闸做二次拦截；
              ③ 主语/宾语长度下限提高，避免单字碎片。
        """
        facts: List[Dict[str, str]] = []
        text = text.strip()
        if not text or len(text) > 200:
            return facts
        seen_content: set = set()      # 同内容去重（多条正则会命中同一句）

        patterns = [
            (r"我是(.+?)(?=[，。！？\s]|$)", "身份", lambda m: _clean_identity(m.group(1))),
            (r"我叫(.+?)(?=[，。！？\s]|$)", "身份", lambda m: _clean_identity(m.group(1))),
            (r"我的([^，。！？\s]{2,10}?)是(.+?)(?=[，。！？\s]|$)",
             "偏好", lambda m: f"{m.group(1)}：{m.group(2)}"),
            # ★ 原 `([^，。！？\s]+?)是(.+?)` 已删除：无主语约束 → 抽取对话碎片。
            #   要重新引入必须带明确的偏好主语（如"我更喜欢/我觉得…是"），不能裸匹配。
            (r"我喜欢(.+?)(?=[，。！？\s]|$)", "偏好", lambda m: f"喜欢：{m.group(1)}"),
            (r"我不喜欢(.+?)(?=[，。！？\s]|$)", "偏好", lambda m: f"不喜欢：{m.group(1)}"),
            (r"我讨厌(.+?)(?=[，。！？\s]|$)", "偏好", lambda m: f"讨厌：{m.group(1)}"),
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
                    if not content or len(content) >= 50:
                        continue
                    # 身份类走形态闸：裸名词（鸡蛋/小狗）直接丢，不留给
                    # persona_kernel 每轮注入。
                    conf = 0.7
                    if category == "身份":
                        conf = _identity_fact_confidence(text, content)
                        if conf <= 0.0:
                            continue
                    if content and _is_meaningful_fact(content):
                        # 去重：多条正则会命中同一句（如「我的生日是X」同时命中
                        # 「我的X是Y」与「我的生日是Y」）→ 同内容只留第一条
                        if content not in seen_content:
                            seen_content.add(content)
                            facts.append({"category": category, "content": content,
                                          "confidence": conf})
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
                self._upsert_fact(conn, scope_id, user_id, fact["category"], fact["content"],
                                  source_msg, float(fact.get("confidence", 0.7)))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[SuperMemory] 存储事实失败: {e}")

    # ── 事件提取 ──

    def extract_events(self, scope_id: str, text: str, user_id: str = "") -> List[Dict[str, Any]]:
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
                    "user_id": str(user_id or ""),
                })
                break

        return events[:1]

    def _store_events(self, scope_id: str, events: List[Dict[str, Any]], user_id: str = ""):
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
                    "INSERT INTO events (scope_id, user_id, event_type, summary, participants, emotion, timestamp, importance, embedding, tier) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(scope_id), str(event.get("user_id") or user_id or ""),
                     event["event_type"], event["summary"], "", event.get("emotion", ""),
                     now, event.get("importance", 0.5), emb,
                     self._classify_tier(event.get("importance", 0.5), 1)),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[SuperMemory] 存储事件失败: {e}")

    # ── 关键记忆召回 ──

    def recall_key_memories(self, scope: str, scope_id: str, current_msg: str = "",
                            limit: int = 5, focus_user: str = "") -> List[Dict[str, Any]]:
        """召回本作用域的关键记忆。

        focus_user：只带这个人的好感度/事实（2026-10-30 真机：群里把所有人的
        好感度一起注进 prompt，聊着聊着就把 A 的事说到 B 头上）。
        """
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
            # 按人过滤（facts 与 events 同一口径，见 _user_scope_filter）
            uf_sql, uf_args = _user_scope_filter(scope, focus_user)

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
                "SELECT category, content, confidence, last_used, embedding, tier FROM facts "
                "WHERE scope_id=? AND confidence > 0.6 AND (? = 0 OR (embedding IS NOT NULL AND embedding != ''))"
                + uf_sql +
                " ORDER BY confidence DESC, last_used DESC LIMIT 100",
                (str(scope_id), 1 if use_vec else 0) + uf_args,
            )
            for row in cur.fetchall():
                raw_facts.append((row, _sim(row[4])))

            # 2. 重要事件（向量优先）
            raw_events = []
            cur = conn.execute(
                "SELECT event_type, summary, emotion, importance, timestamp, embedding, tier FROM events "
                "WHERE scope_id=? AND importance > 0.5 AND (? = 0 OR (embedding IS NOT NULL AND embedding != ''))"
                + uf_sql +
                " ORDER BY timestamp DESC LIMIT 100",
                (str(scope_id), 1 if use_vec else 0) + uf_args,
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
                "SELECT user_id, score, interactions, mood FROM affection "
                "WHERE scope_id=? AND (? = '' OR user_id=?) ORDER BY score DESC LIMIT ?",
                (str(scope_id), str(focus_user or ""), str(focus_user or ""), limit),
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

    def get_prompt_block(self, scope: str, scope_id: str, current_msg: str = "", focus_user: str = "") -> str:
        if not self.config.get("inject_into_prompt", True):
            return ""
        memories = self.recall_key_memories(scope, scope_id, current_msg,
                                         limit=self.config.get("recall_top_k", 5),
                                         focus_user=focus_user)
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

    def _update_persona_kernel(self, scope_id: str, facts: List[Dict[str, str]], user_id: str = ""):
        """把事实沉淀成「长期人格」。

        2026-10-31（1.5.11 记忆清洗）两处改动：
          ① **按人一行**：原本 key 只有 (scope_id, key)，群里谁先说「我是学生」
             就顶掉谁，再注入给所有人；现在带 user_id，各人的身份各自保存。
          ② **低置信度不进**：置信度 < 0.6 的事实（玩笑/接话，见
             `_identity_fact_confidence`）不写 persona_kernel —— 这块是
             每轮无条件注入的，宁缺毋滥。
        """
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
            uid = str(user_id or "")
            for fact in facts:
                if float(fact.get("confidence", 0.7)) < 0.6:
                    continue
                key = category_map.get(fact["category"], "misc")
                cur = conn.execute(
                    "SELECT id, confidence FROM persona_kernel WHERE scope_id=? AND user_id=? AND key=?",
                    (str(scope_id), uid, key),
                )
                row = cur.fetchone()
                if row:
                    conn.execute(
                        "UPDATE persona_kernel SET value=?, confidence=?, last_updated=? WHERE id=?",
                        (fact["content"], 0.9, now, row[0]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO persona_kernel (scope_id, user_id, key, value, confidence, last_updated) VALUES (?, ?, ?, ?, ?, ?)",
                        (str(scope_id), uid, key, fact["content"], 0.9, now),
                    )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[SuperMemory] 更新人格核心失败: {e}")

    def get_persona_export(self, scope_id: str, focus_user: str = "", scope: str = "group") -> Dict[str, Any]:
        try:
            uf_sql, uf_args = _user_scope_filter(scope, focus_user)
            conn = sqlite3.connect(DB_PATH)
            cur = conn.execute(
                "SELECT key, value, confidence FROM persona_kernel WHERE scope_id=?"
                + uf_sql +
                " ORDER BY confidence DESC, last_updated DESC",
                (str(scope_id),) + uf_args,
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

    # 计分词表（2026-10-21 重写）
    #   旧表的问题：把「滚」「牛」「强」这种**单字/高频子串**当关键词，
    #   实测误伤一大片 ——「滚雪球」「勉强可以」「蜗牛」「坚强」全被判成攻击/夸奖。
    #   而且「垃圾废物」这类词在本群里是互怼日常语气，按 -15 计必然把好感度打到下限，
    #   导致「高好感 = 傲娇」那一档永远触发不了。
    #   现在改成：
    #     ① 只有在**对着 Bot 说**且带明显指向时才算重击（含"你"或独占整句）
    #     ② 单字词（牛/强/滚）移出词表，只留无歧义的多字词
    #     ③ 正负幅度调小，让分数靠**互动次数**缓慢爬升，而不是一句话定生死
    _AFF_POS = ("哈哈", "笑死", "好棒", "厉害", "谢谢", "爱了", "好人", "优秀",
                "真棒", "不错", "贴贴", "可爱", "靠谱", "牛啊", "太强了", "谢谢你",
                "辛苦了", "爱你", "可以的")
    _AFF_NEG = ("垃圾", "废物", "傻逼", "sb", "脑残", "菜鸡", "没用", "恶心",
                "去死", "滚吧", "闭嘴", "蠢货", "弱智")
    # 只是语气、不是攻击 —— 出现这些时即使是「垃圾」也按玩笑处理
    _AFF_JOKE = ("草", "笑死", "哈哈哈", "绷不住", "狗东西", "你妈的")

    def update_affection(self, scope_id: str, user_id: str, text: str):
        if not self.config.get("affection_enabled", True):
            return
        # 计分规则见类体上方的 _AFF_POS / _AFF_NEG / _AFF_JOKE 注释（2026-10-21 重写）。
        t = str(text or "").lower()
        # 玩笑信号：有自嘲/大笑/问号堆 → 整句降为"普通互动"，不当攻击
        is_joke = any(k in t for k in self._AFF_JOKE)
        has_neg = any(kw in t for kw in self._AFF_NEG)
        has_pos = any(kw in t for kw in self._AFF_POS)
        if has_neg and not is_joke:
            # ★ 中文没有词边界，纯子串匹配必然误伤（「翻滚吧」含「滚吧」、
            #   「背单词背到sb了」含「sb」）。所以负面词必须**真的在骂 Bot** 才算：
            #     ⓐ 整句去掉标点空白后刚好等于那个词（"傻逼" 单独发）
            #     ⓑ 或句子里有第二人称指向（你/尼/小a/bot）
            #   两者都不满足 → 当成在说别的事，按普通互动 +1。
            _bare = "".join(ch for ch in t if ch.isalnum())
            # 群消息常带 "[昵称(QQ)] " 前缀，判定独占整句前先剥掉它
            # 注意：isalnum() 会把 "[" 和 "]" 一起滤掉，所以必须用**原始文本**
            # 判断有没有前缀，不能拿剥过符号的 _bare 判断（那样前缀永远剥不掉）。
            _body = t
            if t.lstrip().startswith("[") and "]" in t:
                _body = t.split("]", 1)[-1]
            _bare = "".join(ch for ch in _body if ch.isalnum())
            _directed = any(p in _body for p in ("你", "尼", "小a", "bot"))
            _exact = any(_bare == kw for kw in self._AFF_NEG)
            score = -4 if (_directed or _exact) else 1
        elif has_neg and is_joke:
            score = 1
        elif has_pos:
            score = 4
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
                new_score = max(0, min(100, row[0] + score))
                conn.execute(
                    "UPDATE affection SET score=?, interactions=interactions+1, last_interaction=? WHERE scope_id=? AND user_id=?",
                    (new_score, now, str(scope_id), str(user_id)),
                )
            else:
                # 新用户从 10 起（旧版 20 = 一开口就是"熟悉"，陌生人档形同虚设）
                new_score = max(0, min(100, 10 + score))
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
            level = _affection_level(score)
            mood_map = {"neutral": "平静", "happy": "开心", "sad": "低落", "angry": "生气"}
            mood_text = mood_map.get(mood, mood)
            # ── 行为后果（2026-10-21）──
            # 只报「好感度 95，死党」模型不知道该怎么用：它只会换个称谓继续当客服。
            # 关系等级必须落到**具体口气**上：能不能抱怨、能不能开黄腔、要不要客气。
            # 注意：这里给的是"许可范围"，不是"必须表现"，所以写的都是"可以"而不是"要"。
            tone = {
                # 关系浅：抱怨是真的，直白嫌烦，但事照样办
                "陌生": "关系还浅。抱怨是真的、直白的（「又来」「烦不烦」），不要客气，但事必须办完再说。",
                # 抱怨还在，加一点熟人的随便
                "熟悉": "算脸熟了。抱怨照旧，但口气松下来了（「行吧行吧」「就这一次啊」）。",
                # 抱怨变少，开始傲娇
                "好友": "关系不错。抱怨明显变少，换成傲娇（「哼，就这一次」「求我啊」），别真嫌烦。",
                # 全是傲娇/撒娇，基本不抱怨
                "死党": "很熟了。基本不抱怨，全是傲娇和撒娇，或者干脆逗他一下。嘴上说不要，手比谁都快。",
            }[level]
            return (f"【用户关系】{user_id}：好感度 {score}，互动 {interactions} 次，"
                    f"{level}，当前情绪 {mood_text}。\n"
                    f"→ 对他来说你是这个关系档次，说话按这个来：{tone}")
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
        block = _memory.get_prompt_block(scope, scope_id, message, focus_user=user_id)
        if block:
            parts.append(block)
    if user_id and _memory.config.get("affection_enabled", True):
        aff = _memory.get_affection_context(scope_id, user_id)
        if aff:
            parts.append(aff)
    if _memory.config.get("persona_injection", True):
        persona = _memory.get_persona_export(scope_id, focus_user=user_id, scope=scope)
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
            _memory._update_persona_kernel(scope_id, facts, user_id or "")
            events = _memory.extract_events(scope_id, message, user_id or "")
            _memory._store_events(scope_id, events, user_id or "")

        if user_id and _memory.config.get("affection_enabled", True):
            _memory.update_affection(scope_id, user_id, message)
        _memory.consolidate(scope, scope_id)
    return None

# ── 通用插件通道动作（WebUI plugin_id='ego.super_memory'） ──
def _action_stats(params=None):
    params = params or {}
    scope_id = str(params.get("scope_id") or "default").strip()
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM messages WHERE scope_id=?", (scope_id,))
        messages = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM facts WHERE scope_id=?", (scope_id,))
        facts = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM events WHERE scope_id=?", (scope_id,))
        events = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM persona_kernel WHERE scope_id=?", (scope_id,))
        persona = cur.fetchone()[0]
        conn.close()
        return {"messages": messages, "facts": facts, "events": events, "persona": persona}
    except Exception as e:
        return {"error": str(e)}


def _action_test_embed(params=None):
    params = params or {}
    text = str(params.get("text") or "测试")
    try:
        t0 = time.time()
        v1 = _embedder.embed(text)
        if not v1:
            return {"ok": False, "error": "embedding 服务不可用(8083 未启动)"}
        v2 = _embedder.embed(text + "的同类描述")
        t1 = time.time()
        sim = _vec_sim(v1, v2)
        return {"ok": True, "latency_ms": round((t1 - t0) * 1000, 1), "dim": len(v1), "sample_sim": round(sim, 4)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _action_recall(params=None):
    params = params or {}
    scope_id = str(params.get("scope_id") or "default").strip()
    query = str(params.get("query") or "")
    limit = int(params.get("limit") or 5)
    try:
        items = _memory.recall_key_memories(scope="group", scope_id=scope_id, current_msg=query, limit=limit)
        return {"items": items or []}
    except Exception as e:
        return {"items": [], "error": str(e)}


PLUGIN_ACTIONS = {
    "get_config": lambda params: get_config(),
    "set_config": lambda params: set_config(params or {}),
    "stats": _action_stats,
    "test_embed": _action_test_embed,
    "recall": _action_recall,
    "tier_stats": lambda params: get_tier_stats(str((params or {}).get("scope_id") or "default").strip()),
    "set_tier": lambda params: set_tier(
        scope_id=str((params or {}).get("scope_id") or "default").strip(),
        table=str((params or {}).get("table") or "facts").strip(),
        item_id=int((params or {}).get("item_id") or 0),
        tier=str((params or {}).get("tier") or "long").strip(),
    ),
}
