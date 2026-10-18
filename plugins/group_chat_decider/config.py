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
# 2026-10-11 用户定调："多花 token 提高判断精准度，硬规则尽量不使用"——
# 对话链/兴趣/话题判断从硬短路降级为 AI 特征后，AI 承担最终裁量，预算放宽：
# 链式思考 JSON（thinking+三维评分+hook_points）400 偏紧易截断→回空/格式异常。
MAX_TOKENS = 800
TEMPERATURE = 0.3
# 2026-10-11：实测 zhipu glm-4-flash 对真实决策 prompt（~1000 token 上下文）
# 要 22-25s，20s 必超时走兜底 → 提到 25s；MAX_TOKENS 翻倍后留到 30s。
DECISION_TIMEOUT = 30

# 上下文窗口
MAX_RECENT_MSGS = 5
MAX_BOT_REPLIES = 3

# 决策阈值
CONFIDENCE_THRESHOLD = 0.3

# ═══════ 降级治理（2026-10-12 用户报："AI请求失败导致氛围未知，跳过回复"）═══════
# 失败不能"静默闭嘴"，但也不能无差别放行（2026-10-10 的 429 风暴教训）：
#   ① 先重试：偶发失败（超时/空/格式）大概率自愈；
#   ② 单次失败 → fail-open：交**主模型**自裁（注入里明确写"不适合就输出 [不说话]"），
#      氛围沿用上次成功决策的结果（带降级标记），不再是"未知"；
#   ③ 连续失败 ≥ 阈值 → 断路器打开：转保守规则（宁当哑巴不乱插嘴），并显式记录。
AI_RETRY_MAX = 2               # 决策调用最多尝试次数（含首次）
DEGRADE_FAIL_WINDOW = 300      # 断路器统计窗口（秒）
DEGRADE_FAIL_THRESHOLD = 3     # 窗口内失败次数 ≥ 此值 → 断路器打开
ATMOSPHERE_TTL = 900           # "沿用上次氛围"的有效期（秒）

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
