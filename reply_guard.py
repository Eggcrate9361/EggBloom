# -*- coding: utf-8 -*-
"""reply_guard.py —— 回复出口的硬约束后处理（FIXPLAN 1.5.11 P0-2 第 2 条）

## 为什么要有它

人格文件里写满了"别说 AI 腔""别露后台""别自报简历"，但**提示词是概率的**。
1.5.10 真机反复出现两类：模型把工具/视觉的中间态说出去（"这轮只看得到原图"），
以及被问身份时开始念简历（三句话介绍自己）。改人格文件治不了这个 ——
得在**发送前**用代码兜一道。

## 只做三件事（都很窄，宁漏不误伤）

1. **号码**：6 位以上数字串（QQ 号 / 群号）从回复里删掉 —— 连带它所在的那一小句。
2. **露后台**：两级判定 —— 硬词（转述/原图/工具返回/系统提示…）出现即丢整句；
   软词（接口/模型/识别结果…）只有同句带运行信号（这轮/系统/调用/拿不到…）才丢。
   「我在看模型新闻」这种正常聊天的句子不会被误杀。
3. **自报简历**：整段里连续 3 句都以「我是/我今年/我平时/我住在…」开头 → 只留第一句。

## 不做什么

- 不改语气、不换词、不补标点（那是 `de_ai` 的活）
- 不做语义判断、不调模型（零成本、确定性的）
- 不删"用户说的话"：本函数只处理 bot 要发的文本

调用方：`bot_core` 群聊/私聊两条发送链，在 `de_ai_post_process` 之后。
"""
import re

#: 硬后台词：只要出现，这句就不该发给用户
_HARD_LEAK = (
    "转述", "原图", "工具返回", "工具执行", "系统配的", "这轮只看得到",
    "系统提示", "prompt",
)

#: 软后台词：聊天里聊到"模型/接口"是正常的，只有同句带运行信号才算露后台
_SOFT_LEAK = ("接口", "模型", "视觉识别", "识别结果", "上下文")

#: 运行信号 —— 有它才说明这句在讲"我是怎么知道/怎么拿到的"
_OP_MARKERS = (
    "这轮", "系统", "后台", "工具", "调用", "返回", "识别",
    "拿不到", "看不到", "读不到", "获取不到", "从接口",
)

#: 6 位以上连续数字（QQ 号、群号；手机号 11 位也会被盖住）
_NUMBER_RE = re.compile(r"[0-9]{6,}")

#: 括号里的号码：`AAA无理由退货(3865661256)` / `（3865661256）`。
#: ★ 2026-10-31 真机：模型自报身份时几乎总把号写在括号里，而分句只按
#:   `，、；` 切 —— 整句变成一个片段，连号带名字一起被删，回复直接变空
#:   （用户反馈「回复内容没有」）。括号里的号是"附注"，摘掉即可，句子留着。
_PAREN_NUM_RE = re.compile(r"[（(]\s*[0-9]{6,}\s*[)）]")

#: 简历式开头
_RESUME_STARTS = ("我是", "我今年", "我平时", "我住在", "我来自", "我喜欢", "我不喜欢")

_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])")


def _sentence_parts(s: str):
    """切句，保留标点；空片段丢掉。"""
    return [p for p in _SENT_SPLIT.split(s) if p and p.strip()]


def _drop_leak_sentences(line: str) -> str:
    kept = []
    for sent in _sentence_parts(line):
        if any(w in sent for w in _HARD_LEAK):
            continue
        if any(w in sent for w in _SOFT_LEAK) and any(m in sent for m in _OP_MARKERS):
            continue
        kept.append(sent)
    return "".join(kept)


def _strip_numbers(line: str) -> str:
    """删掉含长数字的小句（保留其它小句）。括号里的号只摘号，不连句子。"""
    if "(" in line or "（" in line:
        line = _PAREN_NUM_RE.sub("", line)
        line = re.sub(r"[（(]\s*[)）]", "", line)      # 摘完留下的空括号
    if not _NUMBER_RE.search(line):
        return line
    clauses = re.split(r"(?<=[，,、；;\s（(])", line)
    kept = [c for c in clauses if not _NUMBER_RE.search(c)]
    out = "".join(kept)
    # 清理删完留下的粘连标点/多余空格
    out = re.sub(r"^[，,、；;\s]+", "", out)
    out = re.sub(r"[，,]{2,}", "，", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"([。！？!?])[，,、；;]+", r"\1", out)
    return out.strip()


def _collapse_resume(line: str) -> str:
    """整段都是"我是…我今年…我住在…"时只留第一句。"""
    sents = _sentence_parts(line)
    if len(sents) < 3:
        return line
    hits = sum(1 for s in sents if s.strip().startswith(_RESUME_STARTS))
    if hits >= 3 and hits >= len(sents) - 1:
        return sents[0].strip()
    return line


def guard(text: str) -> str:
    """发送前的硬约束。干净文本原样返回（幂等）。"""
    if not text:
        return text
    out_lines = []
    for line in str(text).split("\n"):
        _l = line
        _l = _strip_numbers(_l)
        _l = _drop_leak_sentences(_l)
        _l = _collapse_resume(_l)
        if _l.strip():
            out_lines.append(_l.rstrip())
    return "\n".join(out_lines).strip()
