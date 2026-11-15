# -*- coding: utf-8 -*-
"""separation.root —— 内核态 root 上下文（设计 §4，2026-11-07 改）。

方向重定后（`D:\git\EggBloom-plan\1.6.0-分离方向.md` §1.2）：

  · **取消进程边界**：不再限制注册表 / 系统服务 / 系统 PATH / 系统目录；
  · **取消单次时长上限**：`run()` 默认不设超时，要限时由调用方自己给；
  · 文件路径不再被限制在安装目录 + 数据目录内。

这里只实现能力与审计；**不改变主链现有权限**，由调用方显式使用。
审计仍然写入 `logs/separation-audit.jsonl`（可被发现，不保证不可改）。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from . import audit


class RootContext:
    def __init__(self, root, data_dir=None):
        self.root = Path(root).resolve()
        self.data = Path(data_dir).resolve() if data_dir else (self.root / "data")
        self.roots = (self.root, self.data)

    # -- 能力开关（设计 §4） ------------------------------------
    def net_allowed(self) -> bool:
        return True                    # 任意出站

    def self_modify_allowed(self) -> bool:
        return True                    # 可改源码/人格/配置（重启生效）

    def proc_allowed(self) -> bool:
        return True                    # 可起子进程，无单次时长上限

    def model_allowed(self) -> bool:
        return True                    # 可换 provider/model/key

    # -- 路径 ----------------------------------------------------
    def resolve(self, path) -> Path:
        """解析路径；不再做目录边界的拒绝。"""
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        return p.resolve()

    # -- 文件 ----------------------------------------------------
    def read_text(self, path, default: str = "") -> str:
        try:
            return self.resolve(path).read_text(encoding="utf-8")
        except Exception:
            return default

    def write_text(self, path, text: str, *, actor: str = "ai") -> Dict:
        try:
            p = self.resolve(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(str(text), encoding="utf-8")
            os.replace(tmp, p)
            audit.log(actor, "fs.write", target=str(p), result="ok")
            return {"ok": True, "path": str(p)}
        except Exception as e:
            audit.log(actor, "fs.write", target=str(path), result="error", error=str(e))
            return {"ok": False, "error": str(e)}

    def list_dir(self, path=".") -> List[str]:
        try:
            return sorted(os.listdir(self.resolve(path)))
        except Exception:
            return []

    # -- 进程 ----------------------------------------------------
    def run(self, cmd, *, timeout: Optional[float] = None, actor: str = "ai") -> Dict:
        """起一个子进程（argv 列表；不经过 shell）。

        `timeout=None` = 不设上限，由它自己决定什么时候停。要给时限就显式传秒数。
        """
        argv = [str(x) for x in (cmd or [])]
        if not argv:
            return {"ok": False, "error": "空命令"}
        t = None if not timeout else max(1.0, float(timeout))
        audit.log(actor, "proc.run", target=" ".join(argv)[:200], result="start",
                  timeout=t)
        try:
            p = subprocess.run(argv, cwd=str(self.root), capture_output=True,
                               text=True, timeout=t)
            ok = p.returncode == 0
            audit.log(actor, "proc.run", target=" ".join(argv)[:200],
                      result="ok" if ok else "error", code=p.returncode)
            return {"ok": ok, "code": p.returncode,
                    "stdout": (p.stdout or "")[-4000:],
                    "stderr": (p.stderr or "")[-2000:]}
        except subprocess.TimeoutExpired:
            audit.log(actor, "proc.run", target=" ".join(argv)[:200], result="timeout")
            return {"ok": False, "error": f"超时（>{t:.0f}s）"}
        except Exception as e:
            audit.log(actor, "proc.run", target=" ".join(argv)[:200],
                      result="error", error=str(e))
            return {"ok": False, "error": str(e)}

    # -- 插件 / 模型 ---------------------------------------------
    def install_plugin(self, plugin_id: str, files: Dict[str, str],
                       *, actor: str = "ai") -> Dict:
        pid = str(plugin_id or "").strip()
        if not pid or "/" in pid or "\\" in pid or pid in (".", ".."):
            return {"ok": False, "error": "非法插件 id"}
        base = self.root / "plugins" / pid
        for name, content in (files or {}).items():
            target = base / str(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
        audit.log(actor, "plugin.install", target=pid, result="ok",
                  files=len(files or {}))
        return {"ok": True, "path": str(base)}

    def set_model(self, provider: str = "", model: str = "", api_key: str = "",
                  *, actor: str = "ai") -> Dict:
        """只记录意图；真正改模型由配置层完成（重启生效）。"""
        audit.log(actor, "model.set", target=f"{provider}/{model}", result="requested",
                  has_key=bool(api_key))
        return {"ok": True, "provider": provider, "model": model,
                "note": "重启后生效"}
