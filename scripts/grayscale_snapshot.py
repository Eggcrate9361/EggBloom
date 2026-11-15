# -*- coding: utf-8 -*-
"""灰度开工：把活日志拷一份 + 记基线（1.6.0 刀 2 `life_context_enable`）。

为什么拷：`qq_bot.log` 在 Bot 重启时会被截断（今天实测 10.8MB → 2.5MB），
24 小时的窗口靠一个文件是留不住的。拷贝放在数据目录的 `_grayscale/<ts>/` 下。
"""
import glob
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))
import grayscale_report as gr  # noqa: E402

SWITCH_TS = "2026-11-01 21:33:21"


def main():
    log = gr.find_log("")
    if not log:
        print("找不到活日志", file=sys.stderr)
        return 2
    if gr.is_redirected(log):
        print("⚠ 这个 Python 把日志路径重定向了（多半是微软商店版 Python）。\n"
              "   请用 App 自带解释器跑：<安装目录>\\python\\python.exe "
              "scripts\\grayscale_snapshot.py", file=sys.stderr)
        return 3
    stamp = time.strftime("%Y%m%d-%H%M")
    # 快照放仓库 data/logs/_grayscale/（不入库），方便直接用普通 python 读；
    # 日志所在目录是 AppData，之后可能被 Bot 重启截断，所以拷贝是必须的。
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "logs", "_grayscale", stamp)
    if not os.path.isdir(os.path.dirname(os.path.dirname(root))):
        root = os.path.join(os.path.dirname(log), "_grayscale", stamp)
    outdir = root
    os.makedirs(outdir, exist_ok=True)

    with open(log, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    today = ""
    for ln in reversed(lines):
        ts = gr.parse_ts(ln)
        if ts:
            today = ts[:10]
            break
    today_lines = [ln for ln in lines if (gr.parse_ts(ln) or today)[:10] == today]
    before, after = gr.split_by_switch(today_lines, SWITCH_TS)

    snap = os.path.join(outdir, "qq_bot_before.log")
    shutil.copy2(log, snap)
    for extra in glob.glob(log + ".*"):
        shutil.copy2(extra, os.path.join(outdir, os.path.basename(extra)))

    data = {
        "switch_ts": SWITCH_TS,
        "snapshot_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_log": log,
        "source_size": os.path.getsize(log),
        "before": gr.summarize(before),
        "after_so_far": gr.summarize(after),
        "note": ("基线窗口是 Bot 21:26 启动之后到 21:33:21 之间；更早的日志已被"
                 "重启截断。对照时用 --switch 同一时刻切，别用总数。"),
    }
    with open(os.path.join(outdir, "baseline.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"快照目录: {outdir}")
    print(f"活日志  : {log}  ({data['source_size']} 字节)")
    print(f"基线窗口: {len(before)} 行 / 切换后至今 {len(after)} 行")
    print("\n切换前:")
    for k, v in data["before"].items():
        print(f"  {k:<18}{v}")
    print("\n切换后（至今）:")
    for k, v in data["after_so_far"].items():
        print(f"  {k:<18}{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
