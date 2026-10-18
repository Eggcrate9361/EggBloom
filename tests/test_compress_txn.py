# -*- coding: utf-8 -*-
"""
T7 批三 · P7 群聊历史压缩「事务化」验证（不变量 A5）。

原实现的问题：
  `_GROUP_SUMMARY_LEN[gid] = len(gh)` 写在**调 LLM 之前**。它兼职了"防并发"（没别的锁），
  但代价是 **LLM 失败/空返回时标记却立着** → 下次调用因"不足 10 条"跳过压缩，
  旧历史可能长时间不再被压缩（真 bug，不是纯理论）。

现在的契约（一次明确的事务）：
  1. 置开始标记（同时承担防并发：新鲜标记 → 不重入）
  2. 做事（调 LLM）
  3. **成功才**提交长度标记并清开始标记
  失败/空返回/异常 → **一定清开始标记**（下次可重试）
  过期标记（>120s，疑似进程中断）→ 丢弃并重试

运行：python -m unittest tests.test_compress_txn -v
"""
import io
import os
import re
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_core  # noqa: E402

GID = "testgroup"


def _mk_history(n=40):
    """造 n 条群聊历史（user/assistant 交替）。"""
    out = []
    for i in range(n):
        out.append({"role": "user" if i % 2 == 0 else "assistant",
                    "content": f"消息{i}"})
    return out


class _Resp:
    def __init__(self, content):
        self._b = json_body(content)

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def json_body(content):
    import json
    if content is None:
        return json.dumps({"choices": []}).encode("utf-8")
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


class TestCompressTxn(unittest.TestCase):
    def setUp(self):
        self._orig_urlopen = bot_core.urllib.request.urlopen
        self._orig_cfg = bot_core._llm_config
        bot_core._llm_config = lambda *a, **k: {"model_name": "t", "api_url": "http://x",
                                                "api_key": "k"}
        for d in (bot_core._GROUP_SUMMARY_START, bot_core._GROUP_SUMMARY_LEN,
                  bot_core._GROUP_SUMMARY_AT, bot_core.GROUP_SUMMARY,
                  bot_core._GROUP_SUMMARY_PREV):
            d.clear()
        bot_core.group_chat_history[GID] = _mk_history(40)

    def tearDown(self):
        bot_core.urllib.request.urlopen = self._orig_urlopen
        bot_core._llm_config = self._orig_cfg
        bot_core.group_chat_history.pop(GID, None)
        for d in (bot_core._GROUP_SUMMARY_START, bot_core._GROUP_SUMMARY_LEN,
                  bot_core._GROUP_SUMMARY_AT, bot_core.GROUP_SUMMARY,
                  bot_core._GROUP_SUMMARY_PREV):
            d.clear()

    def _fake(self, content="摘要内容", exc=None):
        def _u(req, timeout=None, context=None):
            if exc:
                raise exc
            return _Resp(content)
        bot_core.urllib.request.urlopen = _u

    # ── 成功路径 ──
    def test_success_commits_len_and_clears_marker(self):
        self._fake("今天聊了考试和天气")
        bot_core.summarize_group_history(GID)
        self.assertIn(GID, bot_core.GROUP_SUMMARY)
        self.assertIn("考试", bot_core.GROUP_SUMMARY[GID])
        self.assertEqual(bot_core._GROUP_SUMMARY_LEN[GID], 40, "成功后才提交长度标记")
        self.assertNotIn(GID, bot_core._GROUP_SUMMARY_START, "成功后必须清开始标记")
        self.assertEqual(len(bot_core.group_chat_history[GID]), 16, "压缩后只留 16 条")

    # ── ★ 核心：失败不得留下幽灵标记 ──
    def test_llm_exception_leaves_no_marker(self):
        self._fake(exc=RuntimeError("网络炸了"))
        bot_core.summarize_group_history(GID)
        self.assertNotIn(GID, bot_core._GROUP_SUMMARY_START, "失败后必须清开始标记")
        self.assertNotIn(GID, bot_core._GROUP_SUMMARY_LEN, "失败不得提交长度标记")

    def test_empty_choices_leaves_no_marker(self):
        self._fake(None)                     # choices 为空
        bot_core.summarize_group_history(GID)
        self.assertNotIn(GID, bot_core._GROUP_SUMMARY_START)
        self.assertNotIn(GID, bot_core._GROUP_SUMMARY_LEN)

    def test_empty_summary_leaves_no_marker(self):
        self._fake("   ")                    # 摘要空白
        bot_core.summarize_group_history(GID)
        self.assertNotIn(GID, bot_core._GROUP_SUMMARY_START)
        self.assertNotIn(GID, bot_core._GROUP_SUMMARY_LEN)

    def test_retry_possible_after_failure(self):
        """★ 失败后下一次必须能真正重试（原实现会因幽灵标记跳过）。"""
        calls = {"n": 0}

        def _u(req, timeout=None, context=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("第一次失败")
            return _Resp("第二次成功")

        bot_core.urllib.request.urlopen = _u
        bot_core.summarize_group_history(GID)
        self.assertIsNone(bot_core.GROUP_SUMMARY.get(GID))
        bot_core.summarize_group_history(GID)          # 应能重试
        self.assertIn(GID, bot_core.GROUP_SUMMARY, "失败后应能重试成功")
        self.assertEqual(calls["n"], 2, "第二次必须真的调了 LLM（没被跳过）")

    # ── 防并发（原语义必须保留）──
    def test_fresh_marker_blocks_reentry(self):
        bot_core._GROUP_SUMMARY_START[GID] = {"at": time.time(), "len": 38}
        calls = {"n": 0}

        def _u(req, timeout=None, context=None):
            calls["n"] += 1
            return _Resp("不该被调用")

        bot_core.urllib.request.urlopen = _u
        bot_core.summarize_group_history(GID)
        self.assertEqual(calls["n"], 0, "有新鲜进行中标记时不得重入")

    def test_stale_marker_is_abandoned(self):
        """过期标记 = 疑似进程中断 → 丢弃并重试（A5：未匹配的 start 是持久锁）。"""
        bot_core._GROUP_SUMMARY_START[GID] = {"at": time.time() - 999, "len": 38}
        self._fake("恢复后摘要")
        bot_core.summarize_group_history(GID)
        self.assertIn(GID, bot_core.GROUP_SUMMARY, "过期标记应被丢弃并允许重试")
        self.assertNotIn(GID, bot_core._GROUP_SUMMARY_START)

    # ── 节流语义未变 ──
    def test_throttle_still_applies(self):
        bot_core._GROUP_SUMMARY_LEN[GID] = 38           # 刚压过
        bot_core._GROUP_SUMMARY_AT[GID] = time.time()
        calls = {"n": 0}

        def _u(req, timeout=None, context=None):
            calls["n"] += 1
            return _Resp("x")

        bot_core.urllib.request.urlopen = _u
        bot_core.summarize_group_history(GID)           # 只多 2 条 + 1 小时内 → 跳过
        self.assertEqual(calls["n"], 0)
        self.assertNotIn(GID, bot_core._GROUP_SUMMARY_START, "被节流跳过时不该留标记")

    def test_short_history_noop(self):
        bot_core.group_chat_history[GID] = _mk_history(8)
        self._fake("x")
        bot_core.summarize_group_history(GID)
        self.assertNotIn(GID, bot_core.GROUP_SUMMARY)

    # ── 接线（源码级）──
    def test_marker_set_after_not_before(self):
        with io.open(os.path.join(_ROOT, "bot_core.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("_GROUP_SUMMARY_START", src, "缺少事务开始标记")
        # 长度标记只能在 inner 的成功路径里提交
        inner = src[src.index("def _summarize_group_history_inner"):]
        inner = inner[:inner.find("\ndef ", 10)]
        self.assertIn("_GROUP_SUMMARY_LEN[group_id] = len(gh)", inner)
        # 外层函数不得再提前写长度标记
        outer = src[src.index("def summarize_group_history"):src.index("def _summarize_group_history_inner")]
        self.assertNotIn("_GROUP_SUMMARY_LEN[group_id] = len(gh)", outer,
                         "外层又提前提交了长度标记（幽灵标记会回来）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
