# -*- coding: utf-8 -*-
"""persona 四参数 → 形式参数（1.5.9 §5.2 / 提交 11）。

方案的口径：**人格 = 长期系数，情绪 = 短期偏移，两者相加作用到同一套形式参数。**
这个模块只负责"人格那一半"，叠在 `mood_engine.get_form_params()` 的结果上，
不新建第二套结构。

| 参数 | 含义 | 默认 | 影响的出口（本模块） |
|---|---|---|---|
| `persona_sharpness`     | 嘴毒程度 | **0.75** | 语气词池分档 + 句首语气词概率 |
| `persona_talkativeness` | 话多程度 | 0.00 | 不在本职（`bot_core._budget_shift` 管配额档位） |
| `persona_curiosity`     | 好奇程度 | **0.55** | 陈述句上的追问概率 |
| `persona_warmth`        | 温度     | **0.35** | 表情概率 + 句末软化 |

## 值域与标定

四个都是 **0–1**（方案 §5.2 明确"四个 0–1 的浮点"）。0 不是"关"，是"这一维最低"：
  · sharpness 0 → 语气池走 low（哦/…），不注入语气词
  · curiosity 0 → 不追问
  · warmth    0 → 不加表情概率、不软化句末

默认值按方案给的 0.75 / 0.55 / 0.35 落地。标定原则：**默认要能感觉到，但不能成为噪音** ——

```
语气词   0.18 × sharpness = 0.135   每 7~8 条一次（真机回测后从 0.30 降下来）
追问     0.30 × curiosity = 0.165   上限；真机上只有"叙事陈述"才触发，
                                 实测 217 条里仅 0.2% 会加尾巴（多数是判断句，不该追）
表情加成 0.20 × warmth    = 0.070   只在"没检测到情绪"时当兜底概率
句末软化 (0.5 + warmth) × 0.35 = 0.30，且只在情绪判"该软"时才有机会
```

## 不做的事

· 不碰语义：本模块只改句末标点、句首语气词、是否追加一句短问。
· 不重复实现 talkativeness：它已经有消费点（`_budget_shift`），照方案留在那里。
· 不给 sharpness 加"反问概率"：方案 §3.6 正在压问句率（目标 ≤15%），
  再把"更毒 → 更多反问"接上，等于两条目标互相拆台。留给后续版本。
"""

import random

#: sharpness 分档阈值（0–1）
SHARP_HIGH = 0.66
SHARP_LOW = 0.33
#: sharpness 低于这个值就不注入句首语气词（低毒 = 不咋咋呼呼）
FILLER_MIN = 0.5
#: 句首语气词概率 = FILLER_BASE × sharpness。
#: ★ 2026-10-28 真机回测（217 条真实回复）后从 0.30 降到 0.18：
#:   0.30 对应默认 0.75 → 22.5%，实测每 4 条就有 1 条被硬塞前缀，
#:   而原来的池子里「我说」「得」放在句首经常读不通（"我说，记不太清了啊"）。
FILLER_BASE = 0.18
#: 追问概率 = FOLLOWUP_BASE × curiosity
FOLLOWUP_BASE = 0.30
#: 表情加成 = EMOJI_BASE × warmth（叠加在情绪给的那份上）
EMOJI_BASE = 0.20
#: 句末软化的基础概率；实际概率 = SOFT_BASE × (0.5 + warmth)
SOFT_BASE = 0.35
#: 追问只对"够长的**叙事**陈述"生效。
#: ★ 2026-10-28 用 217 条真机回复回测后收紧：原来只要求"陈述 + ≥6 字"，
#:   结果「铁路学院啊，还行吧」「记不太清了啊」「图片我打不开啊」这类
#:   **判断/答复**也被追一句「后来呢」—— 读起来是纯噪音。
#:   现在要求触发里带叙事标记（了/昨天/刚才/然后/…）且够长，且回复是单段。
FOLLOWUP_MIN_TRIGGER = 12
#: 叙事标记：有"发生了什么"的意思才谈得上"后来呢"
_NARRATIVE_WORDS = ("了", "昨天", "今天", "刚才", "刚刚", "然后", "后来",
                    "结果", "本来", "一开始", "早上", "晚上", "上周", "那次")

#: 句首语气词分档：high = 外放/带刺，mid = 常态，low = 冷淡
_FILLER_POOLS = {
    # 只留"放句首一定读得通"的词。真机样例里「我说，记不太清了啊」是硬伤，
    # 「得，」「嚯，」在书面群里像在演相声，已从 high 池移除。
    "high": ("啧", "行吧", "嚯"),
    "mid": ("嗯", "哦", "那个"),
    "low": ("哦", "…", "嗯"),
}

#: 追问池：只放**对任何话题都成立**的短问句，避免驴唇不对马嘴
_FOLLOWUPS = ("然后呢", "真的假的", "你怎么想", "后来呢", "所以呢")

#: 陈述句判定：带问号/疑问词的不追（已经在问了，不需要再追）
_Q_MARKS = ("?", "？", "吗", "呢", "什么", "为什么", "为啥", "怎么", "咋",
            "多少", "几点", "哪", "谁", "是不是", "能不能", "要不要", "有没有")


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _unit(v, default=0.0):
    """夹到 0–1。"""
    return max(0.0, min(1.0, _num(v, default)))


def get_config(cfg=None):
    """读三个系数（主配置顶层，缺省取方案默认值）。"""
    cfg = cfg or {}
    return {
        "sharpness": _unit(cfg.get("persona_sharpness", 0.75), 0.75),
        "curiosity": _unit(cfg.get("persona_curiosity", 0.55), 0.55),
        "warmth": _unit(cfg.get("persona_warmth", 0.35), 0.35),
    }


def apply(form, cfg=None):
    """把人格偏移叠进形式参数，返回**新的** dict（不改调用方那份）。

    只加键、不删键：`budget_shift` / `delay_scale` / `valence` 原样穿过。
    """
    f = dict(form or {})
    c = get_config(cfg)

    f["emoji_bonus"] = round(
        float(f.get("emoji_bonus") or 0.0) + EMOJI_BASE * c["warmth"], 3)
    f["soft_punct"] = bool(f.get("soft_punct")) or c["warmth"] >= 0.5
    f["soft_prob"] = round(min(1.0, SOFT_BASE * (0.5 + c["warmth"])), 3)

    if c["sharpness"] >= SHARP_HIGH:
        f["filler_pool"] = "high"
    elif c["sharpness"] <= SHARP_LOW:
        f["filler_pool"] = "low"
    f["filler_prob"] = round(
        FILLER_BASE * c["sharpness"] if c["sharpness"] >= FILLER_MIN else 0.0, 3)

    f["followup_prob"] = round(FOLLOWUP_BASE * c["curiosity"], 3)
    return f


def is_statement(text):
    """这句是不是陈述（没有问号、没有疑问词）。"""
    s = str(text or "")
    return bool(s.strip()) and not any(m in s for m in _Q_MARKS)


def _pick(seq, r):
    if not seq:
        return ""
    i = int(float(r) * len(seq))
    return seq[min(max(i, 0), len(seq) - 1)]


def decorate(text, form, trigger="", rng=None):
    """按形式参数做**形式**微调。三个概率都为 0 时等于原样返回。"""
    s = str(text or "")
    if not s.strip():
        return s
    f = form or {}
    rnd = random.random if rng is None else (lambda: float(rng))

    # ① 句末软化（情绪判"该软"或 warmth 高时才可能发生）
    if f.get("soft_punct") and s.rstrip().endswith("。"):
        if rnd() < _num(f.get("soft_prob"), 0.0):
            s = s.rstrip()[:-1] + "～"

    # ② 句首语气词
    pool = _FILLER_POOLS.get(str(f.get("filler_pool") or "mid"))
    if pool and _num(f.get("filler_prob"), 0.0) > 0:
        if rnd() < _num(f.get("filler_prob"), 0.0):
            _p = _pick(pool, rnd())
            # 语气词后面补逗号："啧吃了"像打字错误，"啧，吃了"才是人说话；
            # "…" 不需要（省略号本身带停顿）。
            s = f"{_p}{s}" if _p.endswith(("…", "，")) else f"{_p}，{s}"

    # ③ 追问（只在"对方在讲一段事、我这条也是单段陈述"时）
    p = _num(f.get("followup_prob"), 0.0)
    _trig = str(trigger or "").strip()
    if (p > 0 and is_statement(trigger)
            and len(_trig) >= FOLLOWUP_MIN_TRIGGER
            and any(w in _trig for w in _NARRATIVE_WORDS)
            and "\n" not in s
            and not any(m in s for m in ("?", "？")) and rnd() < p):
        s = f"{s.rstrip('。')}，{_pick(_FOLLOWUPS, rnd())}"
    return s
