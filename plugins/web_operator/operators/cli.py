# -*- coding: utf-8 -*-
import subprocess, shlex
from pathlib import Path
from typing import Any, Dict


class CliOperator:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.cfg = config.get("cli", {})

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
        params = task.get("params", {}) or {}
        cmd = params.get("cmd")
        if not cmd:
            return self._fail("missing cmd")
        cwd = params.get("cwd") or str(Path.home())
        timeout = int(params.get("timeout") or 30)
        if not self._allowed(cmd):
            return self._fail("command blocked by policy", [{"level": "error", "text": f"blocked: {cmd}"}])
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            logs = [
                {"level": "info", "text": f"执行命令：{cmd}"},
                {"level": "success", "text": f"exit={proc.returncode}"},
            ]
            if proc.stdout:
                logs.append({"level": "info", "text": proc.stdout[-4000:]})
            if proc.stderr:
                logs.append({"level": "warn", "text": proc.stderr[-4000:]})
            return self._ok(
                {"cmd": cmd, "cwd": cwd, "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr},
                logs,
            )
        except subprocess.TimeoutExpired:
            return self._fail("command timeout", [{"level": "error", "text": f"timeout after {timeout}s: {cmd}"}])
        except Exception as e:
            return self._fail(str(e), [{"level": "error", "text": str(e)}])

    def _allowed(self, cmd: str) -> bool:
        if self.cfg.get("allow_any_command", False):
            return True
        blocked = [x.lower() for x in self.cfg.get("blocked_commands", [])]
        if any(b in cmd.lower() for b in blocked):
            return False
        allowed = [x.lower() for x in self.cfg.get("allowed_commands", [])]
        if not allowed:
            return True
        first = shlex.split(cmd)[0].lower()
        return first in allowed
