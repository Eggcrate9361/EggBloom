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

# 时段定义（左闭右开）：(起始小时, 名称, 语气提示, 这会儿在做什么)
#
# ★ 第 4 列「在做什么」（2026-10-24）：
#   此前 Bot 只有"现在几点"，没有"我这会儿在干嘛"。被问「你在干嘛」时它只能现编，
#   而现编又撞上【诚实铁律】的"不编造"—— 两条规则互相打脸。
#   活动作为**时段的属性**放在这里，而不是另开一张表：一个概念一个源。
#   （曾考虑新建 harness/activity.py，但那必然要重复一遍小时边界，
#   正是 MEMORY.md 里「两套判据事故」的形状。）
#
#   为什么不额外伤缓存：`coarse_text` 本来就含时段名，时段一变它就变；
#   活动跟着时段变，变化频率完全相同 → 前缀缓存断裂次数 +0。
_PERIODS = (
    (0, "凌晨", "（大部分人在睡觉，能在线的是夜猫子）", "躺着，快睡了"),
    (5, "清晨", "（刚天亮，说话轻一点）", "刚醒，还没完全起来"),
    (8, "上午", "（正常工作/上课时间）", "在上课或者看书"),
    (11, "中午", "（午饭时间）", "在吃饭"),
    (14, "下午", "（下午工作/上课时间，容易犯困）", "在写东西，或者摸鱼"),
    (17, "傍晚", "（下班/放学时间）", "刚下课，在外面晃"),
    (19, "晚上", "（休闲时间，人在线概率高）", "在自己待着"),
    (23, "深夜", "（该睡了，语气可以软一点）", "准备睡了，但还醒着"),
)


def _entry_of(ts):
    """返回命中的 _PERIODS 条目（4 元组）。"""
    hour = datetime.datetime.fromtimestamp(ts).hour
    entry = _PERIODS[0]
    for row in _PERIODS:
        if hour >= row[0]:
            entry = row
        else:
            break
    return entry


def period_of(ts):
    """返回 (时段名, 语气提示)。"""
    row = _entry_of(ts)
    return row[1], row[2]


def hours_of(*names):
    """这些时段覆盖的小时集合（24 小时制，左闭右开，跨天自动折回 0–23）。

    ★ 2026-10-28（1.5.9 提交 9「时间线单一来源」）：
      `de_ai._TIME_PERIOD_WORDS` 原来自己写了一张小时区间表，与这里的
      `_PERIODS` 边界**不一致**（time_ctx 上午从 8 点，de_ai 从 7 点）——
      同一时刻两个模块能判出相反结论。现在那张表只声明"这个词属于哪个时段"，
      边界一律从这里取。
    """
    want = {str(n) for n in (names or ()) if n}
    if not want:
        return frozenset()
    out = set()
    for i, row in enumerate(_PERIODS):
        if row[1] not in want:
            continue
        start = row[0]
        end = _PERIODS[i + 1][0] if i + 1 < len(_PERIODS) else _PERIODS[0][0] + 24
        for h in range(start, end):
            out.add(h % 24)
    return frozenset(out)


def period_names():
    """全部时段名（按一天中的顺序）。"""
    return tuple(row[1] for row in _PERIODS)


def activity_of(ts):
    """这会儿在做什么（一句人话，已含"在"或动作本身）。

    凌晨被 @ 时模型知道自己在睡觉，就不会答"我在写代码"。
    只描述**状态**，不描述内容 —— 编具体内容（"在看某本书第三章"）仍然是编造。
    """
    return _entry_of(time.time() if ts is None else ts)[3]


# ── 静默时段（"睡觉"的硬闸）───────────────────────────────────────
# 为什么放在这里、而不是留在 proactive_talk：
#   quiet_hours 原本只是主动开口插件的一个配置项，于是它只挡得住"Bot 自己想说话"，
#   挡不住"群里有人 @ 它，它答"。用户在本机运行版里早就配了 1:00-7:00 静默，
#   意图明确，但群聊回复链（bot_core）从不读这个键 —— 凌晨两点被 @ 照样答。
#   一个概念两处判据，且只有一处生效。
#
#   现在解析逻辑只有这一份，谁要判断都问这里：
#     · proactive_talk 主动开口     → 硬闸，命中直接不开口
#     · bot_core     群聊/私聊回复  → 硬闸，命中不回复（被点名则另算）
#
#   "睡觉"和"不回复"不是一回事：群里被 @ 属于叫醒，真人也可能回一句
#   "这么晚了还不睡"。所以这里只回答"是不是静默时段"，命中后干什么由调用方定。

def parse_window(spec):
    """把 '23:00-7:00' 解析成 (start_min, end_min)，支持跨天。失败 → None。

    空串 → None（= 没配），不是"全天静默"。这个区别必须保留：
    把空串当成一个有效窗口，等于让没配置过的人永远静默。
    """
    spec = (spec or "").strip()
    if not spec:
        return None
    try:
        a, b = spec.split("-", 1)
        sh, sm = [int(x) for x in a.strip().split(":")]
        eh, em = [int(x) for x in b.strip().split(":")]
        if not (0 <= sh <= 24 and 0 <= eh <= 24 and 0 <= sm < 60 and 0 <= em < 60):
            return None
        return sh * 60 + sm, eh * 60 + em
    except Exception:
        return None


def in_quiet_hours(spec, ts=None):
    """现在是否落在静默时段。空/解析失败 → False（宁可不静默，不要误静默）。"""
    win = parse_window(spec)
    if not win:
        return False
    ts = time.time() if ts is None else ts
    dt = datetime.datetime.fromtimestamp(ts)
    cur = dt.hour * 60 + dt.minute
    start, end = win
    if start == end:
        return False          # 起止相同 = 零长度窗口，不静默
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end     # 跨天（如 23:00-7:00）


def coarse_text(ts=None):
    """粗粒度：仅日期 + 时段（小时级）。变化慢 → 可放靠前位置。

    ★ 2026-10-24：末尾带「这会儿在做什么」。它跟时段同步变化，
      与时段名同一刷新节奏，不额外增加缓存断裂。
    """
    if ts is None:
        ts = time.time()
    dt = datetime.datetime.fromtimestamp(ts)
    period, hint = period_of(ts)
    return (f"【现在】{dt.year}年{dt.month}月{dt.day}日"
            f" 星期{_WEEKDAYS[dt.weekday()]}，{period}{hint}。"
            f"你{activity_of(ts)}。")


def fine_text(ts=None):
    """精粒度：精确到分钟。变化快 → **只能**放尾部（当前消息之前）。"""
    if ts is None:
        ts = time.time()
    return "【精确时间】" + datetime.datetime.fromtimestamp(ts).strftime("%H:%M")


# ── 时间前提核对（"没有时间观念"的确定性兜底）────────────────────────
# 为什么要这一层，而不是只靠 prompt 讲道理：
#   模型对"现在几点"的推理是**软的** —— 即使 prompt 里写了 10:44，它也可能顺着
#   对方那句"晚上好"往下答，因为接话的惯性比读时间强得多。人不会这样：大白天有人
#   跟你说"晚上好"，你第一反应是"你睡糊涂了"，不是"晚上好"。
#   所以这里把**矛盾本身**算出来，直接告诉模型"这句话跟钟点对不上"——
#   把一个需要它主动比对的任务，降级成一句它只需要照做的指令。
#
# 只匹配"整句就是一个问候"的情况（<= 10 字且以问候开头），
# 避免"他昨天跟我说晚上好"这种叙述被误判。
_GREETINGS = (
    # (问候词, 合法小时集合, 常见替代说法)
    ("凌晨好", lambda h: h <= 5, "凌晨好"),
    ("早上好", lambda h: 4 <= h <= 10, "早上好"),
    ("早安", lambda h: 4 <= h <= 10, "早安"),
    ("上午好", lambda h: 7 <= h <= 11, "上午好"),
    ("中午好", lambda h: 10 <= h <= 13, "中午好"),
    ("下午好", lambda h: 11 <= h <= 17, "下午好"),
    ("傍晚好", lambda h: 16 <= h <= 19, "傍晚好"),
    ("晚上好", lambda h: h >= 16 or h <= 4, "晚上好"),
    ("晚安", lambda h: h >= 20 or h <= 5, "晚安"),
)

#: 整句最大长度（去掉空白/标点）：超过就算叙述，不判前提
_GREET_MAX_LEN = 10
#: 句末语气词/标点，匹配时剥掉
_TAIL_NOISE = "啊呀哦喔噢呢吧啦~～!！。.,，…　 \t\r\n"


def _hhmm(dt):
    return dt.strftime("%H:%M")


#: 口语钟点：人不会说"这才23点吧"，会说"这才十一点吧"/"都快十一点了"。
#  13 点以上按 12 小时制回读（13→"下午一点"），简体中文数字。
_CN_NUM = ("", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
           "十一", "十二")


def _spoken_hour(hour):
    """把 24 小时制转成人会说的时间短语（用于生成反驳例句）。

    13 点以上带"下午/晚上"前缀：单说"一点"在没有上下文的提示里指不清是
    凌晨一点还是下午一点，而这个提示的用途恰恰就是消歧。
    """
    h = hour % 24
    if h == 0:
        return "十二点"
    if h < 12:
        return _CN_NUM[h] + "点"
    if h == 12:
        return "十二点"
    _prefix = "下午" if h < 18 else "晚上"
    return _prefix + _CN_NUM[h - 12] + "点"


def mismatch_note(user_msg, ts=None):
    """对方说的问候跟实际钟点对不上 → 返回一段**可直接执行**的提示；否则 ""。

    例：10:44 收到「晚上好」→
        【时间对不上】对方说「晚上好」，但现在是 10:44（上午）……
    """
    if not user_msg:
        return ""
    raw = str(user_msg).strip()
    # 只处理"整句就是个问候"：先剥标点语气词
    core = raw
    while core and core[-1] in _TAIL_NOISE:
        core = core[:-1]
    if not core or len(core) > _GREET_MAX_LEN:
        return ""
    for word, ok, _alias in _GREETINGS:
        if not core.startswith(word):
            continue
        # 问候词之后只允许语气词（"晚上好啊""晚上好呀"），别的一律当叙述跳过
        rest = core[len(word):].strip(_TAIL_NOISE)
        if rest:
            continue
        if ts is None:
            ts = time.time()
        dt = datetime.datetime.fromtimestamp(ts)
        if ok(dt.hour):
            return ""
        period, _hint = period_of(ts)
        # 例句用**口语钟点**（"这才十点吧"），不是"这才10点吧" —— 后者像日志不像人话。
        return (
            f"【时间对不上·重要】对方说「{core}」，但现在是 "
            f"{_hhmm(dt)}（{period}）。"
            f"这句话跟钟点矛盾，**别顺着答**：直接点出来，"
            f"比如「这才{_spoken_hour(dt.hour)}吧」「你睡糊涂了」「外面大太阳呢」，"
            f"然后再接自己的话。不要回「{core}」，也不要装作现在是{word[:2]}。"
        )
    return ""


class TimeContext:
    """每会话的时间上下文缓存。

    ★ 语义修复（2026-10-22）：`parts()` **总是**返回当前时间文本。

    原来的实现是"同一天/同一分钟已给过 → 返回空串表示本轮不用注入"。那个设计
    建立在一个错误前提上：以为 prompt 是持久累积的（给过一次就一直留在上下文里）。
    实际 `_build_cached_messages` 每轮都**从头重建** messages —— 返回空串等于
    "这一轮 prompt 里没有时间"。结果是：
      · 粗粒度每天只在第一轮出现，之后一整天都消失；
      · 精粒度每分钟只在第一轮出现，同一分钟内第二句开始就没时间。
    而且空串会让 system 消息**数量**在不同轮次间变化（有时 6 条、有时 5 条），
    这比文本变化更伤前缀缓存 —— 整段结构位移，命中区缩短。

    正确做法：文本恒定返回，让"变化慢的放前面、变化快的放后面"这件事由**位置**
    承担，而不是靠"这一轮干脆不给"。粗粒度按 (日期, 时段) 变（一天 ≤8 次），
    精粒度按分钟变（放尾部，约 10 token/轮）—— 两者都在可缓存/可忽略的量级。
    """

    __slots__ = ("_key", "_coarse", "_coarse_stamp", "_fine", "_fine_minute", "_lock")

    def __init__(self, key=""):
        self._key = key
        self._coarse = ""
        self._coarse_stamp = None
        self._fine = ""
        self._fine_minute = None
        self._lock = threading.Lock()

    def parts(self, ts=None, *, need_precise=False):
        """返回 (coarse, fine)，**两段都非空**（除非系统时钟异常）。

        coarse : 日期 + 星期 + 时段（含语气提示）。按 (日期, 时段) 刷新。
        fine   : 精确到分钟。每轮都给 —— 尾部位置，成本约 10 token。
                 need_precise 参数保留仅为兼容旧调用；现在行为一致。
        """
        if ts is None:
            ts = time.time()
        dt = datetime.datetime.fromtimestamp(ts)
        day = (dt.year, dt.month, dt.day)
        minute = (day, dt.hour, dt.minute)
        stamp = (day, period_of(ts)[0])

        with self._lock:
            # 粗粒度文本按 (日期, 时段) 缓存；到点才重算，避免每轮 strftime
            if self._coarse_stamp != stamp or not self._coarse:
                self._coarse = coarse_text(ts)
                self._coarse_stamp = stamp
            coarse = self._coarse

            if self._fine_minute != minute or not self._fine:
                self._fine = fine_text(ts)
                self._fine_minute = minute
            fine = self._fine

            return coarse, fine

    def coarse(self, ts=None):
        return self.parts(ts)[0]

    def fine(self, ts=None, *, force=False):
        return self.parts(ts)[1]


# ── 全局默认实例（群聊场景复用）────────────────────────────────────
_DEFAULT = TimeContext("__default__")


def default_parts(ts=None, *, need_precise=False):
    return _DEFAULT.parts(ts, need_precise=need_precise)
