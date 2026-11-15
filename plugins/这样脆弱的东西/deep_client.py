# -*- coding: utf-8 -*-
"""蒸馏专用 LLM 通道：和 bot 共用凭据，但独立配置。

为什么不能直接用 bot 注入进来的那个客户端？

  1. 推理型模型（实测 deepseek-v4-flash）默认会把 max_tokens 全烧在思考上：
     completion=cap、reasoning=cap、content 为空，接口还不报错；
     单次调用轻松超过 provider 里写死的 60 秒超时。
  2. 但 bot 自己在聊天时可能正需要这份思考能力 ——
     顺手把共享客户端改成"不许思考"，等于悄悄改掉了机器人的行为。

所以这里单独造一支笔：同一套凭据、关掉思考、低温度、不重试
（重试交给上层的限速闸门统一管，避免两处重试叠成雪崩）。

若造不出来（没配 Key / 非 OpenAI 兼容端点），就退回注入的客户端 —— 宁慢不废。
"""
import json
import os
import sys

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(os.path.dirname(PLUGIN_DIR))

# 这些 provider 的思考字段叫法不同，默认不要塞 OpenAI 风格的 thinking
_NON_OPENAI = {"anthropic", "claude", "google", "gemini", "ollama"}

_cache = {"client": None, "note": ""}


def _config():
    from .config import get_config
    return get_config()


def extra_body_for(cfg, provider):
    """算出该给请求体附加什么字段。

    优先级：显式配置的 deep_extra_body > deep_disable_thinking 开关。
    """
    raw = cfg.get("deep_extra_body")
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except Exception:
            raw = None
    if isinstance(raw, dict) and raw:
        return raw
    if cfg.get("deep_disable_thinking") and (provider or "").lower() not in _NON_OPENAI:
        return {"thinking": {"type": "disabled"}}
    return None


def build(cfg=None):
    """按配置造一支蒸馏专用客户端。失败返回 (None, 原因)。"""
    cfg = cfg or _config()
    if not cfg.get("deep_client_direct", True):
        return None, "配置要求只用注入的客户端"
    try:
        sys.path.insert(0, APP_DIR)
        from config import load_config, get_provider_api_key
        from ai_client import AIClient
    except Exception as e:
        return None, f"无法导入主程序 AI 模块：{e}"

    try:
        live = load_config()
    except Exception as e:
        return None, f"读取主程序配置失败：{e}"

    provider = live.get("api_provider", "deepseek")
    api_key = get_provider_api_key(provider)
    if not api_key and (provider or "").lower() != "ollama":
        return None, "拿不到 API Key"

    body = extra_body_for(cfg, provider)
    try:
        client = AIClient(
            live.get("api_url", ""), api_key, live.get("model_name", "deepseek-chat"),
            provider=provider, temperature=float(cfg.get("deep_temperature", 0.3)),
            max_retries=1, extra_body=body,
        )
    except Exception as e:
        return None, f"构造客户端失败：{e}"

    note = f"{provider} / {live.get('model_name')}"
    if body:
        note += f" / 附加 {json.dumps(body, ensure_ascii=False)}"
    return client, note


def get(injected=None, cfg=None):
    """取蒸馏专用客户端。优先自建，失败退回注入的那个。

    返回 (client, note)：note 用来在 UI 日志里说明这一轮到底走哪条路。
    """
    cfg = cfg or _config()
    if _cache["client"] is not None:
        return _cache["client"], _cache["note"]

    client, note = build(cfg)
    if client is None:
        if injected is not None:
            return injected, f"沿用 bot 注入的客户端（{note}）"
        return None, note
    _cache["client"] = client
    _cache["note"] = note
    return client, note


def reset():
    """配置变了要重新造。"""
    _cache["client"] = None
    _cache["note"] = ""
