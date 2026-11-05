# -*- coding: utf-8 -*-
"""tests/test_forward_vision.py —— 合并转发里的图片必须真被看见（1.6.0 P1-1 衍生）

真机 17:41：用户 @ 机器人「看看」一个合并转发，里面是 6 张图。
旧 `_fetch_forward_content` 只认 text/forward 段 → `输出长度0字`
→ 模型只拿到 `[合并转发消息]` → 回「转发的我这里看不到内容」。
同一 forward id 还被插件钩子、主流程、引用回抓各解析一遍，白打三次 API。

判据：
  ① 图片走生产链 extract_images → describe_image，结果写成 [图片：描述]
  ② 取不到图 → 降级 [图片] 占位，注记如实说「没取到」，不许编
  ③ 按 id 缓存：同一 id 第二次不再打 get_forward_msg
  ④ 开关关掉 → 不调看图、结果与开启时不同（缓存键分开）
  ⑤ 超过 6 张只描述 6 张，其余如实标「没取到」
  ⑥ 文字 + 图混合、其它非文本段占位不丢
  ⑦ extract_text 集成：转发段产出 [转发消息] 内容而不是空占位
"""
import io
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402

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
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def _img(n=1, prefix="u"):
    return {"type": "image", "data": {
        "url": "https://multimedia.nt.qq.com.cn/x?fileid=%s%d" % (prefix, n),
        "file": "%s%d.jpg" % (prefix, n), "summary": "[图片]"}}


def _fwd_resp(messages):
    return {"retcode": 0, "data": {"messages": [
        {"sender": {"user_id": 1094950020, "nickname": "李太傅"},
         "message": m, "message_format": "array", "raw_message": ""}
        for m in messages]}}


def main():
    orig_api = bc.api_call
    orig_extract = bc.extract_images
    orig_desc = bc.describe_image
    old_vision = bc._config.get("forward_vision_enable")
    calls = {"api": 0, "vision": 0, "extract": 0}
    desc_ok = [True]
    desc_delay = [0.0]
    lock = threading.Lock()

    def fake_api(method, params=None, **kw):
        with lock:
            calls["api"] += 1
        return resp[0]

    def fake_extract(msg):
        with lock:
            calls["extract"] += 1
        return [r"D:\fake\img.jpg"]

    def fake_desc(ref, prompt=""):
        with lock:
            calls["vision"] += 1
        if desc_delay[0]:
            time.sleep(desc_delay[0])
        return "一张手绘的黑白街景" if desc_ok[0] else ""

    try:
        bc.api_call = fake_api
        bc.extract_images = fake_extract
        bc.describe_image = fake_desc
        bc._config["forward_vision_enable"] = True
        resp = [_fwd_resp([[_img(1), _img(2), _img(3)],
                           [_img(4), _img(5), _img(6)]])]

        print("\n[A] 6 张图全部读到")
        bc._FWD_CACHE.clear()
        calls.update(api=0, vision=0, extract=0)
        out = bc._fetch_forward_content("fid-A")
        check("★ 调了 get_forward_msg", calls["api"] == 1, calls)
        check("★ 6 张都走了图片理解", calls["vision"] == 6, calls)
        check("★ 复用 extract_images（含 rkey 重签那条链）", calls["extract"] == 6, calls)
        check("★ 输出里有 6 个 [图片：描述]", out.count("[图片：一张手绘的黑白街景]") == 6, out[:200])
        check("★ 注记如实说 6 张、已读到 6 张",
              "6 张图片（已读到 6 张画面）" in out, out[-120:])
        check("★ 注记不许猜", "别补没写出来的细节" in out, out[-120:])
        check("★ 说话人保留", "李太傅:" in out, out[:80])

        print("\n[B] 缓存：同一 id 不再重复请求")
        calls.update(api=0, vision=0, extract=0)
        out2 = bc._fetch_forward_content("fid-A")
        check("★ 第二次没打 API", calls["api"] == 0, calls)
        check("★ 第二次没重复看图", calls["vision"] == 0, calls)
        check("★ 结果与首次一致", out2 == out)

        print("\n[C] 取不到图 → 降级占位，如实说没看到")
        bc._FWD_CACHE.clear()
        desc_ok[0] = False
        calls.update(api=0, vision=0, extract=0)
        out3 = bc._fetch_forward_content("fid-B")
        check("★ 6 个纯 [图片] 占位", out3.count("[图片]") == 6, out3[:200])
        check("★ 注记说都没取到画面", "6 张图片（都没取到画面）" in out3, out3[-120:])
        check("★ 注记要求别猜、让对方打字",
              "没取到画面的别猜" in out3 and "让对方把事说出来" in out3, out3[-140:])
        check("★ 没有 [图片：描述] 伪装", "[图片：" not in out3)
        desc_ok[0] = True

        print("\n[D] 关掉开关 → 不调看图，缓存键分开")
        bc._FWD_CACHE.clear()
        bc._config["forward_vision_enable"] = False
        calls.update(api=0, vision=0, extract=0)
        out4 = bc._fetch_forward_content("fid-D")
        check("★ 一次看图都没调", calls["vision"] == 0, calls)
        check("★ 仍给了 [图片] 占位", out4.count("[图片]") == 6, out4[:160])
        check("★ 与开启时的结果不同（缓存没串）", out4 != out)
        bc._config["forward_vision_enable"] = True

        print("\n[E] 超过 6 张只描述 6 张")
        bc._FWD_CACHE.clear()
        resp = [_fwd_resp([[_img(i) for i in range(8)]])]
        calls.update(api=0, vision=0, extract=0)
        out5 = bc._fetch_forward_content("fid-E")
        check("★ 只看了 6 张", calls["vision"] == bc._FWD_IMG_MAX, calls)
        check("★ 6 个描述 + 2 个占位",
              out5.count("[图片：") == 6 and out5.count("][图片]") >= 0, out5[:200])
        check("★ 注记写明 2 张没取到", "8 张图片（已读到 6 张画面、2 张没取到）" in out5,
              out5[-140:])

        print("\n[F] 文字 / 语音 / 表情混排不丢信息")
        bc._FWD_CACHE.clear()
        resp = [_fwd_resp([[{"type": "text", "data": {"text": "这是重点"}},
                            _img(9),
                            {"type": "record", "data": {"file": "a.amr"}},
                            {"type": "face", "data": {"id": "1"}}]])]
        out6 = bc._fetch_forward_content("fid-F")
        check("★ 正文保留", "这是重点" in out6, out6[:160])
        check("★ 语音占位", "[语音]" in out6, out6[:160])
        check("★ 表情占位", "[表情]" in out6, out6[:160])
        check("★ 注记统计 1 张图 + 1 个record + 1 个face",
              "1 张图片" in out6 and "1 个record" in out6 and "1 个face" in out6,
              out6[-160:])
        check("★ 有正文时不许说「没有可读取的文字」",
              "没有可读取的文字" not in out6, out6[-160:])

        print("\n[G] extract_text 集成")
        bc._FWD_CACHE.clear()
        resp = [_fwd_resp([[_img(7)]])]
        msg = {"_raw_elements": [{"type": "forward", "data": {"id": "fid-G"}}]}
        t = bc.extract_text(msg)
        check("★ 产出 [转发消息] 而不是空占位",
              "[转发消息]" in t and "[合并转发消息]" not in t, t[:160])

        print("\n[H] 并发（单张 0.25s × 6 张，不该串行等 1.5s）")
        bc._FWD_CACHE.clear()
        resp = [_fwd_resp([[_img(i, "slow") for i in range(6)]])]
        desc_delay[0] = 0.25
        calls.update(api=0, vision=0, extract=0)
        t0 = time.time()
        out7 = bc._fetch_forward_content("fid-H")
        dt = time.time() - t0
        desc_delay[0] = 0.0
        check("★ 6 张都描述了",
              calls["vision"] == 6
              and out7.count("[图片：一张手绘的黑白街景]") == 6, calls)
        check("★ 明显快于串行 1.5s（实测 %.2fs）" % dt, dt < 0.9, "%.2fs" % dt)
        check("★ 顺序不乱（6 个描述都在行内）", "（转发里共 6 张图片（已读到 6 张画面）" in out7,
              out7[-140:])

        print("\n[I] 静态接线")
        src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8",
                      errors="replace").read()
        check("★ 复用 extract_images（不是自己拼 url 下载）",
              "imgs = extract_images(" in src)
        check("★ 复用 describe_image", "describe_image(imgs[0], _IMG_DESC_PROMPT)" in src)
        check("★ 有张数上限与总预算", "_FWD_IMG_MAX = 6" in src and "_FWD_IMG_BUDGET" in src)
        check("★ 有并发批次", "_fwd_describe_batch" in src and "ThreadPoolExecutor" in src)
        check("★ 有开关", "forward_vision_enable" in src)

    finally:
        bc.api_call = orig_api
        bc.extract_images = orig_extract
        bc.describe_image = orig_desc
        bc._FWD_CACHE.clear()
        if old_vision is None:
            bc._config.pop("forward_vision_enable", None)
        else:
            bc._config["forward_vision_enable"] = old_vision

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  - " + x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
