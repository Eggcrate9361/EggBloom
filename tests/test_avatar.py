# -*- coding: utf-8 -*-
"""头像取图/描述/缓存（avatar.py，2026-10-30）。

钉死：
  1. URL 形态正确（qlogo，公开、不需要协议层）
  2. 有缓存就不重复下载
  3. 描述有缓存就不重复看（不再花视觉的钱）
  4. 取不到/看不懂 → 空串，不编
  5. 被问"头像"时才触发（bot_core._avatar_desc）

运行：python tests/test_avatar.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="avatar_test_")
import avatar as av  # noqa: E402

av.DATA_DIR = TMP
av.DESC_FILE = os.path.join(TMP, "descriptions.json")

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


_calls = {"download": 0, "describe": 0}


def fake_download(uid):
    _calls["download"] += 1
    p = av.image_path(uid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"\xff\xd8fakejpeg")
    return p


def fake_describe(path):
    _calls["describe"] += 1
    return "一个蓝发少女的头像"


try:
    # 1. URL
    check("URL 带 QQ 号与尺寸",
          av.url_for("10001") == "https://q1.qlogo.cn/g?b=qq&nk=10001&s=640",
          av.url_for("10001"))

    # 2. fetch 缓存（用假下载，不联网）
    real_fetch = av.fetch
    av.fetch = lambda uid: (fake_download(uid) if not os.path.isfile(av.image_path(uid)) else av.image_path(uid))
    p1 = av.fetch("10001")
    p2 = av.fetch("10001")
    check("取到本地路径", bool(p1) and os.path.isfile(p1), p1)
    check("第二次不重复下载", p1 == p2)

    # 3. describe 缓存
    d1 = av.describe("10001", fake_describe)
    d2 = av.describe("10001", fake_describe)
    check("拿到描述", d1 == "一个蓝发少女的头像", d1)
    check("第二次不重复看", _calls["describe"] == 1, _calls)
    check("描述落盘", os.path.isfile(av.DESC_FILE))

    # 4. 失败姿态
    av.fetch = real_fetch
    av.fetch = lambda uid: ""
    check("取不到图 → 空串", av.describe("99999", fake_describe) == "")
    av.fetch = lambda uid: fake_download(uid)
    check("看不懂 → 空串", av.describe("88888", lambda p: "") == "")

    # 5. bot_core 触发条件
    import bot_core as bc
    orig = av.describe
    seen = []

    def spy(uid, fn=None, **kw):
        seen.append(uid)
        return "描述"

    av.describe = spy
    try:
        check("问头像才触发", bool(bc._avatar_desc("10001", "我的头像是谁")) and seen == ["10001"], seen)
        seen.clear()
        check("不问不触发", bc._avatar_desc("10001", "今天好累") == "" and seen == [], seen)
    finally:
        av.describe = orig
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
