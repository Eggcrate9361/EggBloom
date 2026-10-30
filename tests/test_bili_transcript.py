# -*- coding: utf-8 -*-
"""harness/bili_transcript 单测（B 站链接 → 字幕稿，2026-10-30）。

全部离线：HTTP 走注入的假实现，签名用真实算法自证。
重点验证 **WBI 兜底那一段真的会走**（x/player/v2 给空时）—— 那是线上最常
失败的地方，不测等于没写。

运行：python tests/test_bili_transcript.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import bili_transcript as bt  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


# ── 1. 链接解析 ──────────────────────────────────────────────────
cases = [
    ("BV1dL8K6HEm3", {"bvid": "BV1dL8K6HEm3"}),
    ("https://www.bilibili.com/video/BV1dL8K6HEm3?p=3", {"bvid": "BV1dL8K6HEm3", "p": 3}),
    ("av170001", {"aid": 170001}),
    ("【标题】 https://b23.tv/abcd123 ", {"short_url": "https://b23.tv/abcd123"}),
    ("看这个 BV1xx411c7mD 好笑", {"bvid": "BV1xx411c7mD"}),
    ("今天天气不错", {}),
]
for text, want in cases:
    got = bt.parse_bili_url(text)
    check(f"解析 {text[:28]!r}", got == want, got)

# ── 2. WBI 签名 ─────────────────────────────────────────────────
raw = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
mix = bt.mixin_key(raw)
check("mixin 取 32 位", len(mix) == 32, len(mix))
check("mixin 第 0 位是原串第 46 位", mix[0] == raw[46], f"{mix[0]} vs {raw[46]}")
check("mixin 第 1 位是原串第 47 位", mix[1] == raw[47], f"{mix[1]} vs {raw[47]}")
check("mixin 短串不越界也不抛",
      isinstance(bt.mixin_key("abc"), str) and len(bt.mixin_key("abc")) <= 32,
      bt.mixin_key("abc"))
check("mixin 空串返回空", bt.mixin_key("") == "")

q1 = bt.sign_wbi({"bvid": "BV1", "cid": "2", "wts": "3"}, "imgk", "subk")
check("签名带 w_rid", "w_rid=" in q1, q1)
check("w_rid 是 32 位 md5", re.search(r"w_rid=([0-9a-f]{32})$", q1) is not None, q1)
check("参数按键排序", q1.startswith("bvid=BV1&cid=2&wts=3"), q1)
q2 = bt.sign_wbi({"bvid": "BV1", "cid": "2", "wts": "4"}, "imgk", "subk")
check("参数变了签名就变", q1 != q2)
q3 = bt.sign_wbi({"bvid": "BV1", "cid": "2", "wts": "3"}, "imgk2", "subk")
check("密钥变了签名就变", q1 != q3)

# ── 3. 清洗 ────────────────────────────────────────────────────
dirty = [
    {"from": 0, "to": 1, "text": "第一句"},
    {"from": 1, "to": 2, "text": "第一句"},                 # 连续重复
    {"from": 2, "to": 3, "text": "请不吝点赞订阅转发"},      # 幻觉模板
    {"from": 3, "to": 4, "text": "  第二句  有空格  "},
    {"from": 4, "to": 5, "text": ""},                      # 空
]
clean = bt.clean_cues(dirty)
check("去重后剩两条", len(clean) == 2, clean)
check("幻觉行被丢", all("点赞" not in c["text"] for c in clean), clean)
check("首尾空白被去", clean[-1]["text"].endswith("有空格"), clean[-1])

# ── 4. 文本格式化 ───────────────────────────────────────────────
txt = bt.cues_to_text([{"from": 5, "to": 6, "text": "你好"},
                       {"from": 65, "to": 66, "text": "在吗"},
                       {"from": 3725, "to": 3726, "text": "很久以后"}], head="# 标题")
check("第一行是标题", txt.splitlines()[0] == "# 标题", txt)
check("mm:ss 格式", "[00:05] 你好" in txt, txt)
check("分钟进位", "[01:05] 在吗" in txt, txt)
check("超过一小时用 h:mm:ss", "[1:02:05] 很久以后" in txt, txt)

# ── 5. 主流程（假 HTTP）────────────────────────────────────────
SUBS = [{"lan": "zh-CN", "lan_doc": "中文（自动生成）",
         "subtitle_url": "//aisubtitle.hdslb.com/bfs/subtitle/x.json"}]
BODY = {"body": [{"from": 0, "to": 2, "content": "大家好"},
                 {"from": 2, "to": 4, "content": "今天讲抽帧"}]}


def fake_http(url, params):
    if "/x/web-interface/view" in url:
        return {"code": 0, "data": {"bvid": "BV1dL8K6HEm3", "title": "抽帧看视频",
                                    "owner": {"name": "某人"}, "duration": 120,
                                    "pages": [{"cid": 999, "page": 1}]}}
    if "/x/player/v2" in url:
        return {"code": 0, "data": {"subtitle": {"subtitles": SUBS}}}
    if "aisubtitle" in url:
        return BODY
    return {}


r = bt.BiliTranscript(http_json=fake_http).run("看 BV1dL8K6HEm3")
check("主流程 ok", r["ok"] is True, r.get("error"))
check("来源是字幕轨", r["data"]["source"] == "subtitle", r["data"]["source"])
check("拿到两条字幕", len(r["data"]["cues"]) == 2, r["data"]["cues"])
check("正文带标题", "抽帧看视频" in r["data"]["text"], r["data"]["text"][:60])
check("正文带时间戳", "[00:00] 大家好" in r["data"]["text"], r["data"]["text"])

# ── 6. WBI 兜底路径 ────────────────────────────────────────────
seen = []


def fake_http_wbi(url, params):
    seen.append(url)
    if "/x/web-interface/view" in url:
        return {"code": 0, "data": {"bvid": "BV1dL8K6HEm3", "title": "T",
                                    "owner": {"name": "O"}, "pages": [{"cid": 7}]}}
    if "/x/player/v2" in url:
        return {"code": 0, "data": {"subtitle": {"subtitles": []}}}      # 空
    if "/x/web-interface/nav" in url:
        return {"code": 0, "data": {"wbi_img": {
            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}}
    if "/x/player/wbi/v2" in url:
        return {"code": 0, "data": {"subtitle": {"subtitles": SUBS}}}
    if "aisubtitle" in url:
        return BODY
    return {}


r2 = bt.BiliTranscript(http_json=fake_http_wbi).run("BV1dL8K6HEm3")
check("v2 空时真的走了 wbi/v2",
      any("/x/player/wbi/v2" in u for u in seen), seen)
check("WBI 路径也拿到字幕", r2["ok"] and len(r2["data"]["cues"]) == 2, r2.get("error"))

# ── 7. 没有字幕 / 坏链接 ───────────────────────────────────────


def fake_http_nosub(url, params):
    if "/x/web-interface/view" in url:
        return {"code": 0, "data": {"title": "无字幕", "owner": {"name": "O"},
                                    "pages": [{"cid": 1}]}}
    return {"code": 0, "data": {"subtitle": {"subtitles": []}}}


r3 = bt.BiliTranscript(http_json=fake_http_nosub).run("BV1dL8K6HEm3")
check("没字幕也 ok（只是没内容）", r3["ok"] is True, r3)
check("没字幕如实说 source=none", r3["data"]["source"] == "none", r3["data"])
check("没字幕给一句人话", "没有字幕轨" in r3["data"]["note"], r3["data"].get("note"))
check("正文为空不瞎编", r3["data"]["text"] == "")

r4 = bt.BiliTranscript(http_json=fake_http).run("今天天气不错")
check("没有链接 → 失败", r4["ok"] is False and "没有" in r4["error"], r4)

r5 = bt.BiliTranscript(http_json=lambda u, p: {}).run("BV1dL8K6HEm3")
check("取不到视频信息 → 失败", r5["ok"] is False, r5)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
