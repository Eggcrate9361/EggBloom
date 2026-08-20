# -*- coding: utf-8 -*-
"""message_router.py  消息过滤路由器（无意义消息判定 + 会话管理）"""
import re

# 无意义短消息白名单
SHORT_NOISE = {"嗯", "哦", "6", "哈哈", "好的", "行", "可以", "ok", "OK",
               "收到", "1", "2", "3", "?", "？", "...", "。。", "。。。",
               "啊", "呃", "啧", "噗", "哈", "嗯嗯", "哦哦", "咕咕", "嘎嘎",
               "咕咕嘎嘎", "咕嘎", "额", "额。", "额。。"}

# 2字有意义内容白名单（不触发会话结束）
TWO_CHAR_KEEP = {"不信", "没有", "是的", "不是", "你呢", "谁啊", "在哪",
                 "不行", "等等", "真的", "假的", "没错", "不对", "你好",
                 "抱歉", "厉害", "懂了", "不懂", "来了", "在了", "到了",
                 "干嘛", "怎么", "啥啊", "什么", "为啥", "人呢", "几点", "谁呀",
                 "出来", "在哪", "喏", "给", "好", "没", "对", "是", "嗯", "哦",
                 "啊", "哈", "嘿", "切", "啧", "喂", "唉", "哎", "噢", "呀",
                 "呢", "吧", "嘛", "呗", "啦", "咯", "呵", "呵", "操", "靠",
                 "哥哥", "姐姐", "弟弟", "妹妹", "爸爸", "妈妈", "爷爷", "奶奶",
                 "叔叔", "阿姨", "舅舅", "老公", "老婆", "宝贝", "宝宝", "师傅", "师父",
                 "学长", "学姐", "老板", "大哥", "大姐", "老大", "老哥", "老弟"}

# 群内已知其他人名（用于定向消息过滤）
KNOWN_NAMES = ["王宇航", "王雨涵"]
SHORT_LEN = 8
SESSION_END_LEN = 2

# 旁观者评论模式（检测围观 Bot 对话的用户）
BYSTANDER_PATTERNS = [
    r'你们.*(?:演|唱|说|讲|搁这|在这)',
    r'你俩.*(?:演|唱|说|搞|玩|弄|讲|搁|这|在)',
    r'你两个.*(?:演|唱|说)',
    r'搁这(?:演|唱|说)',
    r'(?:连续剧|相声|小品|双簧|二人转)',
    r'(?:搁这|在这).*(?:演|唱)',
]


def is_bystander_commentary(text):
    """检测旁观者对 Bot 对话的评论"""
    for p in BYSTANDER_PATTERNS:
        if re.search(p, text): return True
    return False


def is_meaningless(text, has_xiao_a=False, bot_group_nick="", bot_qq=""):
    """
    判断单条消息是否无意义（短消息/乱码/给别人的/旁观者评论）。
    返回三元组 (is_meaningless: bool, should_end_session: bool, reason: str)
    """
    if not text or not isinstance(text, str): return True, False, "empty"

    # 预计算字符统计，避免后续分支重复遍历
    _chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    _alnum = sum(1 for c in text if c.isalnum())

    # 包含 @小a 或者直接叫了名字 → 有意义
    if "小a" in text or has_xiao_a: return False, False, ""

    # 消息以 @ 开头但不是 @Bot → 定向给他人
    if text.startswith("@"):
        m = re.match(r'^@(\S+)', text)
        if m and "小a" not in m.group(1) and m.group(1) != bot_group_nick:
            return True, False, f"at_other:{m.group(1)}"
    for name in KNOWN_NAMES:
        if text.startswith(f"@{name}") or text.startswith(name):
            return True, False, f"for_{name}"
        if text.endswith(name) or text.endswith(f" {name}") or f" {name}" in text:
            return True, False, f"to_{name}"
        if f"@{name}" in text or f" {name}" in text or f" {name}" in text:
            return True, False, f"find_{name}"

    # 旁观者评论（"你们演连续剧""你俩说相声"等）
    if is_bystander_commentary(text): return True, False, "bystander"

    # 短消息噪声过滤
    if len(text) <= SHORT_LEN:
        if text in SHORT_NOISE: return True, False, "short_noise"
        if text.isdigit() and len(text) <= 3: return True, False, "digits"
        if len(set(text)) <= 2 and len(text) <= 1: return True, False, "repeated"

        # 2字消息：白名单放行，其余触发会话结束
        if len(text) <= SESSION_END_LEN:
            if text in TWO_CHAR_KEEP: return False, False, "keep"
            if _chinese == 0 and _alnum == 0: return False, False, "kaomoji"
            return True, True, "end_session"  # 其他两字消息触发会话结束

        return True, False, "short"

    # 短消息但包含 emoji / 特殊符号（非中文非字母非数字）
    if len(text) <= 15:
        if _chinese == 0 and _alnum == 0: return False, False, "emoji"

    # 无意义乱码检测
    if len(text) <= 10:
        if _chinese == 0 and _alnum == 0: return True, False, "nonsense"

    # 低熵重复字符
    if len(text) >= 3:
        if len(set(text)) / len(text) <= 0.5: return True, False, "low_entropy"

    return False, False, "meaningful"


def is_similar_to_recent(recent_texts, new_text, threshold=0.6):
    """检测新消息是否与近期消息高度相似（防刷屏/重复）"""
    if not recent_texts: return False, ""
    if len(new_text) <= 8 and len(new_text) / max(len(set(new_text)), 1) > 2:
        return False, ""
    n = set(new_text)
    if not n: return False, ""
    for rt in recent_texts:
        r = set(rt)
        if not r: continue
        overlap = len(n & r) / min(len(n), len(r))
        if overlap > threshold: return True, rt
    for rt in recent_texts:
        if len(new_text) >= 3 and len(rt) >= 3:
            shorter = min(len(new_text), len(rt))
            if new_text[:shorter] == rt[:shorter]: return True, rt
    return False, ""
