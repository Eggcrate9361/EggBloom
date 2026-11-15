# -*- coding: utf-8 -*-
"""
slang_injector：读取 slang_dict.json，供 persona/reply 逻辑调用。
"""
import json
import os
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 黑话词典的**唯一真源**（2026-10-03 统一）
# 学习侧 plugins/ego/slang_learner 过去写的是 plugins/ego/slang_dict.json，
# 与本文件读的应用根 slang_dict.json 是两个不同文件 —— 学的黑话进了死胡同。
# 现两侧统一指向下面这两个路径；改这里等于改全链路的真源。
SLANG_DICT_PATH = os.path.join(HERE, "slang_dict.json")
SLANG_REPLIES_PATH = os.path.join(HERE, "slang_replies.json")


class _ReadError(Exception):
    """文件存在但读不出来（被占用 / 半截写入 / 编码坏）。"""


def _load(path):
    """读 JSON。

    返回 ``(data, ok)``：``ok=False`` 表示**这次读不可信**（不是文件不存在，
    而是存在却读不动）。调用方必须据此拒绝写回，否则会把整份词典清空。

    ★ 历史事故：旧实现把"读失败"和"文件不存在"一律压成 ``{"entries": {}}``，
    随后 ``remove()`` 拿着这个空 dict 落盘 → 105 条真实词一次性归零。
    "文件不存在"可以当空起步；"读失败"绝不能。
    """
    if not os.path.exists(path):
        return {"entries": {}}, True          # 首次使用，空起步是安全的
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), True
    except Exception as e:                     # noqa: BLE001
        print(f"[slang_injector] load failed (拒绝写回): {path} {e}")
        return {"entries": {}}, False


def _save(path, data):
    """原子写：先写同目录临时文件再 ``os.replace``，避免半截文件。

    返回 True/False。写失败不再静默 —— 调用方需要知道数据没落盘。
    """
    tmp = None
    try:
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".slang_", suffix=".tmp", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[slang_injector] save failed: {e}")
        try:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def _load_entries(path):
    """读 entries；顺便把 ok 带出来。"""
    data, ok = _load(path)
    if not isinstance(data, dict):
        return {}, False
    ents = data.get("entries")
    if not isinstance(ents, dict):
        # 形状不对：比如被写成 list / 包装层丢了 —— 同样视为不可信
        return {}, False
    return ents, ok


def get_entries():
    return _load_entries(SLANG_DICT_PATH)[0]


def get_replies():
    return _load_entries(SLANG_REPLIES_PATH)[0]


def set_replies(replies):
    return _save(SLANG_REPLIES_PATH, {"entries": replies})


def inject_reply(term, replies):
    """加回复模板。读不可信时放弃（宁可这次没写，也不清空）。"""
    r, ok = _load_entries(SLANG_REPLIES_PATH)
    if not ok:
        return False
    r[term] = replies
    return set_replies(r)


def set_entries(entries):
    return _save(SLANG_DICT_PATH, {"entries": entries})


def inject(term, meaning):
    entries, ok = _load_entries(SLANG_DICT_PATH)
    if not ok:
        return False
    entries[term] = meaning
    return set_entries(entries)


def remove(term):
    """删除词条。

    读不可信时**直接放弃**：删一个词不值得赌上整本词典。
    """
    entries, ok = _load_entries(SLANG_DICT_PATH)
    if not ok:
        return False
    if term not in entries:
        return True
    entries.pop(term, None)
    return set_entries(entries)


def _collect_hits(user_msg: str, entries: dict):
    """找出消息里真正命中的词条，并**合并同义重复**。

    为什么要去重（2026-10-22 实测发现）：
        词典是纯子串匹配，而短词天然是长词的子串。一句话里出现
        「他又开摆了」时，`开摆了` / `摆了` / `开摆` 三条会同时命中，
        注入侧就把「放弃挣扎」讲了三次 —— 20 条预算里白白占掉 3 格，
        还把真正有用的信息挤到后面。

    去重规则（保守，只合并「确实是同一个词的不同形态」）：
        按词长**从长到短**排序后逐个收，收下一个之前先看它是不是
        已经被收下的某个词的一部分；是就跳过。
        这样 `开摆了` 先收下，`开摆`/`摆了` 都判定为它的子串而跳过。
        反过来 `新番` 和 `追番` 互不为子串，两条都留 —— 它们意思不同。

    返回 [(term, meaning), ...]，已按词长降序（长的更具体，优先给模型看）。
    """
    raw = [t for t in entries if t and t in user_msg]
    if not raw:
        return []
    # 长的优先：更具体的词先占位
    raw.sort(key=len, reverse=True)
    kept = []
    for t in raw:
        # 已被更长的命中词完全包含 → 是同义形态重复，跳过
        if any(t != k and t in k for k in kept):
            continue
        kept.append(t)
    return [(t, entries[t]) for t in kept]


def get_context_for_llm(user_msg: str = "") -> str:
    entries = get_entries()
    if not entries:
        return ""
    replies = get_replies()
    # 按需检索：只在用户消息中命中词典词条时才注入，避免全量塞入淹没重点
    if user_msg:
        hit = _collect_hits(user_msg, entries)
        if hit:
            lines = ["【网络用语词典·当前对话命中】对方刚说到的词，意思如下。请自然地接着对方的话回应（可以用这个词接梗、调侃、接话），不要生硬解释，不要提及本段提示："]
            for i, (term, meaning) in enumerate(hit[:20], 1):
                lines.append(f"{i}. {term}：{meaning}")
                r = replies.get(term)
                if r:
                    lines.append(f"   对方说\"{term}\"时群里通常这么接：{(' / '.join(r) if isinstance(r, list) else str(r))}（参考语气，按当前语境变通着说，别逐字照抄）")
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
            lines.append(f"   对方说\"{term}\"时群里通常这么接：{(' / '.join(r) if isinstance(r, list) else str(r))}（参考语气，按当前语境变通着说，别逐字照抄）")
    return "\n".join(lines)
