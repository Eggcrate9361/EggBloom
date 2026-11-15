# -*- coding: utf-8 -*-
"""autonomy.wire —— 接线与回滚（设计 §13/§15）。

接线是**最后一步**，只写 `_installed.json` 与插件清单；接线前失败，
前面的产物全部可删，本体无感知。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional


def _installed_path(data_dir) -> Path:
    return Path(data_dir) / "autonomy" / "_installed.json"


def read_installed(data_dir) -> list:
    try:
        p = _installed_path(data_dir)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def record_installed(data_dir, record: Dict[str, Any]) -> str:
    """追加一条 `_installed.json`；没有这条记录的东西视为不存在。"""
    p = _installed_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    items = read_installed(data_dir)
    row = dict(record or {})
    row.setdefault("installed_at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    row.setdefault("acquired_by", "ai")
    # 同一产物重复安装时更新原记录，不堆重复行（_installed 是账本，不是日志）
    key = (str(row.get("need") or ""), str(row.get("kind") or ""),
           str(row.get("target") or ""))
    items = [it for it in items
             if (str(it.get("need") or ""), str(it.get("kind") or ""),
                 str(it.get("target") or "")) != key]
    items.append(row)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, p)
    return str(p)


def install_plugin(plugin_dir, files: Dict[str, str]) -> Dict[str, Any]:
    base = Path(plugin_dir)
    try:
        for name, content in (files or {}).items():
            target = base / str(name)
            if base.resolve() not in target.resolve().parents and target.resolve() != base.resolve():
                return {"ok": False, "error": f"越界文件：{name}"}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
        return {"ok": True, "path": str(base)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def snapshot_rollback(data_dir, root, *,
                      names=("bot_config.json", "plugins/personality.txt",
                             "plugin_state.json")) -> str:
    """接线前快照配置/人格，失败时还原（设计 §15 第 3 条）。"""
    ts = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000:06d}"
    d = Path(data_dir) / "autonomy" / "rollback" / ts
    d.mkdir(parents=True, exist_ok=True)
    for name in names:
        src = Path(root) / name
        if src.exists():
            dst = d / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return str(d)


def restore_rollback(rollback_dir, root) -> Dict[str, Any]:
    d = Path(rollback_dir)
    if not d.exists():
        return {"ok": False, "error": "回滚目录不存在"}
    restored = []
    for p in d.rglob("*"):
        if p.is_file():
            rel = p.relative_to(d)
            # 拒绝跑出 root 的相对路径
            if ".." in rel.parts:
                continue
            target = Path(root) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            restored.append(str(target))
    return {"ok": True, "restored": restored}
