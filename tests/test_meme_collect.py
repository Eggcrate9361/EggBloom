# -*- coding: utf-8 -*-
"""表情包自动采集（meme_collect.py，2026-10-30）。

钉死：
  1. 小图/常见格式才收；大图、怪格式跳过
  2. 同一张图（sha1）只进一次
  3. 标签进 tags.json，匹配池立刻能用到
  4. 每日上限生效
  5. 关掉开关 = 什么都不做

运行：python tests/test_meme_collect.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="memecollect_test_")
import meme_collect as mc  # noqa: E402

mc.MEME_DIR = os.path.join(TMP, "memes")
mc.COLLECT_DIR = os.path.join(mc.MEME_DIR, "collected")
mc.INDEX_FILE = os.path.join(mc.COLLECT_DIR, "index.json")
mc.TAGS_FILE = os.path.join(mc.MEME_DIR, "tags.json")
os.makedirs(mc.MEME_DIR, exist_ok=True)
json.dump({}, open(mc.TAGS_FILE, "w", encoding="utf-8"))

mc._cfg = lambda: {"meme_collect_enable": True, "meme_collect_classify": False,
                   "meme_collect_daily_cap": 30}
mc._CLASSIFIER = lambda paths: "开心"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


def make_img(name, size=3000):
    p = os.path.join(TMP, name)
    with open(p, "wb") as f:
        f.write(b"GIF89a" + name.encode("utf-8") + b"\x00" * size)
    return p


try:
    g1 = make_img("a.gif")
    rel = mc.maybe_collect(g1, tags=["开心"], source="group")
    check("小图入库", bool(rel) and os.path.isfile(os.path.join(mc.COLLECT_DIR, os.path.basename(rel))), rel)
    tags = json.load(open(mc.TAGS_FILE, encoding="utf-8"))
    check("标签写进 tags.json", tags.get(rel) == ["开心"], tags)
    idx = json.load(open(mc.INDEX_FILE, encoding="utf-8"))
    check("索引有 sha1 记录", len(idx) == 1, idx)

    check("同一张图不重复收", mc.maybe_collect(g1) == "")
    check("索引没变", len(json.load(open(mc.INDEX_FILE, encoding="utf-8"))) == 1)

    # 太小 / 太大 / 怪格式
    tiny = os.path.join(TMP, "tiny.gif")
    open(tiny, "wb").write(b"x" * 10)
    check("太小的跳过", mc.maybe_collect(tiny) == "")
    big = make_img("big.gif", size=4 * 1024 * 1024)
    check("超 3MB 跳过", mc.maybe_collect(big) == "")
    txt = os.path.join(TMP, "note.txt")
    open(txt, "w").write("hello")
    check("非图片跳过", mc.maybe_collect(txt) == "")

    # 分类器兜底（不传 tags 时自动判）
    g2 = make_img("b.gif")
    mc._cfg = lambda: {"meme_collect_enable": True, "meme_collect_classify": True,
                       "meme_collect_daily_cap": 30, "meme_collect_fill_desc": True}
    mc.set_describer(lambda p: "蓝发少女竖大拇指")
    rel2 = mc.maybe_collect(g2)
    tags2 = json.load(open(mc.TAGS_FILE, encoding="utf-8"))
    check("自动判情绪并打标签", tags2.get(rel2) == ["开心"], tags2.get(rel2))
    desc_path = os.path.join(mc.MEME_DIR, "descriptions.json")
    descs = json.load(open(desc_path, encoding="utf-8")) if os.path.isfile(desc_path) else {}
    check("后台补的画面描述已落盘", "蓝发少女" in str(descs.get(rel2) or ""), descs.get(rel2))

    # 关掉补描述：仍入库，但不写描述
    g2b = make_img("b2.gif")
    mc._cfg = lambda: {"meme_collect_enable": True, "meme_collect_classify": True,
                       "meme_collect_daily_cap": 30, "meme_collect_fill_desc": False}
    rel2b = mc.maybe_collect(g2b)
    descs2 = json.load(open(desc_path, encoding="utf-8")) if os.path.isfile(desc_path) else {}
    check("关掉补描述后不再写", rel2b and rel2b not in descs2, descs2.get(rel2b))

    # 每日上限
    mc._cfg = lambda: {"meme_collect_enable": True, "meme_collect_classify": False,
                       "meme_collect_daily_cap": 2}
    g3 = make_img("c.gif")
    check("达到日上限后不再收", mc.maybe_collect(g3) == "")

    # 开关
    mc._cfg = lambda: {"meme_collect_enable": False}
    g4 = make_img("d.gif")
    check("关掉开关 = 不收", mc.maybe_collect(g4) == "")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
