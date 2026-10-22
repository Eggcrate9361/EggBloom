# -*- coding: utf-8 -*-
"""tests/_quiet_exit.py —— 让测试进程干净退出（停掉 bot_core 带起来的守护线程）。

## 为什么需要

`import bot_core` 不只是导入 —— 它会**真的启动运行时**：插件系统初始化、调度器
tick/worker 线程、proactive_talk 心跳、ai-worker 线程池。实测导入后残留 6 个
守护线程：

    ['proactive_talk', 'scheduler_tick', 'scheduler_worker',
     'ai-worker-0', 'ai-worker-1', 'ai-worker-2']

这些线程是 daemon，CPython 在解释器退出时不会等它们，但**它们还在往 stdout
写日志**。于是出现这个非常坑的现象（2026-10-22 全量回归踩到）：

    ========================================================
    通过 32 / 失败 0            ← 测试全过
    Fatal Python error: _enter_buffered_busy: could not acquire
    lock for <_io.BufferedWriter name='<stdout>'>
    ...daemon threads

    → 进程退出码 0xC0000409（3221226505）

**测试本身是绿的，退出码是红的**。任何 CI / runner 按退出码判定，都会把
「全过」报成「崩溃」。`test_history_ledger` / `test_search_parsing` 都中招。

## 用法

在测试文件里 **`import bot_core` 之后**加一行：

    from _quiet_exit import quiet_exit
    quiet_exit()

它注册 atexit：先停插件线程（调 `on_unload`），再给线程一点时间收尾。
必须在 import bot_core 之后调用 —— 之前插件还没初始化，拿不到句柄。

## 为什么不是「修 bot_core 让它别起线程」

因为那不是 bug：桌面端就是要这些线程跑起来的。测试是**借用了生产的启动路径**，
所以收尾的责任在测试侧。改生产代码去迁就测试，会让真跑起来的桌面端变哑。
"""
import atexit
import sys
import threading
import time

_registered = False


def quiet_exit(wait_sec: float = 0.35) -> None:
    """注册退出钩子：停掉 import bot_core 带起来的守护线程。幂等。"""
    global _registered
    if _registered:
        return
    _registered = True
    atexit.register(_do)

    def _do_wrapper():
        _do(wait_sec)

    # 用带 wait 的版本替换（atexit 已注册 _do，这里改成真正要跑的）
    try:
        atexit.unregister(_do)
    except Exception:
        pass
    atexit.register(_do_wrapper)


def _do(wait_sec: float = 0.35) -> None:
    """停插件线程 → 等它们退出 → 冲刷新 stdout。全流程吞异常。"""
    # ── 1. 停插件（scheduler / proactive_talk 都实现了 on_unload）──
    try:
        pm = sys.modules.get("plugins.plugin_manager")
        mgr = None
        if pm is not None:
            getter = getattr(pm, "_INSTANCE", None) or getattr(pm, "_instance", None)
            mgr = getter
        if mgr is None:
            bc = sys.modules.get("bot_core")
            if bc is not None and hasattr(bc, "get_plugin_manager"):
                mgr = bc.get_plugin_manager()
        if mgr is not None and hasattr(mgr, "unload_all"):
            mgr.unload_all()
    except Exception:
        pass

    # ── 2. 直接调插件自己的停机函数（unload_all 不可用时的兜底）──
    for modname, fn in (
        ("plugins.scheduler", "_stop_engine"),
        ("plugins.proactive_talk", "_stop_thread"),
    ):
        try:
            m = sys.modules.get(modname)
            if m is not None:
                f = getattr(m, fn, None)
                if callable(f):
                    f()
        except Exception:
            pass

    # ── 3. 等非主线程退出（最多 wait_sec）──
    deadline = time.time() + max(0.0, wait_sec)
    while time.time() < deadline:
        alive = [t for t in threading.enumerate()
                 if t is not threading.main_thread() and t.is_alive()]
        if not alive:
            break
        time.sleep(0.02)

    # ── 4. 冲刷新，减少 daemon 抢 stdout 锁的窗口 ──
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass