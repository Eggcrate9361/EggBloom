# -*- coding: utf-8 -*-
"""message_shell.py —— 消息壳（`[引用:…]` / `[当前]`）的**唯一拆解口**。

## 背景（2026-11-10 真机证据）

`bot_core` 入站会把一条引用消息拼成：

    [引用:显示名｜他在引用X，**不是在叫我**，别抢话] 被引原文\\n[当前] 本次发言

这个壳是**给模型看的**：模型需要知道「谁在引用谁」才不会抢话。但壳里两段
都不是本次发言 —— 被引原文可能是 Bot 自己上一句，`[引用:…]` 注记是我们写给
模型的指令。此前只有 `self_learn.collect_message` 一个入口剥了壳，其余下游
全部把带壳文本当「用户原话」用，于是：

  · 存档 / 记忆：`[当前]`、`别抢话` 进沙漏，核心记忆注入原文里出现程序注记；
  · 口癖学习：`QQ` / `用户` / `[引` / `(Bot)` 被当成这个人的说话习惯；
  · 日志：决策摘要把注记写进 `chat_logs`；
  · **检索**：被引原文（恰好是 Bot 自己上一句、含强触发词「出处」）被
    `_pick_search_query` 挑成检索词 → 搜回「先」字字典页 → 模型照着编造细节。

仓库里已经有 5 份各自独立、判据不一的剥壳实现
（`bot_core._strip_bot_annotation` / `people_alias.strip_annotations` /
`reply_budget.strip_ctx_marks` / `reply_guard._strip_ctx_marks` /
`harness.consistency._QUOTE_INLINE`），每次只堵一个出水口。本模块不重构它们，
只给**新增的存档 / 日志 / 检索路径**提供同一个真相源。

## 三个语义，别混用

| 函数 | 输入 | 输出 | 给谁 |
| --- | --- | --- | --- |
| `utterance()` | 单条消息 | **只有本次发言** | 存档、记忆分发、口癖学习 |
| `for_log()` | 单条或批次 | 删掉程序注记，**保留全部真实正文行** | 给人复盘的日志 |
| `searchable()` | 批次 / 近况混合文本 | 只有本次发言，且不含 Bot 自己的行 | 检索词与「要不要搜」判定 |
| `for_context()` | 近况 / 时间线单行 | 保留引用对象与全部正文，只删注记里的**程序指令** | 给模型看的「群聊近况」块 |

**给模型看的上下文（`_pipeline_upsert` 的 `text` / `GROUP_TIMELINE` 回抓）不剥壳** ——
引用关系是判断「谁在跟谁说话」的必要信息，剥掉反而会加剧串人。
`for_context()` 不是剥壳：它只在「近况块」这个展示口把
`[引用:某人｜他在引用X，**不是在叫我**，别抢话]` 收敛成 `[引用:某人]`，
让模型仍知道谁引用了谁，但不再把「别抢话」这类指令当成群友说过的话。

放在根目录而不是 `harness/`：`harness/__init__.py` 会连带 import 二十多个子模块，
其中部分反向依赖主链；启动早期引入有循环导入风险。
"""
import re

__all__ = ["utterance", "for_log", "searchable", "for_context"]

_QUOTE_NOTE_RE = re.compile(r"\[引用:[^\]\n]{0,200}\]")
#: `[引用:显示名｜…程序指令…]` → 只保留 `[引用:显示名]`
_QUOTE_NOTE_INLINE_RE = re.compile(
    r"(\[引用:[^\]｜|\n]{1,60})[｜|][^\]\n]{0,200}\]")
_QUOTE_BARE_RE = re.compile(r"\[引用\]")
_CURRENT_LINE_RE = re.compile(r"(?m)^[ \t]*\[当前\][ \t]*")
_BOT_LINE_RE = re.compile(r"(?:我\(Bot\)|\(Bot\)|\[系统\]|【系统】)")
_CHUNK_HEAD_RE = re.compile(
    r"^(?:【群聊近况|【要求】|【视觉识别|【联网搜索结果】|【核心记忆】)"
)


def utterance(text) -> str:
    """单条消息 → **本次发言**的纯文本（丢掉被引原文与程序注记）。

    与既有 `bot_core._strip_bot_annotation` 判据完全一致（那个函数现为本函数
    的薄封装），额外兼容私聊的裸 `[引用]` 形态。
    """
    t = str(text or "")
    if "\n[当前] " in t:
        return t.split("\n[当前] ", 1)[1].strip()
    t = re.sub(r"^\[引用:[^\]]*\]\s*", "", t)
    t = re.sub(r"^\[引用\]\s*", "", t)
    return t.strip()


def for_log(text) -> str:
    """批次 / 流水 → 删掉程序注记块，保留所有真实正文行。

    与 `utterance()` 的区别：日志要能复盘「这一批里都有谁说了什么」，
    不能只留最后一句；但 `[引用:X｜…]` 这段注记本身是程序写的，必须删。
    """
    t = str(text or "")
    t = _QUOTE_NOTE_RE.sub("", t)
    t = _QUOTE_BARE_RE.sub("", t)
    t = _CURRENT_LINE_RE.sub("", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def for_context(text) -> str:
    """近况 / 时间线单行 → 保留引用对象与正文，删掉注记里的程序指令。

    `[引用:张三｜他在引用张三，**不是在叫我**，别抢话] 被引原文`
    → `[引用:张三] 被引原文`

    与 `for_log()` 的区别：日志给人复盘，注记整块删掉；近况块给模型判断
    「谁在跟谁说话」，引用对象必须留。只删 `｜` 之后那段程序指令文字。
    对不含壳的普通文本是恒等变换。
    """
    t = str(text or "")
    if "｜" not in t and "|" not in t:
        return t
    return _QUOTE_NOTE_INLINE_RE.sub(lambda m: m.group(1) + "]", t)


def searchable(text) -> str:
    """检索输入 → 只有本次发言，且不含被引原文 / Bot 自己 / 上下文块。

    真机事故（11-10 10:01）：`[引用:我(Bot)｜…] 先把出处给我，再吹\\n[当前] …`
    里「出处」是强触发词，被引原文被 `_pick_search_query` 挑成检索词，
    搜回「先」字字典页。所以这里**先按 `[当前]` 切开**，再逐行丢掉：
    引用行、Bot 自己的行、近况/检索结果这类注入块。
    """
    t = str(text or "")
    if "\n[当前] " in t:
        # 只取第一个 [当前] 之后的内容：壳结构是「引用在前、本次发言在后」。
        t = t.split("\n[当前] ", 1)[1]
    elif t.lstrip().startswith("[当前] "):
        t = t.split("[当前] ", 1)[1]
    out = []
    for ln in t.replace("\r\n", "\n").split("\n"):
        s = ln.strip()
        if not s:
            continue
        if _CHUNK_HEAD_RE.match(s):
            continue
        # 行内检测，不能只看行首：真实批次是
        # `[昵称·锚] [引用:某人｜…] 被引原文`，注记前面还有说话人前缀。
        if "[引用:" in s or s.startswith("[引用]") or "[引用]" in s:
            continue
        if _BOT_LINE_RE.search(s):
            continue
        out.append(s)
    return "\n".join(out).strip()
