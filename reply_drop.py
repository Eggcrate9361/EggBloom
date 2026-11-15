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
#: 样本不足时的起始线。它**低于**地板 —— 地板会在后面统一抬上去，
#: 这样"样本不足"和"样本够但 p99 偏小"两条路径收敛到同一条下限，
#: 不会出现"样本少的时候线反而更宽"。
FALLBACK_HARD = 90.0

#: 结构性地板：**任何**合法生成的耗时下限都不得低于它。
#:
#: ★ 2026-10-27 改（真机 18:30 两次全丢）：
#:     18:30:47 [工具轮次] 达到上限 3 轮，收尾       ← 搜到了 11 月那条
#:     18:30:54 [丢弃:过时 51.4s（线 45s）] 本批不发
#:     18:31:18 [丢弃:过时 48.7s（线 45s）] 本批不发
#:
#:   线 45 秒 = 兜底 90 × 0.5。而一次带搜索的回复**结构上**就要 50 秒
#:   （3 轮 × (模型调用 + 搜索) + 收尾）→ 被点名 + 要搜索 = 必丢。
#:
#:   所以线不能低于"合法工作本身的耗时"。
#:
#: ★ 地板为什么是 120 而不是"跟着分布走"：
#:   地板是**兜底**，分布是**校准**。两者都要，但职责不同：
#:     · 分布负责"比平时慢多少才算异常"（p99×1.5）
#:     · 地板负责"再慢也不能算异常，只要还在合法耗时内"
#:   如果地板也跟分布走，那么分布整体变慢时地板跟着变慢 ——
#:   那地板就永远不生效，等于没有。
#:
#:   所以地板是**独立于分布的绝对下限**：它编码的是"一次带工具链的
#:   合法回复最多花多久"这个事实（3 轮工具 + 收尾 ≈ 60 秒，120 秒留一倍余量），
#:   而不是"最近的样本有多慢"。分布往上走时线跟着走（更严），
#:   分布往下走时线被地板托住（不误杀）。两个机制各管一头。
STRUCTURAL_FLOOR = 120.0

#: 分布导出线 = p99 × 这个系数。1.5 倍是给上游抖动的余量，
#: 不是给"再多搜几轮"的余量（那部分由话头翻篇负责）。
P99_MULT = 1.5

#: 被点名时的系数。
#:
#: ★ 2026-10-27 改：**从 0.5 改成 1.0（不收紧）**。
#:   原设计写的是"对方在等，宽容度收窄到一半"。真机证明这是错的：
#:   上面那两次丢弃就是它造成的 —— 而被点名的场景恰恰最该回复。
#:
#:   直觉错在哪：「以为你聋了比晚回更伤」这句话本身没错，
#:   但我把它实现成了"把回复丢掉"—— 而**不回复**才是"以为你聋了"的
#:   最强形式。晚 50 秒回一句准确的答案，对方只会觉得你想了一会儿；
#:   一声不吭，对方只会觉得你挂了。
#:
#:   所以：被点名时**不放宽也不收紧**（1.0）。真要区别对待，
#:   应该区别对待"是否丢弃"，而不是把线压到结构上必然越不过的地方。
ADDRESSED_MULT = 1.0


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

        线 = max(结构性地板, 实测 p99 × 系数)；被点名时再 × ADDRESSED_MULT。

        ★ 结构性地板（2026-10-27）：线**永远不低于** STRUCTURAL_FLOOR。
          带搜索的回复结构上就要 50 秒，线画在 45 秒就等于"必丢"。
          地板保证线只挡真卡死，不参与日常判断。
        """
        p99 = self.p99(group_id)
        if p99 is None:
            base, src = FALLBACK_HARD, "兜底(样本不足)"
        else:
            base, src = p99 * P99_MULT, f"实测p99={p99:.1f}s×{P99_MULT}"
        # ★ 地板：任何线的下限
        if base < STRUCTURAL_FLOOR:
            base = STRUCTURAL_FLOOR
            src = f"{src}→抬到地板{STRUCTURAL_FLOOR:.0f}s"
        if addressed:
            return max(15.0, base * ADDRESSED_MULT), src + f"×{ADDRESSED_MULT}(在等)"
        return base, src


#: 全局单例（主链用）。测试可 new 一个干净的。
LATENCY = LatencyProfile()


#: 1.5.10 C5：新消息超过这个绝对数就**不再丢**，直接发（认了）。
#: 真机压测：8 msg/s × 5–20s 生成 = 40–160 条新消息，比例判据下 12/12 全丢，
#: out=0。"话头翻篇"在高压下退化成"永不回复"，这比发一句略旧的话更糟。
_SEND_ANYWAY_OVER = 30


def drop_reason(*, new_msgs: int = 0, age: float = 0.0, turns: int = 2,
                hard_sec: float = 0.0, addressed: bool = False,
                text: str = "", official_re=None,
                send_anyway_over: int = 0) -> str:
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
    #    2026-10-29（1.5.10 C5）：加绝对上限 —— 新消息超过上限时**发出去**。
    #    理由：高频群里按比例判据永远不会满足"话头没翻"，
    #    结果是每条回复都丢（压测 out=0）。宁可发一句接旧话头的回复，
    #    也不能让 Bot 在热闹的群里彻底哑掉。
    _over = int(send_anyway_over or _SEND_ANYWAY_OVER)
    if turns_i > 0 and new_i >= turns_i:
        if _over > 0 and new_i >= _over:
            return ""
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