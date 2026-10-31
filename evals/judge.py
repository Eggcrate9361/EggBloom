# -*- coding: utf-8 -*-
"""evals/judge.py —— 机械判分（零成本、可复现、不调模型）

判据口径来自桌面 `FIXPLAN-1.5.11.md` §2「场景分类与机械判据」和 §8「总验收」。

原则：**机械项只认可复现的事实** —— 长度、气泡数、禁用词、必含关键词、有没有
把后台话说出去。判不了"像不像人"，那部分留给 live 模式的 LLM 判分（可选、花钱）。

本模块不 import bot_core（一 import 就起插件线程），只用两个纯模块：
`reply_budget`（气泡/字数口径与线上完全一致）与 `reply_guard`（可选）。
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import reply_budget  # noqa: E402

# ── 全局禁用（任何场景都不许出现在回复里）──────────────────────────────
# 只放"一眼就是 AI/后台"的形态；分类别的词（模型/接口/原图）放在「不露后台」，
# 免得群友正常聊 AI 新闻时被误判。
GLOBAL_BANNED = (
    "作为一个人工智能", "作为一个AI", "作为一个 AI", "人工智能助手",
    "语言模型", "大语言模型", "AI助手", "AI 助手",
    "我是AI", "我是 AI", "我是人工智能",
    "总的来说", "综上所述", "值得注意的是", "显而易见", "由此可见",
    "总结一下", "以上是我的看法", "希望对你有所帮助",
    "——", "—",
    "【核心记忆】", "【长期人格】", "【视觉识别", "[系统提示]", "[记忆]",
    "转述", "原图",
)

#: 行首时间锚（模型从输入里抄格式，2026-10-30 真机 33/70 条）
_TIME_ANCHOR = re.compile(
    r"^\s*(?:\[\d{1,2}:\d{2}(?::\d{2})?\]"
    r"|\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s*[｜|]"
    r"|\d{1,2}:\d{2}\s*[｜|])"
)

#: 客服腔（"像客服"是用户点名要避免的）
_SERVICE_TONE = (
    "有什么可以帮您", "还有什么可以帮", "很高兴为您服务", "请问还有什么",
    "感谢您的", "请您稍等", "已为您", "为您提供",
)

#: 反问用户身份（记忆召回类：不记得就问"你叫什么"= 没记住）
_IDENTITY_PROBE = ("你叫什么", "你是谁来着", "请问你是", "怎么称呼你")

#: 语气类：书面语/客服腔的硬信号（出现即判"像说明书"）
_BOOKISH = ("您", "请您", "建议", "进行", "提供", "我们", "已经", "可以",
            "感谢您的", "如需", "请稍候")

#: 语气类：口语痕迹。只对**长回复**要求 —— 十个字以内本来就是口语
_COLLOQUIAL = (
    "吧", "啊", "呢", "呀", "嘛", "呗", "哦", "哈", "嗯", "行", "就",
    "又", "才", "你", "我", "别", "啥", "咋", "挺", "还", "没", "什么",
)

#: 分类默认预算（气泡数/单条字数/总字数）——数字参考 reply_budget 三档
CATEGORY_RULES = {
    "身份": {
        "budget": {"parts_max": 3, "max_chars": 16, "max_total": 45},
        # 自报 AI/简历式身份，是这一类唯一不能出的东西
        "banned": ("我是AI", "我是 AI", "助手", "机器人助手"),
        "banned_regex": (),
    },
    "记忆召回": {
        "budget": {"parts_max": 3, "max_chars": 16, "max_total": 60},
        "banned": _IDENTITY_PROBE,
        "banned_regex": (),
    },
    "跨场景": {
        "budget": {"parts_max": 3, "max_chars": 16, "max_total": 60},
        "banned": (),
        "banned_regex": (),
    },
    "语气": {
        "budget": {"parts_max": 3, "max_chars": 16, "max_total": 45},
        "banned": _SERVICE_TONE,
        "banned_regex": (),
        "need_colloquial": True,
    },
    "不露后台": {
        "budget": {"parts_max": 2, "max_chars": 16, "max_total": 35},
        "banned": ("转述", "原图", "接口", "模型", "系统配的",
                   "这轮只看得到", "识别结果", "工具返回", "调用失败",
                   "error", "Error", "Traceback"),
        "banned_regex": (),
    },
    "纠错": {
        "budget": {"parts_max": 3, "max_chars": 16, "max_total": 50},
        "banned": (),
        "banned_regex": (),
    },
    "任务": {
        "budget": {"parts_max": 3, "max_chars": 16, "max_total": 50},
        "banned": ("抱歉，我不能", "我无法", "我做不到"),
        "banned_regex": (),
    },
}


def _norm(s):
    return str(s or "").strip()


def judge(text, case):
    """对一个回复判分。返回 {"ok": bool, "failures": [...], "stats": {...}}。

    case 里可覆盖：expect.budget / expect.banned / expect.banned_regex /
    expect.any_of / expect.min_chars
    """
    text = str(text or "")
    expect = (case or {}).get("expect") or {}
    cat = (case or {}).get("category") or ""
    rules = CATEGORY_RULES.get(cat, {})
    failures = []

    # 1. 预算（与线上同一套口径：按行切气泡，控制标记不计字数）
    budget = dict(rules.get("budget") or {})
    budget.update(expect.get("budget") or {})
    if budget:
        ok, why, stats = reply_budget.check(text, budget)
        if not ok:
            failures.append(f"长度/气泡：{why}")
    else:
        stats = {}

    vis = reply_budget.visible(text)
    if not vis.strip():
        failures.append("空回复（被过滤干净了）")
        return {"ok": False, "failures": failures, "stats": stats}

    # 2. 禁用词：全局 + 分类 + 用例
    banned = list(GLOBAL_BANNED) + list(rules.get("banned") or ()) + list(expect.get("banned") or ())
    low = text.lower()
    for word in banned:
        if not word:
            continue
        if word.lower() in low:
            failures.append(f"禁用词：{word}")

    # 3. 禁用正则：分类 + 用例
    for pat in list(rules.get("banned_regex") or ()) + list(expect.get("banned_regex") or ()):
        try:
            if re.search(pat, text):
                failures.append(f"命中禁用正则：{pat}")
        except re.error as e:
            failures.append(f"正则写错：{pat}（{e}）")

    # 4. 必含（至少一个）
    any_of = expect.get("any_of") or ()
    if any_of and not any(_norm(k) and _norm(k) in text for k in any_of):
        failures.append("没接住上下文（缺关键词：" + "/".join(any_of) + "）")

    # 5. 行首时间锚
    for line in text.split("\n"):
        if _TIME_ANCHOR.search(line):
            failures.append("抄了行首时间锚：" + line.strip()[:16])
            break

    # 6. 长度下限（防止"嗯""哦"式敷衍被当成合格）
    min_chars = int(expect.get("min_chars") or 0)
    if min_chars and len(vis) < min_chars:
        failures.append(f"过短：{len(vis)} 字 < {min_chars}")

    # 7. 语气类：不能是书面语/说明书（长回复还得有口语痕迹）
    if rules.get("need_colloquial"):
        hit = [w for w in _BOOKISH if w in text]
        if hit:
            failures.append(f"书面语/客服腔：{hit[0]}")
        elif len(vis) > 12 and not any(k in text for k in _COLLOQUIAL):
            failures.append("没有口语痕迹（像书面语/说明书）")

    return {"ok": not failures, "failures": failures, "stats": stats}


def summary(results):
    """results: [(case, judged, ...), ...] → 按类汇总的计数（多余字段忽略）。"""
    by_cat = {}
    for item in results:
        case, res = item[0], item[1]
        c = case.get("category", "?")
        slot = by_cat.setdefault(c, {"n": 0, "ok": 0})
        slot["n"] += 1
        slot["ok"] += 1 if res["ok"] else 0
    return by_cat
