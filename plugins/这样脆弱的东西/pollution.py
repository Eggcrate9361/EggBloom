# -*- coding: utf-8 -*-
"""污染防控：把不属于"这个人"的东西从语料里剔出去，并留下可核对的记录。

被污染的语料是最危险的错误，因为它**不会报错**——
「我叫张三，是一个程序员」「我的生日是3月15号」会被当成他的自我介绍，
一路写进身份层、传进人格注入卡，而且看起来完全合理。

实际抓到的三类（都在本项目的真实数据里）：

  1. **开发期测试会话**（2026-06-24 01:42~02:03）
     「我喜欢吃火锅」「我的生日是3月15号」「最近在研究AI机器人」
     「大家好，我是新来的」「周末出去玩还是宅在家」——
     这是当年调人格识别用的脚本对话，句式和真话一样，
     单靠词表拦不住（"我喜欢吃火锅"本身是真话句式）。
     → 所以用**会话级检测**：一个时间窗里大部分消息都命中测试签名，
       整窗隔离，而不是逐条猜。
  2. **机器人自己的回复被记成他的话**
     「（看到正经问题，精神一振）哦哦哦！MAA啊！」——这是小a的语气，
     还带着 `[当前]` 标记（agent 侧的日志结构）。它会混进例句，
     让模型去模仿机器人的语气而不是这个人的。
  3. **占位/无信息消息**：`[合并转发消息]`、`[嵌套转发消息]`、`你好`×40。
     前者没内容，后者虽真实但会稀释统计（"你好"出现 40 次会让
     "口头禅"变成"你好"）。

原则：**隔离，不删除**——被隔离的消息保留在报告里，人可以核对；
宁可错杀要有据可查，不能漏放要无迹可寻。
"""
import re
from collections import Counter

# —— 强签名：单条命中即隔离 ——
_STRONG = (
    "我叫张三", "我叫李四", "我叫王五", "hello world", "this is a test",
    "lorem ipsum", "我是新来的", "欢迎欢迎", "周末话题", "大家好，我是新来的",
    "我的生日是", "生日是3月", "birthday", "占位", "测试用", "样例数据",
)

# —— 弱签名：单条不算，要靠会话级占比 ——
_WEAK = (
    "^我喜欢", "^我最近在", "^最近在研究", "^最近在做", "^最近在做一个",
    "^用\\S{1,8}做\\S{1,8}回复$", "^今天天气怎么样", "^周末出去玩",
    "^好主意，去吃火锅", "^我爱吃", "^我喜欢火锅", "^测试", "^test",
    "^晚上吃什么", "^去吃火锅", "^周末一起",
)

_RE_STRONG = tuple(re.compile(p, re.I) for p in _STRONG)
_RE_WEAK = tuple(re.compile(p) for p in _WEAK)

# —— 机器人侧消息的标记 ——
_RE_AGENT_ACTION = re.compile(r"^（[^）]{2,24}）")      # （看到正经问题，精神一振）
_RE_AGENT_MARK = re.compile(r"\[当前\]")                # agent 侧日志结构
_RE_AGENT_SOFT = re.compile(r"（[^）]{2,20}）[，。！!]?[嗷哦诶诶嘿呀~]")
_RE_MARK_ONLY = re.compile(
    r"^\s*(?:\[(?:合并)?转发消息\]|\[嵌套转发消息\]|\[图片\]|\[视频\]|\[语音\]"
    r"|\[QQ红包\]|\[文件[^\]]*\]|\[表情[^\]]*\]|[\s\n]+)+\s*$")


def is_agent_side(text):
    """这条消息是不是机器人（或日志里的 agent 侧）说的。

    ⚠ 必须区分两个方向：
      「@小a 叫两声」 = 他在叫机器人（user 侧，要保留）
      「小a: 你好呀」 = 机器人在说话（agent 侧，要剔除）
    """
    t = (text or "").strip()
    if not t:
        return False
    if _RE_AGENT_MARK.search(t):
        return True                     # [当前] 是 agent 回复的日志结构
    if _RE_AGENT_ACTION.match(t):
        return True                     # （动作）开头是小a的习惯
    try:
        from . import bot_identity
        if bot_identity.is_bot_speech(t):
            return True                 # 「机器人的名字:」开头 = 它在说话
    except Exception:
        pass
    return False


def is_marker_only(text):
    return bool(_RE_MARK_ONLY.match(text or ""))


def _hits_strong(text):
    t = (text or "").strip()
    return any(p.search(t) for p in _RE_STRONG)


def _hits_weak(text):
    t = (text or "").strip()
    return any(p.search(t) for p in _RE_WEAK)


def _burst_key(ts, gap_minutes=10):
    """把时间戳转成"会话键"：相邻消息间隔 ≤ gap 分钟算同一场。"""
    try:
        import time as _t
        fmt = "%Y-%m-%d %H:%M:%S" if len(ts) > 16 else "%Y-%m-%d %H:%M"
        return _t.mktime(_t.strptime(ts, fmt)) // (gap_minutes * 60)
    except Exception:
        return ts or "no-ts"


def detect(msgs, bare_only_bursts=True):
    """扫描语料，返回 (clean, quarantined)。

    quarantined: [{"msg":…, "reason":…, "fp":…}] —— fp 是该消息的指纹，
    用于把「证据已被隔离」追查到账本里的编号并清洗旧结论。
    """
    from . import evidence as _ev          # 延迟导入避免循环依赖
    clean, quarantined = [], []

    def _q(m, reason):
        try:
            fp = _ev.fingerprint(m)
        except Exception:
            fp = ""
        quarantined.append({"msg": m, "reason": reason, "fp": fp})

    # 第一遍：单条强签名 + 机器人侧 + 纯标记
    rest = []
    for m in msgs:
        t = (m.get("text") or "").strip()
        if not t:
            continue
        if is_marker_only(t):
            _q(m, "纯平台标记，无实际内容")
            continue
        if is_agent_side(t):
            _q(m, "机器人侧消息（含 agent 日志标记或动作文本）")
            continue
        if _hits_strong(t):
            _q(m, "命中测试/占位数据强签名")
            continue
        rest.append(m)

    # 第二遍：会话级检测。把剩下的按"时间会话"分组，
    # 一个会话里弱签名占比过高 → 整场隔离（这是单条词表拦不住的部分）
    groups = {}
    for m in rest:
        groups.setdefault(_burst_key(m.get("timestamp") or ""), []).append(m)

    quarantined_ids = set()
    for key, items in groups.items():
        if len(items) < 3:
            continue
        weak = sum(1 for m in items if _hits_weak(m.get("text")))
        bare = sum(1 for m in items if m.get("assumed_speaker"))
        # 弱签名过半、且这些消息都没有发送者信息 → 开发期脚本对话
        if weak / len(items) >= 0.5 and (bare == len(items) or not bare_only_bursts):
            reason = (f"测试会话（{len(items)} 条中 {weak} 条命中"
                      f"自我陈述/测试句式，且全部无发送者标记）")
            for m in items:
                quarantined_ids.add(id(m))
                _q(m, reason)

    for m in rest:
        if id(m) not in quarantined_ids:
            clean.append(m)

    return clean, quarantined


def report(quarantined, limit=8):
    """给人看的隔离报告：条数 + 按原因分类 + 样例。"""
    if not quarantined:
        return {}
    reasons = Counter(q["reason"].split("（")[0] for q in quarantined)
    samples = []
    seen = set()
    for q in quarantined:
        r = q["reason"].split("（")[0]
        if r in seen:
            continue
        seen.add(r)
        samples.append({"reason": r,
                        "text": (q["msg"].get("text") or "")[:60],
                        "ts": (q["msg"].get("timestamp") or "")[:16]})
        if len(samples) >= limit:
            break
    return {"total": len(quarantined), "by_reason": dict(reasons),
            "samples": samples}
