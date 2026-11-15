# -*- coding: utf-8 -*-
"""插件接管回归：确保「插件开着」真的等于「插件起作用」。

本文件只测 2026-10-21 这轮修的东西，且**不碰生产库**
（super_memory / chat_style 都重定向到 tempfile 沙箱）。
"""
import os
import sys
import json
import tempfile
import unittest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

_SANDBOX = tempfile.mkdtemp(prefix="eggbloom_test_plugins_")


def _redirect_db():
    """把两个会写盘的插件库指到沙箱。

    不重定向的话，跑一次测试就往生产库塞几条假群消息 ——
    这是上一轮真实踩到的坑（affection 表里躺着 6 条 _t_gate_*）。
    """
    import plugins.ego.super_memory as sm
    sm.DB_PATH = os.path.join(_SANDBOX, "super_memory.db")
    try:
        sm._memory._init_db()
    except Exception:
        pass
    import plugins.ego.chat_style as cs
    cs.DB_PATH = os.path.join(_SANDBOX, "chat_style.db")
    try:
        cs._learner._init_db()
    except Exception:
        pass


_redirect_db()


# ═══════════════════════════════════════════════════════════
# 1. mood_engine：早退路径的结构一致性（曾经 KeyError: 'text'）
# ═══════════════════════════════════════════════════════════
class TestMoodShape(unittest.TestCase):
    def setUp(self):
        from plugins.ego import mood_engine as me
        self.me = me

    def test_empty_group_has_text_field(self):
        """没有情绪数据的群，get_mood 也必须带 text —— 否则 get_prompt_block 崩。"""
        m = self.me._mood.get_mood("__never_seen_group__")
        for k in ("label", "text", "score", "boost"):
            self.assertIn(k, m, f"缺字段 {k}: {m}")
        self.assertEqual(m["label"], "neutral")

    def test_disabled_also_has_text_field(self):
        """mood_enabled=False 的早退路径同样不能缺 text。"""
        old = self.me._mood.config.get("mood_enabled")
        try:
            self.me._mood.config["mood_enabled"] = False
            m = self.me._mood.get_mood("any")
            self.assertIn("text", m)
            self.assertEqual(m["label"], "neutral")
        finally:
            self.me._mood.config["mood_enabled"] = old

    def test_prompt_block_does_not_raise(self):
        """平静时不该抛异常，也不该硬塞一句『当前心情』进提示词。"""
        block = self.me._mood.get_prompt_block("__never_seen_group__")
        self.assertEqual(block, "")

    def test_no_duplicate_injection_when_neutral(self):
        """label 若用中文『平静』，会被 `== "neutral"` 判漏而错误注入。"""
        m = self.me._mood.get_mood("__never_seen_group__")
        self.assertNotEqual(m["label"], "平静",
                            "label 必须是内部英文键，中文只放在 text 里")


# ═══════════════════════════════════════════════════════════
# 2. EGO after_reply：人格改写必须能传到发送端
# ═══════════════════════════════════════════════════════════
class TestEgoAfterReply(unittest.TestCase):
    def setUp(self):
        import plugins.ego as ego
        self.ego = ego

    def test_prohibited_removed(self):
        r = self.ego.after_reply(message="你好", group_id="g1",
                                 reply="作为AI，我无法回答这个问题。", context={})
        self.assertIsInstance(r, dict)
        self.assertEqual(r.get("action"), "override")
        self.assertNotIn("作为AI", r["reply"])
        self.assertNotIn("我无法回答", r["reply"])

    def test_clean_reply_not_overridden(self):
        """没有违禁词、长度正常 → 不该返回 override（避免无意义的『改写』日志）。"""
        r = self.ego.after_reply(message="你好", group_id="g1",
                                 reply="这个确实挺离谱的", context={})
        self.assertIsNone(r)

    def test_negation_not_mangled(self):
        """「不建议你这么做」不能被删成「不这么做」——语义会翻转。"""
        r = self.ego.after_reply(message="你好", group_id="g1",
                                 reply="不建议你这么做，建议你换个思路。", context={})
        if isinstance(r, dict):
            self.assertIn("不建议", r["reply"])

    def test_punctuation_tidied(self):
        """删词后不能留下「。 ，」这种撞在一起的标点。"""
        r = self.ego.after_reply(message="你好", group_id="g1",
                                 reply="作为AI，我无法回答这个问题。总的来说，建议你多喝水。",
                                 context={})
        self.assertIsInstance(r, dict)
        self.assertNotIn("。，", r["reply"])
        self.assertNotIn("，。", r["reply"])

    def test_length_capped(self):
        r = self.ego.after_reply(message="你好", group_id="g1",
                                 reply="啊" * 500, context={})
        self.assertIsInstance(r, dict)
        self.assertLessEqual(len(r["reply"]), 241)


# ═══════════════════════════════════════════════════════════
# 3. 违禁词表必须存在且非空（曾经文件根本不存在 = 能力从未生效）
# ═══════════════════════════════════════════════════════════
class TestProhibitedTable(unittest.TestCase):
    def test_file_exists_and_not_empty(self):
        from plugins.ego.personality_engine import load_prohibited_patterns
        pats = load_prohibited_patterns()
        self.assertGreater(len(pats), 10, "违禁句式表为空等于人格禁令全空转")

    def test_core_patterns_present(self):
        from plugins.ego.personality_engine import load_prohibited_patterns
        pats = load_prohibited_patterns()
        for must in ("作为AI", "总的来说", "综上所述"):
            self.assertIn(must, pats)


# ═══════════════════════════════════════════════════════════
# 4. few-shot 示例库：必须非空，且不能乱配
# ═══════════════════════════════════════════════════════════
class TestFewShot(unittest.TestCase):
    def test_examples_non_empty(self):
        from plugins.ego.personality_engine import get_example_retriever
        r = get_example_retriever()
        self.assertGreater(sum(len(v) for v in r.examples.values()), 10,
                           "示例库空着，人格注入就只剩状态标签")

    def test_no_zero_score_match(self):
        """零分场景不能被当作示例注入（原来会随机配货）。"""
        from plugins.ego.personality_engine import get_example_retriever, get_state
        r = get_example_retriever()
        st = get_state("__test_user__")
        got = r.retrieve("随便说点什么吧今天心情还行", st, top_k=2)
        self.assertEqual(got, [], f"不该命中任何场景，却返回了 {got}")

    def test_relevant_match(self):
        from plugins.ego.personality_engine import get_example_retriever, get_state
        r = get_example_retriever()
        st = get_state("__test_user__")
        got = r.retrieve("这新番真好看啊，你们看了吗", st, top_k=2)
        self.assertGreater(len(got), 0)

    def test_state_prompt_has_no_fake_affection(self):
        """personality_engine 的 affection 恒为 0.5，不能再当动态数据注入。"""
        from plugins.ego.personality_engine import get_state
        st = get_state("__test_user__")
        self.assertNotIn("好感度", st.to_prompt(),
                         "真正的好感度在 super_memory，两处同时注入会互相打脸")


# ═══════════════════════════════════════════════════════════
# 5. chat_style：单值特征不得增殖 + 注入质量
# ═══════════════════════════════════════════════════════════
class TestChatStyle(unittest.TestCase):
    SCOPE, UID = "__test_scope__", "__test_uid__"

    def _clean(self):
        import sqlite3
        import plugins.ego.chat_style as cs
        c = sqlite3.connect(cs.DB_PATH)
        c.execute("DELETE FROM style_samples WHERE scope_id=?", (self.SCOPE,))
        c.execute("DELETE FROM style_features WHERE scope_id=?", (self.SCOPE,))
        c.commit()
        c.close()

    def setUp(self):
        self._clean()

    def tearDown(self):
        self._clean()

    def test_single_valued_feature_not_multiplying(self):
        """平均句长的 value 每轮都变，刷新不能一行一行往上堆。"""
        import sqlite3
        from plugins.ego.chat_style import auto_learn
        import plugins.ego.chat_style as cs
        msgs = ["草这破游戏又崩了", "服了服了真服了", "哈哈哈笑死我了啊",
                "麻了彻底麻了属实", "绝了这操作真绝了", "谁懂啊家人们",
                "蚌埠住了", "不是吧又崩了吧", "这破网真行", "有点东西啊这"]
        for m in msgs:
            auto_learn(self.SCOPE, self.UID, m)
        c = sqlite3.connect(cs.DB_PATH)
        n = c.execute("SELECT COUNT(*) FROM style_features WHERE scope_id=? AND feature_type='平均句长'",
                      (self.SCOPE,)).fetchone()[0]
        c.close()
        self.assertLessEqual(n, 1, f"平均句长堆积成 {n} 行")

    def test_meaningless_punct_filtered(self):
        """『标点习惯：，』没有区分度，不该占注入槽位。"""
        from plugins.ego.chat_style import auto_learn
        import plugins.ego.chat_style as cs
        for m in ["草，这破游戏又崩了", "服了，又是这破网", "哈哈哈，笑死我了",
                  "麻了，彻底麻了", "绝了，真绝了", "谁懂，啊家人们"]:
            auto_learn(self.SCOPE, self.UID, m)
        prompt = cs._learner.get_style_prompt(self.SCOPE, self.UID)
        self.assertNotIn("标点习惯：，", prompt)

    def test_per_type_capped(self):
        """同一类型最多 2 条，避免单一维度刷屏。"""
        from plugins.ego.chat_style import auto_learn
        import plugins.ego.chat_style as cs
        for m in ["服了服了服了", "服了又是这破网", "服了真服了",
                  "麻了麻了麻了", "麻了彻底麻了", "麻了真麻了"] * 2:
            auto_learn(self.SCOPE, self.UID, m)
        prompt = cs._learner.get_style_prompt(self.SCOPE, self.UID)
        for line in prompt.split("\n"):
            pass  # 逐行统计交给下面的断言
        counts = {}
        for line in prompt.split("\n"):
            if line.startswith("- "):
                t = line[2:].split("：")[0]
                counts[t] = counts.get(t, 0) + 1
        for t, n in counts.items():
            self.assertLessEqual(n, 2, f"{t} 出现 {n} 次")


# ═══════════════════════════════════════════════════════════
# 6. web_search：强触发词不得被语气词否决
# ═══════════════════════════════════════════════════════════
class TestWebSearchPriority(unittest.TestCase):
    def test_real_question_with_laugh_still_searches(self):
        from plugins.web_search import _needs_search
        self.assertTrue(_needs_search("今天天气怎么样啊哈哈哈"),
                        "语气词不能把真问题吃掉")

    def test_pure_chitchat_not_searched(self):
        from plugins.web_search import _needs_search
        self.assertFalse(_needs_search("哈哈哈哈笑死我了"))

    def test_strong_keyword_wins_over_skip(self):
        from plugins.web_search import _needs_search
        self.assertTrue(_needs_search("在吗，帮我查一下这个"))

    def test_no_intent_not_searched(self):
        from plugins.web_search import _needs_search
        self.assertFalse(_needs_search("这新番真好看啊，你们看了吗"))


# ═══════════════════════════════════════════════════════════
# 7. 插件装载与启停状态
# ═══════════════════════════════════════════════════════════
class TestPluginLoaded(unittest.TestCase):
    def test_all_discovered_loaded(self):
        from plugins.plugin_manager import init_plugin_manager
        pm = init_plugin_manager()
        pm.load_all()
        bad = [x for x in pm.list_plugins()
               if not x.get("_loaded") or x.get("_error")]
        self.assertEqual(bad, [], f"有插件加载失败: {bad}")

    def test_safety_plugins_stay_off(self):
        """capabilities/qzone 会改资料或对外发布，默认必须关。"""
        from plugins.plugin_manager import init_plugin_manager
        pm = init_plugin_manager()
        pm.load_all()
        state = {x["name"]: x.get("enabled") for x in pm.list_plugins()}
        for name in ("capabilities", "qzone"):
            self.assertFalse(state.get(name), f"{name} 不应默认启用")

    def test_ego_has_both_hooks(self):
        from plugins.plugin_manager import init_plugin_manager
        pm = init_plugin_manager()
        pm.load_all()
        # 注意：注册表里的键是**目录名** ego；list_plugins 里的 display name 才是 EGO
        cands = [n for _p, n, _f in pm._hook_candidates("before_reply")]
        self.assertIn("ego", cands)
        self.assertIn("ego", [n for _p, n, _f in pm._hook_candidates("after_reply")])

    def test_after_reply_chain_has_multiple(self):
        """after_reply 必须是链式（多个插件），不能只有一个能跑。"""
        from plugins.plugin_manager import init_plugin_manager
        pm = init_plugin_manager()
        pm.load_all()
        cands = [n for _p, n, _f in pm._hook_candidates("after_reply")]
        self.assertGreaterEqual(len(cands), 2, f"after_reply 候选只有 {cands}")

    def test_proactive_talk_enabled(self):
        """用户 2026-10-21 明确要求开启。"""
        from plugins.plugin_manager import init_plugin_manager
        pm = init_plugin_manager()
        pm.load_all()
        state = {x["name"]: x.get("enabled") for x in pm.list_plugins()}
        self.assertTrue(state.get("proactive_talk"))

    def test_after_reply_is_chained_not_short_circuited(self):
        """after_reply 必须链式：高优先级插件的 override 不能挡住后面的插件。

        这条锁的是 2026-10-21 修的真 bug —— 原来用 execute_hook（单数，
        首个非 None 即返回），ego(优先级 20) 一返回 override，
        web_search(30) 的幻觉拦截就永远不执行。
        """
        from plugins.plugin_manager import init_plugin_manager
        pm = init_plugin_manager()
        pm.load_all()
        txt, chain = pm.execute_hook_chain(
            "after_reply",
            message="这新番真好看啊",
            group_id="__chain_probe__",
            reply="作为AI，我无法回答这个问题。总的来说，建议你多喝水。",
            context={})
        # 人格改写必须生效
        self.assertNotIn("作为AI", txt)
        self.assertIn("ego", chain, f"ego 未参与改写，链={chain}")

    def test_chain_feeds_result_forward(self):
        """后一个插件必须能看见前一个改完的文本（否则无法叠加）。

        做法：临时注册一个探针钩子，记下它收到的 reply，
        再和前面插件改完的结果比对。
        """
        from plugins.plugin_manager import init_plugin_manager
        pm = init_plugin_manager()
        pm.load_all()

        got = {}

        def _probe(message="", group_id="", reply="", context=None, **_kw):
            got["reply"] = reply
            return None

        # 直接插进注册表，priority 设成最大 → 排在所有真实插件之后
        pm._plugins["__probe__"] = {
            "meta": {"name": "__probe__", "enabled": True, "priority": 9999},
            "hooks": {"after_reply": _probe},
            "module": None, "error": None,
        }
        try:
            raw = "作为AI，我无法回答这个问题。"
            txt, _chain = pm.execute_hook_chain(
                "after_reply", message="你好", group_id="__chain_probe2__",
                reply=raw, context={})
            self.assertIn("reply", got, "探针钩子没被调用")
            self.assertNotEqual(got["reply"], raw,
                                "探针收到的仍是原始文本 → 上游改写没传下去")
            self.assertEqual(got["reply"], txt,
                             "探针收到的文本应与最终结果一致")
        finally:
            pm._plugins.pop("__probe__", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)