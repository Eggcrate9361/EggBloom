# -*- coding: utf-8 -*-
"""harness/bili_api 单测（B 站只读能力层，2026-10-30）。

全部离线：HTTP 走注入的假实现。
重点钉三件事：
  1. **字幕必须走 WBI 版**（旧接口给的 url 没 auth_key，下载会 403）；
  2. **弹幕 XML 必须显式按 UTF-8 解**（requests 会猜成 ISO-8859-1，中文全乱码）；
  3. 拿不到就返回空，不许编。

运行：python tests/test_bili_api.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import bili_api as B  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


# ── 1. 链接解析 ──────────────────────────────────────────────────
for text, want in [
    ("BV1dL8K6HEm3", {"bvid": "BV1dL8K6HEm3"}),
    ("https://www.bilibili.com/video/BV1dL8K6HEm3?p=3", {"bvid": "BV1dL8K6HEm3", "p": 3}),
    ("av170001", {"aid": 170001}),
    ("【标题】 https://b23.tv/abcd123 ", {"short_url": "https://b23.tv/abcd123"}),
    ("今天天气不错", {}),
]:
    got = B.parse_url(text)
    check(f"解析 {text[:26]!r}", got == want, got)

# ── 2. WBI 签名 ─────────────────────────────────────────────────
raw = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
check("mixin 32 位", len(B.mixin_key(raw)) == 32, B.mixin_key(raw))
check("mixin 第 0 位 = 原串第 46 位", B.mixin_key(raw)[0] == raw[46])
check("mixin 第 1 位 = 原串第 47 位", B.mixin_key(raw)[1] == raw[47])
check("mixin 空串安全", B.mixin_key("") == "")
q1 = B.sign_wbi({"bvid": "BV1", "cid": "2", "wts": "3"}, "imgk", "subk")
check("签名带 w_rid", re.search(r"w_rid=([0-9a-f]{32})$", q1) is not None, q1)
check("参数按键排序", q1.startswith("bvid=BV1&cid=2&wts=3"), q1)
check("参数变则签名变", q1 != B.sign_wbi({"bvid": "BV1", "cid": "2", "wts": "4"}, "imgk", "subk"))

# ── 3. 文本工具 ────────────────────────────────────────────────
dirty = [{"from": 0, "to": 1, "text": "第一句"},
         {"from": 1, "to": 2, "text": "第一句"},
         {"from": 2, "to": 3, "text": "请不吝点赞订阅转发"},
         {"from": 3, "to": 4, "text": "  第二句  有空  "},
         {"from": 4, "to": 5, "text": ""}]
clean = B.clean_cues(dirty)
check("去重+去幻觉后两条", len(clean) == 2, clean)
check("首尾空白被去", clean[-1]["text"].endswith("有空"), clean[-1])
check("mm:ss 格式", B.fmt_ts(65) == "01:05", B.fmt_ts(65))
check("超 1 小时带小时", B.fmt_ts(3725) == "1:02:05", B.fmt_ts(3725))
check("cues_to_text 带标题", B.cues_to_text(clean, "# T").startswith("# T"))
check("cues_to_text 带时间", "[00:00] 第一句" in B.cues_to_text(clean), B.cues_to_text(clean))

hot = B.hot_words(["名场面", "名场面", "名场面", "啊", "1", "x" * 20])
check("弹幕高频按次数排", hot and hot[0]["word"] == "名场面" and hot[0]["count"] == 3, hot)
check("过滤单字/纯数字/超长", all(2 <= len(h["word"]) <= 8 for h in hot), hot)

# ── 4. view 归一化 ─────────────────────────────────────────────


def fake_http(url, params):
    if "/x/web-interface/view" in url:
        return {"code": 0, "data": {
            "bvid": "BV1x", "aid": 9, "cid": 77, "title": "T", "desc": "D",
            "duration": 100, "pubdate": 1700000000,
            "owner": {"name": "UP", "mid": 5}, "pic": "http://x.jpg",
            "pages": [{"cid": 77, "page": 1, "part": "P1", "duration": 100}],
            "stat": {"view": 1000, "like": 10, "coin": 2, "favorite": 3,
                     "share": 4, "danmaku": 55, "reply": 6}}}
    return {}


v = B.BiliAPI(http_json=fake_http).view("BV1x")
check("view 取到标题", v.get("title") == "T", v.get("title"))
check("view 取到 UP", v.get("owner") == "UP", v.get("owner"))
check("view 统计齐全", v["stat"]["danmaku"] == 55 and v["stat"]["view"] == 1000, v.get("stat"))
check("view 分P 归一化", v["pages"][0]["cid"] == 77, v.get("pages"))

# ── 5. 字幕：WBI 优先 ──────────────────────────────────────────
calls = []


def fake_http_sub(url, params):
    calls.append(url)
    if "/x/web-interface/nav" in url:
        return {"code": 0, "data": {"wbi_img": {
            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}}
    if "/x/player/wbi/v2" in url:
        return {"code": 0, "data": {"subtitle": {"subtitles": [
            {"lan": "zh-CN", "lan_doc": "中文", "subtitle_url": "//a/sub.json"}]}}}
    if "/x/player/v2" in url:
        return {"code": 0, "data": {"subtitle": {"subtitles": []}}}
    if "a/sub.json" in url:
        return {"body": [{"from": 0, "to": 2, "content": "你好"},
                         {"from": 2, "to": 4, "content": "世界"}]}
    return {}


api = B.BiliAPI(http_json=fake_http_sub)
tr = api.transcript("BV1x", 9, 77)
check("字幕走的是 WBI 版", any("/x/player/wbi/v2" in u for u in calls), calls)
check("WBI 版没成才回退旧接口", calls.count("/x/player/v2") == 0, calls)
check("拿到两条字幕", len(tr["cues"]) == 2, tr)
check("lan_doc 带出来", tr.get("lan_doc") == "中文", tr.get("lan_doc"))
check("来源标 subtitle", tr.get("source") == "subtitle", tr.get("source"))


def fake_http_nosub(url, params):
    if "/x/web-interface/nav" in url:
        return {"code": -101, "data": None}
    return {"code": 0, "data": {"subtitle": {"subtitles": []}}}


tr2 = B.BiliAPI(http_json=fake_http_nosub).transcript("BV1x", 9, 77)
check("没字幕 → source=none", tr2.get("source") == "none", tr2)
check("没字幕 → 空数组不编", tr2.get("cues") == [], tr2)

# ── 6. 弹幕 XML ───────────────────────────────────────────────
XML = ('<?xml version="1.0" encoding="UTF-8"?><i><d p="3.5,1,25,16777215,1700000000,0,abc,1">第二条</d>'
       '<d p="1.2,1,25,16777215,1700000000,0,def,2">第一条 &amp; 转义</d></d></i>')


def fake_text(url, params):
    return XML


dm = B.BiliAPI(http_json=lambda u, p: {}, text_get=fake_text).danmaku(77)
check("弹幕解析出 2 条", len(dm) == 2, dm)
check("弹幕按时间排序", dm[0]["text"].startswith("第一条"), dm)
check("弹幕实体已反转义", "&" in dm[0]["text"], dm[0]["text"])
check("弹幕带时间戳", abs(dm[1]["t"] - 3.5) < 0.01, dm[1])
check("XML 为空 → 空数组", B.BiliAPI(http_json=lambda u, p: {}, text_get=lambda u, p: "").danmaku(1) == [])

# ── 7. 热评 ───────────────────────────────────────────────────


def fake_http_cm(url, params):
    if "/x/v2/reply" in url:
        return {"code": 0, "data": {"replies": [
            {"member": {"uname": "甲"}, "like": 99, "rcount": 3,
             "content": {"message": "好笑"}}]}}
    return {}


cm = B.BiliAPI(http_json=fake_http_cm).comments(9)
check("热评解析", len(cm) == 1 and cm[0]["user"] == "甲" and cm[0]["like"] == 99, cm)
check("热评为空时返回空数组", B.BiliAPI(http_json=lambda u, p: {}).comments(9) == [])

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
