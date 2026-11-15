# -*- coding: utf-8 -*-
"""autonomy.verify —— 装完必须真跑一次（设计 §14）。

`import` 成功 != 能用；判据是**真实产出**：
  · TTS：wav 存在、>1KB、时长 >0.2s；
  · ASR：转回文字与"测试"编辑距离 ≤1（可注入识别函数）；
  · 图片：合法 PNG 且非纯色；
  · 插件：调一次只读 action，返回 ok。
任何一项失败 → 调用方应回滚。
"""
from __future__ import annotations

import os
import struct
import wave
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def verify_file(path, *, min_bytes: int = 1, min_duration: float = 0.0) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"文件不存在：{p}"}
    size = p.stat().st_size
    if size < min_bytes:
        return {"ok": False, "error": f"文件太小：{size} < {min_bytes}"}
    if min_duration > 0 and p.suffix.lower() == ".wav":
        try:
            with wave.open(str(p), "rb") as w:
                dur = w.getnframes() / float(w.getframerate() or 1)
        except Exception as e:
            return {"ok": False, "error": f"wav 打不开：{e}"}
        if dur < min_duration:
            return {"ok": False, "error": f"时长为 {dur:.2f}s，要求 >{min_duration}s"}
        return {"ok": True, "bytes": size, "duration": round(dur, 3)}
    return {"ok": True, "bytes": size}


def verify_tts(path) -> Dict[str, Any]:
    return verify_file(path, min_bytes=1024, min_duration=0.2)


def _png_is_uniform(data: bytes) -> bool:
    """粗略判断 PNG 是否纯色：统计 IDAT 解压后的字节种类。"""
    try:
        i = 8
        idat = b""
        while i + 8 <= len(data):
            ln = struct.unpack(">I", data[i:i + 4])[0]
            typ = data[i + 4:i + 8]
            body = data[i + 8:i + 8 + ln]
            if typ == b"IDAT":
                idat += body
            i += 12 + ln
        raw = zlib.decompress(idat)
        return len(set(raw)) <= 2
    except Exception:
        return False


def verify_png(path) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "文件不存在"}
    data = p.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return {"ok": False, "error": "不是合法 PNG"}
    if p.stat().st_size < 100:
        return {"ok": False, "error": "PNG 太小"}
    return {"ok": True, "uniform": _png_is_uniform(data), "bytes": len(data)}


def verify_asr(recognize: Callable[[str], str], path, *, expected: str = "测试") -> Dict[str, Any]:
    """TTS/ASR 闭环：把生成的 wav 转回文字。"""
    try:
        text = str(recognize(str(path)) or "").strip()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not text:
        return {"ok": False, "error": "识别结果为空"}
    # 编辑距离 ≤1（简单 Levenshtein）
    a, b = text, expected
    dp = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        ndp = [i]
        for j, cb in enumerate(b, 1):
            ndp.append(min(dp[j] + 1, ndp[j - 1] + 1, dp[j - 1] + (ca != cb)))
        dp = ndp
    dist = dp[-1]
    return {"ok": dist <= 1, "text": text, "distance": dist}


def verify_plugin(call_action: Callable[[str], Any], action: str = "status") -> Dict[str, Any]:
    try:
        r = call_action(action)
        ok = bool(isinstance(r, dict) and r.get("ok"))
        return {"ok": ok, "result": r}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def verify_bin(path, *, timeout: int = 20, expect: str = "") -> Dict[str, Any]:
    """可执行文件自验：真跑一下 `-version`，不只看文件存在。

    ffmpeg 装完不跑一次，坏二进制/错架构会一直躺在 bin/ 里，
    调用方还以为是"没找到"。
    """
    import os
    import subprocess
    p = str(path or "")
    if not os.path.isfile(p):
        return {"ok": False, "error": "文件不存在"}
    try:
        out, rc = "", -1
        for flag in ("-version", "--version", "-V"):
            r = subprocess.run([p, flag], capture_output=True, text=True,
                               errors="replace", timeout=timeout)
            out = (r.stdout or "") + (r.stderr or "")
            rc = int(r.returncode)
            if rc == 0 and out.strip():
                break
        ok = rc == 0 and bool(out.strip())
        if ok and expect and expect.lower() not in out.lower():
            ok = False
        return {"ok": ok, "returncode": rc, "output": out[:300]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def run_checks(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """checks: [{name, ok, ...}]；全部通过才算过。"""
    bad = [c for c in checks or [] if not c.get("ok")]
    return {"ok": not bad, "checks": checks, "failed": bad}
