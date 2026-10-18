# -*- coding: utf-8 -*-
"""
chat_style 群友说话方式学习插件

核心：
- 学习群友的说话方式、口头禅、句式、表情习惯
- 联动 super_memory：从历史聊天记录自动提取风格
- 记忆驱动风格权重：高频特征加强，低频衰减
- 支持手动标记学习目标和注入样本

用法：
    from plugins.chat_style import PLUGIN_META, before_reply, learn_from_user
"""

import json
import os
import re
import sqlite3
import time
import threading
from typing import Any, Dict, List, Optional

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "chat_style.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

PLUGIN_META = {
    "name": "chat_style",
    "version": "0.2.0",
    "description": "学习群友说话方式，联动记忆自动提取风格特征",
    "author": "EggBloom",
    "group_only": True,
    "enabled": True,
    "dependencies": [],
    "hooks": ["before_reply"],
    "priority": 20,
    "estimated_tokens": 120,
    "tags": ["说话风格"],
    "page": "plugins/chat_style/ui.html",
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
        print(f"[ChatStyle] 保存失败 {path}: {e}")


class ChatStyleLearner:
    """群友说话方式学习器"""

    def __init__(self):
        defaults = {
            "enabled": True,
            "learn_targets": [],
            "max_samples_per_user": 100,
            "style_weight": 0.4,
            "auto_learn_enabled": True,
            "auto_learn_min_samples": 5,
            "auto_learn_from_memory": True,
            "memory_plugin_name": "super_memory",
            "feature_decay_days": 30,
        }
        self.config = _load_json(CONFIG_PATH, defaults)
        for key, value in defaults.items():
            if key not in self.config:
                self.config[key] = value
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS style_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    reply TEXT DEFAULT '',
                    timestamp REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_style_user ON style_samples(scope_id, user_id, timestamp);

                CREATE TABLE IF NOT EXISTS style_features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    feature_type TEXT NOT NULL,
                    feature_value TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    use_count INTEGER DEFAULT 0,
                    last_used REAL NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_features_user ON style_features(scope_id, user_id);
            """)
            conn.commit()
            # Migrate old schema if needed
            try:
                cols = [r[1] for r in conn.execute('PRAGMA table_info(style_features)').fetchall()]
                if 'use_count' not in cols:
                    conn.execute('ALTER TABLE style_features ADD COLUMN use_count INTEGER DEFAULT 0')
                if 'last_used' not in cols:
                    conn.execute('ALTER TABLE style_features ADD COLUMN last_used REAL NOT NULL DEFAULT 0')
                conn.commit()
            except Exception:
                pass
            conn.close()
        except Exception as e:
            print(f"[ChatStyle] 初始化数据库失败: {e}")

    def get_config(self) -> Dict[str, Any]:
        return dict(self.config)

    def set_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            allowed = {"learn_targets", "max_samples_per_user", "style_weight",
                       "auto_learn_enabled", "auto_learn_min_samples",
                       "auto_learn_from_memory", "memory_plugin_name", "feature_decay_days"}
            for key, value in data.items():
                if key in allowed:
                    self.config[key] = value
            self.config["enabled"] = bool(data.get("enabled", self.config.get("enabled", True)))
            _save_json(CONFIG_PATH, self.config)
            return self.get_config()

    def add_message(self, scope_id: str, user_id: str, message: str, reply: str = ""):
        """添加一条消息样本并更新特征"""
        if not self.config.get("enabled", True):
            return
        with self._lock:
            auto_learn = self.config.get("auto_learn_enabled", True)
            if not auto_learn and user_id not in self.config.get("learn_targets", []):
                return
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    "INSERT INTO style_samples (scope_id, user_id, message, reply, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (str(scope_id), str(user_id), message[:500], reply[:500], time.time())
                )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[ChatStyle] 记录消息失败: {e}")
                return
            self._maybe_refresh_features(scope_id, user_id)

    def _maybe_refresh_features(self, scope_id: str, user_id: str):
        """样本量达标后增量刷新特征（节流，避免每条消息全量提取）"""
        min_samples = max(1, int(self.config.get("auto_learn_min_samples", 5)))
        try:
            conn = sqlite3.connect(DB_PATH)
            count = conn.execute(
                "SELECT COUNT(*) FROM style_samples WHERE scope_id=? AND user_id=?",
                (str(scope_id), str(user_id))
            ).fetchone()[0]
            conn.close()
        except Exception:
            return
        if count >= min_samples:
            self._refresh_features(scope_id, user_id)

    def _refresh_features(self, scope_id: str, user_id: str):
        """从样本重新提取特征，增量更新权重"""
        features = self._extract_features(scope_id, user_id)
        if not features:
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            now = time.time()
            for f in features:
                row = conn.execute(
                    "SELECT id, weight, use_count FROM style_features WHERE scope_id=? AND user_id=? AND feature_type=? AND feature_value=?",
                    (str(scope_id), str(user_id), f["type"], f["value"])
                ).fetchone()
                if row:
                    new_weight = min(1.0, (row[1] * row[2] + f["weight"]) / (row[2] + 1))
                    conn.execute(
                        "UPDATE style_features SET weight=?, use_count=use_count+1, last_used=? WHERE id=?",
                        (new_weight, now, row[0])
                    )
                else:
                    conn.execute(
                        "INSERT INTO style_features (scope_id, user_id, feature_type, feature_value, weight, last_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (str(scope_id), str(user_id), f["type"], f["value"], f["weight"], now, now)
                    )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[ChatStyle] 更新特征失败: {e}")

    def _extract_features(self, scope_id: str, user_id: str) -> List[Dict[str, Any]]:
        """从样本中提取风格特征"""
        try:
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute(
                "SELECT message FROM style_samples WHERE scope_id=? AND user_id=? ORDER BY timestamp DESC LIMIT ?",
                (str(scope_id), str(user_id), self.config.get("max_samples_per_user", 100))
            ).fetchall()
            conn.close()
        except Exception:
            return []

        if not rows:
            return []

        messages = [r[0] for r in rows]
        features: List[Dict[str, Any]] = []

        # 1. 高频口头禅（2-4字词 + 英文词）
        word_freq: Dict[str, int] = {}
        for msg in messages:
            words = re.findall(r'[\u4e00-\u9fff]{2,4}|[a-zA-Z]{2,6}', msg)
            for w in words:
                word_freq[w] = word_freq.get(w, 0) + 1
        for word, freq in sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:10]:
            if freq >= 2:
                features.append({
                    "type": "口头禅",
                    "value": word,
                    "weight": min(1.0, freq / 15)
                })

        # 2. 开头句式
        starters: Dict[str, int] = {}
        for msg in messages:
            if len(msg) >= 2:
                starters[msg[:2]] = starters.get(msg[:2], 0) + 1
        for starter, freq in sorted(starters.items(), key=lambda x: x[1], reverse=True)[:5]:
            if freq >= 2:
                features.append({
                    "type": "开头句式",
                    "value": starter,
                    "weight": min(1.0, freq / 8)
                })

        # 3. 结尾语气词
        endings: Dict[str, int] = {}
        for msg in messages:
            if len(msg) >= 1:
                endings[msg[-1]] = endings.get(msg[-1], 0) + 1
        for end, freq in sorted(endings.items(), key=lambda x: x[1], reverse=True)[:5]:
            if freq >= 2 and end in {'啊', '呢', '吧', '哈', '哦', '呀', '嘛', '呗', '昂', '噢'}:
                features.append({
                    "type": "结尾语气",
                    "value": end,
                    "weight": min(1.0, freq / 10)
                })

        # 4. 表情/符号习惯
        emoticons = []
        for msg in messages:
            emoticons.extend(re.findall(r'[\(（][^)）]*[)）]|[/\\][a-zA-Z]+[/\\]|[\u3000-\u303f\uff00-\uffef]{2,}', msg))
        common_emotes: Dict[str, int] = {}
        for e in emoticons:
            common_emotes[e] = common_emotes.get(e, 0) + 1
        for emote, freq in sorted(common_emotes.items(), key=lambda x: x[1], reverse=True)[:5]:
            features.append({
                "type": "表情习惯",
                "value": emote,
                "weight": min(1.0, freq / 8)
            })

        # 5. 标点习惯
        punct: Dict[str, int] = {}
        for msg in messages:
            for ch in msg:
                if ch in {'！', '？', '…', '～', '♪', '☆', '。', '，', '、'}:
                    punct[ch] = punct.get(ch, 0) + 1
        for p, freq in sorted(punct.items(), key=lambda x: x[1], reverse=True)[:4]:
            if freq >= 2:
                features.append({
                    "type": "标点习惯",
                    "value": p,
                    "weight": min(1.0, freq / 12)
                })

        # 6. 平均句长
        lengths = [len(msg) for msg in messages if msg]
        if lengths:
            avg_len = sum(lengths) / len(lengths)
            features.append({
                "type": "平均句长",
                "value": f"{avg_len:.0f}字",
                "weight": 0.4
            })

        return features

    def get_features(self, scope_id: str, user_id: str, top_k: int = 12) -> List[Dict[str, Any]]:
        """获取已存储的特征，按权重排序"""
        try:
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute(
                "SELECT feature_type, feature_value, weight, use_count FROM style_features WHERE scope_id=? AND user_id=? ORDER BY weight DESC, use_count DESC LIMIT ?",
                (str(scope_id), str(user_id), top_k)
            ).fetchall()
            conn.close()
        except Exception:
            return []
        return [
            {"type": r[0], "value": r[1], "weight": r[2], "use_count": r[3]}
            for r in rows
        ]

    def decay_features(self, days: int = 30):
        """过期特征降权"""
        try:
            conn = sqlite3.connect(DB_PATH)
            cutoff = time.time() - days * 86400
            conn.execute(
                "UPDATE style_features SET weight = weight * 0.5 WHERE last_used < ? AND weight > 0.05",
                (cutoff,)
            )
            conn.execute(
                "DELETE FROM style_features WHERE weight < 0.01 AND last_used < ?",
                (cutoff,)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[ChatStyle] 衰减特征失败: {e}")

    def get_style_prompt(self, scope_id: str, user_id: str) -> str:
        """生成风格注入 prompt"""
        if not self.config.get("enabled", True):
            return ""
        auto_learn = self.config.get("auto_learn_enabled", True)
        if not auto_learn and user_id not in self.config.get("learn_targets", []):
            return ""

        features = self.get_features(scope_id, user_id)
        if not features:
            return ""

        lines = ["【说话风格参考】以下是从该用户历史消息中提取的说话习惯，请自然融入回复："]
        for f in features[:8]:
            lines.append(f"- {f['type']}：{f['value']}")

        return "\n".join(lines)

    def learn_from_memory(self, scope_id: str, user_id: str):
        """从 super_memory 的历史消息中自动学习风格"""
        if not self.config.get("auto_learn_from_memory", True):
            return 0
        memory_name = self.config.get("memory_plugin_name", "super_memory")
        try:
            memory_mod = __import__(f"plugins.{memory_name}", fromlist=["SuperMemory"])
            sm = memory_mod.SuperMemory()  # type: ignore
        except Exception as e:
            print(f"[ChatStyle] 加载记忆插件失败: {e}")
            return 0

        try:
            conn = sqlite3.connect(sm.DB_PATH)
            rows = conn.execute(
                "SELECT user_msg FROM messages WHERE scope=? AND scope_id=? ORDER BY timestamp DESC LIMIT ?",
                ("group", str(scope_id), self.config.get("max_samples_per_user", 100))
            ).fetchall()
            conn.close()
        except Exception:
            return 0

        if not rows:
            return 0

        messages = [r[0] for r in rows if r[0]]
        if not messages:
            return 0

        # 加入样本并自动成为学习目标
        with self._lock:
            if user_id not in self.config.get("learn_targets", []):
                self.config["learn_targets"] = list(self.config.get("learn_targets", [])) + [user_id]
                _save_json(CONFIG_PATH, self.config)

        count = 0
        for msg in messages[: self.config.get("max_samples_per_user", 100)]:
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    "INSERT OR IGNORE INTO style_samples (scope_id, user_id, message, reply, timestamp) VALUES (?, ?, ?, '', ?)",
                    (str(scope_id), str(user_id), msg[:500], time.time())
                )
                conn.commit()
                conn.close()
                count += 1
            except Exception:
                continue

        if count:
            self._refresh_features(scope_id, user_id)
        return count

    def learn_from_user(self, scope_id: str, user_id: str, messages: List[str]):
        """手动注入学习样本"""
        with self._lock:
            if user_id not in self.config.get("learn_targets", []):
                self.config["learn_targets"] = list(self.config.get("learn_targets", [])) + [user_id]
                _save_json(CONFIG_PATH, self.config)
        for msg in messages:
            self.add_message(scope_id, user_id, msg)


# 模块级单例
_learner = ChatStyleLearner()


def get_config() -> Dict[str, Any]:
    return _learner.get_config()


def set_config(data: Dict[str, Any]) -> Dict[str, Any]:
    return _learner.set_config(data)


def learn_from_user(scope_id: str, user_id: str, messages: List[str]):
    """手动学习某个用户的说话方式"""
    _learner.learn_from_user(scope_id, user_id, messages)


def learn_from_memory(scope_id: str, user_id: str) -> int:
    """从记忆中自动学习某个用户的说话方式，返回学习条数"""
    return _learner.learn_from_memory(scope_id, user_id)


def auto_learn(scope_id: str, user_id: str, message: str):
    """自动学习入口：bot_core 在收到消息时调用，采集说话样本并提取风格"""
    try:
        _learner.add_message(str(scope_id), str(user_id), message)
    except Exception as e:
        print(f"[ChatStyle] 自动学习失败: {e}")


def auto_learn_all(scope_id: str, user_id: str, messages: list):
    """批量自动学习入口"""
    for msg in messages:
        auto_learn(scope_id, user_id, msg)


def before_reply(message: str = "", group_id: str = "", context: dict = None, **kwargs) -> dict | None:
    """
    如果当前对话涉及已学习的用户，注入风格提示
    """
    if not PLUGIN_META.get("enabled", True):
        return None

    ctx = context or {}
    target_users = _learner.config.get("learn_targets", [])
    auto_learn = _learner.config.get("auto_learn_enabled", True)
    sender = ctx.get("sender", "")
    mentioned = ctx.get("mentioned_users", [])
    # 私聊（group_id 为空）时以 sender 作为会话作用域，否则群聊特征查不到
    scope_id = group_id or sender or ""

    # 如果发送者是学习目标（或自动学习模式），注入其风格
    if sender in target_users or auto_learn:
        prompt = _learner.get_style_prompt(scope_id, sender)
        if prompt:
            _learner.decay_features(_learner.config.get("feature_decay_days", 30))
            return {
                "action": "inject",
                "content": prompt,
                "meta": {"trigger": "chat_style", "user_id": sender}
            }

    # 如果消息中提到了学习目标，也注入
    for uid in mentioned:
        if uid in target_users:
            prompt = _learner.get_style_prompt(scope_id, uid)
            if prompt:
                return {
                    "action": "inject",
                    "content": prompt,
                    "meta": {"trigger": "chat_style", "user_id": uid}
                }

    return None


def on_load():
    print("[ChatStyle] 群友说话方式学习插件 v0.2.0 已启用")
    # 自动衰减过期特征
    _learner.decay_features(_learner.config.get("feature_decay_days", 30))


def on_unload():
    print("[ChatStyle] 群友说话方式学习插件已卸载")


__all__ = [
    "PLUGIN_META",
    "before_reply",
    "learn_from_user",
    "learn_from_memory",
    "auto_learn",
    "auto_learn_all",
    "get_config",
    "set_config",
    "on_load",
    "on_unload",
]

# ── 通用插件通道动作（WebUI plugin_id='ego.chat_style'） ──
def _action_toggle(params=None):
    cfg = get_config()
    enabled = not bool(cfg.get("enabled", True))
    set_config({"enabled": enabled})
    return {"enabled": enabled, "config": get_config()}


def _action_config(params=None):
    params = params or {}
    key = (params.get("key") or "").strip()
    if key:
        return set_config({key: params.get("value")})
    return get_config()


def _action_custom(params=None):
    params = params or {}
    method = params.get("method") or ""
    scope_id = str(params.get("scope_id") or "")
    user_id = str(params.get("user_id") or "")
    if method == "learn_from_user":
        messages = params.get("messages") or []
        if not isinstance(messages, list):
            messages = [str(messages)]
        learn_from_user(scope_id, user_id, messages)
        return {"ok": True, "count": len(messages)}
    if method == "learn_from_memory":
        try:
            count = learn_from_memory(scope_id, user_id)
            return {"ok": True, "count": int(count or 0)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "unknown method: " + method}


def _action_features(params=None):
    params = params or {}
    scope_id = (params.get("scope_id") or "").strip() or "default"
    user_id = (params.get("user_id") or "").strip()
    try:
        feats = _learner.get_features(scope_id=scope_id, user_id=user_id)
        return {"features": feats.get("features", []) if isinstance(feats, dict) else feats}
    except Exception as e:
        return {"features": [], "error": str(e)}


PLUGIN_ACTIONS = {
    "toggle": _action_toggle,
    "config": _action_config,
    "custom": _action_custom,
    "get_features": _action_features,
    "get_config": lambda params: get_config(),
    "set_config": lambda params: set_config(params or {}),
}
