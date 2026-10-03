# -*- coding: utf-8 -*-
"""harness/time_ctx.py —— 时间上下文**分级**注入原语。

问题
    bot 的 system prompt 要告诉模型"现在几点"。但若每轮都注入精确到分钟的时间：
      - 时间戳每 60 秒变一次 → **前缀缓存必然失效**（前缀只要有一个 token 变了，
        后面全部按未命中计价，DeepSeek 上是 10x 差价）；
      - 而模型其实很少真的要精确到分钟。

思路（分级投递）
    - **粗粒度**（时段+日期）变化很慢，注入到**靠前**位置（历史摘要之前）也能长期命中；
    - **精粒度**（精确分钟）放到**靠后**位置（当前用户消息之前），只影响尾部极少量 token。

    因此时间既"在场"，又不污染长前缀。这是"极低成本"的直接落点：
    时间**不是不注入**，而是注入在**代价最低的位置**、按**最低必要精度**。

用法
    ctx = TimeContext("g_12345")            # 每会话一个
    coarse, fine = ctx.parts()              # 拿两段文本（fine 可能为空 = 本分钟无需刷新）
    ...
    coarse, fine = ctx.parts(ts=...)
"""
import datetime
import threading
import time

_WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")

# 时段定义（左闭右开）：(起始小时, 名称, 语气提示)
_PERIODS = (
    (0, "凌晨", "（大部分人在睡觉，能在线的是夜猫子）"),
    (5, "清晨", "（刚天亮，说话轻一点）"),
    (8, "上午", "（正常工作/上课时间）"),
    (11, "中午", "（午饭时间）"),
    (14, "下午", "（下午工作/上课时间，容易犯困）"),
    (17, "傍晚", "（下班/放学时间）"),
    (19, "晚上", "（休闲时间，人在线概率高）"),
    (23, "深夜", "（该睡了，语气可以软一点）"),
)


def period_of(ts):
    """返回 (时段名, 语气提示)。"""
    hour = datetime.datetime.fromtimestamp(ts).hour
    name, hint = _PERIODS[0][1], _PERIODS[0][2]
    for h, n, x in _PERIODS:
        if hour >= h:
            name, hint = n, x
        else:
            break
    return name, hint


def coarse_text(ts=None):
    """粗粒度：仅日期 + 时段（小时级）。变化慢 → 可放靠前位置。"""
    if ts is None:
        ts = time.time()
    dt = datetime.datetime.fromtimestamp(ts)
    period, hint = period_of(ts)
    return (f"【现在】{dt.year}年{dt.month}月{dt.day}日"
            f" 星期{_WEEKDAYS[dt.weekday()]}，{period}{hint}")


def fine_text(ts=None):
    """精粒度：精确到分钟。变化快 → **只能**放尾部（当前消息之前）。"""
    if ts is None:
        ts = time.time()
    return "【精确时间】" + datetime.datetime.fromtimestamp(ts).strftime("%H:%M")


class TimeContext:
    """每会话的时间上下文缓存：粗/精两段各自按需刷新。

    - 粗粒度：同一天内**不刷新**（日期变了才变）；
    - 精粒度：同一分钟内有且只用一次（`parts()` 返回的 fine 可能为 "" 表示无需刷新）。
    """

    __slots__ = ("_key", "_coarse", "_coarse_day", "_fine", "_fine_minute", "_lock")

    def __init__(self, key=""):
        self._key = key
        self._coarse = ""
        self._coarse_day = None
        self._fine = ""
        self._fine_minute = None
        self._lock = threading.Lock()

    def parts(self, ts=None, *, need_precise=False):
        """返回 (coarse, fine)。

        coarse : 若为空表示本次无需注入（同一天且已给过）。
        fine   : 若为空表示本分钟无需注入。need_precise=True 时**总是**给（用于
                 agent 执行模式——执行任务时精确时间确实可能有用）。
        """
        if ts is None:
            ts = time.time()
        dt = datetime.datetime.fromtimestamp(ts)
        day = (dt.year, dt.month, dt.day)
        minute = (day, dt.hour, dt.minute)

        with self._lock:
            coarse = ""
            if self._coarse_day != day or not self._coarse:
                self._coarse = coarse_text(ts)
                self._coarse_day = day
                coarse = self._coarse

            fine = ""
            if need_precise:
                fine = fine_text(ts)
                self._fine_minute = minute
                self._fine = fine
            elif self._fine_minute != minute:
                self._fine = fine_text(ts)
                self._fine_minute = minute
                fine = self._fine

            return coarse, fine

    def coarse(self, ts=None):
        c, _ = self.parts(ts)
        return c

    def fine(self, ts=None, *, force=False):
        if force:
            if ts is None:
                ts = time.time()
            return fine_text(ts)
        _, f = self.parts(ts)
        return f


# ── 全局默认实例（群聊场景复用）────────────────────────────────────
_DEFAULT = TimeContext("__default__")


def default_parts(ts=None, *, need_precise=False):
    return _DEFAULT.parts(ts, need_precise=need_precise)
