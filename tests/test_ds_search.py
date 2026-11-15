# -*- coding: utf-8 -*-
"""ds_search 的职责边界与归一化。

★ 2026-10-23 二次清理后，本文件只测两件事：
  ① 官方 schema 的坑位（null 字段、rerank_score 缺失、非 dict 条目）
  ② 没 key 时必须明确「不可用」，不能装成空结果

**不再测排序 / 截断 / 质量判定** —— 那三块已经从本文件删掉，
权威源是 search_core，测试在 tests/test_search_core.py。
留着旧用例会让人以为改排序要改两处（这正是清理前的真实状况）。

不联网。search / fetch 需要真 key，属于集成测试。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ds_search as d
import search_core


class TestNormalize(unittest.TestCase):
    """官方 schema 里 site_name / date / site_icon / authority 都可能是 null。"""

    def test_null_fields(self):
        it = d._normalize_item({"title": "t", "url": "u", "rerank_score": 2.0,
                                "site_name": None, "date": None, "authority": None})
        self.assertEqual(it["site_name"], "")
        self.assertEqual(it["date"], "")
        self.assertEqual(it["authority"], -1)
        self.assertNotIn("None", str(it))

    def test_missing_fields_get_defaults(self):
        it = d._normalize_item({"title": "只有标题"})
        for k in ("url", "snippet", "summary", "content", "site_name", "date"):
            self.assertEqual(it[k], "", k)
        self.assertEqual(it["score"], 0.0)
        self.assertEqual(it["authority"], -1)

    def test_rerank_score_string(self):
        """官方偶尔给字符串。float() 能转是运气，转不了不能炸。"""
        self.assertEqual(d._normalize_item({"rerank_score": "2.5"})["score"], 2.5)
        self.assertEqual(d._normalize_item({"rerank_score": "bad"})["score"], 0.0)
        self.assertEqual(d._normalize_item({"rerank_score": None})["score"], 0.0)

    def test_authority_zero_kept(self):
        """authority=0 是「明确不高」，不能和「未知」混成同一个值。"""
        self.assertEqual(d._normalize_item({"authority": 0})["authority"], 0)
        self.assertEqual(d._normalize_item({"authority": None})["authority"], -1)


class TestToCore(unittest.TestCase):
    """归一到统一结果层。字段名和量纲必须和 SearchResult 对齐。"""

    def _raw(self, **kw):
        base = {
            "title": "《原神》7.1 版本更新说明",
            "url": "https://ys.mihoyo.com/main/news/1",
            "snippet": "全新版本已上线",
            "summary": "长摘要" * 40,
            "content": "",
            "site_name": "ys.mihoyo.com",
            "date": "2026-09-23T10:00:00+08:00",
            "authority": 4,
            "rerank_score": 2.6,
        }
        base.update(kw)
        return base

    def test_score_preserved(self):
        """DeepSeek 的 rerank_score 是它自己的模型判的，不许被本地重算覆盖。"""
        it = d.to_core(self._raw())
        self.assertEqual(it["score"], 2.6)
        it2 = search_core.fill_scores([it], "原神最新版本")[0]
        self.assertEqual(it2["score"], 2.6, "fill_scores 不该覆盖已有分数")

    def test_source_tagged(self):
        self.assertEqual(d.to_core(self._raw())["source"], "ds")

    def test_date_trimmed_to_day(self):
        self.assertEqual(d.to_core(self._raw())["date"], "2026-09-23")

    def test_authority_preserved_not_reestimated(self):
        """官方说 4 就是 4。不许因为域名不在本地白名单里就降级。"""
        it = d.to_core(self._raw(url="https://some-unknown-blog.cn/x", authority=4))
        self.assertEqual(it["authority"], 4)

    def test_site_name_falls_back_to_domain(self):
        it = d.to_core(self._raw(site_name=""))
        self.assertEqual(it["site_name"], "ys.mihoyo.com")

    def test_to_core_list_drops_non_dict(self):
        got = d.to_core_list([self._raw(), None, "junk", 42, self._raw()])
        self.assertEqual(len(got), 2)

    def test_to_core_list_empty_safe(self):
        self.assertEqual(d.to_core_list([]), [])
        self.assertEqual(d.to_core_list(None), [])

    def test_shape_matches_make(self):
        """两条路产出的键集合必须一致，否则下游判空会漏。"""
        mine = d.to_core(self._raw())
        theirs = search_core.make(url="https://a.com/x", title="t", source="bing")
        self.assertEqual(set(mine), set(theirs))


class TestUnavailable(unittest.TestCase):
    """没 key 时必须明确返回「不可用」，不能装成空结果。"""

    def test_available_false_without_key(self):
        orig = d._api_key
        d._api_key = lambda: ""
        try:
            self.assertFalse(d.available())
        finally:
            d._api_key = orig

    def test_search_without_key_raises(self):
        orig = d._api_key
        d._api_key = lambda: ""
        try:
            with self.assertRaises(d.DSSearchError):
                d.search("x")
        finally:
            d._api_key = orig

    def test_blank_query_returns_empty(self):
        self.assertEqual(d.search(""), [])
        self.assertEqual(d.search("   "), [])

    def test_fetch_rejects_non_http(self):
        with self.assertRaises(d.DSSearchError):
            d.fetch("file:///etc/passwd")


class TestNoDuplicateLogic(unittest.TestCase):
    """钉死「一条规则只有一个权威源」。

    这四个函数曾经在本文件里有一份、search_core 里也有一份，
    两边各自演化。删掉之后不许再长回来 —— 加回来就意味着
    「改排序要改两处」，而那种错误不会报错，只会慢慢不同步。
    """

    def test_sorting_lives_only_in_search_core(self):
        for name in ("_sort_key", "rank", "final_score", "format_for_model",
                     "quality_of", "_clip", "_age_bonus", "wants_recency",
                     "_authority_note", "_fmt_date"):
            self.assertFalse(hasattr(d, name), f"ds_search 不该再有 {name}")

    def test_search_core_owns_them(self):
        for name in ("sort_key", "rank", "final_score", "format_for_model",
                     "quality_of", "_clip", "_age_bonus", "wants_recency",
                     "_authority_note", "_fmt_date"):
            self.assertTrue(hasattr(search_core, name), f"search_core 缺 {name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)