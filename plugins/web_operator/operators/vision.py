# -*- coding: utf-8 -*-
"""
Vision Assist - 视觉辅助模块
=============================
将网页截图发给 VLM（默认智谱 GLM-4.6V-Flash，失败自动降级到 GLM-4V-Flash），
返回文字描述与建议操作。供纯文本主模型（如 DeepSeek）在网页任务中"看懂"页面。

统一入口：
    analyze_image(image_ref, prompt, config)
      image_ref: 本地路径 或 http(s) URL 或 data URI
      config:    完整插件配置（含 vision_assist 字段）

API Key 解析优先级（不会把真实 key 写进 git）：
    1. 环境变量 ZHIPU_API_KEY
    2. Windows 凭据管理器（cred_store.py，DPAPI，条目名 zhipu）
    3. config 中 vision_assist.api_key（仅当非占位/非空）
    （真实 key 永不打印，日志仅显示是否存在）
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

PLUGIN_DATA_DIR = Path(__file__).resolve().parents[1] / "data"

PLACEHOLDER_KEYS = {
    "YOUR_ZHIPU_API_KEY_HERE",
    "your_zhipu_api_key_here",
    "YOUR_GLM_API_KEY_HERE",
    "",
}


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
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
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


def _resolve_api_key(va: dict) -> str:
    """安全地获取 API Key：环境变量 > Windows 凭据管理器 > 插件配置。绝不把真实 key 写入日志。"""
    key = (os.environ.get("ZHIPU_API_KEY") or "").strip()
    if key:
        return key
    # 凭据管理器（cred_store.py，DPAPI）——不再扫描 secret.json 明文文件
    try:
        import sys
        from pathlib import Path as _Path
        _p = _Path(__file__).resolve()
        for _parent in _p.parents:
            if (_parent / "cred_store.py").exists():
                if str(_parent) not in sys.path:
                    sys.path.insert(0, str(_parent))
                break
        from cred_store import read_cred
        cred = (read_cred("zhipu") or "").strip()
        if cred and cred not in PLACEHOLDER_KEYS and "YOUR_" not in cred:
            return cred
    except Exception:
        pass
    cfg_key = (va.get("api_key") or "").strip()
    if cfg_key and cfg_key not in PLACEHOLDER_KEYS and "YOUR_" not in cfg_key:
        return cfg_key
    return ""


def _model_chain(model: str) -> list:
    """主模型 -> 备用模型 降级链，可用环境变量 ZHIPU_MODELS 覆盖"""
    raw = os.environ.get("ZHIPU_MODELS") or ""
    models = [m.strip() for m in raw.split(",") if m.strip()] if raw.strip() else []
    base = model or "glm-4.6v-flash"
    chain = [base]
    for alt in ("glm-4v-flash", "glm-4v"):
        if alt not in chain:
            chain.append(alt)
    seen = []
    for m in chain:
        if m and m not in seen:
            seen.append(m)
    return seen


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
        try:
            data = json.loads(r.read().decode("utf-8"))
        except Exception:
            data = {"raw": r.read(1000).decode("utf-8", "ignore")}
        return r.status, data


def analyze_image(image_ref: str, prompt: str, config: dict = None) -> dict:
    """
    分析一张截图/图片，返回 VLM 文本描述。失败自动降级备用视觉模型。

    :param image_ref: 本地路径 / http(s) URL / data URI
    :param prompt:    给模型的指令（如"描述页面当前状态"）
    :param config:    完整插件配置；缺省时从 env / secret 文件兜底
    :return: {ok, result|error, logs, meta}
    """
    cfg = config or {}
    va = dict(cfg.get("vision_assist") or {})
    base_url = (va.get("base_url") or os.environ.get("ZHIPU_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    primary_model = (va.get("model") or "glm-4.6v-flash").strip()
    api_key = _resolve_api_key(va)
    timeout = float(os.environ.get("ZHIPU_TIMEOUT") or va.get("timeout", 30) or 30)
    retry_wait = float(os.environ.get("ZHIPU_RETRY_DELAY", "2") or 2)
    thinking = (va.get("thinking", True) or bool(os.getenv("ZHIPU_THINKING")))
    logs = []
    models = _model_chain(primary_model)
    has_key = bool(api_key)
    logs.append({"level": "debug", "text": f"[vision] key={'configured' if has_key else 'MISSING'}, models={models}"})

    if not api_key:
        logs.append({"level": "warn", "text": "[vision] 未配置 ZHIPU_API_KEY，跳过视觉分析"})
        return {
            "ok": False,
            "error": "ZHIPU_API_KEY 未配置（环境变量 / 配置 / data/secret.json）",
            "logs": logs,
            "meta": {"vision_used": False, "reason": "no_api_key"},
        }

    try:
        image_url = _normalize_image(image_ref)
    except Exception as e:
        return {"ok": False, "error": f"image 解析失败: {e}", "logs": logs, "meta": {"vision_used": False}}

    if image_url.startswith("data:") and len(image_url) > 6 * 1024 * 1024:
        logs.append({"level": "warn", "text": "[vision] 图片 base64 过大，可能被模型拒绝"})

    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    use_http = _pick_http_lib()
    started = time.time()
    last_err = None

    for model in models:
        for attempt in range(2):  # 单模型快速失败：最多 2 次，之后立即切换
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
            if thinking:
                payload["thinking"] = {"type": "enabled"}
            try:
                status, data = _post_once(use_http, url, headers, payload, timeout)
            except urllib.error.HTTPError as e:
                raw = (e.read(500).decode("utf-8", "ignore") if hasattr(e, "read") else "") or str(e)
                last_err = f"{model}: HTTP {e.code}"
                logs.append({"level": "error", "text": f"[vision] {model} 失败 {last_err} (1305=峰值繁忙)"})
                if "1305" in raw and e.code == 429:
                    break  # 模型繁忙：直接切换下一个模型
                if e.code in (429, 500, 502, 503, 504) and attempt < 1:
                    time.sleep(retry_wait)
                    continue
                if e.code in (401, 403):
                    logs.append({"level": "error", "text": f"[vision] 鉴权失败 HTTP {e.code}，请检查 ZHIPU_API_KEY"})
                    return {
                        "ok": False,
                        "error": f"{model}: 鉴权失败 HTTP {e.code}",
                        "logs": logs,
                        "meta": {"vision_used": True, "elapsed_ms": int((time.time() - started) * 1000)},
                    }
                last_err = f"{model}: HTTP {e.code}: {raw[:200]}"
                break
            except Exception as e:
                last_err = f"{model}: 网络错误 {e}"
                logs.append({"level": "error", "text": f"[vision] {model} 请求异常: {e}"})
                if attempt < 1:
                    time.sleep(retry_wait)
                    continue
                break
            if status != 200:
                msg = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else (data.get("raw") or data)
                last_err = f"{model}: HTTP {status} {str(msg)[:200]}"
                logs.append({"level": "error", "text": f"[vision] {model} HTTP {status}"})
                if "1305" in str(msg) or "峰值" in str(msg):
                    break  # 峰值繁忙，切下一个模型
                if status in (429, 500, 502, 503, 504):
                    if attempt < 1:
                        time.sleep(retry_wait)
                        continue
                    continue
                break
            # 成功
            try:
                content = data["choices"][0]["message"]["content"]
                if isinstance(content, list):
                    content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
            except Exception:
                content = data.get("raw") or str(data)[:2000]
            elapsed_ms = int((time.time() - started) * 1000)
            if not content:
                continue
            logs.append({"level": "success", "text": f"[vision] {model} 分析完成，{elapsed_ms}ms"})
            return {
                "ok": True,
                "result": {
                    "summary": content,
                    "vision_model": model,
                    "elapsed_ms": elapsed_ms,
                },
                "logs": logs,
                "meta": {"vision_used": True, "model": model, "elapsed_ms": elapsed_ms, "used_primary": model == models[0]},
            }

    elapsed_ms = int((time.time() - started) * 1000)
    logs.append({"level": "error", "text": f"[vision] 全部模型失败: {last_err}"})
    return {
        "ok": False,
        "error": f"VLM 全部模型失败: {last_err}",
        "logs": logs,
        "meta": {"vision_used": True, "elapsed_ms": elapsed_ms},
    }


# 简化的执行入口，供 execute_task 的 vision.analyze 调用
def execute(params, config=None):
    """execute_task 兼容签名"""
    image_ref = (params or {}).get("image") or (params or {}).get("image_path")
    prompt = (params or {}).get("prompt") or "请描述这张网页截图的当前状态，并建议下一个操作。"
    if not image_ref:
        return {"ok": False, "error": "missing image", "logs": [{"level": "error", "text": "缺少 image 参数"}]}
    return analyze_image(image_ref, prompt, config or {})


# ---------------------------------------------------------------------------
# 视觉定位（bbox grounding）：给元素描述，返回屏幕坐标，供点击类兜底
# ---------------------------------------------------------------------------
import re as _re


def _parse_first_point(text: str):
    """从模型回复中抽取第一个 {"x":..,"y":..} 或 [x,y] 或 x,y 坐标。"""
    if not text:
        return None
    # 1) {"x": 12, "y": 34}
    m = _re.search(r'"?x"?\s*[:=]\s*(\d+)\s*,\s*"?y"?\s*[:=]\s*(\d+)', text, _re.IGNORECASE)
    if m:
        return {"x": int(m.group(1)), "y": int(m.group(2))}
    # 2) [x, y]
    m = _re.search(r'\[\s*(\d+)\s*,\s*(\d+)\s*\]', text)
    if m:
        return {"x": int(m.group(1)), "y": int(m.group(2))}
    # 3) 两个独立数字兜底
    nums = _re.findall(r'\d+', text)
    if len(nums) >= 2:
        return {"x": int(nums[0]), "y": int(nums[1])}
    return None


def ground_element(image_ref: str, description: str, config: dict = None) -> dict:
    """
    让 VLM 在截图中定位 description 所指元素，返回中心坐标。
    :return: {ok, point:{x,y} | None, error?, meta}
    """
    cfg = config or {}
    va = cfg.get("vision_assist") or {}
    if not va.get("enabled", False):
        return {"ok": False, "error": "vision disabled", "point": None}
    prompt = (
        "你是网页视觉定位助手。下面是一张网页截图。请找到与该描述最匹配的那个可点击元素，"
        "返回它的中心点像素坐标。只输出 JSON，不要解释："
        '{"x": <整数像素>, "y": <整数像素>}\n'
        f"目标描述：{description}"
    )
    res = analyze_image(image_ref, prompt, cfg)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "point": None, "logs": res.get("logs")}
    summary = ((res.get("result") or {}).get("summary")) or ""
    point = _parse_first_point(summary)
    if not point:
        return {"ok": False, "error": "无法从视觉回复解析坐标", "point": None,
                "logs": (res.get("logs") or []) + [{"level": "warn", "text": f"[ground] 未解析到坐标: {summary[:80]}"}]}
    return {"ok": True, "point": point, "logs": res.get("logs"),
            "meta": {"model": (res.get("result") or {}).get("vision_model")}}