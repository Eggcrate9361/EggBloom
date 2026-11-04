# -*- coding: utf-8 -*-
"""tests/test_image_subtype.py —— 表情包要按表情发，不能靠 QQ 猜（2026-11-04）

用户：「表情包发出来有时候是表情包，有时候是图片形式」。

OneBot v11 的 image 消息段有 `sub_type`：0=普通图片、1=表情包。
不带这个字段时由 QQ 客户端自己判断，于是同一批表情包会时而表情、时而图片。
本用例钉两件事：

  ① `send_image` / `send_group_image` 收 `sub_type`，且**只有非 0 才写进消息段**
     （普通图片的报文要逐字节不变，不然是没必要的回归面）
  ② 所有**表情包**发送点都显式传 1；普通图片（截图/画图结果）不传
"""
import os
import sys
import types

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


def main():
    import tempfile
    import io as _io

    # 抓 _ob_send_msg 的实际报文
    sent = []
    orig = bc._ob_send_msg

    def fake_send(msg, target=None, **kw):
        sent.append({"msg": msg, "target": target, "kw": kw})
        return True, {"retcode": 0}

    tmp = tempfile.mkdtemp(prefix="img_")
    img = os.path.join(tmp, "m.png")
    # 1x1 PNG
    _io.open(img, "wb").write(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082"))
    bc._ob_send_msg = fake_send
    try:
        print("[A] 普通图片：报文里不该出现 sub_type")
        sent.clear()
        bc.send_image(img, "123", sub_type=0)
        data = sent[0]["msg"][0]["data"]
        check("★ 不带 sub_type", "sub_type" not in data, data)
        check("仍是 image 段", sent[0]["msg"][0]["type"] == "image")

        print("\n[B] 表情包：带 sub_type=1")
        sent.clear()
        bc.send_image(img, "123", sub_type=1)
        data = sent[0]["msg"][0]["data"]
        check("★ sub_type=1", data.get("sub_type") == 1, data)
        check("file 还是 base64", str(data.get("file", "")).startswith("base64://"))

        print("\n[C] 群聊同样")
        sent.clear()
        bc.send_group_image("999", img, sub_type=1)
        data = sent[0]["msg"][0]["data"]
        check("★ 群里 sub_type=1", data.get("sub_type") == 1, data)
        check("走的是群发送", sent[0]["kw"].get("is_group") is True, sent[0]["kw"])
        sent.clear()
        bc.send_group_image("999", img)
        check("群普通图不带 sub_type", "sub_type" not in sent[0]["msg"][0]["data"])

        print("\n[D] 默认值是 0（老调用点行为不变）")
        import inspect
        sig = inspect.signature(bc.send_image)
        check("★ send_image 默认 0", sig.parameters["sub_type"].default == 0, sig)
        sig2 = inspect.signature(bc.send_group_image)
        check("★ send_group_image 默认 0", sig2.parameters["sub_type"].default == 0, sig2)

    finally:
        bc._ob_send_msg = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n[E] 五处表情包调用点都传了 1（源码断言）")
    src = open(bc.__file__, encoding="utf-8", errors="replace").read()
    n1 = src.count("sub_type=1")
    check("★ 至少 5 处传 1", n1 >= 5, n1)
    for name, pat in (
        ("群表情", "send_group_image(group_id, meme_path, sub_type=1)"),
        ("回敬表情", "send_group_image(target, path, sub_type=1)"),
        ("私聊回敬", "send_image(path, target, sub_type=1)"),
        ("工具发表情-群", 'send_group_image(str(c["group_id"]), path, sub_type=1)'),
        ("工具发表情-私聊", "send_image(path, sender, sub_type=1)"),
    ):
        check(f"  {name} 传了 1", pat in src, pat[:40])

    print("\n[F] 普通图片不要被误伤")
    check("画图结果仍按普通图片发",
          "send_group_image(group_id, img_path)" in src)
    check("截图仍按普通图片发", "send_image(shot, target_qq=target_qq)" in src)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
