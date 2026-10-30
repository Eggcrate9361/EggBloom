# -*- coding: utf-8 -*-
"""私聊读图策略（P0-1，2026-10-30）。

## 钉死的行为

私聊是**针对你一个人**发的图，默认就该看。旧口径把群聊那套
`memes_only`（要强信号才读）直接搬过来，实测 4 张图里 2 张根本没进视觉，
模型只看到 `[图片]` 占位 → 编出"我这儿还是糊的"。

新口径只有一种情况跳过：**多张 + 全是表情包 + 除了图一个字都没说**（连发斗图）。
跳过时必须给一句"这批没读"，让模型知道自己瞎，而不是图糊。

运行：python tests/test_private_image_policy.py
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

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


_ALL_MEME = lambda imgs: True          # noqa: E731
_NOT_MEME = lambda imgs: False         # noqa: E731

# 没图：照旧读（不产生说明）
need, note = bot_core._private_img_policy("", [], all_memes=_ALL_MEME)
check("没有图 → need=True", need is True and note == "", (need, note))

# 一张表情包 + 文字：旧口径会跳过，新口径必须读
need, note = bot_core._private_img_policy(
    "送你一个表情包，你看看", ["a.gif"], all_memes=_ALL_MEME)
check("单张表情包 + 文字 → 读（一对一不再门禁）", need is True and note == "", (need, note))

# 一张表情包 + 纯占位：单张不跳过（斗图是"多张"才算）
need, note = bot_core._private_img_policy("[图片]", ["a.gif"], all_memes=_ALL_MEME)
check("单张纯图 → 仍然读", need is True and note == "", (need, note))

# 多张纯占位：这是连发斗图，跳过 + 必须带说明
need, note = bot_core._private_img_policy(
    "[图片][图片][图片]", ["a.gif", "b.gif", "c.gif"], all_memes=_ALL_MEME)
check("多张连发斗图 → 跳过", need is False, need)
check("跳过带'没读'说明", "没去看它们" in note and "别猜" in note, note)

# 多张但说了话：不跳过（哪怕全是表情包）
need, note = bot_core._private_img_policy(
    "哈哈哈哈笑死", ["a.gif", "b.gif"], all_memes=_ALL_MEME)
check("多张 + 有文字 → 读", need is True and note == "", (need, note))

# 照片/截图：多张也读
need, note = bot_core._private_img_policy(
    "[图片][图片]", ["a.jpg", "b.jpg"], all_memes=_NOT_MEME)
check("照片/截图 → 读", need is True and note == "", (need, note))

# 判定函数抛异常：宁可读，也不能因为判不出来就不看
def _boom(imgs):
    raise RuntimeError("读文件头失败")

need, note = bot_core._private_img_policy("[图片][图片]", ["a.gif"], all_memes=_boom)
check("判定异常 → 退回读", need is True and note == "", (need, note))

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
