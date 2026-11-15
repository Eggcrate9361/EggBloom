# -*- coding: utf-8 -*-
"""scripts/dedupe_memes.py —— 清理已有的重复表情（感知哈希）

2026-11-01：采集早期只按 sha1 去重，同图换编码会各存一份。
这个脚本扫 collected/，用 dHash 找"看着是同一张"的组，默认只报告。

    python scripts/dedupe_memes.py                 # 只报告
    python scripts/dedupe_memes.py --dist 6        # 放宽阈值
    python scripts/dedupe_memes.py --apply         # 删多余（先整目录备份）
"""
import argparse
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import meme_collect as mc  # noqa: E402


def scan(collected, dist):
    files = {}
    for n in sorted(os.listdir(collected)):
        p = os.path.join(collected, n)
        if not os.path.isfile(p):
            continue
        if not n.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
            continue
        h = mc._dhash(p)
        if h is not None:
            files[p] = h
    paths = list(files)
    groups, used = [], set()
    for i, a in enumerate(paths):
        if a in used:
            continue
        g = [a]
        for b in paths[i + 1:]:
            if b in used:
                continue
            if mc._hamming(files[a], files[b]) <= dist:
                g.append(b)
                used.add(b)
        if len(g) > 1:
            used.add(a)
            groups.append(g)
    return groups


def main(argv=None):
    ap = argparse.ArgumentParser(description="清理重复表情")
    ap.add_argument("--dir", default=os.path.join(ROOT, "memes", "memes", "collected"))
    ap.add_argument("--dist", type=int, default=4, help="dHash 距离阈值")
    ap.add_argument("--apply", action="store_true", help="真的删除（先备份）")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.dir):
        print(f"目录不存在: {a.dir}")
        return 2
    groups = scan(a.dir, a.dist)
    print(f"扫描 {a.dir}：{len(groups)} 组疑似重复（阈值 {a.dist}）")
    total = 0
    for g in groups:
        keep = sorted(g, key=lambda p: os.path.getmtime(p))[0]
        dup = [p for p in g if p != keep]
        total += len(dup)
        print(f"\n  保留 {os.path.basename(keep)}")
        for p in dup:
            print(f"   重复 {os.path.basename(p)}")
    if not a.apply:
        print(f"\n[dry-run] 可删 {total} 个；加 --apply 真删（会先备份）")
        return 0
    bak = os.path.join(a.dir, "_dups_backup_" + time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(bak, exist_ok=True)
    for g in groups:
        keep = sorted(g, key=lambda p: os.path.getmtime(p))[0]
        for p in g:
            if p != keep:
                shutil.move(p, os.path.join(bak, os.path.basename(p)))
    print(f"\n已删除 {total} 个，备份在 {bak}")
    print("提示：索引里可能还留着条目，跑一次采集/重启会自动对不上并被忽略。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
