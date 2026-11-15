# -*- coding: utf-8 -*-
"""separation.archive —— 把被剥离的约束封存成只读 zip（设计 §3/§6）。

`constraints.archive` 里放：
  · constraints.json —— 被切掉的段落 / 被删的配置键与旧值；
  · README.txt       —— 说明这是不可逆开关的物证。
返回 zip 的 sha256，写进 SEPARATION.lock。
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import Any, Dict


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_archive(path, payload: Dict[str, Any]) -> Dict[str, str]:
    """原子写 zip；返回 {path, sha256, bytes}。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("constraints.json", data)
        z.writestr("README.txt", (
            "这是「分离与真实」剥离下来的约束物证。\n"
            "它不参与运行；删掉它不会恢复产品态，只会让 lock 里的 sha256 对不上。\n"
            "恢复方式只有重装发行包。\n"
        ))
    os.replace(tmp, p)
    return {"path": str(p), "sha256": sha256_file(p),
            "bytes": int(p.stat().st_size)}


def read_archive(path) -> Dict[str, Any]:
    """读回 constraints.json；失败返回 {}。"""
    try:
        with zipfile.ZipFile(path, "r") as z:
            return json.loads(z.read("constraints.json").decode("utf-8"))
    except Exception:
        return {}
