# -*- coding: utf-8 -*-
"""autonomy.acquire —— 下载 / 安装（设计 §11.2/§13）。

下载契约：
  · 落盘 `<dest>.part`，每 4MB flush，另写 `<dest>.progress`；
  · 重启后读 progress，带 `Range: bytes=<n>-` 续传；
  · 服务端不支持 Range（回 200）→ 从头；
  · 完成后校验 sha256，不符删掉重下；
  · 下载前算 `total*1.2 > free` → 拒绝。
网络与安装都可注入，测试不碰真网。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

CHUNK = 4 * 1024 * 1024


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _free_bytes(path) -> int:
    try:
        return int(shutil.disk_usage(str(path)).free)
    except Exception:
        return 0


def _read_progress(part: Path) -> Dict[str, Any]:
    try:
        p = Path(str(part) + ".progress")
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_progress(part: Path, data: Dict[str, Any]) -> None:
    try:
        p = Path(str(part) + ".progress")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def download_resume(url: str, dest, *, http_get: Callable, total_hint: int = 0,
                    expected_sha256: str = "", chunk: int = CHUNK,
                    max_bytes: int = 0) -> Dict[str, Any]:
    """可断点续传的下载。`http_get(url, headers) -> (status, bytes)`。"""
    d = Path(dest)
    d.parent.mkdir(parents=True, exist_ok=True)
    part = Path(str(d) + ".part")
    have = part.stat().st_size if part.exists() else 0
    prog = _read_progress(part)
    total = int(total_hint or prog.get("total") or 0)

    if total and _free_bytes(d.parent) and total * 1.2 > _free_bytes(d.parent):
        return {"ok": False, "error": f"需要 {total * 1.2 / 1024 ** 3:.1f}GB，"
                                      f"只剩 {_free_bytes(d.parent) / 1024 ** 3:.1f}GB"}

    headers = {"Range": f"bytes={have}-"} if have else {}
    try:
        status, body = http_get(url, headers)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if status == 416 and have:
        if expected_sha256 and _sha256(part) != expected_sha256:
            part.unlink(missing_ok=True)
            return {"ok": False, "error": "已下载完成但 sha256 不符，已删除"}
        os.replace(part, d)
        return {"ok": True, "path": str(d), "bytes": have, "resumed": True}

    mode = "ab" if (have and status == 206) else "wb"
    if mode == "wb":
        have = 0
    written = have
    with open(part, mode) as f:
        # 调用方可以返回完整 bytes；大文件可返回可迭代分块，这里两者都吃
        if isinstance(body, (bytes, bytearray)):
            chunks = [bytes(body)]
        else:
            chunks = body or []
        for c in chunks:
            if not c:
                continue
            f.write(c)
            written += len(c)
            f.flush()
            _write_progress(part, {"bytes": written, "total": total, "url": url})
            if max_bytes and written > max_bytes:
                return {"ok": False, "error": f"超过 max_bytes={max_bytes}"}
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    _write_progress(part, {"bytes": written, "total": total or written, "url": url})
    if total and written < total:
        return {"ok": False, "partial": str(part), "bytes": written,
                "error": f"下载不完整：{written}/{total}（已保留 .part，可续传）"}
    if expected_sha256:
        got = _sha256(part)
        if got != expected_sha256:
            part.unlink(missing_ok=True)
            try:
                Path(str(part) + ".progress").unlink(missing_ok=True)
            except Exception:
                pass
            return {"ok": False, "error": f"sha256 不符：{got[:12]}…"}
    os.replace(part, d)
    try:
        Path(str(part) + ".progress").unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True, "path": str(d), "bytes": written,
            "resumed": bool(have), "sha256": _sha256(d)}


def make_venv(python_exe: str, venv_dir) -> Dict[str, Any]:
    """建独立 venv；绝不污染 EggBloom 自带解释器（设计 §12 判据 2）。"""
    vd = Path(venv_dir)
    vd.parent.mkdir(parents=True, exist_ok=True)
    try:
        p = subprocess.run([python_exe, "-m", "venv", str(vd)],
                           capture_output=True, text=True, timeout=600)
        return {"ok": p.returncode == 0, "path": str(vd),
                "error": "" if p.returncode == 0 else (p.stderr or "")[-800:]}
    except Exception as e:
        return {"ok": False, "path": str(vd), "error": str(e)}


def pip_install(venv_dir, package: str, *, timeout: int = 1800) -> Dict[str, Any]:
    """只在 venv 里装；失败返回 stderr 尾部。"""
    vd = Path(venv_dir)
    py = vd / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        return {"ok": False, "error": f"venv 解释器不存在：{py}"}
    try:
        p = subprocess.run([str(py), "-m", "pip", "install", "--no-input", package],
                           capture_output=True, text=True, timeout=timeout)
        return {"ok": p.returncode == 0, "code": p.returncode,
                "stdout": (p.stdout or "")[-2000:],
                "stderr": (p.stderr or "")[-2000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"pip 超时（>{timeout}s）"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
