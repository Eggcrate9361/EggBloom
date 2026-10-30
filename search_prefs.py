# -*- coding: utf-8 -*-
"""search_prefs.py —— 搜索策略开关（2026-10-29，用户要求）。

背景（真机，2026-10-29 13:21–13:30）
    自家的 web_access 抓取（Bing + 360 合并）在**长问句式**查询上质量塌得很厉害：
      · 问「明日方舟 2026 感谢庆典 半周年 限定 是谁」→ 返回 2023/2025 的 SEO 盘点页；
      · 换词重搜「以风雪为誓」→ 只捞到 PRTS 的技能描述；
      · 最终模型回「今年还没官宣」——**有证据却答成没有**。
    同一时刻 DeepSeek 原生搜索（ds_search）返回的是带发布日期的结构化结果，
    对"最近/最新"类问题明显更靠谱。所以给兜底插件加一个开关：**优先模型搜索**。

开关存哪
    `plugins/web_search/data/config.json` 的 `prefer_model_search`
    （就是兜底插件自己的配置，插件页可改；默认 **开**）。
    环境变量 `EGGBLOOM_SEARCH_PREFER=own` 可临时压回自家搜索（排障用，不必改配置）。

两种调用者共用本模块
    · bot_core 的 `search_web` 工具（主链）
    · plugins/web_search 的 before_reply（兜底注入）
    判定与取文本都只在这里实现一次 —— 避免"主链开了、插件没开"的分叉。
"""
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_HERE, "plugins", "web_search", "data", "config.json")

#: 默认值：**优先模型搜索**（用户 2026-10-29 明确要的行为）
_DEFAULT_PREFER = True

#: 问句尾巴/客套话 —— 检索前剥掉。真机：把「是谁/今年/帮我查」一起丢给
#: DeepSeek 检索，它会把整句当"讨论帖"找，相关度 0.00；只留核心词反而能命中。
_ASK_TAIL_RE = re.compile(
    r"(请问|帮我查一下|帮我查|帮忙查|帮我搜一下|帮我搜|查一下|搜一下|查查|搜搜|"
    r"告诉我|说一下|到底是|究竟|是谁|是什么|是什么东西|多少钱|多少|怎么样|怎么办|"
    r"为什么|为啥|什么时候|啥时候|在哪里|哪里|哪个|哪种|吗|呢|啊|吧|呀|哦)$"
)
#: 开头的客套/请求词（「帮我查一下…」「请问…」）——整形时一并剥掉。
_ASK_HEAD_RE = re.compile(
    r"^(请问|想问一下|问一下|我想知道|帮我查一下|帮我查查|帮我查|帮忙查一下|帮忙查|"
    r"帮我搜一下|帮我搜搜|帮我搜|查一下|搜一下|查查|搜搜|告诉我|说一下)"
)
#: 只影响语气的词（查询里保留"年份"这类硬约束，只去虚词）
_FILLER_RE = re.compile(r"(的|了|吧|呢|啊|呀|嘛|哦|哈)")


def _raw_config():
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def prefer_model_search():
    """要不要先走模型搜索（DeepSeek 原生）。环境变量优先级最高。"""
    env = str(os.environ.get("EGGBLOOM_SEARCH_PREFER", "")).strip().lower()
    if env in ("own", "local", "self", "0", "false", "no"):
        return False
    if env in ("model", "ds", "deepseek", "1", "true", "yes"):
        return True
    cfg = _raw_config()
    if "prefer_model_search" in cfg:
        return bool(cfg.get("prefer_model_search"))
    return _DEFAULT_PREFER


# ── 结果层策略（2026-10-30）─────────────────────────────────
#  两个键都只影响**结果层**，不影响搜索走哪条路：
#    blocked_domains  命中的域名不进结果（硬过滤，与 search_core 的 _LOW_QUALITY 降权不是一回事）
#    diversity        排序后按站交错（前几条尽量来自不同站点）
#
#  为什么放在这里读：web_access 与插件都要用同一份判断。插件页写的是
#  `plugins/web_search/data/config.json`，两处各读一份迟早分叉（10-23 已经踩过）。


def blocked_domains():
    """生效中的域名黑名单。读不到/格式不对一律当空表 —— 搜索不能因此不可用。"""
    env = str(os.environ.get("EGGBLOOM_SEARCH_BLOCKED", "")).strip()
    if env:
        return [x.strip() for x in re.split(r"[,;\s]+", env) if x.strip()]
    cfg = _raw_config()
    raw = cfg.get("blocked_domains")
    if isinstance(raw, str):
        raw = re.split(r"[,;\s]+", raw)
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for x in raw:
        s = str(x or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def diversity_enabled():
    """排序后是否按站交错。默认开：一个站占满前三，等于把一次搜索缩成一个口径。"""
    env = str(os.environ.get("EGGBLOOM_SEARCH_DIVERSITY", "")).strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    cfg = _raw_config()
    if "diversity" in cfg:
        return bool(cfg.get("diversity"))
    return True


def shape_query(text):
    """把一句人话收成检索词：去客套/疑问尾巴、去语气虚词、压空白。

    只动"怎么问"，不动"问什么"：年份、作品名、事件名全部保留。
    收完太短（<2 字）就退回原文，宁可不整形也别把查询弄没。
    """
    q = str(text or "").strip()
    if not q:
        return q
    orig = q
    # 先剥开头的请求词（「帮我查一下原神7.1更新」→「原神7.1更新」）
    q = _ASK_HEAD_RE.sub("", q).strip(" ，,。.、！!？?~～")
    # 反复剥尾（「…是谁啊」要剥两层）
    for _ in range(3):
        new = _ASK_TAIL_RE.sub("", q).strip(" ，,。.、！!？?~～")
        if new == q:
            break
        q = new
    if len(q) >= 8:
        q2 = _FILLER_RE.sub("", q)
        if len(q2) >= 2:
            q = q2
    q = re.sub(r"\s+", " ", q).strip()
    if len(q) < 2:
        return orig
    return q


def _ds_search_once(query):
    """跑一次 DeepSeek 原生搜索，返回 (items, q_info) 或 (None, None)。"""
    try:
        import ds_search
        if not ds_search.available():
            return None, None
        results = ds_search.search(str(query or ""), count=8, ori_question=str(query or ""))
        if not results:
            return [], None
        import search_core
        items = ds_search.to_core_list(results, source="ds")
        if not items:
            return [], None
        return items, search_core.quality_of(items, query)
    except Exception:
        return None, None


def model_search_text(query, log=None, retry_thin=True):
    """用 DeepSeek 原生搜索取结果，返回给模型读的文本；拿不到返回空串。

    ★ 2026-10-29 真机：「明日方舟 半周年 限定干员 2026」8 条结果、质量 thin、
      相关度 0.00 —— 引擎把整句当"讨论"在找。所以结果过薄时**换短查询再搜一次**
      （整形后的核心词），把两批结果合并去重再排序；仍为薄就如实标注，
      不许装作查到了。
    """
    def _log(msg):
        try:
            if log:
                log(msg)
        except Exception:
            pass

    try:
        import search_core
    except Exception as e:
        _log(f"[搜索] search_core 不可用: {e}")
        return ""

    items, q_info = _ds_search_once(query)
    if items is None:
        _log("[搜索] 模型搜索不可用/未配置 DeepSeek Key")
        return ""
    if not items:
        _log(f"[搜索] 模型搜索「{query}」返回空")
        return ""

    def _thin(info):
        if not info:
            return False
        return str(info.get("verdict") or "") in ("thin", "poor", "bad", "") or \
            float(info.get("top_score") or 0) < 0.5

    if retry_thin and _thin(q_info):
        short = shape_query(query)
        if short and short != query and len(short) < len(query):
            items2, info2 = _ds_search_once(short)
            if items2:
                # 按 url 去重合并（保留高分条目顺序由 format_for_model 重排）
                seen = set()
                merged = []
                for it in list(items2) + list(items):
                    u = str(it.get("url") or it.get("title") or "")
                    if u in seen:
                        continue
                    seen.add(u)
                    merged.append(it)
                items = merged
                q_info = search_core.quality_of(items, short) or info2 or q_info
                _log(f"[搜索] 结果过薄 → 换短查询重搜一次：「{short}」→ "
                     f"合并 {len(items)} 条 (质量 {(q_info or {}).get('verdict')})")

    try:
        # ★ 2026-10-30：黑名单对模型搜索这条路同样生效 —— 否则用户在插件页
        #   屏蔽的域名会从"优先模型搜索"这条更常用的路原样回来。
        _blocked = blocked_domains()
        if _blocked:
            items = search_core.filter_blocked_domains(items, _blocked)
            if not items:
                _log("[搜索] 结果全部命中域名黑名单")
                return ""
        body = search_core.format_for_model(items, query)
        if not body:
            return ""
        parts = [f"【{query}】搜索结果：",
                 search_core.wrap_external(body),
                 search_core.EXTERNAL_NOTE]
        hint = search_core.quality_hint(q_info)
        if hint:
            parts.append("\n\n" + hint)
        _log(f"[搜索] 模型搜索「{query}」→ {len(items)} 条 "
             f"(质量 {(q_info or {}).get('verdict')}, top {(q_info or {}).get('top_score')})")
        return "\n".join(parts)
    except Exception as e:
        _log(f"[搜索] 模型搜索结果排版失败: {str(e)[:120]}")
        return ""
