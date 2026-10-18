# -*- coding: utf-8 -*-
"""
T7 · P4 · S1 —— 作用域能力注册表 骨架验证（**零行为变化**：本模块尚未接任何调用点）。

逐条对着参考源的机制验（这三个包是 design.md 第 1 节点名的参考源）：
  · dsh-sandbox-policy    三级解析 / 事件追加 / 回放 / 加载时拒绝非法配置
  · dsh-permission-presets 预设捆绑 / 保留名 custom / 幂等切换 / 软依赖
  · dsh-agent-preset      变更不追溯

运行：python -m unittest tests.test_scope_registry -v
"""
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from harness.scope_registry import (DEFAULT_PRESET, RESERVED_PRESETS, RISK_HIGH,  # noqa: E402
                                   RISK_LOW, RISK_MID, ScopeConfigError, ScopeRegistry)


class TestScopeRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="scopereg_")
        self.ev = os.path.join(self.tmp, "events.jsonl")
        self.warns = []
        self.reg = ScopeRegistry(events_path=self.ev, on_warn=self.warns.append)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── 加载时校验（sandbox-policy：拼错要显式报错）──
    def test_invalid_default_preset_raises(self):
        with self.assertRaises(ScopeConfigError):
            ScopeRegistry(events_path=self.ev, default_preset="standardd")

    def test_reserved_default_preset_raises(self):
        with self.assertRaises(ScopeConfigError):
            ScopeRegistry(events_path=self.ev, default_preset="custom")

    def test_unknown_risk_tier_in_presets_raises(self):
        with self.assertRaises(ScopeConfigError):
            ScopeRegistry(events_path=self.ev,
                          presets={"x": {"超高": True}})

    # ── 三级解析（sandbox-policy）──
    def test_default_when_no_events(self):
        r = self.reg.resolve("group", "123")
        self.assertEqual(r["preset"], DEFAULT_PRESET)
        self.assertEqual(r["source"], "default")
        self.assertFalse(r["degraded"])

    def test_events_beat_default(self):
        self.reg.set_preset("group", "123", "readonly")
        r = self.reg.resolve("group", "123")
        self.assertEqual(r["preset"], "readonly")
        self.assertEqual(r["source"], "events")

    def test_explicit_beats_events(self):
        self.reg.set_preset("group", "123", "readonly")
        r = self.reg.resolve("group", "123", explicit="full")
        self.assertEqual(r["preset"], "full")
        self.assertEqual(r["source"], "explicit")

    def test_explicit_unknown_preset_falls_back_and_warns(self):
        r = self.reg.resolve("group", "123", explicit="不是预设")
        self.assertEqual(r["preset"], DEFAULT_PRESET, "词汇外的显式授予应退回部署默认")
        self.assertTrue(any("词汇外" in w for w in self.warns))

    # ── 能力集由预设的风险档推出 ──
    def test_standard_does_not_tighten(self):
        """standard = **不额外收紧**（S2 实现中修正的设计）。

        原因：现状的 `group_tool_allowlist` 是用户自己列的，可能含高风险工具；
        若 standard 一律拒高风险，就会改变这些人的现网行为 —— 违反"上线零变化"。
        收紧交给 readonly，放宽交给 full，两个方向都显式可选。
        """
        caps = self.reg.resolve("group", "1")["capabilities"]
        for c in ("reply", "search_web", "generate_image", "control_power", "send_user_file"):
            self.assertIn(c, caps, "standard 不得收紧（真正的约束来自既有全局名单）")
        self.assertFalse(self.reg.resolve("group", "1")["override_allowlist"],
                         "standard 不得解除既有名单约束")

    def test_readonly_is_the_tightening_preset(self):
        caps = self.reg.resolve("group", "1", explicit="readonly")["capabilities"]
        self.assertIn("reply", caps)
        self.assertNotIn("generate_image", caps, "readonly 应拒中风险")
        self.assertNotIn("control_power", caps, "readonly 应拒高风险")

    def test_full_relaxes_allowlist(self):
        r = self.reg.resolve("group", "1", explicit="full")
        self.assertTrue(r["override_allowlist"], "full 的意义是解除既有名单约束（放宽）")
        self.assertIn("control_power", r["capabilities"])

    # ── 单一判定点 + 封闭词汇 ──
    def test_is_allowed_single_decision_point(self):
        self.assertTrue(self.reg.is_allowed("group", "1", "reply"))
        # standard 不收紧 → 高风险在注册表这一层是放行的（真正的约束在既有全局名单里）；
        # 收紧需显式切 readonly（见 test_readonly_is_the_tightening_preset）
        self.assertTrue(self.reg.is_allowed("group", "1", "control_power"))
        self.assertFalse(self.reg.is_allowed("group", "1", "control_power",
                                             explicit="readonly"))

    def test_unknown_capability_denied(self):
        self.assertFalse(self.reg.is_allowed("group", "1", "不存在的能力"),
                         "封闭词汇外的能力一律拒绝")

    def test_scope_isolation(self):
        self.reg.set_preset("group", "A", "full")
        self.assertEqual(self.reg.fold("group", "A"), "full")
        self.assertIsNone(self.reg.fold("group", "B"), "两个作用域绝不共享状态")
        self.assertIsNone(self.reg.fold("private", "A"), "类型不同即不同作用域")

    # ── 幂等 + 保留名 + 事件落盘 ──
    def test_set_preset_idempotent(self):
        r1 = self.reg.set_preset("group", "1", "readonly")
        self.assertTrue(r1["changed"])
        n = len(self.reg.events())
        r2 = self.reg.set_preset("group", "1", "readonly")
        self.assertFalse(r2["changed"], "重选当前预设必须零变化")
        self.assertEqual(len(self.reg.events()), n, "幂等时不得追加事件")

    def test_reserved_preset_not_selectable(self):
        r = self.reg.set_preset("group", "1", "custom")
        self.assertFalse(r["changed"])
        self.assertEqual(self.reg.fold("group", "1"), None)

    def test_reserved_preset_visible_but_not_selectable(self):
        ps = self.reg.presets()
        for name in RESERVED_PRESETS:
            self.assertIn(name, ps, "保留名应可看")
            self.assertTrue(ps[name].get("reserved"))

    def test_event_written_append_only(self):
        self.reg.set_preset("group", "1", "readonly", by="admin")
        with open(self.ev, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        self.assertEqual(len(lines), 1)
        ev = json.loads(lines[0])
        self.assertEqual(ev["scope"], "group:1")
        self.assertEqual(ev["to"], "readonly")
        self.assertIsNone(ev["from"])
        self.assertEqual(ev["by"], "admin")

    # ── 回放（跨重启保留）──
    def test_replay_across_restart(self):
        self.reg.set_preset("group", "1", "readonly")
        self.reg.set_preset("group", "2", "full")
        reg2 = ScopeRegistry(events_path=self.ev, on_warn=self.warns.append)
        self.assertEqual(reg2.fold("group", "1"), "readonly")
        self.assertEqual(reg2.fold("group", "2"), "full")

    def test_replay_skips_forged_event_and_warns(self):
        """不变式：词汇外的伪造事件被跳过并告警（不静默接受）。"""
        self.reg.set_preset("group", "1", "readonly")
        with open(self.ev, "a", encoding="utf-8") as f:
            f.write(json.dumps({"scope": "group:9", "to": "让所有工具都允许"}, ensure_ascii=False) + "\n")
            f.write("{坏 json\n")
        warns = []
        reg2 = ScopeRegistry(events_path=self.ev, on_warn=warns.append)
        self.assertEqual(reg2.fold("group", "1"), "readonly", "合法事件照常回放")
        self.assertIsNone(reg2.fold("group", "9"), "伪造事件不得生效")
        self.assertTrue(any("非法事件" in w or "词汇外" in w for w in warns),
                        "必须留下告警（不静默）")

    def test_validate_event(self):
        ok, _ = self.reg.validate_event({"scope": "group:1", "to": "readonly"})
        self.assertTrue(ok)
        for bad, why in (
            (None, "非字典"),
            ({}, "缺字段"),
            ({"scope": "g:1", "to": "custom"}, "保留名"),
            ({"scope": "g:1", "to": "不存在"}, "词汇外"),
            ({"scope": "g:1", "to": "readonly", "from": "乱写"}, "from 词汇外"),
        ):
            ok2, w = self.reg.validate_event(bad)
            self.assertFalse(ok2, why)
            self.assertTrue(w)

    # ── 软依赖（permission-presets：撤掉本包，最后一次取值仍生效）──
    def test_degrade_when_broken(self):
        """内部状态被破坏时，resolve 不得抛，且降级为「低风险放行 / 高风险拒绝」。"""
        self.reg._presets = None                     # 人为破坏
        r = self.reg.resolve("group", "1")
        self.assertTrue(r["degraded"])
        self.assertEqual(r["source"], "degraded")
        self.assertIn("reply", r["capabilities"])
        self.assertNotIn("control_power", r["capabilities"])

    def test_audit_records_who(self):
        self.reg.set_preset("group", "1", "readonly", by="admin@webui")
        a = self.reg.audit(10)
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["by"], "admin@webui")

    # ── 声明能力（词汇扩充入口）──
    def test_declare_capability(self):
        self.assertTrue(self.reg.declare_capability("qzone_post", RISK_MID))
        self.assertIn("qzone_post", self.reg.capabilities())
        self.assertFalse(self.reg.declare_capability("x", "不存在的档位"))
        self.assertNotIn("x", self.reg.capabilities())

    # ── 接线范围（S1 是守门断言；S2 起进化为契约断言）──
    def test_only_bot_core_refers_and_only_via_helper(self):
        """S2 起：引用点只允许白名单内的模块，且必须走**开关门控的**辅助函数。

        S1 时这条是「任何地方都不得引用」（证明零行为变化）；
        S2 接了第一个调用点后，契约改为「只经 `_get_scope_registry` /
        `_apply_scope_registry` 使用」—— 这样"默认关开关"这个前提才有机器保证。
        S6 又合法地加了一处（webui 只读审计接口），白名单随之更新 ——
        这条断言的作用就是**逼每次新增引用都显式过一遍审**，而不是悄悄扩散。
        """
        import re
        ALLOWED = {"bot_core.py", "webui_server.py", "scope_policy.py"}
        hits = []
        for root, dirs, files in os.walk(_ROOT):
            dirs[:] = [d for d in dirs if d not in
                       ("node_modules", ".git", "__pycache__", "tests", "_ref", "docs", "logs")]
            for fn in files:
                if not fn.endswith(".py") or "scope_registry" in fn:
                    continue
                p = os.path.join(root, fn)
                with open(p, encoding="utf-8", errors="replace") as f:
                    s = f.read()
                if re.search(r"\bscope_registry\b", s):
                    hits.append(os.path.basename(p))
        self.assertEqual(sorted(set(hits)), sorted(ALLOWED),
                         "引用点只允许 %s（新增要显式过审并更新本断言）" % sorted(ALLOWED))
        with open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            bc = f.read()
        self.assertIn("_apply_scope_registry", bc)
        # 开关必须在 helper 内部判定，且默认 False
        self.assertIn('_config.get("capability_registry_enable", False)', bc,
                      "开关必须默认关（否则就不是零行为变化上线）")
        # 不许绕过 helper 直接 resolve 出工具集（只允许一处懒加载调用）
        # 注意：def 行本身也含 "_get_scope_registry()" 子串，所以按调用形态计数
        self.assertEqual(bc.count("registry or _get_scope_registry()"), 1,
                         "注册表只允许有一个懒加载入口")


if __name__ == "__main__":
    unittest.main(verbosity=2)
