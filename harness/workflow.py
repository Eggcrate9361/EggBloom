# -*- coding: utf-8 -*-
"""harness/workflow.py —— DAG 工作流（1.6.0「四件套」之二）。

## 为什么

`autonomy/loop.py` 的七步是**硬编码**的顺序 if/return：想加一步、想让某步重试、
想让两条分支并行、想重跑一半，都得改那个函数本身。本层把「流程定义」和
「运行态」都抽出来落盘，凭 `run_id` 跨会话恢复。

## 能力

- **DAG**：节点 + `depends` 依赖；拓扑分层，同层无依赖的节点**并行**执行；
- **条件分支**：节点的 `when(ctx)` 为假 → 该节点 `skipped`（分支未命中）；
- **步骤级重试**：`retries=n`，节点抛异常时最多重试 n 次；
- **步骤级回退**：`fallback=<node_id>`，节点最终失败时自动执行回退节点（只跑一次，
  防环），典型用途是清理/回滚；
- **流程定义 + 运行态持久化**：`defs/<name>.json` 与 `runs/<run_id>.json`；
- **跨会话恢复**：`resume(run_id)` 读回运行态，已 `done` 的节点不重跑，
  其余重新执行；`status/cancel` 同样只认 run_id。

## 节点函数约定

`run(ctx)`：读/写共享 `ctx`（dict），返回值记进 `steps[id].result`。
约定 `ctx["__final__"] = {...}` 表示**提前终止整个流程**并把该 dict 作为
`start()["result"]` 返回 —— 这是给「七步里任何一步都可能提前 return」用的。

## 处理器（callable）与持久化的矛盾

`run/when` 是函数，不能进 JSON。做法：定义时把 callables 登记到进程内注册表
`_HANDLERS`（键 `name:node_id`），盘上只存「有没有 handler」的布尔标记。
`resume()` 时靠 `define()` 在导入期重新登记（或显式传 `handlers=`）把函数装回去 ——
所以「定义模块被 import」是跨会话恢复的前提，这与 `subagent`/插件的恢复模型一致。
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

# ── 状态 ────────────────────────────────────────────────────────
ST_PENDING = "pending"
ST_RUNNING = "running"
ST_DONE = "done"
ST_FAILED = "failed"
ST_SKIPPED = "skipped"

RUN_RUNNING = "running"
RUN_DONE = "done"
RUN_FAILED = "failed"
RUN_CANCELLED = "cancelled"

_FINAL_KEY = "__final__"


def _now() -> float:
    return time.time()


def default_dir() -> str:
    d = os.environ.get("EGGBLOOM_WORKFLOW_DIR")
    if d:
        return str(d)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "workflows")


def _defs_dir(store_dir: str) -> str:
    return os.path.join(store_dir, "defs")


def _runs_dir(store_dir: str) -> str:
    return os.path.join(store_dir, "runs")


def _atomic_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json",
                               dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ── 处理器注册表（进程内，函数不落盘）────────────────────────────
_HANDLERS: Dict[str, dict] = {}
_HAND_LOCK = threading.Lock()
_DEFS: Dict[str, dict] = {}
_RUN_INDEX: Dict[str, str] = {}        # run_id -> store_dir（跨会话时靠 store_dir 参数）
_RUN_LOCKS: Dict[str, threading.RLock] = {}
_RUN_LOCKS_LOCK = threading.Lock()
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _safe_id(value: str, kind: str) -> str:
    ident = str(value or "").strip()
    if not ident or ".." in ident or not _SAFE_ID.fullmatch(ident):
        raise ValueError(f"非法 {kind}: {value!r}")
    return ident


def _run_lock(run_id: str) -> threading.RLock:
    with _RUN_LOCKS_LOCK:
        return _RUN_LOCKS.setdefault(run_id, threading.RLock())


def _hkey(name: str, node_id: str) -> str:
    return f"{name}:{node_id}"


def _topo(nodes: List[dict]) -> (List[str], Dict[str, int]):
    """返回 (拓扑序, 层级)。非法依赖 / 有环 → ValueError。"""
    ids = [n["id"] for n in nodes]
    idset = set(ids)
    if len(idset) != len(ids):
        raise ValueError("节点 id 重复")
    deps = {n["id"]: list(n.get("depends") or []) for n in nodes}
    for nid, ds in deps.items():
        for d in ds:
            if d not in idset:
                raise ValueError(f"节点 {nid} 依赖了不存在的节点 {d}")
    level: Dict[str, int] = {}
    # 反复松弛求层级（同时天然检环：有环则永远算不完）
    for _ in range(len(ids) + 1):
        changed = False
        for nid in ids:
            want = 0 if not deps[nid] else max(level.get(d, -1) for d in deps[nid]) + 1
            if want > len(ids):
                raise ValueError("依赖图有环")
            if level.get(nid, -1) < want:
                level[nid] = want
                changed = True
        if not changed:
            break
    if set(level) != idset:
        raise ValueError("依赖图有环")
    order = sorted(ids, key=lambda x: (level[x], x))
    return order, level


def define(name: str, nodes: List[dict], *, store_dir: str = "") -> dict:
    """登记一份流程定义，返回**可序列化**的定义 dict。

    节点字段：id / depends / when / run / retries / fallback。
    `when`、`run` 是 callable，登记进注册表但不写进盘上的定义。
    """
    name = _safe_id(str(name or "").strip() or ("flow-" + uuid.uuid4().hex[:8]),
                    "流程名")
    clean = []
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            raise ValueError(f"第 {i} 个节点不是 dict")
        nid = str(n.get("id") or f"n{i}").strip()
        run_fn = n.get("run") if callable(n.get("run")) else None
        when_fn = n.get("when") if callable(n.get("when")) else None
        clean.append({
            "id": nid,
            "depends": [str(x) for x in (n.get("depends") or [])],
            "retries": max(0, int(n.get("retries") or 0)),
            "fallback": str(n.get("fallback") or "").strip(),
            "has_run": run_fn is not None,
            "has_when": when_fn is not None,
        })
        with _HAND_LOCK:
            _HANDLERS[_hkey(name, nid)] = {"run": run_fn, "when": when_fn}
    defn = {"name": name, "nodes": clean}
    _topo(clean)                        # 立刻校验依赖/环
    _DEFS[name] = defn
    sd = store_dir or default_dir()
    _atomic_json(os.path.join(_defs_dir(sd), f"{name}.json"), defn)
    return defn


def _resolve_defn(spec, store_dir: str) -> dict:
    """spec 可以是：名字(str) / define 返回的 dict / 原始节点 list。"""
    sd = store_dir or default_dir()
    if isinstance(spec, str):
        spec = _safe_id(spec, "流程名")
        if spec in _DEFS:
            return _DEFS[spec]
        d = _read_json(os.path.join(_defs_dir(sd), f"{spec}.json"))
        if d is None:
            raise ValueError(f"找不到流程定义：{spec}")
        return d
    if isinstance(spec, dict) and "nodes" in spec:
        return spec
    if isinstance(spec, list):
        return define("flow-" + uuid.uuid4().hex[:8], spec, store_dir=sd)
    raise ValueError("start 需要 流程名 / 定义 dict / 节点 list")


def _resolve_handlers(name: str, defn: dict, handlers) -> Dict[str, dict]:
    """把每个节点的 run/when 函数装回去：优先用显式 handlers，否则查注册表。"""
    out: Dict[str, dict] = {}
    explicit = handlers if isinstance(handlers, dict) else {}
    for n in defn["nodes"]:
        nid = n["id"]
        reg = _HANDLERS.get(_hkey(name, nid), {})
        h = {"run": reg.get("run"), "when": reg.get("when")}
        src = explicit.get(nid, explicit.get(_hkey(name, nid)))
        if src is not None:
            if callable(src):
                h["run"] = src
            elif isinstance(src, dict):
                if callable(src.get("run")):
                    h["run"] = src["run"]
                if callable(src.get("when")):
                    h["when"] = src["when"]
        out[nid] = h
    return out


def _new_state(run_id: str, name: str, context: dict, defn: dict) -> dict:
    steps = {n["id"]: {"status": ST_PENDING, "attempts": 0,
                       "result": None, "error": "", "skip_reason": ""}
             for n in defn["nodes"]}
    return {
        "run_id": run_id, "name": name, "status": RUN_RUNNING,
        "context": dict(context or {}), "steps": steps,
        "result": None, "created": _now(), "updated": _now(),
    }


def _run_node(nid: str, ctx: dict, hmap: Dict[str, dict], retries: int):
    """跑一个节点（含重试）。返回 (status, result, error, attempts)。"""
    h = hmap.get(nid, {})
    fn = h.get("run")
    if not callable(fn):
        return ST_FAILED, None, "MissingHandler: 节点执行器未注册", 1
    err = ""
    attempts = 0
    for _ in range(retries + 1):
        attempts += 1
        try:
            out = fn(ctx)
            return ST_DONE, out, "", attempts
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    return ST_FAILED, None, err, attempts


def _clone_ctx(ctx: dict) -> dict:
    out = {}
    for key, value in (ctx or {}).items():
        if isinstance(value, (dict, list, set, tuple)):
            try:
                out[key] = copy.deepcopy(value)
            except Exception:
                out[key] = value
        else:
            out[key] = value
    return out


def _ctx_changes(base: dict, local: dict) -> dict:
    changed = {}
    for key, value in (local or {}).items():
        if key not in base:
            changed[key] = value
            continue
        try:
            same = value == base[key]
        except Exception:
            same = value is base[key]
        if not same:
            changed[key] = value
    return changed


def _execute(state: dict, defn: dict, hmap: Dict[str, dict],
             stop_check=None) -> dict:
    """按拓扑层级执行：同层无依赖节点并行；支持条件分支、重试、回退、提前终止。"""
    order, level = _topo(defn["nodes"])
    by_level: Dict[int, List[str]] = {}
    for nid in order:
        by_level.setdefault(level[nid], []).append(nid)
    deps = {n["id"]: list(n.get("depends") or []) for n in defn["nodes"]}
    retries = {n["id"]: int(n.get("retries") or 0) for n in defn["nodes"]}
    fallback = {n["id"]: str(n.get("fallback") or "").strip() for n in defn["nodes"]}
    fallback_targets = {v for v in fallback.values() if v}
    ctx = state["context"]
    steps = state["steps"]
    fallback_done = set()
    aborted = False

    for L in sorted(by_level):
        if aborted:
            break
        if stop_check and stop_check():
            state["status"] = RUN_CANCELLED
            state["updated"] = _now()
            return state
        batch = [nid for nid in by_level[L] if steps[nid]["status"] == ST_PENDING]
        # 先判依赖与条件（条件分支未命中 → skipped）
        ready = []
        for nid in batch:
            ds = deps[nid]
            if any(steps[d]["status"] != ST_DONE for d in ds):
                steps[nid] = {"status": ST_SKIPPED, "attempts": 0,
                              "result": None, "error": "",
                              "skip_reason": "依赖未满足"}
                continue
            when = hmap.get(nid, {}).get("when")
            if nid in fallback_targets:
                continue
            if callable(when) and not _safe_when(when, ctx):
                steps[nid] = {"status": ST_SKIPPED, "attempts": 0,
                              "result": None, "error": "",
                              "skip_reason": "条件分支未命中"}
                continue
            ready.append(nid)

        if not ready:
            continue

        # 同层并行执行
        def _one(nid):
            local = _clone_ctx(ctx)
            result = _run_node(nid, local, hmap, retries[nid])
            return nid, result, local

        results = {}
        changes = {}
        if len(ready) == 1:
            nid, r, local = _one(ready[0])
            results[nid] = r
            changes[nid] = _ctx_changes(ctx, local)
        else:
            with ThreadPoolExecutor(max_workers=len(ready)) as ex:
                for nid, r, local in ex.map(_one, ready):
                    results[nid] = r
                    changes[nid] = _ctx_changes(ctx, local)

        # 先把**整批**结果合并完（不能因为某个节点触发回退就丢掉并行兄弟的结果）
        failed_nodes = []
        for nid, (st, res, err, att) in results.items():
            steps[nid] = {"status": st, "attempts": att,
                          "result": res, "error": err, "skip_reason": ""}
            if st == ST_FAILED:
                failed_nodes.append(nid)

        owners = {}
        for nid in sorted(ready):
            if steps[nid]["status"] != ST_DONE:
                continue
            for key, value in changes.get(nid, {}).items():
                if key in owners and owners[key][1] != value:
                    step = steps[nid]
                    step["status"] = ST_FAILED
                    step["error"] = f"并发上下文冲突: {key} 已被 {owners[key][0]} 修改"
                    failed_nodes.append(nid)
                    break
                owners[key] = (nid, value)
        for key, (_owner, value) in owners.items():
            ctx[key] = value

        # 步骤级回退：失败节点配了 fallback 就执行回退节点（只跑一次，防环）
        for nid in failed_nodes:
            fb = fallback.get(nid)
            if not fb or fb not in steps or fb in fallback_done:
                continue
            fallback_done.add(fb)
            ctx["__fallback_from__"] = nid
            fb_st, fb_res, fb_err, fb_att = _run_node(fb, ctx, hmap, retries.get(fb, 0))
            steps[fb] = {"status": fb_st, "attempts": fb_att,
                         "result": fb_res, "error": fb_err, "skip_reason": ""}

        if _FINAL_KEY in ctx:
            aborted = True
            break
        # 有节点最终失败且没配（或已跑过）回退 → 整个流程失败，不再往后走
        if any(steps[n]["status"] == ST_FAILED and not fallback.get(n)
               for n in by_level[L]):
            aborted = True
            break

    # 收尾：剩余 pending 全部标 skipped
    for nid in order:
        if steps[nid]["status"] == ST_PENDING:
            steps[nid]["status"] = ST_SKIPPED
            steps[nid]["skip_reason"] = "流程提前结束"

    if _FINAL_KEY in ctx:
        state["result"] = ctx[_FINAL_KEY]
        state["status"] = RUN_DONE
    elif any(s["status"] == ST_FAILED for s in steps.values()):
        state["result"] = ctx
        state["status"] = RUN_FAILED
    else:
        state["result"] = ctx
        state["status"] = RUN_DONE
    state["updated"] = _now()
    return state


def _safe_when(when: Callable, ctx: dict) -> bool:
    try:
        return bool(when(ctx))
    except Exception:
        return False


def _store_dir_for(run_id: str, store_dir: str) -> str:
    return store_dir or _RUN_INDEX.get(run_id) or default_dir()


def _find_run(run_id: str, store_dir: str) -> Optional[dict]:
    rid = str(run_id or "").strip()
    if not rid:
        return None
    sd = _store_dir_for(rid, store_dir)
    d = _read_json(os.path.join(_runs_dir(sd), f"{rid}.json"))
    if d is not None:
        return d
    # 兜底：在默认目录下按文件名找（跨会话没传 store_dir 的情况）
    base = _runs_dir(default_dir())
    try:
        for name in os.listdir(base):
            if name == f"{rid}.json":
                return _read_json(os.path.join(base, name))
    except OSError:
        pass
    return None


def _save_run(state: dict, store_dir: str) -> None:
    sd = store_dir or default_dir()
    rid = _safe_id(str(state.get("run_id") or ""), "run_id")
    with _run_lock(rid):
        _atomic_json(os.path.join(_runs_dir(sd), f"{rid}.json"), state)
        _RUN_INDEX[rid] = sd


def start(spec, *, context: Optional[dict] = None, run_id: str = "",
          store_dir: str = "", handlers=None) -> dict:
    """启动一份工作流，同步跑到结束/中止，返回运行态 dict（含 result）。

    `spec`：流程名 / define 返回的 dict / 节点 list。
    """
    sd = store_dir or default_dir()
    defn = _resolve_defn(spec, sd)
    name = defn["name"]
    hmap = _resolve_handlers(name, defn, handlers)
    rid = _safe_id(str(run_id or "").strip() or ("run-" + uuid.uuid4().hex[:12]),
                   "run_id")
    with _run_lock(rid):
        path = os.path.join(_runs_dir(sd), f"{rid}.json")
        if os.path.exists(path):
            raise ValueError(f"run_id 已存在，请用 resume: {rid}")
        state = _new_state(rid, name, context or {}, defn)
        _save_run(state, sd)
    return _execute_and_save(state, defn, hmap, sd)


def _execute_and_save(state: dict, defn: dict, hmap: dict, sd: str) -> dict:
    rid = str(state.get("run_id") or "")
    try:
        _execute(state, defn, hmap, stop_check=lambda: (
            (_find_run(rid, sd) or {}).get("status") == RUN_CANCELLED))
    except Exception as e:
        state["status"] = RUN_FAILED
        state["result"] = {"ok": False, "error": f"工作流异常: {type(e).__name__}: {e}"}
        state["updated"] = _now()
    with _run_lock(rid):
        disk = _find_run(rid, sd) or {}
        if disk.get("status") == RUN_CANCELLED and state.get("status") != RUN_CANCELLED:
            state["status"] = RUN_CANCELLED
            state["context"] = disk.get("context", state.get("context") or {})
            state["result"] = disk.get("result")
        _save_run(state, sd)
    return state


def status(run_id: str, *, store_dir: str = "") -> Optional[dict]:
    """凭 run_id 查运行态（只读盘，不认内存）。"""
    return _find_run(_safe_id(run_id, "run_id"), store_dir)


def resume(run_id: str, *, store_dir: str = "", handlers=None,
           context: Optional[dict] = None) -> Optional[dict]:
    """凭 run_id 恢复：已 done 的节点不重跑，其余重新执行。"""
    run_id = _safe_id(run_id, "run_id")
    with _run_lock(run_id):
        state = _find_run(run_id, store_dir)
    if state is None:
        return None
    if state["status"] in (RUN_DONE, RUN_CANCELLED):
        return state
    sd = _store_dir_for(run_id, store_dir)
    defn = _resolve_defn(state["name"], sd)
    hmap = _resolve_handlers(state["name"], defn, handlers)
    if context:
        state["context"].update(context)
    # 清掉非终态结果，保留 done 节点（避免重复副作用）
    for nid, s in state["steps"].items():
        if s["status"] != ST_DONE:
            state["steps"][nid] = {"status": ST_PENDING, "attempts": 0,
                                   "result": None, "error": "", "skip_reason": ""}
    state["context"].pop(_FINAL_KEY, None)
    state["status"] = RUN_RUNNING
    state["result"] = None
    return _execute_and_save(state, defn, hmap, sd)


def cancel(run_id: str, reason: str = "", *, store_dir: str = "") -> bool:
    run_id = _safe_id(run_id, "run_id")
    with _run_lock(run_id):
        state = _find_run(run_id, store_dir)
        if state is None:
            return False
        state["status"] = RUN_CANCELLED
        state["context"]["__cancel_reason__"] = str(reason or "")
        state["updated"] = _now()
        _save_run(state, _store_dir_for(run_id, store_dir))
        return True


def list_runs(*, store_dir: str = "") -> List[dict]:
    sd = store_dir or default_dir()
    base = _runs_dir(sd)
    out = []
    try:
        for name in sorted(os.listdir(base)):
            if not name.endswith(".json"):
                continue
            d = _read_json(os.path.join(base, name))
            if d:
                out.append({"run_id": d.get("run_id"), "name": d.get("name"),
                            "status": d.get("status"), "updated": d.get("updated")})
    except OSError:
        pass
    return out


__all__ = [
    "ST_PENDING", "ST_RUNNING", "ST_DONE", "ST_FAILED", "ST_SKIPPED",
    "RUN_RUNNING", "RUN_DONE", "RUN_FAILED", "RUN_CANCELLED",
    "default_dir", "define", "start", "resume", "status", "cancel", "list_runs",
]
