# -*- coding: utf-8 -*-
"""
注意力机制 - 追踪群聊中各用户的活跃度

灵感来自 astrbot_plugin_group_chat_plus，简化适配 EggBloom。
"""

import time
import threading
from dataclasses import dataclass
from typing import Dict


@dataclass
class UserAttention:
    """单个用户的注意力状态"""
    score: float = 0.0
    last_active: float = 0.0
    cooldown_until: float = 0.0    # 冷却到期时间戳

    @property
    def is_cooling(self) -> bool:
        return time.time() < self.cooldown_until

    def decay(self, rate: float):
        """指数衰减"""
        if self.score > 0.01:
            elapsed = time.time() - self.last_active
            self.score *= (rate ** elapsed)
            if self.score < 0.01:
                self.score = 0.0


class AttentionTracker:
    """群聊注意力追踪器"""

    def __init__(self, decay_rate=0.85, message_boost=0.2, reply_boost=0.4,
                 threshold=0.8, cooldown_seconds=120, max_users=50):
        self.decay_rate = decay_rate
        self.message_boost = message_boost
        self.reply_boost = reply_boost
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self.max_users = max_users
        self._lock = threading.Lock()
        self._groups: Dict[str, Dict[str, UserAttention]] = {}

    def _get_group(self, group_id: str) -> Dict[str, UserAttention]:
        if group_id not in self._groups:
            self._groups[group_id] = {}
        return self._groups[group_id]

    def on_message(self, group_id: str, user_id: str):
        """用户发消息时调用"""
        with self._lock:
            users = self._get_group(group_id)
            u = users.get(user_id)
            if u is None:
                if len(users) >= self.max_users:
                    # 淘汰最久未活跃的
                    oldest_id = min(users, key=lambda k: users[k].last_active)
                    if users[oldest_id].score < 0.05:
                        del users[oldest_id]
                    else:
                        return
                u = UserAttention()
                users[user_id] = u
            # 衰减到当前
            u.decay(self.decay_rate)
            # 加分
            u.score = min(1.0, u.score + self.message_boost)
            u.last_active = time.time()

    def on_reply(self, group_id: str, user_id: str):
        """Bot 回复某用户时调用"""
        with self._lock:
            users = self._get_group(group_id)
            u = users.get(user_id)
            if u is None:
                u = UserAttention()
                users[user_id] = u
            u.decay(self.decay_rate)
            u.score = min(1.0, u.score + self.reply_boost)
            u.last_active = time.time()

    def should_skip(self, group_id: str, user_id: str) -> bool:
        """检查该用户是否在冷却中（注意力溢出）"""
        with self._lock:
            users = self._get_group(group_id)
            u = users.get(user_id)
            if u is None:
                return False
            u.decay(self.decay_rate)
            # 判断是否需要冷却
            if u.score >= self.threshold and not u.is_cooling:
                u.cooldown_until = time.time() + self.cooldown_seconds
                return True  # 触发冷却
            return u.is_cooling

    def get_scores(self, group_id: str) -> Dict[str, float]:
        """获取当前各用户注意力分数（调试用）"""
        with self._lock:
            users = self._get_group(group_id)
            result = {}
            for uid, u in users.items():
                u.decay(self.decay_rate)
                result[uid] = round(u.score, 3)
            return result

    def reset_user(self, group_id: str, user_id: str):
        """重置某用户注意力"""
        with self._lock:
            users = self._get_group(group_id)
            if user_id in users:
                del users[user_id]

    def reset_group(self, group_id: str):
        """重置整个群的注意力"""
        with self._lock:
            self._groups.pop(group_id, None)
