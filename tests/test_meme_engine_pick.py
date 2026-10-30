# -*- coding: utf-8 -*-
"""表情包匹配：去重 + 近义库存回落（meme_engine.py，2026-10-30）。

真机问题："总是那几个表情包"。两个原因：
  1. `_pick_random` 的去重比较键不一致（basename vs 相对路径）→ 永不命中；
  2. 没有库存的情绪（无语/烦躁/兴奋…）掉进"全库随机" → 回一张不相干的图。

钉死：
  1. 最近发过的不会被再次选中（池子够大时）
  2. 池子只有一张时允许重复（不能返回 None）
  3. 词表里没有的标签 → 用近义库存，不落到全库随机

运行：python tests/test_meme_engine_pick.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="memepick_test_")
import meme_engine as me  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


me.MEME_DIR = os.path.join(TMP, "memes")
me.TAGS_FILE = os.path.join(me.MEME_DIR, "tags.json")
os.makedirs(me.MEME_DIR, exist_ok=True)

birds = []
for i in range(5):
    name = f"happy_{i}.gif"
    open(os.path.join(me.MEME_DIR, name), "wb").write(b"x")
    birds.append(name)
lazy = ["lazy_0.gif", "lazy_1.gif"]
for n in lazy:
    open(os.path.join(me.MEME_DIR, n), "wb").write(b"x")

db = {n: ["开心"] for n in birds}
for n in lazy:
    db[n] = ["呆滞"]
json.dump(db, open(me.TAGS_FILE, "w", encoding="utf-8"), ensure_ascii=False)
me.reload_index()

try:
    # 1. 去重：把开心池里 4 张都标成"最近发过"，只剩一张可发
    me._recent_memes[:] = [n for n in birds if n != "happy_3.gif"]
    first = me.match_meme("开心")
    check("最近发过的不会被选中", os.path.basename(first) == "happy_3.gif", first)
    second = me.match_meme("开心")
    check("下一张不会立刻重复", os.path.basename(second) != "happy_3.gif", second)

    # 2. 池子只有一张时允许重复（不能返回 None）
    check("只剩一张也能返回", all(me.match_meme("开心") for _ in range(3)))

    # 3. 无语 → 近义库存（呆滞），不是全库随机
    me._recent_memes[:] = []
    picks = {os.path.basename(me.match_meme("无语")) for _ in range(20)}
    check("无语落到呆滞池", picks <= set(lazy) and picks, picks)

    # 4. 未知词仍然能兜底（不返回 None）
    p = me.match_meme("完全没听过的词")
    check("未知词有兜底", bool(p), p)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
