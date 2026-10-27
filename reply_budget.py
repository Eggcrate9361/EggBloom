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

#: 硬上限兜底（配置项同名）
HARD_MAX_CHARS = 16
HARD_MAX_PARTS = 3
HARD_MAX_TOTAL = 80

#: 表情标记不算字数 —— 「[表情:笑]」是控制指令，不是给用户看的文本
_MEME_TAG = re.compile(r"\[表情[:：][^\]]*\]")
#: 结构标记同理（引用、图片描述）
_MARKUP = re.compile(r"\[(图片|引用|搜索|不说话)[^\]]*\]")


def pick(rng: Optional[float] = None, shift: int = 0) -> Dict:
    """按分布抽一档。传入 rng 便于测试复现。

    shift：情绪兴奋度给的偏移（+1 上移一档 / -1 下移）。夹在首尾之间，
    不会因为一次激动就突破 A 档 —— 档位是分布，不是滑杆。
    """
    r = random.random() if rng is None else float(rng)
    acc = 0.0
    idx = len(BUDGETS) - 1
    for i, (_n, prob, _lo, _hi, _per, _tot) in enumerate(BUDGETS):
        acc += prob
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


def strip_ctx_marks(text: str) -> str:
    """剥掉行首的上下文标记。

    ★ 2026-10-27 真机：模型把输入里的 `[10-27 18:55]` 抄进了回复正文
      （"记不住，我又不是录音机" 变成 "[10-27 18:55] 记不住…"）。
      这个标记既不是内容，也不该参与"这句话完整吗""多长"的判断 ——
      不剥的话 `visible()` 去掉空格后变成 "[10-2718:55]记不住…"，
      结尾落在"机"上 → 被判成半截 → 优先级降到最低。

    只剥**行首**；行中间的不动（"我在[18:46]说的"是正文）。

    一行最多剥两轮（时间戳 + 说话人标签），避免把正文里的方括号也吃掉。
    """
    out = []
    for ln in str(text or "").split("\n"):
        _l = ln
        for _ in range(2):
            _new = _CTX_MARK.sub("", _l, count=1)
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


def fit(text: str, b: Dict) -> str:
    """超配额时的兜底：取最短的一条。不做截断 —— 半句比长句更假。"""
    ps = parts(text)
    if not ps:
        return ""
    return min(ps, key=lambda p: len(visible(p)))


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


__all__ = ["BUDGETS", "HARD_MAX_CHARS", "HARD_MAX_PARTS", "HARD_MAX_TOTAL",
           "pick", "prompt_line", "visible", "parts", "check", "fit",
           "anchor_ok", "regen_hint", "strip_ctx_marks"]
