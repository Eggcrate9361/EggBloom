# -*- coding: utf-8 -*-
"""tests/test_meme_save_turn.py —— “存表情包”该存哪张

2026-10-31 真机（私聊 /ω＼ 22:54–22:55）：

    22:54:07 发图 → 它读了图并回话
    22:54:28 「存表情包」→ meme_save: {"ok": false, "error": "这一轮没有可存的图片"}
    22:55:33 又发图 → 22:55:41 meme_save 仍报 "这一轮没有可存的图片"

两个根因：
  A. describe 模式下图走视觉转文字，`images` 传到模型时是 None，
     而 `set_turn` 只认 `images` → 工具侧永远看不到"这轮有图"。
     修：`call_deepseek(..., turn_images=真实图列表)` 与 `images` 分开传。
  B. 用户发完图、隔一条文字再说「存」时，本轮本来就没图。
     修：记住"上一次真的带图"的那轮（同人、5 分钟内），保存时回看。

锁五件事：
  1. 本轮有图 → 存本轮
  2. 本轮无图 + 上一轮有图（同人、时限内）→ 存上一轮
  3. 换人 → 不许拿别人的图
  4. 显式 path 优先
  5. 超过 5 分钟 → 不回看（避免"存"到很久以前的图）
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import tool_meme as M  # noqa: E402

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
    print("[1] 本轮有图")
    M.set_turn(images=["a.jpg"], is_group=False, sender="u1")
    check("存本轮", M.turn_images_for_save() == ["a.jpg"], M.turn_images_for_save())

    print("\n[2] 下一轮无图，回看上一轮（同人）")
    M.set_turn(images=[], is_group=False, sender="u1")
    check("回看到 a.jpg", M.turn_images_for_save() == ["a.jpg"], M.turn_images_for_save())

    print("\n[3] 换人")
    M.set_turn(images=[], is_group=False, sender="u2")
    check("不拿别人的图", M.turn_images_for_save() == [], M.turn_images_for_save())

    print("\n[4] 显式 path 优先")
    M.set_turn(images=["x.jpg"], is_group=False, sender="u2")
    check("用指定路径", M.turn_images_for_save("y.jpg") == ["y.jpg"],
          M.turn_images_for_save("y.jpg"))

    print("\n[5] 超过 5 分钟不回看")
    M.set_turn(images=["old.jpg"], is_group=False, sender="u3")
    M._LAST_IMAGES["ts"] = time.time() - 400
    M.set_turn(images=[], is_group=False, sender="u3")
    check("过期不取", M.turn_images_for_save() == [], M.turn_images_for_save())

    print("\n[6] 窗口可配（0 = 只认本轮）")
    _orig_cfg = M._cfg
    try:
        M._cfg = lambda: {"meme_save_lookback_sec": 0}
        M.set_turn(images=["p.jpg"], is_group=False, sender="u4")
        M.set_turn(images=[], is_group=False, sender="u4")
        check("配 0 → 不回看", M.turn_images_for_save() == [], M.turn_images_for_save())
        M._cfg = lambda: {"meme_save_lookback_sec": 600}
        M.set_turn(images=[], is_group=False, sender="u4")
        check("配 600 → 还看得到", M.turn_images_for_save() == ["p.jpg"], M.turn_images_for_save())
    finally:
        M._cfg = _orig_cfg

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
