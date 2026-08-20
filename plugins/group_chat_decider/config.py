# -*- coding: utf-8 -*-
"""
group_chat_decider config.py
插件配置 - 模型、决策阈值、概率筛选、注意力机制
"""

# 是否启用决策插件
ENABLED = True

# 决策调用的模型配置
DECIDER_MODEL = None
DECIDER_API_URL = None
DECIDER_API_KEY = None

# 决策参数
MAX_TOKENS = 400
TEMPERATURE = 0.3
DECISION_TIMEOUT = 20

# 上下文窗口
MAX_RECENT_MSGS = 5
MAX_BOT_REPLIES = 3

# 决策阈值
CONFIDENCE_THRESHOLD = 0.3

# ═══════ 概率筛选层 ═══════
PROBABILITY_ENABLED = False       # 关闭随机概率门——每条消息都进判断
INITIAL_PROBABILITY = 0.25        # 基础回复概率 25%（已关闭）
AFTER_REPLY_BOOST = 0.9           # 刚回复后临时提升到 90%
AFTER_REPLY_DURATION = 60         # 提升持续 60 秒
IDLE_GROUP_BOOST = 0.15

# ═══════ 注意力机制 ═══════
ATTENTION_ENABLED = True
ATTENTION_DECAY_RATE = 0.85       # 每秒衰减系数（0.85 = 每秒衰减15%）
ATTENTION_MESSAGE_BOOST = 0.2     # 用户发消息时注意力+0.2
ATTENTION_REPLY_BOOST = 0.4       # Bot回复该用户时+0.4
ATTENTION_THRESHOLD = 0.8         # 注意力超过此值时进入冷却
ATTENTION_COOLDOWN_SECONDS = 120  # 冷却时长（秒）
MAX_TRACKED_USERS = 50            # 最多追踪的用户数
