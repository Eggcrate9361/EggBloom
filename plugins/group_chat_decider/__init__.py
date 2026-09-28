# -*- coding: utf-8 -*-
"""
group_chat_decider 群聊决策插件 v3.0 - 话题流 + 群画像 + 记忆匹配 + 情绪感知

核心升级：
- 话题流追踪：识别连续消息中的话题延续/转换，判断 bot 该不该接、怎么接
- 群画像：每个群独立记忆活跃时段、话题偏好、插嘴密度
- 记忆匹配：从 super_memory 查 bot 是否有关似经历，有就提高参与度
- 情绪感知：识别群聊情绪（high/medium/low energy）
- 连续对话感知：bot 刚插话后是否该继续
- AI 读空气升级：不只是"回不回"，而是输出"氛围/情绪/插话点/接梗建议"

用法：
    from plugins.group_chat_decider import PLUGIN_META, before_reply, after_reply
"""

import json
import os
import re
import sqlite3
import time
import threading
import random
import sys
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "group_chat_decider.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

PLUGIN_META = {
    "name": "group_chat_decider",
    "version": "3.0.0",
    "description": "群聊预决策 - 话题流+群画像+记忆匹配+情绪感知",
    "author": "EggBloom",
    "group_only": True,
    "enabled": True,
    "dependencies": [],
    "hooks": ["before_reply", "after_reply"],
    "priority": 5,
    "estimated_tokens": 300,
    "tags": ["群聊决策", "API可接入"],
    "page": "plugins/group_chat_decider/ui.html",
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
        print(f"[GroupDecider] 保存失败 {path}: {e}")


# ═══════════════════════════════════════════
# 数据层
# ═══════════════════════════════════════════

class GroupChatDB:
    """群聊决策数据库"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS group_profiles (
                    group_id TEXT PRIMARY KEY,
                    active_hours TEXT DEFAULT '{}',
                    topic_keywords TEXT DEFAULT '[]',
                    avg_msg_interval REAL DEFAULT 60,
                    reply_density REAL DEFAULT 0.3,
                    last_active REAL NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS topic_flows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    message TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_topic_group ON topic_flows(group_id, timestamp);

                CREATE TABLE IF NOT EXISTS emotion_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    emotion TEXT NOT NULL,
                    energy REAL NOT NULL,
                    timestamp REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_emotion_group ON emotion_logs(group_id, timestamp);

                CREATE TABLE IF NOT EXISTS conversation_chains (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    chain_type TEXT NOT NULL,
                    participants TEXT DEFAULT '[]',
                    message_count INTEGER DEFAULT 0,
                    last_activity REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chain_group ON conversation_chains(group_id, last_activity);
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[GroupDecider] 初始化数据库失败: {e}")

    def update_group_profile(self, group_id: str, **kwargs):
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                existing = conn.execute(
                    "SELECT * FROM group_profiles WHERE group_id=?", (str(group_id),)
                ).fetchone()
                if existing:
                    sets = ", ".join(f"{k}=?" for k in kwargs)
                    vals = list(kwargs.values()) + [str(group_id)]
                    conn.execute(f"UPDATE group_profiles SET {sets} WHERE group_id=?", vals)
                else:
                    cols = ",".join(kwargs.keys())
                    placeholders = ",".join("?" for _ in kwargs)
                    vals = list(kwargs.values())
                    conn.execute(
                        f"INSERT INTO group_profiles (group_id, {cols}, last_active, created_at) VALUES (?, {placeholders}, ?, ?)",
                        [str(group_id)] + vals + [time.time(), time.time()]
                    )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[GroupDecider] 更新群画像失败: {e}")

    def get_group_profile(self, group_id: str) -> dict:
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT * FROM group_profiles WHERE group_id=?", (str(group_id),)
            ).fetchone()
            conn.close()
            if not row:
                return {}
            cols = [d[0] for d in conn.execute("PRAGMA table_info(group_profiles)").fetchall()]
            return dict(zip(cols, row))
        except Exception:
            return {}

    def add_topic_message(self, group_id: str, topic: str, message: str, sender: str):
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "INSERT INTO topic_flows (group_id, topic, message, sender, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (str(group_id), topic[:50], message[:200], str(sender), time.time())
                )
                # 保留最近 200 条
                conn.execute(
                    "DELETE FROM topic_flows WHERE group_id=? AND id NOT IN (SELECT id FROM topic_flows WHERE group_id=? ORDER BY timestamp DESC LIMIT 200)",
                    (str(group_id), str(group_id))
                )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[GroupDecider] 记录话题流失败: {e}")

    def get_recent_topic_messages(self, group_id: str, topic: str, limit: int = 10) -> List[dict]:
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT message, sender, timestamp FROM topic_flows WHERE group_id=? AND topic=? ORDER BY timestamp DESC LIMIT ?",
                (str(group_id), topic[:50], limit)
            ).fetchall()
            conn.close()
            return [{"message": r[0], "sender": r[1], "timestamp": r[2]} for r in rows]
        except Exception:
            return []

    def log_emotion(self, group_id: str, emotion: str, energy: float):
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "INSERT INTO emotion_logs (group_id, emotion, energy, timestamp) VALUES (?, ?, ?, ?)",
                    (str(group_id), emotion, energy, time.time())
                )
                # 保留最近 500 条
                conn.execute(
                    "DELETE FROM emotion_logs WHERE group_id=? AND id NOT IN (SELECT id FROM emotion_logs WHERE group_id=? ORDER BY timestamp DESC LIMIT 500)",
                    (str(group_id), str(group_id))
                )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[GroupDecider] 记录情绪失败: {e}")

    def get_recent_emotions(self, group_id: str, limit: int = 20) -> List[dict]:
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT emotion, energy, timestamp FROM emotion_logs WHERE group_id=? ORDER BY timestamp DESC LIMIT ?",
                (str(group_id), limit)
            ).fetchall()
            conn.close()
            return [{"emotion": r[0], "energy": r[1], "timestamp": r[2]} for r in rows]
        except Exception:
            return []

    def update_conversation_chain(self, group_id: str, chain_type: str, participants: List[str]):
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                existing = conn.execute(
                    "SELECT id, message_count FROM conversation_chains WHERE group_id=? AND chain_type=?",
                    (str(group_id), chain_type)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE conversation_chains SET message_count=?, last_activity=?, participants=? WHERE id=?",
                        (existing[1] + 1, time.time(), json.dumps(participants, ensure_ascii=False), existing[0])
                    )
                else:
                    conn.execute(
                        "INSERT INTO conversation_chains (group_id, chain_type, participants, message_count, last_activity) VALUES (?, ?, ?, ?, ?)",
                        (str(group_id), chain_type, json.dumps(participants, ensure_ascii=False), 1, time.time())
                    )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[GroupDecider] 更新对话链失败: {e}")

    def get_recent_chains(self, group_id: str, limit: int = 5) -> List[dict]:
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT chain_type, participants, message_count, last_activity FROM conversation_chains WHERE group_id=? ORDER BY last_activity DESC LIMIT ?",
                (str(group_id), limit)
            ).fetchall()
            conn.close()
            return [
                {
                    "chain_type": r[0],
                    "participants": json.loads(r[1]) if r[1] else [],
                    "message_count": r[2],
                    "last_activity": r[3]
                }
                for r in rows
            ]
        except Exception:
            return []


# ═══════════════════════════════════════════
# 话题流追踪
# ═══════════════════════════════════════════

class TopicFlowTracker:
    """话题流追踪器"""

    # 话题关键词映射
    TOPIC_KEYWORDS = {
        "游戏": {"游戏", "打游戏", "开黑", "王者", "原神", "崩铁", "LOL", "无畏契约", "Steam", "Switch", "ps5", "bug", "报错", "跑不起来"},
        "番剧": {"番", "动画", "新番", "追番", "看番", "动漫", "漫改", "声优", "OP", "ED"},
        "编程": {"代码", "编程", "python", "javascript", "bug", "报错", "写代码", "github", "api", "后端", "前端"},
        "生活": {"吃了", "喝了", "睡觉", "起床", "下班", "上班", "周末", "加班", "外卖", "奶茶", "咖啡"},
        "情感": {"喜欢", "讨厌", "难过", "开心", "生气", "分手", "表白", "暗恋", "恋爱", "单身"},
        "音乐": {"歌", "音乐", "歌手", "演唱会", "专辑", "单曲", "网易云", "QQ音乐"},
        "八卦": {"瓜", "八卦", "吃瓜", "热搜", "新闻", "爆料"},
    }

    def __init__(self, db: GroupChatDB):
        self.db = db

    def detect_topic(self, message: str) -> str:
        """检测消息属于哪个话题"""
        t = message.lower()
        for topic, keywords in self.TOPIC_KEYWORDS.items():
            if any(kw in t for kw in keywords):
                return topic
        return "闲聊"

    def is_topic_continuation(self, group_id: str, current_topic: str, window: int = 5) -> bool:
        """检查当前话题是否在最近消息中延续"""
        recent = self.db.get_recent_topic_messages(group_id, current_topic, limit=window)
        if not recent:
            return False
        # 最近 5 条中有 2+ 条同话题 → 延续
        return len(recent) >= 2

    def get_topic_transition(self, group_id: str, current_topic: str) -> str:
        """获取话题转换状态"""
        if self.is_topic_continuation(group_id, current_topic):
            return "continuation"
        return "new_topic"


# ═══════════════════════════════════════════
# 群画像
# ═══════════════════════════════════════════

class GroupProfiler:
    """群画像管理器"""

    def __init__(self, db: GroupChatDB):
        self.db = db

    def update_hours(self, group_id: str):
        """更新群活跃时段"""
        try:
            conn = sqlite3.connect(self.db.db_path)
            row = conn.execute(
                "SELECT active_hours FROM group_profiles WHERE group_id=?", (str(group_id),)
            ).fetchone()
            conn.close()
            hours = {}
            if row and row[0]:
                try:
                    hours = json.loads(row[0])
                except Exception:
                    hours = {}
        except Exception:
            hours = {}

        hour = time.localtime().tm_hour
        hours[str(hour)] = hours.get(str(hour), 0) + 1
        self.db.update_group_profile(group_id, active_hours=json.dumps(hours, ensure_ascii=False))

    def get_active_level(self, group_id: str) -> str:
        """获取群活跃度等级"""
        profile = self.db.get_group_profile(group_id)
        density = profile.get("reply_density", 0.3)
        if density > 0.7:
            return "high"
        elif density > 0.4:
            return "medium"
        return "low"

    def get_peak_hours(self, group_id: str) -> List[int]:
        """获取群最活跃时段"""
        profile = self.db.get_group_profile(group_id)
        try:
            hours = json.loads(profile.get("active_hours", "{}"))
        except Exception:
            hours = {}
        if not hours:
            return []
        sorted_hours = sorted(hours.items(), key=lambda x: x[1], reverse=True)
        return [int(h) for h, _ in sorted_hours[:3]]


# ═══════════════════════════════════════════
# 记忆匹配
# ═══════════════════════════════════════════

class MemoryMatcher:
    """记忆匹配器 - 从 super_memory 查 bot 是否有关似经历"""

    def __init__(self, memory_plugin_name: str = "ego.super_memory"):
        self.memory_plugin_name = memory_plugin_name
        self._client_cache = None

    def _get_memory_plugin(self):
        if self._client_cache is not None:
            return self._client_cache
        # 真实路径在 plugins/ego/super_memory（SuperMemory 由 plugins.ego 导出）
        candidates = [self.memory_plugin_name]
        if self.memory_plugin_name != "ego.super_memory":
            candidates.append("ego.super_memory")
        if self.memory_plugin_name != "super_memory":
            candidates.append("super_memory")
        for name in candidates:
            try:
                memory_mod = __import__(f"plugins.{name}", fromlist=["SuperMemory"])
                sm = getattr(memory_mod, "SuperMemory", None)
                if sm is not None:
                    self._client_cache = sm()
                    return self._client_cache
            except Exception:
                continue
        print(f"[GroupDecider] 加载记忆插件失败: plugins.{self.memory_plugin_name} 不可用")
        return None

    def match_topic(self, topic: str, group_id: str, limit: int = 5) -> List[dict]:
        """匹配 bot 记忆中的相关经历"""
        memory = self._get_memory_plugin()
        if memory is None:
            return []
        try:
            facts = memory.recall_key_memories(group_id, topic, top_k=limit)
            return facts if facts else []
        except Exception:
            return []

    def has_experience(self, topic: str, group_id: str) -> bool:
        """bot 是否有该话题的相关经历"""
        return len(self.match_topic(topic, group_id, limit=1)) > 0


# ═══════════════════════════════════════════
# 情绪感知
# ═══════════════════════════════════════════

class EmotionSense:
    """群聊情绪感知"""

    POSITIVE_WORDS = {"哈哈", "哈哈哈", "笑死", "太好笑了", "牛", "牛逼", "厉害", "赞", "棒", "不错", "开心", "快乐", "爱了", "真香", "666", "yyds", "绝了", "破防", "泪目", "感动", "祝福", "恭喜", "撒花"}
    NEGATIVE_WORDS = {"难过", "伤心", "气死", "烦", "恶心", "垃圾", "什么鬼", "服了", "吐了", "下头", "无语", "生气", "愤怒", "骂", "滚", "别吵", "吵死了", "能不能", "烦不烦"}
    HIGH_ENERGY_PATTERNS = [r"！{2,}", r"\?{2,}", r"ha{2,}", r"哈{2,}", r"草{1,}", r"woc", r"wo cao", r"我靠", r"救命", r"离谱", r"离大谱"]

    def __init__(self, db: GroupChatDB):
        self.db = db

    def detect_emotion(self, message: str, recent_messages: List[str]) -> Tuple[str, float]:
        """检测情绪和能量等级"""
        t = message.lower()
        energy = 0.5

        # 检查正面词
        pos_count = sum(1 for w in self.POSITIVE_WORDS if w in t)
        neg_count = sum(1 for w in self.NEGATIVE_WORDS if w in t)

        # 检查高能量模式
        high_energy = any(re.search(p, t) for p in self.HIGH_ENERGY_PATTERNS)

        if pos_count > neg_count:
            emotion = "positive"
            energy = min(1.0, 0.6 + pos_count * 0.1)
        elif neg_count > pos_count:
            emotion = "negative"
            energy = min(1.0, 0.6 + neg_count * 0.1)
        else:
            emotion = "neutral"
            energy = 0.5

        if high_energy:
            energy = min(1.0, energy + 0.3)

        # 考虑最近消息
        if recent_messages:
            recent_text = " ".join(recent_messages[-5:]).lower()
            recent_pos = sum(1 for w in self.POSITIVE_WORDS if w in recent_text)
            recent_neg = sum(1 for w in self.NEGATIVE_WORDS if w in recent_text)
            if recent_pos > recent_neg:
                emotion = "positive"
                energy = min(1.0, energy + 0.1)
            elif recent_neg > recent_pos:
                emotion = "negative"
                energy = min(1.0, energy + 0.1)

        return emotion, energy

    def log_emotion(self, group_id: str, emotion: str, energy: float):
        self.db.log_emotion(group_id, emotion, energy)

    def get_group_emotion_trend(self, group_id: str, limit: int = 10) -> str:
        """获取群情绪趋势"""
        emotions = self.db.get_recent_emotions(group_id, limit=limit)
        if not emotions:
            return "neutral"
        pos = sum(1 for e in emotions if e["emotion"] == "positive")
        neg = sum(1 for e in emotions if e["emotion"] == "negative")
        if pos > neg:
            return "positive"
        elif neg > pos:
            return "negative"
        return "neutral"


# ═══════════════════════════════════════════
# 对话链感知
# ═══════════════════════════════════════════

class ConversationChainDetector:
    """对话链检测器 - 识别 bot 是否在连续对话中"""

    def __init__(self, db: GroupChatDB):
        self.db = db

    def detect_chain_type(self, group_id: str, recent_msgs: List, bot_qq: str) -> str:
        """检测对话链类型"""
        if not recent_msgs:
            return "isolated"

        senders = []
        for msg in recent_msgs[-6:]:
            if isinstance(msg, (list, tuple)) and len(msg) > 1:
                senders.append(str(msg[1]))

        if not senders:
            return "isolated"

        unique_senders = set(senders)
        bot_count = senders.count(bot_qq)

        # Bot 刚插话，连续 2+ 条
        if bot_count >= 2 and len(unique_senders) <= 2:
            return "bot_continuation"

        # 2+ 用户在互聊，Bot 没参与
        if len(unique_senders) >= 2 and bot_qq not in unique_senders:
            return "user_conversation"

        # Bot 被 @ 或刚被提到
        if bot_qq in unique_senders:
            return "bot_mentioned"

        # 单用户连续发言
        if len(unique_senders) == 1:
            return "single_user"

        return "multi_user"

    def update_chain(self, group_id: str, chain_type: str, participants: List[str]):
        self.db.update_conversation_chain(group_id, chain_type, participants)


# ═══════════════════════════════════════════
# 核心决策器
# ═══════════════════════════════════════════

@dataclass
class DecisionV3:
    """决策结果 v3"""
    should_reply: bool = True
    reason: str = ""
    atmosphere: str = "闲聊"
    atmosphere_confidence: float = 0.0
    strategy: str = "轻松闲聊"
    strategy_detail: str = ""
    topic: str = "闲聊"
    topic_continuation: bool = False
    emotion: str = "neutral"
    energy: float = 0.5
    memory_match: bool = False
    memory_items: List[dict] = None
    conversation_chain: str = "isolated"
    suggested_tone: str = "normal"
    hook_points: List[str] = None

    def __post_init__(self):
        if self.memory_items is None:
            self.memory_items = []
        if self.hook_points is None:
            self.hook_points = []

    def to_dict(self) -> dict:
        return asdict(self)

    def to_prompt_injection(self) -> str:
        """注入到主模型 system_prompt 的决策上下文"""
        parts = []
        parts.append(f"【群聊氛围】{self.atmosphere}（置信度 {self.atmosphere_confidence:.1f}）")
        parts.append(f"【回复策略】{self.strategy}——{self.strategy_detail}")
        if self.topic and self.topic != "闲聊":
            parts.append(f"【当前话题】{self.topic}（{'延续' if self.topic_continuation else '新话题'}）")
        if self.emotion != "neutral":
            parts.append(f"【群聊情绪】{self.emotion}（能量 {self.energy:.1f}）")
        if self.memory_match and self.memory_items:
            mem_summaries = [m.get("content", "") for m in self.memory_items[:3]]
            parts.append(f"【相关记忆】{'；'.join(mem_summaries)}")
        if self.conversation_chain != "isolated":
            parts.append(f"【对话链】{self.conversation_chain}")
        if self.suggested_tone != "normal":
            parts.append(f"【建议语气】{self.suggested_tone}")
        if self.hook_points:
            parts.append(f"【接梗建议】{', '.join(self.hook_points)}")
        return "\n".join(parts)


# ═══════════════════════════════════════════
# 全局状态
# ═══════════════════════════════════════════

_db = None
_topic_tracker = None
_profiler = None
_memory_matcher = None
_emotion_sense = None
_chain_detector = None
_ai_client_cache = {}
_last_reply_time = {}


def _get_db() -> GroupChatDB:
    global _db
    if _db is None:
        _db = GroupChatDB(DB_PATH)
    return _db


def _get_topic_tracker() -> TopicFlowTracker:
    global _topic_tracker
    if _topic_tracker is None:
        _topic_tracker = TopicFlowTracker(_get_db())
    return _topic_tracker


def _get_profiler() -> GroupProfiler:
    global _profiler
    if _profiler is None:
        _profiler = GroupProfiler(_get_db())
    return _profiler


def _get_memory_matcher() -> MemoryMatcher:
    global _memory_matcher
    if _memory_matcher is None:
        _memory_matcher = MemoryMatcher()
    return _memory_matcher


def _get_emotion_sense() -> EmotionSense:
    global _emotion_sense
    if _emotion_sense is None:
        _emotion_sense = EmotionSense(_get_db())
    return _emotion_sense


def _get_chain_detector() -> ConversationChainDetector:
    global _chain_detector
    if _chain_detector is None:
        _chain_detector = ConversationChainDetector(_get_db())
    return _chain_detector


# ═══════════════════════════════════════════
# AI 客户端
# ═══════════════════════════════════════════

def set_ai_client(client):
    _ai_client_cache["client"] = client


def _get_ai_client():
    """决策 AI：优先用 free_models 预设（智谱/阿里/硅基/本地），
    未配置 key 时降级回主模型（保持向后兼容）。"""
    client = _ai_client_cache.get("client")
    if client is not None:
        return client
    try:
        from . import free_models
        if free_models.is_configured():
            # free 预设已配置 key —— 通过 providers 薄封装直接调用，无需 AIClient
            return _FreeModelClient()
    except Exception:
        pass
    try:
        import __main__ as main_mod
        if hasattr(main_mod, "ai"):
            client = main_mod.ai
            _ai_client_cache["client"] = client
            return client
    except (ImportError, AttributeError):
        pass
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import load_config, get_provider_api_key
        from ai_client import AIClient
        cfg = load_config()
        api_url = cfg.get("api_url", "")
        provider = cfg.get("api_provider", "deepseek")
        api_key = get_provider_api_key(provider)
        model = cfg.get("model_name", "deepseek-flash")
        if api_key or provider == "ollama":
            client = AIClient(api_url, api_key, model, provider=provider, temperature=cfg.get("ai_temperature"))
            _ai_client_cache["client"] = client
            return client
    except Exception as e:
        print(f"[GroupDecider] AI客户端初始化失败: {e}")
    return None


class _FreeModelClient:
    """适配 free_models 预设的轻量客户端，提供 AIClient 兼容的 .call()"""

    def call(self, messages, tools=None, stream=False, max_tokens=300):
        from . import providers
        # 提取 user 提示文本（兼容纯文本 / 多模态结构）
        prompt = ""
        for m in messages:
            content = m.get("content", "") if isinstance(m, dict) else ""
            if isinstance(content, str):
                prompt += content + "\n"
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        prompt += str(c.get("text", "")) + "\n"
        result = providers.call_text(prompt, max_tokens=max_tokens)
        if not result:
            return None, None, None
        return result, None, None


# ═══════════════════════════════════════════
# 硬规则
# ═══════════════════════════════════════════

# AI 主动参与度三档：conservative 保守 / normal 正常 / active 活跃
PARTICIPATION_LEVELS = {
    "conservative": {
        "label": "保守",
        "desc": "只回应明确信号（@、问句、强兴趣），少刷存在感",
        "prompt": "除非被明确 @、有直接提问、或话题与 Bot 强相关，否则尽量跳过，避免打扰群聊。",
    },
    "normal": {
        "label": "正常",
        "desc": "按话题相关性自然参与，有参与点就接话",
        "prompt": "如果消息是闲聊且 Bot 没有明显参与点，应该跳过；话题相关时自然参与。",
    },
    "active": {
        "label": "活跃",
        "desc": "更主动找接梗点，允许在用户互聊时适度插话",
        "prompt": "主动找参与点：积极接梗、补充观点、在话题延续时顺势参与，即使没有明确 @ 也可以参与，但别刷屏。",
    },
}

PARTICIPATION_DEFAULT = "normal"

def _get_participation() -> str:
    """读取 config.json 里的参与度档位（非法值回退 normal）"""
    from . import free_models
    try:
        p = free_models.load_plugin_config().get("participation", PARTICIPATION_DEFAULT)
        return p if p in PARTICIPATION_LEVELS else PARTICIPATION_DEFAULT
    except Exception:
        return PARTICIPATION_DEFAULT


# ═══════════════════════════════════════════
# 决策参数（v3 真正消费，存 data/config.json）
# ═══════════════════════════════════════════

DECISION_PARAMS_DEFAULTS = {
    "confidence_threshold": 0.3,    # AI 读空气低置信度放行阈值（低于此值默认放行）
    "max_context_messages": 5,      # 送入 AI 的最近消息条数
    "max_bot_replies": 3,           # 最近窗口内 Bot 连续回复上限（超过则冷却）
    "cooldown_seconds": 120,        # Bot 回复后冷却时长（秒），冷却期内不主动插话
    "max_tracked_users": 50,        # 群画像最多追踪的用户数（供 GroupProfiler 使用）
}

def _get_decision_params() -> dict:
    """读取 config.json 里的决策参数（缺失项补默认值）"""
    from . import free_models
    try:
        cfg = free_models.load_plugin_config()
        out = {}
        for k, v in DECISION_PARAMS_DEFAULTS.items():
            raw = cfg.get(k, v)
            try:
                if isinstance(v, int):
                    raw = int(raw)
                else:
                    raw = float(raw)
            except Exception:
                raw = v
            out[k] = raw
        return out
    except Exception:
        return dict(DECISION_PARAMS_DEFAULTS)

_BOT_INTEREST_KEYWORDS = {"游戏", "番", "编程", "代码", "python", "javascript",
                           "心理学", "精神", "人格", "焦虑", "抑郁", "失眠",
                           "MOBA", "开放世界", "看番", "追番", "写代码",
                           "打游戏", "王者荣耀", "原神", "崩铁", "英雄联盟",
                           "bug", "bug了", "报错", "报错了吗", "跑不起来"}

_SKIP_TRIVIAL = {"1", "2", "3", "6", "嗯", "哦", "哈哈", "hhh", "好的",
                  "行", "ok", "OK", "666", "……", "额", "额。", "噗", "?", "？", " "}

_LIKELY_REPLY_KEYWORDS = {"小a", "？", "?", "吗", "呢", "吧", "嘛",
                           "怎么", "什么", "为啥", "为什么", "是不是",
                           "有没有", "能不能", "会不会", "要不要", "求助", "帮忙", "看看", "快看"}


def _has_bot_interest(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _BOT_INTEREST_KEYWORDS)


def _has_likely_reply_signal(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _LIKELY_REPLY_KEYWORDS)


def _is_trivial(text: str) -> bool:
    return text.strip().lower() in _SKIP_TRIVIAL


# ═══════════════════════════════════════════
# AI Prompt 构建
# ═══════════════════════════════════════════

def _build_ai_prompt(message: str, context: dict, decision_context: dict) -> str:
    """构建 AI 读空气 prompt（v4 链式思考版）"""
    parts = []

    # 基础信息
    parts.append("你是小a，一个 18 岁的网上冲浪选手，现在在一个群里。")
    parts.append("请像真人一样『先思考、再判断』：先分析这条消息，再决定 bot 该如何参与群聊。")

    # 当前消息
    parts.append(f"\n当前消息：{message}")

    # 图片描述（视觉理解）
    image_desc = decision_context.get("image_desc", "")
    if image_desc:
        parts.append(f"\n【消息里的图片内容】{image_desc}")

    # 转发消息内容
    forward_text = decision_context.get("forward_text", "")
    if forward_text:
        parts.append(f"\n【转发的聊天记录】\n{forward_text}")

    # 话题信息
    topic = decision_context.get("topic", "闲聊")
    topic_cont = decision_context.get("topic_continuation", False)
    parts.append(f"话题：{topic}（{'延续' if topic_cont else '新话题'}）")

    # 群情绪
    emotion = decision_context.get("emotion", "neutral")
    energy = decision_context.get("energy", 0.5)
    parts.append(f"群聊情绪：{emotion}（能量等级 {energy:.1f}）")

    # 对话链
    chain = decision_context.get("conversation_chain", "isolated")
    parts.append(f"对话链：{chain}")

    # 记忆匹配
    memory_items = decision_context.get("memory_items", [])
    if memory_items:
        mem_summaries = [m.get("content", "") for m in memory_items[:3]]
        parts.append(f"相关记忆：{'；'.join(mem_summaries)}")

    # 最近消息
    recent_msgs = context.get("recent_msgs", [])
    if recent_msgs:
        ctx_limit = int(_get_decision_params()["max_context_messages"])
        parts.append("\n最近消息：")
        for i, msg in enumerate(recent_msgs[-ctx_limit:]):
            if isinstance(msg, (list, tuple)) and len(msg) > 2:
                sender = msg[1]
                text = msg[2]
                parts.append(f"  {i+1}. {sender}: {text}")

    # 群画像
    profiler = _get_profiler()
    group_id = context.get("group_id", "")
    try:
        _profile = profiler.db.get_group_profile(group_id)
        _active_level = profiler.get_active_level(group_id)
        _peak = profiler.get_peak_hours(group_id)
        parts.append(f"\n群画像：活跃度 {_active_level}，峰值时段 {_peak or '未知'}")
    except Exception:
        pass

    # 链式思考引导
    parts.append("\n请按以下步骤在头脑中分析（thinking 字段记录要点）：")
    parts.append('''1. 意图分析：这条消息在聊什么？是否在问 Bot？是否要求回应？
2. 话题价值：这个话题值得 Bot 参与吗？参与能带来什么（接梗/信息/共鸣/陪伴）？
3. 时机判断：现在插话合适吗？（考虑群活跃度、是否用户互聊、是否刚插过话）
4. 风险判断：参与会不会冷场、冒犯、刷屏？

然后输出 JSON 格式：''')
    parts.append('''{
  "thinking": "简洁的推理过程（2-3句）",
  "should_reply": true/false,
  "participate_value": 0.0-1.0,
  "timing_score": 0.0-1.0,
  "risk_score": 0.0-1.0,
  "reason": "简短原因",
  "atmosphere": "氛围描述",
  "atmosphere_confidence": 0.0-1.0,
  "strategy": "回复策略",
  "strategy_detail": "策略细节",
  "suggested_tone": "建议语气：轻松/吐槽/严肃/接梗/安慰",
  "hook_points": ["接梗建议1", "接梗建议2"]
}''')
    parts.append('''评分说明：
- participate_value：话题对 Bot 的参与价值（高=值得参与，低=没参与点）
- timing_score：此刻插话的时机合适度（高=好时机）
- risk_score：参与的风险（高=风险大，如冒犯/冷场/刷屏）
- 三者独立 0-1，should_reply 应综合三者：价值高+时机好+风险低 → 参与''')

    # 参与度倾向（从 config 注入）
    participation = _get_participation()
    p_meta = PARTICIPATION_LEVELS.get(participation, PARTICIPATION_LEVELS[PARTICIPATION_DEFAULT])
    parts.append(f"\n当前参与度档位：{p_meta['label']}")
    parts.append(p_meta["prompt"])

    parts.append("\n注意：")
    parts.append("- 如果 bot 刚插话且对话在用户间继续，应该冷却")
    parts.append("- 如果话题与 bot 记忆/兴趣相关，应该提高参与度")
    parts.append("- 回复要像真人：简短、口语化、有情绪")
    parts.append("- 不要编造 thinking，只基于给定的上下文分析")

    return "\n".join(parts)


# ═══════════════════════════════════════════
# 主决策流程
# ═══════════════════════════════════════════

def decide(message: str, group_id: str, context: dict = None) -> Optional[DecisionV3]:
    """v3 主决策流程"""
    if not message or not message.strip():
        return None

    ctx = context or {}
    recent_msgs = ctx.get("recent_msgs", [])
    bot_replies = ctx.get("bot_replies", [])
    bot_qq = ctx.get("bot_qq", "")
    bot_name = ctx.get("bot_name", "小a")
    history = ctx.get("history", [])

    # 参与度档位
    participation = _get_participation()
    is_active = participation == "active"
    is_conservative = participation == "conservative"

    # 决策参数（v3 真正消费）
    dparams = _get_decision_params()
    cooldown = int(dparams["cooldown_seconds"])
    max_bot_replies = int(dparams["max_bot_replies"])

    # ═══ 冷却检查：Bot 刚回复过该群 ═══
    if cooldown > 0 and not ctx.get("is_at_bot"):
        last_reply = _last_reply_time.get(group_id, 0)
        if last_reply and (time.time() - last_reply) < cooldown:
            return DecisionV3(
                should_reply=False,
                reason=f"[冷却] Bot {int(time.time()-last_reply)}秒前刚回复过，冷却中",
                atmosphere="冷却中",
                atmosphere_confidence=0.95,
                strategy="冷却",
                strategy_detail=f"回复后 {cooldown} 秒内不主动插话（被 @ 除外）",
                topic="闲聊",
                topic_continuation=False,
            )

    # ═══ 连续回复限制：窗口内 Bot 回复次数超限 ═══
    if max_bot_replies > 0 and not ctx.get("is_at_bot"):
        recent_bot_replies = 0
        for msg in recent_msgs[-6:]:
            if isinstance(msg, (list, tuple)) and len(msg) > 1 and str(msg[1]) == bot_qq:
                recent_bot_replies += 1
        if recent_bot_replies >= max_bot_replies:
            return DecisionV3(
                should_reply=False,
                reason=f"[连续回复] Bot 最近已回复 {recent_bot_replies} 条，达到上限",
                atmosphere="克制",
                atmosphere_confidence=0.9,
                strategy="冷却",
                strategy_detail=f"最近窗口内 Bot 回复达 {max_bot_replies} 条上限，暂停避免刷屏",
                topic="闲聊",
                topic_continuation=False,
            )

    # 提取发送者
    sender_id = ""
    if recent_msgs:
        last_msg = recent_msgs[-1]
        if isinstance(last_msg, (list, tuple)) and len(last_msg) > 1:
            sender_id = str(last_msg[1])

    # 初始化组件
    db = _get_db()
    topic_tracker = _get_topic_tracker()
    profiler = _get_profiler()
    memory_matcher = _get_memory_matcher()
    emotion_sense = _get_emotion_sense()
    chain_detector = _get_chain_detector()

    # ═══ 话题检测 ═══
    topic = topic_tracker.detect_topic(message)
    topic_continuation = topic_tracker.is_topic_continuation(group_id, topic)

    # ═══ 情绪感知 ═══
    ctx_limit = int(_get_decision_params()["max_context_messages"])
    recent_texts = [m[2] for m in recent_msgs[-ctx_limit:] if isinstance(m, (list, tuple)) and len(m) > 2]
    emotion, energy = emotion_sense.detect_emotion(message, recent_texts)
    emotion_sense.log_emotion(group_id, emotion, energy)

    # ═══ 对话链检测 ═══
    chain_type = chain_detector.detect_chain_type(group_id, recent_msgs, bot_qq)
    participants = list(set(str(m[1]) for m in recent_msgs[-6:] if isinstance(m, (list, tuple)) and len(m) > 1))
    chain_detector.update_chain(group_id, chain_type, participants)

    # ═══ 记忆匹配 ═══
    memory_items = []
    memory_match = False
    if topic != "闲聊":
        memory_items = memory_matcher.match_topic(topic, group_id, limit=3)
        memory_match = len(memory_items) > 0

    # ═══ 更新群画像 ═══
    profiler.update_hours(group_id)

    # ═══ 硬规则层 ═══
    if _is_trivial(message):
        return DecisionV3(
            should_reply=False,
            reason="[硬规则] 纯无意义内容",
            atmosphere="安静",
            atmosphere_confidence=0.95,
            strategy="不回复",
            strategy_detail="无意义内容无需回复",
            topic=topic,
            topic_continuation=topic_continuation,
            emotion=emotion,
            energy=energy,
            memory_match=memory_match,
            memory_items=memory_items,
            conversation_chain=chain_type,
        )

    if _has_bot_interest(message) and not (is_conservative and not _has_likely_reply_signal(message)):
        return DecisionV3(
            should_reply=True,
            reason="[硬规则] 消息涉及Bot感兴趣的话题",
            atmosphere="积极",
            atmosphere_confidence=0.9,
            strategy="积极参与",
            strategy_detail="话题与Bot兴趣相关，自然参与讨论",
            topic=topic,
            topic_continuation=topic_continuation,
            emotion=emotion,
            energy=energy,
            memory_match=memory_match,
            memory_items=memory_items,
            conversation_chain=chain_type,
            suggested_tone="轻松" if emotion == "positive" else "安慰" if emotion == "negative" else "正常",
        )

    if _has_likely_reply_signal(message):
        return DecisionV3(
            should_reply=True,
            reason="[硬规则] 消息含问句或指向性信号",
            atmosphere="询问",
            atmosphere_confidence=0.85,
            strategy="回应",
            strategy_detail="有问句/指向信号，大概率需要回应",
            topic=topic,
            topic_continuation=topic_continuation,
            emotion=emotion,
            energy=energy,
            memory_match=memory_match,
            memory_items=memory_items,
            conversation_chain=chain_type,
        )

    # ═══ 规则预判层 ═══
    # Bot 刚插话后冷却
    if chain_type == "bot_continuation" and not (is_active and topic_continuation and memory_match):
        return DecisionV3(
            should_reply=False,
            reason="[对话链] Bot刚插话，避免连续发言",
            atmosphere="连贯",
            atmosphere_confidence=0.8,
            strategy="冷却",
            strategy_detail="Bot刚参与过对话，暂停回复以模拟自然节奏",
            topic=topic,
            topic_continuation=topic_continuation,
            emotion=emotion,
            energy=energy,
            memory_match=memory_match,
            memory_items=memory_items,
            conversation_chain=chain_type,
        )

    # 2+ 用户在互聊且 Bot 没被 @ → 跳过
    if chain_type == "user_conversation" and not ctx.get("is_at_bot") and not (is_active and (topic_continuation or memory_match)):
        return DecisionV3(
            should_reply=False,
            reason="[对话链] 用户在互聊，Bot不插嘴",
            atmosphere="用户对话中",
            atmosphere_confidence=0.85,
            strategy="不回复",
            strategy_detail="其他用户正在对话，Bot不插嘴",
            topic=topic,
            topic_continuation=topic_continuation,
            emotion=emotion,
            energy=energy,
            memory_match=memory_match,
            memory_items=memory_items,
            conversation_chain=chain_type,
        )

    # 话题延续 + 有记忆 → 提高参与度
    if topic_continuation and memory_match:
        return DecisionV3(
            should_reply=True,
            reason="[话题+记忆] 话题延续且Bot有相关经历",
            atmosphere="共鸣",
            atmosphere_confidence=0.8,
            strategy="接话",
            strategy_detail="话题延续，且Bot有相关经历，适合接话",
            topic=topic,
            topic_continuation=topic_continuation,
            emotion=emotion,
            energy=energy,
            memory_match=memory_match,
            memory_items=memory_items,
            conversation_chain=chain_type,
            suggested_tone="轻松" if emotion == "positive" else "安慰" if emotion == "negative" else "正常",
            hook_points=[m.get("content", "") for m in memory_items[:2]],
        )

    # ═══ AI 读空气层 ═══
    client = _get_ai_client()
    if client is None:
        return DecisionV3(
            should_reply=True,
            reason="AI客户端不可用，默认放行",
            atmosphere="未知",
            atmosphere_confidence=0.0,
            strategy="待定",
            strategy_detail="AI客户端不可用，由主模型自行判断",
            topic=topic,
            topic_continuation=topic_continuation,
            emotion=emotion,
            energy=energy,
            memory_match=memory_match,
            memory_items=memory_items,
            conversation_chain=chain_type,
        )

    try:
        decision_context = {
            "topic": topic,
            "topic_continuation": topic_continuation,
            "emotion": emotion,
            "energy": energy,
            "memory_match": memory_match,
            "memory_items": memory_items,
            "conversation_chain": chain_type,
        }

        # ═══ 增强：转发消息 + 图片理解 ═══
        _extra = _extract_forward_and_images(message, ctx, decision_context)
        if _extra:
            decision_context.update(_extra)

        prompt = _build_ai_prompt(message, ctx, decision_context)
        messages = [{"role": "user", "content": prompt}]

        # 读取配置
        try:
            from .config import MAX_TOKENS, DECISION_TIMEOUT
            max_tokens = MAX_TOKENS
            timeout = DECISION_TIMEOUT
        except ImportError:
            max_tokens = 300
            timeout = 10

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.call, messages, max_tokens=max_tokens)
            try:
                result = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                return DecisionV3(
                    should_reply=True,
                    reason="[AI超时] 默认放行",
                    atmosphere="未知",
                    atmosphere_confidence=0.0,
                    strategy="待定",
                    strategy_detail="AI调用超时，由主模型自行判断",
                    topic=topic,
                    topic_continuation=topic_continuation,
                    emotion=emotion,
                    energy=energy,
                    memory_match=memory_match,
                    memory_items=memory_items,
                    conversation_chain=chain_type,
                )

        if isinstance(result, tuple):
            reply = result[0] if len(result) > 0 else None
        else:
            reply = result

        if not reply:
            return DecisionV3(
                should_reply=True,
                reason="[AI返回空] 默认放行",
                atmosphere="未知",
                atmosphere_confidence=0.0,
                strategy="待定",
                strategy_detail="AI返回为空，由主模型自行判断",
                topic=topic,
                topic_continuation=topic_continuation,
                emotion=emotion,
                energy=energy,
                memory_match=memory_match,
                memory_items=memory_items,
                conversation_chain=chain_type,
            )

        # 解析 AI 输出
        data = _parse_json_output(reply)
        if data is None:
            return DecisionV3(
                should_reply=True,
                reason="[AI格式异常] 默认放行",
                atmosphere="未知",
                atmosphere_confidence=0.0,
                strategy="待定",
                strategy_detail="AI返回格式异常，由主模型自行判断",
                topic=topic,
                topic_continuation=topic_continuation,
                emotion=emotion,
                energy=energy,
                memory_match=memory_match,
                memory_items=memory_items,
                conversation_chain=chain_type,
            )

        should_reply = bool(data.get("should_reply", True))
        reason = str(data.get("reason", ""))[:200]
        atmosphere = str(data.get("atmosphere", "闲聊"))
        atmosphere_confidence = float(data.get("atmosphere_confidence", 0.0))
        strategy = str(data.get("strategy", "轻松闲聊"))
        strategy_detail = str(data.get("strategy_detail", ""))[:200]
        suggested_tone = str(data.get("suggested_tone", "normal"))
        hook_points = data.get("hook_points", [])
        if not isinstance(hook_points, list):
            hook_points = [str(hook_points)]
        thinking = str(data.get("thinking", ""))[:200]

        # ═══ v4 链式思考加权决策 ═══
        # AI 提供多维评分时：综合评分 + 参与度偏移决定
        has_scores = any(k in data for k in ("participate_value", "timing_score", "risk_score"))
        if has_scores:
            scored, score_suffix, scores = _score_to_decision(data, participation)
            # 低置信度时仍默认放行（保留原保护逻辑）
            conf_threshold = float(_get_decision_params()["confidence_threshold"])
            if atmosphere_confidence < conf_threshold and not scored:
                scored = True
                score_suffix = f"[低置信度({atmosphere_confidence:.2f})放行] {score_suffix}"
            should_reply = scored
            if thinking:
                reason = f"{score_suffix} {reason} | 思考:{thinking}"
            else:
                reason = f"{score_suffix} {reason}"
        else:
            # 旧输出降级：低置信度时默认放行
            conf_threshold = float(_get_decision_params()["confidence_threshold"])
            if atmosphere_confidence < conf_threshold:
                should_reply = True
                reason = f"[低置信度({atmosphere_confidence:.2f})放行] {reason}"

        return DecisionV3(
            should_reply=should_reply,
            reason=reason,
            atmosphere=atmosphere,
            atmosphere_confidence=atmosphere_confidence,
            strategy=strategy,
            strategy_detail=strategy_detail,
            topic=topic,
            topic_continuation=topic_continuation,
            emotion=emotion,
            energy=energy,
            memory_match=memory_match,
            memory_items=memory_items,
            conversation_chain=chain_type,
            suggested_tone=suggested_tone,
            hook_points=hook_points[:3],
        )

    except Exception as e:
        print(f"[GroupDecider] 决策异常: {e}")
        return DecisionV3(
            should_reply=True,
            reason=f"决策异常: {e}",
            atmosphere="未知",
            atmosphere_confidence=0.0,
            strategy="待定",
            strategy_detail="AI调用异常，默认放行",
            topic=topic,
            topic_continuation=topic_continuation,
            emotion=emotion,
            energy=energy,
            memory_match=memory_match,
            memory_items=memory_items,
            conversation_chain=chain_type,
        )


def _extract_forward_and_images(message: str, ctx: dict, decision_context: dict) -> dict:
    """增强：识别转发聊天记录 + 图片，注入决策上下文。

    返回 dict（可能含 forward_text / image_desc / has_forward / has_image），
    或 None（无需增强）。
    """
    from . import free_models, providers
    cfg = free_models.load_plugin_config()
    forward_enabled = cfg.get("forward_analysis", True)
    vision_enabled = cfg.get("vision_enabled", True)
    extra = {}

    # ── 转发聊天记录 ──
    if forward_enabled and message and ("[转发]" in message or "[合并转发]" in message or "[转发消息]" in message):
        fwd_text = _collect_forward_text(message, ctx)
        if fwd_text:
            # 用免费文本模型提炼转发要点（失败则原样截断）
            summary = fwd_text
            try:
                if providers is not None and free_models.is_configured():
                    _p = ("你是群聊分析助手。下面是某人转发的聊天记录片段，请用 2-3 句话概括："
                          "1) 这段记录在聊什么 2) 有没有值得 Bot 接话的点 3) 转发者可能是想让群友看什么。"
                          "直接输出概括，不要客套。\n\n转发记录：\n" + fwd_text[:1500])
                    _r = providers.call_text(_p, max_tokens=200)
                    if _r:
                        summary = _r.strip()
            except Exception:
                pass
            extra["forward_text"] = summary[:500]
            extra["has_forward"] = True

    # ── 图片理解 ──
    if vision_enabled and message and "[图片]" in message:
        img_path = _find_recent_image(ctx)
        if img_path:
            try:
                if free_models.is_configured():
                    _v = providers.call_vision(
                        img_path,
                        "你是群聊助手。请简短描述这张图片的内容（30字以内），"
                        "并判断：这张图是不是在表达一个话题/梗，Bot 有没有接话的空间。",
                    )
                    if _v.get("ok") and _v.get("result", {}).get("summary"):
                        extra["image_desc"] = _v["result"]["summary"][:300]
                        extra["has_image"] = True
            except Exception:
                pass

    return extra or None


def _collect_forward_text(message: str, ctx: dict) -> str:
    """收集转发内容：优先从当前消息提取，其次扫描 recent_msgs"""
    lines = []
    for seg in message.split("\n"):
        seg = seg.strip()
        if seg and ("[转发]" in seg or "[合并转发]" in seg or "[转发消息]" in seg):
            # 去掉标记前缀，保留内容
            lines.append(seg)
    if not lines:
        for m in (ctx.get("recent_msgs") or [])[-5:]:
            if isinstance(m, (list, tuple)) and len(m) > 2:
                tx = str(m[2])
                if "[转发]" in tx or "[合并转发]" in tx or "[转发消息]" in tx:
                    lines.append(tx)
    return "\n".join(lines)[:2000]


def _find_recent_image(ctx: dict) -> str:
    """从 recent_msgs 条目第5个元素取图片路径（bot_core append 时提取）"""
    # 直接传入的 images 优先
    images = ctx.get("images") or ctx.get("image_paths") or []
    if images and isinstance(images, list):
        for p in images:
            if isinstance(p, str) and p:
                return p
    # recent_msgs 条目 (time, sender, text, msg_id, img_paths)
    for m in (ctx.get("recent_msgs") or [])[-5:]:
        if isinstance(m, (list, tuple)) and len(m) >= 5:
            paths = m[4]
            if isinstance(paths, list):
                for p in paths:
                    if isinstance(p, str) and p:
                        return p
            elif isinstance(paths, str) and paths:
                return paths
    return ""


def _score_to_decision(data: dict, participation: str) -> tuple:
    """基于 AI 多维评分计算最终 should_reply 与 reason 后缀。

    综合分 = 参与价值 * 时机 - 风险 * 风险权重(0.6)
    再按参与度档位加偏移。返回 (should_reply, reason_suffix, scores)
    """
    try:
        value = max(0.0, min(1.0, float(data.get("participate_value", 0.5))))
    except (TypeError, ValueError):
        value = 0.5
    try:
        timing = max(0.0, min(1.0, float(data.get("timing_score", 0.5))))
    except (TypeError, ValueError):
        timing = 0.5
    try:
        risk = max(0.0, min(1.0, float(data.get("risk_score", 0.2))))
    except (TypeError, ValueError):
        risk = 0.2

    score = value * timing - risk * 0.6

    # 参与度偏移
    if participation == "active":
        score += 0.15
    elif participation == "conservative":
        score -= 0.15

    # 临界区由 AI 明确表态主导
    ai_want = bool(data.get("should_reply", True))
    if 0.25 <= score <= 0.45:
        should = ai_want
        reason_suffix = f"[临界区由AI表态:{'参与' if ai_want else '不参与'}]"
    else:
        should = score >= 0.35
        reason_suffix = f"[评分 价值{value:.2f}×时机{timing:.2f}-风险{risk:.2f}={score:.2f}]"

    return should, reason_suffix, {"participate_value": value, "timing_score": timing, "risk_score": risk}


def _parse_json_output(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```json" in text:
        try:
            start = text.index("```json") + 7
            end = text.index("```", start)
            return json.loads(text[start:end].strip())
        except (ValueError, json.JSONDecodeError):
            pass
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        pass
    return None


# ═══════════════════════════════════════════
# 钩子函数
# ═══════════════════════════════════════════

def before_reply(message: str = "", group_id: str = "", context: dict = None, **kwargs) -> dict | None:
    """before_reply 挂载点"""
    if not PLUGIN_META["enabled"]:
        return None
    if not message or not message.strip():
        return None

    ctx = context or {}
    recent_msgs = ctx.get("recent_msgs", [])
    bot_qq = ctx.get("bot_qq", "")

    # 提取发送者
    sender_id = ""
    if recent_msgs:
        last_msg = recent_msgs[-1]
        if isinstance(last_msg, (list, tuple)) and len(last_msg) > 1:
            sender_id = str(last_msg[1])

    # 被 @ 或叫了小a → 放行
    if ctx.get("is_at_bot") or ctx.get("has_xiao_a") or ctx.get("has_text_at"):
        return None

    # 执行决策
    decision = decide(message, group_id, ctx)
    if decision is None:
        return None

    if not decision.should_reply:
        return {
            "action": "skip",
            "reason": f"[Decider v3] {decision.reason} (氛围:{decision.atmosphere})",
        }

    # 记录 Bot 回复时间（用于冷却）
    _last_reply_time[group_id] = time.time()

    return {
        "action": "inject",
        "content": decision.to_prompt_injection(),
        "meta": decision.to_dict(),
    }


def after_reply(message: str = "", group_id: str = "", reply: str = "", context: dict = None, **kwargs) -> dict | None:
    """after_reply 挂载点：更新群画像和对话链"""
    if not PLUGIN_META["enabled"]:
        return None

    ctx = context or {}
    recent_msgs = ctx.get("recent_msgs", [])
    bot_qq = ctx.get("bot_qq", "")

    # 提取发送者
    sender_id = ""
    if recent_msgs:
        last_msg = recent_msgs[-1]
        if isinstance(last_msg, (list, tuple)) and len(last_msg) > 1:
            sender_id = str(last_msg[1])

    if sender_id and group_id:
        try:
            from .decider import record_reply, record_message
            record_reply(group_id, sender_id)
        except Exception:
            pass

    # 更新话题流
    if message and group_id:
        try:
            topic_tracker = _get_topic_tracker()
            topic = topic_tracker.detect_topic(message)
            topic_tracker.db.add_topic_message(group_id, topic, message, sender_id or "bot")
        except Exception:
            pass

    return None


def on_load():
    from . import free_models
    cfg = free_models.load_plugin_config()
    PLUGIN_META["enabled"] = bool(cfg.get("enabled", True))
    print(f"[GroupDecider] v{PLUGIN_META['version']} loaded, enabled={PLUGIN_META['enabled']}")


def on_unload():
    print("[GroupDecider] unloaded")


# ═══════════════════════════════════════════
# 标准插件通道：AI 配置 / 启停 / 统计
# ═══════════════════════════════════════════

def _ai_config_status() -> dict:
    """汇总 AI 配置与各预设状态（供 UI 展示）"""
    from . import free_models
    cfg = free_models.load_plugin_config()
    active = cfg.get("active_model", free_models.DEFAULT_MODEL)
    return {
        "active_model": active if active in free_models.FREE_MODELS else free_models.DEFAULT_MODEL,
        "vision_enabled": bool(cfg.get("vision_enabled", True)),
        "forward_analysis": bool(cfg.get("forward_analysis", True)),
        "vision_max_images": int(cfg.get("vision_max_images", 3)),
        "participation": _get_participation(),
        "participation_levels": {k: v["label"] for k, v in PARTICIPATION_LEVELS.items()},
        "decision_params": _get_decision_params(),
        "decision_param_defaults": dict(DECISION_PARAMS_DEFAULTS),
        "models": free_models.list_models(),
        "default_model": free_models.DEFAULT_MODEL,
    }


def _get_config(params: dict = None) -> dict:
    from . import free_models
    enabled = bool(PLUGIN_META.get("enabled", True))
    return {
        "enabled": enabled,
        "config": _ai_config_status(),
    }


def _set_ai_config(params: dict = None) -> dict:
    from . import free_models
    params = params or {}
    updates = {}
    m = (params.get("active_model") or "").strip()
    if m and m in free_models.FREE_MODELS:
        updates["active_model"] = m
    if "vision_enabled" in params:
        updates["vision_enabled"] = bool(params["vision_enabled"])
    if "forward_analysis" in params:
        updates["forward_analysis"] = bool(params["forward_analysis"])
    if "vision_max_images" in params:
        try:
            v = int(params["vision_max_images"])
            updates["vision_max_images"] = max(1, min(v, 10))
        except Exception:
            pass
    if "participation" in params:
        p = str(params["participation"]).strip()
        if p in PARTICIPATION_LEVELS:
            updates["participation"] = p
    # 合并决策参数（confidence_threshold 等）
    for k, v in params.items():
        if k in DECISION_PARAMS_DEFAULTS:
            default = DECISION_PARAMS_DEFAULTS[k]
            try:
                if isinstance(default, int):
                    updates[k] = max(1, int(v)) if k != "confidence_threshold" else int(v)
                else:
                    updates[k] = max(0.0, min(1.0, float(v)))
            except Exception:
                pass
    free_models.save_plugin_config(updates)
    return {"ok": True, "config": _ai_config_status()}


def _set_provider_key(params: dict = None) -> dict:
    from . import free_models
    params = params or {}
    pid = (params.get("provider") or "").strip()
    key = (params.get("api_key") or "").strip()
    if pid not in free_models.FREE_MODELS:
        return {"ok": False, "error": f"未知预设: {pid}"}
    ok = free_models.save_secret_key(pid, key)
    return {"ok": ok, "configured": free_models.is_configured(pid), "config": _ai_config_status()}


def _set_config(params: dict = None) -> dict:
    """写决策参数（confidence_threshold 等）+ 参与度，持久化到 config.json"""
    from . import free_models
    params = params or {}
    updates = {}
    valid = set(DECISION_PARAMS_DEFAULTS.keys())
    for k, v in params.items():
        if k not in valid:
            continue
        default = DECISION_PARAMS_DEFAULTS[k]
        try:
            if isinstance(default, int):
                nv = int(v)
                updates[k] = max(1, nv) if k != "confidence_threshold" else nv
            else:
                nv = float(v)
                updates[k] = max(0.0, min(1.0, nv))
        except Exception:
            continue
    if "participation" in params:
        p = str(params["participation"]).strip()
        if p in PARTICIPATION_LEVELS:
            updates["participation"] = p
    free_models.save_plugin_config(updates)
    return {"ok": True, "config": _ai_config_status()}


def _toggle(params: dict = None) -> dict:
    PLUGIN_META["enabled"] = not bool(PLUGIN_META.get("enabled", True))
    return {"enabled": bool(PLUGIN_META["enabled"])}


def _stats(params: dict = None) -> dict:
    try:
        db = _get_db()
        conn = sqlite3.connect(db.db_path)
        rows = {}
        for table in ("group_profiles", "topic_flows", "emotion_logs", "conversation_chains"):
            try:
                rows[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception:
                rows[table] = 0
        conn.close()
        return {"ok": True, "stats": rows}
    except Exception as e:
        return {"ok": False, "error": str(e), "stats": {}}


PLUGIN_ACTIONS = {
    "get_config": _get_config,
    "set_config": _set_config,
    "set_ai_config": _set_ai_config,
    "set_provider_key": _set_provider_key,
    "toggle": _toggle,
    "stats": _stats,
}


__all__ = [
    "PLUGIN_META",
    "PLUGIN_ACTIONS",
    "before_reply",
    "after_reply",
    "decide",
    "DecisionV3",
    "set_ai_client",
    "on_load",
    "on_unload",
]
