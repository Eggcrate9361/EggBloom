# -*- coding: utf-8 -*-
"""plugins/scheduler —— cron / 指数退避 / 默认任务（1.6.0）单测。

钉死：
  A. compute_next() 新增 5 段 cron 解析（分 时 日 月 周，*、范围、列表、步长）
  B. 非法 cron 不空转（退到 1 小时后）
  C. interval / daily / weekly 三种原 kind 行为保持不变
  D. 失败的指数退避（backoff_delay / compute_next_after_error）
  E. add_task(kind="cron") / update_task 接受 cron；至少 1 个默认任务
  F. 引擎接线：派发失败后 consecutive_failures 累加、next_run 被退避推后

除了 F 用极短的引擎集成（临时 state、可注入 ctx），其余都是纯函数/离线，
不联网、不碰真实模型/ffmpeg。运行：python tests/test_scheduler_cron.py
"""
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import plugins.scheduler as S  # noqa: E402

TMP = tempfile.mkdtemp(prefix="sched_cron_test_")

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


def _dt(expr, y, mo, d, h, mi):
    return datetime.fromtimestamp(S._cron_next(expr, datetime(y, mo, d, h, mi).timestamp()))


# 保存模块全局，finally 里逐一还原（避免污染同进程后续用例）
_saved = {k: getattr(S, k) for k in (
    "_STATE_PATH", "_tasks", "_ctx", "_queue", "_running", "_threads",
    "_stop", "_stats", "MIN_INTERVAL", "TICK")}
engine_started = False

try:
    S._STATE_PATH = os.path.join(TMP, "scheduler.json")
    S._tasks = []
    S._ctx = None

    # ══ A. cron 5 段解析（固定时间戳，确定性断言）════════════════════
    x = _dt("*/5 * * * *", 2026, 1, 2, 10, 3)
    check("cron */5 → 下一个 5 分刻度", (x.hour, x.minute) == (10, 5), x.strftime("%H:%M"))
    x = _dt("0 9 * * *", 2026, 1, 2, 10, 0)
    check("cron 每天 09:00（已过 → 明天）", (x.day, x.hour, x.minute) == (3, 9, 0), str(x))
    x = _dt("0 9 * * *", 2026, 1, 2, 8, 0)
    check("cron 每天 09:00（未到 → 今天）", (x.day, x.hour, x.minute) == (2, 9, 0), str(x))
    x = _dt("30 2 * * 1", 2026, 1, 2, 0, 0)     # 周五 → 下周一 02:30
    check("cron 周字段 1=周一", (x.day, x.hour, x.minute, x.weekday()) == (5, 2, 30, 0), str(x))
    x = _dt("0 0 1 * *", 2026, 1, 15, 12, 0)
    check("cron 日字段 1=每月 1 号", (x.month, x.day, x.hour) == (2, 1, 0), str(x))
    x = _dt("15,45 8-10 * * *", 2026, 1, 2, 7, 0)
    check("cron 列表 + 范围（8:15）", (x.hour, x.minute) == (8, 15), x.strftime("%H:%M"))
    x = _dt("0 12 1-5 * *", 2026, 1, 6, 0, 0)   # 1/6 是周二，日 1-5 不命中 → 2/1
    check("cron 日范围跨月", (x.month, x.day) == (2, 1), str(x))

    # ══ B. 非法 / 段数不对 → 退到 1 小时后，不空转 ═══════════════════
    now = datetime(2026, 1, 2, 10, 0).timestamp()
    check("cron 段数不对 → +3600", abs(S._cron_next("* * *", now) - (now + 3600)) < 1)
    check("cron 非法字符 → +3600", abs(S._cron_next("x y z a b", now) - (now + 3600)) < 1)
    check("cron 越界字段 → +3600", abs(S._cron_next("99 * * * *", now) - (now + 3600)) < 1)

    # ══ C. 三种原 kind 保持不变 ═════════════════════════════════════
    n_int = S.compute_next({"kind": "interval", "every": 60, "next_run": 0}, now)
    check("interval 首次在未来", n_int > now)
    exp = datetime(2026, 1, 2, 9, 30)
    if exp.timestamp() <= now:
        exp += timedelta(days=1)
    nd = S.compute_next({"kind": "daily", "at": "09:30"}, now)
    check("daily 仍是下一个 09:30", abs(nd - exp.timestamp()) < 2, str(datetime.fromtimestamp(nd)))
    nw = S.compute_next({"kind": "weekly", "at": "08:00", "weekday": 0}, now)
    check("weekly 仍是周一 08:00",
          datetime.fromtimestamp(nw).weekday() == 0
          and datetime.fromtimestamp(nw).strftime("%H:%M") == "08:00")
    check("非法 at 仍退到 1 小时后",
          abs(S.compute_next({"kind": "daily", "at": "25:99"}, now) - (now + 3600)) < 1)

    # compute_next 的 cron 分支（kind=cron 走 expr）
    nc = S.compute_next({"kind": "cron", "cron": "0 9 * * *"}, now)
    check("compute_next(kind=cron) 用 cron 字段",
          datetime.fromtimestamp(nc).strftime("%H:%M") == "09:00", str(datetime.fromtimestamp(nc)))
    check("cron 任务不带 at 也不会被 _parse_at 提前拦掉", nc > now + 3600 or nc > now)

    # ══ D. 失败指数退避（纯函数）════════════════════════════════════
    check("backoff 1/2/3 次 = 2/4/8", [S.backoff_delay(n) for n in (1, 2, 3)] == [2.0, 4.0, 8.0])
    check("backoff 封顶 300", S.backoff_delay(100) == 300.0)
    t = {"kind": "interval", "every": 5, "next_run": 0}
    e1 = S.compute_next_after_error(t, now, 1)
    e3 = S.compute_next_after_error(t, now, 3)
    check("失败 1 次至少推后 2s", e1 >= now + 2, e1 - now)
    check("失败 3 次推得更远（8s）", e3 >= now + 8 and e3 > e1, (e1 - now, e3 - now))
    check("退避不早于正常排程", e1 >= S.compute_next(t, now))

    # ══ E. add_task(kind=cron) + 默认任务 ═══════════════════════════
    ct = S.add_task(name="cron 任务", kind="cron", cron="*/5 * * * *")
    check("add_task 接受 cron kind", ct.get("kind") == "cron" and "error" not in ct, ct)
    check("cron 字段存进任务", ct.get("cron") == "*/5 * * * *")
    check("cron 任务 next_run 已排定", float(ct.get("next_run") or 0) > 0)
    check("新任务带 consecutive_failures=0", ct.get("consecutive_failures") == 0)
    up = S.update_task(ct["id"], cron="0 3 * * *")
    check("update_task 接受 cron", up.get("cron") == "0 3 * * *", up.get("cron"))
    check("DEFAULT_TASKS 至少 1 个", len(S.DEFAULT_TASKS) >= 1)

    S._tasks = []
    n_def = S.ensure_default_tasks()
    check("★ 空任务表补至少 1 个默认任务", n_def >= 1 and len(S._tasks) >= 1, (n_def, len(S._tasks)))
    check("默认任务含 cron kind", any(t.get("kind") == "cron" for t in S._tasks))
    check("ensure_default_tasks 幂等（非空不再补）", S.ensure_default_tasks() == 0)

    # ══ F. 引擎接线：派发失败 → 指数退避 ═════════════════════════════
    class RaisingCtx:
        def log(self, msg, level="info"):
            pass

        def emit(self, event_name, **payload):
            raise RuntimeError("派发失败")

    # 清干净状态文件与全局，再启引擎
    try:
        os.remove(S._STATE_PATH)
    except OSError:
        pass
    S._tasks = []
    S._ctx = None
    S._queue = queue.Queue()
    S._running = set()
    S._threads = {}
    S._stop = threading.Event()
    S._stats = {"ticked": 0, "fired": 0, "skipped": 0, "errors": 0, "last_tick": 0}
    S.MIN_INTERVAL = 1
    S.TICK = 0.1
    ctx = RaisingCtx()
    S.on_load(ctx)
    engine_started = True
    fail_task = S.add_task(name="会失败的任务", kind="interval", every=1, event="test.fail")
    with S._lock:                      # 让它立刻到期，缩短等待
        fail_task["next_run"] = time.time() - 1
    deadline = time.time() + 5
    row = {}
    while time.time() < deadline:
        with S._lock:
            row = dict(next((t for t in S._tasks if t["id"] == fail_task["id"]), {}))
        if int(row.get("runs") or 0) >= 1:
            break
        time.sleep(0.05)
    check("引擎真的派发过（失败也算 runs）", int(row.get("runs") or 0) >= 1, row.get("runs"))
    check("★ 失败后 consecutive_failures 累加", int(row.get("consecutive_failures") or 0) >= 1,
          row.get("consecutive_failures"))
    check("★ 失败后 next_run 被退避推到未来",
          float(row.get("next_run") or 0) > time.time(), row.get("next_run"))
    check("失败原因记进 last_error", bool(row.get("last_error")), row.get("last_error"))
    S.on_unload(ctx)
    engine_started = False
finally:
    if engine_started:
        try:
            S.on_unload(S._ctx)
        except Exception:
            pass
    for k, v in _saved.items():
        setattr(S, k, v)
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
