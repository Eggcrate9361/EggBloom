# -*- coding: utf-8 -*-
"""戳一戳反射层：把稳定习惯、当前状态和临场模糊度压成动作。

纯函数模块，不读文件、不发网络、不 import bot_core。调用方负责：
  1. 完成自回流 / 名单 / 黑名单 / 重复事件等硬过滤；
  2. 从人格、关系、心情、群聊状态和历史反馈中收集 facts；
  3. 按返回的 action 执行，或在 action=model 时交给主模型。

actions:
  skip   不回应
  poke   反戳
  meme   发一张表情包
  speak  进入主链生成一句话
  model  本地把握不足，交给主模型决定文字或动作
"""
from __future__ import annotations

from copy import deepcopy
import re

ACTIONS = ("skip", "poke", "meme", "speak")
_MODEL_ACTION_RE = re.compile(r"\[\s*(?:POKE|戳|戳一戳|反戳)\s*\]", re.I)
MODEL_ACTION_HINT = (
    "\n【可选动作】想戳回去才输出 [POKE]；也可以回话、只发表情或保持沉默。"
    "不要默认反戳，[POKE] 只表示动作，不要附正文。"
)


def parse_model_action(text):
    """解析主模型动作标记。返回 (去掉标记的文本, 是否选择反戳)。"""
    raw = str(text or "")
    wanted = bool(_MODEL_ACTION_RE.search(raw))
    clean = _MODEL_ACTION_RE.sub("", raw).strip() if wanted else raw
    return clean, wanted


def _bounded(value, lo=-2.0, hi=2.0):
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _fact(facts, key, default=None):
    return (facts or {}).get(key, default)


def _meme_emotion(facts, scores):
    mood = str(_fact(facts, "mood", "neutral"))
    count = int(_fact(facts, "poke_count", 1) or 1)
    if mood == "negative":
        return "嫌弃"
    if count >= 3:
        return "炸毛"
    if scores.get("poke", 0) >= scores.get("speak", 0):
        return "烦躁"
    return "呆滞"


def decide(facts: dict | None = None) -> dict:
    """返回动作决策。

    facts 只接受可序列化值，便于日志回放：
      relationship       ordinary / close / very_close
      mood               positive / neutral / negative
      persona            playful / clingy / cold / tired
      scene              group / private
      active_conversation bool
      serious_topic      bool
      night              bool
      poke_count         int
      seconds_since_previous_poke float | None
      last_action        skip / poke / meme / speak / model / ""
      seconds_since_last_action float | None
      feedback           {action: -1..1}
    """
    f = dict(facts or {})
    relation = str(_fact(f, "relationship", "ordinary"))
    mood = str(_fact(f, "mood", "neutral"))
    persona = str(_fact(f, "persona", "playful"))
    scene = str(_fact(f, "scene", "group"))
    active = bool(_fact(f, "active_conversation", False))
    serious = bool(_fact(f, "serious_topic", False))
    night = bool(_fact(f, "night", False))
    count = max(1, int(_fact(f, "poke_count", 1) or 1))
    previous_gap = _fact(f, "seconds_since_previous_poke")
    last_action = str(_fact(f, "last_action", "") or "")
    last_gap = _fact(f, "seconds_since_last_action")
    feedback = _fact(f, "feedback", {}) or {}

    def hard(action, reason, confidence=0.96):
        return {
            "action": action,
            "emotion": _meme_emotion(f, {}),
            "confidence": confidence,
            "reason": reason,
            "scores": {},
        }

    # 硬状态优先：不玩反戳，也不把严肃上下文变成打闹。
    if serious:
        return hard("skip", "serious_topic")
    if relation == "ordinary" and count == 1:
        if active and mood == "positive" and persona == "playful":
            return hard("model", "ordinary_first_but_active", 0.42)
        return hard("skip", "ordinary_first_poke")
    if night and relation != "very_close":
        return hard("skip", "night_non_intimate")
    if persona in ("cold", "tired") and relation != "very_close":
        return hard("skip", f"{persona}_persona")

    scores = {name: 0.0 for name in ACTIONS}

    if relation == "very_close":
        scores["poke"] += 0.58
        scores["meme"] += 0.18
        scores["speak"] += 0.16
        scores["skip"] += 0.04
    elif relation == "close":
        scores["poke"] += 0.42
        scores["meme"] += 0.28
        scores["speak"] += 0.34
        scores["skip"] += 0.12
    else:
        scores["poke"] += 0.08
        scores["meme"] += 0.22
        scores["speak"] += 0.38
        scores["skip"] += 0.42

    if count >= 3:
        scores["poke"] -= 1.2
        scores["meme"] += 0.32
        scores["skip"] += 0.52
    elif count == 2:
        scores["poke"] -= 0.9
        scores["meme"] += 0.42
        scores["speak"] += 0.12
        scores["skip"] += 0.18

    if previous_gap is not None:
        try:
            gap = float(previous_gap)
            if gap <= 20:
                scores["poke"] -= 0.5
                scores["skip"] += 0.18
            elif gap <= 120:
                scores["poke"] -= 0.2
        except (TypeError, ValueError):
            pass

    if active:
        scores["speak"] += 0.16
        scores["meme"] += 0.08
        scores["skip"] -= 0.08
    else:
        scores["speak"] -= 0.16
        scores["skip"] += 0.12

    if mood == "positive":
        scores["poke"] += 0.12
        scores["meme"] += 0.12
    elif mood == "negative":
        scores["poke"] -= 0.45
        scores["speak"] -= 0.18
        scores["skip"] += 0.25

    if persona == "playful":
        scores["poke"] += 0.1
        scores["meme"] += 0.08
    elif persona == "clingy":
        scores["speak"] += 0.1
        scores["poke"] += 0.05
    elif persona in ("cold", "tired"):
        scores["skip"] += 0.12
        scores["poke"] -= 0.2

    if scene == "private" and relation == "very_close":
        scores["speak"] += 0.06

    if last_action == "poke":
        scores["poke"] -= 1.4
        scores["meme"] += 0.14
    elif last_action == "meme":
        scores["meme"] -= 0.45
        scores["speak"] += 0.08
    elif last_action == "speak":
        scores["speak"] -= 0.18

    if last_gap is not None:
        try:
            if float(last_gap) < 60:
                scores["poke"] = -2.0
        except (TypeError, ValueError):
            pass

    for action in ACTIONS:
        scores[action] += _bounded(feedback.get(action, 0.0), -1.0, 1.0)

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ordered[0]
    second_score = ordered[1][1]
    margin = best_score - second_score

    if best_score < 0.4 or margin < 0.12:
        return {
            "action": "model",
            "emotion": _meme_emotion(f, scores),
            "confidence": round(max(0.0, min(0.49, best_score / 2.0)), 3),
            "reason": f"low_margin:{best}:{margin:.2f}",
            "scores": {k: round(v, 3) for k, v in scores.items()},
        }

    return {
        "action": best,
        "emotion": _meme_emotion(f, scores) if best == "meme" else "",
        "confidence": round(max(0.0, min(0.99, best_score)), 3),
        "reason": f"{relation}:count={count}:{last_action or 'none'}",
        "scores": {k: round(v, 3) for k, v in scores.items()},
    }


def explain(decision: dict) -> str:
    """稳定的一行日志摘要。"""
    d = deepcopy(decision or {})
    scores = d.get("scores") or {}
    top = ",".join(f"{k}={v}" for k, v in scores.items()) if scores else "-"
    return (f"action={d.get('action', 'model')} confidence={d.get('confidence', 0)} "
            f"reason={d.get('reason', '')} scores={top}")
