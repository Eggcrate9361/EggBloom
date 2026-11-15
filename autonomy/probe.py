# -*- coding: utf-8 -*-
"""autonomy.probe —— 环境探测 EnvReport（设计 §12）。

探测**不做决策，只给事实**：OS / Python / 硬件 / 磁盘 / 网络 / 已装 / 缺口。
所有外部动作都可注入，默认实现只用 stdlib；测试不碰网络。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_NET_HOSTS = {"pypi": ("pypi.org", 443), "huggingface": ("huggingface.co", 443),
              "github": ("github.com", 443)}


def default_net_check(name: str) -> Dict[str, Any]:
    """TCP 443 轻探；3 秒内连上算 ok。不拉页面、不带凭据。"""
    host, port = _NET_HOSTS.get(str(name), ("", 0))
    if not host:
        return {"status": "unknown"}
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=3)
        s.close()
        return {"status": "ok", "elapsed": round(time.time() - t0, 2)}
    except Exception as e:
        return {"status": "timeout" if "timed out" in str(e).lower() else "error",
                "elapsed": round(time.time() - t0, 2), "error": str(e)[:80]}


def _python_info() -> Dict[str, Any]:
    return {
        "exe": str(sys.executable),
        "ver": platform.python_version(),
        "venv": str(sys.prefix) if sys.prefix != getattr(sys, "base_prefix", sys.prefix) else "",
        "pip_ok": shutil.which("pip") is not None,
    }


def _ram_gb() -> float:
    try:
        if os.name == "nt":
            import ctypes
            class _MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = _MEM()
            m.dwLength = ctypes.sizeof(_MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / (1024 ** 3), 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3), 1)
    except Exception:
        return 0.0


def _gpu_info(probe: Optional[Callable[[], list]] = None) -> List[Dict[str, Any]]:
    if probe is not None:
        try:
            return list(probe() or [])
        except Exception:
            return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4)
        rows = []
        for line in (out.stdout or "").splitlines():
            parts = [p.strip() for p in line.split(",")]
            if parts and parts[0]:
                rows.append({"name": parts[0],
                             "vram_gb": round(float(parts[1]) / 1024, 1) if len(parts) > 1 else 0.0})
        return rows
    except Exception:
        return []


def _disk_info(paths: Optional[list] = None) -> Dict[str, Any]:
    out = {}
    for p in (paths or [os.getcwd()]):
        try:
            u = shutil.disk_usage(str(p))
            out[str(p)] = {"free_gb": round(u.free / (1024 ** 3), 1)}
        except Exception:
            out[str(p)] = {"free_gb": 0.0}
    return out


def _installed(data_dir: Path) -> Dict[str, Any]:
    pip_pkgs = []
    try:
        import importlib.metadata as md
        for d in md.distributions():
            name = (d.metadata or {}).get("Name")
            if name:
                pip_pkgs.append(f"{name}=={d.version}")
    except Exception:
        pass
    models = []
    try:
        base = data_dir / "models"
        if base.exists():
            for p in base.iterdir():
                if p.is_dir():
                    models.append(p.name)
    except Exception:
        pass
    return {"pip": sorted(set(pip_pkgs))[:200], "models": models}


def env_report(*, data_dir=None, net_check: Optional[Callable[[str], Dict[str, Any]]] = None,
               disk_paths: Optional[list] = None,
               gpu_probe: Optional[Callable[[], list]] = None,
               missing_for: Optional[Dict[str, list]] = None) -> Dict[str, Any]:
    """产出一份结构化环境报告；不落盘（落盘用 write_report）。"""
    dd = Path(data_dir) if data_dir else Path(os.getcwd()) / "data"
    net = {}
    for url in ("pypi", "huggingface", "github"):
        if net_check is None:
            net_check = default_net_check
        try:
            net[url] = dict(net_check(url) or {})
        except Exception as e:
            net[url] = {"status": "error", "error": str(e)[:120]}
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "os": {"name": platform.system(), "ver": platform.version(),
               "arch": platform.machine()},
        "python": _python_info(),
        "hw": {"cpu": platform.processor() or platform.machine(),
               "cores": os.cpu_count() or 0,
               "ram_gb": _ram_gb(),
               "gpu": _gpu_info(gpu_probe),
               "cuda": ""},
        "disk": _disk_info(disk_paths),
        "net": net,
        "installed": _installed(dd),
        "missing_for": dict(missing_for or {}),
    }


def write_report(report: Dict[str, Any], data_dir=None) -> str:
    dd = Path(data_dir) if data_dir else Path(os.getcwd()) / "data"
    p = dd / "autonomy" / f"env-{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(p)
