# -*- coding: utf-8 -*-
"""scheduler —— EggBloom 统一调度器（真插件 ABI v2）

## 为什么存在

在此之前，项目里有三套各自为政的定时器：

    bot_core.py            threading.Timer，管消息缓冲合并（3/5/15 秒）
    proactive_talk         自己的 _heartbeat_loop，管主动开口（默认 900 秒）
    slang_learner          独立间隔循环，管抓热词

三处各自 sleep、互不可见。再加"定时任务"功能就是第四套。

**本插件的定位：全项目只留一处 sleep。** 它自己不做事，只负责「到点了就广播事件」，
具体做什么由订阅方（其他插件）在 `on_event` 里决定 —— 这样调度与业务解耦。

## 设计要点

- **单 tick 线程 + 单 worker 线程**：tick 只做"到点了吗"的判断并投递队列，
  真正的派发在 worker 里做 —— 订阅方阻塞也不会拖慢调度（proactive_talk 要调 AI，可能几秒）。
- **同一任务不重入**：上一轮还没跑完就跳过这一轮，并在结果里标记為 skipped。
- **next_run 由排程反推**，不是 now+interval，所以不会累积漂移。
- **任务持久化**在 plugins/state/scheduler.json，重启不丢。

## 任务类型

    interval   每 N 秒（N ≥ 5）
    daily      每天 HH:MM
    weekly     每周 <weekday> HH:MM（0=周一 … 6=周日）

## 事件约定

任务到点会广播：

    ctx.emit(task["event"], **task["payload"])

订阅方：

    def on_event(ctx, event_name, payload):
        if event_name == "scheduler.heartbeat":
            ...
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from datetime import datetime, timedelta

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "scheduler",
    "version": "1.0.0",
    "api_version": 2,
    "author": "EggBloom",
    "description": "统一调度器：全项目只留一处 sleep，到点广播事件",
    "dependencies": [],
    "priority": 10,          # 数字小 = 先初始化，保证消费者加载时它已就绪
    "default_enabled": True,
}

MIN_INTERVAL = 5             # 间隔任务下限（秒），防止把 CPU 打满
TICK = 1.0                   # 调度精度（秒）

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_PATH = os.path.join(os.path.dirname(_PLUGIN_DIR), "state", "scheduler.json")

_lock = threading.Lock()
_tasks: list = []
_running: set = set()          # 正在派发的任务 id
_queue: "queue.Queue[str]" = queue.Queue()
_threads: dict = {}
_stop = threading.Event()
_ctx = None
_stats = {"ticked": 0, "fired": 0, "skipped": 0, "errors": 0, "last_tick": 0}


# ══════════════════════════════════════════════════════════════
#  持久化（自己管自己的 state 文件，与 ctx.get_state 同一路径）
# ══════════════════════════════════════════════════════════════

def _load() -> list:
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        tasks = data.get("tasks") if isinstance(data, dict) else data
        return list(tasks or [])
    except (OSError, ValueError):
        return []


def _save() -> bool:
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"tasks": _tasks}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _STATE_PATH)
        return True
    except OSError:
        return False


# ══════════════════════════════════════════════════════════════
#  排程计算
# ══════════════════════════════════════════════════════════════

def _parse_at(at: str):
    """'HH:MM' → (hour, minute)；非法返回 None。"""
    try:
        hh, mm = str(at).strip().split(":")
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, AttributeError):
        pass
    return None


def compute_next(task: dict, now_ts: float) -> float:
    """算出该任务下一次应该触发的 unix 时间戳。

    一律基于"排程点"推算而不是 now + 间隔 —— 避免执行耗时累积成漂移。
    """
    kind = task.get("kind") or "interval"

    if kind == "interval":
        every = max(MIN_INTERVAL, int(task.get("every") or 60))
        base = float(task.get("next_run") or 0) or now_ts
        nxt = base + every
        if nxt <= now_ts:                      # 断点/长暂停后追赶，但只跳到未来
            missed = int((now_ts - nxt) // every) + 1
            nxt += missed * every
        return nxt

    at = _parse_at(task.get("at") or "")
    if at is None:
        return now_ts + 3600                   # 配置坏了：1 小时后再试，不空转
    h, m = at
    now = datetime.fromtimestamp(now_ts)

    if kind == "daily":
        cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)
        return cand.timestamp()

    if kind == "weekly":
        wd = int(task.get("weekday") or 0) % 7      # 0=周一
        cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
        delta = (wd - now.weekday()) % 7
        cand += timedelta(days=delta)
        if cand <= now:
            cand += timedelta(days=7)
        return cand.timestamp()

    return now_ts + 3600


# ══════════════════════════════════════════════════════════════
#  引擎
# ══════════════════════════════════════════════════════════════

def _log(msg: str, level: str = "info"):
    if _ctx is not None:
        try:
            _ctx.log(msg, level)
            return
        except Exception:
            pass
    print(f"[scheduler] [{level}] {msg}")


def _tick_loop():
    while not _stop.is_set():
        try:
            now = time.time()
            _stats["ticked"] += 1
            _stats["last_tick"] = now
            due = []
            with _lock:
                for t in _tasks:
                    if not t.get("enabled", True):
                        continue
                    nxt = float(t.get("next_run") or 0)
                    if nxt <= 0:                       # 新任务：登记下一次
                        t["next_run"] = compute_next(t, now)
                        continue
                    if nxt <= now:
                        due.append(t)
            for t in due:
                if t["id"] in _running:
                    _stats["skipped"] += 1
                    with _lock:
                        t["next_run"] = compute_next(t, time.time())
                        t["skipped"] = int(t.get("skipped") or 0) + 1
                    _save()
                    _log(f"任务「{t.get('name')}」上一轮未结束，跳过本轮", "warn")
                    continue
                _queue.put(t["id"])
        except Exception as e:                          # 调度线程绝不能死
            _stats["errors"] += 1
            _log(f"tick 异常: {e}", "error")
        _stop.wait(TICK)


def _worker_loop():
    while not _stop.is_set():
        try:
            tid = _queue.get(timeout=0.5)
        except queue.Empty:
            continue
        with _lock:
            task = next((t for t in _tasks if t["id"] == tid), None)
        if not task or not task.get("enabled", True):
            continue
        _running.add(tid)
        started = time.time()
        err = ""
        try:
            if _ctx is None:
                err = "ctx 未就绪"
            else:
                _ctx.emit(task.get("event") or "scheduler.tick", **{
                    "task_id": task["id"],
                    "task_name": task.get("name") or "",
                    **(task.get("payload") or {}),
                })
                _stats["fired"] += 1
        except Exception as e:
            err = str(e)
            _stats["errors"] += 1
        finally:
            _running.discard(tid)
            cost = time.time() - started
            with _lock:
                task["last_run"] = started
                task["runs"] = int(task.get("runs") or 0) + 1
                task["last_error"] = err
                task["last_cost"] = round(cost, 2)
                task["next_run"] = compute_next(task, time.time())
            _save()
            if err:
                _log(f"任务「{task.get('name')}」派发失败: {err}", "warn")
            elif cost > 3:
                _log(f"任务「{task.get('name')}」耗时 {cost:.1f}s（订阅方偏慢，建议自行开线程）", "warn")


def _start_engine():
    if _threads.get("tick") and _threads["tick"].is_alive():
        return
    _stop.clear()
    for key, fn in (("tick", _tick_loop), ("worker", _worker_loop)):
        th = threading.Thread(target=fn, name=f"scheduler_{key}", daemon=True)
        th.start()
        _threads[key] = th
    _log("引擎已启动（1 tick 线程 + 1 worker 线程）")


def _stop_engine():
    _stop.set()
    for th in list(_threads.values()):
        try:
            th.join(timeout=2)
        except Exception:
            pass
    _threads.clear()
    _log("引擎已停止")


# ══════════════════════════════════════════════════════════════
#  任务 CRUD（同时供 PLUGIN_ACTIONS 与内部使用）
# ══════════════════════════════════════════════════════════════

def add_task(name: str, kind: str = "interval", every: int = 60, at: str = "",
             weekday: int = 0, event: str = "scheduler.tick",
             payload: dict | None = None, enabled: bool = True) -> dict:
    kind = kind if kind in ("interval", "daily", "weekly") else "interval"
    if kind == "interval":
        every = max(MIN_INTERVAL, int(every or 60))
    if kind in ("daily", "weekly") and _parse_at(at) is None:
        return {"error": "at 必须是 HH:MM"}
    task = {
        "id": f"t{int(time.time() * 1000) % 100000000}{len(_tasks) % 100:02d}",
        "name": (name or "").strip() or "未命名任务",
        "kind": kind,
        "every": every,
        "at": at,
        "weekday": int(weekday) % 7,
        "event": (event or "scheduler.tick").strip(),
        "payload": payload or {},
        "enabled": bool(enabled),
        "created_at": time.time(),
        "last_run": 0,
        "runs": 0,
        "skipped": 0,
        "last_error": "",
        "next_run": 0,
    }
    with _lock:
        task["next_run"] = compute_next(task, time.time())
        _tasks.append(task)
    _save()
    return task


def update_task(task_id: str, **fields) -> dict:
    with _lock:
        task = next((t for t in _tasks if t["id"] == task_id), None)
        if not task:
            return {"error": f"没有任务 {task_id}"}
        for k in ("name", "kind", "every", "at", "weekday", "event", "payload", "enabled"):
            if k in fields and fields[k] is not None:
                task[k] = fields[k]
        if task["kind"] == "interval":
            task["every"] = max(MIN_INTERVAL, int(task.get("every") or 60))
        task["next_run"] = compute_next(task, time.time())
        snapshot = dict(task)
    _save()
    return snapshot


def remove_task(task_id: str) -> dict:
    global _tasks
    with _lock:
        before = len(_tasks)
        _tasks = [t for t in _tasks if t["id"] != task_id]
        removed = before - len(_tasks)
    if removed:
        _save()
    return {"removed": removed}


def run_now(task_id: str) -> dict:
    with _lock:
        task = next((t for t in _tasks if t["id"] == task_id), None)
    if not task:
        return {"error": f"没有任务 {task_id}"}
    if task_id in _running:
        return {"error": "该任务正在执行中"}
    _queue.put(task_id)
    return {"queued": task_id, "event": task.get("event")}


def list_tasks() -> dict:
    now = time.time()
    with _lock:
        rows = []
        for t in _tasks:
            r = dict(t)
            nxt = float(t.get("next_run") or 0)
            r["next_in"] = max(0, int(nxt - now)) if nxt else None
            r["next_at"] = (datetime.fromtimestamp(nxt).strftime("%m-%d %H:%M:%S")
                            if nxt else "—")
            r["running"] = t["id"] in _running
            rows.append(r)
    return {
        "tasks": rows,
        "now": now,
        "running": sorted(_running),
        "stats": dict(_stats),
        "engine": bool(_threads.get("tick") and _threads["tick"].is_alive()),
    }


# ══════════════════════════════════════════════════════════════
#  ABI 钩子
# ══════════════════════════════════════════════════════════════

def on_load(ctx):
    global _ctx, _tasks
    _ctx = ctx
    _tasks = _load()
    for t in _tasks:                       # 重启后重算下一次（不补跑错过的）
        t["next_run"] = compute_next(t, time.time())
    _save()
    _start_engine()
    enabled = len([t for t in _tasks if t.get("enabled", True)])
    ctx.log(f"已加载 {len(_tasks)} 个任务（启用 {enabled} 个）")


def on_unload(ctx):
    _stop_engine()


def on_enable(ctx):
    global _ctx
    _ctx = ctx
    _start_engine()


def on_disable(ctx):
    _stop_engine()


PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_unload": on_unload,
    "on_enable": on_enable,
    "on_disable": on_disable,
}


# ══════════════════════════════════════════════════════════════
#  WebUI 动作（/api/plugins/action → plugin_id=scheduler）
# ══════════════════════════════════════════════════════════════

def _a_list(params):
    return list_tasks()


def _a_add(params):
    p = params or {}
    r = add_task(
        name=p.get("name") or "",
        kind=p.get("kind") or "interval",
        every=int(p.get("every") or 60),
        at=p.get("at") or "",
        weekday=int(p.get("weekday") or 0),
        event=p.get("event") or "scheduler.tick",
        payload=p.get("payload") or {},
        enabled=bool(p.get("enabled", True)),
    )
    return r if "error" in r else {"ok": True, "task": r}


def _a_update(params):
    p = dict(params or {})
    tid = p.pop("id", None) or p.pop("task_id", None)
    if not tid:
        return {"error": "缺少 id"}
    return update_task(tid, **p)


def _a_remove(params):
    tid = (params or {}).get("id") or (params or {}).get("task_id")
    if not tid:
        return {"error": "缺少 id"}
    return remove_task(tid)


def _a_run_now(params):
    tid = (params or {}).get("id") or (params or {}).get("task_id")
    if not tid:
        return {"error": "缺少 id"}
    return run_now(tid)


def _a_toggle(params):
    tid = (params or {}).get("id")
    if not tid:
        return {"error": "缺少 id"}
    with _lock:
        task = next((t for t in _tasks if t["id"] == tid), None)
        if not task:
            return {"error": f"没有任务 {tid}"}
        task["enabled"] = not task.get("enabled", True)
        val = task["enabled"]
        task["next_run"] = compute_next(task, time.time())
    _save()
    return {"ok": True, "id": tid, "enabled": val}


def execute_action(action, params=None):
    return PLUGIN_ACTIONS.get(action, lambda p: {"error": f"未知动作 {action}"})(params or {})


PLUGIN_ACTIONS = {
    "list": _a_list,
    "add": _a_add,
    "update": _a_update,
    "remove": _a_remove,
    "run_now": _a_run_now,
    "toggle": _a_toggle,
}

PLUGIN_UI = {"page": "plugins/scheduler/ui.html", "title": "定时任务"}
