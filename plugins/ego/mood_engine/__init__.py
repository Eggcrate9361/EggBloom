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

# 日内基线（2026-10-24）：按时段给一个"默认心情"，情绪衰减后落到这里而不是 0。
#
# 幅度上限刻意压在 2 以内 —— `_format` 里 score >= 2 就变 happy，<= -1 变 sad。
# 所以值域写在这两个阈值之内，基线**只改倾向、不改标签**：
# 同样的对话，晚上更容易被逗乐，凌晨更容易显得没精神，但不会"每晚八点准时开心"。
# 想更明显就把值加大，但超过 2 就会变成按点切换情绪，那比没有基线更假。
_CIRCADIAN_BASELINE = {
    "凌晨": -0.8,   # 困，反应钝
    "清晨": -0.4,   # 刚醒，还没热起来
    "上午": 0.3,
    "中午": -0.2,   # 饭后犯困
    "下午": 0.2,
    "傍晚": 0.6,
    "晚上": 1.2,    # 一天里状态最好的时候
    "深夜": 0.0,    # 该睡了，情绪回到中性偏低
}

# 基线的硬夹上下限（开区间，不含端点）——与 `_format` 的判定阈值对应：
#   happy 需要 score >= 2，sad 需要 score <= -1。
# 因此单独一条基线 + 零事件必须仍落在 (-1, 2) 内，否则"基线"就变成了
# "按点切换情绪标签"。用户改了配置也越不过这道夹。
_BASELINE_CEIL = 1.9
_BASELINE_FLOOR = -0.9

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
        """返回某群的当前情绪（含自然衰减后的实时值 + 日内基线）

        ★ 2026-10-21 修复：这里原先有两条早退路径返回
        `{"label": "平静", "score": 0, "boost": 0.0}` —— 既缺 `text` 字段，
        label 又用了中文「平静」（其余路径都是 `neutral` 这种英文键）。
        后果有两个：
          · `get_prompt_block` 立刻取 `mood["text"]` → KeyError: 'text'
            （日志里那条 `[MoodEngine] before_reply error: 'text'` 就是它），
            整个心情注入被吞掉，插件等于没开。
          · 即使补上 text，`label == "neutral"` 也判不中「平静」，
            平静时会绕过提前返回、硬塞一句「【当前心情】平静」进提示词。
        统一走 `_format(0.0)`，只有一条出口，结构天然一致。

        ★ 2026-10-24：三条出口统一走 `_effective()`，日内基线只在读取时叠加。
          早退路径也必须叠 —— 否则"这个群还没有情绪记录"时基线失效，
          而这恰恰是新群/重启后最常见的情况。
        """
        if not self.config.get("mood_enabled", True):
            return self._format(self._effective(0.0))
        gid = str(group_id or "default")
        m = self.moods.get(gid)
        if not m:
            return self._format(self._effective(0.0))
        return self._format(self._effective(self._decay_score(m)))

    def _effective(self, score: float) -> float:
        """瞬时情绪 + 日内基线。**只用于读取**，绝不写回 self.moods。"""
        return float(score) + self._baseline_score()

    def get_all(self) -> dict:
        out = {}
        for gid, m in self.moods.items():
            out[gid] = self._format(self._effective(self._decay_score(m)))
        return out

    def _decay_score(self, m: dict) -> float:
        """随时间回归中性：score 按比例向 0 衰减

        ★ 注意：这里返回的是**瞬时情绪**，不含日内基线。
          基线只在读取路径（get_mood / get_all）上叠加，绝不写回 score ——
          否则基线会被 update() 当成已发生的事件存下来，下次再叠一遍，
          一天下来分数会单调爬升或下滑（典型的"派生值被当状态存"错误）。
        """
        score = float(m.get("score", 0))
        updated = float(m.get("updated", time.time()))
        elapsed = max(0.0, time.time() - updated)
        decay_sec = float(self.config.get("decay_seconds", 600))
        if decay_sec <= 0:
            return score
        ratio = 1.0 - min(1.0, elapsed / decay_sec)
        return score * ratio

    def _baseline_score(self) -> float:
        """日内情绪基线（2026-10-24）。

        在这之前情绪衰减永远回到 0 —— 意思是"一天里任何时候平静起来都一模一样"。
        真人不是这样：晚上精神好，凌晨容易低落。差别不大，但连续几十轮下来
        "它每个时段都一个样"是能被感觉到的。

        幅度刻意压在 (-1, 2) 区间内：这个区间对应 label=neutral，
        所以基线**不会强制切换情绪标签**，只是让情绪更容易往某个方向偏。
        基线一旦大到能把 neutral 顶成 happy/sad，就会变成"每天晚上八点准时开心"，
        比没有基线更假。

        不进 prompt 的额外成本：neutral 时 get_prompt_block 返回空串，
        所以大部分时候它一个字都不加。
        """
        table = self.config.get("circadian_baseline")
        if not isinstance(table, dict):
            table = _CIRCADIAN_BASELINE
        try:
            from harness.time_ctx import period_of
            period = period_of(time.time())[0]
        except Exception:
            return 0.0
        try:
            v = float(table.get(period, 0.0))
        except (TypeError, ValueError):
            return 0.0
        # ★ 结构保证：单独一条基线（没有任何对话事件）不得把情绪推出 neutral。
        #   `_format` 的阈值是 >=2 → happy、<=-1 → sad，所以把基线夹在开区间内，
        #   "0 + 基线"必然仍是 neutral。写死了这条，才谈得上"只改倾向不改标签" ——
        #   光靠默认值自觉是不够的，用户随时可以在配置里把晚上改成 5。
        return max(_BASELINE_FLOOR, min(_BASELINE_CEIL, v))

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
