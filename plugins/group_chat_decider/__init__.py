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
        """匹配 bot 记忆中的相关经历。

        2.0 阶段C：改走 harness.memory_context builder —— 旧实现有两层病：
        ①"命中即注 3 条、无相关性阈值"（1.7-T7 评估旧账：话题词命中≠相关，
          低相关旧账被莫名 cue）；
        ②旧直连调用签名本身就是错的 —— recall_key_memories(scope, scope_id,
          current_msg, limit)，这里传成了 (group_id, topic, top_k=...)，
          TypeError 被 except 吞成恒 []，memory_match 实际**从来没亮过**。
        builder 的 τ 阈值闸 + 跨源去重堵①；正常返回（含过阈零条=有效结论"不相关"）
        即为终判，仅 builder 抛异常才退回直连（用修正后的签名）。"""
        try:
            from harness import memory_context as _mc
            items = _mc.collect(topic, scope="group", scope_id=str(group_id),
                                sources=("sm",))
            return [{"content": t, "score": s} for s, t in items]
        except Exception:
            pass
        memory = self._get_memory_plugin()
        if memory is None:
            return []
        try:
            facts = memory.recall_key_memories(scope="group", scope_id=str(group_id),
                                               current_msg=topic, limit=limit)
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

    def detect_chain_type(self, group_id: str, recent_msgs: List, bot_qq: str,
                          bot_recent_n: int = 0, bot_last_ts: float = 0.0) -> str:
        """检测对话链类型（v2 · 2026-10-11：活跃段切分 + 交替判定）

        v1 的问题（22:36 实证误判后复盘）：
        ① 只数"窗口里有几个不同人"，没有时间语义 —— 40 秒前路过插一嘴的人
           会把"Bot 与 U1 的活跃对话"污染成"互聊"；
        ② 没有方向语义 —— 同一人连续追问（正是在跟 Bot 说话）和 A/B 交替对聊
           （真互聊）被同等对待；
        ③ Bot 在场性靠 bot_recent_n 条数补丁，是"说过话"不是"在台上"。

        v2 判据：
        · **活跃段**：从最新往回，相邻消息间隔 > SEG_GAP 即切断 —— 只有段内
          消息参与判定（段外的历史路人自动出局）；
        · **Bot 在台上**（bot_last_ts 距今 ≤ BOT_STAGE）：且有人在其后接话 →
          bot_mentioned（这是 Bot 的对话链，别拦）；Bot 连说≥2条无人接 →
          bot_continuation（冷却语义）；
        · **user_conversation 需要交替证据**：段内用户序列最后两条来自**不同人**
          （A→B 互相回应）才判互聊；同一人连发是 single_user，不是互聊。
        """
        if not recent_msgs:
            return "isolated"

        # ── 活跃段切分 ──
        SEG_GAP = 45.0      # 段内最大消息间隔（秒）
        BOT_STAGE = 90.0    # Bot 发言后视为"仍在台上"的时长（秒）
        seg = []            # [(ts, sender), ...] 时间正序
        prev_ts = None
        for msg in reversed(recent_msgs[-10:]):
            if not isinstance(msg, (list, tuple)) or len(msg) < 2:
                continue
            try:
                ts = float(msg[0])
            except (TypeError, ValueError):
                ts = 0.0
            if prev_ts is not None and ts > 0 and prev_ts - ts > SEG_GAP:
                break
            seg.append((ts, str(msg[1])))
            prev_ts = ts
        seg.reverse()
        if not seg:
            return "isolated"

        import time as _t
        _now = _t.time()
        _bot = str(bot_qq)

        # ── Bot 在台上？ ──
        if bot_last_ts and (_now - float(bot_last_ts)) <= BOT_STAGE:
            # Bot 发言之后有用户接话 → 当前是 Bot 参与的对话链
            if any(ts >= float(bot_last_ts) - 1.0 for ts, s in seg if s != _bot):
                return "bot_mentioned"
            if int(bot_recent_n) >= 2:
                return "bot_continuation"
            return "bot_mentioned"

        # ── Bot 不在台：用户侧结构判定 ──
        user_seq = [s for _, s in seg if s != _bot]
        if not user_seq:
            return "bot_continuation" if bot_recent_n else "isolated"
        if len(user_seq) >= 2 and user_seq[-1] != user_seq[-2]:
            return "user_conversation"   # 最后两条 A→B 交替 = 真互聊
        if len(set(user_seq)) == 1:
            return "single_user"          # 同一人连发（可能正要对 Bot 说话）
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
    observe_after: int = 0       # >0 = 别急着结束，N 秒后再看一眼（潜水前观察）

    def __post_init__(self):
        if self.memory_items is None:
            self.memory_items = []
        if self.hook_points is None:
            self.hook_points = []

    def to_dict(self) -> dict:
        return asdict(self)

    def to_prompt_injection(self) -> str:
        """给主模型的现场参考（2026-10-25 收窄）。

        旧版是一份「氛围 + 回复策略 + 置信度 + 对话链 + 建议语气 + 接梗建议」的
        规格书，模型照单执行，回复变成「按条目交付」的汇报腔。现在只留现场
        事实，并明确标注**不是指令**；置信度/能量/对话链这些仪表读数不进 prompt
        —— 模型看到数字会跟着用分析口吻说话。
        """
        parts = ["【现场判断（系统参考，不是对方说的话；别复述这些词）】"]
        parts.append(f"- 氛围：{self.atmosphere}")
        if self.strategy:
            _how = f"- 怎么接：{self.strategy}"
            if self.strategy_detail:
                _how += f"（{self.strategy_detail}）"
            parts.append(_how)
        if self.topic and self.topic != "闲聊":
            parts.append(f"- 当前话题：{self.topic}"
                         f"（{'延续' if self.topic_continuation else '新话题'}）")
        if self.emotion != "neutral":
            parts.append(f"- 大家情绪：{self.emotion}")
        if self.memory_match and self.memory_items:
            mem_summaries = [m.get("content", "") for m in self.memory_items[:3]]
            parts.append(f"- 相关记忆：{'；'.join(mem_summaries)}")
        if self.suggested_tone != "normal":
            parts.append(f"- 语气：{self.suggested_tone}")
        if self.hook_points:
            parts.append(f"- 可以接的点：{', '.join(self.hook_points)}")
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
_last_reply_target = {}   # {group_id: (被回复者QQ, 时间)} —— 接话豁免用
_bot_spoke_times = {}     # {group_id: [真实发出时刻…]} —— 防刷屏闸用
_pending_reply = {}       # {group_id: 决策放行时刻} —— 回复生成期间的预占冷却
_followup_until = {}      # {group_id: 接话窗口截止} —— 回完等追问（学 qq-bridge 的 purpose=reply/messages）
_decision_cache = {}      # {(group, text, tail_ts): (ts, result)} 同消息二次派发复用


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
    """决策 AI：优先走**免费车道**（free_llm 直连 zen/kilo，免 key），
    车道不可用时降级回主模型（保持向后兼容）。"""
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
    """适配免费车道档位的轻量客户端，提供 AIClient 兼容的 .call()

    2026-10-12 起：真正的上行是 free_llm（直连 zen/kilo 免密车道），
    这里只做"prompt 抽取 + 转成 call_text"这一层适配。
    """

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
    # 2026-10-13 用户定调：「决策不需要考虑任何冷却」—— 群里另一个 AI 聊得流畅，
    # 而我们的 90s 冷却把 71 秒前的回复当成"不该再说"的依据，整轮被跳过。
    # 节奏交给 AI 自己判断（prompt 里已有"刚插话且对话在用户间继续应该冷却"），
    # 不用定时器替它拍板。防刷屏只留一个极端阀。
    "max_bot_replies": 5,           # 极端防刷屏阀：窗口内发言达上限才停（默认放宽）
    "cooldown_seconds": 0,          # 0 = 决策不考虑冷却
    # 2026-10-13（学 qq-bridge 的"等、回与沉睡前观察"）：
    #   followup_wait_seconds   回完之后的**接话窗口**：对方追问就还能再答一次
    #   followup_max_chars      短消息阈值（短 = 多半是追问）
    #   observe_before_silence_seconds 决定潜��前的**观察窗口**（0=不观察）；
    #                          窗口内话题转向我们时会重新决策一次
    "followup_wait_seconds": 45,
    "followup_max_chars": 40,
    "observe_before_silence_seconds": 25,
    "followup_window_seconds": 300, # 接话豁免窗口：Bot 上一句的对话者继续发问，冷却期内放行
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
                           "明日方舟", "方舟",   # 2026-10-11：群内高频话题，词表竟缺
                           "bug", "bug了", "报错", "报错了吗", "跑不起来"}

_SKIP_TRIVIAL = {"1", "2", "3", "6", "嗯", "哦", "哈哈", "hhh", "好的",
                  "行", "ok", "OK", "666", "……", "额", "额。", "噗", "?", "？", " "}

_LIKELY_REPLY_KEYWORDS = {"小a", "？", "?", "吗", "呢", "吧", "嘛",
                           "怎么", "什么", "为啥", "为什么", "是不是",
                           "有没有", "能不能", "会不会", "要不要", "求助", "帮忙", "看看", "快看"}


def _has_bot_interest(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _BOT_INTEREST_KEYWORDS)


def _has_likely_reply_signal(text: str, ctx: dict = None) -> bool:
    """明显该回复的信号（问句/语气词/**配置里的 Bot 称呼**）。"""
    t = (text or "").lower()
    kws = list(_LIKELY_REPLY_KEYWORDS)
    for n in _mention_names(ctx):
        nl = str(n).lower()
        if nl and nl not in kws:
            kws.append(nl)
    return any(kw in t for kw in kws)


# ── 2026-10-10 决策重写（信号两级拆分）──
# 群友互聊带问句 ≠ 对 Bot 说话。旧版"含？/吗/呢就硬放行"导致 Bot 频繁插嘴
# 别人的对话（不合时宜）。现在拆成：指向 Bot → 硬放行；普通问句 → 交给 AI 读空气。
_QUESTION_WORDS = {"？", "?", "吗", "呢", "怎么", "什么", "为啥", "为什么",
                   "是不是", "有没有", "能不能", "会不会", "多少", "哪个", "谁", "啥"}
# Bot 称呼：**不写死**。优先用调用方（bot_core）通过 ctx 注入的 bot_nicknames /
# bot_name；两者都没有时才用下面这组兜底（保持旧行为不回归）。
_BOT_MENTIONS = {"小a", "小A", "@小a", "@小A"}


def _norm_mention(s) -> str:
    """称呼归一化：去 @（半/全角）与空格、转小写 —— 群友大小写随手。"""
    s = str(s or "").strip()
    try:
        import unicodedata
        s = unicodedata.normalize("NFKC", s)   # 全角ａ→a、＠→@
    except Exception:
        pass
    return s.lstrip("@").replace(" ", "").replace("\u3000", "").lower()


def _mention_names(ctx: dict = None) -> list:
    """本次决策可用的 Bot 称呼列表（ctx 优先，其次兜底表）。"""
    c = ctx or {}
    out = []
    for n in (c.get("bot_nicknames") or []):
        n = str(n).strip()
        if n and n not in out:
            out.append(n)
    bn = str(c.get("bot_name") or "").strip()
    if bn and bn not in out:
        out.append(bn)
    return out or list(_BOT_MENTIONS)
# 正反问通用式（2026-10-11 实证补）："你玩不玩明日方舟" 无问号无"吗"，
# 词表只收"是不是/有没有"等固定形式 → V不V 全漏判，AI 回空时兜底静默误杀。
_ABI_RE = re.compile(r'([\u4e00-\u9fa5])不\1')       # 玩不玩/好不好/去不去/对不对
_ABAB_RE = re.compile(r'([\u4e00-\u9fa5]{2})不\1')   # 知道不知道/清楚不清楚


def _bot_directed(text: str, ctx: dict = None) -> bool:
    """消息明确指向 Bot：文本提到 Bot 称呼 / 触发标志（由调用方注入）。

    2026-10-12：称呼不再写死 —— 从 ctx 读 bot_nicknames（bot_core 按配置注入），
    匹配大小写/全角/@ 不敏感（线上实证："小A，你会不会忘掉大家" 曾被判成未指向 Bot）。
    """
    c = ctx or {}
    if c.get("is_at_bot") or c.get("has_xiao_a") or c.get("has_bot_nick") or c.get("has_quote"):
        return True
    if not text:
        return False
    t = _norm_mention(text)
    if not t:
        return False
    return any(_norm_mention(m) and _norm_mention(m) in t for m in _mention_names(ctx))


def _has_question_signal(text: str) -> bool:
    t = text.lower()
    if any(kw in t for kw in _QUESTION_WORDS):
        return True
    # 正反问（V不V / AB不AB）："你玩不玩明日方舟" 没有任何疑问词表命中，
    # 但它是明确的疑问句 —— 2026-10-11 实证误杀场景。
    return bool(_ABI_RE.search(text) or _ABAB_RE.search(text))


def _is_trivial(text: str) -> bool:
    return text.strip().lower() in _SKIP_TRIVIAL


# ═══════════════════════════════════════════════════════════════════════
#  降级治理：决策 AI 失败时怎么办
#  ────────────────────────────────────────────────────────────────────
#  用户 2026-10-12 报："决策的一个问题是 AI 请求失败导致氛围未知，跳过回复"。
#  此前五种失败（客户端不可用/超时/返回空/格式异常/异常）统一走
#  `_ai_fallback_allow()`：规则侧判 skip 就**静默闭嘴**，氛围给"未知"——
#  于是"AI 偶发抽风"和"AI 判断这轮别说话"在用户眼里长得一模一样。
#
#  三个层次（既不静默、也不重演 2026-10-10 的 429 无差别插话）：
#    ① 重试：偶发失败先自愈（见 decide 里的 AI_RETRY_MAX）
#    ② 单次失败 → fail-open：交主模型自裁（注入里写明"不适合就输出 [不说话]"），
#       氛围**沿用上次成功决策**（带降级标记）而不是"未知"
#    ③ 连续失败达阈值 → 断路器打开：转保守规则（宁当哑巴不乱插嘴），并显式记录
# ═══════════════════════════════════════════════════════════════════════
_AI_FAIL_TIMES: list = []            # 最近的失败时刻（断路器统计）
_LAST_ATMOSPHERE: dict = {}          # group_id -> (atmosphere, confidence, ts)


def _deg_cfg(name, default):
    """从插件 config.py 读降级参数（缺失时给默认，便于老配置平滑升级）。"""
    try:
        from . import config as _c
        return getattr(_c, name, default)
    except Exception:
        return default


def _record_ai_failure():
    now = time.time()
    win = float(_deg_cfg("DEGRADE_FAIL_WINDOW", 300) or 300)
    _AI_FAIL_TIMES.append(now)
    while _AI_FAIL_TIMES and now - _AI_FAIL_TIMES[0] > win:
        _AI_FAIL_TIMES.pop(0)
    return len(_AI_FAIL_TIMES)


def _breaker_open() -> bool:
    """窗口内失败次数是否已达阈值（连续挂 → 别再赌运气）。"""
    now = time.time()
    win = float(_deg_cfg("DEGRADE_FAIL_WINDOW", 300) or 300)
    th = int(_deg_cfg("DEGRADE_FAIL_THRESHOLD", 3) or 3)
    recent = [t for t in _AI_FAIL_TIMES if now - t <= win]
    return len(recent) >= th


def _remember_atmosphere(group_id, atmosphere, confidence):
    """记住本群最近一次**成功**决策看到的氛围（失败时沿用，别退化成"未知"）。"""
    if not group_id or not atmosphere or atmosphere == "未知":
        return
    _LAST_ATMOSPHERE[str(group_id)] = (str(atmosphere), float(confidence or 0.0), time.time())


def _recall_atmosphere(group_id):
    """失败时取上次氛围。返回 (atmosphere, confidence, reused)。"""
    rec = _LAST_ATMOSPHERE.get(str(group_id or ""))
    ttl = float(_deg_cfg("ATMOSPHERE_TTL", 900) or 900)
    if rec and time.time() - rec[2] <= ttl:
        return f"{rec[0]}（沿用上次）", round(rec[1] * 0.5, 2), True
    return "未知", 0.0, False


def _degraded_decision(kind: str, group_id: str, base: dict, rule_allow: bool, detail: str,
                       rule_skip: bool = False, force_conservative: bool = False):
    """AI 不可用时的统一降级决策。

    kind     : 失败类型串（已含第几次尝试），进 reason 供排查
    rule_allow/rule_skip : 规则侧兜底倾向（仅保守模式生效）
    detail   : 给主模型的策略说明
    force_conservative : 客户端**根本没配**（配置态，不是偶发失败）→ 直接保守，
                         不做 fail-open（否则没配 key 的机器会变成"无差别插话"）
    """
    atm, conf, reused = _recall_atmosphere(group_id)
    nfail = len(_AI_FAIL_TIMES)
    if force_conservative or _breaker_open():
        # 保守：规则说别插嘴就别插嘴（宁当哑巴），并**显式**说明原因
        should = bool(rule_allow) and not rule_skip
        why_tail = "客户端缺失转保守" if force_conservative else f"连续失败{nfail}次转保守"
        reason = f"{kind}·{why_tail}" + ("放行" if should else "静默")
        strategy = "保守"
        detail2 = detail + ("（决策模型未配置，走规则侧兜底）" if force_conservative
                            else "（决策AI连续失败，已转保守规则兜底）")
        conf = 0.0
    else:
        # 单次失败 → 不静默：交给主模型自裁（主链支持 [不说话]）
        should = True
        reason = f"{kind}·交主模型自裁"
        strategy = "自裁"
        detail2 = (detail + "（决策AI本次不可用，改由你自己判断该不该接话；"
                   "不合适就输出 [不说话]，别硬接。）")
        if reused:
            detail2 += "（氛围为上次结果，仅供参考）"
    return DecisionV3(
        should_reply=should,
        reason=reason,
        atmosphere=atm,
        atmosphere_confidence=conf,
        strategy=strategy,
        strategy_detail=detail2[:200],
        suggested_tone="normal",
        **base,
    )


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

    # Bot 自己最近说过什么（2026-10-10 决策重写）：
    # 主循环把 Bot 消息过滤出 recent_msgs，旧版决策 AI 因此**看不见 Bot 刚说过的话**，
    # 无法判断"这句是不是在接我的话/我是不是刚插过嘴"——接不上话与乱插嘴的又一来源。
    bot_replies = context.get("bot_replies", []) or []
    if bot_replies:
        parts.append("\n你（Bot）最近的发言（新→旧）：")
        for _i, _r in enumerate(list(reversed(list(bot_replies)))[-3:]):
            parts.append(f"  - {str(_r)[:80]}")

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

    # 规则侧参考信号（2026-10-11 降级）：只是提示，不是命令，你可以不同意
    _hint = decision_context.get("rule_hint", "")
    _ihit = decision_context.get("interest_hit", False)
    if _hint or _ihit:
        _sig = []
        if _ihit:
            _sig.append("命中Bot兴趣词")
        if _hint:
            _sig.append(_hint)
        parts.append("规则侧参考信号（仅供你参考，你可以不同意）：" + "、".join(_sig))

    # 参与度倾向（从 config 注入）
    participation = _get_participation()
    p_meta = PARTICIPATION_LEVELS.get(participation, PARTICIPATION_LEVELS[PARTICIPATION_DEFAULT])
    parts.append(f"\n当前参与度档位：{p_meta['label']}")
    parts.append(p_meta["prompt"])

    parts.append("\n注意：")
    parts.append("- 如果 bot 刚插话且对话在用户间继续，应该冷却")
    # ★ 划走权（学 qq-bridge「群聊不是客服队列」）：别把缓冲里的历史当成待办清单。
    parts.append(
        "- **只有最后那条（[当前] 之后的内容）是对你说的**；上面的历史只是背景，"
        "可以不接、可以只接最后一条。不要逐条点评、不要总结群聊在聊什么、"
        "不要因为'看到了'就每条都回。")
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

    # 提取发送者（冷却豁免要用，必须在冷却检查之前）
    # 优先 ctx.sender（主链 buffer 的真实发送者）；recent_msgs[-1] 可能还是
    # 别人的上一条，直接用会把"追问豁免"记到错误的人头上。
    sender_id = str(ctx.get("sender") or ctx.get("user_id") or "")
    if not sender_id and recent_msgs:
        last_msg = recent_msgs[-1]
        if isinstance(last_msg, (list, tuple)) and len(last_msg) > 1:
            sender_id = str(last_msg[1])

    # ═══ 冷却检查：Bot 刚回复过该群 ═══
    # （2026-10-10 决策重写）三个要点：
    # ① 权威时钟 = after_reply（真实发出时刻）；
    # ② _pending_reply（决策放行但回复还在生成）= 预占冷却 —— 旧版这条完全没防，
    #    "同一条/相邻消息连答两次"（沙漏 20:55 双调用）正出在这：回复要生成几秒~
    #    几十秒，期间再进一条消息，旧冷却看 _last_reply_time 还是旧值 → 二次放行。
    # ③ **接话豁免**：Bot 上一条回复的对象就是当前发送者，且他在追问
    #   （间隔 < followup_window）→ 视为对话延续，不冷却。
    #   旧版一刀切冷却会拦掉"同一个人追第二个问题"，Bot 显得答非所问接不上话。
    if cooldown > 0 and not ctx.get("is_at_bot"):   # cooldown_seconds=0 → 本段整体不生效
        nowts = time.time()
        last_reply = _last_reply_time.get(group_id, 0)
        _pend = _pending_reply.get(group_id, 0)
        # 预占超 120s 视为回复生成失败 → 丢弃，避免把该群永久焊死
        if _pend and nowts - _pend >= 120:
            _pend = 0
        # 有效冷却起点 = 真实发出 与 预占放行 取更近的一个
        _eff = max(last_reply, _pend)
        if _eff and (nowts - _eff) < cooldown:
            fw = int(dparams.get("followup_window_seconds", 300))
            _tgt, _tgt_at = _last_reply_target.get(group_id, ("", 0))
            _is_followup = (sender_id and _tgt == sender_id
                            and 0 <= nowts - _tgt_at < fw
                            and last_reply > 0)  # 预占期（还没真发出）不给追问豁免
            if not _is_followup:
                _src = "回复生成中(预占)" if _pend >= last_reply else "刚回复过"
                return DecisionV3(
                    should_reply=False,
                    reason=f"[冷却] Bot {int(nowts-_eff)}秒前{_src}，冷却中",
                    atmosphere="冷却中",
                    atmosphere_confidence=0.95,
                    strategy="冷却",
                    strategy_detail=f"回复后 {cooldown} 秒内不主动插话（被 @ / 对话者追问除外）",
                    topic="闲聊",
                    topic_continuation=False,
                )

    # ═══ 防刷屏闸（2026-10-10 修复空转问题）═══
    # 旧版扫描 recent_msgs 里 bot_qq 的消息数 —— 但主循环把 Bot 自己的消息
    # **过滤掉了不入 recent_msgs**（bot_core 8448 行），这个闸永远数到 0，形同虚设。
    # 现在改用插件自己维护的"真实发出时间序列"（after_reply 登记）。
    if max_bot_replies > 0 and not ctx.get("is_at_bot"):
        _times = _bot_spoke_times.get(group_id)
        if _times:
            nowts = time.time()
            fresh_n = sum(1 for t in _times if nowts - t < cooldown)
            if fresh_n >= max_bot_replies:
                return DecisionV3(
                    should_reply=False,
                    reason=f"[防刷屏] Bot {cooldown}秒内已发言 {fresh_n} 条，达到上限",
                    atmosphere="克制",
                    atmosphere_confidence=0.9,
                    strategy="冷却",
                    strategy_detail=f"{cooldown} 秒窗口内发言达 {max_bot_replies} 条上限，暂停避免刷屏",
                    topic="闲聊",
                    topic_continuation=False,
                )
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

    # ═══ 对话链检测（v2：活跃段切分 + 交替判定 + Bot 台上时间戳）═══
    # Bot 在场性用 _bot_spoke_times（真实发出序列）—— recent_msgs 不含 Bot 消息
    # （主循环过滤，bot_core 8448），不补就是"Bot 永远不在场"（22:36 误判根因）。
    _bot_n_in_window = 0
    _bot_last_ts = _bot_spoke_times.get(group_id, [0.0])[-1] if _bot_spoke_times.get(group_id) else 0.0
    try:
        _ts_list = [float(m[0]) for m in recent_msgs if isinstance(m, (list, tuple)) and m]
        if _ts_list:
            _span = max(30.0, max(_ts_list) - min(_ts_list) + 5.0)
            _nowts = time.time()
            _bot_n_in_window = sum(1 for t in _bot_spoke_times.get(group_id, []) if _nowts - t <= _span)
    except Exception:
        _bot_n_in_window = 0
    chain_type = chain_detector.detect_chain_type(group_id, recent_msgs, bot_qq,
                                                  bot_recent_n=_bot_n_in_window,
                                                  bot_last_ts=_bot_last_ts)
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

    # ── 2026-10-10 决策重写：信号两级拆分 ──
    # 旧版"含 ？/吗/呢 就硬放行"把群友互聊的问句也当 calls，Bot 频繁不合时宜插嘴。
    # 拆成：指向 Bot（@/称呼/引用）→ 硬放行；普通问句 → 只算"软信号"，
    # 交给 AI 读空气裁；AI 不可用时才凭软信号兜底放行。
    _bot_asked = _bot_directed(message, ctx)
    _question_soft = (not _bot_asked) and _has_question_signal(message)
    _interest_hit = _has_bot_interest(message)

    # ═══ 事实闸门（只保留"确定性事实"，判断一律交给 AI）═══
    # 2026-10-11 用户定调："多花 token 提高判断精准度，硬规则尽量不使用"。
    # 旧硬规则层里的 兴趣词命中 / 对话链互聊跳过 / 话题+记忆硬放行 本质都是
    # **"该不该接"的判断**伪装成规则 —— 正是 22:36(互聊误判) 与 23:07(问句误杀)
    # 两次事故的根因。现全部降级：AI 在线时作为特征喂给 AI 自行裁，AI 挂了才
    # 在兜底里当安全网。这里只留三条不可辩驳的事实闸门。
    #
    # 事实闸门①：纯无意义内容（"嗯""……""6"）——不是判断，是字符事实。
    if _is_trivial(message):
        return DecisionV3(
            should_reply=False,
            reason="[事实] 纯无意义内容",
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

    # （旧"兴趣词硬放行"已删 —— _interest_hit 作为特征喂 AI，见 prompt 构建）

    # ═══ 接话窗口（2026-10-13，学 qq-bridge 的「等、回与沉睡前观察」）═══
    # 我们此前的"冷却=0"把旧版的追问豁免一起关掉了 → 群友追问时也要等下一轮
    # 才可能被回应。这里独立成一个**正向窗口**：回完之后的 followup_wait_seconds 内，
    # 对方追问（短句/问句）或直接 @/叫我们 → 直接放行，不再过参与度与冷却。
    _fu_win = int(dparams.get("followup_wait_seconds", 45) or 0)
    _now = time.time()
    if _fu_win > 0:
        _until = _followup_until.get(group_id, 0)
        if _until > _now:
            _tgt, _tgt_at = _last_reply_target.get(group_id, ("", 0))
            _same_person = bool(sender_id) and str(_tgt) == str(sender_id)
            _short = len((message or "").strip()) <= int(dparams.get("followup_max_chars", 40) or 40)
            _qsig = _has_question_signal(message) or _short
            if (_same_person and _qsig) or _bot_directed(message, ctx):
                _followup_until.pop(group_id, None)   # 窗口用掉即收，别一直开着
                return DecisionV3(
                    should_reply=True,
                    reason=f"[接话] 刚回过这位，对方在追问（窗口剩 {int(_until - _now)}s）",
                    atmosphere="对话延续",
                    atmosphere_confidence=0.8,
                    strategy="接话",
                    strategy_detail="对方在上一句后继续追问，顺着答，别重新开场",
                    topic=topic,
                    topic_continuation=True,
                    conversation_chain=chain_type,
                )
        elif _until:
            _followup_until.pop(group_id, None)

    # 事实闸门②：消息明确指向 Bot（@/称呼/引用）——这是"话是否对着 Bot 说"的
    # 事实判定，不是"该不该说"的价值判断，保留硬放行（被@不回复是失礼不是判断）。
    if _bot_asked:
        _q = _has_question_signal(message)
        return DecisionV3(
            should_reply=True,
            reason="[硬规则] 消息明确指向Bot" + ("且在提问" if _q else ""),
            atmosphere="询问" if _q else "闲聊",
            atmosphere_confidence=0.85,
            strategy="回应",
            strategy_detail="消息点名/引用了Bot，属于对Bot说话",
            topic=topic,
            topic_continuation=topic_continuation,
            emotion=emotion,
            energy=energy,
            memory_match=memory_match,
            memory_items=memory_items,
            conversation_chain=chain_type,
            suggested_tone="轻松" if emotion == "positive" else "安慰" if emotion == "negative" else "正常",
        )

    # ═══ 判断降级：对话链/话题/兴趣 不再是硬短路，改为 AI 特征 + 兜底安全网 ═══
    # 2026-10-11 用户定调后重构：旧版这三块（Bot刚插话跳过 / 用户互聊跳过 /
    # 话题+记忆硬放行）会在 AI 之前直接 return，等于用关键词/计数替 AI 拍板，
    # 是误判来源。现在：
    #   · AI 在线 → 只把这些当**特征**喂进 prompt（见 _build_ai_prompt 的
    #     "规则侧参考信号"段），最终该不该说由 AI 综合裁；
    #   · AI 不可用 → 才启用它们作为兜底安全网（宁当哑巴不乱插嘴）。
    # 计算"规则侧倾向"（仅供兜底与特征用，不短路）：
    _rule_says_skip = (
        (chain_type == "bot_continuation" and not (is_active and topic_continuation and memory_match))
        or (chain_type == "user_conversation" and not ctx.get("is_at_bot")
            and not (is_active and (topic_continuation or memory_match)))
    )
    _rule_says_go = bool(topic_continuation and memory_match)

    # ── AI 不可用兜底（2026-10-10 fail-safe；2026-10-11 纳入降级后的规则倾向）──
    # 旧版所有 AI 异常一律"默认放行"→ 429 风暴时无差别插话（当晚 21:32 即是 429）。
    # 现在：软信号问句 / 话题+记忆 / 活跃档 放行，但规则侧明确判 skip 时压过。
    # ★ 2026-10-12 起：它只在**断路器打开**（连续失败）时生效；单次失败改走
    #   _degraded_decision 的 fail-open（交主模型自裁），见上方"降级治理"段。
    def _ai_fallback_allow() -> bool:
        if _rule_says_skip:
            return False
        return bool(_question_soft or _rule_says_go or is_active)

    # 降级决策共用的字段（topic/chain/emotion… 都是事实层算出来的，与失败无关）
    _deg_base = {
        "topic": topic,
        "topic_continuation": topic_continuation,
        "emotion": emotion,
        "energy": energy,
        "memory_match": memory_match,
        "memory_items": memory_items,
        "conversation_chain": chain_type,
    }

    # ═══ AI 读空气层 ═══
    client = _get_ai_client()
    if client is None:
        # 客户端缺失 = **配置态**（没配 key / 初始化失败），不是偶发失败：
        # 直接走保守规则兜底（与 2026-10-10~10-11 的行为一致），不做 fail-open，
        # 也不计入断路器统计（否则没配 key 的机器会变成"无差别插话"）。
        return _degraded_decision(
            "[AI客户端不可用]", group_id, _deg_base,
            rule_allow=_ai_fallback_allow(), rule_skip=_rule_says_skip,
            detail="决策模型未配置或初始化失败。", force_conservative=True)

    try:
        decision_context = {
            "topic": topic,
            "topic_continuation": topic_continuation,
            "emotion": emotion,
            "energy": energy,
            "memory_match": memory_match,
            "memory_items": memory_items,
            "conversation_chain": chain_type,
            # 规则侧参考信号（2026-10-11 降级：不再是短路，只是给 AI 的提示，
            # AI 可以不同意）——兴趣词命中 / 规则倾向。
            "interest_hit": _interest_hit,
            "rule_hint": ("规则侧倾向不插话(对话链)" if _rule_says_skip
                          else "规则侧倾向接话(话题+记忆)" if _rule_says_go
                          else "规则侧无强倾向"),
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

        # ── 决策调用：带重试（降级治理第①层）────────────────────────────
        # 偶发失败（429/网络抖动/模型偶发吐非 JSON）先重试一次，别一上来就降级：
        # 第一次用配置超时；重试用 60% 超时（避免两倍等待拖垮回复时机）。
        import concurrent.futures
        _attempts = max(1, int(_deg_cfg("AI_RETRY_MAX", 2) or 2))
        _fail_kind = ""
        result = None
        data = None
        for _attempt in range(_attempts):
            _to = timeout if _attempt == 0 else max(4.0, float(timeout) * 0.6)
            # ★ 用显式 shutdown(wait=False) 而不是 `with` 块：`with` 在退出时会
            #   等**在途的那次调用**跑完 —— future.result(timeout) 明明已经在
            #   _to 秒返回了，调用方却还要继续等上游把整个响应吐完（30s 的超时
            #   实打实要等 30s+）。超时后立刻放手，慢线程自己收尾。
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                try:
                    future = executor.submit(client.call, messages, max_tokens=max_tokens)
                    result = future.result(timeout=_to)
                except concurrent.futures.TimeoutError:
                    result, _fail_kind = None, f"[AI超时]（第{_attempt + 1}次 / {_to:.0f}s）"
                    print(f"[GroupDecider] 决策调用超时（第{_attempt + 1}次，{_to:.0f}s）")
                    continue
                except Exception as _e:
                    result, _fail_kind = None, f"[AI异常]（第{_attempt + 1}次）{str(_e)[:60]}"
                    print(f"[GroupDecider] 决策调用异常（第{_attempt + 1}次）: {_e}")
                    continue
            finally:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
            reply = result[0] if isinstance(result, tuple) and result else (
                None if isinstance(result, tuple) else result)
            if not reply:
                result, _fail_kind = None, f"[AI返回空]（第{_attempt + 1}次）"
                print(f"[GroupDecider] 决策返回为空（第{_attempt + 1}次）")
                continue
            data = _parse_json_output(reply)
            if data is None:
                result, _fail_kind = None, f"[AI格式异常]（第{_attempt + 1}次）"
                print(f"[GroupDecider] 决策输出非 JSON（第{_attempt + 1}次）")
                continue
            break

        if data is None:
            _record_ai_failure()
            return _degraded_decision(
                _fail_kind or "[AI不可用]", group_id, _deg_base,
                rule_allow=_ai_fallback_allow(), rule_skip=_rule_says_skip,
                detail="决策模型本次没有给出可用判断。")

        should_reply = bool(data.get("should_reply", True))
        reason = str(data.get("reason", ""))[:200]
        atmosphere = str(data.get("atmosphere", "闲聊"))
        atmosphere_confidence = float(data.get("atmosphere_confidence", 0.0))
        # 记下这次看到的氛围：下一轮 AI 失败时沿用（别退化成"未知"）
        _remember_atmosphere(group_id, atmosphere, atmosphere_confidence)
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
            scored, score_suffix, scores = _score_to_decision(data, participation,
                                                              asked=_question_soft)
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
        _record_ai_failure()
        # 走到这里连 _deg_base 都可能没建好（异常发生在更早的环节）→ 兜底用空字段
        return _degraded_decision(
            f"[决策异常]{str(e)[:60]}", group_id, locals().get("_deg_base") or {},
            rule_allow=True, rule_skip=False,
            detail="决策链路抛异常，本次改由主模型自行判断。")


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


def _score_to_decision(data: dict, participation: str, asked: bool = False) -> tuple:
    """基于 AI 多维评分计算最终 should_reply 与 reason 后缀。

    综合分 = 参与价值 * 时机 - 风险 * 风险权重(0.6)，再按参与度档位加偏移。

    asked（2026-10-12 仿真 C1 修）：消息是**问 Bot 的话**（正反问/疑问信号）。
    这条公式语义上只管"没人喊我、我要不要主动插话"——被当面问时 timing/risk
    的语义全拧巴（答话不存在"时机不合适"），旧版却照用公式算术否决 AI 的
    "想回"表态，实测同一句"你玩不玩明日方舟"4 次跑出 1 应答 3 跳过（0.23/
    0.24/0.44/0.03 反复横跳阈值线）= 群里"有时答有时不答"的飘忽根源。
    现在：被问场景以 AI 表态为准（判断权在 AI，符合用户定调），公式只留给
    主动插话场景当防话痨护栏。"""
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

    ai_want = bool(data.get("should_reply", True))
    if asked:
        # 被问 → AI 表态就是判决；风险极高(冒犯/刷屏)时公式仍有否决权
        if risk >= 0.8:
            should, reason_suffix = False, f"[被问但风险{risk:.2f}过高不回]"
        else:
            should, reason_suffix = ai_want, f"[被问·AI表态:{'答' if ai_want else '不回'} 价值{value:.2f}]"
        return should, reason_suffix, {"participate_value": value, "timing_score": timing, "risk_score": risk}

    score = value * timing - risk * 0.6

    # 参与度偏移
    if participation == "active":
        score += 0.15
    elif participation == "conservative":
        score -= 0.15

    # 临界区由 AI 明确表态主导
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

    # ── 决策缓存（2026-10-10）──
    # 主链对同一条消息会两次派发 before_reply（无触发预决策 → 触发后正式注入）。
    # 旧版第二次重新跑完整决策：多烧一次 AI、冷却时钟被"决定即计时"污染两次
    # （"刚说完又抢话"的另一半根因）。现在以 (群, 文本, 时间线尾条时间戳) 为键
    # 缓存首次判定，第二次派发直接复用 —— 注入不丢、AI 不重烧、时钟不重复。
    recent_msgs = ctx.get("recent_msgs", [])
    _ck_tail_ts = recent_msgs[-1][0] if (recent_msgs and isinstance(recent_msgs[-1], (list, tuple)) and recent_msgs[-1]) else 0
    # ★ 缓存键必须区分 observe_retry：潜水前观察是**同一段文本**的二次决策，
    #   不带这个标志就会命中第一次的缓存 → 又返回 observe → 无限循环观察。
    _ck = (group_id, message[:80], _ck_tail_ts, bool(ctx.get("observe_retry")))
    _hit = _decision_cache.get(_ck)
    if _hit and time.time() - _hit[0] < 30:
        _cached = _hit[1]
        if _cached is None:
            return None
        return dict(_cached)

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
    _result = None
    if decision is not None:
        if not decision.should_reply:
            # ★ 潜水前观察（2026-10-13，学 qq-bridge 的「沉睡前观察」）：
            #   不是"判了不接就结束"，而是过一会儿再看一眼 —— 话题可能转向我们。
            #   宿主只会重试一次（observe_retry），所以不会变成轮询。
            _obs = 0
            try:
                # 只对"正常的价值判断"排观察；无意义内容、决策器降级/异常（客户端缺失、
                # 超时、格式错）重看一遍也不会变好，纯属白等。
                _no_observe = ("无意义", "客户端缺失", "AI超时", "AI返回空", "AI格式异常", "异常")
                if (not ctx.get("observe_retry")
                        and not any(k in decision.reason for k in _no_observe)):
                    _obs = int(_get_decision_params().get("observe_before_silence_seconds", 25) or 0)
            except Exception:
                _obs = 0
            if _obs > 0:
                _result = {
                    "action": "observe",
                    "after": _obs,
                    "reason": f"[Decider v3] {decision.reason}（{_obs}s 后再看一眼；氛围:{decision.atmosphere}）",
                }
            else:
                _result = {
                    "action": "skip",
                    "reason": f"[Decider v3] {decision.reason} (氛围:{decision.atmosphere})",
                }
        else:
            # 冷却时钟统一（2026-10-10）：
            # ① 权威"已发言"时钟 = after_reply（真实发出）；
            # ② 这里写 _pending_reply 作**预占冷却** —— 回复生成要几秒~几十秒，
            #    期间同群再进消息若只看 _last_reply_time（还是旧的）会二次放行，
            #    这正是"连答两次"的第三条路径。pending 让冷却期提前咬合。
            _pending_reply[group_id] = time.time()
            _result = {
                "action": "inject",
                "content": decision.to_prompt_injection(),
                "meta": decision.to_dict(),
            }
    # 写缓存（None 也要缓存，防第二次派发重烧决策）
    if len(_decision_cache) > 120:
        _cut = time.time() - 30
        for _k in [k for k, (ts, _) in _decision_cache.items() if ts < _cut]:
            _decision_cache.pop(_k, None)
    _decision_cache[_ck] = (time.time(), _result)
    return _result


def after_reply(message: str = "", group_id: str = "", reply: str = "", context: dict = None, **kwargs) -> dict | None:
    """after_reply 挂载点：更新群画像和对话链"""
    if not PLUGIN_META["enabled"]:
        return None

    # ── 冷却时钟统一（2026-10-10 修复）──
    # 此前真实发送后只更新 decider.py 旧模块的 _last_reply_time，
    # 而 v3 decide() 读的是 __init__ 的同名全局 —— 两个 dict 永不同步，
    # 导致"Bot 刚说完又抢话"。这里以**真实发出**为唯一权威时钟：
    # 记录发出时刻、发言序列（防刷屏闸）、以及本轮对话对象（接话豁免）。
    if reply:
        _now = time.time()
        _last_reply_time[group_id] = _now
        _pending_reply.pop(group_id, None)  # 已真实发出，预占失效
        _lst = _bot_spoke_times.setdefault(group_id, [])
        _lst.append(_now)
        if len(_lst) > 20:
            del _lst[:-20]

    ctx = context or {}
    recent_msgs = ctx.get("recent_msgs", [])
    bot_qq = ctx.get("bot_qq", "")

    # 提取发送者
    sender_id = ""
    if recent_msgs:
        last_msg = recent_msgs[-1]
        if isinstance(last_msg, (list, tuple)) and len(last_msg) > 1:
            sender_id = str(last_msg[1])

    # 对话对象判定（接话豁免用，2026-10-10）：
    # 主链 context 的 sender（本轮回复针对的触发消息发送者）优先；
    # 回退 recent_msgs 最后一条。Bot 自己的消息不进 recent_msgs（主循环过滤），
    # 所以最后一条必然是入站用户消息 —— 即"Bot 刚在回谁"。
    _who = str(ctx.get("sender") or ctx.get("user_id") or "")
    if not _who:
        for msg in reversed(recent_msgs):
            if isinstance(msg, (list, tuple)) and len(msg) > 1:
                _who = str(msg[1])
                break
    if _who and reply:
        _last_reply_target[group_id] = (_who, _last_reply_time.get(group_id, time.time()))
    if reply:
        # ★ 接话窗口：回完不是结束，45s 内对方追问还能再答一次
        #   （qq-bridge 的 purpose="messages"：回复后等可能的回复）
        try:
            _fu = int(_get_decision_params().get("followup_wait_seconds", 45) or 0)
            if _fu > 0:
                _followup_until[group_id] = _now + _fu
        except Exception:
            pass
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
        return {"ok": False, "error": f"未知档位: {pid}"}
    # 2026-10-12：改走免密车道后**不再需要任何第三方 key**。
    # 保留这个动作只为不让旧界面报错，并如实回一句"不需要 key"。
    return {"ok": True, "no_key_needed": True,
            "message": "决策已改走免费车道（zen/kilo，免 key）；档位只需选模型，不用填 Key",
            "configured": free_models.is_configured(pid), "config": _ai_config_status()}


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
