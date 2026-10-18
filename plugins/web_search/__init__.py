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


def _needs_search(message: str) -> bool:
    msg = (message or "").strip()
    if not msg or len(msg) < 2:
        return False
    if _matches_any(msg, DEFAULT_SKIP_PATTERNS):
        return False
    return _matches_any(msg, DEFAULT_NEED_SEARCH_KEYWORDS)


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

    if not _needs_search(msg):
        return None

    try:
        search_result = search_web(msg)
    except Exception as e:
        print(f"[web_search] 搜索失败: {e}")
        search_result = f"搜索「{msg}」失败，请稍后再试。"

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
            "query": msg,
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
