# -*- coding: utf-8 -*-
"""scripts/grayscale_report.py 的判据（灰度对照不能靠肉眼）。

为什么单独测这个脚本：24 小时后要看的是"合并前后有没有变"，如果统计本身
读错（正则少读 / 窗口切错），结论就是错的 —— 而这种错不会报异常。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import grayscale_report as gr  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


LOG = """\
[2026-11-01 20:00:01] [记忆] uid=1 scene=private 注入 3 段/209 字（称呼 63 / 检索 74）
[2026-11-01 20:00:02] AI (1000 tokens, 0.001 yuan)
[2026-11-01 20:00:03] [发送] 私聊 → 1: retcode=0
[2026-11-01 21:40:00] [生活上下文] 合并 12 块 → 422 字（预算 8000，超了整块丢尾）
[2026-11-01 21:40:01] [记忆] uid=1 scene=private 注入 4 段/520 字（称呼 63 / 跨场景 82 / 检索 312 / 群画像 63）
[2026-11-01 21:40:02] AI (1200 tokens, 0.002 yuan)
[2026-11-01 21:40:03] [发送] 私聊 → 1: retcode=0
[2026-11-01 21:40:04] [认账闸] 命中：改成认账句
Traceback (most recent call last):
""".splitlines()

# ── 1. 时间戳与切窗 ─────────────────────────────────────────────────
check("能读时间戳", gr.parse_ts(LOG[0]) == "2026-11-01 20:00:01", gr.parse_ts(LOG[0]))
check("无时间戳的行返回 None", gr.parse_ts("Traceback (most recent call last):") is None)

before, after = gr.split_by_switch(LOG, "2026-11-01 21:00:00")
check("切换前的行数", len(before) == 3, len(before))
check("切换后的行数（含无时间戳行跟随后一行）", len(after) == 6, len(after))
check("切换后的第一行是生活上下文",
      "[生活上下文]" in after[0], after[0] if after else None)

# ── 2. 各类指标 ────────────────────────────────────────────────────
b = gr.summarize(before)
a = gr.summarize(after)
check("切换前 AI 1 次", b["ai_calls"] == 1, b)
check("切换后 AI 1 次", a["ai_calls"] == 1, a)
check("平均 tokens 读对（前）", b["ai_tokens_avg"] == 1000, b["ai_tokens_avg"])
check("平均 tokens 读对（后）", a["ai_tokens_avg"] == 1200, a["ai_tokens_avg"])
check("记忆 段/字 读对（前）", (b["mem_seg_avg"], b["mem_chars_avg"]) == (3, 209),
      (b["mem_seg_avg"], b["mem_chars_avg"]))
check("记忆 段/字 读对（后）", (a["mem_seg_avg"], a["mem_chars_avg"]) == (4, 520),
      (a["mem_seg_avg"], a["mem_chars_avg"]))
check("★ 切换前没有合并行", b["life_merge"] == 0, b["life_merge"])
check("★ 切换后合并 1 次、12 块、422 字",
      (a["life_merge"], a["life_blocks_avg"], a["life_chars_avg"]) == (1, 12, 422),
      (a["life_merge"], a["life_blocks_avg"], a["life_chars_avg"]))
# 合并行后面紧跟 AI 调用 = 真跑（2026-11-01 踩过测试进程污染同一份日志）
check("★ 合并行后有 AI → 记为真跑", (a["life_merge_real"], a["life_merge_orphan"]) == (1, 0),
      (a["life_merge_real"], a["life_merge_orphan"]))
_pollute = gr.summarize(["[2026-11-01 21:40:00] [生活上下文] 合并 12 块 → 422 字",
                         "[2026-11-01 21:40:00] 跑完退出"])
check("★ 合并行后面没有 AI → 记为疑似污染",
      (_pollute["life_merge"], _pollute["life_merge_real"], _pollute["life_merge_orphan"])
      == (1, 0, 1), _pollute)
check("发送次数分开算", (b["sends"], a["sends"]) == (1, 1), (b["sends"], a["sends"]))
check("认账闸只算切换后那次", (b["consistency_hits"], a["consistency_hits"]) == (0, 1),
      (b["consistency_hits"], a["consistency_hits"]))
check("Traceback 计到切换后", (b["tracebacks"], a["tracebacks"]) == (0, 1),
      (b["tracebacks"], a["tracebacks"]))

# ── 3. 读不到就是 0，不能编 ──────────────────────────────────────────
empty = gr.summarize([])
check("空窗口全 0", empty["ai_calls"] == 0 and empty["life_merge"] == 0, empty)
weird = gr.summarize(["[2026-11-01 21:00:00] 随便一行", "[2026-11-01 21:00:01] AI (?)"])
check("认不出的行不计入", weird["ai_calls"] == 0 and weird["lines"] == 2, weird)

# ── 4. 命令行不炸 / 选对日志 ────────────────────────────────────────
check("--log 不存在 → 返回码 2", gr.main(["--log", os.path.join(HERE, "_no_such.log")]) == 2)
check("显式 --log 优先（不自动挑）",
      gr.find_log(os.path.join(HERE, "_no_such.log")).endswith("_no_such.log"),
      gr.find_log(os.path.join(HERE, "_no_such.log")))
_pick = gr.find_log("")
check("自动挑：给出的路径存在（或全都不存在时为空）",
      (not _pick) or os.path.exists(_pick), _pick)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
