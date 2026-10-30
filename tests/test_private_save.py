# -*- coding: utf-8 -*-
"""私聊「存」表情包 + meme_find 空结果提示（2026-10-30）。

真机 20:08：用户发图 +「存」，bot 回"只能看不能存，真没这功能"。
私聊图确实以前不进图库（设计如此），但用户明确说「存」就该存。

钉死：
  1. 存意图识别（短句、以动词开头、排除疑问）
  2. meme_find 查不到时给出 total/hint，别让模型说"库里一张都没有"

运行：python tests/test_private_save.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402
from harness import tool_meme as tm  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


f = bot_core._private_save_intent
check("「存」", f("存"))
check("「存一下」", f("存一下"))
check("「收下」", f("收下"))
check("「保存」", f("保存"))
check("「存表情包」", f("存表情包"))
check("疑问句不存", not f("存在吗"))
check("疑问句不存 2", not f("存？"))
check("长句不存", not f("你把这个图存下来放哪儿了我不知道你存了吗"))
check("空串不存", not f(""))

# meme_find：空命中给提示
import json, tempfile, shutil
TMP = tempfile.mkdtemp(prefix="mfind_")
old_root, old_idx, old_desc = tm.MEME_ROOT, tm.INDEX_FILE, tm.DESC_FILE
try:
    tm.MEME_ROOT = TMP
    tm.INDEX_FILE = os.path.join(TMP, "collected", "index.json")
    tm.DESC_FILE = os.path.join(TMP, "descriptions.json")
    os.makedirs(os.path.join(TMP, "collected"), exist_ok=True)
    json.dump({"s1": {"path": "collected/a.gif", "tags": ["崩溃"], "ts": 1}},
              open(tm.INDEX_FILE, "w", encoding="utf-8"))
    r = tm.meme_find("不存在")
    check("查不到带 total", r.get("total") == 1, r)
    check("查不到带 hint", "崩溃" in str(r.get("hint") or ""), r.get("hint"))
    r2 = tm.meme_find("")
    check("空查询照常返回", r2.get("count") == 1, r2)
finally:
    tm.MEME_ROOT, tm.INDEX_FILE, tm.DESC_FILE = old_root, old_idx, old_desc
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
