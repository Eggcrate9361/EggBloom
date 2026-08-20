# -*- coding: utf-8 -*-
import os, socket, platform
from pathlib import Path
from typing import Any, Dict


class SystemOperator:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

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
        if task_type == "system.info":
            return self.info()
        if task_type == "system.port_check":
            return self.port_check(params)
        if task_type == "system.processes":
            return self.processes()
        if task_type == "system.open":
            return self.open(params)
        return self._fail(f"unsupported system task: {task_type}")

    def info(self):
        try:
            import psutil
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage(str(Path.home()))
            result = {
                "os": platform.platform(),
                "python": platform.python_version(),
                "cpu_count": psutil.cpu_count(),
                "memory": {
                    "total": mem.total,
                    "available": mem.available,
                    "percent": mem.percent,
                },
                "disk": {
                    "total": disk.total,
                    "free": disk.free,
                    "percent": disk.percent,
                },
            }
        except Exception as e:
            result = {"os": platform.platform(), "error": str(e)}
        return self._ok(result)

    def port_check(self, params):
        ports = params.get("ports") or []
        if not ports:
            return self._fail("missing ports")
        result = {}
        for port in ports:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.3)
            try:
                result[str(port)] = s.connect_ex(("127.0.0.1", int(port))) == 0
            except Exception:
                result[str(port)] = False
            finally:
                try:
                    s.close()
                except Exception:
                    pass
        return self._ok({"ports": result})

    def processes(self):
        try:
            import psutil
            procs = []
            for p in psutil.process_iter(["pid", "name", "status"]):
                try:
                    procs.append(p.info)
                except Exception:
                    pass
                if len(procs) >= 200:
                    break
            return self._ok({"processes": procs})
        except Exception as e:
            return self._fail(str(e))

    def open(self, params):
        target = params.get("path") or params.get("url") or ""
        if not target:
            return self._fail("missing path/url")
        try:
            os.startfile(target)
            return self._ok({"opened": target})
        except Exception as e:
            return self._fail(str(e))
