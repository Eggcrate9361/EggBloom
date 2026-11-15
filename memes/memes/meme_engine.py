"""
Eggbloom 表情包匹配引擎
根据情绪描述匹配本地表情包库，返回最佳匹配的 GIF 文件路径。

2026-11-08 合并说明：根目录 `meme_engine.py` 曾经是另一份实现（10-30 的去重 /
近义库存回落在这里），11-07 为修「pending 图被发出去」把根入口切到本文件，
但本文件当时是较旧的版本，于是把去重、FALLBACK_TAGS、reload_index 一起弄丢了。
现在本文件是**唯一实现**：审核状态 / 黑名单过滤 + LRU 去重 + 近义库存回落都在，
根入口只做 `sys.modules` 别名，不再有第二份逻辑。
"""

import json
import os
import random
from difflib import SequenceMatcher

MEME_DIR = os.path.dirname(os.path.abspath(__file__))
TAGS_FILE = os.path.join(MEME_DIR, "tags.json")


def _collected_file(name):
    """按**当前** MEME_DIR 取审核态文件；调用方可在导入后改图库路径。"""
    return os.path.join(MEME_DIR, "collected", name)


def _blocked_rels():
    """被拉黑的表情包相对路径（每次匹配现读，改了立刻生效）。"""
    try:
        if os.path.isfile(_collected_file("blocked.json")):
            with open(_collected_file("blocked.json"), "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return {str(v.get("rel") or "").replace("\\", "/")
                        for v in d.values() if isinstance(v, dict)}
    except Exception:
        pass
    return set()


def _approved_rels():
    """可以发的图（旧条目没有 status → 视为 approved）。读不到就返回 None（不过滤）。"""
    try:
        p = _collected_file("index.json")
        if not os.path.isfile(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not d:
            return None
        out = set()
        for v in d.values():
            if not isinstance(v, dict):
                continue
            if str(v.get("status") or "approved") == "approved":
                out.add(str(v.get("path") or "").replace("\\", "/"))
        return out
    except Exception:
        return None


#: 情绪词在 tag 库里**没有**对应标签时的近义库存（2026-10-30）。
#: 以前会掉进「全库随机」——对方说"无语"，它可能回一张哭泣图。
FALLBACK_TAGS = {
    "无语": "呆滞", "烦躁": "炸毛", "兴奋": "开心", "惊喜": "开心",
    "崩溃": "炸毛", "好奇": "疑惑", "生气": "炸毛", "无聊": "呆滞",
}

# 用户情绪词 → 数据库标签（所有 key 必须是 tags.json 中实际存在的标签）
SYNONYM_MAP = {
    "开心":     ["开心", "哈哈", "笑死", "好棒", "太好了", "nice", "恭喜", "高兴", "快乐", "喜悦", "兴奋", "欢快", "好耶", "芜湖"],
    "害羞":     ["害羞", "脸红", "羞涩", "腼腆", "不好意思", "羞耻", "害臊"],
    "惊讶":     ["震惊", "惊讶", "吓到", "目瞪口呆", "吃惊", "愕然", "呆住", "卧槽", "我去", "天哪", "不是吧", "？？？", "什么鬼", "激动"],
    "嫌弃":     ["无语", "无奈", "汗颜", "扶额", "翻白眼", "服了", "……", "嫌弃", "讨厌", "厌恶", "抗拒", "恶心", "受不了", "好丑", "别", "达咩"],
    "尴尬":     ["尴尬", "窘迫", "不知所措", "汗", "囧", "尬住"],
    "慌乱":     ["慌张", "慌乱", "手忙脚乱", "慌神", "着急"],
    "得意":     ["嘚瑟", "嚣张", "神气", "骄傲", "自豪", "不愧是我", "厉害吧", "怎么样", "我就说", "坏笑", "奸笑", "狡猾", "邪恶笑", "贱笑", "放空"],
    "委屈":     ["委屈", "难过", "伤心", "想哭", "呜呜", "可怜"],
    "烦躁":     ["生气", "愤怒", "发火", "恼火", "不爽", "火大", "烦躁"],
    "惊恐":     ["害怕", "恐惧", "吓", "怕怕", "惊恐", "惊惧"],
    "疑惑":     ["疑惑", "疑问", "问号", "不懂", "纳闷", "困惑"],
    "期待":     ["期待", "盼望", "等待", "坐等", "蹲"],
    "崩溃":     ["失望", "失落", "沮丧", "难受", "心累", "摆烂", "放弃", "躺平", "无所谓", "算了", "不干了", "弃疗", "绝望", "裂开", "蚌埠住了"],
    "惊喜":     ["惊喜", "哇", "意外之喜", "没想到"],
    "呆滞":     ["呆", "呆滞", "发呆", "愣住", "石化", "睡觉", "睡", "困死", "好困"],
    "满足":     ["满足", "惬意", "舒服", "享受", "巴适", "累", "疲惫", "疲倦", "累死", "没劲"],
    "温柔":     ["温柔", "温暖", "治愈", "软"],
    "落寞":     ["落寞", "孤寂", "寂寞", "孤独", "冷清"],
    "撒娇":     ["可爱", "萌", "萌萌哒", "卡哇伊"],
    "嗨了":     ["嗨了", "嗨", "精神了", "灵感", "疯狂", "上头", "冲"],
    "馋了":     ["馋了", "馋", "想吃", "饿了", "好想吃", "嘴馋"],
    "中二病":   ["中二病", "中二", "此乃天意", "命运", "觉醒", "力量"],
    "困到发疯": ["困到发疯", "困死", "不想睡", "熬夜", "失眠", "打瞌睡"],
    "被撩到":   ["被撩到", "撩", "心动", "被撩", "撩我", "撩到了"],
    "炸毛":     ["炸毛", "急了", "破防", "炸了", "你说什么", "再说一遍"],
    "发疯":     ["发疯", "人生意义", "我突然觉得", "你们有没有想过", "压力", "受不了了", "啊啊啊"],
    "发情":     ["发情", "你好可爱", "我好喜欢你", "太可爱了", "你能不能不要", "好心动", "喜欢你"],
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

    inverted = {}
    for filename, tags in tag_db.items():
        if isinstance(tags, dict):
            tags = tags.get("tags", [])
        for tag in tags:
            inverted.setdefault(tag, []).append(filename)

    return tag_db, inverted


_tag_db, _inverted_index = _init()


def reload_index():
    """重新读 `TAGS_FILE` 重建标签库与倒排索引，返回标签条数。

    自动采集入库（`meme_collect.py`）后调用，否则新图的标签要等重启才生效。
    按当前模块全局算，不缓存导入时的路径。
    """
    global _tag_db, _inverted_index
    _tag_db, _inverted_index = _init()
    return len(_tag_db)


# 最近发送过的表情包（去重用，避免连续重复）
_recent_memes = []
_RECENT_MAX = 20


def _similarity(a, b):
    """计算两个字符串的相似度"""
    return SequenceMatcher(None, a, b).ratio()


def _pick_random(filenames):
    """从候选里挑一张：过滤磁盘上不存在的，避开最近发过的，全发过则按 LRU 挑最久没发。"""
    if not filenames:
        return None
    existing = [f for f in filenames if os.path.exists(os.path.join(MEME_DIR, f))]
    if not existing:
        return None
    if len(existing) == 1:
        return existing[0]
    # 2026-10-30 修：_record_meme 存的是 basename，这里以前拿完整相对路径比 →
    # 永远不命中，去重等于没做（真机表现就是"总是那几个表情包"）。
    available = [f for f in existing if os.path.basename(f) not in _recent_memes]
    if not available:
        def _age(f):
            b = os.path.basename(f)
            return _recent_memes.index(b) if b in _recent_memes else -1
        return min(existing, key=_age)
    return random.choice(available)


def _record_meme(filepath):
    """记录已发送的表情包"""
    if filepath:
        _recent_memes.append(os.path.basename(filepath))
        if len(_recent_memes) > _RECENT_MAX:
            _recent_memes.pop(0)


def _pick(filenames):
    """审核闸 + 黑名单 + LRU 去重，统一走这里；返回相对路径或 None。"""
    blk = _blocked_rels()
    ok = _approved_rels()
    cands = []
    for f in (filenames or []):
        rel = str(f).replace("\\", "/")
        if rel in blk:
            continue
        # 只认 collected/ 下的审核状态；老图库不在索引里，照常可用
        if ok is not None and rel.startswith("collected/") and rel not in ok:
            continue
        cands.append(f)
    return _pick_random(cands)


def _pick_path(filenames):
    """挑一张并拼成绝对路径；没有可用的候选返回 None。"""
    w = _pick(filenames)
    return os.path.join(MEME_DIR, w) if w else None


def match_meme(emotion_or_keyword):
    """
    根据情绪/关键词匹配最佳表情包。

    返回:
        最佳匹配的文件绝对路径，失败返回 None
    """
    if not _tag_db or not _inverted_index:
        return None

    s = emotion_or_keyword.lower()

    # Step 1: 精确关键词匹配
    for tag, filenames in _inverted_index.items():
        if tag in s:
            path = _pick_path(filenames)
            if path:
                _record_meme(path)
                return path

    # Step 2: 同义词匹配
    for tag, synonyms in SYNONYM_MAP.items():
        for syn in synonyms:
            if syn in s and tag in _inverted_index:
                path = _pick_path(_inverted_index[tag])
                if path:
                    _record_meme(path)
                    return path

    # Step 3: 模糊相似匹配（合并相近标签的文件池）
    best_score = 0
    best_tags = []
    for tag in _inverted_index:
        score = _similarity(tag, s)
        if score > best_score:
            best_score = score
            best_tags = [tag]
        elif score == best_score and score > 0.4:
            best_tags.append(tag)

    if best_tags and best_score > 0.4:
        pool = []
        for t in best_tags:
            pool.extend(_inverted_index.get(t, []))
        if pool:
            path = _pick_path(pool)
            if path:
                _record_meme(path)
                return path

    # Step 3.5: 词表里没有这个标签 → 用近义库存（比全库随机靠谱）
    _fb = FALLBACK_TAGS.get(s)
    if _fb and _fb in _inverted_index:
        path = _pick_path(_inverted_index[_fb])
        if path:
            _record_meme(path)
            return path

    # Step 4: 兜底随机（同样避开最近发过的）
    all_files = list(_tag_db.keys())
    if all_files:
        path = _pick_path(all_files)
        if path:
            _record_meme(path)
            return path

    return None


def parse_meme_intent(reply_text):
    """
    从 AI 回复中解析表情包意图，剥离所有 [表情] 标记。

    支持的格式:
      [表情:开心] → 发一张开心的表情包
      [表情] → 让 AI 自行判断（取回复第一句情绪）

    返回: (cleaned_text, meme_emotion_or_None)
    """
    import re

    if not reply_text:
        return reply_text, None

    m = re.search(r'\[表情(?::(.+?))?\]', reply_text)
    emotion = m.group(1) if m else None

    cleaned = re.sub(r'\[表情(?::.+?)?\]', '', reply_text)
    cleaned = re.sub(r'\[(?:开心|害羞|惊讶|嫌弃|尴尬|慌乱|得意|委屈|烦躁|惊恐|疑惑|期待|崩溃|惊喜|呆滞|满足|温柔|落寞|撒娇|愉悦|享受|绝望|抗拒|痛苦|挣扎|安心|幸福|纠结|懊恼|困惑|不安|紧张|惊慌|担忧|偷窥|放松|悠闲|平静)\]', '', cleaned)
    return cleaned.strip(), emotion


def auto_detect_emotion(reply_text):
    """
    自动从回复文本中检测是否需要表情包，以及什么情绪。
    保守策略：仅当命中明确情绪关键词时才触发。
    """
    text = reply_text

    emotion_hints = {
        "开心": ["哈哈", "笑死", "嘿嘿", "嘻嘻", "好耶", "噗", "hhh", "www", "笑拉了", "笑嘻了", "666", "nice", "开心", "快乐", "高兴", "太好了", "棒", "赞"],
        "嫌弃": ["无语", "服了", "……", "…", "当我没说", "你赢了", "有病", "别闹", "行行行你厉害", "离谱", "逆天", "嫌弃", "吐了"],
        "惊讶": ["卧槽", "我去", "？？？", "什么鬼", "离谱", "逆天", "真的假的", "不会吧", "震撼", "天哪", "我靠"],
        "崩溃": ["崩溃", "裂开", "蚌埠住了", "救命", "麻了", "心态崩了", "破防", "我裂开了", "我人傻了"],
        "委屈": ["呜呜", "想哭", "破防", "扎心", "别骂了", "我酸了", "好委屈", "我佛了"],
        "得意": ["不愧是我", "我就说", "还得是我", "叫爸爸", "就这", "略略略", "才不是", "就不", "哼", "略", "好活", "绝了", "拿捏", "稳了"],
        "烦躁": ["烦躁", "烦死了", "别烦我", "好烦", "烦", "生气", "火大", "没完了是吧", "你烦不烦", "别戳了", "再戳", "急"],
        "惊恐": ["害怕", "瑟瑟发抖", "吓死", "哈人", "恐怖", "太吓人了"],
        "期待": ["加油", "等你", "然后呢", "别停", "冲冲冲", "干就完了", "期待", "好想", "想看"],
        "惊喜": ["哇", "真的假的", "居然", "芜湖", "起飞", "太棒了", "好运"],
        "困惑": ["不懂", "啥意思", "不对劲", "干嘛", "啊？", "什么情况", "为什么", "怎么回事"],
        "呆滞": ["发呆", "愣住", "懵", "困了", "睡了", "睡着了"],
        "满足": ["舒服", "巴适", "安逸", "到位", "爽", "满足", "完美"],
        "撒娇": ["贴贴", "抱抱", "QAQ", "亲亲", "喵", "诶嘿", "诶——", "(´", "(*/"],
        "温柔": ["温柔", "晚安", "摸摸头", "辛苦了", "好梦", "注意身体"],
        "落寞": ["寂寞", "孤独", "冷清", "一个人", "没人理", "好安静"],
        "害羞": ["害羞", "脸红", "不好意思", "哎呀", "讨厌", "别说了", "不要", "人家"],
        "紧张": ["紧张", "好慌", "完了", "糟了", "凉了", "要命"],
        "幸福": ["幸福", "好开心", "太幸福了", "甜", "暖心", "感动"],
        "抗拒": ["不要", "不行", "别", "算了", "我不", "不想", "拒绝"],
        "纠结": ["纠结", "选择困难", "好难选", "都想要", "犹豫", "算了"],
        "痛苦": ["痛苦", "好痛", "受不了", "太难了", "心累", "好累"],
        "享受": ["享受", "好惬意", "舒服极了", "爽翻", "美滋滋"],
        "平静": ["平静", "还好", "一般般", "还行", "无所谓", "随便"],
        "绝望": ["绝望", "完了", "没救了", "死心了", "放弃"],
        "挣扎": ["挣扎", "太难了", "救救我", "怎么办", "头大"],
    }

    priority_order = ["得意", "嫌弃", "委屈", "撒娇", "崩溃",
                      "开心", "惊讶", "期待", "惊喜",
                      "紧张", "害羞", "满足", "幸福", "绝望",
                      "惊恐", "困惑", "呆滞", "落寞",
                      "温柔", "抗拒", "享受", "平静", "痛苦",
                      "烦躁", "挣扎"]

    for emotion in priority_order:
        if emotion not in emotion_hints:
            continue
        if any(kw in text for kw in emotion_hints[emotion]):
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
