# -*- coding: utf-8 -*-
"""
web_search 联网兜底插件

职责：
- 在 before_reply 检测需要联网查证的消息，执行搜索并注入结果
- 在 after_reply 检测 AI 不确定性/幻觉表达，拦截瞎猜式回复
- 目标：不知道就不硬答，必须联网查证时才回答

用法：
    from plugins.web_search import PLUGIN_META, before_reply, after_reply
"""

import sys
import os
import json
import re

# 确保能导入根目录下的 web_access.py
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(PLUGIN_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from web_access import search_web  # noqa: E402

PLUGIN_META = {
    "name": "web_search",
    "version": "0.2.0",
    "description": "联网兜底：需要查证时强制联网搜索，减少幻觉。",
    "author": "EggBloom",
    "group_only": False,
    "enabled": True,
    "dependencies": [],
    "hooks": ["before_reply", "after_reply"],
    "priority": 30,
    "estimated_tokens": 120,
    "tags": ["联网搜索", "API可接入"],
    "page": "plugins/web_search/ui.html",
}

# 触发联网搜索的关键词
DEFAULT_NEED_SEARCH_KEYWORDS = [
    "最新", "现在", "今天", "此刻", "实时", "现在几点",
    "新闻", "热点", "热搜", "发生", "事件",
    "天气", "气温", "下雨", "台风",
    "股价", "行情", "股票", "基金", "汇率",
    "上映", "票房", "比分", "赛程", "结果",
    "谁知道", "有人知道", "真的假的", "确认一下", "求证",
    "查一下", "搜一下", "帮忙查", "有链接吗", "出处",
    "官网", "地址", "电话", "时间", "几点",
]

# 不需要联网的闲聊/表达
DEFAULT_SKIP_PATTERNS = [
    "在干嘛", "在吗", "你好", "嗨", "哈哈", "笑死",
    "不会吧", "离谱", "太强了", "学到了",
]

# ★ 2026-10-21：强触发词 —— 命中即搜，**skip 表不得否决**。
#   原来 `_needs_search` 先查 skip 再查 need，于是
#   「今天天气怎么样啊哈哈哈」会被「哈哈」一票否决，明明在问天气却不搜。
#   语气词是**语气**，不该压过**意图**。这里把"明确的查证意图"单列，
#   让它们拥有比 skip 更高的优先级。
DEFAULT_STRONG_SEARCH_KEYWORDS = [
    "查一下", "搜一下", "帮我查", "帮忙查", "帮我搜",
    "新闻", "热搜", "热点", "股价", "行情", "汇率",
    "天气", "气温", "下雨吗", "会下雨", "台风",
    "上映", "票房", "比分", "赛程",
    "求证", "确认一下", "真的假的", "出处", "有链接吗",
    "现在几点", "今天几号", "星期几",
]

# AI 不确定性/幻觉表达：命中则拦截，不发布
DEFAULT_HALLUCINATION_PATTERNS = [
    "据我所知", "一般来说", "通常来说", "应该", "可能", "也许",
    "好像", "大概是", "我猜", "应该是", "据推测", "据估计",
    "理论上", "理想情况下", "大概率", "小概率",
]

CONFIG_PATH = os.path.join(PLUGIN_DIR, "data", "config.json")


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
        print(f"[WebSearch] 保存失败 {path}: {e}")


def get_config():
    cfg = _load_json(CONFIG_PATH, {})
    return {
        "enabled": bool(PLUGIN_META.get("enabled")),
        "need_search_keywords": cfg.get("need_search_keywords") or list(DEFAULT_NEED_SEARCH_KEYWORDS),
        "skip_patterns": cfg.get("skip_patterns") or list(DEFAULT_SKIP_PATTERNS),
        "hallucination_patterns": cfg.get("hallucination_patterns") or list(DEFAULT_HALLUCINATION_PATTERNS),
    }


def set_config(params: dict):
    if not isinstance(params, dict):
        return {"ok": False, "error": "params must be dict"}
    cfg = _load_json(CONFIG_PATH, {})
    cfg.update(params)
    _save_json(CONFIG_PATH, cfg)
    return get_config()


def _matches_any(text: str, keywords):
    text = text.lower()
    return any(k in text for k in keywords)


# 群聊消息进入插件时带着 `[昵称] 正文` 的行前缀（主链 12031 拼的，给主模型看
# 谁在说话）。这个前缀对**主模型**有用，对**检索词**是纯噪声。
#
# 线上实证（群 829854136，11:49）：
#     群友原话  现在能证明你是人机了吧
#     检索词    [碱式碳酸大脑] 现在能证明你是人机了吧
#     返回      Yahoo 的 The race to tell humans from AI agents 等英文报道
# 昵称占了检索词里唯一具体的名词位置，搜索引擎就把"碱式碳酸大脑"当成主题词。
#
# 只剥**行首的昵称前缀**，不动正文里方的方括号（`[图片]`、`[表情]` 之类
# 由其他层处理；这里改了会影响模型看到的原文）。
_NICK_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"\[[^\[\]\n]{1,30}\]\s*"                       # [昵称] 正文
    r"|"                                            # 或
    r"[^\s：:\[\]]{1,16}\s*[：:](?!//)\s*"          # 昵称：正文（:(?!//) 防止吃掉 http://）
    r")")


def _strip_nick_prefixes(message: str) -> str:
    """把多行合并消息里每行的 `[昵称]` 前缀去掉，用于构造检索词。

    多行时保留分行（多条消息一起搜，语义更完整），只清前缀。

    ★ 2026-10-28 修：每行**循环剥到剥不动为止**（上限 3 层）。
      原来只剥一次，而 merged_text 的真实形态是双层前缀
      `[(/ω＼)·8zff] [༺Matchman༻·fdma] 论文写不完了救命`
      （外层是发送者显示名，内层是消息归属）——剥完一层检索词还带着
      `[༺Matchman༻·fdma]`，拿这玩意去搜索必然返回垃圾。
      真机 1.5.9 压测（20:24）实证，这就是"明明没搜到"的直接原因之一。
    """
    out = []
    for line in (message or "").replace("\r\n", "\n").split("\n"):
        # 循环剥（_NICK_PREFIX_RE 每次只匹配行首一个 `[...]` 块）
        for _ in range(3):
            new = _NICK_PREFIX_RE.sub("", line).strip()
            if new == line:
                break
            line = new
        if line:
            out.append(line)
    return "\n".join(out).strip()


def _needs_search(message: str) -> bool:
    """判断这条消息是否该联网查证。

    优先级（2026-10-21 调整）：
      ① 强触发词命中 → 一定搜（意图明确，语气词不能否决它）
      ② skip 词命中   → 一定不搜（纯闲聊）
      ③ 普通触发词    → 搜
    原来的实现是 ② 在 ①③ 之前，会漏掉「今天天气怎么样啊哈哈哈」这类
    「真问题 + 语气词」的句子——查证需求被一个「哈哈」吃掉了。
    """
    msg = (message or "").strip()
    if not msg or len(msg) < 2:
        return False
    if _matches_any(msg, DEFAULT_STRONG_SEARCH_KEYWORDS):
        return True
    if _matches_any(msg, DEFAULT_SKIP_PATTERNS):
        return False
    return _matches_any(msg, DEFAULT_NEED_SEARCH_KEYWORDS)


def _pick_search_query(message: str) -> str:
    """从合并批次里挑**一行**做检索词（2026-10-28）。

    依据（用户此前实测 + web_access.search_web 注释里的 4 个真机样本）：
    自有搜索对"一个提示词"的结果明显好于整句/整批 ——
    口语修饰词会把实体淹掉，整句得分 0.93-0.99（搜错），砍短后 1.68-1.87（搜对）。

    normalize_query 只会删虚词，**不会从三条不同话题里挑一条**；
    兜底拿整个合并批次当检索词时，批次里混着的别人闲聊就成了主语。
    所以多行批次必须在进搜索前就收敛成一行。

    挑选顺序：
      1. 第一条**单独命中** strong/need 关键词的行（最可能是触发源）
      2. 兜底：第一条非空行
    长度截到 40 字（再长交给 normalize_query 也提炼不动）。
    """
    cfg = get_config()
    strong = cfg.get("strong_search_keywords") or DEFAULT_STRONG_SEARCH_KEYWORDS
    need = cfg.get("need_search_keywords") or DEFAULT_NEED_SEARCH_KEYWORDS
    cleaned = _strip_nick_prefixes(message or "")
    lines = [ln.strip() for ln in cleaned.split("\n") if ln.strip()]
    if not lines:
        return ""
    for ln in lines:
        if _matches_any(ln, strong) or _matches_any(ln, need):
            return ln[:40]
    return lines[0][:40]


def _is_uncertain_reply(reply: str) -> bool:
    reply = (reply or "").strip()
    if not reply:
        return False
    return _matches_any(reply, DEFAULT_HALLUCINATION_PATTERNS)


def before_reply(message: str = "", group_id: str = "", context: dict = None, **kwargs) -> dict | None:
    """
    如果检测到需要联网查证，执行搜索并注入结果；否则放行。
    """
    msg = (message or "").strip()
    if not msg:
        return None

    # 判定用原文（整批），检索词用**挑出来的一行**（见 _pick_search_query）。
    # 判定不能改用清理后的文本：清理只删前缀，不影响关键词命中，但保留原文
    # 更符合"这条消息本身要不要查"的语义，改动面也最小。
    if not _needs_search(msg):
        return None
    query = _pick_search_query(msg) or _strip_nick_prefixes(msg) or msg

    try:
        search_result = search_web(query)
    except Exception as e:
        print(f"[web_search] 搜索失败: {e}")
        search_result = f"搜索「{query}」失败，请稍后再试。"

    return {
        "action": "inject",
        "content": (
            "【联网搜索结果】\n"
            f"{search_result}\n\n"
            "请基于以上搜索结果回答用户问题。"
            "如果搜索结果为空或不够明确，直接说“我没查到，不确定”。"
        ),
        "meta": {
            "trigger": "web_search",
            "query": query,
        },
    }


def after_reply(message: str = "", reply: str = "", context: dict = None, **kwargs) -> dict | None:
    """
    如果 AI 回复里出现了明显的不确定性/瞎猜表达，直接拦截。
    避免把“可能”“应该”“据我所知”这类内容发给用户。
    """
    if not reply:
        return None

    if _is_uncertain_reply(reply):
        return {
            "action": "override",
            "reply": "这事我没查到确切结果，不确定的事我不瞎说了。",
            "meta": {
                "trigger": "web_search",
                "reason": "uncertain",
            },
        }

    return None


def on_load():
    print("[web_search] 联网兜底插件已启用")


def get_api_status():
    """连接监测：检测搜索源是否可达。结果由 plugin_manager 缓存。"""
    from web_access import check_connectivity
    try:
        return check_connectivity()
    except Exception as e:
        return {"ok": False, "status": f"检测异常: {e}", "detail": str(e)}


def on_unload():
    print("[web_search] 联网兜底插件已卸载")


__all__ = [
    "PLUGIN_META",
    "before_reply",
    "after_reply",
    "on_load",
    "on_unload",
    "get_api_status",
]
