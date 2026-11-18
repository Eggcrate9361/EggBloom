# -*- coding: utf-8 -*-
"""memewiki 插件单测（不联网）。

覆盖：命令解析、HTML 压平、两条格式化路径的字数与兜底、
抓取层在「上游挂了 / 返回不是 JSON / 条目不存在」时的降级，
以及 on_message 的限流与「查不到就直说，不瞎编」。

上游 cnmeme.wiki 随时可能改返回、限流或整站挂掉，所以这里一律把 `_get`
换掉 —— 真机联通性不放进用例（那会让 CI 依赖外网，抖动就红）。

用法：python tests/test_memewiki.py
"""
import atexit
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TD = tempfile.mkdtemp(prefix=".codex-memewiki-", dir=ROOT)
os.environ["EGGBLOOM_MEMEWIKI_DB"] = os.path.join(TD, "memewiki.db")

from plugins.memewiki import wiki                       # noqa: E402
import plugins.memewiki as mw                           # noqa: E402
from plugins.memewiki import store as meme_store        # noqa: E402


def _cleanup():
    meme_store.reset_default()
    shutil.rmtree(TD, ignore_errors=True)


atexit.register(_cleanup)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  ← %r" % (extra,)))


def stub(fn):
    """临时替换 wiki._get，用完还原。"""
    orig = wiki._get
    wiki._get = fn
    return orig


# ── 命令解析 ────────────────────────────────────────────────────────
print("[1] 触发前缀")
check("`!梗 yyds` 取出查询词", mw._parse("!梗 yyds") == "yyds", mw._parse("!梗 yyds"))
check("`梗百科 赛博朋克` 是别名", mw._parse("梗百科 赛博朋克") == "赛博朋克")
check("`!梗百科 x` 优先于 `!梗 x`", mw._parse("!梗百科 x") == "x")
check("普通聊天不抢", mw._parse("今天吃什么") is None)
check("冒号分隔也认", mw._parse("!梗：yyds") == "yyds")


# ── HTML 压平 ───────────────────────────────────────────────────────
print("[2] content_html 压成纯文本")
h = ("<h2>来源</h2><p>出自<b>直播</b>&nbsp;口误</p>"
     "<script>alert(1)</script><style>p{}</style><li>其一</li>")
flat = wiki.strip_html(h)
check("段名保留成 `##来源`", "##来源" in flat, flat)
check("去标签留文字", "出自直播 口误" in flat.replace("&nbsp;", " "), flat)
check("script 整段丢掉", "alert" not in flat, flat)
check("style 整段丢掉", "p{}" not in flat, flat)
check("列表项各占一行", "其一" in flat.splitlines(), flat)
check("空输入返回空串", wiki.strip_html("") == "")


# ── 格式化 ──────────────────────────────────────────────────────────
print("[3] 一条消息的格式")
item = {"slug": "yyds", "title": "YYDS", "tags": ["缩写", "电竞", "梗"],
        "content_html": "<p>" + ("很长的正文" * 100) + "</p>",
        "source_video": {"title": "某场直播", "creator_name": "某主播"}}
s = wiki.format_entry(item)
check("首行是标题", s.splitlines()[0] == "【YYDS】", s.splitlines()[:1])
check("正文截断并带省略号", "…" in s and len(s) < 700, len(s))
check("标签最多 5 个", "标签：缩写 / 电竞 / 梗" in s, s)
check("出处视频带作者", "出处视频：某场直播（某主播）" in s, s)
check("末尾给条目链接", s.splitlines()[-1] == "https://cnmeme.wiki/entry/yyds", s.splitlines()[-1:])
check("没有正文时退回 summary",
      wiki.format_entry({"slug": "a", "title": "A", "summary": "一句话"}).find("一句话") > 0)
check("空条目不抛异常", wiki.format_entry({}) == "")

print("[4] 没对上时的候选列表")
check("空结果说「别编」", "别编" in wiki.format_hits([]))
hits = [{"title": "A", "summary": "甲的说明"}, {"title": "B"}, {"title": "C"}, {"title": "D"}]
fh = wiki.format_hits(hits)
check("最多列 3 条", fh.count("· ") == 3, fh)
check("带一句话说明", "甲的说明" in fh, fh)


# ── 抓取层的降级 ────────────────────────────────────────────────────
print("[5] 上游异常不许抛到主链")
orig = stub(lambda *a, **k: (_ for _ in ()).throw(wiki.WikiError("挂了")))
try:
    threw = False
    try:
        wiki.search("x")
    except wiki.WikiError:
        threw = True
    check("search 把上游错误抛成 WikiError", threw)
    check("detail 吞掉错误返回 None", wiki.detail("x") is None)
    check("random_entry 吞掉错误返回 None", wiki.random_entry() is None)
    check("query 不抛异常、明说连不上",
          "连不上" in mw.query("完全未知梗") and "瞎猜" in mw.query("完全未知梗"))
finally:
    wiki._get = orig

print("[6] 正常返回与缓存")
calls = {"n": 0}


def fake_get(path, params=None, timeout=8.0):
    calls["n"] += 1
    if path.endswith("/random"):
        return {"item": {"slug": "r", "title": "随机条"}}
    if path.startswith("/api/v1/public/memes/"):
        slug = path.rsplit("/", 1)[-1]
        return {"item": {"slug": slug, "title": "YYDS", "summary": "永远的神"}}
    return {"items": [{"slug": "yyds", "title": "YYDS", "summary": "永远的神"}]}


orig = stub(fake_get)
try:
    wiki._CACHE.clear()
    check("search 返回列表", len(wiki.search("yyds")) == 1)
    wiki.detail("yyds")
    n1 = calls["n"]
    wiki.detail("yyds")
    check("详情第二次走缓存，不再请求", calls["n"] == n1, calls["n"])
    check("random_entry 取到条目", (wiki.random_entry() or {}).get("title") == "随机条")
    out = mw.query("yyds")
    check("★ 本地命中不访问上游", "【yyds】" in out and "夸人" in out, out)
    remote = mw.query("网络未知梗")
    check("★ 本地未命中时仍联网给条目而非列表", "【YYDS】" in remote, remote)
    check("query 空词给用法", "!梗" in mw.query("  "))
    check("query --raw 回 JSON 片段",
          mw.query("--raw 网络未知梗").startswith("{")
          and "slug" in mw.query("--raw 网络未知梗"))
finally:
    wiki._get = orig
    wiki._CACHE.clear()

print("[7] 查不到就直说，不编")
orig = stub(lambda *a, **k: {"items": []})
try:
    out = mw.query("这不是一个梗")
    check("明说没有", "没有" in out and "别编" in out, out)
finally:
    wiki._get = orig


# ── on_message 契约 ────────────────────────────────────────────────
print("[8] on_message：非命令不干预、限流生效")
class _Ctx:
    def log(self, *a, **k):
        pass


mw._last.clear()
check("普通消息返回 None（不抢话）",
      mw.on_message(_Ctx(), {"text": "随便聊聊", "sender": "u1"}) is None)

orig = stub(lambda *a, **k: {"items": [{"slug": "s", "title": "S", "summary": "x"}],
                             "item": {"slug": "s", "title": "S", "summary": "x"}})
try:
    codes = [mw.on_message(_Ctx(), {"text": "!梗 s", "sender": "u2"}) for _ in range(7)]
    check("前 5 次正常回", all(c and "查太勤" not in c for c in codes[:5]))
    check("第 6 次起限流", "查太勤" in (codes[5] or ""), codes[5:])
    check("换个人不受影响",
          "查太勤" not in (mw.on_message(_Ctx(), {"text": "!梗 s", "sender": "u3"}) or ""))
finally:
    wiki._get = orig
    mw._last.clear()

print()
print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "；".join(FAIL))
    sys.exit(1)
print("全部通过")
sys.exit(0)
