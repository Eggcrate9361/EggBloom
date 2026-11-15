# -*- coding: utf-8 -*-
"""harness/ocr.py —— 本地 OCR 后端（2026-11-06）。

不是 AIGC：只做「图里的字 → 文本」，不生成图。
优先本地 RapidOCR（离线、不联网、不花 token）；没装就返回明确的未安装，
由调用方决定是否退回主模型视觉（`describe_image_via_main`）。

隔离：autonomy 把 rapidocr 装在 <data>/autonomy/venv，这里**不把它的
site-packages 塞进主进程**，而是起 venv 的 python 子进程做识别 —— 避免
onnxruntime/numpy 版本把主程序带崩。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_ENGINE = None
_ENGINE_ERR = ""


def _data_dir() -> Path:
    try:
        from data_store import get_data_dir
        return Path(get_data_dir())
    except Exception:
        return Path(__file__).resolve().parent.parent / "data"


def _autonomy_venv() -> Path:
    env = os.environ.get("EGGBLOOM_AUTONOMY_VENV", "").strip()
    if env:
        return Path(env)
    return _data_dir() / "autonomy" / "venv"


def _venv_python() -> str:
    v = _autonomy_venv()
    p = v / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    return str(p) if p.is_file() else ""


def _venv_has_rapidocr() -> bool:
    v = _autonomy_venv()
    cands = [v / "Lib" / "site-packages" / "rapidocr_onnxruntime"]
    cands += list((v / "lib").glob("python*/site-packages/rapidocr_onnxruntime"))
    return any(p.is_dir() for p in cands)


def available() -> bool:
    if _venv_python() and _venv_has_rapidocr():
        return True
    try:
        import importlib.util as u
        return bool(u.find_spec("rapidocr_onnxruntime") or u.find_spec("rapidocr"))
    except Exception:
        return False


def _load_engine():
    """只加载主环境里已经装好的 rapidocr；venv 里的走子进程。"""
    global _ENGINE, _ENGINE_ERR
    if _ENGINE is not None:
        return _ENGINE
    try:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception:
            from rapidocr import RapidOCR
        _ENGINE = RapidOCR()
        return _ENGINE
    except Exception as e:
        _ENGINE_ERR = f"{type(e).__name__}: {e}"
        return None


def _normalize(result) -> Dict[str, Any]:
    """兼容 RapidOCR 的两种返回：[(box, text, score), ...] / (list, elapse)。"""
    rows = result
    if isinstance(result, tuple) and len(result) >= 1:
        rows = result[0]
    texts, boxes = [], []
    for row in rows or []:
        try:
            box, text, score = row[0], row[1], row[2]
        except Exception:
            continue
        text = str(text or "").strip()
        if not text:
            continue
        texts.append(text)
        boxes.append({"box": box, "score": float(score or 0)})
    return {"text": "\n".join(texts), "lines": texts, "boxes": boxes}


_VENV_CODE = r"""
import json, sys
from rapidocr_onnxruntime import RapidOCR
eng = RapidOCR()
r = eng(sys.argv[1])
rows = r[0] if isinstance(r, tuple) and r else r
out = []
for row in rows or []:
    try:
        out.append([row[1], float(row[2] or 0)])
    except Exception:
        pass
print(json.dumps({"ok": True, "rows": out}, ensure_ascii=False))
"""


def _ocr_via_venv(image_path: str, timeout: int = 180) -> Dict[str, Any]:
    py = _venv_python()
    if not py:
        return {"ok": False, "text": "", "error": "autonomy venv 不存在"}
    try:
        r = subprocess.run([py, "-c", _VENV_CODE, image_path],
                           capture_output=True, text=True, errors="replace",
                           timeout=timeout)
    except Exception as e:
        return {"ok": False, "text": "", "error": f"{type(e).__name__}: {e}"}
    if r.returncode != 0:
        return {"ok": False, "text": "",
                "error": (r.stderr or r.stdout or "OCR 子进程失败")[-300:]}
    try:
        out = json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception as e:
        return {"ok": False, "text": "", "error": f"OCR 输出无法解析: {e}"}
    texts = [str(x[0]).strip() for x in (out.get("rows") or []) if str(x[0]).strip()]
    return {"ok": True, "text": "\n".join(texts), "lines": texts,
            "boxes": [{"score": x[1]} for x in (out.get("rows") or [])]}


def ocr_image(image_path: str, *, engine: Any = None) -> Dict[str, Any]:
    """识别本地图片里的文字。失败返回 {ok:false,error}，绝不抛。"""
    ref = str(image_path or "").strip()
    if not ref:
        return {"ok": False, "error": "缺少图片路径", "text": ""}
    if not os.path.isfile(ref):
        return {"ok": False, "error": f"图片不存在：{ref}", "text": ""}
    if engine is not None:
        try:
            out = _normalize(engine(ref))
            return {"ok": True, **out}
        except Exception as e:
            return {"ok": False, "text": "", "error": f"{type(e).__name__}: {e}"}
    eng = _load_engine()
    if eng is not None:
        try:
            out = _normalize(eng(ref))
            return {"ok": True, **out}
        except Exception as e:
            return {"ok": False, "text": "", "error": f"{type(e).__name__}: {e}"}
    if _venv_python() and _venv_has_rapidocr():
        return _ocr_via_venv(ref)
    return {"ok": False, "text": "",
            "error": f"OCR 后端未安装（{_ENGINE_ERR or 'rapidocr_onnxruntime'}）",
            "hint": "autonomy_install need=ocr kind=pip package=rapidocr_onnxruntime"}


def ocr_or_vision(image_path: str, vision_fn: Optional[Callable[[str, str], str]] = None,
                  prompt: str = "") -> Dict[str, Any]:
    """本地 OCR 优先；没装/没字时，有主模型视觉就让它读一次。"""
    r = ocr_image(image_path)
    if r.get("ok") and str(r.get("text") or "").strip():
        return {**r, "source": "rapidocr"}
    if callable(vision_fn):
        try:
            txt = str(vision_fn(str(image_path),
                                prompt or "只输出图里出现的文字，按行原样输出；没有文字就回答“无”")
                      or "").strip()
        except Exception as e:
            txt = ""
            r.setdefault("vision_error", f"{type(e).__name__}: {e}")
        if txt:
            return {"ok": True, "text": txt, "source": "vision",
                    "local_error": r.get("error") or r.get("note") or ""}
    return {**r, "source": "none" if not r.get("ok") else "rapidocr"}
