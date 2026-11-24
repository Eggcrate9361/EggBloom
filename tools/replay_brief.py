# -*- coding: utf-8 -*-
"""tools/replay_brief.py —— 用历史聊天记录回放「场景状态简报」

为什么有这个工具（2026-11-24）：
    生产一天只有二三十条消息，等真实流量验证「她现在」三行写得好不好，
    要等一周。历史日志里已经有几百条真实对话，可以直接回放：
    把每条入站/出站喂给 `autonomy.ledger` 的纯函数，逐轮渲染
    `render_state_brief()`，看它会不会天天说同一句话（新的八股）。

判据不是"像人"，而是：
    1. 简报非空的比例（太高 = 每轮都在塞东西；太低 = 字段没用）
    2. 同一行原文的重复次数（重复多 = 简报自己成了模板）
    3. 抽样人工看：每一条是不是"此刻真的该说"的状态

用法：
    python tools/replay_brief.py                     # 默认读 D:\\EggBloom水星 下的日志
    python tools/replay_brief.py <日志1> <日志2> ... # 指定文件
    python tools/replay_brief.py --limit 40          # 只看前 40 条非空
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autonomy import ledger as L  # noqa: E402

DEFAULT_GLOBS = [r"D:\EggBloom水星\server-logs-*\bot-journal.log",
                 r"D:\EggBloom水星\triage-*\bot-journal.log"]

_TS = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
#: 老日志（server-logs-1116~1119）只有 syslog 前缀，没有年份：
#:   `11月 16 21:03:03 VM-0-6-ubuntu python[...]: [CHAT]{...}`
_TS_SYSLOG = re.compile(r"^(\d{1,2})月\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})")
_CHAT = re.compile(r"\[CHAT\](\{.*\})\s*$")


def _parse_ts(line: str, default_year: int = 2026):
    """从日志行取时间戳。两种格式都要认，认不出返回 None。"""
    import datetime
    m = _TS.search(line)
    if m:
        try:
            return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return None
    m = _TS_SYSLOG.match(line)
    if m:
        mo, d, hh, mm, ss = (int(x) for x in m.groups())
        try:
            return datetime.datetime(default_year, mo, d, hh, mm, ss).timestamp()
        except ValueError:
            return None
    return None


def _mask(s: str) -> str:
    s = str(s or "")
    return "***" + s[-3:] if len(s) > 3 else "***"


def iter_events(paths):
    """按时间顺序产出 (ts, event)。事件是 [CHAT] 行里能确定的那部分。"""
    rows = []
    for p in paths:
        for line in open(p, encoding="utf-8", errors="replace"):
            m = _CHAT.search(line.strip())
            if not m:
                continue
            ts = _parse_ts(line)
            if not ts:
                continue
            try:
                d = json.loads(m.group(1))
            except Exception:
                continue
            direction = str(d.get("direction") or "")
            text = str(d.get("text") or "")
            if not text.strip():
                continue
            scene = "group" if str(d.get("type") or "") == "group" else "private"
            target = str(d.get("target") or "")
            rows.append((ts, direction, scene, target, text, str(d.get("sender") or "")))
    rows.sort(key=lambda r: r[0])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--limit", type=int, default=30, help="最多打印多少条非空简报")
    a = ap.parse_args()

    paths = a.paths or [p for g in DEFAULT_GLOBS for p in sorted(glob.glob(g))]
    if not paths:
        print("没找到日志")
        return 1
    rows = iter_events(paths)
    print("日志文件 %d 个，可用事件 %d 条" % (len(paths), len(rows)))

    states = {}
    shown = 0
    total_in = 0
    nonempty = 0
    lines = Counter()
    kinds = Counter()

    for ts, direction, scene, target, text, sender in rows:
        key = L.key_of(scene, target)
        st = states.get(key)
        if direction == "in":
            total_in += 1
            ev = {"sender": sender or target, "text": text, "ts": ts}
            if scene == "group":
                ev["group_id"] = target
                ev["at_only"] = False
            st = L.observe_user_message(st, ev, ts)
            states[key] = st
            # 与生产一致：她可能过一会儿才回，这里按"1 分钟后"渲染一次
            brief = L.render_state_brief(st, ts + 60)
            if brief:
                nonempty += 1
                for ln in brief.splitlines():
                    lines[ln] += 1
                    if ln.startswith("刚被"):
                        kinds["情绪"] += 1
                    elif ln.startswith("答应过"):
                        kinds["欠账"] += 1
                    elif ln.startswith("上一条") or ln.startswith("刚说过"):
                        kinds["话头未接"] += 1
                    elif ln.startswith("隔了"):
                        kinds["久别"] += 1
                    elif ln.startswith("跟这个人"):
                        kinds["关系"] += 1
            if brief and shown < a.limit:
                shown += 1
                print("\n[%s] %s %s：%s" % (
                    __import__("time").strftime("%m-%d %H:%M", __import__("time").localtime(ts)),
                    "群" if scene == "group" else "私聊", _mask(target), text[:36].replace("\n", " / ")))
                for ln in brief.splitlines():
                    print("      → " + ln)
        else:
            ev = {"scene": scene, "text": text, "ts": ts, "trace_id": "r%d" % int(ts)}
            if scene == "group":
                ev["group_id"] = target
            else:
                ev["user_id"] = target
            states[key] = L.observe_bot_reply(st, ev, ts)

    print("\n" + "=" * 60)
    print("入站 %d 条；有简报 %d 条（%.0f%%）" % (
        total_in, nonempty, 100.0 * nonempty / max(1, total_in)))
    print("行类型分布：", dict(kinds) or "无")
    print("重复最多的 8 行：")
    for ln, n in lines.most_common(8):
        print("  %2d×  %s" % (n, ln))
    return 0


if __name__ == "__main__":
    sys.exit(main())
