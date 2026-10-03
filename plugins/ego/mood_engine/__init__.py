# -*- coding: utf-8 -*-
"""
EGO mood_engine.py —— Bot 自身情绪状态机
=========================================
让 Bot 有自己的"心情曲线"：收到开心的事会开心、被怼会低落，情绪随时间自然衰减回中性，
并影响回复的语气与温度（temperature）。数据持久化到 data/mood.json，按 group 维度区分。

设计要点：
  - mood_score ∈ [-10, 10]，0 为中性
  - after_reply 依据消息关键词与情感基调更新情绪（开心/低落/被夸/被骂）
  - 情绪随自然时间衰减回归中性（decay_seconds 全量回归）
  - before_reply 注入"当前心情"提示 + 按情绪微调 temperature
  - 情绪有"爆发上限"（clamp），避免极端震荡
"""

import os
import json
import time

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
MOOD_PATH = os.path.join(DATA_DIR, "mood.json")

PLUGIN_META = {
    "name": "mood_engine",
    "version": "0.1.0",
    "description": "Bot 情绪状态机：心情曲线、自然衰减、影响语气与温度",
    "author": "EggBloom",
    "enabled": True,
}

DEFAULT_CONFIG = {
    "mood_enabled": True,
    "decay_seconds": 600,        # 情绪回归中性的速度（秒），越小衰减越快
    "max_boost": 8,              # 单次事件最大情绪冲击
    "temperature_range": 0.6,    # 情绪影响温度的最大偏移量
}

MOOD_LABELS = {
    "very_happy": "开心到飞起",
    "happy": "心情不错",
    "neutral": "平静",
    "sad": "有点低落",
    "down": "闷闷不乐",
    "angry": "有点不爽",
}

# 情感关键词
_POSITIVE_WORDS = ["哈哈", "笑死", "好棒", "太强", "厉害", "谢谢", "爱了", "喜欢", "不错", "开心", "真棒", "贴贴", "牛", "强"]
_NEGATIVE_WORDS = ["难过", "哭", "泪", "惨", "伤心", "烦", "累", "唉", "叹气"]
_ANGER_WORDS = ["生气", "气死", "烦死", "滚", "垃圾", "废物", "傻逼", "sb", "脑残", "菜鸡"]
_PRAISE_WORDS = ["小a", "你真好", "爱你", "好可爱", "真棒", "优秀", "喜欢"]
_HARD_INSULT_WORDS = ["傻逼", "sb", "脑残", "废物", "垃圾", "滚", "没用", "蠢", "笨蛋", "狗"]


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[MoodEngine] 保存失败 {path}: {e}")


class MoodEngine:
    """Bot 情绪状态机（按 group 维度）"""

    def __init__(self):
        self.config: dict = dict(DEFAULT_CONFIG)
        saved = _load_json(MOOD_PATH, {})
        if isinstance(saved, dict):
            cfg = saved.get("config", {})
            if isinstance(cfg, dict):
                self.config.update(cfg)
            self.moods = saved.get("moods", {})
        else:
            self.moods = {}
        if not isinstance(self.moods, dict):
            self.moods = {}
        self._normalize()

    def _normalize(self):
        for gid, m in self.moods.items():
            if not isinstance(m, dict):
                self.moods[gid] = {"score": 0, "updated": time.time()}
            else:
                m.setdefault("score", 0)
                m.setdefault("updated", time.time())

    def _save(self):
        _save_json(MOOD_PATH, {"config": self.config, "moods": self.moods})

    # ── 读取 ──

    def get_mood(self, group_id: str = "") -> dict:
        """返回某群的当前情绪（含自然衰减后的实时值）"""
        if not self.config.get("mood_enabled", True):
            return {"label": "平静", "score": 0, "boost": 0.0}
        gid = str(group_id or "default")
        m = self.moods.get(gid)
        if not m:
            return {"label": "平静", "score": 0, "boost": 0.0}
        score = self._decay_score(m)
        return self._format(score)

    def get_all(self) -> dict:
        out = {}
        for gid, m in self.moods.items():
            score = self._decay_score(m)
            out[gid] = self._format(score)
        return out

    def _decay_score(self, m: dict) -> float:
        """随时间回归中性：score 按比例向 0 衰减"""
        score = float(m.get("score", 0))
        updated = float(m.get("updated", time.time()))
        elapsed = max(0.0, time.time() - updated)
        decay_sec = float(self.config.get("decay_seconds", 600))
        if decay_sec <= 0:
            return score
        ratio = 1.0 - min(1.0, elapsed / decay_sec)
        return score * ratio

    def _format(self, score: float) -> dict:
        if score >= 6:
            label = "very_happy"
        elif score >= 2:
            label = "happy"
        elif score <= -6:
            label = "angry"
        elif score <= -3:
            label = "down"
        elif score <= -1:
            label = "sad"
        else:
            label = "neutral"
        return {
            "label": label,
            "text": MOOD_LABELS.get(label, "平静"),
            "score": round(score, 2),
            "boost": round(score / 10.0, 2),  # -1 ~ 1
        }

    # ── 更新 ──

    def update(self, message: str = "", group_id: str = ""):
        """依据收到的消息情感基调更新情绪"""
        if not self.config.get("mood_enabled", True):
            return
        gid = str(group_id or "default")
        m = self.moods.get(gid)
        if not m:
            m = {"score": 0, "updated": time.time()}
            self.moods[gid] = m
        # 先衰减当前值
        current = self._decay_score(m)
        delta = self._delta_from_text(message)
        new_score = current + delta
        new_score = max(-10, min(10, new_score))
        self.moods[gid] = {"score": new_score, "updated": time.time()}
        self._save()

    def _delta_from_text(self, text: str) -> float:
        """从消息文本估算情绪冲击量（负向需较强证据，避免"小a你真好"误伤）"""
        if not text:
            return 0.0
        t = text.lower()
        # 先判定积极（避免辱骂词与夸奖词同现时误伤）
        if any(w in t for w in _PRAISE_WORDS) and not any(w in t for w in _HARD_INSULT_WORDS):
            delta = 2.5
        elif any(w in t for w in _POSITIVE_WORDS):
            delta = 1.2
        else:
            delta = 0.0
        # 再叠加负向（硬辱骂 > 生气 > 消极）
        if any(w in t for w in _HARD_INSULT_WORDS):
            delta -= 4.0
        elif any(w in t for w in _ANGER_WORDS):
            delta -= 2.5
        elif any(w in t for w in _NEGATIVE_WORDS):
            delta -= 1.5
        max_b = float(self.config.get("max_boost", 8))
        return max(-max_b, min(max_b, delta))

    def reset(self, group_id: str = ""):
        gid = str(group_id or "default")
        self.moods[gid] = {"score": 0, "updated": time.time()}
        self._save()

    # ── 注入 ──

    def get_prompt_block(self, group_id: str = "") -> str:
        """生成注入主模型的当前心情提示（含语气建议与温度偏移）"""
        mood = self.get_mood(group_id)
        if not self.config.get("mood_enabled", True):
            return ""
        label = mood["label"]
        if label == "neutral":
            return ""
        advice = {
            "very_happy": "超开心，多用感叹号、表情，语气活泼热情。",
            "happy": "心情很好，回复轻松带点小雀跃。",
            "sad": "有点低落，语气软一点，别太热情。",
            "down": "闷闷不乐，回复简短些，不想多说。",
            "angry": "有点不爽，语气冷淡克制，别阴阳怪气。",
        }.get(label, "")
        lines = [f"【当前心情】{mood['text']}（{mood['score']}）"]
        if advice:
            lines.append(advice)
        return "\n".join(lines)

    def get_temperature(self, group_id: str = "", base: float = None) -> float:
        """按情绪微调温度（happy 更发散，低落更收敛）"""
        mood = self.get_mood(group_id)
        shift = mood["boost"] * float(self.config.get("temperature_range", 0.6))
        if base is None:
            return 1.0 + shift
        return max(0.2, min(2.0, float(base) + shift))


_mood = MoodEngine()


def get_config() -> dict:
    return dict(_mood.config)


def set_config(data: dict) -> dict:
    if isinstance(data, dict):
        for k, v in data.items():
            if k in DEFAULT_CONFIG:
                _mood.config[k] = v
        _mood._save()
    return get_config()


def before_reply(message: str = "", group_id: str = "", context: dict = None, **kwargs) -> dict | None:
    """注入当前心情提示 + 温度偏移"""
    try:
        gid = group_id or (context or {}).get("group_id", "")
        block = _mood.get_prompt_block(gid)
        if not block:
            return None
        temperature = _mood.get_temperature(gid)
        return {"action": "inject", "content": block, "temperature": temperature}
    except Exception as e:
        print(f"[MoodEngine] before_reply error: {e}")
        return None


def after_reply(message: str = "", group_id: str = "", reply: str = "", context: dict = None, **kwargs) -> dict | None:
    """更新情绪"""
    try:
        gid = group_id or (context or {}).get("group_id", "")
        _mood.update(message, gid)
    except Exception as e:
        print(f"[MoodEngine] after_reply error: {e}")
    return None


def on_load():
    print("[MoodEngine] 已加载")


def on_unload():
    print("[MoodEngine] 已卸载")


__all__ = [
    "PLUGIN_META",
    "MoodEngine",
    "get_config",
    "set_config",
    "before_reply",
    "after_reply",
    "reset_mood",
    "get_moods",
    "on_load",
    "on_unload",
]


def reset_mood(group_id: str = "") -> dict:
    _mood.reset(group_id)
    return {"ok": True, "moods": _mood.get_all()}


def get_moods() -> dict:
    return _mood.get_all()


# ── 通用插件通道动作（WebUI plugin_id='ego.mood_engine'） ──
PLUGIN_ACTIONS = {
    "get_config": lambda params: get_config(),
    "set_config": lambda params: set_config(params or {}),
    "get_moods": lambda params: get_moods(),
    "reset": lambda params: reset_mood(str((params or {}).get("scope_id") or "") or None),
}
