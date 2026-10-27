# -*- coding: utf-8 -*-
"""search_core 的排序、日期、权威性、截断、质量判定。

这是搜索的**唯一结果层**：两条搜索路径（自家扒 SERP / DeepSeek 原生）
都转成 SearchResult 交给它，排序、截断、判质量、拼话术全在这里。
所以这里的判据错了，两条路一起错。

用例重点不在"函数能跑通"，而在**几个踩过的坑不许复发**：

  parse_date      只在文本开头找。正文中间的日期是内容里提到的事件时间，
                  拿它当发布日会让 2024 年的文章因为正文提到 2026 而排最前。
  authority_of    宁可低估。给垃圾站打高分，它会在时效加成下冲到第一位，
                  这个错误比"权威的没被认出来"严重得多。
  fill_scores     引擎位置是主判据。第一版只看词面重合，搜「原神最新版本」
                  时「版本历史 - 原神 WIKI」和官方 7.1 页面拿到**一模一样**的分。
  _nav_heavy      wiki 把导航树铺在正文 div 里，去 aside/nav 不够。
                  第一版「短片段占比 ≥55%」实测没拦住。
  final_score     纯分数排 → 旧公告占前三；纯日期排 → 下载站第一。必须混合。
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import search_core as sc


def _it(title="标题", url="https://example.com/a", snippet="", summary="",
        content="", date="", authority=None, score=None, rank=None):
    return sc.make(url=url, title=title, snippet=snippet, summary=summary,
                   content=content, date=date, authority=authority,
                   score=score, rank_in_source=rank)


class TestDomainAndAuthority(unittest.TestCase):
    """按域名估权威性，0~4；判不出来 -1。原则是宁可低估。"""

    def test_domain_strips_www(self):
        self.assertEqual(sc.domain_of("https://www.zhihu.com/question/1"), "zhihu.com")
        self.assertEqual(sc.domain_of("http://a.b.com/x"), "a.b.com")
        self.assertEqual(sc.domain_of(""), "")
        self.assertEqual(sc.domain_of(None), "")

    def test_gov_edu_are_top(self):
        for u in ("https://www.gov.cn/zhengce/1", "https://www.tsinghua.edu.cn/x",
                  "https://www.nih.gov/a", "https://mit.edu/b"):
            self.assertEqual(sc.authority_of(u), 4, u)

    def test_first_party_sources(self):
        for u in ("https://github.com/x/y", "https://ys.mihoyo.com/main/",
                  "https://docs.python.org/3/", "https://api-docs.deepseek.com/z"):
            self.assertEqual(sc.authority_of(u), 3, u)

    def test_edited_media(self):
        for u in ("https://zhuanlan.zhihu.com/p/1", "https://www.bilibili.com/v/1",
                  "https://stackoverflow.com/q/1"):
            self.assertEqual(sc.authority_of(u), 2, u)

    def test_content_farm_is_zero(self):
        """聚合站必须打到 0 —— 它会拿到时效加分，5 分底子就藏不住了。"""
        for u in ("https://wenku.baidu.com/view/1", "https://zhidao.baidu.com/q/1",
                  "https://www.hao123.com/x"):
            self.assertEqual(sc.authority_of(u), 0, u)

    def test_unknown_domain_is_one_not_high(self):
        self.assertEqual(sc.authority_of("https://some-random-blog.cn/p"), 1)

    def test_empty_url(self):
        self.assertEqual(sc.authority_of(""), -1)
        self.assertEqual(sc.authority_of(None), -1)

    def test_subdomain_of_trusted_matches(self):
        """news.mihoyo.com 也该认出来 —— 官网下面挂子域是常态。"""
        self.assertEqual(sc.authority_of("https://news.mihoyo.com/a"), 3)

    def test_lookalike_domain_not_matched(self):
        """github.com.evil.cn 不是 github.com。后缀匹配必须带点。"""
        self.assertEqual(sc.authority_of("https://github.com.evil.cn/x"), 1)


class TestParseDate(unittest.TestCase):
    """日期抽错比没有更糟：错的日期会在时效排序里把无关内容顶上来。"""

    TODAY = datetime.date(2026, 10, 23)

    def test_chinese_date(self):
        got = sc.parse_date("2026年9月23日 · 全新版本已推出", today=self.TODAY)
        self.assertEqual(got, "2026-09-23")

    def test_iso_date(self):
        self.assertEqual(sc.parse_date("2026-09-23 更新", today=self.TODAY), "2026-09-23")
        self.assertEqual(sc.parse_date("2026/09/23", today=self.TODAY), "2026-09-23")
        self.assertEqual(sc.parse_date("2026.09.23", today=self.TODAY), "2026-09-23")

    def test_relative_hours(self):
        self.assertEqual(sc.parse_date("11 小时之前 · 某新闻", today=self.TODAY),
                         "2026-10-22")

    def test_relative_days(self):
        self.assertEqual(sc.parse_date("3 天之前", today=self.TODAY), "2026-10-20")

    def test_relative_words(self):
        self.assertEqual(sc.parse_date("昨天 · 消息", today=self.TODAY), "2026-10-22")
        self.assertEqual(sc.parse_date("今天 09:00", today=self.TODAY), "2026-10-23")

    def test_date_in_middle_of_text_ignored(self):
        """★ 这是本函数存在的理由。

        「维护公告」正文里提到 2026-09-23 是维护开始时间，
        但这篇文章可能发于 2024 年。拿它当发布日 → 排到最前。
        """
        text = "这是一篇很久以前写的文章" * 6 + "，其中提到 2026-09-23 开始维护"
        self.assertEqual(sc.parse_date(text, today=self.TODAY), "")

    def test_future_date_rejected(self):
        """预发布页会写未来日期。当成发布日会让它永远排第一。"""
        self.assertEqual(sc.parse_date("2099年1月1日", today=self.TODAY), "")

    def test_md_without_year_rolls_back(self):
        """只有月日时按今年算；算出来在未来说明是去年的。"""
        self.assertEqual(sc.parse_date("12月31日", today=self.TODAY), "2025-12-31")
        self.assertEqual(sc.parse_date("01月05日", today=self.TODAY), "2026-01-05")

    def test_empty_and_junk(self):
        for x in ("", None, "   ", "没有日期的一段话", "第3期 共20页"):
            self.assertEqual(sc.parse_date(x, today=self.TODAY), "", repr(x))

    def test_out_of_range_date_skipped(self):
        self.assertEqual(sc.parse_date("2026年13月45日", today=self.TODAY), "")


class TestFillScores(unittest.TestCase):
    """引擎位置是主判据。这是第一版失败的地方。"""

    def test_engine_position_dominates(self):
        """★ 第一版的失败用例。

        搜「原神最新版本」：
          - 官方页标题写「全新7.1版本」，和「最新」零字重合
          - wiki 页标题写「版本 历史」，反而有几个二元组撞上
        只看词面 → 两者同分（实测都是 0.34）。真正靠谱的信号是引擎把它排第几。
        """
        official = _it(title="《原神》官方网站-全新7.1版本上线",
                       url="https://ys.mihoyo.com/", snippet="全新版本已推出",
                       authority=3, rank=1)
        wiki = _it(title="版本 历史 - 原神 WIKI",
                   url="https://wiki.biligame.com/ys/版本历史", snippet="本条目介绍版本历史",
                   authority=1, rank=3)
        got = sc.fill_scores([official, wiki], "原神最新版本")
        self.assertGreater(got[0]["score"], got[1]["score"])

    def test_rank_1_scores_higher_than_rank_8(self):
        a = sc.fill_scores([_it(rank=1)], "原神")
        b = sc.fill_scores([_it(rank=8)], "原神")
        self.assertGreater(a[0]["score"], b[0]["score"])

    def test_score_capped_at_three(self):
        """与 DeepSeek 的 rerank_score 同量纲，上限 3.0，阈值才能共用。"""
        it = _it(title="原神最新版本", snippet="原神最新版本更新公告",
                 url="https://ys.mihoyo.com/", rank=1)
        got = sc.fill_scores([it], "原神最新版本")[0]
        self.assertLessEqual(got["score"], 3.0)

    def test_existing_score_not_overwritten(self):
        """DeepSeek 的 rerank_score 是模型判的，不许被本地词面重算覆盖。"""
        it = _it(score=2.6, rank=9)
        self.assertEqual(sc.fill_scores([it], "x")[0]["score"], 2.6)

    def test_missing_rank_uses_middle(self):
        """没有位置信息（手写数据）当中间位置，不能当第一名。"""
        got = sc.fill_scores([_it(rank=None)], "原神")[0]
        top = sc.fill_scores([_it(rank=1)], "原神")[0]
        self.assertLess(got["score"], top["score"])
        self.assertGreater(got["score"], 0)

    def test_irrelevant_rank_1_not_good(self):
        """★ 实测缺陷：完全无关的结果排第 1 就拿到了优质线。

        搜「恋人不行这个动画好看吗」，Bing 第 1 条是「135编辑器」（排版工具）。
        词面重合 0.0，但"排第 1"给了满 1.5 分 —— 正好等于 GOOD_SCORE，
        quality_of 判成 good，模型于是拿这条去回答"这部动画好看吗"。

        引擎排第 1 不等于相关：广告位、站点兜底页、引擎猜的意图都会排前面。
        所以位置分要按相关度打折。
        """
        it = _it(title="135编辑器 - 专业公众号排版工具_微信公众号编辑器_智能AI排版",
                 snippet="135编辑器是超2000万运营者首选的微信编辑器。",
                 url="https://www.135editor.com/", rank=1)
        got = sc.fill_scores([it], "恋人不行 动画")[0]
        self.assertLess(got["score"], sc.GOOD_SCORE, got["score"])
        self.assertEqual(sc.quality_of([got])["verdict"], "thin")

    def test_relevant_rank_1_still_good(self):
        """打折不能伤到正常命中 —— 官方结果该是 good 还得是 good。"""
        it = _it(title="《原神》官方网站-全新7.1版本上线", snippet="全新版本已推出",
                 url="https://ys.mihoyo.com/", rank=1)
        got = sc.fill_scores([it], "原神最新版本")[0]
        self.assertGreaterEqual(got["score"], sc.GOOD_SCORE, got["score"])

    def test_perfect_match_keeps_full_position_weight(self):
        """完全命中时位置分不该被打折（0.4+0.6×1.0 = 1.0）。"""
        it = _it(title="MaiBot 文档中心", snippet="MaiBot 是一个聊天机器人框架",
                 url="https://docs.mai-mai.org/", rank=1)
        got = sc.fill_scores([it], "MaiBot")[0]
        self.assertAlmostEqual(got["score"], 3.0, places=2)

    def test_authority_contributes(self):
        hi = sc.fill_scores([_it(url="https://ys.mihoyo.com/", rank=5)], "原神")[0]
        lo = sc.fill_scores([_it(url="https://random-blog.cn/", rank=5)], "原神")[0]
        self.assertGreater(hi["score"], lo["score"])

    def test_does_not_mutate_input(self):
        it = _it(rank=1)
        sc.fill_scores([it], "原神")
        self.assertEqual(it["score"], 0.0)


class TestRelevance(unittest.TestCase):
    def test_title_weighs_more_than_snippet(self):
        in_title = sc.relevance("原神最新版本公告", "", "原神最新版本")
        in_snippet = sc.relevance("", "原神最新版本公告", "原神最新版本")
        self.assertGreater(in_title, in_snippet)

    def test_no_overlap_is_zero(self):
        self.assertEqual(sc.relevance("今天天气不错", "适合出门", "原神版本"), 0.0)

    def test_empty_query(self):
        self.assertEqual(sc.relevance("标题", "摘要", ""), 0.0)

    def test_bounded(self):
        for t in ("原神", "原神最新版本公告原文全在这里", ""):
            self.assertLessEqual(sc.relevance(t, t, "原神最新版本"), 1.0)


class TestRecency(unittest.TestCase):
    def test_detects(self):
        for q in ("最新版本", "现在几点", "今天有什么新闻", "最近发布的",
                  "最新版下载", "刚更新了什么", "7.1 上线了吗"):
            self.assertTrue(sc.wants_recency(q), q)

    def test_not_detects(self):
        for q in ("原神是什么", "版本历史", "怎么评价这个", "MaiBot 架构"):
            self.assertFalse(sc.wants_recency(q), q)

    # ── 2026-10-27 真机事故 ──────────────────────────────────
    #  用户问「今年明日方舟周年半周年限定是谁」，模型把检索词改写成
    #  「明日方舟 2026 半周年 限定 干员」——「今年」被改写掉了，
    #  时效加权整个失效（wants_recency=False），2025 年旧闻压过 2026 年新消息，
    #  模型据此答「十一月的还没宣」，而 11 月的消息就在搜索结果里。

    def test_year_number_triggers_recency(self):
        """年份数字本身是时效信号（比「今年」更精确）。"""
        for q in ("明日方舟 2026 半周年 限定 干员",
                  "明日方舟 2026 感谢庆典 限定干员 是谁",
                  "原神 2025 版本"):
            self.assertTrue(sc.wants_recency(q), q)

    def test_year_extraction(self):
        self.assertEqual(sc.query_years("明日方舟 2026 半周年"), {"2026"})
        self.assertEqual(sc.query_years("2025 和 2026 都看看"), {"2025", "2026"})
        self.assertEqual(sc.query_years("没有年份"), set())
        # 不是年份的数字不能误判
        self.assertEqual(sc.query_years("明日方舟 版本 3.8"), set())
        self.assertEqual(sc.query_years("QQ 3865661256"), set())

    def test_stale_year_verdict(self):
        """问 2026、结果全是 2025 → 判 stale_year（不是 good）。"""

        def _it(date, score=2.0):
            return {"date": date, "title": "t", "summary": "s", "score": score}

        items = [_it("2025-06-18"), _it("2025-07-16")]
        r = sc.quality_of(items, "明日方舟 2026 半周年 限定 干员")
        self.assertEqual(r["verdict"], "stale_year", r)
        self.assertIn("2026", " ".join(r["reasons"]))

    def test_stale_year_not_triggered_when_one_matches(self):
        """只要有一条年份对得上，就不该判 stale_year。"""

        def _it(date, score=2.0):
            return {"date": date, "title": "t", "summary": "s", "score": score}

        items = [_it("2025-06-17"), _it("2026-10-17")]
        r = sc.quality_of(items, "明日方舟 2026 感谢庆典 限定干员 是谁")
        self.assertNotEqual(r["verdict"], "stale_year", r)

    def test_stale_year_hint_tells_model_to_research(self):
        """提示必须明确让模型换词再搜，别拿别的年份下结论。"""

        def _it(date, score=2.0):
            return {"date": date, "title": "t", "summary": "s", "score": score}

        info = sc.quality_of([_it("2025-06-18")], "明日方舟 2026 半周年")
        hint = sc.quality_hint(info)
        self.assertIn("年份对不上", hint)
        self.assertIn("再搜一次", hint)
        self.assertIn("不要", hint)

    def test_no_year_in_query_never_stale(self):
        """问句没提年份 → 不判 stale_year（不冤枉 timeless 内容）。"""

        def _it(date, score=2.0):
            return {"date": date, "title": "t", "summary": "s", "score": score}

        items = [_it("2025-06-18"), _it("2020-01-01")]
        r = sc.quality_of(items, "弗洛伊德 精神分析引论")
        self.assertNotEqual(r["verdict"], "stale_year", r)

    def test_single_authority_for_wants_recency(self):
        """★ 一条规则只能有一个权威源。

        这次改动真的踩到了：search_core.py 里同时存在两个 `def wants_recency`
        —— 后一个（旧版）把前一个（带年份判断的）整个覆盖掉，
        改完看起来生效、实则没有。仓库已因「同一概念两处判据」踩过多次。
        """
        import io
        import os
        src = io.open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "search_core.py"), encoding="utf-8").read()
        self.assertEqual(src.count("def wants_recency("), 1,
                         "wants_recency 有多个定义，后者会覆盖前者")
        self.assertEqual(src.count("def query_years("), 1)
        self.assertEqual(src.count("def year_mismatch_count("), 1)

    def test_age_bonus_unknown(self):
        for x in ("", None, "bad", "0000-00-00"):
            self.assertEqual(sc._age_bonus(x), 0.0, repr(x))

    def test_age_bonus_recent_full(self):
        today = datetime.date(2026, 10, 23)
        d = (today - datetime.timedelta(days=3)).isoformat()
        self.assertAlmostEqual(sc._age_bonus(d, today=today), sc._RECENCY_BONUS_MAX)

    def test_age_bonus_old_zero(self):
        today = datetime.date(2026, 10, 23)
        d = (today - datetime.timedelta(days=200)).isoformat()
        self.assertEqual(sc._age_bonus(d, today=today), 0.0)

    def test_age_bonus_capped(self):
        """时效加分必须有上限，否则会盖过相关度。"""
        self.assertLessEqual(sc._RECENCY_BONUS_MAX, 0.5)


class TestFinalScore(unittest.TestCase):
    """踩出来的三条规矩，一条一个用例。"""

    def test_recency_boosts_when_asked_latest(self):
        """问「最新」时，新且基本对题的能压过分略高但旧的。"""
        old = _it(title="原神 5 月版本公告", score=2.46, date="2026-05-17")
        new = _it(title="原神 7.1 版本更新说明", score=2.22, date="2026-09-22")
        got = sc.rank([old, new], "原神最新版本")
        self.assertEqual(got[0]["title"], "原神 7.1 版本更新说明")

    def test_recency_does_not_beat_clearly_better_match(self):
        """补丁不能吃掉主判据：分差明显时，新的顶不过对的。"""
        good = _it(title="完全对题的旧文档", score=3.0, date="2024-01-01")
        weak = _it(title="沾点边的新页面", score=1.0, date="2026-10-20")
        self.assertEqual(sc.rank([good, weak], "原神最新版本")[0]["title"],
                         "完全对题的旧文档")

    def test_download_site_demoted(self):
        """下载站的日期永远最新（页面天天重抓），但不回答「最新版本是什么」。"""
        dl = _it(title="原神app-官方正版软件2026最新版游戏免费下载",
                 score=2.44, date="2026-10-16")
        info = _it(title="《原神》7.1 版本更新说明", score=2.22, date="2026-09-22")
        self.assertEqual(sc.rank([dl, info], "原神最新版本")[0]["title"],
                         "《原神》7.1 版本更新说明")

    def test_download_site_kept_when_asked_for(self):
        """问「在哪下载」时下载站就是答案，不许降权。"""
        dl = _it(title="原神官方下载", score=2.5, date="2026-10-16")
        other = _it(title="原神游戏介绍", score=2.4, date="2026-10-16")
        self.assertEqual(sc.rank([dl, other], "原神在哪下载")[0]["title"],
                         "原神官方下载")

    def test_no_recency_no_bonus(self):
        """不问「最新」时不加时效分 —— 否则任何问题都变成新闻排序。"""
        a = _it(title="a", score=2.0, date="2026-10-20")
        b = _it(title="b", score=2.0, date="2020-01-01")
        self.assertAlmostEqual(sc.final_score(a, "原神是什么"),
                               sc.final_score(b, "原神是什么"))

    def test_rank_returns_new_list(self):
        items = [_it(title="a", score=1.0), _it(title="b", score=2.0)]
        got = sc.rank(items, "x")
        self.assertEqual(items[0]["title"], "a", "不许就地改原列表")
        self.assertEqual(got[0]["title"], "b")

    def test_rank_empty(self):
        self.assertEqual(sc.rank([], "x"), [])
        self.assertEqual(sc.rank(None, "x"), [])


class TestClipAndFormat(unittest.TestCase):
    def test_clip_at_sentence_boundary(self):
        """按句截，不在字中间切 —— 半句话会被模型当成完整信息读。"""
        text = "第一句话说完了。" + "第二句很长" * 200
        got = sc._clip(text, 60)
        self.assertTrue(got.endswith("。") or got.endswith("…"), got[-20:])
        self.assertLessEqual(len(got), 61)

    def test_clip_short_untouched(self):
        self.assertEqual(sc._clip("很短", 100), "很短")

    def test_clip_hard_cut_when_no_punctuation(self):
        got = sc._clip("很长的一段没有标点" * 50, 30)
        self.assertLessEqual(len(got), 31)
        self.assertTrue(got.endswith("…"))

    def test_clip_empty(self):
        self.assertEqual(sc._clip("", 10), "")
        self.assertEqual(sc._clip(None, 10), "")

    def test_format_empty(self):
        self.assertEqual(sc.format_for_model([], "x"), "")
        self.assertEqual(sc.format_for_model(None, "x"), "")

    def test_respects_max_items(self):
        items = [_it(title=f"标题{i}", score=2.0 - i * 0.1, summary="内容" * 100)
                 for i in range(10)]
        out = sc.format_for_model(items, "x", max_items=3)
        self.assertIn("1. ", out)
        self.assertNotIn("4. ", out)

    def test_total_budget(self):
        items = [_it(title=f"标题{i}", summary="内容" * 2000) for i in range(10)]
        out = sc.format_for_model(items, "x", max_items=10)
        self.assertLessEqual(len(out), sc.TOTAL_BUDGET + 400)

    def test_authority_note_only_when_notable(self):
        self.assertEqual(sc._authority_note(4), "官方/权威来源")
        self.assertEqual(sc._authority_note(3), "官方/权威来源")
        for a in (2, 1, 0, -1, None, "x"):
            self.assertEqual(sc._authority_note(a), "", repr(a))

    def test_date_trimmed(self):
        it = _it(date="2026-09-22T14:11:04+00:00", summary="内容")
        out = sc.format_for_model([it], "x")
        self.assertIn("2026-09-22", out)
        self.assertNotIn("14:11", out)

    def test_summary_preferred_over_snippet(self):
        it = _it(summary="这是长摘要内容", snippet="这是短摘要")
        out = sc.format_for_model([it], "x", with_content=False)
        self.assertIn("长摘要", out)
        self.assertNotIn("短摘要", out)

    def test_falls_back_to_snippet(self):
        it = _it(summary="", snippet="只有短摘要")
        self.assertIn("短摘要", sc.format_for_model([it], "x", with_content=False))

    def test_content_rendered_when_present(self):
        it = _it(summary="摘要一句话", content="这是与摘要不同的一段正文内容，补充了细节。" * 8)
        out = sc.format_for_model([it], "x")
        self.assertIn("正文：", out)

    def test_short_content_skipped(self):
        """「菜单 回到顶部」这类骨架残留不该带上，`正文：` 前缀也不该出现。

        实测缺陷：搜「MaiBot」，第 2 条抽到的正文只有「菜单 回到顶部 Ar」。
        抽正文没抽到东西，但前缀照样打出去了 —— 模型会以为那就是正文。
        """
        it = _it(summary="这是一段足够长的摘要内容", content="菜单 回到顶部 Ar")
        out = sc.format_for_model([it], "x")
        self.assertNotIn("正文：", out)
        self.assertNotIn("回到顶部", out)

    def test_content_duplicate_of_summary_skipped(self):
        """★ 实测缺陷：正文跟摘要说的是一回事，带上去纯占预算。

        搜「原神最新版本」，wiki 那条摘要已经说了版本历史，正文又是
        800 字的「大版本每年一次、每个大版本 9 个小版本…」，没回答"最新是几"。
        """
        same = "原神的大版本由一到开始，每年更新一次，每个大版本分为九个小版本。"
        it = _it(summary=same * 4, content=same * 6)
        out = sc.format_for_model([it], "x")
        self.assertNotIn("正文：", out)

    def test_content_kept_when_it_adds_information(self):
        """正文确实有新信息时必须保留 —— 这是补细节的唯一通道。"""
        it = _it(summary="原神 7.1 已于九月二十三日上线。",
                 content="维护时间为 2026 年 9 月 23 日 06:00 至 11:00，"
                         "维护结束后发放原石补偿三百个，同时开启限定祈愿卡池。" * 3)
        out = sc.format_for_model([it], "x")
        self.assertIn("正文：", out)
        self.assertIn("原石", out)

    def test_code_template_content_rejected(self):
        """★ 实测缺陷：网易云音乐那条抓到的是前端模板。

        搜「恋人不行这个动画好看吗」，某条结果的正文是
        `${escape(x.user.nickname)} {if x.user.avatarDetail && ...} {/if}` ——
        页面把模板字符串写在了 HTML 文本里，抽正文时它文字最多、链接最少，
        正好中了"正文"的判据。拿给模型等于乱码。
        """
        junk = ('${escape(x.user.nickname)} {if x.user.avatarDetail > 0 && '
                'x.user.avatarDetail.identityIconUrl} {/if} {if x.user.vipRights} '
                'function(x){ return x.redVipLevel == 1 } {elseif} '
                'document.getElementById("app")') * 3
        it = _it(summary="这是一段正常的摘要内容", content=junk)
        out = sc.format_for_model([it], "x")
        self.assertNotIn("正文：", out)
        self.assertNotIn("escape(", out)

    def test_normal_tech_article_not_mistaken_for_code(self):
        """技术博客里出现 return / const 是正常的，不能一刀切。"""
        text = ("这个函数返回 Promise，调用方用 await 拿结果。"
                "如果不想用 return 也可以直接抛异常，const 声明的变量不可重新赋值。") * 4
        self.assertFalse(sc._looks_like_code(text))

    def test_no_none_leaks(self):
        """字段缺失不能拼出 'None' —— 模型会把它当成内容读。"""
        out = sc.format_for_model([_it(title="t", summary="s")], "x")
        self.assertNotIn("None", out)


class TestQuality(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(sc.quality_of([])["verdict"], "empty")
        self.assertEqual(sc.quality_of(None)["verdict"], "empty")

    def test_good(self):
        r = sc.quality_of([_it(score=1.5, summary="内容"), _it(score=2.0, summary="内容")])
        self.assertEqual(r["verdict"], "good")
        self.assertEqual(r["good_count"], 2)
        self.assertEqual(r["reasons"], [])

    def test_thin_when_all_below_threshold(self):
        r = sc.quality_of([_it(score=1.0, summary="内容"), _it(score=1.2, summary="内容")])
        self.assertEqual(r["verdict"], "thin")
        self.assertTrue(r["reasons"])

    def test_boundary_at_good_score(self):
        self.assertEqual(sc.quality_of([_it(score=sc.GOOD_SCORE, summary="x")])["verdict"],
                         "good")
        self.assertEqual(
            sc.quality_of([_it(score=sc.GOOD_SCORE - 0.01, summary="x")])["verdict"],
            "thin")

    def test_no_text_is_empty_even_with_high_score(self):
        """分数再高，一个字都没有就是没法用。"""
        r = sc.quality_of([_it(score=3.0, summary="", snippet="", content="")])
        self.assertEqual(r["verdict"], "empty")

    def test_top_score_reported(self):
        r = sc.quality_of([_it(score=1.1, summary="x"), _it(score=2.7, summary="y")])
        self.assertAlmostEqual(r["top_score"], 2.7)

    def test_hint_silent_when_good(self):
        self.assertEqual(sc.quality_hint(sc.quality_of([_it(score=2.5, summary="x")])), "")

    def test_hint_present_when_thin(self):
        h = sc.quality_hint(sc.quality_of([_it(score=0.5, summary="x")]))
        self.assertIn("结果质量", h)
        self.assertIn("不要凭常识补全", h)

    def test_hint_present_when_empty(self):
        h = sc.quality_hint(sc.quality_of([]))
        self.assertIn("结果质量", h)

    def test_hint_unknown_verdict_silent(self):
        self.assertEqual(sc.quality_hint({}), "")
        self.assertEqual(sc.quality_hint(None), "")
        self.assertEqual(sc.quality_hint({"verdict": "weird"}), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)