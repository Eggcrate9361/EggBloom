# -*- coding: utf-8 -*-
"""按时间窗分析 qq_bot.log（压测复盘用）

比 soak_watch 严格的地方：
  · 续行（多行日志的第 2 行起）不计入异常 —— 否则注入文本里的"失败"全是误报
  · 旧格式只认 `^[HH:MM:SS]`，不再把续行算成格式问题
  · 按秒统计入站/出站，能看出"消息堆积后有没有追平"

用法：
    python tools/analyze_window.py "2026-10-28 19:40:00" "2026-10-28 19:43:00"
    python tools/analyze_window.py --last 300            # 最近 300 秒
"""
import argparse
import datetime
import io
import os
import re
import sys
from collections import Counter, defaultdict

LOG = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                   "EggBloom_v3", "qq_bot.log")

TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
OLD_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]")
IN_RE = re.compile(r"\[事件\] 收到: post_type=message")
OUT_RE = re.compile(r"\[发送\]|发送图片成功|\[表情\] 发送成功|群回复:")
SKIP_RE = re.compile(r"\[跳过:([^\]]+)\]")
CRASH_RE = re.compile(r"\[崩溃\]|Traceback \(most recent|未捕获异常|AI Worker 异常")
AIFAIL_RE = re.compile(r"AI 失败|AI 不重试")
TO_RE = re.compile(r"\[超时放弃\]|等待超时")
TOOL_RE = re.compile(r"工具调用:")
DECIDE_RE = re.compile(r"\[决策放行\]")
BUF_RE = re.compile(r"新建缓冲区")


def load_window(t0, t1):
    rows = []
    cur = None
    with io.open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = TS_RE.match(line)
            if m:
                ts = m.group(1)
                if t0 <= ts <= t1:
                    cur = {"ts": ts, "lines": [line.rstrip("\n")]}
                    rows.append(cur)
                else:
                    cur = None
                continue
            # 续行并入上一条（若上一条在窗口内）
            if cur is not None:
                cur["lines"].append(line.rstrip("\n"))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("start", nargs="?")
    ap.add_argument("end", nargs="?")
    ap.add_argument("--last", type=int, default=0, help="只分析最近 N 秒")
    a = ap.parse_args()

    if a.last:
        now = datetime.datetime.now()
        t0 = (now - datetime.timedelta(seconds=a.last)).strftime("%Y-%m-%d %H:%M:%S")
        t1 = now.strftime("%Y-%m-%d %H:%M:%S")
    else:
        t0, t1 = a.start, a.end
    if not t0 or not t1:
        print("需要给 start/end 或 --last")
        sys.exit(2)

    rows = load_window(t0, t1)
    print(f"窗口 {t0} → {t1}")
    print(f"日志条数（合并续行后）: {len(rows)}")

    c = Counter()
    skips = Counter()
    per_sec_in = defaultdict(int)
    per_sec_out = defaultdict(int)
    crashes, tos, aifails = [], [], []
    tools = 0

    for r in rows:
        head = r["lines"][0]
        body = "\n".join(r["lines"])
        if IN_RE.search(head):
            c["in"] += 1
            per_sec_in[r["ts"]] += 1
        if OUT_RE.search(head):
            c["out"] += 1
            per_sec_out[r["ts"]] += 1
        if DECIDE_RE.search(head):
            c["decide"] += 1
        if BUF_RE.search(head):
            c["buffer"] += 1
        if TOOL_RE.search(head):
            tools += 1
        m = SKIP_RE.search(head)
        if m:
            c["skip"] += 1
            # 只保留"原因"部分，去掉人数等数字噪声
            skips[re.sub(r"\d+", "N", m.group(1))[:24]] += 1
        if CRASH_RE.search(body):
            c["crash"] += 1
            crashes.append(body[:300])
        if AIFAIL_RE.search(body):
            c["ai_fail"] += 1
            aifails.append(body[:300])
        if TO_RE.search(body):
            c["timeout"] += 1
            tos.append(body[:200])
        if OLD_RE.match(head):
            c["old_fmt"] += 1

    print("\n| 类别 | 次数 |")
    print("|---|---|")
    for k in ("in", "out", "decide", "buffer", "skip", "tool", "crash",
              "ai_fail", "timeout", "old_fmt"):
        print(f"| {k} | {c.get(k, 0)} |")

    if per_sec_in:
        peak = max(per_sec_in.values())
        peak_at = max(per_sec_in, key=per_sec_in.get)
        print(f"\n入站峰值: {peak} 条/秒 @ {peak_at}")
        print(f"有入站的秒数: {len(per_sec_in)}")
    if per_sec_out:
        print(f"出站峰值: {max(per_sec_out.values())} 条/秒")
    if skips:
        print("\n跳过原因 top10:")
        for k, v in skips.most_common(10):
            print(f"  {v:4d}  {k}")
    for name, arr in (("崩溃", crashes), ("AI 失败", aifails), ("超时", tos)):
        if arr:
            print(f"\n{name} 样本（{len(arr)}）:")
            for x in arr[:5]:
                print("  " + x.replace("\n", " / ")[:220])


if __name__ == "__main__":
    main()