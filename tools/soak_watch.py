# -*- coding: utf-8 -*-
"""40 分钟日志哨兵（1.5.9 发布前观察窗）

从当前文件末尾开始 tail，持续 N 秒，按类别计数并抓异常。
不修改任何东西，只读 + 写报告。

用法： python tools/soak_watch.py <log> <seconds> <out.md>
"""
import io
import json
import os
import re
import sys
import time

LOG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "EggBloom_v3", "qq_bot.log")
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 2400
OUT = sys.argv[3] if len(sys.argv) > 3 else r"D:\workbuddy_tmp\soak_report.md"
STATE = OUT + ".json"

# 拿来做归类的模式（顺序即优先级）
PAT = [
    ("crash",   re.compile(r"\[崩溃\]|未捕获异常|Traceback \(most recent")),
    ("ai_fail", re.compile(r"AI 失败|AI Worker 异常|AI 不重试")),
    ("timeout", re.compile(r"\[超时放弃\]|等待超时")),
    ("error",   re.compile(r"ERROR|Error:|错误|失败|异常")),
    ("skip",    re.compile(r"\[跳过:|跳过.*消息|跳过本轮|跳过重搜")),
    ("ai_ok",   re.compile(r"AI 回复 \(")),
    ("send",    re.compile(r"\[发送\]|发送图片成功|\[表情\] 发送成功")),
    ("tool",    re.compile(r"工具调用:")),
]

counts = {k: 0 for k, _ in PAT}
counts["lines"] = 0
counts["unknown_fmt"] = 0          # 没有日期前缀的行（说明有代码还在用旧格式）
examples = {k: [] for k, _ in PAT}
skip_reasons = {}
ai_lat = []
started = time.time()
pos = 0
if os.path.exists(LOG):
    pos = os.path.getsize(LOG)


def _classify(line):
    for name, rx in PAT:
        if rx.search(line):
            return name
    return None


def _snapshot(final=False):
    dur = time.time() - started
    rows = [
        "# 日志观察窗 %s" % ("（已结束）" if final else "（进行中）"),
        "",
        "- 日志: `%s`" % LOG,
        "- 起点: %s（从文件偏移 %d 开始）" % (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)), 0),
        "- 已观察: **%.0f 秒 / %d 秒**" % (dur, DURATION),
        "",
        "| 类别 | 次数 |",
        "|---|---|",
    ]
    for k in ("lines", "crash", "ai_fail", "timeout", "error", "skip",
              "ai_ok", "send", "tool", "unknown_fmt"):
        rows.append("| %s | %d |" % (k, counts.get(k, 0)))
    if skip_reasons:
        rows += ["", "## 跳过原因分布", "", "| 原因 | 次数 |", "|---|---|"]
        for k, v in sorted(skip_reasons.items(), key=lambda x: -x[1])[:15]:
            rows.append("| %s | %d |" % (k.replace("|", "/"), v))
    if examples["crash"] or examples["ai_fail"]:
        rows += ["", "## 异常样本（最多 8 条）", "```"]
        for e in (examples["crash"] + examples["ai_fail"])[:8]:
            rows.append(e)
        rows.append("```")
    rows += ["", "> 生成于 %s" % time.strftime("%Y-%m-%d %H:%M:%S")]
    try:
        io.open(OUT, "w", encoding="utf-8").write("\n".join(rows) + "\n")
    except Exception:
        pass
    try:
        io.open(STATE, "w", encoding="utf-8").write(json.dumps(
            {"counts": counts, "dur": dur, "skip": skip_reasons},
            ensure_ascii=False))
    except Exception:
        pass


SKIP_RE = re.compile(r"\[跳过:([^\]]+)\]|跳过([^:：\n]{2,20})")
while time.time() - started < DURATION:
    try:
        size = os.path.getsize(LOG)
        if size < pos:          # 轮转了，从头读
            pos = 0
        if size > pos:
            with io.open(LOG, "r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            for line in chunk.splitlines():
                if not line.strip():
                    continue
                counts["lines"] += 1
                if not line.startswith("[20"):        # 期望 [2026-…
                    counts["unknown_fmt"] += 1
                kind = _classify(line)
                if kind:
                    counts[kind] += 1
                    if len(examples[kind]) < 8:
                        examples[kind].append(line[:300])
                m = SKIP_RE.search(line)
                if m:
                    key = (m.group(1) or m.group(2) or "").strip()
                    if key:
                        skip_reasons[key] = skip_reasons.get(key, 0) + 1
    except Exception as e:
        counts["error"] += 1
        if len(examples["error"]) < 8:
            examples["error"].append("watcher: %s" % e)
    _snapshot()
    time.sleep(5)

_snapshot(final=True)
print("done", json.dumps(counts, ensure_ascii=False))
