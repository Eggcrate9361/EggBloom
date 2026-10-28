# -*- coding: utf-8 -*-
"""反问闸（1.5.9 §3.6，提交 5 的可选第三步）。

## 这个模块只管一件事：**末句问号**

方案 §3.6 的顺序是"先减、再加闸"：改人格第 13 行 → 三处同义规则并一处 →
配额话术带"不要问" → 最后才是这个开关。默认 **off**，理由写在方案里：

> §3.1 刚把首字压到 5 秒以内，重生会把它推回 7–8 秒。速度与零问号不能同时要。

| 值 | 行为 | 代价 |
|---|---|---|
| `off`（默认） | 只记 `[口癖]`，不改 | 0 |
| `regen` | 末句是问号就重生一次 | 那一条多 2–4 秒 |
| `regen_private` | 只在私聊 / 被 @ 时重生，群聊不重生 | 局部多 2–4 秒 |

## 不做的事（方案明确撤销）

**不剪文本**：末句删除 / 问号改句号 / 收尾模板剥离全部撤掉 ——
改文本会让句子残缺（"那我先"），残句比反问更像人机。
本模块**只输出"要不要重生"的判据**，不提供任何文本变换函数。

## 与旧实现的关系

2026-10-28 早先按交接文件的描述做过一版"问句没答 → 整条不发"的闸，
键名同为 `reply_question_guard` 但语义完全不同（方案里它是末句问号强度）。
已按方案重做：键名归位，旧逻辑删除 —— 同一概念不留两处定义。
"""

#: 收尾腔（叮嘱/总结）——只用于 `[口癖]` 观测，不参与任何拦截
_ENDING_WORDS = ("早点睡", "记得", "注意", "总之", "反正", "就这样", "好好休息",
                 "别忘", "要好好", "照顾好自己")

#: 尾部装饰符：判断"末句是不是问号"时要剥掉，但不剥掉问号本身
_TAIL_NOISE = "”’\"'）)】」』…~～。.!！,，、 \t\r\n"

#: `[表情:xx]` 这类控制标记不是正文
_MARK_HINT = "[表情"


def _plain(text):
    """去掉表情标记与首尾空白，保留正文标点。"""
    s = str(text or "")
    while _MARK_HINT in s:
        i = s.rfind(_MARK_HINT)
        j = s.find("]", i)
        if j < 0:
            s = s[:i]
            break
        s = s[:i] + s[j + 1:]
    return s.strip()


def guard_mode(cfg=None):
    """读 `reply_question_guard`：off / regen / regen_private。

    容错：旧版这个键是布尔（含义也不同），一律按 `off` 处理 ——
    宁可不开闸，也不能让一个含义已经变过的值突然开始花重生钱。
    """
    cfg = cfg or {}
    raw = cfg.get("reply_question_guard", "off")
    if isinstance(raw, bool) or raw is None:
        return "off"
    s = str(raw).strip().lower()
    if s in ("regen_private", "regen-private", "private"):
        return "regen_private"
    if s in ("regen", "on", "true", "1", "yes"):
        return "regen"
    return "off"


def ends_with_question(text):
    """末句是不是以问号结尾（剥掉尾部装饰后判断）。"""
    s = _plain(text).rstrip(_TAIL_NOISE)
    return s.endswith(("?", "？"))


def habit_line(text):
    """`[口癖]` 观测行；没有问题号也不带收尾腔时返回 ''（不刷日志）。"""
    s = _plain(text)
    if not s:
        return ""
    marks = s.count("？") + s.count("?")
    tail = 1 if s.rstrip(_TAIL_NOISE).endswith(_ENDING_WORDS) else 0
    if not marks and not tail:
        return ""
    return f"问号={marks} 收尾={tail} 原文={s[:24]}"


def should_regen(mode, reply, *, is_private=False, addressed=False):
    """这一条要不要重生。

    `regen_private` 的语义是"只在私聊 / 被 @ 时重生"——
    群里随口一句反问不值得多花 2–4 秒，一对一才值得。
    """
    m = str(mode or "off").strip().lower()
    if m not in ("regen", "regen_private"):
        return False
    if not ends_with_question(reply):
        return False
    if m == "regen_private":
        return bool(is_private or addressed)
    return True


def regen_hint():
    """重生时追加的指令。只针对形态，不给内容方向（避免模型换话题）。"""
    return ("（你上一条末句又用了问号。这次别用问号收尾，把话说完，直接给结论。"
            "不要反问我。）")
