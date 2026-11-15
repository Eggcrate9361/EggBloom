# -*- coding: utf-8 -*-
import subprocess
from typing import Any, Dict


class AppOperator:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.cfg = config.get("app", {})

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
        if task_type == "app.launch":
            return self.launch(params)
        if task_type == "app.kill":
            return self.kill(params)
        if task_type == "app.clipboard_read":
            return self.clipboard_read(params)
        if task_type == "app.clipboard_write":
            return self.clipboard_write(params)
        if task_type == "app.notify":
            return self.notify(params)
        return self._fail(f"unsupported app task: {task_type}")

    def launch(self, params):
        exe = params.get("exe") or params.get("path")
        if not exe:
            return self._fail("missing exe")
        args = params.get("args") or []
        if not self.cfg.get("allow_launch", True):
            return self._fail("launch disabled")
        blocked = [x.lower() for x in self.cfg.get("blocked_apps", [])]
        if any(b in exe.lower() for b in blocked):
            return self._fail("app blocked by policy")
        try:
            proc = subprocess.Popen([exe, *args], shell=False, close_fds=True)
            return self._ok({"pid": proc.pid, "exe": exe})
        except Exception as e:
            return self._fail(str(e))

    def kill(self, params):
        name = params.get("name")
        pid = params.get("pid")
        killed = []
        try:
            import psutil
            if pid:
                p = psutil.Process(int(pid))
                p.kill()
                killed.append(int(pid))
            elif name:
                for p in psutil.process_iter(["pid", "name"]):
                    try:
                        if p.info["name"] and name.lower() in p.info["name"].lower():
                            p.kill()
                            killed.append(p.info["pid"])
                    except Exception:
                        pass
        except Exception as e:
            return self._fail(str(e))
        return self._ok({"killed": killed})

    def clipboard_read(self, params):
        try:
            import pyperclip
            text = pyperclip.paste()
            return self._ok({"text": text})
        except Exception as e:
            return self._fail(str(e))

    def clipboard_write(self, params):
        text = params.get("text", "")
        try:
            import pyperclip
            pyperclip.copy(text)
            return self._ok({"text": text})
        except Exception as e:
            return self._fail(str(e))

    def notify(self, params):
        title = params.get("title") or "EggBloom"
        text = params.get("text") or ""
        try:
            from winotify import ToastNotifier
            toast = ToastNotifier()
            toast.show_toast(title, text, duration=5, threaded=True)
            return self._ok({"title": title, "text": text})
        except Exception as e:
            return self._fail(str(e))
