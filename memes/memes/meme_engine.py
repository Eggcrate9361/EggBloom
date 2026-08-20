"""
Eggbloom 表情包匹配引擎
根据情绪描述匹配本地表情包库，返回最佳匹配的 GIF 文件路径。
"""

import json
import os
import random
from difflib import SequenceMatcher

MEME_DIR = os.path.dirname(os.path.abspath(__file__))
TAGS_FILE = os.path.join(MEME_DIR, "tags.json")

# 用户情绪词 → 数据库标签（所有 key 必须是 tags.json 中实际存在的标签）
SYNONYM_MAP = {
    "开心":     ["开心", "哈哈", "笑死", "好棒", "太好了", "nice", "恭喜", "高兴", "快乐", "喜悦", "兴奋", "欢快"],
    "害羞":     ["害羞", "脸红", "羞涩", "腼腆", "不好意思", "羞耻", "害臊"],
    "惊讶":     ["震惊", "惊讶", "吓到", "目瞪口呆", "吃惊", "愕然", "呆住", "卧槽", "我去", "天哪", "不是吧", "？？？", "什么鬼"],
    "嫌弃":     ["无语", "无奈", "汗颜", "扶额", "翻白眼", "服了", "……", "嫌弃", "讨厌", "厌恶", "抗拒", "恶心", "受不了", "好丑", "别", "达咩"],
    "尴尬":     ["尴尬", "窘迫", "不知所措", "汗", "囧", "尬住"],
    "慌乱":     ["慌张", "慌乱", "手忙脚乱", "慌神", "着急"],
    "得意":     ["激动", "嘚瑟", "嚣张", "神气", "骄傲", "自豪", "不愧是我", "厉害吧", "怎么样", "我就说", "坏笑", "奸笑", "狡猾", "邪恶笑", "贱笑"],
    "委屈":     ["委屈", "难过", "伤心", "想哭", "呜呜", "可怜"],
    "烦躁":     ["生气", "愤怒", "发火", "恼火", "不爽", "火大", "烦躁"],
    "惊恐":     ["害怕", "恐惧", "吓", "怕怕", "惊恐", "惊惧"],
    "疑惑":     ["疑惑", "疑问", "问号", "不懂", "纳闷", "困惑"],
    "期待":     ["期待", "盼望", "等待", "坐等", "蹲"],
    "崩溃":     ["失望", "失落", "沮丧", "难受", "心累", "摆烂", "放弃", "躺平", "无所谓", "算了", "不干了", "弃疗", "绝望", "裂开", "蚌埠住了"],
    "惊喜":     ["惊喜", "哇", "意外之喜", "没想到"],
    "呆滞":     ["呆", "呆滞", "放空", "发呆", "愣住", "石化", "睡觉", "睡", "困死", "好困"],
    "满足":     ["满足", "惬意", "舒服", "享受", "巴适", "累", "疲惫", "疲倦", "累死", "没劲"],
    "温柔":     ["温柔", "温暖", "治愈", "软"],
    "落寞":     ["落寞", "孤寂", "寂寞", "孤独", "冷清"],
    "撒娇":     ["可爱", "萌", "萌萌哒", "卡哇伊"],
    "愉悦":     ["愉悦"],
    "享受":     ["享受"],
    "绝望":     ["绝望"],
    "抗拒":     ["抗拒"],
    "痛苦":     ["痛苦"],
    "挣扎":     ["挣扎"],
    "安心":     ["安心"],
    "幸福":     ["幸福"],
    "纠结":     ["纠结"],
    "懊恼":     ["懊恼"],
    "困惑":     ["困惑"],
    "不安":     ["不安"],
    "紧张":     ["紧张"],
    "惊慌":     ["惊慌"],
    "担忧":     ["担忧"],
    "偷窥":     ["偷窥"],
    "放松":     ["放松"],
    "悠闲":     ["悠闲"],
    "平静":     ["平静"],
}

def _init():
    """加载标签库，构建倒排索引"""
    if not os.path.exists(TAGS_FILE):
        return {}, {}
    
    with open(TAGS_FILE, 'r', encoding='utf-8') as f:
        tag_db = json.load(f)
    
    # 构建 标签 → [文件名列表] 倒排索引
    inverted = {}
    for filename, tags in tag_db.items():
        if isinstance(tags, dict):
            tags = tags.get("tags", [])
        for tag in tags:
            inverted.setdefault(tag, []).append(filename)
    
    return tag_db, inverted

_tag_db, _inverted_index = _init()

def _similarity(a, b):
    """计算两个字符串的相似度"""
    return SequenceMatcher(None, a, b).ratio()

def match_meme(emotion_or_keyword):
    """
    根据情绪/关键词匹配最佳表情包。
    
    参数:
        emotion_or_keyword: 情绪描述，如 "开心" / "无语" / "想要一个表示惊讶的表情包"
    
    返回:
        最佳匹配的文件绝对路径，失败返回 None
    """
    if not _tag_db or not _inverted_index:
        return None
    
    s = emotion_or_keyword.lower()
    
    # Step 1: 精确关键词匹配
    for tag, filenames in _inverted_index.items():
        if tag in s:
            winner = random.choice(filenames)
            return os.path.join(MEME_DIR, winner)
    
    # Step 2: 同义词匹配
    best_score = 0
    best_tag = None
    
    for tag, synonyms in SYNONYM_MAP.items():
        for syn in synonyms:
            if syn in s:
                if tag in _inverted_index:
                    return os.path.join(MEME_DIR, random.choice(_inverted_index[tag]))
    
    # Step 3: 模糊相似匹配
    for tag in _inverted_index:
        score = _similarity(tag, s)
        if score > best_score:
            best_score = score
            best_tag = tag
    
    if best_tag and best_score > 0.5 and best_tag in _inverted_index:
        return os.path.join(MEME_DIR, random.choice(_inverted_index[best_tag]))
    
    # Step 4: 兜底随机
    all_files = list(_tag_db.keys())
    if all_files:
        return os.path.join(MEME_DIR, random.choice(all_files))
    
    return None


def parse_meme_intent(reply_text):
    """
    从 AI 回复中解析表情包意图。
    
    支持的格式:
      [表情:开心] → 发一张开心的表情包
      [表情] → 让 AI 自行判断（取回复第一句情绪）
    
    返回: (cleaned_text, meme_emotion_or_None)
    """
    import re
    
    # 查找 [表情:xxx] 或 [表情]
    m = re.search(r'\[表情(?::(.+?))?\]', reply_text)
    if not m:
        return reply_text, None
    
    emotion = m.group(1)  # 可能是 None
    cleaned = reply_text[:m.start()] + reply_text[m.end():]
    cleaned = cleaned.strip()
    
    return cleaned, emotion


def auto_detect_emotion(reply_text):
    """
    自动从回复文本中检测是否需要表情包，以及什么情绪。
    如果 AI 回复语气强烈（感叹号、情绪词多），自动匹配。
    """
    # 情绪词检测 — 所有 key 必须是数据库标签
    emotion_hints = {
        "开心": ["哈哈", "笑死", "好棒", "太好了", "nice", "恭喜", "高兴"],
        "嫌弃": ["无语", "服了", "……", "…", "我服", "这也行", "恶心", "受不了", "好丑", "别", "达咩"],
        "惊讶": ["卧槽", "我去", "天哪", "不是吧", "？？？", "什么鬼", "震惊"],
        "崩溃": ["崩溃", "裂开", "蚌埠", "救命", "要命", "绝望", "摆烂"],
        "委屈": ["呜呜", "想哭", "难过", "伤心", "心疼"],
        "得意": ["不愧是我", "厉害吧", "怎么样", "我就说", "得意", "嚣张"],
        "害羞": ["害羞", "不好意思", "别夸", "别这样", "羞涩"],
        "烦躁": ["生气", "火大", "恼火", "不爽", "烦"],
        "惊恐": ["害怕", "好怕", "吓到", "恐怖", "可怕"],
        "期待": ["加油", "冲", "你可以", "相信", "期待"],
        "困惑": ["不懂", "？", "为什么", "怎么"],
        "惊喜": ["哇", "真的假的", "不会吧", "太好了"],
        "呆滞": ["发呆", "愣住", "懵", "傻了", "困"],
        "满足": ["舒服", "爽", "巴适", "享受", "惬意"],
    }
    
    for emotion, keywords in emotion_hints.items():
        if any(kw in reply_text for kw in keywords):
            return emotion
    
    return None


if __name__ == "__main__":
    print("=== 表情包匹配测试 ===")
    tests = ["开心", "无语了", "害怕", "再来一次", "好累啊不想动", "得意", "委屈呜呜",
             "想要一个坏笑的表情", "表示惊讶", "嫌弃", "尴尬死了", "崩溃了"]
    for t in tests:
        result = match_meme(t)
        status = os.path.basename(result) if result else "未匹配"
        print(f"  '{t}' → {status}")