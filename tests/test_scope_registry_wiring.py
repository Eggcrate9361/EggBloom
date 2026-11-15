# -*- coding: utf-8 -*-
"""
T7 · P4 · S2 —— 作用域注册表接入第一个调用点（群聊工具集）验证。

**S2 的全部价值是"零行为变化"**，所以本文件用**真·快照对比**：
「开关关」（= 改前的既有逻辑）作为基准，与「开关开 + standard」逐字节比。
顺序也一起比 —— 工具顺序由候选池决定，不能凭我的假设写死期望值。

运行：python -m unittest tests.test_scope_registry_wiring -v
"""
import os
import re
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_core  # noqa: E402
from harness.scope_registry import ScopeRegistry  # noqa: E402


def names(tools):
    return [t["function"]["name"] for t in tools]


# 三组代表性配置：默认 / 自定义含高风险 / 自定义含生图
CONFIGS = [
    None,
    ["get_group_members", "web_operate", "open_app"],
    ["get_group_members", "generate_image"],
]


class TestScopeRegistryWiring(unittest.TestCase):
    def setUp(self):
        self._orig_cfg = bot_core._config
        self._orig_reg = bot_core._SCOPE_REGISTRY
        self.tmp = tempfile.mkdtemp(prefix="scopewire_")
        self.reg = ScopeRegistry(
            events_path=os.path.join(self.tmp, "ev.jsonl"), on_warn=lambda m: None)
        self._orig_cfg_snapshot = dict(self._orig_cfg)

    def tearDown(self):
        import shutil
        bot_core._config = self._orig_cfg
        bot_core._SCOPE_REGISTRY = self._orig_reg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, switch=False, allowlist=None):
        c = dict(self._orig_cfg_snapshot)
        c["capability_registry_enable"] = switch
        if allowlist is None:
            c.pop("group_tool_allowlist", None)
        else:
            c["group_tool_allowlist"] = allowlist
        bot_core._config = c

    def _base(self, allowlist, gid="123"):
        """基准 = 开关关（等价于改前的既有逻辑）。"""
        self._cfg(switch=False, allowlist=allowlist)
        bot_core._SCOPE_REGISTRY = self.reg
        return names(bot_core._build_group_tools(gid))

    def _with_registry(self, allowlist, gid="123", reg=None):
        self._cfg(switch=True, allowlist=allowlist)
        bot_core._SCOPE_REGISTRY = self.reg if reg is None else reg
        return names(bot_core._build_group_tools(gid))

    # ── ★ 核心：开关开 + standard ≡ 开关关（逐字节，含顺序）──
    def test_standard_is_byte_identical_to_base_for_all_configs(self):
        for cfg in CONFIGS:
            base = self._base(cfg)
            self.assertTrue(base, "基准不该为空（配置 %r）" % cfg)
            got = self._with_registry(cfg)
            self.assertEqual(got, base,
                             "★ standard 必须与改前逐字节相同（配置 %r）" % cfg)

    # ── ★ 那个最关键的坑：用户名单里故意放了高风险工具 ──
    def test_high_risk_in_custom_allowlist_survives_standard(self):
        cfg = ["get_group_members", "open_app", "web_operate"]
        base = self._base(cfg)
        self.assertIn("open_app", base, "前提：基准里确实有高风险工具")
        self.assertEqual(self._with_registry(cfg), base,
                         "standard 若收紧就会砍掉 open_app → 改变现网行为")

    # ── 收紧档（readonly）才真的过滤 ──
    def test_readonly_filters_by_scope(self):
        cfg = ["get_group_members", "open_app", "web_operate", "generate_image"]
        self.reg.set_preset("group", "123", "readonly")
        got = self._with_registry(cfg, gid="123")
        self.assertEqual(set(got), {"get_group_members"},
                         "readonly 只该留低风险 —— 实际 %r" % got)

    def test_preset_is_per_scope(self):
        cfg = ["get_group_members", "open_app"]
        self.reg.set_preset("group", "123", "readonly")
        self.assertEqual(set(self._with_registry(cfg, gid="123")), {"get_group_members"})
        self.assertEqual(set(self._with_registry(cfg, gid="999")), set(self._base(cfg)),
                         "别的群不受影响（逐群隔离）")

    def test_full_relaxes(self):
        """full 带 override_allowlist；S2 只透传该标志，不改 base（放宽留给 S4）。"""
        cfg = ["get_group_members"]
        base = self._base(cfg)
        self.assertEqual(self._with_registry(cfg), base,
                         "S2 阶段 full 不改变结果（只透传标志）")
        self.assertTrue(self.reg.resolve("group", "123", explicit="full")["override_allowlist"])

    # ── 软依赖：注册表坏了/缺失/空集 → 原样返回（绝不凭空清空工具）──
    def test_broken_registry_returns_tools_unchanged(self):
        cfg = ["get_group_members", "web_operate"]
        base = self._base(cfg)

        class _Boom:
            def resolve(self, *a, **k):
                raise RuntimeError("注册表炸了")
        self.assertEqual(self._with_registry(cfg, reg=_Boom()), base,
                         "软依赖：失败必须原样返回")

    def test_missing_registry_returns_tools_unchanged(self):
        cfg = ["get_group_members"]
        base = self._base(cfg)
        self.assertEqual(self._with_registry(cfg, reg=False), base)

    def test_empty_caps_returns_tools_unchanged(self):
        cfg = ["get_group_members"]
        base = self._base(cfg)

        class _Empty:
            def resolve(self, *a, **k):
                return {"capabilities": frozenset()}
        self.assertEqual(self._with_registry(cfg, reg=_Empty()), base,
                         "空集时宁可不过滤，也不让工具凭空消失")

    # ── 免重启热改：事件文件变了就自动重载 ──
    def test_hot_reload_picks_up_external_change(self):
        """S5 界面做好前，改档位只能靠写事件文件 —— 主进程必须能自动看到。"""
        ev = os.path.join(self.tmp, "ev.jsonl")
        bot_core._SCOPE_REGISTRY = self.reg
        bot_core._SCOPE_REGISTRY_MTIME = None
        bot_core._get_scope_registry()                      # 首次：记录 mtime
        self.assertEqual(self.reg.fold("group", "777"), None)

        # 外部进程写入一条事件（模拟 scripts/scope_policy.py 的动作）
        import time as _t
        _t.sleep(0.01)
        reg2 = ScopeRegistry(events_path=ev, on_warn=lambda m: None)
        reg2.set_preset("group", "777", "readonly", by="外部")
        os.utime(ev, None)                                   # 确保 mtime 变化

        bot_core._get_scope_registry()                       # 再取用 → 应热重载
        self.assertEqual(self.reg.fold("group", "777"), "readonly",
                         "★ 外部改了事件文件，主进程必须自动生效（免重启）")

    def test_hot_reload_failure_is_soft(self):
        bot_core._SCOPE_REGISTRY = self.reg
        bot_core._SCOPE_REGISTRY_MTIME = None
        self.reg._events_path = "Z:/不存在的盘/ev.jsonl"      # mtime 取不到
        self.assertIs(bot_core._get_scope_registry(), self.reg,
                      "取 mtime 失败也只能退化为不重载，不能返回 None")

    # ── 接线（源码级）──
    def test_both_call_sites_pass_group_id(self):
        with open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        calls = re.findall(r"_build_group_tools\(([^)]*)\)", src)
        self.assertTrue(calls, "找不到调用点")
        for c in calls:
            self.assertIn("group_id", c,
                          "每个调用点都必须传 group_id，否则逐群授权无从生效：%r" % c)

    def test_switch_defaults_off(self):
        """默认配置里开关不存在 → 视为关（零行为变化上线的前提）。"""
        cfg = ["get_group_members", "open_app"]
        base = self._base(cfg)                    # 基准用同一份名单算，否则是苹果比橘子
        c = dict(self._orig_cfg_snapshot)
        c.pop("capability_registry_enable", None)  # 假装配置里根本没有这个键
        c["group_tool_allowlist"] = list(cfg)
        bot_core._config = c
        bot_core._SCOPE_REGISTRY = self.reg
        self.reg.set_preset("group", "123", "readonly")      # 即使已切到收紧档
        self.assertEqual(names(bot_core._build_group_tools("123")), base,
                         "开关缺省=关 → 注册表不得参与过滤")
        self.assertNotIn("capability_registry_enable", self._orig_cfg_snapshot,
                         "真实配置里不该预置这个键（默认关）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
