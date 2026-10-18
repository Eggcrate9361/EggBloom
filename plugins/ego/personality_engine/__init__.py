# -*- coding: utf-8 -*-
"""
personality_engine 人格引擎插件 v0.2.0

核心能力（来自 personality-engine-final.md）：
- ConversationState：话题/模式/连续追问/情绪
- ExampleRetriever：按场景检索 few-shot 示例
- build_personality_prompt：动态组装 prompt
- post_process：禁用词/颜文字/长度/口头禅
- ActiveReplyEngine：空闲时主动搭话
- LearningEngine：夜间批量学习
- build_memory_context：叙事化记忆注入

注意：主逻辑作为插件提供，由 bot_core / 其他插件按需调用。
不强制侵入 bot_core.py。
"""

import json
import os
import random
import time
from typing import Any, Dict, List, Optional

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
PERSONALITY_PATH = os.path.join(os.path.dirname(os.path.dirname(PLUGIN_DIR)), "personality.txt")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(PLUGIN_DIR)), "personality", "examples")
STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(PLUGIN_DIR)), "conversation_state")
PROHIBITED_PATH = os.path.join(os.path.dirname(os.path.dirname(PLUGIN_DIR)), "personality", "prohibited_patterns.txt")

PLUGIN_META = {
    "name": "personality_engine",
    "version": "0.2.0",
    "description": "人格引擎：会话状态 + 记忆注入 + few-shot + 动态 prompt + 学习闭环",
    "author": "EggBloom",
    "group_only": False,
    "enabled": True,
    "dependencies": [],
    "hooks": ["before_reply", "after_reply"],
    "priority": 20,
    "estimated_tokens": 0,
    "tags": ["人格"],
    "page": "plugins/personality_engine/ui.html",
}

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(EXAMPLES_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)


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
        print(f"[PersonalityEngine] 保存失败 {path}: {e}")


def parse_personality(content: str) -> Dict[str, Any]:
    sections: Dict[str, str] = {}
    current_section = None
    current_content: List[str] = []

    for line in content.split("\n"):
        if line.startswith("【") and "】" in line:
            if current_section:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = line.strip()
            current_content = []
        else:
            current_content.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_content).strip()

    return {"raw": content, "sections": sections}


def _distill_persona() -> str:
    """蒸馏人格卡（由「这样脆弱的东西」插件发布），存在则优先于手写人设。

    读取的是插件的 data/published/active.persona.txt —— 由该插件的
    publish / disable / rollback 动作维护，这里只读不写。
    读取失败时静默回退到手写人设，绝不能因为蒸馏插件出问题而拖垮 bot。
    """
    try:
        # PLUGIN_DIR = app/plugins/ego/personality_engine → 三级 dirname 是 app 根
        _app = os.path.dirname(os.path.dirname(os.path.dirname(PLUGIN_DIR)))
        _p = os.path.join(_app, "plugins", "这样脆弱的东西",
                          "data", "published", "active.persona.txt")
        if _p and os.path.exists(_p):
            with open(_p, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                return content
    except Exception:
        pass
    return ""


def get_personality() -> Dict[str, Any]:
    """读取人格，返回 raw/sections/config。

    优先级：蒸馏人格卡（若已发布）> personality.txt。
    """
    _distilled = _distill_persona()
    if _distilled:
        return {
            "raw": _distilled,
            "sections": parse_personality(_distilled).get("sections", {}),
            "config": get_config(),
        }

    if not os.path.exists(PERSONALITY_PATH):
        return {"raw": "", "sections": {}, "config": get_config()}

    try:
        with open(PERSONALITY_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return {"raw": "", "sections": {}, "config": get_config()}

    return {
        "raw": content,
        "sections": parse_personality(content).get("sections", {}),
        "config": get_config(),
    }


def save_personality(sections: Dict[str, str], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """保存人格配置到 personality.txt，并可选更新 config"""
    parts = []

    section_order = [
        "【你是谁】",
        "【日常】",
        "【性格】",
        "【细节补充】",
        "【口头禅】",
        "【情感表达】",
        "【特殊情绪】",
        "【情绪关键词映射】",
        "【说话方式】",
        "【群聊行为】",
        "【遇到各种事】",
        "【绝对不能说的话】",
        "【图片生成】",
        "【自主学习成果】",
    ]

    for section in section_order:
        if section in sections:
            parts.append(section)
            content = sections[section].strip()
            if content:
                parts.append(content)

    for section, content in sections.items():
        if section not in section_order:
            parts.append(section)
            if content:
                parts.append(content)

    new_content = "\n".join(parts)

    try:
        with open(PERSONALITY_PATH, "w", encoding="utf-8") as f:
            f.write(new_content)
        if isinstance(config, dict):
            set_config(config)
        return {"success": True, "path": PERSONALITY_PATH}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_config() -> Dict[str, Any]:
    return _load_json(CONFIG_PATH, {
        "auto_backup": True,
        "backup_count": 5,
        "example_enabled": True,
        "active_reply": {
            "enable": False,
            "possibility": 0.1,
            "idle_minutes": 30,
            "whitelist": [],
        },
    })


def set_config(data: Dict[str, Any]) -> Dict[str, Any]:
    config = get_config()
    for key in ["auto_backup", "backup_count", "example_enabled"]:
        if key in data:
            config[key] = data[key]
    if "active_reply" in data and isinstance(data["active_reply"], dict):
        config["active_reply"] = data["active_reply"]
    _save_json(CONFIG_PATH, config)
    return config


# ------------------------------------------------------------------
# 会话状态
# ------------------------------------------------------------------
class ConversationState:
    """轻量会话状态，不搞复杂权重。"""

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        self.current_topic: Optional[str] = None
        self.mode: str = "normal"
        self.turn_count: int = 0
        self.last_user_msg: str = ""
        self.last_bot_reply: str = ""
        self.emotion: str = "neutral"
        self.affection: float = 0.5
        self.last_update: float = time.time()
        self._load()

    def _path(self) -> str:
        return os.path.join(STATE_DIR, f"{self.user_id}.json")

    def _load(self):
        data = _load_json(self._path(), {})
        for key in [
            "current_topic",
            "mode",
            "turn_count",
            "last_user_msg",
            "last_bot_reply",
            "emotion",
            "affection",
            "last_update",
        ]:
            if key in data:
                setattr(self, key, data[key])

    def _save(self):
        _save_json(self._path(), {
            "current_topic": self.current_topic,
            "mode": self.mode,
            "turn_count": self.turn_count,
            "last_user_msg": self.last_user_msg,
            "last_bot_reply": self.last_bot_reply,
            "emotion": self.emotion,
            "affection": self.affection,
            "last_update": self.last_update,
        })

    def update(self, user_msg: str, bot_reply: str):
        self.last_user_msg = user_msg
        self.last_bot_reply = bot_reply
        self.last_update = time.time()

        if self._is_follow_up(user_msg):
            self.turn_count += 1
        else:
            self.turn_count = 0
            self.current_topic = self._extract_topic(user_msg)

        if self._is_serious(user_msg):
            self.mode = "serious"
        elif self._is_playful(user_msg):
            self.mode = "playful"
        else:
            self.mode = "normal"

        self.emotion = self._detect_emotion(user_msg)
        self._save()

    def _is_follow_up(self, msg: str) -> bool:
        follow_up_hints = ["呢", "那", "然后", "还有", "再说", "再说说", "往下", "继续"]
        return any(msg.endswith(h) or f" {h}" in msg for h in follow_up_hints) or self.turn_count > 0

    def _extract_topic(self, msg: str) -> str:
        if not msg:
            return "default"
        return msg[:8]

    def _is_serious(self, msg: str) -> bool:
        serious_keywords = ["难受", "抱歉", "对不起", "麻烦", "帮帮我", "怎么办", "焦虑", "抑郁", "累", "压力"]
        return any(k in msg for k in serious_keywords)

    def _is_playful(self, msg: str) -> bool:
        playful_keywords = ["哈哈", "笑死", "好耶", "太强了", "牛", "666", "🐮", "草", "绷不住了"]
        return any(k in msg for k in playful_keywords)

    def _detect_emotion(self, msg: str) -> str:
        positive = ["开心", "哈哈", "好耶", "谢谢", "喜欢", "爱", "棒", "厉害", "笑", "舒服"]
        negative = ["难受", "烦", "讨厌", "难过", "哭", "累", "压力", "焦虑", "抑郁", "吐"]
        if any(k in msg for k in positive):
            return "happy"
        if any(k in msg for k in negative):
            return "sad"
        return "neutral"

    def to_prompt(self) -> str:
        return (
            f"当前对话状态：话题={self.current_topic}，模式={self.mode}，"
            f"情绪={self.emotion}，连续追问={self.turn_count}，"
            f"好感度={self.affection:.1f}"
        )


def get_state(user_id: str = "default") -> ConversationState:
    return ConversationState(user_id)


# ------------------------------------------------------------------
# 记忆上下文
# ------------------------------------------------------------------
def build_memory_context(
    user_id: str,
    message: str,
    state: ConversationState,
    private_memory=None,
    self_learning=None,
    super_memory=None,
) -> str:
    parts = [state.to_prompt()]

    if super_memory and hasattr(super_memory, "recall"):
        try:
            results = super_memory.recall(user_id, message, top_k=3)
            if results:
                parts.append("相关记忆：" + "；".join(str(r) for r in results[:3]))
        except Exception:
            pass

    if private_memory and hasattr(private_memory, "get_relevant"):
        try:
            mem = private_memory.get_relevant(user_id, message)
            if mem:
                parts.append("私聊记忆：" + str(mem))
        except Exception:
            pass

    if self_learning and hasattr(self_learning, "get_style_hints"):
        try:
            hints = self_learning.get_style_hints(user_id)
            if hints:
                parts.append("学习特征：" + str(hints))
        except Exception:
            pass

    return "\n".join(parts) if parts else ""


# ------------------------------------------------------------------
# prompt 组装
# ------------------------------------------------------------------
def build_personality_prompt(
    state: ConversationState,
    memory_ctx: str,
    examples: List[Dict[str, str]],
    user_msg: str,
    user_id: str = "preview",
) -> str:
    parts = []

    if memory_ctx:
        parts.append(memory_ctx)

    if examples:
        parts.append("参考示例：")
        for idx, ex in enumerate(examples[:2], 1):
            user_example = ex.get("user", ex.get("input", ""))
            bot_example = ex.get("bot", ex.get("reply", ""))
            if user_example and bot_example:
                parts.append(f"{idx}. 用户：{user_example}\n   小a：{bot_example}")

    if state.mode == "serious":
        parts.append("注意：当前用户情绪偏严肃，回复更稳、更简洁，少玩梗。")
    elif state.mode == "playful":
        parts.append("注意：当前气氛轻松，可以适当玩梗，保持活泼。")

    if state.emotion == "happy":
        parts.append("用户看起来比较开心，可以更放松。")
    elif state.emotion == "sad":
        parts.append("用户看起来有点低落，先接住情绪，别急着开玩笑。")

    return "\n".join(parts) if parts else ""


# ------------------------------------------------------------------
# 示例检索
# ------------------------------------------------------------------
class ExampleRetriever:
    def __init__(self, base_path: str = EXAMPLES_DIR):
        self.base_path = base_path
        self.examples = self._load_all(base_path)

    def _load_all(self, base_path: str) -> Dict[str, List[Dict[str, str]]]:
        examples: Dict[str, List[Dict[str, str]]] = {}
        if not os.path.isdir(base_path):
            return examples
        for name in os.listdir(base_path):
            if not (name.endswith(".yaml") or name.endswith(".json")):
                continue
            scene = name.rsplit(".", 1)[0]
            path = os.path.join(base_path, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    examples[scene] = data
                elif isinstance(data, dict):
                    examples[scene] = [data]
            except Exception:
                pass
        return examples

    def retrieve(self, message: str, state: ConversationState, top_k: int = 2) -> List[Dict[str, str]]:
        if not self.examples:
            return []
        candidates = []
        text = (message or "").lower()
        for scene, items in self.examples.items():
            score = 0
            lowered = scene.lower()
            if lowered in text:
                score += 2
            keywords = lowered.split("_")
            for kw in keywords:
                if kw and kw in text:
                    score += 1
            candidates.append((score, items))
        candidates.sort(key=lambda x: x[0], reverse=True)
        results: List[Dict[str, str]] = []
        for _, items in candidates[:top_k]:
            results.extend(items[:3])
        return results[:top_k]


def get_example_retriever() -> ExampleRetriever:
    return ExampleRetriever()


# ------------------------------------------------------------------
# 动态温度
# ------------------------------------------------------------------
def get_dynamic_temperature(state: ConversationState) -> float:
    base = 0.75
    if state.mode == "serious":
        base = 0.6
    elif state.mode == "playful":
        base = 0.9
    if state.emotion == "happy":
        base += 0.1
    elif state.emotion == "sad":
        base -= 0.1
    return max(0.4, min(1.2, base))


# ------------------------------------------------------------------
# 后处理
# ------------------------------------------------------------------
def post_process(reply: str, state: ConversationState) -> str:
    prohibited = load_prohibited_patterns()
    for pattern in prohibited:
        if pattern and pattern in reply:
            reply = reply.replace(pattern, "")
    reply = reply.strip()
    if len(reply) > 240:
        reply = reply[:240].rstrip() + "…"
    return reply


def load_prohibited_patterns() -> List[str]:
    try:
        if not os.path.exists(PROHIBITED_PATH):
            return []
        with open(PROHIBITED_PATH, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception:
        return []


# ------------------------------------------------------------------
# 主动回复
# ------------------------------------------------------------------
class ActiveReplyEngine:
    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or get_config().get("active_reply", {})
        self.enable = bool(self.cfg.get("enable", False))
        self.possibility = float(self.cfg.get("possibility", 0.1))
        self.idle_minutes = int(self.cfg.get("idle_minutes", 30))
        self.whitelist = list(self.cfg.get("whitelist", []))

    def should_reply(self, user_id: str, group_id: str) -> bool:
        if not self.enable:
            return False
        if self.whitelist and user_id not in self.whitelist:
            return False
        if random.random() > self.possibility:
            return False
        return True

    def pick_topic(self, state: ConversationState) -> str:
        candidates = [
            "在干嘛",
            "最近有没有遇到好玩的事",
            "今天过得怎么样",
            "有没有在摸鱼",
            "要不要聊点轻松的",
        ]
        if state.mode == "serious":
            candidates = ["要不多聊聊？", "需要我陪着说会儿吗？", "想说说看吗？"]
        return random.choice(candidates)


def get_active_reply_engine() -> ActiveReplyEngine:
    return ActiveReplyEngine(get_config().get("active_reply"))


# ------------------------------------------------------------------
# 学习引擎
# ------------------------------------------------------------------
class LearningEngine:
    def learn(self, recent_dialogues: List[Dict[str, str]], example_retriever: Optional[ExampleRetriever] = None):
        if not recent_dialogues:
            return
        scores: List[float] = []
        for item in recent_dialogues:
            reply = item.get("bot", "")
            score = 0.0
            if any(c in reply for c in ["…", "！", "?"]):
                score += 0.5
            if any(c in reply for c in ["哈哈", "真的假的", "笑死", "好家伙"]):
                score += 0.5
            if 10 <= len(reply) <= 120:
                score += 0.5
            scores.append(score)

        avg = sum(scores) / len(scores)
        high_quality = [
            recent_dialogues[i] for i, s in enumerate(scores) if s >= max(avg, 2.5)
        ][:5]

        self._save_learning_candidates(high_quality)
        low_quality = [
            recent_dialogues[i] for i, s in enumerate(scores) if s < 1.0
        ][:5]
        self._save_prohibited([item.get("bot", "") for item in low_quality if item.get("bot")])

    def _save_learning_candidates(self, candidates: List[Dict[str, str]]):
        path = os.path.join(DATA_DIR, "learning_candidates.json")
        try:
            existing = _load_json(path, [])
            if not isinstance(existing, list):
                existing = []
            existing.extend(candidates)
            _save_json(path, existing[:50])
        except Exception as e:
            print(f"[PersonalityEngine] 保存 learning_candidates 失败: {e}")

    def _save_prohibited(self, replies: List[str]):
        try:
            existing = set(load_prohibited_patterns())
            for reply in replies:
                reply = reply.strip()
                if reply and reply not in existing:
                    existing.add(reply)
            with open(PROHIBITED_PATH, "w", encoding="utf-8") as f:
                for item in sorted(existing):
                    f.write(item + "\n")
        except Exception as e:
            print(f"[PersonalityEngine] 保存 prohibited 失败: {e}")


def get_learning_engine() -> LearningEngine:
    return LearningEngine()


# ------------------------------------------------------------------
# 示例库
# ------------------------------------------------------------------
def get_examples(scene: str = "") -> Dict[str, Any]:
    try:
        files = []
        if not os.path.isdir(EXAMPLES_DIR):
            return {"examples": [], "scenes": []}
        for name in os.listdir(EXAMPLES_DIR):
            if not (name.endswith(".yaml") or name.endswith(".json")):
                continue
            files.append(name.rsplit(".", 1)[0])
        scenes = sorted(files)
        examples = []
        for s in scenes:
            path = os.path.join(EXAMPLES_DIR, s + ".json")
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if scene and s != scene:
                    continue
                if isinstance(data, list):
                    examples.extend(data)
                elif isinstance(data, dict):
                    examples.append(data)
            except Exception:
                pass
        return {"examples": examples, "scenes": scenes}
    except Exception as e:
        return {"examples": [], "scenes": [], "error": str(e)}


def save_examples(data: Dict[str, Any]) -> Dict[str, Any]:
    examples = data.get("examples") if isinstance(data, dict) else None
    if examples is None and isinstance(data, list):
        examples = data
    if not isinstance(examples, list):
        return {"success": False, "error": "examples must be a list"}
    path = os.path.join(EXAMPLES_DIR, "examples.json")
    try:
        os.makedirs(EXAMPLES_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(examples, f, ensure_ascii=False, indent=2)
        return {"success": True, "path": path}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ------------------------------------------------------------------
# 插件钩子
# ------------------------------------------------------------------
def _get_user_id(context: Optional[dict] = None) -> str:
    ctx = context or {}
    return str(ctx.get("sender") or ctx.get("user_id") or "default")


def before_reply(message: str = "", group_id: str = "", context: Optional[dict] = None, **kwargs) -> Optional[dict]:
    """before_reply 钩子：注入人格引擎的 prompt/温度，不改动主流程。"""
    try:
        user_id = _get_user_id(context)
        state = get_state(user_id)
        config = get_config()
        use_examples = config.get("example_enabled", True)
        retriever = get_example_retriever()
        examples = retriever.retrieve(message, state, top_k=2) if use_examples else []
        memory_ctx = build_memory_context(
            user_id,
            message,
            state,
            private_memory=kwargs.get("private_memory"),
            self_learning=kwargs.get("self_learning"),
            super_memory=kwargs.get("super_memory"),
        )
        prompt = build_personality_prompt(state, memory_ctx, examples, message)
        temperature = get_dynamic_temperature(state)
        return {
            "action": "inject",
            "content": prompt,
            "temperature": temperature,
            "personality_state": {
                "topic": state.current_topic,
                "mode": state.mode,
                "emotion": state.emotion,
                "turn_count": state.turn_count,
            },
        }
    except Exception as e:
        print(f"[PersonalityEngine] before_reply error: {e}")
        return None


def after_reply(message: str = "", group_id: str = "", reply: str = "", context: Optional[dict] = None, **kwargs) -> Optional[dict]:
    """after_reply 钩子：更新会话状态，并做轻度后处理。"""
    try:
        user_id = _get_user_id(context)
        state = get_state(user_id)
        state.update(message, reply)
        processed = post_process(reply, state)
        if processed != reply:
            return {"action": "override", "reply": processed}
        return None
    except Exception as e:
        print(f"[PersonalityEngine] after_reply error: {e}")
        return None


# ------------------------------------------------------------------
# 对外接口
# ------------------------------------------------------------------
__all__ = [
    "PLUGIN_META",
    "get_personality",
    "save_personality",
    "get_config",
    "set_config",
    "get_examples",
    "save_examples",
    "ConversationState",
    "ExampleRetriever",
    "build_memory_context",
    "build_personality_prompt",
    "post_process",
    "get_active_reply_engine",
    "get_learning_engine",
    "before_reply",
    "after_reply",
    "load_prohibited_patterns",
]


def on_load():
    print("[PersonalityEngine] 人格引擎 v0.2.0 已启用")

# ── 通用插件通道动作（WebUI plugin_id='ego.personality_engine'） ──
def _action_save_personality(params=None):
    params = params or {}
    sections = params.get("sections") or params.get("personality") or {}
    return save_personality(sections)


def _action_save_examples(params=None):
    params = params or {}
    return save_examples(params.get("examples") or [])


PLUGIN_ACTIONS = {
    "get_config": lambda params: get_config(),
    "set_config": lambda params: set_config(params or {}),
    "get_personality": lambda params: get_personality(),
    "save_personality": _action_save_personality,
    "get_examples": lambda params: get_examples(str((params or {}).get("scene") or "")),
    "save_examples": _action_save_examples,
    "get_state": lambda params: get_state(),
}
