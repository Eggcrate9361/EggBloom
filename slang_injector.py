# -*- coding: utf-8 -*-
"""
slang_injector：读取 slang_dict.json，供 persona/reply 逻辑调用。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 黑话词典的**唯一真源**（2026-10-03 统一）
# 学习侧 plugins/ego/slang_learner 过去写的是 plugins/ego/slang_dict.json，
# 与本文件读的应用根 slang_dict.json 是两个不同文件 —— 学的黑话进了死胡同。
# 现两侧统一指向下面这两个路径；改这里等于改全链路的真源。
SLANG_DICT_PATH = os.path.join(HERE, "slang_dict.json")
SLANG_REPLIES_PATH = os.path.join(HERE, "slang_replies.json")


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"entries": {}}


def _save(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[slang_injector] save failed: {e}")


def get_entries():
    data = _load(SLANG_DICT_PATH)
    return data.get("entries", {})


def get_replies():
    data = _load(SLANG_REPLIES_PATH)
    return data.get("entries", {})


def set_replies(replies):
    _save(SLANG_REPLIES_PATH, {"entries": replies})


def inject_reply(term, replies):
    r = get_replies()
    r[term] = replies
    set_replies(r)


def set_entries(entries):
    _save(SLANG_DICT_PATH, {"entries": entries})


def inject(term, meaning):
    entries = get_entries()
    entries[term] = meaning
    set_entries(entries)


def remove(term):
    entries = get_entries()
    entries.pop(term, None)
    set_entries(entries)


def get_context_for_llm(user_msg: str = "") -> str:
    entries = get_entries()
    if not entries:
        return ""
    replies = get_replies()
    # 按需检索：只在用户消息中命中词典词条时才注入，避免全量塞入淹没重点
    if user_msg:
        hit = []
        for term, meaning in entries.items():
            if term and term in user_msg:
                hit.append((term, meaning))
        if hit:
            lines = ["【网络用语词典·当前对话命中】对方刚说到的词，意思如下。请自然地接着对方的话回应（可以用这个词接梗、调侃、接话），不要生硬解释，不要提及本段提示："]
            for i, (term, meaning) in enumerate(hit[:20], 1):
                lines.append(f"{i}. {term}：{meaning}")
                r = replies.get(term)
                if r:
                    lines.append(f"   对方说\"{term}\"时，你可以直接这样回：{(' / '.join(r) if isinstance(r, list) else str(r))}")
            return "\n".join(lines)
        # T2-5 未命中时：只注入 5 条高频词条（原 15 条，瘦身 2/3）
        lines = ["【网络用语词典】以下词条包含使用场景，对方可能用到，请在回复时自然使用对应语气/语境，不要生硬解释："]
        for i, (term, meaning) in enumerate(list(entries.items())[:5], 1):
            lines.append(f"{i}. {term}：{meaning}")
        return "\n".join(lines)
    # T2-5 无用户消息时只注入 5 条高频（原 80 条）
    lines = ["【网络用语词典】以下词条包含使用场景，请在回复时自然使用对应语气/语境，不要生硬解释，不要提及本段提示："]
    for i, (term, meaning) in enumerate(list(entries.items())[:5], 1):
        lines.append(f"{i}. {term}：{meaning}")
        r = replies.get(term)
        if r:
            lines.append(f"   对方说\"{term}\"时，你可以直接这样回：{(' / '.join(r) if isinstance(r, list) else str(r))}")
    return "\n".join(lines)
