# -*- coding: utf-8 -*-
"""
T7 批一 ⑤ P3 · 钩子三档拦截语义 验证（waterfall / guard / observe）。

语义出处：DSH 的 `dsh-tools` 三档 ——
  · observe   只读：返回值**一律忽略**，异常隔离 → 观测类插件永远不改变主流程
  · guard     **单调**：任一环拒绝后**终止后续环**，下游无法推翻 → 有副作用插件的安全根
  · waterfall **可重排**：链式，上一环返回值经 carry 进入下一环

本测试用最小假插件管理器驱动**真实实现**（`execute_hooks_semantic`），
不 mock 被测逻辑；同时验证旧 `execute_hooks` 语义未被改动。

运行：python -m unittest tests.test_hook_semantics -v
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plugins.plugin_manager import HOOK_SEMANTICS, PluginManager  # noqa: E402


def mk(*plugins):
    """构造一个只填 _plugins 的 PluginManager（不触发发现/加载/落盘）。"""
    pm = PluginManager.__new__(PluginManager)
    pm._plugins = {}
    for name, priority, fn in plugins:
        pm._plugins[name] = {
            "tier": "legacy", "module": None,
            "meta": {"name": name, "enabled": True, "priority": priority},
            "hooks": {"before_reply": fn},
        }
    return pm


def inject(text):
    return lambda **kw: {"action": "inject", "content": text}


def skip(reason):
    return lambda **kw: {"action": "skip", "content": reason}


def boom(**kw):
    raise RuntimeError("插件炸了")


class TestHookSemantics(unittest.TestCase):
    # ── 基础契约 ──
    def test_unknown_semantics_raises(self):
        pm = mk(("a", 10, inject("x")))
        with self.assertRaises(ValueError):
            pm.execute_hooks_semantic("before_reply", semantics="waterfal")   # 拼错
        self.assertEqual(HOOK_SEMANTICS, ("waterfall", "guard", "observe"))

    def test_priority_order_respected(self):
        pm = mk(("late", 90, inject("L")), ("early", 10, inject("E")))
        out = pm.execute_hooks_semantic("before_reply", semantics="guard")
        self.assertEqual([n for n, _ in out["injects"]], ["early", "late"])

    def test_disabled_plugin_skipped(self):
        pm = mk(("on", 10, inject("ON")), ("off", 20, inject("OFF")))
        pm._plugins["off"]["meta"]["enabled"] = False
        out = pm.execute_hooks_semantic("before_reply", semantics="guard")
        self.assertEqual([n for n, _ in out["injects"]], ["on"])

    # ── observe：只读，返回值一律忽略 ──
    def test_observe_ignores_results(self):
        pm = mk(("noisy", 10, inject("不该被收集")))
        out = pm.execute_hooks_semantic("before_reply", semantics="observe")
        self.assertEqual(out["injects"], [], "observe 档不得收集注入")
        self.assertEqual(out["skips"], [], "observe 档不得产生拒绝")
        self.assertEqual(out["trail"], [("noisy", "observed")])

    def test_observe_isolates_exception(self):
        """观测插件的异常不得影响其它环，也不得抛出（不变量 A10）。"""
        pm = mk(("bad", 10, boom), ("good", 20, inject("G")))
        out = pm.execute_hooks_semantic("before_reply", semantics="observe")
        self.assertEqual(out["trail"], [("bad", "error"), ("good", "observed")])
        self.assertEqual(out["injects"], [])

    # ── guard：单调，拒绝后下游无法推翻 ──
    def test_guard_skip_is_monotonic(self):
        """★ 核心：guard 拒绝后，**后续插件根本不被调用**（下游无法推翻）。"""
        called = []

        def later(**kw):
            called.append("later")
            return {"action": "inject", "content": "下游注入"}

        pm = mk(("blocker", 10, skip("广告消息")), ("later", 20, later))
        out = pm.execute_hooks_semantic("before_reply", semantics="guard")
        self.assertEqual(len(out["skips"]), 1)
        self.assertEqual(out["skips"][0][0], "blocker")
        self.assertEqual(called, [], "拒绝后下游插件仍被调用了 —— 单调性被破坏")
        self.assertEqual(out["injects"], [])

    def test_guard_keeps_earlier_injects(self):
        """拒绝之前已收集的注入保留（拒绝只截断下游，不回溯）。"""
        pm = mk(("early", 10, inject("E")), ("blocker", 20, skip("不行")),
                ("late", 30, inject("L")))
        out = pm.execute_hooks_semantic("before_reply", semantics="guard")
        self.assertEqual([n for n, _ in out["injects"]], ["early"])
        self.assertEqual([n for n, _ in out["skips"]], ["blocker"])

    def test_guard_prompt_blocks_merged(self):
        pm = mk(("mem", 10, lambda **kw: {"action": "inject",
                                          "prompt_blocks": ["画像A", "画像B"]}))
        out = pm.execute_hooks_semantic("before_reply", semantics="guard")
        self.assertEqual(out["injects"][0][1], "画像A\n画像B")

    def test_guard_isolates_exception_and_continues(self):
        pm = mk(("bad", 10, boom), ("good", 20, inject("G")))
        out = pm.execute_hooks_semantic("before_reply", semantics="guard")
        self.assertEqual([n for n, _ in out["injects"]], ["good"])
        self.assertEqual(out["trail"][0], ("bad", "error"))

    # ── waterfall：链式传递 ──
    def test_waterfall_chains_carry(self):
        seen = []

        def first(**kw):
            seen.append(kw.get("carry"))
            return {"action": "inject", "content": "第一环"}

        def second(**kw):
            seen.append(kw.get("carry"))
            return {"action": "inject", "content": "第二环"}

        pm = mk(("f", 10, first), ("s", 20, second))
        out = pm.execute_hooks_semantic("before_reply", semantics="waterfall",
                                        initial="起点")
        self.assertEqual(seen[0], "起点", "首环应收到 initial")
        self.assertEqual(seen[1], {"action": "inject", "content": "第一环"},
                         "次环应收到上一环的返回值")
        self.assertEqual(out["value"], {"action": "inject", "content": "第二环"})
        self.assertEqual(len(out["injects"]), 2, "waterfall 也收集注入")

    def test_waterfall_not_monotonic(self):
        """waterfall 不截断：拒绝后下游照常执行（与 guard 的关键区别）。"""
        called = []
        pm = mk(("blocker", 10, skip("x")),
                ("later", 20, lambda **kw: called.append(1) or {"action": "inject", "content": "L"}))
        out = pm.execute_hooks_semantic("before_reply", semantics="waterfall")
        self.assertEqual(called, [1], "waterfall 不应因 skip 截断下游")
        self.assertEqual(len(out["skips"]), 1)
        self.assertEqual(len(out["injects"]), 1)

    # ── 旧 execute_hooks 语义未被改动（收集式，无截断）──
    def test_legacy_execute_hooks_unchanged(self):
        pm = mk(("a", 10, inject("A")), ("b", 20, skip("B")), ("c", 30, inject("C")))
        res = pm.execute_hooks("before_reply", message="x")
        self.assertEqual([n for n, _ in res], ["a", "b", "c"],
                         "旧 execute_hooks 必须仍是「全部收集、不截断」")

    def test_shared_candidate_filter(self):
        """execute_hooks 与 execute_hooks_semantic 共用同一筛选（防两套漂移）。"""
        pm = mk(("a", 10, inject("A")))
        legacy = [n for n, _ in pm.execute_hooks("before_reply")]
        sem = [n for n, _ in pm.execute_hooks_semantic("before_reply", semantics="guard")["injects"]]
        self.assertEqual(legacy, sem)


if __name__ == "__main__":
    unittest.main(verbosity=2)
