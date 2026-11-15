# -*- coding: utf-8 -*-
"""schedule_core —— 周日程表的纯逻辑（1.5.10，独立插件）。

设计目标：让"刚下课""在上班"这类话**有事实依据**，而不是模型自由发挥。
  · 配置是周表（星期 + 起止时间 + 标题 + 地点），可选例外日期（放假/调休）；
  · 纯函数：解析 / 当前状态 / 下一段 / 一行 prompt；
  · 不读网络、不碰主链。

数据格式（plugins/schedule/data/config.json）：
    {
      "timezone": "Asia/Shanghai",
      "state_when_free": "在家",
      "entries": [
        {"days": [1,2,3,4,5], "start": "08:00", "end": "11:40",
         "title": "上课", "place": "教室"},
        {"days": [1,2,3,4,5], "start": "13:30", "end": "17:00",
         "title": "实验室", "place": "实验楼"}
      ],
      "exceptions": ["2026-11-01"]
    }
days: 1=周一 … 7=周日。
"""
from __future__ import annotations

import datetime as _dt
import re

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_hm(s: str) -> int:
    """"08:30" → 510（分钟）。非法抛 ValueError（不静默变 0）。"""
    m = _TIME_RE.match(str(s or "").strip())
    if not m:
        raise ValueError(f"时间格式应为 HH:MM：{s!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"时间越界：{s!r}")
    return h * 60 + mi


def parse_entry(e: dict) -> dict:
    """校验并规整一条日程。缺字段/时间倒序 → ValueError。"""
    if not isinstance(e, dict):
        raise ValueError("日程条目不是对象")
    title = str(e.get("title") or "").strip()
    if not title:
        raise ValueError("日程条目缺少 title")
    days = e.get("days") or []
    if not isinstance(days, list) or not days or any(int(d) not in range(1, 8) for d in days):
        raise ValueError(f"日程 {title} 的 days 必须是 1–7 的列表")
    start, end = parse_hm(e.get("start")), parse_hm(e.get("end"))
    if end <= start:
        raise ValueError(f"日程 {title} 的结束时间必须晚于开始")
    return {"days": sorted({int(d) for d in days}), "start": start, "end": end,
            "title": title, "place": str(e.get("place") or "").strip(),
            "start_hm": str(e.get("start")).strip(), "end_hm": str(e.get("end")).strip()}


def parse_config(cfg: dict):
    """解析整份配置 → (entries, exceptions, state_when_free)。坏条目报错不吞。"""
    cfg = cfg or {}
    entries = [parse_entry(e) for e in (cfg.get("entries") or [])]
    exc = set()
    for d in (cfg.get("exceptions") or []):
        try:
            exc.add(_dt.date.fromisoformat(str(d)[:10]))
        except ValueError as e:
            raise ValueError(f"例外日期格式应为 YYYY-MM-DD：{d!r}") from e
    return entries, exc, str(cfg.get("state_when_free") or "在家")


def _now(ts=None, tz=None) -> _dt.datetime:
    if tz:
        try:
            from zoneinfo import ZoneInfo
            return _dt.datetime.fromtimestamp(ts or 0 or _dt.datetime.now().timestamp(), ZoneInfo(tz))
        except Exception:
            pass
    return _dt.datetime.fromtimestamp(ts) if ts is not None else _dt.datetime.now()


def current(entries, exceptions, ts=None, tz=None) -> dict:
    """当前状态：在上什么 / 下一段是什么 / 其余为自由时间。

    返回 {phase: in|before|free, title, place, start, end, next_title, next_start,
          remaining_min}
    """
    now = _now(ts, tz)
    if now.date() in set(exceptions or ()):
        return {"phase": "free", "title": "", "place": "", "start": "", "end": "",
                "next_title": "", "next_start": "", "remaining_min": 0,
                "reason": "exception"}
    today = now.weekday() + 1          # 1=周一
    minutes = now.hour * 60 + now.minute
    todays = [e for e in entries if today in e["days"]]
    for e in todays:
        if e["start"] <= minutes < e["end"]:
            return {"phase": "in", "title": e["title"], "place": e["place"],
                    "start": e["start_hm"], "end": e["end_hm"],
                    "next_title": "", "next_start": "",
                    "remaining_min": e["end"] - minutes}
    upcoming = [e for e in todays if e["start"] > minutes]
    if upcoming:
        nxt = min(upcoming, key=lambda x: x["start"])
        return {"phase": "before", "title": "", "place": "", "start": "", "end": "",
                "next_title": nxt["title"], "next_start": nxt["start_hm"],
                "remaining_min": nxt["start"] - minutes}
    # 今天没有了：找下一次（最多看 7 天）
    for add in range(1, 8):
        day = (today + add - 1) % 7 + 1
        nxts = sorted([e for e in entries if day in e["days"]], key=lambda x: x["start"])
        if nxts:
            return {"phase": "free", "title": "", "place": "", "start": "", "end": "",
                    "next_title": nxts[0]["title"], "next_start": nxts[0]["start_hm"],
                    "remaining_min": (add * 1440) - minutes + nxts[0]["start"]}
    return {"phase": "free", "title": "", "place": "", "start": "", "end": "",
            "next_title": "", "next_start": "", "remaining_min": 0}


def prompt_line(state: dict, free_state: str = "在家") -> str:
    """一行事实（给 prompt）。空 entries 时由调用方决定不注入，这里不编造。"""
    if state.get("reason") == "exception":
        return "【日程】今天放假，没有安排。"
    if state.get("phase") == "in":
        place = f"在{state['place']}" if state.get("place") else ""
        return f"【日程】现在{state['title']}（{state['start']}–{state['end']}{('，' + place) if place else ''}），到 {state['end']} 结束。"
    if state.get("phase") == "before":
        return f"【日程】现在{free_state}，{state['next_start']} 要去{state['next_title']}。"
    if state.get("next_title"):
        return f"【日程】现在{free_state}，下一次安排是 {state['next_title']}（{state['next_start']}）。"
    return f"【日程】现在{free_state}，今天没有别的安排。"


def parse_lines(text: str):
    """把「1-5 08:00-11:40 上课 @教室」这种行格式解析成 entries（UI 用）。

    每行：<天数> <起>-<止> <标题> [@地点]
      天数：1-5 / 1,3,5 / 6,7
    """
    out = []
    for lineno, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            raise ValueError(f"第 {lineno} 行：应为「天数 起-止 标题 [@地点]」")
        days_s, span, title = parts[0], parts[1], " ".join(parts[2:])
        days = []
        for chunk in days_s.replace("，", ",").split(","):
            if "-" in chunk:
                a, b = chunk.split("-", 1)
                days.extend(range(int(a), int(b) + 1))
            else:
                days.append(int(chunk))
        if "-" not in span:
            raise ValueError(f"第 {lineno} 行：时间应为 起-止")
        st, en = span.split("-", 1)
        place = ""
        if "@" in title:
            title, place = title.split("@", 1)
        out.append({"days": days, "start": st, "end": en,
                    "title": title.strip(), "place": place.strip()})
    return out
