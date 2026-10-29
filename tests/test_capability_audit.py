# -*- coding: utf-8 -*-
"""harness/reply_audit 第三类断言「能力自述」单测（方案 v2 §5，2026-10-30）。

真机样本全部取自 10-29 私聊测试（用户 2011257385）：
    "收了啊" / "我这边就一个[图片]占位" / "图我这儿还是糊的"
    "我这边没谁戳我" / "图是系统自己配的"

运行：python tests/test_capability_audit.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import reply_audit as ra  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


OK_VISION = [(0, "vision", True, "银发蓝眼")]
FAIL_VISION = [(0, "vision", False, "timeout")]
NO_CALLS = []


# ── 1. 露后台 ────────────────────────────────────────────────────
h = ra.find_capability_claims("我这边就一个[图片]占位，里面是猪是猫全靠你说", NO_CALLS)
check("『占位』判露后台", [k for k, _ in h] == ["mechanism"], h)
h = ra.find_capability_claims("那些不是我挑的，图是系统自己配的", NO_CALLS)
check("『系统自己配』判露后台", [k for k, _ in h] == ["mechanism"], h)
h = ra.find_capability_claims("解析失败了，三条链都没回来", NO_CALLS)
check("『解析失败』判露后台", [k for k, _ in h] == ["mechanism"], h)
h = ra.find_capability_claims("这个模型挺好看的", NO_CALLS)
check("『模型』单独出现不误伤（可能是手办）", h == [], h)

# ── 2. 看不到图：三态 ─────────────────────────────────────────────
h = ra.find_capability_claims("图我这儿还是糊的", NO_CALLS, had_media=True)
check("有图 + 一次没调 → 拦（vision_not_tried）",
      [k for k, _ in h] == ["vision_not_tried"], h)
h = ra.find_capability_claims("图我这儿还是糊的", FAIL_VISION, had_media=True)
check("有图 + 视觉失败过 → 放过（它是真试过）", h == [], h)
h = ra.find_capability_claims("图我这儿还是糊的", OK_VISION, had_media=True)
check("有图 + 视觉成功却说糊 → 矛盾（vision_seen_but_denied）",
      [k for k, _ in h] == ["vision_seen_but_denied"], h)
h = ra.find_capability_claims("图我这儿还是糊的", NO_CALLS, had_media=False)
check("这一轮没图 → 不判'看不到'", h == [], h)

# 同一张图 9 秒内两种答案：第二次读到了，就不许再说糊
h = ra.find_capability_claims("看不到，图又没读着", OK_VISION, had_media=True)
check("『看不到』+ 成功调用 → 矛盾", [k for k, _ in h] == ["vision_seen_but_denied"], h)

# ── 3. 收不到 ≠ 没发生 ───────────────────────────────────────────
h = ra.find_capability_claims("我这边没谁戳我", NO_CALLS)
check("『没谁戳我』判否定断言", [k for k, _ in h] == ["negative"], h)
h = ra.find_capability_claims("没人跟我说过这个", NO_CALLS)
check("『没人跟我说』判否定断言", [k for k, _ in h] == ["negative"], h)
h = ra.find_capability_claims("我没有什么想说的", NO_CALLS)
check("『没有什么想说的』不误伤", h == [], h)
h = ra.find_capability_claims("今天没人陪我吃饭，我自己吃的", NO_CALLS)
check("『没人陪我吃饭』不误伤（不是收消息）", h == [], h)

# ── 4. 改写 ─────────────────────────────────────────────────────
out = ra.rewrite_capability_claims(
    "我这边就一个[图片]占位，里面是猪是猫全靠你说", [("mechanism", "我这边就一个[图片]占位，里面是猪是猫全靠你说")])
check("露后台 → 固定话术", "占位" not in out and "没取到" in out, out)

out = ra.rewrite_capability_claims("我这边没谁戳我", [("negative", "我这边没谁戳我")])
check("否定断言 → 『没记录到』", "没记录到" in out and "没谁" not in out, out)

out = ra.rewrite_capability_claims("图我这儿还是糊的\n行吧", [("vision_not_tried", "图我这儿还是糊的")])
check("只替换命中句，其余保留", "行吧" in out and "糊的" not in out, out)

check("没命中就原样返回", ra.rewrite_capability_claims("你好", []) == "你好")

# ── 5. 回归：操作完成态那一类不能被带坏 ───────────────────────────
hits = ra.find_unsupported_claims("已经发给你了", [])
check("操作完成态仍照旧拦", len(hits) == 1, hits)
hits = ra.find_unsupported_claims("已经发给你了", [(0, "send_user_file", True, "ok")])
check("有成功调用就放过", hits == [], hits)
check("能力自述不误吃操作完成态",
      ra.find_capability_claims("已经发给你了", NO_CALLS) == [],
      ra.find_capability_claims("已经发给你了", NO_CALLS))

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
