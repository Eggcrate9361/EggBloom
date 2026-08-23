# -*- coding: utf-8 -*-
"""
group_chat_decider providers.py
===============================
统一模型调用层——文本决策 + 视觉描述，OpenAI 兼容格式，
provider 从 free_models.py 的预设注册表选择，密钥完全插件自包含。

支持：
  - call_text(prompt)：文本决策（GLM-4-Flash / qwen-turbo / ...）
  - call_vision(image_ref, prompt)：图片描述（GLM-4V-Flash / qwen-vl-plus / ...）
  - 失败自动重试 + 降级（备用预设）
"""
import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import free_models

PLUGIN_DATA_DIR = free_models.DATA_DIR

_TEXT_TIMEOUT = 20
_VISION_TIMEOUT = 30


def _pick_http_lib():
    try:
        import requests
        return requests
    except Exception:
        return None


def _image_to_data_url(image_path: str) -> str:
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"image not found: {image_path}")
    data = p.read_bytes()
    ext = p.suffix.lower()
    mime = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    }.get(ext, "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _normalize_image(image_ref: str) -> str:
    image_ref = (image_ref or "").strip()
    if not image_ref:
        raise ValueError("empty image ref")
    if image_ref.startswith("data:"):
        return image_ref
    if image_ref.startswith("http://") or image_ref.startswith("https://"):
        return image_ref
    return _image_to_data_url(image_ref)


def _post_once(use_http, url, headers, payload, timeout):
    if use_http is not None:
        resp = use_http.post(url, headers=headers, json=payload, timeout=timeout)
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:1000]}
        return resp.status_code, data
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            data = {"raw": raw[:1000].decode("utf-8", "ignore")}
        return r.status, data


def _fallback_chain(provider_id: str) -> list:
    """备用预设链：当前预设失败时降级。ollama 不参与跨网降级。"""
    chain = [provider_id]
    if provider_id == "ollama":
        return chain
    for alt in ("zhipu", "aliyun", "siliconflow"):
        if alt not in chain:
            chain.append(alt)
    return chain


def _call_chat(provider_id: str, messages: list, model: str, timeout: float,
               max_tokens: int = 400) -> tuple:
    """调用单个预设的 chat completions。返回 (text, error)"""
    try:
        p = free_models.get_provider(provider_id)
    except Exception as e:
        return None, f"provider 解析失败: {e}"
    api_key = p.get("api_key")
    if not api_key:
        return None, f"{provider_id} 未配置 key（凭据管理器 / 环境变量）"
    url = f"{p['base_url'].rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    use_http = _pick_http_lib()
    try:
        status, data = _post_once(use_http, url, headers, payload, timeout)
    except urllib.error.HTTPError as e:
        raw = (e.read(500).decode("utf-8", "ignore") if hasattr(e, "read") else "") or str(e)
        if e.code in (401, 403):
            return None, f"{provider_id}: 鉴权失败 HTTP {e.code}（key 无效）"
        return None, f"{provider_id}: HTTP {e.code} {raw[:120]}"
    except Exception as e:
        return None, f"{provider_id}: 网络错误 {e}"
    if status != 200:
        msg = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else str(data)[:200]
        return None, f"{provider_id}: HTTP {status} {msg}"
    try:
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        return (content or "").strip() or None, None
    except Exception:
        return None, f"{provider_id}: 响应格式异常"


def call_text(prompt: str, max_tokens: int = 400, provider_id: str = None) -> str:
    """
    文本决策调用。走激活预设，失败自动降级备用预设。
    返回最终文本（可能为空串）。
    """
    pid = provider_id or free_models.get_active_model()
    for attempt_pid in _fallback_chain(pid):
        messages = [{"role": "user", "content": prompt}]
        model = free_models.get_provider(attempt_pid).get("text_model", "")
        text, err = _call_chat(attempt_pid, messages, model, _TEXT_TIMEOUT, max_tokens)
        if text:
            return text
        if err and "未配置 key" in err:
            continue  # 无 key 直接试下一个
        if err:
            # 有 key 但失败，稍等再试下一个
            continue
    return ""


def call_vision(image_ref: str, prompt: str, provider_id: str = None) -> dict:
    """
    视觉描述调用。返回 {ok, result|error, provider, elapsed_ms}
    """
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
        api_key = p.get("api_key")
        if not api_key:
            continue
        model = p.get("vision_model", "")
        url = f"{p['base_url'].rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        use_http = _pick_http_lib()
        try:
            status, data = _post_once(use_http, url, headers, payload, _VISION_TIMEOUT)
        except Exception:
            continue
        if status != 200:
            continue
        try:
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        except Exception:
            content = None
        if not content:
            continue
        return {
            "ok": True,
            "result": {"summary": content.strip(), "vision_model": model},
            "provider": attempt_pid,
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    return {"ok": False, "error": "所有视觉预设均失败", "provider": "", "elapsed_ms": int((time.time() - started) * 1000)}