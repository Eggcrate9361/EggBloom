# -*- coding: utf-8 -*-
"""说话节奏：全动态——用他自己的数据和结果校准（1.5.9）。

## 三版演进

    第一版  固定 3 秒           —— 拍脑袋，换个人就不成立
    第二版  quiet = 2.5 × 中位数 —— 窗口动态了，但 2.5 还是拍的
    第三版  quiet = P90 × 1.15 × 校准倍数  ← 这一版

## 这一版的判据

  · `P90`：他 90% 的连发间隔都在这个值以内。等超过它，就是另起一段。
    分位数自动适应分布，不需要"2.5 倍"这种手工系数。
  · 校准倍数：每次判定"他说完了"之后，观察他多久又开口。
      - 在 `quiet` 的一半之内又说了 → 明显漏判，倍数上调
      - 超过 3 倍 quiet 才说       → 判定正确，倍数缓慢回落
    倍数夹在 [0.6, 3.0]，冷启动 1.0。

## 冷启动

没有样本时给**保守**的 5 秒（不是"设计值"，是"还不知道，先别抢答"）；
有 3 个以上间隔后切到 P90 判据。硬顶同理，冷启动 10 秒。

## 边界

  · 间隔 > 15 秒不进样本（那是"在等回复"，不是连发 —— 2026-10-28 改）
  · 只保留最近 12 个间隔，跟着人变
  · 线程安全：消息线程与 Timer 线程都会读写

不 import bot_core，不做 IO，可单测。
"""
import threading
from typing import Dict, List, Optional

#: 冷启动兜底：还不知道他的节奏时，别抢答
DEFAULT_QUIET = 5.0
DEFAULT_CAP = 10.0
#: 判定说完的最小安静时间
MIN_QUIET = 1.8
#: 超过这个间隔不算连发（秒）
#:
#: ★ 2026-10-28 修：120 → 15。这是 1.5.9 真机事故（私聊回复等 2 分 14 秒）的根因：
#:   对话轮的间隔（对方发一条、等你的回复，20-60s）被当成了**连发间隔**进了样本，
#:   P90 于是被抬到 ~30s → quiet = P90×1.15×倍数 ≈ 70-90s，cap=quiet×4 ≈ 300s+
#:   —— 节奏器把"他在等回复"理解成"他还要说"，于是永远不回。
#:   对方等不到回复只好自问自答，回复生成出来时已经是在复读用户自己给的答案。
#:   真正的连发（一个人把一句话拆几条发）间隔是 1-10 秒；15 秒以上的静默
#:   就是"说完了一句，在等回应"。
MAX_SAMPLE = 15.0
#: 保留最近几个间隔
HISTORY = 12
#: 有这么多间隔之后才切到 P90 判据
MIN_SAMPLES = 3
#: 校准倍数的活动范围
RATIO_MIN = 0.6
RATIO_MAX = 3.0


class SpeechPace:
    """按人记录发言间隔，用分位数 + 结果校准给出"还要不要等"。"""

    def __init__(self, min_quiet: float = MIN_QUIET, history: int = HISTORY,
                 min_samples: int = MIN_SAMPLES,
                 default_quiet: float = DEFAULT_QUIET,
                 default_cap: float = DEFAULT_CAP):
        self.min_quiet = float(min_quiet)
        self.history = int(history)
        self.min_samples = int(min_samples)
        self.default_quiet = float(default_quiet)
        self.default_cap = float(default_cap)
        self._gaps: Dict[str, List[float]] = {}
        self._last: Dict[str, float] = {}
        self._ratio: Dict[str, float] = {}      # 校准倍数
        self._settled: Dict[str, float] = {}    # 上次判定"说完"的时刻
        self._hits: Dict[str, int] = {}         # 漏判次数
        self._miss: Dict[str, int] = {}         # 判定正确次数
        self._lock = threading.Lock()

    # ── 采样 ──

    def note(self, key: str, ts: float) -> Optional[float]:
        """登记一次发言，返回与上一条的间隔（首次返回 None）。

        顺带做校准：如果上次刚判定"他说完了"、他却在 quiet 的一半之内又开口，
        说明判早了 —— 倍数上调。
        """
        key = str(key or "")
        if not key:
            return None
        ts = float(ts)
        with self._lock:
            last = self._last.get(key)
            self._last[key] = ts
            settled = self._settled.pop(key, None)
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
            # ── 结果校准 ──
            if settled is not None:
                q = self._quiet_locked(key)
                if gap <= max(self.min_quiet, q * 0.5):
                    self._ratio[key] = min(RATIO_MAX,
                                            self._ratio.get(key, 1.0) * 1.10)
                    self._hits[key] = self._hits.get(key, 0) + 1
                elif gap >= q * 3:
                    self._ratio[key] = max(RATIO_MIN,
                                           self._ratio.get(key, 1.0) * 0.97)
                    self._miss[key] = self._miss.get(key, 0) + 1
            return gap

    def mark_settled(self, key: str, ts: float) -> None:
        """调用方判定"这批到此为止"时记一笔，下次他开口时用来校准。"""
        key = str(key or "")
        if not key:
            return
        with self._lock:
            self._settled[key] = float(ts)

    # ── 读数 ──

    def _median_locked(self, key: str) -> float:
        """调用方已持锁时用这个。median_gap 在锁内调它会死锁（锁不可重入）。"""
        arr = self._gaps.get(key) or []
        if not arr:
            return self.default_quiet
        s = sorted(arr)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0

    def median_gap(self, key: str) -> float:
        key = str(key or "")
        with self._lock:
            return self._median_locked(key)

    def _p90_locked(self, key: str) -> Optional[float]:
        arr = sorted(self._gaps.get(key) or [])
        if len(arr) < self.min_samples:
            return None
        idx = min(len(arr) - 1, int(round(0.9 * (len(arr) - 1))))
        return arr[idx]

    def _quiet_locked(self, key: str) -> float:
        """三个判据取最大，不用单一分位。

        单用 P90 会被"段内停顿"骗：实测那批间隔是 [0,2,2,1,2]，P90=2，
        quiet 算出来 2.3，而 38 秒那条距上一条正好 2 秒 —— 差 0.3 秒就越界，
        整段被切成两半。段内停顿与段间停顿在分布上是重叠的，靠一个分位
        分不开，所以三个都看：
          · P90 × 1.5   ：覆盖大部分连发间隔
          · median × 2.5：慢一点的人不至于被抢答
          · 3.0 秒下限  ：再快的人也要给一点反应时间
        取最大 = 宁可多等，也不要把他一段话切成几批。
        """
        p90 = self._p90_locked(key)
        if p90 is None:
            return self.default_quiet
        med = self._median_locked(key)
        base = max(3.0, p90 * 1.5, med * 2.5)
        return max(self.min_quiet, base * self._ratio.get(key, 1.0))

    def quiet_needed(self, key: str) -> float:
        """安静多久算他说完。有样本走 P90，没样本用保守兜底。"""
        key = str(key or "")
        with self._lock:
            return self._quiet_locked(key)

    def hard_cap(self, key: str) -> float:
        """整批最多等多久（防无限等）。跟着 quiet 走，不另设一套系数。"""
        key = str(key or "")
        with self._lock:
            q = self._quiet_locked(key)
            if self._p90_locked(key) is None:
                return self.default_cap
            # 4 倍 quiet：一整段话（几条到十几条）要能收进来。
            # 2 倍会把「说 6 秒、中间停 2 秒」的一段话切成两半。
            return max(8.0, q * 4.0)

    def ratio(self, key: str) -> float:
        key = str(key or "")
        with self._lock:
            return self._ratio.get(key, 1.0)

    def forget(self, key: str) -> None:
        key = str(key or "")
        with self._lock:
            for d in (self._gaps, self._last, self._ratio, self._settled,
                      self._hits, self._miss):
                d.pop(key, None)

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        """调试用：每个人的节奏、倍数、需要的安静时间。"""
        with self._lock:
            keys = set(self._gaps) | set(self._last) | set(self._ratio)
        return {k: {"pace": round(self.median_gap(k), 2),
                    "quiet": round(self.quiet_needed(k), 2),
                    "cap": round(self.hard_cap(k), 2),
                    "ratio": round(self.ratio(k), 2),
                    "samples": len(self._gaps.get(k) or [])}
                for k in sorted(keys)}


__all__ = ["SpeechPace", "DEFAULT_QUIET", "DEFAULT_CAP", "MIN_QUIET",
           "MAX_SAMPLE", "HISTORY", "MIN_SAMPLES", "RATIO_MIN", "RATIO_MAX"]
