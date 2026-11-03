# -*- coding: utf-8 -*-
"""plugins/proactive_poke/policy.py —— 主动戳一戳的判定链（1.6.0 §4.1.1）

纯函数，不碰网络、不碰时间：输入事实，输出"戳不戳 / 为什么不戳"。
插件主体只负责收集事实 + 执行动作。

## 三条闸门（规格 §4.1.1，缺一不可）

① **沉默够久**：与对方最后一次互动距今 ≥ `poke_proactive_min_silence`（默认 4 小时）。
   刚聊完就戳是骚扰，不是"主动"。
② **不在谷时**：复用全局 `quiet_hours`（深夜硬静默）。主动动作撞上别人睡觉，
   比不说话更糟 —— 它会在第二天早上留下一个"你半夜戳我"的痕迹。
③ **额度**：今日未达 `poke_proactive_daily_cap`（默认 3 人/天），
   且该对象 24 小时内没被戳过、也没被无视过。

## "被无视"怎么判

严格判断需要"对方是否看到"，拿不到。这里用可观测的替代：
戳完之后 24 小时里对方**没有任何消息**，就算被无视。
这不是完美判据，但它保守 —— 宁可少戳一次，也不当复读机。
"""
from __future__ import annotations

import re

MIN_SILENCE_DEFAULT = 4 * 3600      # 4 小时
DAILY_CAP_DEFAULT = 3
RETRY_WINDOW = 24 * 3600            # 同一对象 24h 内不重复戳


def parse_quiet_hours(raw):
    """把 `quiet_hours` 解析成 [(起, 止), ...]（小时为单位的浮点）。

    支持两种写法：
      · "23:00-07:00"                 —— 单段，可跨零点
      · "23:00-07:00,13:00-14:00"     —— 多段
    解析不出来就返回 []（= 没有谷时），不抛异常：配置写错不该让插件整个挂掉。
    """
    out = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part or "-" not in part:
            continue
        a, _, b = part.partition("-")
        ta, tb = _hhmm(a), _hhmm(b)
        if ta is None or tb is None:
            continue
        out.append((ta, tb))
    return out


def _hhmm(s):
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*$", str(s or ""))
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    if not (0 <= h <= 24 and 0 <= mi < 60):
        return None
    return h + mi / 60.0


def in_quiet_hours(now_ts, quiet_hours) -> bool:
    """now 是否落在谷时区间内（支持跨零点）。"""
    segs = quiet_hours if isinstance(quiet_hours, list) else parse_quiet_hours(quiet_hours)
    if not segs:
        return False
    import datetime
    lt = datetime.datetime.fromtimestamp(now_ts)
    h = lt.hour + lt.minute / 60.0
    for a, b in segs:
        if a == b:
            continue
        if a < b:
            if a <= h < b:
                return True
        else:                      # 跨零点，例如 23:00-07:00
            if h >= a or h < b:
                return True
    return False


def day_key(now_ts) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(now_ts).strftime("%Y%m%d")


def decide(*, now, target, last_interact=0, last_poked=0,
           skipped_after_poke=False, today_count=0, cfg=None) -> dict:
    """返回 {"act": bool, "reason": str}。reason 恒定给出，便于日志与排障。"""
    cfg = dict(cfg or {})
    min_silence = float(cfg.get("poke_proactive_min_silence") or MIN_SILENCE_DEFAULT)
    cap = int(cfg.get("poke_proactive_daily_cap") or DAILY_CAP_DEFAULT)

    if not str(target or "").strip():
        return {"act": False, "reason": "没有目标"}
    if in_quiet_hours(now, cfg.get("quiet_hours") or ""):
        return {"act": False, "reason": "谷时静默"}
    if today_count >= cap:
        return {"act": False, "reason": f"今日已达上限（{today_count}/{cap} 人）"}
    # 上一次戳完对方一句话都没说 → 不再戳，直到他先开口（标记由 message.in 清掉）。
    # 不设时限："被无视后 24 小时再戳一次"的观感比不戳更差，而且拿不到"他到底看没看到"。
    if skipped_after_poke:
        return {"act": False, "reason": "上次戳完没回应，等他先说话"}
    if last_poked and (now - float(last_poked)) < RETRY_WINDOW:
        return {"act": False, "reason": "24h 内已戳过"}
    if not last_interact:
        # 从没互动过的人不在"主动"范围里 —— 那不叫主动找老朋友，叫陌生打扰
        return {"act": False, "reason": "没有互动记录"}
    silence = now - float(last_interact)
    if silence < min_silence:
        return {"act": False, "reason": f"沉默不够（{int(silence / 60)} 分钟 < {int(min_silence / 60)} 分钟）"}
    return {"act": True, "reason": f"可以戳（已沉默 {int(silence / 3600)} 小时）"}


def pick_target(now, candidates, state, cfg=None) -> dict:
    """从候选里挑第一个可戳的。candidates: [{target, last_interact, last_poked, ignored}]"""
    today = day_key(now)
    st = dict(state or {})
    count = int((st.get("daily") or {}).get(today) or 0)
    for c in (candidates or []):
        d = decide(now=now, target=c.get("target"),
                   last_interact=c.get("last_interact") or 0,
                   last_poked=c.get("last_poked") or 0,
                   skipped_after_poke=bool(c.get("ignored")),
                   today_count=count, cfg=cfg)
        if d["act"]:
            return {"target": c.get("target"), "reason": d["reason"]}
    return {"target": "", "reason": "没有可戳的对象"}
