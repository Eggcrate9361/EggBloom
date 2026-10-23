# -*- coding: utf-8 -*-
"""
委员会口音插件 - 《控制》The Board 风格

核心特征：大量斜杠分割多选词，呈现"多种可能性同时存在"的超自然感。

用法：
    from board_voice import PLUGIN_META, after_reply
"""

import random
import re
import os
import json
from .voice import transform_to_board  # get_specific_reply 保留在 voice.py，此处不再用

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# ==============================================================
# 上下文关键词检测
# ==============================================================
CONTEXT_KEYWORDS = {
    "greeting": ["你好", "在吗", "你是谁", "自我介绍", "打招呼", "hello", "hi", "嗨", "早", "晚上好", "下午好"],
    "farewell": ["再见", "拜拜", "走了", "溜了", "下线", "bye", "晚安", "先走了", "告辞", "回头见"],
    "thanks": ["谢谢", "感谢", "多谢", "thanks", "谢了", "辛苦了"],
    "praise": ["厉害", "牛", "强", "好棒", "可以", "666", "nice", "good", "太强了", "大佬", "学到了"],
    "complaint": ["烦", "累", "无聊", "讨厌", "气死", "无语", "崩溃", "受不了", "烦死了", "好烦"],
    "emotion": ["开心", "难过", "伤心", "生气", "高兴", "郁闷", "焦虑", "紧张", "激动", "兴奋", "委屈"],
    "question": ["什么", "怎么", "为什么", "哪里", "谁", "几", "多少", "吗", "呢", "？"],
    "joke": ["笑", "哈哈", "乐", "搞笑", "段子", "好笑", "笑死", "笑cry", "逗", "233"],
    "chat": ["嗯", "哦", "是的", "对", "好的", "行", "可以", "ok", "确实", "真的"],
    "sleep": ["困", "睡", "晚安", "休息", "熬夜", "失眠", "睡觉", "打瞌睡"],
    "meal": ["吃", "饭", "饿", "午饭", "晚饭", "早餐", "外卖", "做饭", "食堂", "奶茶"],
    "weather": ["天气", "下雨", "晴天", "热", "冷", "温度", "预报", "下雪", "刮风"],
    "news": ["新闻", "热点", "热搜", "最新", "头条", "发生", "事件"],
    "help": ["帮", "救", "怎么", "求助", "问题", "不会", "教", "指导", "帮忙"],
    "game": ["游戏", "打游戏", "王者", "原神", "吃鸡", "LOL", "steam", "开黑", "排位", "段位"],
    "work": ["工作", "上班", "加班", "项目", "deadline", "会议", "老板", "同事", "工资", "摸鱼"],
    "study": ["学习", "作业", "考试", "复习", "论文", "上课", "大学", "高中", "考研"],
    "shopping": ["买", "购物", "淘宝", "京东", "拼多多", "打折", "优惠", "快递", "下单"],
    "travel": ["旅行", "旅游", "出去玩", "景点", "酒店", "机票", "高铁", "自驾"],
    "health": ["身体", "感冒", "生病", "头疼", "医院", "药", "运动", "健身", "跑步"],
    "money": ["钱", "工资", "收入", "支出", "理财", "基金", "股票", "存钱", "花钱"],
    "relationship": ["朋友", "聊天", "社交", "认识", "约", "聚会", "闺蜜", "兄弟"],
    "entertainment": ["电影", "电视剧", "综艺", "音乐", "歌", "追剧", "看番", "动漫", "小说"],
    "tech": ["电脑", "手机", "软件", "代码", "bug", "编程", "python", "开发", "调试"],
}


CONTEXT_TO_TEMPLATE = {
    "greeting": "daily_greeting",
    "farewell": "daily_farewell",
    "thanks": "generic",
    "praise": "generic",
    "complaint": "daily_complaint",
    "emotion": "daily_emotion",
    "question": "daily_question",
    "joke": "daily_joke",
    "chat": "daily_chat",
    "sleep": "daily_sleep",
    "meal": "daily_meal",
    "weather": "daily_weather",
    "news": "daily_news",
    "help": "daily_help",
    "game": "daily_game",
    "work": "daily_work",
    "study": "daily_study",
    "shopping": "daily_shopping",
    "travel": "daily_travel",
    "health": "daily_health",
    "money": "daily_money",
    "relationship": "daily_relationship",
    "entertainment": "daily_entertainment",
    "tech": "daily_tech",
}

# ==============================================================
# 插件元信息
# ==============================================================
PLUGIN_META = {
    "name": "board_voice",
    "version": "1.2.0",
    "description": "委员会口音 - 模仿《控制》中 The Board 的说话风格（斜 slash 分割多选词）",
    "author": "EggBloom Community",
    "group_only": False,
    # ★ 2026-10-23：默认关闭。这个插件会把**每一句**正常回复改成
    #   「在的/并且/同时/以及，什么事」这种斜杠体，与当前人格方向
    #   （短句、口语、不装）正面冲突。它是个猎奇风格的娱乐插件，
    #   适合想玩的时候单独打开，不适合作为默认。
    #   注意：plugin_state.json 里如果有旧的 true，会盖过这里的默认值，
    #   升级后需要到插件页手动关一次，或删掉 state 里那一项。
    "enabled": False,
    "dependencies": [],
    "hooks": ["after_reply"],
    "priority": 90,
    "estimated_tokens": 0,
    "tags": ["语音", "娱乐"],
    "page": "plugins/board_voice/ui.html",
}

# 转换强度 (0.0-1.0)
INTENSITY = 0.7
_CONFIG_PATH = os.path.join(PLUGIN_DIR, "data", "config.json")


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
        print(f"[BoardVoice] 保存失败 {path}: {e}")


def get_config():
    cfg = _load_json(_CONFIG_PATH, {})
    intensity = cfg.get("intensity", INTENSITY)
    try:
        intensity = max(0.0, min(1.0, float(intensity)))
    except Exception:
        intensity = INTENSITY
    return {"intensity": intensity, "enabled": bool(PLUGIN_META.get("enabled"))}


def set_config(params: dict):
    if not isinstance(params, dict):
        return {"ok": False, "error": "params must be dict"}
    cfg = _load_json(_CONFIG_PATH, {})
    cfg.update(params)
    _save_json(_CONFIG_PATH, cfg)
    if "intensity" in params:
        try:
            globals()["INTENSITY"] = max(0.0, min(1.0, float(params["intensity"])))
        except Exception:
            pass
    return get_config()


# ==============================================================
# 钩子
# ==============================================================

# 颜文字/emoji 正则
EMOTICON_PATTERN = re.compile(
    r'[\(（][^)）]*[)）]'   # (xxx) 或 （xxx）
    r'|[/\\][\\/\-_|]+'      # /xxx/ 或 \xxx\ 
    r'|(?:^|\s)[A-Za-z]{1,3}(?:\s|$)'  # 短英文缩写如 lol hhh
    r'|[^\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\w\s.,!?;:\'"-]',  # 特殊符号
    re.UNICODE
)

# 更精确的颜文字匹配
EMOTICON_STRICT = re.compile(
    r'\([^)]*\)'        # (xxx)
    r'|\([^)]*$/[^\)]*\)'  # (xxx/xxx)
    r'|[/\\][a-zA-Z]+[/\\]',  # /xxx/
    re.UNICODE
)


def _strip_emoticons(text: str) -> str:
    """去除颜文字和表情符号，保留中文和标点"""
    # 去除常见颜文字格式
    text = re.sub(r'\(´[^)]*\)', '', text)   # (´▽`ʃ♡ƪ) 等
    text = re.sub(r'\(〃[^)]*\)', '', text)   # (〃∀〃) 等
    text = re.sub(r'\(*/[^)]*\)', '', text)   # (*/ω＼*) 等
    text = re.sub(r'\(∩[^)]*\)', '', text)   # (∩｀д´) 等
    text = re.sub(r'\([；;・][^)]*\)', '', text)
    text = re.sub(r'\(ﾟ[^)]*\)', '', text)
    text = re.sub(r'Σ\([^)]*\)', '', text)
    text = re.sub(r'╮\([^)]*\)', '', text)
    text = re.sub(r'ヾ\([^)]*\)', '', text)
    # 去除颜文字符号组合
    text = re.sub(r'[ʃ♡ƪ]+', '', text)
    text = re.sub(r'[ノノ～~]{2,}', '', text)
    # 清理多余空格
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _detect_context(message: str) -> str:
    """从消息内容检测上下文"""
    msg_lower = message.lower()
    for ctx, keywords in CONTEXT_KEYWORDS.items():
        for kw in keywords:
            if kw in msg_lower:
                return ctx
    return "generic"


def _get_template_context(ctx: str) -> str:
    """将上下文类型转换为模板名"""
    return CONTEXT_TO_TEMPLATE.get(ctx, "generic")


def after_reply(message: str = "", group_id: str = "", reply: str = "", context: dict = None) -> dict:
    """after_reply 钩子：将 Bot 回复转换为委员会风格"""
    if not reply or not reply.strip():
        return None

    # 不转换系统指令/插件消息
    if reply.startswith("[") or reply.startswith("/") or reply.startswith("THE BOARD"):
        return None

    # 不转换搜索结果（用户消息包含搜索标记，回复可能被工具结果注入）
    if "[搜索" in message:
        return None

    # 不转换太短的消息
    if len(reply.strip()) < 3:
        return None

    # 检测上下文
    ctx = _detect_context(message)
    template_ctx = _get_template_context(ctx)

    # ★ 2026-10-23 删除「特定场景直接换模板」这一支。
    #   原实现：
    #       specific = get_specific_reply(ctx)
    #       if specific and random.random() < 0.4:
    #           return {"action": "override", "reply": specific}
    #   `get_specific_reply` 返回的是**写死的三选一**（打招呼/道谢/道别各 3 条），
    #   与模型这轮说了什么完全无关，只由关键词命中决定。40% 概率意味着平均每
    #   2.5 条回复就有 1 条被换成同一批文本 —— 不同的人问不同的事，输出一样。
    #   这就是"文本被覆盖"最直接的形态，也是本轮要清的东西。
    #   没有改成"降概率保留"：固定文本池本身没有存在理由，留 10% 也还是那三条。
    #   `get_specific_reply` 函数保留在 voice.py 里，但不再被调用。

    # 转换为委员会风格
    transformed = transform_to_board(reply, intensity=INTENSITY, context=template_ctx)

    if transformed and transformed != reply:
        transformed = _strip_emoticons(transformed)
        transformed = re.sub(r'[/\\]+$', '', transformed).strip()
        if transformed:
            return {"action": "override", "reply": transformed}
    return None


def on_load():
    """插件加载"""
    print(f"[BoardVoice] 委员会口音插件已启用 (强度: {INTENSITY})")
    print("[BoardVoice] THE BOARD ACKNOWLEDGES YOUR PRESENCE. THE HOUSE PROVIDES.")


def on_unload():
    """插件卸载"""
    print("[BoardVoice] THE BOARD DEPARTS. THE HOUSE REMEMBERS.")
