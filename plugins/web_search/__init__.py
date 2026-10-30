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
import time

# 确保能导入根目录下的 web_access.py
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(PLUGIN_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from web_access import search_web  # noqa: E402

PLUGIN_META = {
    "name": "web_search",
    "version": "1.2.0",
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

# ── 「本轮真的联网过吗」标记（2026-10-29 修）────────────────────────
# 事故（真机 13:35:34，私聊）：用户发图 → 图片理解失败 → 模型回「图挂了」，
# 随后 after_reply 把整句覆盖成「这事我没查到确切结果，不确定的事我不瞎说了。」
# 用户看到的是一条莫名其妙的拒绝。根因是幻觉拦截**不看有没有搜过** ——
# 只要回复里出现「应该/可能/好像」这种日常词就整句替换。
#
# 现在：before_reply 真的注入过搜索结果才记一个短时标记（同一对象 180 秒），
# after_reply 只在标记存在时才可能拦截。没联网 = 永不改写回复。
_INJECTED = {}
_INJECT_TTL = 180.0


# ── 搜索统计 / 最近查询（1.5.10 精致化：插件页要真实数据）────────────
#: 只存内存（重启清空）—— 它是观测量，不参与任何判断。
_RECENT_SEARCH = __import__("collections").deque(maxlen=50)
_SEARCH_STATS = {"total": 0, "model": 0, "own": 0, "fail": 0, "ms_total": 0}


def _record_search(query: str, source: str, ms: float, chars: int = 0) -> None:
    try:
        _SEARCH_STATS["total"] += 1
        if source in ("model", "own", "fail"):
            _SEARCH_STATS[source] += 1
        _SEARCH_STATS["ms_total"] += int(ms)
        _RECENT_SEARCH.append({
            "ts": time.time(), "query": str(query or "")[:80],
            "source": source, "ms": int(ms), "chars": int(chars),
        })
    except Exception:
        pass


def _reply_key(group_id="", context=None):
    ctx = context or {}
    who = str(ctx.get("sender") or ctx.get("user_id") or ctx.get("uid") or "")
    return f"{group_id or 'private'}:{who}"


def _mark_injected(group_id="", context=None):
    try:
        _INJECTED[_reply_key(group_id, context)] = time.time()
        # 防无界：只留最近 50 条
        if len(_INJECTED) > 50:
            for k in sorted(_INJECTED, key=_INJECTED.get)[:10]:
                _INJECTED.pop(k, None)
    except Exception:
        pass


def _recently_injected(group_id="", context=None):
    try:
        ts = _INJECTED.get(_reply_key(group_id, context))
        return bool(ts) and (time.time() - ts) <= _INJECT_TTL
    except Exception:
        return False


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
        # ★ 2026-10-29（用户要求）：优先模型搜索。默认开 ——
        #   自家抓取的长问句结果质量太差（真机：2026 的问题返回 2024 的盘点页），
        #   而 DeepSeek 原生搜索返回带日期的结构化结果。开关只在配置里，
        #   主链的 search_web 也读同一个键（search_prefs）。
        "prefer_model_search": bool(cfg.get("prefer_model_search", True)),
        # ★ 2026-10-29：幻觉拦截开关。默认开，但只在"本轮真的联网过"时才可能生效。
        "block_uncertain_reply": bool(cfg.get("block_uncertain_reply", True)),
        # ★ 2026-10-30：结果层两个策略。
        #   blocked_domains 命中的域名不进结果（硬过滤，让位给后面的候选）；
        #   diversity 排序后按站交错，前几条尽量来自不同站点。
        #   两者都由 search_prefs 读同一个 config.json，主链与插件不会分叉。
        "blocked_domains": list(cfg.get("blocked_domains") or []),
        "diversity": bool(cfg.get("diversity", True)),
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
# 线上实证（群 123456789，11:49）：
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


#: 信息需求判据（1.5.10 C5b）：问号或疑问词。
#: 真机压测里「论文写不完了救命」这类吐槽被注入了【联网搜索结果】——
#: 关键词表里的"今天/现在"会把感叹句也当成查询，白花一次搜索和延迟。
_INFO_NEED_RE = re.compile(
    r"[?？]|吗|呢|怎么|怎样|为什么|为啥|多少|哪[里个些]|谁|是不是|有没有|能不能|"
    r"可不可以|多久|几点|如何|什么"
)


def _looks_like_info_need(message: str) -> bool:
    return bool(_INFO_NEED_RE.search(str(message or "")))


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
    # ★ 2026-10-29（1.5.10 C5b）：普通触发词只对**问句/信息需求**生效。
    #   感叹、吐槽、自言自语不搜 —— 否则"今天好累"也会因为命中"今天"去联网。
    if not _looks_like_info_need(msg):
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


def _text_content_only(message: str) -> str:
    """去掉图片/表情/语音占位符与视觉描述块，只留**用户打出来的字**。

    为什么（2026-10-29 用户）：「图片应该由模型理解，不该触发搜索」。
    图片消息的文本是 `[图片]` 或一段【视觉识别 · …图片内容】转述，
    在里面做关键词匹配等于让"图里出现的字"决定要不要联网 —— 双层错误。
    """
    s = str(message or "")
    # 视觉描述块整段丢掉（它是转述，不是用户说的话）
    s = re.sub(r"【视觉识别[^】]*】.*", " ", s, flags=re.S)
    # 媒体占位符
    s = re.sub(r"\[(?:图片|表情|动画表情|语音|视频|卡片|分享|文件|引用)[^\]]{0,40}\]", " ", s)
    # 群聊消息前缀：[HH:MM] [昵称] / 昵称：
    s = re.sub(r"(?m)^\s*(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?(?:\[[^\]]{0,40}\]\s*|[^\s：:\[\]]{1,16}\s*[：:]\s*)", "", s)
    s = re.sub(r"@\S{1,40}", " ", s)
    return s.strip()


def before_reply(message: str = "", group_id: str = "", context: dict = None, **kwargs) -> dict | None:
    """
    如果检测到需要联网查证，执行搜索并注入结果；否则放行。
    """
    msg = (message or "").strip()
    if not msg:
        return None

    # ★ 2026-10-29：纯图片/表情消息永不联网。图片理解走视觉层（bot_core），
    #   搜索插件不该因为图里的字或占位符去查网。
    _text_only = _text_content_only(msg)
    if not _text_only:
        return None

    # 判定用原文（整批），检索词用**挑出来的一行**（见 _pick_search_query）。
    # 判定不能改用清理后的文本：清理只删前缀，不影响关键词命中，但保留原文
    # 更符合"这条消息本身要不要查"的语义，改动面也最小。
    if not _needs_search(_text_only):
        return None
    query = _pick_search_query(_text_only) or _strip_nick_prefixes(_text_only) or _text_only

    # 2026-10-29：检索前整形（去「是谁/帮我查/今年」这类问句成分），
    # 与 search_prefs 的短查询重搜共用同一套规则。
    try:
        from search_prefs import shape_query
        shaped = shape_query(query)
        if shaped:
            query = shaped
    except Exception:
        pass

    # ★ 2026-10-29（用户要求）：优先模型搜索（DeepSeek 原生），开关=prefer_model_search。
    #   先模型、模型拿不到再回自家抓取 —— 反过来（先自家）已经实测过：
    #   长问句会把 2024 年的盘点页当答案，模型据此回「还没官宣」。
    #   这里与 bot_core 的 search_web 共用 search_prefs，两处不会分叉。
    search_result = ""
    _t0 = time.time()
    _source = ""
    try:
        from search_prefs import prefer_model_search, model_search_text
        if prefer_model_search():
            search_result = model_search_text(query, log=lambda m: print(f"[web_search] {m}"))
            if search_result:
                _source = "model"
                print(f"[web_search] 模型搜索命中: {query}")
    except Exception as e:
        print(f"[web_search] 模型搜索不可用（回退自家搜索）: {e}")
        search_result = ""

    if not search_result:
        try:
            search_result = search_web(query)
            _source = "own"
            print(f"[web_search] 自家搜索: {query}")
        except Exception as e:
            print(f"[web_search] 搜索失败: {e}")
            search_result = f"搜索「{query}」失败，请稍后再试。"
            _source = "fail"

    _record_search(query, _source or ("fail" if not search_result else "own"),
                   (time.time() - _t0) * 1000.0, len(str(search_result or "")))

    # 真的拿到内容才记标记（失败提示语不算"查过"，否则又会去拦截正常回复）
    try:
        if search_result and "失败" not in str(search_result)[:40]:
            _mark_injected(group_id, context)
    except Exception:
        pass

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
    """幻觉拦截：**只对本轮真的联网查过的回复生效**。

    2026-10-29 真机事故：没联网的私聊回复（「图挂了」）因为日常词被整句换成
    罐头拒绝语。所以这里先查标记；没标记就直接放行，并且可在插件页关掉拦截。
    """
    if not reply:
        return None

    group_id = str(kwargs.get("group_id") or "")
    try:
        if not bool(get_config().get("block_uncertain_reply", True)):
            return None
    except Exception:
        pass

    if not _recently_injected(group_id, context):
        # 没联网 → 这一句的不确定性是人格的一部分，不该被插件改写
        return None

    if _is_uncertain_reply(reply):
        return {
            "action": "override",
            "reply": "这个我联网查了，没看到确切说法，就不瞎说了。",
            "meta": {
                "trigger": "web_search",
                "reason": "uncertain_after_search",
            },
        }

    return None


def on_load():
    print("[web_search] 联网兜底插件已启用")


def _act_stats(_p=None):
    s = dict(_SEARCH_STATS)
    n = max(1, s["total"])
    return {"ok": True, "stats": s, "avg_ms": round(s["ms_total"] / n) if s["total"] else 0}


def _act_recent(_p=None):
    return {"ok": True, "searches": list(reversed(list(_RECENT_SEARCH)))}


def _act_engines(_p=None):
    """引擎健康快照（失败分类 / 冷却剩余秒数）。纯观测。"""
    try:
        from web_access import engine_status
        return {"ok": True, "engines": engine_status()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _act_reset_engines(_p=None):
    """手动解除冷却（"现在就想再试一次"的出口）。"""
    try:
        from web_access import reset_engines, engine_status
        reset_engines()
        return {"ok": True, "engines": engine_status()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


PLUGIN_ACTIONS = {
    "get_config": get_config,
    "set_config": set_config,
    "stats": _act_stats,
    "recent": _act_recent,
    "engines": _act_engines,
    "reset_engines": _act_reset_engines,
}


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
