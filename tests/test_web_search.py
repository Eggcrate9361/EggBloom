# -*- coding: utf-8 -*-
"""web_access 的查询词提炼、SERP 解析、合并去重、主从接线。

四块的风险方向不同，用例也分开写：

  normalize_query  提炼过头会把实体删掉（「明天天气」被删成「天气」就搜不到
                   日期相关的答案），所以「宁可少删」的用例比「删干净」重要。
  _parse_*         解析器改成纯函数就是为了能在这里喂 HTML 夹具。
                   2026-10-13 的事故定下两条规矩：只认 b_algo 块、
                   跳过搜索引擎自家链接。没有测试盯着，改版就又回去了。
  _merge_results   去重过度会丢结果，去重不足会把同一篇抓两遍。
                   位置交错是 _rank_in_source 站得住的前提。
  主从接线         自家优先、DeepSeek 兜底。分支判错会白花钱或白等超时。

全部离线。DeepSeek 和抓取入口都被替身顶掉。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import search_core as sc
import web_access as wa


class TestNormalizeQuery(unittest.TestCase):
    """整句直接搜，引擎会把问句里的虚词当主体。"""

    def test_strip_polite_prefix(self):
        self.assertEqual(wa.normalize_query("帮我查一下原神最新版本"), "原神最新版本")

    def test_strip_multi_layer(self):
        self.assertEqual(wa.normalize_query("请问能帮我查一下MaiBot是什么吗"), "MaiBot")

    def test_strip_know_you_prefix(self):
        self.assertEqual(wa.normalize_query("你知道MaiBot吗"), "MaiBot")

    def test_strip_trailing_punct(self):
        self.assertEqual(wa.normalize_query("原神7.1有什么新内容？"), "原神7.1有什么新内容")

    def test_short_input_returns_original(self):
        """一个字的搜索词没有提炼空间，动了就是错。"""
        self.assertEqual(wa.normalize_query("今"), "今")
        self.assertEqual(wa.normalize_query("啊"), "啊")

    def test_empty_safe(self):
        self.assertEqual(wa.normalize_query(""), "")
        self.assertEqual(wa.normalize_query(None), "")

    def test_keeps_entity_untouched(self):
        """实体（型号、人名、专有名词）必须原样留下。"""
        q = "deepseek-v3 和 gpt-4o 哪个便宜"
        out = wa.normalize_query(q)
        self.assertIn("deepseek-v3", out)
        self.assertIn("gpt-4o", out)

    def test_never_returns_blank_for_nonblank_input(self):
        """提炼逻辑再怎么删，也不能把一句话删成空串。"""
        for q in ("帮我查一下", "请问", "你知道", "是什么", "怎么样"):
            self.assertTrue(wa.normalize_query(q), q)

    def test_single_char_particle_not_subbed_mid_word(self):
        """★ 单字语气词只能剥句尾，不能全文替换。

        `replace("么", " ")` 会把「有什么新内容」切成「有什 新内容」，
        提炼出来的检索词直接搜不到东西。
        """
        out = wa.normalize_query("原神7.1有什么新内容")
        self.assertIn("什么", out)
        self.assertNotIn("有什 新", out)


class TestAttachContents(unittest.TestCase):
    """给排在前面的结果抓正文。

    2026-10-25 实测：5 个查询共 15 条结果，只有 7 条抓到（47%）。
    B站、知乎整类抓不到（反爬）。原来这些失败**不留任何痕迹** ——
    返回空串就跳过，看起来像"没配这个功能"，坏了也没人知道。
    """

    def setUp(self):
        self._orig_fetch = wa._fetch_html
        self._orig_block = wa._block_text
        wa._fetch_html = lambda url, timeout=15, headers=None: ""

    def tearDown(self):
        wa._fetch_html = self._orig_fetch
        wa._block_text = self._orig_block

    def _capture(self, fn):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = fn()
        return out, buf.getvalue()

    def test_attaches_content_to_its_own_item(self):
        """正文挂在结果自己身上，不是回一串无归属的文本。"""
        wa._fetch_html = lambda url, timeout=15, headers=None: "<html>x</html>"
        wa._block_text = lambda html: "这是一段抓到的正文内容"
        items = [sc.make(url="https://a.example/1", title="A", snippet="s1"),
                 sc.make(url="https://b.example/2", title="B", snippet="s2")]
        out, log = self._capture(lambda: wa._attach_contents(items, max_pages=2))
        self.assertEqual(out[0]["content"], "这是一段抓到的正文内容")
        self.assertEqual(out[1]["content"], "这是一段抓到的正文内容")
        self.assertEqual(log.strip(), "", "全部成功时不该打日志")

    def test_failure_is_logged_with_host(self):
        """抓不到页面时必须留一行日志 —— 静默失败没法排查。"""
        items = [sc.make(url="https://www.bilibili.com/video/BV1x",
                         title="B站", snippet="s")]
        out, log = self._capture(lambda: wa._attach_contents(items, max_pages=1))
        self.assertEqual(out[0].get("content") or "", "")
        self.assertIn("正文抓取失败", log)
        self.assertIn("bilibili.com", log, log)
        self.assertIn("抓不到页面", log, log)

    def test_no_text_blocks_logged_differently(self):
        """抓到了页面但抽不出正文，原因要和"抓不到"分开 —— 排查方向不一样。"""
        wa._fetch_html = lambda url, timeout=15, headers=None: "<html></html>"
        wa._block_text = lambda html: ""
        items = [sc.make(url="https://a.example/1", title="A", snippet="s")]
        out, log = self._capture(lambda: wa._attach_contents(items, max_pages=1))
        self.assertIn("页面无正文块", log, log)

    def test_unusable_url_not_counted(self):
        """不该抓的 URL（360 导航页）不进目标，也不该算作失败。"""
        items = [sc.make(url="https://ranks.hao.360.com/x", title="360导航", snippet="s")]
        out, log = self._capture(lambda: wa._attach_contents(items, max_pages=3))
        self.assertEqual(log.strip(), "", "过滤掉的不算失败")


class TestUsableUrl(unittest.TestCase):
    """抓正文之前先筛 URL。360 返回相对路径，旧实现直接喂 urllib 就报错。"""

    def test_reject_relative(self):
        self.assertFalse(wa._is_usable_url("/s?q=aibot官网入口&src=related"))

    def test_reject_search_page_itself(self):
        for u in ("https://www.bing.com/search?q=x",
                  "https://www.so.com/s?q=x",
                  "https://www.baidu.com/s?wd=x",
                  "https://duckduckgo.com/?q=x"):
            self.assertFalse(wa._is_usable_url(u), u)

    def test_reject_javascript(self):
        self.assertFalse(wa._is_usable_url("javascript:void(0)"))

    def test_reject_360_own_pages(self):
        """★ 2026-10-25：原来只过滤 360.cn，360.com 的导航/榜单页全漏进来。

        实测一条查询的第 3 位就是 `ranks.hao.360.com` —— 它不是结果，
        是 360 自己的榜单页，还占掉一个抓正文的名额。
        """
        for u in ("https://ranks.hao.360.com/abc",
                  "https://hao.360.com/?src=x",
                  "https://www.360.com/x"):
            self.assertFalse(wa._is_usable_url(u), u)

    def test_host_match_not_substring(self):
        """按域名后缀匹配，不是整条 URL 子串 —— 否则路径里带 bing.com 会被误杀。"""
        self.assertTrue(wa._is_usable_url("https://example.com/?ref=bing.com"),
                        "路径里出现 bing.com 不代表这是 Bing 的页面")
        # 子域仍然要拦
        self.assertFalse(wa._is_usable_url("https://cn.bing.com/search?q=x"))

    def test_accept_normal(self):
        for u in ("https://ys.mihoyo.com/main/",
                  "https://zhuanlan.zhihu.com/p/123",
                  "http://example.com/a"):
            self.assertTrue(wa._is_usable_url(u), u)


BING_FIXTURE = """
<html><body>
<div id="b_results">
  <li class="b_algo"><h2><a href="https://zhuanlan.zhihu.com/p/123">200元以内机械键盘推荐</a></h2>
    <div class="b_caption"><p>这几款键盘性价比极高，适合入门玩家长期使用，手感出色值得购买。</p></div>
    <div class="news_dt">2026-09-23</div></li>
  <li class="b_algo"><h2><a href="https://www.bilibili.com/video/BV1xx">十款200元机械键盘横评</a></h2>
    <div class="b_caption"><p>从轴体到键帽全面横评，帮你选出最值得买的百元级机械键盘。</p></div></li>
  <div class="b_top"><div class="b_caption"><p>这是侧栏广告文本，不在 b_algo 里，绝不能进结果。</p></div></div>
  <li class="b_algo"><h2><a href="https://cn.bing.com/ad">bing自家链接要跳过</a></h2>
    <div class="b_caption"><p>bing.com 域名的结果链接应当被过滤掉不返回。</p></div></li>
</div></body></html>
"""

SO_FIXTURE = """
<html><body>
<ul>
  <li class="res-list" data-result-type="normal" data-result-pos="1">
    <h3 class="res-title"><a href="https://www.so.com/link?m=AAAABBBBCCCC" data-mdurl="https://zhuanlan.zhihu.com/p/456" rel="noopener" target="_blank"><em>机械键盘</em>怎么选</a></h3>
    <p class="res-desc">从轴体到键帽，这份<em>选购指南</em>覆盖了入门玩家最关心的几个点。</p></li>
  <li class="res-list" data-result-type="normal" data-result-pos="2">
    <h3 class="res-title"><a href="https://www.so.com/link?m=DDDDEEEEFFFF" rel="noopener" target="_blank">只有跳转壳、没有 data-mdurl 的块</a></h3>
    <p class="res-desc">这种块不返回结果，因为解跳转壳拿到的是广告兜底页。</p></li>
  <li class="res-list" data-result-type="normal" data-result-pos="3">
    <h3 class="res-title"><a href="https://www.so.com/link?m=GGGGHHHH" data-mdurl="https://image.so.com/i?src=360pic_text" target="_blank">360 站内图片页</a></h3>
    <p class="res-desc">data-mdurl 指向 360 自己家的页面，仍然要过滤掉。</p></li>
  <li class="res-list" data-result-type="normal" data-result-pos="5">
    <h3 class="res-title"><a href="https://www.so.com/link?m=IIIIJJJJ" data-mdurl="https://www.9game.cn/news/12002327.html" target="_blank">好玩的胖鱼游戏有哪些</a></h3>
    <p class="res-desc">人气最高的胖鱼游戏榜单介绍。</p></li>
</ul></body></html>
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


class TestParseBing(unittest.TestCase):
    """2026-10-13 事故钉死：只认 b_algo 块。"""

    def test_only_b_algo(self):
        res = wa._parse_bing(BING_FIXTURE)
        self.assertEqual(len(res), 2, [x["title"] for x in res])
        self.assertFalse(any("侧栏广告" in (x["title"] + x["snippet"]) for x in res))

    def test_fields_attached_to_same_result(self):
        """★ 这是重写的**唯一目的**。

        旧实现返回 (summaries, urls) 两个平行列表，摘要 A 配链接 B 是常态。
        现在标题、摘要、链接必须来自同一个结果块。
        """
        res = wa._parse_bing(BING_FIXTURE)
        first = res[0]
        self.assertIn("机械键盘", first["title"])
        self.assertIn("性价比", first["snippet"])
        self.assertIn("zhuanlan.zhihu.com", first["url"])

    def test_self_link_skipped(self):
        res = wa._parse_bing(BING_FIXTURE)
        self.assertFalse(any("cn.bing.com" in x["url"] for x in res))

    def test_rank_in_source_recorded(self):
        """打分主判据靠它，解析时必须记。"""
        res = wa._parse_bing(BING_FIXTURE)
        self.assertEqual([x["_rank_in_source"] for x in res], [1, 2])

    def test_date_extracted(self):
        res = wa._parse_bing(BING_FIXTURE)
        self.assertEqual(res[0]["date"], "2026-09-23")

    def test_empty_input(self):
        self.assertEqual(wa._parse_bing(""), [])

    def test_no_b_algo_returns_empty(self):
        """事故形态：页面里只有非 b_algo 的杂文本 → 必须返回空，不硬凑。"""
        junk = '<html><div class="b_caption"><p>HTTP状态码 当浏览者访问一个网页时…</p></div></html>'
        self.assertEqual(wa._parse_bing(junk), [])

    def test_respects_limit(self):
        many = "".join(
            f'<li class="b_algo"><h2><a href="https://a.com/{i}">标题{i}足够长的标题</a></h2>'
            f'<p>第 {i} 条摘要内容也足够长可以参与解析处理</p></li>'
            for i in range(20))
        self.assertEqual(len(wa._parse_bing(f"<html>{many}</html>", limit=5)), 5)


class TestParseSo(unittest.TestCase):
    def test_parses_blocks(self):
        """真实 360 结构：href 是跳转壳，真实地址在 data-mdurl。

        ★ 2026-10-24：夹具换成真实 SERP 形状。旧夹具写的是直链
          （`href="https://zhuanlan.zhihu.com/p/456"`），而 360 实际返回
          的全是 `so.com/link?m=…` —— 夹具验的是想象，真实链路 0 条通过。
        """
        res = wa._parse_so(SO_FIXTURE)
        self.assertEqual(len(res), 2, [x["url"] for x in res])
        urls = [x["url"] for x in res]
        # 用的是 data-mdurl，不是 href 的跳转壳
        self.assertIn("https://zhuanlan.zhihu.com/p/456", urls)
        self.assertFalse(any("so.com/link" in u for u in urls), urls)

    def test_inline_em_not_split(self):
        """<em> 是引擎高亮，不能把词拆成「机械 键盘」。"""
        res = wa._parse_so(SO_FIXTURE)
        self.assertIn("机械键盘", res[0]["title"])
        self.assertIn("选购指南", res[0]["snippet"])

    def test_no_mdurl_dropped(self):
        """没有 data-mdurl 的块跳过：解跳转壳拿到的是广告兜底页。"""
        res = wa._parse_so(SO_FIXTURE)
        self.assertFalse(any("没有 data-mdurl" in x["title"] for x in res),
                         [x["title"] for x in res])

    def test_self_host_dropped(self):
        """data-mdurl 指向 360 自家页面时仍要过滤。"""
        res = wa._parse_so(SO_FIXTURE)
        self.assertFalse(any("so.com" in x["url"] for x in res),
                         [x["url"] for x in res])

    def test_rank_uses_result_pos(self):
        """名次用 data-result-pos，不是枚举序号 —— 跳过广告后序号会虚高。"""
        res = wa._parse_so(SO_FIXTURE)
        # 第 4 条挂着 data-result-pos="5"（前面有个广告位被跳过）
        self.assertEqual([x["_rank_in_source"] for x in res], [1, 5],
                         [(x["title"], x["_rank_in_source"]) for x in res])

    def test_empty(self):
        self.assertEqual(wa._parse_so(""), [])


class TestParseDdg(unittest.TestCase):
    def test_uddg_unwrapped(self):
        res = wa._parse_ddg(DDG_FIXTURE)
        self.assertEqual(len(res), 2, [x["url"] for x in res])
        self.assertEqual(res[0]["url"], "https://post.smzdm.com/p/abc")

    def test_direct_link_kept(self):
        res = wa._parse_ddg(DDG_FIXTURE)
        self.assertEqual(res[1]["url"], "https://direct.example.com/kb")

    def test_empty(self):
        self.assertEqual(wa._parse_ddg(""), [])


class TestMergeResults(unittest.TestCase):
    """合并去重。位置交错是打分站得住的前提。

    ★ 夹具里的标题都写得够长（>12 字），不是为了凑字数 ——
      `_merge_results` 有一条「标题+摘要 <12 字就丢」的规则，专门丢
      「加入社区」「查看完整名单」这类导航文案。短标题会被它正确丢掉，
      写成三个字会让用例测的是那条规则而不是合并逻辑。
    """

    def _mk(self, title, url, source, rank=1, snippet=""):
        return sc.make(url=url, title=title, snippet=snippet, source=source,
                       rank_in_source=rank)

    def test_merge_two_batches(self):
        """两个源的同名条目必须明显不同，否则会被判成同一篇。

        跨源去重比的是三字组重合 —— 「Bing 的第一条结果」和
        「360 的第一条结果」重合率过半，判定为同一件事是**正确**的。
        所以这里用真正不同的两条。
        """
        got = wa._merge_results([
            [self._mk("200元机械键盘推荐清单", "https://a.com/1", "bing")],
            [self._mk("原神 7.1 版本更新说明汇总", "https://b.com/2", "so")],
        ])
        self.assertEqual(len(got), 2)

    def test_interleaved_by_position(self):
        """★ 首尾拼接会让 360 的第 1 名等同于 Bing 的第 9 名。

        两个引擎的标题要写得**话题不同** —— 写成「必应第0条…」和
        「三六零第0条…」会被跨源去重正确判为同一篇（三字组几乎全重合），
        那样测到的就是去重逻辑，不是交错逻辑。
        """
        bing = [self._mk(f"必应{i}号机械键盘评测文章", f"https://a.com/{i}", "bing", rank=i + 1)
                for i in range(4)]
        so = [self._mk(f"三六零{i}号原神版本说明文档", f"https://b.com/{i}", "so", rank=i + 1)
              for i in range(2)]
        got = wa._merge_results([bing, so])
        self.assertEqual([x["title"] for x in got],
                         ["必应0号机械键盘评测文章", "三六零0号原神版本说明文档",
                          "必应1号机械键盘评测文章", "三六零1号原神版本说明文档",
                          "必应2号机械键盘评测文章", "必应3号机械键盘评测文章"])

    def test_dedup_by_url(self):
        got = wa._merge_results([
            [self._mk("原神新版本上线公告正文已发布", "https://a.com/x", "bing")],
            [self._mk("米哈游官方发布版本说明文档", "http://www.a.com/x/", "so")],
        ])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["url"], "https://a.com/x")

    def test_dedup_cross_source_only(self):
        """同一引擎内的条目本来就不重复；句式雷同不应被误删。"""
        same = [self._mk("第1名推荐机械键盘入门款", "https://a.com/1", "bing", 1),
                self._mk("第2名推荐机械键盘入门款", "https://a.com/2", "bing", 2)]
        self.assertEqual(len(wa._merge_results([same])), 2)

    def test_dedup_by_summary_overlap(self):
        """同一篇新闻被不同源截成不同长度，按三字组重合判重。"""
        base = "米哈游今日公布原神 7.1 版本将于九月二十三日上线"
        got = wa._merge_results([
            [self._mk(base + "，新增地图与角色。", "https://a.com/1", "bing")],
            [self._mk(base + "，并同步开启限时活动。", "https://b.com/2", "so")],
        ])
        self.assertEqual(len(got), 1)

    def test_filters_unusable_url(self):
        got = wa._merge_results(
            [[self._mk("一条标题足够长的搜索结果", "/s?q=aibot官网入口&src=related", "so")]])
        self.assertEqual(got, [])

    def test_respects_limit(self):
        batch = [self._mk(f"第{i}条搜索结果的标题内容", f"https://a.com/{i}", "bing", i)
                 for i in range(20)]
        self.assertEqual(len(wa._merge_results([batch], limit=8)), 8)

    def test_drops_too_short(self):
        """短于 12 字的多半是标题碎片或导航词，不进结果。"""
        got = wa._merge_results([[
            sc.make(url="https://a.com/1", title="加入社区", snippet="", source="bing"),
            sc.make(url="https://a.com/2", title="查看完整名单", snippet="", source="bing"),
        ]])
        self.assertEqual(got, [])

    def test_empty_input(self):
        self.assertEqual(wa._merge_results([]), [])
        self.assertEqual(wa._merge_results(None), [])

    def test_ignores_non_dict(self):
        got = wa._merge_results([["junk", None,
                                  self._mk("正常的搜索结果标题内容够长", "https://a.com/1", "bing")]])
        self.assertEqual(len(got), 1)

    def test_same_page_by_title(self):
        """★ 实测缺陷：同一页面的两个入口都进了结果。

        搜「原神最新版本」时 Bing 给了：
            https://ys.mihoyo.com/main/   标题「《原神》官方网站-全新7.1版本…」
            https://ys.mihoyo.com/        标题一模一样
        URL key 分别是 ys.mihoyo.com/main 和 ys.mihoyo.com，键不同就都留下了。
        结果是同一个页面占两个名额，给模型的文本里也出现两遍。
        """
        same_title = "《原神》官方网站-全新7.1版本上线"
        got = wa._merge_results([[
            self._mk(same_title, "https://ys.mihoyo.com/main/", "bing", 1),
            self._mk(same_title, "https://ys.mihoyo.com/", "bing", 2),
        ]])
        self.assertEqual(len(got), 1, [x["url"] for x in got])

    def test_same_page_by_home_page(self):
        """标题略有差异但都是站点首页 → 还是同一个页面。"""
        got = wa._merge_results([[
            self._mk("米哈游官方网站的首页入口地址", "https://ys.mihoyo.com/main/", "bing", 1),
            self._mk("米哈游游戏官方网站首页", "https://ys.mihoyo.com/", "bing", 2),
        ]])
        self.assertEqual(len(got), 1, [x["url"] for x in got])

    def test_different_sites_same_title_kept(self):
        """不同站点的同名标题是不同页面 —— 每个官网都有「公司简介」。"""
        got = wa._merge_results([[
            self._mk("公司简介与主要服务范围说明", "https://a.com/about", "bing", 1),
            self._mk("公司简介与主要服务范围说明", "https://b.com/about", "bing", 2),
        ]])
        self.assertEqual(len(got), 2)

    def test_same_site_different_title_kept(self):
        """同站点不同标题是不同文章，不能因为同域名就删。"""
        got = wa._merge_results([[
            self._mk("原神 7.1 版本更新说明", "https://ys.mihoyo.com/news/1", "bing", 1),
            self._mk("原神 7.1 角色前瞻介绍", "https://ys.mihoyo.com/news/2", "bing", 2),
        ]])
        self.assertEqual(len(got), 2)


class TestBlockText(unittest.TestCase):
    """挑正文块。**换过一次实现，这里钉死为什么换。**

    原实现是 `_nav_heavy`：把整页文字倒出来，再判断"是不是导航"。
    实测三个判据都不成立（细节见 web_access._block_text 的 docstring）：
    链接密度、片段均长、句末标点密度，在「正常技术文档」上都会误杀。
    现在的做法是直接找正文容器：文字多、链接少的那个 div。
    """

    def test_picks_content_block_over_nav(self):
        """页面里既有导航块也有正文块 → 必须挑正文块。"""
        nav = "".join(f'<a href="/x{i}">栏目{i}</a>' for i in range(40))
        body = "<p>" + "原神 7.1 版本已于九月二十三日正式上线，新增可探索区域。" * 8 + "</p>"
        html = (f"<html><body><div class='side'>{nav}</div>"
                f"<div class='main'>{body}</div></body></html>")
        got = wa._block_text(html)
        self.assertIn("原神", got)
        self.assertLess(got.count("栏目"), 5, got[:200])

    def test_link_heavy_block_loses(self):
        """两个块文字量相近时，链接多的那个要输。"""
        links = "".join(f'<a href="/y{i}">导航条目{i}号</a>' for i in range(30))
        prose = "<p>" + "这是一段没有任何链接的正常正文内容。" * 12 + "</p>"
        html = f"<div>{links}</div><div>{prose}</div>"
        got = wa._block_text(html)
        self.assertIn("正常正文", got)

    def test_drops_script_and_style(self):
        html = ("<div><script>var secret_code = 1;</script>"
                "<style>.cls{color:red}</style>"
                "<p>" + "正文内容足够长才会被采纳为正文块。" * 12 + "</p></div>")
        got = wa._block_text(html)
        self.assertNotIn("secret_code", got)
        self.assertNotIn("color:red", got)

    def test_short_page_returns_empty(self):
        """空壳页（纯 JS 渲染）不该硬凑正文 —— 返回空比返回垃圾强。"""
        self.assertEqual(wa._block_text("<html><body><div id='app'></div></body></html>"), "")

    def test_empty_input(self):
        self.assertEqual(wa._block_text(""), "")
        self.assertEqual(wa._block_text(None), "")

    def test_falls_back_to_whole_text_without_containers(self):
        """老式纯 <p> 页面没有 div 容器 → 退回整页文本，不能返回空。"""
        html = "<p>" + "这段话没有外层容器但内容完整可读。" * 15 + "</p>"
        got = wa._block_text(html)
        self.assertIn("没有外层容器", got)

    def test_respects_budget(self):
        html = "<div><p>" + "很长的正文内容" * 2000 + "</p></div>"
        self.assertLessEqual(len(wa._block_text(html, budget=500)), 500)

    def test_code_template_block_skipped(self):
        """★ 模板残留块「文字多、链接少」，打分上正好是正文，必须提前排除。

        实测：网易云音乐页面把模板字符串写在 HTML 文本里，抓出来是
        `${escape(x.user.nickname)} {if ...} {/if}`。它不是正文，
        但在打分上比真正文还像正文。
        """
        junk = ('${escape(x.user.nickname)} {if x.user.avatarDetail && '
                'x.user.avatarDetail.identityIconUrl} {/if} function(x){ return x }') * 20
        real = "<p>" + "这一块才是真正的正文内容，讲的是这首歌的背景故事。" * 10 + "</p>"
        html = f"<div class='tpl'>{junk}</div><div class='body'>{real}</div>"
        got = wa._block_text(html)
        self.assertIn("真正的正文", got)
        self.assertNotIn("escape(", got)

    # ── 以下是 2026-10-23 端到端实测暴露的四个缺陷，逐个钉住 ──────────

    def test_template_delimiter_alone_rejects(self):
        """★ 模板定界符命中**一个**就够，不能要求凑满 3 个标记。

        实测：网易云首页抽出 760 字登录弹窗，里面是 `{/if}`、
        `{list suggests as item}`、`${item|escape}`。老标记表只收
        `${`/`{{`/`}}`，`{/` 和 `{list ` 不命中，整段蒙混过关。
        散文里不可能出现 `{/if}` 这种写法，这条判据的误伤率是 0。
        """
        junk = ("扫码登录 二维码已失效 点击刷新 " * 6
                + "{list suggests as item} ${item|escape} {/list}"
                + " 忘记密码？ 短信登录 自动登录 获取验证码 " * 6)
        self.assertTrue(wa._looks_like_code_block(junk))

    def test_prose_with_html_mention_not_rejected(self):
        """反例：讲 HTML 的文章里 `</div>` 是合法正文，不能一出现就判死。

        所以 `</` 走的是"次数 ≥3"而不是"命中即死"，
        `{...}` 那批才是命中即死。
        """
        prose = ("在 HTML 里，块级元素用 <div> 包裹，闭合写成 </div>。"
                 "这是一段完全正常的正文，讲的是标签怎么写。") * 4
        self.assertFalse(wa._looks_like_code_block(prose))

    def test_block_boundary_not_fixed_window(self):
        """★ 取块必须按标签闭合截断，不能取定长窗口。

        实测：B站首页胜出的是 `<div class="left-entry__item">` —— 一个导航项。
        定长窗口 `html[start:start+12000]` 早就越过了导航项本身，
        把后面所有兄弟节点全吞了。于是「一个导航项的子树」和「整页」
        统计成同一串文字，链接密度 0.01、长度 3329，完美冒充正文。

        这里用 `_subtree` 直接验边界：小容器后面跟着一个大容器，
        从小容器起点取到的片段**不许包含**大容器的内容。
        """
        inner = "<span>" + "这个导航项自己的文字。" * 3 + "</span>"
        pad = "<p>" + "后面这一大段属于兄弟节点，不该被算进这个块。" * 40 + "</p>"
        html = f"<div class='tiny'>{inner}</div><div class='sibling'>{pad}</div>"
        got = wa._subtree(html, 0)
        self.assertIn("这个导航项自己的文字", got)
        self.assertNotIn("不该被算进这个块", got)

    def test_subtree_closes_on_own_tag(self):
        """_subtree 直接测：返回的片段必须在这个容器自己的闭合处结束。"""
        html = "<div id='a'><p>里面</p></div><p>外面</p>"
        got = wa._subtree(html, 0)
        self.assertIn("里面", got)
        self.assertNotIn("外面", got)

    def test_no_content_page_returns_empty_not_nav(self):
        """★ 所有块都被排除时，**返回空**，不能兜底倒出整页。

        实测：网易云首页所有块都被模板判据排除后，旧兜底把整页 3000 字
        倒了出来 —— 那不是"退而求其次"，是把导航菜单当正文交给模型。
        区分方式：页面有容器标签，说明是被判据主动排除的，就该空着。
        """
        nav = "".join(f"<div>栏目{i}</div>" for i in range(300))
        tpl = '<div>' + "${x} {/if}" * 50 + "</div>"
        html = f"<html><body>{nav}{tpl}</body></html>"
        self.assertEqual(wa._block_text(html), "")

    def test_entity_escaped_tags_stripped(self):
        """★ 先剥标签、再解码、再剥，缺一不可。

        实测：BWiki 那条正文里漏出 `<img alt="坎蒂丝" src="https://...">`。
        页面把标签二次转义成 `&lt;img ...&gt;`：剥标签那步看到的还是实体，
        什么也剥不到；解码在之后发生，标签就此复活。
        """
        got = wa._clean_text('前 &lt;img src="https://x/y.png"&gt; 后')
        self.assertNotIn("<img", got)
        self.assertIn("前", got)
        self.assertIn("后", got)

    def test_entity_decode_only_once(self):
        """★ 解码只能做一次。

        第一版写成「解码+剥标签」循环两轮，结果更糟：第二轮解码时，
        页面里合法出现的 `&lt;` 会变成 `<`，与后面的文字连成"标签"，
        把正常正文一起吃掉。BWiki 首页因此从 353 字导航退化成更碎的东西。
        """
        # 单层转义要剥掉，但正常文字不能被吞
        got = wa._clean_text("用 &lt;div&gt; 表示块，&amp; 用 &lt;/div&gt; 结束")
        self.assertIn("表示块", got)
        self.assertIn("结束", got)

    def test_svg_stripped(self):
        """内联 SVG 整段丢掉 —— `<path d="M4.89...">` 全是坐标，一个字的信息都没有。"""
        paragraph = "这一段是正文，讲的是页面在说什么事情。"
        html = ('<div><svg viewBox="0 0 24 24"><path fill-rule="evenodd" '
                'd="M4.89054 5.1 L18.2 9.3"/></svg>'
                '<p>' + paragraph * 12 + "</p></div>")
        got = wa._block_text(html)
        self.assertNotIn("M4.89054", got)
        self.assertIn("讲的是页面在说什么事情", got)

    def test_navigation_only_page_rejected_by_sentences(self):
        """★ 成句数量是准入门槛，不是权重。

        实测：B站首页 3000 字全是「首页 番剧 直播 游戏中心 会员购 漫画…」，
        成句 1 个；而 SPA 用 div 做导航（不是 `<a>`），链接密度 0.00，
        骗过了所有旧判据。成句数不看链接，只看"这段文字有没有在说事"。
        """
        nav = "<div>" + " ".join(f"栏目{i}" for i in range(400)) + "</div>"
        # 3000 字、无链接、无模板特征 —— 旧判据全都拦不住
        self.assertGreater(len(wa._clean_text(nav)), 200)
        self.assertEqual(wa._block_text(f"<html><body>{nav}</body></html>"), "")

    def test_content_page_passes_sentence_gate(self):
        """反例：正文页必须过闸，门槛不能误杀短正文。

        夹具规模照着真实公告页写（现实里一条公告不会只有 48 字，还有页脚、
        相关阅读之类）。这里要验的是**门槛不误杀**，不是"超短页面也要收" ——
        后者由 `len(text) < 200` 那条既有下限管，两回事。
        """
        body = ("<p>服务器将于本周五凌晨两点开始维护，预计持续三小时，"
                "期间所有功能不可用。</p>"
                "<p>请提前保存正在编辑的内容，避免进度丢失。"
                "维护结束后会向全服玩家发放一份补偿道具，请注意查收邮箱。</p>"
                "<p>如果维护提前完成，我们会第一时间在公告栏同步消息。"
                "对本次维护带来的不便，敬请谅解。</p>")
        html = f"<div class='post'>{body}</div>"
        got = wa._block_text(html)
        self.assertIn("维护", got)


class TestQueryVariants(unittest.TestCase):
    """砍短重试：不够好时先去掉尾部修饰词再免费搜一遍。

    2026-10-25 实测这是「问题与答案不符合」的主要机制 —— 用户的口语修饰词
    会把实体淹掉，引擎去买不相干的东西：

        恋人不行 这个动画 好看吗   0.99 → 李荣浩《恋人》MV
        恋人不行                  1.87 → 我怎么可能成为你的恋人（正确）
        推荐几个便宜好用的键盘      0.93 → 人民日报推荐100本书单
        便宜好用的键盘             1.68 → 什么值得买键盘推荐（正确）
    """

    def test_longest_first(self):
        """从长到短，第一个是原句 —— 好结果不该被砍。"""
        v = wa._query_variants("养大肥鱼 很火 游戏")
        self.assertEqual(v, ["养大肥鱼 很火 游戏", "养大肥鱼 很火", "养大肥鱼"])

    def test_only_trims_tail(self):
        """只砍尾，不重排、不改写。"""
        for q in ("恋人不行 这个动画 好看吗", "核科学技术学院 心理委员 培训"):
            for v in wa._query_variants(q):
                self.assertTrue(q.startswith(v), (q, v))

    def test_single_word_unchanged(self):
        """没有空格的中文串不砍 —— 砍哪一刀都可能把实体劈开。"""
        self.assertEqual(wa._query_variants("养大肥鱼"), ["养大肥鱼"])
        self.assertEqual(wa._query_variants("核科学技术学院心理委员"), ["核科学技术学院心理委员"])

    def test_never_leaves_one_char(self):
        """最后不能只剩单字：实测「帮」这种单字查询必然返回字典释义。"""
        for q in ("帮 我 查 天气", "养 大 肥 鱼"):
            for v in wa._query_variants(q):
                self.assertGreaterEqual(len(v), 2, v)

    def test_numeric_first_token_not_trimmed(self):
        """数字开头的首词是限定语不是实体，砍到它更糟。

        实测「200元价位 机械键盘 推荐」砍成「200元价位」→ Bing 按数字匹配，
        返回「HTTP statusCode 200、300、400」，分数还从 1.04 涨到 1.28。
        分数涨了、答案更错 —— 说明这套打分测不出对错，只能靠这类结构判据兜。
        """
        for q in ("200元价位 机械键盘 推荐", "2026 年 放假安排"):
            self.assertEqual(wa._query_variants(q), [q], q)

    def test_normal_entity_still_trimmed(self):
        """实体开头的照常砍（不能因为上面那条把正常情况也关掉）。"""
        self.assertEqual(wa._query_variants("克莱门莎 角色 是谁"),
                         ["克莱门莎 角色 是谁", "克莱门莎 角色", "克莱门莎"])

    def test_spaces_kept_between_tokens(self):
        """多词变体保留空格：实测带空格比去掉空格好。

        「克莱门莎 角色 是谁」带空格 1.63 ／ 去掉空格 1.32。
        Bing 把空格当词边界分开匹配，粘成一个串反而匹配不上。
        单字变体没有内部空格，所以最终结果不受影响。
        """
        for v in wa._query_variants("克莱门莎 角色 是谁"):
            if len(v.split()) > 1:
                self.assertIn(" ", v)

    def test_empty(self):
        self.assertEqual(wa._query_variants(""), [])


class TestSearchWebWiring(unittest.TestCase):
    """主从接线：自家优先、DeepSeek 兜底。"""

    def setUp(self):
        self._orig = (wa._parse_bing, wa._parse_so, wa._fetch_html,
                      wa._attach_contents, wa._search_via_deepseek)
        wa._QUERY_CACHE.clear()
        wa._fetch_html = lambda url, timeout=15, headers=None: ""
        wa._attach_contents = lambda items, max_pages=3, timeout=8: items

    def tearDown(self):
        (wa._parse_bing, wa._parse_so, wa._fetch_html,
         wa._attach_contents, wa._search_via_deepseek) = self._orig
        wa._QUERY_CACHE.clear()

    def _bing_returns(self, *items):
        wa._parse_bing = lambda html, limit=8: list(items)
        wa._parse_so = lambda html, limit=8: []

    def test_own_search_used_first(self):
        """自家有结果就不该花钱走 DeepSeek。"""
        called = []
        wa._search_via_deepseek = lambda q, nq: (called.append(1), ([], False))[1]
        self._bing_returns(sc.make(url="https://ys.mihoyo.com/", title="原神 7.1 版本上线",
                                   snippet="全新版本已推出，新增区域与角色",
                                   source="bing", rank_in_source=1))
        out = wa.search_web("帮我查一下原神最新版本")
        self.assertIn("原神", out)
        self.assertEqual(called, [], "自家命中时不该调 DeepSeek")

    def test_falls_back_when_own_empty(self):
        """自家一条都没抓到 → 必须走兜底，否则就是搜不了。"""
        wa._parse_bing = lambda html, limit=8: []
        wa._parse_so = lambda html, limit=8: []
        wa._search_via_deepseek = lambda q, nq: (
            [sc.make(url="https://a.com/x", title="兜底来的结果标题",
                     snippet="兜底结果的摘要内容够长可以用", source="ds")], True)
        out = wa.search_web("原神")
        self.assertIn("兜底", out)

    def test_thin_goes_to_deepseek(self):
        """★ 自家结果「偏薄」也要兜底，不只是「空」才兜底。

        2026-10-24 修之前：只有一条结果都没有才兜底。于是 quality_of 算出
        thin、甚至把「结果质量：偏薄」写进了给模型的话里，路由却完全不看它
        —— 实测搜「养大肥鱼 很火 游戏」，自家返回「养」的汉语字典（0.60），
        DeepSeek 能给出正确的游戏条目，但兜底一次都没被调用。
        """
        called = []
        # 一条低分结果：判 thin
        self._bing_returns(sc.make(url="https://dict.example/z", title="养 _ 养 怎么读",
                                   snippet="养字的意思 1.抚育，供给生活品", source="bing",
                                   rank_in_source=1, score=0.6))
        wa._search_via_deepseek = lambda q, nq: (
            called.append(1),
            ([sc.make(url="https://game.example/1", title="大肥鱼养成游戏重磅更新",
                      snippet="30多万人玩过的大肥鱼养成游戏迎来首次更新", source="ds",
                      score=2.4)], True))[1]
        out = wa.search_web("养大肥鱼 很火 游戏")
        self.assertEqual(called, [1], "thin 必须触发兜底")
        self.assertIn("大肥鱼养成游戏", out, "应当采用兜底结果，而不是那条字典页")
        self.assertNotIn("汉语字典", out)

    def test_thin_keeps_own_when_deepseek_unavailable(self):
        """兜底这条路没走通（没 key/网络错）→ 保留自家 thin 结果，不能什么都不给。"""
        self._bing_returns(sc.make(url="https://dict.example/z", title="养 _ 养 怎么读",
                                   snippet="养字的意思 1.抚育，供给生活品", source="bing",
                                   rank_in_source=1, score=0.6))
        wa._search_via_deepseek = lambda q, nq: ([], False)
        out = wa.search_web("养大肥鱼 很火 游戏")
        self.assertIn("怎么读", out)
        self.assertNotIn("未找到相关结果", out)

    def test_both_empty_says_so(self):
        wa._parse_bing = lambda html, limit=8: []
        wa._parse_so = lambda html, limit=8: []
        wa._search_via_deepseek = lambda q, nq: ([], True)
        self.assertIn("未找到相关结果", wa.search_web("一个不存在的东西zzz"))

    def test_engine_exception_does_not_kill_search(self):
        """一个引擎抛错，另一个的结果还得用上。"""
        def _boom(html, limit=8):
            raise RuntimeError("engine down")

        wa._parse_bing = _boom
        wa._parse_so = lambda html, limit=8: [
            sc.make(url="https://b.com/1", title="从 360 拿到的一条结果标题",
                    snippet="这条摘要内容够长可以进结果列表", source="so",
                    rank_in_source=1)]
        # ★ 2026-10-24：这条结果是 thin（1 条、分低），路由会去问兜底。
        #   兜底也拿不到 → 必须**保留自家那份**，不能因为追求质量什么都不给。
        wa._search_via_deepseek = lambda q, nq: ([], False)
        out = wa.search_web("原神")
        self.assertIn("360", out)

    def test_noisy_query_shows_refined_term(self):
        """提炼过的话要告诉模型实际搜的是什么，否则它会把偏差当事实。"""
        got = {}
        wa._parse_bing = lambda html, limit=8: [
            sc.make(url="https://ys.mihoyo.com/", title="原神 7.1 版本上线",
                    snippet="全新版本已推出，新增区域与角色", source="bing",
                    rank_in_source=1)]
        wa._parse_so = lambda html, limit=8: []
        # 抓的是提炼后的词
        wa._fetch_html = lambda url, timeout=15, headers=None: got.setdefault(
            "url", url) or ""
        out = wa.search_web("帮我查一下原神最新版本")
        self.assertIn("%E5%8E%9F%E7%A5%9E", got.get("url", ""), got)
        self.assertIn("检索词", out)

    def test_blank_query(self):
        self.assertIn("为空", wa.search_web(""))

    def test_cache_avoids_second_fetch(self):
        n = []
        wa._parse_bing = lambda html, limit=8: (n.append(1), [
            sc.make(url="https://a.com/1", title="标题够长的结果",
                    snippet="摘要内容也够长可以进结果", source="bing",
                    rank_in_source=1)])[1]
        wa._parse_so = lambda html, limit=8: []
        wa._search_via_deepseek = lambda q, nq: ([], False)
        wa.search_web("原神")
        wa.search_web("原神")
        self.assertEqual(len(n), 1, "同一 query 30 秒内不该再抓一遍")

    def test_quality_hint_when_thin(self):
        """结果偏薄时必须带约束话术，否则模型会拿常识补全。"""
        self._bing_returns(sc.make(url="https://random-blog.cn/x",
                                   title="沾点边的结果标题写得够长",
                                   snippet="摘要很短", source="bing", rank_in_source=8))
        wa._search_via_deepseek = lambda q, nq: ([], False)
        out = wa.search_web("原神最新版本是几")
        self.assertIn("结果质量", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)