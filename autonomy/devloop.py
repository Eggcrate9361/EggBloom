# -*- coding: utf-8 -*-
"""autonomy/devloop.py —— 自主开发层：改已有代码 → 重启 → 回归（1.6.0 P3）。

## 这一层解决什么

`plugin_forge` 只管**造新插件**（生成 → staging → 人审批），
`autonomy/loop.py` 只管**装外部产物**（pip / 模型 / 二进制）。
「改仓库里已经有的代码，然后重启、跑回归」这三段一直是空的：
既没有编排、没有失败回滚、也没有「到底改没改」的凭据。

本模块把三段做成一份 `harness.workflow` DAG，和七步自主循环用同一套
运行态持久化（凭 `run_id` 查 / 恢复 / 取消）。

## 硬约束（比功能重要）

1. **本模块不自带任何真实执行器**。改码、重启进程、跑全量回归
   都必须由调用方显式注入 `backend`；默认 `PreviewBackend` 只记意图，
   `applied` 恒为 False。
2. **两道闸**：`confirm=False`（默认）→ 一律预演，就算注入了真 backend
   也不执行；`confirm=True` 但没给 backend → 直接拒绝，不猜一个默认实现。
3. **失败即回滚**：任何一段失败都调 `backend.rollback()` 并把
   `__final__` 写成失败结果；节点抛异常时由 workflow 的 `fallback`
   再兜一次回退（只跑一次）。
4. **凭据可查**：每段把 `{stage, ok, applied, detail}` 追加到 `ctx["trace"]`，
   随运行态一起落盘。没写进 trace 的动作视为没发生。
5. 源码库里**不会**因为 import 本模块发生任何写盘/重启；`define()` 只在
   显式 `run()` 时写 `store_dir` 下的流程定义与运行态。

## backend 契约

    snapshot(ctx) -> dict      # 改前快照（回滚依据）
    apply_patch(ctx) -> dict   # 改已有代码
    restart(ctx) -> dict       # 重启
    regression(ctx) -> dict    # 回归
    rollback(ctx) -> dict      # 回滚

返回 dict 里 `ok` 为假即视为失败；`applied` 由 backend 自己声明，
本模块不替它断言「已生效」。
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Any, Dict, List, Optional

FLOW_NAME = "selfdev_3stage"
STAGES = ("verify", "patch", "restart", "regress", "finalize")
_FINAL = "__final__"
_BAD_PATH = re.compile(r"(^/|[a-zA-Z]:|^\.|\.\.|\\|^$)")

__all__ = [
    "FLOW_NAME", "STAGES", "PreviewBackend", "plan", "run",
    "status", "resume", "cancel",
]


# ── 默认后端：只记意图 ───────────────────────────────────────────
class PreviewBackend:
    """不落盘、不重启、不跑测试的后端。`applied` 恒 False。"""

    name = "preview"

    def __init__(self):
        self.calls: List[str] = []

    def snapshot(self, ctx):
        self.calls.append("snapshot")
        return {"ok": True, "applied": False, "preview": True,
                "detail": "改前快照（预演）"}

    def apply_patch(self, ctx):
        self.calls.append("apply_patch")
        return {"ok": True, "applied": False, "preview": True,
                "detail": "改已有代码（预演）"}

    def restart(self, ctx):
        self.calls.append("restart")
        return {"ok": True, "applied": False, "preview": True,
                "detail": "重启（预演）"}

    def regression(self, ctx):
        self.calls.append("regression")
        return {"ok": True, "applied": False, "preview": True,
                "detail": "回归（预演）"}

    def rollback(self, ctx):
        self.calls.append("rollback")
        return {"ok": True, "applied": False, "preview": True,
                "detail": "回滚（预演）"}


# ── 入参校验 ─────────────────────────────────────────────────────
def _check_diff(diff) -> List[str]:
    """diff 形状检查。返回问题列表（空 = 通过）。不执行、不读文件。"""
    problems = []
    if not isinstance(diff, dict):
        return ["diff 必须是对象"]
    files = diff.get("files")
    if not isinstance(files, list) or not files:
        return ["diff.files 必须是非空列表"]
    for i, item in enumerate(files):
        p = item.get("path") if isinstance(item, dict) else item
        p = str(p or "").strip().replace("\\", "/")
        if _BAD_PATH.search(p):
            problems.append(f"files[{i}] 非法路径: {p!r}（须相对、无 ..、无盘符）")
    return problems


def _be(ctx):
    """取当前 backend；运行态从盘上恢复后它是字符串，这里一律当丢失处理。"""
    b = ctx.get("backend")
    return b if hasattr(b, "apply_patch") else None


def _trace(ctx, stage: str, ok: bool, applied: bool, detail: str) -> None:
    ctx.setdefault("trace", []).append({
        "stage": stage, "ok": bool(ok), "applied": bool(applied),
        "detail": str(detail)[:300],
    })


def _call(ctx, stage: str, method: str) -> Dict[str, Any]:
    b = _be(ctx)
    if b is None:
        _trace(ctx, stage, False, False, "backend 丢失（跨进程恢复需重新注入）")
        return {"ok": False, "applied": False, "detail": "backend 丢失"}
    fn = getattr(b, method, None)
    if not callable(fn):
        _trace(ctx, stage, False, False, f"backend 缺 {method}()")
        return {"ok": False, "applied": False, "detail": f"backend 缺 {method}()"}
    try:
        r = fn(ctx)
        r = r if isinstance(r, dict) else {"ok": False, "detail": "返回不是 dict"}
    except Exception as e:
        r = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    _trace(ctx, stage, bool(r.get("ok")), bool(r.get("applied")),
           str(r.get("detail") or ""))
    return r


def _fail(ctx, stage: str, reason: str) -> Dict[str, Any]:
    """某段失败：立刻回滚 + 提前终止整个流程。"""
    _call(ctx, "rollback", "rollback")
    ctx["failed_at"] = stage
    ctx[_FINAL] = _result(ctx, ok=False, error=f"{stage}: {reason}")
    return {"stop": True}


def _result(ctx, *, ok: bool, error: str = "") -> Dict[str, Any]:
    return {
        "ok": bool(ok), "flow": FLOW_NAME, "dry_run": bool(ctx.get("dry_run", True)),
        "target": str(ctx.get("target") or ""),
        "stages": list(STAGES), "trace": list(ctx.get("trace") or []),
        "applied": any(t.get("applied") for t in (ctx.get("trace") or [])),
        "error": str(error or ""),
    }


# ── DAG 节点 ─────────────────────────────────────────────────────
def _node_verify(ctx):
    problems = _check_diff(ctx.get("diff"))
    if problems:
        return _fail(ctx, "verify", "；".join(problems[:3]))
    r = _call(ctx, "verify", "snapshot")
    if not r.get("ok"):
        return _fail(ctx, "verify", str(r.get("detail") or "快照失败"))
    return {"ok": True, "files": len(ctx["diff"]["files"])}


def _node_patch(ctx):
    r = _call(ctx, "patch", "apply_patch")
    if not r.get("ok"):
        return _fail(ctx, "patch", str(r.get("detail") or "改码失败"))
    return {"ok": True, "applied": bool(r.get("applied"))}


def _node_restart(ctx):
    r = _call(ctx, "restart", "restart")
    if not r.get("ok"):
        return _fail(ctx, "restart", str(r.get("detail") or "重启失败"))
    return {"ok": True, "restarted": bool(r.get("applied"))}


def _node_regress(ctx):
    r = _call(ctx, "regress", "regression")
    if not r.get("ok"):
        return _fail(ctx, "regress", str(r.get("detail") or "回归未通过"))
    return {"ok": True, "ran": bool(r.get("applied"))}


def _node_finalize(ctx):
    ctx[_FINAL] = _result(ctx, ok=True)
    return {"ok": True}


def _node_rollback(ctx):
    """fallback 专用：节点抛异常时由 workflow 直接调，不进正常依赖链。"""
    _call(ctx, "rollback", "rollback")
    ctx["failed_at"] = ctx.get("__fallback_from__") or "unknown"
    return {"ok": True}


def _when_go(ctx) -> bool:
    return _FINAL not in ctx


def _when_never(ctx) -> bool:
    return False


_NODES = [
    {"id": "verify", "run": _node_verify},
    {"id": "patch", "depends": ["verify"], "when": _when_go,
     "run": _node_patch, "fallback": "rollback"},
    {"id": "restart", "depends": ["patch"], "when": _when_go,
     "run": _node_restart, "fallback": "rollback"},
    {"id": "regress", "depends": ["restart"], "when": _when_go,
     "run": _node_regress, "fallback": "rollback"},
    {"id": "finalize", "depends": ["regress"], "when": _when_go,
     "run": _node_finalize},
    {"id": "rollback", "when": _when_never, "run": _node_rollback},
]


# ── 对外 API ─────────────────────────────────────────────────────
def plan() -> Dict[str, Any]:
    """只描述流程，不跑、不写盘。给 WebUI / 交接文档看。"""
    return {
        "flow": FLOW_NAME,
        "stages": list(STAGES),
        "deps": {"verify": [], "patch": ["verify"], "restart": ["patch"],
                 "regress": ["restart"], "finalize": ["regress"]},
        "fallback": "rollback（异常时只跑一次）",
        "gates": [
            "confirm=False（默认）→ 一律 PreviewBackend 预演，applied 恒 False",
            "confirm=True 且未注入 backend → 直接拒绝",
            "任何一段失败 → backend.rollback() + 提前终止",
        ],
        "note": "本层不自带改码 / 重启 / 回归实现，执行器由调用方注入。",
    }


def _store(store_dir: str) -> str:
    return str(store_dir or "").strip() or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "workflows")


def run(diff, *, backend=None, confirm: bool = False, store_dir: str = "",
        run_id: str = "", target: str = "", note: str = "") -> Dict[str, Any]:
    """跑三段闭环。默认只预演；真执行必须同时给 `confirm=True` 与 backend。"""
    from harness import workflow as wf

    problems = _check_diff(diff)
    if problems:
        return {"ok": False, "dry_run": True, "run_id": "", "stages": list(STAGES),
                "trace": [], "applied": False, "error": "；".join(problems[:3]),
                "flow": FLOW_NAME}
    if confirm and backend is None:
        return {"ok": False, "dry_run": True, "run_id": "", "stages": list(STAGES),
                "trace": [], "applied": False, "flow": FLOW_NAME,
                "error": "confirm=True 需要显式注入 backend；本层不自带真实执行器"}

    dry = not bool(confirm)
    be = PreviewBackend() if dry else backend
    sd = _store(store_dir)
    ctx: Dict[str, Any] = {
        "diff": diff, "target": str(target or (diff.get("target") if isinstance(diff, dict) else "") or ""),
        "note": str(note or ""), "dry_run": dry,
        "backend": be, "backend_name": getattr(be, "name", type(be).__name__),
        "trace": [],
    }
    rid = str(run_id or "").strip() or ("dev-" + uuid.uuid4().hex[:10])
    wf.define(FLOW_NAME, _NODES, store_dir=sd)
    state = wf.start(FLOW_NAME, context=ctx, store_dir=sd, run_id=rid)
    out = _summarize(state, rid, dry)
    return out


def _summarize(state: dict, run_id: str, dry: bool) -> Dict[str, Any]:
    res = state.get("result")
    if isinstance(res, dict) and "ok" in res and "stages" in res:
        out = dict(res)
    else:
        ctx = state.get("context") or {}
        failed = [n for n, s in (state.get("steps") or {}).items()
                  if s.get("status") == "failed"]
        out = _result(ctx, ok=False,
                      error=f"工作流异常终止：{', '.join(failed) or state.get('status')}")
    out["run_id"] = run_id
    out["status"] = state.get("status")
    out["dry_run"] = dry
    return out


def status(run_id: str, *, store_dir: str = ""):
    from harness import workflow as wf
    return wf.status(run_id, store_dir=_store(store_dir))


def resume(run_id: str, *, backend=None, confirm: bool = False,
           store_dir: str = ""):
    """跨进程恢复：运行态里的 backend 是字符串，必须重新注入。"""
    from harness import workflow as wf
    sd = _store(store_dir)
    dry = not bool(confirm)
    be = PreviewBackend() if dry else backend
    if be is None:
        return {"ok": False, "run_id": run_id, "dry_run": dry,
                "error": "恢复需要重新注入 backend"}
    st = wf.resume(run_id, store_dir=sd,
                   context={"backend": be, "dry_run": dry})
    if st is None:
        return {"ok": False, "run_id": run_id, "dry_run": dry,
                "error": "找不到运行态"}
    return _summarize(st, run_id, dry)


def cancel(run_id: str, reason: str = "", *, store_dir: str = "") -> bool:
    from harness import workflow as wf
    return wf.cancel(run_id, reason=reason, store_dir=_store(store_dir))
