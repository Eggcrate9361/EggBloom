# -*- coding: utf-8 -*-
"""tests/test_image_fetch.py —— 取图必须先拿"当前有效"的地址

用户 2026-10-13 问：「为什么别的 AI 可以读表情包、可以读引用的图片，都是同一个
DeepSeek 的模型？」

根因不在模型，在**取图方式**：QQ 的图片 URL 带 rkey、**会过期**（SnowLuma 源码注释
原文 "image rkeys expire"）。我们一直在下载事件里带的那条 url，失效就 403/超时 →
图没进来 → 模型只能对着 `[图片]` 占位瞎编（线上那句"引用图片解析失败"就是这么来的）。

修法：`extract_images` 先用 OneBot `get_image` 重新签发地址（driver 会 mint 当前
rkey，部分实现直接回本地缓存路径或 base64），拿不到才退回事件里的 url。
本测试锁死这条优先级与各种返回形状。
"""
import base64
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    import requests  # noqa: F401
except ImportError:
    print("SKIP: 需要 requests（pip install requests）")
    sys.exit(0)

import bot_core  # noqa: E402

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


def main():
    tmp = tempfile.mkdtemp()
    real_png = os.path.join(tmp, "cache.png")
    with open(real_png, "wb") as f:
        f.write(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFhAJ/2yq0XAAAAABJRU5ErkJggg=="))

    orig_api = bot_core.api_call
    print("=== ① onebot_fresh_image 的各种返回形状 ===")
    try:
        # a) 回 url
        bot_core.api_call = lambda ep, data=None, timeout=15: (
            {"retcode": 0, "data": {"url": "https://mm.com/fresh.jpg", "file": "x"}}
            if ep == "get_image" else {"retcode": -1})
        v, why = bot_core.onebot_fresh_image("F387.jpg")
        check("回 url → 原样返回", v == "https://mm.com/fresh.jpg", f"{v} / {why}")

        # b) 回本地缓存路径
        bot_core.api_call = lambda ep, data=None, timeout=15: (
            {"retcode": 0, "data": {"file": real_png}} if ep == "get_image" else {"retcode": -1})
        v, why = bot_core.onebot_fresh_image("F387.jpg")
        check("回本地路径 → 原样返回", v == real_png, f"{v} / {why}")

        # c) 回 base64
        b64 = base64.b64encode(b"hello-img").decode()
        bot_core.api_call = lambda ep, data=None, timeout=15: (
            {"retcode": 0, "data": {"base64": b64}} if ep == "get_image" else {"retcode": -1})
        v, why = bot_core.onebot_fresh_image("F387.jpg")
        check("回 base64 → 转成 data URI", v.startswith("data:image/png;base64,"), f"{v[:40]} / {why}")

        # d) 失败/空
        bot_core.api_call = lambda ep, data=None, timeout=15: {"retcode": 100}
        v, why = bot_core.onebot_fresh_image("F387.jpg")
        check("retcode≠0 → 空 + 可读原因", v == "" and "retcode" in why, f"{v} / {why}")
        bot_core.api_call = lambda ep, data=None, timeout=15: {"retcode": 0, "data": {}}
        v, why = bot_core.onebot_fresh_image("F387.jpg")
        check("空 data → 空 + 可读原因", v == "" and why, f"{v} / {why}")
        check("空 file id → 空 + 可读原因",
              bot_core.onebot_fresh_image("") [0] == "", "")

        # ── ② extract_images：优先用 get_image 的新地址 ──
        print("\n=== ② extract_images 的取图优先级 ===")
        # get_image 给本地路径 → 应该直接用它（根本不去下载过期 url）
        bot_core.api_call = lambda ep, data=None, timeout=15: (
            {"retcode": 0, "data": {"file": real_png}} if ep == "get_image" else {"retcode": -1})
        msg = {"message": [{"type": "image",
                            "data": {"file": "F387.jpg",
                                     "url": "https://multimedia.nt.qq.com.cn/download?EXPIRED=1"}}]}
        imgs = bot_core.extract_images(msg)
        check("★ 用 get_image 的地址（不下载过期 url）", imgs == [real_png], str(imgs))

        # get_image 失败 → 退回事件 url（打桩 requests，避免真下载）
        bot_core.api_call = lambda ep, data=None, timeout=15: {"retcode": 100}
        stale = "https://multimedia.nt.qq.com.cn/download?EXPIRED=1"
        msg2 = {"message": [{"type": "image", "data": {"file": "F387.jpg", "url": stale}}]}
        orig_req = bot_core.requests
        orig_ok = bot_core.REQUESTS_OK

        class _Resp:
            status_code = 200
            content = b"cached-bytes"

        class _Req:
            def get(self, *a, **k):
                return _Resp()

        bot_core.requests = _Req()
        bot_core.REQUESTS_OK = True
        imgs2 = bot_core.extract_images(msg2)
        bot_core.requests = orig_req
        bot_core.REQUESTS_OK = orig_ok
        check("get_image 失败 → 退回事件 url 并下载", len(imgs2) == 1 and os.path.exists(imgs2[0]),
              str(imgs2))

        # 无 image 段 → 空
        check("无图片段 → 空列表", bot_core.extract_images({"message": [{"type": "text", "data": {"text": "hi"}}]}) == [])
        check("message 不是列表 → 空列表", bot_core.extract_images({"message": "hi"}) == [])
    finally:
        bot_core.api_call = orig_api

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
