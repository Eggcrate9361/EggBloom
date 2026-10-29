# -*- coding: utf-8 -*-
"""
T7 批一 · P24 prompt 段注册表 + 严格渲染 验证。

立场（不变量 A9）：**格式错误的提示词比明确失败更糟**。
本批把 `_build_static_prompt` 尾部的 5 段手工 `+=` 迁进注册表，并给出
`strict=True` 的严格渲染模式；**输出必须逐字节不变**（黄金哈希守着）。

运行：python -m unittest tests.test_prompt_segments -v
"""
import hashlib
import io
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_core  # noqa: E402
import de_ai  # noqa: E402

GOLDEN = os.path.join(_HERE, "fixtures_static_prompt_golden.json")

# ── 环境钉死 ─────────────────────────────────────────────────────────
# static_prompt 会拼进「你是谁」身份块（昵称/QQ）与人格文本，de_ai 段又依赖
# 配置开关。这些在开发机（有真 bot_config.json）与 CI（什么都没有）上不同，
# 黄金哈希必须建立在**钉死的假值**上，否则测试只在某台机器上能过。
PINNED_NICK = "测试昵称"
PINNED_QQ = "10000"
PINNED_PERSONALITY = "固定人格文本：测试用，不含任何真实信息。"
PINNED_DEAI = "【钉死的去AI味段】短句。别啰嗦。"


def _pin_environment(case):
    """把身份块 / 人格 / de_ai 段全部换成固定值；返回还原函数。"""
    old = {
        "nicks": bot_core._bot_nicknames,
        "group_nick": bot_core.BOT_GROUP_NICK,
        "qq": bot_core.BOT_QQ,
        "load_p": bot_core.load_personality,
        "deai": de_ai.de_ai_rule_block,
    }
    bot_core._bot_nicknames = lambda: [PINNED_NICK]
    bot_core.BOT_GROUP_NICK = PINNED_NICK
    bot_core.BOT_QQ = PINNED_QQ
    bot_core.load_personality = lambda: PINNED_PERSONALITY
    de_ai.de_ai_rule_block = lambda cfg=None: PINNED_DEAI

    def restore():
        bot_core._bot_nicknames = old["nicks"]
        bot_core.BOT_GROUP_NICK = old["group_nick"]
        bot_core.BOT_QQ = old["qq"]
        bot_core.load_personality = old["load_p"]
        de_ai.de_ai_rule_block = old["deai"]
        bot_core._STATIC_PROMPT_CACHE.clear()
    case.addCleanup(restore)
    bot_core._STATIC_PROMPT_CACHE.clear()


class TestPromptSegments(unittest.TestCase):
    def setUp(self):
        self._snapshot = list(bot_core._PROMPT_SEGMENTS)

    def tearDown(self):
        bot_core._PROMPT_SEGMENTS[:] = self._snapshot

    # ── 1. ★黄金哈希：在钉死环境下逐字节不变 ──
    def test_golden_byte_identical(self):
        _pin_environment(self)
        with io.open(GOLDEN, encoding="utf-8") as f:
            golden = json.load(f)
        for is_group, key in ((True, "group"), (False, "private")):
            bot_core._STATIC_PROMPT_CACHE.clear()
            t = bot_core._build_static_prompt(is_group)
            sha = hashlib.sha256(t.encode("utf-8")).hexdigest()
            self.assertEqual(len(t), golden[key]["len"],
                             f"{key} 长度变了：{len(t)} vs {golden[key]['len']}")
            self.assertEqual(sha, golden[key]["sha256"], f"{key} 内容变了")

    # ── 2. 注册表本身 ──
    def test_segments_registered_in_order(self):
        names = bot_core.prompt_segment_names()
        self.assertEqual(names, ["回答多样性", "去AI味铁律", "表情包", "诚实铁律",
                                 "别怕不体面", "别露后台", "怎么接话", "表态",
                                 "降级", "群聊规则"],
                         f"段顺序变了：{names}")

    def test_scope_filtering(self):
        """scope=group 的段不得出现在私聊渲染里（反之亦然）。"""
        bot_core._PROMPT_SEGMENTS[:] = []
        bot_core.register_prompt_segment("组专用", order=1, scope="group")(lambda g: "G")
        bot_core.register_prompt_segment("私专用", order=2, scope="private")(lambda g: "P")
        bot_core.register_prompt_segment("通用", order=3, scope="both")(lambda g: "B")
        g = [n for n, _ in bot_core.render_prompt_segments(True)]
        p = [n for n, _ in bot_core.render_prompt_segments(False)]
        self.assertEqual(g, ["组专用", "通用"])
        self.assertEqual(p, ["私专用", "通用"])

    def test_order_is_respected(self):
        bot_core._PROMPT_SEGMENTS[:] = []
        for nm, od in (("C", 30), ("A", 10), ("B", 20)):
            bot_core.register_prompt_segment(nm, order=od)(lambda g, v=nm: v)
        out = [n for n, _ in bot_core.render_prompt_segments(True)]
        self.assertEqual(out, ["A", "B", "C"], "段未按 order 升序")

    def test_empty_segment_skipped(self):
        bot_core._PROMPT_SEGMENTS[:] = []
        bot_core.register_prompt_segment("空段", order=1)(lambda g: "")
        bot_core.register_prompt_segment("None段", order=2)(lambda g: None)
        bot_core.register_prompt_segment("有段", order=3)(lambda g: "x")
        self.assertEqual([n for n, _ in bot_core.render_prompt_segments(True)], ["有段"])

    # ── 3. ★严格渲染语义 ──
    def test_strict_raises_on_failure(self):
        def boom(g):
            raise ValueError("段炸了")
        bot_core._PROMPT_SEGMENTS[:] = []
        bot_core.register_prompt_segment("坏段", order=1)(boom)
        with self.assertRaises(bot_core.PromptRenderError) as cm:
            bot_core.render_prompt_segments(True, strict=True)
        self.assertIn("坏段", str(cm.exception))

    def test_non_strict_reports_and_skips(self):
        """非严格：记入 on_error 并跳过该段，其余段照常渲染（历史行为：de_ai 缺失时静默跳过）。"""
        def boom(g):
            raise RuntimeError("nope")
        bot_core._PROMPT_SEGMENTS[:] = []
        bot_core.register_prompt_segment("坏段", order=1)(boom)
        bot_core.register_prompt_segment("好段", order=2)(lambda g: "OK")
        errs = []
        out = bot_core.render_prompt_segments(True, strict=False,
                                              on_error=lambda n, e: errs.append((n, str(e))))
        self.assertEqual([n for n, _ in out], ["好段"])
        self.assertEqual(errs[0][0], "坏段")
        self.assertIn("nope", errs[0][1])

    def test_prompt_render_error_is_runtime_error(self):
        self.assertTrue(issubclass(bot_core.PromptRenderError, RuntimeError))

    # ── 4. 接线（源码级）：_build_static_prompt 必须走注册表 ──
    def test_build_static_prompt_uses_registry(self):
        with io.open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        seg = src[src.find("def _build_static_prompt"):]
        seg = seg[:seg.find("def load_personality")]
        self.assertIn("render_prompt_segments(", seg, "_build_static_prompt 未走段注册表")
        # 段契约：返回值自带前导分隔符 → 组装处不得再叠加 "\n\n"
        self.assertIn("system_prompt += _seg_text", seg,
                      "组装处叠加了额外分隔符（会让每段多出两个换行）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
