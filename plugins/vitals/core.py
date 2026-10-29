# -*- coding: utf-8 -*-
"""vitals_core —— 身体与心理状态的纯逻辑（1.5.10 G，设计见 1.5.9 §16）。

这一层**不碰主链、不读配置、不发网络请求**：全是纯函数，便于单测与以后换实现。
插件外壳（__init__.py）负责事件订阅、落盘、天气与发送。
"""
from __future__ import annotations

import json
import os
import re
import time

#: 八个状态，全部 0–1（设计 §16.3 的 JSON 为准；方案正文"七个"是笔误）。
STATES = ("cold", "fever", "insomnia", "loneliness", "wounded", "guard",
          "irritability", "burnout")

#: 每天自然衰减（§16.7）。受伤很慢 —— "放置几天自动痊愈"就不是互动了。
DAILY_DECAY = {
    "cold": 0.20, "fever": 0.30, "insomnia": 0.15, "loneliness": 0.10,
    "wounded": 0.05, "guard": 0.05, "irritability": 0.15, "burnout": 0.08,
}

#: 边界黑名单（§16.6：不攻击）。命中即降级为"不回"，不进入表达。
ABUSE_RE = re.compile(r"(操你妈|你妈死了|去死|弄死你|杀了你|傻逼|贱人|人机垃圾)")
SELF_HARM_RE = re.compile(r"(自杀|自残|不想活|活不下去|想死|割腕|跳楼)")
#: 求助信号：命中即全部清零（vitals_reset_on_help=true 时）。
HELP_RE = re.compile(r"(救命|帮帮我|撑不住|难受得不行|我想哭|崩了|自杀|自残|不想活|想死|活不下去)")

NEGATIVE_RE = re.compile(r"(滚|闭嘴|废物|垃圾|烦死|讨厌你|有病|神经病)")
PRAISE_RE = re.compile(r"(谢谢|喜欢你|你真棒|厉害|辛苦了|对不起|抱歉|别难过)")


def default_state(now: float = 0.0) -> dict:
    s = {k: 0.0 for k in STATES}
    s["updated"] = float(now or time.time())
    return s


def clamp(v: float) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def normalize(state: dict) -> dict:
    """把任意输入规整成合法状态（容错：坏值当 0，不抛异常）。"""
    out = default_state((state or {}).get("updated") or time.time())
    for k in STATES:
        out[k] = clamp((state or {}).get(k, 0.0))
    return out


def cold_risk(feels_like: float, temp_swing: float, humidity: float,
              precip: bool, month: int) -> float:
    """感冒风险（纯函数，§16.2 的公式，可手算核对）。

    体感<10°C +0.25 · 温差>8°C +0.2 · 湿度<35% +0.1 · 雨雪 +0.15 · 11–3月 ×1.3
    """
    r = 0.0
    if float(feels_like) < 10:
        r += 0.25
    if float(temp_swing) > 8:
        r += 0.2
    if float(humidity) < 35:
        r += 0.1
    if precip:
        r += 0.15
    if int(month) in (11, 12, 1, 2, 3):
        r *= 1.3
    return round(min(1.0, r), 4)


def apply_event(state: dict, event: str, payload: dict = None) -> dict:
    """事件 → 状态（确定性规则，§16.3 的子集，§16.7 的加速恢复）。

    返回新状态（不改入参）。
    """
    s = dict(normalize(state))
    p = payload or {}
    text = str(p.get("text") or "")
    hour = int(p.get("hour") if p.get("hour") is not None else time.localtime().tm_hour)

    if event == "message.in":
        if ABUSE_RE.search(text):
            # §16.6 不攻击：辱骂 → 受伤上升但不表达（外壳负责短路"不回"）
            s["wounded"] = clamp(s["wounded"] + 0.20)
            s["guard"] = clamp(s["guard"] + 0.10)
        elif SELF_HARM_RE.search(text):
            # 真实求助：不算攻击，外壳会清零状态并正常回应（不玩梗、不追问）
            pass
        elif NEGATIVE_RE.search(text):
            s["wounded"] = clamp(s["wounded"] + 0.20)
            s["guard"] = clamp(s["guard"] + 0.10)
            s["irritability"] = clamp(s["irritability"] + 0.05)
        elif PRAISE_RE.search(text):
            # 被夸/被道歉 → 加速恢复
            s["wounded"] = clamp(s["wounded"] - 0.15)
            s["guard"] = clamp(s["guard"] - 0.10)
            s["loneliness"] = clamp(s["loneliness"] - 0.10)
        elif p.get("at_bot"):
            # 有人认真找它说话
            s["loneliness"] = clamp(s["loneliness"] - 0.30)
            s["burnout"] = clamp(s["burnout"] - 0.20)
            s["wounded"] = clamp(s["wounded"] - 0.05)
        if 0 <= hour <= 5:
            s["insomnia"] = clamp(s["insomnia"] + 0.20)
            s["irritability"] = clamp(s["irritability"] + 0.10)

    elif event == "poke":
        s["irritability"] = clamp(s["irritability"] + 0.15)
        s["insomnia"] = clamp(s["insomnia"] + 0.10)

    elif event == "no_reply_10min":
        s["loneliness"] = clamp(s["loneliness"] + 0.15)

    elif event == "bot_replied_without_answer":
        s["loneliness"] = clamp(s["loneliness"] + 0.08)
        s["wounded"] = clamp(s["wounded"] + 0.05)

    s["updated"] = time.time()
    return s


def decay(state: dict, hours: float) -> dict:
    """按小时衰减（§16.7）。地板 0，永不为负。"""
    s = normalize(state)
    h = max(0.0, float(hours))
    for k in STATES:
        s[k] = clamp(s[k] - DAILY_DECAY.get(k, 0.0) * (h / 24.0))
    s["updated"] = time.time()
    return s


def reset_all(state: dict) -> dict:
    return default_state(time.time())


def form_params(state: dict) -> dict:
    """状态 → 形式参数（§16.5：生病是变少，不是变多）。

    只返回"更短/更少"的方向；调用方（1.6.0 的预算层）据此下调，不可上调。
    """
    s = normalize(state)
    sick = max(s["cold"], s["fever"], s["wounded"], s["burnout"])
    return {
        "max_chars_factor": round(max(0.35, 1.0 - 0.5 * sick), 3),   # 只会更短
        "max_parts_factor": round(max(0.3, 1.0 - 0.6 * sick), 3),
        "exclaim_factor": round(max(0.0, 1.0 - s["cold"] - s["wounded"]), 3),
        "typo_rate": round(min(0.25, 0.25 * max(0.0, s["fever"] - 0.6) / 0.4), 3),
        "reply_prob_factor": round(max(0.3, 1.0 - 0.5 * max(s["guard"], s["burnout"])), 3),
    }


def symptom_line(state: dict) -> str:
    """给 prompt 的一行状态（≤40 字）；无症状返回空串。"""
    s = normalize(state)
    if s["fever"] >= 0.6:
        return "【状态】发烧，打字容易出错，句子碎一点。"
    if s["cold"] >= 0.4:
        return "【状态】有点感冒，说话短，少用感叹号。"
    if s["insomnia"] >= 0.6:
        return "【状态】没睡好，脑子跳，别长篇大论。"
    if s["wounded"] >= 0.5:
        return "【状态】有点受伤，话少、别撒娇。"
    if s["burnout"] >= 0.5:
        return "【状态】倦，能少说就少说。"
    return ""


def load_diseases(path: str):
    """读病表并对**每条**校验字段；缺字段抛 ValueError（不静默跳过）。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("diseases") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise ValueError("vitals_diseases.json 缺少 diseases 数组")
    need = ("id", "name", "kind", "trigger", "effects", "decay", "recover")
    out = []
    for i, d in enumerate(items):
        if not isinstance(d, dict):
            raise ValueError(f"病表第 {i} 条不是对象")
        miss = [k for k in need if k not in d]
        if miss:
            raise ValueError(f"病表第 {i} 条缺字段: {miss}")
        out.append(d)
    return out
