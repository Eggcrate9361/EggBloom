# -*- coding: utf-8 -*-
"""scripts/cost_now.py —— 现在花多少钱（实时）

## 为什么要有它

`token_stats.jsonl` 只记 token，不记钱；日志里的 `AI (… tokens, … yuan)`
是散在一行行的。想"实时看价格"，得有个地方把
**每条记录 × 当前时段单价** 折成钱并累计。

## 口径（与 pricing.py 一致，不自己造）

- DeepSeek 三档价：缓存命中 / 未命中 / 输出，**高峰（周一至周五 9–12、14–18 点）翻倍**；
- 别的厂商走平铺价并标 `estimated`（不冒充真实账单）；
- 只读 `logs/token_stats.jsonl`，不连 Bot、不改任何状态。

## 用法

    python scripts/cost_now.py                # 今天到现在的合计
    python scripts/cost_now.py --days 3       # 近三天
    python scripts/cost_now.py --watch 20     # 实时盯：每 20 秒打印新增记录与累计
    python scripts/cost_now.py --json         # 机器可读
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import pricing  # noqa: E402


def find_stats_file() -> str:
    """找正在写的那份 token_stats.jsonl（副本优先，其次仓库）。"""
    cands = [
        r"D:\EggBloom水星\EggBloom Desktop-new\resources\app\logs\token_stats.jsonl",
        os.path.join(ROOT, "logs", "token_stats.jsonl"),
    ]
    live = [p for p in cands if os.path.exists(p)]
    if not live:
        return ""
    return max(live, key=lambda p: os.path.getmtime(p))


def load(path: str, days: int = 1) -> list:
    """读最近 `days` 天的记录。坏行跳过（这条链不许抛）。"""
    today = datetime.now().date()
    keep = {(today.toordinal() - i) for i in range(max(1, days))}
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                ts = str(r.get("ts") or "")
                try:
                    d = datetime.strptime(ts[:10], "%Y-%m-%d").date()
                except Exception:
                    continue
                if d.toordinal() not in keep:
                    continue
                rows.append(r)
    except Exception:
        pass
    rows.sort(key=lambda r: str(r.get("ts") or ""))
    return rows


def cost_of(rec: dict) -> dict:
    usage = {
        "prompt_tokens": rec.get("prompt_tokens"),
        "completion_tokens": rec.get("completion_tokens"),
        "cache_hit_tokens": rec.get("cache_hit_tokens"),
        "prompt_cache_miss_tokens": rec.get("cache_miss_tokens"),
    }
    try:
        when = datetime.strptime(str(rec.get("ts"))[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        when = None
    return pricing.estimate("deepseek", str(rec.get("model") or "deepseek-flash"),
                            usage, now=when)


def summarize(rows: list) -> dict:
    total = 0.0
    by_lane, by_scene, by_day, by_tier = {}, {}, {}, {}
    for r in rows:
        e = cost_of(r)
        c = float(e.get("cost") or 0)
        total += c
        for key, bucket in (("lane", by_lane), ("scene", by_scene)):
            k = str(r.get(key) or "-")
            bucket.setdefault(k, {"n": 0, "cost": 0.0, "tokens": 0})
            bucket[k]["n"] += 1
            bucket[k]["cost"] = round(bucket[k]["cost"] + c, 6)
            bucket[k]["tokens"] += int(r.get("total_tokens") or 0)
        d = str(r.get("ts") or "")[:10]
        by_day.setdefault(d, {"n": 0, "cost": 0.0, "tokens": 0})
        by_day[d]["n"] += 1
        by_day[d]["cost"] = round(by_day[d]["cost"] + c, 6)
        by_day[d]["tokens"] += int(r.get("total_tokens") or 0)
        t = str(e.get("tier") or "-")
        by_tier[t] = round(by_tier.get(t, 0.0) + c, 6)
    return {"n": len(rows), "cost": round(total, 6),
            "tokens": sum(int(r.get("total_tokens") or 0) for r in rows),
            "avg_cost": round(total / len(rows), 6) if rows else 0.0,
            "by_lane": by_lane, "by_scene": by_scene,
            "by_day": by_day, "by_tier": by_tier}


def _fmt(s: dict) -> str:
    out = [f"  次数 {s['n']} · token {s['tokens']:,} · 费用 ¥{s['cost']:.4f}"
           f" · 单次均值 ¥{s['avg_cost']:.5f}"]
    if s["by_lane"]:
        out.append("  按通道：" + " / ".join(
            f"{k} {v['n']}次 ¥{v['cost']:.4f}" for k, v in
            sorted(s["by_lane"].items(), key=lambda kv: -kv[1]["cost"])))
    if s["by_tier"]:
        out.append("  按时段：" + " / ".join(
            f"{pricing.label(k)} ¥{v:.4f}" for k, v in s["by_tier"].items()))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="实时看花费（读 token_stats.jsonl）")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--watch", type=int, default=0, help="每 N 秒刷新（0 = 只看一次）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--file", default="")
    args = ap.parse_args(argv)
    try:
        for st in (sys.stdout, sys.stderr):
            st.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    path = args.file or find_stats_file()
    if not path or not os.path.exists(path):
        print("找不到 token_stats.jsonl（副本 ...\\logs\\ 或仓库 logs\\）", file=sys.stderr)
        return 2

    if not args.watch:
        s = summarize(load(path, args.days))
        if args.json:
            print(json.dumps({"file": path, "window_days": args.days, "summary": s},
                             ensure_ascii=False, indent=2))
        else:
            print(f"文件: {path}\n最近 {args.days} 天：")
            print(_fmt(s))
        return 0

    # 实时：按"已处理到第几条"增量读（文件是追加写、已按 ts 排序）
    rows = load(path, args.days)
    seen_n = len(rows)
    print(f"盯：{path}\n起始：{_fmt(summarize(rows))}\n"
          f"（每 {args.watch}s 刷新，Ctrl+C 结束）")
    try:
        while True:
            time.sleep(max(3, args.watch))
            rows = load(path, args.days)
            if len(rows) >= seen_n:
                new = rows[seen_n:]
            else:                      # 文件被轮转/截断：整份重读，别漏
                new = rows
            seen_n = len(rows)
            if not new:
                continue
            add = summarize(new)
            cur = summarize(rows)
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 新增 {add['n']} 次"
                  f" · {add['tokens']:,} token · ¥{add['cost']:.5f}")
            for r in new[-6:]:
                e = cost_of(r)
                print(f"    {str(r.get('ts'))[11:]} {r.get('lane')}/{r.get('scene')} "
                      f"{int(r.get('total_tokens') or 0):>6} tok  ¥{e['cost']:.5f}"
                      f"  [{pricing.label(e['tier'])}]")
            print(f"  累计：{_fmt(cur)}")
    except KeyboardInterrupt:
        print("\n停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
