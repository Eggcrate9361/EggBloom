# -*- coding: utf-8 -*-
"""
清单 2.2 · 私聊画像链路验证。

缺陷原文：bot_core.py 私聊路径 `pm_ctx = ""`（硬编码空串）→ 私聊画像从未生效。
调查结论（本轮）：画像**不再**走 private_memory_context —— T5/T6 建成的 ego 钩子
路径已经把 super_memory 的画像（facts）+ 好感 + 长期人格，经
`_run_ego_before_reply → ego_inject_context → 【EGO上下文】` 送进 prompt。
本测试把这条链**逐环钉死**，防止将来有人在重构中把链弄断：

  A. 过滤键名：插件管理器注册键是包名小写 "ego"，_run_ego_before_reply 的
     `plugin_name != "ego"` 过滤必须放行（曾疑似大小写不匹配，实测匹配）。
  B. prompt_blocks 拼接：super_memory 返回 {"action":"inject","prompt_blocks":[...]}
     的契约被 _run_ego_before_reply 正确拼接。
  C. 消费点：_inject_ego_context 把注入文本以【EGO上下文】写入 dynamic_parts。
  D. 调用点接线：私聊路径确实把 _ego_ctx 传给 ego_inject_context（源码级断言）。
  E. 数据源：super_memory.before_reply 确实调用 get_prompt_block（源码级断言）。

运行：python -m unittest tests.test_private_profile -v
"""
import os
import re
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_core  # noqa: E402

PROFILE_BLOCK = "用户画像: 喜欢猫；养了一只叫煤球的黑猫；偏好简短回复"


class _FakePM:
    """最小插件管理器桩：只实现 _run_ego_before_reply 用到的两个成员。"""

    def __init__(self, legacy):
        self._legacy = legacy

    def has_hook(self, hook_name):
        return hook_name in self._legacy

    def execute_hooks(self, hook_name, **kwargs):
        out = []
        for name, fn in self._legacy.get(hook_name, {}).items():
            try:
                r = fn(**kwargs)
            except Exception as e:  # 与真实管理器一致：吞异常继续
                print(f"[Plugin:{name}] hook {hook_name} 异常: {e}")
                continue
            if r is not None:
                out.append((name, r))
        return out


class TestPrivateProfileChain(unittest.TestCase):
    def setUp(self):
        self._orig_pm = bot_core.get_plugin_manager
        self._orig_ok = bot_core.PLUGIN_OK
        # ★ 2026-10-30：这条链测的是「注入有没有被收集」，不是预算。
        #   今日账本 `data/plugin_budget.json` 会被其它跑 bot_core 的测试刷满
        #   （实测 ego 594/600），预算一满注入被整段丢掉 → 断言假红。
        #   这里明确关掉预算，让测试与"今天跑过几次"无关。
        self._orig_budget = (bot_core._config.get("plugin_inject_budget_enabled", "__MISSING__"),
                             bot_core._config.get("plugin_inject_daily_chars", "__MISSING__"))
        bot_core._config["plugin_inject_budget_enabled"] = False
        bot_core._config["plugin_inject_daily_chars"] = 0

    def tearDown(self):
        bot_core.get_plugin_manager = self._orig_pm
        bot_core.PLUGIN_OK = self._orig_ok
        for k, v in zip(("plugin_inject_budget_enabled", "plugin_inject_daily_chars"),
                        self._orig_budget):
            if v == "__MISSING__":
                bot_core._config.pop(k, None)
            else:
                bot_core._config[k] = v

    def _install(self, ego_result):
        bot_core.PLUGIN_OK = True
        bot_core.get_plugin_manager = lambda: _FakePM(
            {"before_reply": {"ego": (lambda **kw: ego_result)}})

    # ── A + B：注入收集链 ──
    def test_prompt_blocks_collected(self):
        """super_memory 契约（prompt_blocks）经 ego 收集后进入注入文本。"""
        self._install({"action": "inject", "prompt_blocks": [PROFILE_BLOCK]})
        text, temp = bot_core._run_ego_before_reply(
            "在吗", group_id="", ctx={"scope": "private", "sender": "10001"})
        self.assertIn(PROFILE_BLOCK, text, f"画像块应出现在注入文本中，实际：{text!r}")
        self.assertIsNone(temp)

    def test_lowercase_ego_key_accepted(self):
        """注册键是小写 "ego"（= plugins/ego 包名），过滤条件必须放行。
        若将来有人把过滤改成精确匹配 "EGO" 或改动键名生成，此测试会红。"""
        self._install({"action": "inject", "prompt_blocks": [PROFILE_BLOCK]})
        text, _ = bot_core._run_ego_before_reply("在吗", group_id="", ctx={"scope": "private"})
        self.assertTrue(text, "小写 ego 键的注入被过滤掉了 → 私聊画像整条链断")

    def test_non_ego_legacy_still_filtered(self):
        """旧式路径只收 ego：其它旧式插件的注入不得混入（防注入面扩大）。"""
        pm = _FakePM({"before_reply": {
            "ego": (lambda **kw: {"action": "inject", "prompt_blocks": [PROFILE_BLOCK]}),
            "web_search": (lambda **kw: {"action": "inject", "content": "SHOULD_NOT_APPEAR"}),
        }})
        bot_core.PLUGIN_OK = True
        bot_core.get_plugin_manager = lambda: pm
        text, _ = bot_core._run_ego_before_reply("在吗", group_id="", ctx={"scope": "private"})
        self.assertIn(PROFILE_BLOCK, text)
        self.assertNotIn("SHOULD_NOT_APPEAR", text)

    def test_exception_isolated(self):
        """单个插件抛异常不影响主流程（返回空注入）。"""
        def boom(**kw):
            raise RuntimeError("boom")
        pm = _FakePM({"before_reply": {"ego": boom}})
        bot_core.PLUGIN_OK = True
        bot_core.get_plugin_manager = lambda: pm
        text, temp = bot_core._run_ego_before_reply("在吗", group_id="", ctx={})
        self.assertEqual(text, "")
        self.assertIsNone(temp)

    # ── C：消费点 ──
    def test_inject_ego_context_writes_dynamic_parts(self):
        parts = []
        bot_core._inject_ego_context(parts, PROFILE_BLOCK)
        self.assertEqual(len(parts), 1)
        self.assertIn("【EGO上下文】", parts[0])
        self.assertIn(PROFILE_BLOCK, parts[0])
        # 2026-10-31（P0-2）：包装语里的「回复时自然地融会贯通，不要生硬复述」是话术，
        # 已归人格文件；这里只要求带上**事实标签**（这是谁的状态、不是对方说的话）。
        self.assertIn("不是对方说的话", parts[0])

    def test_inject_ego_context_empty_noop(self):
        parts = []
        bot_core._inject_ego_context(parts, "")
        self.assertEqual(parts, [], "空注入不得产生空条目")

    # ── D / E：接线与数据源（源码级断言，沿用 test_plugin_bridge 的做法）──
    def test_private_call_site_wires_ego_ctx(self):
        with open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        # 私聊调用点必须把 ego 注入传进 ego_inject_context（而不是只传空的 pm_ctx）
        # 2026-10-30：P0-1 之后入参从 text 改成 _pm_text_for_ai（跳过说明只进本轮），
        # P1-4 又在末尾加了 on_slow=…，所以这里只断言"那次调用带着 ego_inject_context"，
        # 不再卡死调用尾部的形状。
        self.assertRegex(
            src, r"call_deepseek\((?:text|_pm_text_for_ai)[^\n]*ego_inject_context=_ego_ctx",
            "私聊调用点没有把 ego 注入接进 ego_inject_context")
        # pm_ctx="" 的桩注释必须保留说明（画像走 ego 钩子），防止后人误当缺陷"修"回去
        self.assertIn("ego 钩子", src)

    def test_super_memory_before_reply_uses_prompt_block(self):
        with open(os.path.join(_ROOT, "plugins", "ego", "super_memory", "__init__.py"),
                  encoding="utf-8") as f:
            src = f.read()
        # before_reply 里必须调用 get_prompt_block（facts=画像）与私聊 scope 归一
        seg = src[src.find("def before_reply"):src.find("def after_reply")]
        # 2026-10-31：1.5.10 起这次调用多了 focus_user=user_id（记忆按人），
        # 之前卡死整串的断言因此假红 —— 只断言"这次调用还在"，不卡参数尾部。
        self.assertRegex(seg, r"_memory\.get_prompt_block\(\s*scope,\s*scope_id,\s*message",
                         "before_reply 没有调用 get_prompt_block（画像召回断了）")
        self.assertIn('scope_id = user_id or "private"', seg,
                      "私聊必须以 sender 作为作用域，否则画像召回/写入不同 key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
