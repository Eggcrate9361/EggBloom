# -*- coding: utf-8 -*-
"""机器人身份注册表：让整条蒸馏链路都知道「谁是机器人」。

这不是可有可无的元数据，而是**区分两类致命污染的前提**：

  1. 「@示例昵称 爱你哟」—— 他在叫机器人（人类 → bot，是宝贵的关系层数据）
  2. 「示例昵称: [图片]」「（看到正经问题，精神一振）哦哦哦！」
     —— 这是**机器人自己在说话**，混进语料会让模型去模仿机器人的语气

不认识机器人的名字，这两类就分不开。实测里第二种已经漏进去过。

名字从两处来：
  - `personality.txt` 的人设首行（「你是小a，18 岁…」）—— 自动解析
  - 配置 `bot_names` —— 用户手动补充（比如群里的昵称 示例昵称）
"""
import os
import re

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.dirname(os.path.dirname(_PLUGIN_DIR))

# ★ 2026-10-27：personality.txt 现在的写法是「我叫小a，……」而不是「你是小a」，
#   旧正则只认「你是」→ 永远匹配不到 → bot_names() 恒空。
#   两个句式都认（人设文件改过写法，不能让解析器跟着旧格式一起死）。
_NAME_RE = re.compile(r"^(?:你是|我叫)[「\"'『]?([^\uFF0C，,。.\s！!？?】]{1,12})", re.M)


def bot_names():
    """机器人名字的集合（去重、去空）。"""
    names = set()
    try:
        from .config import get_config
        for n in (get_config().get("bot_names") or []):
            n = str(n).strip()
            if n:
                names.add(n)
    except Exception:
        pass

    # ★ 2026-10-27 修正：原来指向 `<app>/personality.txt`（仓库根）——
    #   那个文件不存在，于是 bot_names() 恒返回空集，读不到机器人自称。
    #   权威位置是 `<app>/plugins/personality.txt`（与 bot_core.PERSONA_CANONICAL 一致）。
    p = os.path.join(_PROJECT, "plugins", "personality.txt")
    try:
        with open(p, "r", encoding="utf-8") as f:
            head = f.read(400)
        m = _NAME_RE.search(head)
        if m:
            names.add(m.group(1))
    except OSError:
        pass
    return names


def is_bot_name(word):
    """这个名字是不是机器人的。"""
    if not word:
        return False
    w = str(word).strip().lstrip("@＠")
    return w in bot_names()


def is_bot_speech(text):
    """这条文本是不是**机器人自己在说话**（而不是他在叫机器人）。

    两条规则，方向相反，必须分清：
      - `小a: 你好呀` / `示例昵称：[图片]` → 机器人在说话（agent 侧）
      - `@小a 叫两声` → 他在叫机器人（user 侧，要保留）
    """
    t = (text or "").strip()
    if not t:
        return False
    names = bot_names()
    for n in names:
        # 机器人名字 + 冒号开头 = 它在说话
        if re.match(rf"^\s*@?\s*{re.escape(n)}\s*[:：]", t):
            return True
    return False


def mentions_bot(text):
    """这条文本里有没有叫机器人（@ 或直呼其名）。"""
    t = (text or "").strip()
    if not t:
        return False
    for n in bot_names():
        if f"@{n}" in t or re.search(rf"^\s*{re.escape(n)}\s*[,，\s]", t):
            return True
    return False
