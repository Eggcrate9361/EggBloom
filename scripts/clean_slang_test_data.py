# -*- coding: utf-8 -*-
"""
scripts/clean_slang_test_data.py — 清掉学习侧测试期留下的垃圾数据。

背景：开发 slang_learner 时用测试词验证过 pending 列表显示，
垃圾漏进了真源词典与候选池。经确认后清理。

清理对象（在 --app 指定的 resources\\app 下）：
  1. 真源 slang_dict.json 里的测试词条（新梗测试1/2、绝杀 等）
  2. data/pending.json、data/injected.json 里 id 命中测试词的记录（含重复项）
  3. plugins/ego/slang_dict.json、plugins/ego/slang_replies.json（已无人读取的分裂旧文件，--purge-legacy 时删）

所有被删内容都写进一份 .removed-<时间戳>.json 留痕，不从磁盘上凭空消失。

用法：
    python scripts/clean_slang_test_data.py --app "<resources\\app>" --dry-run
    python scripts/clean_slang_test_data.py --app "<resources\\app>" --purge-legacy
"""
import argparse
import json
import os
import shutil
import sys
import time

# 测试/垃圾词条判定：前缀或精确匹配
TEST_EXACT = {"绝杀", "新梗测试1", "新梗测试2", "测试梗XYZ"}
TEST_PREFIX = ("新梗测试", "测试梗", "zzz_", "_闭环探针", "_闭环")


def is_test_id(s):
    if not isinstance(s, str):
        return False
    s = s.strip()
    return s in TEST_EXACT or s.startswith(TEST_PREFIX)


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  ! 读取失败 {path}: {e}")
        return default


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def clean(app_root, dry_run=False, purge_legacy=False):
    removed = {"dict": {}, "pending": [], "injected": [],
               "legacy_files": []}

    # ── 1. 真源词典 ────────────────────────────────────────────────
    dict_path = os.path.join(app_root, "slang_dict.json")
    data = _load_json(dict_path, {"entries": {}})
    entries = data.get("entries", {}) if isinstance(data, dict) else {}
    hits = [k for k in list(entries) if is_test_id(k)]
    print(f"[真源词典] {len(entries)} 条，命中测试词 {len(hits)} 条")
    for k in hits:
        removed["dict"][k] = entries[k]
        print(f"  - {k!r} => {str(entries[k])[:50]!r}")
    for k in hits:
        entries.pop(k, None)

    # ── 2. 候选池 ──────────────────────────────────────────────────
    ddir = os.path.join(app_root, "plugins", "ego", "slang_learner", "data")
    for fname, key in (("pending.json", "pending"), ("injected.json", "injected")):
        p = os.path.join(ddir, fname)
        lst = _load_json(p, [])
        if not isinstance(lst, list):
            print(f"[{fname}] 格式异常，跳过")
            continue
        keep, drop = [], []
        for it in lst:
            if isinstance(it, dict) and is_test_id(it.get("id")):
                drop.append(it)
            else:
                keep.append(it)
        removed[key] = drop
        print(f"[{fname}] {len(lst)} → {len(keep)}（移除 {len(drop)} 条测试记录）")
        for it in drop:
            print(f"  - {str(it.get('id'))!r} status={it.get('status')}")
        if not dry_run:
            _save_json(p, keep)

    # ── 3. 分裂的旧文件 ────────────────────────────────────────────
    legacy = [
        os.path.join(app_root, "plugins", "ego", "slang_dict.json"),
        os.path.join(app_root, "plugins", "ego", "slang_replies.json"),
    ]
    for p in legacy:
        if os.path.exists(p):
            print(f"[旧文件] {'将删除' if purge_legacy else '保留'} {p}")
            removed["legacy_files"].append(p)

    if dry_run:
        print("\n(DRY RUN) 未写入任何文件")
        return 0

    tag = time.strftime("%Y%m%d-%H%M%S")
    if removed["dict"]:
        _save_json(dict_path, {"entries": entries})
        print(f"\n✔ 真源词典写入 {dict_path}（{len(entries)} 条）")
    rpath = os.path.join(ddir, f"slang_testdata_removed-{tag}.json")
    os.makedirs(ddir, exist_ok=True)
    with open(rpath, "w", encoding="utf-8") as f:
        json.dump({"removed_at": tag, **removed}, f, ensure_ascii=False, indent=2)
    print(f"✔ 删除留痕写入 {rpath}")

    if purge_legacy:
        for p in legacy:
            if os.path.exists(p):
                bak = f"{p}.bak-{tag}"
                shutil.copy2(p, bak)
                os.remove(p)
                print(f"✔ 已删除 {p}（备份 {os.path.basename(bak)}）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, help="resources\\app 绝对路径")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--purge-legacy", action="store_true",
                    help="同时删除已无人读取的 plugins/ego/slang_dict.json / slang_replies.json（先备份）")
    a = ap.parse_args()
    app = os.path.abspath(a.app)
    if not os.path.isdir(app):
        print(f"错误：{app} 不是目录")
        return 2
    return clean(app, a.dry_run, a.purge_legacy)


if __name__ == "__main__":
    sys.exit(main())
