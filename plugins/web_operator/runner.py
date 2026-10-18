# -*- coding: utf-8 -*-
"""
Chain runner：串行执行任务链。
"""

import time
from typing import Any, Dict, List


def _ensure_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def run_chain(params: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    chain = _ensure_list(params.get("chain") or [])
    continue_on_error = bool(params.get("continue_on_error", True))
    max_failures = int(params.get("max_failures", 2) or 2)
    if not chain:
        return {"ok": False, "error": "empty chain", "logs": [{"level": "error", "text": "chain is empty"}]}

    from . import execute_task
    logs: List[Dict[str, Any]] = []
    failures = 0
    context: Dict[str, Any] = {}
    results = []
    started = time.time()

    for idx, step in enumerate(chain, 1):
        task_type = step.get("type") or ""
        step_params = step.get("params") or {}
        logs.append({"level": "info", "text": f"[chain] step {idx}/{len(chain)}: {task_type}"})
        try:
            res = execute_task(task_type, step_params, context=context)
        except Exception as e:
            res = {"ok": False, "error": str(e), "logs": [{"level": "error", "text": str(e)}]}
        step_logs = res.get("logs") or []
        logs.extend(step_logs)
        results.append({"step": idx, "type": task_type, "ok": res.get("ok"), "result": res.get("result"), "error": res.get("error")})
        if not res.get("ok"):
            failures += 1
            logs.append({"level": "warn", "text": f"[chain] step {idx} failed: {res.get('error')}"})
            if not continue_on_error or failures > max_failures:
                break
        if isinstance(res.get("result"), dict):
            context.setdefault("previous_result", {})
            context["previous_result"].update(res["result"])

    elapsed_ms = int((time.time() - started) * 1000)
    ok = failures == 0
    return {
        "ok": ok,
        "result": {
            "steps": results,
            "failures": failures,
            "elapsed_ms": elapsed_ms,
            "completed_steps": len(results),
            "total_steps": len(chain),
        },
        "logs": logs,
        "meta": {"chain": True, "failures": failures},
    }
