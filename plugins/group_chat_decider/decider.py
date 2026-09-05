# -*- coding: utf-8 -*-
"""
群聊决策器 - 概率筛选 + 注意力机制 + 超轻量AI调用

两层过滤：
  第一层：概率门（零成本）→ 大部分消息直接跳过
  第二层：AI读空气（轻量模型）→ 精准判断
"""

import json
import sys
import os
import time
import random
import traceback
from dataclasses import dataclass, asdict
from typing import Optional

try:
    import config as plugin_config
    from prompt_templates import build_prompt
    from attention import AttentionTracker
except ImportError:
    from . import config as plugin_config
    from .prompt_templates import build_prompt
    from .attention import AttentionTracker


@dataclass
class Decision:
    """决策结果"""
    should_reply: bool = True
    reason: str = ""
    atmosphere: str = "闲聊"
    atmosphere_confidence: float = 0.0
    strategy: str = "轻松闲聊"
    strategy_detail: str = ""
    probability_passed: bool = True   # 是否通过概率门
    attention_skipped: bool = False   # 是否被注意力机制跳过

    def to_dict(self) -> dict:
        return asdict(self)

    def to_prompt_injection(self) -> str:
        """注入到主模型 system_prompt 的决策上下文"""
        parts = []
        parts.append(f"【群聊氛围】{self.atmosphere}（置信度 {self.atmosphere_confidence:.1f}）")
        parts.append(f"【回复策略】{self.strategy}——{self.strategy_detail}")
        return "\n".join(parts)


# ═══════ 全局状态 ═══════

_ai_client_cache = {}
_attention_tracker = None
_last_reply_time = {}   # {group_id: timestamp} 每个群最后一次回复时间


def _get_attention_tracker():
    global _attention_tracker
    if _attention_tracker is None:
        _attention_tracker = AttentionTracker(
            decay_rate=plugin_config.ATTENTION_DECAY_RATE,
            message_boost=plugin_config.ATTENTION_MESSAGE_BOOST,
            reply_boost=plugin_config.ATTENTION_REPLY_BOOST,
            threshold=plugin_config.ATTENTION_THRESHOLD,
            cooldown_seconds=plugin_config.ATTENTION_COOLDOWN_SECONDS,
            max_users=plugin_config.MAX_TRACKED_USERS,
        )
    return _attention_tracker


def set_ai_client(client):
    _ai_client_cache["client"] = client


def _get_ai_client():
    client = _ai_client_cache.get("client")
    if client is not None:
        return client

    try:
        import __main__ as main_mod
        if hasattr(main_mod, "ai"):
            client = main_mod.ai
            _ai_client_cache["client"] = client
            return client
    except (ImportError, AttributeError):
        pass

    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import load_config, get_provider_api_key
        from ai_client import AIClient

        cfg = load_config()
        api_url = plugin_config.DECIDER_API_URL or cfg.get("api_url", "")
        provider = cfg.get("api_provider", "deepseek")
        api_key = plugin_config.DECIDER_API_KEY or get_provider_api_key(provider)
        model = plugin_config.DECIDER_MODEL or cfg.get("model_name", "deepseek-chat")

        if api_key or provider == "ollama":
            client = AIClient(api_url, api_key, model, provider=provider, temperature=cfg.get("ai_temperature"))
            _ai_client_cache["client"] = client
            return client
    except Exception as e:
        print(f"[GroupDecider] AI客户端初始化失败: {e}")

    return None


def _parse_json_output(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    if "```json" in text:
        try:
            start = text.index("```json") + 7
            end = text.index("```", start)
            return json.loads(text[start:end].strip())
        except (ValueError, json.JSONDecodeError):
            pass

    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        pass

    return None


# ═══════ 概率筛选层 ═══════

def _probability_gate(group_id: str, user_id: str) -> bool:
    """
    概率门：决定是否允许进入AI决策阶段。
    返回 True = 允许通过，False = 被概率门拦截。
    """
    if not plugin_config.PROBABILITY_ENABLED:
        return True

    now = time.time()

    # 计算当前概率
    prob = plugin_config.INITIAL_PROBABILITY

    # 回复后临时提升
    last_reply = _last_reply_time.get(group_id, 0)
    if now - last_reply < plugin_config.AFTER_REPLY_DURATION:
        elapsed = now - last_reply
        # 从 AFTER_REPLY_BOOST 线性衰减到 INITIAL_PROBABILITY
        ratio = elapsed / plugin_config.AFTER_REPLY_DURATION
        prob = plugin_config.AFTER_REPLY_BOOST * (1 - ratio) + prob * ratio

    # 概率门
    if random.random() < prob:
        return True
    return False


def record_reply(group_id: str, user_id: str = ""):
    """Bot 回复后记录时间（用于概率提升）"""
    _last_reply_time[group_id] = time.time()
    if user_id and plugin_config.ATTENTION_ENABLED:
        _get_attention_tracker().on_reply(group_id, user_id)


def record_message(group_id: str, user_id: str):
    """用户发消息时记录注意力"""
    if plugin_config.ATTENTION_ENABLED and user_id:
        _get_attention_tracker().on_message(group_id, user_id)


def get_attention_scores(group_id: str) -> dict:
    """获取注意力分数（调试用）"""
    return _get_attention_tracker().get_scores(group_id)


# ═══════ 核心决策 ═══════

# Layer 0 硬规则：Bot兴趣关键词
_BOT_INTEREST_KEYWORDS = {"游戏", "番", "编程", "代码", "python", "javascript",
                           "心理学", "精神", "人格", "焦虑", "抑郁", "失眠",
                           "MOBA", "开放世界", "看番", "追番", "写代码",
                           "打游戏", "王者荣耀", "原神", "崩铁", "英雄联盟",
                           "bug", "bug了", "报错", "报错了吗", "跑不起来"}

# Layer 0 硬规则：纯无意义词（短消息跳过）
_SKIP_TRIVIAL = {"1", "2", "3", "6", "嗯", "哦", "哈哈", "hhh", "好的",
                  "行", "ok", "OK", "666", "……", "额", "额。", "噗"}

# Layer 1: 硬放行关键词（消息含这些词 → 大概率要回复）
_LIKELY_REPLY_KEYWORDS = {"小a", "？", "?", "吗", "呢", "吧", "嘛",
                           "怎么", "什么", "为啥", "为什么", "是不是",
                           "有没有", "能不能", "会不会", "要不要"}


def _has_bot_interest(text: str) -> bool:
    """检查消息是否涉及Bot感兴趣的话题"""
    t = text.lower()
    return any(kw in t for kw in _BOT_INTEREST_KEYWORDS)


def _has_likely_reply_signal(text: str) -> bool:
    """检查消息是否有明显需要回复的信号"""
    t = text.lower()
    return any(kw in t for kw in _LIKELY_REPLY_KEYWORDS)


def _is_trivial(text: str) -> bool:
    """检查消息是否纯无意义"""
    return text.strip().lower() in _SKIP_TRIVIAL


def decide(message: str, group_id: str, context: dict = None) -> Optional[Decision]:
    """
    三层决策：硬规则 → 规则预判 → AI读空气
    """
    if not plugin_config.ENABLED:
        return None

    if not message or not message.strip():
        return None

    ctx = context or {}
    recent_msgs = ctx.get("recent_msgs", [])
    bot_replies = ctx.get("bot_replies", [])
    bot_qq = ctx.get("bot_qq", "")
    bot_name = ctx.get("bot_name", "小a")
    history = ctx.get("history", [])

    # 提取当前消息的发送者
    sender_id = ""
    if recent_msgs:
        last_msg = recent_msgs[-1]
        if isinstance(last_msg, (list, tuple)) and len(last_msg) > 1:
            sender_id = str(last_msg[1])

    # 记录注意力
    if sender_id:
        record_message(group_id, sender_id)

    # ══ 注意力冷却检查 ══
    if plugin_config.ATTENTION_ENABLED and sender_id:
        if _get_attention_tracker().should_skip(group_id, sender_id):
            return Decision(
                should_reply=False,
                reason=f"[注意力] 用户 {sender_id} 注意力溢出冷却中",
                atmosphere="冷却中",
                atmosphere_confidence=0.95,
                strategy="冷却跳过",
                strategy_detail="该用户近期互动过多，暂停回复以模拟自然节奏",
                probability_passed=True,
                attention_skipped=True,
            )

    # ═══════ Layer 0: 硬规则（零成本，100%准确） ═══════

    # 纯无意义词 → 跳过
    if _is_trivial(message):
        return Decision(
            should_reply=False,
            reason="[硬规则] 纯无意义内容",
            atmosphere="安静",
            atmosphere_confidence=0.95,
            strategy="不回复",
            strategy_detail="无意义内容无需回复",
            probability_passed=True,
        )

    # 涉及Bot兴趣 → 放行
    if _has_bot_interest(message):
        return Decision(
            should_reply=True,
            reason="[硬规则] 消息涉及Bot感兴趣的话题",
            atmosphere="闲聊",
            atmosphere_confidence=0.9,
            strategy="轻松闲聊",
            strategy_detail="话题与Bot兴趣相关，自然参与讨论",
            probability_passed=True,
        )

    # 有明显回复信号（问句/指向Bot）→ 放行
    if _has_likely_reply_signal(message):
        return Decision(
            should_reply=True,
            reason="[硬规则] 消息含问句或指向性信号",
            atmosphere="闲聊",
            atmosphere_confidence=0.85,
            strategy="轻松闲聊",
            strategy_detail="有问句/指向信号，大概率需要回应",
            probability_passed=True,
        )

    # ═══════ Layer 1: 规则预判（零成本） ═══════

    # 构建完整消息列表：最近消息 + Bot回复
    full_msgs = list(recent_msgs[-10:]) if recent_msgs else []

    # 判断Bot是否在最近对话中
    bot_in_recent = False
    bot_recent_count = 0
    recent_senders = set()
    for msg in full_msgs:
        if isinstance(msg, (list, tuple)) and len(msg) > 1:
            sid = str(msg[1])
            recent_senders.add(sid)
            if sid == bot_qq:
                bot_in_recent = True
                bot_recent_count += 1

    # 也从 bot_replies 判断
    if not bot_in_recent and bot_replies:
        bot_in_recent = True
        bot_recent_count = len(bot_replies[-3:])

    # 从群聊历史检查 Bot 是否参与过对话
    if not bot_in_recent and history:
        for entry in history[-10:]:
            if isinstance(entry, dict):
                if entry.get("role") == "assistant":
                    bot_in_recent = True
                    break

    # 规则1: Bot在对话中 → 放行（对话延续）
    if bot_in_recent and bot_recent_count > 0:
        return Decision(
            should_reply=True,
            reason=f"[规则] Bot在最近对话中参与了{bot_recent_count}条，当前消息可能是对话延续",
            atmosphere="闲聊",
            atmosphere_confidence=0.85,
            strategy="轻松闲聊",
            strategy_detail="Bot刚参与过对话，回应概率高",
            probability_passed=True,
        )

    # 规则2: Bot不在对话中 + 2+用户在互聊 → 跳过
    unique_others = set(s for s in recent_senders if s != bot_qq)
    if len(unique_others) >= 2 and not bot_in_recent:
        # 但如果有回复信号，仍放行给AI判断
        if _has_likely_reply_signal(message):
            pass  # 不跳过，让AI决定
        else:
            return Decision(
                should_reply=False,
                reason=f"[规则] 最近{len(full_msgs)}条消息为{len(unique_others)}个用户间互聊，Bot不参与",
                atmosphere="用户对话中",
                atmosphere_confidence=0.85,
                strategy="不回复",
                strategy_detail="其他用户正在对话，Bot不插嘴",
                probability_passed=True,
            )

    # 规则3: Bot不在对话中 + 只有1个用户 + 短间隔(<15秒连续发) → 可能自言自语
    if len(unique_others) <= 1 and not bot_in_recent and len(full_msgs) >= 3:
        # 检查最近3条是否同一人发的
        last_3_senders = []
        for msg in full_msgs[-3:]:
            if isinstance(msg, (list, tuple)) and len(msg) > 1:
                last_3_senders.append(str(msg[1]))
        if len(last_3_senders) >= 3 and len(set(last_3_senders)) == 1:
            return Decision(
                should_reply=False,
                reason=f"[规则] 单用户连续发{len(last_3_senders)}条，可能自言自语",
                atmosphere="自言自语",
                atmosphere_confidence=0.75,
                strategy="不回复",
                strategy_detail="用户在自言自语，无需回应",
                probability_passed=True,
            )

    # ═══════ Layer 2: AI 读空气（~80-200 token） ═══════
    # 走到这里的都是规则无法确定的模糊情况
    prompt = build_prompt(message, recent_msgs, bot_replies,
                          bot_name=bot_name, bot_qq=bot_qq, history=history)

    client = _get_ai_client()
    if client is None:
        # AI不可用时放行，让主模型自己判断
        return Decision(
            should_reply=True,
            reason="AI客户端不可用，默认放行",
            atmosphere="未知",
            atmosphere_confidence=0.0,
            strategy="待定",
            strategy_detail="AI客户端不可用，由主模型自行判断",
            probability_passed=True,
        )

    try:
        messages = [{"role": "user", "content": prompt}]
        result = client.call(messages, max_tokens=plugin_config.MAX_TOKENS)

        if isinstance(result, tuple):
            reply = result[0] if len(result) > 0 else None
        else:
            reply = result

        if not reply:
            return Decision(
                should_reply=True,
                reason="AI返回为空，默认放行",
                atmosphere="未知",
                atmosphere_confidence=0.0,
                strategy="待定",
                strategy_detail="AI返回为空，由主模型自行判断",
                probability_passed=True,
            )

        data = _parse_json_output(reply)
        if data is None:
            return Decision(
                should_reply=True,
                reason="AI返回格式异常，默认放行",
                atmosphere="未知",
                atmosphere_confidence=0.0,
                strategy="待定",
                strategy_detail="AI返回格式异常，由主模型自行判断",
                probability_passed=True,
            )

        should_reply = bool(data.get("should_reply", True))
        reason = str(data.get("reason", ""))[:200]
        atmosphere = str(data.get("atmosphere", "闲聊"))
        atmosphere_confidence = float(data.get("atmosphere_confidence", 0.0))
        strategy = str(data.get("strategy", "轻松闲聊"))
        strategy_detail = str(data.get("strategy_detail", ""))[:200]

        # 低置信度时默认放行（不确定就回，宁可多回不漏回）
        if atmosphere_confidence < plugin_config.CONFIDENCE_THRESHOLD:
            should_reply = True
            reason = f"[低置信度({atmosphere_confidence:.2f})放行] {reason}"

        return Decision(
            should_reply=should_reply,
            reason=reason,
            atmosphere=atmosphere,
            atmosphere_confidence=atmosphere_confidence,
            strategy=strategy,
            strategy_detail=strategy_detail,
            probability_passed=True,
        )

    except Exception as e:
        print(f"[GroupDecider] 决策异常: {e}")
        traceback.print_exc()
        return Decision(
            should_reply=True,
            reason=f"决策异常: {e}",
            atmosphere="未知",
            atmosphere_confidence=0.0,
            strategy="待定",
            strategy_detail="AI调用异常，默认放行",
            probability_passed=True,
        )
