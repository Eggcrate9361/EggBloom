# -*- coding: utf-8 -*-
"""
T7 批二 · P14 卸载 fail-closed 验证（不变量 A4 / A14）。

问题：`unload()` 原先直接调 on_unload 并摘注册表 —— 若此刻还有钩子在执行
（web_operator / scheduler 这类有副作用插件），它的线程被悬在半空：模块没了、
日志写不进、dispose 从未"完全停稳"。

对策：每个钩子调用计入**在途**；卸载时若有在途且未 force → **拒绝卸载**
（失败关闭：宁可拒绝，也不静默放行一次不安全的卸载）。

运行：python -m unittest tests.test_unload_fail_closed -v
"""
import os
import sys
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import plugins.plugin_manager as pmmod  # noqa: E402
from plugins.plugin_manager import PluginManager  # noqa: E402


class _FakeModule:
    def __init__(self):
        self.unloaded = 0

    def on_unload(self):
        self.unloaded += 1


def mk(name, fn, priority=50):
    pm = PluginManager.__new__(PluginManager)
    mod = _FakeModule()
    pm._plugins = {name: {
        "tier": "legacy", "module": mod,
        "meta": {"name": name, "enabled": True, "priority": priority},
        "hooks": {"before_reply": fn},
    }}
    return pm, mod


class TestUnloadFailClosed(unittest.TestCase):
    def setUp(self):
        with pmmod._INFLIGHT_CV:
            pmmod._INFLIGHT.clear()

    tearDown = setUp

    # ── 在途计数 ──
    def test_inflight_returns_to_zero(self):
        pm, _ = mk("p", lambda **kw: None)
        self.assertEqual(pm.inflight_count("p"), 0)
        pm.execute_hooks("before_reply", message="x")
        self.assertEqual(pm.inflight_count("p"), 0, "钩子结束后在途应归零")

    def test_inflight_visible_while_running(self):
        seen = {}

        def slow(**kw):
            seen["during"] = pm.inflight_count("p")
            return None

        pm, _ = mk("p", slow)
        pm.execute_hooks("before_reply")
        self.assertEqual(seen["during"], 1, "执行期间在途应为 1")

    def test_inflight_cleared_on_exception(self):
        def boom(**kw):
            raise RuntimeError("炸")

        pm, _ = mk("p", boom)
        out = pm.execute_hooks_semantic("before_reply", semantics="guard")
        self.assertEqual(out["trail"][0][1], "error")
        self.assertEqual(pm.inflight_count("p"), 0, "异常路径也必须归零（finally）")

    # ── 核心：fail-closed ──
    def test_unload_refused_while_busy(self):
        """★ 有在途 → 拒绝卸载，且 on_unload **不得**被调用。"""
        started = threading.Event()
        release = threading.Event()

        def blocking(**kw):
            started.set()
            release.wait(3)
            return None

        pm, mod = mk("p", blocking)
        t = threading.Thread(target=lambda: pm.execute_hooks("before_reply"))
        t.start()
        started.wait(2)
        try:
            ok = pm.unload("p")                      # 有在途 → 应拒绝
            self.assertFalse(ok, "有在途时必须拒绝卸载（fail-closed）")
            self.assertEqual(mod.unloaded, 0, "被拒时不得调用 on_unload")
            self.assertIn("p", pm._plugins, "被拒时不得从注册表摘除")
        finally:
            release.set()
            t.join(3)
        # 跑完之后就能正常卸载
        self.assertTrue(pm.unload("p"))
        self.assertEqual(mod.unloaded, 1)

    def test_force_unload_bypasses(self):
        release = threading.Event()

        def blocking(**kw):
            release.wait(3)

        pm, mod = mk("p", blocking)
        started = threading.Event()
        pm2fn = lambda: (started.set(), pm.execute_hooks("before_reply"))
        t = threading.Thread(target=pm2fn)
        t.start()
        started.wait(2)
        try:
            self.assertTrue(pm.unload("p", force=True), "force=True 应能强卸")
            self.assertEqual(mod.unloaded, 1)
        finally:
            release.set()
            t.join(3)

    def test_wait_idle_then_ok(self):
        """wait_idle：等在途跑完 → 之后卸载通过。"""
        release = threading.Event()

        def blocking(**kw):
            release.wait(3)

        pm, _ = mk("p", blocking)
        threading.Thread(target=lambda: pm.execute_hooks("before_reply"), daemon=True).start()
        time.sleep(0.15)
        threading.Timer(0.2, release.set).start()
        self.assertTrue(pm.wait_idle("p", timeout=2), "等在途结束后应返回 True")
        self.assertTrue(pm.unload("p"))

    def test_wait_idle_timeout(self):
        release = threading.Event()

        def blocking(**kw):
            release.wait(5)

        pm, _ = mk("p", blocking)
        threading.Thread(target=lambda: pm.execute_hooks("before_reply"), daemon=True).start()
        time.sleep(0.15)
        try:
            self.assertFalse(pm.wait_idle("p", timeout=0.3), "超时应返回 False")
        finally:
            release.set()

    # ── 显式结果版（给 UI/排查）──
    def test_unload_ex_reason(self):
        pm, _ = mk("p", lambda **kw: None)
        got = pm.unload_ex("p")
        self.assertTrue(got["ok"])
        self.assertEqual(got["inflight"], 0)
        got2 = pm.unload_ex("不存在")
        self.assertFalse(got2["ok"])
        self.assertIn("未找到", got2["reason"])

    def test_unload_ex_refuses_with_reason_when_busy(self):
        started = threading.Event()
        release = threading.Event()

        def blocking(**kw):
            started.set()
            release.wait(3)

        pm, _ = mk("p", blocking)
        threading.Thread(target=lambda: pm.execute_hooks("before_reply"), daemon=True).start()
        started.wait(2)
        try:
            got = pm.unload_ex("p")
            self.assertFalse(got["ok"])
            self.assertEqual(got["inflight"], 1)
            self.assertIn("在途", got["reason"])
        finally:
            release.set()

    # ── 重载也守规矩 ──
    def test_reload_aborts_when_unload_refused(self):
        started = threading.Event()
        release = threading.Event()

        def blocking(**kw):
            started.set()
            release.wait(3)

        pm, mod = mk("p", blocking)
        pm.load = lambda name: (_ for _ in ()).throw(AssertionError("不该走到 load"))
        threading.Thread(target=lambda: pm.execute_hooks("before_reply"), daemon=True).start()
        started.wait(2)
        try:
            self.assertFalse(pm.reload("p"), "卸载被拒时重载必须中止")
            self.assertEqual(mod.unloaded, 0)
        finally:
            release.set()

    def test_inflight_keyed_per_plugin(self):
        """在途计数按插件分开（一个忙不该拦住另一个）。"""
        release = threading.Event()
        pmbusy, _ = mk("busy", lambda **kw: release.wait(3))
        pmfree, _ = mk("free", lambda **kw: None)
        threading.Thread(target=lambda: pmbusy.execute_hooks("before_reply"), daemon=True).start()
        time.sleep(0.15)
        try:
            self.assertEqual(pmbusy.inflight_count("busy"), 1)
            self.assertEqual(pmbusy.inflight_count("free"), 0)
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main(verbosity=2)
