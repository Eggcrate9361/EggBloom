# -*- coding: utf-8 -*-
"""回复预算：按分布下发，不是给上限（1.5.9 §B3）。

## 为什么不用"写短一点"

这条形容词在 prompt 里存在过三个版本，长度从来没被管住。而决策器里那条
**数字**约束（`followup_max_chars=40`）管住了。结论：给数字，不给形容词。

## 三档

    A 60%   1 条    ≤8 字    总 ≤8
    B 30%   1–2 条  ≤16 字   总 ≤32
    C 10%   2–3 条  ≤40 字   总 ≤80

硬上限只兜底：单条 16 / 3 条 / 总 80。超了不是截断 —— 240 字截断会产出半句，
比长更假。调用方应当带「N 字超上限，重写」重生一次；仍超就用 `fit()` 取最短一条。

## 下限：短不等于空

只压上限会退化成「哦/嗯/啊」。`anchor_ok()` 要求回复与触发消息至少共享一个
2-gram，或长度 ≥8 —— 否则也重生。

不 import bot_core，不做 IO，可单测。
"""
import random
import re
from typing import Dict, List, Optional, Tuple

#: (档名, 概率, 最少条, 最多条, 单条上限, 总上限)
BUDGETS: Tuple[Tuple[str, float, int, int, int, int], ...] = (
    ("A", 0.60, 1, 1, 8, 8),
    ("B", 0.30, 1, 2, 16, 32),
    ("C", 0.10, 2, 3, 40, 80),
)

#: 档 C（长句档）的默认概率。配置项 `reply_long_ratio` 覆盖它（0–0.5）。
DEFAULT_LONG_RATIO = BUDGETS[-1][1]

#: 硬上限兜底（配置项同名）
HARD_MAX_CHARS = 16
HARD_MAX_PARTS = 3
HARD_MAX_TOTAL = 80

#: 表情标记不算字数 —— 「[表情:笑]」是控制指令，不是给用户看的文本
_MEME_TAG = re.compile(r"\[表情[:：][^\]]*\]")
#: 结构标记同理（引用、图片描述）
_MARKUP = re.compile(r"\[(图片|引用|搜索|不说话)[^\]]*\]")


def weights(long_ratio: Optional[float] = None) -> Tuple[float, float, float]:
    """三档权重。`long_ratio` 只换档 C 的权重，A:B 比例保持不变。

    1.5.9 §3.3：`reply_long_ratio` 就是"抽到长句档的概率"（默认 0.1，0–0.5）。
    之前它被硬编码在 BUDGETS 里，配置项并不存在 —— 属于"配了也不生效"。
    """
    try:
        c = float(long_ratio) if long_ratio is not None else DEFAULT_LONG_RATIO
    except (TypeError, ValueError):
        c = DEFAULT_LONG_RATIO
    c = max(0.0, min(0.5, c))
    a0, b0 = float(BUDGETS[0][1]), float(BUDGETS[1][1])
    rest = 1.0 - c
    scale = a0 + b0
    # round：不取整会出现 0.6000000000000001，把 `rng=0.60` 判回 A 档
    # （边界值必须精确落在 0.6 / 0.9 上，否则"抽签分布"对不上文档）
    return (round(rest * a0 / scale, 6), round(rest * b0 / scale, 6), round(c, 6))


def pick(rng: Optional[float] = None, shift: int = 0,
         long_ratio: Optional[float] = None) -> Dict:
    """按分布抽一档。传入 rng 便于测试复现。

    shift：情绪兴奋度给的偏移（+1 上移一档 / -1 下移）。夹在首尾之间，
    不会因为一次激动就突破 A 档 —— 档位是分布，不是滑杆。
    long_ratio：档 C 的目标概率（见 `weights()`）。
    """
    r = random.random() if rng is None else float(rng)
    w = weights(long_ratio)
    acc = 0.0
    idx = len(BUDGETS) - 1
    for i, _item in enumerate(BUDGETS):
        acc += w[i]
        if r < acc:
            idx = i
            break
    idx = max(0, min(len(BUDGETS) - 1, idx + int(shift or 0)))
    name, prob, lo, hi, per, total = BUDGETS[idx]
    return {"name": name, "parts_min": lo, "parts_max": hi,
            "max_chars": per, "max_total": total}


def prompt_line(b: Dict) -> str:
    """给模型的话术。数字 + 禁止项，一句话说完。"""
    lo, hi = int(b.get("parts_min") or 1), int(b.get("parts_max") or 1)
    per = int(b.get("max_chars") or HARD_MAX_CHARS)
    _n = f"{lo} 条" if lo == hi else f"{lo}–{hi} 条"
    _tail = "不要解释，不要总结。"
    if b.get("name") == "A":
        _tail = "不要问，直接说完。不要解释，不要总结。"
    return f"这次最多说 {_n}，每条不超过 {per} 个字。{_tail}"


def visible(text: str) -> str:
    """去掉控制标记后的可见文本。

    ★ 2026-10-27：连**行首的上下文标记**一起剥（时间戳/说话人标签）。
      模型会从输入里模仿这些标记（真机："[10-27 18:55] 记不住…"），
      它们不是内容，不该计入字数、也不该影响"完整句"的判断。
    """
    t = _MEME_TAG.sub("", str(text or ""))
    t = _MARKUP.sub("", t)
    t = strip_ctx_marks(t)
    return re.sub(r"\s+", "", t)


def parts(text: str) -> List[str]:
    """按行切条，丢掉空行。"""
    return [ln.strip() for ln in str(text or "").split("\n") if ln.strip()]


#: 行首的上下文标记（模型从输入里模仿来的）：
#:   [18:46] / [10-27 18:55] / [名字] / [名字·4位锚]
_CTX_MARK = re.compile(
    r"^\s*\[(?:\d{1,2}:\d{2}(?::\d{2})?"          # 时间 [18:46] / [18:46:16]
    r"|\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?"  # 日期+时间 [10-27 18:55]
    r"|[^\[\]]{1,24}(?:·[0-9a-z]{4})?)"            # 说话人 [名字] / [名字·锚]
    r"\]\s*"
)

#: 2026-10-30（P2-2）：时间锚换成**不带方括号**的写法（`10-30 11:40｜`）。
#: 带方括号时模型会把它当"回复该有的格式"学走（实测 33/70 条回复行首都抄了），
#: 只能靠事后剥。换了写法之后照抄的概率大幅下降；这条正则留下来兜旧格式的嘴。
_CTX_MARK_PLAIN = re.compile(
    # 10-30 11:40｜ 或 21:12｜（2026-10-30 真机：模型只抄了钟点）
    r"^\s*(?:\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?"
    r"|\d{1,2}:\d{2}(?::\d{2})?)\s*[｜|]\s*"
)


def strip_ctx_marks(text: str) -> str:
    """剥掉行首的上下文标记。

    ★ 2026-10-27 真机：模型把输入里的 `[10-27 18:55]` 抄进了回复正文
      （"记不住，我又不是录音机" 变成 "[10-27 18:55] 记不住…"）。
      这个标记既不是内容，也不该参与"这句话完整吗""多长"的判断 ——
      不剥的话 `visible()` 去掉空格后变成 "[10-2718:55]记不住…"，
      结尾落在"机"上 → 被判成半截 → 优先级降到最低。

    ★ 2026-10-30（P2-2）：根因是**写法**——方括号看起来像格式。时间锚
      改成 `10-30 11:40｜`（不带括号、带分隔符），同时这里仍兼容旧写法。

    只剥**行首**；行中间的不动（"我在[18:46]说的"是正文）。

    一行最多剥两轮（时间戳 + 说话人标签），避免把正文里的方括号也吃掉。
    """
    out = []
    for ln in str(text or "").split("\n"):
        _l = ln
        for _ in range(2):
            _new = _CTX_MARK.sub("", _l, count=1)
            _new = _CTX_MARK_PLAIN.sub("", _new, count=1)
            if _new == _l:
                break
            _l = _new
        out.append(_l)
    return "\n".join(out)


def check(text: str, b: Dict, hard: Optional[Dict] = None) -> Tuple[bool, str, Dict]:
    """返回 (是否合规, 原因, 统计)。

    先按档位判，再用硬上限兜底 —— 档位是目标，硬上限是底线。
    """
    hard = hard or {}
    max_chars = min(int(b.get("max_chars") or HARD_MAX_CHARS),
                    int(hard.get("max_chars") or HARD_MAX_CHARS))
    max_parts = min(int(b.get("parts_max") or HARD_MAX_PARTS),
                    int(hard.get("max_parts") or HARD_MAX_PARTS))
    max_total = min(int(b.get("max_total") or HARD_MAX_TOTAL),
                    int(hard.get("max_total") or HARD_MAX_TOTAL))

    ps = parts(text)
    vis = [visible(p) for p in ps]
    stats = {"parts": len(ps), "chars": [len(v) for v in vis],
             "total": sum(len(v) for v in vis)}

    if not vis or stats["total"] == 0:
        return (False, "空回复", stats)
    if stats["parts"] > max_parts:
        return (False, f"{stats['parts']} 条超过 {max_parts} 条", stats)
    if stats["total"] > max_total:
        return (False, f"共 {stats['total']} 字超过 {max_total}", stats)
    over = [i for i, n in enumerate(stats["chars"]) if n > max_chars]
    if over:
        return (False, f"第 {over[0] + 1} 条 {stats['chars'][over[0]]} 字超过 {max_chars}",
                stats)
    return (True, "", stats)


def _is_complete_sentence(p: str) -> bool:
    """这条能独立成句吗（不是半截、不是清单碎片）。

    判据：**不以连接符收尾**就算完整。

    ★ 2026-10-27 改。原来要求"结尾必须有句末标点"，真机证明太严：
        "记不住，我又不是录音机"（无句号）→ 被判半截 → 优先级降到最低
        → 选中的反而是敷衍那句。

    QQ 上真人说话常常不打句号，而"半截"的特征是**连接符收尾**
    （逗号/顿号/冒号/破折号 —— 明显话没说完）。
    """
    t = visible(p)
    if not t:
        return False
    # 连接符收尾 = 半截
    if t.endswith(("，", ",", "、", "；", ";", "：", ":", "…", "-", "—",
                   "（", "(", "《", "“", "\"")):
        return False
    return True


def fit(text: str, b: Dict, trigger: str = "") -> str:
    """超配额时的兜底：从已生成的几条里挑一条。

    ★ 2026-10-27 改判据：原来取**最短**的一条，真机证明是错的：

        模型给了 2 条共 33 字（超 B 档 32 字）：
          "你问点能查的行不行？"   (23 字)  ← 敷衍
          "[10-27 18:55] 记不住，我又不是录音机" (10 字)  ← 正面回答
        旧版取最短 → 发了那句敷衍的，把**有内容**的扔了。

        「最短」和「最该发」根本不是一回事 —— 短的常常是空的
        （「嗯」「行吧」都是短的）。

        新判据（按优先级，先命中先得）：
          ① **回应了触发消息**（与触发共享 2-gram，或本身够长）
             —— 最强信号：问的是"我一天前说过什么"，得有个接得住的答案
          ② 能独立成句（不以连接符收尾）
          ③ 有实质内容（不是纯语气词）
          ④ 兜底：最后一条（通常是最重要的结论）
        同级内取**较长**的 —— 长才有信息量。
    """
    ps = parts(text)
    if not ps:
        return ""
    # ★ 剥掉行首的上下文标记后再挑 —— 标记不是内容，
    #   留着会让"多长"的判断算上它（真机："[10-27 18:55] 记不住…"）。
    ps = [strip_ctx_marks(p).strip() for p in ps]
    ps = [p for p in ps if p]
    if not ps:
        return ""
    # ① 回应触发消息的优先
    if trigger and str(trigger).strip():
        _anchored = [p for p in ps if anchor_ok(p, trigger, min_chars=8)]
        if _anchored:
            return max(_anchored, key=lambda p: (len(visible(p)), ps.index(p)))
    # ② 完整句
    complete = [p for p in ps if _is_complete_sentence(p)]
    pool = complete or ps
    # ③ 有实义吗（不是纯语气词/标点）
    _substantive = [p for p in pool if len(visible(p).strip("。，！？、~… ")) >= 4]
    pool = _substantive or pool
    # 同级取较长者；长度相同时取最后一条（通常是结论）
    best = max(pool, key=lambda p: (len(visible(p)), ps.index(p)))
    return best


def _bigrams(s: str) -> set:
    t = visible(s)
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def anchor_ok(reply: str, trigger: str, *, min_chars: int = 5) -> bool:
    """下限检查：短不等于空。

    与触发消息共享至少一个 2-gram，或长度 ≥ min_chars。
    避免只压上限后退化成「哦 / 嗯 / 啊」。

    min_chars 从 8 降到 5（2026-10-27 真机）：7 个字的「能，把内容给我」
    被判成"锚点不足"。判据要挡的是「哦/嗯/行吧」，不是完整短句。
    """
    t = visible(reply)
    if len(t) >= min_chars:
        return True
    return bool(_bigrams(reply) & _bigrams(trigger))


def regen_hint(reason: str, b: Dict) -> str:
    """重生时追加的一句。说清超了什么，别只说"重写"。"""
    return f"（上一条{reason}，重写：最多 {b.get('parts_max', 1)} 条，每条 {b.get('max_chars', 8)} 字以内）"


#: 生病时最多压到这么短 —— 再短就不是人话了（"行"已经是极限）。
FACTOR_MIN_CHARS = 6
FACTOR_MIN_TOTAL = 8


def apply_factors(b: Dict, factors: Optional[Dict] = None) -> Dict:
    """把「状态」给的形式参数叠到档位上（1.5.10 F4 尾项，2026-10-30）。

    factors 来自 `plugins/vitals/core.form_params()`：

        max_chars_factor   0.35–1.0   生病 → 每条更短
        max_parts_factor   0.3–1.0    生病 → 条数更少

    ★ 只做乘法、**不换档**：档位是 A/B/C 的分布（多常说话），换档会改分布；
      压上限只改"这次说多长"。两件事不能混。
    ★ 系数夹在 0–1：传 1.5 也不会让回复变长 —— 生病只会让人话少。
    ★ 系数都是 1 时**原样返回入参的副本**（不写任何新键）：vitals 关掉/没病
      必须与"没有这个功能"逐字节等价。
    """
    out = dict(b or {})
    f = factors if isinstance(factors, dict) else {}

    def _f(key: str) -> float:
        try:
            v = float(f.get(key, 1.0))
        except (TypeError, ValueError):
            return 1.0
        return max(0.0, min(1.0, v))

    cf, pf = _f("max_chars_factor"), _f("max_parts_factor")
    if cf >= 0.999 and pf >= 0.999:
        return out

    per = int(out.get("max_chars") or HARD_MAX_CHARS)
    tot = int(out.get("max_total") or HARD_MAX_TOTAL)
    pmax = int(out.get("parts_max") or HARD_MAX_PARTS)
    out["max_chars"] = max(FACTOR_MIN_CHARS, min(per, int(round(per * cf))))
    out["max_total"] = max(FACTOR_MIN_TOTAL, min(tot, int(round(tot * cf))))
    out["parts_max"] = max(1, min(pmax, int(round(pmax * pf))))
    try:
        out["parts_min"] = min(int(out.get("parts_min") or 1), out["parts_max"])
    except (TypeError, ValueError):
        out["parts_min"] = 1
    out["factors"] = {"chars": round(cf, 3), "parts": round(pf, 3)}
    return out


__all__ = ["BUDGETS", "HARD_MAX_CHARS", "HARD_MAX_PARTS", "HARD_MAX_TOTAL",
           "FACTOR_MIN_CHARS", "FACTOR_MIN_TOTAL",
           "pick", "prompt_line", "visible", "parts", "check", "fit",
           "anchor_ok", "regen_hint", "strip_ctx_marks", "apply_factors"]
