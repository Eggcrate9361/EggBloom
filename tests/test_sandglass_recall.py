# -*- coding: utf-8 -*-
"""
T7 批一 ★0 · 沙漏历史召回（FTS5 → prompt）验证。

背景：沙漏的 FTS5 加速层（`nexsandglass/sandglass_sqlite.py`）早已存在且带自动降级，
但一直没接到 prompt 组装链上。本轮加 `sandglass_bridge.recall_history_block`：
有界（≤limit 条、每条截断）、只读、**命中才产出**（无命中/降级 = 零 token 成本），
并只在**群聊**路径接入（私聊已有 super_memory 向量 top-K，避免同源重复注入）。

运行：python -m unittest tests.test_sandglass_recall -v
"""
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import sandglass_bridge as sb  # noqa: E402


class TestRecallHistoryBlock(unittest.TestCase):
    def setUp(self):
        self._orig_enabled = sb._enabled
        self._orig_avail = sb.is_available
        self._orig_search = sb._search_context_for_message

    def tearDown(self):
        sb._enabled = self._orig_enabled
        sb.is_available = self._orig_avail
        sb._search_context_for_message = self._orig_search

    def _ready(self, search_impl):
        sb._enabled = True
        sb.is_available = lambda: True
        sb._search_context_for_message = search_impl

    # ── 命中路径 ──
    def test_hit_produces_block(self):
        self._ready(lambda msg, limit=3, scope=None: "  - 2026-09-01 群里聊过露营装备\n  - 2026-09-03 提到要买帐篷")
        out = sb.recall_history_block("周末去露营吗")
        self.assertIn("【相关历史】", out)
        self.assertIn("露营装备", out)
        self.assertIn("帐篷", out)
        self.assertIn("不要主动提及你在检索记录", out)

    def test_block_is_bounded_to_limit(self):
        """条数上限：注入 10 条来源也只取 limit 条（token 成本有界）。"""
        self._ready(lambda msg, limit=3, scope=None: "\n".join(f"  - 记忆{i}" for i in range(10)))
        out = sb.recall_history_block("随便", limit=3)
        body = [l for l in out.splitlines() if l.strip().startswith("-")]
        self.assertEqual(len(body), 3, f"应只注入 3 条，实际 {len(body)}")

    def test_each_line_truncated(self):
        """单条截断：超长历史行不得整段塞进 prompt。"""
        self._ready(lambda msg, limit=3, scope=None: "  - " + "长" * 500)
        out = sb.recall_history_block("随便", limit=3, width=140)
        longest = max(len(l) for l in out.splitlines())
        self.assertLessEqual(longest, 140 + 8, f"单行过长：{longest}")

    # ── 零成本路径（无命中必须什么都不产出）──
    def test_no_hit_returns_empty(self):
        self._ready(lambda msg, limit=3, scope=None: "")
        self.assertEqual(sb.recall_history_block("无命中"), "")

    def test_search_exception_returns_empty(self):
        def boom(msg, limit=3):
            raise RuntimeError("FTS 挂了")
        self._ready(boom)
        self.assertEqual(sb.recall_history_block("随便"), "", "检索异常必须降级为空，不能抛")

    def test_disabled_returns_empty(self):
        self._ready(lambda msg, limit=3, scope=None: "  - 不该出现")
        sb._enabled = False
        self.assertEqual(sb.recall_history_block("随便"), "")

    def test_unavailable_returns_empty(self):
        self._ready(lambda msg, limit=3, scope=None: "  - 不该出现")
        sb.is_available = lambda: False
        self.assertEqual(sb.recall_history_block("随便"), "")

    def test_whitespace_only_result_returns_empty(self):
        self._ready(lambda msg, limit=3, scope=None: "  -   \n  -  \n")
        self.assertEqual(sb.recall_history_block("随便"), "")

    # ── 接入点（源码级断言：必须只在群聊路径接）──
    def test_wired_in_group_path_only(self):
        with open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        # 2026-10-19：降级调用点必须**带 scope**（否则跨群/私聊记忆会注进本群）
        self.assertIn('recall_history_block(user_msg, scope=("group", str(group_id)))',
                      src, "降级出口未接入 prompt 组装链，或漏了 scope")
        # 必须在 `if group_id:` 分支里（私聊不接，避免与 super_memory 向量重复注入）
        seg = src[src.find("T7 批一 ★0"):src.find("T7 批一 ★0") + 420]
        self.assertIn("if group_id:", seg, "召回未限定在群聊路径")
        # 私聊 light 模式保留（语义检索归 super_memory）
        self.assertIn("get_memory_prompt_block(user_msg, light=True)", src)

    # ── ★ scope 透传（2026-10-19 修的降级泄漏）──
    def test_scope_passed_through_to_search(self):
        """scope 必须透传到检索层 —— 这是跨群泄漏的防线。"""
        seen = {}

        def _spy(msg, limit=3, scope=None):
            seen["scope"] = scope
            return "  - 命中"

        self._ready(_spy)
        sb.recall_history_block("随便", scope=("group", "123"))
        self.assertEqual(seen.get("scope"), ("group", "123"),
                         "scope 未透传到 _search_context_for_message")

    def test_scope_passed_through_scored_path(self):
        """min_score>0 走 search_scored_context 时，scope 同样必须透传。"""
        seen = {}
        orig = getattr(sb, "search_scored_context", None)
        try:
            def _spy(msg, limit=5, scope=None):
                seen["scope"] = scope
                return [(0.9, "高分命中")]
            sb.search_scored_context = _spy
            sb._enabled = True
            sb.is_available = lambda: True
            out = sb.recall_history_block("随便", min_score=0.35, scope=("group", "999"))
            self.assertIn("高分命中", out)
            self.assertEqual(seen.get("scope"), ("group", "999"),
                             "scope 未透传到 search_scored_context")
        finally:
            if orig is not None:
                sb.search_scored_context = orig

    def test_no_scope_keeps_legacy_behavior(self):
        """不传 scope = 不过滤（旧调用方行为不变，不因本次修复而改变）。"""
        seen = {}

        def _spy(msg, limit=3, scope=None):
            seen["scope"] = scope
            return "  - 命中"

        self._ready(_spy)
        sb.recall_history_block("随便")
        self.assertIsNone(seen.get("scope"), "默认应为 None（不过滤）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
