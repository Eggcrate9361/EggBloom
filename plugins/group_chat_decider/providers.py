# -*- coding: utf-8 -*-
"""
group_chat_decider providers.py
===============================
统一调用层（薄封装）——文本决策 + 视觉描述。

★ 2026-10-03 收敛（任务 2.2）
本文件原先**自带一整套** HTTP 调用实现（`_pick_http_lib` / `_post_once` /
`_fallback_chain` / 手写 requests+urllib 双路径 / 手拼 payload），与主层
`providers.py` + `ai_client.py` 完全重复，是四套 LLM 调用路径之一。

现在只保留**本插件特有的部分**：
  - 预设选择与降级链（zhipu / aliyun / siliconflow / ollama，见 free_models.py）
  - 失败自动重试 + 降级备用预设
  - 把「预设」翻译成主层 `make_provider(...)` 的参数
所有 HTTP / 重试 / SSL / 响应解析 **一律交给主层 `providers.py`**，
不再自己实现。对外 API（call_text / call_vision）签名保持不变，
因此 __init__.py 与 vision.py 的调用点无需改动。

公开：
  - call_text(prompt, max_tokens=400, provider_id=None) -> str
  - call_vision(image_ref, prompt, provider_id=None) -> {ok, result|error, provider, elapsed_ms}
"""
import base64
import os
import sys
import time
from pathlib import Path

from . import free_models

PLUGIN_DATA_DIR = free_models.DATA_DIR

_TEXT_TIMEOUT = 20
_VISION_TIMEOUT = 30


def _main_providers():
    """惰性取项目根的 providers 模块（主层唯一 HTTP 实现）。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from providers import make_provider, OpenAIProvider  # noqa: F401
    return make_provider


def _image_to_data_url(image_path: str) -> str:
    """本地图片 → data URI。复用主层 ai_client.image_to_base64，避免重复实现。"""
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if root not in sys.path:
            sys.path.insert(0, root)
        from ai_client import image_to_base64
        url = image_to_base64(image_path)
        if url:
            return url
    except Exception:
        pass
    # 兜底：主层不可用时自行编码（保持原行为）
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"image not found: {image_path}")
    ext = p.suffix.lower()
    mime = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    }.get(ext, "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


def _normalize_image(image_ref: str) -> str:
    image_ref = (image_ref or "").strip()
    if not image_ref:
        raise ValueError("empty image ref")
    if image_ref.startswith("data:"):
        return image_ref
    if image_ref.startswith("http://") or image_ref.startswith("https://"):
        return image_ref
    return _image_to_data_url(image_ref)


def _build_client(pid: str):
    """把免费预设翻译成主层 provider 适配器。返回 (client_like, model, error)。

    预设全部是 OpenAI 兼容端点（含 ollama 的 /v1 兼容口），故 provider 名一律
    走 "openai"（主层 OpenAIProvider）；ollama 的 base_url 已带 /v1，同样兼容。
    """
    try:
        make_provider = _main_providers()
    except Exception as e:
        return None, "", f"主层 providers 加载失败: {e}"
    try:
        p = free_models.get_provider(pid)
    except Exception as e:
        return None, "", f"provider 解析失败: {e}"
    api_key = p.get("api_key")
    if not api_key:
        return None, "", f"{pid} 未配置 key（凭据管理器 / 环境变量）"
    base_url = (p.get("base_url") or "").rstrip("/")
    if not base_url:
        return None, "", f"{pid} 缺少 base_url"
    model = p.get("text_model", "")
    adapter = make_provider(
        api_url=f"{base_url}/chat/completions",
        api_key=api_key,
        model=model,
        provider="openai",
        max_retries=2,
        temperature=None,
    )
    return adapter, model, None


def _fallback_chain(provider_id: str) -> list:
    """备用预设链：当前预设失败时降级。ollama 不参与跨网降级。"""
    chain = [provider_id]
    if provider_id == "ollama":
        return chain
    for alt in ("zhipu", "aliyun", "siliconflow"):
        if alt not in chain:
            chain.append(alt)
    return chain


def call_text(prompt: str, max_tokens: int = 400, provider_id: str = None) -> str:
    """文本决策调用。走激活预设，失败自动降级备用预设。返回最终文本（可能为空串）。"""
    pid = provider_id or free_models.get_active_model()
    for attempt_pid in _fallback_chain(pid):
        adapter, _model, err = _build_client(attempt_pid)
        if adapter is None:
            continue
        try:
            reply, _tools, _usage = adapter.chat(
                [{"role": "user", "content": prompt}], max_tokens=max_tokens
            )
        except Exception:
            continue
        if reply:
            return reply.strip()
    return ""


def call_vision(image_ref: str, prompt: str, provider_id: str = None) -> dict:
    """视觉描述调用。返回 {ok, result|error, provider, elapsed_ms}"""
    pid = provider_id or free_models.get_active_model()
    try:
        image_url = _normalize_image(image_ref)
    except Exception as e:
        return {"ok": False, "error": f"image 解析失败: {e}", "provider": "", "elapsed_ms": 0}

    started = time.time()
    for attempt_pid in _fallback_chain(pid):
        try:
            p = free_models.get_provider(attempt_pid)
        except Exception:
            continue
        if not p.get("api_key"):
            continue
        model = p.get("vision_model", "")
        base_url = (p.get("base_url") or "").rstrip("/")
        if not base_url or not model:
            continue
        try:
            make_provider = _main_providers()
            adapter = make_provider(
                api_url=f"{base_url}/chat/completions",
                api_key=p.get("api_key"),
                model=model,
                provider="openai",
                max_retries=1,
                temperature=None,
            )
            reply, _tools, _usage = adapter.chat(
                [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": prompt},
                    ],
                }],
                max_tokens=400,
            )
        except Exception:
            continue
        if not reply:
            continue
        return {
            "ok": True,
            "result": {"summary": reply.strip(), "vision_model": model},
            "provider": attempt_pid,
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    return {"ok": False, "error": "所有视觉预设均失败", "provider": "",
            "elapsed_ms": int((time.time() - started) * 1000)}
