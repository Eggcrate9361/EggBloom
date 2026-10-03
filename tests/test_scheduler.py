# -*- coding: utf-8 -*-
"""scheduler 插件自检：排程计算 / 真实触发 / 重入保护 / 持久化 / 动作接口。

用法（在项目根目录）：
    python tests/test_scheduler.py

不需要 pytest。会真的等几秒来验证 tick 线程确实在派发。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import plugins.scheduler as S  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


class FakeCtx:
    """假 ctx：只记录 emit 与日志，不依赖主程序。"""

    def __init__(self):
        self.events = []
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append((level, msg))

    def emit(self, event_name, **payload):
        self.events.append((event_name, payload))
        return [("fake", None)]


# ══ 1. 排程计算（纯函数）══════════════════════════════════════
print("=== 1. 排程计算 ===")
now = time.time()

t_int = {"kind": "interval", "every": 60, "next_run": 0}
n1 = S.compute_next(t_int, now)
check("interval：首次给出现在之后", n1 > now, f"+{n1 - now:.0f}s")

t_int["next_run"] = now - 300          # 错过 5 个周期
n2 = S.compute_next(t_int, now)
check("interval：错过周期后只跳到未来（不补跑）", n2 > now, f"+{n2 - now:.0f}s")
check("interval：对齐到排程点而非 now+every", abs((n2 - now) % 60) < 1.0 or abs((n2 - now) % 60 - 60) < 1.0,
      f"余数 {(n2 - now) % 60:.1f}s")

from datetime import datetime, timedelta  # noqa: E402

tomorrow_9 = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
nd = S.compute_next({"kind": "daily", "at": "09:30"}, now)
check("daily：算出下一个 09:30", abs(nd - tomorrow_9.timestamp()) < 2, datetime.fromtimestamp(nd).strftime("%m-%d %H:%M"))

nbad = S.compute_next({"kind": "daily", "at": "25:99"}, now)
check("daily：非法时间不空转（退到 1 小时后）", abs(nbad - (now + 3600)) < 2)

nw = S.compute_next({"kind": "weekly", "at": "08:00", "weekday": 0}, now)
check("weekly：结果是周一 08:00", datetime.fromtimestamp(nw).weekday() == 0
      and datetime.fromtimestamp(nw).strftime("%H:%M") == "08:00",
      datetime.fromtimestamp(nw).strftime("%a %H:%M"))

# ══ 2. 真实触发（把下限降到 1 秒以便快速验证）══════════════════
print("\n=== 2. 真实触发 ===")
S.MIN_INTERVAL = 1
ctx = FakeCtx()
S.on_load(ctx)
check("引擎已启动", S.list_tasks()["engine"] is True)

task = S.add_task(name="自检心跳", kind="interval", every=1,
                  event="selftest.heartbeat", payload={"tag": "t1"})
check("任务已创建", "id" in task, str(task.get("id")))
check("next_run 已排定", float(task.get("next_run") or 0) > 0)

deadline = time.time() + 6
while time.time() < deadline and len([e for e in ctx.events if e[0] == "selftest.heartbeat"]) < 2:
    time.sleep(0.2)

hb = [e for e in ctx.events if e[0] == "selftest.heartbeat"]
check("tick 线程真的派发了事件（≥2 次）", len(hb) >= 2, f"收到 {len(hb)} 次")
check("payload 原样送达", hb and hb[0][1].get("tag") == "t1", str(hb[0][1] if hb else None))
check("payload 带上了任务元信息", hb and hb[0][1].get("task_id") == task["id"], str(hb[0][1] if hb else None))

row = next((t for t in S.list_tasks()["tasks"] if t["id"] == task["id"]), {})
check("runs 计数在涨", int(row.get("runs") or 0) >= 2, f"runs={row.get('runs')}")
check("last_run 已记录", float(row.get("last_run") or 0) > 0)
check("next_run 已推进到未来", float(row.get("next_run") or 0) > time.time() - 1)
check("无错误", not row.get("last_error"), row.get("last_error") or "")

# ══ 3. 重入保护 ══════════════════════════════════════════════
print("\n=== 3. 重入保护 ===")
slow = S.add_task(name="慢任务", kind="interval", every=1, event="selftest.slow")
orig_emit = ctx.emit


def slow_emit(event_name, **payload):
    if event_name == "selftest.slow":
        time.sleep(2.5)                  # 模拟订阅方很慢（如调 AI）
    return orig_emit(event_name, **payload)


ctx.emit = slow_emit
time.sleep(4)
ctx.emit = orig_emit
slow_row = next((t for t in S.list_tasks()["tasks"] if t["id"] == slow["id"]), {})
check("慢任务被跳过而不是堆积", int(slow_row.get("skipped") or 0) >= 1,
      f"skipped={slow_row.get('skipped')}, runs={slow_row.get('runs')}")
S.remove_task(slow["id"])

# ══ 4. 立即执行 / 启停 / 增删改 ═══════════════════════════════
print("\n=== 4. 动作接口 ===")
before = len(ctx.events)
S.run_now(task["id"])
time.sleep(0.8)
check("run_now 立即派发", len(ctx.events) > before, f"{before} → {len(ctx.events)}")

r = S.PLUGIN_ACTIONS["toggle"]({"id": task["id"]})
check("toggle 关闭成功", r.get("enabled") is False, str(r))
cnt = len(ctx.events)
time.sleep(2.2)
check("停用后不再派发", len(ctx.events) == cnt, f"{cnt} → {len(ctx.events)}")

S.PLUGIN_ACTIONS["toggle"]({"id": task["id"]})
r = S.PLUGIN_ACTIONS["update"]({"id": task["id"], "name": "改名了"})
check("update 改名生效", r.get("name") == "改名了", str(r.get("name")))

r = S.PLUGIN_ACTIONS["add"]({"name": "坏任务", "kind": "daily", "at": "99:99"})
check("add 校验非法时间", "error" in r, str(r))

r = S.PLUGIN_ACTIONS["list"]({})
check("list 返回 task/engine/stats", all(k in r for k in ("tasks", "engine", "stats")), str(list(r.keys())))

r = S.PLUGIN_ACTIONS["remove"]({"id": task["id"]})
check("remove 生效", r.get("removed") == 1, str(r))

# ══ 5. 持久化 ════════════════════════════════════════════════
print("\n=== 5. 持久化 ===")
check("状态文件已写出", os.path.isfile(S._STATE_PATH), S._STATE_PATH)
persist = S.add_task(name="要活过重启的", kind="daily", at="23:59")
S.on_unload(ctx)
reloaded = S._load()
check("重启后任务还在", any(t.get("name") == "要活过重启的" for t in reloaded),
      f"文件里 {len(reloaded)} 个任务")

# 清理自检痕迹
with S._lock:
    S._tasks = []
S._save()
try:
    os.remove(S._STATE_PATH)
except OSError:
    pass

print("\n" + "=" * 46)
print(f"结果：{'全部通过' if not fails else '失败 ' + str(fails)}")
sys.exit(1 if fails else 0)
