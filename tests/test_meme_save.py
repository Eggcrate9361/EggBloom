# -*- coding: utf-8 -*-
"""meme_save：由主模型判意图后存图（2026-10-30）。

用户指出的问题：私聊「存」用关键词硬判（startswith 存/收下…），
合并文本一带 [图片] 占位就失效。改为工具：模型自己看懂"用户要我存这张图"，
然后调 meme_save —— 不再有词表。

钉死：
  1. 不传 path 就存这一轮 set_turn 的图
  2. 传 path 就只存指定那张
  3. 没有图 → 如实失败
  4. 重复/超大 → 报告 failed，不假装成功
  5. 注册进 write 组（写型，私聊+意图）

运行：python tests/test_meme_save.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import meme_collect as mc  # noqa: E402
from harness import tool_meme as tm  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


orig = mc.maybe_collect
calls = []


def fake(path, *, tags=None, source="auto", from_group=""):
    calls.append((path, source, from_group))
    if "dup" in str(path):
        return ""
    return "collected/" + os.path.basename(str(path))


try:
    mc.maybe_collect = fake

    tm.set_turn(images=["C:/tmp/a.jpg", "C:/tmp/b.jpg"], is_group=False,
                sender="10001", group_id="")
    r = tm.meme_save()
    check("不传 path 存本轮全部", r.get("count") == 2, r)
    check("场景是私聊", r.get("scene") == "private", r)
    check("两张都被调用", len(calls) == 2, calls)

    calls.clear()
    tm.set_turn(images=["C:/tmp/a.jpg", "C:/tmp/dup.jpg"], is_group=False,
                sender="10001")
    r2 = tm.meme_save()
    check("重复的进 failed", r2.get("count") == 1 and len(r2.get("failed") or []) == 1, r2)

    calls.clear()
    r3 = tm.meme_save("C:/tmp/only.png")
    check("指定 path 只存一张", len(calls) == 1 and calls[0][0].endswith("only.png"), calls)

    tm.set_turn(images=[], is_group=False, sender="10001")
    check("没有图如实失败", tm.meme_save().get("ok") is False)

    tm.set_turn(images=["C:/tmp/g.jpg"], is_group=True, sender="", group_id="829854136")
    calls.clear()
    r4 = tm.meme_save()
    check("群里记 group 来源", calls and calls[0][1] == "group" and calls[0][2] == "829854136", calls)
    check("场景是群", r4.get("scene") == "group", r4)

    fns = tm.register_all()
    check("注册三个函数", set(fns) == {"meme_find", "meme_send", "meme_save"}, fns)
    from harness import tool_registry as tr
    check("meme_save 归 work 组", "meme_save" in tr.names_in(tr.GROUP_WORK),
          tr.names_in(tr.GROUP_WORK))
finally:
    mc.maybe_collect = orig

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
