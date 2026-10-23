# -*- coding: utf-8 -*-
"""tests/test_search_parsing.py —— SERP 解析回归（2026-10-13 搜索事故）

线上事故：群里问"200块机械键盘推荐"，search_web 返回的全是
「HTTP状态码 200 详解」「200（阿拉伯数字）百度百科」——Bing 免JS端
把 "200" 匹配成了 HTTP 200，旧解析器又把页面上任意 b_caption/<p>
当结果抓，模型拿垃圾结果"搜了个寂寞"，最后 AI决定不回复(空)。

★ 2026-10-23 迁移说明：
  本文件原来测的是 `bot_core._parse_bing_results` / `_parse_sogou_results` /
  `_parse_ddg_results`。那三个函数已经删除（返回平行列表，摘要和链接没有
  归属关系，且线上早就不走了）。但因为删除而**丢掉这份夹具是不行的** ——
  b_algo 块边界、跳过 bing 自家链接、uddg 还原这几条是事故换来的规矩，
  现在的实现是 `web_access._parse_bing` / `_parse_so` / `_parse_ddg`，
  所以把用例整体迁过来，判据一条不减。

  顺带一个收益：不再 import bot_core，测试不会拉起插件系统和守护线程，
  跑一次从十几秒降到几十毫秒。

  `_parse_sogou_results` 随函数一起删除（Sogou 现在需要 JS + 验证码，
  `_own_search` 从来没接它，是纯死代码），对应的搜狗用例也一并移除。
  它当年存在的理由（Bing 把「200元机械键盘」匹配成 HTTP 200）现在由
  `_merge_results` 的多引擎合并覆盖，见 tests/test_web_search.py。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import web_access as wa

BING_FIXTURE = """
<html><body>
<div id="b_results">
  <li class="b_algo"><h2><a href="https://zhuanlan.zhihu.com/p/123">200元以内机械键盘推荐</a></h2>
    <div class="b_caption"><p>这几款键盘性价比极高，适合入门玩家长期使用，手感出色值得购买。</p></div></li>
  <li class="b_algo"><h2><a href="https://www.bilibili.com/video/BV1xx">十款200元机械键盘横评</a></h2>
    <div class="b_caption"><p>从轴体到键帽全面横评，帮你选出最值得买的百元级机械键盘。</p></div></li>
  <div class="b_top"><div class="b_caption"><p>这是侧栏广告文本，不在 b_algo 里，绝不能进结果。</p></div></div>
  <li class="b_algo"><h2><a href="https://cn.bing.com/ad">bing自家链接要跳过</a></h2>
    <div class="b_caption"><p>bing.com 域名的结果链接应当被过滤掉不返回。</p></div></li>
</div></body></html>
"""

DDG_FIXTURE = """
<html><body>
<div class="result results_links">
  <div class="result__body">
    <h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpost.smzdm.com%2Fp%2Fabc&rut=h">200元机械键盘选购指南</a></h2>
    <a class="result__snippet" href="#">五款200元价位最值得入手的机械键盘推荐，覆盖有线与三模无线。</a>
  </div>
</div>
<div class="result">
  <div class="result__body">
    <h2><a class="result__a" href="https://direct.example.com/kb">直接链接的结果</a></h2>
    <a class="result__snippet" href="#">这条结果的链接不需要 uddg 还原处理也能正常解析。</a>
  </div>
</div>
</body></html>
"""


class TestBingOnlyBAlgo(unittest.TestCase):
    """① 只认 b_algo 块（块外广告/侧栏文本一概不要）。"""

    def test_count_excludes_sidebar(self):
        res = wa._parse_bing(BING_FIXTURE)
        self.assertEqual(len(res), 2, [x["title"] for x in res])

    def test_first_result_fields(self):
        res = wa._parse_bing(BING_FIXTURE)
        self.assertIn("机械键盘", res[0]["title"])
        self.assertIn("性价比", res[0]["snippet"])

    def test_self_link_skipped(self):
        res = wa._parse_bing(BING_FIXTURE)
        self.assertFalse(any("cn.bing.com" in r["url"] for r in res))

    def test_sidebar_never_mixed_in(self):
        res = wa._parse_bing(BING_FIXTURE)
        self.assertFalse(any("侧栏广告" in (r["title"] + r["snippet"]) for r in res))

    def test_empty_input(self):
        self.assertEqual(wa._parse_bing(""), [])

    def test_accident_form_returns_empty(self):
        """旧版事故形态：页面里只有非 b_algo 的杂文本 → 必须返回空（换引擎，
        不硬凑），而不是把侧栏/页脚文本当结果。"""
        junk = '<html><div class="b_caption"><p>HTTP状态码 当浏览者访问一个网页时…</p></div></html>'
        self.assertEqual(wa._parse_bing(junk), [])


class TestDdgUddgUnwrap(unittest.TestCase):
    """② DDG 的链接是跳转壳，要抠 uddg 还原真链。"""

    def test_count(self):
        self.assertEqual(len(wa._parse_ddg(DDG_FIXTURE)), 2)

    def test_uddg_unwrapped(self):
        res = wa._parse_ddg(DDG_FIXTURE)
        self.assertEqual(res[0]["url"], "https://post.smzdm.com/p/abc")

    def test_direct_link_kept(self):
        res = wa._parse_ddg(DDG_FIXTURE)
        self.assertEqual(res[1]["url"], "https://direct.example.com/kb")

    def test_snippet_parsed(self):
        res = wa._parse_ddg(DDG_FIXTURE)
        self.assertIn("五款", res[0]["snippet"])


class TestFieldsBelongTogether(unittest.TestCase):
    """★ 2026-10-23 新增：这是重写解析器的唯一目的。

    旧实现返回 (summaries, urls) 两个**平行列表**，摘要 A 配链接 B 是常态。
    并行列表上看不出错 —— 两条都有、都非空、长度也一致 ——
    但把摘要归到结果上时就会错位，正文也挂不到具体某条上。
    """

    def test_title_url_snippet_from_same_block(self):
        res = wa._parse_bing(BING_FIXTURE)
        # 第一条是知乎那条，摘要必须是它自己的「性价比」而不是第二条的「横评」
        self.assertIn("zhuanlan.zhihu.com", res[0]["url"])
        self.assertIn("性价比", res[0]["snippet"])
        self.assertNotIn("横评", res[0]["snippet"])

        self.assertIn("bilibili.com", res[1]["url"])
        self.assertIn("横评", res[1]["snippet"])
        self.assertNotIn("性价比", res[1]["snippet"])

    def test_rank_recorded(self):
        """`_rank_in_source` 是打分主判据，解析时必须记下来。"""
        res = wa._parse_bing(BING_FIXTURE)
        self.assertEqual([x["_rank_in_source"] for x in res], [1, 2])


if __name__ == "__main__":
    unittest.main(verbosity=2)