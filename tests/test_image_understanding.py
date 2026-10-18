# -*- coding: utf-8 -*-
"""tests/test_image_understanding.py —— 聊天图片理解回归（群聊 + 私聊）

覆盖线上真实事故（2026-10-12 21:36 群[20001]）：
    有人发图并 @ 了 Bot，Bot 只拿到引用消息 raw_message 里的 CQ 串，
    模型看到 multimedia.nt.qq.com.cn 的下载 URL 就去 web_operate 打开它，
    最后回用户「图片我打不开啊 就一坨链接」。

本测试锁死四件事：
  1. CQ 串**不再**原样进 prompt：URL/token 不许出现在最终 user_msg 里；
  2. 图片被转成文字描述并注入（【视觉识别 · 群聊图片内容】块）；
  3. 群聊回复路径真的把图带到了（此前写死 `images = []`）；
  4. 纯图消息不再被判成"无意义"（extract_text 补 [图片] 分支）。

不调真实视觉/对话模型：vision 与 call_deepseek 都被替换成桩，
只断言"送给模型的 prompt 长什么样"。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
IMG = os.path.join(ROOT, "docs")          # 只借一个真实存在的文件路径当"图"
CQ_RAW = ("[CQ:reply,id=-1637749379]"
          "[CQ:image,file=F387A42C9B5C3CC3249DC5524C00182A.jpg,"
          "url=https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=EhT76jobo&rkey=SUPERSECRETKEY,sub_type=0]")


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


def test_cq_sanitize():
    print("\n=== 1) CQ 清洗：URL/token 不许留在文本里 ===")
    out = bot_core.sanitize_cq(CQ_RAW)
    check("图片码 → [图片]", "[图片]" in out, out[:80])
    check("回复码 → [reply]", "[reply]" in out, out[:80])
    check("★ 不再含下载 URL", "multimedia.nt.qq.com.cn" not in out, out[:120])
    check("★ 不再含 rkey（token）", "SUPERSECRETKEY" not in out, out[:120])
    check("普通文本原样返回", bot_core.sanitize_cq("你好呀") == "你好呀")
    urls = bot_core.cq_image_urls(CQ_RAW)
    check("抠出的图片地址正确", urls and urls[0].startswith("https://multimedia.nt.qq.com.cn/download"), str(urls))


def test_extract_text_image():
    print("\n=== 2) extract_text：图片段留占位，纯图消息不再变空串 ===")
    msg = {"message": [{"type": "text", "data": {"text": "看看这个"}},
                       {"type": "image", "data": {"file": "x.jpg"}}]}
    t1 = bot_core.extract_text(msg)
    check("文字 + 图 → 文字 [图片]", "看看这个" in t1 and "[图片]" in t1, repr(t1))
    t2 = bot_core.extract_text({"message": [{"type": "image", "data": {"file": "x.jpg"}}]})
    check("纯图 → [图片]（非空）", t2.strip() == "[图片]", repr(t2))
    check("★ 纯图消息不被判为无意义", not bot_core.is_meaningless_message("[图片]"), "")


def test_describe_block_stubbed():
    print("\n=== 3) 图片块：视觉描述进 prompt（视觉调用打桩）===")
    orig = bot_core.describe_image
    bot_core.describe_image = lambda ref, prompt="": "一只在报错的机器人（桩描述）"
    try:
        blk = bot_core.build_image_context_block([os.path.join(IMG, "发布打包.md")], scene="群聊")
    finally:
        bot_core.describe_image = orig
    check("块里有描述", "桩描述" in blk, blk[:120])
    check("块有标语", "视觉识别" in blk and "图片内容" in blk, blk[:120])
    check("描述失败时返回空串（不中断回复）",
          bot_core.build_image_context_block([]) == "", "")


def test_group_reply_carries_image():
    print("\n=== 4) 群聊回复链：图真的带到了，且 prompt 不含 URL ===")
    sent = {}
    seen = {}

    def fake_vl(ref, prompt=""):
        return "一张动漫表情包：猫猫震惊脸"


    def fake_call(user_msg, images=None, *a, **kw):
        seen["user_msg"] = user_msg
        seen["images"] = images
        return "我看到了，是猫猫震惊脸"

    def fake_send(gid, text, *a, **kw):
        sent["reply"] = text

    orig = (bot_core.describe_image, bot_core.call_deepseek, bot_core.smart_send_group,
            bot_core.startup_time, dict(bot_core._config))
    bot_core.describe_image = fake_vl
    bot_core.call_deepseek = fake_call
    bot_core.smart_send_group = fake_send
    bot_core.startup_time = time.time() - 10**6      # 跳过开机冷却

    def _run_once(mode):
        seen.clear(); sent.clear()
        bot_core._config["image_reply_mode"] = mode
        gid = "_test_img_group_"
        bot_core.GROUP_MSG_BUFFER[gid] = {
            "text": "[小明] [引用:小红] [图片]\n[当前] 这是啥",
            "sender": "10001", "time": time.time(),
            "is_at_bot": True, "has_xiao_a": False, "has_bot_nick": False,
            "has_quote": True, "has_text_at": False, "in_session": True,
            "has_context": True, "images": [os.path.join(IMG, "发布打包.md")], "timer": None,
        }
        bot_core.process_buffered_message(gid)
        bot_core.GROUP_MSG_BUFFER.pop(gid, None)
        return seen.get("user_msg") or "", seen.get("images")

    try:
        # ① describe 模式：图 → 文字描述注入 prompt，原图不交主模型
        um_d, imgs_d = _run_once("describe")
        check("describe：模型确实被调用", bool(um_d), "user_msg 为空")
        check("★ describe：prompt 含图片内容块", "视觉识别" in um_d and "图片内容" in um_d, um_d[-160:])
        check("★ describe：prompt 含视觉描述", "猫猫震惊脸" in um_d, um_d[-160:])
        check("★ describe：prompt 不含原始下载 URL", "multimedia.nt.qq.com.cn" not in um_d, um_d[-200:])
        check("★ describe：prompt 不含 rkey token", "SUPERSECRETKEY" not in um_d, um_d[-200:])
        check("describe：原图不交主模型", imgs_d in ([], None), str(imgs_d)[:80])
        check("describe：回复已发出", bool(sent.get("reply")), str(sent))

        # ② native 模式（2026-10-12 起的默认）：原图直接交主模型，不注入描述
        um_n, imgs_n = _run_once("native")
        check("★ native：原图已交给主模型", bool(imgs_n), str(imgs_n)[:80])
        check("native：不再注入描述块（省一次视觉调用）", "视觉识别" not in um_n, um_n[-120:])
        check("★ native：prompt 仍不含原始下载 URL", "multimedia.nt.qq.com.cn" not in um_n, um_n[-200:])
        check("native：回复已发出", bool(sent.get("reply")), str(sent))
    finally:
        bot_core.describe_image, bot_core.call_deepseek, bot_core.smart_send_group, \
            bot_core.startup_time = orig[:4]
        bot_core._config.clear(); bot_core._config.update(orig[4])


def main():
    test_cq_sanitize()
    test_extract_text_image()
    test_describe_block_stubbed()
    test_group_reply_carries_image()
    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
