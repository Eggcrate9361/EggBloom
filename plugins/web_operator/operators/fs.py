# -*- coding: utf-8 -*-
import os, shutil, fnmatch
from pathlib import Path
from typing import Any, Dict


class FsOperator:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.cfg = config.get("fs", {})

    def _logs(self, *texts):
        return [{"level": "info", "text": t} for t in texts]

    def _ok(self, result, logs=None, meta=None):
        payload = {"ok": True, "result": result}
        if logs:
            payload["logs"] = logs
        if meta:
            payload["meta"] = meta
        return payload

    def _fail(self, error, logs=None):
        payload = {"ok": False, "error": error}
        if logs:
            payload["logs"] = logs
        return payload

    def execute(self, task: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        task_type = task.get("type", "")
        params = task.get("params", {}) or {}
        if task_type == "fs.read":
            return self.read(params)
        if task_type == "fs.write":
            return self.write(params)
        if task_type == "fs.append":
            return self.append(params)
        if task_type == "fs.list":
            return self.list_dir(params)
        if task_type == "fs.move":
            return self.move(params)
        if task_type == "fs.copy":
            return self.copy(params)
        if task_type == "fs.delete":
            return self.delete(params)
        if task_type == "fs.search":
            return self.search(params)
        return self._fail(f"unsupported fs task: {task_type}")

    def _resolve(self, raw: str) -> Path:
        p = Path(raw)
        if not p.is_absolute():
            p = Path.cwd() / p
        return p.resolve()

    def _allowed_path(self, path: Path) -> bool:
        blocked = [Path(x).resolve() for x in self.cfg.get("blocked_paths", [])]
        allowed = [Path(x).resolve() for x in self.cfg.get("allowed_paths", [])]
        try:
            for b in blocked:
                if path == b or b in path.parents:
                    return False
        except Exception:
            pass
        if allowed:
            return any(path == a or a in path.parents for a in allowed)
        return True

    def read(self, params):
        path = self._resolve(params.get("path", ""))
        if not self._allowed_path(path):
            return self._fail("path blocked")
        if not path.exists() or not path.is_file():
            return self._fail("file not found")
        size = path.stat().st_size
        limit = int(self.cfg.get("max_file_size_mb", 10) or 10) * 1024 * 1024
        if size > limit:
            return self._fail("file too large")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            return self._ok({"path": str(path), "size": size, "text": text[:20000]})
        except Exception as e:
            return self._fail(str(e))

    def write(self, params):
        path = self._resolve(params.get("path", ""))
        content = params.get("content", "")
        if not self._allowed_path(path):
            return self._fail("path blocked")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return self._ok({"path": str(path), "size": len(content.encode("utf-8"))})
        except Exception as e:
            return self._fail(str(e))

    def append(self, params):
        path = self._resolve(params.get("path", ""))
        content = params.get("content", "")
        if not self._allowed_path(path):
            return self._fail("path blocked")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(content)
            return self._ok({"path": str(path)})
        except Exception as e:
            return self._fail(str(e))

    def list_dir(self, params):
        path = self._resolve(params.get("path", "."))
        if not self._allowed_path(path):
            return self._fail("path blocked")
        if not path.exists() or not path.is_dir():
            return self._fail("directory not found")
        pattern = params.get("pattern") or "*"
        entries = []
        for item in sorted(path.iterdir()):
            name = item.name
            if fnmatch.fnmatch(name, pattern):
                entries.append({
                    "name": name,
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else None,
                    "path": str(item),
                })
        return self._ok({"path": str(path), "entries": entries[:500]})

    def move(self, params):
        src = self._resolve(params.get("src", ""))
        dst = self._resolve(params.get("dst", ""))
        if not self._allowed_path(src) or not self._allowed_path(dst):
            return self._fail("path blocked")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            return self._ok({"src": str(src), "dst": str(dst)})
        except Exception as e:
            return self._fail(str(e))

    def copy(self, params):
        src = self._resolve(params.get("src", ""))
        dst = self._resolve(params.get("dst", ""))
        if not self._allowed_path(src) or not self._allowed_path(dst):
            return self._fail("path blocked")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
            return self._ok({"src": str(src), "dst": str(dst)})
        except Exception as e:
            return self._fail(str(e))

    def delete(self, params):
        path = self._resolve(params.get("path", ""))
        if not self._allowed_path(path):
            return self._fail("path blocked")
        try:
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(str(path))
                else:
                    path.unlink()
            return self._ok({"path": str(path)})
        except Exception as e:
            return self._fail(str(e))

    def search(self, params):
        root = self._resolve(params.get("path", "."))
        keyword = params.get("keyword", "")
        if not self._allowed_path(root):
            return self._fail("path blocked")
        matches = []
        try:
            for dirpath, dirnames, filenames in os.walk(str(root)):
                base = Path(dirpath)
                if not self._allowed_path(base):
                    continue
                for name in filenames:
                    full = base / name
                    if keyword.lower() in name.lower():
                        matches.append(str(full))
                    if len(matches) >= 200:
                        break
                if len(matches) >= 200:
                    break
        except Exception:
            pass
        return self._ok({"path": str(root), "keyword": keyword, "matches": matches})
