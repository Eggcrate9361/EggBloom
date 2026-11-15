# -*- coding: utf-8 -*-
"""autonomy.plan —— 把缺口 + 环境事实变成可执行计划（设计 §12/§13）。

不做下载；只决定"装什么、装在哪、空间够不够"。
产物四类，路径互不混放：
  pip    -> <data>/autonomy/venv
  model  -> <data>/models/<family>/<name>
  bin    -> <app>/bin/<name>.exe（harness 的 ffmpeg 查找路径）
  plugin -> <root>/plugins/<id>
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def _free_gb(env: Dict[str, Any], path: str) -> float:
    try:
        return float((((env or {}).get("disk") or {}).get(path) or {}).get("free_gb") or 0.0)
    except Exception:
        return 0.0


def make_plan(gap, env: Dict[str, Any], *, data_dir, plugins_dir,
              package: str = "", kind: str = "", url: str = "",
              sha256: str = "", size_bytes: int = 0,
              family: str = "") -> Dict[str, Any]:
    need = str(getattr(gap, "need", "") or "").strip()
    k = str(kind or "").strip().lower()
    if not k:
        if package or "pip" in need.lower():
            k = "pip"
        elif need.lower() in ("tts", "asr", "ocr", "image", "imagegen"):
            k = "pip"
        elif need.lower() in ("ffmpeg", "ffprobe"):
            k = "pip"          # 默认装 imageio-ffmpeg（带二进制，主进程可 import）
        else:
            k = "pip"
    dd = Path(data_dir)
    artifacts: List[Dict[str, Any]] = []
    if k == "pip":
        # 不把能力名当包名（need=ocr 时 pip install ocr 是错的）
        _pkgs = {"ocr": "rapidocr_onnxruntime", "asr": "faster-whisper",
                 "tts": "edge-tts", "ffmpeg": "imageio-ffmpeg",
                 "ffprobe": "imageio-ffmpeg", "video": "imageio-ffmpeg"}
        name = package or _pkgs.get(need.lower(), need)
        artifacts.append({"kind": "pip", "name": name,
                          "target": str(dd / "autonomy" / "venv")})
    elif k == "model":
        fam = family or "misc"
        artifacts.append({"kind": "model", "name": need or "model",
                          "target": str(dd / "models" / fam / (need or "model")),
                          "url": url, "sha256": sha256,
                          "size_bytes": int(size_bytes or 0)})
    elif k == "bin":
        # harness/video_frames.find_ffmpeg 找的是 <app>/bin/ffmpeg.exe；
        # 不能装到 data/bin，否则装完照样"没 ffmpeg"。
        app_bin = Path(plugins_dir).resolve().parent / "bin"
        fname = need or "bin"
        if os.name == "nt" and not fname.lower().endswith(".exe"):
            fname += ".exe"
        artifacts.append({"kind": "bin", "name": need or "bin",
                          "target": str(app_bin / fname),
                          "url": url, "sha256": sha256,
                          "size_bytes": int(size_bytes or 0)})
    elif k == "plugin":
        artifacts.append({"kind": "plugin", "name": need or "plugin",
                          "target": str(Path(plugins_dir) / (need or "plugin"))})
    else:
        return {"ok": False, "error": f"未知产物类型：{k}", "artifacts": []}

    # 磁盘预检：只对已知大小的下载做硬判断（设计 §12 判据 3）
    need_bytes = int(size_bytes or 0)
    if need_bytes:
        free = _free_gb(env, str(dd))
        if free and need_bytes * 1.2 > free * (1024 ** 3):
            return {"ok": False, "error": f"需要 {need_bytes * 1.2 / 1024 ** 3:.1f}GB，"
                                          f"只剩 {free:.1f}GB", "artifacts": artifacts}

    steps = ["probe", "acquire", "verify", "wire"]
    return {"ok": True, "need": need, "kind": k, "artifacts": artifacts,
            "steps": steps, "reason": f"{getattr(gap, 'kind', '')}: {need}"}


def render(plan: Dict[str, Any]) -> str:
    if not plan or not plan.get("ok"):
        return "计划不可行：" + str((plan or {}).get("error") or "未知")
    lines = [f"计划：{plan.get('need')}（{plan.get('kind')}）"]
    for a in plan.get("artifacts") or []:
        lines.append(f"  - {a['kind']} → {a['target']}")
    return "\n".join(lines)
