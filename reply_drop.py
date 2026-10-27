# -*- coding: utf-8 -*-
"""回复该不该丢：按**这次生成有多慢**判，不按固定秒数判（1.5.9 §B5 修订）

## 为什么改（2026-10-27 真机，12 次丢弃全部统计）

```
12 次丢弃：过时 11 / 话头翻篇 1
过时值：26.0 29.0 29.4 30.8 31.1 31.1 38.1 49.4 55.1 59.6 64.7
gen_send 分布（n=86）：p50=0.1  p90=14.2  p95=18.6  max=26.5
```

所有被丢的回复都落在 **26–65 秒**，而配置里 `reply_stale_drop_sec=25`。
两者不是巧合：**25 秒这条线画在了搜索链的必经路径上。**

一次带搜索的回复 = 3 轮工具 × (模型调用 + 搜索) + 收尾调用，
结构上就不可能低于 25 秒。所以阈值不是"偶尔误伤"，
而是"凡搜索必丢" —— 用户看到的「所有调用搜索工具的问题都无法回复」
就是这么来的。

## 判据改什么

旧判据是**绝对时长**：`age > hard_sec` → 丢。
它把两件不同的事混成一个数：

  ① 等批 + 排队（模型还没开始干）
  ② 真的在生成（模型在干活，即将产出）

这两段在同一个 `age` 里，而你无法用"多久"区分"卡住了"和"正在多轮搜索"。

新判据只问一个问题：**这条回复现在发出去，还接得上话吗？**

  · 话头翻篇（有新消息盖过去）→ 丢。这是主判据，与耗时无关。
  · 群里一直没新消息 → **不丢**。没有人被这条回复打扰，
    晚 60 秒的回复仍然是这个群当前唯一在推进的话题。
  · 时间只做**极端兜底**：防"生成卡死十分钟"。线由实测 p99 定，
    不是拍的 —— 见 `hard_line()`。

一句话：**没人说话的时候，晚回不算过时。**

## 唯一例外：有人明确在等

如果这轮是被 @ / 点名触发的，对方就在等你的答话。
那时"晚"是有代价的（对方以为你聋了），所以给一条更紧的线。
但它同样是分布导出的，不是写死的。
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

#: 至少要有这么多条实测样本，才用自己的分布定线；否则用兜底线。
MIN_SAMPLES = 20

#: 兜底线（拿不到分布时用）。按实测 p95≈18.6s 的 4 倍留余量 ——
#: 只为挡"卡死"，不参与日常判断。
FALLBACK_HARD = 90.0

#: 分布导出线 = p99 × 这个系数。1.5 倍是给上游抖动的余量，
#: 不是给"再多搜几轮"的余量（那部分由话头翻篇负责）。
P99_MULT = 1.5

#: 被点名时的系数：对方在等，宽容度收窄到一半。
ADDRESSED_MULT = 0.5


def _pct(sorted_vals: List[float], q: float) -> float:
    """线性插值分位数。空列表由调用方保证不传。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return float(sorted_vals[lo]) * (1 - frac) + float(sorted_vals[hi]) * frac


class LatencyProfile:
    """记录"生成完成时距批次第一条"的实测分布，据此给出丢弃线。

    为什么需要它而不是一个常数：这条线应该跟着**模型和工具链的速度**走。
    换快模型、关掉工具轮、上游变慢 —— 任何一种都会让分布整体平移，
    而常数不会跟着动。真机第一次就踩了：阈值定 25，实际 p90 是 14、
    搜索链要到 40+，常数等于把搜索链整体判死。

    ★ 按群分组统计（2026-10-27 晚）。原先所有群共用一个池子，实测发现
      测试脚本 import bot_core 后会往同一个全局单例里灌样本
      （群号 `_test_poke_pipeline_` 之类，age 0.1–5.2 秒），
      把真实群的分布整个带偏 —— 真实群的慢样本被测试的快样本稀释。
      现在按群分桶，线只看**真实群**的样本。

    ★ 合成群号过滤：群号以 `_test_` / `test_` 开头的是测试造的，
      一律不进池子。这不是"猜测试群"，是"测试自己声明的命名"。

    线程安全：主链在 Timer 线程记、等待线程读。
    """

    #: 测试群号前缀 —— 这些群的样本不进分布池
    TEST_PREFIXES = ("_test_", "test_")

    def __init__(self, history: int = 200):
        self._lock = threading.Lock()
        self._vals: List[float] = []
        self._history = max(20, int(history))
        #: 分场景：被点名 / 未被点名。两者的容忍度不同，混在一起会互相污染。
        self._vals_addressed: List[float] = []
        #: 按群分桶 {gid: [age, ...]}（只存真实群）
        self._by_group: Dict[str, List[float]] = {}

    @classmethod
    def is_test_group(cls, gid: str) -> bool:
        g = str(gid or "")
        return g.startswith(cls.TEST_PREFIXES)

    def note(self, age: float, *, addressed: bool = False,
             group_id: str = "") -> None:
        """记一条"这次生成花了多久"。非正值忽略（评测/桩调用会传 0）。

        `group_id` 给了就按群分桶；测试群（`_test_*`）直接忽略。
        """
        try:
            v = float(age)
        except Exception:
            return
        if v <= 0 or v > 3600:
            return
        gid = str(group_id or "")
        if gid and self.is_test_group(gid):
            return
        with self._lock:
            self._vals.append(v)
            if len(self._vals) > self._history:
                self._vals = self._vals[-self._history:]
            if addressed:
                self._vals_addressed.append(v)
                if len(self._vals_addressed) > self._history:
                    self._vals_addressed = self._vals_addressed[-self._history:]
            if gid:
                bucket = self._by_group.setdefault(gid, [])
                bucket.append(v)
                if len(bucket) > self._history:
                    del bucket[:-self._history]

    def p99(self, group_id: str = "") -> Optional[float]:
        """该群的 p99（不给群号则用全局池）。

        按群取优先：不同群的生成速度差很多 —— 活跃群里模型被顶得多、
        冷群里没人抢机器。全局池会把两边的慢样本混在一起，
        冷群的线被活跃群拉宽，而冷群恰恰是最容易"发出去也没人接"的。
        群样本不够时回退全局池，再不够回退兜底线。
        """
        with self._lock:
            if group_id:
                bucket = list(self._by_group.get(str(group_id)) or [])
                if len(bucket) >= MIN_SAMPLES:
                    return _pct(sorted(bucket), 0.99)
            vals = sorted(self._vals)
        if len(vals) < MIN_SAMPLES:
            return None
        return _pct(vals, 0.99)

    def stats(self, group_id: str = "") -> dict:
        with self._lock:
            vals = sorted(self._vals)
            addressed = sorted(self._vals_addressed)
            bucket = sorted(self._by_group.get(str(group_id)) or []) if group_id else []
        if not vals:
            return {"n": 0}
        out = {
            "n": len(vals),
            "p50": round(_pct(vals, 0.5), 1),
            "p90": round(_pct(vals, 0.9), 1),
            "p99": round(_pct(vals, 0.99), 1),
            "max": round(vals[-1], 1),
            "addressed_n": len(addressed),
        }
        if bucket:
            out["gid_n"] = len(bucket)
            out["gid_p99"] = round(_pct(bucket, 0.99), 1)
        return out

    def hard_line(self, *, addressed: bool = False, group_id: str = "") -> tuple:
        """当前该用的兜底线，返回 (秒, 来源说明)。

        样本够 → 用自己分布的 p99 × 1.5；不够 → FALLBACK_HARD。
        被点名时再 × ADDRESSED_MULT。
        """
        p99 = self.p99(group_id)
        if p99 is None:
            base, src = FALLBACK_HARD, "兜底(样本不足)"
        else:
            base, src = p99 * P99_MULT, f"实测p99={p99:.1f}s×{P99_MULT}"
        if addressed:
            return max(15.0, base * ADDRESSED_MULT), src + f"×{ADDRESSED_MULT}(在等)"
        return base, src


#: 全局单例（主链用）。测试可 new 一个干净的。
LATENCY = LatencyProfile()


def drop_reason(*, new_msgs: int = 0, age: float = 0.0, turns: int = 2,
                hard_sec: float = 0.0, addressed: bool = False,
                text: str = "", official_re=None) -> str:
    """这条回复该不该整条丢。返回原因（空串 = 照发）。

    判据顺序（先问最要紧的）：

      ① 话头翻篇 —— 生成期间群里盖过去 >= turns 条，发出去接的是旧话头。
         这是**主判据**，与耗时无关。
      ② 官腔 —— 命中就丢，与时间无关。
      ③ 极端兜底 —— 只有超过 hard_sec（分布导出）才丢。

    ★ 与旧版的差别（关键）：旧版在 ③ 之前用固定秒数拦，
      而"群里没人说话"这件事从来没被考虑。没人说话的时候，
      晚 60 秒的回复不打断任何人 —— 它就是当前话题唯一的推进。

    `hard_sec<=0` 表示调用方不要时间兜底（只靠 ①②）。
    这是**合法配置**，不是"关掉保护"：①② 仍在工作。
    """
    try:
        turns_i = int(turns or 0)
    except Exception:
        turns_i = 0
    try:
        new_i = int(new_msgs or 0)
    except Exception:
        new_i = 0
    # ① 话头翻篇
    if turns_i > 0 and new_i >= turns_i:
        return f"话头翻篇 {new_i} 条新消息"
    # ② 官腔
    if text and official_re is not None:
        try:
            if official_re.search(text):
                return "官腔"
        except Exception:
            pass
    # ③ 极端兜底
    try:
        hard = float(hard_sec or 0)
    except Exception:
        hard = 0.0
    if hard > 0 and float(age) > hard:
        return f"过时 {float(age):.1f}s（线 {hard:.0f}s）"
    return ""