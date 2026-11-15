# -*- coding: utf-8 -*-
"""tests/test_meme_save_report.py —— 「知不知道刚存的是哪张 / 是不是重复」

2026-11-01 用户要求：
  1. 能知道自己刚刚存的表情包是哪个（回去要说得出"存的是哪张"）
  2. 知道自己存了重复表情包 —— **只在私聊说**，群里不展开

锁五件事：
  A. 存成功 → saved 里带 rel + tags + desc（有描述时）
  B. 私聊存到重复 → ok=True（目的已达成）、duplicates 带原条目、"这张之前就存过了"
  C. 群聊存到重复 → 不出现 duplicates 字段，归到 failed
  D. 私聊重复但本轮还有新图 → 新图照存，duplicates 单独列出
  E. 没有任何图 → 如实失败
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import meme_collect  # noqa: E402
import meme_desc  # noqa: E402
from harness import tool_meme as tm  # noqa: E402

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


def _patch(maybe=None, why="", existing=("", "")):
    meme_collect.maybe_collect = (lambda *a, **k: maybe) if maybe is not None else (lambda *a, **k: "")
    meme_collect.skip_reason = lambda *a, **k: why
    meme_collect.existing_entry = lambda *a, **k: existing
    meme_desc.tags_for = lambda *a, **k: ["发疯"]
    meme_desc.desc_for = lambda *a, **k: "白发少年翻白眼，背景黄星星"


def main():
    print("[A] 存成功 → 知道存的是哪张")
    _patch(maybe="collected/new.jpg")
    tm.set_turn(images=["C:/tmp/a.jpg"], is_group=False, sender="u1")
    r = tm.meme_save()
    check("ok", r.get("ok") is True, r)
    item = (r.get("saved") or [{}])[0]
    check("saved 是对象且带 rel", isinstance(item, dict) and item.get("rel") == "collected/new.jpg", item)
    check("带标签", item.get("tags") == ["发疯"], item)
    check("带描述", "白发" in str(item.get("desc", "")), item)
    check("私聊不编造 duplicates", "duplicates" not in r, r)

    print("\n[B] 私聊重复 → 认账，不报错")
    _patch(why="这张已经存过了", existing=("collected/old.jpg", "same"))
    tm.set_turn(images=["C:/tmp/a.jpg"], is_group=False, sender="u1")
    r = tm.meme_save()
    check("ok=True（目的已达成）", r.get("ok") is True, r)
    check("duplicates 指向原图", r.get("duplicates") == [{"path": "collected/old.jpg", "how": "same"}], r)
    check("note 说明白", "存过" in str(r.get("note", "")), r)

    print("\n[C] 群聊重复 → 不展开")
    _patch(why="这张已经存过了", existing=("collected/old.jpg", "same"))
    tm.set_turn(images=["C:/tmp/a.jpg"], is_group=True, group_id="123", sender="")
    r = tm.meme_save()
    check("没有 duplicates 字段", "duplicates" not in r, r)
    check("归到 failed", bool(r.get("failed")), r)

    print("\n[D] 私聊：新图 + 重复各一张")
    calls = {"n": 0}

    def _mc(path, **k):
        calls["n"] += 1
        return "collected/n.jpg" if calls["n"] == 1 else ""

    meme_collect.maybe_collect = _mc
    meme_collect.skip_reason = lambda *a, **k: "这张已经存过了"
    meme_collect.existing_entry = lambda *a, **k: ("collected/old.jpg", "visual")
    tm.set_turn(images=["C:/tmp/new.jpg", "C:/tmp/old.jpg"], is_group=False, sender="u1")
    r = tm.meme_save()
    check("新图存了", len(r.get("saved") or []) == 1, r)
    check("重复被标出", (r.get("duplicates") or [{}])[0].get("how") == "visual", r)

    print("\n[E] 没有任何图")
    _patch()
    tm.set_turn(images=[], is_group=False, sender="u1")
    tm._LAST_IMAGES.update(images=[], sender="", ts=0.0)
    r = tm.meme_save()
    check("如实失败", r.get("ok") is False and "没有" in str(r.get("error", "")), r)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
