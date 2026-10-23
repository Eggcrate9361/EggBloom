# -*- coding: utf-8 -*-
"""群聊决策提示词 v3 —— 场感优先，规则兜底。

v2 是五条 if-else 规则表：
    Bot参与过对话 → 回复；问句 → 回复；两人互聊 → 不回复；
    无意义短消息 → 不回复；不确定 → 回复
这套规则的问题不是错，是**用规则替代场感**。人判断插不插嘴靠的是
「这句谁在跟谁说、自己上次开口多久前、群里现在是不是已经太吵」，
不是一张表。表只能表达"这类消息该不该回"，表达不了"这一刻该不该开口"。

v3 改成两段：
  上半 = 场感数据（说话人、时间间隔、Bot 上次开口、5 分钟发言占比、对话是否闭合）
  下半 = 三条底线规则（@ 必回、纯噪声不回、拿不准就回）
规则只保留"不能错"的那几条，其余交给模型读场感。

★ 一条纪律（来自 life-frequency 的教训）：
  提示词里印的数值必须和代码里实际送进来的字段一一对应。
  印一个代码不给的数，比不印更糟 —— 模型会拿它编。
  所以下面每个 {占位符} 都必须能在 build_prompt 里找到对应实参。
"""

# 场感块：所有数值都注明单位与含义，避免模型把"0.6"当成"60%"或"6 次"
PROMPT_TEMPLATE = """你是群聊里的一个人，现在要判断「这一刻该不该开口」。不是判断这条消息值不值得回，是判断你插话合不合适。

Bot: {bot_name}（QQ：{bot_qq}）

【最近对话】（[群友] 是别人，[我] 是你自己说过的话）
{chat_log}

【现在这条】
{viewer}：{message}

【场感】
{field_block}

【判断口径】
真人插话不靠规则，靠这几件事：
- 【谁在跟谁说】对方是在问你、问大家，还是跟另一个人聊。两人一来一回且跟你无关，插进去就是打断。
- 【你自己有多吵】5 分钟发言占比超过 25% 就偏多了，超过 50% 是刷屏。这时候该收，不该接。
- 【上一次开口多久前】刚说完又接像复读机；隔很久才冒一句，才像突然想说话。
- 【对话停在哪】对方说完没人接了（间隔大）→ 接一句比不接好；正在热聊 → 别打断。
- 【你接得上吗】接不上就别硬接。安静不丢人，硬接一句废话才丢人。

【不能错的几条】
- 被 @、被点名、被引用 → 必须回复，此时不看上面任何判断。
- 纯噪声（单个表情、单个字、纯符号、复读同一句）→ 不回复。
- 拿不准 → 回复。少说一句的代价比漏掉一句大。

只输出 JSON，不要解释：
{{"should_reply":true或false,"reason":"一句话理由","atmosphere":"氛围","atmosphere_confidence":0.0-1.0,"strategy":"策略","strategy_detail":"建议"}}

氛围：闲聊/争论/求助/搞笑/安静/八卦/争吵/通知/用户对话中/自言自语
策略：轻松闲聊/严肃回答/幽默吐槽/引导话题/安慰/不回复/简短回应/共情安慰/八卦参与"""


def _fmt_ago(sec) -> str:
    """秒 → 人话。负数/None 表示无数据，明确写「无记录」而不是编一个数。"""
    try:
        s = float(sec)
    except (TypeError, ValueError):
        return "无记录"
    if s < 0:
        return "无记录"
    if s < 60:
        return f"{s:.0f}秒前"
    if s < 3600:
        return f"{s / 60:.0f}分钟前"
    return f"{s / 3600:.1f}小时前"


def build_field_block(**kw) -> str:
    """把场感数据渲染成给模型看的几行。

    缺失的数据一律写「无记录」——绝不省略行、也不填默认数。
    省略会让模型以为"没有这条约束"，填数会让它当真。
    """
    lines = []

    lines.append("- 你上一次在这个群说话：" + _fmt_ago(kw.get("bot_last_reply_ago", -1)))

    share = kw.get("bot_share_5min", -1)
    try:
        sh = float(share)
    except (TypeError, ValueError):
        sh = -1.0
    if sh < 0:
        lines.append("- 你最近 5 分钟的发言占比：无记录（群刚活跃或话太少）")
    else:
        pct = int(round(sh * 100))
        if pct >= 50:
            hint = "已经刷屏了，该收"
        elif pct >= 25:
            hint = "偏多，这一条最好别接"
        else:
            hint = "正常"
        lines.append(f"- 你最近 5 分钟的发言占比：{pct}%（{hint}）")

    lines.append("- 这批消息最后一条距今：" + _fmt_ago(kw.get("batch_idle_gap", -1)))

    cnt = kw.get("batch_msg_count", -1)
    if isinstance(cnt, int) and cnt >= 0:
        lines.append(f"- 这一批几条消息合成：{cnt} 条")

    closed = kw.get("conversation_closed")
    if closed is True:
        lines.append("- 对话状态：对方说完就停了，没人接话")
    elif closed is False:
        lines.append("- 对话状态：还在来回，有人接着")

    return "\n".join(lines)


def build_prompt(message: str, recent_msgs: list, bot_replies: list,
                 bot_name: str = "小a", bot_qq: str = "", history: list = None,
                 sender: str = "", **field_kw) -> str:
    """构建群聊决策提示词 —— 场感优先。

    sender：当前这条消息的真实发送人 QQ（由 bot_core 从缓冲区直接给）。
            不给时退回"对方" —— **不要**从 recent_msgs[-1] 猜：
            那个列表里混着 bot 自己发出去的消息（_remember_own_group_msg
            会把自己的话登记进去），猜出来经常是 bot 自己。
    field_kw 直接透传给 build_field_block；缺字段时渲染成「无记录」。
    """

    chat_lines = []

    if recent_msgs:
        for msg in recent_msgs[-6:]:
            if isinstance(msg, (list, tuple)) and len(msg) > 1:
                sid = str(msg[1])
                text = str(msg[2])[:60] if len(msg) > 2 else str(msg[0])[:60]
                # ★ 标注改成 [我] / [群友]：v2 用 [Bot] / [后四位号码]，
                #   模型分不清"Bot"是别人对它的称呼还是它自己 —— 会把
                #   自己说过的话当成"有个人在说话"，于是接着答自己的话。
                prefix = "[我]" if (bot_qq and sid == str(bot_qq)) else "[群友]"
                chat_lines.append(f"{prefix} {text}")
            elif isinstance(msg, dict):
                text = str(msg.get("text", msg.get("content", msg)))[:60]
                role = "[我]" if str(msg.get("role", "")) == "assistant" else "[群友]"
                chat_lines.append(f"{role} {text}")
            else:
                chat_lines.append(f"[群友] {str(msg)[:60]}")

    if bot_replies:
        for reply in bot_replies[-2:]:
            text = str(reply)[:60] if not isinstance(reply, (list, tuple)) else (
                str(reply[1])[:60] if len(reply) > 1 else str(reply[0])[:60])
            # ★ 去重（2026-10-23）：recent_msgs 里可能已经包含 bot 自己发的消息 ——
            #   bot_core 的 _remember_own_group_msg 会把自己的话登记进
            #   GROUP_RECENT_MSGS。此时再追加 bot_replies 就会把同一句话印两遍，
            #   模型看到「我: 刚在刷图 / 我: 刚在刷图」会以为自己在复读。
            _t = (text or "").strip()
            if _t and any(_t in ln for ln in chat_lines if ln.startswith("[我]")):
                continue
            chat_lines.append(f"[我] {text}")

    chat_log_text = "\n".join(chat_lines[-8:]) if chat_lines else "（无）"

    # 当前消息的说话人：用传入的真实 sender，不从 recent_msgs 猜。
    viewer = "对方"
    if sender and str(sender) != str(bot_qq):
        viewer = f"群友{str(sender)[-4:]}"

    return PROMPT_TEMPLATE.format(
        chat_log=chat_log_text,
        message=message[:300],
        viewer=viewer,
        bot_name=bot_name,
        bot_qq=bot_qq,
        field_block=build_field_block(**field_kw),
    )