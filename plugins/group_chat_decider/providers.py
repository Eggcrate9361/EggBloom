# -*- coding: utf-8 -*-
"""
group_chat_decider providers.py
===============================
统一调用层（薄封装）——文本决策 + 视觉描述，**全部直连免费车道**。

2026-10-12 用户定调：「决策取消智谱管线，换 dsh-our-free-model」，
且明确**不要经过 dsh 路由**。因此这里不再把第三方免费云（智谱/阿里/硅基/本地）
翻译成主层 provider 适配器，而是直接调根目录 `free_llm.py` ——
它直连 dsh-our-free-model 使用的两条免密车道（zen / kilo）：免 key、免登录、
不经任何本地路由进程。

对外 API 签名保持不变（`__init__.py` / `vision.py` / UI 都不用改）：
  - call_text(prompt, max_tokens=400, provider_id=None) -> str
  - call_vision(image_ref, prompt, provider_id=None) -> {ok, result|error, provider, elapsed_ms}

失败语义也与旧版一致：任何异常/不可用都**返回空串 / {ok:False}**，不抛给调用方
（决策链自己还有 fail-open 与断路器，见 __init__.py 的"降级治理"段）。
"""
import base64
import os
import sys
import time
from pathlib import Path

from . import free_models

PLUGIN_DATA_DIR = free_models.DATA_DIR

_TEXT_TIMEOUT = 45
_VISION_TIMEOUT = 120


def _free_llm():
    """惰性取根目录的 free_llm（唯一 HTTP 实现）。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    import free_llm
    return free_llm


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


def _model_chain(pid: str, kind: str = "text") -> list:
    """档位自带的模型打头，再补车道默认链（同一条链内降级）。

    档位里的写形是 `车道:模型`（如 zen:space-bunny-free）；免费车道整体不可用时
    返回空链 → 调用方返回空串/失败，由决策的降级治理接手（fail-open → 断路器）。
    """
    try:
        p = free_models.get_provider(pid)
    except Exception:
        p = {}
    head = (p.get(f"{kind}_model") or "").strip()
    try:
        fl = _free_llm()
        rest = list(fl.vision_models() if kind == "vision" else fl.text_models())
    except Exception:
        fl, rest = None, []
    if fl is None:
        return []
    chain = []
    if head:
        chain.append(fl._parse_spec(head))
    for item in rest:
        if item not in chain:
            chain.append(item)
    return chain


def call_text(prompt: str, max_tokens: int = 400, provider_id: str = None) -> str:
    """文本决策调用。返回最终文本（失败返回空串，绝不抛）。"""
    pid = provider_id or free_models.get_active_model()
    try:
        fl = _free_llm()
    except Exception:
        return ""
    chain = _model_chain(pid, "text")
    if not chain:
        return ""
    try:
        r = fl.chat([{"role": "user", "content": prompt}], models=chain,
                    max_tokens=max_tokens, timeout=_TEXT_TIMEOUT)
    except Exception:
        return ""
    return (r.get("text") or "").strip() if r.get("ok") else ""


def call_vision(image_ref: str, prompt: str, provider_id: str = None) -> dict:
    """视觉描述调用。返回 {ok, result|error, provider, elapsed_ms}"""
    pid = provider_id or free_models.get_active_model()
    try:
        _normalize_image(image_ref)      # 只做校验；坏路径要在这里就返回
    except Exception as e:
        return {"ok": False, "error": f"image 解析失败: {e}", "provider": "", "elapsed_ms": 0}

    started = time.time()
    try:
        fl = _free_llm()
    except Exception as e:
        return {"ok": False, "error": f"free_llm 加载失败: {e}", "provider": pid,
                "elapsed_ms": int((time.time() - started) * 1000)}
    chain = _model_chain(pid, "vision")
    if not chain:
        return {"ok": False, "error": "免费车道不可用", "provider": pid,
                "elapsed_ms": int((time.time() - started) * 1000)}
    try:
        r = fl.chat_vision(image_ref, prompt, models=chain,
                           max_tokens=400, timeout=_VISION_TIMEOUT)
    except Exception as e:
        return {"ok": False, "error": f"调用异常 {type(e).__name__}: {e}", "provider": pid,
                "elapsed_ms": int((time.time() - started) * 1000)}
    if r.get("ok") and r.get("summary"):
        return {
            "ok": True,
            "result": {"summary": r["summary"], "vision_model": r.get("model", "")},
            "provider": pid,
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    return {"ok": False, "error": r.get("error") or "视觉模型未返回内容", "provider": pid,
            "elapsed_ms": int((time.time() - started) * 1000)}
