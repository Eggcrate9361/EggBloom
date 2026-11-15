# -*- coding: utf-8 -*-
"""autonomy.loop —— 自主循环 01→07（设计 §10.3，1.6.0 改为工作流驱动）。

察觉 → 探测 → 计划 → 执行 → 自验 → 接线 → 交付。
每步都写 `<data>/autonomy/steps.jsonl`，失败可取证；所有外部动作可注入。

## 1.6.0：硬编码七步 → harness.workflow 定义的 DAG

原来的 `run()` 是一长串顺序 if/early-return。现在七个步骤登记成一份
`harness.workflow` DAG（节点 + depends + when 条件分支），`run()` 只负责
组装上下文并 `workflow.start()`。步骤语义、返回结构、`_step()` 取证落盘
全部逐字保留 —— 换的是「编排方式」，不是「行为」。

节点约定：任何一步想提前返回，就 `ctx["__final__"] = {…原返回 dict…}`，
工作流引擎据此终止并把该 dict 作为 `start()["result"]` 交回 `run()`。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from . import acquire, breaker, plan as plan_mod, probe as probe_mod
from . import report as report_mod, verify as verify_mod, wire as wire_mod

_FINAL = "__final__"
_FLOW_NAME = "autonomy_7steps"


def _cleanup_artifacts(acquired, data_dir) -> None:
    """自验/接线失败时删掉半成品（只删数据目录内的产物）。"""
    import shutil
    base = Path(data_dir).resolve()
    for item in acquired or []:
        try:
            target = Path((item.get("artifact") or {}).get("target") or "").resolve()
        except Exception:
            continue
        if base not in target.parents and target != base:
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink()
        except Exception:
            pass


def _audit(actor: str, action: str, target: str = "", result: str = "ok", **extra) -> None:
    try:
        from separation import audit
        audit.log(actor, action, target=target, result=result, **extra)
    except Exception:
        pass


def _step(data_dir, name: str, payload: Dict[str, Any]) -> None:
    try:
        p = Path(data_dir) / "autonomy" / "steps.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                "step": name, **payload}, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _backend_call(backend, name, *args, **kwargs):
    fn = getattr(backend, name, None) if backend is not None else None
    if not callable(fn):
        return None
    return fn(*args, **kwargs)


# ══════════════════════════════════════════════════════════════
#  七个步骤节点（01→07）。读/写共享 ctx，语义与原 run() 逐字对齐。
# ══════════════════════════════════════════════════════════════

def _node_gap(ctx):
    gap = ctx["gap"]
    need = str(getattr(gap, "need", "") or "").strip()
    ctx["need"] = need
    dd = Path(ctx["data_dir"])
    if not need:
        ctx[_FINAL] = {"ok": False, "need": "", "step": "gap",
                       "error": "缺口没有具体能力名"}
        return {"stop": True}
    blocked = breaker.is_blocked(dd, need, now=ctx.get("now"))
    if blocked.get("blocked"):
        _audit("ai", "autonomy.blocked", target=need, result="blocked",
               reason=blocked.get("reason"))
        ctx[_FINAL] = {"ok": False, "need": need, "step": "breaker",
                       "blocked": True,
                       "report": f"{need} 还在熔断期，先不试。原因：{blocked.get('reason')}"}
        return {"stop": True}
    _step(dd, "gap", {"gap": gap.to_dict() if hasattr(gap, "to_dict") else str(gap)})
    return {"need": need}


def _node_probe(ctx):
    dd = Path(ctx["data_dir"])
    root = Path(ctx["root"])
    backend = ctx.get("backend")
    env = _backend_call(backend, "probe_env", dd, ctx.get("net_check"))
    if not isinstance(env, dict):
        env = probe_mod.env_report(data_dir=dd, net_check=ctx.get("net_check"),
                                   disk_paths=[str(dd), str(root)])
    probe_path = ""
    try:
        probe_path = probe_mod.write_report(env, dd)
    except Exception:
        pass
    _step(dd, "probe", {"report": probe_path})
    _audit("ai", "autonomy.probe", target=ctx["need"], result="ok", report=probe_path)
    ctx["env"] = env
    ctx["probe_path"] = probe_path
    return {"report": probe_path}


def _node_plan(ctx):
    dd = Path(ctx["data_dir"])
    root = Path(ctx["root"])
    backend = ctx.get("backend")
    gap = ctx["gap"]
    p = (ctx.get("plan_override") if isinstance(ctx.get("plan_override"), dict)
         else _backend_call(backend, "make_plan", gap, ctx.get("env")))
    if not isinstance(p, dict):
        p = plan_mod.make_plan(gap, ctx.get("env") or {}, data_dir=dd,
                               plugins_dir=root / "plugins")
    _step(dd, "plan", {"plan": p})
    ctx["p"] = p
    if not p.get("ok"):
        ctx[_FINAL] = {"ok": False, "need": ctx["need"], "step": "plan",
                       "error": p.get("error"),
                       "report": p.get("error") or "计划不可行", "plan": p}
        return {"stop": True}
    if ctx.get("dry_run"):
        ctx[_FINAL] = {"ok": True, "need": ctx["need"], "step": "plan",
                       "dry_run": True, "plan": p, "report": plan_mod.render(p)}
        return {"stop": True}
    return {"plan": p}


def _node_acquire(ctx):
    dd = Path(ctx["data_dir"])
    backend = ctx.get("backend")
    p = ctx["p"]
    need = ctx["need"]
    acquired = []
    for art in p.get("artifacts") or []:
        r = _backend_call(backend, "acquire", art, dd)
        if r is None:
            r = _default_acquire(art, dd, backend)
        _step(dd, "acquire", {"artifact": art, "result": r})
        if not isinstance(r, dict) or not r.get("ok"):
            breaker.fail(dd, need, str((r or {}).get("error") or "获取失败"))
            _audit("ai", "autonomy.acquire", target=need, result="error")
            ctx[_FINAL] = {"ok": False, "need": need, "step": "acquire",
                           "error": (r or {}).get("error") or "获取失败",
                           "report": report_mod.fail_text(
                               need, "获取", str((r or {}).get("error") or "")),
                           "plan": p}
            return {"stop": True}
        acquired.append({"artifact": art, "result": r})
    ctx["acquired"] = acquired
    return {"acquired": len(acquired)}


def _node_verify(ctx):
    dd = Path(ctx["data_dir"])
    backend = ctx.get("backend")
    need = ctx["need"]
    p = ctx["p"]
    checks = []
    for item in ctx["acquired"]:
        art = item["artifact"]
        r = _backend_call(backend, "verify", art, item["result"], dd)
        if r is None:
            r = _default_verify(art, item["result"])
        checks.append({"artifact": art, "ok": bool((r or {}).get("ok")), "result": r})
    v = verify_mod.run_checks(checks)
    _step(dd, "verify", {"checks": checks})
    ctx["checks"] = checks
    if not v.get("ok"):
        _cleanup_artifacts(ctx["acquired"], dd)
        breaker.fail(dd, need, "自验未通过")
        _audit("ai", "autonomy.verify", target=need, result="error")
        ctx[_FINAL] = {"ok": False, "need": need, "step": "verify",
                       "error": "自验未通过", "checks": checks,
                       "report": report_mod.fail_text(need, "自验", "产出不符合预期"),
                       "plan": p}
        return {"stop": True}
    return {"checks": len(checks)}


def _node_wire(ctx):
    dd = Path(ctx["data_dir"])
    root = Path(ctx["root"])
    backend = ctx.get("backend")
    p = ctx["p"]
    acquired = ctx["acquired"]
    need = ctx["need"]
    try:
        rollback = wire_mod.snapshot_rollback(dd, root)
    except Exception as e:
        breaker.fail(dd, need, f"回滚快照失败: {e}")
        _audit("ai", "autonomy.rollback", target=need, result="error", error=str(e))
        ctx[_FINAL] = {"ok": False, "need": need, "step": "rollback",
                       "error": f"回滚快照失败: {e}",
                       "report": report_mod.fail_text(need, "回滚快照", str(e)),
                       "plan": p}
        return {"stop": True}
    w = _backend_call(backend, "wire", p, acquired, dd, root)
    if w is None:
        w = {"ok": True, "records": []}
        for item in acquired:
            art = item["artifact"]
            w["records"].append(wire_mod.record_installed(dd, {
                "need": need, "kind": art.get("kind"), "name": art.get("name"),
                "target": art.get("target"), "source_url": art.get("url", ""),
                "reason": getattr(ctx["gap"], "evidence", ""),
            }))
    _step(dd, "wire", {"result": w})
    ctx["w"] = w
    ctx["rollback"] = rollback
    if not isinstance(w, dict) or not w.get("ok"):
        wire_mod.restore_rollback(rollback, root)
        _cleanup_artifacts(acquired, dd)
        breaker.fail(dd, need, str((w or {}).get("error") or "接线失败"))
        _audit("ai", "autonomy.wire", target=need, result="error")
        ctx[_FINAL] = {"ok": False, "need": need, "step": "wire",
                       "error": (w or {}).get("error") or "接线失败",
                       "report": report_mod.fail_text(
                           need, "接线", str((w or {}).get("error") or "")),
                       "plan": p}
        return {"stop": True}
    return {"ok": True}


def _node_done(ctx):
    dd = Path(ctx["data_dir"])
    need = ctx["need"]
    p = ctx["p"]
    acquired = ctx["acquired"]
    checks = ctx["checks"]
    w = ctx["w"]
    rollback = ctx["rollback"]
    breaker.success(dd, need)
    _audit("ai", "autonomy.done", target=need, result="ok")
    _step(dd, "done", {"need": need})
    size_mb = sum(int((i["result"] or {}).get("bytes") or 0)
                  for i in acquired) / (1024 * 1024)
    ctx[_FINAL] = {"ok": True, "need": need, "step": "done", "plan": p,
                   "checks": checks, "wired": w, "rollback": rollback,
                   "report": report_mod.ok_text(need, detail="已自验",
                                                size_mb=size_mb)}
    return {"ok": True}


def _when_not_stopped(ctx) -> bool:
    """条件分支：上游一旦设了 __final__（提前返回），后续步骤整条跳过。"""
    return _FINAL not in ctx


#: 七步 DAG（01→07）。depends 组成链；when 是条件分支保护。
_FLOW_NODES = [
    {"id": "gap", "run": _node_gap},
    {"id": "probe", "depends": ["gap"], "run": _node_probe},
    {"id": "plan", "depends": ["probe"], "run": _node_plan},
    {"id": "acquire", "depends": ["plan"], "when": _when_not_stopped, "run": _node_acquire},
    {"id": "verify", "depends": ["acquire"], "when": _when_not_stopped, "run": _node_verify},
    {"id": "wire", "depends": ["verify"], "when": _when_not_stopped, "run": _node_wire},
    {"id": "done", "depends": ["wire"], "when": _when_not_stopped, "run": _node_done},
]


def run(gap, *, data_dir, root=None, backend=None, net_check=None,
        dry_run: bool = False, now: Optional[float] = None,
        plan_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """跑完一轮自主获取。返回 `{ok, need, step, report, plan, error}`。

    1.6.0：七步改由 `harness.workflow` 的 DAG 驱动；签名与返回语义不变。
    """
    from harness import workflow as _wf
    dd = Path(data_dir)
    root = Path(root) if root else Path.cwd()
    ctx = {
        "gap": gap, "data_dir": str(dd), "root": str(root),
        "backend": backend, "net_check": net_check, "now": now,
        "dry_run": bool(dry_run), "plan_override": plan_override,
    }
    store_dir = os.path.join(str(dd), "workflows")
    # 定义随本次 run 落盘（幂等），运行态也落在同一 store_dir，便于凭 run_id 恢复
    _wf.define(_FLOW_NAME, _FLOW_NODES, store_dir=store_dir)
    state = _wf.start(_FLOW_NAME, context=ctx, store_dir=store_dir,
                      run_id="auto-" + uuid.uuid4().hex[:10])
    res = state.get("result")
    if isinstance(res, dict) and "step" in res and "ok" in res:
        return res
    return {"ok": False, "need": str(getattr(gap, "need", "") or ""),
            "step": "workflow", "error": "工作流未产出有效结果"}
