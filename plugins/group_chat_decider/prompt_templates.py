# -*- coding: utf-8 -*-
"""
群聊决策提示词模板 v2 — 精简 + 完整上下文
"""

PROMPT_TEMPLATE = """你是群聊判断器，判断Bot是否该回复当前消息。

Bot: {bot_name}（QQ：{bot_qq}）

最近对话：
{chat_log}

当前消息：
{sender}: {message}

规则：
- Bot参与过对话 / 用户在跟Bot说话 → 回复
- 问句 / 话题跟Bot兴趣相关 → 回复
- 两人互聊且与Bot无关 → 不回复
- 无意义短消息（嗯/哦/哈哈/6）→ 不回复
- 不确定时 → 回复（宁可多回不漏回）

JSON:
{{"reply":true或false,"reason":"一句话理由","atmosphere":"氛围","atmosphere_confidence":0.0-1.0,"strategy":"策略","strategy_detail":"建议"}}

氛围：闲聊/争论/求助/搞笑/安静/八卦/争吵/通知/用户对话中/自言自语
策略：轻松闲聊/严肃回答/幽默吐槽/引导话题/安慰/不回复/简短回应/共情安慰/八卦参与"""


def build_prompt(message: str, recent_msgs: list, bot_replies: list,
                 bot_name: str = "小a", bot_qq: str = "", history: list = None) -> str:
    """构建群聊决策提示词 — 精简上下文"""

    # 只取最近5条消息 + 最近2条Bot回复，够判断即可
    chat_lines = []

    if recent_msgs:
        for msg in recent_msgs[-5:]:
            if isinstance(msg, (list, tuple)) and len(msg) > 1:
                sender = str(msg[1])
                text = str(msg[2])[:60] if len(msg) > 2 else str(msg[0])[:60]
                prefix = "[Bot]" if sender == bot_qq else f"[{sender[-4:]}]"
                chat_lines.append(f"{prefix} {text}")
            elif isinstance(msg, dict):
                text = msg.get("text", msg.get("content", str(msg)))[:60]
                chat_lines.append(f"[用户] {text}")
            else:
                chat_lines.append(f"[用户] {str(msg)[:60]}")

    if bot_replies:
        for reply in bot_replies[-2:]:
            text = str(reply)[:60] if not isinstance(reply, (list, tuple)) else str(reply[1])[:60] if len(reply) > 1 else str(reply[0])[:60]
            chat_lines.append(f"[Bot] {text}")

    chat_log_text = "\n".join(chat_lines[-7:]) if chat_lines else "（无）"

    # 提取当前消息发送者
    sender_display = "用户"
    if recent_msgs:
        last_msg = recent_msgs[-1]
        if isinstance(last_msg, (list, tuple)) and len(last_msg) > 1:
            sid = str(last_msg[1])
            sender_display = bot_name if sid == bot_qq else "用户"

    return PROMPT_TEMPLATE.format(
        chat_log=chat_log_text,
        message=message[:300],
        sender=sender_display,
        bot_name=bot_name,
        bot_qq=bot_qq,
    )
