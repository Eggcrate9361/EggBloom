# -*- coding: utf-8 -*-
"""tests/test_search_parsing.py —— 搜索结果解析回归（2026-10-13 搜索事故）

线上事故：群里问"200块机械键盘推荐"，search_web 返回的全是
「HTTP状态码 200 详解」「200（阿拉伯数字）百度百科」——Bing 免JS端
把 "200" 匹配成了 HTTP 200，旧解析器又把页面上任意 b_caption/<p>
当结果抓，模型拿垃圾结果"搜了个寂寞"，最后 AI决定不回复(空)。

钉死三件事：
  ① _parse_bing_results 只认 b_algo 块（块外广告/侧栏文本一概不要）；
  ② _parse_sogou_results 认 vr-title + fz-mid 摘要，/link 跳转壳补全；
  ③ _parse_ddg_results 认 result__a/result__snippet，uddg 还原真链。
全部离线（HTML 夹具），不碰网络。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

# 停掉 bot_core 带起来的守护线程：否则测试全过、退出码却是 0xC0000409
# （解释器关闭时 daemon 线程还在写 stdout，抢锁失败 → 拖垮退出码）。
# 详见 tests/_quiet_exit.py 开头的说明。
sys.path.insert(0, HERE)
from _quiet_exit import quiet_exit  # noqa: E402
quiet_exit()

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


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

SOGOU_FIXTURE = """
<html><body>
<div class="vrTitle-box"><h3 class="vr-title"><!--x--><a href="/link?url=ABC123" target="_blank">200元以下,性价比最高的<em>机械键盘</em>推荐!</a></h3>
<div class="fz-mid space-txt base-ellipsis clamp2">这几款<em>机械键盘</em>性价比极高，适合办公和初学者日常使用。</div></div>
<div class="vrTitle-box"><h3 class="vr-title"><a href="https://www.zhihu.com/question/456">200预算买什么键盘</a></h3>
<p>知乎上的高赞回答摘要，讨论百元级键盘的选购要点。</p></div>
</body></html>
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


def main():
    print("\n=== ① Bing：只认 b_algo 块 ===")
    res = bot_core._parse_bing_results(BING_FIXTURE)
    check("解析出 2 条（侧栏广告不算）", len(res) == 2, str(len(res)))
    if len(res) >= 2:
        check("第一条标题正确", "机械键盘" in res[0][1], res[0][1])
        check("摘要带站点语义", "性价比" in res[0][2], res[0][2])
        check("bing.com 自家链接被跳过", not any("cn.bing.com" in r[0] for r in res))
    check("侧栏广告文本绝不混入", not any("侧栏广告" in (r[1] + r[2]) for r in res))
    check("空输入 → 空列表", bot_core._parse_bing_results("") == [])

    print("\n=== ② 搜狗：vr-title + fz-mid 摘要 + /link 补全 ===")
    res2 = bot_core._parse_sogou_results(SOGOU_FIXTURE)
    check("解析出 2 条", len(res2) == 2, str(len(res2)))
    if len(res2) >= 2:
        check("/link 跳转壳补成绝对地址", res2[0][0].startswith("https://www.sogou.com/link"),
              res2[0][0])
        check("em 高亮标签被剥净", "<em>" not in res2[0][1] and "<em>" not in res2[0][2],
              res2[0][1])
        check("fz-mid 摘要进结果", "性价比极高" in res2[0][2], res2[0][2])
        check("<p> 摘要兜底也生效", "高赞回答" in res2[1][2], res2[1][2])

    print("\n=== ③ DDG：uddg 还原真链 ===")
    res3 = bot_core._parse_ddg_results(DDG_FIXTURE)
    check("解析出 2 条", len(res3) == 2, str(len(res3)))
    if len(res3) >= 2:
        check("uddg 壳还原为真链", res3[0][0] == "https://post.smzdm.com/p/abc", res3[0][0])
        check("直链原样保留", res3[1][0] == "https://direct.example.com/kb", res3[1][0])
        check("摘要解析", "五款" in res3[0][2], res3[0][2])

    print("\n=== ④ 事故钉死：垃圾结果不许混进 Bing 解析 ===")
    # 旧版事故形态：页面里只有非 b_algo 的杂文本 → 必须返回空（走下一引擎），
    # 而不是把侧栏/页脚文本当结果
    junk = '<html><div class="b_caption"><p>HTTP状态码 当浏览者访问一个网页时…</p></div></html>'
    check("无 b_algo 块 → 0 条（换引擎，不硬凑）",
          bot_core._parse_bing_results(junk) == [])

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
