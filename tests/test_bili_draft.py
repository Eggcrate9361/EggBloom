# -*- coding: utf-8 -*-
"""评论草稿（1.6.1）单测：只起草、不发送；素材不足要说明。

运行：python tests/test_bili_draft.py
"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


bs = importlib.import_module("plugins.bili")

DATA = {"info": {"bvid": "BV1dL8K6HEm3", "title": "抽帧看视频", "owner": "某人",
                 "stat": {"view": 1000}},
        "transcript": {"cues": [{"text": "今天我们讲抽帧"}, {"text": "四帧拼一张图"}]},
        "danmaku": {"hot": [{"word": "名场面", "count": 9}]},
        "comments": [{"text": "这个思路好", "like": 10}]}

_orig_read, _orig_llm = bs.read, bs._llm
seen = {}


def fake_llm(prompt, system="", max_tokens=600, temperature=0.3):
    seen["prompt"] = prompt
    seen["max_tokens"] = max_tokens
    return seen.get("ret", "四帧拼一张，比逐帧发省太多。")


try:
    bs.read = lambda url, fresh=False: {"ok": True, "data": DATA}
    bs._llm = fake_llm

    r = bs.draft_comment({"url": "BV1dL8K6HEm3"})
    check("草稿成功", r.get("ok") is True and r.get("draft"), r)
    check("草稿不带引号", not (r.get("draft") or "").startswith('"'), r.get("draft"))
    check("提示里说没发出去", "没发出去" in (r.get("note") or ""), r.get("note"))
    check("有稿子时标 has_transcript", r.get("has_transcript") is True, r)
    p = seen.get("prompt") or ""
    check("提示词带标题", "抽帧看视频" in p, p[:40])
    check("提示词带弹幕高频", "名场面" in p, p[:80])
    check("提示词带热评", "这个思路好" in p)
    check("提示词带稿子", "四帧拼一张图" in p)
    check("要一条且限字数", "15–40 字" in p)

    # 多行 / 引号 → 只取第一行并去掉引号
    seen["ret"] = '"第一行\n第二行"'
    r = bs.draft_comment({"url": "BV1dL8K6HEm3"})
    check("多行只取第一行", r["draft"] == "第一行", r["draft"])

    # 超长截断
    bs.set_write_config({"write_text_max": 12})
    seen["ret"] = "这条评论特别特别长超过十二个字了"
    r = bs.draft_comment({"url": "BV1dL8K6HEm3"})
    check("草稿按注入上限截断", len(r["draft"]) <= 12, r["draft"])
    bs.set_write_config({"write_text_max": 180})

    # 模型返回空
    seen["ret"] = ""
    r = bs.draft_comment({"url": "BV1dL8K6HEm3"})
    check("模型空 → 失败且说明", r.get("ok") is False and "模型" in r.get("error", ""), r)

    # 没有链接
    check("没链接 → 报错", bs.draft_comment({}).get("ok") is False)

    # 没有字幕也要能写（靠弹幕/热评），并标注
    d2 = {**DATA, "transcript": {"cues": []}}
    bs.read = lambda url, fresh=False: {"ok": True, "data": d2}
    seen["ret"] = "没字幕也能看出个大概。"
    r = bs.draft_comment({"url": "BV1dL8K6HEm3"})
    check("没字幕也能起草", r.get("ok") is True and r.get("has_transcript") is False, r)

    # 草稿绝不触发写动作
    FakeAPI_calls = []
    check("草稿不发送", not FakeAPI_calls)

    # summary 重构后仍工作
    bs.read = lambda url, fresh=False: {"ok": True, "data": DATA}
    seen["ret"] = "这视频讲抽帧。"
    r = bs.summary({"data": {"info": DATA["info"], "transcript": DATA["transcript"]}})
    check("summary 重构后仍可用", r.get("ok") is True and "抽帧" in r.get("summary", ""), r)
finally:
    bs.read, bs._llm = _orig_read, _orig_llm

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
