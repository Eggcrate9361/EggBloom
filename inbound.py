# -*- coding: utf-8 -*-
"""入站消息：归一与内容指纹（1.5.9 §B0）。

## 症状不是"判重不够"，是"同一件事有多种表示"

一条入站消息可能带五种形态：原文 / 引用预览 / 图片描述 / `[图片]` 占位符 /
`@` 渲染昵称。又被存进三处：批次文本、`group_chat_history`、`GROUP_TIMELINE`。
模型看到 2–3 份、字面各不相同，只能当成多条新消息，于是回两次。

现存三次判重都是事后救火：

    _mark_seen_msg_id      挡同一事件重复投递，挡不住同一内容换形态
    is_similar_to_recent   挡跨轮字面相似，挡不住换说法的语义重复
    message_sent 过滤      挡自己的回声，挡不住引用块里带回自己的话

## 本模块做什么

    normalize_text()  把文本压成一种表示（去占位符 / 标点 / 全半角 / 空白）
    fingerprint()     同人 + 2-gram 集合的短哈希
    DupeGuard         30 秒窗口内同人同指纹判重

窗口不能长：真人会重复说「草」「哈哈哈」。30 秒内同一个人连说两句一模一样的
话，在群聊里几乎只有一种解释 —— 同一条消息被投递了两次。

不 import bot_core，不做 IO，可单测。
"""
import hashlib
import re
import threading
import time
from typing import Dict, Optional

#: 这些占位符不算内容 —— 「[图片]」与真的一张图，对判重来说是一回事
_PLACEHOLDER = re.compile(
    r"\[(图片|表情|语音|视频|文件|动画表情|回复|引用|CQ:[^\]]*)\]")
_PUNCT = re.compile(r"[\s，。！？、；：,.!?;:~～`'\"“”‘’（）()\[\]【】<>《》…—\-_=+*/\\|]+")
_FULL2HALF = str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
                           "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
                           "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                           "abcdefghijklmnopqrstuvwxyz")


def normalize_text(text: str) -> str:
    """只留内容本身：去占位符、标点、空白，全角转半角，英文小写。"""
    t = str(text or "")
    t = _PLACEHOLDER.sub("", t)
    t = t.translate(_FULL2HALF)
    t = _PUNCT.sub("", t)
    return t.lower()


def fingerprint(sender: str, text: str) -> str:
    """同人 + 2-gram 集合。换标点、换空白、加个表情都不改指纹。

    返回空串表示"没有可比的内容"（纯表情/纯图），调用方应当放行 ——
    对空内容判重会把「[图片]」全当成同一条。
    """
    t = normalize_text(text)
    if not t:
        return ""
    if len(t) == 1:
        grams = {t}
    else:
        grams = {t[i:i + 2] for i in range(len(t) - 1)}
    raw = "|".join(sorted(grams)) + "|" + str(sender or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class DupeGuard:
    """窗口内同人同指纹判重。

    线程安全：消息处理是多线程的（主循环 + Timer + 插件线程）。
    有上限：超 max_keys 先按时间清一半，不清空 —— 清空会让攻击者用一个
    高频词把表刷满，把真重复放进来。
    """

    def __init__(self, window: float = 30.0, max_keys: int = 4000):
        self.window = float(window)
        self.max_keys = int(max_keys)
        self._seen: Dict[str, float] = {}
        self._lock = threading.Lock()

    def hit(self, group_id, sender, text, now: Optional[float] = None) -> bool:
        """命中返回 True（应丢弃），未命中登记并返回 False。"""
        fp = fingerprint(sender, text)
        if not fp:
            return False
        key = f"{group_id}:{fp}"
        t = float(now if now is not None else time.time())
        with self._lock:
            old = self._seen.get(key)
            if old is not None and t - old < self.window:
                return True
            self._seen[key] = t
            if len(self._seen) > self.max_keys:
                self._evict(t)
            return False

    def _evict(self, now: float) -> None:
        cutoff = now - self.window
        alive = {k: v for k, v in self._seen.items() if v >= cutoff}
        if len(alive) > self.max_keys // 2:
            # 还在窗口内的也太多：按时间丢掉最旧的一半，保留最近的一半
            ordered = sorted(alive.items(), key=lambda kv: kv[1])
            alive = dict(ordered[len(ordered) // 2:])
        self._seen = alive

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._seen)


__all__ = ["normalize_text", "fingerprint", "DupeGuard"]
