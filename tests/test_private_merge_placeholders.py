# -*- coding: utf-8 -*-
"""tests/test_private_merge_placeholders.py —— 合并后的图片占位符不许翻倍

2026-10-31 真机（私聊 Texas 16:23）：

    用户发：一张表情包
    日志：  收到: [图片] [图片] +1图        ← 占位两个，图只有一张
    模型：  「就看清一张…另一张我这儿没取到」
    被追问：「哦，两张图其实是一张啊」「哪来的第二张，我瞎说的？」

根因：缓冲里的 parts 已经带 `[图片]`（入口 `extract_text` 给的），
`_private_flush` 重新入队时又给每张图补一个 image 段 → 一条图变两个占位。

锁三件事：
  A. 纯图（parts=["[图片]"] + 1 张图）→ 重入队后只有一个 `[图片]`
  B. 图文（parts=["[图片]", "在吗"] + 1 张图）→ 只有「在吗」+ 一个 `[图片]`
  C. 两张图 → 两个占位（不能修成"永远一个"）

运行：python tests/test_private_merge_placeholders.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

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


class _Q:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def _flush(parts, images):
    """构造一条缓冲记录并触发刷出，返回重新入队的那条消息。"""
    q = _Q()
    orig_q = bot_core.MSG_QUEUE
    bot_core.MSG_QUEUE = q
    try:
        bot_core.PRIVATE_MSG_BUFFER["9001"] = {
            "parts": parts, "images": images, "first": 0.0, "last": 0.0,
        }
        bot_core._private_flush("9001")
    finally:
        bot_core.MSG_QUEUE = orig_q
        bot_core.PRIVATE_MSG_BUFFER.pop("9001", None)
    return q.items[0] if q.items else None


def main():
    print("[A] 纯图：一条图只能有一个占位")
    item = _flush(["[图片]"], ["C:/tmp/a.jpg"])
    text = bot_core.extract_text(item) if item else ""
    check("重入队成功", bool(item))
    check("只有一个 [图片]", text.count("[图片]") == 1, repr(text))

    print("\n[B] 图文：文字保留，图一个占位")
    item = _flush(["[图片]", "在吗"], ["C:/tmp/a.jpg"])
    text = bot_core.extract_text(item) if item else ""
    check("文字还在", "在吗" in text, repr(text))
    check("只有一个 [图片]", text.count("[图片]") == 1, repr(text))

    print("\n[C] 两张图：两个占位（不能修成永远一个）")
    item = _flush(["[图片]"], ["C:/tmp/a.jpg", "C:/tmp/b.jpg"])
    text = bot_core.extract_text(item) if item else ""
    check("两个 [图片]", text.count("[图片]") == 2, repr(text))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
