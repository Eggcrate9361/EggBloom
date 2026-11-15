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

# 1.6.0：任务失败后的指数退避（秒）。第 n 次连续失败 → base * 2**(n-1)，封顶 CAP。
BACKOFF_BASE = 2.0
BACKOFF_CAP = 300.0

# 1.6.0：至少 1 个默认任务（任务表为空时补）。用 cron 每天 03:00 自检，
# 事件名独立，不干扰 interval/daily/weekly 三种 kind，也不会在短测试窗口里触发。
DEFAULT_TASKS = [
    {"name": "每日自检", "kind": "cron", "cron": "0 3 * * *",
     "event": "scheduler.daily_selfcheck", "payload": {}},
]

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


# ══════════════════════════════════════════════════════════════
#  1.6.0：cron 表达式（5 段）解析
#    分 时 日 月 周（0=周日..6=周六，7 也接受=周日）
#    支持 * / a-b / a,b / *／n / a-b／n / a／n
#    dom 与 dow 同时受限时采用 AND（两者都命中才触发）——确定性优先的简化。
# ══════════════════════════════════════════════════════════════

def _cron_field(field: str, lo: int, hi: int):
    """一段 → 允许值集合；非法返回 None。"""
    field = str(field or "").strip()
    if not field:
        return None
    out = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            return None
        step = 1
        base = part
        if "/" in part:
            base, _, s = part.partition("/")
            try:
                step = int(s)
            except (ValueError, AttributeError):
                return None
            if step <= 0:
                return None
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            try:
                start, end = int(a), int(b)
            except (ValueError, AttributeError):
                return None
        else:
            try:
                start = int(base)
            except (ValueError, AttributeError):
                return None
            end = hi if "/" in part else start
        if start < lo or end > hi or start > end:
            return None
        out.update(range(start, end + 1, step))
    return out or None


def _cron_dow_to_py(values) -> set:
    """cron 周(0=周日..7=周日) → python weekday(0=周一..6=周日)。"""
    return {(v % 7 + 6) % 7 for v in values}


def _cron_next(expr: str, now_ts: float) -> float:
    """算出 cron 表达式下一次匹配的 unix 时间戳（严格晚于 now_ts）。

    按「天 → 时 → 分」逐级跳，不对每分钟做百万次循环；最多找 4 年。
    找不到 / 表达式非法 → 退到 1 小时后（与非法 `at` 一致，不空转）。
    """
    parts = str(expr or "").split()
    if len(parts) != 5:
        return now_ts + 3600
    minute = _cron_field(parts[0], 0, 59)
    hour = _cron_field(parts[1], 0, 23)
    dom = _cron_field(parts[2], 1, 31)
    month = _cron_field(parts[3], 1, 12)
    dow = _cron_field(parts[4], 0, 7)
    if None in (minute, hour, dom, month, dow):
        return now_ts + 3600
    dow_py = _cron_dow_to_py(dow)

    now = datetime.fromtimestamp(now_ts)
    cand = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = cand + timedelta(days=366 * 4)
    while cand <= limit:
        if (cand.month not in month or cand.day not in dom
                or cand.weekday() not in dow_py):
            cand = (cand + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if cand.hour not in hour:
            cand = cand.replace(minute=0) + timedelta(hours=1)
            continue
        if cand.minute not in minute:
            cand += timedelta(minutes=1)
            continue
        return cand.timestamp()
    return now_ts + 3600


def compute_next(task: dict, now_ts: float) -> float:
    """算出该任务下一次应该触发的 unix 时间戳。

    一律基于"排程点"推算而不是 now + 间隔 —— 避免执行耗时累积成漂移。
    interval / daily / weekly 三种 kind 行为保持不变；1.6.0 新增 cron。
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

    if kind == "cron":                        # 1.6.0：5 段 cron 表达式
        return _cron_next(task.get("cron") or task.get("at") or "", now_ts)

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


# ── 1.6.0：失败指数退避（纯函数，便于离线单测）────────────────────
def backoff_delay(consecutive_failures: int) -> float:
    """第 n 次连续失败（n>=1）的退避秒数：base * 2**(n-1)，封顶 CAP。"""
    n = max(1, int(consecutive_failures or 1))
    return min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (n - 1)))


def compute_next_after_error(task: dict, now_ts: float,
                             consecutive_failures: int) -> float:
    """派发失败后的下一次时间 = 正常排程 与 (now + 指数退避) 取较晚者。"""
    base = compute_next(task, now_ts)
    return max(base, now_ts + backoff_delay(consecutive_failures))


def ensure_default_tasks() -> int:
    """至少 1 个默认任务：任务表为空时补进 DEFAULT_TASKS，返回补的条数。

    不持有 `_lock` 调 `add_task`（后者自己会取锁），避免与 `threading.Lock`
    （非可重入）死锁。供 `on_load()` 与 WebUI「恢复默认」调用。
    """
    with _lock:
        not_empty = bool(_tasks)
    if not_empty:
        return 0
    n = 0
    for spec in DEFAULT_TASKS:
        if "error" not in add_task(**spec):
            n += 1
    return n


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
                task["last_cost"] = round(cost, 2)
                now2 = time.time()
                if err:
                    # 1.6.0：失败指数退避，连续失败越久推得越远（成功即清零）
                    fails = int(task.get("consecutive_failures") or 0) + 1
                    task["consecutive_failures"] = fails
                    task["last_error"] = err
                    task["next_run"] = compute_next_after_error(task, now2, fails)
                else:
                    task["consecutive_failures"] = 0
                    task["last_error"] = ""
                    task["next_run"] = compute_next(task, now2)
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
             payload: dict | None = None, enabled: bool = True,
             cron: str = "") -> dict:
    # 1.6.0：新增 cron kind；interval/daily/weekly 三种保持原样
    kind = kind if kind in ("interval", "daily", "weekly", "cron") else "interval"
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
        "cron": str(cron or ""),
        "event": (event or "scheduler.tick").strip(),
        "payload": payload or {},
        "enabled": bool(enabled),
        "created_at": time.time(),
        "last_run": 0,
        "runs": 0,
        "skipped": 0,
        "last_error": "",
        "consecutive_failures": 0,
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
        for k in ("name", "kind", "every", "at", "weekday", "cron",
                  "event", "payload", "enabled"):
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
    ensure_default_tasks()                 # 1.6.0：至少 1 个默认任务
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
        cron=p.get("cron") or "",
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
