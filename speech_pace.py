# -*- coding: utf-8 -*-
"""说话节奏：判断"他说完没有"，不用固定秒数（1.5.9 通用化）。

## 为什么不用固定窗口

固定 3 秒/8 秒都是从某一次观测反推的：有人 1 秒一条被抢答，有人 6 秒一条
被拆成两批。换个人、换个群、换个时段就不成立。

真正决定"说完没有"的，是**他自己的节奏**：

    pace = 这个人最近几条消息间隔的中位数
    安静超过 max(1.8, 2.5 × pace)  → 说完了

平时 1 秒一条的人，2.5 秒没动就是说完；平时 8 秒一条的人，20 秒才判定。
没有历史时才退回 2.5 秒兜底 —— 兜底是"还不知道"，不是"设计值"。

## 边界

  · 间隔 > 120 秒不算进节奏（那是换了个话题，不是连发）
  · 只保留最近 8 个间隔，跟着人变
  · 线程安全：消息线程与 Timer 线程都会读

不 import bot_core，不做 IO，可单测。
"""
import threading
from typing import Dict, List, Optional

#: 没有历史时的兜底（不是设计值，是"还不知道"）
DEFAULT_PACE = 2.5
#: 判定说完的最小安静时间 —— 再快的人也要给一点反应时间
MIN_QUIET = 1.8
#: 超过这个间隔不算"连发"，不进节奏样本（秒）
MAX_SAMPLE = 120.0
#: 保留最近几个间隔
HISTORY = 8


class SpeechPace:
    """按人记录发言间隔，给出"还要不要等"。"""

    def __init__(self, default_pace: float = DEFAULT_PACE,
                 min_quiet: float = MIN_QUIET, history: int = HISTORY):
        self.default_pace = float(default_pace)
        self.min_quiet = float(min_quiet)
        self.history = int(history)
        self._gaps: Dict[str, List[float]] = {}
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()

    # ── 采样 ──

    def note(self, key: str, ts: float) -> Optional[float]:
        """登记一次发言，返回与上一条的间隔（首次返回 None）。"""
        key = str(key or "")
        if not key:
            return None
        ts = float(ts)
        with self._lock:
            last = self._last.get(key)
            self._last[key] = ts
            if last is None:
                return None
            gap = ts - last
            if gap < 0:
                return None
            if 0 < gap <= MAX_SAMPLE:
                arr = self._gaps.setdefault(key, [])
                arr.append(gap)
                if len(arr) > self.history:
                    arr.pop(0)
            return gap

    # ── 读数 ──

    def median_gap(self, key: str) -> float:
        key = str(key or "")
        with self._lock:
            arr = list(self._gaps.get(key) or [])
        if not arr:
            return self.default_pace
        s = sorted(arr)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0

    def quiet_needed(self, key: str) -> float:
        """安静多久算说完。"""
        return max(self.min_quiet, 2.5 * self.median_gap(key))

    def hard_cap(self, key: str) -> float:
        """整批最多等多久（防无限等）。"""
        return max(6.0, 4.0 * self.median_gap(key))

    def forget(self, key: str) -> None:
        key = str(key or "")
        with self._lock:
            self._gaps.pop(key, None)
            self._last.pop(key, None)

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        """调试用：每个人的节奏与需要的安静时间。"""
        with self._lock:
            keys = set(self._gaps) | set(self._last)
        return {k: {"pace": round(self.median_gap(k), 2),
                    "quiet": round(self.quiet_needed(k), 2),
                    "cap": round(self.hard_cap(k), 2)} for k in sorted(keys)}


__all__ = ["SpeechPace", "DEFAULT_PACE", "MIN_QUIET", "MAX_SAMPLE", "HISTORY"]
