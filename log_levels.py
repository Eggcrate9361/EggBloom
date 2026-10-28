# -*- coding: utf-8 -*-
"""log_levels.py —— 日志分级规则的唯一来源

为什么单独一个模块：
    终端着色（bot_core._colorize）和 WebUI 日志页都要把一行日志归到某个级别。
    规则写两份必然会漂：改了一边忘了另一边，同一个项目里出现两套判级。
    所以关键词表只放这里，两边都 import。

级别：
    err   错误      崩溃、异常、失败
    warn  注意      超时、重试、跳过、限流
    ai    AI 输出   模型回复、重试
    send  发送      消息/图片真正发出去
    sys   子系统    插件、事件、表情、工具调用
    info  其他      不上色的普通行

判级顺序：
    1. 错误词 → 2. 注意词 → 3. AI 词 → 4. 行首标签 → info
    错误优先于标签，因为「[表情] 发送失败」应该算 err 而不是 sys。

关于标签：
    真实日志长这样 `[Plugin:proactive_talk] 心跳线程启动`、`[事件] 收到: ...`。
    老版规则写的是 `[Plugin]`（带右括号），永远匹配不上；这里改成按**标签名**
    匹配，取行首 `[` 与 `]`/`:` 之间的部分。
"""
import re

LEVELS = ("err", "warn", "ai", "send", "sys")

# 关键词判级，顺序即优先级
RULES = (
    ("err", ("CRASH", "未捕获异常", "失败", "异常", "错误",
             "ERROR", "Error", "error", "Traceback")),
    ("warn", ("警告", "跳过", "冷却", "超时", "限流", "重试", "未匹配")),
    ("ai", ("AI 回复", "AI (", "AI失败", "AI 重试", "AI 不重试")),
)

# 反向词：句子里虽然带着「失败/异常」这类词，但语义是「已经好了」，
# 判成红色会误导。命中就先归 info。（实例：「写盘已恢复（此前连续失败 2 次）」）
CALM = ("已恢复", "恢复正常", "重连成功", "已自动重启", "恢复正常")

# 行首标签 → 级别（按标签名匹配，不带括号）
TAG_LEVELS = {
    "发送": "send",
    "发图": "send",
    "Plugin": "sys",
    "Sandglass": "sys",
    "事件": "sys",
    "表情": "sys",
    "表情包回敬": "sys",
    "画图": "sys",
    "搜索": "sys",
    "私聊": "sys",
    "群": "sys",
    "群管理": "sys",
    "scope": "sys",
    "状态": "sys",
    "harness": "sys",
    "Agent": "sys",
    "转发": "sys",
    "文件": "sys",
    "出站审计": "sys",
    "输出预算": "sys",
    "调试": "sys",
}

# WebUI 徽章文字
LABELS = {
    "err": "ERR",
    "warn": "WARN",
    "ai": "AI",
    "send": "SEND",
    "sys": "SYS",
    "info": "INFO",
}

_TS = re.compile(
    r"^\[(?:\d{4}-\d{2}-\d{2}[ T])?\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\]\s*")
_TAG = re.compile(r"^\[([^\]\s:]{1,20})[\]\s:]")


def leading_tag(text: str) -> str:
    """取行首标签名。`[00:12:34] [Plugin:x] ...` → 'Plugin'；没有则空串。

    时间戳两种写法都要能剥掉（2026-10-28 起写入端带日期）：

        [18:44:45] [发送] …                    ← 旧
        [2026-10-28 18:44:45] [发送] …         ← 新

    只写 `[0-9:]{5,8}` 的话，新格式一行都剥不掉，`_TAG` 会把
    `2026-10-28` 当成标签名 → 查不到 → 全部退化成 INFO，
    WebUI 日志页的颜色和徽章会集体失灵（而且不报错）。
    """
    s = _TS.sub("", str(text or "").strip())
    m = _TAG.match(s)
    return m.group(1) if m else ""


def classify(text) -> str:
    """返回 'err' / 'warn' / 'ai' / 'send' / 'sys' / 'info' 之一。"""
    if not text:
        return "info"
    s = str(text)
    for calm in CALM:
        if calm in s:
            return "info"
    for level, keys in RULES:
        for k in keys:
            if k in s:
                return level
    return TAG_LEVELS.get(leading_tag(s), "info")
