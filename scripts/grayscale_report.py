# -*- coding: utf-8 -*-
"""scripts/grayscale_report.py —— 灰度前后对照（1.6.0 刀 2 `life_context_enable`）。

## 为什么要有它

"灰度 24 小时"如果没有对照，第二天只能说"好像还行"。这个脚本把
`qq_bot.log` 按**切换时刻**切成前后两段，用同一套正则统计同一批指标：

| 指标 | 说明 |
|---|---|
| AI 调用 | 次数 / 平均 tokens —— 合并上下文会不会让 prompt 变长 |
| `[记忆]` 注入 | 段数 / 字数 —— 记忆这一块有没有被合并影响 |
| `[生活上下文]` 合并 | 块数 / 字数 —— 开关真的生效了没、合并了多少 |
| `[发送]` | 出站次数 |
| Traceback | 有没有被合并打崩 |

## 用法

    # 只看今天（单窗口）
    python scripts/grayscale_report.py --log "…\\data\\logs\\qq_bot.log"

    # 前后对照（切换时刻取自 bot_config 或显式传入）
    python scripts/grayscale_report.py --log "…" --switch "2026-11-01 21:35:00"

    # 机器可读（给放回交接文档用）
    python scripts/grayscale_report.py --log "…" --switch "…" --json

## 边界

只解析日志文本，不连 Bot、不读配置；日志格式变了这里会**少读**而不是乱读
（每类都只认自己那条正则，读不到就是 0，并在输出里标出来）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

#: 日志行首时间戳
TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
#: AI (1051 tokens, 0.002227 yuan)
# 2026-11-02：日志真身是 `AI 回复 (8468 tokens, ￥0.001755, 缓存命中 …)`，
# 旧正则只认 `AI (N tokens, X yuan)` → 一直统计成 0（灰度报告差点误判成"没真跑"）。
AI_RE = re.compile(r"AI (?:回复\s*)?\((\d+)\s*tokens?,\s*[￥¥]?\s*([-\d.]+)")
#: [记忆] uid=… scene=private 注入 3 段/209 字（称呼 63 / …）
MEM_RE = re.compile(r"\[记忆\].*注入\s*(\d+)\s*段/(\d+)\s*字")
#: [生活上下文] 合并 12 块 → 422 字（预算 8000…）
LIFE_RE = re.compile(r"\[生活上下文\] 合并\s*(\d+)\s*块\s*→\s*(\d+)\s*字")
SEND_RE = re.compile(r"\[发送\]")
GUARD_RE = re.compile(r"\[认账闸\]")


def parse_ts(line: str) -> Optional[str]:
    m = TS_RE.match(line)
    return m.group(1) if m else None


def summarize(lines: List[str]) -> Dict[str, object]:
    """统计一段日志：各指标的次数与均值。

    ★ `life_merge_real` vs `life_merge_orphan`（2026-11-01 踩出来的）：
      测试进程 import bot_core 时也会写**同一份**活日志（数据根指到
      `%LOCALAPPDATA%\\EggBloom_v3`），于是"合并 N 块"这种行里混进了测试输出。
      真实一轮里合并行之后紧跟 `AI (...)`，测试输出后面没有 —— 用这个区分：
      只有 real 才是"灰度真跑过的轮数"，orphan 单独报出来当污染信号。
    """
    ai_n = ai_tok = 0
    mem_n = mem_seg = mem_chars = 0
    life_n = life_blocks = life_chars = 0
    life_real = life_orphan = 0
    sends = guards = traces = 0
    for i, ln in enumerate(lines):
        m = AI_RE.search(ln)
        if m:
            ai_n += 1
            ai_tok += int(m.group(1))
        m = MEM_RE.search(ln)
        if m:
            mem_n += 1
            mem_seg += int(m.group(1))
            mem_chars += int(m.group(2))
        m = LIFE_RE.search(ln)
        if m:
            life_n += 1
            life_blocks += int(m.group(1))
            life_chars += int(m.group(2))
            tail = lines[i + 1:i + 4]
            if any(AI_RE.search(x) for x in tail):
                life_real += 1
            else:
                life_orphan += 1
        if SEND_RE.search(ln):
            sends += 1
        if GUARD_RE.search(ln):
            guards += 1
        if "Traceback" in ln:
            traces += 1
    return {
        "lines": len(lines),
        "ai_calls": ai_n,
        "ai_tokens_avg": round(ai_tok / ai_n) if ai_n else 0,
        "mem_inject": mem_n,
        "mem_seg_avg": round(mem_seg / mem_n, 1) if mem_n else 0,
        "mem_chars_avg": round(mem_chars / mem_n) if mem_n else 0,
        "life_merge": life_n,
        "life_merge_real": life_real,
        "life_merge_orphan": life_orphan,
        "life_blocks_avg": round(life_blocks / life_n, 1) if life_n else 0,
        "life_chars_avg": round(life_chars / life_n) if life_n else 0,
        "sends": sends,
        "consistency_hits": guards,
        "tracebacks": traces,
    }


def split_by_switch(lines: List[str], switch_ts: str) -> Tuple[List[str], List[str]]:
    """按时间戳切成 (切换前, 切换后)。没有时间戳的行跟着上一行归属。"""
    before: List[str] = []
    after: List[str] = []
    cur_after = False
    for ln in lines:
        ts = parse_ts(ln)
        if ts:
            cur_after = ts >= switch_ts
        (after if cur_after else before).append(ln)
    return before, after


def _fmt(rows: List[Tuple[str, object, object]]) -> str:
    out = [f"{'指标':<16}{'切换前':>10}{'切换后':>10}"]
    out.append("-" * 36)
    for name, a, b in rows:
        out.append(f"{name:<16}{str(a):>10}{str(b):>10}")
    return "\n".join(out)


FIELDS = [
    ("日志行", "lines"),
    ("AI 调用", "ai_calls"),
    ("平均 tokens", "ai_tokens_avg"),
    ("记忆注入次数", "mem_inject"),
    ("记忆段数/次", "mem_seg_avg"),
    ("记忆字数/次", "mem_chars_avg"),
    ("合并上下文次数", "life_merge"),
    ("↳ 真跑（后有 AI）", "life_merge_real"),
    ("↳ 疑似污染", "life_merge_orphan"),
    ("合并块数/次", "life_blocks_avg"),
    ("合并字数/次", "life_chars_avg"),
    ("发送次数", "sends"),
    ("认账闸命中", "consistency_hits"),
    ("Traceback", "tracebacks"),
]


def is_redirected(path: str) -> bool:
    """这个 Python 看到的路径是不是被重定向了（微软商店版 Python 的坑）。

    ★ 2026-11-01 实测：`python` 若是 **Microsoft Store 版**，`%LOCALAPPDATA%`
      会被重定向到 `…\\Packages\\PythonSoftwareFoundation.Python.3.13_…\\LocalCache\\Local`，
      于是同一路径读到的是**几十分钟前的旧副本**（那次读到 10.8MB / 21:22，
      而 PowerShell 看到的是 2.5MB / 21:42）。用 `realpath` 一比就知道。
      对策：读活日志一律用 App 自带的 `python\\python.exe`（真 CPython），
      或者先用 PowerShell 把文件拷到非 AppData 的目录。
    """
    try:
        return os.path.realpath(path) != os.path.abspath(path)
    except Exception:
        return False


def find_log(explicit: str = "") -> str:
    """找正在写的 qq_bot.log。

    ★ 2026-11-01 踩过：机器上同时存在两份日志 ——
      `%LOCALAPPDATA%\\EggBloom_v3\\qq_bot.log`（**活的**，Bot 实际在写）
      和 `<repo>\\data\\logs\\qq_bot.log`（旧的，某个时刻之后就不再增长）。
    按修改时间挑最新的那份；显式传 --log 时无条件用它。
    """
    if explicit:
        return explicit
    cands = [
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "EggBloom_v3",
                     "qq_bot.log"),
        os.path.join(os.getcwd(), "data", "logs", "qq_bot.log"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "logs", "qq_bot.log"),
    ]
    cands = [c for c in cands if os.path.exists(c)]
    if not cands:
        return ""
    return max(cands, key=lambda p: os.path.getmtime(p))


def _safe_console() -> None:
    """控制台是 cp936 时，"↳"这类字符会让整个报表在最后一步崩掉（今天踩过）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="灰度前后对照（读 qq_bot.log）")
    ap.add_argument("--log", default="", help="qq_bot.log 路径（默认自动挑最新的）")
    ap.add_argument("--switch", default="",
                    help="切换时刻 'YYYY-MM-DD HH:MM:SS'；不传则只统计整个文件")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--allow-stale", action="store_true",
                    help="路径被重定向时也照样读（默认拒绝，避免拿旧副本下结论）")
    args = ap.parse_args(argv)
    _safe_console()

    log = find_log(args.log)
    if not log or not os.path.exists(log):
        print("找不到日志：自动挑过的位置都没有。用 --log 指定"
              "（活的通常是 %LOCALAPPDATA%\\EggBloom_v3\\qq_bot.log）",
              file=sys.stderr)
        return 2
    if is_redirected(log):
        print(f"⚠ 这个 Python 看到的日志路径被**重定向**了：\n"
              f"   给定 : {log}\n"
              f"   实际 : {os.path.realpath(log)}\n"
              f"  多半是微软商店版 Python 把 %LOCALAPPDATA% 指到了包内 LocalCache，"
              f"读到的是旧副本。请改用 App 自带解释器"
              f"（<安装目录>\\python\\python.exe），"
              f"或先用 PowerShell 把日志拷到 D: 再传 --log。\n"
              f"  确认要读这份旧副本时加 --allow-stale。", file=sys.stderr)
        if not args.allow_stale:
            return 3

    with open(log, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    today = ""
    for ln in reversed(lines):
        ts = parse_ts(ln)
        if ts:
            today = ts[:10]
            break
    lines = [ln for ln in lines if (parse_ts(ln) or today)[:10] == today]

    if args.switch:
        before, after = split_by_switch(lines, args.switch)
        b, a = summarize(before), summarize(after)
        data = {"log": log, "switch": args.switch, "before": b, "after": a}
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            rows = [(n, b[k], a[k]) for n, k in FIELDS]
            print(f"日志: {log}\n切换: {args.switch}（当天 {today}）\n")
            print(_fmt(rows))
            if not a["life_merge"]:
                print("\n⚠ 切换后没有 `[生活上下文] 合并` 行 —— 开关可能没生效，"
                      "或这段时间没有真实对话。")
            elif a["life_merge_orphan"]:
                print(f"\n⚠ 切换后有 {a['life_merge_orphan']} 条合并行后面没有 AI 调用 —— "
                      f"多半是**测试进程**在写同一份日志（数据根指到 AppData）。"
                      f"看「真跑」那一行，别用总数下结论。")
        return 0

    s = summarize(lines)
    data = {"log": log, "date": today, "window": s}
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"日志: {log}（当天 {today}，共 {s['lines']} 行）")
        for n, k in FIELDS:
            print(f"  {n:<16}{s[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
