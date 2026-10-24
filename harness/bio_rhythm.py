# -*- coding: utf-8 -*-
"""harness/bio_rhythm.py —— Bot 自己的作息（一般精度）。

问题
    `time_ctx` 解决的是"**知道现在几点**"：时段名 + 一句语气提示，喂给模型。
    但它不改变 Bot 的**行为参数** —— 凌晨三点和晚上九点，主动开口的概率
    一模一样。于是出现一个具体现象：`proactive_talk` 的 `quiet_hours`
    默认为空（"不静默"），凌晨也会照 35% 的概率去搭话。

    这不是"语气不对"，是"该睡的时候不睡"。

立场（一般精度，用户 2026-10-24 定）
    一套**通用**的时段作息，不针对某个群/某个人微调，也不做精细到分钟的
    个性化。理由：Bot 不是真的在过人的生活，细化到个人只会得到一条谁也
    验证不了的曲线；而"凌晨少说话、晚上多说话"这种粗规律，方向明确、
    可观测、可回退。

与 time_ctx 的关系（★ 单一权威源）
    时段边界**不在这里定义**，直接复用 `time_ctx._PERIODS`。
    同一个概念（"现在属于哪个时段"）出现两套小时边界，就是这个仓库最高频
    的 bug 形态（见 MEMORY.md「三类两套判据事故」）。这里只给每个时段
    挂三个参数：精神状态 / 主动开口乘子 / 一句人话状态。

三个参数分别影响什么
    proactivity : 乘在 proactive_talk 的 speak_chance 上（**真实行为**）
    energy      : 精神状态 0~1。当前只用于观测与 UI 显示，**不注入 prompt** ——
                  注入会让每小时变一次，作废前缀缓存（那是 64% 命中率的来源，
                  比这点收益值钱）。将来要影响回复长度，走另外的、不破坏缓存的路。
    hint        : 一句人话状态，供日志/UI 显示，不进模型 prompt。

为什么不带"疲劳累积"
    连续聊 N 小时变懒，需要跨请求的状态跟踪（什么时候开始活跃、中间空了多久
    算休息）。那是一套独立的状态机，属于"高精度"范畴。当前只做时段作息，
    需要时再单独加，不混在一个模块里。
"""
import datetime
import time

from . import time_ctx

__all__ = ["bio_state", "proactivity_factor", "energy_of", "BIO_TABLE"]

#: 每个时段的作息参数。键必须是 time_ctx._PERIODS 里出现过的时段名 ——
#: 对不上就在 import 时炸（见模块末尾的自检），不静默失配。
#:   energy       : 精神状态 0~1（观测用）
#:   proactivity  : 主动开口概率乘子。>1 = 比基准更爱说；<1 = 更懒得说
#:   hint         : 一句人话状态（日志/UI 用，不进模型 prompt）
BIO_TABLE = {
    "凌晨": {"energy": 0.15, "proactivity": 0.15,
             "hint": "大部分人在睡觉，没什么好说的"},
    "清晨": {"energy": 0.45, "proactivity": 0.50,
             "hint": "刚醒，话短，懒得展开"},
    "上午": {"energy": 0.80, "proactivity": 0.90,
             "hint": "清醒，正常参与"},
    "中午": {"energy": 0.60, "proactivity": 0.70,
             "hint": "饭后犯困，回复会短一截"},
    "下午": {"energy": 0.85, "proactivity": 1.00,
             "hint": "状态平稳"},
    "傍晚": {"energy": 0.90, "proactivity": 1.10,
             "hint": "放松下来，话开始变多"},
    "晚上": {"energy": 1.00, "proactivity": 1.20,
             "hint": "最活跃的时段，群里人也最多"},
    "深夜": {"energy": 0.40, "proactivity": 0.40,
             "hint": "该睡了，语气软，话少"},
}

#: 乘子上下限。上限必须存在：用户可能把 speak_chance 设到 0.9，
#: 乘以 1.2 会得到 1.08 → 每次心跳都开口，等于把概率闸拆了。
_PROACTIVITY_MAX = 1.25
_PROACTIVITY_MIN = 0.05


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _params(ts):
    """按时段取参数。取不到时段名 → 中性值（不猜、不炸）。"""
    period, _hint = time_ctx.period_of(ts)
    return period, BIO_TABLE.get(period)


def bio_state(ts=None):
    """当前作息状态 → dict。

    返回 {period, energy, proactivity, hint}。
    ts 为 None 用当前时间。时段名不在表里 → 中性值（energy 0.7 / 乘子 1.0）。
    """
    if ts is None:
        ts = time.time()
    period, p = _params(ts)
    if not p:
        return {"period": period, "energy": 0.7, "proactivity": 1.0,
                "hint": "时段未登记，按中性处理"}
    return {"period": period,
            "energy": _clamp(float(p["energy"]), 0.0, 1.0),
            "proactivity": _clamp(float(p["proactivity"]),
                                  _PROACTIVITY_MIN, _PROACTIVITY_MAX),
            "hint": p["hint"]}


def proactivity_factor(ts=None):
    """主动开口概率乘子（已夹在 [_PROACTIVITY_MIN, _PROACTIVITY_MAX]）。"""
    return bio_state(ts)["proactivity"]


def energy_of(ts=None):
    """精神状态 0~1（观测用）。"""
    return bio_state(ts)["energy"]


# ── 自检：时段边界必须与 time_ctx 一致 ────────────────────────────
# 有人在 time_ctx 里加了时段却忘了在这里挂参数，或者反过来，
# 都会让某一小时落到"中性值"分支 —— 不报错，但作息在那一个小时失效。
# 这种静默失配正是这个仓库反复吃亏的形状，所以在 import 时就炸。
_TC_PERIODS = {_row[1] for _row in time_ctx._PERIODS}
_missing = _TC_PERIODS - set(BIO_TABLE)
_extra = set(BIO_TABLE) - _TC_PERIODS
if _missing or _extra:
    raise ImportError(
        "bio_rhythm 的时段表与 time_ctx._PERIODS 不一致："
        "time_ctx 有而这里没有=%s；这里有时 time_ctx 没有=%s。"
        "作息边界只认 time_ctx 一份，改时段请同步改 BIO_TABLE。"
        % (sorted(_missing), sorted(_extra)))
