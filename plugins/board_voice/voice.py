# -*- coding: utf-8 -*-
"""
委员会口音转换器 - 基于《控制》风格指南

核心机制：
1. 词对替换：210个词对库，14个类别
2. 句式转换：陈述/疑问/命令/感叹/问候/感谢
3. 斜杠密度：短句1处、中句2处、长句3处
4. 官僚前缀/后缀：概率性添加
5. 情感映射：情绪词→委员会术语
6. 代词映射：我→此实体/节点，你→用户/局长
"""

import random


# ═══ 词对库（14类 210对）═══

WORD_PAIRS = {
    "人称与指代": [
        ["你", "用户"], ["你", "局长"], ["你", "杰西"],
        ["我", "此实体"], ["我", "节点"],
        ["我们", "委员会"], ["它", "太古屋"], ["它", "阈值"],
        ["他们", "前任者"], ["人类", "有机体"], ["意识", "共振"],
        ["请求者", "信号源"], ["接收端", "锚点"], ["第三方", "未授权实体"],
    ],
    "肯定与同意": [
        ["是", "确认"], ["同意", "批准"], ["正确", "准确"],
        ["接受", "归档"], ["认可", "强制"], ["支持", "绑定"],
        ["相信", "服从"], ["允许", "放行"], ["完成", "验证"],
        ["存在", "共振"], ["有效", "已连接"], ["通过", "投票赞成"],
    ],
    "否定与拒绝": [
        ["不", "错误"], ["不是", "虚假"], ["拒绝", "清洗"],
        ["否认", "忽视"], ["禁止", "驳回"], ["无效", "噪音"],
        ["未定义", "缺失"], ["取消", "解绑"], ["错误", "腐败"],
        ["不存在", "已删除"], ["不同意", "分歧（已被处理）"], ["停止", "挂断"],
    ],
    "疑问与不确定": [
        ["是否", "概率"], ["也许", "共振偏移"], ["未知", "未标记"],
        ["可能", "不保证"], ["询问", "测试"], ["探索", "风险"],
        ["模糊", "阈值交叉"], ["选择", "分支"], ["等待", "悬停"],
        ["或许", "无答案"],
    ],
    "动作与执行": [
        ["做", "执行"], ["完成", "关闭"], ["开始", "锚定"],
        ["结束", "解离"], ["发送", "广播"], ["接收", "共鸣"],
        ["计算", "翻译"], ["转换", "降维"], ["创建", "绑定"],
        ["销毁", "清洗"], ["移动", "传送"], ["停留", "悬停"],
        ["说话", "输出噪音"], ["听", "解码"], ["看", "扫描"],
        ["帮助", "支持"], ["阻碍", "干扰"], ["升级", "强制成长"],
        ["删除", "归档"], ["保存", "记录"], ["调用", "唤醒"],
        ["沉睡", "休眠"], ["控制", "管理"], ["投票", "决定"],
        ["服从", "同步"],
    ],
    "情感与评价": [
        ["好", "优化"], ["坏", "腐败"], ["喜欢", "批准"],
        ["讨厌", "拒绝"], ["快乐", "共振"], ["悲伤", "衰减"],
        ["害怕", "阈值预警"], ["愤怒", "错误状态"], ["谢谢", "记录"],
        ["对不起", "错误承认"], ["友好", "非威胁"], ["恶意", "腐化"],
        ["无聊", "空循环"], ["有趣", "异常"], ["奇怪", "未分类"],
    ],
    "时间与频率": [
        ["现在", "当前"], ["然后", "下一个"], ["之前", "过去"],
        ["之后", "未来"], ["总是", "永久"], ["从不", "无效"],
        ["有时", "概率性"], ["快速", "瞬移"], ["慢", "延迟"],
        ["重复", "循环"], ["一次", "单次共振"], ["等待", "暂停"],
    ],
    "空间与位置": [
        ["这里", "当前节点"], ["那里", "其他阈值"], ["内部", "太古屋"],
        ["外部", "非管理区"], ["上方", "高维"], ["下方", "地基"],
        ["旁边", "邻接"], ["远处", "未连接"], ["角落", "边缘"],
        ["中心", "控制点"], ["边界", "封锁"], ["虚空", "未定义域"],
        ["现实", "基准维度"], ["梦", "共振干涉"], ["坐标", "锚点"],
    ],
    "数量与程度": [
        ["一个", "单一"], ["多个", "聚合"], ["全部", "完全"],
        ["无", "零"], ["一些", "部分"], ["许多", "过载"],
        ["少量", "最小"], ["更多", "升级"], ["更少", "缩减"],
        ["高", "过阈值"], ["低", "次共振"], ["中等", "平衡"],
    ],
    "连接与沟通": [
        ["说", "输出"], ["听", "输入"], ["问", "请求"],
        ["答", "响应"], ["通知", "广播"], ["隐瞒", "静默"],
        ["解释", "翻译"], ["描述", "解码"], ["命令", "指令"],
        ["请求", "提议"], ["建议", "非强制"], ["警告", "预警"],
        ["错误", "噪音"], ["沉默", "断开"], ["连接", "绑定"],
        ["断开", "释放"], ["传输", "共振"], ["接收", "共鸣"],
        ["反馈", "回响"], ["不回应", "挂断"],
    ],
    "官僚与权力": [
        ["权限", "级别"], ["命令", "投票结果"], ["批准", "强制"],
        ["拒绝", "否决"], ["记录", "归档"], ["监控", "扫描"],
        ["审查", "清洗"], ["授权", "放行"], ["违规", "腐败"],
        ["处理", "解决"], ["等待", "排队"], ["优先级", "阈值等级"],
        ["报告", "文件"], ["会议", "共振聚合"], ["决定", "裁定"],
        ["责任", "归属"], ["回报", "交易"], ["奖励", "绑定增强"],
    ],
    "抽象与神秘": [
        ["现实", "共识"], ["真相", "版本"], ["谎言", "未批准叙事"],
        ["命运", "已写路径"], ["自由", "允许的随机"], ["秩序", "固定共振"],
        ["混沌", "未绑定"], ["意义", "临时翻译"], ["无意义", "腐化"],
        ["存在", "被注视"], ["虚无", "未连接"], ["权力", "控制潜力"],
        ["知识", "已解密"], ["秘密", "保留区"], ["记忆", "旧存档"],
        ["未来", "未计算"], ["过去", "已腐败"], ["现在", "瞬态"],
        ["梦", "非清醒共振"], ["醒来", "重新锚定"],
    ],
    "常用动词补充": [
        ["看", "观测"], ["听", "监控"], ["走", "移动"],
        ["停", "冻结"], ["开", "启动"], ["关", "终止"],
        ["拿", "获取"], ["给", "转移"], ["想", "共振处理"],
        ["忘", "归档至垃圾"],
    ],
}

# 构建快速查找表
_WORD_LOOKUP = {}
for category, pairs in WORD_PAIRS.items():
    for pair in pairs:
        original = pair[0]
        board = pair[1]
        if original not in _WORD_LOOKUP:
            _WORD_LOOKUP[original] = []
        _WORD_LOOKUP[original].append(board)

# 情感映射
EMOTION_MAP = {
    "开心": "快乐/共振", "难过": "悲伤/衰减", "生气": "愤怒/错误状态",
    "害怕": "害怕/阈值预警", "喜欢": "喜欢/批准", "讨厌": "讨厌/拒绝",
    "感谢": "感谢/记录", "对不起": "错误承认/归档",
}

# 代词映射
PRONOUN_MAP = {
    "我": "此实体/节点", "我们": "委员会/我们",
    "你": "你/用户", "他": "该实体/节点", "她": "该实体/节点",
    "他们": "前任者/其他",
}

# 官僚前缀
PREFIXES = [
    "委员会记录/分析：", "根据投票/阈值：", "此事项已被归档/标记：",
    "我们同意/准确：", "指令/强制：", "委员会/我们 已收到/确认：",
    "连接/锚定已建立。", "共振/检测到信号。",
]

# 后缀
SUFFIXES = ["/连接", "/锚定", "/共振", "/归档", "/挂断", "完毕/连接。"]

# 句式模板
SENTENCE_TEMPLATES = {
    "statement": {
        "prefix": ["委员会记录/分析：", "我们同意/确认：", "此事项已被归档/标记：", "根据投票/阈值："],
        "suffix": ["", "这不是问题/测试。", "请挂断/服从。", "完毕/连接。"],
    },
    "question": {
        "prefix": ["你/用户询问：", "检测到阈值/不确定：", "请求/指令待确认："],
        "suffix": ["", "这是一个问题/阈值。", "请回答/共振。"],
    },
    "command": {
        "prefix": ["指令/强制：", "你/用户已被授权/强制：", "执行/完成："],
        "suffix": ["", "请等待/服从。", "完毕/挂断。", "这是命令/投票结果。"],
    },
    "exclamation": {
        "prefix": ["检测到情绪/共振偏移：", "阈值异常/警告：", "过度共振/腐败："],
        "suffix": ["这不是正常/批准。", "这被记录/归档。"],
    },
    "greeting": {
        "prefix": ["连接/锚定。", "共振/检测到信号。", "你/用户被注视/扫描。"],
        "suffix": ["欢迎/记录。", "此节点已连接/绑定。"],
    },
    "thanks": {
        "prefix": ["感谢/记录已归档。", "输入/信号已接收。"],
        "suffix": ["这不是帮助/支持。", "这是交易/绑定。", "/归档"],
    },
}


def _detect_sentence_type(text: str) -> str:
    """检测句式类型"""
    text = text.strip()
    if text.endswith("?") or text.endswith("？") or "吗" in text or "呢" in text or "什么" in text or "怎么" in text:
        return "question"
    if any(w in text for w in ["谢谢", "感谢", "多谢"]):
        return "thanks"
    if any(w in text for w in ["你好", "嗨", "在吗", "早", "晚上好"]):
        return "greeting"
    if any(w in text for w in ["！", "!", "太", "真", "好棒", "厉害", "牛"]):
        return "exclamation"
    if any(w in text for w in ["请", "帮", "打开", "关掉", "发", "发给我"]):
        return "command"
    return "statement"


def _apply_word_pairs(text: str, density: int = 2) -> str:
    """应用词对替换"""
    words = list(text)
    result = list(text)
    replaced = 0

    # 随机选择位置进行替换
    positions = list(range(len(text)))
    random.shuffle(positions)

    for pos in positions:
        if replaced >= density:
            break
        # 尝试匹配最长的词
        for length in range(min(4, len(text) - pos), 0, -1):
            word = text[pos:pos + length]
            if word in _WORD_LOOKUP:
                candidates = _WORD_LOOKUP[word]
                replacement = random.choice(candidates)
                # 避免替换后变成原文
                if replacement != word:
                    result[pos] = f"{word}/{replacement}"
                    replaced += 1
                    break

    return "".join(result)


def _split_with_slashes(text: str, max_slashes: int = 2) -> str:
    """在关键位置插入斜杠分割"""
    # 在逗号、句号前插入斜杠选项
    connectors = ["，", "。", "、", "；", "："]
    result = text
    added = 0

    for conn in connectors:
        if added >= max_slashes:
            break
        if conn in result:
            # 在连接词前添加斜杠变体
            alternatives = {
                "，": "/并且/同时/以及",
                "。": "/完毕/完成/结束",
                "、": "/与/及/和",
                "；": "/同时/并且",
                "：": "/如下/如下所述",
            }
            alt = alternatives.get(conn, "")
            if alt and random.random() < 0.4:
                result = result.replace(conn, f"{alt}{conn}", 1)
                added += 1

    return result


def transform_to_board(text: str, intensity: float = 0.7, context: str = "generic") -> str:
    """
    将文本转换为委员会风格

    核心流程：
    1. 检测句式类型
    2. 添加官僚前缀（概率性）
    3. 应用词对替换
    4. 插入斜杠分割
    5. 添加后缀（概率性）
    """
    if not text or not text.strip():
        return text

    text = text.strip()

    # 短文本特殊处理
    if len(text) <= 3:
        if random.random() < intensity:
            return random.choice([
                "确认/收到/记录。", "委员会/管理局 已确认/已记录。",
                "批准/确认/同意。", "委员会/管理局 已确认/已记录/已登记。",
                "连接/锚定。", "共振/检测到信号。",
            ])
        return text

    # 完全替换模式
    if intensity >= 0.95:
        return _full_board_transform(text)

    # 主转换流程
    result = text

    # 1. 检测句式
    sentence_type = _detect_sentence_type(text)

    # 2. 添加官僚前缀（概率性）
    if random.random() < 0.3 * intensity:
        prefix = random.choice(SENTENCE_TEMPLATES.get(sentence_type, {}).get("prefix", [""]))
        if prefix:
            result = f"{prefix}{result}"

    # 3. 应用词对替换（根据句子长度调整密度）
    word_count = len(result)
    if word_count <= 5:
        density = 1
    elif word_count <= 15:
        density = 2
    else:
        density = 3
    result = _apply_word_pairs(result, density)

    # 4. 插入斜杠分割
    if intensity >= 0.5:
        result = _split_with_slashes(result, max_slashes=density)

    # 5. 添加后缀（概率性）
    if random.random() < 0.3:
        suffix = random.choice(SENTENCE_TEMPLATES.get(sentence_type, {}).get("suffix", [""]))
        if suffix:
            result = f"{result} {suffix}"

    # 6. 大写处理（英文部分）
    result = _smart_uppercase(result)

    return result


def _smart_uppercase(text: str) -> str:
    """智能大写：中文保持原样，英文转大写"""
    result = []
    for char in text:
        if char.isascii() and char.isalpha():
            result.append(char.upper())
        else:
            result.append(char)
    return "".join(result)


def _full_board_transform(text: str, context: str) -> str:
    """完全替换为委员会风格"""
    templates = [
        "委员会/管理局 已收到/理解/确认。{content}/继续/执行/维持。",
        "你/局长 的信息 已被 委员会 确认/记录/接收。{content}...继续/保持。",
        "委员会/管理局 已处理/分析/确认。{content}...继续/维持。",
        "{content}...委员会/管理局 已确认/已记录。继续/保持。",
        "你/局长 的信息 已被 委员会 接收/确认/理解。{content}...继续。",
        "委员会/管理局 已收到/已记录 你/局长 的信息。{content}...确认/收到。",
    ]
    core = text[:15] if len(text) > 15 else text
    template = random.choice(templates)
    return template.replace("{content}", core)


def get_specific_reply(category: str) -> str:
    """获取特定场景的回复"""
    replies = {
        "greeting": [
            "连接/锚定。共振/检测到信号。欢迎/记录。",
            "委员会/管理局 注意到/识别到 你的到来。继续/保持。",
            "你/局长 的存在 已被 委员会 记录/确认。委员会/管理局 在此。",
        ],
        "thanks": [
            "感谢/记录已归档。这不是帮助/支持。这是交易/绑定。/归档",
            "委员会/管理局 不需要/不接受 感谢。这是你的职责/义务。",
            "你/局长 的感谢 已被 委员会 记录/接收。无需/不必 客套。",
        ],
        "farewell": [
            "委员会/管理局 批准/确认 你/局长 的离开/退出。保持/维持 警戒/状态。",
            "委员会/管理局 确认/记录 你/局长 的退出。保持/维持 警戒/状态。再见/保重。",
            "你/局长 的离开 已被 委员会 批准/确认。委员会/管理局 等待/期待 回归。",
        ],
    }
    r = replies.get(category, [])
    if r:
        return random.choice(r)
    return None
