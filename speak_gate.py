# -*- coding: utf-8 -*-
"""发言闸：零 token 前置门 + 发言率上限 + 连续自限（1.5.9 §3.8，提交 6）。

## 为什么这一条最值钱

"不回"这件事现在也要花一次模型调用 —— 决策器本身是 AI。而"该不该进决策器"
根本不需要 AI：有没有人叫我、这句里有没有可接的话头、这个群我最近是不是说太多了，
全是代码能判的。

没命中就 `[跳过:档位N]` 落日志、**一次模型都不调**；命中的才进决策器。
方案原话：「这一条比 §3.8 表格里那些阈值更省钱，因为它省的是"没说话的那次调用"。」

## 四档（纯代码）

| 档 | 判据 | 结果 |
|---|---|---|
| 1 | 只有 at_bot / xiao_a / quote_me | 其余一律跳过 |
| 2 | 档 1 + 命中话题关键词表 | 同上 |
| 3（默认） | 档 2 + 概率 `speak_probability`（0.15） | 同上 |
| 4 | 全响应（调试用） | 直接进决策器 |

被点名（`forced`）的批次**在闸之前就返回**，任何频控都拦不住它 ——
"叫它不应"比话多更伤拟人（方案 §3.8 的 ⚠️）。

## 两道频控

· **发言率**：窗口内 `out / in × 100 > speak_rate_max_per_100` → 跳过。
  样本不足（`in < 20`）不判 —— 新群第一句话就"超发"是统计假象。
· **连续自限**：`consecutive_reply_limit` 条之后强制静默 5 分钟（`COOLDOWN_SEC`）。
  有人说话就把计数清零，所以它拦的是"没人理还一直自说自话"。

## 边界

本模块不碰文本、不调模型、不读全局状态：所有输入都由调用方传入（纯函数），
便于单测。窗口取数与"连说计数"在 `bot_core._speak_note` 里维护。
"""

#: 连说限流触发后的静默时长（秒）。方案写"5–10 分钟"，取 5 分钟：
#: 再长会让"群里聊起来了它还不说话"变得可感知。
COOLDOWN_SEC = 300
#: 发言率判定的最小样本：入站消息少于这个数不判（避免新群首句就超发）
RATE_MIN_SAMPLE = 20

#: 档 2 的"话题关键词"表。只放**明显在抛话头**的词：提问、求助、情绪外露。
#: 不放"哈哈/草"这类纯反应词 —— 那些进决策器也只是多花一次调用。
#:
#: ★ 2026-10-28 用 146 条真机非触发消息回测后扩表：原表只覆盖 34%，
#:   漏掉的正是这个群最常见的那类 ——「你好」「那你今年几岁」「我要删了你」
#:   「我恨体育」。判据改成三层（见 `hit_topic`），不是单纯堆词。
_TOPIC_WORDS = (
    "吗", "呢", "什么", "为什么", "为啥", "怎么", "咋", "多少", "哪", "谁",
    "是不是", "能不能", "要不要", "有没有", "如何",
    "有人", "在不在", "来个人", "求", "帮忙", "帮我", "救命",
    "怎么办", "离谱", "无语", "笑死", "好烦", "烦死", "累了", "无聊",
    "睡不着", "吃了吗", "晚安", "早安", "生日快乐",
    # 疑问词补漏（真机："那你今年几岁" / "这啥东西" 都没命中）
    "几岁", "多大", "几个", "哪个", "啥",
    # 问候（真机出现多次）
    "你好", "您好", "在吗", "在么", "嗨",
    # 情绪/关系高信号：这类话是在**对人说**，装看不见比回错更伤
    "恨", "喜欢", "讨厌", "删了", "滚", "傻", "恶心", "气死",
)

#: 第二人称：短句里的"你/您"几乎都是在跟某个具体的人说话（群里就是喊 Bot）。
_ADDRESS_WORDS = ("你", "您")
#: "短句"上限。长句带"你"多半是两个人聊，把整段拉进决策器只会多花钱。
_ADDRESS_MAX_LEN = 14

#: 只用于日志的档位名
_TIER_LABEL = {1: "只认点名", 2: "关键词", 3: "关键词+概率", 4: "全响应"}


def _clamp_int(v, lo, hi, default):
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _clamp_float(v, lo, hi, default):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f))


def get_config(cfg=None):
    cfg = cfg or {}
    return {
        "tier": _clamp_int(cfg.get("speak_gate_tier", 3), 1, 4, 3),
        "probability": _clamp_float(cfg.get("speak_probability", 0.15), 0.0, 1.0, 0.15),
        "max_per_100": _clamp_int(cfg.get("speak_rate_max_per_100", 10), 1, 50, 10),
        "consecutive_limit": _clamp_int(cfg.get("consecutive_reply_limit", 2), 1, 5, 2),
    }


def hit_topic(text):
    """这句里有没有可接的话头（档 2 的判据）。

    三层，先硬后软：
      ① 命中词表（提问/求助/问候/情绪）
      ② 短句里出现第二人称 —— 对 Bot 说话基本都是短句
      ③ 其余算纯旁白
    """
    s = str(text or "").strip()
    if not s:
        return False
    if any(w in s for w in _TOPIC_WORDS):
        return True
    if len(s) <= _ADDRESS_MAX_LEN and any(w in s for w in _ADDRESS_WORDS):
        return True
    return False


def rate_reason(stats, max_per_100):
    """发言率超限 → 返回原因；否则 ''。stats = {"in": n, "out": n}。"""
    try:
        n_in = int(stats.get("in") or 0)
        n_out = int(stats.get("out") or 0)
    except Exception:
        return ""
    if n_in < RATE_MIN_SAMPLE:
        return ""
    ratio = n_out * 100.0 / n_in
    if ratio <= max_per_100:
        return ""
    return f"发言率 {n_out}/{n_in}={ratio:.0f}% > {max_per_100}%"


def consecutive_reason(stats, limit):
    """连说超限且还在冷却 → 返回原因；否则 ''。"""
    try:
        consec = int(stats.get("consecutive") or 0)
        ago = float(stats.get("last_out_ago", -1))
    except Exception:
        return ""
    if consec < limit:
        return ""
    if ago < 0:
        return ""
    if ago >= COOLDOWN_SEC:
        return ""          # 冷却已过，允许再说（随后计数会被重置）
    return f"连说 {consec} 条（{int(ago)}s 前刚说过）"


def allow(batch, cfg=None, *, stats=None, rnd=None, now=None):
    """返回 (是否进决策器, 原因)。

    调用方**必须先判 forced**：被点名的批次不进这个函数。
    这里再兜一层 —— 万一调用方漏了，`forced` 为真仍然放行。
    """
    b = batch or {}
    forced = bool(b.get("forced") or b.get("is_at_bot") or b.get("has_xiao_a")
                  or b.get("has_bot_nick") or b.get("has_text_at")
                  or (b.get("has_quote") and b.get("quote_me"))
                  # 戳一戳是**直接指向 Bot** 的动作（批次上带 source=poke / poke_uid）。
                  # 真机回放发现它原来会被当普通消息挡掉 85% —— 戳了不理，比话多更糟。
                  or b.get("source") == "poke" or bool(b.get("poke_uid")))
    if forced:
        return True, "触发"

    c = get_config(cfg)
    st = stats or {}

    why = rate_reason(st, c["max_per_100"])
    if why:
        return False, why
    why = consecutive_reason(st, c["consecutive_limit"])
    if why:
        return False, why

    tier = c["tier"]
    text = str(b.get("text") or "")
    if tier >= 4:
        return True, "档4-" + _TIER_LABEL[4]
    if tier == 1:
        return False, "档1-" + _TIER_LABEL[1]
    if hit_topic(text):
        return True, f"档{tier}-命中话头"
    if tier == 2:
        return False, "档2-无话头"
    # 档 3：概率放行
    r = (float(rnd) if rnd is not None else __import__("random").random())
    if r < c["probability"]:
        return True, f"档3-概率{r:.2f}"
    return False, f"档3-概率未中({r:.2f})"


def prompt_note(cfg=None):
    """给日志/自检用的一行摘要。"""
    c = get_config(cfg)
    return (f"档位={c['tier']}({_TIER_LABEL[c['tier']]}) 概率={c['probability']} "
            f"上限={c['max_per_100']}/100 连说={c['consecutive_limit']}")
