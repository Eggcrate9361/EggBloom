# -*- coding: utf-8 -*-
"""scripts/make_meme_sample.py —— 给 Standard 包挑一小把表情包

背景
    完整表情包库里有二创动漫图，不适合随公开包分发，
    所以 Standard 曾经「只有表情包代码、没有表情包」——能认图、能接梗，却无图可发。

本脚本从**本机完整库**里挑一小把（默认 20 张）放进 Standard：

  * 只挑文件名干净的 —— 原库有 101 个文件名带 QQ 群号（zhenxun_rl_<群号>_*.gif），
    那是别人的号，不能进公开包；
  * 优先小文件（jpg < gif），整包增量控制在 ~2 MB；
  * 生成一份只含这些条目的 tags.json —— 表情包引擎按 tags.json 索引，
    不扫目录，所以种子文件必须一起给。

产物（写进 Standard 暂存树）
    resources/app/memes/memes/sample/<图片>
    resources/app/memes/memes/tags.json      ← 只含 sample 条目

用法
    python scripts/make_meme_sample.py --dst "<Standard>/resources/app/memes/memes"
    python scripts/make_meme_sample.py --count 30 --max-mb 5 ...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil

DEFAULT_SRC = r"E:\EggBloom水星\EggBloom Desktop-new\resources\app\memes"
SAMPLE_DIR = "sample"
BAD_NUM = re.compile(r"\d{6,}")          # 6 位以上数字 = 疑似 QQ 号，一律不要
IMG_EXT = (".gif", ".png", ".jpg", ".jpeg", ".webp")


def pick(src: str, count: int, max_mb: float):
    tags_path = os.path.join(src, "memes", "tags.json")
    if not os.path.isfile(tags_path):
        raise SystemExit(f"找不到完整库的 tags.json：{tags_path}")
    db = json.load(open(tags_path, encoding="utf-8"))

    cands = []
    for key, tags in db.items():
        if not str(key).lower().endswith(IMG_EXT):
            continue
        if BAD_NUM.search(str(key)):
            continue
        p = os.path.join(src, "memes", key.replace("/", os.sep))
        if not os.path.isfile(p):
            continue
        cands.append((os.path.getsize(p), key, tags))
    if not cands:
        raise SystemExit("完整库里没有文件名干净的条目")

    # 小的优先，jpg 优先于 gif（同尺寸下更小）
    cands.sort(key=lambda t: (t[0], t[1].endswith(".gif")))
    budget = int(max_mb * 1048576)
    picked, total = [], 0
    for size, key, tags in cands:
        if len(picked) >= count or total + size > budget:
            break
        picked.append((size, key, tags))
        total += size
    return picked, total, len(cands)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC, help="本机完整表情包库（含 memes/tags.json）")
    ap.add_argument("--dst", required=True, help="Standard 暂存树的 resources/app/memes/memes")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--max-mb", type=float, default=3.0)
    args = ap.parse_args()

    picked, total, pool = pick(args.src, args.count, args.max_mb)
    sample_dir = os.path.join(args.dst, SAMPLE_DIR)
    os.makedirs(sample_dir, exist_ok=True)

    new_db = {}
    for size, key, tags in picked:
        name = os.path.basename(key)
        shutil.copy2(os.path.join(args.src, "memes", key.replace("/", os.sep)),
                     os.path.join(sample_dir, name))
        new_db[f"{SAMPLE_DIR}/{name}"] = tags

    with open(os.path.join(args.dst, "tags.json"), "w", encoding="utf-8") as f:
        json.dump(new_db, f, ensure_ascii=False, indent=1)

    print(f"候选 {pool} 条 → 选用 {len(picked)} 条，共 {total/1048576:.2f} MB")
    for size, key, tags in picked[:5]:
        print(f"   {size/1024:6.1f} KB  {key}  {tags[:4]}")
    if len(picked) > 5:
        print(f"   …… 另 {len(picked) - 5} 条")
    print(f"tags.json 已写 {len(new_db)} 条 → {os.path.join(args.dst, 'tags.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
